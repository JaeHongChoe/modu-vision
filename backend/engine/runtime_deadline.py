"""Terminate the process group owned by an inference deadline."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any


def validate_deadline(value):
    if value is not None and (type(value) is not int or not 1 <= value <= 86400000):
        raise ValueError('Inference deadline_ms must be an integer from 1 to 86400000')
    return value


def execute_owned_process(command, *, deadline_ms, env=None, cwd=None,cancel_event=None) -> dict[str, Any]:
    """The budget includes initialization; termination also targets descendant work."""
    validate_deadline(deadline_ms)
    started=time.monotonic()
    kwargs={'start_new_session':True} if os.name!='nt' else {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP}
    # Files avoid a child blocking on a filled output pipe. Inference results use
    # a separate bounded JSON file, not arbitrary model logging on stdout.
    import tempfile
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=env,cwd=cwd,**kwargs)
        cancelled=False
        try:
            if cancel_event is None:process.wait(timeout=None if deadline_ms is None else max(.0001,deadline_ms/1000-(time.monotonic()-started)))
            else:
                while process.poll() is None:
                    cancelled=cancel_event.is_set()
                    if cancelled or (deadline_ms is not None and (time.monotonic()-started)*1000>=deadline_ms):raise subprocess.TimeoutExpired(command,deadline_ms)
                    try:process.wait(timeout=.1)
                    except subprocess.TimeoutExpired:pass
        except subprocess.TimeoutExpired:
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5,check=False)
            else:
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            process.kill() if process.poll() is None else None
            process.wait(timeout=5)
            return {'status':'cancelled' if cancelled else 'timeout','final_verdict':'REVIEW','rejection_reason':'CANCELLED' if cancelled else 'INFERENCE_DEADLINE_EXCEEDED',
                'roi_count':0,'defective_roi_count':0,'crops':[],'execution_steps':[],
                'deadline':{'deadline_ms':deadline_ms,'elapsed_ms':round((time.monotonic()-started)*1000,3),
                            'pid':process.pid,'terminated':True,'termination_scope':'owned_process_group'}}
        stdout.seek(0);stderr.seek(0)
        return {'status':'completed','returncode':process.returncode,
                'stdout':stdout.read(65536).decode('utf-8',errors='replace'),
                'stderr':stderr.read(65536).decode('utf-8',errors='replace'),
                'pid':process.pid,'elapsed_ms':round((time.monotonic()-started)*1000,3)}
