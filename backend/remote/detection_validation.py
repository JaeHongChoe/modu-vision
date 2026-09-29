"""Check that COCO image references survive a portable remote snapshot."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath


def _relative_image_path(value: object, split: str, index: int) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ValueError(f"COCO {split} image {index} has an invalid file_name: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"COCO {split} image {index} must use a relative file_name inside images/{split}: {value!r}")
    return path


def validate_coco_detection_paths(dataset_dir: str | Path) -> dict[str, int]:
    """Validate every train/val image reference before copying a detection dataset.

    Returns the image count per partition. Raises ValueError with the failing
    partition and image when the COCO input cannot be safely moved to a server.
    """
    try:
        root = Path(dataset_dir).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"COCO dataset directory is unavailable: {dataset_dir}") from exc
    if not root.is_dir():
        raise ValueError(f"COCO dataset directory is not a folder: {root}")

    counts: dict[str, int] = {}
    for split in ("train", "val"):
        images_dir = root / "images" / split
        annotation_file = root / f"annotations_{split}.json"
        try:
            resolved_images_dir = images_dir.resolve(strict=True)
            if not resolved_images_dir.is_dir() or not resolved_images_dir.is_relative_to(root):
                raise ValueError(f"COCO {split} images directory escapes the dataset: {images_dir}")
            with annotation_file.open("r", encoding="utf-8") as handle:
                annotation = json.load(handle)
        except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"COCO {split} images or annotations are unavailable or invalid: {exc}") from exc

        rows = annotation.get("images") if isinstance(annotation, dict) else None
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"COCO {split} annotations require a nonempty images list")

        for index, row in enumerate(rows):
            name = row.get("file_name") if isinstance(row, dict) else None
            relative = _relative_image_path(name, split, index)
            image = images_dir.joinpath(*relative.parts)
            try:
                resolved_image = image.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError(f"COCO {split} image {index} is missing: {name!r}") from exc
            if not resolved_image.is_relative_to(resolved_images_dir) or not resolved_image.is_file():
                raise ValueError(f"COCO {split} image {index} escapes images/{split} or is not a file: {name!r}")
        counts[split] = len(rows)

    return counts
