"""Build and verify content-only dataset archives for a remote worker."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Event
from typing import BinaryIO, Iterable


PROTOCOL_VERSION = 1
_CHUNK_SIZE = 1024 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SnapshotCancelled(RuntimeError):
    """The owner cancelled while a snapshot was being copied or verified."""


class SnapshotValidationError(ValueError):
    """The archive or manifest cannot safely be used as training input."""


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class SnapshotManifest:
    protocol_version: int
    files: tuple[SnapshotFile, ...]
    total_bytes: int
    manifest_sha256: str
    archive_sha256: str
    manifest_path: Path
    archive_path: Path
    data_path: Path


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise SnapshotCancelled("Snapshot cancelled")


def _sha256_file(path: Path, cancel: Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_SIZE):
            _check_cancel(cancel)
            digest.update(chunk)
    _check_cancel(cancel)
    return digest.hexdigest()


def _copy_and_hash(source: BinaryIO, destination: BinaryIO, cancel: Event | None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(_CHUNK_SIZE):
        _check_cancel(cancel)
        destination.write(chunk)
        digest.update(chunk)
        size += len(chunk)
    _check_cancel(cancel)
    return size, digest.hexdigest()


def _source_files(directory: Path, ancestors: frozenset[Path] = frozenset()) -> Iterable[tuple[Path, Path]]:
    """Walk lexical names while following directory links without following cycles."""
    resolved = directory.resolve(strict=True)
    if resolved in ancestors:
        raise SnapshotValidationError(f"Directory symlink cycle at {directory}")
    next_ancestors = ancestors | {resolved}
    for child in sorted(directory.iterdir(), key=lambda path: path.name):
        if child.is_dir():
            yield from _source_files(child, next_ancestors)
        elif child.is_file():
            yield child, child
        else:
            raise SnapshotValidationError(f"Unsupported or broken source entry: {child}")


def _manifest_bytes(files: tuple[SnapshotFile, ...], total_bytes: int) -> bytes:
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "total_bytes": total_bytes,
        "files": [{"path": row.path, "size": row.size, "sha256": row.sha256} for row in files],
    }
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _add_file(archive: tarfile.TarFile, source: Path, name: str) -> None:
    entry = tarfile.TarInfo(name)
    entry.size = source.stat().st_size
    entry.mode = 0o644
    entry.mtime = 0
    with source.open("rb") as handle:
        archive.addfile(entry, handle)


def _write_archive(path: Path, files: tuple[SnapshotFile, ...], data_path: Path, manifest_path: Path,
                   cancel: Event | None) -> None:
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            _check_cancel(cancel)
            _add_file(archive, manifest_path, "manifest.json")
            for row in files:
                _check_cancel(cancel)
                _add_file(archive, data_path.joinpath(*PurePosixPath(row.path).parts), f"data/{row.path}")
    _check_cancel(cancel)


def build_snapshot(
    source: Path, destination: Path, cancel: Event,
    *, exclude_relative_paths: frozenset[str] = frozenset(),
) -> SnapshotManifest:
    """Copy a task-ready directory into an immutable, hash-listed archive.

    The caller prepares Studio labels and any externally saved split before
    calling this generic copier. Symlink targets are copied as regular bytes,
    including targets outside the selected source tree; their target paths are
    never encoded in the archive.
    """
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute().resolve(strict=False)
    if not source.is_dir():
        raise ValueError("Snapshot source must be a directory")
    if destination.exists():
        raise FileExistsError(destination)
    if destination.is_relative_to(source):
        raise ValueError("Snapshot destination must be outside source")
    _check_cancel(cancel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        data_path = staging / "data"
        data_path.mkdir()
        entries: list[SnapshotFile] = []
        for lexical_path, content_path in _source_files(source):
            _check_cancel(cancel)
            relative = lexical_path.relative_to(source).as_posix()
            if relative in exclude_relative_paths:
                continue
            output = data_path.joinpath(*PurePosixPath(relative).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            with content_path.open("rb") as reader, output.open("xb") as writer:
                size, digest = _copy_and_hash(reader, writer, cancel)
            entries.append(SnapshotFile(relative, size, digest))
        files = tuple(sorted(entries, key=lambda row: row.path))
        if not files:
            raise SnapshotValidationError("Snapshot source contains no files")
        total_bytes = sum(row.size for row in files)
        manifest_path = staging / "manifest.json"
        manifest_path.write_bytes(_manifest_bytes(files, total_bytes))
        archive_path = staging / "snapshot.tar.gz"
        _write_archive(archive_path, files, data_path, manifest_path, cancel)
        manifest_sha256 = _sha256_file(manifest_path, cancel)
        archive_sha256 = _sha256_file(archive_path, cancel)
        _check_cancel(cancel)
        staging.replace(destination)
        return SnapshotManifest(PROTOCOL_VERSION, files, total_bytes, manifest_sha256, archive_sha256,
                                destination / "manifest.json", destination / "snapshot.tar.gz", destination / "data")
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _safe_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise SnapshotValidationError(f"Unsafe archive path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in (".", "..", "") for part in value.split("/")):
        raise SnapshotValidationError(f"Unsafe archive path: {value!r}")
    return path


def _parse_manifest(data: bytes) -> tuple[tuple[SnapshotFile, ...], int]:
    if len(data) > _MAX_MANIFEST_BYTES:
        raise SnapshotValidationError("Manifest is too large")
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotValidationError("Manifest is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("protocol_version") != PROTOCOL_VERSION:
        raise SnapshotValidationError("Unsupported snapshot protocol version")
    rows = payload.get("files")
    if not isinstance(rows, list) or not rows:
        raise SnapshotValidationError("Manifest has no files")
    files = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise SnapshotValidationError("Invalid manifest file record")
        name = row.get("path")
        _safe_relative_path(name)
        if name in seen:
            raise SnapshotValidationError(f"Duplicate manifest path: {name}")
        seen.add(name)
        size = row.get("size")
        sha256 = row.get("sha256")
        if type(size) is not int or size < 0 or not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise SnapshotValidationError(f"Invalid hash or size for {name}")
        files.append(SnapshotFile(name, size, sha256))
    total_bytes = payload.get("total_bytes")
    if type(total_bytes) is not int or total_bytes != sum(row.size for row in files):
        raise SnapshotValidationError("Manifest total_bytes mismatch")
    return tuple(files), total_bytes


def extract_snapshot(archive_path: Path, destination: Path, expected_manifest_sha256: str,
                     cancel: Event | None = None) -> SnapshotManifest:
    """Validate every archive member and hash before publishing extracted data."""
    archive_path = Path(archive_path)
    destination = Path(destination).absolute()
    if destination.exists():
        raise FileExistsError(destination)
    if not isinstance(expected_manifest_sha256, str) or not _SHA256_RE.fullmatch(expected_manifest_sha256):
        raise SnapshotValidationError("Invalid expected manifest hash")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                members = {}
                for member in archive.getmembers():
                    _check_cancel(cancel)
                    path = _safe_relative_path(member.name)
                    if not member.isfile() or member.name in members:
                        raise SnapshotValidationError(f"Archive contains a link, special file, or duplicate: {member.name}")
                    if member.name != "manifest.json" and (len(path.parts) < 2 or path.parts[0] != "data"):
                        raise SnapshotValidationError(f"Unexpected archive member: {member.name}")
                    members[member.name] = member
                manifest_member = members.get("manifest.json")
                if manifest_member is None or manifest_member.size > _MAX_MANIFEST_BYTES:
                    raise SnapshotValidationError("Missing or oversized manifest")
                manifest_reader = archive.extractfile(manifest_member)
                if manifest_reader is None:
                    raise SnapshotValidationError("Cannot read manifest")
                manifest_bytes = manifest_reader.read(_MAX_MANIFEST_BYTES + 1)
                manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
                if manifest_sha256 != expected_manifest_sha256:
                    raise SnapshotValidationError("Manifest hash mismatch")
                files, total_bytes = _parse_manifest(manifest_bytes)
                expected_members = {"manifest.json", *(f"data/{row.path}" for row in files)}
                if set(members) != expected_members:
                    raise SnapshotValidationError("Archive files do not match manifest")
                (staging / "data").mkdir()
                (staging / "manifest.json").write_bytes(manifest_bytes)
                for row in files:
                    _check_cancel(cancel)
                    member = members[f"data/{row.path}"]
                    if member.size != row.size:
                        raise SnapshotValidationError(f"File size mismatch: {row.path}")
                    target = (staging / "data").joinpath(*PurePosixPath(row.path).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    reader = archive.extractfile(member)
                    if reader is None:
                        raise SnapshotValidationError(f"Cannot read file: {row.path}")
                    with target.open("xb") as writer:
                        size, digest = _copy_and_hash(reader, writer, cancel)
                    if size != row.size or digest != row.sha256:
                        raise SnapshotValidationError(f"File hash mismatch: {row.path}")
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise SnapshotValidationError(f"Invalid snapshot archive: {exc}") from exc
        archive_sha256 = _sha256_file(archive_path, cancel)
        _check_cancel(cancel)
        staging.replace(destination)
        return SnapshotManifest(PROTOCOL_VERSION, files, total_bytes, manifest_sha256, archive_sha256,
                                destination / "manifest.json", archive_path, destination / "data")
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def verify_snapshot_tree(
    directory: Path, expected_manifest_sha256: str, *, allow_archive: bool = False,
) -> Path:
    """Verify a previously extracted snapshot before another operation reads it."""
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    data_path = directory / "data"
    archive_path = directory / "snapshot.tar.gz"
    if (directory.is_symlink() or manifest_path.is_symlink() or data_path.is_symlink()
            or not manifest_path.is_file() or not data_path.is_dir()):
        raise SnapshotValidationError("Snapshot tree is missing or contains a link")
    entries = {entry.name for entry in directory.iterdir()}
    if allow_archive and "snapshot.tar.gz" in entries:
        if archive_path.is_symlink() or not archive_path.is_file():
            raise SnapshotValidationError("Local snapshot archive contains a link or special file")
        entries.remove("snapshot.tar.gz")
    if entries != {"manifest.json", "data"}:
        raise SnapshotValidationError("Snapshot tree has unexpected entries")
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise SnapshotValidationError("Snapshot manifest hash mismatch")
    files, _ = _parse_manifest(manifest_bytes)
    expected = {row.path: row for row in files}
    actual: set[str] = set()
    for root, directories, filenames in os.walk(data_path, followlinks=False):
        for name in directories:
            if (Path(root) / name).is_symlink():
                raise SnapshotValidationError("Snapshot data contains a directory link")
        for name in filenames:
            path = Path(root) / name
            if path.is_symlink() or not path.is_file():
                raise SnapshotValidationError("Snapshot data contains a link or special file")
            relative = path.relative_to(data_path).as_posix()
            row = expected.get(relative)
            if row is None:
                raise SnapshotValidationError(f"Unexpected snapshot file: {relative}")
            if path.stat().st_size != row.size or _sha256_file(path) != row.sha256:
                raise SnapshotValidationError(f"Snapshot file hash mismatch: {relative}")
            actual.add(relative)
    if actual != set(expected):
        raise SnapshotValidationError("Snapshot data is missing manifest files")
    return data_path


def package_worker_bundle(destination: Path) -> Path:
    """Package only importable worker/engine source, never tests or caches."""
    destination = Path(destination).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parents[2]
    backend_root = project_root / "backend"
    sources = sorted(
        path for folder in ("api", "engine", "remote", "utils")
        for path in (backend_root / folder).rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
    )
    with destination.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for source in sources:
                _add_file(archive, source, source.relative_to(project_root).as_posix())
    return destination
