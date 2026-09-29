"""
backend/api/routes_flowchart.py

Multi-Model Chaining & Flowchart Pipeline Execution (inspired by Workflow Workflow Flowchart):
Enables visual assembly of multi-stage vision AI inspection:
  [Input Image] -> [Stage 1: Detection / ROI Crop] -> [Stage 2: Defect Inspection / Anomaly] -> [Stage 3: Rule Decision] -> [Output]
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Literal, Optional
import urllib.parse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import torch

from backend.api.routes_evaluation import _resolve_job_artifacts
from backend.api.routes_training import training_job_manager
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
    get_default_flowchart,
    get_single_detection_flowchart,
    get_single_segmentation_flowchart,
    ordered_linear_nodes,
    safe_crop_roi,
)
from backend.utils.error_catalog import format_error_response
from backend.engine.checkpoint_paths import is_job_id

logger = logging.getLogger("vision_ai_studio.routes_flowchart")

router = APIRouter(prefix="/api/flowchart", tags=["flowchart"])

FLOWCHARTS_DIR = Path("./projects/flowcharts")
FLOWCHARTS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_PIPELINE_FILE = FLOWCHARTS_DIR / "pipeline.json"

# Persistent cached engine instance for rapid warm execution
_ENGINE = FlowchartEngine()


InspectionTask = Literal["anomaly", "segmentation", "classification"]
PipelineTask = Literal["detection", "anomaly", "segmentation", "classification"]


def _recipe_file(task: PipelineTask, source_dataset_path: Optional[str] = None) -> Path:
    if not source_dataset_path:
        return DEFAULT_PIPELINE_FILE.with_name(f"pipeline_{task}.json")
    source = Path(source_dataset_path).expanduser().resolve()
    if not source.is_dir():
        raise HTTPException(status_code=422, detail="Select an existing dataset folder before saving or loading its flowchart.")
    source_key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
    return DEFAULT_PIPELINE_FILE.with_name(f"pipeline_{task}_{source_key}.json")


def _pipeline_matches_recipe(pipeline: FlowchartPipeline, task: PipelineTask) -> bool:
    detectors = [node for node in pipeline.nodes if node.data.node_type == "detection_crop"]
    inspections = [node for node in pipeline.nodes if node.data.node_type == "inspection"]
    if task == "detection":
        return bool(detectors)
    return bool(inspections) and all(node.data.task == task for node in inspections)


def _inferred_recipe(pipeline: FlowchartPipeline) -> PipelineTask:
    inspections = [node for node in pipeline.nodes if node.data.node_type == "inspection"]
    if inspections:
        task = inspections[0].data.task
        if task in ("anomaly", "segmentation", "classification"):
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
) -> FlowchartPipeline:
    """Retrieves the saved pipeline or a runnable single-model template."""
    recipe_files = [_recipe_file(inspection_task, source_dataset_path)]
    if source_dataset_path:
        recipe_files.append(_recipe_file(inspection_task))
    recipe_files.append(DEFAULT_PIPELINE_FILE)
    for path in recipe_files:
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pipeline = FlowchartPipeline.model_validate(data)
            ordered_linear_nodes(pipeline)
            if path == DEFAULT_PIPELINE_FILE and inspection_task == "detection" and any(
                node.data.node_type == "inspection" for node in pipeline.nodes
            ):
                # An old shared ROI graph is not the new detector-only default.
                continue
            if _pipeline_matches_recipe(pipeline, inspection_task):
                return pipeline
            if path != DEFAULT_PIPELINE_FILE:
                raise ValueError("Saved flowchart task does not match its recipe file.")
        except Exception as e:
            logger.exception("Could not read saved pipeline: %s", e)
            raise HTTPException(status_code=409, detail=f"Saved flowchart is invalid: {e}") from e
    return get_single_detection_flowchart() if inspection_task == "detection" else _single_inspection_template(inspection_task)


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
    inspection_task: Literal["anomaly", "segmentation", "classification"] = "segmentation",
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


class FlowchartModelReference(BaseModel):
    job_id: str
    task: Literal["detection", "anomaly", "segmentation", "classification"]


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


@router.post("/pipeline")
def save_pipeline(
    pipeline: FlowchartPipeline, recipe_task: Optional[PipelineTask] = None,
    source_dataset_path: Optional[str] = None,
):
    """Saves a supported linear flow without corrupting the previous file."""
    try:
        ordered_linear_nodes(pipeline)
        task = recipe_task or _inferred_recipe(pipeline)
        if not _pipeline_matches_recipe(pipeline, task):
            raise ValueError(f"Pipeline model tasks do not match the {task} recipe.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    target_path = _recipe_file(task, source_dataset_path)
    temporary_path: Optional[Path] = None
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target_path.parent,
            prefix=f".{target_path.name}.", suffix=".tmp", delete=False,
        ) as f:
            temporary_path = Path(f.name)
            json.dump(pipeline.model_dump(), f, indent=2, ensure_ascii=False)
        os.replace(temporary_path, target_path)
        return {"status": "saved", "pipeline_id": pipeline.id, "node_count": len(pipeline.nodes), "recipe_task": task}
    except Exception as e:
        logger.exception("Failed to save pipeline: %s", e)
        raise HTTPException(
            status_code=500,
            detail=format_error_response("ERR_UNKNOWN", details=f"Failed to save flowchart: {e}"),
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@router.get("/sample-images")
def get_sample_images():
    """
    Returns candidate inspection images from operational folders and datasets
    for the Flowchart Studio UI Image Picker (Feature F30).
    """
    candidates: List[Dict[str, Any]] = []
    seen_paths = set()

    search_dirs = [
        Path("/Users/kai/Downloads/운영서버/test_crop_output"),
        Path("/Users/kai/Downloads/운영서버/visual_inspection"),
        Path("/Users/kai/Downloads/운영서버/detailed_diagnosis"),
        Path("./datasets"),
        Path("./projects"),
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
                        "source": "operational" if "운영서버" in p_str else "dataset",
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
def run_flowchart(req: FlowchartRunRequest):
    """
    Runs either original-resolution tiled segmentation or detector ROI inspection.
    A local result image is returned as a bounded preview; excessive tile counts
    yield REVIEW without pretending that the source image was inspected.
    """
    try:
        pipeline = req.pipeline or get_pipeline()
        try:
            ordered_nodes = ordered_linear_nodes(pipeline)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not req.image_path or not Path(req.image_path).is_file():
            raise HTTPException(status_code=422, detail="Select an existing inspection image before running the flowchart.")
        model_contexts: Dict[str, Any] = {}
        local_model_jobs: set[str] = set()
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
            checkpoint = _ENGINE._resolve_checkpoint(job_id, node.data.task or "")
            if checkpoint is None:
                raise HTTPException(status_code=409, detail=f"Trained model is unavailable for {node.data.label}.")
            try:
                metadata = torch.load(checkpoint, map_location="cpu", weights_only=True)
                actual_task = str(metadata.get("task", "")).lower()
                expected_task = "detection" if node.data.node_type == "detection_crop" else (node.data.task or "").lower()
                if actual_task != expected_task or "model_state_dict" not in metadata:
                    raise ValueError(f"Expected {expected_task}, found {actual_task or 'unknown'}")
            except Exception as exc:
                raise HTTPException(status_code=409, detail=f"Model is incompatible with {node.data.label}: {exc}") from exc
            from backend.remote.operations import remote_job_context
            from backend.remote.coordinator import ArtifactValidationError

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
