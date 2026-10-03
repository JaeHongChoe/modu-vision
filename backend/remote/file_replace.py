"""Replacing and reading a small file that another process rewrites or reads many times a second.

On Windows a file another process has open cannot be replaced: Python opens files without delete sharing, so the rename
fails with 'Access is denied'. A running job's status file is rewritten by its worker while the app reads it, and its
cancel file the other way round, so both sides retry a PermissionError briefly (about two seconds) on Windows. Elsewhere
an error is an error at once. Standard library only: a remote worker imports this before anything heavy.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

_WINDOWS = os.name == "nt"
ATTEMPTS = 60


def _retrying(action):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return action()
        except PermissionError:
            if not _WINDOWS or attempt == ATTEMPTS:
                raise
            time.sleep(min(0.002 * attempt, 0.05))


def replace_file(source, target) -> None:
    """os.replace that waits out a reader on Windows."""
    _retrying(lambda: os.replace(source, target))


def read_text(path, encoding: str = "utf-8") -> str:
    """Path.read_text that waits out a replacement in progress on Windows."""
    return _retrying(lambda: Path(path).read_text(encoding=encoding))
