"""Validated structure/defect and OCR rules; measurements remain distinct from absence."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

import cv2
import numpy as np


def validate_blob_rules(params: Mapping[str, Any]) -> None:
    if params.get("rule_mode", "defect_presence") not in (
        "defect_presence",
        "required_structure",
    ):
        raise ValueError("Blob rule_mode must be defect_presence or required_structure")
    for key in ("min_blob_area_px", "min_blob_count_for_ng"):
        value = params.get(key, 1)
        if type(value) is not int or value < 1:
            raise ValueError("Blob area and count limits must be positive integers")
    ids = params.get("class_ids")
    if ids is not None and (
        not isinstance(ids, list)
        or not ids
        or any(type(v) is not int or v < 1 for v in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Blob class_ids must contain unique foreground integers")
    rules = params.get("class_rules", [])
    if not isinstance(rules, list):
        raise ValueError("Blob class_rules must be a list")
    seen = set()
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or type(rule.get("class_id")) is not int
            or rule["class_id"] < 1
            or rule["class_id"] in seen
        ):
            raise ValueError("Blob class rules require unique foreground class IDs")
        seen.add(rule["class_id"])
        for key in ("min_count", "max_count", "min_area_px", "max_area_px"):
            if key in rule and (type(rule[key]) is not int or rule[key] < 0):
                raise ValueError(
                    "Blob class count/area bounds must be nonnegative integers"
                )
        for key in ("min_mean_grayscale", "max_mean_grayscale"):
            value = rule.get(key)
            if key in rule and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 255
            ):
                raise ValueError(
                    "Blob grayscale bounds must be finite and from 0 to 255"
                )
        for low, high in (
            ("min_count", "max_count"),
            ("min_area_px", "max_area_px"),
            ("min_mean_grayscale", "max_mean_grayscale"),
        ):
            if low in rule and high in rule and rule[low] > rule[high]:
                raise ValueError("Blob maximum bound is below its minimum")


def measure_blob_rules(
    pixels: np.ndarray,
    masks: Mapping[int, np.ndarray],
    names: Mapping[int, str],
    params: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Required bounds describe total selected area; defect bounds filter components."""
    validate_blob_rules(params)
    mode = params.get("rule_mode", "defect_presence")
    rules = {r["class_id"]: r for r in params.get("class_rules", [])}
    ids = params.get("class_ids") or list(rules) or list(masks)
    if not ids or any(i not in masks for i in ids):
        raise ValueError(
            "Blob has no measured segmentation evidence for the selected class"
        )
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    measurements = []
    failed = False
    for index in ids:
        mask = masks[index]
        if mask.shape != gray.shape:
            mask = cv2.resize(
                mask, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST
            )
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            (mask > 0).astype(np.uint8), connectivity=8
        )
        rule = rules.get(index, {})
        components = []
        for component in range(1, count):
            area = int(stats[component, cv2.CC_STAT_AREA])
            if area < params.get("min_blob_area_px", 1):
                continue
            mean = float(gray[labels == component].mean())
            if mode == "defect_presence" and not (
                rule.get("min_area_px", 0) <= area <= rule.get("max_area_px", math.inf)
                and rule.get("min_mean_grayscale", 0)
                <= mean
                <= rule.get("max_mean_grayscale", 255)
            ):
                continue
            components.append({"area_px": area, "mean_grayscale": mean})
        amount = len(components)
        total = sum(c["area_px"] for c in components)
        mean = (
            sum(c["area_px"] * c["mean_grayscale"] for c in components) / total
            if total
            else None
        )
        if mode == "required_structure":
            violations = []
            for field, value, lower, upper in (
                (
                    "count",
                    amount,
                    rule.get("min_count", 1),
                    rule.get("max_count", math.inf),
                ),
                (
                    "area_px",
                    total,
                    rule.get("min_area_px", 0),
                    rule.get("max_area_px", math.inf),
                ),
            ):
                if not lower <= value <= upper:
                    violations.append(field)
            if "min_mean_grayscale" in rule or "max_mean_grayscale" in rule:
                if mean is None or not rule.get(
                    "min_mean_grayscale", 0
                ) <= mean <= rule.get("max_mean_grayscale", 255):
                    violations.append("mean_grayscale")
            is_ng = bool(violations)
        else:
            is_ng = amount > 0 and (
                amount >= rule.get("min_count", params.get("min_blob_count_for_ng", 1))
                or ("max_count" in rule and amount > rule["max_count"])
            )
            violations = ["defect_presence"] if is_ng else []
        failed |= is_ng
        measurements.append(
            {
                "class_id": index,
                "class_name": names.get(index, f"class_{index}"),
                "evidence_present": True,
                "count": amount,
                "area_px": total,
                "largest_blob_area_px": max(
                    (c["area_px"] for c in components), default=0
                ),
                "mean_grayscale": mean,
                "components": components,
                "rule_mode": mode,
                "violations": violations,
                "verdict": "NG" if is_ng else "OK",
            }
        )
    return measurements, failed


def validate_ocr_rules(params: Mapping[str, Any]) -> None:
    if ("expected_text" in params) == ("regex" in params):
        raise ValueError("OCR requires exactly one expected_text or regex rule")
    pattern = params.get("expected_text", params.get("regex"))
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("OCR requires an expected_text or regex rule")
    if "regex" in params:
        if len(pattern) > 512:
            raise ValueError("OCR regex must be at most 512 characters")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid OCR regex: {exc}") from exc
    corrections = params.get("correction_map", {})
    if not isinstance(corrections, dict) or any(
        not isinstance(k, str) or len(k) != 1 or not isinstance(v, str) or len(v) != 1
        for k, v in corrections.items()
    ):
        raise ValueError("OCR correction_map needs single-character keys and values")
    rules = params.get("position_rules", [])
    if not isinstance(rules, list):
        raise ValueError("OCR position_rules must be a list")
    seen = set()
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or type(rule.get("index")) is not int
            or rule["index"] < 0
            or rule["index"] in seen
        ):
            raise ValueError(
                "OCR position rules require unique nonnegative integer indices"
            )
        seen.add(rule["index"])
        if not any(key in rule for key in ("allowed_chars", "fixed_char")):
            raise ValueError("OCR position requires allowed_chars or fixed_char")
        if "fixed_char" in rule and (
            not isinstance(rule["fixed_char"], str) or len(rule["fixed_char"]) != 1
        ):
            raise ValueError("OCR fixed_char must be one character")
        if "allowed_chars" in rule and (
            not isinstance(rule["allowed_chars"], str) or not rule["allowed_chars"]
        ):
            raise ValueError("OCR allowed_chars must be a nonempty string")
        if (
            "fixed_char" in rule
            and "allowed_chars" in rule
            and rule["fixed_char"] not in rule["allowed_chars"]
        ):
            raise ValueError("OCR fixed_char must belong to allowed_chars")


def evaluate_ocr_rules(text: str, params: Mapping[str, Any]) -> dict[str, Any]:
    validate_ocr_rules(params)
    if not isinstance(text, str):
        raise ValueError("OCR model transcript must be a string")
    corrected = "".join(
        params.get("correction_map", {}).get(char, char) for char in text
    )
    violations = []
    matched = (
        corrected == params["expected_text"]
        if "expected_text" in params
        else re.fullmatch(params["regex"], corrected) is not None
    )
    if not matched:
        violations.append(
            {"rule": "expected_text" if "expected_text" in params else "regex"}
        )
    for rule in params.get("position_rules", []):
        index = rule["index"]
        char = corrected[index] if index < len(corrected) else None
        if (
            char is None
            or ("fixed_char" in rule and char != rule["fixed_char"])
            or ("allowed_chars" in rule and char not in rule["allowed_chars"])
        ):
            violations.append(
                {
                    "rule": "position",
                    "index": index,
                    "observed": char,
                    **{
                        k: rule[k] for k in ("allowed_chars", "fixed_char") if k in rule
                    },
                }
            )
    return {
        "original_text": text,
        "corrected_text": corrected,
        "correction_applied": text != corrected,
        "rule_violations": violations,
        "matched": not violations,
    }
