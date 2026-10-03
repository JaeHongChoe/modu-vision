"""External LabelMe files use the same decoding in the gallery and the annotation API."""
import json
import locale

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import routes_annotation


def _source(tmp_path, monkeypatch, encoding):
    source = tmp_path / "images"
    source.mkdir()
    image = source / "part.png"
    Image.new("RGB", (32, 24)).save(image)
    label = image.with_suffix(".json")
    label.write_bytes(json.dumps({
        "imageWidth": 32, "imageHeight": 24,
        "shapes": [{"label": "찍힘", "shape_type": "rectangle", "points": [[2, 3], [9, 11]]}],
    }, ensure_ascii=False).encode(encoding))
    monkeypatch.setattr(routes_annotation, "ANNOTATIONS_DIR", tmp_path / "studio")
    app = FastAPI()
    app.include_router(routes_annotation.router)
    return TestClient(app), image, label


@pytest.mark.parametrize("encoding,platform_encoding", [
    ("utf-8", "cp949"),
    ("utf-8-sig", "UTF-8"),
    ("cp949", "cp949"),
])
def test_external_labelme_encoding_preserves_labels_and_geometry(tmp_path, monkeypatch, encoding, platform_encoding):
    client, image, label = _source(tmp_path, monkeypatch, encoding)
    original = label.read_bytes()
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: platform_encoding)

    response = client.get("/api/annotations/part", params={"file_path": str(image)})

    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["image_width"], result["image_height"]) == (32, 24)
    assert len(result["annotations"]) == 1
    assert result["annotations"][0]["label"] == "찍힘"
    assert result["annotations"][0]["bbox"] == [2, 3, 9, 11]
    assert label.read_bytes() == original, "opening a source label must not rewrite it"


def test_unsupported_source_encoding_returns_a_conversion_hint_not_a_server_error(tmp_path, monkeypatch):
    client, image, label = _source(tmp_path, monkeypatch, "cp949")
    original = label.read_bytes()
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "UTF-8")

    response = client.get("/api/annotations/part", params={"file_path": str(image)})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "part.json" in detail and "save it as UTF-8" in detail
    assert label.read_bytes() == original
