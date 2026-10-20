import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from backend.engine.model_catalog import model_family_catalog

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("/capabilities")
def capabilities():
    return model_family_catalog()


@router.get("/decision-threshold")
def decision_threshold(job_id: str, request: Request):
    """The decision threshold this model uses outside step 4, and where it comes from (audit F-08 / A4).

    It is the same resolution the single-model export applies: an evaluation calibration from step 4 counts only while
    its evidence still matches the saved predictions, the evaluation binding and this exact checkpoint; otherwise the
    model's saved threshold. Flow nodes use it as their default and show it next to their own value; a saved or
    approved flow is never changed by this read.
    """
    from backend.api.routes_project import get_current_project
    from backend.engine.exporter import resolve_export_threshold
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", job_id or ""):
        raise HTTPException(422, "모델 작업 ID 형식이 올바르지 않습니다.")
    project = get_current_project(request)
    models = Path(project["models_dir"]).resolve()
    model_dir = (models / job_id).resolve()
    if not model_dir.is_relative_to(models) or model_dir.is_symlink():
        raise HTTPException(422, "모델 위치가 프로젝트 밖입니다.")
    checkpoint, meta_path = model_dir / "best_model.pt", model_dir / "model_meta.json"
    if not checkpoint.is_file() or not meta_path.is_file():
        raise HTTPException(404, "완료된 모델을 찾을 수 없습니다.")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(422, "모델 정보를 읽지 못했습니다.") from exc
    decision = resolve_export_threshold(checkpoint, meta, meta.get("task"), meta.get("classes"))
    return {"job_id": job_id, "threshold": round(float(decision["threshold"]), 4),
            "source": decision["threshold_source"], "calibrated": bool(decision["calibrated"]),
            "not_transferable_reason": decision["calibration_not_transferable"],
            "model_threshold": round(float(meta.get("optimal_threshold", 0.5)), 4)}
