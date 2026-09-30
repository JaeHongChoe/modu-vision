"""Project-owned learned Rotation preparation, jobs and deployment artifacts."""
from __future__ import annotations
import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import uuid

import numpy as np
from PIL import Image
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from backend.api.routes_project import get_current_project
from backend.engine.rotation import (prepare_rotation_dataset, load_rotation_manifest, train_rotation,
    evaluate_rotation_checkpoint, predict_rotation_array, export_rotation_package)
from backend.engine.specialized_models import resolve_specialized_checkpoint
from backend.engine.specialized_training_jobs import start_job, list_jobs, read_job, cancel_job

router = APIRouter(prefix='/api/rotation', tags=['rotation'])


class Row(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image: str
    correction_deg: float = Field(ge=-180, le=180, allow_inf_nan=False)
    split: Literal['train','val','test']
    source_sha256: str | None = None


class PrepareRequest(BaseModel):
    source_dataset_path: str
    samples: list[Row] = Field(min_length=1)


class TrainRequest(BaseModel):
    dataset_path: str
    epochs: int = Field(default=20,ge=1,le=500)
    batch_size: int = Field(default=8,ge=1,le=128)
    image_size: int = Field(default=64,ge=16,le=512)
    width: Literal[8,16,32] = 16
    learning_rate: float = Field(default=1e-3,gt=0,le=1,allow_inf_nan=False)
    seed: int = 17
    device: Literal['cpu','cuda','mps'] = 'cpu'
    background: bool = False
    warm_start_job_id: str | None = None


class EvaluateRequest(BaseModel):
    job_id: str
    dataset_path: str
    split: Literal['val','test'] = 'test'
    device: str = 'cpu'


class PredictRequest(BaseModel):
    job_id: str
    image_path: str
    device: str = 'cpu'
    include_aligned: bool = False


class ExportRequest(BaseModel):
    job_id: str


def _root(request): return Path(get_current_project(request)['models_dir']) / 'rotation'


def _owned_dataset(project, supplied):
    path = Path(supplied).expanduser()
    if path.is_symlink() or not path.resolve().is_relative_to(Path(project['dataset_dir']).resolve()): raise ValueError('Rotation prepared data must belong to the active project')
    manifest = load_rotation_manifest(path)
    source = project.get('source_dataset_dir')
    if not source or manifest.provenance.get('source_dataset_path') != str(Path(source).resolve()): raise ValueError('Rotation prepared input must match the active project source')
    return manifest


def _checkpoint(request, job_id):
    project = get_current_project(request)
    try: return resolve_specialized_checkpoint(project['models_dir'], job_id, 'rotation', project.get('source_dataset_dir'))[0]
    except (ValueError,OSError,RuntimeError,KeyError) as exc: raise HTTPException(404,str(exc)) from exc


@router.post('/prepare')
def prepare(req: PrepareRequest, request: Request):
    project = get_current_project(request); source = project.get('source_dataset_dir')
    if not source or Path(source).resolve() != Path(req.source_dataset_path).resolve(): raise HTTPException(409,'Rotation source must be the active project source')
    try:
        output = Path(project['dataset_dir']) / 'rotation' / uuid.uuid4().hex
        manifest = prepare_rotation_dataset(source, output, [r.model_dump(exclude_none=True) for r in req.samples])
        return {'dataset_path':str(output),'provenance':manifest.provenance,'sample_count':len(manifest.samples)}
    except (ValueError,OSError) as exc: raise HTTPException(422,str(exc)) from exc


@router.get('/manifest')
def manifest(dataset_path: str, request: Request):
    try:
        loaded = _owned_dataset(get_current_project(request), dataset_path)
        return {'dataset_path':str(loaded.root),'provenance':loaded.provenance,'samples':[{'image':r.image,'correction_deg':r.correction_deg,'split':r.split,'source_sha256':r.source_sha256} for r in loaded.samples]}
    except (ValueError,OSError) as exc: raise HTTPException(422,str(exc)) from exc


@router.get('/datasets')
def datasets(request: Request):
    project=get_current_project(request);rows=[]
    for path in sorted((Path(project['dataset_dir'])/'rotation').glob('*/rotation.json')):
        try:
            loaded=_owned_dataset(project,path.parent)
            rows.append({'dataset_path':str(loaded.root),'provenance':loaded.provenance,'sample_count':len(loaded.samples)})
        except (ValueError,OSError,KeyError,TypeError):continue
    return {'datasets':rows}


@router.post('/train')
def train(req: TrainRequest, request: Request):
    project = get_current_project(request)
    try:
        loaded = _owned_dataset(project, req.dataset_path); source = project['source_dataset_dir']
        from backend.engine.specialized_warm_start import resolve_family_parent
        parent = resolve_family_parent(project['models_dir'], req.warm_start_job_id, 'rotation', source, loaded.root, req.model_dump()) if req.warm_start_job_id else None
        output = _root(request) / uuid.uuid4().hex
        result = start_job(project=project,task='rotation',source=source,family_dataset=loaded.root,output=output,options=req,warm_start=parent,
            family_digest=lambda:load_rotation_manifest(loaded.root).provenance['dataset_sha256'],
            runner=lambda event,progress,device:train_rotation(loaded.root,output,epochs=req.epochs,batch_size=req.batch_size,image_size=req.image_size,
                width=req.width,learning_rate=req.learning_rate,seed=req.seed,device=device,cancel_event=event,on_progress=progress,warm_start=parent))
        return JSONResponse(result,status_code=202) if req.background else result
    except (ValueError,OSError,RuntimeError,InterruptedError) as exc: raise HTTPException(422,str(exc)) from exc


@router.get('/warm-start-parents')
def parents(dataset_path: str, request: Request, image_size: int = 64, width: int = 16):
    project=get_current_project(request)
    try:
        prepared = _owned_dataset(project,dataset_path)
        from backend.engine.specialized_warm_start import list_family_parents
        return list_family_parents(project['models_dir'],'rotation',project['source_dataset_dir'],prepared.root,{'image_size':image_size,'width':width})
    except (ValueError,OSError) as exc: raise HTTPException(422,str(exc)) from exc


@router.get('/jobs')
def jobs(request: Request): return {'jobs':list_jobs(_root(request))}


@router.get('/jobs/{job_id}')
def job(job_id: str, request: Request):
    try: return read_job(_root(request),job_id)
    except (ValueError,OSError) as exc: raise HTTPException(404,str(exc)) from exc


@router.post('/jobs/{job_id}/cancel')
def cancel(job_id: str, request: Request):
    try: return cancel_job(_root(request),job_id)
    except (ValueError,OSError) as exc: raise HTTPException(404,str(exc)) from exc


@router.get('/models')
def models(request: Request):
    rows=[]
    for checkpoint in sorted(_root(request).glob('*/best_model.pt')):
        try:
            valid=_checkpoint(request,checkpoint.parent.name)
            rows.append({'job_id':checkpoint.parent.name,'checkpoint_path':str(valid),'metadata':json.loads(valid.with_name('model_meta.json').read_text())})
        except HTTPException: continue
    return {'models':rows}


@router.post('/evaluate')
def evaluate(req: EvaluateRequest, request: Request):
    project=get_current_project(request); checkpoint=_checkpoint(request,req.job_id)
    try:
        prepared=_owned_dataset(project,req.dataset_path)
        from backend.engine.evaluation_history import archive_specialized_evaluation
        result=evaluate_rotation_checkpoint(checkpoint,prepared.root,split=req.split,device=req.device)
        return archive_specialized_evaluation(project,checkpoint,project['source_dataset_dir'],result,task='rotation',dataset_path=str(prepared.root))
    except (ValueError,OSError,RuntimeError) as exc: raise HTTPException(422,str(exc)) from exc


@router.post('/predict')
def predict(req: PredictRequest, request: Request):
    project=get_current_project(request); checkpoint=_checkpoint(request,req.job_id)
    path=Path(req.image_path).expanduser()
    if path.is_symlink() or not path.is_file() or not project.get('source_dataset_dir') or not path.resolve().is_relative_to(Path(project['source_dataset_dir']).resolve()): raise HTTPException(422,'Rotation input must belong to the active source')
    try:
        raw=path.read_bytes()
        with Image.open(BytesIO(raw)) as opened: rgb=np.asarray(opened.convert('RGB'))
        result=predict_rotation_array(checkpoint,rgb,device=req.device)
        image=result.pop('aligned_image'); result['transform']=result['transform'].tolist();result['source_sha256']=sha256(raw).hexdigest()
        if req.include_aligned:
            stream=BytesIO();Image.fromarray(image).save(stream,format='PNG');result['aligned_image_base64']=base64.b64encode(stream.getvalue()).decode()
        return result
    except (ValueError,OSError,RuntimeError) as exc: raise HTTPException(422,str(exc)) from exc


@router.post('/export')
def export(req: ExportRequest, request: Request):
    project=get_current_project(request);checkpoint=_checkpoint(request,req.job_id)
    try:
        output=Path(project['reports_dir'])/'rotation_exports'/uuid.uuid4().hex
        exported=export_rotation_package(checkpoint,output)
        return {'package_dir':str(exported),'format':'torchscript','checkpoint_sha256':sha256(checkpoint.read_bytes()).hexdigest()}
    except (ValueError,OSError,RuntimeError) as exc: raise HTTPException(422,str(exc)) from exc
