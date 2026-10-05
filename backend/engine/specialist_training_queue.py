"""Admission for native specialist adapters, using the common ledger and devices.

Priority ordering covers the live native specialist cohort. Other executors keep
their dispatchers; their reservations and project quotas still block this one.
Python closures cannot be replayed after a backend exit: saved jobs remain
observable, and are interrupted rather than silently starting a new training.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import threading
import uuid

import psutil

from fastapi import HTTPException

from backend.contracts.context import get_project_context
from backend.engine.job_scheduler import JobScheduler
from backend.engine.job_state import ACTIVE
from backend.engine.job_store import JobConflict, UnknownJob, ledger
from backend.engine.shared_scheduler import shared_leases

_LOCK = threading.RLock()
_READY = {}
_KEY = re.compile(r'[A-Za-z0-9._:-]{1,128}\Z')
_INSTANCE = uuid.uuid4().hex


class NativeAdmission:
    def __init__(self, store, scheduler, identifier, output, actor, event):
        self.store, self.scheduler = store, scheduler
        self.job_id, self.output, self.actor, self.event = identifier, Path(output), actor, event
        self.key = (str(store.path), identifier)
        self.lease = None
        self._heartbeat_error = None
        self._ownership_lock = threading.RLock()
        self.identity = {'native_worker': True, 'owner_pid': os.getpid(),
                         'owner_created_at': psutil.Process().create_time(), 'owner_instance': _INSTANCE}

    def request_cancel(self, reason='user requested cancellation'):
        self.store.request_cancel(self.job_id, self.actor, reason)
        self.event.set()

    def check(self):
        if self._heartbeat_error is not None:
            raise RuntimeError('Specialist ownership heartbeat failed') from self._heartbeat_error
        if self.store.cancel_intent(self.job_id):
            self.event.set()
        if self.event.is_set():
            raise InterruptedError('Training cancelled')
        if self.lease is not None:
            self.store.checkpoint(self.job_id, self.identity, self.lease.fence, require_uncancelled=True)

    def complete(self, publish):
        with self._ownership_lock:
            return self._complete(publish)

    def _complete(self,publish):
        self.check()
        def guarded_publish():
            # finish holds the ledger's write lock; a cancel intent accepted before
            # that lock is observed, and a later attempt cannot race publication.
            if self.event.is_set() or self.store.cancel_intent(self.job_id):
                raise InterruptedError('Training cancelled before publication')
            publish()
        self.scheduler.publish_result(self.lease, 'completed', publish=guarded_publish)

    def abandon(self, error):
        with _LOCK:
            try:
                if self.store.get(self.job_id).state in ACTIVE:
                    if self.lease is not None:
                        self.scheduler.publish_result(self.lease, 'failed', {'reason': str(error)})
                    else:
                        self.store.finish(self.job_id, 'fail', {'reason': str(error)})
            finally:
                _READY.pop(self.key, None)

    def finish_error(self, error):
        with self._ownership_lock:
            return self._finish_error(error)

    def _finish_error(self,error):
        if self.store.get(self.job_id).state not in ACTIVE: return
        outcome = 'aborted' if isinstance(error, InterruptedError) or type(error).__name__ == 'RotatedTrainingCancelled' else 'failed'
        if self.event.is_set() and not self.store.cancel_intent(self.job_id):
            self.store.request_cancel(self.job_id, self.actor, str(error))
        if self.lease is not None:
            self.scheduler.publish_result(self.lease, outcome, {'reason': str(error)})
        else:
            self.store.finish(self.job_id, 'abort' if outcome == 'aborted' else 'fail', {'reason': str(error)})

    @contextmanager
    def scope(self):
        done = threading.Event()
        heartbeat = None
        try:
            with _LOCK:
                _READY[self.key] = self
            while self.lease is None:
                self.check()
                with _LOCK:
                    _dispatch(self)
                if self.lease is None:
                    self.event.wait(.03)
            self.check()
            def refresh():
                while not done.wait(max(.01, min(5, self.scheduler.lease_seconds/3))):
                    try:
                        with self._ownership_lock:
                            if self.store.get(self.job_id).state not in ACTIVE:return
                            self.lease = self.scheduler.heartbeat(self.lease)
                            if self.store.cancel_intent(self.job_id): self.event.set()
                    except Exception as exc:
                        self._heartbeat_error = exc
                        self.event.set()  # Only this adapter's cooperative worker.
                        return
            heartbeat = threading.Thread(target=refresh, daemon=True, name=f'native-lease-{self.job_id[:8]}')
            heartbeat.start()
            yield self
            if self.store.get(self.job_id).state in ACTIVE:
                self.finish_error(RuntimeError('Native worker left without publishing a completion'))
        except BaseException as exc:
            # No reservation is returned until the owned runner has unwound.
            # A stale fence cannot finish or release a newer attempt's devices.
            self.finish_error(exc)
            raise
        finally:
            done.set()
            if heartbeat is not None: heartbeat.join()
            with _LOCK:
                _READY.pop(self.key, None)


def _dispatch(admission):
    candidates = [value for key, value in _READY.items()
                  if key[0] == str(admission.store.path) and value.lease is None and not value.event.is_set()]
    if not candidates: return
    lease = admission.scheduler.claim_job(f'native:{os.getpid()}:{_INSTANCE}',
        {'hosts': ['local-compute'], 'job_ids': [value.job_id for value in candidates]})
    if lease is not None:
        _READY[(str(admission.store.path), lease.job_id)].lease = lease


def reserve(request, project, task, output, options, event):
    """Reserve identity and queue controls before the native journal or thread."""
    key = request.headers.get('Idempotency-Key')
    if key is not None and not _KEY.fullmatch(key):
        raise HTTPException(422, 'Idempotency-Key must be 1-128 letters, digits or . _ : -')
    context = get_project_context(request)
    store = ledger()
    scheduler = JobScheduler(store, shared_leases())
    output = Path(output).resolve()
    spec = {**options.model_dump(), 'task': task, 'output_root': str(output.parent)}
    try:
        ref = store.submit(context, request.app.state.context_registry.project_key(context),
            'specialist_training', spec, key, job_id=output.name, output_dir=str(output),
            parent_id=getattr(options, 'warm_start_job_id', None),
            registry_root=str(request.app.state.context_registry.root), project_dir=project['project_dir'])
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if not ref.created:
        row = store.record(ref.id)
        replay = store.response(ref.id) or {'job_id': ref.id, 'status': ref.state}
        for name in ('job.json', 'job_state.json'):
            path = Path(row['output_dir']) / name
            if path.is_file() and not path.is_symlink():
                saved = json.loads(path.read_text(encoding='utf-8'))
                if saved.get('job_id') == ref.id: replay = saved
        return None, {**replay, **queue_status(row['output_dir']), 'idempotent_replay': True}
    admission = NativeAdmission(store, scheduler, ref.id, output, context.actor_id, event)
    try:
        with _LOCK:
            scheduler.enqueue(ref.id, ref.revision, priority=options.priority,
                resources={'host': 'local-compute', 'selector': 'all'},
                budget={'max_runtime_s': options.max_runtime_s} if options.max_runtime_s is not None else {})
            _READY[admission.key] = admission
            if not options.queue:
                _dispatch(admission)
                if admission.lease is None:
                    admission.abandon('Local device is busy; queue is disabled')
                    raise HTTPException(409, 'Local device is busy; queue is disabled')
        return admission, None
    except BaseException as exc:
        admission.abandon(exc)
        raise


def queue_status(output):
    """Only an exact output-bound row may enrich a native project's readback."""
    output = Path(output).resolve()
    store = ledger()
    try: row = store.record(output.name)
    except UnknownJob: return {}
    if row['kind'] != 'specialist_training' or Path(row['output_dir']).resolve() != output: return {}
    queued=JobScheduler(store, None,lease_seconds=30)._ordered(
        item for item in store.queued() if item['kind']=='specialist_training' and item['project_key']==row['project_key'])
    view=next(({'position':position,'wait_reason':item['wait_reason'] or 'priority'}
               for position,item in enumerate(queued,1) if item['id']==output.name),{})
    return {'priority': row['priority'], 'queue_position': view.get('position'),
            'wait_reason': view.get('wait_reason'), 'ledger_state': row['state']}


def cancel_owned(output):
    with _LOCK:
        admission = _READY.get((str(ledger().path), Path(output).name))
        if admission is not None and admission.output == Path(output).resolve():
            admission.request_cancel()
        else:
            store=ledger()
            try:row=store.record(Path(output).name)
            except UnknownJob:return
            if row['kind']=='specialist_training' and Path(row['output_dir']).resolve()==Path(output).resolve():
                store.request_cancel(row['id'],row['actor_id'],'user requested cancellation')


def interrupt_unlaunched(output):
    """Reconcile a lost closure only without an attempt, or with proven owner exit.

    A PID alone is insufficient. A matching process creation time means the
    owner may still be executing; unavailable identity leaves its devices held.
    """
    store=ledger()
    try:row=store.record(Path(output).name)
    except UnknownJob:return
    if (row['kind']=='specialist_training' and Path(row['output_dir']).resolve()==Path(output).resolve()
            and row['state'] in {'accepted','queued'} and not store.attempts(row['id'])):
        store.finish(row['id'],'interrupt',{'reason':'Native adapter stopped before launch; explicit new submission required'})
    elif row['kind']=='specialist_training' and Path(row['output_dir']).resolve()==Path(output).resolve() and row['state'] in ACTIVE:
        identity=store.checkpoint_value(row['id'])
        if identity.get('native_worker') is not True or not identity.get('owner_pid') or not identity.get('owner_created_at'):return
        try:
            if psutil.Process(identity['owner_pid']).create_time()==identity['owner_created_at']:return
        except psutil.NoSuchProcess:pass
        except (psutil.AccessDenied, OSError):return
        attempts=store.attempts(row['id'])
        if not attempts:return
        from backend.engine.job_scheduler import AttemptLease
        last=attempts[-1]
        lease=AttemptLease(row['id'],last['number'],last['fencing_token'],0,last['worker_id'],json.loads(row['resources_json']))
        JobScheduler(store,shared_leases()).publish_result(lease,'aborted' if store.cancel_intent(row['id']) else 'interrupted',
            {'reason':'Native process identity proves owner exit; no automatic training replay'})
