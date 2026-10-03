"""Project-scoped single-object rotated-box candidates and cancellable local training."""

from __future__ import annotations

import hashlib
import base64
import io
import json
import math
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
    source_dataset_path: str = ''
    device: str = 'cpu'
    training_provenance: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    epochs_completed: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)
    warm_start: Any = field(default=None, repr=False)

    def summary(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "status": self.status,
                "epochs_completed": self.epochs_completed, "total_epochs": self.total_epochs,
                "started_at": self.started_at, "result": self.result, "error": self.error,
                "training_provenance": self.training_provenance,
                "dataset_path":str(self.dataset_path),"source_dataset_path":self.source_dataset_path,
                "device":self.device,
                "warm_start": self.warm_start.lineage() if self.warm_start else None}


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
    direction_deg: float | None = Field(None,ge=0,lt=360,allow_inf_nan=False)


class ManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str = Field(min_length=1)
    samples: list[RotatedSampleInput] = Field(min_length=1)

class PrepareRequest(BaseModel):
    source_dataset_path: str
    samples: list[RotatedSampleInput] = Field(min_length=1)


class FitBoxRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    image_path:str
    mode:Literal['center','face','irregular']
    points:list[tuple[float,float]]=Field(min_length=3,max_length=128)
    source_sha256:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')


def _fitting_source(image_path,request):
    project=get_current_project(request);configured=project.get('source_dataset_dir')
    path=Path(image_path).expanduser()
    if (not configured or not path.is_absolute() or path.is_symlink() or not path.is_file()
            or not path.resolve().is_relative_to(Path(configured).resolve())
            or any(parent.is_symlink() for parent in path.parents if parent!=Path(configured).parent)):
        raise ValueError('Rotated fitting image must be a regular file in the active source dataset')
    return path


@router.get('/fit-source')
def fit_source(image_path:str,request:Request):
    try:
        path=_fitting_source(image_path,request)
        with Image.open(path) as opened:
            size=list(opened.size);preview=opened.convert('RGB');preview.thumbnail((800,600),Image.Resampling.BILINEAR)
        stream=io.BytesIO();preview.save(stream,format='PNG')
        return {'source_size':size,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'preview_data_url':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()}
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@router.post('/fit-box')
def fit_box(req:FitBoxRequest,request:Request):
    try:
        from backend.engine.rotated_detection import box_from_polygon,_valid_box,_points
        path=_fitting_source(req.image_path,request)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if req.source_sha256 and req.source_sha256!=digest:raise ValueError('Fitting source changed after preview')
        with Image.open(path) as opened:width,height=opened.size
        if any(not math.isfinite(v) for point in req.points for v in point):raise ValueError('Fitting coordinates must be finite')
        if any(x<0 or x>width or y<0 or y>height for x,y in req.points):raise ValueError('Fitting point escaped source image bounds')
        if req.mode=='irregular':box=box_from_polygon(req.points)
        else:
            if len(req.points)!=3:raise ValueError('Center and face modes require exactly three points')
            first,second,third=req.points
            dx,dy=second[0]-first[0],second[1]-first[1];length=math.hypot(dx,dy)
            if length<=0:raise ValueError('Fitting edge must have positive length')
            nx,ny=-dy/length,dx/length;depth=(third[0]-first[0])*nx+(third[1]-first[1])*ny
            if req.mode=='center':cx,cy=first;box_width=2*length;box_height=2*abs(depth)
            else:cx=(first[0]+second[0])/2+nx*depth/2;cy=(first[1]+second[1])/2+ny*depth/2;box_width=length;box_height=abs(depth)
            box={'cx':cx,'cy':cy,'width':box_width,'height':box_height,'angle_deg':(math.degrees(math.atan2(dy,dx))+90)%180-90}
        box=_valid_box(box,width,height)
        return {'box':box,'polygon':_points(box).tolist(),'source_size':[width,height],
                'source_sha256':digest,'mode':req.mode}
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


class TrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str = Field(min_length=1)
    epochs: int = Field(default=10, ge=1, le=200)
    batch_size: int = Field(default=8, ge=1, le=64)
    image_size: int = Field(default=64, ge=16, le=512)
    learning_rate: float = Field(default=1e-3, gt=0, le=1)
    device:Literal['cpu','mps','cuda']='cpu'
    warm_start_job_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    dataset_path: str = Field(min_length=1)
    split: Literal["val", "test"] = "test"
    device:Literal['cpu','mps','cuda']='cpu'


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    image_path: str = Field(min_length=1)
    device:Literal['cpu','mps','cuda']='cpu'


def _project_source(request: Request, requested: str) -> Path:
    project = get_current_project(request)
    configured = project.get("source_dataset_dir")
    if not isinstance(configured, str) or not configured:
        raise HTTPException(status_code=422, detail="Select this project's source dataset before rotated training")
    try:
        from backend.engine.prepared_family_datasets import resolve_family_dataset
        return resolve_family_dataset(project,'rotated_detection',requested).root
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


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
        from backend.engine.shared_scheduler import compute_lease_scope
        with compute_lease_scope(job.job_id,options.device):
            result = train_rotated_detector(
                job.dataset_path, job.output_dir, epochs=options.epochs,
                batch_size=options.batch_size, image_size=options.image_size,
                learning_rate=options.learning_rate, device=options.device, cancel_event=job.cancel, warm_start=job.warm_start,
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
                from backend.engine.specialized_training_jobs import persist_training_configuration
                persist_training_configuration(job.output_dir,options.model_dump(exclude={'dataset_path','warm_start_job_id'}))
                checksum=hashlib.sha256((job.output_dir/'best_model.pt').read_bytes()).hexdigest()
                meta_path=job.output_dir/'model_meta.json';meta=json.loads(meta_path.read_text(encoding='utf-8'))
                meta.update(checkpoint_sha256=checksum,source_dataset_path=job.source_dataset_path,dataset_path=str(job.dataset_path),
                            training_config=options.model_dump(exclude={'dataset_path','warm_start_job_id'}))
                meta_path.write_text(json.dumps(meta),encoding='utf-8')
                receipt={'job_id':job.job_id,'task':'rotated_detection','status':'completed',
                    'source_dataset_path':job.source_dataset_path,'dataset_path':str(job.dataset_path),
                    'training_provenance':job.training_provenance,'checkpoint_sha256':checksum,
                    'dataset_fingerprint':job.training_provenance['dataset_fingerprint']}
                if job.warm_start:
                    receipt['warm_start'] = job.warm_start.lineage()
                (job.output_dir/'job_receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
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
        "provenance":manifest.provenance,
        "samples": [
            {"image": image, "source_sha256": rows[0].source_sha256, "split": rows[0].split, **({"objects": [{"label": row.label,"box": row.box,**({"direction_deg":row.direction_deg} if row.direction_deg is not None else {})} for row in rows]} if manifest.version==2 else {"label": rows[0].label,"box": rows[0].box,**({"direction_deg":rows[0].direction_deg} if rows[0].direction_deg is not None else {})})}
            for image, rows in _manifest_groups(manifest).items()
        ],
    }


@router.post("/manifest")
def save_manifest(req: ManifestRequest, request: Request):
    project=get_current_project(request);configured=project.get('source_dataset_dir')
    if not configured:raise HTTPException(422,'Select original source first')
    source=Path(configured).resolve()
    try:
        from backend.engine.prepared_family_datasets import prepare_family_dataset,resolve_family_dataset
        if Path(req.dataset_path).resolve()!=source:resolve_family_dataset(project,'rotated_detection',req.dataset_path)
        manifest=prepare_family_dataset('rotated_detection',source,Path(project['dataset_dir'])/'rotated_detection'/uuid.uuid4().hex,[row.model_dump(exclude_none=True) for row in req.samples])
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _manifest_result(manifest)

@router.post('/prepare')
def prepare(req:PrepareRequest,request:Request):
    project=get_current_project(request);configured=project.get('source_dataset_dir')
    if not configured or Path(configured).resolve()!=Path(req.source_dataset_path).resolve():raise HTTPException(422,'Rotated original source must match active project')
    try:
        from backend.engine.prepared_family_datasets import prepare_family_dataset
        data=prepare_family_dataset('rotated_detection',configured,Path(project['dataset_dir'])/'rotated_detection'/uuid.uuid4().hex,[row.model_dump(exclude_none=True) for row in req.samples])
        return _manifest_result(data)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

@router.get('/datasets')
def datasets(request:Request):
    from backend.engine.prepared_family_datasets import list_prepared_family_datasets
    return {'datasets':list_prepared_family_datasets(get_current_project(request),'rotated_detection')}


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
    from backend.engine.specialized_warm_start import resolve_family_parent
    _models_root(request)
    try:
        parent = resolve_family_parent(project['models_dir'], req.warm_start_job_id, 'rotated_detection', project['source_dataset_dir'], source, req.model_dump()) if req.warm_start_job_id else None
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
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
                       source, root / job_id, req.epochs,source_dataset_path=project['source_dataset_dir'],device=req.device,training_provenance=binding, warm_start=parent)
        _JOBS[_key(job)] = job
        _write_state(job)
        context=copy_context()
        job.thread = threading.Thread(target=lambda:context.run(_run_job,job,req),
                                      name=f"rotated-{job.job_id[:8]}", daemon=True)
        job.thread.start()
        return job.summary()


@router.get('/warm-start-parents')
def warm_start_parents(dataset_path: str, request: Request, image_size: int = 64):
    from backend.engine.specialized_warm_start import list_family_parents
    source = _project_source(request, dataset_path)
    project = get_current_project(request)
    _models_root(request)
    try:
        return list_family_parents(project['models_dir'], 'rotated_detection', project['source_dataset_dir'], source, {'image_size': image_size})
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/jobs')
def list_training_jobs(request:Request):
    root=_models_root(request);rows=[]
    for directory in sorted(root.iterdir()) if root.is_dir() else []:
        try:
            if _JOB_ID.fullmatch(directory.name):rows.append(get_job(directory.name,request))
        except HTTPException:continue
    return {'jobs':rows}

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
        # No process of this app owns the job any more (the app restarted). As for every other family it is interrupted,
        # not failed: no candidate was registered and the same settings can run again.
        state = {**state, "status": "interrupted", "error": "Application stopped before training completed"}
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
                       "dataset_path":meta.get('dataset_path'),"source_dataset_path":meta.get('source_dataset_path'),
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
        result = evaluate_rotated_detector(checkpoint, source, split=req.split, device=req.device)
        project=get_current_project(request)
        return archive_specialized_evaluation(project, checkpoint, project['source_dataset_dir'],
            result, task='rotated_detection',dataset_path=str(source))
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
        result = predict_rotated_box(checkpoint, image, device=req.device)
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
