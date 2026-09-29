"""Dataset-scoped location for Studio annotations, separate from source images."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Optional


LEGACY_ANNOTATIONS_ROOT = Path("./annotations")
_REQUEST_ANNOTATION_ROOT: ContextVar[Optional[Path]] = ContextVar("project_annotation_root", default=None)
_REQUEST_PROJECT_ROOT: ContextVar[Optional[Path]] = ContextVar("active_project_root", default=None)


def set_request_annotation_root(path: Path) -> Token:
    """Scope Studio edits to the active project for one authenticated request."""
    return _REQUEST_ANNOTATION_ROOT.set(Path(path).resolve())


def reset_request_annotation_root(token: Token) -> None:
    _REQUEST_ANNOTATION_ROOT.reset(token)


def set_request_project_root(path: Path) -> Token:
    return _REQUEST_PROJECT_ROOT.set(Path(path).resolve())


def reset_request_project_root(token: Token) -> None:
    _REQUEST_PROJECT_ROOT.reset(token)


def request_project_root() -> Optional[Path]:
    return _REQUEST_PROJECT_ROOT.get()


def scoped_annotation_root(default: Path) -> Path:
    return _REQUEST_ANNOTATION_ROOT.get() or Path(default)


def dataset_annotation_dir(
    dataset_folder: Path, root: Path = LEGACY_ANNOTATIONS_ROOT, *, use_scope: bool = True,
) -> Path:
    canonical = str(Path(dataset_folder).resolve())
    dataset_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    selected_root = scoped_annotation_root(root) if use_scope else Path(root)
    return selected_root / "by_dataset" / dataset_key


def migrate_legacy_dataset_overlay(project: dict) -> Optional[dict]:
    """Copy one legacy overlay into an empty project scope without altering it.

    A staged copy avoids exposing a partial overlay to later requests. Existing
    project labels always take precedence, including an intentionally empty
    overlay directory.
    """
    source = project.get("source_dataset_dir")
    if not source:
        return None
    source_dir = Path(source).expanduser().resolve()
    legacy = dataset_annotation_dir(source_dir, LEGACY_ANNOTATIONS_ROOT, use_scope=False)
    target = dataset_annotation_dir(source_dir, Path(project["annotations_dir"]), use_scope=False)
    if not legacy.is_dir() or target.exists() or legacy.resolve() == target.resolve():
        return None
    if legacy.is_symlink() or any(path.is_symlink() for path in legacy.rglob("*")):
        raise ValueError(f"Legacy annotation overlay contains a symbolic link: {legacy}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".migrating-{uuid.uuid4().hex}"
    try:
        shutil.copytree(legacy, staging)
        if target.exists():
            return None
        os.replace(staging, target)
        record = {
            "source_dataset_dir": str(source_dir),
            "legacy_overlay_dir": str(legacy.resolve()),
            "project_overlay_dir": str(target.resolve()),
            "copied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        marker = Path(project["annotations_dir"]) / ".legacy_overlay_migration.json"
        marker.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record
    finally:
        if staging.exists():
            shutil.rmtree(staging)
