"""Deterministic, disjoint partitions for anomaly data without named split folders."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List


def _unique_sorted(paths: Iterable[Path]) -> List[Path]:
    seen = set()
    unique = []
    for path in sorted(paths):
        canonical = path.resolve()
        if canonical not in seen:
            seen.add(canonical)
            unique.append(path)
    return unique


def partition_normal_images(paths: Iterable[Path]) -> Dict[str, List[Path]]:
    """Keep roughly 80% for fitting, then divide the holdout into val/test.

    With fewer than three normals, empty splits are preferable to reusing a
    training image as validation or test evidence.
    """
    images = _unique_sorted(paths)
    count = len(images)
    if count <= 1:
        holdout = 0
    elif count == 2:
        holdout = 1
    else:
        holdout = min(count - 1, max(2, int(count * 0.2 + 0.5)))
    train_count = count - holdout
    val_count = (holdout + 1) // 2
    return {
        "train": images[:train_count],
        "val": images[train_count:train_count + val_count],
        "test": images[train_count + val_count:],
    }


def partition_evaluation_images(paths: Iterable[Path]) -> Dict[str, List[Path]]:
    """Divide non-training examples between validation and independent test."""
    images = _unique_sorted(paths)
    val_count = (len(images) + 1) // 2
    return {"val": images[:val_count], "test": images[val_count:]}
