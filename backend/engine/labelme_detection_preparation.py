"""Prepare flat LabelMe defect polygons as portable, split COCO detection data."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable, Dict, Optional

from PIL import Image

from backend.engine.labelme_preparation import prepare_labelme_segmentation


def _box(points: object, crop_box: list[int], image_size: int) -> list[float] | None:
    if not isinstance(points, list) or len(points) < 2:
        return None
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
    except (TypeError, ValueError, IndexError):
        return None
    if not all(math.isfinite(value) for value in xs + ys):
        return None
    left, top, right, bottom = crop_box
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise ValueError("Prepared LabelMe crop has invalid dimensions")
    x1 = max(0.0, min(float(image_size), (min(xs) - left) * image_size / width))
    y1 = max(0.0, min(float(image_size), (min(ys) - top) * image_size / height))
    x2 = max(0.0, min(float(image_size), (max(xs) - left) * image_size / width))
    y2 = max(0.0, min(float(image_size), (max(ys) - top) * image_size / height))
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2 - x1, y2 - y1]


def _annotations(row: dict, image_size: int) -> tuple[list[tuple[str, list[float]]], bool]:
    source = json.loads(Path(row["source_json"]).read_text(encoding="utf-8"))
    shapes = source.get("annotations", []) if row["annotation_source"] == "studio" else source.get("shapes", [])
    results: list[tuple[str, list[float]]] = []
    has_normal_marker = any(isinstance(shape, dict) and
                            (shape.get("is_normal") or shape.get("label") == "OK")
                            for shape in shapes)
    for shape in shapes:
        if not isinstance(shape, dict) or shape.get("is_normal") or shape.get("label") == "OK":
            continue
        points = shape.get("polygon") or shape.get("points")
        if not points and isinstance(shape.get("bbox"), list) and len(shape["bbox"]) == 4:
            x1, y1, x2, y2 = shape["bbox"]
            points = [[x1, y1], [x2, y2]]
        bbox = _box(points, row["crop_box"], image_size)
        if bbox is not None:
            label = str(shape.get("label") or "defect").strip() or "defect"
            results.append((label, bbox))
    if results:
        return results, False
    if has_normal_marker:
        return [], True
    # A Studio brush mask can have no polygons. Keep the region trainable.
    with Image.open(row["mask"]) as mask:
        bounds = mask.getbbox()
    if bounds:
        left, top, right, bottom = bounds
        return [("defect", [float(left), float(top), float(right - left), float(bottom - top)])], False
    return [], False


def prepare_labelme_detection(
    source: Path,
    output: Path,
    image_size: int = 256,
    assignments: Optional[Dict[str, str]] = None,
    require_complete_assignments: bool = False,
    cancellation_requested: Optional[Callable[[], bool]] = None,
    annotation_root: Optional[Path] = None,
) -> Dict[str, int]:
    """Freeze Step 1's split and turn each defect polygon into a COCO box.

    The source folder is only read. Images are cropped by the existing LabelMe
    segmentation preparation so tiny inspection defects survive resizing.
    """
    source, output = Path(source), Path(output)
    counts = prepare_labelme_segmentation(
        source, output, image_size=image_size, assignments=assignments,
        require_complete_assignments=require_complete_assignments,
        cancellation_requested=cancellation_requested,
        annotation_root=annotation_root,
    )
    rows = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
    labeled: list[tuple[dict, list[tuple[str, list[float]]]]] = []
    categories: set[str] = set()
    for row in rows:
        if cancellation_requested and cancellation_requested():
            from backend.engine.labelme_preparation import LabelMePreparationCancelled
            raise LabelMePreparationCancelled("LabelMe detection preparation cancelled")
        boxes, is_normal = _annotations(row, image_size)
        if not boxes and not is_normal:
            raise ValueError(f"Prepared detection image has no defect box: {row['source_image']}")
        labeled.append((row, boxes))
        categories.update(label for label, _ in boxes)
    category_ids = {name: index + 1 for index, name in enumerate(sorted(categories))}
    category_rows = [{"id": category_id, "name": name} for name, category_id in category_ids.items()]
    for split in ("train", "val", "test"):
        images, annotations = [], []
        for row, boxes in labeled:
            if row["split"] != split:
                continue
            image_id = len(images) + 1
            images.append({"id": image_id, "file_name": Path(row["image"]).name,
                           "width": image_size, "height": image_size})
            for label, bbox in boxes:
                annotations.append({"id": len(annotations) + 1, "image_id": image_id,
                                    "category_id": category_ids[label], "bbox": bbox,
                                    "area": bbox[2] * bbox[3], "iscrowd": 0})
        if images:
            (output / f"annotations_{split}.json").write_text(
                json.dumps({"images": images, "annotations": annotations,
                            "categories": category_rows}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    return counts
