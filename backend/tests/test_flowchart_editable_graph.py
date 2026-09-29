"""Execution and validation contracts for editable Stage 5 graphs."""

import numpy as np
import pytest
import torch
import cv2

from backend.engine import flowchart_engine as flowchart_module
from backend.engine.flowchart_engine import (
    CropInspectionResult,
    FlowEdge,
    FlowNode,
    FlowNodeData,
    FlowchartEngine,
    FlowchartInspectionLimitError,
    FlowchartPipeline,
    get_single_segmentation_flowchart,
    get_five_model_chain_flowchart,
    get_conditional_inspection_flowchart,
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
    key = engine._cache_key("detection", "job_detector", "fast", checkpoint)
    assert engine._model_classes[key] == [
        name for name in classes if name != "background"
    ]


def test_model_cache_reloads_same_job_id_from_another_project(tmp_path, monkeypatch):
    first_path = tmp_path / "project_a" / "best_model.pt"
    second_path = tmp_path / "project_b" / "best_model.pt"
    for path, weight in ((first_path, 1.0), (second_path, 2.0)):
        path.parent.mkdir()
        model = torch.nn.Linear(1, 2)
        with torch.no_grad():
            model.weight.fill_(weight)
        torch.save({
            "task": "detection", "preset": "fast", "classes": ["defect"],
            "model_state_dict": model.state_dict(),
        }, path)

    current = [first_path]
    engine = FlowchartEngine(device="cpu", checkpoint_resolver=lambda *_: current[0])
    monkeypatch.setattr(flowchart_module, "create_detection_model", lambda **_: torch.nn.Linear(1, 2))

    first, first_trained = engine._get_detection_model("job_shared")
    current[0] = second_path
    second, second_trained = engine._get_detection_model("job_shared")

    assert first_trained and second_trained
    assert second is not first
    assert float(first.weight[0, 0]) == 1.0
    assert float(second.weight[0, 0]) == 2.0

    replacement = torch.nn.Linear(1, 2)
    with torch.no_grad():
        replacement.weight.fill_(3.0)
    torch.save({
        "task": "detection", "preset": "fast", "classes": ["defect"],
        "model_state_dict": replacement.state_dict(),
    }, second_path)
    third, third_trained = engine._get_detection_model("job_shared")
    assert third_trained and third is not second
    assert float(third.weight[0, 0]) == 3.0


def test_verified_checkpoint_scope_pins_exact_job_and_task(tmp_path):
    first = tmp_path / "first.pt"
    second = tmp_path / "second.pt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    engine = FlowchartEngine(device="cpu", checkpoint_resolver=lambda *_: first)
    assert engine._resolve_checkpoint("job_shared", "detection") == first

    with flowchart_module.verified_checkpoint_scope({("job_shared", "detection"): second}):
        assert engine._resolve_checkpoint("job_shared", "detection") == second
        with pytest.raises(flowchart_module.FlowchartInspectionConfigurationError, match="verified"):
            engine._resolve_checkpoint("job_shared", "segmentation")

    assert engine._resolve_checkpoint("job_shared", "detection") == first


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


def _chain_graph(*, gated=False):
    nodes = [
        _node("input", "input"),
        _node("detect", "detection_crop", task="detection"),
        _node("inspect_a", "inspection", task="classification"),
        _node("inspect_b", "inspection", task="segmentation"),
        _node("decision", "decision"),
        _node("output", "output"),
    ]
    nodes[1].data.model_job_id = "job_detect"
    edges = [
        FlowEdge(id="input-detect", source="input", target="detect"),
        FlowEdge(id="detect-a", source="detect", target="inspect_a", payload_type="roi"),
        FlowEdge(id="a-b", source="inspect_a", target="inspect_b", payload_type="roi",
                 isBranch="fail" if gated else None),
        FlowEdge(id="b-decision", source="inspect_b", target="decision"),
        FlowEdge(id="decision-output", source="decision", target="output"),
    ]
    return FlowchartPipeline(id="chain", name="chain", nodes=nodes, edges=edges)


def test_sequential_models_pass_detector_rois_to_each_inspector(monkeypatch):
    pipeline = _chain_graph()
    assert [node.id for node in ordered_linear_nodes(pipeline)] == [
        "input", "detect", "inspect_a", "inspect_b", "decision", "output",
    ]
    assert FlowchartPipeline.model_validate_json(pipeline.model_dump_json()).edges[2].payload_type == "roi"
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_extract_candidate_rois", lambda *_: ([
        {"id": "part_1", "label": "part", "bbox": [10, 12, 30, 32], "confidence": 0.9},
    ], 1.0, "passed"))
    received = []

    def inspect(image, rois, node):
        received.append((node.id, [roi["bbox"] for roi in rois]))
        return [CropInspectionResult(
            roi_id="part_1", label="part", bbox=[10, 12, 30, 32],
            defect_score=0.9 if node.id == "inspect_a" else 0.1,
            verdict="NG" if node.id == "inspect_a" else "OK",
            crop_thumbnail="data:image/png;base64,AA==", flaw_type="test",
        )], 1.0, "passed"

    monkeypatch.setattr(engine, "_inspect_crops", inspect)
    result = engine.execute(pipeline=pipeline, image=np.zeros((40, 50, 3), dtype=np.uint8))

    assert received == [
        ("inspect_a", [[10, 12, 30, 32]]),
        ("inspect_b", [[10, 12, 30, 32]]),
    ]
    assert result["final_verdict"] == "OK"  # the terminal inspector provides decision evidence
    assert result["roi_count"] == 1
    assert result["execution_steps"][2]["output_count"] == 1
    assert result["execution_steps"][2]["branch_verdict"] == "NG"


@pytest.mark.parametrize("first_verdict,expected_node,expected_final", [
    ("NG", "inspect_fail", "NG"),
    ("OK", "inspect_pass", "OK"),
])
def test_inspection_branch_runs_only_matching_model(monkeypatch, first_verdict, expected_node, expected_final):
    pipeline = _branched_graph()
    pipeline.nodes[1].data.task = "classification"
    pipeline.nodes[2].id = "inspect_pass"
    pipeline.nodes.insert(3, _node("inspect_fail", "inspection", task="classification"))
    pipeline.edges = [
        FlowEdge(id="input-a", source="input", target="inspect_a"),
        FlowEdge(id="a-pass", source="inspect_a", target="inspect_pass", isBranch="pass"),
        FlowEdge(id="a-fail", source="inspect_a", target="inspect_fail", isBranch="fail"),
        FlowEdge(id="pass-decision", source="inspect_pass", target="decision"),
        FlowEdge(id="fail-decision", source="inspect_fail", target="decision"),
        *pipeline.edges[-3:],
    ]
    engine = FlowchartEngine(device="cpu")
    called = []

    def inspect(image, rois, node):
        called.append(node.id)
        verdict = first_verdict if node.id == "inspect_a" else first_verdict
        return [CropInspectionResult(
            roi_id="full_image", label=node.id, bbox=[0, 0, 40, 32],
            defect_score=0.9 if verdict == "NG" else 0.1, verdict=verdict,
            crop_thumbnail="data:image/png;base64,AA==", flaw_type="test",
        )], 1.0, "passed"

    monkeypatch.setattr(engine, "_inspect_crops", inspect)
    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 40, 3), dtype=np.uint8))

    assert called == ["inspect_a", expected_node]
    assert result["final_verdict"] == expected_final
    steps = {step["node_id"]: step for step in result["execution_steps"]}
    opposite = "inspect_pass" if expected_node == "inspect_fail" else "inspect_fail"
    assert steps[opposite]["status"] == "skipped"
    assert steps[opposite]["skip_reason"] == "condition_not_met"
    assert steps["inspect_a"]["selected_edge_ids"] == ["a-fail" if first_verdict == "NG" else "a-pass"]


def test_unconditional_result_edge_stays_active_alongside_conditional_branch(monkeypatch):
    pipeline = _chain_graph(gated=True)
    pipeline.edges.append(FlowEdge(id="a-decision", source="inspect_a", target="decision", payload_type="result"))
    engine = FlowchartEngine(device="cpu")

    def inspect(image, rois, node):
        verdict = "OK" if node.id == "inspect_a" else "NG"
        return [CropInspectionResult(
            roi_id="part_1", label="part", bbox=[0, 0, 40, 32],
            defect_score=0.1 if verdict == "OK" else 0.9, verdict=verdict,
            crop_thumbnail="data:image/png;base64,AA==", flaw_type="test",
        )], 1.0, "passed"

    monkeypatch.setattr(engine, "_extract_candidate_rois", lambda *_: ([
        {"id": "part_1", "label": "part", "bbox": [0, 0, 40, 32]},
    ], 1.0, "passed"))
    monkeypatch.setattr(engine, "_inspect_crops", inspect)
    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 40, 3), dtype=np.uint8))

    assert result["final_verdict"] == "OK"
    assert result["roi_count"] == 1
    steps = {step["node_id"]: step for step in result["execution_steps"]}
    assert steps["inspect_b"]["status"] == "skipped"
    assert steps["inspect_a"]["selected_edge_ids"] == ["a-decision"]


def test_typed_result_payload_cannot_feed_another_model():
    pipeline = _chain_graph()
    pipeline.edges[2].payload_type = "result"
    with pytest.raises(ValueError, match="payload|result"):
        ordered_linear_nodes(pipeline)


def test_unmatched_only_gate_returns_review_instead_of_ok(monkeypatch):
    pipeline = _chain_graph(gated=True)
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_extract_candidate_rois", lambda *_: ([
        {"id": "part_1", "label": "part", "bbox": [0, 0, 40, 32]},
    ], 1.0, "passed"))
    monkeypatch.setattr(engine, "_inspect_crops", lambda image, rois, node: ([CropInspectionResult(
        roi_id="part_1", label="part", bbox=[0, 0, 40, 32], defect_score=0.1,
        verdict="OK", crop_thumbnail="data:image/png;base64,AA==", flaw_type="test",
    )], 1.0, "passed"))

    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 40, 3), dtype=np.uint8))

    assert result["final_verdict"] == "REVIEW"
    assert result["roi_count"] == 0
    assert "route" in result["rejection_reason"].lower()


def test_detector_after_inspection_restores_nested_roi_coordinates(monkeypatch):
    pipeline = _chain_graph()
    pipeline.nodes[3].data.node_type = "detection_crop"
    pipeline.nodes[3].data.task = "detection"
    pipeline.nodes[3].data.model_job_id = "job_second_detector"
    engine = FlowchartEngine(device="cpu")
    seen_shapes = []

    def detect(image, node):
        seen_shapes.append((node.id, image.shape[:2]))
        box = [10, 12, 30, 32] if node.id == "detect" else [1, 2, 7, 8]
        return [{"id": "crop_1", "label": "defect", "bbox": box, "confidence": 0.95}], 1.0, "passed"

    monkeypatch.setattr(engine, "_extract_candidate_rois", detect)
    monkeypatch.setattr(engine, "_inspect_crops", lambda image, rois, node: ([CropInspectionResult(
        roi_id="crop_1", label="part", bbox=[10, 12, 30, 32], defect_score=0.1,
        verdict="OK", crop_thumbnail="data:image/png;base64,AA==", flaw_type="test",
    )], 1.0, "passed"))

    result = engine.execute(pipeline=pipeline, image=np.zeros((50, 60, 3), dtype=np.uint8))

    assert seen_shapes == [("detect", (50, 60)), ("inspect_b", (20, 20))]
    assert result["final_verdict"] == "NG"
    assert result["crops"][0]["bbox"] == [11, 14, 17, 20]


def test_partial_model_failure_requires_review_unless_policy_explicit(monkeypatch):
    pipeline = _branched_graph()
    engine = FlowchartEngine(device="cpu")

    def inspect(image, rois, node):
        if node.id == "inspect_b":
            raise FlowchartInspectionLimitError("too many tiles")
        return [CropInspectionResult(
            roi_id="full_image", label="defect", bbox=[0, 0, 40, 32],
            defect_score=0.9, verdict="NG",
            crop_thumbnail="data:image/png;base64,AA==", flaw_type="test",
        )], 1.0, "passed"

    monkeypatch.setattr(engine, "_inspect_crops", inspect)
    image = np.zeros((32, 40, 3), dtype=np.uint8)
    result = engine.execute(pipeline=pipeline, image=image)
    assert result["final_verdict"] == "REVIEW"
    assert result["routed_output_node_id"] == "review"
    assert "too many tiles" in result["rejection_reason"]

    decision = next(node for node in pipeline.nodes if node.data.node_type == "decision")
    decision.data.params["incomplete_policy"] = "ng"
    explicit = engine.execute(pipeline=pipeline, image=image)
    assert explicit["final_verdict"] == "NG"
    assert explicit["routed_output_node_id"] == "ng"


def test_multimodel_templates_are_executable_dags_without_bound_checkpoints():
    five = get_five_model_chain_flowchart()
    assert [node.data.task for node in ordered_linear_nodes(five)
            if node.data.node_type in ("inspection", "detection_crop")] == [
        "classification", "detection", "classification", "classification", "segmentation",
    ]
    assert all(node.data.model_job_id is None for node in five.nodes)
    conditional = get_conditional_inspection_flowchart()
    ordered_linear_nodes(conditional)
    first = next(node for node in conditional.nodes if node.id == "inspect_gate")
    assert {edge.isBranch for edge in conditional.edges if edge.source == first.id} == {
        "pass", "fail",
    }


@pytest.mark.parametrize("parameter,bad_value", [
    ("incomplete_policy", "ok"),
    ("review_fallback", "review-as-ok"),
])
def test_unknown_incomplete_route_policy_is_rejected(parameter, bad_value):
    pipeline = get_single_segmentation_flowchart()
    decision = next(node for node in pipeline.nodes if node.data.node_type == "decision")
    decision.data.params[parameter] = bad_value
    with pytest.raises(ValueError, match=parameter):
        ordered_linear_nodes(pipeline)


def test_packaged_runtime_can_resolve_its_verified_model_artifact(tmp_path, monkeypatch):
    checkpoint = tmp_path / "best_model.pt"
    torch.save({
        "task": "segmentation", "model_name": "unet", "classes": ["OK", "NG"],
        "model_state_dict": torch.nn.Linear(1, 2).state_dict(),
    }, checkpoint)
    monkeypatch.setattr(flowchart_module, "build_segmentation_model", lambda **_: torch.nn.Linear(1, 2))
    requested = []

    def resolve(job_id, task):
        requested.append((job_id, task))
        return checkpoint if job_id == "job_bundled" else None

    engine = FlowchartEngine(device="cpu", checkpoint_resolver=resolve)
    model, trained = engine._get_inspection_model("segmentation", "job_bundled")

    assert requested == [("job_bundled", "segmentation")]
    assert trained is True
    assert isinstance(model, torch.nn.Linear)


def _fixed_roi_graph(bbox):
    return FlowchartPipeline(
        id="fixed_roi", name="fixed_roi", nodes=[
            _node("input", "input"),
            FlowNode(id="fixed", position={"x": 100, "y": 0}, data=FlowNodeData(
                label="fixed", node_type="fixed_roi", params={"roi_bbox": bbox},
            )),
            _node("inspect", "inspection", task="classification"),
            _node("decision", "decision"),
            _node("output", "output"),
        ], edges=[
            FlowEdge(id="input-fixed", source="input", target="fixed", payload_type="image"),
            FlowEdge(id="fixed-inspect", source="fixed", target="inspect", payload_type="roi"),
            FlowEdge(id="inspect-decision", source="inspect", target="decision", payload_type="result"),
            FlowEdge(id="decision-output", source="decision", target="output", payload_type="result"),
        ],
    )


def test_fixed_roi_uses_original_image_pixels_and_maps_inspection_to_source(tmp_path, monkeypatch):
    class BrightRegionModel(torch.nn.Module):
        def forward(self, image):
            brightness = image.mean(dim=(1, 2, 3))
            return torch.stack((1 - brightness, brightness * 3), dim=1)

    image = np.zeros((1500, 2200, 3), dtype=np.uint8)
    image[800:1100, 1700:2000] = 255
    image_path = tmp_path / "full_resolution.png"
    assert cv2.imwrite(str(image_path), image)
    pipeline = _fixed_roi_graph([1700, 800, 2000, 1100])
    assert [node.id for node in ordered_linear_nodes(pipeline)] == [
        "input", "fixed", "inspect", "decision", "output",
    ]
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **_: (BrightRegionModel(), True))
    engine._model_classes[("classification", "job_inspect", "fast")] = ["OK", "defect"]

    result = engine.execute(pipeline=pipeline, image_path=str(image_path))

    assert result["inspected_image_size"] == [2200, 1500]
    assert result["final_verdict"] == "NG"
    assert result["roi_count"] == 1
    assert result["crops"][0]["bbox"] == [1700, 800, 2000, 1100]
    assert result["execution_steps"][1]["output_payload_type"] == "roi"
    assert result["execution_steps"][1]["output_count"] == 1


def test_fixed_roi_outside_image_requires_review_without_running_model(monkeypatch):
    pipeline = _fixed_roi_graph([2300, 800, 2500, 1100])
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **_: pytest.fail("model must not run"))

    result = engine.execute(pipeline=pipeline, image=np.zeros((1500, 2200, 3), dtype=np.uint8))

    assert result["final_verdict"] == "REVIEW"
    assert result["roi_count"] == 0
    assert result["execution_steps"][1]["output_count"] == 0
    assert result["execution_steps"][2]["skip_reason"] == "empty_roi"


@pytest.mark.parametrize("bbox", [
    [10, 20, 10, 40], [10, 20, 25, 24], [-1, 20, 40, 50],
    [10, 20, float("nan"), 60], [10, 20, 40], [10, 20, 40, "60"],
])
def test_fixed_roi_rejects_invalid_original_pixel_rectangle(bbox):
    with pytest.raises(ValueError, match="Fixed ROI rectangle"):
        ordered_linear_nodes(_fixed_roi_graph(bbox))


def test_fixed_roi_template_exposes_usable_inspection_chain():
    from backend.api import routes_flowchart

    pipeline = routes_flowchart.get_fixed_roi_template(
        inspection_task="classification", job_id="job_inspect",
    )

    assert [node.data.node_type for node in ordered_linear_nodes(pipeline)] == [
        "input", "fixed_roi", "inspection", "decision", "output",
    ]
    assert next(node for node in pipeline.nodes if node.id == "node_inspect").data.task == "classification"
    assert next(node for node in pipeline.nodes if node.id == "node_fixed_roi").data.params["roi_bbox"] == [0, 0, 512, 512]
