"""Exception-safe publication of an annotation's JSON and raster mask.

Callers hold their metadata or legacy image lock for publication and rollback.
Files are visible immediately to nested import/fingerprint consumers. This is
not a crash-atomic, multi-file filesystem transaction.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)


class AnnotationFileTransaction:
    """Retain the first original of each target until its outer commit succeeds."""

    def __init__(self):
        self._originals: dict[Path, Path | None] = {}
        self._changed: set[Path] = set()
        self._temporary: set[Path] = set()
        self.failed = False

    def _stage(self, target: Path, data: bytes) -> Path:
        # No JSON/PNG suffix: dataset scanners must never treat these as labels.
        descriptor, name = tempfile.mkstemp(prefix=".annotation-atomic-", dir=target.parent)
        path = Path(name)
        self._temporary.add(path)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.is_file():
            path.chmod(stat.S_IMODE(target.stat().st_mode))
        return path

    def _remember(self, target: Path) -> None:
        if target in self._originals:
            return
        if not target.exists():
            self._originals[target] = None
            return
        descriptor, name = tempfile.mkstemp(prefix=".annotation-atomic-", dir=target.parent)
        backup = Path(name)
        self._temporary.add(backup)
        with os.fdopen(descriptor, "wb") as handle, target.open("rb") as original:
            shutil.copyfileobj(original, handle)
            handle.flush()
            os.fsync(handle.fileno())
        backup.chmod(stat.S_IMODE(target.stat().st_mode))
        self._originals[target] = backup

    def publish(self, updates: Mapping[Path, bytes | None]) -> None:
        """Stage every byte string, then publish writes/deletions under the lock."""
        if self.failed:
            raise RuntimeError("Annotation transaction has already failed")
        updates = {Path(path): value for path, value in updates.items()}
        staged: dict[Path, Path] = {}
        try:
            for target, data in updates.items():
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    raise ValueError(f"Annotation target must be a regular file: {target}")
                if data is not None and not isinstance(data, bytes):
                    raise TypeError("Annotation publication requires encoded bytes")
            for target, data in updates.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                if data is not None:
                    staged[target] = self._stage(target, data)
            for target in updates:
                self._remember(target)
            for target, data in updates.items():
                if data is None and not target.exists():
                    continue
                # Include an attempted replacement in rollback even if it raises.
                self._changed.add(target)
                if data is None:
                    target.unlink()
                else:
                    os.replace(staged[target], target)
                    self._temporary.discard(staged[target])
        except BaseException:
            self.failed = True
            self.rollback()
            raise

    def rollback(self) -> None:
        """Restore all earliest originals; keep recovery files if restoration fails."""
        errors = []
        for target in tuple(self._changed):
            backup = self._originals[target]
            try:
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(backup, target)
                    self._temporary.discard(backup)
                self._changed.discard(target)
            except OSError as exc:
                errors.append(f"{target}: {exc}")
        if errors:
            raise RuntimeError("Annotation transaction rollback failed; recovery files retained: " + "; ".join(errors))
        self.cleanup()

    def cleanup(self) -> None:
        """Remove private staging/backup files after successful commit or rollback."""
        for path in tuple(self._temporary):
            try:
                path.unlink(missing_ok=True)
                self._temporary.discard(path)
            except OSError:
                # A committed transaction must not become a failed save solely
                # because an unused backup could not be removed.
                logger.warning("Could not remove annotation transaction file %s", path, exc_info=True)
