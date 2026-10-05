"""Project-owned, persisted training lifecycle for cancellable family adapters."""
from __future__ import annotations
from contextvars import copy_context
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

PROCESS_INSTANCE=uuid.uuid4().hex
_LOCK=threading.RLock()
_EVENTS={}
ACTIVE={'queued','running','stopping'}


def persist_training_configuration(output,config):
    """Keep reusable controls inside the hash-bound checkpoint and metadata."""
    import torch
    output=Path(output);checkpoint=output/'best_model.pt'
    payload=torch.load(checkpoint,map_location='cpu',weights_only=True);payload['training_config']=dict(config)
    temporary=checkpoint.with_suffix('.tmp');torch.save(payload,temporary);temporary.replace(checkpoint)
    digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    for name in ('model_meta.json','metadata.json'):
        path=output/name
        if path.is_file():
            metadata=json.loads(path.read_text(encoding='utf-8'));metadata.update(training_config=dict(config),checkpoint_sha256=digest);_write(path,metadata)


def require_training_source(project, requested):
    source=Path(requested).expanduser().resolve()
    configured=project.get('source_dataset_dir')
    if not configured or source!=Path(configured).expanduser().resolve():
        raise ValueError('Training dataset must be the active project source')
    return source


def _write(path,record):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('w',encoding='utf-8') as handle:
            json.dump(record,handle,ensure_ascii=False);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def read_job(root,identifier):
    if not isinstance(identifier,str) or re.fullmatch('[0-9a-f]{32}',identifier) is None:raise ValueError('Invalid training job ID')
    directory=Path(root)/identifier;path=directory/'job.json'
    if directory.is_symlink() or path.is_symlink() or not path.is_file():raise FileNotFoundError('Training job is unavailable in the active project')
    with _LOCK:
        record=json.loads(path.read_text(encoding='utf-8'))
        if record.get('job_id')!=identifier:raise ValueError('Training job identity differs')
        if record['status'] in ACTIVE and (record.get('owner_instance')!=PROCESS_INSTANCE or str(directory.resolve()) not in _EVENTS):
            record.update(status='interrupted',error='Application stopped before training completed')
            record.setdefault('events',[]).append({'at':time.time(),'status':'interrupted','epoch':record.get('epoch',0),'batch':record.get('batch',0)})
            _write(path,record)
            from backend.engine.specialist_training_queue import interrupt_unlaunched
            interrupt_unlaunched(directory)
        from backend.engine.specialist_training_queue import queue_status
        return {**record, **queue_status(directory)}


def list_jobs(root):
    rows=[read_job(root,path.parent.name) for path in Path(root).glob('*/job.json') if not path.parent.is_symlink()]
    return sorted(rows,key=lambda row:row['created_at'],reverse=True)


def cancel_job(root,identifier):
    with _LOCK:
        record=read_job(root,identifier);directory=Path(root)/identifier
        event=_EVENTS.get(str(directory.resolve()))
        if record['status'] in {'queued','running'} and event is not None:
            from backend.engine.specialist_training_queue import cancel_owned
            cancel_owned(directory)
            event.set();record['status']='stopping'
            record.setdefault('events',[]).append({'at':time.time(),'status':'stopping','epoch':record.get('epoch',0),'batch':record.get('batch',0)})
            _write(directory/'job.json',record)
        from backend.engine.specialist_training_queue import queue_status
        return {**record, **queue_status(directory)}


def start_job(*,project,task,source,output,options,runner,family_digest,warm_start=None,family_dataset=None,request=None):
    from backend.engine.training_provenance import bind_family_training,validate_training_binding,persist_model_binding
    from backend.engine.runtime_device import resolve_runtime_device
    source=require_training_source(project,source);output=Path(output).resolve();root=output.parent
    device=str(resolve_runtime_device(options.device))
    current_digest=family_digest()
    dataset=Path(family_dataset).resolve() if family_dataset is not None else source
    binding=bind_family_training(project,dataset,task)
    binding.update(family_dataset_path=str(dataset),family_dataset_sha256=current_digest,label_kind=task)
    event=threading.Event();key=str(output)
    admission=None
    if request is not None:
        from backend.engine.specialist_training_queue import reserve
        admission,replay=reserve(request,project,task,output,options,event)
        if replay is not None:return replay
    record={'job_id':output.name,'task':task,'status':'queued','epoch':0,'batch':0,'batches':0,
        'epochs':options.epochs,'dataset_path':str(dataset),'source_dataset_path':str(source),
        'device':device,'owner_instance':PROCESS_INSTANCE,'created_at':time.time(),'error':None,
        'training_provenance':binding,'events':[],
        'budget':{'max_runtime_s':options.max_runtime_s} if getattr(options,'max_runtime_s',None) is not None else {}}
    if warm_start is not None:
        record['warm_start'] = warm_start.lineage()
    def persist(**changes):
        with _LOCK:
            # Cancellation must not race a batch callback back to running.
            if event.is_set() and changes.get('status')=='running':changes['status']='stopping'
            if event.is_set() and 'status' not in changes and record['status'] in {'queued','running'}:changes['status']='stopping'
            record.update(changes)
            record['events']=[*record['events'][-1999:],{'at':time.time(),'status':record['status'],'epoch':record['epoch'],'batch':record['batch']}]
            _write(output/'job.json',record)
    with _LOCK:
        if admission is None and any(row['status'] in ACTIVE for row in list_jobs(root)):raise ValueError('Another family training job is active in this project')
        try:
            _EVENTS[key]=event;persist()
        except BaseException as exc:
            _EVENTS.pop(key,None)
            if admission is not None:admission.abandon(exc)
            raise
    from backend.engine.runtime_budget import RuntimeBudget
    def expired():
        if admission is not None:admission.request_cancel('runtime budget exceeded')
        with _LOCK:
            if record['status'] in ACTIVE:
                persist(status='stopping',stop_reason='time_limit')
    budget=RuntimeBudget(getattr(options,'max_runtime_s',None),event,
        lambda started:persist(runtime_started_at=started),expired)
    def execute():
        lease=None;lease_stack=ExitStack()
        try:
            if event.is_set():raise InterruptedError('Training cancelled')
            from backend.engine.shared_scheduler import compute_lease_scope
            lease=lease_stack.enter_context(admission.scope() if admission is not None else compute_lease_scope(output.name,device))
            lease_stack.enter_context(budget)
            persist(status='running');validate_training_binding(binding)
            def progress(values):
                budget.check()
                if admission is not None:admission.check()
                elif lease is not None:lease.heartbeat(output.name)
                persist(**values)
            result=runner(event,progress,device)
            budget.check()
            validate_training_binding(binding)
            if family_digest()!=current_digest:raise ValueError('Family training labels or source changed during training')
            persist_model_binding(output,binding)
            if event.is_set():raise InterruptedError('Training cancelled during finalization')
            configuration=options.model_dump(exclude={'dataset_path','background','warm_start_job_id','queue','priority'}) if hasattr(options,'model_dump') else {}
            persist_training_configuration(output,configuration)
            checkpoint=output/'best_model.pt';digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            metadata=output/'model_meta.json'
            meta=json.loads(metadata.read_text(encoding='utf-8'));meta.update(source_dataset_path=str(source),dataset_path=str(dataset),
                training_config=configuration)
            meta['checkpoint_sha256']=digest
            _write(metadata,meta)
            receipt={'job_id':output.name,'task':task,'status':'completed','source_dataset_path':str(source),
                'dataset_path':str(dataset),'dataset_fingerprint':binding['dataset_fingerprint'],
                'training_provenance':binding,'checkpoint_sha256':digest}
            if warm_start is not None:
                receipt['warm_start'] = warm_start.lineage()
            result={**result,'checkpoint_sha256':digest}
            response={'job_id':output.name,'checkpoint_path':str(checkpoint),'model_sha256':digest,'result':result}
            # Serialize completion with cancel acceptance. A stopping journal never
            # publishes an executable checkpoint, even if binding writes took time.
            with _LOCK:
                budget.seal()
                if event.is_set():raise InterruptedError('Training cancelled during finalization')
                def publish():
                    _write(output/'job_receipt.json',receipt)
                    persist(status='completed',model_sha256=digest,result=result)
                if admission is not None:admission.complete(publish)
                else:publish()
            return response
        except (InterruptedError,ValueError,OSError,RuntimeError) as exc:
            for name in ('best_model.pt','model_meta.json','job_receipt.json'):(output/name).unlink(missing_ok=True)
            persist(status='stopped' if isinstance(exc,InterruptedError) else 'failed',
                error='Training runtime limit exceeded' if budget.spent and isinstance(exc,InterruptedError) else str(exc),
                **({'stop_reason':'time_limit'} if budget.spent else {}))
            if admission is not None:admission.finish_error(exc)
            if not options.background:raise
        finally:
            try:
                lease_stack.close()
            finally:
                with _LOCK:_EVENTS.pop(key,None)
    if options.background:
        context=copy_context()
        try:threading.Thread(target=lambda:context.run(execute),daemon=True,name=f'{task}-{output.name[:8]}').start()
        except BaseException as exc:
            with _LOCK:
                _EVENTS.pop(key,None);persist(status='failed',error=str(exc))
            if admission is not None:admission.abandon(exc)
            raise
        with _LOCK:
            from backend.engine.specialist_training_queue import queue_status
            return {**copy.deepcopy(record),**(queue_status(output) if admission is not None else {})}
    return execute()
