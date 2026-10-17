"""Per-app capabilities for explicit local-CPU partial runs, not native proof."""
import hashlib
import os
from pathlib import Path
import stat
import threading
import uuid

from fastapi import HTTPException


_CREATE_LOCK = threading.Lock()


def image_identity(path):
    """Read the same resolved regular file before issuing/consuming a capability."""
    path = Path(path).resolve(strict=True)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('Partial inspection image is not an ordinary file')
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        held = os.fstat(stream.fileno())
        if (held.st_dev, held.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('Partial inspection image changed while opening')
        for chunk in iter(lambda:stream.read(1024*1024), b''):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    named = path.lstat()
    fields = ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
    if any(getattr(value, field) != getattr(before, field)
           for value in (held, after, named) for field in fields):
        raise ValueError('Partial inspection image changed while reading')
    return str(path), digest.hexdigest()


def registry_for(app):
    with _CREATE_LOCK:
        registry = getattr(app.state, 'partial_CPU_runs', None)
        if registry is None:
            registry = PartialRunRegistry(app)
            app.state.partial_CPU_runs = registry
        if type(registry) is not PartialRunRegistry or registry.app is not app:
            raise HTTPException(409, 'Partial run registry belongs to another application')
        return registry


class PartialRunRegistry:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.rows = {}
        self.limit = 128

    def prepare(self, owner, binding):
        with self.lock:
            # Retire only already terminal business records, never work or an
            # unused capability. Capacity pressure cannot claim execution exit.
            if len(self.rows) >= self.limit:
                terminal = next((key for key, row in self.rows.items()
                                 if row.snapshot()['execution_finished']), None)
                if terminal is not None:
                    del self.rows[terminal]
            if len(self.rows) >= self.limit:
                raise HTTPException(503, 'Partial run capability capacity is busy')
            row = PartialRun(uuid.uuid4().hex, owner, binding)
            self.rows[row.run_id] = row
            return row.snapshot()

    def owned(self, run_id, owner):
        with self.lock:
            row = self.rows.get(run_id)
        if row is None or row.owner != owner:
            raise HTTPException(404, 'Partial run is unavailable in this project')
        return row


class PartialRun:
    def __init__(self, run_id, owner, binding):
        self.run_id = run_id
        self.owner = owner
        self.binding = binding
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.state = 'prepared'
        self.execution_finished = False
        self.worker = None
        self.publish = None
        self.validate_cache = None
        self.error_type = None

    def snapshot(self):
        with self.lock:
            return {'run_id':self.run_id, 'project_id':self.owner[2],
                    'stop_node_id':self.binding['stop_node_id'],
                    'execution_target':'local', 'device':'cpu',
                    'state':self.state, 'execution_finished':self.execution_finished,
                    'error_type':self.error_type}

    def consume(self, binding):
        with self.lock:
            if self.state != 'prepared' or binding != self.binding:
                raise HTTPException(409, 'Partial run capability was consumed or its input changed')
            self.state = 'running'
            self.worker = threading.get_ident()

    def cancel(self):
        with self.lock:
            if self.state == 'prepared':
                self.cancel_event.set()
                self.state = 'cancelled'
                self.execution_finished = True  # No worker has been admitted.
            elif self.state in ('running','cancelling'):
                self.cancel_event.set()
                self.state = 'cancelling'
            elif self.state != 'cancelled':
                raise HTTPException(409, 'Partial run already has a terminal outcome')
            return self.snapshot()

    def check(self):
        from backend.engine.flowchart_engine import FlowchartPartialCancelled
        if self.cancel_event.is_set():
            raise FlowchartPartialCancelled(self)

    def stage_cache(self, publish, validate):
        with self.lock:
            self.check()
            self.publish = publish
            self.validate_cache = validate

    def finish(self, result, binding):
        # This method is called only after the original handler has returned,
        # including engine/executor/device and compute-lease finally blocks.
        # Hash/input validation is outside the cancellation linearization lock.
        cache_valid = self.validate_cache is None or self.validate_cache()
        with self.lock:
            if self.worker != threading.get_ident() or self.state not in ('running','cancelling'):
                raise RuntimeError('Partial completion is not owned by its admitted worker')
            if binding != self.binding:
                raise HTTPException(409, 'Prepared partial input changed during execution')
            if self.cancel_event.is_set():
                self.publish = self.validate_cache = None
                self.state = 'cancelled'
                self.execution_finished = True
                return self.cancelled_result()
            if result.get('status') != 'partial' or result.get('stop_node_id') != self.binding['stop_node_id']:
                raise RuntimeError('Partial engine returned a different execution scope')
            if self.publish is not None and cache_valid:
                # The same lock makes cancellation and publication/commit one
                # choice. No model work, hashing, await or join occurs here.
                self.publish()
            elif not cache_valid and 'debug_cache' in result:
                result['debug_cache'] = {**result['debug_cache'], 'status':'bypassed'}
            self.publish = self.validate_cache = None
            self.state = 'completed'
            self.execution_finished = True
            return {**result, 'partial_run_id':self.run_id}

    def cancelled_after_unwind(self, exception):
        from backend.engine.flowchart_engine import FlowchartPartialCancelled
        with self.lock:
            if (type(exception) is not FlowchartPartialCancelled or exception.control is not self
                    or self.worker != threading.get_ident() or not self.cancel_event.is_set()
                    or self.state not in ('running','cancelling')):
                raise RuntimeError('Foreign cancellation cannot complete this partial run')
            self.publish = self.validate_cache = None
            self.state = 'cancelled'
            self.execution_finished = True
            return self.cancelled_result()

    def cancelled_result(self):
        return {'status':'cancelled', 'partial_run_id':self.run_id,
                'stop_node_id':self.binding['stop_node_id'],
                'final_verdict':'REVIEW', 'is_ok':False, 'crops':[],
                'execution_target':'local', 'execution_device':'cpu',
                'compute_profile_id':None}

    def failed_after_unwind(self, exception):
        with self.lock:
            self.publish = self.validate_cache = None
            self.error_type = type(exception).__name__
            self.state = 'failed'
            self.execution_finished = True
