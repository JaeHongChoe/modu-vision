"""
backend/api/routes_export.py

Standalone Model & Runtime Export:
Generates a model package with a Python inference runner:
- ONNX Optimized Model (model.onnx) or TorchScript (model.pt)
- Configuration & Calibration metadata (config.json)
- Python inference client (infer.py) with package requirements
- Deployment handbook (README_DEPLOY.md)
"""

from __future__ import annotations

import logging
import json
from pathlib import Path
import pickle
import platform
import re
import subprocess
import hashlib
import math
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
import torch

from backend.engine.exporter import (
    EXPORTS_DIR,
    export_runtime_package as engine_export_runtime,
    locate_checkpoint,
)
from backend.utils.error_catalog import format_error_response
from backend.api.routes_project import get_current_project
from backend.remote.flow_preflight import selected_flow_preflight,selected_target
from backend.api.routes_evaluation import _resolve_job_artifacts
from backend.api.routes_flowchart import _FLOW_SAVE_LOCK, _recipe_file, _version_dir
from backend.engine.checkpoint_paths import is_job_id
from backend.engine.flowchart_engine import FlowchartPipeline, ordered_linear_nodes
from backend.engine import flow_package as flow_package_engine
from backend.engine.flow_package import build_flow_package
from backend.engine.spatial_calibration import project_calibration_store
from backend.engine.fixture_flow import project_fixtures
from backend.engine.specialized_models import FLOW_TASKS, SPECIALIZED_TASKS, flow_model_task, valid_flow_job, resolve_specialized_checkpoint
from backend.engine.industrial_adapters import read_image_safely_rgb
from backend.engine.edge_runtime import SUPPORTED_TARGETS, normalize_target
from backend.remote.coordinator import ArtifactValidationError

logger = logging.getLogger("vision_ai_studio.routes_export")

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/edge-targets")
def edge_targets():
    os_name = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system(), platform.system().lower())
    try:
        host = normalize_target(os_name, platform.machine())
    except ValueError:
        host = None
    return {"profile": "edge_cpu", "device": "cpu", "supported": SUPPORTED_TARGETS,
            "host": host, "python": {"minimum": "3.10", "maximum_exclusive": "3.14"}}


class ParityImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    image_id: Optional[str] = None


class ExportFlowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_dataset_path: str
    recipe_task: str
    package_name: str
    version_id: Optional[str] = None
    verification_image_path: Optional[str] = None
    verification_image_id: Optional[str] = None
    parity_images: Optional[List[ParityImageRequest]] = Field(default=None, min_length=2, max_length=64)
    parity_device: Optional[str] = None
    compute_profile_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    approval_revision_ids: Optional[Dict[str, str]] = None
    deployment_profile: Literal["standard", "edge_cpu",'edge_cuda'] = "standard"
    target_os: Optional[str] = None
    target_arch: Optional[str] = None
    runtime_config: Optional[Dict[str,Any]] = None

    @model_validator(mode="after")
    def validate_deployment(self):
        from backend.engine.runtime_configuration import runtime_options
        runtime = runtime_options(self.runtime_config)
        if self.compute_profile_id is not None and self.parity_images is None:
            raise ValueError('Selected remote package parity requires a frozen multi-image cohort')
        if self.deployment_profile in ('edge_cpu','edge_cuda'):
            target = normalize_target(self.target_os, self.target_arch)
            self.target_os, self.target_arch = target["os"], target["architecture"]
        elif self.target_os is not None or self.target_arch is not None:
            raise ValueError("Target OS/architecture requires the edge_cpu deployment profile")
        if self.parity_images is not None or self.parity_device is not None:
            if self.parity_images is None:
                raise ValueError("parity_device requires a frozen parity_images cohort")
            if self.parity_device is None:
                raise ValueError("A parity_images cohort requires an explicit parity_device")
            if self.verification_image_path is not None:
                raise ValueError("Use either parity_images or the limited single verification_image_path, not both")
            if self.parity_device.startswith("openvino:"):
                raise ValueError("OpenVINO packages are accepted through the measured optimization path")
            if self.parity_device != runtime["device"]:
                raise ValueError(f"parity_device {self.parity_device!r} must be the package runtime device {runtime['device']!r}")
        return self


def _resolve_flow_checkpoint(project: Dict[str, Any], source: Path, task: str, job_id: str) -> Path:
    """The completed checkpoint of one flow model for this dataset; raises when it is missing or does not match."""
    if task in SPECIALIZED_TASKS:
        checkpoint, _ = resolve_specialized_checkpoint(project["models_dir"], job_id, task, str(source))
    else:
        _, checkpoint, _, _, _, _ = _resolve_job_artifacts(
            job_id, source_dataset_path=str(source), source_task=task,
        )
        from backend.remote.operations import remote_job_context
        remote_job_context(checkpoint.parent, job_id)
    payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if (not isinstance(payload, dict) or str(payload.get("task", "")).lower() != task
            or "model_state_dict" not in payload):
        raise ValueError("Checkpoint task or model weights do not match the flow")
    return checkpoint


def _saved_flow(project: Dict[str, Any], source: Path, recipe_task: str, version_id: Optional[str]) -> FlowchartPipeline:
    """Load a saved, source-matched graph."""
    project_dir = Path(project["project_dir"]).resolve()
    if version_id:
        if not re.fullmatch(r"[0-9a-f]{32}", version_id):
            raise HTTPException(status_code=422, detail="Invalid saved flow version ID")
        saved_path = _version_dir(project_dir) / f"{version_id}.json"
    else:
        saved_path = _recipe_file(recipe_task, str(source), project_dir)
    with _FLOW_SAVE_LOCK:
        if saved_path.is_symlink() or not saved_path.is_file():
            raise HTTPException(status_code=409, detail="Save this flow for the selected dataset before exporting it")
        try:
            saved = json.loads(saved_path.read_text(encoding="utf-8"))
            if version_id:
                if (saved.get("version_id") != version_id or saved.get("recipe_task") != recipe_task
                        or saved.get("source_dataset_path") != str(source)):
                    raise ValueError("Saved flow version belongs to a different source or recipe")
                saved = saved["pipeline"]
            pipeline = FlowchartPipeline.model_validate(saved)
            ordered_linear_nodes(pipeline)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=f"Saved flow is invalid: {exc}") from exc
    return pipeline


def _saved_flow_models(project: Dict[str, Any], source: Path, recipe_task: str, version_id: Optional[str]):
    """Load a saved, source-matched graph and resolve every model checkpoint it names."""
    pipeline = _saved_flow(project, source, recipe_task, version_id)
    checkpoints: Dict[str, Path] = {}
    job_tasks: Dict[str, str] = {}
    for node in pipeline.nodes:
        task = flow_model_task(node)
        if task is None:
            continue
        job_id = node.data.model_job_id
        if not valid_flow_job(job_id, task) or task not in FLOW_TASKS:
            raise HTTPException(status_code=409, detail=f"Flow model is missing or invalid at {node.id}")
        if job_id in job_tasks and job_tasks[job_id] != task:
            raise HTTPException(status_code=409, detail=f"Flow model {job_id} has conflicting tasks")
        job_tasks[job_id] = task
        if job_id in checkpoints:
            continue
        try:
            checkpoint = _resolve_flow_checkpoint(project, source, task, job_id)
        except (HTTPException, OSError, ValueError, RuntimeError, pickle.UnpicklingError,
                ArtifactValidationError) as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Completed {task} model {job_id} does not match the selected dataset or saved flow: {exc}",
            ) from exc
        checkpoints[job_id] = checkpoint
    return pipeline, checkpoints, job_tasks


def _release_candidates(project: Dict[str, Any], source: Path, task: str, job_id: str, checkpoint: Path) -> List[Dict[str, Any]]:
    """Existing approval revisions that still verify for this exact checkpoint, newest first.

    A revision qualifies when its stored evidence verifies, it names this job and
    checkpoint digest, its evaluation data is unchanged and no later rollback
    replaced it. Being the task's active revision is reported, not required, so
    several models of one task can each be released with their own approval.
    """
    from backend.api import routes_model_deployments as deployments
    checkpoint_sha = deployments._sha256(Path(checkpoint))
    with deployments._store(project) as conn:
        active = deployments._active(conn, source, task)
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM revisions WHERE source_dataset_path = ? AND task = ? AND job_id = ? AND checkpoint_sha256 = ? "
            "ORDER BY rowid DESC", (str(source), task, job_id, checkpoint_sha)).fetchall()]
        rolled_back = {row[0] for row in conn.execute(
            "SELECT parent_revision_id FROM revisions WHERE source_dataset_path = ? AND task = ? AND action = 'rollback' "
            "AND parent_revision_id IS NOT NULL", (str(source), task)).fetchall()}
    fingerprint = deployments._fingerprint(source)
    candidates = []
    for row in rows:
        if row["revision_id"] in rolled_back or row["evaluation_dataset_fingerprint"] != fingerprint:
            continue
        verified = deployments.verified_approval_revision(project, row["revision_id"], expected_task=task)
        if verified is None or verified.get("job_id") != job_id or verified.get("checkpoint_sha256") != checkpoint_sha:
            continue
        candidates.append({key: row[key] for key in ("revision_id", "action", "reviewer", "reason", "created_at", "comparison_id")}
                          | {"checkpoint_sha256": checkpoint_sha, "is_active": bool(active and active["revision_id"] == row["revision_id"])})
    return candidates


def _selected_release(project: Dict[str, Any], source: Path, task: str, job_id: str, checkpoint: Path,
                      revision_id: str) -> Optional[Dict[str, str]]:
    """Bind an explicitly selected revision to the exact model; never creates an approval."""
    for candidate in _release_candidates(project, source, task, job_id, checkpoint):
        if candidate["revision_id"] == revision_id:
            return {"revision_id": revision_id, "job_id": job_id, "task": task,
                    "checkpoint_sha256": candidate["checkpoint_sha256"]}
    return None


def _canonical_source(req_source: str) -> Path:
    source = Path(req_source).expanduser().resolve()
    if not source.is_dir():
        raise HTTPException(status_code=422, detail="Select an existing source dataset folder")
    return source


@router.get("/flow/approval-prerequisites")
def flow_approval_prerequisites(source_dataset_path: str, recipe_task: str, request: Request,
                                version_id: Optional[str] = None):
    """Read back the verified approvals each saved-flow model can be released with."""
    if recipe_task not in (*FLOW_TASKS, "mixed"):
        raise HTTPException(status_code=422, detail="Unsupported flow recipe task")
    source = _canonical_source(source_dataset_path)
    project = get_current_project(request)
    pipeline, checkpoints, job_tasks = _saved_flow_models(project, source, recipe_task, version_id)
    models, selected = [], {}
    for job_id, checkpoint in checkpoints.items():
        task = job_tasks[job_id]
        candidates = _release_candidates(project, source, task, job_id, checkpoint)
        active = next((row["revision_id"] for row in candidates if row["is_active"]), None)
        if active:
            selected[job_id] = active
        models.append({
            "job_id": job_id, "task": task,
            "node_ids": [node.id for node in pipeline.nodes if node.data.model_job_id == job_id],
            "checkpoint_sha256": candidates[0]["checkpoint_sha256"] if candidates else hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            "candidates": candidates, "selected_revision_id": active,
            "reason": None if active else ("select_verified_revision" if candidates else "no_verified_approval"),
        })
    status = ("ready" if len(selected) == len(models) else
              "blocked" if any(not row["candidates"] for row in models) else "selection_required")
    return {"status": status, "approval_revision_ids": selected, "models": models,
            "approval_created": False}


class FlowPreflightRequest(BaseModel):
    """A deployment preflight of one saved flow version on a target (E07)."""
    model_config = ConfigDict(extra="forbid")
    source_dataset_path: str = Field(..., min_length=1)
    recipe_task: str
    version_id: str = Field(..., pattern=r"^[0-9a-f]{32}$")
    target: Dict[str, Any]


def _preflight_store(project: Dict[str, Any]):
    from backend.engine.flow_preflight import PreflightStore
    return PreflightStore(Path(project["project_dir"]).resolve() / "deployment_preflight")


def _preflight_release(project: Dict[str, Any], source: Path, recipe_task: str, version_id: str) -> tuple[FlowchartPipeline, dict]:
    from backend.engine.flow_provenance import pipeline_sha256
    pipeline = _saved_flow(project, source, recipe_task, version_id)
    return pipeline, {"kind": "saved_flow", "version_id": version_id, "recipe_task": recipe_task,
                      "pipeline_sha256": pipeline_sha256(pipeline)}


def _preflight_requirements(project: Dict[str, Any], source: Path, pipeline: FlowchartPipeline, target: dict):
    """Resolve local artifacts identically for a fresh report and when its kept evidence is reopened."""
    from backend.engine import flow_preflight as preflight
    here = target["kind"] == "this_computer"
    checked: Dict[str, Dict[str, Any]] = {}

    def resolve_model(node, task, job_id):
        if not valid_flow_job(job_id, task) or task not in FLOW_TASKS:
            return {"state": "missing", "detail": "no completed model is connected"}
        if job_id not in checked:
            try:
                checkpoint = _resolve_flow_checkpoint(project, source, task, job_id)
                checked[job_id] = {"state": "ready", "checkpoint": checkpoint,
                                   "evidence_ref": f"sha256:{hashlib.sha256(checkpoint.read_bytes()).hexdigest()}"}
            except HTTPException as exc:
                checked[job_id] = {"state": "missing" if exc.status_code == 404 else "mismatch", "detail": str(exc.detail)}
            except (OSError, ValueError, RuntimeError, pickle.UnpicklingError, ArtifactValidationError) as exc:
                checked[job_id] = {"state": "mismatch", "detail": str(exc)}
        return checked[job_id]

    return preflight.collect_requirements(pipeline, target=target, resolve_model=resolve_model,
                                         resolve_calibration=project_calibration_store(project).load, here=here)


@router.post("/flow/preflight")
def flow_preflight(req: FlowPreflightRequest, request: Request):
    """Check every dependency of a saved flow version on a target, node by node, and keep the report. On this computer
    the runtime and device are checked; for an edge target they stay unverified until the package's own preflight runs
    there. Nothing is installed, downloaded or substituted."""
    from backend.engine import flow_preflight as preflight
    if req.recipe_task not in (*FLOW_TASKS, "mixed"):
        raise HTTPException(status_code=422, detail="Unsupported flow recipe task")
    try:
        target = preflight.normalize_target(req.target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source = _canonical_source(req.source_dataset_path)
    project = get_current_project(request)
    pipeline, release = _preflight_release(project, source, req.recipe_task, req.version_id)
    if target['kind']=='selected_compute':
        from backend.remote.profiles import get_profile_store
        try:
            profile=get_profile_store().get(target['compute_profile_id']);selected_target(profile,target['device'])
        except (FileNotFoundError,ValueError) as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        _,checkpoints,_=_saved_flow_models(project,source,req.recipe_task,req.version_id)
        try:
            report=selected_flow_preflight(profile,project,pipeline,checkpoints,release,target['device'])
            if get_profile_store().get(profile.id)!=profile:raise ValueError('Selected preflight profile changed during execution')
            _,current=_preflight_release(project,source,req.recipe_task,req.version_id)
            if current!=release:raise ValueError('Selected preflight saved flow changed during execution')
        except Exception as exc:
            raise HTTPException(status_code=503,detail=f'Selected preflight failed; no local fallback: {exc}') from exc
        _preflight_store(project).save(report)
        return {**report,'stale':False,'stale_reasons':[]}
    here = target["kind"] == "this_computer"
    requirements = _preflight_requirements(project, source, pipeline, target)
    report = preflight.build_report(pipeline, requirements, release=release, target=target,
                                    environment_value=preflight.environment() if here else None)
    _preflight_store(project).save(report)
    return {**report, "stale": False, "stale_reasons": []}


@router.get("/flow/preflights")
def list_flow_preflights(request: Request, version_id: Optional[str] = None):
    rows = _preflight_store(get_current_project(request)).list()
    return {"reports": [row for row in rows if version_id is None or (row.get("recipe_release") or {}).get("version_id") == version_id]}


@router.get("/flow/preflights/{report_id}")
def read_flow_preflight(report_id: str, request: Request, source_dataset_path: str, target: Optional[str] = None):
    """A kept report with whether it still speaks for its flow version, the target now selected (``target``, JSON) and
    this computer's environment (a runtime pack or version changed, a device appeared or went)."""
    from backend.engine import flow_preflight as preflight
    project = get_current_project(request)
    try:
        report = _preflight_store(project).load(report_id)
        selected = preflight.normalize_target(json.loads(target)) if target else None
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="No such preflight report") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    release = report["recipe_release"]
    requirements = None
    try:
        source = _canonical_source(source_dataset_path)
        pipeline, current = _preflight_release(project, source, release["recipe_task"], release["version_id"])
        requirements = _preflight_requirements(project, source, pipeline, report["target_identity"])
    except HTTPException:
        current = {"kind": "saved_flow", "version_id": None}
    here = report["target_identity"].get("kind") == "this_computer"
    reasons = preflight.staleness(report, release=current, target=selected, environment_value=preflight.environment() if here else None,
                                 current_requirements=requirements)
    if report['target_identity'].get('kind')=='selected_compute':
        from backend.remote.profiles import get_profile_store
        candidate=selected or preflight.normalize_target(report['target_identity'])
        try:
            profile=get_profile_store().get(candidate['compute_profile_id']) if candidate['kind']=='selected_compute' else None
            bound=selected_target(profile,candidate['device']) if profile else candidate
        except (FileNotFoundError,ValueError):bound={}
        # Never compare the remote environment with this API host or present a
        # stored remote check as a fresh observation without contacting it.
        reasons=[reason for reason in reasons if reason!='target_changed']
        if bound!=report['target_identity']:reasons.append('target_changed')
        reasons.append('selected_environment_not_rechecked')
    return {**report, "stale": bool(reasons), "stale_reasons": reasons}


def _validated_cohort(req: ExportFlowRequest, source: Path) -> List[Dict[str, Optional[str]]]:
    from backend.engine.runtime_device import resolve_runtime_device
    try:
        if req.compute_profile_id is None:
            resolve_runtime_device(req.parity_device)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Parity target device is unavailable: {exc}") from exc
    images = []
    for row in req.parity_images or []:
        image = Path(row.path).expanduser()
        if image.is_symlink() or not image.resolve().is_relative_to(source) or not image.resolve().is_file():
            raise HTTPException(status_code=422, detail="Parity cohort images must be files inside the selected source")
        images.append({"path": str(image.resolve()), "image_id": row.image_id})
    return images


def _failed_parity(exc: Exception, *, scope: str, device: str, package: Path) -> Dict[str, Any]:
    report = {"contract": flow_package_engine.PARITY_CONTRACT, "status": "failed", "scope": scope, "device": device,
              "error": str(exc)[-1000:], "images": [], "mismatched_fields": [], "completed_count": 0}
    try:
        report.update(flow_package_engine._parity_identity(package))
    except (OSError, ValueError, KeyError) as identity_error:
        report["identity_error"] = str(identity_error)
    return report


@router.post("/flow")
def export_saved_flow(req: ExportFlowRequest, request: Request):
    """Export only a saved, source-matched graph and all its verified checkpoints."""
    if req.recipe_task not in (*FLOW_TASKS, "mixed"):
        raise HTTPException(status_code=422, detail="Unsupported flow recipe task")
    source = _canonical_source(req.source_dataset_path)
    parity_profile = None
    if req.compute_profile_id is not None:
        from backend.remote.profiles import get_profile_store
        from backend.remote.package_parity import validate_parity_target
        parity_profile = get_profile_store().get(req.compute_profile_id)
        if parity_profile is None:
            raise HTTPException(status_code=404, detail='Selected package parity compute profile is unavailable')
        try:
            validate_parity_target(parity_profile, req.parity_device)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.verification_image_path:
        image = Path(req.verification_image_path).expanduser().resolve()
        if not image.is_file():
            raise HTTPException(status_code=422, detail="Select an existing parity verification image")
        try:
            preview = read_image_safely_rgb(image, max_dim=32)
            if preview.size == 0:
                raise ValueError("Empty image")
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Parity verification image is unreadable") from exc
    cohort = _validated_cohort(req, source) if req.parity_images is not None else None
    project = get_current_project(request)
    project_dir = Path(project["project_dir"]).resolve()
    pipeline, checkpoints, job_tasks = _saved_flow_models(project, source, req.recipe_task, req.version_id)

    approved_revisions = None
    if req.approval_revision_ids is not None:
        if set(req.approval_revision_ids) != set(checkpoints):
            raise HTTPException(status_code=409, detail="Every flow model needs one selected approval revision")
        approved_revisions = {}
        for job_id, checkpoint in checkpoints.items():
            revision = _selected_release(project, source, job_tasks[job_id], job_id, checkpoint,
                                         req.approval_revision_ids[job_id])
            if revision is None:
                raise HTTPException(status_code=409,
                                    detail=f"Flow model {job_id} has no verified approval revision {req.approval_revision_ids[job_id]!r} for its checkpoint")
            approved_revisions[job_id] = revision
    try:
        result = build_flow_package(
            pipeline=pipeline, checkpoints=checkpoints,
            output_base_dir=project_dir / "exports" / "flows",
            package_name=req.package_name,
            approved_revisions=approved_revisions,
            deployment_profile=req.deployment_profile, target_os=req.target_os, target_arch=req.target_arch,
            runtime_config=req.runtime_config,
            calibrations=project_calibration_store(project).load,
            fixtures=project_fixtures(project).load,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write flow package: {exc}") from exc

    from backend.engine.product_delivery import record_package
    package = Path(result["package_path"])
    result["parity"] = {"status": "not_run"}
    if approved_revisions is not None and cohort is None:
        # Approved field releases need a passed cohort receipt; raw or one-image policies are not issued.
        result["release_policy"] = None
        result["release_policy_withheld"] = "cohort_parity_required"
    if cohort is None and not req.verification_image_path:
        if approved_revisions is not None:
            from backend.engine.release_eligibility import authorize_release_action
            try:
                authorize_release_action(package, project, action='export', source=source)
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=409, detail=f"Export approval became stale: {exc}") from exc
        record_package(project, package, version_id=req.version_id, recipe_task=req.recipe_task, parity=result["parity"])
        return result
    scope = "cohort" if cohort is not None else "single_image"
    device = req.parity_device if cohort is not None else "cpu"
    images = cohort if cohort is not None else [{"path": req.verification_image_path, "image_id": req.verification_image_id}]
    target_identity = ({'execution_target': 'selected_compute', 'compute_profile_id': parity_profile.id,
                        'compute_profile_name': parity_profile.name, 'compute_gpu_selector': parity_profile.gpu_selector}
                       if parity_profile else {'execution_target': 'local', 'compute_profile_id': None})
    # Record a failure first: if the package changes or the process stops mid-check, the library keeps it.
    record_package(project, package, version_id=req.version_id, recipe_task=req.recipe_task,
                   parity={**_failed_parity(RuntimeError("Parity verification started but recorded no result"),
                                           scope=scope, device=device, package=package), **target_identity})
    try:
        if parity_profile is not None:
            from backend.remote.package_parity import verify_package_on_compute
            report = verify_package_on_compute(parity_profile, project, package_dir=package, pipeline=pipeline,
                                               checkpoints=checkpoints, images=images, device=device)
            if get_profile_store().get(parity_profile.id) != parity_profile:
                raise ArtifactValidationError('Selected package parity profile changed during execution')
        else:
            report = flow_package_engine.verify_flow_parity_cohort(
                package_dir=package, pipeline=pipeline, checkpoints=checkpoints, images=images, device=device, scope=scope,
            )
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        report = {**_failed_parity(exc, scope=scope, device=device, package=package), **target_identity}
    # Every executed check leaves its receipt in the package and the library, including failures.
    flow_package_engine.write_parity_receipt(package, report)
    try:
        record_package(project, package, version_id=req.version_id, recipe_task=req.recipe_task, parity=report)
    except (ValueError, OSError) as exc:
        # A package that no longer verifies cannot replace the failure already recorded for it.
        report = {**report, "status": "failed", "error": f"{report.get('error') or ''} Library record refused: {exc}".strip()}
        flow_package_engine.write_parity_receipt(package, report)
    if report["status"] != "passed":
        raise HTTPException(status_code=409, detail={
            "message": ("Exported flow did not match the app engine on the frozen parity input" if report["status"] == "mismatch"
                        else f"Exported flow parity verification failed: {report.get('error')}"),
            "package_path": str(package), "parity": report,
        })
    result["parity"] = report
    if approved_revisions is not None:
        from backend.engine.release_eligibility import authorize_release_action, release_authority
        try:
            with release_authority(project, source=source):
                authorize_release_action(package, project, action='export', source=source)
                if scope == "cohort":
                    receipt = package / flow_package_engine.PARITY_RECEIPT
                    result["release_policy"] = {**result["release_policy"], "device": report["device"],
                                                "parity_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=f"Export approval became stale during verification: {exc}") from exc
    return result


@router.get('/runtime-capabilities')
def runtime_capabilities():
    from backend.engine.openvino_runtime import available_openvino_devices
    try:openvino={**available_openvino_devices(),'available':True}
    except ValueError as exc:openvino={'available':False,'devices':[],'error':str(exc)}
    cuda=[f'cuda:{index}' for index in range(torch.cuda.device_count())] if torch.cuda.is_available() else []
    mps=['mps'] if torch.backends.mps.is_available() else []
    return {'torch_devices':['cpu',*cuda,*mps],'openvino':openvino,'native_sdk':{'languages':['Python','C++','C#'],'transport':'embedded_cpython_c_abi'},
            'hardware_acceptance':'requires_target_execution','optimization_precisions':['fp32','fp16','int8']}


class OptimizeFlowRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    package_dir:str
    source_dataset_path:str
    precision:Literal['fp32','fp16','int8']='fp32'
    device:str='CPU'
    cpu_threads:int=Field(default=1,ge=1,le=64,strict=True)
    calibration_images:List[str]=Field(default_factory=list,max_length=1024)
    validation_images:List[str]=Field(min_length=1,max_length=1024)


@router.post('/flow/optimize')
def optimize_flow(req:OptimizeFlowRequest,request:Request):
    from backend.engine.specialized_training_jobs import require_training_source
    from backend.engine.runtime_optimization_jobs import start_job
    project=get_current_project(request)
    if not project:raise HTTPException(409,'Open a project before optimizing a saved flow')
    try:
        source=require_training_source(project,req.source_dataset_path)
        package=Path(req.package_dir).expanduser()
        owned=Path(project['project_dir']).resolve()/'exports'/'flows'
        if package.is_symlink() or not package.resolve().is_relative_to(owned):raise ValueError('Optimization package must belong to this project')
        for value in [*req.calibration_images,*req.validation_images]:
            image=Path(value).expanduser()
            if image.is_symlink() or not image.is_file() or not image.resolve().is_relative_to(source):raise ValueError('Calibration/validation images must belong to the active canonical source')
        from backend.engine.runtime_configuration import runtime_options
        runtime_options({'device':'openvino:'+req.device,'cpu_threads':req.cpu_threads})
        receipt=_optimization_input_receipt(project,source,req.calibration_images,req.validation_images)
        return start_job(project['project_dir'],{**{key:value for key,value in req.model_dump().items() if key!='source_dataset_path'},'input_receipt':receipt})
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@router.get('/flow/optimization-jobs/{job_id}')
def optimization_job(job_id:str,request:Request):
    from backend.engine.runtime_optimization_jobs import read_job
    project=get_current_project(request)
    if not project:raise HTTPException(409,'Open a project before reading runtime optimization')
    try:return read_job(project['project_dir'],job_id)
    except (ValueError,OSError) as exc:raise HTTPException(404,str(exc)) from exc


@router.post('/flow/optimization-jobs/{job_id}/cancel')
def cancel_optimization(job_id:str,request:Request):
    from backend.engine.runtime_optimization_jobs import cancel_job
    project=get_current_project(request)
    if not project:raise HTTPException(409,'Open a project before canceling runtime optimization')
    try:return cancel_job(project['project_dir'],job_id)
    except (ValueError,OSError) as exc:raise HTTPException(404,str(exc)) from exc


def _optimization_input_receipt(project,source,calibration,validation):
    from backend.engine.flow_package_runtime import _sha256
    from backend.api.routes_model_comparisons import _fingerprint
    from backend.engine.dataset_loaders import split_root_scope,_classification_split_assignments
    from backend.api.routes_dataset import _split_manifest_file
    with split_root_scope(Path(project.get('dataset_dir',Path(project['project_dir'])/'dataset'))/'splits'):
        assignments=_classification_split_assignments(source) or {}
        split=_split_manifest_file(source)
    def rows(images):
        values=[]
        for image in images:
            path=Path(image).resolve();relative=path.relative_to(source)
            partition=assignments.get(str(path))
            if partition is None:partition=next((p for p in relative.parts[:-1] if p in ('train','val','test')),None)
            values.append({'relative_path':relative.as_posix(),'sha256':_sha256(path),'split':partition})
        return values
    return {'source_dataset_path':str(source),'source_fingerprint':_fingerprint(source),
            'split_manifest_path':str(split) if split.is_file() else None,
            'split_manifest_sha256':_sha256(split) if split.is_file() else None,
            'calibration_images':rows(calibration),'validation_images':rows(validation)}


class PrecisionApprovalRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reviewer:str=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=8,max_length=2000)
    holdout_reviewed:Literal[True]
    maximum_absolute_drift:float=Field(ge=0,allow_inf_nan=False)
    approval_revision_ids:Dict[str,str]

    @field_validator('holdout_reviewed',mode='before')
    @classmethod
    def explicit_review(cls,value):
        if value is not True:raise ValueError('Holdout review requires explicit true')
        return value

    @field_validator('maximum_absolute_drift',mode='before')
    @classmethod
    def numeric_bound(cls,value):
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Drift bound requires an explicit finite number')
        return value


def _optimization_approval_context(project,job_id):
    from backend.engine.runtime_optimization_jobs import read_job
    from backend.engine.flow_package_runtime import verify_flow_package,_sha256
    from backend.api.routes_model_deployments import _store,_active,verified_release_revision
    record=read_job(project['project_dir'],job_id)
    if record['status']!='completed' or not record.get('result'):raise ValueError('Only a completed measured optimization candidate can be approved')
    candidate=Path(record['result']['package_path']);_,checkpoints=verify_flow_package(candidate)
    if record['result'].get('candidate_manifest_sha256')!=_sha256(candidate/'manifest.json'):raise ValueError('Completed measured optimization candidate has changed')
    if not candidate.resolve().is_relative_to(Path(project['project_dir']).resolve()/'exports/flows'):raise ValueError('Optimization candidate leaves its owning project')
    info=json.loads((candidate/'openvino_models.json').read_text(encoding='utf-8'));receipt=info['input_receipt']
    source=Path(project['source_dataset_dir']).resolve()
    if receipt['source_dataset_path']!=str(source):raise ValueError('Optimization source differs from this active project')
    current=_optimization_input_receipt(project,source,[str(source/row['relative_path']) for row in receipt['calibration_images']],[str(source/row['relative_path']) for row in receipt['validation_images']])
    if current!=receipt:raise ValueError('Optimization source, split, calibration or heldout hashes changed')
    if any(row['split'] not in ('val','test') for row in receipt['validation_images']):raise ValueError('Validation must use saved heldout val/test images')
    if any(row['precision']=='int8' for row in info['models']) and any(row['split']!='train' for row in receipt['calibration_images']):raise ValueError('INT8 calibration must use saved training images')
    revisions={}
    for row in info['models']:
        with _store(project) as conn:active=_active(conn,source,row['task'])
        revision=verified_release_revision(project,active['revision_id'],source=source,task=row['task'],job_id=row['job_id'],checkpoint=checkpoints[row['job_id']]) if active else None
        if revision is None:raise ValueError(f"Model {row['task']} needs its current active checkpoint approval before precision acceptance")
        revisions[row['job_id']]=revision
    return candidate,info,revisions


@router.get('/flow/optimization-jobs/{job_id}/approval-prerequisites')
def precision_approval_prerequisites(job_id:str,request:Request):
    project=get_current_project(request)
    if not project:raise HTTPException(409,'Open a project before reviewing runtime acceptance')
    try:
        candidate,info,revisions=_optimization_approval_context(project,job_id)
        return {'status':'ready','approval_revision_ids':{job:row['revision_id'] for job,row in revisions.items()},'models':info['models'],'candidate_path':str(candidate)}
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/flow/optimization-jobs/{job_id}/heldout-results/{index}')
def precision_heldout_result(job_id:str,index:int,request:Request):
    from backend.engine.runtime_optimization_jobs import read_job
    from backend.engine.flow_package_runtime import verify_flow_package,_sha256
    project=get_current_project(request)
    if not project:raise HTTPException(409,'Open a project before reviewing runtime evidence')
    try:
        record=read_job(project['project_dir'],job_id)
        if record['status']!='completed' or not record.get('result'):raise ValueError('Conversion has no completed heldout evidence')
        candidate=Path(record['result']['package_path']);verify_flow_package(candidate)
        if not candidate.resolve().is_relative_to(Path(project['project_dir']).resolve()/'exports/flows') or _sha256(candidate/'manifest.json')!=record['result'].get('candidate_manifest_sha256'):raise ValueError('Completed optimization candidate has changed')
        rows=json.loads((candidate/'heldout_flow_results.json').read_text(encoding='utf-8'))
        if index<0 or index>=len(rows):raise ValueError('Heldout image index is out of range')
        relative=rows[index]['result_path'];path=candidate/relative
        if relative!=f'heldout/heldout_{index:04d}.json' or _sha256(path)!=rows[index]['result_sha256']:raise ValueError('Heldout output hash or path changed')
        return {'index':index,'total':len(rows),**json.loads(path.read_text(encoding='utf-8'))}
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(409,str(exc)) from exc


@router.post('/flow/optimization-jobs/{job_id}/approve')
def approve_precision(job_id:str,req:PrecisionApprovalRequest,request:Request):
    from backend.engine.runtime_precision_approval import approve_precision_package
    project=get_current_project(request)
    if not project:raise HTTPException(409,'Open a project before approving runtime acceptance')
    try:
        candidate,info,revisions=_optimization_approval_context(project,job_id)
        if req.approval_revision_ids!={job:row['revision_id'] for job,row in revisions.items()}:raise ValueError('Active model approval changed since this review was opened')
        output=Path(project['project_dir'])/'exports/flows'/('approved_runtime_'+uuid.uuid4().hex)
        approved=approve_precision_package(candidate,output,revisions=revisions,reviewer=req.reviewer,reason=req.reason,
            maximum_absolute_drift=req.maximum_absolute_drift,holdout_reviewed=req.holdout_reviewed)
        policy=Path(project['project_dir'])/'exports/runtime_policies'/f"{approved['release_policy']['manifest_sha256']}.json"
        policy.parent.mkdir(parents=True,exist_ok=True)
        with policy.open('x',encoding='utf-8') as writer:json.dump(approved['release_policy'],writer,indent=2)
        approved['release_policy_path']=str(policy)
        return approved
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(409,str(exc)) from exc


class ExportRuntimeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: Optional[str] = None
    export_format: str = "onnx"  # 'onnx', 'torchscript'
    resolution: Optional[int] = 256
    quantize_fp16: Optional[bool] = False
    package_name: Optional[str] = "modu_vision_model_package"


@router.post("/runtime")
def export_runtime_package(req: ExportRuntimeRequest):
    """
    Exports a trained vision model into a standalone Python runtime package.
    Produces:
      - model.onnx or model.pt (TorchScript)
      - config.json (including calibration status)
      - infer.py (standalone runnable Python CLI client)
      - requirements.txt (runtime Python dependencies)
      - README_DEPLOY.md
    """
    try:
        from backend.remote.operations import remote_job_context, run_remote_export
        from backend.remote.coordinator import ArtifactValidationError, RemoteDisconnected

        checkpoint = locate_checkpoint(req.job_id) if req.job_id else None
        if checkpoint is not None:
            remote_context = remote_job_context(checkpoint.parent, checkpoint.parent.name)
            if remote_context is not None:
                return run_remote_export(
                    remote_context, req.export_format, req.resolution or 256,
                    bool(req.quantize_fp16),
                    req.package_name or f"modu_vision_export_{req.job_id}",
                )
        return engine_export_runtime(
            job_id=req.job_id,
            export_format=req.export_format,
            resolution=req.resolution or 256,
            quantize_fp16=bool(req.quantize_fp16),
            package_name=req.package_name,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RemoteDisconnected as e:
        raise HTTPException(status_code=503, detail=f"Remote export connection lost; retry the same run: {e}") from e
    except ArtifactValidationError as e:
        raise HTTPException(status_code=502, detail=f"Remote export could not be verified: {e}") from e
    except Exception as e:
        logger.exception("Export runtime package failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail=format_error_response("ERR_UNKNOWN", details=f"Failed to export runtime package: {e}"),
        )
