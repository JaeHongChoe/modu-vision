"""Execution contracts for the two supported inspection flows."""

import base64
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import torch
from fastapi import HTTPException

from backend.api import routes_flowchart
from backend.engine import flowchart_engine as engine_module
from backend.engine.flowchart_engine import (
    FlowEdge,
    FlowNode,
    FlowNodeData,
    FlowchartEngine,
    FlowchartPipeline,
    FlowchartRunRequest,
    get_default_flowchart,
)
from backend.engine.segmentation.model import build_segmentation_model


def _single_segmentation_pipeline() -> FlowchartPipeline:
    nodes = [
        FlowNode(id="image", position={"x": 0, "y": 0}, data=FlowNodeData(label="Image", node_type="input")),
        FlowNode(id="segment", position={"x": 1, "y": 0}, data=FlowNodeData(
            label="Full image segmentation", node_type="inspection", task="segmentation",
            model_job_id="trained_segmentation", threshold=0.5,
            params={"min_defect_area_px": 8},
        )),
        FlowNode(id="decision", position={"x": 2, "y": 0}, data=FlowNodeData(
            label="Decision", node_type="decision", rule="any_defect_is_ng",
        )),
        FlowNode(id="result", position={"x": 3, "y": 0}, data=FlowNodeData(label="Local result", node_type="output")),
    ]
    edges = [
        FlowEdge(id="e1", source="image", target="segment"),
        FlowEdge(id="e2", source="segment", target="decision"),
        FlowEdge(id="e3", source="decision", target="result"),
    ]
    return FlowchartPipeline(id="single_seg", name="Single segmentation", nodes=nodes, edges=edges)


class _PositiveSegmentation(torch.nn.Module):
    def forward(self, image: torch.Tensor) -> torch.Tensor:
        logits = torch.zeros((1, 2, image.shape[-2], image.shape[-1]), device=image.device)
        logits[:, 1] = 8.0
        return logits


class _OnePixelSegmentation(torch.nn.Module):
    def forward(self, image: torch.Tensor) -> torch.Tensor:
        logits = torch.zeros((1, 2, image.shape[-2], image.shape[-1]), device=image.device)
        logits[:, 0] = 8.0
        logits[:, 1, 0, 0] = 16.0
        return logits


class _RedPixelSegmentation(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        self.calls += 1
        defect_logits = image[:, :1] * 20.0 - 10.0
        return torch.cat([torch.zeros_like(defect_logits), defect_logits], dim=1)


def test_single_segmentation_inspects_entire_image_without_detector(monkeypatch):
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_detection_model", lambda **kwargs: pytest.fail("detector must not run"))
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **kwargs: (_PositiveSegmentation(), True))

    result = engine.execute(pipeline=_single_segmentation_pipeline(), image=np.zeros((40, 60, 3), dtype=np.uint8))

    assert result["status"] == "success"
    assert result["final_verdict"] == "NG"
    assert result["roi_count"] == 1
    assert result["crops"][0]["bbox"] == [0, 0, 60, 40]
    assert "분할" in result["crops"][0]["flaw_type"]
    assert "Anomaly" not in result["crops"][0]["flaw_type"]
    assert "전체 이미지" in result["rejection_reason"]
    assert [step["node_id"] for step in result["execution_steps"]] == ["image", "segment", "decision", "result"]
    assert result["execution_steps"][-1]["status"] == "flagged_ng"
    annotated = cv2.imdecode(
        np.frombuffer(base64.b64decode(result["annotated_image"].split(",", 1)[1]), np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert annotated[20, 30, 2] > 40  # interior red tint from the actual model mask


def test_zero_detected_rois_requires_review_instead_of_ok(monkeypatch):
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_extract_candidate_rois", lambda *args: ([], 1.0, "passed"))
    monkeypatch.setattr(engine, "_inspect_crops", lambda *args: ([], 0.0, "skipped"))

    result = engine.execute(pipeline=get_default_flowchart(), image=np.zeros((40, 60, 3), dtype=np.uint8))

    assert result["final_verdict"] == "REVIEW"
    assert result["is_ok"] is False
    assert result["roi_count"] == 0
    assert result["execution_steps"][2]["status"] == "skipped"
    assert result["execution_steps"][3]["status"] == "review_required"
    assert result["execution_steps"][4]["status"] == "review_required"


def test_disconnected_edges_rejected_before_model_inference(monkeypatch):
    engine = FlowchartEngine(device="cpu")
    pipeline = get_default_flowchart()
    pipeline.edges[0].target = "node_decision"
    monkeypatch.setattr(engine, "_get_detection_model", lambda **kwargs: pytest.fail("model must not run"))

    with pytest.raises(ValueError, match="(?i)pipeline|edge|linear|connected|branch"):
        engine.execute(pipeline=pipeline, image=np.zeros((40, 60, 3), dtype=np.uint8))


def test_execution_follows_edges_when_node_storage_order_differs(monkeypatch):
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **kwargs: (_PositiveSegmentation(), True))
    pipeline = _single_segmentation_pipeline()
    pipeline.nodes.reverse()

    result = engine.execute(pipeline=pipeline, image=np.zeros((40, 60, 3), dtype=np.uint8))

    assert [step["node_id"] for step in result["execution_steps"]] == [
        "image", "segment", "decision", "result",
    ]


def test_segmentation_minimum_area_controls_crop_verdict(monkeypatch):
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **kwargs: (_OnePixelSegmentation(), True))
    node = _single_segmentation_pipeline().nodes[1]

    crops, _, _ = engine._inspect_crops(
        np.zeros((40, 60, 3), dtype=np.uint8),
        [{"id": "whole", "bbox": [0, 0, 60, 40], "label": "Image", "confidence": 1.0}],
        node,
    )

    assert crops[0].defect_area_px == 1
    assert crops[0].verdict == "OK"


def test_full_image_segmentation_keeps_small_defect_at_tile_scale(monkeypatch):
    engine = FlowchartEngine(device="cpu")
    model = _RedPixelSegmentation()
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **kwargs: (model, True))
    image = np.zeros((384, 512, 3), dtype=np.uint8)
    image[150:160, 380:390, 0] = 255
    pipeline = _single_segmentation_pipeline()
    pipeline.nodes[1].data.params["min_defect_area_px"] = 50

    result = engine.execute(pipeline=pipeline, image=image)

    assert model.calls > 1
    assert result["final_verdict"] == "NG"
    assert result["crops"][0]["defect_area_px"] >= 50


def test_full_image_tile_limit_returns_review_without_inference(monkeypatch):
    monkeypatch.setattr(engine_module, "MAX_SEGMENTATION_TILES", 4)
    engine = FlowchartEngine(device="cpu")
    model = _RedPixelSegmentation()
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **kwargs: (model, True))

    result = engine.execute(
        pipeline=_single_segmentation_pipeline(),
        image=np.zeros((384, 512, 3), dtype=np.uint8),
    )

    assert model.calls == 0
    assert result["status"] == "review"
    assert result["final_verdict"] == "REVIEW"
    assert "상한 4개" in result["rejection_reason"]


def test_full_image_path_uses_original_resolution_and_preview_cap(monkeypatch, tmp_path):
    image_path = tmp_path / "wide.png"
    cv2.imwrite(str(image_path), np.zeros((100, 1700, 3), dtype=np.uint8))
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **kwargs: (_RedPixelSegmentation(), True))

    result = engine.execute(pipeline=_single_segmentation_pipeline(), image_path=str(image_path))

    preview = cv2.imdecode(
        np.frombuffer(base64.b64decode(result["annotated_image"].split(",", 1)[1]), np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert result["inspected_image_size"] == [1700, 100]
    assert max(preview.shape[:2]) <= 1600


def test_new_flowchart_defaults_to_single_segmentation(monkeypatch, tmp_path):
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "pipeline.json")

    pipeline = routes_flowchart.get_pipeline()

    assert [node.data.node_type for node in pipeline.nodes] == [
        "input", "inspection", "decision", "output",
    ]
    assert pipeline.nodes[1].data.task == "segmentation"


def test_cannot_save_disconnected_pipeline(monkeypatch, tmp_path):
    target = tmp_path / "pipeline.json"
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", target)
    pipeline = get_default_flowchart()
    pipeline.edges[0].target = "node_decision"

    with pytest.raises(HTTPException) as error:
        routes_flowchart.save_pipeline(pipeline)

    assert error.value.status_code == 422
    assert not target.exists()


def test_missing_image_is_reported_before_model_configuration():
    request = FlowchartRunRequest(image_path=None, pipeline=get_default_flowchart())

    with pytest.raises(HTTPException) as error:
        routes_flowchart.run_flowchart(request)

    assert error.value.status_code == 422
    assert "image" in str(error.value.detail).lower()


def test_trained_segmentation_checkpoint_with_null_model_name_loads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = build_segmentation_model(preset="fast", num_classes=2, pretrained=False)
    job_id = "job_123_abc123"
    checkpoint = tmp_path / "models" / job_id / "best_model.pt"
    checkpoint.parent.mkdir(parents=True)
    torch.save({
        "task": "segmentation",
        "model_name": None,
        "preset": "fast",
        "image_size": [256, 256],
        "classes": ["background", "defect"],
        "model_state_dict": model.state_dict(),
    }, checkpoint)

    engine = FlowchartEngine(device="cpu")
    loaded, trained = engine._get_inspection_model(task="segmentation", job_id=job_id)

    assert trained
    assert loaded is not None
    assert engine._model_input_sizes[("segmentation", job_id, "fast")] == (256, 256)


def test_checkpoint_accepts_only_local_job_artifacts_without_symlink_escape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    models = tmp_path / "models"
    allowed = models / "job_123_abc123" / "best_model.pt"
    allowed.parent.mkdir(parents=True)
    allowed.write_bytes(b"checkpoint")
    outside = tmp_path / "outside.pt"
    outside.write_bytes(b"untrusted")
    linked_job = models / "job_124_def456"
    linked_job.symlink_to(tmp_path, target_is_directory=True)
    linked_file = models / "job_125_abcdef" / "best_model.pt"
    linked_file.parent.mkdir(parents=True)
    linked_file.symlink_to(outside)
    legacy = tmp_path / "projects" / "job_126_abc123" / "models" / "best_model.pt"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy checkpoint")

    engine = FlowchartEngine(device="cpu")
    assert engine._resolve_checkpoint("job_123_abc123", "segmentation") == allowed
    assert engine._resolve_checkpoint("job_126_abc123", "segmentation") == legacy
    assert engine._resolve_checkpoint(str(outside), "segmentation") is None
    assert engine._resolve_checkpoint("../outside.pt", "segmentation") is None
    assert engine._resolve_checkpoint("job_124_def456", "segmentation") is None
    assert engine._resolve_checkpoint("job_125_abcdef", "segmentation") is None


def test_run_rejects_arbitrary_checkpoint_path_before_torch_load(tmp_path, monkeypatch):
    image_path = tmp_path / "inspection.png"
    cv2.imwrite(str(image_path), np.zeros((32, 32, 3), dtype=np.uint8))
    arbitrary = tmp_path / "malicious.pt"
    arbitrary.write_bytes(b"untrusted")
    pipeline = _single_segmentation_pipeline()
    pipeline.nodes[1].data.model_job_id = str(arbitrary)
    monkeypatch.setattr(routes_flowchart.torch, "load", lambda *args, **kwargs: pytest.fail("must reject path before torch.load"))

    with pytest.raises(HTTPException) as error:
        routes_flowchart.run_flowchart(FlowchartRunRequest(pipeline=pipeline, image_path=str(image_path)))

    assert error.value.status_code == 409


def test_run_loads_checkpoint_metadata_with_weights_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    image_path = tmp_path / "inspection.png"
    cv2.imwrite(str(image_path), np.zeros((32, 32, 3), dtype=np.uint8))
    job_id = "job_123_abc123"
    checkpoint = tmp_path / "models" / job_id / "best_model.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"checkpoint")
    pipeline = _single_segmentation_pipeline()
    pipeline.nodes[1].data.model_job_id = job_id
    load_options = []

    def fake_load(path, **kwargs):
        load_options.append(kwargs)
        assert path == checkpoint
        return {"task": "segmentation", "model_state_dict": {}}

    monkeypatch.setattr(routes_flowchart.torch, "load", fake_load)
    monkeypatch.setattr(routes_flowchart._ENGINE, "execute", lambda **kwargs: {"status": "success"})

    result = routes_flowchart.run_flowchart(FlowchartRunRequest(pipeline=pipeline, image_path=str(image_path)))

    assert result["status"] == "success"
    assert load_options == [{"map_location": "cpu", "weights_only": True}]


@pytest.mark.parametrize("status", ["running", "stopping", "aborted", "failed"])
def test_run_rejects_known_training_job_until_completed(tmp_path, monkeypatch, status):
    monkeypatch.chdir(tmp_path)
    image_path = tmp_path / "inspection.png"
    cv2.imwrite(str(image_path), np.zeros((32, 32, 3), dtype=np.uint8))
    job_id = "job_123_abc123"
    checkpoint = tmp_path / "models" / job_id / "best_model.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"partial checkpoint")
    pipeline = _single_segmentation_pipeline()
    pipeline.nodes[1].data.model_job_id = job_id
    monkeypatch.setattr(routes_flowchart.training_job_manager, "get_job", lambda _: SimpleNamespace(status=status))
    monkeypatch.setattr(routes_flowchart.torch, "load", lambda *args, **kwargs: pytest.fail("must wait for completed training"))

    with pytest.raises(HTTPException) as error:
        routes_flowchart.run_flowchart(FlowchartRunRequest(pipeline=pipeline, image_path=str(image_path)))

    assert error.value.status_code == 409
    assert "training" in str(error.value.detail).lower()
