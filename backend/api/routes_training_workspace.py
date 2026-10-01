"""Common first-use preparation and same-job reopen inventory."""
from pathlib import Path
from typing import Literal
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict
from backend.api.routes_project import get_current_project
from backend.engine.training_workspace import model_readiness,persisted_task_rows,import_pretrained_weight,imported_weight

router=APIRouter(prefix='/api/training-workspace',tags=['training-workspace'])

class ReadinessRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task:str
    model:str
    device:Literal['cpu','mps','cuda']='cpu'
    pretrained_checkpoint:str|None=None

@router.post('/readiness')
def readiness(body:ReadinessRequest,request:Request):
    project=get_current_project(request)
    from backend.engine.model_catalog import model_family_catalog
    family=next((row for row in model_family_catalog()['families'] if row['task']==body.task),None)
    allowed=set(family['architectures']) if family else set()
    if body.task=='anomaly':allowed.update(('dinov3_vits16','dinov3_vitb16','dinov3_vitl16'))
    if body.model not in allowed:raise HTTPException(422,'Choose a supported model in this family')
    checkpoint=body.pretrained_checkpoint or imported_weight(project,body.task,body.model)
    return {**model_readiness(body.task,body.model,body.device,checkpoint),'selected_checkpoint_path':checkpoint}

@router.get('/tasks')
def tasks(request:Request):
    project=get_current_project(request);models=Path(project['models_dir']);source=project.get('source_dataset_dir','');labelset=project.get('active_labelset_id','default')
    from backend.api import routes_training,routes_automated_training
    from backend.engine.shared_scheduler import shared_leases
    rows=[];errors=[]
    for record in routes_training.training_job_manager.list_jobs():
        binding=record.dataset_binding or {}
        if not Path(record.output_dir).resolve().is_relative_to(models.resolve()) or record.source_dataset_path!=source or binding.get('labelset_id','default')!=labelset:continue
        rows.append({'kind':'training','job_id':record.job_id,'task':record.task,'status':record.status,'phase':record.phase,
            'source_dataset_path':record.source_dataset_path,'training_provenance':binding,'compute_profile_id':record.remote_profile_id,
            'current_epoch':record.current_epoch,'total_epochs':record.total_epochs,'error':record.error})
    # Saved completion uses the existing hash/source verifier; reading does not launch work.
    for folder in models.glob('job_*'):
        if any(row.get('job_id')==folder.name for row in rows):continue
        record=routes_training._completed_receipt_record(folder.name,request)
        if record:rows.append({'kind':'training','job_id':record.job_id,'task':record.task,'status':record.status,'source_dataset_path':source,
            'training_provenance':record.dataset_binding,'compute_profile_id':record.remote_profile_id,'current_epoch':record.current_epoch,'total_epochs':record.total_epochs})
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
            rows.append(row)
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
    try:reservations=[{k:v for k,v in row.items() if k!='owner'} for row in shared_leases().list() if row.get('project_id')==project['id'] or any((item.get('job_id') or item.get('search_id'))==row['job_id'] for item in rows)]
    except (ValueError,OSError) as exc:reservations=None;errors.append({'kind':'reservations','message':str(exc)})
    return {'tasks':rows,'reservations':reservations,'errors':errors,'source_dataset_path':source,'labelset_id':labelset}


class WeightImportRequest(ReadinessRequest):
    expected_sha256:str|None=None

@router.post('/import-weights')
def import_weights(body:WeightImportRequest,request:Request):
    if not body.pretrained_checkpoint:raise HTTPException(422,'Choose a pretrained file')
    readiness(body,request)
    try:return import_pretrained_weight(get_current_project(request),body.task,body.model,body.pretrained_checkpoint,body.expected_sha256)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc
