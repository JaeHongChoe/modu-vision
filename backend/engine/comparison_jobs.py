"""Scoped durable comparison receipts; no automatic restart or model activation.

The ledger fences report publication. External dataset writers are detected by
fresh binding checks, not locked by this adapter. Legacy receipts stay legacy.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

import psutil

from backend.engine.job_state import TERMINAL
from backend.engine.job_store import JobConflict, StaleRevision, StaleFencingToken

KIND = 'model_comparison'
_BOOT = uuid.uuid4().hex
_LIVE = set()
_RUNNING = set()
_LOCK = threading.RLock()


def _owner():
    process = psutil.Process(os.getpid())
    return {'boot': _BOOT, 'pid': process.pid, 'created': process.create_time(),
            'command_sha256': hashlib.sha256(json.dumps(process.cmdline()).encode('utf-8')).hexdigest()}


def _alive(owner):
    try:
        process = psutil.Process(owner['pid'])
        return (process.is_running() and process.create_time() == owner['created']
                and hashlib.sha256(json.dumps(process.cmdline()).encode('utf-8')).hexdigest() == owner['command_sha256'])
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, KeyError, TypeError):
        return None


def _sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def _sync_directory(path):
    if os.name == 'nt':
        return  # Native directory flush behavior remains Windows acceptance.
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ComparisonJobs:
    def __init__(self, store):
        self.store = store

    def _live_key(self, identifier):
        return str(self.store.path), identifier

    def scoped(self, identifier, context, project_key):
        row = self.store.record(identifier)
        if (row['kind'] != KIND or row['project_key'] != project_key
                or row['workspace_id'] != context.workspace_id or row['actor_id'] != context.actor_id):
            raise KeyError(identifier)
        return row

    def replay(self, context, project_key, key, payload):
        row = self.store.reserved(context, project_key, KIND, key) if key else None
        # Existing durable requests preceded explicit compute fields. They are
        # host CPU only; defaults preserve their exact dispatch, not retarget it.
        defaults={'execution_target':'local_cpu','compute_profile_id':None,'device':'cpu'}
        if row is not None and {**defaults,**json.loads(row['spec_json'])['request']} != {**defaults,**payload}:
            raise JobConflict('Idempotency-Key already belongs to a different comparison request')
        return row

    def submit(self, context, project_key, project, payload, binding, key):
        spec = {'request': payload, 'binding': binding, 'owner': _owner(),
                'project_dir': str(Path(project['project_dir']).resolve())}
        with _LOCK:
            try:
                ref = self.store.submit(context, project_key, KIND, spec, key,
                                        job_id='comparejob_' + uuid.uuid4().hex,
                                        project_dir=spec['project_dir'])
            except JobConflict:
                row = self.replay(context, project_key, key, payload)
                if row is None:
                    raise
                return row['id'], False
            if ref.created:
                _LIVE.add(self._live_key(ref.id))
            return ref.id, ref.created


    def cancelled(self, identifier):
        return bool(self.store.cancel_intent(identifier))

    def cancel(self, identifier):
        row = self.store.record(identifier)
        if row['state'] not in TERMINAL:
            self.store.request_cancel(identifier, row['actor_id'], 'Comparison cancelled by user')
            if row['state'] == 'accepted' and not self.store.attempts(identifier):
                try:
                    self.store.finish(identifier, 'abort', {'reason': 'Cancelled before attempt'})
                except (StaleFencingToken, ValueError):
                    pass

    def _owned(self, path, result, marker):
        if path.is_symlink() or not path.is_file():
            return False
        try:
            stat = path.stat()
            if [stat.st_dev, stat.st_ino] != result['identity'] or _sha(path) != result['sha256']:
                return False
            return json.loads(path.read_text(encoding='utf-8')).get('_durable_comparison') == marker
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def available(self, identifier):
        row = self.store.record(identifier)
        operation = self.store.checkpoint_value(identifier)
        result = operation.get('result')
        if row['state'] != 'completed' or not result:
            return False
        spec = json.loads(row['spec_json'])
        output = Path(spec['project_dir']) / 'reports' / 'model_comparisons'
        if output.is_symlink() or output.parent.is_symlink():
            return False
        return self._owned(output / (result['id'] + '.json'), result, result['marker'])

    def recover(self, identifier):
        row = self.store.record(identifier)
        if row['state'] in TERMINAL:
            return
        with _LOCK:
            if self._live_key(identifier) in _LIVE:
                return
        spec = json.loads(row['spec_json'])
        alive = _alive(spec['owner'])
        # Another process with the recorded identity is never stopped/adopted.
        if alive is not False and not self.store.checkpoint_value(identifier).get('dispatch_failed'):
            return
        attempts = self.store.attempts(identifier)
        if attempts and attempts[-1]['owner_boot_id'] != spec['owner']['boot']:
            return  # No process creation identity for an unconfirmed foreign attempt.
        token = attempts[-1]['fencing_token'] if attempts else None
        try:
            operation = self.store.checkpoint_value(identifier)
            operation['error'] = 'Previous comparison worker ended; automatic resume is unsupported'
            self.store.checkpoint(identifier, operation, token)
            self.store.finish(identifier, 'abort' if self.cancelled(identifier) else 'interrupt',
                              {'reason': 'Previous comparison worker ended; automatic resume is unsupported'}, token)
        except (StaleFencingToken, ValueError):
            pass

    def view(self, identifier, *, recover=True):
        if recover:
            self.recover(identifier)
        row = self.store.record(identifier)
        spec = json.loads(row['spec_json'])
        operation = self.store.checkpoint_value(identifier)
        state = row['state']
        result = operation.get('result', {})
        available = self.available(identifier)
        with _LOCK:
            live = self._live_key(identifier) in _LIVE
        ownership_error = ('Worker ownership unconfirmed; automatic restart/adoption is unsupported'
                           if state not in TERMINAL and not live else None)
        status = {'accepted': 'queued', 'aborted': 'cancelled'}.get(state, state)
        if state == 'completed':
            status = result.get('status', 'completed')
        return {'job_id': identifier, 'payload': spec['request'], 'status': status,
                'state': state, 'revision': row['revision'], 'durable': True, 'resumable': False,
                'total_images': operation.get('total', 0), 'completed_images': operation.get('processed', 0),
                'cancel_requested': int(self.cancelled(identifier)), 'result_available': available,
                'cancel_disposition': ('late_terminal' if self.cancelled(identifier) and state == 'completed'
                                       else 'requested' if self.cancelled(identifier) else None),
                'report_id': result.get('id') if available else None,
                'error': operation.get('error') or ownership_error or ('Verified comparison report unavailable' if state == 'completed' and not available else None),
                'created_at': row['created_ns'] / 1e9, 'updated_at': row['updated_ns'] / 1e9,
                'binding': spec['binding'], 'attempts': self.store.attempts(identifier)}

    def run(self, identifier, evaluator, output, verify):
        with _LOCK:
            if self._live_key(identifier) in _RUNNING:
                return
            _RUNNING.add(self._live_key(identifier))
        token = None
        stage = None
        final = None
        result = None
        operation = {}
        try:
            row = self.store.record(identifier)
            if row['state'] != 'accepted':
                return
            if self.cancelled(identifier):
                self.store.finish(identifier, 'abort', {'reason': 'Cancelled before start'})
                return
            try:
                self.store.transition(identifier, row['revision'], 'start')
            except StaleRevision:
                if self.cancelled(identifier):
                    self.store.finish(identifier, 'abort', {'reason': 'Cancelled before start'})
                return
            # begin_attempt may race a cancel revision; cancellation never starts inference.
            while True:
                if self.cancelled(identifier):
                    self.store.finish(identifier, 'abort', {'reason': 'Cancelled before attempt'})
                    return
                try:
                    target=json.loads(row['spec_json'])['request'].get('execution_target','local_cpu')
                    attempt = self.store.begin_attempt(identifier, self.store.get(identifier).revision,
                        'selected_compute_comparison' if target=='selected_compute' else 'local_comparison', _BOOT, os.getpid())
                    token = attempt.fencing_token
                    break
                except StaleRevision:
                    continue
            spec = json.loads(row['spec_json'])
            verify(spec['binding'])
            def progress(processed, total):
                operation.update(processed=processed, total=total)
                self.store.checkpoint(identifier, operation, token)
            def publish_report(report):
                nonlocal stage, final, result
                if self.cancelled(identifier):
                    raise InterruptedError('Comparison cancelled before staging')
                verify(spec['binding'])
                if report.get('status') not in ('completed', 'completed_with_errors'):
                    raise ValueError('Invalid comparison report terminal status')
                if not isinstance(report.get('comparison_id'), str) or not __import__('re').fullmatch(r'comparison_[0-9a-f]{32}', report['comparison_id']):
                    raise ValueError('Invalid comparison report identity')
                output.mkdir(parents=True, exist_ok=True)
                if output.is_symlink() or output.parent.is_symlink():
                    raise ValueError('Comparison output cannot be linked')
                _sync_directory(output.parent)
                marker = {'job_id': identifier, 'fencing_token': token, 'spec_sha256': row['spec_sha256']}
                report['_durable_comparison'] = marker
                stage = output / ('.' + identifier + '-' + str(token) + '-' + uuid.uuid4().hex + '.tmp')
                final = output / (report['comparison_id'] + '.json')
                with stage.open('x', encoding='utf-8') as handle:
                    json.dump(report, handle, ensure_ascii=False, sort_keys=True, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                stat = stage.stat()
                result = {'id': report['comparison_id'], 'sha256': _sha(stage), 'identity': [stat.st_dev, stat.st_ino],
                          'marker': marker, 'status': report['status'], 'stage': stage.name}
                operation['result'] = result
                self.store.checkpoint(identifier, operation, token, require_uncancelled=True)
                def publish():
                    verify(spec['binding'])
                    # This read occurs while finish holds the ledger writer transaction.
                    if self.cancelled(identifier):
                        raise InterruptedError('Comparison cancelled before publication')
                    if not self._owned(stage, result, marker):
                        raise ValueError('Owned comparison stage changed')
                    os.link(stage, final, follow_symlinks=False)
                    _sync_directory(output)
                    return {'id': result['id'], 'revision': 1, 'sha256': result['sha256']}
                self.store.finish(identifier, 'complete', {'report_id': result['id'], 'domain_status': result['status']}, token, publish)
            evaluator(progress, lambda: self.cancelled(identifier), publish_report, spec['binding'])
            if self.store.get(identifier).state != 'completed':
                raise ValueError('Evaluator returned without durable report publication')
        except (StaleFencingToken, StaleRevision):
            # A stale owner may not end another attempt or remove its files.
            pass
        except Exception as exc:
            operation['error'] = str(exc)[:1000]
            try:
                self.store.checkpoint(identifier, operation, token)
                if self.store.get(identifier).state not in TERMINAL:
                    self.store.finish(identifier, 'abort' if isinstance(exc, InterruptedError) else 'fail', {'error': str(exc)[:1000]}, token)
            except (StaleFencingToken, StaleRevision, ValueError):
                pass
        finally:
            if stage is not None and result is not None and self._owned(stage, result, result['marker']):
                stage.unlink(missing_ok=True)
            if final is not None and result is not None and self.store.get(identifier).state != 'completed' and self._owned(final, result, result['marker']):
                final.unlink(missing_ok=True)
            with _LOCK:
                _LIVE.discard(self._live_key(identifier))
                _RUNNING.discard(self._live_key(identifier))


def release_dispatch(jobs, identifier, *, failed=False):
    with _LOCK:
        _LIVE.discard(jobs._live_key(identifier))

    if failed:
        operation = jobs.store.checkpoint_value(identifier)
        operation.update(dispatch_failed=True, error='Comparison dispatch failed; automatic restart is unsupported')
        jobs.store.checkpoint(identifier, operation)
