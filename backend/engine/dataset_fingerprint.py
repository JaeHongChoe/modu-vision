"""Version the local source data that a training job represents.

Large inspection images use filesystem identity and high-resolution timestamps;
small label/configuration files are also hashed by content. The version includes
Studio labels and the saved split, which live outside the source directory.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable, Optional

from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS


_LABEL_EXTENSIONS = {".json", ".txt", ".xml", ".csv", ".yaml", ".yml"}
_TRACKED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | _LABEL_EXTENSIONS


def _files_under(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if not name.startswith(".") and not name.startswith("__"))
        for name in sorted(files):
            if name.startswith(".") or name.startswith("._"):
                continue
            path = Path(directory) / name
            if path.suffix.lower() in _TRACKED_EXTENSIONS and path.is_file():
                yield path


def _update_file(digest: "hashlib._Hash", path: Path, relative: str) -> None:
    stat = path.stat()
    digest.update(relative.encode("utf-8", "surrogateescape"))
    digest.update(b"\0")
    digest.update(f"{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}".encode("ascii"))
    digest.update(b"\0")
    if path.suffix.lower() in _LABEL_EXTENSIONS:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        digest.update(b"\0")


def fingerprint_dataset(
    folder: Path,
    *,
    studio_root: Path = Path("./annotations"),
    split_manifest: Optional[Path] = None,
) -> str:
    """Return a deterministic version for source images, labels, and split.

    The caller provides the split manifest path to keep this engine independent
    of API route configuration and to support isolated test fixtures.
    """
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"Dataset directory is missing: {folder}")
    digest = hashlib.sha256()
    digest.update(b"modu-dataset-fingerprint-v1\0")
    digest.update(str(folder).encode("utf-8", "surrogateescape"))
    digest.update(b"\0")

    for path in _files_under(folder):
        _update_file(digest, path, f"source/{path.relative_to(folder).as_posix()}")

    studio_dir = dataset_annotation_dir(folder, studio_root)
    for path in _files_under(studio_dir):
        _update_file(digest, path, f"studio/{path.relative_to(studio_dir).as_posix()}")

    if split_manifest is not None and Path(split_manifest).is_file():
        manifest = Path(split_manifest)
        digest.update(b"split-manifest\0")
        with manifest.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    else:
        digest.update(b"no-split-manifest\0")
    return f"v1:{digest.hexdigest()}"
