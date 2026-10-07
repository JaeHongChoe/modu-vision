"""Project workspaces, recent projects, and the active project across restarts."""

from __future__ import annotations

import json
import sqlite3
import logging
import os
import re
import tempfile
import time
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.engine.checkpoint_paths import set_active_project_models_dir, active_project_models_dir
from backend.engine.annotation_storage import migrate_legacy_dataset_overlay
from backend.engine.project_labelsets import activate_labelset, create_labelset, labelset_root, load_labelsets
from backend.engine.project_archive import ArchiveError, create_archive, restore_archive
from backend.engine.project_migration import preview_migration, legacy_preview_migration, apply_migration, normalize_legacy_manifest, restore_migration, finish_migration, MigrationError

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
    account=getattr(request.state,'account_user',None)
    accounts=getattr(request.app.state,'accounts',None)
    try:
        if project.get("active_labelset_id", "default") == "default":
            migrate_legacy_dataset_overlay(project)
    except (OSError, ValueError) as exc:
        raise HTTPException(422,detail=f'Project activation failed; legacy annotation migration needs recovery: {exc}') from exc
    try:
        from backend.api.routes_dataset import migrate_legacy_split_manifest

        migrate_legacy_split_manifest(project)
    except (OSError, ValueError) as exc:
        raise HTTPException(422,detail=f'Project activation failed; legacy split migration needs recovery: {exc}') from exc
    if account and accounts:
        accounts.register_project(project['id'],project['project_dir'],account['id'])
        request.state.scoped_project=project
        return project
    pointer=_project_root(request)/_ACTIVE_FILE_NAME
    history=_history_file(request)
    prior_files={path:path.read_bytes() if path.exists() else None for path in (pointer,history)}
    prior_models=active_project_models_dir()
    try:
        # Publish activation only after required migrations and history writes succeed.
        _record_project_in_history(request, project)
        _write_json(_project_root(request) / _ACTIVE_FILE_NAME, {"project_dir": project["project_dir"]})
        set_active_project_models_dir(project["models_dir"])
    except (OSError,ValueError) as exc:
        for path,raw in prior_files.items():
            try:
                if raw is None:path.unlink(missing_ok=True)
                else:
                    from backend.engine.project_migration import _atomic_bytes
                    _atomic_bytes(path,raw)
            except OSError:logger.error('Activation pointer recovery failed for %s',path.name)
        set_active_project_models_dir(prior_models)
        raise HTTPException(status_code=422, detail=f'Project activation failed: {exc}') from exc
    request.app.state.current_project = project
    return project


def _load_project(path: Path) -> Dict[str, Any]:
    # Includes legacy normalization and lazy labelset initialization, even when
    # called by authorization/context selection before the general API guard.
    if not path.is_dir() or not (path/'project.json').is_file():
        return _load_project_unfenced(path)
    try:legacy_preview_migration(path)  # reject unsupported input before creating admission files
    except (MigrationError,OSError,ValueError) as exc:raise HTTPException(422,detail=str(exc)) from exc
    from backend.engine.migration_guard import maintenance_guard
    guard=maintenance_guard(path)
    try:guard.__enter__()
    except ValueError as exc:raise HTTPException(423,detail=str(exc)) from exc
    try:return _load_project_unfenced(path)
    finally:guard.__exit__(None,None,None)


def _load_project_unfenced(path: Path) -> Dict[str, Any]:
    manifest = path / "project.json"
    if not path.is_dir():
        raise HTTPException(status_code=404, detail=f"Project directory not found: {path}")
    if not manifest.is_file():
        raise HTTPException(status_code=422, detail=f"No project.json in {path}. Create a project here first.")
    stage = "manifest-compatibility"
    try:
        # Opening/authorizing an active project validates its manifest. A full
        # migration dry-run inventories models, external datasets and live job
        # stores and belongs to the explicit compatibility/migration routes.
        legacy_preview_migration(path)
        stage = "manifest-read"
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("project.json must contain an object")
        # A workspace can be moved; its managed folders move with project.json.
        data = dict(saved)
        # Validate the project shape before the backed-up schema normalization.
        stage = "manifest-validation"
        ProjectConfigResponse.model_validate(saved)
        stage = "legacy-normalization"
        normalize_legacy_manifest(path)
        stage = "normalized-manifest-read"
        saved = json.loads(manifest.read_text(encoding="utf-8"));data=dict(saved)
        stage = "labelset-read"
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
        stage = "resolved-validation"
        project = ProjectConfigResponse.model_validate(data).model_dump()
    except (OSError, ValueError, TypeError, ValidationError) as exc:
        # An activation I/O error can concern a labelset or recovery file, not the
        # manifest. Preserve the refusal and identify the actual I/O operation
        # without disclosing traceback paths, arguments or local variables.
        diagnostics = [f"stage={stage}"]
        cause = exc
        seen = set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if isinstance(cause, OSError):
                for attribute in ("errno", "winerror"):
                    number = getattr(cause, attribute, None)
                    if type(number) is int:
                        diagnostics.append(f"{attribute}={number}")
                frames = traceback.extract_tb(cause.__traceback__)
                if frames and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,79}", frames[-1].name):
                    diagnostics.append(f"source={frames[-1].name}")
                operations = [frame.name for frame in frames if "backend" in Path(frame.filename).parts
                              and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,79}", frame.name)][-3:]
                if operations:
                    diagnostics.append(f"via={'/'.join(operations)}")
                break
            cause = cause.__cause__
        raise HTTPException(status_code=422, detail=f"Invalid project.json: {exc} [{';'.join(diagnostics)}]") from exc
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
    template: dict[str, Any] | None = None


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


class RetentionPolicyRequest(BaseModel):
    retention_days: int = Field(default=30, strict=True, ge=0, le=3650)
    trash_days: int = Field(default=30, strict=True, ge=0, le=3650)
    quota_bytes: int | None = Field(default=None, strict=True, ge=1)


class RetentionPinRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=128)
    paths: list[str] = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=200)


class RetentionTrashRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=1000)
    dry_run: bool = True
    expected_preview_sha256: str | None = Field(default=None,strict=True,pattern='^[0-9a-f]{64}$')


class RetentionRestoreRequest(BaseModel):
    trash_id: str = Field(pattern='^[0-9a-f]{32}$')


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    task: Optional[Literal["classification", "detection", "segmentation", "anomaly"]] = None
    description: Optional[str] = None
    active_preset: Optional[Literal["fast", "precision"]] = None
    source_dataset_dir: Optional[str] = None


class ProjectConfigResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal[1] = 1
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


class CompatibilityApplyRequest(ProjectOpenRequest):
    expected_source_sha256: Optional[str] = Field(None,pattern=r"^[0-9a-f]{64}$")
    expected_manifest_sha256: str = Field(...,pattern=r'^[0-9a-f]{64}$')


@router.post('/compatibility/preview')
def compatibility_preview(req:ProjectOpenRequest,request:Request):
    _compatibility_scope(req.project_dir,request)
    try:return preview_migration(Path(req.project_dir))
    except (MigrationError,OSError,ValueError) as exc:raise HTTPException(422,detail=str(exc)) from exc


@router.post('/compatibility/apply')
def compatibility_apply(req:CompatibilityApplyRequest,request:Request):
    _compatibility_scope(req.project_dir,request,write=True)
    try:return apply_migration(Path(req.project_dir),req.expected_manifest_sha256,expected_source_sha256=req.expected_source_sha256)
    except (MigrationError,OSError,ValueError) as exc:raise HTTPException(409,detail=str(exc)) from exc


class CompatibilityRecoveryRequest(ProjectOpenRequest):
    migration_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    action: Literal['restore','finish']


@router.post('/compatibility/recover')
def compatibility_recover(req:CompatibilityRecoveryRequest,request:Request):
    _compatibility_scope(req.project_dir,request,write=True)
    try:
        return restore_migration(Path(req.project_dir),req.migration_id) if req.action=='restore' else finish_migration(Path(req.project_dir),req.migration_id)
    except (MigrationError,OSError,ValueError) as exc:raise HTTPException(409,detail=str(exc)) from exc


@router.post('/compatibility/global-preview')
def compatibility_global_preview(request:Request):
    account=getattr(request.state,'account_user',None)
    if account and not account.get('administrator'):
        raise HTTPException(403,'Global migration inventory requires workspace administrator permission')
    from backend.engine.project_migration import preview_global_migration
    import os
    root=Path(os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR') or Path.home()/'.modu_vision')
    try:return preview_global_migration(root)
    except (MigrationError,OSError,ValueError) as exc:raise HTTPException(422,detail=str(exc)) from exc


def _compatibility_scope(directory,request,write=False):
    account=getattr(request.state,'account_user',None)
    if not account:return
    selected=getattr(request.state,'scoped_project',None)
    if not selected or Path(directory).expanduser().resolve()!=Path(selected['project_dir']).resolve():
        raise HTTPException(403,'Compatibility checks must address the selected authorized project')
    if write and request.app.state.accounts.project_role(account['id'],selected['id'])!='owner':
        raise HTTPException(403,'Project owner permission is required for schema migration')


@router.post("/create", response_model=ProjectConfigResponse)
def create_project(req: ProjectCreateRequest, request: Request):
    """Create a new workspace; never replace an existing project.json."""
    from backend.engine.project_templates import validate_template, apply_template
    template = None
    if req.template is not None:
        try:
            template = validate_template(req.template)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
    if req.project_dir:
        path = Path(req.project_dir).expanduser().resolve()
    else:
        slug = re.sub(r"[^\w-]+", "_", req.name.strip().lower(), flags=re.UNICODE).strip("_")
        path = (_project_root(request) / (slug or f"project_{uuid.uuid4().hex[:8]}")).resolve()
    manifest = path / "project.json"
    if template is not None and path.exists():
        raise HTTPException(409, detail='A template requires a fresh project location')
    if manifest.exists():
        raise HTTPException(status_code=409, detail=f"Project already exists at {path}. Open it instead.")
    if path.exists() and not path.is_dir():
        raise HTTPException(status_code=409, detail=f"A file already exists at {path}.")

    path.mkdir(parents=True, exist_ok=True)
    for dirname in ("dataset", "models", "reports", "annotations"):
        (path / dirname).mkdir(exist_ok=True)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    project = ProjectConfigResponse(
        id=uuid.uuid4().hex[:8], name=req.name.strip(), task=template['task'] if template else req.task,
        project_dir=str(path), dataset_dir=str(path / "dataset"),
        models_dir=str(path / "models"), reports_dir=str(path / "reports"),
        annotations_dir=str(path / "annotations"), description=req.description,
        active_preset=template['active_preset'] if template else req.active_preset, created_at=now, updated_at=now,
    ).model_dump()
    load_labelsets(path)
    _write_json(manifest, project)
    if template is not None:
        apply_template(path, template)
    return _activate_project(request, project)


@router.post("/open", response_model=ProjectConfigResponse)
def open_project(req: ProjectOpenRequest, request: Request):
    """Open only a valid project workspace, keeping arbitrary image folders untouched."""
    target=Path(req.project_dir).expanduser().resolve()
    if not target.is_dir() or not (target/'project.json').is_file():
        return _activate_project(request,_load_project(target))  # retain existing404/422 validation
    try:legacy_preview_migration(target)
    except (MigrationError,OSError,ValueError) as exc:raise HTTPException(422,detail=str(exc)) from exc
    from backend.engine.migration_guard import maintenance_guard
    guard=maintenance_guard(target)
    try:guard.__enter__()
    except ValueError as exc:raise HTTPException(423,detail=str(exc)) from exc
    try:
        project=_load_project(target)
        return _activate_project(request,project)
    finally:guard.__exit__(None,None,None)


@router.get("/current", response_model=ProjectConfigResponse)
def get_current_project(request: Request):
    scoped=getattr(request.state,'scoped_project',None)
    if scoped is not None:return scoped
    if getattr(request.state,'account_user',None) is not None:
        raise HTTPException(409,'Select an authorized shared project first')
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


@contextmanager
def _project_context_mutation(project):
    """Fence source/labelset publication against live release authorization."""
    from backend.engine.runtime_process_control import runtime_state_lock
    with_context = runtime_state_lock(project["project_dir"])
    try:
        with_context.__enter__()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Project release authorization is in progress; retry the project change") from exc
    try:
        yield _load_project(Path(project["project_dir"]))
    finally:
        with_context.__exit__(None, None, None)


@router.put("/update", response_model=ProjectConfigResponse)
def update_project(req: ProjectUpdateRequest, request: Request):
    with _project_context_mutation(get_current_project(request)) as project:
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
    account=getattr(request.state,'account_user',None)
    if account:
        return {'projects':[_load_project(Path(row['path'])) for row in request.app.state.accounts.projects_for(account['id'])]}
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
        with _project_context_mutation(project) as current:
            return create_labelset(Path(current["project_dir"]), req.name)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/labelsets/{set_id}/activate", response_model=ProjectConfigResponse)
def select_labelset(set_id: str, request: Request):
    project = get_current_project(request)
    try:
        with _project_context_mutation(project) as current:
            activate_labelset(Path(current["project_dir"]), set_id)
            return _activate_project(request, _load_project(Path(current["project_dir"])))
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


@router.get('/template')
def project_setup_template(request: Request):
    from backend.engine.project_templates import export_template
    try:
        return export_template(get_current_project(request))
    except (ValueError, OSError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


def _retention_action(request, action):
    from backend.engine.artifact_retention import ArtifactRetention
    project=get_current_project(request)
    try:return action(ArtifactRetention(project['project_dir']),project)
    except (ValueError,OSError,KeyError,sqlite3.Error) as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/retention')
def retention_status(request: Request):
    return _retention_action(request,lambda store,project:store.status(project))


@router.put('/retention/policy')
def retention_policy(req: RetentionPolicyRequest,request: Request):
    return _retention_action(request,lambda store,project:store.configure(**req.model_dump()))


@router.post('/retention/pins')
def retention_pin(req: RetentionPinRequest,request: Request):
    if req.owner.startswith(('derived:','backup:','deployment:')):raise HTTPException(422,'System retention pin owners are reserved')
    return _retention_action(request,lambda store,project:{'pinned':store.pin('manual:'+req.owner,req.paths,reason=req.reason)})


@router.post('/retention/trash')
def retention_trash(req: RetentionTrashRequest,request: Request):
    return _retention_action(request,lambda store,project:store.move_to_trash(req.paths,project=project,dry_run=req.dry_run,expected_preview_sha256=req.expected_preview_sha256))


@router.post('/retention/restore-trash')
def retention_restore(req: RetentionRestoreRequest,request: Request):
    return _retention_action(request,lambda store,project:store.restore_trash(req.trash_id))


@router.post("/restore", response_model=ProjectConfigResponse)
def restore_project(req: ProjectRestoreRequest, request: Request):
    try:
        restored = restore_archive(Path(req.archive_path), Path(req.target_dir))
        return _activate_project(request, _load_project(restored))
    except ArchiveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
