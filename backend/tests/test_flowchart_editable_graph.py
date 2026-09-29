"""Execution and validation contracts for editable Stage 5 graphs."""

import numpy as np
import pytest
import torch

from backend.engine import flowchart_engine as flowchart_module
from backend.engine.flowchart_engine import (
    CropInspectionResult,
    FlowEdge,
    FlowNode,
    FlowNodeData,
    FlowchartEngine,
    FlowchartPipeline,
    get_single_segmentation_flowchart,
    get_default_flowchart,
    ordered_linear_nodes,
)


def _node(node_id, node_type, *, task=None):
    return FlowNode(
        id=node_id, position={"x": 0, "y": 0},
        data=FlowNodeData(label=node_id, node_type=node_type, task=task,
                          model_job_id=f"job_{node_id}" if node_type == "inspection" else None),
    )


def _branched_graph():
    nodes = [
        _node("input", "input"),
        _node("inspect_a", "inspection", task="segmentation"),
        _node("inspect_b", "inspection", task="segmentation"),
        _node("decision", "decision"),
        _node("ok", "output"),
        _node("ng", "output"),
        _node("review", "output"),
    ]
    edges = [
        FlowEdge(id="a", source="input", target="inspect_a"),
        FlowEdge(id="b", source="input", target="inspect_b"),
        FlowEdge(id="c", source="inspect_a", target="decision"),
        FlowEdge(id="d", source="inspect_b", target="decision"),
        FlowEdge(id="e", source="decision", target="ok", isBranch="pass"),
        FlowEdge(id="f", source="decision", target="ng", isBranch="fail"),
        FlowEdge(id="g", source="decision", target="review", isBranch="review"),
    ]
    return FlowchartPipeline(id="branched", name="branched", nodes=nodes, edges=edges)


def test_branch_graph_orders_models_and_round_trips_branch_labels():
    pipeline = _branched_graph()
    order = ordered_linear_nodes(pipeline)
    assert [node.id for node in order[:4]] == ["input", "inspect_a", "inspect_b", "decision"]
    saved = FlowchartPipeline.model_validate_json(pipeline.model_dump_json())
    assert {edge.isBranch for edge in saved.edges if edge.source == "decision"} == {"pass", "fail", "review"}


def test_branches_execute_both_models_and_only_selected_output(monkeypatch):
    pipeline = _branched_graph()
    engine = FlowchartEngine(device="cpu")
    inspected = []

    def inspect(image, rois, node):
        inspected.append(node.id)
        verdict = "NG" if node.id == "inspect_b" else "OK"
        return [CropInspectionResult(
            roi_id=f"{node.id}_full_image", label=node.id, bbox=[0, 0, 40, 32],
            defect_score=0.9 if verdict == "NG" else 0.1, verdict=verdict,
            crop_thumbnail="data:image/png;base64,AA==", flaw_type="test",
        )], 1.0, "passed"

    monkeypatch.setattr(engine, "_inspect_crops", inspect)
    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 40, 3), dtype=np.uint8))

    assert inspected == ["inspect_a", "inspect_b"]
    assert result["final_verdict"] == "NG"
    assert result["routed_output_node_id"] == "ng"
    assert [step["status"] for step in result["execution_steps"][-3:]] == ["skipped", "flagged_ng", "skipped"]
    assert result["defective_roi_count"] == 1


@pytest.mark.parametrize("mutation, reason", [
    (lambda p: p.edges.append(FlowEdge(id="cycle", source="decision", target="inspect_a")), "decision|edge|upstream"),
    (lambda p: p.edges[3].__setattr__("target", "inspect_a"), "connected|decision|incoming|edge"),
    (lambda p: p.edges[5].__setattr__("isBranch", "pass"), "branch|pass|fail"),
])
def test_invalid_graph_connections_rejected(mutation, reason):
    pipeline = _branched_graph()
    mutation(pipeline)
    with pytest.raises(ValueError, match=reason):
        ordered_linear_nodes(pipeline)


def test_existing_linear_graph_still_executes(monkeypatch):
    pipeline = get_single_segmentation_flowchart("job_segment")
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_inspect_crops", lambda image, rois, node: ([
        CropInspectionResult(roi_id="full_image", label="full", bbox=[0, 0, 40, 32],
                             defect_score=0.1, verdict="OK", crop_thumbnail="data:image/png;base64,AA==",
                             flaw_type="test")], 1.0, "passed"))

    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 40, 3), dtype=np.uint8))
    assert result["final_verdict"] == "OK"
    assert result["routed_output_node_id"] == "node_output"


def test_unknown_decision_rule_is_rejected_before_model_execution():
    pipeline = _branched_graph()
    next(node for node in pipeline.nodes if node.data.node_type == "decision").data.rule = "unknown_rule"
    with pytest.raises(ValueError, match="rule"):
        ordered_linear_nodes(pipeline)


def test_invalid_model_threshold_is_rejected_before_inference():
    pipeline = _branched_graph()
    next(node for node in pipeline.nodes if node.id == "inspect_a").data.threshold = 1.5
    with pytest.raises(ValueError, match="threshold"):
        ordered_linear_nodes(pipeline)


@pytest.mark.parametrize("classes, head_size", [
    (["defect"], 2),
    (["scratch", "chip"], 3),
    (["background", "defect"], 2),
])
def test_detection_checkpoint_head_includes_background_and_keeps_single_class_compatibility(
    tmp_path, monkeypatch, classes, head_size,
):
    checkpoint = tmp_path / "best_model.pt"
    torch.save({
        "task": "detection", "preset": "fast", "classes": classes,
        "model_state_dict": torch.nn.Linear(1, head_size).state_dict(),
    }, checkpoint)
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_resolve_checkpoint", lambda *_: checkpoint)
    monkeypatch.setattr(flowchart_module, "create_detection_model", lambda **kwargs: torch.nn.Linear(1, kwargs["num_classes"]))

    loaded, trained = engine._get_detection_model("job_detector")

    assert trained is True
    assert loaded.out_features == head_size
    assert engine._model_classes[("detection", "job_detector", "fast")] == [
        name for name in classes if name != "background"
    ]


def test_stage5_detection_boxes_keep_trained_class_names(monkeypatch):
    class FakeDetector(torch.nn.Module):
        def forward(self, images):
            return [{
                "boxes": torch.tensor([[1., 2., 10., 12.], [12., 2., 20., 12.]]),
                "scores": torch.tensor([0.95, 0.85]),
                "labels": torch.tensor([1, 2]),
            }]

    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_detection_model", lambda **_: (FakeDetector(), True))
    engine._model_classes[("detection", "job_detector", "fast")] = ["scratch", "chip"]
    node = _node("detector", "detection_crop")
    node.data.model_job_id = "job_detector"
    node.data.threshold = 0.5

    rois, _, _ = engine._extract_candidate_rois(np.zeros((32, 40, 3), dtype=np.uint8), node)

    assert [roi["label"] for roi in rois] == ["scratch", "chip"]


def test_classification_sums_all_defect_class_probabilities(monkeypatch):
    class ThreeClassModel(torch.nn.Module):
        def forward(self, images):
            return torch.tensor([[0.0, 0.0, 5.0]], device=images.device)

    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **_: (ThreeClassModel(), True))
    engine._model_classes[("classification", "job_classifier", "fast")] = ["OK", "scratch", "chip"]
    node = _node("classifier", "inspection", task="classification")
    node.data.model_job_id = "job_classifier"
    node.data.threshold = 0.5

    crops, _, _ = engine._inspect_crops(
        np.zeros((32, 40, 3), dtype=np.uint8),
        [{"id": "full_image", "label": "full", "bbox": [0, 0, 40, 32]}], node,
    )

    assert crops[0].verdict == "NG"
    assert crops[0].defect_score > 0.9


def test_classification_without_normal_class_returns_review(monkeypatch):
    class TwoClassModel(torch.nn.Module):
        def forward(self, images):
            return torch.tensor([[0.0, 3.0]], device=images.device)

    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **_: (TwoClassModel(), True))
    engine._model_classes[("classification", "job_classifier", "fast")] = ["scratch", "chip"]
    pipeline = get_single_segmentation_flowchart("job_classifier")
    pipeline.nodes[1].data.task = "classification"

    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 40, 3), dtype=np.uint8))

    assert result["final_verdict"] == "REVIEW"
    assert "normal" in result["rejection_reason"].lower() or "OK" in result["rejection_reason"]


def test_detector_padding_controls_downstream_roi_crop(monkeypatch):
    class TwoClassModel(torch.nn.Module):
        def forward(self, images):
            return torch.tensor([[5.0, 0.0]], device=images.device)

    pipeline = get_default_flowchart()
    detector = next(node for node in pipeline.nodes if node.data.node_type == "detection_crop")
    inspector = next(node for node in pipeline.nodes if node.data.node_type == "inspection")
    detector.data.crop_padding = 7
    inspector.data.crop_padding = 0
    inspector.data.task = "classification"
    inspector.data.model_job_id = "job_classify"
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_extract_candidate_rois", lambda *_: ([
        {"id": "roi_1", "label": "part", "bbox": [10, 10, 20, 20], "confidence": 0.9}
    ], 1.0, "passed"))
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **_: (TwoClassModel(), True))
    engine._model_classes[("classification", "job_classify", "fast")] = ["OK", "defect"]

    result = engine.execute(pipeline=pipeline, image=np.zeros((40, 40, 3), dtype=np.uint8))

    assert result["crops"][0]["bbox"] == [3, 3, 27, 27]


def test_negative_detector_padding_is_rejected():
    pipeline = get_default_flowchart()
    next(node for node in pipeline.nodes if node.data.node_type == "detection_crop").data.crop_padding = -1
    with pytest.raises(ValueError, match="padding"):
        ordered_linear_nodes(pipeline)


def test_segmentation_rejects_region_count_rule_that_can_under_count_defects():
    pipeline = get_single_segmentation_flowchart("job_segmentation")
    next(node for node in pipeline.nodes if node.data.node_type == "decision").data.rule = "max_flaws_allowed"
    with pytest.raises(ValueError, match="max_flaws_allowed|segmentation"):
        ordered_linear_nodes(pipeline)
