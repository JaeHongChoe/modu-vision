"""Resolve only checkpoints produced under a local training job directory."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional


_JOB_ID = re.compile(r"job_[A-Za-z0-9][A-Za-z0-9_-]{0,119}\Z")


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


def trusted_job_dir(job_id: str, recorded_output_dir: Optional[str] = None) -> Optional[Path]:
    """Find models/job_* or projects/job_*/models without following symlinks."""
    if not is_job_id(job_id):
        return None
    cwd = Path.cwd()
    candidates = (
        (cwd / "models", cwd / "models" / job_id),
        (cwd / "projects", cwd / "projects" / job_id / "models"),
    )
    recorded = os.path.abspath(recorded_output_dir) if recorded_output_dir else None
    for root, candidate in candidates:
        if recorded is not None and os.path.abspath(candidate) != recorded:
            continue
        if root.is_symlink() or (root / job_id).is_symlink() or candidate.is_symlink():
            continue
        if (candidate.is_dir() and candidate.resolve().is_relative_to(root.resolve())
                and completed_job_receipt(candidate) is not None):
            return candidate
    return None


def trusted_checkpoint(job_id: str, recorded_output_dir: Optional[str] = None) -> Optional[Path]:
    directory = trusted_job_dir(job_id, recorded_output_dir)
    if directory is None:
        return None
    checkpoint = directory / "best_model.pt"
    if (checkpoint.is_symlink() or not checkpoint.is_file()
            or not checkpoint.resolve().is_relative_to(directory.resolve())):
        return None
    return checkpoint
