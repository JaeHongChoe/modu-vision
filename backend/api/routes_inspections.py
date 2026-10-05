"""Durable inspection runs and append-only operator review records.

The source model result is written once. A later operator decision lives in a
separate review log so a manual override never rewrites the model evidence.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from backend.engine.sqlite_wal import use_wal  # a concurrent WAL switch is retried, not failed
from backend.engine.sqlite_schema import add_missing_columns  # two openers never add a column twice
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import State

from backend.api.routes_project import _load_project, _project_root, get_current_project
from backend.api.routes_dataset import list_dataset_images
from backend.api.routes_evaluation import _resolve_job_artifacts
from backend.api.routes_flowchart import _FLOW_SAVE_LOCK
from backend.engine.flowchart_engine import (
    FlowchartExecutionResult, FlowchartPipeline, FlowchartRunRequest,
    ordered_linear_nodes, verified_checkpoint_scope,
)
from backend.engine.checkpoint_paths import is_job_id
from backend.engine.flow_provenance import canonical_pipeline_json
from backend.engine.specialized_models import SPECIALIZED_TASKS, FLOW_TASKS, flow_model_task, valid_flow_job, resolve_specialized_checkpoint


router = APIRouter(prefix="/api/inspections", tags=["inspections"])
Verdict = Literal["OK", "NG", "REVIEW"]
RowState = Literal["pending", "running", "OK", "NG", "REVIEW", "error", "skipped"]
PROCESS_INSTANCE = uuid.uuid4().hex


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _csv_cell(value: Any) -> Any:
    """Keep untrusted filenames and review text literal in spreadsheet viewers."""
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_identity(source_folder: str) -> Optional[str]:
    """Compare source aliases without rewriting older run evidence or listing directories."""
    try:
        return str(Path(source_folder).expanduser().resolve())
    except (OSError, RuntimeError, ValueError, TypeError):
        # An unavailable or malformed legacy alias must not hide other runs.
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_references(pipeline: FlowchartPipeline) -> Dict[str, str]:
    references: Dict[str, str] = {}
    for node in pipeline.nodes:
        task = flow_model_task(node)
        if task is None: continue
        job_id = node.data.model_job_id
        if (not job_id or not valid_flow_job(job_id, task)
                or task not in FLOW_TASKS
                or (job_id in references and references[job_id] != task)):
            raise HTTPException(status_code=409, detail="Saved inspection flow has an invalid model reference.")
        references[job_id] = task
    if not references:
        raise HTTPException(status_code=409, detail="Active saved inspection flow has no completed model.")
    return references


def _owned_checkpoint(project: Dict[str, Any], job_id: str, checkpoint: Path) -> bool:
    """Accept the active project's model or a legacy global job, never another project model root."""
    parent = checkpoint.parent.resolve()
    from backend.engine.specialized_models import require_completed_checkpoint
    try:require_completed_checkpoint(checkpoint)
    except (ValueError,OSError):return False
    return parent in {*(Path(project["models_dir"]).resolve()/task/job_id for task in SPECIALIZED_TASKS),
        Path(project["models_dir"]).resolve() / job_id,
        (Path.cwd() / "models" / job_id).resolve(),
        (Path.cwd() / "projects" / job_id / "models").resolve(),
    }


def _verified_run_provenance(payload: "CreateRun", project: Dict[str, Any]) -> tuple[Optional[str], Dict[str, str], Dict[str, str]]:
    """Bind a configured project run to its active saved graph, source images, and checkpoints."""
    configured = project.get("source_dataset_dir")
    if not configured:
        # Old projects without a selected source keep their existing records readable.
        return None, {}, {}
    source = Path(configured).expanduser().resolve()
    requested = Path(payload.source_folder).expanduser().resolve()
    if not source.is_dir() or requested != source or payload.task != project.get("task"):
        raise HTTPException(status_code=409, detail="Inspection source or task differs from the active project.")

    project_dir = Path(project["project_dir"]).resolve()
    pointer = project_dir / "flowcharts" / "active.json"
    with _FLOW_SAVE_LOCK:
        try:
            active = json.loads(pointer.read_text(encoding="utf-8"))
            version_id = active["version_id"]
            if (active.get("project_id") != project["id"]
                    or active.get("source_dataset_path") != str(source)
                    or not isinstance(version_id, str)
                    or len(version_id) != 32
                    or any(char not in "0123456789abcdef" for char in version_id)):
                raise ValueError("Active flow belongs to another project or dataset.")
            record = json.loads((project_dir / "flowcharts" / "versions" / f"{version_id}.json").read_text(encoding="utf-8"))
            if record.get("version_id") != version_id or record.get("source_dataset_path") != str(source):
                raise ValueError("Saved flow version does not match the active project and source.")
            saved_graph = record["pipeline"]
            if not isinstance(saved_graph, dict):
                raise ValueError("Saved inspection graph is invalid.")
            pipeline = FlowchartPipeline.model_validate(saved_graph)
            submitted = FlowchartPipeline.model_validate(payload.pipeline)
            # Browser JSON turns integral positions such as 50.0 into 50. Both
            # graphs execute identically after schema validation.
            if pipeline != submitted:
                raise ValueError("Inspection graph differs from the active saved flow version.")
            ordered_linear_nodes(pipeline)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(status_code=409, detail=f"Active saved inspection flow is unavailable or changed: {exc}") from exc

    listed = list_dataset_images(
        folder_path=str(source), task=payload.task, limit=50000, offset=0,
        split=None if payload.scope == "all" else payload.scope,
        class_name=None, label_status=None,
    )
    image_index = {str(Path(item["file_path"]).resolve()): item for item in listed["items"]}
    for image in payload.images:
        path = Path(image.file_path).expanduser().resolve()
        expected = image_index.get(str(path))
        if (not path.is_file() or not path.is_relative_to(source) or expected is None
                or image.file_path != expected["file_path"]
                or image.image_id != expected["image_id"]
                or image.file_name != expected["file_name"]
                or image.split != expected["split"]):
            raise HTTPException(status_code=409, detail=f"Inspection image is not in the selected source and scope: {image.file_path}")

    model_sha256: Dict[str, str] = {}
    model_paths: Dict[str, str] = {}
    for job_id, task in _model_references(pipeline).items():
        try:
            if task in SPECIALIZED_TASKS:
                checkpoint,_ = resolve_specialized_checkpoint(project["models_dir"],job_id,task,str(source))
            else:
                _, checkpoint, _, _, _, _ = _resolve_job_artifacts(
                job_id, source_dataset_path=str(source), source_task=task,
            )
            local_job = Path(project["models_dir"]).resolve() / job_id
            if not _owned_checkpoint(project, job_id, checkpoint) or (
                local_job.is_dir() and checkpoint.parent.resolve() != local_job
            ):
                raise ValueError("Another project's checkpoint was selected for this job ID.")
            model_sha256[job_id] = _sha256(checkpoint)
            model_paths[job_id] = str(checkpoint.resolve())
        except (HTTPException, OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=f"Saved model {job_id} is no longer verified for this source and task.") from exc
    return version_id, model_sha256, model_paths


class ImageReference(BaseModel):
    model_config = ConfigDict(extra="allow")
    image_id: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    split: Optional[str] = None
    thumbnail_url: Optional[str] = None


class CreateRun(BaseModel):
    source_folder: str = Field(min_length=1)
    task: Literal["classification", "detection", "segmentation", "anomaly"]
    scope: Literal["test", "val", "train", "all"]
    pipeline: Dict[str, Any]
    images: List[ImageReference] = Field(min_length=1, max_length=50000)
    execution_target: Literal["local", "selected_compute", "model_compute"] = "model_compute"
    device: str = Field(default="cpu", pattern=r"^(cpu|mps|cuda(?::[0-9]+)?)$")
    compute_profile_id: Optional[str] = None
    project_id: Optional[str] = None


def _freeze_execution_config(payload: CreateRun, project: Dict[str, Any]) -> Dict[str, Any]:
    if payload.execution_target != "model_compute" and not payload.project_id:
        raise HTTPException(status_code=422, detail="Explicit inspection execution requires its project identity.")
    if payload.project_id is not None and payload.project_id != project["id"]:
        raise HTTPException(status_code=409, detail="Inspection project changed before the run was created.")
    profile = None
    if payload.execution_target == "selected_compute":
        from backend.remote.profiles import get_profile_store
        if not payload.compute_profile_id:
            raise HTTPException(status_code=422, detail="Choose an explicit execution compute profile.")
        selected = get_profile_store().get(payload.compute_profile_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Execution compute profile is unavailable.")
        profile = selected.model_dump(mode="json")
    elif payload.compute_profile_id is not None:
        raise HTTPException(status_code=422, detail="A compute profile applies only to selected compute execution.")
    return {"execution_target": payload.execution_target, "device": payload.device,
            "compute_profile_id": payload.compute_profile_id, "project_id": project["id"],
            "profile": profile}


def _verified_execution_choice(run: Dict[str, Any], project: Dict[str, Any]):
    """Reopen the exact choice instead of following a subsequently selected server."""
    from backend.remote.profiles import ComputeProfile, get_profile_store
    encoded = run.get("execution_config_json")
    if not encoded:
        return {"execution_target": "model_compute", "device": "cpu", "compute_profile_id": None,
                "project_id": project["id"], "profile": None}, None
    try:
        config = json.loads(encoded)
        if (hashlib.sha256(_canonical_json(config).encode()).hexdigest() != run.get("execution_config_sha256")
                or config.get("project_id") != project["id"]):
            raise ValueError("Stored execution identity or digest changed.")
        validated = FlowchartRunRequest.model_validate(config)
        if validated.execution_target != config.get("execution_target") or validated.device != config.get("device"):
            raise ValueError("Stored execution choice is invalid.")
        frozen_profile = None
        if validated.execution_target == "selected_compute":
            frozen_profile = ComputeProfile.model_validate(config["profile"])
            current = get_profile_store().get(validated.compute_profile_id)
            if (frozen_profile.id != validated.compute_profile_id or current is None
                    or current.model_dump() != frozen_profile.model_dump()):
                raise ValueError("Execution compute profile changed after this run was created.")
        elif config.get("profile") is not None or validated.compute_profile_id is not None:
            raise ValueError("Stored execution has an unexpected compute profile.")
        return config, frozen_profile
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=409, detail=f"Inspection execution configuration is invalid: {exc}") from exc


@contextmanager
def _execution_request(request: Request, project: Dict[str, Any], frozen_profile):
    """Scope nested flow execution to the run's own project without changing the app's active project.

    The request's project scope (the selected project, its context and the context variable) is replaced as a whole:
    nested routes resolve the project from it before the app state. A shared account executes only runs of the
    project it selected and was authorized for.
    """
    from backend.contracts.context import current_project_context
    from backend.engine.annotation_storage import (
        set_request_annotation_root, reset_request_annotation_root,
        set_request_project_root, reset_request_project_root,
    )
    from backend.engine.dataset_loaders import set_request_split_root, reset_request_split_root
    state = {**request.scope.get("state", {}), "frozen_execution_profile": frozen_profile}
    selected = state.get("scoped_project")
    if state.get("account_user") is not None and (selected is None or selected.get("id") != project["id"]):
        raise HTTPException(status_code=409, detail="Select this inspection run's project to execute it.")
    state["scoped_project"] = project
    registry = getattr(request.app.state, "context_registry", None)
    if registry is not None:
        state["project_context"] = registry.context(project, state.get("account_user"))
    app_state = dict(request.app.state._state)
    app_state["current_project"] = project
    scope = {**request.scope, "app": SimpleNamespace(state=State(app_state)), "state": state}
    context_token = current_project_context.set(state.get("project_context"))
    project_token = set_request_project_root(Path(project["project_dir"]))
    annotation_token = set_request_annotation_root(Path(project["annotations_dir"]))
    split_token = set_request_split_root(Path(project["dataset_dir"]) / "splits")
    try:
        yield Request(scope)
    finally:
        reset_request_split_root(split_token)
        reset_request_annotation_root(annotation_token)
        reset_request_project_root(project_token)
        current_project_context.reset(context_token)


class UpdateRow(BaseModel):
    image_path: str = Field(min_length=1)
    state: RowState
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ExecuteRow(BaseModel):
    image_path: str = Field(min_length=1)


class FinishRun(BaseModel):
    status: Literal["completed", "stopped"]


class ReviewRow(BaseModel):
    image_path: str = Field(min_length=1)
    final_verdict: Verdict
    reason: str = Field(min_length=2, max_length=2000)
    reviewer: str = Field(min_length=1, max_length=100)


@contextmanager
def _run_index(request: Request):
    """A run ID always resolves to its creation project, even after the active project changes."""
    path = _project_root(request) / "inspection_run_index.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    use_wal(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS run_projects (
        run_id TEXT PRIMARY KEY, project_dir TEXT NOT NULL, project_id TEXT NOT NULL
    )""")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _run_project(request: Request, run_id: str) -> Dict[str, Any]:
    with _run_index(request) as conn:
        owner = conn.execute("SELECT project_dir, project_id FROM run_projects WHERE run_id = ?", (run_id,)).fetchone()
    if owner is None:
        # Restored archives give copied runs fresh IDs. Register a local run on
        # first access so later requests remain bound after a project switch.
        project = get_current_project(request)
        database = Path(project["project_dir"]) / "inspection_history.sqlite3"
        if database.is_file() and not database.is_symlink():
            try:
                with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as local:
                    found = local.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            except sqlite3.Error:
                found = None
            if found:
                with _run_index(request) as conn:
                    conn.execute("INSERT OR IGNORE INTO run_projects VALUES (?, ?, ?)",
                                 (run_id, project["project_dir"], project["id"]))
                    owner = conn.execute(
                        "SELECT project_dir, project_id FROM run_projects WHERE run_id = ?", (run_id,),
                    ).fetchone()
        if owner is None:
            # Runs created before the index was introduced remain readable in their active project.
            return project
    # A shared account reaches only runs of the project it selected and was authorized for: knowing another project's
    # run ID never opens that project's database, rows or files.
    if getattr(request.state, "account_user", None) is not None:
        selected = getattr(request.state, "scoped_project", None)
        same = (selected is not None and selected.get("id") == owner["project_id"]
                and Path(selected.get("project_dir", "")).expanduser().resolve() == Path(owner["project_dir"]).expanduser().resolve())
        if not same:  # a restored copy keeps the ID at another folder; 404 does not confirm the run exists elsewhere
            raise HTTPException(status_code=404, detail="Inspection run not found in the active project.")
    try:
        project = _load_project(Path(owner["project_dir"]).expanduser().resolve())
    except HTTPException as exc:
        raise HTTPException(status_code=409, detail="Inspection run's original project is unavailable.") from exc
    if project["id"] != owner["project_id"]:
        raise HTTPException(status_code=409, detail="Inspection run project identity changed.")
    return project


@contextmanager
def _store(request: Request, run_id: Optional[str] = None):
    project = _run_project(request, run_id) if run_id else get_current_project(request)
    db_path = Path(project["project_dir"]) / "inspection_history.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    # Queries stay inside this project's database; legacy rows may have been
    # saved with a trailing slash, parent segment, or symlink source spelling.
    conn.create_function("source_identity", 1, _source_identity)
    conn.execute("PRAGMA foreign_keys = ON")
    use_wal(conn)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            source_folder TEXT NOT NULL,
            task TEXT NOT NULL,
            scope TEXT NOT NULL,
            pipeline_id TEXT NOT NULL,
            pipeline_name TEXT NOT NULL,
            pipeline_hash TEXT NOT NULL,
            pipeline_json TEXT NOT NULL,
            saved_version_id TEXT,
            model_sha256_json TEXT,
            model_paths_json TEXT,
            execution_config_json TEXT,
            execution_config_sha256 TEXT,
            status TEXT NOT NULL,
            owner_instance TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rows (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            image_path TEXT NOT NULL,
            image_json TEXT NOT NULL,
            state TEXT NOT NULL,
            result_json TEXT,
            image_sha256 TEXT,
            error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, image_path)
        );
        CREATE TABLE IF NOT EXISTS reviews (
            review_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            image_path TEXT NOT NULL,
            final_verdict TEXT NOT NULL,
            reason TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id, image_path) REFERENCES rows(run_id, image_path)
        );
        CREATE INDEX IF NOT EXISTS runs_source_idx ON runs(source_folder, task, created_at);
        CREATE INDEX IF NOT EXISTS reviews_row_idx ON reviews(run_id, image_path, created_at);
    """)
    # A history file written by an older build gains its columns once, even when two requests open it together.
    add_missing_columns(conn, "runs", {"owner_instance": "TEXT", "saved_version_id": "TEXT", "model_sha256_json": "TEXT",
                                       "model_paths_json": "TEXT", "execution_config_json": "TEXT",
                                       "execution_config_sha256": "TEXT"})
    add_missing_columns(conn, "rows", {"image_sha256": "TEXT"})
    stale_ids = [row["run_id"] for row in conn.execute(
        "SELECT run_id FROM runs WHERE status = 'running' AND (owner_instance IS NULL OR owner_instance != ?)",
        (PROCESS_INSTANCE,),
    )]
    if stale_ids:
        stamp = _now()
        conn.executemany(
            "UPDATE rows SET state = 'skipped', updated_at = ? WHERE run_id = ? AND state IN ('pending', 'running')",
            [(stamp, run_id) for run_id in stale_ids],
        )
        conn.executemany(
            "UPDATE runs SET status = 'stopped', updated_at = ? WHERE run_id = ?",
            [(stamp, run_id) for run_id in stale_ids],
        )
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _run_row(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    record = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if record is None:
        raise HTTPException(status_code=404, detail="Inspection run not found in the active project.")
    return record


def _read_run(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    run = _run_row(conn, run_id)
    rows = conn.execute("SELECT * FROM rows WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
    reviews = conn.execute(
        "SELECT * FROM reviews WHERE run_id = ? ORDER BY created_at, rowid", (run_id,)
    ).fetchall()
    by_image: Dict[str, List[Dict[str, Any]]] = {}
    for review in reviews:
        item = {key: review[key] for key in review.keys() if key not in {"run_id", "image_path"}}
        by_image.setdefault(review["image_path"], []).append(item)
    output = {key: run[key] for key in run.keys() if key not in {"pipeline_json", "model_sha256_json", "model_paths_json", "execution_config_json"}}
    output["canonical_source_folder"] = _source_identity(run["source_folder"])
    output["pipeline"] = json.loads(run["pipeline_json"])
    output["model_sha256"] = json.loads(run["model_sha256_json"] or "{}")
    output["execution_config"] = json.loads(run["execution_config_json"]) if run["execution_config_json"] else None
    output["rows"] = []
    for row in rows:
        history = by_image.get(row["image_path"], [])
        output["rows"].append({
            "image": json.loads(row["image_json"]),
            "state": row["state"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "image_sha256": row["image_sha256"],
            "error": row["error"],
            "review": history[-1] if history else None,
            "reviews": history,
        })
    return output


def _verified_execution_context(run: sqlite3.Row, project: Dict[str, Any]) -> tuple[FlowchartPipeline, Dict[tuple[str, str], Path]]:
    """Reopen this run's immutable graph and exact checkpoint files, independent of the current active flow."""
    source = Path(run["source_folder"]).expanduser().resolve()
    if (project.get("source_dataset_dir") != str(source) or project.get("task") != run["task"]
            or not source.is_dir() or not run["saved_version_id"]):
        raise HTTPException(status_code=409, detail="Inspection run no longer matches this project's source and task.")
    version_id = run["saved_version_id"]
    version_path = Path(project["project_dir"]) / "flowcharts" / "versions" / f"{version_id}.json"
    try:
        version = json.loads(version_path.read_text(encoding="utf-8"))
        saved_graph = version["pipeline"]
        run_graph = json.loads(run["pipeline_json"])
        pipeline = FlowchartPipeline.model_validate(saved_graph)
        submitted = FlowchartPipeline.model_validate(run_graph)
        if (version.get("version_id") != version_id
                or version.get("source_dataset_path") != str(source)
                or pipeline != submitted
                or hashlib.sha256(_canonical_json(run_graph).encode("utf-8")).hexdigest() != run["pipeline_hash"]):
            raise ValueError("Saved graph or inspection graph hash changed.")
        ordered_linear_nodes(pipeline)
        model_hashes = json.loads(run["model_sha256_json"] or "{}")
        model_paths = json.loads(run["model_paths_json"] or "{}")
        references = _model_references(pipeline)
        if (not isinstance(model_hashes, dict) or not isinstance(model_paths, dict)
                or set(references) != set(model_hashes) or set(references) != set(model_paths)):
            raise ValueError("Stored model provenance is incomplete.")
        checkpoints: Dict[tuple[str, str], Path] = {}
        for job_id, task in references.items():
            recorded = model_paths[job_id]
            if not isinstance(recorded, str) or not isinstance(model_hashes[job_id], str):
                raise ValueError("Stored checkpoint identity is invalid.")
            checkpoint = Path(recorded)
            local_job = Path(project["models_dir"]).resolve() / job_id
            if (checkpoint.name != "best_model.pt" or checkpoint.is_symlink() or not checkpoint.is_file()
                    or str(checkpoint.resolve()) != recorded
                    or not _owned_checkpoint(project, job_id, checkpoint)
                    or (local_job.is_dir() and checkpoint.parent.resolve() != local_job)
                    or _sha256(checkpoint) != model_hashes[job_id]):
                raise ValueError(f"Checkpoint {job_id} differs from the recorded inspection model.")
            checkpoints[(job_id, task)] = checkpoint
        return pipeline, checkpoints
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=409, detail=f"Stored inspection provenance is invalid: {exc}") from exc


def _check_checkpoint_hashes(run: sqlite3.Row, checkpoints: Dict[tuple[str, str], Path]) -> None:
    expected = json.loads(run["model_sha256_json"] or "{}")
    try:
        if any(_sha256(path) != expected[job_id] for (job_id, _task), path in checkpoints.items()):
            raise ValueError("A checkpoint changed during inspection execution.")
    except (OSError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"Inspection checkpoint changed: {exc}") from exc


@router.post("/runs")
def create_run(payload: CreateRun, request: Request):
    paths = [image.file_path for image in payload.images]
    if len(paths) != len(set(paths)):
        raise HTTPException(status_code=422, detail="Inspection image paths must be unique within a run.")
    if not payload.pipeline.get("id") or not payload.pipeline.get("name"):
        raise HTTPException(status_code=422, detail="A named flowchart is required for inspection provenance.")
    project = get_current_project(request)
    execution_config = _freeze_execution_config(payload, project)
    execution_json = _canonical_json(execution_config)
    source_folder = _source_identity(payload.source_folder)
    if source_folder is None:
        raise HTTPException(status_code=422, detail="Inspection source path cannot be resolved.")
    try:
        pipeline_json = canonical_pipeline_json(payload.pipeline)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Inspection graph is invalid: {exc}") from exc
    saved_version_id, model_sha256, model_paths = _verified_run_provenance(payload, project)
    run_id, stamp = str(uuid.uuid4()), _now()
    with _run_index(request) as index:
        index.execute("INSERT INTO run_projects VALUES (?, ?, ?)",
                      (run_id, project["project_dir"], project["id"]))
    with _store(request, run_id) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, source_folder, task, scope, pipeline_id, pipeline_name, "
            "pipeline_hash, pipeline_json, saved_version_id, model_sha256_json, model_paths_json, "
            "execution_config_json, execution_config_sha256, "
            "status, owner_instance, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, source_folder, payload.task, payload.scope,
             str(payload.pipeline["id"]), str(payload.pipeline["name"]),
             hashlib.sha256(pipeline_json.encode("utf-8")).hexdigest(), pipeline_json,
             saved_version_id, _canonical_json(model_sha256), _canonical_json(model_paths),
             execution_json, hashlib.sha256(execution_json.encode()).hexdigest(),
             "running", PROCESS_INSTANCE, stamp, stamp),
        )
        conn.executemany(
            "INSERT INTO rows (run_id, image_path, image_json, state, result_json, image_sha256, error, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, image.file_path, image.model_dump_json(), "pending", None, None, None, stamp)
             for image in payload.images],
        )
    return {
        "run_id": run_id, "status": "running",
        "saved_version_id": saved_version_id,
        "pipeline_hash": hashlib.sha256(pipeline_json.encode("utf-8")).hexdigest(),
        "model_sha256": model_sha256,
        "execution_config": execution_config,
    }


@router.get("/review-queue")
def review_queue(request: Request, source_folder: str, task: Literal["classification", "detection", "segmentation", "anomaly"],
                 limit: int = Query(100, ge=1, le=500)):
    """Surface unresolved model REVIEW results and diagnostic errors across this project's runs."""
    project = get_current_project(request)
    configured = project.get("source_dataset_dir")
    source_folder = _source_identity(source_folder)
    if (not configured or source_folder is None or _source_identity(configured) != source_folder
            or project.get("task") != task):
        raise HTTPException(status_code=409, detail="Review queue source or task differs from the active project.")
    latest_review = """(SELECT reviews.final_verdict FROM reviews
        WHERE reviews.run_id = rows.run_id AND reviews.image_path = rows.image_path
        ORDER BY reviews.created_at DESC, reviews.rowid DESC LIMIT 1)"""
    where = f"""source_identity(runs.source_folder) = ? AND runs.task = ? AND
        (rows.state = 'error' OR (rows.state IN ('OK', 'NG', 'REVIEW') AND
        ({latest_review} = 'REVIEW' OR (rows.state = 'REVIEW' AND {latest_review} IS NULL))))"""
    with _store(request) as conn:
        totals = conn.execute(
            f"""SELECT COUNT(*) AS total,
                SUM(CASE WHEN rows.state = 'error' THEN 1 ELSE 0 END) AS diagnostic_errors
                FROM rows JOIN runs ON runs.run_id = rows.run_id WHERE {where}""",
            (source_folder, task),
        ).fetchone()
        found = conn.execute(
            f"""SELECT rows.run_id, rows.image_json, rows.state, rows.error, rows.result_json,
                runs.created_at, runs.pipeline_name, runs.saved_version_id,
                {latest_review} AS operator_verdict
                FROM rows JOIN runs ON runs.run_id = rows.run_id
                WHERE {where}
                ORDER BY runs.created_at DESC, runs.rowid DESC, rows.rowid ASC LIMIT ?""",
            (source_folder, task, limit),
        ).fetchall()
    items = []
    for row in found:
        state = row["state"]
        items.append({
            "run_id": row["run_id"], "image": json.loads(row["image_json"]),
            "model_verdict": state if state in ("OK", "NG", "REVIEW") else None,
            "operator_verdict": row["operator_verdict"],
            "status": "error" if state == "error" else "still_review" if row["operator_verdict"] else "unreviewed",
            "can_review": state in ("OK", "NG", "REVIEW"), "error": row["error"],
            "created_at": row["created_at"], "pipeline_name": row["pipeline_name"],
            "saved_version_id": row["saved_version_id"],
        })
    error_count = totals["diagnostic_errors"] or 0
    return {"items": items, "total": totals["total"],
            "review_required": totals["total"] - error_count,
            "diagnostic_errors": error_count}


@router.get("/runs")
def list_runs(request: Request, source_folder: Optional[str] = None, task: Optional[str] = None):
    conditions, params = [], []
    if source_folder:
        conditions.append("source_identity(source_folder) = ?")
        params.append(_source_identity(source_folder))
    if task:
        conditions.append("task = ?")
        params.append(task)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with _store(request) as conn:
        records = conn.execute(
            f"SELECT run_id, source_folder, task, scope, pipeline_id, pipeline_name, "
            f"pipeline_hash, saved_version_id, model_sha256_json, execution_config_json, status, created_at, updated_at FROM runs {where} "
            "ORDER BY created_at DESC, rowid DESC LIMIT 100", params,
        ).fetchall()
        runs = []
        for record in records:
            item = dict(record)
            item["model_sha256"] = json.loads(item.pop("model_sha256_json") or "{}")
            item["execution_config"] = json.loads(item.pop("execution_config_json") or "null")
            counts = conn.execute(
                "SELECT state, COUNT(*) AS count FROM rows WHERE run_id = ? GROUP BY state",
                (record["run_id"],),
            ).fetchall()
            item["counts"] = {row["state"]: row["count"] for row in counts}
            item["total"] = sum(item["counts"].values())
            runs.append(item)
    return {"runs": runs}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    with _store(request, run_id) as conn:
        return _read_run(conn, run_id)


@router.get("/runs/{run_id}/evidence-image")
def evidence_image(run_id: str, request: Request, image_path: str = Query(...)):
    """Display source bytes only when they match this run's captured identity."""
    with _store(request, run_id) as conn:
        run = _run_row(conn, run_id)
        row = conn.execute("SELECT image_sha256 FROM rows WHERE run_id=? AND image_path=?",
                           (run_id, image_path)).fetchone()
        if row is None:
            raise HTTPException(404, "Image does not belong to this inspection run.")
        expected = row["image_sha256"]
        source = Path(run["source_folder"]).expanduser().resolve()
        version = run["saved_version_id"]
    if not expected:
        raise HTTPException(409, "This run has no captured source image hash; historical original is unavailable.")
    from backend.engine.evidence_image import verified_preview
    try:
        preview = verified_preview(image_path, source, expected)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run_id, "saved_version_id": version, **preview}



@router.post("/runs/{run_id}/execute")
def execute_row(run_id: str, payload: ExecuteRow, request: Request):
    """Run the saved graph in this server and persist its result without trusting renderer JSON."""
    project = _run_project(request, run_id)
    with _store(request, run_id) as conn:
        run = _run_row(conn, run_id)
        row = conn.execute(
            "SELECT image_json, state, result_json FROM rows WHERE run_id = ? AND image_path = ?",
            (run_id, payload.image_path),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Image does not belong to the inspection run.")
        if row["state"] in {"OK", "NG", "REVIEW"} and row["result_json"]:
            return json.loads(row["result_json"])
        if run["status"] != "running" or row["state"] != "pending":
            raise HTTPException(status_code=409, detail="Image is already running or finished in this inspection run.")
        image = json.loads(row["image_json"])
        run_record = dict(run)

    pipeline, checkpoints = _verified_execution_context(run_record, project)
    execution_config, frozen_profile = _verified_execution_choice(run_record, project)
    image_path = Path(payload.image_path).expanduser().resolve()
    source_path = Path(run_record["source_folder"]).expanduser().resolve()
    if (not image_path.is_file() or not image_path.is_relative_to(source_path)
            or str(image_path) != payload.image_path or image.get("file_path") != payload.image_path):
        raise HTTPException(status_code=409, detail="Inspection image path changed or is unavailable.")
    image_sha256 = _sha256(image_path)
    with _store(request, run_id) as conn:
        run = _run_row(conn, run_id)
        changed = conn.execute(
            "UPDATE rows SET state = 'running', updated_at = ? WHERE run_id = ? AND image_path = ? "
            "AND state = 'pending' AND EXISTS (SELECT 1 FROM runs WHERE run_id = ? AND status = 'running')",
            (_now(), run_id, payload.image_path, run_id),
        )
        if run["status"] != "running" or changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="Inspection run or image changed before execution.")

    from backend.api import routes_flowchart
    try:
        with _execution_request(request, project, frozen_profile) as bound_request, verified_checkpoint_scope(checkpoints):
            raw = routes_flowchart.run_flowchart(FlowchartRunRequest(
                image_path=payload.image_path, image_id=image["image_id"], pipeline=pipeline,
                execution_target=execution_config["execution_target"], device=execution_config["device"],
                compute_profile_id=execution_config["compute_profile_id"], project_id=project["id"],
            ), request=bound_request)
        result = FlowchartExecutionResult.model_validate(raw)
        if (result.image_path != payload.image_path or result.image_id != image["image_id"]
                or result.status not in ("success", "review")
                or not result.execution_steps):
            raise HTTPException(status_code=502, detail="Flowchart engine returned a result for a different image or without execution evidence.")
        if execution_config["execution_target"] != "model_compute" and (
                raw.get("execution_target") != execution_config["execution_target"]
                or raw.get("execution_device") != execution_config["device"]
                or raw.get("compute_profile_id") != execution_config["compute_profile_id"]):
            raise HTTPException(status_code=502, detail="Flowchart result used a different execution target, device, or compute profile.")
        if _sha256(image_path) != image_sha256:
            raise HTTPException(status_code=409, detail="Inspection image changed during execution.")
        _check_checkpoint_hashes(run_record, checkpoints)
    except Exception:
        # The browser may record a diagnostic error or retry; it cannot supply a model verdict.
        with _store(request, run_id) as conn:
            conn.execute(
                "UPDATE rows SET state = 'pending', updated_at = ? WHERE run_id = ? AND image_path = ? AND state = 'running'",
                (_now(), run_id, payload.image_path),
            )
        raise

    saved = result.model_dump(mode="json")
    for key in ("execution_target", "execution_device", "compute_profile_id", "compute_gpu_selector", "device_name", "model_sha256"):
        if key in raw:
            saved[key] = raw[key]
    stamp = _now()
    with _store(request, run_id) as conn:
        run = _run_row(conn, run_id)
        if run["status"] != "running":
            raise HTTPException(status_code=409, detail="Inspection run was stopped before the model result could be saved.")
        updated = conn.execute(
            "UPDATE rows SET state = ?, result_json = ?, image_sha256 = ?, error = NULL, updated_at = ? "
            "WHERE run_id = ? AND image_path = ? AND state = 'running'",
            (result.final_verdict, json.dumps(saved, ensure_ascii=False), image_sha256,
             stamp, run_id, payload.image_path),
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=409, detail="Inspection image changed before the model result could be saved.")
        conn.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", (stamp, run_id))
    return saved


@router.put("/runs/{run_id}/rows")
def update_row(run_id: str, payload: UpdateRow, request: Request):
    with _store(request, run_id) as conn:
        run = _run_row(conn, run_id)
        if run["status"] != "running":
            raise HTTPException(status_code=409, detail="Completed inspection runs cannot be changed.")
        row = conn.execute(
            "SELECT state, image_json FROM rows WHERE run_id = ? AND image_path = ?",
            (run_id, payload.image_path),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Image does not belong to the inspection run.")
        if row["state"] != "pending":
            raise HTTPException(status_code=409, detail="Only a pending image may receive a client diagnostic error.")
        if payload.state != "error" or payload.result is not None:
            raise HTTPException(status_code=409, detail="Only a diagnostic error may be submitted by the client; the server records model verdicts.")
        if not payload.error:
            raise HTTPException(status_code=422, detail="An inspection error needs its diagnostic message.")
        stamp = _now()
        conn.execute(
            "UPDATE rows SET state = ?, result_json = ?, error = ?, updated_at = ? WHERE run_id = ? AND image_path = ?",
            (payload.state, json.dumps(payload.result, ensure_ascii=False) if payload.result else None,
             payload.error, stamp, run_id, payload.image_path),
        )
        conn.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", (stamp, run_id))
    return {"status": "saved", "run_id": run_id, "image_path": payload.image_path}


@router.put("/runs/{run_id}/finish")
def finish_run(run_id: str, payload: FinishRun, request: Request):
    with _store(request, run_id) as conn:
        run = _run_row(conn, run_id)
        if run["status"] != "running":
            raise HTTPException(status_code=409, detail="Inspection run is already finished.")
        unfinished = conn.execute(
            "SELECT COUNT(*) FROM rows WHERE run_id = ? AND state IN ('pending', 'running')", (run_id,)
        ).fetchone()[0]
        if payload.status == "completed" and unfinished:
            raise HTTPException(status_code=409, detail=f"{unfinished} images have no completed inspection record.")
        stamp = _now()
        if payload.status == "stopped":
            conn.execute(
                "UPDATE rows SET state = 'skipped', updated_at = ? WHERE run_id = ? AND state IN ('pending', 'running')",
                (stamp, run_id),
            )
        conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (payload.status, stamp, run_id),
        )
    return {"status": payload.status, "run_id": run_id}


@router.post("/runs/{run_id}/reviews")
def review_row(run_id: str, payload: ReviewRow, request: Request):
    with _store(request, run_id) as conn:
        _run_row(conn, run_id)
        row = conn.execute(
            "SELECT state FROM rows WHERE run_id = ? AND image_path = ?",
            (run_id, payload.image_path),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Image does not belong to the inspection run.")
        if row["state"] not in {"OK", "NG", "REVIEW"}:
            raise HTTPException(status_code=409, detail="Only inspected images can receive an operator verdict.")
        review_id, stamp = str(uuid.uuid4()), _now()
        conn.execute(
            "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
            (review_id, run_id, payload.image_path, payload.final_verdict,
             payload.reason.strip(), payload.reviewer.strip(), stamp),
        )
    return {"review_id": review_id, "final_verdict": payload.final_verdict, "created_at": stamp}


@router.get("/runs/{run_id}/export")
def export_run(run_id: str, request: Request, format: Literal["csv", "json"] = Query("csv")):
    with _store(request, run_id) as conn:
        run = _read_run(conn, run_id)
    if format == "json":
        return {"format": "json", "filename": f"inspection-{run_id}.json",
                "content": json.dumps(run, ensure_ascii=False, indent=2)}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["run_id", "saved_version_id", "pipeline_hash", "model_sha256", "image_sha256", "image_path",
                     "image_id", "split", "model_state", "model_verdict", "operator_verdict",
                     "reviewer", "review_reason", "error"])
    for row in run["rows"]:
        latest = row["review"] or {}
        original = row["result"] or {}
        writer.writerow([_csv_cell(value) for value in [
            run_id, run["saved_version_id"], run["pipeline_hash"], _canonical_json(run["model_sha256"]),
            row["image_sha256"],
            row["image"]["file_path"], row["image"]["image_id"], row["image"].get("split"),
            row["state"], original.get("final_verdict"), latest.get("final_verdict"),
            latest.get("reviewer"), latest.get("reason"), row["error"],
        ]])
    return {"format": "csv", "filename": f"inspection-{run_id}.csv", "content": output.getvalue()}
