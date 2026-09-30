"""Project-scoped single-object rotated-box candidates and cancellable local training."""

from __future__ import annotations

import hashlib
import base64
import io
import json
import os
import re
import tempfile
import threading
from contextvars import copy_context
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from backend.api.routes_project import get_current_project
from backend.engine.specialized_models import require_completed_checkpoint
from backend.engine.rotated_detection import (
    RotatedTrainingCancelled, evaluate_rotated_detector, load_rotated_manifest,
    predict_rotated_box, train_rotated_detector, write_rotated_manifest,
)


router = APIRouter(prefix="/api/rotated-detection", tags=["rotated-detection"])
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
_LOCK = threading.RLock()


@dataclass
class _LiveJob:
    project_dir: Path
    job_id: str
    dataset_path: Path
    output_dir: Path
    total_epochs: int
    training_provenance: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    epochs_completed: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)

    def summary(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "status": self.status,
                "epochs_completed": self.epochs_completed, "total_epochs": self.total_epochs,
                "started_at": self.started_at, "result": self.result, "error": self.error,
                "training_provenance": self.training_provenance}


_JOBS: dict[tuple[str, str], _LiveJob] = {}


class RotatedBoxInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cx: float
    cy: float
    width: float
    height: float
    angle_deg: float


class RotatedSampleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(min_length=1)
    split: Literal["train", "val", "test"]
    label: str | None = None
    box: RotatedBoxInput | None = None
    objects: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=32)
    source_sha256: str | None = None


class ManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str = Field(min_length=1)
    samples: list[RotatedSampleInput] = Field(min_length=1)


class TrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str = Field(min_length=1)
    epochs: int = Field(default=10, ge=1, le=200)
    batch_size: int = Field(default=8, ge=1, le=64)
    image_size: int = Field(default=64, ge=16, le=512)
    learning_rate: float = Field(default=1e-3, gt=0, le=1)


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    dataset_path: str = Field(min_length=1)
    split: Literal["val", "test"] = "test"


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    image_path: str = Field(min_length=1)


def _project_source(request: Request, requested: str) -> Path:
    project = get_current_project(request)
    configured = project.get("source_dataset_dir")
    if not isinstance(configured, str) or not configured:
        raise HTTPException(status_code=422, detail="Select this project's source dataset before rotated training")
    source = Path(configured).expanduser().resolve()
    target = Path(requested).expanduser()
    if (not target.is_absolute() or target.is_symlink() or target.resolve() != source
            or not source.is_dir()):
        raise HTTPException(status_code=422, detail="Rotated dataset must be the active project's source directory")
    return source


def _models_root(request: Request) -> Path:
    project = get_current_project(request)
    project_root = Path(project["project_dir"]).resolve()
    models_dir = Path(project["models_dir"])
    if models_dir.is_symlink() or models_dir.resolve() != project_root / "models":
        raise HTTPException(status_code=422, detail="Project model storage is invalid")
    root = models_dir / "rotated_detection"
    if root.is_symlink():
        raise HTTPException(status_code=422, detail="Rotated model storage is invalid")
    return root


def _job_dir(request: Request, job_id: str) -> Path:
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=422, detail="Invalid rotated job ID")
    root = _models_root(request)
    directory = root / job_id
    if directory.is_symlink() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="Rotated job not found in active project")
    return directory


def _checkpoint(request: Request, job_id: str) -> Path:
    directory = _job_dir(request, job_id)
    checkpoint = directory / "best_model.pt"
    metadata = directory / "model_meta.json"
    if (checkpoint.is_symlink() or metadata.is_symlink()
            or not checkpoint.is_file() or not metadata.is_file()):
        raise HTTPException(status_code=404, detail="Completed rotated checkpoint not found in active project")
    try:require_completed_checkpoint(checkpoint)
    except (ValueError,OSError) as exc:raise HTTPException(409,str(exc)) from exc
    return checkpoint


def _state_path(job: _LiveJob) -> Path:
    return job.output_dir / "job_state.json"


def _write_state(job: _LiveJob) -> None:
    job.output_dir.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=job.output_dir,
                                         prefix=".rotated-job-", suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(job.summary(), temporary, ensure_ascii=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, _state_path(job))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _key(job: _LiveJob) -> tuple[str, str]:
    return str(job.project_dir), job.job_id


def _set_job(job: _LiveJob, status: str, *, result: dict[str, Any] | None = None,
             error: str | None = None) -> None:
    with _LOCK:
        job.status = status
        job.result = result
        job.error = error
        _write_state(job)


def _run_job(job: _LiveJob, options: TrainRequest) -> None:
    try:
        from backend.engine.training_provenance import validate_training_binding,persist_model_binding
        validate_training_binding(job.training_provenance)
        original_digest=load_rotated_manifest(job.dataset_path).provenance['dataset_sha256']
        result = train_rotated_detector(
            job.dataset_path, job.output_dir, epochs=options.epochs,
            batch_size=options.batch_size, image_size=options.image_size,
            learning_rate=options.learning_rate, device="cpu", cancel_event=job.cancel,
        )
        validate_training_binding(job.training_provenance)
        if load_rotated_manifest(job.dataset_path).provenance['dataset_sha256']!=original_digest:
            raise ValueError('Rotated labels/source changed during training')
        with _LOCK:
            if job.cancel.is_set():
                for name in ("best_model.pt", "model_meta.json", "job_receipt.json"):
                    (job.output_dir / name).unlink(missing_ok=True)
                _set_job(job, "aborted")
            else:
                persist_model_binding(job.output_dir,job.training_provenance)
                checksum=hashlib.sha256((job.output_dir/'best_model.pt').read_bytes()).hexdigest()
                meta_path=job.output_dir/'model_meta.json';meta=json.loads(meta_path.read_text())
                meta.update(checkpoint_sha256=checksum,source_dataset_path=str(job.dataset_path))
                meta_path.write_text(json.dumps(meta))
                receipt={'job_id':job.job_id,'task':'rotated_detection','status':'completed',
                    'source_dataset_path':str(job.dataset_path),'dataset_path':str(job.dataset_path),
                    'training_provenance':job.training_provenance,'checkpoint_sha256':checksum,
                    'dataset_fingerprint':job.training_provenance['dataset_fingerprint']}
                (job.output_dir/'job_receipt.json').write_text(json.dumps(receipt))
                result.update(checkpoint_sha256=checksum,model_sha256=checksum)
                _set_job(job, "completed", result=result)
    except RotatedTrainingCancelled:
        for name in ('best_model.pt','model_meta.json','job_receipt.json'):(job.output_dir/name).unlink(missing_ok=True)
        _set_job(job, "aborted")
    except Exception as exc:
        for name in ('best_model.pt','model_meta.json','job_receipt.json'):(job.output_dir/name).unlink(missing_ok=True)
        _set_job(job, "failed", error=str(exc))


def _manifest_groups(manifest):
    groups={}
    for row in manifest.records: groups.setdefault(row.image,[]).append(row)
    return groups


def _manifest_result(manifest) -> dict[str, Any]:
    return {
        "dataset_path": str(manifest.root),
        "sample_count": len(manifest.records),
        "class_name": manifest.class_name,
        "class_names": list(manifest.class_names), "version": manifest.version,
        "split_counts": manifest.provenance["split_counts"],
        "dataset_sha256": manifest.provenance["dataset_sha256"],
        "samples": [
            {"image": image, "source_sha256": rows[0].source_sha256, "split": rows[0].split, **({"objects": [{"label": row.label,"box": row.box} for row in rows]} if manifest.version==2 else {"label": rows[0].label,"box": rows[0].box})}
            for image, rows in _manifest_groups(manifest).items()
        ],
    }


@router.post("/manifest")
def save_manifest(req: ManifestRequest, request: Request):
    source = _project_source(request, req.dataset_path)
    try:
        manifest = write_rotated_manifest(source, [row.model_dump(exclude_none=True) for row in req.samples])
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _manifest_result(manifest)


@router.get("/manifest")
def read_manifest(dataset_path: str, request: Request):
    source = _project_source(request, dataset_path)
    try:
        return _manifest_result(load_rotated_manifest(source))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/train")
def start_training(req: TrainRequest, request: Request):
    source = _project_source(request, req.dataset_path)
    try:
        load_rotated_manifest(source)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project = get_current_project(request)
    from backend.engine.training_provenance import bind_family_training
    binding=bind_family_training(project,source,'rotated_detection')
    binding.update(family_dataset_path=str(source),family_dataset_sha256=load_rotated_manifest(source).provenance['dataset_sha256'],label_kind='rotated_detection')
    root = _models_root(request)
    with _LOCK:
        for existing in _JOBS.values():
            if existing.status in ("running", "stopping"):
                raise HTTPException(status_code=409, detail="Another rotated training job is already running")
        job_id = uuid.uuid4().hex
        job = _LiveJob(Path(project["project_dir"]).resolve(), job_id,
                       source, root / job_id, req.epochs,training_provenance=binding)
        _JOBS[_key(job)] = job
        _write_state(job)
        context=copy_context()
        job.thread = threading.Thread(target=lambda:context.run(_run_job,job,req),
                                      name=f"rotated-{job.job_id[:8]}", daemon=True)
        job.thread.start()
        return job.summary()


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    directory = _job_dir(request, job_id)
    project = get_current_project(request)
    with _LOCK:
        job = _JOBS.get((str(Path(project["project_dir"]).resolve()), job_id))
        if job is not None:
            return job.summary()
    path = directory / "job_state.json"
    if path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail="Rotated job state is unavailable")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Rotated job state is invalid") from exc
    if not isinstance(state, dict) or state.get("job_id") != job_id:
        raise HTTPException(status_code=422, detail="Rotated job state identity is invalid")
    if state.get("status") in ("running", "stopping"):
        state = {**state, "status": "failed", "error": "Training process ended before completion"}
    return state


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    _job_dir(request, job_id)
    project = get_current_project(request)
    with _LOCK:
        job = _JOBS.get((str(Path(project["project_dir"]).resolve()), job_id))
        if job is None:
            return get_job(job_id, request)
        if job.status == "running":
            job.cancel.set()
            job.status = "stopping"
            _write_state(job)
        return job.summary()


@router.get("/models")
def list_models(request: Request):
    root = _models_root(request)
    if not root.is_dir():
        return {"models": []}
    models = []
    for directory in sorted(root.iterdir()):
        if directory.is_symlink() or not directory.is_dir() or not _JOB_ID.fullmatch(directory.name):
            continue
        checkpoint, metadata = directory / "best_model.pt", directory / "model_meta.json"
        if (checkpoint.is_symlink() or metadata.is_symlink()
                or not checkpoint.is_file() or not metadata.is_file()):
            continue
        try:
            require_completed_checkpoint(checkpoint)
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        except (OSError, ValueError):
            continue
        if (not isinstance(meta, dict) or meta.get("task") != "rotated_detection"
                or meta.get("checkpoint_sha256") != digest):
            continue
        models.append({"job_id": directory.name, "model_sha256": digest,
                       "dataset_sha256": meta.get("dataset_sha256"),
                       "validation": meta.get("validation"),
                       "class_name": meta.get("class_name")})
    return {"models": models}


@router.post("/evaluate")
def evaluate(req: EvaluateRequest, request: Request):
    checkpoint = _checkpoint(request, req.job_id)
    source = _project_source(request, req.dataset_path)
    try:
        from backend.engine.evaluation_history import archive_specialized_evaluation
        result = evaluate_rotated_detector(checkpoint, source, split=req.split, device="cpu")
        return archive_specialized_evaluation(get_current_project(request), checkpoint, source,
            result, task='rotated_detection')
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/predict")
def predict(req: PredictRequest, request: Request):
    checkpoint = _checkpoint(request, req.job_id)
    project = get_current_project(request)
    configured = project.get("source_dataset_dir")
    if not isinstance(configured, str) or not configured:
        raise HTTPException(status_code=422, detail="Project source dataset is not selected")
    source = Path(configured).expanduser().resolve()
    image = Path(req.image_path).expanduser()
    if (not image.is_absolute() or image.is_symlink() or not image.resolve().is_relative_to(source)
            or not image.is_file()):
        raise HTTPException(status_code=422, detail="Rotated test image must be in the active source dataset")
    try:
        result = predict_rotated_box(checkpoint, image, device="cpu")
        with Image.open(image) as opened:
            preview = opened.convert("RGB")
            preview.thumbnail((480, 320), Image.Resampling.BILINEAR)
        scale_x = preview.width / result["image_size"][0]
        scale_y = preview.height / result["image_size"][1]
        for detection in result.get("detections",[result]):
            outline = [(float(x) * scale_x, float(y) * scale_y) for x, y in detection["polygon"]]
            ImageDraw.Draw(preview).line(outline + outline[:1], fill=(34, 211, 238), width=2)
        encoded = io.BytesIO()
        preview.save(encoded, format="PNG")
        result["preview_data_url"] = "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")
        result["preview_size"] = [preview.width, preview.height]
        return result
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
