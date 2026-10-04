"""Project backups in the common JobStore; ZIP publication stays owned until verified.

Restart reruns archive staging from its immutable source snapshot, under the same
job identity. Restore execution is intentionally not a capability of this adapter.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import tempfile

from backend.engine.job_store import JobStore, StaleRevision, StaleFencingToken
from backend.engine.dataset_import_job import ImportNotAcceptable
from backend.engine.project_archive import create_archive, _digest_file, _sqlite_file, _sqlite_snapshot

KIND = 'project_backup'


class DataBackupJobs:
    def __init__(self, store: JobStore):
        self.store = store

    @staticmethod
    def snapshot(project: dict) -> str:
        digest = hashlib.sha256()
        for label, text in (('project', project['project_dir']), ('source', project.get('source_dataset_dir'))):
            if not text:
                continue
            root = Path(text)
            if not root.is_dir():
                raise ImportNotAcceptable('Backup source is unavailable')
            for directory, folders, files in os.walk(root, followlinks=False):
                folders[:] = sorted(name for name in folders if label != 'project' or name != '.retention')
                for name in folders:
                    if (Path(directory) / name).is_symlink():
                        raise ImportNotAcceptable('Backup linked directories are unsupported')
                for name in sorted(files):
                    path = Path(directory) / name
                    if label == 'project' and (name.endswith(('-wal', '-shm', '.lock')) or name.startswith('.annotation-atomic-')):
                        continue
                    if path.is_symlink():
                        raise ImportNotAcceptable('Backup linked files are unsupported')
                    if label == 'project' and _sqlite_file(path):
                        # Hash the same logical DB view the existing archive writer copies, including committed WAL.
                        with tempfile.TemporaryDirectory(prefix='modu-backup-snapshot-') as staging:
                            content_sha = _digest_file(_sqlite_snapshot(path, Path(staging)))
                    else:
                        content_sha = _digest_file(path)
                    digest.update(json.dumps([label, str(path.relative_to(root)), content_sha], ensure_ascii=False).encode('utf-8'))
        return digest.hexdigest()

    def submit(self, context, project_key: str, project: dict, key=None):
        # Replay the reservation before source I/O; a completed result survives source removal.
        if key is not None and self.store.reserved(context, project_key, KIND, key) is not None:
            return self.store.submit(context, project_key, KIND, {'project': project}, key, project_dir=project['project_dir'])
        # Snapshot before new reservation: unsupported input never leaves an accepted orphan.
        snapshot = self.snapshot(project)
        ref = self.store.submit(context, project_key, KIND, {'project': project}, key,
                                project_dir=project['project_dir'])
        if ref.created:
            stage = self.store.path.parent / 'data-operations' / ref.id
            self.store.checkpoint(ref.id, {'kind': KIND, 'source_snapshot': snapshot,
                'expected_target_revision': snapshot, 'progress_unit': 'archive', 'attempt': 0,
                'progress': {'phase': 'accepted', 'processed': 0, 'total': 1},
                'staged_output': str(stage), 'result_ref': None, 'expires_at': None})
        return ref

    def view(self, job_id, project_key, actor_id):
        record = self.store.record(job_id)
        if record['kind'] != KIND or record['project_key'] != project_key or record['actor_id'] != actor_id:
            raise KeyError(job_id)
        operation = self.store.checkpoint_value(job_id)
        result = operation.get('result_ref')
        available = False
        if record['state'] == 'completed' and result and operation.get('expires_at', 0) > time.time():
            path = Path(result['path'])
            try:
                available = path.is_file() and _digest_file(path) == result['sha256']
            except OSError:
                available = False
        return {'job_id': job_id, 'state': record['state'], 'revision': record['revision'],
            **operation, 'attempt': len(self.store.attempts(job_id)), 'downloadable': available,
            'resumable': record['state'] == 'interrupted' and not self.store.cancel_intent(job_id),
            'capabilities': {'resume': True, 'restore': False},
            'restore_reason': 'Durable restore execution is unsupported; restore a verified archive to a new directory through the existing explicit restore action'}

    def resume(self, job_id, project_key, actor_id):
        view = self.view(job_id, project_key, actor_id)
        ref = self.store.get(job_id)
        if ref.state in ('accepted', 'running', 'completed'):
            return ref
        if not view['resumable']:
            raise ImportNotAcceptable('Only an interrupted uncancelled backup can resume')
        project = json.loads(self.store.record(job_id)['spec_json'])['project']
        if self.snapshot(project) != view['source_snapshot']:
            raise ImportNotAcceptable('Backup source or target revision changed since submission')
        return self.store.transition(job_id, ref.revision, 'resume')

    def run(self, job_id):
        record = self.store.record(job_id)
        if record['kind'] != KIND:
            raise ValueError('Not a backup job')
        ref = self.store.get(job_id)
        if ref.state != 'accepted':
            return ref
        if self.store.cancel_intent(job_id):
            return self.store.transition(job_id, ref.revision, 'abort', {'reason': 'cancelled before staging'})
        ref = self.store.transition(job_id, ref.revision, 'start')
        fence = self.store.begin_attempt(job_id, ref.revision, 'backup-thread', None, os.getpid()).fencing_token
        operation = self.store.checkpoint_value(job_id)
        project = json.loads(record['spec_json'])['project']
        try:
            if self.snapshot(project) != operation['source_snapshot']:
                raise ImportNotAcceptable('Backup source changed since submission')
            operation['progress'] = {'phase': 'staging', 'processed': 0, 'total': 1}
            self.store.checkpoint(job_id, operation, fence)
            # Existing archive writer uses a .partial file and verifies its inventory before rename.
            result = create_archive(project, Path(operation['staged_output']))
            path = Path(result['archive_path'])
            if self.store.cancel_intent(job_id):
                raise InterruptedError('cancelled before verification')
            if self.snapshot(project) != operation['source_snapshot']:
                raise ImportNotAcceptable('Backup source changed during staging')
            operation.update(result_ref={'path': str(path), 'sha256': _digest_file(path),
                'count': result['file_count'], 'bytes': path.stat().st_size}, expires_at=time.time() + 7 * 86400,
                progress={'phase': 'verified', 'processed': 1, 'total': 1})
            self.store.checkpoint(job_id, operation, fence, require_uncancelled=True)
            return self.store.finish(job_id, 'complete', {'operation': operation}, fencing_token=fence)
        except StaleFencingToken:
            return self.store.get(job_id)
        except InterruptedError as exc:
            return self.store.finish(job_id, 'abort', {'reason': str(exc)}, fencing_token=fence)
        except Exception as exc:
            if self.store.checkpoint_value(job_id).get('result_ref'):
                return self.store.get(job_id)  # verified output is reconciled at startup, never replaced by failure
            return self.store.finish(job_id, 'fail', {'error': {'message': str(exc)}}, fencing_token=fence)

    def start(self, job_id):
        thread = threading.Thread(target=self.run, args=(job_id,), daemon=True, name=f'Backup-{job_id[:8]}')
        thread.start()
        return thread

    def recover_orphans(self):
        interrupted, completed = [], []
        for row in self.store.active(KIND):
            try:
                operation = self.store.checkpoint_value(row['id'])
                result = operation.get('result_ref')
                if result and Path(result['path']).is_file() and _digest_file(Path(result['path'])) == result['sha256'] and not self.store.cancel_intent(row['id']):
                    self.store.transition(row['id'], row['revision'], 'complete', {'operation': operation, 'recovered': True})
                    completed.append(row['id'])
                    continue
                self.store.transition(row['id'], row['revision'], 'interrupt', {'reason': 'Backend restarted; explicit resume required'})
                interrupted.append(row['id'])
            except StaleRevision:
                pass
        return {'interrupted': interrupted, 'completed': completed}
