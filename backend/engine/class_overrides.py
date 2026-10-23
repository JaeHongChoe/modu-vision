"""Per-image classification classes chosen in step 2, without moving or renaming the user's image files.

A folder-based classification project takes each image's class from its folder name. When the user picks a different
class for an image, the choice is stored here and every place that reads the folder class (training and evaluation
datasets, the step-1 counts, the gallery labels, whole-flow truth from labels) applies it.

Storage: one file per source folder and label set, in the project's annotation area (never inside the user's folder)::

    <annotation root>/by_dataset/<source key>/classification/class_overrides.json
    {"schema_version": 1, "folder_path": "<resolved source>", "overrides": {"OK/a.png": "NG"}}

Keys are POSIX paths relative to the source. The file sits inside the dataset overlay folder, so the dataset
fingerprint, dataset versions and the impact report already treat a change as a data change (models, evaluations and
flows trained or measured on the old classes become stale). Removing an override returns the image to its folder class.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, Optional, Tuple

SCHEMA_VERSION = 1
MAX_CLASS_NAME = 100
NORMAL_FIRST = ("ok", "good", "normal", "pass", "정상_ok", "정상")


def overrides_path(source: Path) -> Path:
    from backend.engine.annotation_storage import dataset_annotation_dir
    return dataset_annotation_dir(Path(source)) / "classification" / "class_overrides.json"


def _relative(source: Path, image: Path) -> str:
    source = Path(source).resolve()
    resolved = Path(image).resolve()
    if not resolved.is_relative_to(source):
        raise ValueError("이미지가 데이터 폴더 밖에 있습니다.")
    return resolved.relative_to(source).as_posix()


def _read_document(source: Path) -> dict:
    path = overrides_path(source)
    if path.is_symlink():
        raise ValueError("Class override file cannot be a symbolic link")
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "folder_path": str(Path(source).resolve()), "overrides": {}}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != SCHEMA_VERSION or not isinstance(document.get("overrides"), dict):
        raise ValueError("Unsupported class override file")
    if document.get("folder_path") != str(Path(source).resolve()):
        # A copied project or a moved source keeps its own classes only for the folder they were chosen in.
        return {"schema_version": SCHEMA_VERSION, "folder_path": str(Path(source).resolve()), "overrides": {}}
    return document


def read_class_overrides(source: Path) -> Dict[str, str]:
    """{resolved absolute image path: class} for the source, or {} when none were chosen (or no project scope)."""
    source = Path(source).resolve()
    try:
        document = _read_document(source)
    except (OSError, ValueError):
        return {}
    result = {}
    for relative, name in document["overrides"].items():
        parts = PurePosixPath(relative).parts
        if not isinstance(name, str) or not name or PurePosixPath(relative).is_absolute() or ".." in parts:
            continue
        result[str((source / relative).resolve())] = name
    return result


def effective_class(image: Path, folder_class: Optional[str], overrides: Dict[str, str]) -> Tuple[Optional[str], str]:
    """(class, "override" | "folder") for one image."""
    chosen = overrides.get(str(Path(image).resolve()))
    return (chosen, "override") if chosen else (folder_class, "folder")


def sort_classes(names) -> list:
    return sorted(set(names), key=lambda name: (0 if name.lower() in NORMAL_FIRST else 1, name.lower()))


def set_class_override(project_root: Path, source: Path, image: Path, class_name: Optional[str], actor: str,
                       folder_class: Optional[str]) -> dict:
    """Choose ``class_name`` for ``image`` (None, empty or the folder class removes the choice). Atomic write plus an
    audit event on the image's metadata row, which also invalidates its team reviews."""
    actor = (actor or "").strip()
    if not actor or len(actor) > 100:
        raise ValueError("작업자 이름을 1~100자로 입력하세요.")
    if class_name is not None:
        class_name = class_name.strip()
        if len(class_name) > MAX_CLASS_NAME or any(ch in class_name for ch in "/\\\0"):
            raise ValueError("클래스 이름은 100자 이하이며 / \\ 를 쓸 수 없습니다.")
    source = Path(source).resolve()
    image = Path(image).resolve()
    if not image.is_file():
        raise FileNotFoundError(str(image))
    relative = _relative(source, image)
    document = _read_document(source)
    previous = document["overrides"].get(relative)
    if not class_name or class_name == folder_class:
        document["overrides"].pop(relative, None)
        current = None
    else:
        document["overrides"][relative] = class_name
        current = class_name
    if previous != current:
        path = overrides_path(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        document["overrides"] = dict(sorted(document["overrides"].items()))
        handle, temporary = tempfile.mkstemp(prefix=".class_overrides.", dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(document, stream, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        from backend.engine import dataset_metadata as dm
        from backend.engine.team_data import invalidate_reviews
        with dm.metadata_transaction(project_root, source) as ledger:
            row = dm._ensure(ledger, project_root, source, image)
            invalidate_reviews(row, "class_changed")
            dm._event(row, actor, "class_changed", {"class": current, "previous": previous, "folder_class": folder_class})
    return {"relative_path": relative, "class": current or folder_class, "class_source": "override" if current else "folder",
            "folder_class": folder_class, "changed": previous != current}
