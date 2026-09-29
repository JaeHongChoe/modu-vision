"""Stage 5 combines parallel segmentation and patch evidence explicitly."""

import numpy as np

from backend.engine.flowchart_engine import (
    CropInspectionResult, FlowEdge, FlowNode, FlowNodeData, FlowchartEngine,
    FlowchartPipeline, ordered_linear_nodes,
)


def _node(node_id, node_type, *, task=None, rule=None, params=None):
    return FlowNode(
        id=node_id, position={"x": 0, "y": 0},
        data=FlowNodeData(label=node_id, node_type=node_type, task=task,
                          model_job_id=f"job_{node_id}" if node_type == "inspection" else None,
                          rule=rule, params=params or {}),
    )


def _pipeline():
    nodes = [
        _node("input", "input"),
        _node("roi", "fixed_roi", params={"roi_bbox": [0, 0, 32, 32]}),
        _node("seg", "inspection", task="segmentation"),
        _node("patch", "inspection", task="patch_classification"),
        _node("blob", "blob_measure", params={"min_blob_area_px": 2, "min_blob_count_for_ng": 2}),
        _node("combine", "aggregate", rule="all_ng"),
        _node("decision", "decision", rule="aggregate_verdict"),
        _node("output", "output"),
    ]
    edges = [
        FlowEdge(id="e1", source="input", target="roi", payload_type="image"),
        FlowEdge(id="e2", source="roi", target="seg", payload_type="roi"),
        FlowEdge(id="e3", source="roi", target="patch", payload_type="roi"),
        FlowEdge(id="e4", source="seg", target="blob", payload_type="result"),
        FlowEdge(id="e5", source="blob", target="combine", payload_type="result"),
        FlowEdge(id="e6", source="patch", target="combine", payload_type="result"),
        FlowEdge(id="e7", source="combine", target="decision", payload_type="result"),
        FlowEdge(id="e8", source="decision", target="output", payload_type="result"),
    ]
    return FlowchartPipeline(id="parallel_aggregate", name="ROI seg+patch", nodes=nodes, edges=edges)


def test_blob_count_and_all_ng_aggregate_preserve_both_branch_evidence(monkeypatch):
    pipeline = _pipeline()
    assert ordered_linear_nodes(pipeline)
    engine = FlowchartEngine(device="cpu")

    def inspect(_image, _rois, node):
        result = CropInspectionResult(
            roi_id=node.id, label=node.id, bbox=[0, 0, 32, 32],
            defect_score=0.9 if node.id == "seg" else 0.1,
            verdict="NG" if node.id == "seg" else "OK",
            crop_thumbnail="", flaw_type="fixture",
        )
        if node.id == "seg":
            mask = np.zeros((32, 32), dtype=np.uint8)
            mask[2:5, 2:5] = 1
            mask[12:15, 12:15] = 1
            result._defect_mask = mask
        return [result], 1.0, "passed"

    monkeypatch.setattr(engine, "_inspect_crops", inspect)
    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 32, 3), dtype=np.uint8))
    assert result["final_verdict"] == "OK"  # all_ng requires both branches to fail
    assert len(result["crops"]) == 2
    assert next(crop for crop in result["crops"] if crop["label"] == "seg")["blob_count"] == 2
    assert next(step for step in result["execution_steps"] if step["node_id"] == "combine")["branch_verdict"] == "OK"


def test_aggregate_requires_every_configured_branch_before_issuing_verdict(monkeypatch):
    pipeline = _pipeline()
    next(edge for edge in pipeline.edges if edge.source == "patch" and edge.target == "combine").isBranch = "fail"
    engine = FlowchartEngine(device="cpu")

    def inspect(_image, _rois, node):
        result = CropInspectionResult(
            roi_id=node.id, label=node.id, bbox=[0, 0, 32, 32],
            defect_score=0.9 if node.id == "seg" else 0.1,
            verdict="NG" if node.id == "seg" else "OK",
            crop_thumbnail="", flaw_type="fixture",
        )
        if node.id == "seg":
            result._defect_mask = np.ones((32, 32), dtype=np.uint8)
        return [result], 1.0, "passed"

    monkeypatch.setattr(engine, "_inspect_crops", inspect)
    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 32, 3), dtype=np.uint8))
    assert result["final_verdict"] == "REVIEW"
    assert "missing" in result["rejection_reason"].lower()


def test_blob_area_uses_source_image_pixels_after_model_resize(monkeypatch):
    pipeline = _pipeline()
    blob = next(node for node in pipeline.nodes if node.id == "blob")
    blob.data.params = {"min_blob_area_px": 3, "min_blob_count_for_ng": 1}
    engine = FlowchartEngine(device="cpu")

    def inspect(_image, _rois, node):
        result = CropInspectionResult(
            roi_id=node.id, label=node.id, bbox=[0, 0, 32, 32],
            defect_score=0.9, verdict="NG", crop_thumbnail="", flaw_type="fixture",
        )
        if node.id == "seg":
            mask = np.zeros((16, 16), dtype=np.uint8)
            mask[2, 2] = 1
            result._defect_mask = mask
        return [result], 1.0, "passed"

    monkeypatch.setattr(engine, "_inspect_crops", inspect)
    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 32, 3), dtype=np.uint8))
    crop = next(crop for crop in result["crops"] if crop["label"] == "seg")
    assert crop["blob_count"] == 1
    assert crop["largest_blob_area_px"] == 4


def test_aggregate_keeps_unique_roi_identity_from_parallel_branches(monkeypatch):
    pipeline = _pipeline()
    engine = FlowchartEngine(device="cpu")

    def inspect(_image, _rois, node):
        crop = CropInspectionResult(
            roi_id="same_roi", label=node.id, bbox=[0, 0, 32, 32],
            defect_score=0.9, verdict="NG", crop_thumbnail="", flaw_type="fixture",
        )
        if node.id == "seg":
            crop._defect_mask = np.ones((32, 32), dtype=np.uint8)
        return [crop], 1.0, "passed"

    monkeypatch.setattr(engine, "_inspect_crops", inspect)
    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 32, 3), dtype=np.uint8))
    assert {crop["roi_id"] for crop in result["crops"]} == {"blob:same_roi", "patch:same_roi"}
    assert {crop["source_node_id"] for crop in result["crops"]} == {"blob", "patch"}


def test_imported_untyped_measurement_edges_recover_result_payload(monkeypatch):
    pipeline = _pipeline()
    for edge in pipeline.edges:
        if edge.target in {"blob", "combine"}:
            edge.payload_type = None
    engine = FlowchartEngine(device="cpu")

    def inspect(_image, _rois, node):
        crop = CropInspectionResult(
            roi_id=node.id, label=node.id, bbox=[0, 0, 32, 32],
            defect_score=0.2, verdict="OK", crop_thumbnail="", flaw_type="fixture",
        )
        if node.id == "seg":
            crop._defect_mask = np.zeros((32, 32), dtype=np.uint8)
        return [crop], 1.0, "passed"

    monkeypatch.setattr(engine, "_inspect_crops", inspect)
    result = engine.execute(pipeline=pipeline, image=np.zeros((32, 32, 3), dtype=np.uint8))
    steps = {step["node_id"]: step for step in result["execution_steps"]}
    assert steps["seg"]["output_payload_type"] == "result"
    assert steps["blob"]["output_payload_type"] == "result"
