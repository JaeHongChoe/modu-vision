import json
from pathlib import Path

from PIL import Image

from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.labelme_detection_preparation import prepare_labelme_detection


def _example(folder: Path, name: str, label: str) -> Path:
    image = folder / f"{name}.png"
    Image.new("RGB", (128, 128), "white").save(image)
    (folder / f"{name}.json").write_text(json.dumps({
        "imagePath": image.name,
        "imageWidth": 128,
        "imageHeight": 128,
        "shapes": [{"label": label, "shape_type": "polygon", "points": [[40, 40], [52, 40], [52, 52], [40, 52]]}],
    }), encoding="utf-8")
    return image


def test_labelme_polygons_prepare_portable_split_coco_without_changing_sources(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    train = _example(source, "train", "Bow")
    val = _example(source, "val", "Bow")
    output = tmp_path / "prepared"

    counts = prepare_labelme_detection(
        source, output, image_size=128,
        assignments={str(train.resolve()): "train", str(val.resolve()): "val"},
        require_complete_assignments=True,
        annotation_root=tmp_path / "studio",
    )

    assert counts["train"] == counts["val"] == 1
    for split in ("train", "val"):
        data = json.loads((output / f"annotations_{split}.json").read_text())
        assert data["categories"] == [{"id": 1, "name": "Bow"}]
        assert len(data["images"]) == len(data["annotations"]) == 1
        name = data["images"][0]["file_name"]
        assert (output / "images" / split / name).is_file()
        x, y, width, height = data["annotations"][0]["bbox"]
        assert 0 <= x < 128 and 0 <= y < 128
        assert width > 0 and height > 0
    assert (source / "train.json").is_file()
    assert (output / "source_manifest.json").is_file()


def test_labelme_rectangle_is_a_detection_box(tmp_path):
    source = tmp_path / "rectangles"
    source.mkdir()
    assignments = {}
    for split in ("train", "val"):
        image = source / f"{split}.png"
        Image.new("RGB", (128, 128), "white").save(image)
        image.with_suffix(".json").write_text(json.dumps({
            "imagePath": image.name,
            "shapes": [{"label": "Scratch", "shape_type": "rectangle", "points": [[20, 30], [40, 50]]}],
        }), encoding="utf-8")
        assignments[str(image.resolve())] = split

    output = tmp_path / "prepared"
    counts = prepare_labelme_detection(source, output, image_size=128,
                                       assignments=assignments, require_complete_assignments=True,
                                       annotation_root=tmp_path / "studio")
    assert counts["train"] == counts["val"] == 1
    assert json.loads((output / "annotations_val.json").read_text())["annotations"][0]["bbox"] == [20.0, 30.0, 20.0, 20.0]


def test_studio_normal_image_remains_empty_detection_target(tmp_path):
    source = tmp_path / "normals"
    source.mkdir()
    defect = _example(source, "defect", "Bow")
    normal = source / "normal.png"
    Image.new("RGB", (128, 128), "white").save(normal)
    annotation_root = tmp_path / "studio"
    studio_folder = dataset_annotation_dir(source, annotation_root)
    studio_folder.mkdir(parents=True)
    (studio_folder / "normal.json").write_text(json.dumps({
        "image_id": "normal", "annotations": [
            {"label": "OK", "is_normal": True, "bbox": [30, 30, 60, 60]},
        ],
    }), encoding="utf-8")

    output = tmp_path / "prepared"
    counts = prepare_labelme_detection(source, output, image_size=128,
        assignments={str(defect.resolve()): "train", str(normal.resolve()): "val"},
        require_complete_assignments=True, annotation_root=annotation_root)
    assert counts["train"] == counts["val"] == 1
    assert json.loads((output / "annotations_val.json").read_text())["annotations"] == []
