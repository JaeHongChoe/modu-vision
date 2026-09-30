"""Project-scoped OCR candidates backed by an explicit text-label manifest."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import JSONResponse
from backend.engine.specialized_training_jobs import start_job,require_training_source,read_job,list_jobs,cancel_job

from backend.api.routes_project import get_current_project
from backend.engine.specialized_models import require_completed_checkpoint
from backend.engine.ocr import (
    OCRManifest,
    evaluate_ocr_checkpoint,
    load_ocr_manifest,
    predict_ocr,
    train_ocr,
    write_ocr_manifest,
)


router = APIRouter(prefix="/api/ocr", tags=["ocr"])
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")


class OCRLabelRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(min_length=1)
    text: str = Field(min_length=1)
    split: Literal["train", "val", "test"]


class OCRManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str = Field(min_length=1)
    samples: list[OCRLabelRow] = Field(min_length=1)


class OCRTrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str = Field(min_length=1)
    epochs: int = Field(default=20, ge=1, le=500)
    batch_size: int = Field(default=8, ge=1, le=256)
    image_height: int = Field(default=32, ge=8, le=512)
    image_width: int = Field(default=128, ge=8, le=4096)
    learning_rate: float = Field(default=1e-3, gt=0, le=1)
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    background: bool = False
    seed: int = 0
    warm_start_job_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class OCREvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    dataset_path: str = Field(min_length=1)
    split: Literal["train", "val", "test"] = "test"
    device: Literal["cpu", "cuda", "mps"] = "cpu"


class OCRPredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    image_path: str = Field(min_length=1)
    device: Literal["cpu", "cuda", "mps"] = "cpu"


def _models_root(request: Request) -> Path:
    project = get_current_project(request)
    project_root = Path(project["project_dir"]).resolve()
    models_dir = Path(project["models_dir"])
    if models_dir.is_symlink() or models_dir.resolve() != project_root / "models":
        raise HTTPException(status_code=422, detail="Project model storage is invalid")
    root = models_dir / "ocr"
    if root.is_symlink():
        raise HTTPException(status_code=422, detail="OCR model storage is invalid")
    return root


def _checkpoint(request: Request, job_id: str) -> Path:
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=422, detail="Invalid OCR job ID")
    job_dir = _models_root(request) / job_id
    path = job_dir / "best_model.pt"
    if job_dir.is_symlink() or path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail="OCR checkpoint not found in active project")
    try:require_completed_checkpoint(path)
    except (ValueError,OSError) as exc:raise HTTPException(409,str(exc)) from exc
    return path


def _manifest_response(manifest: OCRManifest) -> dict:
    return {
        "dataset_path": str(manifest.root),
        "sample_count": len(manifest.samples),
        "alphabet": manifest.alphabet,
        "provenance": manifest.provenance,
        "samples": [
            {"image": row.image, "text": row.text, "split": row.split, "source_sha256": row.source_sha256}
            for row in manifest.samples
        ],
    }


@router.post("/manifest")
def create_manifest(req: OCRManifestRequest, request: Request):
    """Pin caller-supplied text labels to image hashes; never guess labels."""
    get_current_project(request)
    try:
        manifest = write_ocr_manifest(req.dataset_path, [row.model_dump() for row in req.samples])
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _manifest_response(manifest)


@router.get("/manifest")
def inspect_manifest(dataset_path: str, request: Request):
    get_current_project(request)
    try:
        return _manifest_response(load_ocr_manifest(dataset_path))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/train")
def train(req: OCRTrainRequest, request: Request):
    project=get_current_project(request)
    try:source=require_training_source(project,req.dataset_path)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    try:
        from backend.engine.specialized_warm_start import resolve_family_parent
        parent = resolve_family_parent(project['models_dir'], req.warm_start_job_id, 'ocr', source, source, req.model_dump()) if req.warm_start_job_id else None
        output=_models_root(request)/uuid.uuid4().hex
        result=start_job(project=project,task='ocr',source=source,output=output,options=req,
            runner=lambda event,progress,device:train_ocr(source,output,epochs=req.epochs,batch_size=req.batch_size,image_size=(req.image_height,req.image_width),learning_rate=req.learning_rate,device=device,seed=req.seed,cancel_event=event,on_progress=progress,warm_start=parent),family_digest=lambda:load_ocr_manifest(source).provenance['dataset_sha256'],warm_start=parent)
        return JSONResponse(result,status_code=202) if req.background else result
    except InterruptedError as exc:raise HTTPException(409,str(exc)) from exc
    except (ValueError,OSError,RuntimeError) as exc:raise HTTPException(422,str(exc)) from exc


@router.get('/warm-start-parents')
def warm_start_parents(dataset_path: str, request: Request, image_height: int = 32, image_width: int = 128):
    from backend.engine.specialized_warm_start import list_family_parents
    project = get_current_project(request)
    _models_root(request)
    try:
        source = require_training_source(project, dataset_path)
        return list_family_parents(project['models_dir'], 'ocr', source, source, {'image_height': image_height, 'image_width': image_width})
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _job_action(request,action,job_id=None):
    try:return action(_models_root(request),job_id) if job_id is not None else action(_models_root(request))
    except FileNotFoundError as exc:raise HTTPException(404,str(exc)) from exc
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@router.get('/jobs')
def jobs(request:Request):return {'jobs':_job_action(request,list_jobs)}


@router.get('/jobs/{job_id}')
def job_status(job_id:str,request:Request):return _job_action(request,read_job,job_id)


@router.post('/jobs/{job_id}/cancel')
def cancel(job_id:str,request:Request):return _job_action(request,cancel_job,job_id)


@router.get("/models")
def list_models(request: Request):
    root = _models_root(request)
    if not root.is_dir():
        return {"models": []}
    models = []
    for job_dir in sorted(root.iterdir()):
        if not _JOB_ID.fullmatch(job_dir.name) or job_dir.is_symlink() or not job_dir.is_dir():
            continue
        checkpoint = job_dir / "best_model.pt"
        metadata = job_dir / "model_meta.json"
        if checkpoint.is_symlink() or metadata.is_symlink() or not checkpoint.is_file() or not metadata.is_file():
            continue
        try:
            require_completed_checkpoint(checkpoint)
            meta = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("task") != "ocr":
            continue
        models.append({
            "job_id": job_dir.name, "model_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            "metadata": meta,
        })
    return {"models": models}


@router.post("/evaluate")
def evaluate(req: OCREvaluateRequest, request: Request):
    checkpoint = _checkpoint(request, req.job_id)
    try:
        from backend.engine.evaluation_history import archive_specialized_evaluation
        project = get_current_project(request)
        result = evaluate_ocr_checkpoint(checkpoint, req.dataset_path, split=req.split, device=req.device)
        return archive_specialized_evaluation(project, checkpoint,
            project.get('source_dataset_dir') or req.dataset_path, result,
            task='ocr', dataset_path=req.dataset_path)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/predict")
def predict(req: OCRPredictRequest, request: Request):
    checkpoint = _checkpoint(request, req.job_id)
    try:
        return predict_ocr(checkpoint, req.image_path, device=req.device)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
