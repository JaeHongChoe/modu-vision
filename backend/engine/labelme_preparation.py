"""Prepare paired LabelMe polygons for ROI segmentation training without editing source files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import numpy as np
from PIL import Image, ImageDraw

from backend.engine.industrial_adapters import find_matching_image, is_valid_labelme_file


def prepare_labelme_segmentation(
    source: Path,
    output: Path,
    image_size: int = 256,
    assignments: Optional[Dict[str, str]] = None,
    seed: int = 42,
) -> Dict[str, int]:
    """Crop around labeled defects and write train/val/test image-mask pairs.

    The crop retains small polygons that would disappear when an entire
    high-resolution inspection photo is resized to model resolution.
    """
    source = Path(source).resolve()
    output = Path(output)
    pairs = []
    for annotation in sorted(source.glob("*.json")):
        if is_valid_labelme_file(annotation, require_image=True):
            image = find_matching_image(annotation)
            if image is not None and image.is_file():
                pairs.append((image, annotation))
    if len(pairs) < 2:
        raise ValueError("At least two paired LabelMe images are needed for train and validation.")

    rng = np.random.default_rng(seed)
    shuffled_indices = rng.permutation(len(pairs))
    val_count = max(1, int(round(len(pairs) * 0.2)))
    default_val = set(shuffled_indices[:val_count].tolist())
    assigned = assignments or {}
    rows = []
    counts = {"train": 0, "val": 0, "test": 0}

    for index, (image_path, annotation_path) in enumerate(pairs):
        data = json.loads(annotation_path.read_text(encoding="utf-8"))
        polygons = [shape.get("points", []) for shape in data.get("shapes", [])
                    if len(shape.get("points", [])) >= 3]
        if not polygons:
            continue
        partition = assigned.get(str(image_path.resolve())) or ("val" if index in default_val else "train")
        if partition not in counts:
            raise ValueError(f"Unknown split partition: {partition}")

        points = [point for polygon in polygons for point in polygon]
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        with Image.open(image_path) as image:
            width, height = image.size
            side = min(width, height, max(image_size, int((max(xs) - min(xs)) * 4),
                                           int((max(ys) - min(ys)) * 4)))
            left = int(max(0, min(width - side, (min(xs) + max(xs) - side) / 2)))
            top = int(max(0, min(height - side, (min(ys) + max(ys) - side) / 2)))
            crop = image.convert("RGB").crop((left, top, left + side, top + side))
            crop = crop.resize((image_size, image_size), Image.Resampling.LANCZOS)

        mask = Image.new("L", (image_size, image_size), 0)
        draw = ImageDraw.Draw(mask)
        for polygon in polygons:
            scaled = [((float(x) - left) * image_size / side,
                       (float(y) - top) * image_size / side) for x, y in polygon]
            draw.polygon(scaled, fill=1)
        if not np.asarray(mask).any():
            raise ValueError(f"Defect polygon vanished during crop: {annotation_path}")

        name = f"sample_{index:05d}.png"
        image_target = output / "images" / partition / name
        mask_target = output / "masks" / partition / name
        image_target.parent.mkdir(parents=True, exist_ok=True)
        mask_target.parent.mkdir(parents=True, exist_ok=True)
        crop.save(image_target)
        mask.save(mask_target)
        counts[partition] += 1
        rows.append({"source_image": str(image_path), "source_json": str(annotation_path),
                     "split": partition, "image": str(image_target), "mask": str(mask_target),
                     "crop_box": [left, top, left + side, top + side]})

    if not counts["train"] or not counts["val"]:
        raise ValueError("The split needs at least one labeled train and validation image.")
    image_files = {p.resolve() for p in source.iterdir() if p.is_file() and not p.name.startswith("._")
                   and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}}
    counts["unlabelled"] = len(image_files - {image.resolve() for image, _ in pairs})
    (output / "source_manifest.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return counts
