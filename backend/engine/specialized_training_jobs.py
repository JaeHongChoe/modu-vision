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
        record=json.loads(path.read_text())
        if record.get('job_id')!=identifier:raise ValueError('Training job identity differs')
        if record['status'] in ACTIVE and (record.get('owner_instance')!=PROCESS_INSTANCE or str(directory.resolve()) not in _EVENTS):
            record.update(status='interrupted',error='Application stopped before training completed')
            record.setdefault('events',[]).append({'at':time.time(),'status':'interrupted','epoch':record.get('epoch',0),'batch':record.get('batch',0)})
            _write(path,record)
        return record


def list_jobs(root):
    rows=[read_job(root,path.parent.name) for path in Path(root).glob('*/job.json') if not path.parent.is_symlink()]
    return sorted(rows,key=lambda row:row['created_at'],reverse=True)


def cancel_job(root,identifier):
    with _LOCK:
        record=read_job(root,identifier);directory=Path(root)/identifier
        event=_EVENTS.get(str(directory.resolve()))
        if record['status'] in {'queued','running'} and event is not None:
            event.set();record['status']='stopping'
            record.setdefault('events',[]).append({'at':time.time(),'status':'stopping','epoch':record.get('epoch',0),'batch':record.get('batch',0)})
            _write(directory/'job.json',record)
        return record


def start_job(*,project,task,source,output,options,runner,family_digest,warm_start=None):
    from backend.engine.training_provenance import bind_family_training,validate_training_binding,persist_model_binding
    from backend.engine.runtime_device import resolve_runtime_device
    source=require_training_source(project,source);output=Path(output).resolve();root=output.parent
    device=str(resolve_runtime_device(options.device))
    current_digest=family_digest()
    binding=bind_family_training(project,source,task)
    binding.update(family_dataset_path=str(source),family_dataset_sha256=current_digest,label_kind=task)
    event=threading.Event();key=str(output)
    record={'job_id':output.name,'task':task,'status':'queued','epoch':0,'batch':0,'batches':0,
        'epochs':options.epochs,'dataset_path':str(source),'source_dataset_path':str(source),
        'device':device,'owner_instance':PROCESS_INSTANCE,'created_at':time.time(),'error':None,
        'training_provenance':binding,'events':[]}
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
        if any(row['status'] in ACTIVE for row in list_jobs(root)):raise ValueError('Another family training job is active in this project')
        _EVENTS[key]=event;persist()
    def execute():
        lease=None;lease_stack=ExitStack()
        try:
            if event.is_set():raise InterruptedError('Training cancelled')
            from backend.engine.shared_scheduler import compute_lease_scope
            lease=lease_stack.enter_context(compute_lease_scope(output.name,device))
            persist(status='running');validate_training_binding(binding)
            def progress(values):
                if lease is not None:lease.heartbeat(output.name)
                persist(**values)
            result=runner(event,progress,device)
            if event.is_set():raise InterruptedError('Training cancelled')
            validate_training_binding(binding)
            if family_digest()!=current_digest:raise ValueError('Family training labels or source changed during training')
            persist_model_binding(output,binding)
            checkpoint=output/'best_model.pt';digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            metadata=output/'model_meta.json'
            meta=json.loads(metadata.read_text());meta.update(source_dataset_path=str(source),dataset_path=str(source))
            if task=='defect_gan':meta['checkpoint_sha256']=digest
            _write(metadata,meta)
            receipt={'job_id':output.name,'task':task,'status':'completed','source_dataset_path':str(source),
                'dataset_path':str(source),'dataset_fingerprint':binding['dataset_fingerprint'],
                'training_provenance':binding,'checkpoint_sha256':digest}
            if warm_start is not None:
                receipt['warm_start'] = warm_start.lineage()
            if task=='defect_gan':result={**result,'checkpoint_sha256':digest}
            response={'job_id':output.name,'checkpoint_path':str(checkpoint),'model_sha256':digest,'result':result}
            # Serialize completion with cancel acceptance. A stopping journal never
            # publishes an executable checkpoint, even if binding writes took time.
            with _LOCK:
                if event.is_set():raise InterruptedError('Training cancelled during finalization')
                _write(output/'job_receipt.json',receipt)
                persist(status='completed',model_sha256=digest,result=result)
            return response
        except (InterruptedError,ValueError,OSError,RuntimeError) as exc:
            for name in ('best_model.pt','model_meta.json','job_receipt.json'):(output/name).unlink(missing_ok=True)
            persist(status='stopped' if isinstance(exc,InterruptedError) else 'failed',error=str(exc))
            if not options.background:raise
        finally:
            lease_stack.close()
            with _LOCK:_EVENTS.pop(key,None)
    if options.background:
        context=copy_context();threading.Thread(target=lambda:context.run(execute),daemon=True,name=f'{task}-{output.name[:8]}').start()
        with _LOCK:return copy.deepcopy(record)
    return execute()
