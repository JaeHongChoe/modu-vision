"""Project-owned conversion journals and cancelable worker processes."""
from contextvars import copy_context
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
from backend.engine.exporter import optimize_verified_flow_package as optimize_flow_package

_LOCK=threading.RLock();_EVENTS={}


def _path(project,job_id):
    if not re.fullmatch(r'[0-9a-f]{32}',job_id):raise ValueError('Invalid optimization job ID')
    directory=Path(project)/'exports'/'optimization_jobs'
    if any(p.is_symlink() for p in (directory,*directory.parents)):raise ValueError('Optimization journal cannot use symlinks')
    return directory/(job_id+'.json')


def _write(path,record):
    path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8');os.replace(temporary,path)


def read_job(project,job_id):
    with _LOCK:
        path=_path(project,job_id)
        if not path.is_file() or path.is_symlink():raise ValueError('Optimization job not found in this project')
        record=json.loads(path.read_text())
        if record['status'] in ('queued','running','stopping') and (str(path),job_id) not in _EVENTS:
            record.update(status='interrupted',error='Conversion owner was interrupted; create a new candidate',updated_at=time.time())
            _write(path,record)
        return record


def start_job(project,options):
    job_id=uuid.uuid4().hex;path=_path(project,job_id);event=threading.Event()
    output=Path(project)/'exports'/'flows'/('openvino_'+job_id)
    record={'job_id':job_id,'status':'queued','options':options,'created_at':time.time(),'updated_at':time.time(),
            'owner_pid':os.getpid(),'package_path':str(output),'result':None,'error':None}
    with _LOCK:
        if any(key[0].startswith(str(path.parent)) for key in _EVENTS):raise ValueError('This project already has an active optimization')
        _EVENTS[(str(path),job_id)]=event;_write(path,record)
    context=copy_context()
    def run():
        try:
            with _LOCK:record.update(status='running',updated_at=time.time());_write(path,record)
            result=optimize_flow_package(**options,output_dir=output,cancel_event=event)
            with _LOCK:record.update(status='completed',result=result,updated_at=time.time());_write(path,record)
        except Exception as exc:
            with _LOCK:record.update(status='cancelled' if isinstance(exc,InterruptedError) else 'failed',error=str(exc),updated_at=time.time());_write(path,record)
        finally:
            with _LOCK:_EVENTS.pop((str(path),job_id),None)
    threading.Thread(target=lambda:context.run(run),name='openvino-'+job_id[:8],daemon=True).start()
    return read_job(project,job_id)


def cancel_job(project,job_id):
    with _LOCK:
        record=read_job(project,job_id);path=_path(project,job_id)
        event=_EVENTS.get((str(path),job_id))
        if event and record['status'] in ('queued','running','stopping'):
            event.set();record.update(status='stopping',updated_at=time.time());_write(path,record)
        return record
