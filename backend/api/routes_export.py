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
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, model_validator
import torch

from backend.engine.exporter import (
    EXPORTS_DIR,
    export_runtime_package as engine_export_runtime,
    locate_checkpoint,
)
from backend.utils.error_catalog import format_error_response
from backend.api.routes_project import get_current_project
from backend.api.routes_model_deployments import verified_release_revision
from backend.api.routes_evaluation import _resolve_job_artifacts
from backend.api.routes_flowchart import _FLOW_SAVE_LOCK, _recipe_file, _version_dir
from backend.engine.checkpoint_paths import is_job_id
from backend.engine.flowchart_engine import FlowchartPipeline, ordered_linear_nodes
from backend.engine.flow_package import build_flow_package, verify_flow_parity
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


class ExportFlowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_dataset_path: str
    recipe_task: str
    package_name: str
    version_id: Optional[str] = None
    verification_image_path: Optional[str] = None
    verification_image_id: Optional[str] = None
    approval_revision_ids: Optional[Dict[str, str]] = None
    deployment_profile: Literal["standard", "edge_cpu"] = "standard"
    target_os: Optional[str] = None
    target_arch: Optional[str] = None

    @model_validator(mode="after")
    def validate_deployment(self):
        if self.deployment_profile == "edge_cpu":
            target = normalize_target(self.target_os, self.target_arch)
            self.target_os, self.target_arch = target["os"], target["architecture"]
        elif self.target_os is not None or self.target_arch is not None:
            raise ValueError("Target OS/architecture requires the edge_cpu deployment profile")
        return self


@router.post("/flow")
def export_saved_flow(req: ExportFlowRequest, request: Request):
    """Export only a saved, source-matched graph and all its verified checkpoints."""
    if req.recipe_task not in (*FLOW_TASKS, "mixed"):
        raise HTTPException(status_code=422, detail="Unsupported flow recipe task")
    source = Path(req.source_dataset_path).expanduser().resolve()
    if not source.is_dir():
        raise HTTPException(status_code=422, detail="Select an existing source dataset folder")
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
    project = get_current_project(request)
    project_dir = Path(project["project_dir"]).resolve()
    if req.version_id:
        if not re.fullmatch(r"[0-9a-f]{32}", req.version_id):
            raise HTTPException(status_code=422, detail="Invalid saved flow version ID")
        saved_path = _version_dir(project_dir) / f"{req.version_id}.json"
    else:
        saved_path = _recipe_file(req.recipe_task, str(source), project_dir)
    with _FLOW_SAVE_LOCK:
        if saved_path.is_symlink() or not saved_path.is_file():
            raise HTTPException(status_code=409, detail="Save this flow for the selected dataset before exporting it")
        try:
            saved = json.loads(saved_path.read_text(encoding="utf-8"))
            if req.version_id:
                if (saved.get("version_id") != req.version_id or saved.get("recipe_task") != req.recipe_task
                        or saved.get("source_dataset_path") != str(source)):
                    raise ValueError("Saved flow version belongs to a different source or recipe")
                saved = saved["pipeline"]
            pipeline = FlowchartPipeline.model_validate(saved)
            ordered_linear_nodes(pipeline)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=f"Saved flow is invalid: {exc}") from exc

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
        except (HTTPException, OSError, ValueError, RuntimeError, pickle.UnpicklingError,
                ArtifactValidationError) as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Completed {task} model {job_id} does not match the selected dataset or saved flow: {exc}",
            ) from exc
        checkpoints[job_id] = checkpoint
    approved_revisions = None
    if req.approval_revision_ids is not None:
        if set(req.approval_revision_ids) != set(checkpoints):
            raise HTTPException(status_code=409, detail="Every flow model needs one active approval revision")
        approved_revisions = {}
        for job_id, checkpoint in checkpoints.items():
            revision = verified_release_revision(
                project, req.approval_revision_ids[job_id], source=source,
                task=job_tasks[job_id], job_id=job_id, checkpoint=checkpoint,
            )
            if revision is None:
                raise HTTPException(status_code=409, detail=f"Flow model {job_id} has no matching active approval")
            approved_revisions[job_id] = revision
    try:
        result = build_flow_package(
            pipeline=pipeline, checkpoints=checkpoints,
            output_base_dir=project_dir / "exports" / "flows",
            package_name=req.package_name,
            approved_revisions=approved_revisions,
            deployment_profile=req.deployment_profile, target_os=req.target_os, target_arch=req.target_arch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write flow package: {exc}") from exc

    result["parity"] = {"status": "not_run"}
    if req.verification_image_path:
        try:
            report = verify_flow_parity(
                package_dir=Path(result["package_path"]), pipeline=pipeline,
                checkpoints=checkpoints, image_path=Path(req.verification_image_path),
                image_id=req.verification_image_id,
            )
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=409, detail={
                "message": f"Exported flow parity verification failed: {exc}",
                "package_path": result["package_path"],
            }) from exc
        if report["status"] != "passed":
            raise HTTPException(status_code=409, detail={
                "message": "Exported flow did not match the app CPU engine on the selected image",
                "package_path": result["package_path"], "parity": report,
            })
        result["parity"] = report
    return result


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
