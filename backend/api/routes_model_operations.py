"""Project-scoped operations policies, journals and owned background workers."""
from contextvars import copy_context
import os
from pathlib import Path
import threading
from typing import Literal
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,model_validator
from backend.api.routes_project import get_current_project
from backend.engine.model_operations import OperationsStore,OperationsBusy,PolicyRevisionConflict,configure_program,run_cycle

router=APIRouter(prefix='/api/model-operations',tags=['model-operations'])
_EVENTS={};_LOCK=threading.RLock()

class OperationsPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task:Literal['classification','patch_classification','detection','segmentation','anomaly','ocr','rotated_detection','rotation','enhancement','defect_gan']='classification'
    parent_job_id:str=Field(min_length=1,max_length=100)
    family_dataset_path:str|None=None
    reviewer:str=Field(min_length=1,max_length=100)
    process_existing:bool=False
    auto_label:bool=False
    confidence_threshold:float=Field(default=.95,ge=.5,le=1,allow_inf_nan=False)
    label_device:str='cpu'
    auto_retrain:bool=False
    require_label_review:bool=True
    training_device:str='cpu'
    preset:Literal['fast','precision']='fast'
    budget:dict=Field(default_factory=lambda:{'max_trials':1,'max_total_epochs':2,'max_seconds':600})
    base_config:dict=Field(default_factory=dict)
    epochs_per_trial:int=Field(default=2,ge=1,le=500)
    auto_approve:bool=False
    auto_deploy:bool=False
    approval_policy_authorized:bool=False
    pipeline_id:str|None=None
    pipeline_version_id:str|None=Field(default=None,pattern=r'^[0-9a-f]{32}$')
    inference_device:str='cpu'
    minimum_sample_count:int=Field(default=8,ge=8,le=1000000)
    minimum_metrics:dict[str,float]=Field(default_factory=dict)
    maximum_metrics:dict[str,float]=Field(default_factory=dict)
    new_data_validation_fraction:float=Field(default=.2,ge=0,le=.5,allow_inf_nan=False)
    watch_interval_seconds:float=Field(default=30,ge=5,le=86400,allow_inf_nan=False)
    @model_validator(mode='after')
    def activation_authorization(self):
        from backend.api.routes_automated_training import Budget
        self.budget=Budget.model_validate(self.budget).model_dump()
        if (self.auto_approve or self.auto_deploy) and not self.approval_policy_authorized:raise ValueError('Explicit automatic approval policy authorization is required')
        if self.auto_deploy and not self.auto_approve:raise ValueError('Automatic deployment requires automatic quality approval')
        if self.auto_approve and self.task=='defect_gan':raise ValueError('Generator outputs need explicit image review before composition or training adoption')
        return self

class RunRequest(BaseModel):
    background:bool=True


class LegacyImpactEntry(BaseModel):
    issue: str
    reason: str
    source_ids: dict[str, str | None]
    lineage: dict[str, str | None]
    quality_cause: Literal['not_established']
    scope_matches: bool | None


class LegacyRecoveryAction(BaseModel):
    action: str
    reason: str
    source_ids: dict[str, str | None]
    requires_human_review: Literal[True]
    automatic: Literal[False]


class LegacyImpactReport(BaseModel):
    schema_version: Literal[1]
    project_id: str | None
    affected: list[LegacyImpactEntry]
    unaffected: list[LegacyImpactEntry]
    unknown: list[LegacyImpactEntry]
    next_action: list[LegacyRecoveryAction]
    quality_approved: Literal[False]
    live_eligibility_checked: Literal[False]
    limits: str


def project(request):
    current=get_current_project(request)
    if not current:raise HTTPException(409,'Open a project first')
    return dict(current)


def _store(request):
    current=project(request);store=OperationsStore(current['project_dir'])
    key=str(Path(current['project_dir']).resolve())
    with _LOCK:active=_EVENTS.get(key)
    for cycle in store.history():
        if cycle['status']=='running' and not active:
            import psutil
            pid=cycle.get('owner_pid')
            if pid and pid!=os.getpid():
                try:
                    owner=psutil.Process(pid);arguments=owner.cmdline()
                    matches_time=abs(owner.create_time()-cycle.get('owner_created_at',0))<.1
                    owned_worker=('backend.engine.operations_worker' in arguments and '--project-dir' in arguments and arguments[arguments.index('--project-dir')+1]==str(Path(current['project_dir']).resolve()))
                    if matches_time and owned_worker:continue
                except (psutil.Error,ValueError,IndexError):pass
            cycle.update(status='interrupted',error='Owner stopped before the cycle completed; start a new cycle to recover')
            store.save(cycle)
    return current,store,key

@router.get('')
def state(request:Request):
    current,store,key=_store(request)
    from backend.engine.operations_worker import watcher_state
    with _LOCK:active=key in _EVENTS
    from backend.engine.workflow_impact import legacy_impact
    return {'policy':store.policy(),'cycles':store.history(),'watcher':watcher_state(current),'active':active,
            'legacy_impact': legacy_impact(current)}


@router.get('/legacy-impact', response_model=LegacyImpactReport)
def legacy_impact_report(request: Request):
    from backend.engine.workflow_impact import legacy_impact
    try: return legacy_impact(project(request))
    except (ValueError, OSError) as exc: raise HTTPException(422, str(exc)) from exc

@router.put('/policy')
def policy(payload:OperationsPolicy,request:Request):
    try:return configure_program(project(request),payload.model_dump())
    except (OperationsBusy,PolicyRevisionConflict) as exc:
        detail={'code':exc.code,'message':str(exc)}
        if isinstance(exc,PolicyRevisionConflict):
            detail.update(expected_revision=exc.expected_revision,current_revision=exc.current_revision)
        raise HTTPException(409,detail=detail) from exc
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc

@router.post('/run')
def run(payload:RunRequest,request:Request):
    current,store,key=_store(request)
    from backend.engine.operations_worker import watcher_state
    if watcher_state(current).get('running'):raise HTTPException(409,'Stop the project watcher before running a manual cycle')
    event=threading.Event()
    with _LOCK:
        if key in _EVENTS:raise HTTPException(409,'This project already has an operations cycle')
        _EVENTS[key]=('starting',event)
    def execute():
        try:return run_cycle(current,event)
        finally:
            with _LOCK:_EVENTS.pop(key,None)
    if payload.background:
        context=copy_context();threading.Thread(target=lambda:context.run(execute),daemon=True,name='model-operations').start()
        return JSONResponse({'status':'queued'},status_code=202)
    try:return execute()
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc

@router.post('/cancel')
def cancel(request:Request):
    current,store,key=_store(request)
    with _LOCK:active=_EVENTS.get(key)
    if active:active[1].set()
    from backend.engine.operations_worker import stop_watcher
    watcher=stop_watcher(current)
    return {'status':'cancelling' if active else 'idle','watcher':watcher}

@router.post('/watch/start')
def watch_start(request:Request):
    current,store,key=_store(request)
    with _LOCK:
        if key in _EVENTS:raise HTTPException(409,'Wait for the active cycle before starting the watcher')
    if not store.policy():raise HTTPException(409,'Configure a policy first')
    from backend.engine.operations_worker import start_watcher
    try:return start_watcher(current)
    except (ValueError,OSError) as exc:raise HTTPException(409,str(exc)) from exc

@router.post('/watch/stop')
def watch_stop(request:Request):
    from backend.engine.operations_worker import stop_watcher
    return stop_watcher(project(request))


@router.get('/models')
def models(request:Request):
    current=project(request)
    from backend.engine.model_operations import _checkpoint
    root=Path(current['models_dir']);items=[]
    if root.is_symlink():raise HTTPException(409,'Model store is linked')
    import json
    from backend.engine.checkpoint_paths import completed_job_receipt
    for metadata_path in root.glob('**/model_meta.json'):
        if metadata_path.is_symlink():continue
        try:
            meta=json.loads(metadata_path.read_text(encoding='utf-8'));identifier=metadata_path.parent.name;task=meta.get('task')
            checkpoint=_checkpoint(current,task,identifier)
            receipt=completed_job_receipt(checkpoint.parent) or {}
            items.append({'job_id':identifier,'task':task,'family_dataset_path':(meta.get('dataset_path') or receipt.get('dataset_path')) if task in ('patch_classification','ocr','rotated_detection','enhancement','rotation','defect_gan') else None,
                          'retrainable':isinstance(meta.get('training_config'),dict),'checkpoint_sha256':__import__('hashlib').sha256(checkpoint.read_bytes()).hexdigest()})
        except (ValueError,OSError,KeyError,TypeError):continue
    return {'models':items}
