"""
backend/tests/test_labeling_ai.py

Comprehensive unit tests for:
1. Bidirectional shape converters (bbox, polygon, mask, rotated_bbox)
2. cv2.minAreaRect numerical precision on oriented bounding boxes
3. Unified REST API endpoint POST /api/annotations/shape-converter
"""

import math
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.engine.labeling_ai import (
    auto_select_contour,
    bbox_to_mask,
    bbox_to_polygon,
    bbox_to_rotated_bbox,
    mask_to_bbox,
    mask_to_polygon,
    mask_to_rotated_bbox,
    polygon_to_bbox,
    polygon_to_mask,
    polygon_to_rotated_bbox,
    rotated_bbox_to_bbox,
    rotated_bbox_to_corners,
    rotated_bbox_to_polygon,
    shape_converter_bbox_to_polygon,
)


@pytest.fixture
def client(tmp_path):
    app = create_app(project_dir=str(tmp_path))
    client = TestClient(app)
    client.headers["X-Vision-Token"] = app.state.api_token
    return client


# --------------------------------------------------------------------------
# 1. BBox <-> Polygon Tests
# --------------------------------------------------------------------------

def test_bbox_to_polygon():
    bbox = [10.0, 20.0, 50.0, 80.0]
    poly = bbox_to_polygon(bbox)
    assert len(poly) == 4
    assert poly[0] == [10.0, 20.0]
    assert poly[1] == [50.0, 20.0]
    assert poly[2] == [50.0, 80.0]
    assert poly[3] == [10.0, 80.0]


def test_polygon_to_bbox():
    poly = [[10.0, 20.0], [50.0, 20.0], [50.0, 80.0], [10.0, 80.0]]
    bbox = polygon_to_bbox(poly)
    assert bbox == [10.0, 20.0, 50.0, 80.0]

    # Non-rectangular polygon
    poly_triangle = [[0.0, 5.0], [10.0, 15.0], [5.0, 0.0]]
    bbox_triangle = polygon_to_bbox(poly_triangle)
    assert bbox_triangle == [0.0, 0.0, 10.0, 15.0]


def test_bbox_polygon_roundtrip():
    original_bbox = [15.5, 25.5, 115.5, 215.5]
    poly = bbox_to_polygon(original_bbox)
    recovered_bbox = polygon_to_bbox(poly)
    assert math.isclose(recovered_bbox[0], original_bbox[0], rel_tol=1e-5)
    assert math.isclose(recovered_bbox[1], original_bbox[1], rel_tol=1e-5)
    assert math.isclose(recovered_bbox[2], original_bbox[2], rel_tol=1e-5)
    assert math.isclose(recovered_bbox[3], original_bbox[3], rel_tol=1e-5)


# --------------------------------------------------------------------------
# 2. Polygon <-> Mask Tests
# --------------------------------------------------------------------------

def test_polygon_to_mask_and_mask_to_polygon():
    shape = (100, 100)
    poly = [[20.0, 20.0], [60.0, 20.0], [60.0, 70.0], [20.0, 70.0]]
    mask = polygon_to_mask(poly, shape)

    assert mask.shape == shape
    assert mask.dtype == np.uint8
    assert mask[40, 40] == 255
    assert mask[10, 10] == 0
    assert mask[80, 80] == 0

    recovered_poly = mask_to_polygon(mask, approx_epsilon=1.0)
    assert len(recovered_poly) >= 4
    rec_bbox = polygon_to_bbox(recovered_poly)
    # Allow 1px discrete raster tolerance
    assert abs(rec_bbox[0] - 20.0) <= 1.5
    assert abs(rec_bbox[1] - 20.0) <= 1.5
    assert abs(rec_bbox[2] - 60.0) <= 1.5
    assert abs(rec_bbox[3] - 70.0) <= 1.5


# --------------------------------------------------------------------------
# 3. Mask <-> BBox Tests
# --------------------------------------------------------------------------

def test_mask_to_bbox_and_bbox_to_mask():
    shape = (120, 120)
    bbox = [25.0, 30.0, 75.0, 90.0]
    mask = bbox_to_mask(bbox, shape)

    assert mask.shape == shape
    assert mask[50, 50] == 255
    assert mask[10, 10] == 0

    computed_bbox = mask_to_bbox(mask)
    assert computed_bbox == [25.0, 30.0, 75.0, 90.0]

    # Empty mask
    empty_mask = np.zeros(shape, dtype=np.uint8)
    assert mask_to_bbox(empty_mask) == [0.0, 0.0, 0.0, 0.0]


# --------------------------------------------------------------------------
# 4. Rotated BBox (OBB) & cv2.minAreaRect Numerical Precision
# --------------------------------------------------------------------------

def test_polygon_to_rotated_bbox_axis_aligned():
    poly = [[10.0, 20.0], [50.0, 20.0], [50.0, 80.0], [10.0, 80.0]]
    res = polygon_to_rotated_bbox(poly)

    cx, cy = res["center"]
    w, h = res["size"]
    angle = res["angle"]

    assert math.isclose(cx, 30.0, abs_tol=1e-3)
    assert math.isclose(cy, 50.0, abs_tol=1e-3)
    # Size dimensions can be ordered (w, h) or (h, w) depending on OpenCV minAreaRect convention
    dims = sorted([w, h])
    assert math.isclose(dims[0], 40.0, abs_tol=1e-3)
    assert math.isclose(dims[1], 60.0, abs_tol=1e-3)
    # Angle for axis-aligned box is 0 or 90 deg depending on OpenCV version
    assert int(round(angle)) in (0, 90, -90)


def test_polygon_to_rotated_bbox_oriented_45_deg():
    # 40x20 rectangle rotated 45 degrees around center (100, 100)
    center = (100.0, 100.0)
    w_true, h_true = 40.0, 20.0
    angle_true = 45.0
    corners = rotated_bbox_to_polygon([center[0], center[1]], [w_true, h_true], angle_true)

    res = polygon_to_rotated_bbox(corners)
    cx, cy = res["center"]
    w_est, h_est = res["size"]

    assert math.isclose(cx, center[0], abs_tol=1e-2)
    assert math.isclose(cy, center[1], abs_tol=1e-2)
    dims_est = sorted([w_est, h_est])
    dims_true = sorted([w_true, h_true])
    assert math.isclose(dims_est[0], dims_true[0], abs_tol=1e-2)
    assert math.isclose(dims_est[1], dims_true[1], abs_tol=1e-2)


def test_bbox_to_rotated_bbox():
    bbox = [20.0, 30.0, 80.0, 90.0]
    rbox = bbox_to_rotated_bbox(bbox)
    assert rbox["center"] == [50.0, 60.0]
    assert rbox["size"] == [60.0, 60.0]
    assert rbox["angle"] == 0.0


def test_rotated_bbox_to_bbox_expansion():
    # A 100x20 bar rotated 45 degrees has an enclosing AABB larger than its unrotated width/height
    center = [150.0, 150.0]
    size = [100.0, 20.0]
    aabb = rotated_bbox_to_bbox(center, size, 45.0)

    # Diagonal of 100 and 20 projected at 45 deg:
    # Extent is approx (100 * cos(45) + 20 * sin(45)) = 120 / sqrt(2) ~= 84.85 half-span, total width ~= 84.85
    width_aabb = aabb[2] - aabb[0]
    height_aabb = aabb[3] - aabb[1]
    assert width_aabb > 80.0
    assert height_aabb > 80.0


def test_mask_to_rotated_bbox():
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[30:70, 40:60] = 255
    res = mask_to_rotated_bbox(mask)
    cx, cy = res["center"]
    w, h = res["size"]
    assert 48.0 <= cx <= 52.0
    assert 48.0 <= cy <= 52.0
    dims = sorted([w, h])
    assert abs(dims[0] - 20.0) <= 2.0
    assert abs(dims[1] - 40.0) <= 2.0


# --------------------------------------------------------------------------
# 5. Unified REST API Endpoint (/api/annotations/shape-converter)
# --------------------------------------------------------------------------

def test_api_shape_converter_bbox_to_polygon(client):
    payload = {
        "source_type": "bbox",
        "target_type": "polygon",
        "data": [10.0, 20.0, 60.0, 80.0],
    }
    resp = client.post("/api/annotations/shape-converter", json=payload)
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    assert res["target_type"] == "polygon"
    assert len(res["converted_data"]["polygon"]) == 4


def test_api_shape_converter_polygon_to_bbox(client):
    payload = {
        "source_type": "polygon",
        "target_type": "bbox",
        "data": [[10.0, 20.0], [50.0, 20.0], [50.0, 60.0], [10.0, 60.0]],
    }
    resp = client.post("/api/annotations/shape-converter", json=payload)
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    assert res["target_type"] == "bbox"
    assert res["converted_data"]["bbox"] == [10.0, 20.0, 50.0, 60.0]


def test_api_shape_converter_bbox_to_rotated_bbox(client):
    payload = {
        "source_type": "bbox",
        "target_type": "rotated_bbox",
        "data": [20.0, 40.0, 100.0, 80.0],
    }
    resp = client.post("/api/annotations/shape-converter", json=payload)
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    assert res["target_type"] == "rotated_bbox"
    rbox = res["converted_data"]
    assert rbox["center"] == [60.0, 60.0]
    assert rbox["size"] == [80.0, 40.0]
    assert rbox["angle"] == 0.0


def test_api_shape_converter_rotated_bbox_to_polygon(client):
    payload = {
        "source_type": "rotated_bbox",
        "target_type": "polygon",
        "data": {
            "center": [100.0, 100.0],
            "size": [60.0, 30.0],
            "angle": 30.0,
        },
    }
    resp = client.post("/api/annotations/shape-converter", json=payload)
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    assert res["target_type"] == "polygon"
    assert len(res["converted_data"]["polygon"]) == 4


def test_api_shape_converter_rotated_bbox_to_bbox(client):
    payload = {
        "source_type": "rotated_bbox",
        "target_type": "bbox",
        "data": [100.0, 100.0, 60.0, 30.0, 0.0],
    }
    resp = client.post("/api/annotations/shape-converter", json=payload)
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    assert res["target_type"] == "bbox"
    assert res["converted_data"]["bbox"] == [70.0, 85.0, 130.0, 115.0]


def test_api_shape_converter_mask_to_bbox(client):
    mask = [[0] * 50 for _ in range(50)]
    for y in range(10, 30):
        for x in range(15, 35):
            mask[y][x] = 255

    payload = {
        "source_type": "mask",
        "target_type": "bbox",
        "data": mask,
    }
    resp = client.post("/api/annotations/shape-converter", json=payload)
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    assert res["target_type"] == "bbox"
    assert res["converted_data"]["bbox"] == [15.0, 10.0, 35.0, 30.0]


def test_api_shape_converter_invalid_requests(client):
    # Missing source_type / target_type
    resp = client.post("/api/annotations/shape-converter", json={"data": [1, 2, 3, 4]})
    assert resp.status_code == 400

    # Malformed bbox
    resp = client.post(
        "/api/annotations/shape-converter",
        json={"source_type": "bbox", "target_type": "polygon", "data": [1, 2]},
    )
    assert resp.status_code == 400
