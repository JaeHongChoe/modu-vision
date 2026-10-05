"""Owned patch preparation and the standard completed-checkpoint lifecycle."""
from pathlib import Path
from typing import Literal
import base64
import hashlib
import io
import logging
import uuid

from fastapi import APIRouter,HTTPException,Request,Query
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
    queue:bool=True
    priority:int=Field(0,ge=-10,le=10)
    max_runtime_s:float|None=Field(None,strict=True,gt=0,le=7*24*3600,allow_inf_nan=False)

class EvaluateRequest(BaseModel):
    job_id:str
    force_recompute:bool=False


class PatchRecipe(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Literal[1]=1
    mode:Literal['max','vote','ng_count']='max'
    threshold:float=Field(.5,ge=0,le=1,strict=True,allow_inf_nan=False)
    vote_fraction:float=Field(.5,gt=0,le=1,strict=True,allow_inf_nan=False)
    minimum_ng_count:int=Field(1,ge=1,le=100000,strict=True)
    threshold_comparison:Literal['greater_than_or_equal']='greater_than_or_equal'

class PredictRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:str
    image_path:str
    device:Literal['cpu','mps','cuda']='cpu'
    recipe:PatchRecipe=Field(default_factory=PatchRecipe)

class RecipeFlowRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:str
    recipe:PatchRecipe=Field(default_factory=PatchRecipe)


def _completed_patch(project,job_id):
    from backend.api.routes_evaluation import _resolve_job_artifacts
    out,checkpoint,meta,task,job,dataset=_resolve_job_artifacts(job_id,source_dataset_path=project.get('source_dataset_dir'),source_task='patch_classification')
    if task!='patch_classification' or not out.resolve().is_relative_to(Path(project['models_dir']).resolve()):
        raise ValueError('Choose a completed patch candidate in the active project')
    _owned(project,str(dataset))
    return checkpoint


def _thumbnail(image):
    from PIL import Image
    image.thumbnail((768,768),Image.Resampling.LANCZOS)
    stream=io.BytesIO();image.save(stream,format='PNG')
    return base64.b64encode(stream.getvalue()).decode()

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


@router.get('/sample')
def sample(dataset_path:str,request:Request,sample_index:int=Query(0,ge=0,le=100000)):
    try:
        data=_owned(get_current_project(request),dataset_path)
        if sample_index>=len(data.patches):raise ValueError('Patch sample index is outside the manifest')
        row=data.patches[sample_index];raw=row.image_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row.source_sha256:raise ValueError('Patch sample bytes changed')
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as opened:image=opened.convert('RGB')
        source=data.provenance.get('source_map',{}).get(row.image,{})
        return {'sample_index':sample_index,'sample_count':len(data.patches),'image':row.image,
                'source_relative_path':source.get('source_relative_path'),'source_sha256':row.source_sha256,
                'box':list(row.box),'label':row.label,'split':row.split,'source_size':list(image.size),
                'patch_size':data.patch_size,'stride':data.stride,'preview_only':True,
                'original_base64':_thumbnail(image.copy()),'patch_base64':_thumbnail(image.crop(row.box))}
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@router.post('/predict')
def predict(body:PredictRequest,request:Request):
    try:
        project=get_current_project(request);checkpoint=_completed_patch(project,body.job_id)
        supplied=Path(body.image_path).expanduser();source=Path(project['source_dataset_dir']).resolve();path=supplied.resolve()
        if supplied.is_symlink() or not path.is_file() or not path.is_relative_to(source):raise ValueError('Patch prediction input must belong to the active source')
        raw=path.read_bytes()
        from PIL import Image
        import numpy as np
        from backend.engine.patch_classification import predict_patch_classification,patch_score_preview
        from backend.engine.native_patches import validate_image_size,DEFAULT_MAX_IMAGE_PIXELS
        with Image.open(io.BytesIO(raw)) as opened:
            validate_image_size(*opened.size,DEFAULT_MAX_IMAGE_PIXELS);rgb=np.asarray(opened.convert('RGB'))
        result=predict_patch_classification(checkpoint,rgb,device=body.device,source_id=str(path),recipe=body.recipe.model_dump())
        result['source_sha256']=hashlib.sha256(raw).hexdigest()
        for row in result['patches']:row['source_sha256']=result['source_sha256']
        return {**result,**patch_score_preview(rgb,result['patches'])}
    except (ValueError,OSError,RuntimeError) as exc:raise HTTPException(422,str(exc)) from exc


@router.post('/recipe-flow')
def recipe_flow(body:RecipeFlowRequest,request:Request):
    try:
        _completed_patch(get_current_project(request),body.job_id)
        from backend.engine.flowchart_engine import get_single_segmentation_flowchart
        from backend.engine.patch_classification import apply_patch_recipe
        flow=get_single_segmentation_flowchart(job_id=body.job_id);flow.name='원본 패치 분류 판정'
        inspect=next(n for n in flow.nodes if n.data.node_type=='inspection');inspect.data.task='patch_classification';inspect.data.label='원본 패치 분류';inspect.data.crop_padding=0
        return apply_patch_recipe(flow,body.recipe.model_dump()).model_dump()
    except (ValueError,OSError,RuntimeError) as exc:raise HTTPException(422,str(exc)) from exc

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
    ledger=None
    try:
        data=_owned(project,body.dataset_path)
        if body.backbone not in {'dinov3_vits16','dinov3_vitb16'}:raise ValueError('Patch training requires a supported DINOv3 pretrained backbone')
        from backend.api.routes_training import training_job_manager,TrainingStartRequest,_reserve_training_job,_LEDGER_ERRORS
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
        # Bind idempotency to the client's selected settings, before inferred
        # weights, family-version writes, a job directory or worker launch.
        selected={key:value for key,value in body.model_dump().items()
            if key in {'backbone','epochs','batch_size','image_size','learning_rate','pretrained_checkpoint','pretrained_sha256'} and value is not None}
        admission=TrainingStartRequest(task='patch_classification',dataset_path=str(data.root),
            device=body.device,config_overrides=selected,warm_start_job_id=body.warm_start_job_id,
            dataset_version_id=body.dataset_version_id,queue=body.queue,priority=body.priority,max_runtime_s=body.max_runtime_s)
        try:
            ledger,replay=_reserve_training_job(request,admission,data.root,Path(project['models_dir']))
            if replay is not None:return replay
            if body.max_runtime_s is not None:
                ledger.store.set_budget(ledger.job_id,{'max_runtime_s':body.max_runtime_s,'max_attempts':1})
        except _LEDGER_ERRORS as exc:raise HTTPException(503,'Patch admission or runtime budget could not be recorded; no worker was launched') from exc
        binding=bind_family_training(project,data.root,'patch_classification',body.dataset_version_id)
        job_id=ledger.job_id;output=Path(project['models_dir'])/job_id
        record=training_job_manager.start_job(job_id,'patch_classification',str(data.root),str(output),
            device=body.device,config_overrides=options,source_dataset_path=source,
            dataset_fingerprint=binding['dataset_fingerprint'],dataset_binding=binding,warm_start=parent,
            ledger=ledger,queue_when_busy=body.queue,priority=body.priority,
            budget={'max_runtime_s':body.max_runtime_s,'max_attempts':1} if body.max_runtime_s is not None else None)
        response={'job_id':job_id,'status':record.status,'dataset_path':str(data.root),'source_dataset_path':source,
                'training_provenance':binding}
        ledger.store.set_response(job_id,response)
        return response
    except Exception as exc:
        if ledger is not None:
            try:
                if ledger.store.get(ledger.job_id).state=='accepted':
                    ledger.finished('failed',{'message':str(exc.detail if isinstance(exc,HTTPException) else exc)})
            except _LEDGER_ERRORS:
                # Keep the original refusal; an unavailable store is not proof
                # of a recorded terminal state or permission to launch again.
                logging.getLogger(__name__).warning('Patch admission cleanup could not be recorded for %s',ledger.job_id)
        if isinstance(exc,(ValueError,OSError)):raise HTTPException(422,str(exc)) from exc
        raise

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
