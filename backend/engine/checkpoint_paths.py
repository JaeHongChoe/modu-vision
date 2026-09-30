"""Resolve only checkpoints produced under a local training job directory."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional


_JOB_ID = re.compile(r"job_[A-Za-z0-9][A-Za-z0-9_-]{0,119}\Z")
_ACTIVE_PROJECT_MODELS_DIR: Optional[Path] = None


def set_active_project_models_dir(path: Optional[str | Path]) -> None:
    """Register the models root selected by the authenticated project API."""
    global _ACTIVE_PROJECT_MODELS_DIR
    if path is None:
        _ACTIVE_PROJECT_MODELS_DIR = None
        return
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Project models directory is unavailable or symlinked: {root}")
    _ACTIVE_PROJECT_MODELS_DIR = root.resolve()


def active_project_models_dir() -> Optional[Path]:
    """Return the models root registered by the currently open project."""
    from backend.engine.annotation_storage import request_project_root
    project=request_project_root()
    if project is not None:
        return project/'models'
    return _ACTIVE_PROJECT_MODELS_DIR


def is_job_id(value: object) -> bool:
    return isinstance(value, str) and _JOB_ID.fullmatch(value) is not None


def completed_job_receipt(output_dir: Path) -> Optional[Dict[str, Any]]:
    """Missing receipts predate persistence; present receipts must say completed."""
    receipt_path = output_dir / "job_receipt.json"
    if receipt_path.is_symlink():
        return None
    if not receipt_path.is_file():
        return {}
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        return receipt if isinstance(receipt, dict) and receipt.get("status") == "completed" else None
    except (OSError, ValueError):
        return None


def trusted_job_dir(
    job_id: str, recorded_output_dir: Optional[str] = None,
    project_models_dir: Optional[str | Path] = None,
) -> Optional[Path]:
    """Find a completed local job below a registered, non-symlinked models root."""
    if not is_job_id(job_id):
        return None
    cwd = Path.cwd()
    candidates = []
    selected_models_dir = Path(project_models_dir) if project_models_dir is not None else active_project_models_dir()
    if selected_models_dir is not None:
        # A project can import a job with an ID already present in the legacy
        # global models directory. Its own model must win for project workflows.
        candidates.append((selected_models_dir, selected_models_dir / job_id))
    from backend.engine.annotation_storage import request_shared_scope
    if not request_shared_scope():
        candidates.extend([
            (cwd / "models", cwd / "models" / job_id),
            (cwd / "projects", cwd / "projects" / job_id / "models"),
        ])
    recorded = Path(recorded_output_dir).expanduser().resolve() if recorded_output_dir else None
    for root, candidate in candidates:
        if root.is_symlink() or (root / job_id).is_symlink() or candidate.is_symlink():
            continue
        if recorded is not None and candidate.resolve() != recorded:
            continue
        if (candidate.is_dir() and candidate.resolve().is_relative_to(root.resolve())
                and completed_job_receipt(candidate) is not None):
            return candidate
    return None


def trusted_checkpoint(
    job_id: str, recorded_output_dir: Optional[str] = None,
    project_models_dir: Optional[str | Path] = None,
) -> Optional[Path]:
    directory = trusted_job_dir(job_id, recorded_output_dir, project_models_dir)
    if directory is None:
        return None
    checkpoint = directory / "best_model.pt"
    if (checkpoint.is_symlink() or not checkpoint.is_file()
            or not checkpoint.resolve().is_relative_to(directory.resolve())):
        return None
    return checkpoint
