"""
backend/api/routes_flowchart.py

Multi-Model Chaining & Flowchart Pipeline Execution (inspired by Neurocle Neuro-T Flowchart):
Enables visual assembly of multi-stage vision AI inspection:
  [Input Image] -> [Stage 1: Detection / ROI Crop] -> [Stage 2: Defect Inspection / Anomaly] -> [Stage 3: Rule Decision] -> [Output]
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, List, Literal, Optional
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import torch

from backend.api.routes_evaluation import _matches_source_dataset, _resolve_job_artifacts
from backend.api.routes_training import training_job_manager
from backend.api.routes_project import get_current_project
from backend.engine.flowchart_engine import (
    CropInspectionResult,
    FlowEdge,
    FlowNode,
    FlowNodeData,
    FlowchartEngine,
    FlowchartExecutionResult,
    FlowchartExecutionStep,
    FlowchartPipeline,
    FlowchartRunRequest,
    get_conditional_inspection_flowchart,
    get_default_flowchart,
    get_five_model_chain_flowchart,
    get_fixed_roi_flowchart,
    get_single_detection_flowchart,
    get_single_segmentation_flowchart,
    ordered_linear_nodes,
    safe_crop_roi,
    verified_checkpoint_scope,
)
from backend.utils.error_catalog import format_error_response
from backend.engine.checkpoint_paths import is_job_id, trusted_checkpoint

logger = logging.getLogger("vision_ai_studio.routes_flowchart")

router = APIRouter(prefix="/api/flowchart", tags=["flowchart"])

FLOWCHARTS_DIR = Path("./projects/flowcharts")
FLOWCHARTS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_PIPELINE_FILE = FLOWCHARTS_DIR / "pipeline.json"

# Persistent cached engine instance for rapid warm execution
_ENGINE = FlowchartEngine()
_FLOW_SAVE_LOCK = threading.RLock()


InspectionTask = Literal["anomaly", "segmentation", "classification", "patch_classification"]
PipelineTask = Literal["detection", "anomaly", "segmentation", "classification", "patch_classification", "mixed"]


def _recipe_file(
    task: PipelineTask, source_dataset_path: Optional[str] = None,
    project_dir: Optional[Path] = None,
) -> Path:
    base = (project_dir / "flowcharts" / "pipeline.json") if project_dir else DEFAULT_PIPELINE_FILE
    if not source_dataset_path:
        return base.with_name(f"pipeline_{task}.json")
    source = Path(source_dataset_path).expanduser().resolve()
    if not source.is_dir():
        raise HTTPException(status_code=422, detail="Select an existing dataset folder before saving or loading its flowchart.")
    source_key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
    return base.with_name(f"pipeline_{task}_{source_key}.json")


def _project_dir(request: Optional[Request]) -> Optional[Path]:
    if request is None:
        return None
    project = get_current_project(request)
    return Path(project["project_dir"]).resolve()


def _legacy_owner(project_dir: Path) -> bool:
    """Give old global flow files to one active project on first migration."""
    claim = DEFAULT_PIPELINE_FILE.parent / ".legacy_flowchart_owner.json"
    if not claim.is_file():
        if not any(DEFAULT_PIPELINE_FILE.parent.glob("pipeline*.json")):
            return False
        claim.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"project_dir": str(project_dir)}, handle)
    try:
        owner = json.loads(claim.read_text(encoding="utf-8"))
        return owner.get("project_dir") == str(project_dir)
    except (OSError, ValueError, AttributeError):
        return False


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    temporary_path: Optional[Path] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _write_pipeline(path: Path, pipeline: FlowchartPipeline) -> None:
    _write_json(path, pipeline.model_dump())


def _restore_flow_file(path: Path, previous: Optional[bytes]) -> None:
    """Restore an atomic JSON file after a multi-file save fails."""
    if previous is None:
        path.unlink(missing_ok=True)
        return
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".rollback", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _version_dir(project_dir: Optional[Path]) -> Path:
    base = (project_dir / "flowcharts") if project_dir else DEFAULT_PIPELINE_FILE.parent
    return base / "versions"


def _active_flow_file(project_dir: Path) -> Path:
    return project_dir / "flowcharts" / "active.json"


def _save_version(
    pipeline: FlowchartPipeline, task: PipelineTask,
    source_dataset_path: Optional[str], project_dir: Optional[Path],
) -> str:
    version_id = uuid.uuid4().hex
    source = str(Path(source_dataset_path).expanduser().resolve()) if source_dataset_path else None
    _write_json(_version_dir(project_dir) / f"{version_id}.json", {
        "version_id": version_id,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "saved_at_ns": time.time_ns(),
        "recipe_task": task,
        "source_dataset_path": source,
        "pipeline": pipeline.model_dump(),
    })
    return version_id


def _pipeline_matches_recipe(pipeline: FlowchartPipeline, task: PipelineTask) -> bool:
    detectors = [node for node in pipeline.nodes if node.data.node_type == "detection_crop"]
    inspections = [node for node in pipeline.nodes if node.data.node_type == "inspection"]
    inspection_tasks = {node.data.task for node in inspections}
    if task == "mixed":
        return len(inspection_tasks) > 1 or bool(detectors and inspections)
    if task == "detection":
        return bool(detectors)
    return bool(inspections) and all(node.data.task == task for node in inspections)


def _inferred_recipe(pipeline: FlowchartPipeline) -> PipelineTask:
    inspections = [node for node in pipeline.nodes if node.data.node_type == "inspection"]
    if len({node.data.task for node in inspections}) > 1:
        return "mixed"
    if inspections:
        task = inspections[0].data.task
        if task in ("anomaly", "segmentation", "classification", "patch_classification"):
            return task
    if any(node.data.node_type == "detection_crop" for node in pipeline.nodes):
        return "detection"
    raise ValueError("Pipeline has no supported model task.")


def _single_inspection_template(inspection_task: InspectionTask, job_id: Optional[str] = None) -> FlowchartPipeline:
    pipeline = get_single_segmentation_flowchart(job_id=job_id)
    if inspection_task == "segmentation":
        return pipeline
    labels = {
        "classification": ("원본 이미지 분류 검사", "전체 이미지 분류"),
        "anomaly": ("원본 이미지 이상 탐지", "전체 이미지 이상 탐지"),
        "patch_classification": ("이미지 패치 분류 검사", "전체 이미지 패치 분류"),
    }
    pipeline.id = f"single_{inspection_task}"
    pipeline.name, inspection_label = labels[inspection_task]
    pipeline.description = "전체 이미지 검사 -> OK/NG/REVIEW 판정 -> 로컬 미리보기"
    inspection = next(node for node in pipeline.nodes if node.data.node_type == "inspection")
    inspection.data.task = inspection_task
    inspection.data.label = inspection_label
    inspection.data.params = {}
    return pipeline


@router.get("/pipeline")
def get_pipeline(
    inspection_task: PipelineTask = "segmentation", source_dataset_path: Optional[str] = None,
    request: Request = None,
) -> FlowchartPipeline:
    """Load this project's saved flow, importing a legacy global file once."""
    with _FLOW_SAVE_LOCK:
        return _get_pipeline_unlocked(inspection_task, source_dataset_path, request)


def _get_pipeline_unlocked(
    inspection_task: PipelineTask, source_dataset_path: Optional[str], request: Request,
) -> FlowchartPipeline:
    project_dir = _project_dir(request)
    scoped_files = [_recipe_file(inspection_task, source_dataset_path, project_dir)]
    legacy_files = [_recipe_file(inspection_task, source_dataset_path)]
    if source_dataset_path:
        scoped_files.append(_recipe_file(inspection_task, project_dir=project_dir))
        legacy_files.append(_recipe_file(inspection_task))
    scoped_files.append((project_dir / "flowcharts" / "pipeline.json") if project_dir else DEFAULT_PIPELINE_FILE)
    legacy_files.append(DEFAULT_PIPELINE_FILE)
    candidates = list(zip(scoped_files, scoped_files))
    if project_dir and _legacy_owner(project_dir):
        candidates.extend(zip(legacy_files, scoped_files))
    for path, migration_target in candidates:
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pipeline = FlowchartPipeline.model_validate(data)
            ordered_linear_nodes(pipeline)
            if path.name == "pipeline.json" and inspection_task == "detection" and any(
                node.data.node_type == "inspection" for node in pipeline.nodes
            ):
                # An old shared ROI graph is not the new detector-only default.
                continue
            if _pipeline_matches_recipe(pipeline, inspection_task):
                if path != migration_target:
                    _write_pipeline(migration_target, pipeline)
                    project = get_current_project(request) if project_dir is not None else None
                    source = (
                        str(Path(source_dataset_path).expanduser().resolve())
                        if source_dataset_path else project.get("source_dataset_dir") if project else None
                    )
                    version_id = _save_version(pipeline, inspection_task, source, project_dir)
                    if project_dir is not None:
                        if source == project.get("source_dataset_dir"):
                            _write_json(_active_flow_file(project_dir), {
                                "version_id": version_id,
                                "project_id": project["id"],
                                "recipe_task": inspection_task,
                                "source_dataset_path": source,
                            })
                return pipeline
            if path.name != "pipeline.json":
                raise ValueError("Saved flowchart task does not match its recipe file.")
        except Exception as e:
            logger.exception("Could not read saved pipeline: %s", e)
            raise HTTPException(status_code=409, detail=f"Saved flowchart is invalid: {e}") from e
    if inspection_task == "detection":
        return get_single_detection_flowchart()
    if inspection_task == "mixed":
        return get_default_flowchart()
    return _single_inspection_template(inspection_task)


@router.get("/pipeline/active", response_model=FlowchartPipeline)
def get_active_pipeline(source_dataset_path: Optional[str] = None, request: Request = None) -> FlowchartPipeline:
    """Reopen the active saved graph only for its owning project and source."""
    with _FLOW_SAVE_LOCK:
        return _get_active_pipeline_unlocked(source_dataset_path, request)


def _get_active_pipeline_unlocked(
    source_dataset_path: Optional[str], request: Request,
) -> FlowchartPipeline:
    if request is None:
        raise HTTPException(status_code=404, detail="No active project flowchart.")
    project = get_current_project(request)
    project_dir = Path(project["project_dir"])
    active_file = _active_flow_file(project_dir)
    if not active_file.is_file():
        raise HTTPException(status_code=404, detail="No active project flowchart.")
    try:
        active = json.loads(active_file.read_text(encoding="utf-8"))
        selected_source = project.get("source_dataset_dir")
        requested_source = (
            str(Path(source_dataset_path).expanduser().resolve())
            if source_dataset_path is not None else selected_source
        )
        if (active.get("project_id") != project["id"]
                or active.get("source_dataset_path") != selected_source
                or requested_source != selected_source):
            raise HTTPException(status_code=404, detail="No active flowchart for this project and dataset.")
        return get_saved_pipeline_version(active["version_id"], request=request)
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail=f"Active flowchart pointer is invalid: {exc}") from exc


@router.get("/templates/single-detection")
def get_single_detection_template(job_id: Optional[str] = None) -> FlowchartPipeline:
    """Create a detector-only defect inspection flow."""
    return get_single_detection_flowchart(job_id=job_id)


@router.get("/templates/single-segmentation")
def get_single_segmentation_template(
    job_id: Optional[str] = None,
    inspection_task: InspectionTask = "segmentation",
) -> FlowchartPipeline:
    """Create a full-image inspection flow without changing the saved graph."""
    return _single_inspection_template(inspection_task, job_id=job_id)


@router.get("/templates/detector-roi")
def get_detector_roi_template(
    inspection_task: InspectionTask = "segmentation",
) -> FlowchartPipeline:
    """Create the other linear graph supported by the execution engine."""
    pipeline = get_default_flowchart()
    pipeline.id = "detector_roi"
    pipeline.name = "검출 ROI 후 결함 검사"
    for node in pipeline.nodes:
        if node.data.node_type == "inspection":
            node.data.task = inspection_task
            node.data.label = "ROI 결함 검사"
            node.data.model_job_id = None
        elif node.data.node_type == "detection_crop":
            node.data.model_job_id = None
    return pipeline


@router.get("/templates/fixed-roi")
def get_fixed_roi_template(
    inspection_task: InspectionTask = "segmentation", job_id: Optional[str] = None,
) -> FlowchartPipeline:
    """Start an editable source-pixel ROI → inspection graph."""
    return get_fixed_roi_flowchart(inspection_task=inspection_task, job_id=job_id)


@router.get("/templates/five-model-chain")
def get_five_model_chain_template() -> FlowchartPipeline:
    return get_five_model_chain_flowchart()


@router.get("/templates/conditional-inspection")
def get_conditional_inspection_template() -> FlowchartPipeline:
    return get_conditional_inspection_flowchart()


class FlowchartModelReference(BaseModel):
    job_id: str
    task: Literal["detection", "anomaly", "segmentation", "classification", "patch_classification"]


class FlowchartModelVerificationRequest(BaseModel):
    source_dataset_path: str
    models: List[FlowchartModelReference] = Field(min_length=1, max_length=8)


@router.post("/models/verify")
def verify_flowchart_models(request: FlowchartModelVerificationRequest):
    """Verify every saved model against the selected source and its node task."""
    verified: List[str] = []
    for model in request.models:
        if not is_job_id(model.job_id):
            raise HTTPException(status_code=409, detail=f"Invalid model job ID: {model.job_id}")
        try:
            _resolve_job_artifacts(
                model.job_id,
                source_dataset_path=request.source_dataset_path,
                source_task=model.task,
            )
        except HTTPException as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Model {model.job_id} is not a completed {model.task} model for the selected dataset.",
            ) from exc
        verified.append(model.job_id)
    return {"verified_job_ids": verified}


@router.get("/models/catalog")
def catalog_flowchart_models(source_dataset_path: str, request: Request = None):
    """List completed checkpoints whose dataset fingerprint still matches."""
    source = Path(source_dataset_path).expanduser().resolve()
    if not source.is_dir():
        raise HTTPException(status_code=422, detail="Select an existing dataset folder.")
    roots = []
    project = get_current_project(request) if request is not None else None
    if project:
        roots.append(Path(project["models_dir"]))
    roots.append(Path.cwd() / "models")
    candidate_dirs = [path for root in roots if root.is_dir() for path in root.glob("job_*") if path.is_dir()]
    candidate_dirs.extend(path for path in (Path.cwd() / "projects").glob("job_*/models") if path.is_dir())
    models: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for directory in candidate_dirs:
        job_id = directory.parent.name if directory.name == "models" else directory.name
        if job_id in seen or not is_job_id(job_id):
            continue
        try:
            output_dir, checkpoint, meta, task, _, _ = _resolve_job_artifacts(
                job_id, source_dataset_path=str(source),
            )
        except (HTTPException, OSError, ValueError):
            continue
        if output_dir.resolve() != directory.resolve() or task not in (
            "detection", "anomaly", "segmentation", "classification", "patch_classification",
        ):
            continue
        seen.add(job_id)
        model_name = str(meta.get("model_name") or meta.get("backbone") or task)
        record = training_job_manager.get_job(job_id)
        best_metric = meta.get("best_metric")
        if not isinstance(best_metric, (int, float)):
            best_metric = record.best_metric if record else None
        models.append({
            "job_id": job_id,
            "task": task,
            "label": f"{model_name} · {job_id}",
            "preset": meta.get("preset") or (record.preset if record else None),
            "best_metric": best_metric,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(checkpoint.stat().st_mtime)),
            "source_dataset_path": str(source),
        })
    models.sort(key=lambda item: item["created_at"], reverse=True)
    return {"models": models, "total": len(models)}


@router.post("/pipeline")
def save_pipeline(
    pipeline: FlowchartPipeline, recipe_task: Optional[PipelineTask] = None,
    source_dataset_path: Optional[str] = None,
    request: Request = None,
):
    """Save a flow inside the active project without corrupting the prior file."""
    try:
        ordered_linear_nodes(pipeline)
        task = recipe_task or _inferred_recipe(pipeline)
        if not _pipeline_matches_recipe(pipeline, task):
            raise ValueError(f"Pipeline model tasks do not match the {task} recipe.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    project = get_current_project(request) if request is not None else None
    project_dir = Path(project["project_dir"]).resolve() if project else None
    project_source = project.get("source_dataset_dir") if project else None
    if source_dataset_path and project_source and str(Path(source_dataset_path).expanduser().resolve()) != project_source:
        raise HTTPException(status_code=409, detail="Flowchart source differs from the active project dataset.")
    effective_source = source_dataset_path or project_source
    target_path = _recipe_file(task, effective_source, project_dir)
    with _FLOW_SAVE_LOCK:
        version_id: Optional[str] = None
        active_path = _active_flow_file(project_dir) if project_dir is not None else None
        previous_recipe: Optional[bytes] = None
        previous_active: Optional[bytes] = None
        try:
            previous_recipe = target_path.read_bytes() if target_path.exists() else None
            previous_active = active_path.read_bytes() if active_path and active_path.exists() else None
            version_id = _save_version(pipeline, task, effective_source, project_dir)
            _write_pipeline(target_path, pipeline)
            if active_path is not None:
                _write_json(active_path, {
                    "version_id": version_id,
                    "project_id": project["id"],
                    "recipe_task": task,
                    "source_dataset_path": (
                        str(Path(effective_source).expanduser().resolve()) if effective_source else None
                    ),
                })
            return {
                "status": "saved", "pipeline_id": pipeline.id,
                "node_count": len(pipeline.nodes), "recipe_task": task,
                "version_id": version_id,
            }
        except Exception as e:
            logger.exception("Failed to save pipeline: %s", e)
            recovery_incomplete = False
            if version_id is not None:
                active_rollback_failed = False
                if active_path is not None:
                    try:
                        _restore_flow_file(active_path, previous_active)
                    except OSError:
                        active_rollback_failed = True
                        logger.exception("Could not roll back active flow file %s", active_path)
                # A failed rollback may leave active.json pointing at the newly
                # written version. Keep both its recipe and version in that case:
                # deleting the version would make the active flow unreadable.
                preserve_new_version = False
                if active_rollback_failed and active_path is not None:
                    try:
                        current_active = json.loads(active_path.read_text(encoding="utf-8"))
                        preserve_new_version = current_active.get("version_id") == version_id
                    except (OSError, ValueError, AttributeError):
                        preserve_new_version = True  # Unknown pointer: do not delete its possible target.
                if preserve_new_version:
                    recovery_incomplete = True
                    logger.error("Kept flow version %s because the active pointer could not be rolled back", version_id)
                else:
                    recipe_rollback_failed = False
                    try:
                        _restore_flow_file(target_path, previous_recipe)
                    except OSError:
                        recipe_rollback_failed = True
                        recovery_incomplete = True
                        logger.exception("Could not roll back flow recipe %s", target_path)
                    if recipe_rollback_failed:
                        logger.error("Kept flow version %s so the unrecovered recipe can be reopened", version_id)
                    else:
                        try:
                            (_version_dir(project_dir) / f"{version_id}.json").unlink(missing_ok=True)
                        except OSError:
                            recovery_incomplete = True
                            logger.exception("Could not remove incomplete flow version %s", version_id)
            details = f"Failed to save flowchart: {e}"
            if recovery_incomplete:
                details += "; recovery incomplete: reopen the active flow and inspect saved versions"
            error_detail = format_error_response("ERR_UNKNOWN", details=details)
            if recovery_incomplete:
                error_detail["recovery_incomplete"] = True
            raise HTTPException(
                status_code=500,
                detail=error_detail,
            ) from e


@router.get("/pipelines")
def list_saved_pipelines(source_dataset_path: Optional[str] = None, request: Request = None):
    """List immutable saved flow revisions from the active project."""
    with _FLOW_SAVE_LOCK:
        return _list_saved_pipelines_unlocked(source_dataset_path, request)


def _list_saved_pipelines_unlocked(source_dataset_path: Optional[str], request: Request):
    if source_dataset_path:
        source = Path(source_dataset_path).expanduser().resolve()
        if not source.is_dir():
            raise HTTPException(status_code=422, detail="Select an existing dataset folder.")
        source_filter = str(source)
    else:
        source_filter = None
    project = get_current_project(request) if request is not None else None
    project_dir = Path(project["project_dir"]).resolve() if project else None
    active_id: Optional[str] = None
    if project_dir is not None:
        active_file = _active_flow_file(project_dir)
        try:
            active = json.loads(active_file.read_text(encoding="utf-8"))
            if (active.get("project_id") == project["id"]
                    and active.get("source_dataset_path") == project.get("source_dataset_dir")):
                active_id = active.get("version_id")
        except (OSError, ValueError, AttributeError):
            pass
    rows: List[Dict[str, Any]] = []
    for path in _version_dir(project_dir).glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or not isinstance(record.get("pipeline"), dict):
                continue
            if source_filter is not None and record.get("source_dataset_path") != source_filter:
                continue
            pipeline = FlowchartPipeline.model_validate(record["pipeline"])
            rows.append({
                "version_id": record["version_id"],
                "pipeline_id": pipeline.id,
                "name": pipeline.name,
                "recipe_task": record["recipe_task"],
                "source_dataset_path": record.get("source_dataset_path"),
                "saved_at": record["saved_at"],
                "created_at": record["saved_at"],
                "saved_at_ns": record.get("saved_at_ns", path.stat().st_mtime_ns),
                "node_count": len(pipeline.nodes),
                "model_count": sum(
                    node.data.node_type in ("detection_crop", "inspection") for node in pipeline.nodes
                ),
                "is_active": record["version_id"] == active_id,
            })
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("Skipping invalid saved flow version %s: %s", path, exc)
    rows.sort(key=lambda row: row["saved_at_ns"], reverse=True)
    latest: set[tuple[str, Optional[str]]] = set()
    for row in rows:
        key = (row["recipe_task"], row["source_dataset_path"])
        row["is_latest"] = key not in latest
        latest.add(key)
        del row["saved_at_ns"]
    return {"pipelines": rows, "total": len(rows)}


@router.put("/pipelines/{version_id}/activate")
def activate_saved_pipeline_version(
    version_id: str, source_dataset_path: str, request: Request,
):
    """Make a saved revision the active flow for this project and dataset."""
    if not re.fullmatch(r"[0-9a-f]{32}", version_id):
        raise HTTPException(status_code=404, detail="Flow version not found.")
    project = get_current_project(request)
    project_dir = Path(project["project_dir"]).resolve()
    requested_source = str(Path(source_dataset_path).expanduser().resolve())
    with _FLOW_SAVE_LOCK:
        path = _version_dir(project_dir) / f"{version_id}.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Flow version not found.")
        if requested_source != project.get("source_dataset_dir"):
            raise HTTPException(status_code=409, detail="Selected flow source differs from the active project dataset.")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (record.get("version_id") != version_id
                    or record.get("source_dataset_path") != requested_source):
                raise ValueError("Saved flow version belongs to another dataset.")
            task = record.get("recipe_task")
            if task not in ("detection", "anomaly", "segmentation", "classification", "patch_classification", "mixed"):
                raise ValueError("Saved flow recipe is invalid.")
            pipeline = FlowchartPipeline.model_validate(record["pipeline"])
            ordered_linear_nodes(pipeline)
            if not _pipeline_matches_recipe(pipeline, task):
                raise ValueError("Saved flow model tasks do not match its recipe.")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(status_code=409, detail=f"Saved flow version is invalid: {exc}") from exc
        _write_json(_active_flow_file(project_dir), {
            "version_id": version_id,
            "project_id": project["id"],
            "recipe_task": task,
            "source_dataset_path": requested_source,
        })
    return {"status": "active", "version_id": version_id, "pipeline": pipeline}


@router.get("/pipelines/{version_id}", response_model=FlowchartPipeline)
def get_saved_pipeline_version(version_id: str, request: Request = None) -> FlowchartPipeline:
    """Open one saved revision without changing the current recipe."""
    if not re.fullmatch(r"[0-9a-f]{32}", version_id):
        raise HTTPException(status_code=404, detail="Flow version not found.")
    with _FLOW_SAVE_LOCK:
        path = _version_dir(_project_dir(request)) / f"{version_id}.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Flow version not found.")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            pipeline = FlowchartPipeline.model_validate(record["pipeline"])
            ordered_linear_nodes(pipeline)
            return pipeline
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=f"Saved flow version is invalid: {exc}") from exc


@router.get("/sample-images")
def get_sample_images(request: Request = None):
    """
    Returns candidate inspection images from operational folders and datasets
    for the Flowchart Studio UI Image Picker (Feature F30).
    """
    candidates: List[Dict[str, Any]] = []
    seen_paths = set()

    if request is None:
        search_dirs = [Path("./datasets"), Path("./projects")]
    else:
        project = get_current_project(request)
        search_dirs = [
            Path(path) for path in (project.get("source_dataset_dir"), project.get("dataset_dir"))
            if path
        ]

    for sdir in search_dirs:
        if not sdir.exists():
            continue
        for ext in [".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"]:
            for f in sdir.glob(f"*{ext}"):
                if f.name.startswith("."):
                    continue
                p_str = str(f.resolve())
                if p_str in seen_paths:
                    continue
                seen_paths.add(p_str)
                try:
                    stat = f.stat()
                    if stat.st_size == 0:
                        continue
                    candidates.append({
                        "name": f.name,
                        "file_path": p_str,
                        "thumbnail_url": f"/api/dataset/thumbnail/preview?file_path={urllib.parse.quote(p_str)}",
                        "source": "dataset",
                        "size_bytes": stat.st_size,
                    })
                except Exception:
                    continue
                if len(candidates) >= 50:
                    break
            if len(candidates) >= 50:
                break
        if len(candidates) >= 50:
            break

    return {"images": candidates, "total": len(candidates)}


@router.post("/run")
def run_flowchart(req: FlowchartRunRequest, request: Request = None):
    """
    Runs either original-resolution tiled segmentation or detector ROI inspection.
    A local result image is returned as a bounded preview; excessive tile counts
    yield REVIEW without pretending that the source image was inspected.
    """
    try:
        project = get_current_project(request) if request is not None else None
        if req.pipeline is not None:
            pipeline = req.pipeline
        elif request is not None:
            with _FLOW_SAVE_LOCK:
                active_file = _active_flow_file(Path(project["project_dir"]))
                if active_file.is_file():
                    active = json.loads(active_file.read_text(encoding="utf-8"))
                    source = project.get("source_dataset_dir")
                    if active.get("project_id") != project["id"] or active.get("source_dataset_path") != source:
                        raise HTTPException(status_code=409, detail="Active flow belongs to a different source dataset.")
                    pipeline = get_saved_pipeline_version(active["version_id"], request=request)
                else:
                    pipeline = get_pipeline(
                        inspection_task=project["task"],
                        source_dataset_path=project.get("source_dataset_dir"),
                        request=request,
                    )
        else:
            pipeline = get_pipeline()
        try:
            ordered_nodes = ordered_linear_nodes(pipeline)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not req.image_path or not Path(req.image_path).is_file():
            raise HTTPException(status_code=422, detail="Select an existing inspection image before running the flowchart.")
        model_contexts: Dict[str, Any] = {}
        local_model_jobs: set[str] = set()
        verified_checkpoints: Dict[tuple[str, str], Path] = {}
        for node in ordered_nodes:
            if node.data.node_type not in ("detection_crop", "inspection"):
                continue
            job_id = node.data.model_job_id
            if not job_id:
                raise HTTPException(status_code=409, detail=f"Model job is missing for {node.data.label}.")
            known_job = training_job_manager.get_job(job_id)
            if known_job is not None and known_job.status != "completed":
                raise HTTPException(
                    status_code=409,
                    detail=f"Model training is {known_job.status} for {node.data.label}; wait for a completed job.",
                )
            expected_task = "detection" if node.data.node_type == "detection_crop" else (node.data.task or "").lower()
            checkpoint = (
                trusted_checkpoint(job_id, project_models_dir=project["models_dir"])
                if project is not None else _ENGINE._resolve_checkpoint(job_id, expected_task)
            )
            if checkpoint is None:
                raise HTTPException(status_code=409, detail=f"Trained model is unavailable for {node.data.label}.")
            if project is not None and not _matches_source_dataset(
                checkpoint.parent, project.get("source_dataset_dir"), expected_task,
            ):
                raise HTTPException(status_code=409, detail=f"Model source is incompatible with {node.data.label}.")
            try:
                metadata = torch.load(checkpoint, map_location="cpu", weights_only=True)
                actual_task = str(metadata.get("task", "")).lower()
                if actual_task != expected_task or "model_state_dict" not in metadata:
                    raise ValueError(f"Expected {expected_task}, found {actual_task or 'unknown'}")
            except Exception as exc:
                raise HTTPException(status_code=409, detail=f"Model is incompatible with {node.data.label}: {exc}") from exc
            from backend.remote.operations import remote_job_context
            from backend.remote.coordinator import ArtifactValidationError

            verified_checkpoints[(job_id, expected_task)] = checkpoint.resolve()

            try:
                context = remote_job_context(checkpoint.parent, job_id)
            except ArtifactValidationError as exc:
                raise HTTPException(status_code=409, detail=f"Remote model provenance is invalid for {node.data.label}: {exc}") from exc
            if context is None:
                local_model_jobs.add(job_id)
            else:
                model_contexts[job_id] = context
        if model_contexts:
            if local_model_jobs:
                raise HTTPException(status_code=409, detail="Flowchart models must all be on the same compute server; train or select matching models.")
            from backend.remote.operations import run_remote_flowchart
            from backend.remote.coordinator import ArtifactValidationError, RemoteDisconnected

            try:
                return run_remote_flowchart(
                    list(model_contexts.values()), pipeline.model_dump(), Path(req.image_path), req.image_id,
                )
            except RemoteDisconnected as exc:
                raise HTTPException(status_code=503, detail=f"Remote flowchart connection lost; retry the same run: {exc}") from exc
            except ArtifactValidationError as exc:
                raise HTTPException(status_code=502, detail=f"Remote flowchart result could not be verified: {exc}") from exc
        with verified_checkpoint_scope(verified_checkpoints):
            result = _ENGINE.execute(
                pipeline=pipeline,
                image_path=req.image_path,
                image_id=req.image_id,
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error executing flowchart pipeline: %s", e)
        raise HTTPException(
            status_code=500,
            detail=format_error_response("ERR_UNKNOWN", details=f"Flowchart execution error: {e}"),
        )
