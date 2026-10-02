"""Which files of a source are images of the dataset, and the label and split their folders give (S3-01).

One definition for every consumer: the gallery/summary listing (``source_image_paths``) and the persistent index use
the same scan root, exclusions and folder label/split rules, so their counts and filters cannot drift apart.

- The scan root is ``source/<task>`` when that folder exists, otherwise the source itself.
- Hidden entries (a part starting with '.'), NAS/OS service folders, and mask/label/annotation folders of geometry and
  anomaly tasks are not dataset images.
- The split is the first ``train``/``val``/``test`` folder on the way to the file; a classification label is the
  file's folder unless that folder is ``images``/``train``/``val``/``test``; an anomaly label is good or defect.
"""
from __future__ import annotations

from pathlib import Path, PurePath
from typing import Optional

from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS

SPLITS = ('train', 'val', 'test')
AUXILIARY_FOLDERS = frozenset({'masks', 'mask', 'ground_truth', 'labels', 'annotations'})
AUXILIARY_TASKS = frozenset({'detection', 'segmentation', 'anomaly', 'anomaly_detection'})
SERVICE_FOLDERS = frozenset({'@eaDir', '#recycle', '#snapshot', '$RECYCLE.BIN', 'System Volume Information'})
LABEL_NEUTRAL = frozenset({'images', *SPLITS})
TASKS = frozenset({'classification', 'detection', 'segmentation', 'anomaly', 'anomaly_detection', 'patch_classification'})


def scan_root(source: Path, task: str) -> Path:
    candidate = Path(source) / task
    return candidate if candidate.is_dir() else Path(source)


def excluded_folder(name: str, task: str) -> bool:
    """A folder whose contents are never dataset images for this task."""
    return name.startswith('.') or name in SERVICE_FOLDERS or (task in AUXILIARY_TASKS and name in AUXILIARY_FOLDERS)


def is_inventory_path(relative_parts: tuple, task: str) -> bool:
    """Whether a file at these source-relative parts is an image of the dataset."""
    if not relative_parts:
        return False
    if any(excluded_folder(part, task) for part in relative_parts[:-1]):
        return False
    name = relative_parts[-1]
    return not name.startswith('.') and PurePath(name).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def folder_label_split(relative: PurePath, task: str) -> tuple[Optional[str], Optional[str]]:
    """The label and split the folders give a source-relative file (annotations may add labels elsewhere)."""
    parents = PurePath(relative).parts[:-1]
    split = next((part for part in parents if part in SPLITS), None)
    if task in ('anomaly', 'anomaly_detection'):
        from backend.engine.grouped_dataset_views import is_anomaly_normal
        return ('good' if is_anomaly_normal(Path(relative)) else 'defect'), split
    if task in ('classification', 'patch_classification'):
        return (parents[-1] if parents and parents[-1] not in LABEL_NEUTRAL else None), split
    return None, split
