"""Version the local source data that a training job represents.

Large inspection images use filesystem identity and high-resolution timestamps;
small label/configuration files are also hashed by content. The version includes
Studio labels and the saved split, which live outside the source directory.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterable, Optional

from backend.engine.annotation_storage import dataset_annotation_dir, dataset_overlay_scopes, request_project_root
from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
from backend.engine.file_identity import stat_by_handle


_LABEL_EXTENSIONS = {".json", ".txt", ".xml", ".csv", ".yaml", ".yml"}
_TRACKED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | _LABEL_EXTENSIONS


def _files_under(root: Path, excluded_project: Optional[Path] = None) -> Iterable[Path]:
    if not root.is_dir():
        return
    for directory, names, files in os.walk(root, followlinks=False):
        folder = Path(directory)
        names[:] = sorted(name for name in names if not name.startswith(".") and not name.startswith("__"))
        if excluded_project is not None:
            if folder == excluded_project:
                names[:] = [name for name in names if name not in {
                    "annotations", "dataset", "flowcharts", "label_suggestions",
                    "models", "reports", "versions",
                }]
            else:
                names[:] = [name for name in names if (folder / name).resolve() != excluded_project]
        for name in sorted(files):
            if name.startswith(".") or name.startswith("._"):
                continue
            path = Path(directory) / name
            if folder == excluded_project and name == "project.json":
                continue
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
    use_scope: bool = True,
    _source_identity: Optional[Path] = None,
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
    identity = Path(_source_identity).resolve() if _source_identity is not None else folder
    digest.update(str(identity).encode("utf-8", "surrogateescape"))
    digest.update(b"\0")

    project_root = request_project_root()
    if project_root is not None and not project_root.is_relative_to(folder):
        project_root = None
    for path in _files_under(folder, project_root):
        _update_file(digest, path, f"source/{path.relative_to(folder).as_posix()}")

    studio_dir = dataset_annotation_dir(identity, studio_root, use_scope=use_scope)
    scopes = dataset_overlay_scopes(folder, studio_root, use_scope=use_scope)
    if _source_identity is not None:
        # Read physical staged images but retain destination-derived overlay identities.
        from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
        scopes = {studio_dir}
        for directory, names, files in os.walk(folder, followlinks=False):
            names[:] = sorted(name for name in names if not name.startswith('.'))
            if any(not name.startswith('.') and Path(name).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS for name in files):
                scopes.add(dataset_annotation_dir(identity / Path(directory).relative_to(folder), studio_root, use_scope=use_scope))
        scopes = sorted(scopes)
    for scope in scopes:
        for path in _files_under(scope):
            # Review identity/audit do not alter pixels/labels. Versions preserve them.
            if path.relative_to(scope).parts[0] == "metadata":
                continue
            prefix = "studio" if scope == studio_dir else f"studio_scoped/{scope.name}"
            _update_file(digest, path, f"{prefix}/{path.relative_to(scope).as_posix()}")

    if split_manifest is not None and Path(split_manifest).is_file():
        manifest = Path(split_manifest)
        digest.update(b"split-manifest\0")
        with manifest.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    else:
        digest.update(b"no-split-manifest\0")
    return f"v1:{digest.hexdigest()}"


@contextmanager
def source_artifact_identity(folder: Path, relative_name: str):
    """Content identity for managed copies; never changes the legacy v1 fingerprint."""
    relative = Path(relative_name)
    if not relative_name or relative.is_absolute() or '..' in relative.parts or '\\' in relative_name or ':' in relative_name:
        raise ValueError('Source name must stay inside the registered dataset')
    root = Path(folder).resolve()
    candidate = root / relative
    if any(path.is_symlink() for path in (candidate, *candidate.parents) if path != root and path.is_relative_to(root)):
        raise ValueError('Source ingestion cannot follow symbolic links')
    path = candidate.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Source file is unavailable in the registered dataset')
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as source:
        before = os.fstat(source.fileno())
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
        after = os.fstat(source.fileno())
        identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        current = stat_by_handle(path)  # a descriptor stat, like before/after (a path stat differs on Windows)
        if (identity(before) != identity(after) or current is None or identity(after) != identity(current)
                or not candidate.resolve().is_relative_to(root)):
            raise ValueError('Source file changed while computing its content identity')
        source.seek(0)
        # Keep the verified descriptor open through copying; never reopen a path
        # that could have been replaced after source validation.
        yield source, digest.hexdigest(), after.st_size
