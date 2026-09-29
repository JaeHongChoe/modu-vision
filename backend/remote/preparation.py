"""Materialize path-bound local splits into portable training trees."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from threading import Event

from backend.engine.dataset_loaders import ClassificationDataset, _classification_split_assignments


class PreparationCancelled(RuntimeError):
    pass


def _check(cancel: Event) -> None:
    if cancel.is_set():
        raise PreparationCancelled("Classification preparation cancelled")


def _copy_with_cancel(source: Path, destination: Path, cancel: Event) -> None:
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            _check(cancel)
            writer.write(chunk)
    _check(cancel)


def prepare_remote_classification(source: Path, destination: Path, cancel: Event) -> None:
    """Freeze Step 1's exact train/val/test images before their path changes."""
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists():
        raise FileExistsError(destination)
    if destination.is_relative_to(source):
        raise ValueError("Prepared dataset must be outside its source")
    _check(cancel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        splits = ("train", "val", "test") if (_classification_split_assignments(source) is not None
                                                  or (source / "test").is_dir()) else ("train", "val")
        datasets = {split: ClassificationDataset(source, split=split) for split in splits}
        if not datasets["train"].samples or not datasets["val"].samples:
            raise ValueError("Remote classification requires nonempty train and val partitions")
        classes = datasets["train"].classes
        if any(dataset.classes != classes for dataset in datasets.values()):
            raise ValueError("Classification classes differ across saved partitions")
        seen: set[Path] = set()
        for split, dataset in datasets.items():
            for image, label_idx in dataset.samples:
                _check(cancel)
                image = Path(image)
                if image in seen:
                    raise ValueError(f"An image was assigned to multiple partitions: {image}")
                seen.add(image)
                if not 0 <= label_idx < len(classes):
                    raise ValueError("Classification label index is outside the class list")
                target_dir = stage / split / classes[label_idx]
                target_dir.mkdir(parents=True, exist_ok=True)
                name = image.name
                target = target_dir / name
                if target.exists():
                    digest = hashlib.sha256(str(image).encode()).hexdigest()[:12]
                    target = target_dir / f"{digest}_{name}"
                _copy_with_cancel(image, target, cancel)
        _check(cancel)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
