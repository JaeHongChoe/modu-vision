"""Project-scoped saved GAN candidates; generation never edits training data."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal
import base64
import json
import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import JSONResponse
from backend.engine.specialized_training_jobs import start_job,require_training_source,read_job,list_jobs,cancel_job

from backend.api.routes_project import get_current_project
from backend.engine.specialized_models import require_completed_checkpoint
from backend.engine.defect_gan import (
    generate_defect_candidates, load_defect_gan_manifest,
    train_defect_gan, write_defect_gan_manifest,
)


router = APIRouter(prefix="/api/defect-gan", tags=["defect-gan"])
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")


class DefectCropRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(min_length=1)
    bbox: list[int] = Field(min_length=4, max_length=4)
    split: Literal["train", "val", "test"]


class GANManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str = Field(min_length=1)
    samples: list[DefectCropRow] = Field(min_length=1)


class GANTrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str = Field(min_length=1)
    epochs: int = Field(default=20, ge=1, le=500)
    batch_size: int = Field(default=8, ge=2, le=128)
    seed: int = 0
    base_channels: int = Field(default=16, ge=8, le=128)
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    background: bool = False


class GANGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    count: int = Field(default=8, ge=1, le=20)
    seed: int = 0
    device: Literal["cpu", "cuda", "mps"] = "cpu"


def _models_root(request: Request) -> Path:
    project = get_current_project(request)
    project_dir = Path(project["project_dir"]).resolve()
    models_dir = Path(project["models_dir"])
    if models_dir.is_symlink() or models_dir.resolve() != project_dir / "models":
        raise HTTPException(status_code=422, detail="Project model storage is invalid")
    root = models_dir / "defect_gan"
    if root.is_symlink():
        raise HTTPException(status_code=422, detail="Defect GAN model storage is invalid")
    return root


def _checkpoint(request: Request, job_id: str) -> Path:
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=422, detail="Invalid GAN job ID")
    job_dir = _models_root(request) / job_id
    path = job_dir / "best_model.pt"
    if job_dir.is_symlink() or path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail="GAN checkpoint not found in active project")
    try:require_completed_checkpoint(path)
    except (ValueError,OSError) as exc:raise HTTPException(409,str(exc)) from exc
    return path


@router.post("/manifest")
def create_manifest(req: GANManifestRequest, request: Request):
    get_current_project(request)
    try:
        path = write_defect_gan_manifest(req.dataset_path, [row.model_dump() for row in req.samples])
        manifest = load_defect_gan_manifest(req.dataset_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"manifest_path": str(path), "manifest_sha256": sha256(path.read_bytes()).hexdigest(),
            "sample_count": len(manifest["samples"]),
            "samples": manifest["samples"],
            "split_counts": {split: sum(row["split"] == split for row in manifest["samples"])
                             for split in ("train", "val", "test")}}


@router.get("/manifest")
def inspect_manifest(dataset_path: str, request: Request):
    get_current_project(request)
    try:
        path = Path(dataset_path).expanduser().resolve() / "defect_gan.json"
        manifest = load_defect_gan_manifest(dataset_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"manifest_path": str(path), "manifest_sha256": sha256(path.read_bytes()).hexdigest(),
            "sample_count": len(manifest["samples"]), "samples": manifest["samples"],
            "split_counts": {split: sum(row["split"] == split for row in manifest["samples"])
                             for split in ("train", "val", "test")}}


def _family_digest(source):
    load_defect_gan_manifest(source)
    return sha256((source/'defect_gan.json').read_bytes()).hexdigest()


@router.post("/train")
def train(req: GANTrainRequest, request: Request):
    project=get_current_project(request)
    try:source=require_training_source(project,req.dataset_path)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    output=_models_root(request)/uuid.uuid4().hex
    try:
        result=start_job(project=project,task='defect_gan',source=source,output=output,options=req,
            runner=lambda event,progress,device:train_defect_gan(source,output,epochs=req.epochs,batch_size=req.batch_size,seed=req.seed,base_channels=req.base_channels,device=device,cancel_event=event,on_progress=progress),family_digest=lambda:_family_digest(source))
        return JSONResponse(result,status_code=202) if req.background else result
    except InterruptedError as exc:raise HTTPException(409,str(exc)) from exc
    except (ValueError,OSError,RuntimeError) as exc:raise HTTPException(422,str(exc)) from exc


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
    for directory in sorted(root.iterdir()):
        if directory.is_symlink() or not directory.is_dir() or not _JOB_ID.fullmatch(directory.name):
            continue
        checkpoint, metadata = directory / "best_model.pt", directory / "model_meta.json"
        if not checkpoint.is_file() or not metadata.is_file() or checkpoint.is_symlink() or metadata.is_symlink():
            continue
        try:
            require_completed_checkpoint(checkpoint)
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            digest = sha256(checkpoint.read_bytes()).hexdigest()
        except (OSError, ValueError):
            continue
        if meta.get("model_kind") != "dcgan_defect_crop" or meta.get("checkpoint_sha256") != digest:
            continue
        models.append({"job_id": directory.name, "checkpoint_sha256": digest,
                       "sample_count": meta.get("sample_count"), "epochs": meta.get("epochs"),
                       "quality_status": "unvalidated"})
    return {"models": models}


@router.post("/generate")
def generate(req: GANGenerateRequest, request: Request):
    project = get_current_project(request)
    checkpoint = _checkpoint(request, req.job_id)
    review_root = Path(project["project_dir"]).resolve() / "synthetic_review"
    if review_root.is_symlink() or (review_root / req.job_id).is_symlink():
        raise HTTPException(status_code=422, detail="Synthetic review storage is invalid")
    output = review_root / req.job_id / uuid.uuid4().hex
    try:
        result = generate_defect_candidates(checkpoint, output, count=req.count,
                                            seed=req.seed, device=req.device)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for item in result["candidates"]:
        item["preview_data_url"] = "data:image/png;base64," + base64.b64encode(Path(item["path"]).read_bytes()).decode("ascii")
    return {"job_id": req.job_id, "review_dir": str(output), **result}


class CandidateReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    candidate_id: str=Field(pattern=r'^candidate_[0-9]{4}$')
    decision: Literal['adopt','reject']
    label: str | None=None
    reviewer: str=Field(min_length=1,max_length=100)
    reason: str=Field(min_length=2,max_length=2000)


class AdoptRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id: str=Field(pattern=r'^[0-9a-f]{32}$')
    review_dir: str
    decisions: list[CandidateReview]=Field(min_length=1,max_length=20)


@router.post('/adopt')
def adopt(req: AdoptRequest, request: Request):
    from backend.engine.defect_gan import adopt_reviewed_candidates
    project=get_current_project(request)
    _checkpoint(request,req.job_id)
    review_root=Path(project['project_dir']).resolve()/'synthetic_review'/req.job_id
    review=Path(req.review_dir)
    if review.is_symlink() or review.parent.resolve()!=review_root.resolve() or not review.is_dir():
        raise HTTPException(status_code=422,detail='Review directory must belong to the active project and generator')
    if project.get('task')!='classification' or not project.get('source_dataset_dir'):
        raise HTTPException(status_code=422,detail='Select a classification project with labeled real train/validation data before adopting generated crops')
    output=Path(project['dataset_dir'])/'synthetic_adoptions'/uuid.uuid4().hex
    try:
        return adopt_reviewed_candidates(review,project['source_dataset_dir'],output,[d.model_dump() for d in req.decisions])
    except (ValueError,OSError,KeyError,TypeError) as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


class GANEvaluateRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id: str=Field(pattern=r'^[0-9a-f]{32}$')
    dataset_path: str
    split: Literal['val','test']='test'
    count: int=Field(default=8,ge=1,le=20)
    seed: int=0


@router.post('/evaluate')
def evaluate(req: GANEvaluateRequest, request: Request):
    from backend.engine.defect_gan import evaluate_defect_generator
    try:
        from backend.engine.evaluation_history import archive_specialized_evaluation
        project=get_current_project(request);checkpoint=_checkpoint(request,req.job_id)
        result=evaluate_defect_generator(checkpoint,req.dataset_path,split=req.split,count=req.count,seed=req.seed)
        return archive_specialized_evaluation(project,checkpoint,project.get('source_dataset_dir') or req.dataset_path,
            result,task='defect_gan',dataset_path=req.dataset_path)
    except (ValueError,OSError,KeyError,RuntimeError) as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


class GANExportRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id: str=Field(pattern=r'^[0-9a-f]{32}$')


@router.post('/export')
def export(req: GANExportRequest, request: Request):
    from backend.engine.gan_package_runtime import build_generator_package
    project=get_current_project(request)
    checkpoint=_checkpoint(request,req.job_id)
    output=Path(project['project_dir'])/'exports'/'gan_generation'/f'{req.job_id}_{uuid.uuid4().hex}'
    try:
        package=build_generator_package(checkpoint,output)
        return {'status':'exported','package_path':str(package),'generator_sha256':sha256(checkpoint.read_bytes()).hexdigest(),'workflow':'generation_review_adoption','quality_status':'unvalidated'}
    except (ValueError,OSError) as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@router.get('/reviews')
def list_reviews(request: Request):
    project=get_current_project(request);root=Path(project['project_dir'])/'synthetic_review'
    result=[]
    if not root.is_dir() or root.is_symlink():return {'reviews':[]}
    for job in root.iterdir():
        if not _JOB_ID.fullmatch(job.name) or job.is_symlink() or not job.is_dir():continue
        for review in job.iterdir():
            if not _JOB_ID.fullmatch(review.name) or review.is_symlink() or not review.is_dir():continue
            manifest=review/'review_manifest.json'
            if manifest.is_symlink() or not manifest.is_file():continue
            try:
                value=json.loads(manifest.read_text());rows=value['candidates']
                result.append({'job_id':job.name,'review_id':review.name,'review_dir':str(review.resolve()),'candidate_count':len(rows),'unreviewed_count':sum(row.get('status')=='synthetic_unreviewed' for row in rows),'modified_at':manifest.stat().st_mtime})
            except (OSError,ValueError,KeyError,TypeError):continue
    return {'reviews':sorted(result,key=lambda row:row['modified_at'],reverse=True)}


@router.get('/reviews/{job_id}/{review_id}')
def open_review(job_id:str,review_id:str,request:Request):
    project=get_current_project(request);_checkpoint(request,job_id)
    if not _JOB_ID.fullmatch(review_id):raise HTTPException(status_code=422,detail='Invalid review ID')
    root=Path(project['project_dir']).resolve()/'synthetic_review'/job_id/review_id
    manifest=root/'review_manifest.json'
    if root.is_symlink() or manifest.is_symlink() or not manifest.is_file():raise HTTPException(status_code=404,detail='Review group is unavailable')
    try:
        result=json.loads(manifest.read_text())
        for row in result['candidates']:
            path=Path(row['path'])
            if path.is_symlink() or path.parent.resolve()!=root.resolve() or sha256(path.read_bytes()).hexdigest()!=row['sha256']:
                raise ValueError('Reviewed generated candidate hash changed')
            row['preview_data_url']='data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode('ascii')
        return {'job_id':job_id,'review_dir':str(root.resolve()),**result}
    except (OSError,ValueError,KeyError,TypeError) as exc:raise HTTPException(status_code=422,detail=str(exc)) from exc
