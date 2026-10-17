"""Explicit prerequisites for external qualification; never download or skip assets.

Ordinary CI selects self-contained test modules explicitly. Tests marked
``private_qualification`` are separate original-evidence replays; selecting them
requires an existing MODU_PRIVATE_QA_ROOT, including during module collection.
"""
import os
from pathlib import Path


def private_qa_root() -> Path:
    value = os.environ.get('MODU_PRIVATE_QA_ROOT')
    if not value or not value.strip():
        raise RuntimeError('MODU_PRIVATE_QA_ROOT is required for this external qualification test; no private fixture is bundled or inferred')
    root = Path(value).expanduser()
    if not root.is_absolute():
        raise RuntimeError('MODU_PRIVATE_QA_ROOT must be an explicit absolute qualification directory')
    if not root.is_dir():
        raise FileNotFoundError('MODU_PRIVATE_QA_ROOT must name an existing qualification directory')
    return root.resolve(strict=True)
