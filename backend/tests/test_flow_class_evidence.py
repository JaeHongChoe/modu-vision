"""Original-pixel class evidence and typed inspection policies."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import zlib

import numpy as np
import pytest
import torch

from backend.engine.flowchart_engine import (
    CropInspectionResult,
    FlowEdge,
    FlowNode,
    FlowNodeData,
    FlowchartEngine,
    get_single_segmentation_flowchart,
    ordered_linear_nodes,
)


class RasterSegmentation(torch.nn.Module):
    """Two foreground channels determined by actual image pixels."""

    def forward(self, image):
        logits = torch.full((len(image), 3, *image.shape[-2:]), -12.0)
        logits[:, 0] = 8
        logits[:, 1][image[:, 1] > 0.7] = 24
        logits[:, 2][image[:, 0] > 0.7] = 24
        return logits.to(image.device)


def pipeline(params=None, *, blob=None, roi=None):
    graph = get_single_segmentation_flowchart("job_raster")
    node = next(n for n in graph.nodes if n.data.node_type == "inspection")
    node.data.params = {
        "min_defect_area_px": 1,
        "class_names": ["background", "pad", "crack"],
        **(params or {}),
    }
    node.data.crop_padding = 0
    if roi:
        fixed = FlowNode(
            id="roi",
            position={},
            data=FlowNodeData(
                label="ROI", node_type="fixed_roi", params={"roi_bbox": roi}
            ),
        )
        graph.nodes.insert(1, fixed)
        first = graph.edges[0]
        graph.edges[0] = first.model_copy(update={"target": "roi"})
        graph.edges.insert(1, FlowEdge(id="roi-model", source="roi", target=node.id))
    if blob is not None:
        measure = FlowNode(
            id="blob",
            position={},
            data=FlowNodeData(label="Measure", node_type="blob_measure", params=blob),
        )
        graph.nodes.insert(-2, measure)
        model_edge = next(e for e in graph.edges if e.source == node.id)
        graph.edges.remove(model_edge)
        graph.edges.extend(
            [
                FlowEdge(id="model-blob", source=node.id, target="blob"),
                FlowEdge(id="blob-decision", source="blob", target=model_edge.target),
            ]
        )
    return graph


def engine(monkeypatch):
    instance = FlowchartEngine(device="cpu")
    monkeypatch.setattr(
        instance, "_get_inspection_model", lambda **_: (RasterSegmentation(), True)
    )
    instance._model_input_sizes[("segmentation", "job_raster", "fast")] = (64, 64)
    return instance


def decode(values):
    return np.frombuffer(
        zlib.decompress(base64.b64decode(values["data"])), dtype=values["dtype"]
    ).reshape(values["shape"])


def test_second_class_survives_tiles_serialization_and_node_trace(monkeypatch):
    image = np.zeros((64, 96, 3), np.uint8)
    image[16:24, 76:84, 0] = 255
    result = engine(monkeypatch).execute(pipeline=pipeline(), image=image)
    assert result["final_verdict"] == "NG"
    crop = result["crops"][0]
    assert crop["predicted_class"] == "crack"
    assert crop["defect_area_px"] == 64
    classes = {row["class_id"]: row for row in crop["segmentation_classes"]}
    assert classes[1]["area_px"] == 0
    assert classes[2]["class_name"] == "crack"
    assert classes[2]["bbox"] == [0, 0, 96, 64]
    assert decode(classes[2]["mask"]).sum() == 64
    assert decode(classes[2]["probability"])[20, 80] > 0.99
    assert decode(classes[2]["mask"]).shape == (64, 96)
    saved = json.loads(json.dumps(result))
    step = next(s for s in saved["execution_steps"] if s["node_id"] == "node_inspect")
    assert (
        next(
            r
            for r in step["artifacts"][0]["evidence"]["segmentation_classes"]
            if r["class_id"] == 2
        )["area_px"]
        == 64
    )


def test_roi_class_mask_uses_original_pixels_and_keeps_both_classes(monkeypatch):
    image = np.zeros((64, 64, 3), np.uint8)
    image[16:24, 20:28, 0] = 255
    image[24:32, 28:36, 1] = 255
    result = engine(monkeypatch).execute(
        pipeline=pipeline(roi=[16, 8, 48, 40]), image=image
    )
    crop = result["crops"][0]
    assert crop["defect_area_px"] == 128
    assert crop["bbox"] == [16, 8, 48, 40]
    classes = {r["class_id"]: r for r in crop["segmentation_classes"]}
    assert classes[1]["area_px"] == 64
    assert classes[2]["area_px"] == 64
    mask = decode(classes[2]["mask"])
    assert mask.shape == (32, 32)
    assert mask[8:16, 4:12].sum() == 64


def test_background_only_is_measured_zero_without_invented_defect(monkeypatch):
    result = engine(monkeypatch).execute(
        pipeline=pipeline(), image=np.zeros((32, 32, 3), np.uint8)
    )
    crop = result["crops"][0]
    assert result["final_verdict"] == "OK"
    assert crop["defect_area_px"] == 0
    assert all(
        r["area_px"] == 0 for r in crop["segmentation_classes"] if r["class_id"] > 0
    )
    background = next(r for r in crop["segmentation_classes"] if r["class_id"] == 0)
    assert background["area_px"] == 32 * 32
    assert background["selected"] is False
    assert decode(background["probability"]).shape == (32, 32)


def test_legacy_binary_without_recorded_classes_keeps_defect_label(monkeypatch):
    class BinarySegmentation(RasterSegmentation):
        def forward(self, image):
            return super().forward(image)[:, [0, 2]]

    instance = engine(monkeypatch)
    monkeypatch.setattr(
        instance, "_get_inspection_model", lambda **_: (BinarySegmentation(), True)
    )
    image = np.zeros((32, 32, 3), np.uint8)
    image[4:12, 4:12, 0] = 255
    graph = get_single_segmentation_flowchart("job_raster")
    result = instance.execute(pipeline=graph, image=image)
    assert result["final_verdict"] == "NG"
    assert result["crops"][0]["predicted_class"] == "defect"


def test_per_class_probability_and_component_size_filters(monkeypatch):
    image = np.zeros((64, 64, 3), np.uint8)
    image[4:6, 4:6, 0] = 255
    image[20:24, 20:24, 0] = 255
    image[40:48, 40:48, 0] = 255
    rules = [
        {
            "class_id": 2,
            "probability_threshold": 0.99,
            "min_area_px": 8,
            "max_area_px": 32,
        }
    ]
    result = engine(monkeypatch).execute(
        pipeline=pipeline({"class_ids": [2], "class_rules": rules}), image=image
    )
    classes = {r["class_id"]: r for r in result["crops"][0]["segmentation_classes"]}
    assert result["final_verdict"] == "NG"
    assert classes[2]["area_px"] == 16
    assert decode(classes[2]["mask"]).sum() == 16


def test_required_class_missing_is_ng_but_unknown_mask_is_review(monkeypatch):
    params = {
        "rule_mode": "required_structure",
        "class_rules": [
            {
                "class_id": 2,
                "min_count": 1,
                "max_count": 2,
                "min_area_px": 4,
                "max_area_px": 100,
            }
        ],
    }
    result = engine(monkeypatch).execute(
        pipeline=pipeline(blob=params), image=np.zeros((32, 32, 3), np.uint8)
    )
    assert result["final_verdict"] == "NG"
    crop = result["crops"][0]
    assert crop["blob_count"] == 0
    assert crop["blob_measurements"][0]["evidence_present"] is True
    instance = engine(monkeypatch)

    def no_mask(*_):
        return (
            [
                CropInspectionResult(
                    roi_id="full_image",
                    label="Image",
                    bbox=[0, 0, 32, 32],
                    defect_score=0,
                    verdict="OK",
                    crop_thumbnail="",
                    flaw_type="fixture",
                )
            ],
            0,
            "passed",
        )

    monkeypatch.setattr(instance, "_inspect_crops", no_mask)
    missing = instance.execute(
        pipeline=pipeline(blob=params), image=np.zeros((32, 32, 3), np.uint8)
    )
    assert missing["final_verdict"] == "REVIEW"


@pytest.mark.parametrize("value,verdict", [(80, "NG"), (140, "OK"), (220, "NG")])
def test_required_structure_gray_bounds_use_original_mask_pixels(
    monkeypatch, value, verdict
):
    params = {
        "rule_mode": "required_structure",
        "class_rules": [
            {
                "class_id": 1,
                "min_count": 1,
                "max_count": 1,
                "min_area_px": 4,
                "max_area_px": 16,
                "min_mean_grayscale": 100,
                "max_mean_grayscale": 180,
            }
        ],
    }
    instance = engine(monkeypatch)

    def evidence(*_):
        crop = CropInspectionResult(
            roi_id="full_image",
            label="part",
            bbox=[0, 0, 16, 16],
            defect_score=1,
            verdict="NG",
            crop_thumbnail="",
            flaw_type="fixture",
            segmentation_classes=[{"class_id": 1, "class_name": "pad"}],
        )
        crop._defect_mask = np.pad(np.ones((2, 2), np.uint8), ((0, 14), (0, 14)))
        return [crop], 0, "passed"

    monkeypatch.setattr(instance, "_inspect_crops", evidence)
    result = instance.execute(
        pipeline=pipeline(blob=params), image=np.full((16, 16, 3), value, np.uint8)
    )
    assert result["final_verdict"] == verdict
    assert result["crops"][0]["blob_measurements"][0]["mean_grayscale"] == value


@pytest.mark.parametrize(
    "blob",
    [
        {"rule_mode": "invented"},
        {
            "rule_mode": "required_structure",
            "class_rules": [{"class_id": 1, "min_count": 2, "max_count": 1}],
        },
        {"class_rules": [{"class_id": True}]},
        {"class_rules": [{"class_id": 1, "min_mean_grayscale": float("nan")}]},
        {"class_rules": [{"class_id": 1, "min_mean_grayscale": None}]},
    ],
)
def test_bad_blob_rules_rejected_before_inference(blob):
    with pytest.raises(ValueError):
        ordered_linear_nodes(pipeline(blob=blob))


def test_defect_presence_zero_minimum_never_invents_a_measured_defect(monkeypatch):
    result = engine(monkeypatch).execute(
        pipeline=pipeline(blob={"class_rules": [{"class_id": 2, "min_count": 0}]}),
        image=np.zeros((32, 32, 3), np.uint8),
    )
    assert result["final_verdict"] == "OK"
    assert result["crops"][0]["blob_count"] == 0


def ocr_pipeline(params):
    graph = get_single_segmentation_flowchart("job_ocr")
    graph.nodes[1].data.task = "ocr"
    graph.nodes[1].data.params = params
    return graph


def test_ocr_correction_and_position_rules_preserve_original(monkeypatch, tmp_path):
    checkpoint = tmp_path / "model.pt"
    checkpoint.write_bytes(b"fixture")
    instance = FlowchartEngine(device="cpu", checkpoint_resolver=lambda *_: checkpoint)
    from backend.engine import ocr

    monkeypatch.setattr(
        ocr,
        "predict_ocr_array",
        lambda *_args, **_kwargs: {"text": "A0B", "confidence": 0.9},
    )
    graph = ocr_pipeline(
        {
            "expected_text": "AOB",
            "correction_map": {"0": "O"},
            "position_rules": [{"index": 1, "allowed_chars": "OX", "fixed_char": "O"}],
        }
    )
    result = instance.execute(pipeline=graph, image=np.zeros((32, 32, 3), np.uint8))
    assert result["final_verdict"] == "OK"
    crop = result["crops"][0]
    assert crop["recognized_text"] == crop["original_text"] == "A0B"
    assert crop["corrected_text"] == "AOB"
    assert crop["correction_applied"] is True
    assert crop["rule_violations"] == []


def test_ocr_position_violation_fails_even_when_regex_matches(monkeypatch, tmp_path):
    checkpoint = tmp_path / "model.pt"
    checkpoint.write_bytes(b"fixture")
    instance = FlowchartEngine(device="cpu", checkpoint_resolver=lambda *_: checkpoint)
    from backend.engine import ocr

    monkeypatch.setattr(
        ocr,
        "predict_ocr_array",
        lambda *_args, **_kwargs: {"text": "A0B", "confidence": 0.9},
    )
    result = instance.execute(
        pipeline=ocr_pipeline(
            {
                "regex": "[A-Z0-9]{3}",
                "position_rules": [{"index": 1, "fixed_char": "O"}],
            }
        ),
        image=np.zeros((32, 32, 3), np.uint8),
    )
    assert result["final_verdict"] == "NG"
    assert result["crops"][0]["rule_violations"][0]["index"] == 1


@pytest.mark.parametrize(
    "params",
    [
        {"expected_text": "A", "correction_map": {"0": "OO"}},
        {"expected_text": "A", "position_rules": [{"index": -1, "fixed_char": "A"}]},
        {
            "expected_text": "A",
            "position_rules": [{"index": True, "allowed_chars": "AB"}],
        },
    ],
)
def test_malformed_ocr_rules_rejected(params):
    with pytest.raises(ValueError):
        ordered_linear_nodes(ocr_pipeline(params))


def test_all_observed_segmentation_classes_can_select_result_branch(monkeypatch):
    image = np.zeros((64, 64, 3), np.uint8)
    image[4:12, 4:12, 0] = 255
    image[24:40, 24:40, 1] = 255
    graph = pipeline()
    model_edge = next(e for e in graph.edges if e.source == "node_inspect")
    model_edge.predicate = {
        "kind": "class",
        "operator": "present",
        "class_name": "crack",
        "min_confidence": 0.9,
    }
    result = engine(monkeypatch).execute(pipeline=graph, image=image)
    assert result["crops"][0]["predicted_class"] == "pad"
    assert result["final_verdict"] == "NG"
    step = next(s for s in result["execution_steps"] if s["node_id"] == "node_inspect")
    assert step["selected_edge_ids"] == [model_edge.id]


def test_rotated_class_rasters_return_to_original_source_pixels(monkeypatch):
    image = np.zeros((32, 48, 3), np.uint8)
    image[8:16, 20:28, 0] = 255
    graph = pipeline()
    rotation = FlowNode(
        id="rotate",
        position={},
        data=FlowNodeData(
            label="Rotate",
            node_type="preprocess",
            params={"operation": "rotate", "angle_deg": 90},
        ),
    )
    graph.nodes.insert(1, rotation)
    graph.edges[0] = graph.edges[0].model_copy(update={"target": "rotate"})
    graph.edges.insert(
        1, FlowEdge(id="rotate-model", source="rotate", target="node_inspect")
    )
    instance = engine(monkeypatch)
    # Native rotated frame dimensions avoid a model interpolation artifact.
    instance._model_input_sizes[("segmentation", "job_raster", "fast")] = (32, 48)
    result = instance.execute(pipeline=graph, image=image)
    crop = result["crops"][0]
    assert result["final_verdict"] == "NG"
    row = next(r for r in crop["segmentation_classes"] if r["class_id"] == 2)
    x1, y1, x2, y2 = row["bbox"]
    source = np.zeros(image.shape[:2], np.uint8)
    source[y1:y2, x1:x2] = decode(row["mask"])
    assert source.sum() == 64
    assert source[8:16, 20:28].sum() == 64
    assert row["area_px"] == crop["defect_area_px"] == 64
    assert row["mean_grayscale"] == 76
    assert decode(row["probability"]).shape == decode(row["mask"]).shape


def test_default_blob_counts_touching_classes_as_one_legacy_union(monkeypatch):
    image = np.zeros((64, 64, 3), np.uint8)
    image[8:16, 8:16, 0] = 255
    image[8:16, 16:24, 1] = 255
    result = engine(monkeypatch).execute(
        pipeline=pipeline(blob={"min_blob_count_for_ng": 2}), image=image
    )
    assert result["final_verdict"] == "OK"
    assert result["crops"][0]["blob_count"] == 1
    assert result["crops"][0]["largest_blob_area_px"] == 128


@pytest.mark.parametrize(
    "rule,verdict",
    [
        ({"min_count": 1, "max_count": 1}, "NG"),
        ({"min_count": 2, "max_count": 2, "min_area_px": 100}, "OK"),
        ({"min_count": 2, "max_count": 2, "max_area_px": 100}, "NG"),
    ],
)
def test_required_structure_bounds_measure_total_class_area(monkeypatch, rule, verdict):
    image = np.zeros((64, 64, 3), np.uint8)
    image[8:16, 8:16, 1] = 255
    image[24:32, 24:32, 1] = 255
    result = engine(monkeypatch).execute(
        pipeline=pipeline(
            blob={
                "rule_mode": "required_structure",
                "class_rules": [{"class_id": 1, **rule}],
            }
        ),
        image=image,
    )
    assert result["final_verdict"] == verdict
    measured = result["crops"][0]["blob_measurements"][0]
    assert measured["count"] == 2
    assert measured["area_px"] == 128


def test_defect_blob_component_gray_filter_and_class_selection(monkeypatch):
    image = np.zeros((64, 64, 3), np.uint8)
    image[8:16, 8:16, 0] = 255  # RGB gray 76, excluded by the rule.
    image[24:32, 24:32, 1] = 255  # RGB gray 150, deliberately unselected.
    graph = pipeline(
        blob={
            "class_ids": [2],
            "class_rules": [{"class_id": 2, "min_mean_grayscale": 100}],
        }
    )
    result = engine(monkeypatch).execute(pipeline=graph, image=image)
    assert result["final_verdict"] == "OK"
    assert result["crops"][0]["blob_count"] == 0
    assert result["crops"][0]["blob_measurements"][0]["mean_grayscale"] is None


def test_probability_filter_keeps_raw_probability_but_no_thresholded_area(monkeypatch):
    from backend.engine.segmentation_evidence import decoded_array

    class SoftSegmentation(torch.nn.Module):
        def forward(self, image):
            probabilities = torch.tensor([0.1, 0.2, 0.7]).reshape(1, 3, 1, 1)
            return (
                probabilities.log()
                .expand(len(image), 3, *image.shape[-2:])
                .to(image.device)
            )

    instance = engine(monkeypatch)
    monkeypatch.setattr(
        instance, "_get_inspection_model", lambda **_: (SoftSegmentation(), True)
    )
    result = instance.execute(
        pipeline=pipeline(
            {
                "class_ids": [2],
                "class_rules": [{"class_id": 2, "probability_threshold": 0.8}],
            }
        ),
        image=np.zeros((32, 32, 3), np.uint8),
    )
    row = next(
        r for r in result["crops"][0]["segmentation_classes"] if r["class_id"] == 2
    )
    assert result["final_verdict"] == "OK"
    assert row["area_px"] == 0
    assert np.allclose(decoded_array(row["probability"]), 0.7)


@pytest.mark.parametrize(
    "params",
    [
        {"class_ids": [True]},
        {"class_ids": [1, 1]},
        {"class_rules": [{"class_id": 2, "probability_threshold": float("inf")}]},
        {"class_rules": [{"class_id": 2, "probability_threshold": None}]},
        {"class_rules": [{"class_id": 2, "min_area_px": 8, "max_area_px": 4}]},
    ],
)
def test_invalid_class_filters_rejected_before_model_load(params):
    with pytest.raises(ValueError):
        ordered_linear_nodes(pipeline(params))


def test_multiclass_blob_package_retains_lossless_rasters_and_rule_measurements(
    tmp_path,
):
    from PIL import Image
    from backend.engine.flow_package import build_flow_package
    from backend.engine.segmentation.model import build_segmentation_model

    model = build_segmentation_model("unet", num_classes=3, pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.head.bias[2] = 8
    checkpoint = tmp_path / "best_model.pt"
    torch.save(
        {
            "task": "segmentation",
            "model_name": "unet",
            "preset": "fast",
            "classes": ["background", "pad", "crack"],
            "image_size": [32, 32],
            "model_state_dict": model.state_dict(),
        },
        checkpoint,
    )
    image = tmp_path / "raster.png"
    Image.fromarray(np.full((32, 48, 3), 140, np.uint8)).save(image)
    graph = pipeline(
        blob={
            "rule_mode": "required_structure",
            "class_rules": [
                {
                    "class_id": 2,
                    "min_count": 1,
                    "max_count": 1,
                    "min_area_px": 1500,
                    "max_area_px": 1600,
                    "min_mean_grayscale": 100,
                    "max_mean_grayscale": 180,
                }
            ],
        }
    )
    reference = FlowchartEngine(
        device="cpu", checkpoint_resolver=lambda *_: checkpoint
    ).execute(pipeline=graph, image_path=str(image))
    result = build_flow_package(
        pipeline=graph,
        checkpoints={"job_raster": checkpoint},
        output_base_dir=tmp_path / "export",
        package_name="class_evidence",
    )
    package = Path(result["package_path"])
    output = tmp_path / "result.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(package / "run_flow.py"),
            "--image",
            str(image),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": ""},
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    packaged = json.loads(output.read_text())
    assert packaged["final_verdict"] == reference["final_verdict"] == "OK"
    crop = packaged["crops"][0]
    assert crop["segmentation_classes"] == reference["crops"][0]["segmentation_classes"]
    assert crop["blob_measurements"] == reference["crops"][0]["blob_measurements"]
    assert crop["blob_measurements"][0]["area_px"] == 32 * 48
    assert next(r for r in crop["segmentation_classes"] if r["class_id"] == 2)["mask"][
        "shape"
    ] == [32, 48]
    assert (package / "backend/engine/segmentation_evidence.py").is_file()
    assert (package / "backend/engine/rule_evaluation.py").is_file()
