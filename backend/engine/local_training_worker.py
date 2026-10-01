"""Owned CLI execution for the app's basic trainer.

The preparation callback stays in the coordinator. Model construction and fit
run in a private child process with immutable inputs and durable control state.
Reattaching this process does not restore optimizer or RNG state.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

import psutil

from backend.engine.runtime_process_control import atomic_private_json, command_sha256

POLL_SECONDS = .05
CANCEL_GRACE_SECONDS = 15.
CANCEL_TERMINATE_SECONDS = 5.
_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


class LocalWorkerUncertain(RuntimeError):
    """The owned worker could not be reconciled; keep its compute claim."""


def _index():
    return Path(os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR') or Path.home() / '.modu_vision') / 'local_jobs'


def _save(journal):
    with _LOCK:
        root = Path(journal['output_dir'])
        path = root / 'local_job.json'
        if path.is_file():
            previous = json.loads(path.read_text())
            if previous.get('job_id') == journal['job_id']:
                for key, value in previous.items():
                    if key.startswith('cancel_') and key not in journal:
                        journal[key] = value
        index = _index()
        index.mkdir(mode=0o700, parents=True, exist_ok=True)
        errors = []
        for destination in (path, index / (journal['job_id'] + '.json')):
            try:
                atomic_private_json(destination, journal)
            except OSError as exc:
                errors.append(exc)
        if len(errors) == 2:
            raise errors[0]


def request_local_cancellation(record):
    """Acknowledge cancellation only after its run-local intent is saved."""
    with _LOCK:
        root = Path(record.output_dir)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = root / 'local_job.json'
        if path.is_file():
            journal = json.loads(path.read_text())
            if journal.get('job_id') != record.job_id:
                raise ValueError('Local cancel intent belongs to another job')
            journal.setdefault('cancel_requested_at', time.time())
            _save(journal)
        atomic_private_json(root / 'local_cancel.json', {'job_id': record.job_id, 'cancel_requested_at': time.time()})


def _cancelled(root, job_id):
    path = root / 'local_cancel.json'
    if not path.exists():
        return False
    return json.loads(path.read_text()).get('job_id') == job_id


def _owned(journal):
    try:
        owner = psutil.Process(journal['owner_pid'])
        command = owner.cmdline()
        if (abs(owner.create_time() - journal['owner_created_at']) < .01
                and command_sha256(command) == journal['owner_command_sha256']
                and (command[1:4] == ['-m', 'backend.training_cli', 'basic-execute']
                     or command[1:3] == ['--basic-training-worker', 'basic-execute'])
                and command[command.index('--spec') + 1] == journal['spec_path']):
            return owner
    except (psutil.Error, KeyError, ValueError, IndexError, TypeError):
        pass
    return None


def _stop_owned(journal, *, force=False):
    if journal.get('owner_token'):
        members = _owned_members(journal)
        if members is None:
            return False
        try:
            for process in reversed(members):
                try:
                    process.kill() if force else process.terminate()
                except psutil.NoSuchProcess:
                    pass
            return True
        except psutil.Error:
            return False
    owner = _owned(journal)
    if owner is None:
        return False
    # psutil retains creation time for signaling. Snapshot only descendants of
    # the proven owner; never signal a device-wide process.
    try:
        descendants = owner.children(recursive=True)
        for process in reversed(descendants):
            try:
                process.kill() if force else process.terminate()
            except psutil.NoSuchProcess:
                pass
        owner.kill() if force else owner.terminate()
        return True
    except psutil.Error:
        return False


def _owned_members(journal):
    """Reconcile inherited marks in the worker's original private session."""
    try:
        pid = journal['owner_pid']
        try:
            leader = psutil.Process(pid)
            if abs(leader.create_time() - journal['owner_created_at']) >= .01:
                return None  # A reused session leader cannot establish ownership.
        except psutil.NoSuchProcess:
            leader = None
        members = []
        for process in psutil.process_iter(['pid']):
            try:
                if os.name != 'nt':
                    if os.getsid(process.pid) != journal['owner_session']:
                        continue
                elif process.username() != journal['owner_username']:
                    continue
                if process.status() == psutil.STATUS_ZOMBIE:
                    continue
                token = process.environ().get('MODU_VISION_LOCAL_WORKER_TOKEN')
                if token != journal['owner_token']:
                    if os.name != 'nt':
                        return None  # Never signal an unmarked session member.
                    continue
                if process.create_time() + .01 < journal['owner_created_at']:
                    return None
                members.append(process)
            except (psutil.NoSuchProcess, ProcessLookupError):
                continue
            except (psutil.Error, PermissionError):
                return None
        return sorted(members, key=lambda process: process.pid == pid, reverse=True)
    except (psutil.Error, OSError, KeyError, TypeError, ValueError):
        return None


def _liveness(journal):
    """Reused PID proves the old worker exited; denied inspection is unknown."""
    if journal.get('owner_token'):
        members = _owned_members(journal)
        return None if members is None else bool(members)
    try:
        owner = psutil.Process(journal['owner_pid'])
        if abs(owner.create_time() - journal['owner_created_at']) >= .01 or owner.status() == psutil.STATUS_ZOMBIE:
            return False
        if _owned(journal) is not None:
            return True
        # The original coordinator may reap the worker between the metadata
        # read and command read. Reconcile that exit instead of retaining a
        # claim merely because this observation crossed the exit boundary.
        if not owner.is_running() or owner.status() == psutil.STATUS_ZOMBIE:
            return False
        return None
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, KeyError, TypeError, ValueError):
        return None


class _AttachedProcess:
    def __init__(self, journal):
        self.journal = journal
        self.pid = journal['owner_pid']

    def poll(self):
        state = _liveness(self.journal)
        if state is None:
            raise LocalWorkerUncertain('Local worker ownership could not be inspected during reconnection')
        return None if state else -1

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            state = _liveness(self.journal)
            if state is False:
                return -1
            if time.monotonic() >= deadline:
                if state is None:
                    raise LocalWorkerUncertain('Local worker exit remains uncertain during bounded observation')
                raise subprocess.TimeoutExpired('owned local worker', timeout)
            time.sleep(POLL_SECONDS)


def recover_local_jobs(manager, *, job_id=None):
    """Observe persisted owned children; never replay preparation or training."""
    from backend.api.routes_training import JobRecord
    from backend.engine.shared_scheduler import ResourceLeases
    index = _index()
    if not index.is_dir():
        return
    for path in sorted(index.glob('job_*.json')):
        try:
            journal = json.loads(path.read_text())
            if job_id is not None and journal['job_id'] != job_id:
                continue
            existing = manager.get_job(journal['job_id'])
            if existing is not None and (job_id is None or existing.status not in ('disconnected', 'stopping')
                                         or existing.thread is not None and existing.thread.is_alive()):
                continue
            root = Path(journal['output_dir']).resolve()
            spec_path = Path(journal['spec_path'])
            if (spec_path.is_symlink() or spec_path.resolve() != root / 'local_spec.json'
                    or hashlib.sha256(spec_path.read_bytes()).hexdigest() != journal['spec_sha256']):
                raise ValueError('Recovered local launch specification differs from its journal')
            spec = json.loads(spec_path.read_text())
            if (spec['job_id'] != journal['job_id'] or Path(spec['output_dir']).resolve() != root
                    or Path(spec['lease_path']).resolve() != manager._leases.path.resolve()):
                raise ValueError('Recovered local specification differs from its job or reservation store')
            leases = ResourceLeases(spec['lease_path'], owner=spec['lease_owner'])
            record = JobRecord(journal['job_id'], spec['task'], spec['preset'], spec['dataset_path'], str(root), 'running',
                               phase='reconnecting', source_dataset_path=spec.get('source_dataset_path'),
                               dataset_fingerprint=spec.get('dataset_fingerprint'), dataset_binding=spec.get('dataset_binding'))
            if spec.get('warm_start'):
                from backend.engine.warm_start import WarmStartParent
                parent = dict(spec['warm_start'], checkpoint_path=Path(spec['warm_start']['checkpoint_path']),
                              classes=tuple(spec['warm_start']['classes']))
                record.warm_start = WarmStartParent(**parent)
            if journal.get('cancel_requested_at') or _cancelled(root, record.job_id):
                record.preparation_cancel.set()
            if _liveness(journal) is None:
                record.status = record.phase = 'disconnected'
                record.result = {'status': 'disconnected', 'optimizer_resume': False}
                leases.mark_uncertain(record.job_id)
                manager.restore_local_job(record)
                continue
            process = _AttachedProcess(journal)
            record.process = process
            def runner(callback, row=record, state=journal, child=process, directory=root):
                return _monitor_with_cleanup(row, callback, state, child, state['spec_sha256'], directory)
            manager.restore_local_job(record, runner=runner, leases=leases)
        except (OSError, ValueError, KeyError, TypeError):
            logger.exception('Could not recover local worker journal %s', path)
def _spec(record, overrides, device, split_root, leases):
    parent = asdict(record.warm_start) if record.warm_start else None
    if parent:
        parent['checkpoint_path'] = str(parent['checkpoint_path'])
        parent['classes'] = list(parent['classes'])
    return {'protocol_version': 1, 'job_id': record.job_id, 'task': record.task, 'preset': record.preset,
            'dataset_path': str(Path(record.dataset_path).resolve()), 'output_dir': str(Path(record.output_dir).resolve()),
            'config_overrides': overrides or {}, 'device': device, 'split_manifest_root': split_root,
            'warm_start': parent, 'dataset_binding': record.dataset_binding,
            'source_dataset_path': record.source_dataset_path, 'dataset_fingerprint': record.dataset_fingerprint,
            'lease_path': str(leases.path), 'lease_owner': leases.owner}


def _observe(record, status, callback, seen):
    record.phase = 'stopping' if record.preparation_cancel.is_set() else status['status']
    for field in ('current_epoch', 'total_epochs', 'current_step', 'total_steps', 'train_loss', 'val_loss',
                  'best_metric', 'metrics', 'loss_history'):
        if field in status:
            setattr(record, field, status[field])
    if not seen.get('started') and status.get('total_epochs'):
        callback.on_training_start({'epochs': status['total_epochs'], 'device': status.get('device'), 'job_id': record.job_id})
        seen['started'] = True
    for row in status.get('loss_history') or []:
        if row['epoch'] > seen.get('epoch', 0):
            callback.on_epoch_end(row['epoch'] - 1, status.get('total_epochs', 0), row['train_loss'],
                                  row.get('val_loss'), row.get('lr', 0), status.get('metrics') or {})
            seen['epoch'] = row['epoch']
    # Callback wrappers append epochs. The worker's persisted history is the
    # canonical list, including epochs seen while the renderer was closed.
    record.loss_history = status.get('loss_history') or []
    step = (status.get('current_epoch'), status.get('current_step'))
    if step != seen.get('step') and status.get('current_step') and status.get('train_loss') is not None:
        callback.on_step_end(status['current_step'] - 1, status.get('total_steps', 0), status['train_loss'],
                             max(0, status.get('current_epoch', 1) - 1))
        seen['step'] = step


def run_owned_training(record, callback, *, config_overrides, device, split_manifest_root, leases):
    root = Path(record.output_dir).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ('local_spec.json', 'local_job.json', 'local_worker.log', 'status.json', 'local_cancel.json'):
        if (root / name).is_symlink():
            raise ValueError('Local worker storage is linked')
    if (root / 'local_job.json').exists():
        raise ValueError('An existing local job cannot be relaunched; create a new run')
    spec_path = root / 'local_spec.json'
    atomic_private_json(spec_path, _spec(record, config_overrides, device, split_manifest_root, leases))
    digest = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    command = [sys.executable, *(['--basic-training-worker'] if getattr(sys, 'frozen', False) else ['-m', 'backend.training_cli']),
               'basic-execute', '--spec', str(spec_path), '--launch-handshake']
    token = uuid.uuid4().hex
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', MODU_VISION_LOCAL_WORKER_TOKEN=token)
    with (root / 'local_worker.log').open('ab') as log:
        os.chmod(root / 'local_worker.log', 0o600)
        child = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[2], env=environment,
                                 stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    record.process = child
    journal = {'protocol_version': 1, 'job_id': record.job_id, 'status': 'launching', 'task': record.task,
               'preset': record.preset, 'output_dir': str(root), 'spec_path': str(spec_path), 'spec_sha256': digest,
               'owner_pid': child.pid, 'owner_created_at': psutil.Process(child.pid).create_time(),
               'owner_command_sha256': command_sha256(command), 'owner_token': token,
               'owner_session': child.pid, 'owner_username': psutil.Process(child.pid).username(), 'optimizer_resume': False}
    try:
        _save(journal)
        child.stdin.write((record.job_id + '\n').encode())
        child.stdin.close()
    except Exception:
        child.stdin.close()
        child.terminate()
        child.wait(timeout=5)
        raise
    return _monitor_with_cleanup(record, callback, journal, child, digest, root)


def _monitor_with_cleanup(record, callback, journal, child, digest, root):
    """Both fresh and reattached observers must reconcile a live child on error."""
    try:
        return _monitor_owned_training(record, callback, journal, child, digest, root)
    except Exception:
        if _liveness(journal) is not False:
            try:
                request_local_cancellation(record)
            except Exception:
                logger.exception('Could not persist cancellation while reconciling failed local observer %s', record.job_id)
            if not _stop_owned(journal) and _liveness(journal) is not False:
                raise LocalWorkerUncertain('Local training worker ownership remains uncertain')
            try:
                _AttachedProcess(journal).wait(timeout=CANCEL_TERMINATE_SECONDS)
            except subprocess.TimeoutExpired:
                if not _stop_owned(journal, force=True) and _liveness(journal) is not False:
                    raise LocalWorkerUncertain('Local training worker exit remains uncertain')
                try:
                    _AttachedProcess(journal).wait(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    raise LocalWorkerUncertain('Local training worker exit remains unconfirmed after cancellation') from exc
        raise


def _monitor_owned_training(record, callback, journal, child, digest, root):
    terminal = None
    seen = {}
    cancellation_started = None
    terminated_at = None
    while True:
        if record.preparation_cancel.is_set() and cancellation_started is None:
            request_local_cancellation(record)
            cancellation_started = time.monotonic()
        status_path = root / 'status.json'
        if status_path.is_file():
            status = json.loads(status_path.read_text())
            if status.get('job_id') != record.job_id or status.get('spec_sha256') != digest:
                raise ValueError('Local worker status differs from its immutable launch')
            _observe(record, status, callback, seen)
            if status['status'] in {'completed', 'failed', 'aborted'}:
                terminal = status
        try:
            code = child.poll()
        except LocalWorkerUncertain:
            if terminal is None:
                raise
            # A process may erase argv while exiting before psutil reports a
            # zombie. Observe a published terminal result for a bounded period;
            # never signal an identity that became uncertain in this interval.
            code = child.wait(timeout=5)
        if code is not None:
            if _liveness(journal) is not False:
                if not _stop_owned(journal) and _liveness(journal) is not False:
                    raise LocalWorkerUncertain('Owned session remains unprovable after leader exit')
                try:
                    _AttachedProcess(journal).wait(timeout=CANCEL_TERMINATE_SECONDS)
                except subprocess.TimeoutExpired:
                    if not _stop_owned(journal, force=True):
                        raise LocalWorkerUncertain('Owned session cleanup remains unprovable after leader exit')
                    try:
                        _AttachedProcess(journal).wait(timeout=5)
                    except subprocess.TimeoutExpired as exc:
                        raise LocalWorkerUncertain('Owned session exit remains unconfirmed after leader exit') from exc
            # Reserved terminal blocks handle a disk-full stale progress file.
            fallback = root / 'terminal_status.json'
            if terminal is None and fallback.is_file():
                status = json.loads(fallback.read_text())
                if (status.get('job_id') == record.job_id and status.get('spec_sha256') == digest
                        and status.get('status') in {'failed', 'aborted'}):
                    terminal = status
            state = 'aborted' if record.preparation_cancel.is_set() else terminal['status'] if terminal else 'interrupted'
            journal.update(status=state, worker_exit_confirmed=True, worker_exit_code=code)
            try:
                _save(journal)
            except OSError:
                pass
            if state == 'failed':
                raise RuntimeError((terminal or {}).get('error') or f'Owned training worker exited without a terminal receipt (exit {code})')
            return {**((terminal or {}).get('result') or {}), 'status': state,
                    'best_metric': (terminal or {}).get('best_metric'), 'worker_exit_confirmed': True,
                    'error': None if terminal or state == 'aborted' else f'Owned worker exited before terminal publication (exit {code})',
                    'optimizer_resume': False}
        if cancellation_started is not None:
            now = time.monotonic()
            if terminated_at is None and now - cancellation_started >= CANCEL_GRACE_SECONDS:
                if not _stop_owned(journal) and _liveness(journal) is not False:
                    raise RuntimeError('Local worker ownership could not be confirmed for cancellation')
                terminated_at = now
            elif terminated_at is not None and now - terminated_at >= CANCEL_TERMINATE_SECONDS:
                if not _stop_owned(journal, force=True) and _liveness(journal) is not False:
                    raise RuntimeError('Local worker ownership could not be confirmed for final cancellation')
                _AttachedProcess(journal).wait(timeout=5)
        time.sleep(POLL_SECONDS)


def execute_basic(spec_path):
    """CLI entry: build and fit the same basic trainer in this owned process."""
    from backend.remote.worker import _StatusWriter, _TrainingStatusCallback, _failed_status
    from backend.engine.trainer import UnifiedAutoMLTrainer
    from backend.engine.dataset_loaders import split_root_scope
    from backend.engine.training_provenance import validate_training_binding
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.engine.warm_start import WarmStartParent
    from backend.api.routes_training import JobRecord, _write_job_receipt
    from backend.engine.device import clear_device_cache
    spec_path = Path(spec_path).resolve()
    spec = json.loads(spec_path.read_text())
    root = spec_path.parent
    if spec.get('protocol_version') != 1 or Path(spec['output_dir']).resolve() != root:
        raise ValueError('Basic training specification differs from its owned directory')
    descriptor = os.open(root / 'local_worker.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    digest = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    writer = _StatusWriter(root, spec['job_id'], spec_sha256=digest)
    callback = _TrainingStatusCallback(writer)
    event = threading.Event()
    stop = threading.Event()
    trainer = None
    leases = ResourceLeases(spec['lease_path'], owner=spec['lease_owner'])
    def watch():
        while not stop.wait(.05):
            if _cancelled(root, spec['job_id']):
                event.set()
                if trainer is not None:
                    trainer.abort()
                return
    def heartbeat():
        while not stop.wait(5):
            leases.heartbeat(spec['job_id'])
    threading.Thread(target=watch, daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: request_local_cancellation(type('OwnedJob', (), {'output_dir': str(root), 'job_id': spec['job_id']})()))
    signal.signal(signal.SIGINT, lambda *_: event.set())
    try:
        parent = spec.get('warm_start')
        if parent:
            parent = dict(parent, checkpoint_path=Path(parent['checkpoint_path']), classes=tuple(parent['classes']))
            parent = WarmStartParent(**parent)
        if _cancelled(root, spec['job_id']):
            result = writer.update(status='aborted')
        else:
            validate_training_binding(spec.get('dataset_binding'))
            trainer = UnifiedAutoMLTrainer(task=spec['task'], dataset_path=spec['dataset_path'], output_dir=root,
                                         preset=spec['preset'], device=spec.get('device'), callback=callback,
                                         config_overrides=spec.get('config_overrides'), warm_start=parent)
            if event.is_set() or _cancelled(root, spec['job_id']):
                trainer.abort()
                result = writer.update(status='aborted')
            else:
                writer.update(status='running')
                with split_root_scope(spec.get('split_manifest_root')):
                    trained = trainer.train(job_id=spec['job_id'])
                validate_training_binding(spec.get('dataset_binding'))
                state = 'aborted' if event.is_set() or _cancelled(root, spec['job_id']) else trained.get('status', 'failed')
                if state not in {'completed', 'failed', 'aborted'}:
                    raise ValueError('Basic trainer returned an unsupported terminal state')
                result = writer.update(status=state, best_metric=trained.get('best_metric'), result=trained)
        record = JobRecord(spec['job_id'], spec['task'], spec['preset'], spec['dataset_path'], str(root), result['status'],
                           source_dataset_path=spec.get('source_dataset_path'), dataset_fingerprint=spec.get('dataset_fingerprint'),
                           warm_start=parent, dataset_binding=spec.get('dataset_binding'))
        for field in ('current_epoch', 'total_epochs', 'current_step', 'total_steps', 'train_loss', 'val_loss', 'best_metric', 'metrics', 'loss_history'):
            if field in result:
                setattr(record, field, result[field])
        _write_job_receipt(record)
        return result
    except Exception as exc:
        return _failed_status(writer, exc, root)
    finally:
        stop.set()
        clear_device_cache()
        path = root / 'local_job.json'
        try:
            journal = json.loads(path.read_text())
            journal.update(status=writer._payload['status'], training_terminal_confirmed=True)
            _save(journal)
        except (OSError, ValueError):
            pass
        # Teardown and owned descendants can outlive terminal publication. The
        # observer releases only after confirming the complete owned session.
        leases.mark_uncertain(spec['job_id'])
