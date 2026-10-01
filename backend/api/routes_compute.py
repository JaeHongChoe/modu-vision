"""Desktop-token-protected compute profile management and connection probe."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Response,Request
from pydantic import BaseModel, ConfigDict, Field
from pathlib import Path
from typing import Literal
import json

from backend.remote.profiles import ComputeProfile, get_profile_store
from backend.remote.ssh_transport import SSHTransport


router = APIRouter(prefix="/api/compute", tags=["compute"])


def _administrator(request):
    account=getattr(getattr(request,'state',None),'account_user',None)
    if account and not account['administrator']:raise HTTPException(403,'Server administrator permission required for SSH compute configuration')


def _project(request):
    from backend.api.routes_project import get_current_project
    return get_current_project(request)


def _owned(record,project):
    return record is not None and Path(record.output_dir).resolve().is_relative_to(Path(project['models_dir']).resolve())


def _row(record):
    return {'job_id':record.job_id,'execution_job_id':record.job_id,'model_id':(record.launch_spec or {}).get('local_model_id',record.job_id),'task':record.task,'operation':(record.launch_spec or {}).get('operation','train'),
            'status':record.status,'phase':record.phase,'compute_profile_id':record.remote_profile_id,
            'current_epoch':record.current_epoch,'total_epochs':record.total_epochs,'current_step':record.current_step,
            'total_steps':record.total_steps,'metrics':record.metrics,'best_metric':record.best_metric,'error':record.error,
            'submitted_at':record.start_time,'dataset_path':(record.dataset_binding or {}).get('family_dataset_path') or (record.launch_spec or {}).get('family_dataset_path') or record.dataset_path,'source_dataset_path':record.source_dataset_path,'training_provenance':record.dataset_binding or {}}


class ProfileInput(ComputeProfile):
    id: str = Field(default_factory=lambda: uuid4().hex)


class SelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compute_profile_id: str | None


@router.get("/profiles")
def list_profiles(request:Request=None) -> dict:
    _administrator(request)
    return {"profiles": [profile.model_dump() for profile in get_profile_store().list()]}


@router.post("/profiles", status_code=201)
def save_profile(profile: ProfileInput,request:Request=None) -> dict:
    _administrator(request)
    saved = get_profile_store().save(ComputeProfile.model_validate(profile.model_dump()))
    return {"profile": saved.model_dump()}


@router.delete("/profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: str,request:Request=None) -> Response:
    _administrator(request)
    if not get_profile_store().delete(profile_id):
        raise HTTPException(status_code=404, detail="Compute profile not found")
    return Response(status_code=204)


@router.get("/selection")
def get_selection(request:Request=None) -> dict:
    _administrator(request)
    return {"compute_profile_id": get_profile_store().get_selected()}


@router.put("/selection")
def set_selection(selection: SelectionInput,request:Request=None) -> dict:
    _administrator(request)
    try:
        selected = get_profile_store().set_selected(selection.compute_profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Compute profile not found") from None
    return {"compute_profile_id": selected}


@router.post("/profiles/{profile_id}/probe")
def probe_profile(profile_id: str,request:Request=None) -> dict:
    _administrator(request)
    profile = get_profile_store().get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Compute profile not found")
    result=SSHTransport().probe(profile)
    inventory=(result.get('checks') or {}).get('device_inventory',{})
    if inventory.get('devices'):
        from backend.engine.shared_scheduler import shared_leases
        try:shared_leases().configure_devices(f"ssh:{profile.ssh_target.rsplit('@',1)[-1].lower()}:{profile.ssh_port}",inventory['devices'])
        except ValueError as exc:result={**result,'ready':False,'message':str(exc)}
    return result


@router.get("/reservations")
def reservations(request:Request=None):
    from backend.engine.shared_scheduler import shared_leases
    import time
    rows = shared_leases().list()
    account=getattr(getattr(request,'state',None),'account_user',None)
    if account:
        project=_project(request)
        rows=[{key:value for key,value in row.items() if key!='owner'} for row in rows if row.get('project_id')==project['id']]
    return {"reservations": [{**row, "expired": row["expires"] < time.time(), "requires_reconciliation": bool(row["remote"] and (row["uncertain"] or row["expires"] < time.time()))} for row in rows]}


@router.get('/capabilities')
def capabilities():
    from backend.engine.automated_trials import _RUNNERS
    from backend.remote.distributed import SUPPORTED_TASKS
    return {'training_tasks':list(_RUNNERS),'distributed_tasks':sorted(SUPPORTED_TASKS),'labeling_provider':'foundation',
            'operations':['train','label','infer'],'sharing_requires':['observed_device_uuid','observed_memory_capacity','explicit_memory_budget'],
            'remote_model_prerequisites':{'label':'worker VISION_MASK_MODEL_DIR; text additionally VISION_GROUNDING_MODEL_DIR; examples additionally VISION_DINO_CHECKPOINT and VISION_DINO_SHA256'}}


@router.get('/targets')
def targets():
    return {'targets':[{'id':p.id,'name':p.name,'gpu_selector':p.gpu_selector,'memory_budget_mb':p.memory_budget_mb,
                        'allow_sharing':p.allow_sharing,'distributed_processes':p.distributed_processes} for p in get_profile_store().list()]}


@router.get('/devices')
def devices(request:Request):
    _administrator(request)
    from backend.engine.compute_inventory import device_inventory
    from backend.engine.shared_scheduler import shared_leases
    observed=device_inventory();leases=shared_leases()
    if observed['devices']:
        try:leases.configure_devices('local-compute',observed['devices'])
        except ValueError as exc:observed['prerequisite']=str(exc)
    return {'local':observed,'observed_remote':leases.devices()}


@router.get('/inputs')
def inputs(task:str,request:Request):
    project=_project(request);root=Path(project['dataset_dir'])
    names={'patch_classification':'patches.json','rotation':'rotation.json','ocr':'ocr.json','rotated_detection':'rotated_boxes.json','enhancement':'pairs.json','defect_gan':'defect_gan.json'}
    if task not in names:return {'inputs':[{'dataset_path':project.get('source_dataset_dir'),'name':'프로젝트 원본'}] if project.get('source_dataset_dir') else []}
    rows=[]
    if root.is_dir() and not root.is_symlink():
        for path in sorted(root.rglob(names[task])):
            if not path.is_symlink() and path.resolve().is_relative_to(root.resolve()):rows.append({'dataset_path':str(path.parent),'name':str(path.parent.relative_to(root))})
    return {'inputs':rows}


class ComputeJobInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task:Literal['classification','patch_classification','detection','segmentation','anomaly','rotation','ocr','rotated_detection','enhancement','defect_gan','labeling']
    operation:Literal['train','label']='train'
    dataset_path:str
    family_dataset_path:str|None=None
    compute_profile_id:str
    preset:Literal['fast','precision']='fast'
    device:Literal['cpu','cuda','cuda:0']='cuda:0'
    config_overrides:dict=Field(default_factory=dict)
    labeling:dict=Field(default_factory=dict)
    dataset_version_id:str|None=None
    warm_start_job_id:str|None=None


@router.post('/jobs',status_code=202)
def submit_job(body:ComputeJobInput,request:Request):
    project=_project(request);account=getattr(request.state,'account_user',None)
    if account:
        role=request.app.state.accounts.project_role(account['id'],project['id'])
        allowed={'owner','reviewer','trainer'}|({'labeler'} if body.operation=='label' else set())
        if role not in allowed:raise HTTPException(403,'This project role cannot submit this compute task')
    source=Path(body.dataset_path).expanduser().resolve()
    if not project.get('source_dataset_dir') or source!=Path(project['source_dataset_dir']).resolve():raise HTTPException(409,'Compute input must match the active project source')
    profile=get_profile_store().get(body.compute_profile_id)
    if profile is None:raise HTTPException(404,'Compute profile not found')
    if body.operation=='label' and body.task!='labeling' or body.operation=='train' and body.task=='labeling':raise HTTPException(422,'Labeling operation and task must be selected together')
    if body.operation=='label' and body.labeling.get('setup'):raise HTTPException(422,'Configure foundation model paths on the worker deployment')
    if body.operation=='label' and profile.distributed_processes>1:raise HTTPException(422,'Labeling uses independent allocated workers; distributed training is a separate mode')
    dataset=Path(body.family_dataset_path).expanduser().resolve() if body.family_dataset_path else source
    try:
        from backend.engine.training_provenance import bind_training_version,bind_family_training
        if body.task in {'ocr','rotated_detection'}:
            from backend.engine.prepared_family_datasets import resolve_family_dataset
            dataset=resolve_family_dataset(project,body.task,dataset).root
        specialized={'patch_classification','rotation','ocr','rotated_detection','enhancement','defect_gan'}
        binding=bind_family_training(project,dataset,body.task,body.dataset_version_id) if body.task in specialized else bind_training_version(project,source,body.dataset_version_id)
        label_baseline=None
        if body.operation=='label':
            from backend.engine.compute_label_results import capture_label_baseline
            label_baseline=capture_label_baseline(project,body.labeling)
        if body.task not in specialized and dataset!=source:raise ValueError('This task uses the registered project source directly')
        readiness=SSHTransport().probe(profile)
        from backend.remote.ssh_transport import require_training_runtime
        require_training_runtime(readiness,body.task,body.preset,body.config_overrides,warm_start=bool(body.warm_start_job_id))
        if body.warm_start_job_id and body.operation!='train':raise ValueError('A labeling job cannot select a training parent')
        from backend.engine.model_execution import resolve_training_parent
        parent=resolve_training_parent(project,body.warm_start_job_id,body.task,dataset,body.preset,body.config_overrides)
        observed=(readiness.get('checks') or {}).get('device_inventory',{}).get('devices',[])
        from backend.api.routes_training import training_job_manager
        if observed:training_job_manager._leases.configure_devices(training_job_manager._lease_host(profile),observed)
        if profile.allow_sharing and not observed:raise ValueError('GPU sharing requires probe-observed physical device capacity')
        if profile.distributed_processes>1:
            from backend.remote.distributed import validate_distributed_request
            validate_distributed_request(body.task,body.device,profile.distributed_processes,available_cuda=(readiness.get('checks') or {}).get('cuda_device_count',len(observed)))
        from backend.engine.dataset_fingerprint import fingerprint_dataset
        from backend.remote.coordinator import make_remote_runner
        native_id=uuid4().hex;identifier='job_'+native_id
        native_family=body.task in specialized-{'patch_classification'}
        output=Path(project['models_dir'])/(body.task if native_family else '')/(native_id if native_family else identifier)
        launch={'preparation':'none','operation':body.operation,'config_overrides':body.config_overrides,'device':body.device,
                'dataset_binding':binding,'family_dataset_path':str(dataset),'project_id':project['id'],'account_id':account['id'] if account else None,'labeling':label_baseline['worker_options'] if label_baseline else body.labeling}
        if parent:launch['warm_start']={**vars(parent),'checkpoint_path':str(parent.checkpoint_path),'classes':list(parent.classes)}
        if native_family:launch['local_model_id']=native_id
        if label_baseline:launch.update(label_baseline=label_baseline,label_images=[row['relative_path'] for row in label_baseline['images']])
        record=training_job_manager.start_remote_job(job_id=identifier,task=body.task,dataset_path=str(dataset),output_dir=str(output),
            remote_profile_id=profile.id,profile=profile,remote_runner=make_remote_runner(profile,launch),preset=body.preset,
            source_dataset_path=str(source),dataset_fingerprint=fingerprint_dataset(source),launch_spec=launch,dataset_binding=binding,warm_start=parent)
        return _row(record)
    except (ValueError,OSError,RuntimeError) as exc:raise HTTPException(422,str(exc)) from exc


@router.get('/jobs')
def jobs(request:Request):
    from backend.api.routes_training import training_job_manager
    project=_project(request)
    return {'jobs':[_row(row) for row in training_job_manager.list_jobs() if _owned(row,project)]}


def _job(job_id,request):
    from backend.api.routes_training import training_job_manager
    record=training_job_manager.get_job(job_id)
    if not _owned(record,_project(request)):raise HTTPException(404,'Compute job not found in the active project')
    return record,training_job_manager


def _control_role(record,request):
    account=getattr(request.state,'account_user',None)
    if not account:return
    role=request.app.state.accounts.project_role(account['id'],_project(request)['id'])
    allowed={'owner','reviewer','trainer'}|({'labeler'} if (record.launch_spec or {}).get('operation')=='label' else set())
    if role not in allowed:raise HTTPException(403,'This project role cannot control this compute task')


@router.get('/jobs/{job_id}')
def job_status(job_id:str,request:Request):return _row(_job(job_id,request)[0])


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id:str,request:Request):
    record,manager=_job(job_id,request)
    _control_role(record,request)
    if not manager.abort_job(job_id):raise HTTPException(409,'Compute job is already terminal')
    return _row(record)


@router.post('/jobs/{job_id}/reconnect')
def reconnect_job(job_id:str,request:Request):
    record,manager=_job(job_id,request)
    _control_role(record,request)
    if record.status!='disconnected':raise HTTPException(409,'Only a disconnected remote job can reconnect')
    manager.reconnect_remote_job(job_id)
    return _row(record)


@router.get('/jobs/{job_id}/results')
def job_results(job_id:str,request:Request):
    record,_=_job(job_id,request)
    if record.status!='completed':raise HTTPException(409,'Compute job has no completed verified results')
    from backend.engine.compute_label_results import verified_result
    return verified_result(record)[0]


@router.post('/jobs/{job_id}/label-proposals')
def label_proposals(job_id:str,request:Request):
    record,_=_job(job_id,request);_control_role(record,request)
    if record.status!='completed' or (record.launch_spec or {}).get('operation')!='label':raise HTTPException(409,'A completed remote labeling job is required')
    from backend.engine.compute_label_results import import_label_proposals
    return import_label_proposals(_project(request),record)


class ComputePredictionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    image_path:str
    threshold:float=Field(.5,ge=0,le=1,allow_inf_nan=False)


@router.post('/jobs/{job_id}/predict')
def predict_job(job_id:str,body:ComputePredictionInput,request:Request):
    record,_=_job(job_id,request);_control_role(record,request)
    if record.status!='completed' or (record.launch_spec or {}).get('operation','train')!='train':raise HTTPException(409,'Choose a completed remote model')
    from backend.api.routes_label_suggestions import _image_path
    from backend.remote.operations import remote_job_context,run_remote_inference
    try:
        image=_image_path(_project(request),body.image_path)
        local_id=(record.launch_spec or {}).get('local_model_id',record.job_id)
        context=remote_job_context(Path(record.output_dir),local_id)
        if context is None:raise ValueError('Verified remote model context is unavailable')
        result,_=run_remote_inference(context,image,body.threshold,image.stem,device=(record.launch_spec or {}).get('device'))
        return {**result,'task':record.task,'model_id':local_id,'compute_profile_id':record.remote_profile_id}
    except (ValueError,OSError,RuntimeError) as exc:raise HTTPException(422,str(exc)) from exc
