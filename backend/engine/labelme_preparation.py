"""Prepare paired LabelMe polygons for ROI segmentation training without editing source files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np
from PIL import Image, ImageDraw

from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.industrial_adapters import find_matching_image, is_valid_labelme_file


class LabelMePreparationCancelled(RuntimeError):
    """Raised when the owning training job is cancelled during preparation."""


def prepare_labelme_segmentation(
    source: Path,
    output: Path,
    image_size: int = 256,
    assignments: Optional[Dict[str, str]] = None,
    require_complete_assignments: bool = False,
    seed: int = 42,
    cancellation_requested: Optional[Callable[[], bool]] = None,
    annotation_root: Optional[Path] = None,
) -> Dict[str, int]:
    """Crop around labeled defects and write train/val/test image-mask pairs.

    The crop retains small polygons that would disappear when an entire
    high-resolution inspection photo is resized to model resolution.
    """
    source = Path(source).resolve()
    output = Path(output)
    studio_dir = dataset_annotation_dir(source, annotation_root or Path("./annotations"))
    image_files = sorted(p.resolve() for p in source.iterdir() if p.is_file() and
                         not p.name.startswith("._") and
                         p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"})

    def check_cancelled() -> None:
        if cancellation_requested is not None and cancellation_requested():
            raise LabelMePreparationCancelled("LabelMe preparation cancelled by user request")

    check_cancelled()
    original_by_image = {}
    for annotation in sorted(source.glob("*.json")):
        check_cancelled()
        if is_valid_labelme_file(annotation, require_image=True):
            matching_image = find_matching_image(annotation)
            if matching_image is not None and matching_image.is_file():
                original_by_image[matching_image.resolve()] = annotation
    pairs = []
    for image in image_files:
        check_cancelled()
        studio_json = studio_dir / f"{image.stem}.json"
        source_json = original_by_image.get(image)
        if studio_json.is_file():
            pairs.append((image, studio_json, True))
        elif source_json is not None:
            pairs.append((image, source_json, False))
    if len(pairs) < 2:
        raise ValueError("At least two paired LabelMe images are needed for train and validation.")

    rng = np.random.default_rng(seed)
    shuffled_indices = rng.permutation(len(pairs))
    val_count = max(1, int(round(len(pairs) * 0.2)))
    default_val = set(shuffled_indices[:val_count].tolist())
    assigned = assignments or {}
    rows = []
    counts = {"train": 0, "val": 0, "test": 0}

    used_images = set()
    for index, (image_path, annotation_path, is_studio) in enumerate(pairs):
        check_cancelled()
        data = json.loads(annotation_path.read_text(encoding="utf-8"))
        polygons = []
        studio_mask = None
        is_normal = False
        if is_studio:
            for item in data.get("annotations", []):
                if item.get("is_normal") or item.get("label") == "OK":
                    is_normal = True
                    continue
                points = item.get("polygon") or item.get("points") or []
                if len(points) >= 3:
                    polygons.append(points)
                elif item.get("bbox") and len(item["bbox"]) == 4:
                    x1, y1, x2, y2 = item["bbox"]
                    polygons.append([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
            mask_path = studio_dir / "masks" / f"{image_path.stem}.png"
            if data.get("mask_file") and mask_path.is_file() and not (is_normal and not polygons):
                with Image.open(mask_path) as saved_mask:
                    studio_mask = saved_mask.convert("L")
        else:
            for shape in data.get("shapes", []):
                if shape.get("is_normal") or shape.get("label") == "OK":
                    is_normal = True
                    continue
                points = shape.get("points", [])
                if len(points) >= 3:
                    polygons.append(points)
                elif shape.get("shape_type") == "rectangle" and len(points) == 2:
                    (x1, y1), (x2, y2) = points
                    polygons.append([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
        if not polygons and studio_mask is None and not is_normal:
            continue
        image_key = str(image_path.resolve())
        if require_complete_assignments and image_key not in assigned:
            raise ValueError(f"Saved split is missing newly labeled image: {image_path}")
        partition = assigned.get(image_key) or ("val" if index in default_val else "train")
        if partition not in counts:
            raise ValueError(f"Unknown split partition: {partition}")

        with Image.open(image_path) as image:
            width, height = image.size
            if studio_mask is not None and studio_mask.size != image.size:
                raise ValueError(f"Studio mask dimensions do not match image: {annotation_path}")
            bounds = studio_mask.getbbox() if studio_mask is not None else None
            if bounds:
                min_x, min_y, max_x, max_y = bounds
            elif polygons:
                points = [point for polygon in polygons for point in polygon]
                xs = [float(point[0]) for point in points]
                ys = [float(point[1]) for point in points]
                min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)
            else:
                min_x, min_y, max_x, max_y = 0, 0, width, height
            side = min(width, height, max(image_size, int((max_x - min_x) * 4),
                                           int((max_y - min_y) * 4)))
            left = int(max(0, min(width - side, (min_x + max_x - side) / 2)))
            top = int(max(0, min(height - side, (min_y + max_y - side) / 2)))
            crop = image.convert("RGB").crop((left, top, left + side, top + side))
            crop = crop.resize((image_size, image_size), Image.Resampling.LANCZOS)
        check_cancelled()

        if studio_mask is not None:
            mask = studio_mask.crop((left, top, left + side, top + side)).resize(
                (image_size, image_size), Image.Resampling.NEAREST).point(lambda value: 1 if value else 0)
        else:
            mask = Image.new("L", (image_size, image_size), 0)
            draw = ImageDraw.Draw(mask)
            for polygon in polygons:
                scaled = [((float(x) - left) * image_size / side,
                           (float(y) - top) * image_size / side) for x, y in polygon]
                draw.polygon(scaled, fill=1)
        if not is_normal and not np.asarray(mask).any():
            raise ValueError(f"Defect polygon vanished during crop: {annotation_path}")
        check_cancelled()

        name = f"sample_{index:05d}.png"
        image_target = output / "images" / partition / name
        mask_target = output / "masks" / partition / name
        image_target.parent.mkdir(parents=True, exist_ok=True)
        mask_target.parent.mkdir(parents=True, exist_ok=True)
        crop.save(image_target)
        mask.save(mask_target)
        counts[partition] += 1
        used_images.add(image_path.resolve())
        rows.append({"source_image": str(image_path), "source_json": str(annotation_path),
                     "annotation_source": "studio" if is_studio else "labelme",
                     "split": partition, "image": str(image_target), "mask": str(mask_target),
                     "crop_box": [left, top, left + side, top + side]})

    if not counts["train"] or not counts["val"]:
        raise ValueError("The split needs at least one labeled train and validation image.")
    check_cancelled()
    counts["unlabelled"] = len(set(image_files) - used_images)
    (output / "source_manifest.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return counts
