"""
backend/tests/test_workflow_features.py

Comprehensive Verification Suite for Workflow Workflow Parity Features:
1. AI Auto-Selector (Smart Magic Wand)
2. Shape Converter (BBox to Polygon)
3. Rotated Bounding Box (OBB)
4. Overkill (과검) vs Underkill (미검) Trade-off Optimization
5. Flowchart Multi-Model Chaining Pipeline
6. Production Model ONNX & Runtime Package Export
"""

import json
from pathlib import Path
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.engine.labeling_ai import (
    auto_select_contour,
    shape_converter_bbox_to_polygon,
    rotated_bbox_to_corners,
)


@pytest.fixture
def client(tmp_path):
    app = create_app(project_dir=str(tmp_path))
    return TestClient(app)


@pytest.fixture
def test_image(tmp_path):
    """Generates synthetic PCB test image with circuit trace and component."""
    img_path = tmp_path / "test_pcb.png"
    img = np.full((256, 256, 3), 40, dtype=np.uint8)
    # Green circuit trace
    cv2.line(img, (20, 50), (220, 50), (0, 180, 0), 3)
    # IC chip component
    cv2.rectangle(img, (60, 80), (180, 180), (100, 100, 100), -1)
    # Defect scratch
    cv2.line(img, (80, 100), (140, 160), (0, 0, 255), 4)
    cv2.imwrite(str(img_path), img)
    return img_path


def test_auto_selector_magic_wand(test_image):
    """Verifies that clicking on a defect extracts a valid closed polygon."""
    res = auto_select_contour(test_image, seed_x=100, seed_y=120, tolerance=25)
    assert "polygon" in res
    assert len(res["polygon"]) >= 3
    assert "bbox" in res
    assert len(res["bbox"]) == 4
    assert res["area"] > 0


def test_shape_converter_bbox_to_polygon(test_image):
    """Verifies coarse bounding box converts to tight contour polygon."""
    coarse_bbox = [50.0, 70.0, 190.0, 190.0]
    res = shape_converter_bbox_to_polygon(test_image, coarse_bbox, sensitivity=0.5)
    assert "polygon" in res
    assert len(res["polygon"]) >= 3
    assert res["area"] > 0


def test_rotated_bbox_to_corners():
    """Verifies rotated bounding box geometry math produces 4 corners."""
    corners = rotated_bbox_to_corners(cx=100, cy=100, width=50, height=30, angle_degrees=45)
    assert len(corners) == 4
    for pt in corners:
        assert len(pt) == 2


def test_annotation_rotated_bbox_save(client, test_image, tmp_path):
    """Verifies API can persist and rasterize rotated_bbox."""
    payload = {
        "image_id": "test_pcb",
        "image_width": 256,
        "image_height": 256,
        "output_dir": str(tmp_path / "annotations"),
        "annotations": [
            {
                "type": "rotated_bbox",
                "label": "chip_rotated",
                "rotated_bbox": [128.0, 128.0, 60.0, 40.0, 30.0],
            }
        ],
    }
    resp = client.post("/api/annotations/save", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "saved"
    assert data["count"] == 1
    assert data["mask_generated"] is True


def test_api_auto_select_and_shape_converter(client, test_image):
    """Verifies HTTP endpoints for auto-select and shape-converter."""
    sel_resp = client.post(
        "/api/annotations/auto-select",
        json={"image_path": str(test_image), "seed_x": 100, "seed_y": 120, "tolerance": 25},
    )
    assert sel_resp.status_code == 200
    sel_data = sel_resp.json()
    assert sel_data["status"] == "success"
    assert len(sel_data["result"]["polygon"]) >= 3

    conv_resp = client.post(
        "/api/annotations/shape-converter",
        json={"image_path": str(test_image), "bbox": [60, 80, 180, 180], "sensitivity": 0.5},
    )
    assert conv_resp.status_code == 200
    conv_data = conv_resp.json()
    assert conv_data["status"] == "success"
    assert len(conv_data["result"]["polygon"]) >= 3


def test_overkill_underkill_optimization(client):
    """Verifies calculation of Overkill vs Underkill trade-off curve and zero-underkill threshold."""
    resp = client.get("/api/evaluation/overkill-underkill?target_max_underkill=0&cost_escape=1000&cost_scrap=20")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "optimal_threshold" in data
    assert "tradeoff_curve" in data
    assert len(data["tradeoff_curve"]) > 5
    # Verify that optimal threshold achieves target underkill
    opt_stats = data["optimal_stats"]
    assert opt_stats["underkill_count"] <= 0


def test_inference_benchmark(client):
    """Verifies inference center speed benchmark API."""
    resp = client.post("/api/evaluation/benchmark", json={"iterations": 10, "resolution": 128})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "fps" in data
    assert data["fps"] > 0
    assert "mean_latency_ms" in data
    assert data["mean_latency_ms"] > 0


def test_flowchart_pipeline_lifecycle(client):
    """Verifies getting, saving, and executing flowchart multi-model pipeline."""
    # 1. Get default pipeline
    resp = client.get("/api/flowchart/pipeline")
    assert resp.status_code == 200
    pipeline = resp.json()
    assert "nodes" in pipeline
    assert len(pipeline["nodes"]) >= 4

    # 2. Save pipeline
    pipeline["name"] = "Updated Test Pipeline"
    save_resp = client.post("/api/flowchart/pipeline", json=pipeline)
    assert save_resp.status_code == 200

    # 3. Run flowchart
    run_resp = client.post("/api/flowchart/run", json={"pipeline": pipeline})
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert run_data["status"] == "success"
    assert "final_verdict" in run_data
    assert run_data["final_verdict"] in ("OK", "NG")
    assert "crops" in run_data
    assert "execution_steps" in run_data


def test_export_runtime_package(client):
    """Verifies Runtime style production model runtime export with ONNX and wrappers."""
    resp = client.post("/api/export/runtime", json={"resolution": 128, "package_name": "test_pkg"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "package_path" in data
    pkg_dir = Path(data["package_path"])
    assert (pkg_dir / "model.onnx").is_file()
    assert (pkg_dir / "config.json").is_file()
    assert (pkg_dir / "infer.py").is_file()
    assert (pkg_dir / "Program.cs").is_file()
    assert (pkg_dir / "main.cpp").is_file()
    assert (pkg_dir / "README_DEPLOY.md").is_file()
