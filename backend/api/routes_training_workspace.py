"""Common first-use preparation and same-job reopen inventory."""
import sqlite3
from pathlib import Path
from typing import Literal
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from backend.api.routes_project import get_current_project
from backend.engine.training_workspace import model_readiness,persisted_task_rows,import_pretrained_weight,imported_weight

router=APIRouter(prefix='/api/training-workspace',tags=['training-workspace'])

class ReadinessRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task:str
    model:str
    device:Literal['cpu','mps','cuda']='cpu'
    pretrained_checkpoint:str|None=None
    compute_profile_id:str|None=None
    preset:Literal['fast','precision']='fast'
    config_overrides:dict=Field(default_factory=dict)
    family_dataset_path:str|None=None
    warm_start_job_id:str|None=None

def selected_profile(identifier):
    from backend.remote.profiles import get_profile_store
    return get_profile_store().get(identifier)

def probe_target(profile):
    from backend.remote.ssh_transport import SSHTransport
    return SSHTransport().probe(profile)


def input_readiness(project,task):
    if not project.get('source_dataset_dir') or task not in {'classification','segmentation','detection','anomaly'}:
        return None,[]
    from backend.engine.dataset_loaders import inspect_dataset,_classification_split_assignments
    from backend.engine.team_data import training_readiness
    try:
        review=training_readiness(project,Path(project['source_dataset_dir']))
        source=Path(project['source_dataset_dir']).resolve()
        assignments=_classification_split_assignments(source)
        if assignments is not None:
            from backend.engine.grouped_dataset_views import source_image_paths
            available={str(path) for path in source_image_paths(source,task)}
            missing=set(assignments)-available
            if missing:raise ValueError('저장된 분할의 원본 이미지가 없습니다. 데이터·분할을 다시 확인하세요.')
            counts={part:sum(partition==part for partition in assignments.values()) for part in ('train','val','test')}
        else:
            counts=dict(inspect_dataset(source,task).split_counts)
        actions=[] if review['ready'] else list(review['blockers'])
        if counts.get('train',0)==0:actions.append('현재 검수 정책·분할에서 학습 이미지가 없습니다. 학습 분할의 라벨을 검수하세요.')
        if counts.get('val',0)==0:actions.append('현재 검수 정책·분할에서 검증 이미지가 없습니다. 검증 분할의 라벨을 검수하세요.')
        return {'split_counts':counts,'review_eligibility_sha256':review['eligibility_sha256']},actions
    except (ValueError,OSError,RuntimeError) as exc:
        return {'split_counts':{}},[str(exc)]

def task_record(record):
    """Execution controls and saved model selection use separate exact identities."""
    launch=record.launch_spec or {}
    return {'kind':'training','job_id':record.job_id,'execution_job_id':record.job_id,
        'model_id':launch.get('local_model_id',record.job_id),'task':record.task,'status':record.status,'phase':record.phase,
        'source_dataset_path':record.source_dataset_path,'dataset_path':(record.dataset_binding or {}).get('family_dataset_path') or launch.get('family_dataset_path') or record.dataset_path,
        'training_provenance':record.dataset_binding or {},'compute_profile_id':record.remote_profile_id,
        'current_epoch':record.current_epoch,'total_epochs':record.total_epochs,'error':record.error}

@router.post('/readiness')
def readiness(body:ReadinessRequest,request:Request):
    project=get_current_project(request)
    from backend.engine.model_catalog import model_family_catalog
    family=next((row for row in model_family_catalog()['families'] if row['task']==body.task),None)
    allowed=set(family['architectures']) if family else set()
    if body.task=='anomaly':allowed.update(('dinov3_vits16','dinov3_vitb16','dinov3_vitl16'))
    if body.model not in allowed:raise HTTPException(422,'Choose a supported model in this family')
    actual_model=body.config_overrides.get('anomaly_backbone','dinov3_vits16') if body.task=='anomaly' and body.model=='dino_synthetic' else body.model
    checkpoint=body.pretrained_checkpoint or body.config_overrides.get('pretrained_checkpoint') or imported_weight(project,body.task,actual_model)
    inputs,input_actions=input_readiness(project,body.task)
    if body.compute_profile_id:
        profile=selected_profile(body.compute_profile_id)
        if profile is None:raise HTTPException(404,'Selected compute profile is unavailable')
        options=dict(body.config_overrides)
        key='model_name' if body.task=='segmentation' else 'anomaly_backbone' if body.task=='anomaly' and actual_model.startswith('dinov3_') else 'backbone'
        options[key]=actual_model
        if body.task=='anomaly' and actual_model.startswith('dinov3_'):options.setdefault('anomaly_method','dino_synthetic')
        if checkpoint:options['pretrained_checkpoint']=checkpoint
        probe=probe_target(profile);actions=list(input_actions);parent=None;dataset=None
        from backend.engine.model_execution import resolve_training_input,resolve_training_parent,remote_weight_readiness
        try:
            if body.family_dataset_path or body.warm_start_job_id:
                dataset=resolve_training_input(project,body.task,body.family_dataset_path)
            if body.warm_start_job_id:
                parent=resolve_training_parent(project,body.warm_start_job_id,body.task,dataset,body.preset,options)
        except (ValueError,OSError,RuntimeError,HTTPException) as exc:actions.append(str(exc))
        from backend.remote.ssh_transport import require_training_runtime
        try:
            require_training_runtime(probe,body.task,body.preset,options,warm_start=bool(body.warm_start_job_id))
            if profile.gpu_selector and (probe.get('checks') or {}).get('cuda_device_count',0)<1:raise ValueError('Selected GPU is unavailable on the server')
            if profile.distributed_processes>1:
                from backend.remote.distributed import validate_distributed_request
                validate_distributed_request(body.task,'cuda' if profile.gpu_selector else 'cpu',profile.distributed_processes,available_cuda=(probe.get('checks') or {}).get('cuda_device_count',0))
        except (ValueError,RuntimeError) as exc:actions.append(str(exc))
        try:
            weights=remote_weight_readiness(probe,body.task,actual_model,{**options,'preset':body.preset},checkpoint,parent)
            if weights['state']=='missing':actions.append('Selected model pretrained weights are missing: import an official checkpoint or prepare the selected server cache')
        except (ValueError,OSError,RuntimeError) as exc:
            weights={'state':'missing','content_verified':False};actions.append(str(exc))
        return {'task':body.task,'model':body.model,'ready':not actions,'target':{'kind':'server','id':profile.id,'name':profile.name},
            'runtime':{'available':not actions,'device':'cuda' if profile.gpu_selector else 'cpu','reason':' · '.join(actions)},
            'dependencies':{'missing':[]},'weights':weights,
            'next_actions':actions,'execution_verified':False,'training_started':False,'quality_approved':False,
            'selected_checkpoint_path':checkpoint,'prepared_dataset_path':str(dataset) if dataset else None,'probe':probe,'input':inputs}
    result=model_readiness(body.task,actual_model,body.device,checkpoint)
    from backend.engine.model_execution import resolve_training_input,resolve_training_parent
    try:
        if body.family_dataset_path or body.warm_start_job_id:
            dataset=resolve_training_input(project,body.task,body.family_dataset_path)
            result['prepared_dataset_path']=str(dataset)
            parent=resolve_training_parent(project,body.warm_start_job_id,body.task,dataset,body.preset,body.config_overrides)
            if parent:
                result['weights']={'state':'parent_verified','sha256':parent.checkpoint_sha256,'content_verified':False}
                result['next_actions']=[action for action in result['next_actions'] if action!='import_official_weights']
                result['ready']=result['runtime']['available'] and not result['dependencies']['missing']
    except (ValueError,OSError,RuntimeError,HTTPException) as exc:
        result['ready']=False;result['next_actions'].append(str(exc))
    if input_actions:
        result['ready']=False;result['next_actions'].extend(input_actions)
    return {**result,'selected_checkpoint_path':checkpoint,'target':{'kind':'local','id':None},'training_started':False,'input':inputs}

@router.get('/tasks')
def tasks(request:Request):
    project=get_current_project(request);models=Path(project['models_dir']);source=project.get('source_dataset_dir','');labelset=project.get('active_labelset_id','default')
    from backend.api import routes_training,routes_automated_training
    from backend.engine.shared_scheduler import shared_leases
    rows=[];errors=[]
    try:reserved={row['job_id'] for row in routes_training.training_job_manager._leases.list()}
    except (OSError,sqlite3.Error):reserved=None  # release is then never claimed
    for record in routes_training.training_job_manager.list_jobs():
        binding=record.dataset_binding or {}
        if not Path(record.output_dir).resolve().is_relative_to(models.resolve()) or record.source_dataset_path!=source or binding.get('labelset_id','default')!=labelset:continue
        rows.append({**task_record(record),'observation':routes_training._job_observation(record,reserved)})  # S1-04
    # Saved completion uses the existing hash/source verifier; reading does not launch work.
    for folder in models.glob('job_*'):
        if any(row.get('job_id')==folder.name for row in rows):continue
        record=routes_training._completed_receipt_record(folder.name,request)
        if record:rows.append({**task_record(record),'observation':routes_training._job_observation(record,reserved)})
    for row in persisted_task_rows(models,source,labelset):
        try:
            kind=row['kind'];identifier=row['job_id']
            if kind in ('ocr','rotation','defect-gan'):
                from backend.engine.specialized_training_jobs import read_job
                row={**read_job(models/row['task'],identifier),'kind':kind}
            elif kind=='enhancement':
                from backend.api.routes_enhancement import job_status
                row={**job_status(identifier,request),'kind':kind}
            elif kind=='rotated-detection':
                from backend.api.routes_rotated_detection import get_job
                row={**get_job(identifier,request),'kind':kind}
            # A verified remote record explicitly names the native model. Keep one
            # execution entry; never infer aliases from similar-looking IDs.
            if not any(item.get('model_id')==identifier and item.get('task')==row.get('task') for item in rows):
                rows.append({**row,'model_id':identifier,'execution_job_id':identifier})
        except (HTTPException,ValueError,OSError,KeyError) as exc:errors.append({'kind':row['kind'],'message':str(exc)})
    try:
        for row in routes_automated_training.jobs(request)['jobs']:
            if row.get('source_dataset_path')==source and (row.get('training_provenance') or {}).get('labelset_id','default')==labelset:rows.append({**row,'kind':'automated'})
    except (HTTPException,ValueError,OSError,KeyError) as exc:errors.append({'kind':'automated','message':str(exc)})
    from backend.engine.labeling_tasks import list_jobs as labeling_jobs
    for row in labeling_jobs(project):
        if row.get('labelset_id','default')!=labelset:continue
        rows.append({**row,'kind':'labeling-batch' if row.get('kind')=='candidate_batch' else 'labeling-feature',
            'job_id':row['id'],'task':'labeling','source_dataset_path':row['source_dataset_dir'],
            'training_provenance':{'labelset_id':row.get('labelset_id','default')},'cancel_supported':True})
    try:
        from backend.api.routes_inspections import list_runs
        for row in list_runs(request,source_folder=source)['runs']:
            rows.append({**row,'kind':'inspection','job_id':row['run_id'],'source_dataset_path':row['source_folder'],
                'compute_profile_id':(row.get('execution_config') or {}).get('compute_profile_id'),
                'scope_kind':'project','cancel_supported':False,'status_url':'/api/inspections/runs/'+row['run_id']})
    except (HTTPException,ValueError,OSError,KeyError) as exc:errors.append({'kind':'inspection','message':str(exc)})
    try:
        from backend.engine.product_delivery import optimization_records,package_library
        for row in optimization_records(project):
            receipt=(row.get('options') or {}).get('input_receipt') or {}
            if receipt.get('source_dataset_path')!=source:continue
            rows.append({**row,'kind':'optimization','task':'runtime_optimization','source_dataset_path':source,
                'scope_kind':'project','cancel_supported':True,'status_url':'/api/export/flow/optimization-jobs/'+row['job_id']})
        for row in package_library(project)['packages']:
            if not row.get('scope_matches',False):continue
            rows.append({**row,'kind':'export','job_id':row['package_id'],'task':row.get('recipe_task','export'),
                'status':'completed' if row.get('integrity')=='verified' else 'unverified','source_dataset_path':source,
                'scope_kind':'project','cancel_supported':False})
    except (ImportError,HTTPException,ValueError,OSError,KeyError,TypeError) as exc:errors.append({'kind':'delivery','message':str(exc)})
    # E08: data jobs share the same ledger and task center, scoped to their creator/project.
    try:
        from backend.api.routes_dataset_imports import _scope, _jobs, _backup_jobs
        from backend.engine.job_store import ledger
        context, project_key, _ = _scope(request)
        store = ledger()
        records = store.active() + store.ended('dataset_import', project_key, ('completed', 'failed', 'aborted', 'interrupted')) + store.ended('project_backup', project_key, ('completed', 'failed', 'aborted', 'interrupted'))
        for record in records:
            if record['project_key'] != project_key or record['actor_id'] != context.actor_id or record['kind'] not in ('dataset_import', 'project_backup'):
                continue
            view = _jobs(request).view(record['id'], project_key) if record['kind'] == 'dataset_import' else _backup_jobs().view(record['id'], project_key, context.actor_id)
            operation = view.get('operation', view)
            rows.append({**view, 'kind': record['kind'], 'status': view['state'], 'task': record['kind'],
                'scope_kind': 'project', 'source_dataset_path': source, 'phase': (view.get('progress') or {}).get('phase', view['state']),
                'data_operation': operation})
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        errors.append({'kind': 'data_operations', 'message': str(exc)})
    try:reservations=[{k:v for k,v in row.items() if k!='owner'} for row in shared_leases().list() if row.get('project_id')==project['id'] or any((item.get('job_id') or item.get('search_id'))==row['job_id'] for item in rows)]
    except (ValueError,OSError,sqlite3.Error) as exc:reservations=None;errors.append({'kind':'reservations','message':str(exc)})
    return {'tasks':rows,'reservations':reservations,'errors':errors,'source_dataset_path':source,'labelset_id':labelset}


class WeightImportRequest(ReadinessRequest):
    expected_sha256:str|None=None

@router.post('/import-weights')
def import_weights(body:WeightImportRequest,request:Request):
    if not body.pretrained_checkpoint:raise HTTPException(422,'Choose a pretrained file')
    readiness(body,request)
    try:return import_pretrained_weight(get_current_project(request),body.task,body.model,body.pretrained_checkpoint,body.expected_sha256)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc
