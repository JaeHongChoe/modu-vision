"""Persisted project-bound quick/search/parent-config training API."""
from contextvars import copy_context
import json
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict, model_validator

from backend.api.routes_project import get_current_project
from backend.engine.automated_trials import run_automated_training, read_search, _write, _RUNNERS

router=APIRouter(prefix='/api/automated-training',tags=['automated-training'])
_EVENTS={};_LOCK=threading.RLock()
_PROCESS_INSTANCE=uuid.uuid4().hex


class Budget(BaseModel):
    model_config=ConfigDict(extra='forbid')
    max_trials:int=Field(default=4,ge=1,le=32)
    max_total_epochs:int=Field(default=8,ge=1,le=512)
    max_memory_mb:int|None=Field(default=None,ge=1,le=1048576)
    max_seconds:float=Field(default=600,gt=0,le=86400,allow_inf_nan=False)


class StartRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task:str='classification'
    dataset_path:str
    family_dataset_path:str|None=None
    preset:Literal['fast','precision']='fast'
    device:str='cpu'
    compute_profile_id:str|None=None
    seed:int=Field(default=0,strict=True,ge=0,le=2147483647)
    reuse_search_id:str|None=None
    mode:Literal['quick','search','fast_retrain']='search'
    budget:Budget=Field(default_factory=Budget)
    search_space:dict=Field(default_factory=dict)
    base_config:dict=Field(default_factory=dict)
    epochs_per_trial:int=Field(default=2,ge=1,le=500)
    objective:Literal['val_loss','loss_latency']='val_loss'
    latency_weight:float=Field(default=0,ge=0,allow_inf_nan=False)
    parent_job_id:str|None=None
    dataset_version_id:str|None=None
    background:bool=True

    @model_validator(mode='after')
    def validate_budget_relationship(self):
        from backend.engine.automated_trials import validated_budget
        validated_budget(self.budget.model_dump(), self.epochs_per_trial)
        return self


@router.get('/capabilities')
def capabilities():
    return {'tasks':{task:{'architectures':list(spec.architectures),'metric_key':spec.metric_key,'direction':spec.direction,
            'search_defaults':spec.search_defaults or {'architectures':list(spec.architectures),'learning_rates':[3e-4,1e-3],
                'weight_decays':[1e-4,.01],'image_sizes':[256],'batch_sizes':[8],'augmentation_profiles':['none','industrial']},
            'prepared_input':task in ('patch_classification','rotation','enhancement','ocr','rotated_detection','defect_gan'),
            'architecture_key':spec.architecture_key or ('model_name' if task=='segmentation' else 'backbone')} for task,spec in _RUNNERS.items()},
            'augmentation_profiles':['none','photometric','industrial'],'modes':['quick','search','fast_retrain']}


@router.post('/start')
def start(req:StartRequest,request:Request):
    project=get_current_project(request);source=Path(req.dataset_path).expanduser().resolve()
    account=getattr(request.state,'account_user',None)
    if account and request.app.state.accounts.project_role(account['id'],project['id']) not in {'owner','reviewer','trainer'}:
        raise HTTPException(403,'This project role cannot submit automated training')
    if req.task not in _RUNNERS:raise HTTPException(422,'No measured trial runner registered for this task')
    if not project.get('source_dataset_dir') or source!=Path(project['source_dataset_dir']).resolve():raise HTTPException(409,'Automated training source must match the active project')
    from backend.engine.training_provenance import bind_training_version,bind_family_training
    dataset=Path(req.family_dataset_path).expanduser() if req.family_dataset_path else source
    # Reuse selects an existing immutable input, rather than silently creating
    # a different version ID for the same bytes. The binding below still checks
    # current labels, splits, source and team-data policy before any worker.
    version = req.dataset_version_id
    if req.reuse_search_id and version is None:
        try:
            previous = read_search(Path(project['models_dir']), req.reuse_search_id)
            if (previous.get('task') != req.task
                    or Path(previous.get('source_dataset_path') or previous['dataset_path']).resolve() != source):
                raise ValueError('Reusable search belongs to a different source or task')
            version = previous['training_provenance']['dataset_version_id']
            if not version: raise ValueError('Reusable search has no immutable training version')
        except (KeyError, TypeError, OSError, ValueError) as exc:
            raise HTTPException(422, f'Cannot bind reusable search: {exc}') from exc
    try:
        if req.task in ('ocr','rotated_detection'):
            from backend.engine.prepared_family_datasets import resolve_family_dataset
            dataset=resolve_family_dataset(project,req.task,dataset).root
        if req.task in ('patch_classification','rotation','enhancement','ocr','rotated_detection','defect_gan'):
            binding=bind_family_training(project,dataset,req.task,version)
        elif req.family_dataset_path and dataset!=source:
            raise ValueError('This task trains directly from the registered project source')
        else:binding=bind_training_version(project,source,version)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc
    try:
        from backend.engine.automated_trials import validate_trial_controls
        validate_trial_controls(req.task,req.preset,req.base_config)
        if req.base_config.get('resume_checkpoint'):
            raise ValueError('Use direct training exact resume; AutoDL candidates require separate new model outputs')
        from backend.engine.automated_trials import _space
        next(_space(req.task,req.search_space,req.base_config,req.mode))
        if req.compute_profile_id:
            from backend.remote.profiles import get_profile_store
            profile=get_profile_store().get(req.compute_profile_id)
            if profile is None:raise ValueError('Selected compute profile is unavailable')
            from backend.engine.remote_automated_trials import validate_remote_search
            validate_remote_search(profile,req.task,req.preset,req.device,req.base_config,req.search_space,req.budget.model_dump(),warm_start=bool(req.parent_job_id))
    except (ValueError,OSError,RuntimeError) as exc:raise HTTPException(422,str(exc)) from exc
    owner={'project_id':project['id'],'account_id':account['id'] if account else None}
    identifier=uuid.uuid4().hex;models=Path(project['models_dir']);event=threading.Event();key=(str(models.resolve()),identifier)
    submission={'search_id':identifier,'status':'queued','task':req.task,'mode':req.mode,'created_at':time.time(),'owner_pid':os.getpid(),'owner_instance':_PROCESS_INSTANCE,'owner_kind':'api',
                'dataset_path':str(dataset),'source_dataset_path':str(source),'training_provenance':binding,'trials':[],'winner':None,'budget':req.budget.model_dump(),'device':req.device,'compute_profile_id':req.compute_profile_id}
    _write(models/'automated_training'/identifier/'submission.json',submission)
    with _LOCK:_EVENTS[key]=event
    def execute():
        try:
            return run_automated_training(task=req.task,dataset_path=dataset,source_dataset_path=source,models_dir=models,preset=req.preset,device=req.device,mode=req.mode,
                budget=req.budget.model_dump(),search_space=req.search_space,base_config={**req.base_config,'objective':req.objective,'latency_weight':req.latency_weight},
                epochs_per_trial=req.epochs_per_trial,parent_job_id=req.parent_job_id,cancel_event=event,search_id=identifier,training_binding=binding,owner_instance=_PROCESS_INSTANCE,
                compute_profile_id=req.compute_profile_id,remote_owner=owner,seed=req.seed,reuse_search_id=req.reuse_search_id)
        except (ValueError,OSError,RuntimeError,KeyError,TypeError) as exc:
            failed={**submission,'status':'failed','error':str(exc)};_write(models/'automated_training'/identifier/'search.json',failed)
            if not req.background:raise HTTPException(422,str(exc)) from exc
        finally:
            with _LOCK:_EVENTS.pop(key,None)
    if req.background:
        context=copy_context();threading.Thread(target=lambda:context.run(execute),daemon=True,name=f'trials-{identifier[:8]}').start()
        return JSONResponse(submission,status_code=202)
    return execute()


@router.get('/parents')
def configuration_parents(task:str,dataset_path:str,request:Request,family_dataset_path:str|None=None):
    """Discover completed parents using each parent's recorded structure."""
    project=get_current_project(request);source=Path(dataset_path).expanduser().resolve()
    if task not in _RUNNERS:raise HTTPException(422,'No measured runner for this task')
    if not project.get('source_dataset_dir') or source!=Path(project['source_dataset_dir']).resolve():raise HTTPException(409,'Parent source differs from active project')
    spec=_RUNNERS[task];models=Path(project['models_dir']);dataset=Path(family_dataset_path).expanduser() if family_dataset_path else source
    if task in ('ocr','rotated_detection'):
        from backend.engine.prepared_family_datasets import resolve_family_dataset
        try:dataset=resolve_family_dataset(project,task,dataset).root
        except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc
    elif dataset!=source and (dataset.is_symlink() or not dataset.resolve().is_relative_to(Path(project['dataset_dir']).resolve())):
        raise HTTPException(422,'Prepared parent input must belong to active project')
    root=models/spec.family if spec.family else models;rows=[]
    if not root.is_dir() or root.is_symlink():return {'parents':rows,'total':0}
    for folder in sorted(root.iterdir()):
        try:
            if folder.is_symlink() or not folder.is_dir():continue
            metadata=json.loads((folder/'model_meta.json').read_text(encoding='utf-8'));config=metadata.get('training_config')
            if metadata.get('task')!=task or not isinstance(config,dict):continue
            lineage=metadata.get('training_provenance') or {}
            if lineage.get('labelset_id','default')!=project.get('active_labelset_id','default'):continue
            if spec.family:
                from backend.engine.specialized_warm_start import resolve_family_parent
                options=dict(config)
                if task=='ocr':
                    height=config.get('image_size',config.get('image_height',32))
                    options['image_size']=tuple(height) if isinstance(height,(list,tuple)) else (height,config.get('image_width',128))
                parent=resolve_family_parent(models,folder.name,task,source,dataset,options)
            else:
                from backend.engine.warm_start import resolve_warm_start_parent,architecture_for
                parent=resolve_warm_start_parent(folder.name,models,source,task,architecture_for(task,metadata.get('preset','fast'),config))
            rows.append({'job_id':folder.name,'checkpoint_sha256':parent.checkpoint_sha256,'training_config':config,'architecture':parent.architecture})
        except (ValueError,OSError,RuntimeError,KeyError,TypeError):continue
    return {'parents':rows,'total':len(rows)}


def _read(request,identifier):
    models=Path(get_current_project(request)['models_dir'])
    directory=models/'automated_training'/identifier
    with _LOCK:
        try:record=read_search(models,identifier);path=directory/'search.json'
        except FileNotFoundError:
            path=directory/'submission.json'
            if path.is_symlink() or not path.is_file():raise HTTPException(404,'Search unavailable in active project')
            record=json.loads(path.read_text(encoding='utf-8'))
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        if record.get('status') in ('queued','running','stopping'):
            key=(str(models.resolve()),identifier);event=_EVENTS.get(key);external_alive=False
            if record.get('owner_kind')=='engine' and type(record.get('owner_pid'))is int:
                try:os.kill(record['owner_pid'],0);external_alive=True
                except (ProcessLookupError,PermissionError,OSError):pass
            if event is None and not external_alive:
                record.update(status='interrupted',winner=None,stop_reason='owner_lost',finished_at=time.time(),
                    error='Training process ended before this search completed; start a new candidate to continue')
                for trial in record.get('trials',[]):
                    if trial.get('status') in ('queued','running','stopping'):
                        trial['status']='interrupted';spec=_RUNNERS.get(record.get('task'));trial_id=trial.get('trial_id')
                        from backend.engine.checkpoint_paths import is_job_id
                        valid=isinstance(trial_id,str) and (is_job_id(trial_id) if spec and not spec.family else len(trial_id)==32 and all(c in '0123456789abcdef' for c in trial_id))
                        if spec and valid:
                            folder=models/spec.family/trial_id if spec.family else models/trial_id
                            receipt=folder/'job_receipt.json'
                            if folder.is_dir() and not folder.is_symlink() and not receipt.is_symlink():
                                existing=json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
                                if existing.get('status')!='completed':_write(receipt,{**existing,'job_id':trial_id,'task':record['task'],'status':'interrupted','search_id':identifier})
                _write(path,record)
            elif (event is not None and event.is_set()) or (directory/'cancel_requested.json').is_file():record['status']='stopping'
        return record


@router.get('/jobs')
def jobs(request:Request):
    root=Path(get_current_project(request)['models_dir'])/'automated_training'
    return {'jobs':[_read(request,p.name) for p in sorted(root.iterdir()) if p.is_dir() and not p.is_symlink()] if root.is_dir() else []}


@router.get('/jobs/{search_id}')
def job(search_id:str,request:Request):return _read(request,search_id)


@router.post('/jobs/{search_id}/cancel')
def cancel(search_id:str,request:Request):
    record=_read(request,search_id);key=(str(Path(get_current_project(request)['models_dir']).resolve()),search_id)
    with _LOCK:
        event=_EVENTS.get(key)
        if record.get('status') in ('queued','running','stopping'):
            _write(Path(key[0])/'automated_training'/search_id/'cancel_requested.json',{'search_id':search_id,'requested_at':time.time()})
            if event is not None:event.set()
            return {**record,'status':'stopping'}
    return record
