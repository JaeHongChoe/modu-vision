"""Owned patch preparation and the standard completed-checkpoint lifecycle."""
from pathlib import Path
import time
import uuid

from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field,ConfigDict

from backend.api.routes_project import get_current_project
from backend.engine.patch_preparation import prepare_patch_dataset
from backend.engine.patch_classification import load_patch_manifest

router=APIRouter(prefix='/api/patch-classification',tags=['patch-classification'])

class PrepareRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    patch_size:int=Field(256,ge=16,le=2048)
    stride:int=Field(128,ge=1,le=2048)
    normal_class:str=Field('OK',min_length=1,max_length=100)
    minimum_overlap:float=Field(.05,gt=0,le=1)

class TrainRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    dataset_path:str
    backbone:str='dinov3_vits16'
    pretrained_checkpoint:str|None=None
    pretrained_sha256:str|None=None
    epochs:int=Field(20,ge=1,le=500)
    batch_size:int=Field(8,ge=1,le=128)
    image_size:int=Field(256,ge=32,le=1024)
    learning_rate:float=Field(1e-4,gt=0,le=1)
    device:str='cpu'
    warm_start_job_id:str|None=None
    dataset_version_id:str|None=None

class EvaluateRequest(BaseModel):
    job_id:str
    force_recompute:bool=False


def _owned(project,supplied):
    requested=Path(supplied).expanduser();path=requested.resolve()
    root=Path(project['dataset_dir'])
    if requested.is_symlink() or root.is_symlink() or not path.is_relative_to(root.resolve()):
        raise ValueError('Patch prepared data must belong to the active project')
    manifest=load_patch_manifest(path)
    source=project.get('source_dataset_dir')
    if not source or manifest.provenance.get('source_dataset_path')!=str(Path(source).resolve()):
        raise ValueError('Patch input must match the active project source')
    return manifest

@router.post('/prepare')
def prepare(body:PrepareRequest,request:Request):
    project=get_current_project(request)
    if not project.get('source_dataset_dir'):raise HTTPException(409,'Select source data first')
    from backend.api.routes_dataset import _read_split_manifest
    source=Path(project['source_dataset_dir']).resolve()
    try:return prepare_patch_dataset(source,Path(project['dataset_dir'])/'patch'/uuid.uuid4().hex,
        assignments=_read_split_manifest(source),**body.model_dump())
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

@router.get('/manifest')
def manifest(dataset_path:str,request:Request):
    try:
        value=_owned(get_current_project(request),dataset_path)
        return {'dataset_path':str(value.root),'classes':value.classes,'normal_class':value.normal_class,
                'patch_size':value.patch_size,'stride':value.stride,'patch_count':len(value.patches),'provenance':value.provenance}
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

@router.get('/datasets')
def datasets(request:Request):
    project=get_current_project(request);rows=[]
    for path in sorted((Path(project['dataset_dir'])/'patch').glob('*/patches.json')):
        try:
            data=_owned(project,path.parent)
            rows.append({'dataset_path':str(data.root),'classes':data.classes,'normal_class':data.normal_class,
                         'patch_size':data.patch_size,'stride':data.stride,'patch_count':len(data.patches),'provenance':data.provenance})
        except (ValueError,OSError,KeyError,TypeError):continue
    return {'datasets':rows}

@router.post('/train')
def train(body:TrainRequest,request:Request):
    project=get_current_project(request)
    try:
        data=_owned(project,body.dataset_path)
        if body.backbone not in {'dinov3_vits16','dinov3_vitb16'}:raise ValueError('Patch training requires a supported DINOv3 pretrained backbone')
        from backend.api.routes_training import training_job_manager
        from backend.engine.training_provenance import bind_family_training
        from backend.engine.warm_start import architecture_for,resolve_warm_start_parent
        options={'backbone':body.backbone,'epochs':body.epochs,'batch_size':body.batch_size,
                 'image_size':[body.image_size,body.image_size],'learning_rate':body.learning_rate}
        from backend.engine.training_workspace import imported_weight
        prepared=body.pretrained_checkpoint or imported_weight(project,'patch_classification',body.backbone)
        if prepared:options['pretrained_checkpoint']=prepared
        if body.pretrained_sha256:options['pretrained_sha256']=body.pretrained_sha256
        source=project['source_dataset_dir'];parent=None
        if body.warm_start_job_id:
            parent=resolve_warm_start_parent(body.warm_start_job_id,Path(project['models_dir']),source,
                'patch_classification',architecture_for('patch_classification','fast',options))
            if tuple(data.classes)!=parent.classes:raise ValueError('Patch parent class mapping differs')
        binding=bind_family_training(project,data.root,'patch_classification',body.dataset_version_id)
        job_id=f'job_{int(time.time())}_{uuid.uuid4().hex[:6]}'
        output=Path(project['models_dir'])/job_id
        record=training_job_manager.start_job(job_id,'patch_classification',str(data.root),str(output),
            device=body.device,config_overrides=options,source_dataset_path=source,
            dataset_fingerprint=binding['dataset_fingerprint'],dataset_binding=binding,warm_start=parent)
        return {'job_id':job_id,'status':record.status,'dataset_path':str(data.root),'source_dataset_path':source,
                'training_provenance':binding}
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

@router.post('/evaluate')
def evaluate(body:EvaluateRequest,request:Request):
    project=get_current_project(request)
    from backend.api.routes_evaluation import _resolve_job_artifacts,run_or_load_evaluation
    out,checkpoint,meta,task,job,dataset=_resolve_job_artifacts(body.job_id,source_dataset_path=project.get('source_dataset_dir'))
    if task!='patch_classification':raise HTTPException(422,'Choose a completed patch classifier')
    try:_owned(project,str(dataset))
    except (ValueError,OSError) as exc:raise HTTPException(409,str(exc)) from exc
    return run_or_load_evaluation(job_id=job,dataset_path=str(dataset),force_recompute=body.force_recompute,
                                  source_dataset_path=project.get('source_dataset_dir'),source_task='patch_classification')
