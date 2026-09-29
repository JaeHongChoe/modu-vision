"""Project workspaces, recent projects, and the active project across restarts."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.engine.checkpoint_paths import set_active_project_models_dir
from backend.engine.annotation_storage import migrate_legacy_dataset_overlay
from backend.engine.project_labelsets import activate_labelset, create_labelset, labelset_root, load_labelsets
from backend.engine.project_archive import ArchiveError, create_archive, restore_archive

logger = logging.getLogger("vision_ai_studio.routes_project")
router = APIRouter(prefix="/api/project", tags=["project"])
_ACTIVE_FILE_NAME = ".active_project.json"
_HISTORY_FILE_NAME = ".recent_projects.json"


def _write_json(path: Path, value: Dict[str, Any] | List[Dict[str, Any]]) -> None:
    """Replace JSON atomically so a crash cannot truncate the only manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _history_file(request: Request) -> Path:
    return _project_root(request) / _HISTORY_FILE_NAME


def _load_history(request: Request) -> List[Dict[str, Any]]:
    try:
        value = json.loads(_history_file(request).read_text(encoding="utf-8"))
        if not isinstance(value, list):
            return []
        return [
            item for item in value
            if isinstance(item, dict) and isinstance(item.get("project_dir"), str)
            and (Path(item["project_dir"]) / "project.json").is_file()
        ]
    except (OSError, ValueError, TypeError):
        return []


def _record_project_in_history(request: Request, project: Dict[str, Any]) -> None:
    path = project["project_dir"]
    history = [item for item in _load_history(request) if item.get("project_dir") != path]
    history.insert(0, {
        "id": project["id"],
        "name": project["name"],
        "task": project["task"],
        "project_dir": path,
        "updated_at": project["updated_at"],
    })
    try:
        _write_json(_history_file(request), history[:20])
    except OSError as exc:
        logger.warning("Could not update recent projects: %s", exc)


def _project_root(request: Request) -> Path:
    return Path(getattr(request.app.state, "project_dir", Path.cwd() / "projects")).resolve()


def _activate_project(request: Request, project: Dict[str, Any]) -> Dict[str, Any]:
    try:
        set_active_project_models_dir(project["models_dir"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _write_json(_project_root(request) / _ACTIVE_FILE_NAME, {"project_dir": project["project_dir"]})
    request.app.state.current_project = project
    try:
        if project.get("active_labelset_id", "default") == "default":
            migrate_legacy_dataset_overlay(project)
    except (OSError, ValueError) as exc:
        logger.warning("Could not copy legacy annotations into project %s: %s", project["id"], exc)
    try:
        from backend.api.routes_dataset import migrate_legacy_split_manifest

        migrate_legacy_split_manifest(project)
    except (OSError, ValueError) as exc:
        logger.warning("Could not copy legacy split into project %s: %s", project["id"], exc)
    _record_project_in_history(request, project)
    return project


def _load_project(path: Path) -> Dict[str, Any]:
    manifest = path / "project.json"
    if not path.is_dir():
        raise HTTPException(status_code=404, detail=f"Project directory not found: {path}")
    if not manifest.is_file():
        raise HTTPException(status_code=422, detail=f"No project.json in {path}. Create a project here first.")
    try:
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("project.json must contain an object")
        # A workspace can be moved; its managed folders move with project.json.
        data = dict(saved)
        active_set = load_labelsets(path)["active_id"]
        data.update({
            "project_dir": str(path),
            "dataset_dir": str(path / "dataset"),
            "models_dir": str(path / "models"),
            "reports_dir": str(path / "reports"),
            "annotations_dir": str(labelset_root(path, active_set)),
            "active_labelset_id": active_set,
        })
        data.setdefault("source_dataset_dir", None)
        project = ProjectConfigResponse.model_validate(data).model_dump()
    except (OSError, ValueError, TypeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid project.json: {exc}") from exc
    if any(project[key] != saved.get(key) for key in
           ("project_dir", "dataset_dir", "models_dir", "reports_dir", "annotations_dir")):
        _write_json(manifest, project)
    for key in ("dataset_dir", "models_dir", "reports_dir", "annotations_dir"):
        Path(project[key]).mkdir(parents=True, exist_ok=True)
    return project


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(..., min_length=1, max_length=100)
    task: Literal["classification", "detection", "segmentation", "anomaly"] = "classification"
    project_dir: Optional[str] = None
    description: str = ""
    active_preset: Literal["fast", "precision"] = "fast"


class ProjectOpenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    project_dir: str = Field(..., min_length=1)


class LabelSetCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class ProjectBackupRequest(BaseModel):
    destination_dir: str = Field(..., min_length=1)


class ProjectRestoreRequest(BaseModel):
    archive_path: str = Field(..., min_length=1)
    target_dir: str = Field(..., min_length=1)


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    task: Optional[Literal["classification", "detection", "segmentation", "anomaly"]] = None
    description: Optional[str] = None
    active_preset: Optional[Literal["fast", "precision"]] = None
    source_dataset_dir: Optional[str] = None


class ProjectConfigResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    task: Literal["classification", "detection", "segmentation", "anomaly"]
    project_dir: str
    dataset_dir: str
    models_dir: str
    reports_dir: str
    annotations_dir: str
    active_labelset_id: str = "default"
    description: str = ""
    active_preset: Literal["fast", "precision"] = "fast"
    source_dataset_dir: Optional[str] = None
    created_at: str
    updated_at: str


@router.post("/create", response_model=ProjectConfigResponse)
def create_project(req: ProjectCreateRequest, request: Request):
    """Create a new workspace; never replace an existing project.json."""
    if req.project_dir:
        path = Path(req.project_dir).expanduser().resolve()
    else:
        slug = re.sub(r"[^\w-]+", "_", req.name.strip().lower(), flags=re.UNICODE).strip("_")
        path = (_project_root(request) / (slug or f"project_{uuid.uuid4().hex[:8]}")).resolve()
    manifest = path / "project.json"
    if manifest.exists():
        raise HTTPException(status_code=409, detail=f"Project already exists at {path}. Open it instead.")
    if path.exists() and not path.is_dir():
        raise HTTPException(status_code=409, detail=f"A file already exists at {path}.")

    path.mkdir(parents=True, exist_ok=True)
    for dirname in ("dataset", "models", "reports", "annotations"):
        (path / dirname).mkdir(exist_ok=True)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    project = ProjectConfigResponse(
        id=uuid.uuid4().hex[:8], name=req.name.strip(), task=req.task,
        project_dir=str(path), dataset_dir=str(path / "dataset"),
        models_dir=str(path / "models"), reports_dir=str(path / "reports"),
        annotations_dir=str(path / "annotations"), description=req.description,
        active_preset=req.active_preset, created_at=now, updated_at=now,
    ).model_dump()
    load_labelsets(path)
    _write_json(manifest, project)
    return _activate_project(request, project)


@router.post("/open", response_model=ProjectConfigResponse)
def open_project(req: ProjectOpenRequest, request: Request):
    """Open only a valid project workspace, keeping arbitrary image folders untouched."""
    project = _load_project(Path(req.project_dir).expanduser().resolve())
    return _activate_project(request, project)


@router.get("/current", response_model=ProjectConfigResponse)
def get_current_project(request: Request):
    project = getattr(request.app.state, "current_project", None)
    if project is not None:
        return project

    pointer = _project_root(request) / _ACTIVE_FILE_NAME
    try:
        path = json.loads(pointer.read_text(encoding="utf-8"))["project_dir"]
        project = _load_project(Path(path).expanduser().resolve())
        return _activate_project(request, project)
    except (OSError, ValueError, TypeError, KeyError, HTTPException) as exc:
        if pointer.exists():
            logger.warning("Saved active project unavailable: %s", exc)

    default_dir = _project_root(request) / "default_project"
    if (default_dir / "project.json").exists():
        return _activate_project(request, _load_project(default_dir))
    return create_project(ProjectCreateRequest(name="Default Project", project_dir=str(default_dir)), request)


@router.put("/update", response_model=ProjectConfigResponse)
def update_project(req: ProjectUpdateRequest, request: Request):
    project = dict(get_current_project(request))
    updates = req.model_dump(exclude_unset=True)
    if "source_dataset_dir" in updates and updates["source_dataset_dir"]:
        source = Path(updates["source_dataset_dir"]).expanduser().resolve()
        workspace = Path(project["project_dir"]).resolve()
        if workspace == source or workspace.is_relative_to(source):
            raise HTTPException(status_code=422, detail="Project workspace cannot be inside its dataset source. Choose a separate project directory.")
        updates["source_dataset_dir"] = str(source)
    project.update(updates)
    project["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    validated = ProjectConfigResponse.model_validate(project).model_dump()
    _write_json(Path(validated["project_dir"]) / "project.json", validated)
    return _activate_project(request, validated)


@router.get("/list")
def list_projects(request: Request):
    return {"projects": _load_history(request)}


@router.get("/labelsets")
def list_labelsets(request: Request):
    project = get_current_project(request)
    try:
        return load_labelsets(Path(project["project_dir"]))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/labelsets")
def add_labelset(req: LabelSetCreateRequest, request: Request):
    project = get_current_project(request)
    try:
        return create_labelset(Path(project["project_dir"]), req.name)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/labelsets/{set_id}/activate", response_model=ProjectConfigResponse)
def select_labelset(set_id: str, request: Request):
    project = get_current_project(request)
    try:
        activate_labelset(Path(project["project_dir"]), set_id)
        return _activate_project(request, _load_project(Path(project["project_dir"])))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Label set not found: {set_id}") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/backup")
def backup_project(req: ProjectBackupRequest, request: Request):
    project = get_current_project(request)
    try:
        return create_archive(project, Path(req.destination_dir))
    except ArchiveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/restore", response_model=ProjectConfigResponse)
def restore_project(req: ProjectRestoreRequest, request: Request):
    try:
        restored = restore_archive(Path(req.archive_path), Path(req.target_dir))
        return _activate_project(request, _load_project(restored))
    except ArchiveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
