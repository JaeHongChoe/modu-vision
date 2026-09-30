"""Owned, persisted local labeling jobs with cooperative model cancellation."""
from __future__ import annotations
import json
import re
import threading
import time
import uuid
from pathlib import Path

from backend.engine.foundation_labeling import LabelingCancelled, check_cancel

_LOCK = threading.RLock()
_LIVE = {}
_ID = re.compile(r'(?:batch|featurejob)_[0-9a-f]{24}\Z')


def _root(project):
    root = Path(project['project_dir']) / 'labeling_jobs'
    if root.is_symlink(): raise ValueError('Labeling job directory cannot be a symbolic link')
    root.mkdir(parents=True, exist_ok=True)
    return root


def write(project, job):
    from backend.api.routes_project import _write_json
    if not _ID.fullmatch(job['id']): raise ValueError('Invalid labeling job ID')
    target = _root(project) / (job['id'] + '.json')
    if target.is_symlink(): raise ValueError('Labeling job file cannot be a symbolic link')
    with _LOCK: _write_json(target, job)


def read(project, job_id):
    if not _ID.fullmatch(job_id): raise ValueError('Invalid labeling job ID')
    path = _root(project) / (job_id + '.json')
    if path.is_symlink(): raise ValueError('Labeling job file cannot be a symbolic link')
    with _LOCK:
        value = json.loads(path.read_text())
        if value.get('project_id') != project['id'] or value.get('source_dataset_dir') != project.get('source_dataset_dir'):
            raise ValueError('Labeling job belongs to another project or dataset')
        if job_id not in _LIVE and value['status'] in ('queued', 'running', 'cancelling'):
            value.update(status='interrupted', error='Backend stopped before this job completed. Start a new job; partial proposals remain reviewable.')
            write(project, value)
    return value


def active(project):
    with _LOCK:
        return any(owner == project['id'] for owner, _, _ in _LIVE.values())


def start(project, kind, payload, worker):
    with _LOCK:
        if active(project): raise ValueError('A labeling task is already running for this project')
        job = {'id': ('batch' if kind == 'candidate_batch' else 'featurejob') + '_' + uuid.uuid4().hex[:24],
               'project_id': project['id'], 'source_dataset_dir': project.get('source_dataset_dir'),
               'labelset_id': project.get('active_labelset_id', 'default'), 'kind': kind, 'status': 'queued',
               'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **payload}
        cancel = threading.Event()
        def run():
            try:
                check_cancel(cancel); job['status'] = 'running'; write(project, job)
                worker(job, cancel)
                check_cancel(cancel); job['status'] = 'completed'
            except LabelingCancelled:
                job['status'] = 'stopped'
            except Exception as exc:
                job.update(status='failed', error=str(exc))
            finally:
                job['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                with _LOCK:
                    write(project, job)
                    _LIVE.pop(job['id'], None)
        thread = threading.Thread(target=run, name='owned-labeling-' + job['id'], daemon=True)
        _LIVE[job['id']] = (project['id'], cancel, thread)
        write(project, job)
        thread.start()
        return dict(job)


def cancel(project, job_id):
    with _LOCK:
        job = read(project, job_id)
        live = _LIVE.get(job_id)
        if live:
            live[1].set()
            job['cancel_requested'] = True
            job['status']='cancelling'
            write(project, job)
        return job


def list_jobs(project, kind=None):
    rows = []
    for path in _root(project).glob('*.json'):
        try:
            row = read(project, path.stem)
            if kind is None or row.get('kind') == kind: rows.append(row)
        except (OSError, ValueError, KeyError): continue
    return sorted(rows, key=lambda row: row.get('created_at', ''), reverse=True)
