"""Immutable dataset inventory and label-set snapshots for a project workspace.

Images stay at their original paths. Every file is hashed and mapped in the
manifest; small source labels, Studio edits, and the split are copied. A restore
requires the source to match the snapshot and never writes source LabelMe files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api import routes_dataset
from backend.api.routes_project import get_current_project
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS

router = APIRouter(prefix="/api/dataset/versions", tags=["dataset-versions"])
_VERSION_ID = re.compile(r"v_[0-9]{8}_[0-9]{6}_[a-f0-9]{8}\Z")
_LABEL_EXTENSIONS = {".json", ".txt", ".xml", ".csv", ".yaml", ".yml"}
_TRACKED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | _LABEL_EXTENSIONS
_MAX_LABEL_COPY_BYTES = 128 * 1024 * 1024
_VERSION_LOCK = threading.RLock()


class VersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(..., min_length=1, max_length=120)
    note: str = Field("", max_length=2000)
    dataset_path: Optional[str] = None


def _current_project(request: Request) -> Dict[str, Any]:
    return get_current_project(request)


def _source_path(project: Dict[str, Any], requested: Optional[str] = None) -> Path:
    saved = project.get("source_dataset_dir")
    if not saved:
        raise HTTPException(status_code=422, detail="Import a dataset into this project before creating a version.")
    source = Path(saved).expanduser().resolve()
    if requested and Path(requested).expanduser().resolve() != source:
        raise HTTPException(status_code=422, detail="Dataset path does not match the active project's source.")
    if not source.is_dir():
        raise HTTPException(status_code=404, detail=f"Dataset source is unavailable: {source}")
    return source


def _versions_root(project: Dict[str, Any]) -> Path:
    root = Path(project["project_dir"]) / "versions"
    if root.is_symlink():
        raise HTTPException(status_code=422, detail="Version directory cannot be a symbolic link.")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _version_path(project: Dict[str, Any], version_id: str) -> Path:
    if not _VERSION_ID.fullmatch(version_id):
        raise HTTPException(status_code=422, detail="Invalid dataset version ID.")
    path = _versions_root(project) / version_id
    if path.is_symlink() or not path.is_dir():
        raise HTTPException(status_code=404, detail=f"Dataset version not found: {version_id}")
    return path


def _iter_files(root: Path, *, source: bool, project_dir: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for folder, names, files in os.walk(root, followlinks=False):
        directory = Path(folder)
        names[:] = sorted(name for name in names if not name.startswith(".") and not name.startswith("__"))
        # A custom project may live below its source. Never inventory its own versions/models.
        names[:] = [name for name in names if (directory / name).resolve() != project_dir]
        if directory == project_dir:
            names[:] = [name for name in names if name not in {
                "annotations", "dataset", "flowcharts", "label_suggestions",
                "versions", "models", "reports",
            }]
        for name in sorted(files):
            if name.startswith(".") or name.startswith("._"):
                continue
            path = directory / name
            if path.is_file() and (not source or path.suffix.lower() in _TRACKED_EXTENSIONS):
                if source and path == project_dir / "project.json":
                    continue
                yield path


def _is_label(path: Path, root: Path) -> bool:
    if path.suffix.lower() in _LABEL_EXTENSIONS:
        return True
    parts = {part.lower() for part in path.relative_to(root).parts[:-1]}
    return bool(parts & {"masks", "mask", "annotations", "labels"})


def _hash_and_optional_copy(path: Path, destination: Optional[Path]) -> tuple[str, int]:
    before = path.stat()
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        with destination.open("wb") if destination is not None else open(os.devnull, "wb") as output:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                if destination is not None:
                    output.write(block)
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise HTTPException(status_code=409, detail=f"Dataset file changed during snapshot: {path}")
    return digest.hexdigest(), after.st_size


def _manifest_digest(manifest: Dict[str, Any]) -> str:
    unsigned = {key: value for key, value in manifest.items() if key != "content_digest"}
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _summary(manifest: Dict[str, Any], *, status: str = "not_checked") -> Dict[str, Any]:
    return {
        "id": manifest["id"], "name": manifest["name"], "note": manifest["note"],
        "labelset_id": manifest.get("labelset_id", "default"),
        "kind": manifest["kind"], "created_at": manifest["created_at"],
        "source_dataset_dir": manifest["source_dataset_dir"],
        "image_count": manifest["image_count"], "label_file_count": manifest["label_file_count"],
        "total_image_bytes": manifest["total_image_bytes"],
        "copied_label_bytes": manifest["copied_label_bytes"],
        "dataset_fingerprint": manifest["dataset_fingerprint"], "status": status,
    }


def _snapshot(project: Dict[str, Any], source: Path, name: str, note: str, kind: str) -> Dict[str, Any]:
    root = _versions_root(project)
    version_id = f"v_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}_{uuid.uuid4().hex[:8]}"
    target = root / version_id
    staging = root / f".creating-{uuid.uuid4().hex}"
    staging.mkdir()
    studio_root = routes_dataset.STUDIO_ANNOTATIONS_DIR
    studio = dataset_annotation_dir(source, studio_root)
    split = routes_dataset._split_manifest_file(source)
    project_dir = Path(project["project_dir"]).resolve()
    rows: List[Dict[str, Any]] = []
    total_image_bytes = 0
    copied_label_bytes = 0
    image_count = 0
    label_count = 0

    def record(origin: str, path: Path, relative: Path, kind_: str) -> None:
        nonlocal total_image_bytes, copied_label_bytes, image_count, label_count
        label = kind_ == "label"
        if label and copied_label_bytes + path.stat().st_size > _MAX_LABEL_COPY_BYTES:
            raise HTTPException(status_code=413, detail="Label files exceed the 128 MB snapshot copy limit. No images were copied.")
        backup = f"labels/{origin}/{relative.as_posix()}" if label else None
        sha256, size = _hash_and_optional_copy(path, staging / backup if backup else None)
        rows.append({
            "origin": origin, "kind": kind_, "relative_path": relative.as_posix(),
            "source_path": str(path.resolve()), "sha256": sha256,
            "size_bytes": size, "snapshot_path": backup,
        })
        if label:
            label_count += 1
            copied_label_bytes += size
        else:
            image_count += 1
            total_image_bytes += size

    try:
        for path in _iter_files(source, source=True, project_dir=project_dir):
            record("source", path, path.relative_to(source), "label" if _is_label(path, source) else "image")
        for path in _iter_files(studio, source=False, project_dir=project_dir):
            record("studio", path, path.relative_to(studio), "label")
        if split.is_file():
            record("split", split, Path("manifest.json"), "label")

        rows.sort(key=lambda row: (row["origin"], row["relative_path"]))
        manifest = {
            "schema_version": 1, "id": version_id, "project_id": project["id"],
            "labelset_id": project.get("active_labelset_id", "default"),
            "name": name.strip(), "note": note.strip(), "kind": kind,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_dataset_dir": str(source), "task": project["task"],
            "image_count": image_count, "label_file_count": label_count,
            "total_image_bytes": total_image_bytes, "copied_label_bytes": copied_label_bytes,
            "dataset_fingerprint": fingerprint_dataset(source, studio_root=studio_root, split_manifest=split),
            "files": rows,
        }
        manifest["content_digest"] = _manifest_digest(manifest)
        with (staging / "manifest.json").open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, target)
        return _summary(manifest, status="verified")
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _read_manifest(project: Dict[str, Any], version_id: str) -> tuple[Path, Dict[str, Any]]:
    path = _version_path(project, version_id)
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("id") != version_id:
            raise ValueError("mismatched version ID")
        if manifest.get("project_id") != project["id"]:
            raise ValueError("version belongs to a different project")
        if manifest.get("content_digest") != _manifest_digest(manifest):
            raise ValueError("manifest digest mismatch")
        if not isinstance(manifest.get("files"), list):
            raise ValueError("missing file inventory")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"Corrupt dataset version {version_id}: {exc}") from exc
    return path, manifest


def _require_active_labelset(project: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    if manifest.get("labelset_id", "default") != project.get("active_labelset_id", "default"):
        raise HTTPException(status_code=409, detail="Dataset version belongs to another label set. Activate that label set first.")


def _file_hash(path: Path, *, allow_symlink: bool = False) -> Optional[str]:
    if not path.is_file() or (path.is_symlink() and not allow_symlink):
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_backup_path(version_dir: Path, row: Dict[str, Any]) -> Path:
    raw = row.get("snapshot_path")
    if not isinstance(raw, str) or not raw.startswith("labels/"):
        raise HTTPException(status_code=422, detail="Version label backup path is invalid.")
    path = version_dir / raw
    if path.is_symlink() or not path.resolve().is_relative_to(version_dir.resolve()):
        raise HTTPException(status_code=422, detail="Version label backup escaped its directory.")
    return path


def _relative_path(row: Dict[str, Any]) -> Path:
    raw = row.get("relative_path")
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise HTTPException(status_code=422, detail="Version contains an invalid relative path.")
    relative = Path(raw)
    if relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
        raise HTTPException(status_code=422, detail="Version path leaves its label directory.")
    return relative


def _verify(project: Dict[str, Any], version_dir: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    source = _source_path(project)
    if str(source) != manifest["source_dataset_dir"]:
        raise HTTPException(status_code=409, detail="The active project points to a different dataset source.")
    studio = dataset_annotation_dir(source, routes_dataset.STUDIO_ANNOTATIONS_DIR)
    split = routes_dataset._split_manifest_file(source)
    changed: List[str] = []
    editable: List[str] = []
    for row in manifest["files"]:
        origin = row["origin"]
        if origin not in {"source", "studio", "split"}:
            raise HTTPException(status_code=422, detail="Version contains an invalid file origin.")
        relative = _relative_path(row)
        if origin == "source":
            source_file = source / relative
            if (str(source_file.resolve()) != row["source_path"]
                    or _file_hash(source_file, allow_symlink=True) != row["sha256"]):
                changed.append(f"source/{relative.as_posix()}")
        else:
            current = studio / relative if origin == "studio" else split
            if _file_hash(current) != row["sha256"]:
                editable.append(f"{origin}/{relative.as_posix()}")
        if row["kind"] == "label":
            backup = _safe_backup_path(version_dir, row)
            if _file_hash(backup) != row["sha256"]:
                changed.append(f"backup/{row['origin']}/{relative.as_posix()}")

    saved_studio = {row["relative_path"] for row in manifest["files"] if row["origin"] == "studio"}
    saved_source = {row["relative_path"] for row in manifest["files"] if row["origin"] == "source"}
    for file in _iter_files(source, source=True, project_dir=Path(project["project_dir"]).resolve()):
        relative = file.relative_to(source).as_posix()
        if relative not in saved_source:
            changed.append(f"source/{relative}")
    for file in _iter_files(studio, source=False, project_dir=Path(project["project_dir"])):
        relative = file.relative_to(studio).as_posix()
        if relative not in saved_studio:
            editable.append(f"studio/{relative}")
    if split.is_file() and not any(row["origin"] == "split" for row in manifest["files"]):
        editable.append("split/manifest.json")
    return {
        "id": manifest["id"], "status": "verified" if not changed else "changed",
        "changed_files": sorted(set(changed)), "editable_changed_files": sorted(set(editable)),
        "checked_file_count": len(manifest["files"]),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _restore_labels(project: Dict[str, Any], version_dir: Path, manifest: Dict[str, Any]) -> None:
    source = Path(manifest["source_dataset_dir"])
    studio = dataset_annotation_dir(source, routes_dataset.STUDIO_ANNOTATIONS_DIR)
    split = routes_dataset._split_manifest_file(source)
    studio.parent.mkdir(parents=True, exist_ok=True)
    split.parent.mkdir(parents=True, exist_ok=True)
    tx = uuid.uuid4().hex
    stage_studio = studio.parent / f".restore-{tx}-studio-stage"
    backup_studio = studio.parent / f".restore-{tx}-studio-backup"
    stage_split = split.parent / f".restore-{tx}-split-stage"
    backup_split = split.parent / f".restore-{tx}-split-backup"
    stage_studio.mkdir()
    has_split_snapshot = False
    try:
        for row in manifest["files"]:
            if row["origin"] == "studio":
                destination = stage_studio / _relative_path(row)
                if not destination.resolve().is_relative_to(stage_studio.resolve()):
                    raise HTTPException(status_code=422, detail="Invalid Studio label path in version.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(_safe_backup_path(version_dir, row), destination)
                if _file_hash(destination) != row["sha256"]:
                    raise HTTPException(status_code=409, detail=f"Version label changed during restore: {row['relative_path']}")
            elif row["origin"] == "split":
                shutil.copyfile(_safe_backup_path(version_dir, row), stage_split)
                if _file_hash(stage_split) != row["sha256"]:
                    raise HTTPException(status_code=409, detail="Saved split changed during restore.")
                has_split_snapshot = True

        studio_moved = False
        studio_installed = False
        split_moved = False
        split_installed = False
        try:
            if split.exists():
                os.replace(split, backup_split)
                split_moved = True
            if has_split_snapshot:
                os.replace(stage_split, split)
                split_installed = True
            if studio.exists():
                os.replace(studio, backup_studio)
                studio_moved = True
            os.replace(stage_studio, studio)
            studio_installed = True
        except OSError as exc:
            try:
                if studio_installed and studio.exists():
                    shutil.rmtree(studio)
                if studio_moved:
                    try:
                        os.replace(backup_studio, studio)
                    except OSError:
                        shutil.copytree(backup_studio, studio)
                if split_installed:
                    split.unlink(missing_ok=True)
                if split_moved:
                    try:
                        os.replace(backup_split, split)
                    except OSError:
                        shutil.copyfile(backup_split, split)
            except (OSError, shutil.Error) as rollback_exc:
                raise HTTPException(status_code=500, detail=f"Restore failed; rollback files retained at {backup_studio} and {backup_split}: {rollback_exc}") from exc
            if backup_studio.exists():
                shutil.rmtree(backup_studio)
            backup_split.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Restore failed; previous editable labels were recovered: {exc}") from exc

        if backup_studio.exists():
            shutil.rmtree(backup_studio)
        backup_split.unlink(missing_ok=True)
    finally:
        if stage_studio.exists():
            shutil.rmtree(stage_studio)
        stage_split.unlink(missing_ok=True)


@router.post("")
def create_version(req: VersionCreateRequest, request: Request):
    project = _current_project(request)
    source = _source_path(project, req.dataset_path)
    with _VERSION_LOCK:
        return _snapshot(project, source, req.name, req.note, "manual")


@router.get("")
def list_versions(request: Request):
    project = _current_project(request)
    versions = []
    for path in sorted(_versions_root(project).iterdir(), reverse=True):
        if path.is_dir() and _VERSION_ID.fullmatch(path.name):
            try:
                _, manifest = _read_manifest(project, path.name)
                if manifest.get("labelset_id", "default") == project.get("active_labelset_id", "default"):
                    versions.append(_summary(manifest))
            except HTTPException:
                versions.append({
                    "id": path.name, "name": "손상된 버전", "note": "", "kind": "manual",
                    "created_at": "", "source_dataset_dir": project.get("source_dataset_dir") or "",
                    "image_count": 0, "label_file_count": 0, "total_image_bytes": 0,
                    "copied_label_bytes": 0, "dataset_fingerprint": "", "status": "corrupt",
                })
    return {"versions": versions}


@router.get("/{version_id}")
def get_version(version_id: str, request: Request):
    project = _current_project(request)
    _, manifest = _read_manifest(project, version_id)
    _require_active_labelset(project, manifest)
    return manifest


@router.get("/{version_id}/verify")
def verify_version(version_id: str, request: Request):
    project = _current_project(request)
    path, manifest = _read_manifest(project, version_id)
    _require_active_labelset(project, manifest)
    return _verify(project, path, manifest)


@router.post("/{version_id}/restore")
def restore_version(version_id: str, request: Request):
    project = _current_project(request)
    with _VERSION_LOCK:
        from backend.api.routes_training import training_job_manager

        active = training_job_manager.get_active_job()
        if active is not None:
            raise HTTPException(status_code=409, detail="A training job is active. Finish or stop it before restoring labels.")
        path, manifest = _read_manifest(project, version_id)
        _require_active_labelset(project, manifest)
        verification = _verify(project, path, manifest)
        if verification["status"] != "verified":
            raise HTTPException(status_code=409, detail={
                "message": "Source files or saved labels changed; restoration was not started.",
                "changed_files": verification["changed_files"],
            })
        source = _source_path(project)
        backup = _snapshot(project, source, f"복원 전 자동 백업 · {manifest['name']}",
                           f"Before restoring {version_id}", "auto_backup")
        _restore_labels(project, path, manifest)
        return {
            "restored_version_id": version_id, "backup_version_id": backup["id"],
            "source_dataset_dir": str(source),
            "dataset_fingerprint": fingerprint_dataset(
                source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
                split_manifest=routes_dataset._split_manifest_file(source),
            ),
        }
