"""COCO paths must remain usable inside a copied remote training snapshot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _detection_tree(root: Path) -> Path:
    for split in ("train", "val"):
        images = root / "images" / split / "nested"
        images.mkdir(parents=True)
        (images / f"{split}.png").write_bytes(b"image")
        (root / f"annotations_{split}.json").write_text(
            json.dumps({"images": [{"id": 1, "file_name": f"nested/{split}.png"}]}),
            encoding="utf-8",
        )
    return root


def _set_image_name(root: Path, split: str, value: object) -> None:
    (root / f"annotations_{split}.json").write_text(
        json.dumps({"images": [{"id": 1, "file_name": value}]}), encoding="utf-8",
    )


def test_accepts_nested_relative_images_for_both_partitions(tmp_path):
    from backend.remote.detection_validation import validate_coco_detection_paths

    root = _detection_tree(tmp_path / "detection")
    assert validate_coco_detection_paths(root) == {"train": 1, "val": 1}


@pytest.mark.parametrize("split,bad_name", [
    ("train", "/tmp/image.png"),
    ("val", "../train/nested/train.png"),
    ("train", "nested/../nested/train.png"),
    ("val", "C:\\images\\val.png"),
    ("train", "\\\\server\\share\\image.png"),
    ("val", "nested/missing.png"),
    ("train", ""),
    ("val", 7),
])
def test_rejects_unportable_or_missing_image_references(tmp_path, split, bad_name):
    from backend.remote.detection_validation import validate_coco_detection_paths

    root = _detection_tree(tmp_path / "detection")
    _set_image_name(root, split, bad_name)

    with pytest.raises(ValueError, match=split):
        validate_coco_detection_paths(root)


def test_rejects_image_symlink_pointing_outside_snapshot(tmp_path):
    from backend.remote.detection_validation import validate_coco_detection_paths

    root = _detection_tree(tmp_path / "detection")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"external")
    link = root / "images" / "train" / "nested" / "linked.png"
    link.symlink_to(outside)
    _set_image_name(root, "train", "nested/linked.png")

    with pytest.raises(ValueError, match="train"):
        validate_coco_detection_paths(root)


def test_rejects_symlink_into_other_partition_even_inside_snapshot(tmp_path):
    from backend.remote.detection_validation import validate_coco_detection_paths

    root = _detection_tree(tmp_path / "detection")
    link = root / "images" / "train" / "nested" / "linked.png"
    link.symlink_to(root / "images" / "val" / "nested" / "val.png")
    _set_image_name(root, "train", "nested/linked.png")

    with pytest.raises(ValueError, match="train"):
        validate_coco_detection_paths(root)


def test_rejects_malformed_or_empty_annotation_images(tmp_path):
    from backend.remote.detection_validation import validate_coco_detection_paths

    root = _detection_tree(tmp_path / "detection")
    (root / "annotations_val.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError, match="val"):
        validate_coco_detection_paths(root)

    (root / "annotations_val.json").write_text('{"images": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="val"):
        validate_coco_detection_paths(root)
