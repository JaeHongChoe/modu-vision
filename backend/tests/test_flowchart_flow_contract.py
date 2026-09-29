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
    get_five_model_chain_flowchart,
    get_single_detection_flowchart,
    get_single_segmentation_flowchart,
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


@pytest.mark.parametrize("task,label", [
    ("classification", "분류"),
    ("anomaly", "이상"),
])
def test_new_single_inspection_flow_matches_completed_model_task(monkeypatch, tmp_path, task, label):
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "pipeline.json")

    default = routes_flowchart.get_pipeline(inspection_task=task)
    selected = routes_flowchart.get_single_segmentation_template(
        job_id="job_123_abc123", inspection_task=task,
    )

    assert [node.data.node_type for node in default.nodes] == [
        "input", "inspection", "decision", "output",
    ]
    assert default.nodes[1].data.task == task
    assert default.nodes[1].data.model_job_id is None
    assert selected.nodes[1].data.task == task
    assert selected.nodes[1].data.model_job_id == "job_123_abc123"
    assert label in selected.name


def test_saved_flow_is_only_loaded_for_its_inspection_recipe(monkeypatch, tmp_path):
    target = tmp_path / "pipeline.json"
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", target)
    saved = routes_flowchart.get_single_segmentation_template(inspection_task="classification")
    routes_flowchart.save_pipeline(saved)

    assert routes_flowchart.get_pipeline(inspection_task="classification").id == saved.id
    anomaly = routes_flowchart.get_pipeline(inspection_task="anomaly")
    assert anomaly.id == "single_anomaly"
    assert anomaly.nodes[1].data.task == "anomaly"
    assert routes_flowchart.get_pipeline(inspection_task="classification").id == saved.id


def test_saving_another_recipe_preserves_the_first_saved_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "pipeline.json")
    classification = routes_flowchart.get_single_segmentation_template(inspection_task="classification")
    classification.name = "Saved classification"
    anomaly = routes_flowchart.get_single_segmentation_template(inspection_task="anomaly")
    anomaly.name = "Saved anomaly"
    detector = routes_flowchart.get_single_detection_template()

    routes_flowchart.save_pipeline(classification, recipe_task="classification")
    routes_flowchart.save_pipeline(anomaly, recipe_task="anomaly")
    routes_flowchart.save_pipeline(detector, recipe_task="detection")

    assert routes_flowchart.get_pipeline(inspection_task="classification").name == "Saved classification"
    assert routes_flowchart.get_pipeline(inspection_task="anomaly").name == "Saved anomaly"
    assert routes_flowchart.get_pipeline(inspection_task="detection").id == "single_detection"
    assert len(list(tmp_path.glob("pipeline_*.json"))) == 3


def test_saved_flows_are_scoped_to_dataset_and_recipe(monkeypatch, tmp_path):
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "pipeline.json")
    source_a = tmp_path / "source_a"
    source_b = tmp_path / "source_b"
    source_a.mkdir()
    source_b.mkdir()
    first = routes_flowchart.get_single_segmentation_template(inspection_task="classification")
    second = routes_flowchart.get_single_segmentation_template(inspection_task="classification")
    first.name = "Source A classification"
    second.name = "Source B classification"

    routes_flowchart.save_pipeline(first, recipe_task="classification", source_dataset_path=str(source_a))
    routes_flowchart.save_pipeline(second, recipe_task="classification", source_dataset_path=str(source_b))

    assert routes_flowchart.get_pipeline("classification", str(source_a)).name == first.name
    assert routes_flowchart.get_pipeline("classification", str(source_b)).name == second.name
    assert len(list(tmp_path.glob("pipeline_classification_*.json"))) == 2


def test_project_flowcharts_are_isolated_even_with_the_same_source(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "legacy" / "pipeline.json")
    source = tmp_path / "images"
    source.mkdir()
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    a = client.post("/api/project/create", json={"name": "A", "project_dir": str(project_a)})
    assert a.status_code == 200
    flow_a = get_single_segmentation_flowchart()
    flow_a.name = "Only in A"
    path = f"/api/flowchart/pipeline?recipe_task=segmentation&source_dataset_path={source}"
    assert client.post(path, json=flow_a.model_dump()).status_code == 200

    b = client.post("/api/project/create", json={"name": "B", "project_dir": str(project_b)})
    assert b.status_code == 200
    assert client.get(f"/api/flowchart/pipeline?inspection_task=segmentation&source_dataset_path={source}").json()["name"] != "Only in A"
    flow_b = get_single_segmentation_flowchart()
    flow_b.name = "Only in B"
    assert client.post(path, json=flow_b.model_dump()).status_code == 200

    assert client.post("/api/project/open", json={"project_dir": str(project_a)}).status_code == 200
    assert client.get(f"/api/flowchart/pipeline?inspection_task=segmentation&source_dataset_path={source}").json()["name"] == "Only in A"
    assert client.post("/api/project/open", json={"project_dir": str(project_b)}).status_code == 200
    assert client.get(f"/api/flowchart/pipeline?inspection_task=segmentation&source_dataset_path={source}").json()["name"] == "Only in B"


def test_existing_legacy_flow_is_imported_once_into_active_project(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "legacy" / "pipeline.json")
    legacy = get_single_segmentation_flowchart()
    legacy.name = "Legacy inspection"
    routes_flowchart.save_pipeline(legacy, recipe_task="segmentation")
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    assert client.post("/api/project/create", json={"name": "A", "project_dir": str(project_a)}).status_code == 200
    assert client.get("/api/flowchart/pipeline?inspection_task=segmentation").json()["name"] == "Legacy inspection"
    assert list((project_a / "flowcharts").glob("pipeline_segmentation.json"))
    assert client.get("/api/flowchart/pipeline/active").json()["name"] == "Legacy inspection"
    assert client.post("/api/project/create", json={"name": "B", "project_dir": str(project_b)}).status_code == 200
    assert client.get("/api/flowchart/pipeline?inspection_task=segmentation").json()["name"] != "Legacy inspection"


def test_mixed_model_chain_can_be_saved_and_reopened(monkeypatch, tmp_path):
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "pipeline.json")
    pipeline = get_single_segmentation_flowchart()
    first = pipeline.nodes[1]
    first.data.task = "classification"
    second = FlowNode(id="segment_two", position={"x": 500, "y": 160}, data=FlowNodeData(
        label="Second model", node_type="inspection", task="segmentation",
    ))
    pipeline.nodes.insert(2, second)
    pipeline.edges[1].source = second.id
    pipeline.edges.insert(1, FlowEdge(id="first-second", source=first.id, target=second.id, payload_type="roi"))

    result = routes_flowchart.save_pipeline(pipeline)

    assert result["recipe_task"] == "mixed"
    reopened = routes_flowchart.get_pipeline(inspection_task="mixed")
    assert [node.data.task for node in reopened.nodes if node.data.node_type == "inspection"] == [
        "classification", "segmentation",
    ]


def test_sample_images_only_list_active_project_source(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "legacy" / "pipeline.json")
    source_a = tmp_path / "source_a"
    source_b = tmp_path / "source_b"
    source_a.mkdir()
    source_b.mkdir()
    cv2.imwrite(str(source_a / "a.png"), np.zeros((16, 16, 3), dtype=np.uint8))
    cv2.imwrite(str(source_b / "b.png"), np.zeros((16, 16, 3), dtype=np.uint8))
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    assert client.post("/api/project/create", json={"name": "A", "project_dir": str(tmp_path / "project_a")}).status_code == 200
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source_a)}).status_code == 200
    images_a = client.get("/api/flowchart/sample-images").json()["images"]
    assert [image["name"] for image in images_a] == ["a.png"]

    assert client.post("/api/project/create", json={"name": "B", "project_dir": str(tmp_path / "project_b")}).status_code == 200
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source_b)}).status_code == 200
    images_b = client.get("/api/flowchart/sample-images").json()["images"]
    assert [image["name"] for image in images_b] == ["b.png"]


def test_run_without_inline_pipeline_uses_active_project_flow(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "legacy" / "pipeline.json")
    image = tmp_path / "inspection.png"
    cv2.imwrite(str(image), np.zeros((16, 16, 3), dtype=np.uint8))
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    assert client.post("/api/project/create", json={"name": "A", "project_dir": str(tmp_path / "project_a")}).status_code == 200
    pipeline = get_single_segmentation_flowchart()
    pipeline.nodes[1].data.label = "A only model"
    assert client.post("/api/flowchart/pipeline", json=pipeline.model_dump()).status_code == 200
    result = client.post("/api/flowchart/run", json={"image_path": str(image)})
    assert result.status_code == 409
    assert "A only model" in result.json()["detail"]


def test_flow_save_keeps_openable_versions_per_project(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "legacy" / "pipeline.json")
    source = tmp_path / "source"
    source.mkdir()
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    assert client.post("/api/project/create", json={"name": "A", "project_dir": str(tmp_path / "project_a")}).status_code == 200
    flow = get_single_segmentation_flowchart()
    flow.name = "First revision"
    path = f"/api/flowchart/pipeline?recipe_task=segmentation&source_dataset_path={source}"
    first = client.post(path, json=flow.model_dump())
    assert first.status_code == 200
    first_version = first.json()["version_id"]
    flow.name = "Second revision"
    second = client.post(path, json=flow.model_dump())
    assert second.status_code == 200
    assert second.json()["version_id"] != first_version

    versions = client.get(f"/api/flowchart/pipelines?source_dataset_path={source}")
    assert versions.status_code == 200
    assert [item["name"] for item in versions.json()["pipelines"]] == ["Second revision", "First revision"]
    assert client.get(f"/api/flowchart/pipelines/{first_version}").json()["name"] == "First revision"
    assert client.get(f"/api/flowchart/pipeline?inspection_task=segmentation&source_dataset_path={source}").json()["name"] == "Second revision"

    assert client.post("/api/project/create", json={"name": "B", "project_dir": str(tmp_path / "project_b")}).status_code == 200
    assert client.get(f"/api/flowchart/pipelines?source_dataset_path={source}").json()["pipelines"] == []
    assert client.get(f"/api/flowchart/pipelines/{first_version}").status_code == 404


def test_activating_saved_revision_changes_active_flow_without_creating_another_version(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "legacy" / "pipeline.json")
    source = tmp_path / "source"
    source.mkdir()
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    assert client.post("/api/project/create", json={"name": "A", "project_dir": str(tmp_path / "project_a")}).status_code == 200
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    flow = get_single_segmentation_flowchart()
    save_path = "/api/flowchart/pipeline"
    first = client.post(save_path, params={"recipe_task": "segmentation", "source_dataset_path": str(source)},
                        json={**flow.model_dump(), "name": "Revision one"})
    second = client.post(save_path, params={"recipe_task": "segmentation", "source_dataset_path": str(source)},
                         json={**flow.model_dump(), "name": "Revision two"})
    assert first.status_code == second.status_code == 200
    first_id, second_id = first.json()["version_id"], second.json()["version_id"]

    active = client.put(f"/api/flowchart/pipelines/{first_id}/activate",
                        params={"source_dataset_path": str(source)})

    assert active.status_code == 200
    assert active.json()["version_id"] == first_id
    assert active.json()["pipeline"]["name"] == "Revision one"
    assert client.get("/api/flowchart/pipeline/active", params={"source_dataset_path": str(source)}).json()["name"] == "Revision one"
    versions = client.get("/api/flowchart/pipelines", params={"source_dataset_path": str(source)}).json()["pipelines"]
    assert len(versions) == 2
    assert {item["version_id"]: item["is_active"] for item in versions} == {first_id: True, second_id: False}


def test_saved_revision_cannot_be_activated_for_another_project_source(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "legacy" / "pipeline.json")
    source_a, source_b = tmp_path / "source_a", tmp_path / "source_b"
    source_a.mkdir()
    source_b.mkdir()
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    assert client.post("/api/project/create", json={"name": "A", "project_dir": str(tmp_path / "project_a")}).status_code == 200
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source_a)}).status_code == 200
    flow = get_single_segmentation_flowchart()
    saved = client.post("/api/flowchart/pipeline", params={"source_dataset_path": str(source_a)}, json=flow.model_dump())
    assert saved.status_code == 200
    version_id = saved.json()["version_id"]

    assert client.put(f"/api/flowchart/pipelines/{version_id}/activate",
                      params={"source_dataset_path": str(source_b)}).status_code == 409
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source_b)}).status_code == 200
    assert client.put(f"/api/flowchart/pipelines/{version_id}/activate",
                      params={"source_dataset_path": str(source_b)}).status_code == 409
    assert client.get("/api/flowchart/pipeline/active", params={"source_dataset_path": str(source_b)}).status_code == 404
    assert client.post("/api/project/create", json={"name": "B", "project_dir": str(tmp_path / "project_b")}).status_code == 200
    assert client.put(f"/api/flowchart/pipelines/{version_id}/activate",
                      params={"source_dataset_path": str(source_a)}).status_code == 404


def test_run_uses_active_mixed_flow_revision_when_pipeline_omitted(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "legacy" / "pipeline.json")
    image = tmp_path / "inspection.png"
    cv2.imwrite(str(image), np.zeros((16, 16, 3), dtype=np.uint8))
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    assert client.post("/api/project/create", json={"name": "A", "project_dir": str(tmp_path / "project_a")}).status_code == 200
    pipeline = get_five_model_chain_flowchart()
    pipeline.nodes[1].data.label = "Active mixed first model"
    saved = client.post("/api/flowchart/pipeline", json=pipeline.model_dump())
    assert saved.status_code == 200
    assert saved.json()["recipe_task"] == "mixed"

    result = client.post("/api/flowchart/run", json={"image_path": str(image)})

    assert result.status_code == 409
    assert "Active mixed first model" in result.json()["detail"]


def test_active_mixed_flow_reopens_after_restart_only_for_same_project_and_source(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "legacy" / "pipeline.json")
    source = tmp_path / "source"
    other_source = tmp_path / "other_source"
    source.mkdir()
    other_source.mkdir()
    project_dir = tmp_path / "project_a"
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    assert client.post("/api/project/create", json={"name": "A", "project_dir": str(project_dir)}).status_code == 200
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    pipeline = get_five_model_chain_flowchart()
    assert client.post(f"/api/flowchart/pipeline?source_dataset_path={source}", json=pipeline.model_dump()).status_code == 200

    restarted = create_app(project_dir=str(tmp_path / "workspaces"))
    again = TestClient(restarted, headers={"X-Vision-Token": restarted.state.api_token})
    assert again.get("/api/flowchart/pipeline/active", params={"source_dataset_path": str(source)}).json()["id"] == "five_model_chain"
    assert again.get("/api/flowchart/pipeline/active", params={"source_dataset_path": str(other_source)}).status_code == 404
    assert again.put("/api/project/update", json={"source_dataset_dir": str(other_source)}).status_code == 200
    assert again.get("/api/flowchart/pipeline/active", params={"source_dataset_path": str(other_source)}).status_code == 404

    assert again.post("/api/project/create", json={"name": "B", "project_dir": str(tmp_path / "project_b")}).status_code == 200
    assert again.get("/api/flowchart/pipeline/active").status_code == 404


def test_legacy_roi_flow_does_not_replace_detector_only_default(monkeypatch, tmp_path):
    legacy = tmp_path / "pipeline.json"
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", legacy)
    legacy.write_text(get_default_flowchart().model_dump_json(), encoding="utf-8")

    assert routes_flowchart.get_pipeline(inspection_task="detection").id == "single_detection"


@pytest.mark.parametrize("rois, verdict, count", [
    ([{"id": "crop_1", "label": "defect", "bbox": [2, 3, 20, 22], "confidence": 0.91}], "NG", 1),
    ([], "OK", 0),
])
def test_detector_only_flow_uses_defect_boxes_without_second_model(monkeypatch, rois, verdict, count):
    pipeline = get_single_detection_flowchart("job_detector")
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_extract_candidate_rois", lambda *_: (rois, 1.0, "passed"))
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **_: pytest.fail("second model must not run"))

    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 40, 3), dtype=np.uint8))

    assert result["final_verdict"] == verdict
    assert result["roi_count"] == count
    assert result["defective_roi_count"] == count
    assert len(result["execution_steps"]) == 4
    if rois:
        assert result["crops"][0]["crop_thumbnail"].startswith("data:image/png;base64,")


def test_detection_recipe_opens_detector_only_template(monkeypatch, tmp_path):
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "pipeline.json")
    default = routes_flowchart.get_pipeline(inspection_task="detection")
    assert default.id == "single_detection"
    assert [node.data.node_type for node in default.nodes] == [
        "input", "detection_crop", "decision", "output",
    ]
    selected = routes_flowchart.get_single_detection_template(job_id="job_123_abc123")
    assert selected.nodes[1].data.model_job_id == "job_123_abc123"


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
    key = engine._cache_key("segmentation", job_id, "fast", checkpoint)
    assert engine._model_input_sizes[key] == (256, 256)


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
