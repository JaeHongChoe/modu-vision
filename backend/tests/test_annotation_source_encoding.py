"""External LabelMe files use the same decoding in the gallery and the annotation API."""
import json
import locale
import hashlib

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


SOURCE_ENCODINGS = [("utf-8-sig", "UTF-8"), ("cp949", "cp949")]


@pytest.mark.parametrize("encoding,platform_encoding", SOURCE_ENCODINGS)
def test_industrial_labelme_consumers_preserve_internal_image_path_and_polygon(tmp_path, monkeypatch, encoding, platform_encoding):
    from backend.engine.industrial_adapters import (
        LabelMeParser, LabelMeRasterizer, find_matching_image, is_valid_labelme_file,
    )
    image = tmp_path / "제품.png"
    Image.new("RGB", (32, 24)).save(image)
    label = tmp_path / "external-annotation.json"
    label.write_bytes(json.dumps({
        "imagePath": image.name, "imageWidth": 32, "imageHeight": 24,
        "shapes": [{"label": "찍힘", "shape_type": "polygon", "points": [[2, 3], [9, 3], [9, 11], [2, 11]]}],
    }, ensure_ascii=False).encode(encoding))
    original = label.read_bytes()
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: platform_encoding)

    # The differing stems prevent a matching-stem fallback from hiding a decode failure.
    assert find_matching_image(label) == image
    assert is_valid_labelme_file(label)
    parsed = LabelMeParser.parse_file(label)
    assert (parsed["file_name"], parsed["width"], parsed["height"]) == (image.name, 32, 24)
    assert parsed["boxes"][0]["category_name"] == "찍힘"
    assert parsed["boxes"][0]["bbox_voc"] == [2, 3, 9, 11]
    mask = LabelMeRasterizer.rasterize_labelme_file(label)
    assert mask.shape == (24, 32) and mask[5, 5] == 1 and mask[0, 0] == 0
    assert label.read_bytes() == original


@pytest.mark.parametrize("encoding,platform_encoding", SOURCE_ENCODINGS)
def test_gallery_class_filter_retains_source_labels_and_dimensions(tmp_path, monkeypatch, encoding, platform_encoding):
    from backend.api import routes_dataset
    _, image, label = _source(tmp_path, monkeypatch, encoding)
    original = label.read_bytes()
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: platform_encoding)
    monkeypatch.setattr(routes_dataset, "STUDIO_ANNOTATIONS_DIR", tmp_path / "studio")
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "splits")
    app = FastAPI()
    app.include_router(routes_dataset.router)
    response = TestClient(app).get("/api/dataset/images", params={
        "folder_path": str(image.parent), "class_name": "찍힘",
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 1
    row = result["items"][0]
    assert row["labels"] == ["찍힘"] and (row["width"], row["height"]) == (32, 24)
    assert label.read_bytes() == original


@pytest.mark.parametrize("encoding,platform_encoding", SOURCE_ENCODINGS)
def test_flat_labelme_class_counts_keep_source_categories(tmp_path, monkeypatch, encoding, platform_encoding):
    from backend.api import routes_dataset
    _, image, label = _source(tmp_path, monkeypatch, encoding)
    original = label.read_bytes()
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: platform_encoding)
    monkeypatch.setattr(routes_dataset, "STUDIO_ANNOTATIONS_DIR", tmp_path / "studio")
    assert routes_dataset._flat_labelme_class_counts(image.parent, {image.resolve()}) == {"찍힘": 1}
    assert label.read_bytes() == original


@pytest.mark.parametrize("consumer", ["parser", "loader", "summary"])
@pytest.mark.parametrize("encoding,platform_encoding", SOURCE_ENCODINGS)
def test_coco_consumers_preserve_category_names_and_geometry(tmp_path, monkeypatch, consumer, encoding, platform_encoding):
    from backend.engine.dataset_loaders import CocoJsonParser, DetectionDataset, inspect_dataset
    source = tmp_path / "coco"
    document = {
        "images": [{"id": 1, "file_name": "제품.png", "width": 32, "height": 24}],
        "categories": [{"id": 7, "name": "찍힘"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 7, "bbox": [2, 3, 7, 8], "area": 56, "iscrowd": 0}],
    }
    for split in ("train", "val"):
        folder = source / "images" / split
        folder.mkdir(parents=True)
        Image.new("RGB", (32, 24)).save(folder / "제품.png")
        (source / f"annotations_{split}.json").write_bytes(json.dumps(document, ensure_ascii=False).encode(encoding))
    original = {p.name: p.read_bytes() for p in source.glob("*.json")}
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: platform_encoding)
    if consumer == "parser":
        parsed = CocoJsonParser.parse_file(source / "annotations_train.json")
        assert parsed["categories"] == {7: "찍힘"}
        assert parsed["annotations_by_image"][1][0]["bbox"] == [2, 3, 7, 8]
    elif consumer == "loader":
        dataset = DetectionDataset(images_dir=source / "images/train", annotation_file=source / "annotations_train.json")
        tensor, target = dataset[0]
        assert dataset.categories == {1: "찍힘"}
        assert list(tensor.shape) == [3, 24, 32]
        assert target["boxes"].tolist() == [[2, 3, 9, 11]] and target["labels"].tolist() == [1]
    else:
        summary = inspect_dataset(source, "detection")
        assert summary.total_images == 2 and summary.classes == {"찍힘": 2}
        assert summary.split_counts == {"train": 1, "val": 1}
    assert {p.name: p.read_bytes() for p in source.glob("*.json")} == original


def _frozen_source_binding(tmp_path, original):
    source = tmp_path / "source"
    source.mkdir()
    label = source / "part.json"
    label.write_bytes(original)
    version = tmp_path / "version"
    version.mkdir()
    backup = version / "source-label.json"
    backup.write_bytes(original)
    row = {
        "origin": "source", "kind": "label", "relative_path": "part.json",
        "snapshot_path": backup.name, "source_path": str(label),
        "sha256": hashlib.sha256(original).hexdigest(),
    }
    (version / "manifest.json").write_text(json.dumps({"files": [row]}), encoding="utf-8")
    return {"version_dir": str(version)}, source, backup, row


@pytest.mark.parametrize("encoding,platform_encoding", SOURCE_ENCODINGS)
def test_frozen_training_view_keeps_original_bytes_and_source_label_geometry(tmp_path, monkeypatch, encoding, platform_encoding):
    from backend.engine.annotation_storage import dataset_annotation_dir
    from backend.engine.training_provenance import frozen_annotation_root
    original = json.dumps({
        "imageWidth": 32, "imageHeight": 24,
        "shapes": [{"label": "찍힘", "shape_type": "rectangle", "points": [[2, 3], [9, 11]]}],
    }, ensure_ascii=False).encode(encoding)
    binding, source, backup, row = _frozen_source_binding(tmp_path, original)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: platform_encoding)
    root = frozen_annotation_root(binding, source, tmp_path / "job")
    copied = dataset_annotation_dir(source, root, use_scope=False) / "part.json"
    assert copied.is_file(), "a source label must not silently disappear during training preparation"
    result = json.loads(copied.read_text(encoding="utf-8"))
    assert result["annotations"][0]["label"] == "찍힘"
    assert result["annotations"][0]["bbox"] == [2, 3, 9, 11]
    assert result["frozen_source_sha256"] == row["sha256"]
    assert binding["frozen_source_labels"] == [str(source / "part.json")]
    assert backup.read_bytes() == (source / "part.json").read_bytes() == original


def test_unsupported_frozen_source_encoding_is_refused_before_training_instead_of_dropped(tmp_path, monkeypatch):
    from backend.engine.source_text import SourceTextError
    from backend.engine.training_provenance import frozen_annotation_root
    original = json.dumps({"shapes": [{"label": "찍힘", "points": [[2, 3], [9, 11]]}]}, ensure_ascii=False).encode("cp949")
    binding, source, backup, _ = _frozen_source_binding(tmp_path, original)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "UTF-8")
    with pytest.raises(SourceTextError, match="save it as UTF-8"):
        frozen_annotation_root(binding, source, tmp_path / "job")
    assert "frozen_source_labels" not in binding
    assert backup.read_bytes() == (source / "part.json").read_bytes() == original
