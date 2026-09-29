"""A broken adjacent LabelMe file must never look like an empty annotation."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from PIL import Image

from backend.api import routes_annotation
from backend.main import create_app


def _client_and_image(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    image = source / "part.png"
    Image.new("RGB", (32, 24)).save(image)
    monkeypatch.setattr(routes_annotation, "ANNOTATIONS_DIR", tmp_path / "studio_annotations")
    app = create_app(project_dir=str(tmp_path / "project"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    return client, image


def _get(client, image):
    return client.get(f"/api/annotations/{image.stem}", params={"file_path": str(image)})


def test_corrupt_adjacent_labelme_json_returns_error_instead_of_empty_labels(tmp_path, monkeypatch):
    client, image = _client_and_image(tmp_path, monkeypatch)
    image.with_suffix(".json").write_text('{"shapes": [', encoding="utf-8")

    response = _get(client, image)

    assert response.status_code == 422
    assert "LabelMe" in str(response.json()["detail"])


def test_nonempty_unconvertible_labelme_shapes_return_error(tmp_path, monkeypatch):
    client, image = _client_and_image(tmp_path, monkeypatch)
    image.with_suffix(".json").write_text(json.dumps({
        "imageWidth": 32, "imageHeight": 24,
        "shapes": [{"label": "Defect", "shape_type": "point", "points": [[4, 5]]}],
    }), encoding="utf-8")

    response = _get(client, image)

    assert response.status_code == 422
    assert "shape" in str(response.json()["detail"]).lower()


def test_valid_labelme_polygon_and_intentionally_empty_shapes_load(tmp_path, monkeypatch):
    client, image = _client_and_image(tmp_path, monkeypatch)
    labelme = image.with_suffix(".json")
    labelme.write_text(json.dumps({
        "imageWidth": 32, "imageHeight": 24,
        "shapes": [{"label": "Scratch", "shape_type": "polygon", "points": [[1, 2], [5, 2], [5, 6]]}],
    }), encoding="utf-8")

    loaded = _get(client, image)

    assert loaded.status_code == 200
    assert loaded.json()["image_width"] == 32
    assert loaded.json()["annotations"][0]["polygon"] == [[1, 2], [5, 2], [5, 6]]

    labelme.write_text(json.dumps({"imageWidth": 32, "imageHeight": 24, "shapes": []}), encoding="utf-8")
    empty = _get(client, image)
    assert empty.status_code == 200
    assert empty.json()["annotations"] == []
