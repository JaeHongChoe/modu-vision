"""
backend/api/routes_flowchart.py

Multi-Model Chaining & Flowchart Pipeline Execution (inspired by Workflow Workflow Flowchart):
Enables visual assembly of multi-stage vision AI inspection:
  [Input Image] -> [Stage 1: Detection / ROI Crop] -> [Stage 2: Defect Inspection / Anomaly] -> [Stage 3: Rule Decision] -> [Output]
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.parse

from fastapi import APIRouter, HTTPException

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
    """Retrieves stored flowchart pipeline or industrial default template."""
    if DEFAULT_PIPELINE_FILE.is_file():
        try:
            with open(DEFAULT_PIPELINE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return FlowchartPipeline.model_validate(data)
        except Exception as e:
            logger.warning("Could not read pipeline file, returning default: %s", e)
    return get_default_flowchart()


@router.post("/pipeline")
def save_pipeline(pipeline: FlowchartPipeline):
    """Saves flowchart multi-model pipeline configuration."""
    try:
        with open(DEFAULT_PIPELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(pipeline.model_dump(), f, indent=2, ensure_ascii=False)
        return {"status": "saved", "pipeline_id": pipeline.id, "node_count": len(pipeline.nodes)}
    except Exception as e:
        logger.exception("Failed to save pipeline: %s", e)
        raise HTTPException(
            status_code=500,
            detail=format_error_response("ERR_UNKNOWN", details=f"Failed to save flowchart: {e}"),
        )


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
    Executes multi-model flowchart pipeline on an input image:
    1. Loads input image safely with adaptive cap (max_dim=1600).
    2. Runs Detection to extract component ROIs with safe bounding box clipping.
    3. Runs Inspection (Anomaly / Segmentation / Classification) on each cropped patch with PyTorch.
    4. Applies Rule Decision logic to determine final OK/NG status and real latencies.
    """
    try:
        pipeline = req.pipeline or get_pipeline()
        result = _ENGINE.execute(
            pipeline=pipeline,
            image_path=req.image_path,
            image_id=req.image_id,
        )
        return result
    except Exception as e:
        logger.exception("Error executing flowchart pipeline: %s", e)
        raise HTTPException(
            status_code=500,
            detail=format_error_response("ERR_UNKNOWN", details=f"Flowchart execution error: {e}"),
        )
