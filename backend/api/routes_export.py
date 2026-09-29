"""
backend/api/routes_export.py

Runtime Style Production Model & Runtime Export:
Generates self-contained, offline-first deployment packages for industrial PC / PLC / C# / C++ factory lines:
- ONNX Optimized Model (model.onnx) or TorchScript (model.pt)
- Configuration & Calibration metadata (config.json)
- Zero-dependency standalone Python inference client (infer.py)
- Industrial C# .NET 8 WPF inspection client snippet (Program.cs)
- Ultra-low-latency C++ OpenCV DNN / ONNXRuntime inspection client snippet (main.cpp)
- Deployment handbook (README_DEPLOY.md)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from backend.engine.exporter import (
    EXPORTS_DIR,
    export_runtime_package as engine_export_runtime,
)
from backend.utils.error_catalog import format_error_response

logger = logging.getLogger("vision_ai_studio.routes_export")

router = APIRouter(prefix="/api/export", tags=["export"])


class ExportRuntimeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: Optional[str] = None
    export_format: str = "onnx"  # 'onnx', 'torchscript'
    resolution: Optional[int] = 256
    quantize_fp16: Optional[bool] = False
    package_name: Optional[str] = "runtime_production_package"


@router.post("/runtime")
def export_runtime_package(req: ExportRuntimeRequest):
    """
    Exports trained vision model into a deployable Runtime style runtime package.
    Produces:
      - model.onnx or model.pt (TorchScript)
      - config.json (including calibrated zero-underkill threshold)
      - infer.py (standalone runnable Python CLI client)
      - Program.cs (C# snippet)
      - main.cpp (C++ snippet)
      - README_DEPLOY.md
    """
    try:
        return engine_export_runtime(
            job_id=req.job_id,
            export_format=req.export_format,
            resolution=req.resolution or 256,
            quantize_fp16=bool(req.quantize_fp16),
            package_name=req.package_name,
        )
    except Exception as e:
        logger.exception("Export runtime package failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail=format_error_response("ERR_UNKNOWN", details=f"Failed to export runtime package: {e}"),
        )
