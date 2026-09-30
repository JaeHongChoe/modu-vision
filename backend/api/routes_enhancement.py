"""Project-scoped paired image-improvement candidates."""
from __future__ import annotations

import hashlib
import io
import json
import uuid
import threading
import re
import time
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.api.routes_project import get_current_project
from backend.engine.enhancement import prepare_enhancement, load_enhancement_manifest, train_enhancement, evaluate_enhancement, predict_enhancement, _atomic

router = APIRouter(prefix="/api/enhancement", tags=["enhancement"])
_PROCESS_INSTANCE = uuid.uuid4().hex
_CANCEL_EVENTS: dict[str, threading.Event] = {}
_JOB_LOCK = threading.RLock()


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_dataset_path: str
    noise_sigma: float = Field(default=15, gt=0, le=50)
    seed: int = 0
    image_paths: list[str] | None = None
    image_limit: int | None = Field(default=None, ge=3, le=10000)


class Train(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str
    epochs: int = Field(default=1, ge=1, le=500)
    batch_size: int = Field(default=4, ge=1, le=256)
    learning_rate: float = Field(default=1e-3, gt=0, le=1)
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    background: bool = False
    warm_start_job_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class Evaluate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    dataset_path: str
    split: Literal["train", "val", "test"] = "test"
    device: Literal["cpu", "cuda", "mps"] = "cpu"


class Predict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    image_path: str
    device: Literal["cpu", "cuda", "mps"] = "cpu"


def _root(request: Request) -> Path:
    project = get_current_project(request)
    root = Path(project["models_dir"])
    if root.is_symlink() or root.resolve() != Path(project["project_dir"]).resolve() / "models" or (root / "enhancement").is_symlink():
        raise HTTPException(422, "Invalid project model storage")
    return root / "enhancement"


def _checkpoint(request: Request, job_id: str) -> Path:
    folder = _root(request) / job_id
    path = folder / "best_model.pt"
    if folder.is_symlink() or path.is_symlink() or not path.is_file():
        raise HTTPException(404, "Enhancement model not found in this project")
    if (folder / 'job.json').exists() and _job(request, job_id)[1]['status'] != 'completed':
        raise HTTPException(409, "Enhancement training has not completed successfully")
    return path


@router.post("/prepare")
def prepare(req: Prepare, request: Request):
    project = get_current_project(request)
    source = Path(req.source_dataset_path).expanduser().resolve()
    if project.get("source_dataset_dir") and source != Path(project["source_dataset_dir"]).resolve():
        raise HTTPException(409, "Enhancement source differs from the active project")
    destination = Path(project["dataset_dir"]) / "enhancement" / uuid.uuid4().hex
    try:
        selected = req.image_paths
        if req.image_limit is not None:
            if selected is None:
                from backend.engine.enhancement import _EXTENSIONS
                selected = [p.relative_to(source).as_posix() for p in sorted(source.rglob('*'))
                            if p.is_file() and not p.is_symlink() and p.suffix.lower() in _EXTENSIONS
                            and not any(part.startswith('.') for part in p.relative_to(source).parts)]
            selected = selected[:req.image_limit]
        return prepare_enhancement(source, destination, seed=req.seed, noise_sigma=req.noise_sigma, image_paths=selected)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/manifest")
def manifest(dataset_path: str, request: Request):
    get_current_project(request)
    try:
        return load_enhancement_manifest(dataset_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/train")
def train(req: Train, request: Request):
    folder = _root(request) / uuid.uuid4().hex
    project = get_current_project(request)
    try:
        manifest = load_enhancement_manifest(req.dataset_path)
        if project.get("source_dataset_dir") and Path(manifest["provenance"]["source_dataset_path"]).resolve() != Path(project["source_dataset_dir"]).resolve():
            raise ValueError("Enhancement training source differs from the active project")
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    from backend.engine.training_provenance import bind_family_training, validate_training_binding, persist_model_binding
    try:
        binding = bind_family_training(project, req.dataset_path, 'enhancement')
        from backend.engine.specialized_warm_start import resolve_family_parent
        _root(request)
        parent = resolve_family_parent(project['models_dir'], req.warm_start_job_id, 'enhancement',
            manifest['provenance']['source_dataset_path'], req.dataset_path, req.model_dump()) if req.warm_start_job_id else None
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    event = threading.Event()
    key = str(folder.resolve())
    record = {"job_id": folder.name, "status": "queued", "epoch": 0, "epochs": req.epochs,
              "dataset_path": req.dataset_path, "source_dataset_path": manifest["provenance"]["source_dataset_path"],
              "owner_instance": _PROCESS_INSTANCE, "device": req.device, "created_at": time.time(), "error": None}
    record['training_provenance'] = binding
    if parent is not None:
        record['warm_start'] = parent.lineage()
    def persist(**changes):
        with _JOB_LOCK:
            if event.is_set() and changes.get('status') == 'running':
                changes['status'] = 'stopping'
            elif event.is_set() and 'status' not in changes and record['status'] in {'queued','running'}:
                changes['status'] = 'stopping'
            record.update(changes)
            _atomic(folder / "job.json", json.dumps(record, ensure_ascii=False).encode())
    def execute():
        try:
            if event.is_set(): raise InterruptedError("Enhancement training cancelled")
            from backend.engine.shared_scheduler import compute_lease_scope
            with compute_lease_scope(folder.name, req.device):
                persist(status="running")
                validate_training_binding(binding)
                def progress(epoch, epochs, loss):
                    persist(epoch=epoch, epochs=epochs, loss=loss)
                result = train_enhancement(req.dataset_path, folder, epochs=req.epochs, batch_size=req.batch_size,
                                           learning_rate=req.learning_rate, device=req.device, cancel_event=event, on_progress=progress, warm_start=parent)
                validate_training_binding(binding)
                if event.is_set(): raise InterruptedError('Enhancement training cancelled')
                persist_model_binding(folder, binding)
                path = folder / "best_model.pt"
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                with _JOB_LOCK:
                    if event.is_set(): raise InterruptedError('Enhancement training cancelled')
                    _atomic(folder / 'job_receipt.json', json.dumps({
                        'job_id': folder.name, 'task': 'enhancement', 'status': 'completed',
                        'source_dataset_path': manifest['provenance']['source_dataset_path'],
                        'dataset_path': req.dataset_path, 'training_provenance': binding,
                        'checkpoint_sha256': digest,
                        **({'warm_start': parent.lineage()} if parent else {}),
                    }, ensure_ascii=False).encode())
                    persist(status="completed", model_sha256=digest, result=result)
                return {"job_id": folder.name, "model_sha256": record["model_sha256"], "result": result}
        except InterruptedError as exc:
            for name in ('best_model.pt', 'model_meta.json', 'metadata.json', 'job_receipt.json'):
                (folder / name).unlink(missing_ok=True)
            persist(status="stopped", error=str(exc))
            if not req.background: raise HTTPException(409, str(exc)) from exc
        except (ValueError, OSError, RuntimeError) as exc:
            for name in ('best_model.pt', 'model_meta.json', 'metadata.json', 'job_receipt.json'):
                (folder / name).unlink(missing_ok=True)
            persist(status="failed", error=str(exc))
            if not req.background: raise HTTPException(422, str(exc)) from exc
        finally:
            with _JOB_LOCK: _CANCEL_EVENTS.pop(key, None)
    # Write the journal separately; train_enhancement requires a new model directory.
    with _JOB_LOCK: _CANCEL_EVENTS[key] = event
    if req.background:
        persist()
        threading.Thread(target=execute, daemon=True, name=f"enhancement-{folder.name}").start()
        return JSONResponse({**record}, status_code=202)
    return execute()


@router.get('/warm-start-parents')
def warm_start_parents(dataset_path: str, request: Request):
    from backend.engine.specialized_warm_start import list_family_parents
    project = get_current_project(request)
    _root(request)
    try:
        manifest = load_enhancement_manifest(dataset_path)
        source = Path(manifest['provenance']['source_dataset_path']).resolve()
        if not project.get('source_dataset_dir') or source != Path(project['source_dataset_dir']).resolve():
            raise ValueError('Enhancement parent source differs from the active project')
        return list_family_parents(project['models_dir'], 'enhancement', source, dataset_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _job(request: Request, job_id: str) -> tuple[Path, dict]:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(422, "Invalid enhancement job ID")
    folder = _root(request) / job_id
    path = folder / "job.json"
    if folder.is_symlink() or path.is_symlink() or not path.is_file():
        raise HTTPException(404, "Enhancement job not found in this project")
    with _JOB_LOCK:
        record = json.loads(path.read_text())
        if record["status"] in {"queued", "running", "stopping"} and record["owner_instance"] != _PROCESS_INSTANCE:
            record.update(status="interrupted", error="Application stopped before training completed")
            _atomic(path, json.dumps(record, ensure_ascii=False).encode())
    return folder, record


@router.get("/jobs")
def jobs(request: Request):
    root = _root(request)
    records = [_job(request, path.parent.name)[1] for path in root.glob("*/job.json")]
    return {"jobs": sorted(records, key=lambda row: row["created_at"], reverse=True)}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    return _job(request, job_id)[1]


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, request: Request):
    folder, record = _job(request, job_id)
    with _JOB_LOCK:
        if record["status"] in {"queued", "running"}:
            event = _CANCEL_EVENTS.get(str(folder.resolve()))
            if event is not None:
                event.set(); record["status"] = "stopping"
                _atomic(folder / "job.json", json.dumps(record, ensure_ascii=False).encode())
    return record


@router.get("/models")
def models(request: Request):
    root = _root(request)
    records = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if folder.is_symlink() or len(folder.name) != 32 or any(c not in "0123456789abcdef" for c in folder.name):
            continue
        meta, checkpoint = folder / "model_meta.json", folder / "best_model.pt"
        if meta.is_symlink() or checkpoint.is_symlink() or not meta.is_file() or not checkpoint.is_file():
            continue
        try:
            data = json.loads(meta.read_text())
            journal = folder / 'job.json'
            if journal.is_file() and _job(request, folder.name)[1]['status'] != 'completed':
                continue
            if data.get("task") == "enhancement":
                records.append({"job_id": folder.name, "metadata": data, "model_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()})
        except (ValueError, OSError):
            continue
    return {"models": records}


@router.post("/evaluate")
def evaluate(req: Evaluate, request: Request):
    path = _checkpoint(request, req.job_id)
    try:
        result = evaluate_enhancement(path, req.dataset_path, split=req.split, device=req.device)
        from backend.engine.evaluation_history import archive_specialized_evaluation
        project = get_current_project(request)
        manifest = load_enhancement_manifest(req.dataset_path)
        result = archive_specialized_evaluation(project, path, Path(manifest['provenance']['source_dataset_path']),
                                                result, task='enhancement', dataset_path=req.dataset_path)
        folder = path.parent / "evaluations"
        folder.mkdir(exist_ok=True)
        # Keep the same immutable ID in the family view and unified history.
        (folder / f"{result['evaluation_id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/predict")
def predict(req: Predict, request: Request):
    path = _checkpoint(request, req.job_id)
    image_path = Path(req.image_path).expanduser().resolve()
    project = get_current_project(request)
    source = project.get("source_dataset_dir")
    if source and not image_path.is_relative_to(Path(source).resolve()):
        raise HTTPException(409, "Image does not belong to the selected project source")
    try:
        import base64
        with Image.open(image_path) as image:
            pixels = np.array(image.convert("RGB"), copy=True)
        enhanced = predict_enhancement(path, pixels, req.device)
        output = io.BytesIO()
        Image.fromarray(enhanced).save(output, format="PNG")
        return {"image_base64": base64.b64encode(output.getvalue()).decode(), "width": enhanced.shape[1],
                "height": enhanced.shape[0], "model_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "job_id": req.job_id}
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
