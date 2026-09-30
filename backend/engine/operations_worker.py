"""An app-owned project watcher that survives closing the desktop renderer."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import psutil
from backend.engine.model_operations import OperationsStore,run_cycle


def _paths(project):
    root=Path(project['project_dir'])/'operations_worker';root.mkdir(exist_ok=True)
    if root.is_symlink() or any((root/name).is_symlink() for name in ('owner.json','stop','worker.log')):raise ValueError('Operations worker storage is linked')
    return root


def _owned(project):
    root=_paths(project);owner=root/'owner.json'
    if not owner.is_file():return None
    try:
        row=json.loads(owner.read_text());process=psutil.Process(row['pid']);args=process.cmdline()
        if abs(process.create_time()-row['created_at'])>.1:return None
        if 'backend.engine.operations_worker' in args and '--project-dir' in args and args[args.index('--project-dir')+1]==str(Path(project['project_dir']).resolve()):return process
    except (ValueError,OSError,KeyError,psutil.Error):pass
    return None


def watcher_state(project):
    process=_owned(project)
    return {'running':process is not None,'pid':process.pid if process else None,'stopping':(_paths(project)/'stop').exists()}


def _start_watcher_locked(project):
    if _owned(project):return watcher_state(project)
    root=_paths(project);(root/'stop').unlink(missing_ok=True)
    with (root/'worker.log').open('ab') as log:
        process=subprocess.Popen([sys.executable,'-m','backend.engine.operations_worker','--project-dir',str(Path(project['project_dir']).resolve())],
            cwd=Path(__file__).resolve().parents[2],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    owner={'pid':process.pid,'created_at':psutil.Process(process.pid).create_time()}
    temporary=root/'owner.tmp';temporary.write_text(json.dumps(owner));temporary.chmod(0o600);temporary.replace(root/'owner.json')
    return watcher_state(project)


def start_watcher(project):
    from backend.engine.model_operations import _cycle_lock
    with _cycle_lock(project,'operations_watcher_start.lock'):return _start_watcher_locked(project)


def stop_watcher(project):
    root=_paths(project);process=_owned(project)
    if process:(root/'stop').touch(mode=0o600)
    return {'running':bool(process),'status':'cancelling' if process else 'stopped'}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Watch one owned project for new data and run its configured operations policy')
    parser.add_argument('--project-dir',required=True);parser.add_argument('--once',action='store_true');args=parser.parse_args(argv)
    from backend.api.routes_project import _load_project
    project=_load_project(Path(args.project_dir).expanduser().resolve());root=_paths(project);event=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:event.set());signal.signal(signal.SIGINT,lambda *_:event.set())
    def monitor():
        while not event.wait(.05):
            if (root/'stop').exists():event.set();return
    threading.Thread(target=monitor,daemon=True).start()
    while not event.is_set():
        policy=OperationsStore(project['project_dir']).policy()
        if not policy:raise ValueError('Configure a policy before starting the watcher')
        try:result=run_cycle(project,event)
        except ValueError as exc:
            if str(exc)=='This project already has an operations cycle':event.wait(1);continue
            raise
        print(json.dumps({'cycle_id':result['cycle_id'],'status':result['status'],'error':result['error']}),flush=True)
        if args.once or result['status'] in ('blocked','failed'):break
        event.wait(policy['watch_interval_seconds'])
    return 0

if __name__=='__main__':raise SystemExit(main())
