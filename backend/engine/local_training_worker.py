"""Owned CLI execution for the app's basic trainer.

The preparation callback stays in the coordinator. Model construction and fit
run in a private child process with immutable inputs and durable control state.
Reattaching this process does not restore optimizer or RNG state.
"""
from __future__ import annotations

from dataclasses import asdict
import functools
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import psutil

from backend.engine.runtime_process_control import atomic_private_json, command_sha256, session_isolation
from backend.remote.file_replace import read_text
from backend.engine.global_store_paths import resolve_store_path, store_admission, owned_root

POLL_SECONDS = .05
OWNERSHIP_RECHECK_SECONDS = 1.
_WINDOWS = os.name == 'nt'
CANCEL_GRACE_SECONDS = 15.
CANCEL_TERMINATE_SECONDS = 5.
# A process created this long before the current boot cannot still be alive.
# The margin absorbs boot-time estimation and small wall-clock adjustments.
BOOT_MARGIN_SECONDS = 300.
_TERMINAL_STATES = ('completed', 'failed', 'aborted', 'interrupted')
_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


class LocalWorkerUncertain(RuntimeError):
    """The owned worker could not be reconciled; keep its compute claim."""


def _index():
    return resolve_store_path(Path(os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR') or Path.home() / '.modu_vision') / 'local_jobs')


def _save(journal):
    with _LOCK, store_admission(_index()):
        root = Path(journal['output_dir'])
        path = root / 'local_job.json'
        if path.is_file():
            previous = json.loads(path.read_text(encoding='utf-8'))
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
    """Acknowledge cancellation only after its run-local intent is saved; then send the cooperative signal (the cancel
    file the worker watches), recorded apart from the intent in the run journal and the job ledger (S1-04)."""
    with _LOCK:
        root = Path(record.output_dir)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = root / 'local_job.json'
        journal = None
        if path.is_file():
            journal = json.loads(path.read_text(encoding='utf-8'))
            if journal.get('job_id') != record.job_id:
                raise ValueError('Local cancel intent belongs to another job')
            journal.setdefault('cancel_requested_at', time.time())
            _save(journal)
        first_signal = not (root / 'local_cancel.json').exists()
        atomic_private_json(root / 'local_cancel.json', {'job_id': record.job_id, 'cancel_requested_at': time.time()})
        if journal is not None and not journal.get('cancel_signal_sent_at'):
            journal['cancel_signal_sent_at'] = time.time()
            try:
                _save(journal)
            except OSError:  # the cancel file is written: the signal was sent, and the stop goes on
                logger.exception('Could not record the cooperative signal sent to %s', record.job_id)
    if first_signal:
        _step(record, 'cancel_signalled', {'signal': 'cooperative'})


_SIGNAL_KEYS = {'terminate': 'cancel_terminate_sent_at', 'kill': 'cancel_kill_sent_at'}


def _step(record, event, payload):
    """Record one cancel or exit step in the job ledger, when the job has one (the worker process itself has none)."""
    step = getattr(getattr(record, 'ledger', None), 'step', None)
    if step is not None:
        step(event, payload)


def _record_signal(record, journal, name):
    """A terminate or kill this observer sent while cancelling, recorded once in the run journal and the ledger."""
    key = _SIGNAL_KEYS[name]
    if journal.get(key):
        return
    journal[key] = time.time()
    try:
        _save(journal)
    except OSError:
        logger.exception('Could not record the %s signal sent to %s', name, record.job_id)
    _step(record, 'cancel_signalled', {'signal': name})


def _cancelled(root, job_id):
    path = root / 'local_cancel.json'
    if not path.exists():
        return False
    return json.loads(read_text(path)).get('job_id') == job_id  # waits out the app's replacement of the file on Windows


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


@functools.lru_cache(maxsize=1)
def _boot_id():
    """Identifier of the current boot, when the platform exposes one.

    It cannot change while this process runs, so it is read once; observers
    check liveness every POLL_SECONDS.
    """
    try:
        if sys.platform.startswith('linux'):
            return Path('/proc/sys/kernel/random/boot_id').read_text(encoding='utf-8').strip() or None
        if sys.platform == 'darwin':
            value = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'], capture_output=True, text=True,
                                   encoding='utf-8', errors='replace', timeout=5).stdout.strip()
            return value or None
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _boot_evidence(journal):
    """'earlier' for a changed boot identifier, 'estimated_earlier' from boot
    time alone (journals without an identifier, Windows), otherwise None."""
    recorded, current = journal.get('owner_boot_id'), _boot_id()
    if recorded and current:
        return 'earlier' if recorded != current else None
    if journal['owner_created_at'] < psutil.boot_time() - BOOT_MARGIN_SECONDS:
        return 'estimated_earlier'
    return None


def _owned_members(journal):
    """Reconcile inherited marks in the worker's original private session.

    A changed boot identifier is conclusive. An estimated earlier boot never
    outweighs live processes that carry this run's token; it only lets an
    otherwise unrelated, unmarked session count as exited, without signals.
    """
    try:
        pid = journal['owner_pid']
        evidence = _boot_evidence(journal)
        if evidence == 'earlier':
            return []
        leader_proven = False
        reused_at = None  # start of another process now at the recorded number
        try:
            leader = psutil.Process(pid)
            leader_created = leader.create_time()
            leader_proven = abs(leader_created - journal['owner_created_at']) < .01
            if not leader_proven:
                # A stepped wall clock can move a live leader's reported start
                # time (Linux derives it from boot time), so its token decides.
                try:
                    token = leader.environ().get('MODU_VISION_LOCAL_WORKER_TOKEN')
                except psutil.AccessDenied:
                    # Another user's process cannot be this user's worker. On
                    # Windows a process with another start time at this number
                    # that this backend cannot open is a different process
                    # (Windows start times are absolute); the scan decides.
                    if _WINDOWS:
                        token = None
                    elif leader.username() != journal['owner_username']:
                        return []
                    else:
                        return None
                if token != journal['owner_token']:
                    # POSIX never reuses a PID while a process group or session
                    # with that ID exists, so another process at this PID proves
                    # the owned group ended. Windows keeps the token scan.
                    if not _WINDOWS:
                        return []
                    leader, reused_at = None, leader_created
        except psutil.NoSuchProcess:
            leader = None
        members = []
        denied = []
        unmarked = False
        for process in psutil.process_iter(['pid']):
            try:
                # The iterator caches Process objects across PID lifetimes.
                # Read the current identity before its ownership evidence.
                process = psutil.Process(process.pid)
                if not _WINDOWS:
                    if os.getsid(process.pid) != journal['owner_session']:
                        continue
                elif process.username() != journal['owner_username']:
                    continue
                if process.status() == psutil.STATUS_ZOMBIE:
                    continue
                token = process.environ().get('MODU_VISION_LOCAL_WORKER_TOKEN')
                if token != journal['owner_token']:
                    if not _WINDOWS:
                        unmarked = True  # never signalled; decides below
                    continue
                if process.create_time() + .01 < journal['owner_created_at']:
                    return None
                members.append(process)
            except (psutil.NoSuchProcess, ProcessLookupError):
                continue
            except psutil.AccessDenied:
                # Windows lists every account's processes, and a standard user
                # cannot open services or elevated ones: they are set aside and
                # decided below. On POSIX a denied process in the worker's own
                # session leaves its state unknown.
                if _WINDOWS:
                    denied.append(process)
                    continue
                return None
            except (psutil.Error, PermissionError):
                return None
        if denied:
            # A worker this backend cannot open is still its worker when it is
            # the proven leader (same number and start time), or was started by
            # the leader or a marked worker: a worker started while the app ran
            # elevated outlives a later, non-elevated backend. Its state is then
            # unknown; it is never reported as exited.
            # Windows keeps a parent's number after the parent exits and
            # reuses numbers quickly, so a parent number alone proves nothing:
            # as in psutil's children(), the child must have started after the
            # parent, and before any other process took the recorded number.
            # A worker from before this boot cannot be anyone's parent now.
            starts = {process.pid: process.create_time() for process in members}
            if evidence != 'estimated_earlier':
                starts.setdefault(pid, journal['owner_created_at'])
            for process in denied:
                try:
                    if process.pid == pid and leader_proven:
                        return None
                    parent = process.ppid()
                    if parent not in starts:
                        continue
                    created = process.create_time()
                except psutil.NoSuchProcess:
                    continue  # exited during the scan
                except psutil.Error:
                    return None
                if created + .01 >= starts[parent] and (parent != pid or reused_at is None or created < reused_at):
                    return None
        if members:
            # Marked workers are alive; a session shared with unmarked processes
            # stays unknown so nothing unmarked is ever signalled.
            return None if unmarked else sorted(members, key=lambda process: process.pid == pid, reverse=True)
        if unmarked:
            return [] if evidence == 'estimated_earlier' else None
        return []
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
        deadline = time.monotonic() + OWNERSHIP_RECHECK_SECONDS
        state = _liveness(self.journal)
        while state is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LocalWorkerUncertain('Local worker ownership could not be inspected during reconnection')
            time.sleep(min(POLL_SECONDS, remaining))
            if time.monotonic() >= deadline:
                raise LocalWorkerUncertain('Local worker ownership could not be inspected during reconnection')
            state = _liveness(self.journal)
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


def _restore_confirmed_terminal(manager, record, journal, root, leases):
    from backend.api.routes_training import _write_job_receipt
    record.status = record.phase = journal['status']
    status = {}
    status_path = root / 'status.json'
    try:
        if status_path.is_file() and not status_path.is_symlink():
            candidate = json.loads(status_path.read_text(encoding='utf-8'))
            if candidate.get('job_id') == record.job_id and candidate.get('spec_sha256') == journal['spec_sha256']:
                status = candidate
    except (OSError, ValueError):
        # A damaged progress file must not hide a confirmed terminal job.
        logger.exception('Ignoring unreadable local status for %s', record.job_id)
    for field in ('current_epoch', 'total_epochs', 'current_step', 'total_steps', 'train_loss', 'val_loss',
                  'best_metric', 'metrics', 'loss_history'):
        if field in status:
            setattr(record, field, status[field])
    record.result = {**(status.get('result') or {}), 'status': record.status, 'best_metric': status.get('best_metric'),
                     'worker_exit_confirmed': True, 'optimizer_resume': False}
    if record.status in ('failed', 'interrupted'):
        message = status.get('error') or (
            f"Owned worker exited before terminal publication (exit {journal.get('worker_exit_code')})"
            if record.status == 'interrupted' else None)
        if message:
            record.error = {'message': message}
    if not (root / 'job_receipt.json').is_file():
        try:
            _write_job_receipt(record)
        except Exception:
            logger.exception('Could not persist restored local receipt for %s', record.job_id)
    # Replaces a disconnected/stopping record (reconnect) or registers history.
    manager.restore_local_job(record)
    # Exit is confirmed, so a reservation left by an interrupted observer is stale.
    leases.release(record.job_id, terminal=True)


def recover_local_jobs(manager, *, job_id=None):
    """Observe persisted owned children; never replay preparation or training."""
    _recover_local_journals(manager, job_id=job_id)
    if job_id is None:
        # Startup: once workers are observed, every job the ledger accepted is accounted for and
        # none is launched again (S1-02). A ledger problem never blocks worker recovery.
        from backend.api.routes_training import reconcile_job_ledger
        try:
            reconcile_job_ledger(manager)
        except (OSError, sqlite3.Error, ValueError, KeyError):
            logger.exception('Could not reconcile the persistent job ledger after worker recovery')


def _recover_local_journals(manager, *, job_id=None):
    from backend.api.routes_training import JobRecord
    from backend.engine.shared_scheduler import ResourceLeases
    index = _index()
    if not index.is_dir():
        return
    for path in sorted(index.glob('job_*.json')):
        try:
            journal = json.loads(path.read_text(encoding='utf-8'))
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
            spec = json.loads(spec_path.read_text(encoding='utf-8'))
            if (spec['job_id'] != journal['job_id'] or Path(spec['output_dir']).resolve() != root
                    or resolve_store_path(spec['lease_path']).resolve() != manager._leases.path.resolve()):
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
            if journal.get('worker_exit_confirmed') is True and journal.get('status') in _TERMINAL_STATES:
                # An observer already confirmed the owned session exited. Restore
                # history only; probing its old PID could match another process.
                _restore_confirmed_terminal(manager, record, journal, root, leases)
                continue
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
    lease_path=leases.path
    installation,owner=owned_root(lease_path)
    if installation is not None:lease_path=installation/owner['scopes']['leases']
    return {'protocol_version': 1, 'job_id': record.job_id, 'task': record.task, 'preset': record.preset,
            'dataset_path': str(Path(record.dataset_path).resolve()), 'output_dir': str(Path(record.output_dir).resolve()),
            'config_overrides': overrides or {}, 'device': device, 'split_manifest_root': split_root,
            'warm_start': parent, 'dataset_binding': record.dataset_binding,
            'source_dataset_path': record.source_dataset_path, 'dataset_fingerprint': record.dataset_fingerprint,
            'lease_path': str(lease_path), 'lease_owner': leases.owner}


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
                                 stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, **session_isolation())
    record.process = child
    journal = {'protocol_version': 1, 'job_id': record.job_id, 'status': 'launching', 'task': record.task,
               'preset': record.preset, 'output_dir': str(root), 'spec_path': str(spec_path), 'spec_sha256': digest,
               'owner_pid': child.pid, 'owner_created_at': psutil.Process(child.pid).create_time(),
               'owner_command_sha256': command_sha256(command), 'owner_token': token,
               'owner_session': child.pid, 'owner_username': psutil.Process(child.pid).username(),
               'owner_boot_id': _boot_id(), 'optimizer_resume': False}
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
            sent = _stop_owned(journal)
            if not sent and _liveness(journal) is not False:
                raise LocalWorkerUncertain('Local training worker ownership remains uncertain')
            if sent:
                _record_signal(record, journal, 'terminate')
            try:
                _AttachedProcess(journal).wait(timeout=CANCEL_TERMINATE_SECONDS)
            except subprocess.TimeoutExpired:
                sent = _stop_owned(journal, force=True)
                if not sent and _liveness(journal) is not False:
                    raise LocalWorkerUncertain('Local training worker exit remains uncertain')
                if sent:
                    _record_signal(record, journal, 'kill')
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
            status = json.loads(read_text(status_path))  # waits out the worker's replacement of the file on Windows
            if status.get('job_id') != record.job_id or status.get('spec_sha256') != digest:
                raise ValueError('Local worker status differs from its immutable launch')
            _observe(record, status, callback, seen)
            acknowledged = status.get('cancel_acknowledged_at')
            if isinstance(acknowledged, (int, float)) and not journal.get('cancel_acknowledged_at'):
                journal['cancel_acknowledged_at'] = acknowledged
                try:
                    _save(journal)
                except OSError:
                    logger.exception('Could not record the cancel acknowledgement of %s', record.job_id)
                _step(record, 'cancel_acknowledged', {'acknowledged_at': acknowledged})
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
                status = json.loads(fallback.read_text(encoding='utf-8'))
                if (status.get('job_id') == record.job_id and status.get('spec_sha256') == digest
                        and status.get('status') in {'failed', 'aborted'}):
                    terminal = status
            state = 'aborted' if record.preparation_cancel.is_set() else terminal['status'] if terminal else 'interrupted'
            journal.update(status=state, worker_exit_confirmed=True, worker_exit_code=code)
            try:
                _save(journal)
            except OSError:
                pass
            _step(record, 'worker_exit_confirmed', {'exit_code': code, 'state': state})
            if state == 'failed':
                raise RuntimeError((terminal or {}).get('error') or f'Owned training worker exited without a terminal receipt (exit {code})')
            return {**((terminal or {}).get('result') or {}), 'status': state,
                    'best_metric': (terminal or {}).get('best_metric'), 'worker_exit_confirmed': True,
                    'error': None if terminal or state == 'aborted' else f'Owned worker exited before terminal publication (exit {code})',
                    'optimizer_resume': False}
        if cancellation_started is not None:
            now = time.monotonic()
            if terminated_at is None and now - cancellation_started >= CANCEL_GRACE_SECONDS:
                sent = _stop_owned(journal)
                if not sent and _liveness(journal) is not False:
                    raise RuntimeError('Local worker ownership could not be confirmed for cancellation')
                if sent:
                    _record_signal(record, journal, 'terminate')
                terminated_at = now
            elif terminated_at is not None and now - terminated_at >= CANCEL_TERMINATE_SECONDS:
                sent = _stop_owned(journal, force=True)
                if not sent and _liveness(journal) is not False:
                    raise RuntimeError('Local worker ownership could not be confirmed for final cancellation')
                if sent:
                    _record_signal(record, journal, 'kill')
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
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
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
            try:
                cancelled = _cancelled(root, spec['job_id'])
            except (OSError, ValueError):
                continue  # a cancel file being replaced is read again on the next tick
            if cancelled:
                # The worker's acknowledgement, recorded apart from the signals sent and from the confirmed exit (S1-04);
                # a status that is already terminal is kept (checked and written under the writer's lock). The trainer
                # stops even if recording the acknowledgement fails.
                try:
                    writer.acknowledge_cancel()
                finally:
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
            result = writer.update(status='aborted', cancel_acknowledged_at=time.time())
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
            journal = json.loads(path.read_text(encoding='utf-8'))
            journal.update(status=writer._payload['status'], training_terminal_confirmed=True)
            _save(journal)
        except (OSError, ValueError):
            pass
        # Teardown and owned descendants can outlive terminal publication. The
        # observer releases only after confirming the complete owned session.
        leases.mark_uncertain(spec['job_id'])
