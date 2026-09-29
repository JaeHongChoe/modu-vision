"""Studio edits must be isolated from source LabelMe files and used by training."""

import hashlib
import json

from PIL import Image

from backend.api import routes_annotation
from backend.engine.labelme_preparation import prepare_labelme_segmentation


def _image(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jpg"
    Image.new("RGB", (64, 64), (100, 100, 100)).save(path)
    return path


def _labelme(path):
    annotation = path.with_suffix(".json")
    annotation.write_text(json.dumps({
        "imagePath": path.name, "imageWidth": 64, "imageHeight": 64,
        "shapes": [{"label": "Bow", "shape_type": "polygon",
                    "points": [[10, 10], [20, 10], [15, 20]]}],
    }), encoding="utf-8")
    return annotation


def _save_studio(path, points, label="Bow"):
    return routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(
        image_id=path.stem,
        image_path=str(path),
        image_width=64,
        image_height=64,
        annotations=[routes_annotation.AnnotationItem(
            type="polygon", label=label, category_id=1, polygon=points,
        )],
    ))


def test_studio_edit_overrides_source_labelme_during_training_without_source_mutation(monkeypatch, tmp_path):
    source = tmp_path / "source"
    first = _image(source, "ng_0001")
    second = _image(source, "ng_0002")
    original = _labelme(first)
    _labelme(second)
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    annotation_root = tmp_path / "studio_annotations"
    monkeypatch.setattr(routes_annotation, "ANNOTATIONS_DIR", annotation_root)

    _save_studio(first, [[42, 42], [52, 42], [47, 52]], label="Scratch")
    restored = routes_annotation.get_annotations(first.stem, file_path=str(first))
    assert restored["annotations"][0]["label"] == "Scratch"

    result = prepare_labelme_segmentation(source, tmp_path / "prepared", image_size=64,
                                          annotation_root=annotation_root)
    manifest = json.loads((tmp_path / "prepared" / "source_manifest.json").read_text())
    edited = next(row for row in manifest if row["source_image"] == str(first))
    assert result["train"] + result["val"] == 2
    assert edited["annotation_source"] == "studio"
    assert edited["source_json"].startswith(str(annotation_root))
    assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash


def test_newly_labeled_image_without_labelme_file_joins_training(monkeypatch, tmp_path):
    source = tmp_path / "source"
    first = _image(source, "ng_0001")
    second = _image(source, "ng_0002")
    third = _image(source, "ng_0003")
    _labelme(first)
    _labelme(second)
    annotation_root = tmp_path / "studio_annotations"
    monkeypatch.setattr(routes_annotation, "ANNOTATIONS_DIR", annotation_root)

    _save_studio(third, [[40, 40], [50, 40], [45, 50]])
    result = prepare_labelme_segmentation(source, tmp_path / "prepared", image_size=64,
                                          annotation_root=annotation_root)
    assert result["train"] + result["val"] == 3
    assert result["unlabelled"] == 0
    assert not third.with_suffix(".json").exists()


def test_identical_image_ids_from_different_datasets_do_not_share_edits(monkeypatch, tmp_path):
    first = _image(tmp_path / "a", "ng_0001")
    second = _image(tmp_path / "b", "ng_0001")
    monkeypatch.setattr(routes_annotation, "ANNOTATIONS_DIR", tmp_path / "studio_annotations")
    _save_studio(first, [[1, 1], [10, 1], [5, 10]], label="Bow")
    _save_studio(second, [[40, 40], [50, 40], [45, 50]], label="Scratch")

    assert routes_annotation.get_annotations(first.stem, file_path=str(first))["annotations"][0]["label"] == "Bow"
    assert routes_annotation.get_annotations(second.stem, file_path=str(second))["annotations"][0]["label"] == "Scratch"


def test_unlabelled_image_does_not_inherit_labelme_from_another_dataset(monkeypatch, tmp_path):
    first = _image(tmp_path / "a", "ng_0001")
    second = _image(tmp_path / "b", "ng_0001")
    _labelme(second)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_annotation, "ANNOTATIONS_DIR", tmp_path / "studio_annotations")

    restored = routes_annotation.get_annotations(first.stem, file_path=str(first))
    assert restored["annotations"] == []
