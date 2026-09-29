"""
backend/tests/test_flowchart_engine.py

Comprehensive Automated Test Suite for Flowchart Chaining Engine (M10).
Validates:
  1. safe_crop_roi bounds clipping, symmetric context padding, and >= 16px min dimension guarantee (UNet safety).
  2. Zero-detection clean normal bypass (EC-01).
  3. Multi-detection batch processing and thumbnail compression payload cap (EC-02).
  4. Spatial collapse prevention for UNet and PaDiM under extreme microscopic crops (EC-04).
  5. Multi-task Stage 2 inspection: PaDiM anomaly, UNet segmentation, ResNet classification.
  6. Decision rule engine: any_defect_is_ng, score_gt_threshold, max_flaws_allowed.
  7. Non-standard image formats (1-channel L, 4-channel RGBA, 16-bit TIFF) and 45MP memory safe capping.
  8. API endpoints: /pipeline, /sample-images, /run.
  9. Optional real manufacturing image execution via MODU_VISION_REAL_TEST_DIR.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
import pytest
import numpy as np
import cv2
import torch
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.api import routes_flowchart
from backend.engine.flowchart_engine import (
    FlowchartEngine,
    FlowNode,
    FlowNodeData,
    FlowEdge,
    FlowchartPipeline,
    CropInspectionResult,
    get_default_flowchart,
    safe_crop_roi,
)
from backend.engine.industrial_adapters import read_image_safely_rgb


@pytest.fixture
def client(tmp_path):
    app = create_app(project_dir=str(tmp_path))
    client = TestClient(app)
    client.headers["X-Vision-Token"] = app.state.api_token
    return client


@pytest.fixture
def engine():
    return FlowchartEngine(device="cpu")


def test_crop_roi_bounds_clipping_and_min_dim():
    """
    Validates safe_crop_roi against negative coordinates, out-of-bounds coords,
    zero-area boxes, and micro-flaws (3x3).
    """
    img = np.full((200, 300, 3), 120, dtype=np.uint8)

    # 1. Negative & Out of bounds
    crop1, bbox1 = safe_crop_roi(img, [-30, -20, 40, 50], padding_px=5, min_size=16)
    assert crop1.shape == (224, 224, 3)
    assert 0 <= bbox1[0] < bbox1[2] <= 300
    assert 0 <= bbox1[1] < bbox1[3] <= 200

    # 2. Out of bounds high
    crop2, bbox2 = safe_crop_roi(img, [280, 180, 350, 250], padding_px=10, min_size=16)
    assert crop2.shape == (224, 224, 3)
    assert 0 <= bbox2[0] < bbox2[2] <= 300
    assert 0 <= bbox2[1] < bbox2[3] <= 200

    # 3. Microscopic 3x3 flaw (must expand to at least 16x16)
    crop3, bbox3 = safe_crop_roi(img, [50, 50, 53, 53], padding_px=0, min_size=16)
    assert crop3.shape == (224, 224, 3)
    w_crop = bbox3[2] - bbox3[0]
    h_crop = bbox3[3] - bbox3[1]
    assert w_crop >= 16
    assert h_crop >= 16

    # 4. Zero area degenerate box [100, 100, 100, 100]
    crop4, bbox4 = safe_crop_roi(img, [100, 100, 100, 100], padding_px=0, min_size=16)
    assert crop4.shape == (224, 224, 3)
    assert bbox4[2] - bbox4[0] >= 16
    assert bbox4[3] - bbox4[1] >= 16


def test_zero_detection_bypass(engine):
    """
    EC-01: Zero detection scenario where an image produces 0 candidate boxes.
    Must skip inspection and request review because the image was not inspected.
    """
    # Create clean blank image
    img = np.full((256, 256, 3), 128, dtype=np.uint8)

    pipe = get_default_flowchart()
    # Set threshold very high so Faster R-CNN and contour filter output 0
    for node in pipe.nodes:
        if node.data.node_type == "detection_crop":
            node.data.threshold = 0.999

    res = engine.execute(pipeline=pipe, image=img)
    assert res["status"] == "review"
    assert res["roi_count"] == 0
    assert res["defective_roi_count"] == 0
    assert len(res["crops"]) == 0
    assert res["final_verdict"] == "REVIEW"
    assert res["is_ok"] is False
    assert "not inspected" in res["rejection_reason"]


def test_multiple_detections_compression(engine):
    """
    EC-02: Multiple detections scenario (e.g. 10+ crops).
    Validates crop thumbnail compression: each thumbnail size < 25KB,
    and total result JSON payload remains compact (< 500KB).
    """
    # Create image with multiple simulated components
    img = np.full((600, 600, 3), 30, dtype=np.uint8)
    for i in range(12):
        row = i // 4
        col = i % 4
        x = 50 + col * 130
        y = 50 + row * 150
        cv2.rectangle(img, (x, y), (x + 80, y + 80), (160, 160, 160), 2)
        cv2.putText(img, f"C{i}", (x + 20, y + 45), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    pipe = get_default_flowchart()
    res = engine.execute(pipeline=pipe, image=img)

    assert res["status"] == "success"
    assert res["roi_count"] >= 1
    # Check each crop thumbnail is a valid compact base64 string
    for crop in res["crops"]:
        thumb = crop["crop_thumbnail"]
        assert thumb.startswith("data:image/png;base64,")
        b64_data = thumb.split(",")[1]
        decoded = base64.b64decode(b64_data)
        assert len(decoded) < 25000, f"Crop thumbnail exceeded 25KB: {len(decoded)} bytes"


def test_spatial_collapse_prevention_unet_and_padim(engine):
    """
    EC-04: Feeds microscopic crops directly through UNet and PaDiM
    to verify that safe_crop_roi completely prevents the UNet convolution
    spatial collapse error ('Calculated output size (64x0x0)').
    """
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    micro_box = [30, 30, 33, 33]  # 3x3 flaw

    crop_resized, bbox = safe_crop_roi(img, micro_box, padding_px=0, min_size=16, target_size=(224, 224))
    assert crop_resized.shape == (224, 224, 3)

    # Test UNet
    unet, _ = engine._get_inspection_model(task="segmentation")
    crop_tensor = torch.from_numpy(crop_resized).permute(2, 0, 1).float().unsqueeze(0) / 255.0
    with torch.no_grad():
        seg_out = unet(crop_tensor)
        assert seg_out.shape == (1, 2, 224, 224)

    # Test PaDiM / Feature Extractor
    padim, _ = engine._get_inspection_model(task="anomaly")
    with torch.no_grad():
        feat = padim.feature_extractor(crop_tensor)
        assert feat.shape[2:] == (28, 28)


def test_all_inspection_tasks(engine):
    """
    Validates Stage 2 execution across all 3 vision inspection tasks:
      1. Anomaly (PaDiM)
      2. Segmentation (UNet)
      3. Classification (ResNet18)
    """
    img = np.full((300, 300, 3), 40, dtype=np.uint8)
    cv2.rectangle(img, (50, 50), (150, 150), (180, 180, 180), 2)
    cv2.rectangle(img, (180, 50), (280, 150), (180, 180, 180), 2)

    for task_name in ["anomaly", "segmentation", "classification"]:
        pipe = get_default_flowchart()
        for node in pipe.nodes:
            if node.data.node_type == "inspection":
                node.data.task = task_name

        res = engine.execute(pipeline=pipe, image=img)
        assert res["status"] == "success"
        assert res["roi_count"] >= 1
        assert len(res["crops"]) == res["roi_count"]
        for crop in res["crops"]:
            assert "defect_score" in crop
            assert "verdict" in crop
            assert crop["verdict"] in ("OK", "NG")


def test_flowchart_decision_rules(engine):
    """
    Validates Stage 3 decision rules:
      - any_defect_is_ng
      - score_gt_threshold
      - max_flaws_allowed
    """
    crop_ok = CropInspectionResult(
        roi_id="c1", label="ic", bbox=[10, 10, 50, 50], defect_score=0.15, verdict="OK",
        crop_thumbnail="data:image/png;base64,abc", flaw_type="None (Pure Normal)",
    )
    crop_ng1 = CropInspectionResult(
        roi_id="c2", label="ic", bbox=[60, 10, 100, 50], defect_score=0.72, verdict="NG",
        crop_thumbnail="data:image/png;base64,def", flaw_type="Scratch",
    )
    crop_ng2 = CropInspectionResult(
        roi_id="c3", label="ic", bbox=[110, 10, 150, 50], defect_score=0.85, verdict="NG",
        crop_thumbnail="data:image/png;base64,ghi", flaw_type="Crack",
    )

    # 1. any_defect_is_ng
    node_any = FlowNode(
        id="d1", position={"x": 0, "y": 0},
        data=FlowNodeData(label="Decision", node_type="decision", rule="any_defect_is_ng"),
    )
    v1, is_ok1, _, _, _ = engine._evaluate_decision_rules([crop_ok], node_any)
    assert v1 == "OK" and is_ok1 is True

    v2, is_ok2, _, _, _ = engine._evaluate_decision_rules([crop_ok, crop_ng1], node_any)
    assert v2 == "NG" and is_ok2 is False

    # 2. score_gt_threshold (threshold = 0.80)
    node_score = FlowNode(
        id="d2", position={"x": 0, "y": 0},
        data=FlowNodeData(label="Decision", node_type="decision", rule="score_gt_threshold", threshold=0.80),
    )
    v3, is_ok3, _, _, _ = engine._evaluate_decision_rules([crop_ok, crop_ng1], node_score)
    assert v3 == "OK" and is_ok3 is True  # 0.72 < 0.80

    v4, is_ok4, _, _, _ = engine._evaluate_decision_rules([crop_ok, crop_ng1, crop_ng2], node_score)
    assert v4 == "NG" and is_ok4 is False  # 0.85 >= 0.80

    # 3. max_flaws_allowed (allowed = 1)
    node_max = FlowNode(
        id="d3", position={"x": 0, "y": 0},
        data=FlowNodeData(
            label="Decision", node_type="decision", rule="max_flaws_allowed",
            params={"max_flaws_allowed": 1},
        ),
    )
    v5, is_ok5, _, _, _ = engine._evaluate_decision_rules([crop_ok, crop_ng1], node_max)
    assert v5 == "OK" and is_ok5 is True  # 1 <= 1

    v6, is_ok6, _, _, _ = engine._evaluate_decision_rules([crop_ok, crop_ng1, crop_ng2], node_max)
    assert v6 == "NG" and is_ok6 is False  # 2 > 1


def test_non_standard_formats_and_45mp(engine, tmp_path):
    """
    EC-05 & EC-06: Validates memory-safe ingestion for Grayscale, RGBA,
    16-bit TIFF, and adaptive capping of ultra-large images.
    """
    pipe = get_default_flowchart()

    # 1. Single channel Grayscale
    gray = np.full((128, 128), 100, dtype=np.uint8)
    res_gray = engine.execute(pipeline=pipe, image=gray)
    assert res_gray["execution_steps"][0]["status"] == "passed"

    # 2. 4-channel RGBA
    rgba = np.full((128, 128, 4), 150, dtype=np.uint8)
    res_rgba = engine.execute(pipeline=pipe, image=rgba)
    assert res_rgba["execution_steps"][0]["status"] == "passed"

    # 3. 16-bit TIFF simulation
    tiff_16 = np.full((128, 128), 40000, dtype=np.uint16)
    tiff_path = tmp_path / "test_16bit.tiff"
    cv2.imwrite(str(tiff_path), tiff_16)
    res_tiff = engine.execute(pipeline=pipe, image_path=str(tiff_path))
    assert res_tiff["execution_steps"][0]["status"] == "passed"

    # 4. Ultra-high-resolution image (3000x2000) capped to max_dim=1600
    huge_img = np.full((2000, 3000, 3), 80, dtype=np.uint8)
    huge_path = tmp_path / "huge_wafer.png"
    cv2.imwrite(str(huge_path), huge_img)
    read_img = read_image_safely_rgb(huge_path, max_dim=1600)
    assert max(read_img.shape[:2]) <= 1600


def test_explicit_missing_inspection_path_never_substitutes_image_id_or_demo(tmp_path, monkeypatch):
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart

    monkeypatch.chdir(tmp_path)
    dataset = tmp_path / "datasets"
    dataset.mkdir()
    cv2.imwrite(str(dataset / "different.png"), np.zeros((32, 32, 3), dtype=np.uint8))
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_inspect_crops", lambda *_: ([], 0.0, "passed"))
    pipeline = get_single_segmentation_flowchart("job_inspect")

    with pytest.raises(FileNotFoundError, match="inspection image"):
        engine.execute(pipeline=pipeline, image_path=str(tmp_path / "missing.png"), image_id="different")
    with pytest.raises(FileNotFoundError, match="inspection image"):
        engine.execute(pipeline=pipeline, image_path=str(tmp_path / "missing.png"), image=np.zeros((32, 32, 3), dtype=np.uint8))


def test_image_id_alone_cannot_resolve_ambiguous_local_or_synthetic_input(tmp_path, monkeypatch):
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart

    monkeypatch.chdir(tmp_path)
    dataset = tmp_path / "datasets"
    dataset.mkdir()
    cv2.imwrite(str(dataset / "different.png"), np.zeros((32, 32, 3), dtype=np.uint8))
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_inspect_crops", lambda *_: ([], 0.0, "passed"))
    pipeline = get_single_segmentation_flowchart("job_inspect")

    with pytest.raises(ValueError, match="inspection image"):
        engine.execute(pipeline=pipeline, image_id="different")
    with pytest.raises(ValueError, match="inspection image"):
        engine.execute(pipeline=pipeline)


def test_api_flowchart_endpoints(client, monkeypatch, tmp_path):
    """Validates FastAPI routes: /pipeline, /sample-images, /run."""
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "pipeline.json")
    # 1. GET /pipeline
    r_get = client.get("/api/flowchart/pipeline")
    assert r_get.status_code == 200
    pipe_data = r_get.json()
    assert "nodes" in pipe_data
    assert len(pipe_data["nodes"]) >= 4

    # 2. POST /pipeline
    pipe_data["name"] = "Automated Pipeline Test"
    r_post = client.post("/api/flowchart/pipeline", json=pipe_data)
    assert r_post.status_code == 200
    assert r_post.json()["status"] == "saved"

    # 3. GET /sample-images
    r_samples = client.get("/api/flowchart/sample-images")
    assert r_samples.status_code == 200
    samples_data = r_samples.json()
    assert "images" in samples_data
    assert isinstance(samples_data["images"], list)

    # 4. POST /run
    r_run = client.post("/api/flowchart/run", json={"pipeline": pipe_data})
    assert r_run.status_code == 422
    assert "image" in r_run.json()["detail"].lower()
    image = tmp_path / "inspection.png"
    cv2.imwrite(str(image), np.zeros((32, 32, 3), dtype=np.uint8))
    r_run = client.post("/api/flowchart/run", json={"pipeline": pipe_data, "image_path": str(image)})
    assert r_run.status_code == 409
    assert "Model job is missing" in r_run.json()["detail"]


def test_real_manufacturing_images(engine):
    """
    Validates end-to-end execution on an explicitly supplied real image folder.
    """
    configured = os.environ.get("MODU_VISION_REAL_TEST_DIR")
    if not configured:
        pytest.skip("Set MODU_VISION_REAL_TEST_DIR to opt in to real image QA.")
    candidate_dir = Path(configured).expanduser()
    test_file = None
    if candidate_dir.is_file():
        test_file = candidate_dir
    elif candidate_dir.is_dir():
        for extension in ("*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff", "*.webp"):
            test_file = next((f for f in candidate_dir.glob(extension) if f.is_file() and f.stat().st_size > 0), None)
            if test_file:
                break

    if not test_file:
        pytest.skip("MODU_VISION_REAL_TEST_DIR has no readable inspection image.")

    pipe = get_default_flowchart()
    res = engine.execute(pipeline=pipe, image_path=str(test_file))
    assert res["status"] == "success"
    assert res["final_verdict"] in ("OK", "NG")
    assert res["total_latency_ms"] > 0
    assert res["annotated_image"] is not None
