"""Lossless class rasters and component filters in source-image pixels."""

from __future__ import annotations

import base64
import math
import zlib
from typing import Any

import cv2
import numpy as np


def encoded_array(value: np.ndarray, dtype: str) -> dict[str, Any]:
    array = np.asarray(value, dtype=np.dtype(dtype))
    return {
        "dtype": dtype,
        "encoding": "zlib_base64",
        "shape": list(array.shape),
        "data": base64.b64encode(zlib.compress(array.tobytes())).decode("ascii"),
    }


def decoded_array(value: dict[str, Any]) -> np.ndarray:
    if value.get("encoding") != "zlib_base64" or value.get("dtype") not in (
        "uint8",
        "float32",
    ):
        raise ValueError("Unsupported segmentation raster encoding")
    shape = value.get("shape")
    if (
        not isinstance(shape, list)
        or len(shape) != 2
        or any(type(v) is not int or v < 1 for v in shape)
    ):
        raise ValueError("Invalid segmentation raster shape")
    raw = zlib.decompress(base64.b64decode(value["data"], validate=True))
    dtype = np.dtype(value["dtype"])
    if len(raw) != math.prod(shape) * dtype.itemsize:
        raise ValueError("Segmentation raster bytes do not match its shape")
    return np.frombuffer(raw, dtype=dtype).reshape(shape)


def validate_segmentation_params(params: dict[str, Any]) -> None:
    ids = params.get("class_ids")
    if ids is not None and (
        not isinstance(ids, list)
        or not ids
        or any(type(v) is not int or v < 1 for v in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError(
            "Segmentation class_ids must contain unique foreground class integers"
        )
    names = params.get("class_names")
    if names is not None and (
        not isinstance(names, list)
        or len(names) < 2
        or any(not isinstance(v, str) or not v.strip() for v in names)
    ):
        raise ValueError(
            "Segmentation class_names must include background and foreground names"
        )
    rules = params.get("class_rules", [])
    if not isinstance(rules, list):
        raise ValueError("Segmentation class_rules must be a list")
    seen = set()
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or type(rule.get("class_id")) is not int
            or rule["class_id"] < 1
            or rule["class_id"] in seen
        ):
            raise ValueError(
                "Segmentation class rules require unique foreground class IDs"
            )
        seen.add(rule["class_id"])
        threshold = rule.get("probability_threshold")
        if "probability_threshold" in rule and (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold)
            or not 0 <= threshold <= 1
        ):
            raise ValueError(
                "Segmentation probability threshold must be finite and from 0 to 1"
            )
        for key in ("min_area_px", "max_area_px"):
            if key in rule and (type(rule[key]) is not int or rule[key] < 1):
                raise ValueError(
                    "Segmentation component area limits must be positive integers"
                )
        if rule.get("max_area_px", math.inf) < rule.get("min_area_px", 1):
            raise ValueError("Segmentation maximum area is below its minimum")
    for key in ("min_defect_area_px", "max_defect_area_px"):
        minimum = 0 if key == "min_defect_area_px" else 1
        if key in params and (type(params[key]) is not int or params[key] < minimum):
            raise ValueError(
                "Segmentation defect area limits must be nonnegative minimum and positive maximum integers"
            )
    if params.get("max_defect_area_px", math.inf) < params.get("min_defect_area_px", 0):
        raise ValueError("Segmentation maximum defect area is below its minimum")


def filter_components(
    mask: np.ndarray, minimum: int = 1, maximum: int | None = None
) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    accepted = [
        index
        for index in range(1, count)
        if stats[index, cv2.CC_STAT_AREA] >= minimum
        and (maximum is None or stats[index, cv2.CC_STAT_AREA] <= maximum)
    ]
    return np.isin(labels, accepted).astype(np.uint8)


def class_evidence(
    probabilities: np.ndarray,
    pixels: np.ndarray,
    bbox: list[int],
    names: list[str],
    threshold: float,
    params: dict[str, Any],
) -> tuple[list[dict], dict[int, np.ndarray], np.ndarray]:
    """Keep every channel; only configured foreground classes form the union."""
    validate_segmentation_params(params)
    probabilities = np.asarray(probabilities, dtype=np.float32)
    if (
        probabilities.ndim != 3
        or probabilities.shape[0] < 2
        or not np.isfinite(probabilities).all()
    ):
        raise ValueError("Segmentation model must return finite class probabilities")
    h, w = pixels.shape[:2]
    classes = probabilities.shape[0]
    if names and len(names) != classes:
        raise ValueError("Segmentation class names do not match model channels")
    names = names or (
        ["background", "defect"]
        if classes == 2
        else ["background", *[f"class_{i}" for i in range(1, classes)]]
    )
    rules = {row["class_id"]: row for row in params.get("class_rules", [])}
    selected = params.get("class_ids", list(range(1, classes)))
    if any(index >= classes for index in [*selected, *rules]):
        raise ValueError("Segmentation class selection is outside model channels")
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    entries = []
    masks = {}
    union = np.zeros((h, w), np.uint8)
    transform = [[1, 0, bbox[0]], [0, 1, bbox[1]], [0, 0, 1]]
    for index in range(classes):
        probability = probabilities[index]
        if probability.shape != (h, w):
            probability = cv2.resize(
                probability, (w, h), interpolation=cv2.INTER_LINEAR
            )
        rule = rules.get(index, {})
        raw = (probability > rule.get("probability_threshold", threshold)).astype(
            np.uint8
        )
        mask = filter_components(
            raw, rule.get("min_area_px", 1), rule.get("max_area_px")
        )
        area = int(mask.sum())
        masks[index] = mask
        if index in selected:
            union |= mask
        entries.append(
            {
                "class_id": index,
                "class_name": names[index],
                "area_px": area,
                "raw_area_px": int(raw.sum()),
                "probability_threshold": rule.get("probability_threshold", threshold),
                "confidence": float(probability.max()),
                "mean_grayscale": float(gray[mask > 0].mean()) if area else None,
                "selected": index in selected,
                "bbox": list(bbox),
                "source_transform": transform,
                "mask": encoded_array(mask, "uint8"),
                "probability": encoded_array(probability, "float32"),
            }
        )
    return entries, masks, union


def remap_class_evidence(
    entries: list[dict],
    transform: np.ndarray,
    source_shape: tuple[int, int],
    bbox: list[int],
    source_pixels: np.ndarray,
    params: dict[str, Any],
) -> tuple[list[dict], dict[int, np.ndarray]]:
    """Reproject local class rasters to a clipped original-image bounding rectangle."""
    h, w = source_shape
    x1, y1, x2, y2 = bbox
    mapped = []
    masks = {}
    gray = cv2.cvtColor(source_pixels, cv2.COLOR_RGB2GRAY)
    rules = {row["class_id"]: row for row in params.get("class_rules", [])}
    for row in entries:
        local_probability = decoded_array(row["probability"])
        raw = cv2.warpPerspective(
            (local_probability > row["probability_threshold"]).astype(np.uint8),
            transform,
            (w, h),
            flags=cv2.INTER_NEAREST,
        )[y1:y2, x1:x2]
        rule = rules.get(row["class_id"], {})
        mask = filter_components(
            raw, rule.get("min_area_px", 1), rule.get("max_area_px")
        )
        probability = cv2.warpPerspective(
            local_probability, transform, (w, h), flags=cv2.INTER_LINEAR
        )[y1:y2, x1:x2]
        area = int(mask.sum())
        masks[row["class_id"]] = mask
        mapped.append(
            {
                **row,
                "bbox": list(bbox),
                "source_transform": [[1, 0, x1], [0, 1, y1], [0, 0, 1]],
                "area_px": area,
                "raw_area_px": int(raw.sum()),
                "mask": encoded_array(mask, "uint8"),
                "probability": encoded_array(probability, "float32"),
                "mean_grayscale": (
                    float(gray[y1:y2, x1:x2][mask > 0].mean()) if area else None
                ),
            }
        )
    return mapped, masks
