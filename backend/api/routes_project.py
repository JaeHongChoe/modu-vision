"""
backend/api/routes_project.py

Project Configuration, Workspace Management & Active State Persistence.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("vision_ai_studio.routes_project")

router = APIRouter(prefix="/api/project", tags=["project"])

# Global state for currently active project
_CURRENT_PROJECT: Optional[Dict[str, Any]] = None
_HISTORY_FILE = Path.home() / ".vision_ai_studio_history.json"


def _load_history() -> List[Dict[str, Any]]:
    if _HISTORY_FILE.exists():
        try:
            with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_history(history: List[Dict[str, Any]]) -> None:
    try:
        with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.debug("Failed to write history file: %s", e)


def _record_project_in_history(proj: Dict[str, Any]) -> None:
    history = _load_history()
    # Filter out existing matching project_dir
    history = [h for h in history if h.get("project_dir") != proj.get("project_dir")]
    history.insert(0, {
        "id": proj.get("id"),
        "name": proj.get("name"),
        "task": proj.get("task"),
        "project_dir": proj.get("project_dir"),
        "updated_at": proj.get("updated_at"),
    })
    _save_history(history[:20])  # keep recent 20


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(..., min_length=1, max_length=100)
    task: Literal["classification", "detection", "segmentation", "anomaly"] = "classification"
    project_dir: Optional[str] = None
    description: Optional[str] = ""
    active_preset: Optional[Literal["fast", "precision"]] = "fast"


class ProjectOpenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    project_dir: str = Field(..., min_length=1)


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    task: Optional[Literal["classification", "detection", "segmentation", "anomaly"]] = None
    description: Optional[str] = None
    active_preset: Optional[Literal["fast", "precision"]] = None


class ProjectConfigResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    task: str
    project_dir: str
    dataset_dir: str
    models_dir: str
    reports_dir: str
    annotations_dir: str
    description: str = ""
    active_preset: str = "fast"
    created_at: str
    updated_at: str


@router.post("/create", response_model=ProjectConfigResponse)
def create_project(req: ProjectCreateRequest, request: Request):
    """Creates a new project workspace directory and initializes project.json."""
    global _CURRENT_PROJECT

    base_root = getattr(request.app.state, "project_dir", Path.cwd() / "projects")
    if req.project_dir:
        p_path = Path(req.project_dir).resolve()
    else:
        slug = req.name.lower().replace(" ", "_")
        p_path = (Path(base_root) / slug).resolve()

    p_path.mkdir(parents=True, exist_ok=True)
    dataset_dir = p_path / "dataset"
    models_dir = p_path / "models"
    reports_dir = p_path / "reports"
    annotations_dir = p_path / "annotations"

    for d in (dataset_dir, models_dir, reports_dir, annotations_dir):
        d.mkdir(parents=True, exist_ok=True)

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    proj_id = str(uuid.uuid4())[:8]

    proj_data = {
        "id": proj_id,
        "name": req.name,
        "task": req.task,
        "project_dir": str(p_path),
        "dataset_dir": str(dataset_dir),
        "models_dir": str(models_dir),
        "reports_dir": str(reports_dir),
        "annotations_dir": str(annotations_dir),
        "description": req.description or "",
        "active_preset": req.active_preset or "fast",
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    config_file = p_path / "project.json"
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(proj_data, f, indent=2, ensure_ascii=False)

    _CURRENT_PROJECT = proj_data
    _record_project_in_history(proj_data)
    return proj_data


@router.post("/open", response_model=ProjectConfigResponse)
def open_project(req: ProjectOpenRequest):
    """Opens an existing project from project_dir containing project.json."""
    global _CURRENT_PROJECT
    p_path = Path(req.project_dir).resolve()
    config_file = p_path / "project.json"

    if not config_file.exists():
        # If directory exists but no project.json, auto-initialize basic project
        if p_path.is_dir():
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            proj_data = {
                "id": str(uuid.uuid4())[:8],
                "name": p_path.name,
                "task": "classification",
                "project_dir": str(p_path),
                "dataset_dir": str(p_path / "dataset"),
                "models_dir": str(p_path / "models"),
                "reports_dir": str(p_path / "reports"),
                "annotations_dir": str(p_path / "annotations"),
                "description": "",
                "active_preset": "fast",
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            for k in ("dataset_dir", "models_dir", "reports_dir", "annotations_dir"):
                Path(proj_data[k]).mkdir(parents=True, exist_ok=True)
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(proj_data, f, indent=2, ensure_ascii=False)
        else:
            raise HTTPException(status_code=404, detail=f"Project directory not found: {req.project_dir}")
    else:
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                proj_data = json.load(f)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Corrupted project.json: {e}")

    _CURRENT_PROJECT = proj_data
    _record_project_in_history(proj_data)
    return proj_data


@router.get("/current", response_model=ProjectConfigResponse)
def get_current_project(request: Request):
    """Returns the currently active project configuration, or a default session project if none is loaded."""
    global _CURRENT_PROJECT
    if _CURRENT_PROJECT is not None:
        return _CURRENT_PROJECT

    # If no project is currently open, create or return a default scratch project
    base_root = getattr(request.app.state, "project_dir", Path.cwd() / "projects")
    default_dir = Path(base_root) / "default_project"
    default_dir.mkdir(parents=True, exist_ok=True)
    req = ProjectCreateRequest(name="Default Project", task="classification", project_dir=str(default_dir))
    return create_project(req, request)


@router.put("/update", response_model=ProjectConfigResponse)
def update_project(req: ProjectUpdateRequest):
    """Updates attributes of the currently active project."""
    global _CURRENT_PROJECT
    if _CURRENT_PROJECT is None:
        raise HTTPException(status_code=404, detail="No active project open to update")

    if req.name is not None:
        _CURRENT_PROJECT["name"] = req.name
    if req.task is not None:
        _CURRENT_PROJECT["task"] = req.task
    if req.description is not None:
        _CURRENT_PROJECT["description"] = req.description
    if req.active_preset is not None:
        _CURRENT_PROJECT["active_preset"] = req.active_preset

    _CURRENT_PROJECT["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    config_file = Path(_CURRENT_PROJECT["project_dir"]) / "project.json"
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(_CURRENT_PROJECT, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Failed to write updated project.json: %s", e)

    _record_project_in_history(_CURRENT_PROJECT)
    return _CURRENT_PROJECT


@router.get("/list")
def list_projects():
    """Lists recent projects from history."""
    return {"projects": _load_history()}
