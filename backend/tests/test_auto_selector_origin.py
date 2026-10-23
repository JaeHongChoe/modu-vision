"""The auto selector is a drawing aid, not a draft (docs/product-direction-easy-labeling-agent.md §3.3).

Its shape joins the labels like a drawn one and is saved by the person's own save, so what it must do is say how it
was made: the engine and the route report whether the selector fell back to a small box around the click, a shape
saved with an origin of kind `auto_selector` keeps that origin (fallback mark included), the model-draft origin keeps
its required fields, and an origin of an unknown kind is refused as before.
"""
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.engine.labeling_ai import auto_select_contour
from backend.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(project_dir=str(tmp_path))
    client = TestClient(app)
    client.headers["X-Vision-Token"] = app.state.api_token
    return client


def _board_image(folder: Path) -> Path:
    """A grey chip with a red scratch on a dark board: a click on the scratch finds a region."""
    path = folder / "board.png"
    img = np.full((256, 256, 3), 40, dtype=np.uint8)
    cv2.rectangle(img, (60, 80), (180, 180), (100, 100, 100), -1)
    cv2.line(img, (80, 100), (140, 160), (0, 0, 255), 4)
    cv2.imwrite(str(path), img)
    return path


def _blank_image(folder: Path) -> Path:
    """A 4x4 uniform image: no contour reaches the selector's minimum area, so only the fallback box remains."""
    path = folder / "blank.png"
    cv2.imwrite(str(path), np.full((4, 4, 3), 128, dtype=np.uint8))
    return path


AUTO_SELECTOR_ORIGIN = {
    "kind": "auto_selector", "backend": "opencv_flood_fill", "seed_point": [100.0, 120.0], "tolerance": 25,
    "fallback": False, "created_at": "2026-10-22T09:00:00.000Z",
}
MODEL_DRAFT_ORIGIN = {
    "kind": "model_draft", "proposal_id": "suggestion_x", "candidate_id": "suggestion_x_c0", "reviewer": "kai",
    "adopted_at": "2026-10-22T09:00:00Z",
}


def _shape(origin=None, **extra):
    item = {"id": "shape-1", "type": "polygon", "label": "scratch", "category_id": 2,
            "polygon": [[80.0, 100.0], [140.0, 100.0], [140.0, 160.0]], **extra}
    if origin is not None:
        item["origin"] = origin
    return item


def _save(client, image, annotations):
    return client.post("/api/annotations/save", json={
        "image_id": image.stem, "image_path": str(image), "image_width": 256, "image_height": 256,
        "annotations": annotations})


def _read(client, image):
    res = client.get(f"/api/annotations/{image.stem}", params={"file_path": str(image)})
    assert res.status_code == 200, res.text
    return res.json()["annotations"]


def test_the_selector_tells_a_found_outline_and_the_fallback_box_apart(tmp_path):
    found = auto_select_contour(_board_image(tmp_path), seed_x=100, seed_y=120, tolerance=25)
    assert found["fallback"] is False and found["area"] > 0 and len(found["polygon"]) >= 3
    fallback = auto_select_contour(_blank_image(tmp_path), seed_x=2, seed_y=2, tolerance=25)
    assert fallback["fallback"] is True
    xmin, ymin, xmax, ymax = fallback["bbox"]
    assert (xmax - xmin, ymax - ymin) == (25.0, 25.0), "the 12 px box around the click, not a found region"
    assert fallback["seed_point"] == [2.0, 2.0]


def test_the_route_reports_the_fallback_with_the_backend_it_used(client, tmp_path):
    blank = client.post("/api/annotations/auto-select",
                        json={"image_path": str(_blank_image(tmp_path)), "seed_x": 2, "seed_y": 2, "tolerance": 25})
    assert blank.status_code == 200, blank.text
    result = blank.json()["result"]
    assert result["fallback"] is True and result["source"] == "opencv_flood_fill" and len(result["polygon"]) >= 3
    board = client.post("/api/annotations/auto-select",
                        json={"image_path": str(_board_image(tmp_path)), "seed_x": 100, "seed_y": 120, "tolerance": 25})
    assert board.status_code == 200, board.text
    assert board.json()["result"]["fallback"] is False


def test_a_saved_auto_selector_origin_survives_the_round_trip(client, tmp_path):
    image = _board_image(tmp_path)
    fallback_origin = {**AUTO_SELECTOR_ORIGIN, "fallback": True, "seed_point": [3.0, 4.0]}
    saved = _save(client, image, [_shape(), _shape(AUTO_SELECTOR_ORIGIN, id="shape-2"),
                                  _shape(fallback_origin, id="shape-3")])
    assert saved.status_code == 200, saved.text
    drawn, outlined, boxed = _read(client, image)
    assert "origin" not in drawn, "a shape a person drew has no origin key"
    # The save stores the origin as its model dumps it, so the unused box prompt is present as null (as job_id is on a
    # model-draft origin); every value the renderer sent is kept.
    stored_found, stored_fallback = {**AUTO_SELECTOR_ORIGIN, "box": None}, {**fallback_origin, "box": None}
    assert outlined["origin"] == stored_found
    assert boxed["origin"] == stored_fallback, "the fallback mark is kept, so the box stays recognisable after a save"
    # A later save of the same list (what the renderer sends back) keeps every origin unchanged.
    again = _save(client, image, _read(client, image))
    assert again.status_code == 200, again.text
    assert [item.get("origin") for item in _read(client, image)] == [None, stored_found, stored_fallback]


def test_a_model_draft_origin_is_kept_and_still_needs_its_fields(client, tmp_path):
    image = _board_image(tmp_path)
    assert _save(client, image, [_shape(MODEL_DRAFT_ORIGIN)]).status_code == 200
    stored = _read(client, image)[0]["origin"]
    assert stored["kind"] == "model_draft" and stored["proposal_id"] == "suggestion_x"
    assert stored["backend"] == "trained_model"
    # An origin without a kind is a model draft, as every origin was before the auto selector had one.
    unkinded = {key: value for key, value in MODEL_DRAFT_ORIGIN.items() if key != "kind"}
    assert _save(client, image, [_shape(unkinded)]).status_code == 200
    assert _read(client, image)[0]["origin"]["kind"] == "model_draft"
    missing = {key: value for key, value in MODEL_DRAFT_ORIGIN.items() if key != "proposal_id"}
    assert _save(client, image, [_shape(missing)]).status_code == 422


@pytest.mark.parametrize("origin", [
    pytest.param({**AUTO_SELECTOR_ORIGIN, "kind": "sam2_click"}, id="unknown kind"),
    pytest.param({**AUTO_SELECTOR_ORIGIN, "kind": 7}, id="kind that is not a name"),
    pytest.param({key: value for key, value in AUTO_SELECTOR_ORIGIN.items() if key != "created_at"}, id="no created_at"),
    pytest.param({**AUTO_SELECTOR_ORIGIN, "seed_point": [1.0]}, id="click with one coordinate"),
])
def test_an_origin_the_save_cannot_name_is_refused_and_writes_nothing(client, tmp_path, origin):
    image = _board_image(tmp_path)
    assert _save(client, image, [_shape()]).status_code == 200
    assert _save(client, image, [_shape(), _shape(origin, id="shape-2")]).status_code == 422
    assert [item["id"] for item in _read(client, image)] == ["shape-1"], "the refused save left the labels as they were"
