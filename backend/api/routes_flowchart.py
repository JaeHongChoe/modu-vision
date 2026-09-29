"""
backend/api/routes_flowchart.py

Multi-Model Chaining & Flowchart Pipeline Execution (inspired by Neurocle Neuro-T Flowchart):
Enables visual assembly of multi-stage vision AI inspection:
  [Input Image] -> [Stage 1: Detection / ROI Crop] -> [Stage 2: Defect Inspection / Anomaly] -> [Stage 3: Rule Decision] -> [Output]
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional
import urllib.parse

from fastapi import APIRouter, HTTPException
import torch

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
    get_single_segmentation_flowchart,
    ordered_linear_nodes,
    safe_crop_roi,
)
from backend.utils.error_catalog import format_error_response

logger = logging.getLogger("vision_ai_studio.routes_flowchart")

router = APIRouter(prefix="/api/flowchart", tags=["flowchart"])

FLOWCHARTS_DIR = Path("./projects/flowcharts")
FLOWCHARTS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_PIPELINE_FILE = FLOWCHARTS_DIR / "pipeline.json"

# Persistent cached engine instance for rapid warm execution
_ENGINE = FlowchartEngine()


@router.get("/pipeline")
def get_pipeline() -> FlowchartPipeline:
    """Retrieves the saved pipeline or a runnable single-model template."""
    if DEFAULT_PIPELINE_FILE.is_file():
        try:
            with open(DEFAULT_PIPELINE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            pipeline = FlowchartPipeline.model_validate(data)
            ordered_linear_nodes(pipeline)
            return pipeline
        except Exception as e:
            logger.exception("Could not read saved pipeline: %s", e)
            raise HTTPException(status_code=409, detail=f"Saved flowchart is invalid: {e}") from e
    return get_single_segmentation_flowchart()


@router.get("/templates/single-segmentation")
def get_single_segmentation_template(job_id: Optional[str] = None) -> FlowchartPipeline:
    """Create a full-image segmentation flow without changing the saved graph."""
    return get_single_segmentation_flowchart(job_id=job_id)


@router.post("/pipeline")
def save_pipeline(pipeline: FlowchartPipeline):
    """Saves a supported linear flow without corrupting the previous file."""
    try:
        ordered_linear_nodes(pipeline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    temporary_path: Optional[Path] = None
    try:
        DEFAULT_PIPELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=DEFAULT_PIPELINE_FILE.parent,
            prefix=f".{DEFAULT_PIPELINE_FILE.name}.", suffix=".tmp", delete=False,
        ) as f:
            temporary_path = Path(f.name)
            json.dump(pipeline.model_dump(), f, indent=2, ensure_ascii=False)
        os.replace(temporary_path, DEFAULT_PIPELINE_FILE)
        return {"status": "saved", "pipeline_id": pipeline.id, "node_count": len(pipeline.nodes)}
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
