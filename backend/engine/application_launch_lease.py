"""Durable POSIX launch admission for one committed owned app/database pair.

This foundation does not implement a native app handshake or model inference.
Only an unspawned reservation can be cleared. A successful spawn, failed spawn
attempt, supervisor crash or observed direct-child exit keeps update admission
closed until a future verifiable process-tree reconciliation adapter exists.
"""
from contextlib import contextmanager
import errno
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import socket
import subprocess
import time
import uuid

import psutil

from backend.engine.global_store_paths import owned_root, store_admission
from backend.engine.process_isolation import session_isolation

ACTIVE_LEASE = 'application-launch-lease.json'
LEASES = '.application-launches'
RUNTIME_HOMES = '.application-runtime-homes'
DATABASE_LOCK = 'application-database-ownership.lock'
TRANSITION_LOCK = 'transition.lock'
CONTROL_PATHS = {ACTIVE_LEASE, LEASES, DATABASE_LOCK, RUNTIME_HOMES, '.application-writer-epochs'}
STATES = {'reserved', 'starting', 'ready', 'exited', 'recovery_required'}


class LaunchLeaseError(ValueError):
    pass


class LeaseTransitionBusy(LaunchLeaseError):
    """Only original nonblocking flock contention; no ownership admission."""


def _update():
    from backend.engine import runtime_update
    return runtime_update


def _checkpoint(point):
    """Durable fault boundaries; no application or process operation."""


def _refuse(reason):
    raise LaunchLeaseError('Application launch ownership requires recovery: '+reason)


def _identity(pid):
    process = psutil.Process(pid)
    return {'pid': pid, 'created_at': process.create_time(),
        'command_sha256': hashlib.sha256(_update()._canonical(process.cmdline())).hexdigest()}


def _capture_original_child_identity(child):
    """Bound the first identity only; never adopt or repair a recorded process.

    A freshly exec'd Linux child can temporarily expose an empty command line.
    Retain the sole original Popen and first observed birth while waiting for
    two equal nonempty samples. Every read rechecks fresh birth, parent, session
    and the original handle. Ambiguity keeps the persisted spawn intent closed.
    """
    deadline = time.monotonic()+.5
    pid = child.pid
    birth = psutil.Process(pid).create_time()
    empty_command = hashlib.sha256(_update()._canonical([])).hexdigest()
    previous = None

    def original():
        if child.pid != pid or child.poll() is not None: raise psutil.NoSuchProcess(pid)
        fresh = psutil.Process(pid)
        if fresh.create_time() != birth or fresh.ppid() != os.getpid():
            raise LaunchLeaseError('Original child birth or parent changed during startup')
        if os.getsid(pid) != pid or os.getpgid(pid) != pid:
            raise LaunchLeaseError('Original child session changed during startup')

    while True:
        if time.monotonic() >= deadline: raise LaunchLeaseError('Original child identity sampling deadline exceeded')
        original()
        observed = _identity(pid)
        original()
        if (not _identity_shape(observed) or observed['pid'] != pid or observed['created_at'] != birth):
            raise LaunchLeaseError('Original child identity changed during startup')
        if time.monotonic() >= deadline: raise LaunchLeaseError('Original child identity sampling deadline exceeded')
        if observed['command_sha256'] == empty_command:
            if previous is not None: raise LaunchLeaseError('Original child command changed during startup')
        elif previous is not None:
            if observed != previous: raise LaunchLeaseError('Original child command changed during startup')
            return observed
        else: previous = observed
        remaining = deadline-time.monotonic()
        if remaining <= 0: raise LaunchLeaseError('Original child identity sampling deadline exceeded')
        time.sleep(min(.005, remaining))


def _identity_shape(value):
    return (isinstance(value, dict) and set(value) == {'pid', 'created_at', 'command_sha256'}
        and type(value['pid']) is int and value['pid'] > 0
        and type(value['created_at']) in (int, float) and math.isfinite(value['created_at']) and value['created_at'] > 0
        and _update()._hex(value['command_sha256']))


def _runtime_home_environment(root, nonce):
    """Derive one retained private runtime home; caller environment has no authority.

    The lease has persisted its only spawn attempt before this operation. These
    directories are retained on errors/exits because tree reconciliation has not
    been proved. No cache from a previous nonce is reused or removed.
    """
    if os.name != 'posix': raise LaunchLeaseError('Owned runtime home requires POSIX directory handles')
    descriptors = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def directory(parent, name, *, fresh=False):
        try: os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            if fresh: raise LaunchLeaseError('Owned runtime home nonce already exists')
        fd = os.open(name, flags, dir_fd=parent); descriptors.append(fd)
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise LaunchLeaseError('Owned runtime home must be a private original directory')
        return fd

    try:
        root_fd = os.open(root, flags); descriptors.append(root_fd)
        _, owner = owned_root(root)
        root_info = os.fstat(root_fd)
        if (root_info.st_dev, root_info.st_ino) != (owner['root_identity']['device'], owner['root_identity']['inode']):
            raise LaunchLeaseError('Owned runtime home installation identity changed')
        homes_fd = directory(root_fd, RUNTIME_HOMES)
        home_fd = directory(homes_fd, nonce, fresh=True)
        cache_fd = directory(home_fd, 'cache', fresh=True)
        config_fd = directory(home_fd, 'config', fresh=True)
        directory(home_fd, 'tmp', fresh=True)
        for name in ('torch', 'huggingface', 'matplotlib'): directory(cache_fd, name, fresh=True)
        directory(config_fd, 'yolo', fresh=True)
        home = root/RUNTIME_HOMES/nonce
        # Directory creation is descriptor-relative. Refuse a changed published
        # path before handing the fixed absolute paths to the owned executable.
        _update()._unlinked(home)
        current = home.lstat(); original = os.fstat(home_fd)
        if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
            raise LaunchLeaseError('Owned runtime home identity changed before spawn')
        cache = home/'cache'
        return {'HOME': str(home), 'USERPROFILE': str(home), 'CFFIXED_USER_HOME': str(home),
            'TMPDIR': str(home/'tmp'), 'TMP': str(home/'tmp'), 'TEMP': str(home/'tmp'),
            'XDG_CACHE_HOME': str(cache), 'XDG_CONFIG_HOME': str(home/'config'),
            'TORCH_HOME': str(cache/'torch'), 'HF_HOME': str(cache/'huggingface'),
            'MPLCONFIGDIR': str(cache/'matplotlib'), 'YOLO_CONFIG_DIR': str(home/'config/yolo'),
            'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
            'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'none',
            'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
    except OSError as exc:
        raise LaunchLeaseError('Owned runtime home could not be admitted: '+type(exc).__name__) from exc
    finally:
        for fd in reversed(descriptors): os.close(fd)


def _paths(root):
    update = _update()
    for name in CONTROL_PATHS: update._unlinked(root/name)


def _lock_identity_shape(value):
    return (isinstance(value, dict) and set(value) == {'device', 'inode'}
        and type(value['device']) is int and value['device'] >= 0
        and type(value['inode']) is int and value['inode'] > 0)


def _validate_lock_stat(st, identity):
    if os.name != 'posix': raise LaunchLeaseError('Application launch transitions require POSIX native locks')
    if (not _lock_identity_shape(identity) or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
            or st.st_size != 0 or stat.S_IMODE(st.st_mode) != 0o600 or st.st_uid != os.geteuid()
            or identity != {'device': st.st_dev, 'inode': st.st_ino}):
        _refuse('transition lock identity changed')


def _record(root, nonce, *, record=None):
    update = _update(); directory = update._unlinked(root/LEASES/nonce)
    if not update._hex(nonce, 32) or not directory.is_dir(): _refuse('missing lease history')
    if record is None: record = update._json(update._read(directory/'journal.json'))
    names = {'schema_version', 'protocol_version', 'nonce', 'revision', 'state', 'binding', 'supervisor', 'process',
        'spawn_attempted', 'claimed', 'ready_receipt_sha256', 'exit_observation', 'known_image_receipt_sha256', 'reason', 'transition_lock_identity'}
    if isinstance(record, dict) and record.get('protocol_version') in (3, 4): names.add('cpu_execution')
    if isinstance(record, dict) and record.get('protocol_version') == 4: names.add('writer_drain')
    if (not isinstance(record, dict) or set(record) != names or type(record['schema_version']) is not int
            or record['schema_version'] != 1 or type(record['protocol_version']) is not int or record['protocol_version'] not in (2, 3, 4)
            or record['nonce'] != nonce or type(record['revision']) is not int or record['revision'] < 1
            or record['state'] not in STATES or not _identity_shape(record['supervisor'])
            or record['process'] is not None and not _identity_shape(record['process'])
            or type(record['spawn_attempted']) is not bool or type(record['claimed']) is not bool
            or record['reason'] is not None and (not isinstance(record['reason'], str) or len(record['reason']) > 500)):
        _refuse('invalid lease journal')
    lock = update._unlinked(directory/TRANSITION_LOCK)
    try: current_lock = lock.stat()
    except OSError as exc: raise LaunchLeaseError('Application launch ownership requires recovery: transition lock is missing') from exc
    _validate_lock_stat(current_lock, record['transition_lock_identity'])
    binding = record['binding']; _, owner = owned_root(root)
    fields = {'installation_id', 'update_id', 'application_generation', 'database_pointer', 'database_generation_path',
        'executable', 'executable_sha256', 'application_manifest_sha256', 'source_sha256', 'envelope_sha256',
        'authority_path', 'authority_sha256', 'version', 'runtime_packs'}
    if (not isinstance(binding, dict) or set(binding) != fields or binding['installation_id'] != owner['installation_id']
            or not update._hex(binding['update_id'], 32) or binding['application_generation'] != binding['update_id']
            or any(not update._hex(binding[key]) for key in ('executable_sha256', 'application_manifest_sha256',
                'source_sha256', 'envelope_sha256', 'authority_sha256'))): _refuse('foreign lease binding')
    for key in ('ready_receipt_sha256', 'known_image_receipt_sha256'):
        if record[key] is not None and not update._hex(record[key]): _refuse('invalid receipt identity')
    intent = directory/'spawn-intent.json'; update._unlinked(intent)
    if record['spawn_attempted']:
        if not intent.exists(): _refuse('spawn ownership intent is missing')
        expected = {'schema_version': 1, 'nonce': nonce, 'binding_sha256': update._sha(update._canonical(binding))}
        if update._canonical(update._json(update._read(intent))) != update._canonical(expected): _refuse('spawn ownership intent changed')
    elif intent.exists(): _refuse('unrecorded spawn intent')
    if record['state'] in {'reserved', 'exited'} and (record['spawn_attempted'] or record['process'] is not None or record['claimed']):
        _refuse('contradictory unspawned lease')
    if record['state'] == 'exited' and update._canonical(record['exit_observation']) != update._canonical({'never_spawned': True}): _refuse('unproved process-tree exit')
    if record['state'] == 'reserved' and record['exit_observation'] is not None: _refuse('invalid reserved exit observation')
    if record['state'] in {'starting', 'ready'} and not record['spawn_attempted']: _refuse('missing spawn admission')
    if record['state'] == 'ready' and (not record['claimed'] or record['process'] is None or record['ready_receipt_sha256'] is None):
        _refuse('unbound readiness receipt')
    expected_members = {'journal.json', TRANSITION_LOCK} | ({'spawn-intent.json'} if record['spawn_attempted'] else set())
    bootstrap = directory/'bootstrap-receipt.json'; update._unlinked(bootstrap)
    if bootstrap.exists():
        expected_members.add('bootstrap-receipt.json')
        raw = update._read(bootstrap)
        receipt = update._json(raw)
        names = {'schema_version', 'kind', 'nonce', 'binding', 'main_process', 'backend_process',
            'backend_executable', 'backend_executable_sha256', 'backend_build_identity_sha256', 'backend_frozen',
            'challenge_sha256', 'epoch'}
        if (record['ready_receipt_sha256'] is None or update._sha(raw) != record['ready_receipt_sha256']
                or not isinstance(receipt, dict) or set(receipt) != names
                or type(receipt['schema_version']) is not int or receipt['schema_version'] != 1
                or receipt['kind'] != 'authenticated_controller_binding_only' or receipt['nonce'] != nonce
                or update._canonical(receipt['binding']) != update._canonical(binding)
                or update._canonical(receipt['main_process']) != update._canonical(record['process'])
                or not _identity_shape(receipt['backend_process'])
                or not isinstance(receipt['backend_executable'], str)
                or not update._hex(receipt['backend_executable_sha256'])
                or receipt['backend_build_identity_sha256'] is not None and not update._hex(receipt['backend_build_identity_sha256'])
                or type(receipt['backend_frozen']) is not bool or not update._hex(receipt['challenge_sha256'])
                or not update._hex(receipt['epoch'], 32)):
            _refuse('authenticated bootstrap receipt changed or unbound')
    if record['known_image_receipt_sha256'] is not None:
        expected_members.add('known-image-receipt.json')
        if update._sha(update._read(directory/'known-image-receipt.json')) != record['known_image_receipt_sha256']:
            _refuse('known-image receipt changed')
    if record.get('cpu_execution') is not None:
        from backend.engine.application_launch_execution import validate_sealed_execution
        expected_members.update(validate_sealed_execution(record, directory))
    if record.get('protocol_version') == 4:
        if record['state'] == 'exited': _refuse('writer epoch release has no qualified adapter')
        drain = record['writer_drain']
        required = {'writer_id', 'registration_sha256', 'registration_registry_sha256', 'phase', 'request', 'receipt', 'backend_exit'}
        if (not isinstance(drain, dict) or set(drain) != required or not update._hex(drain['writer_id'], 32)
                or any(not update._hex(drain[k]) for k in ('registration_sha256', 'registration_registry_sha256'))
                or not isinstance(drain['phase'], str) or drain['phase'] not in {'enrolled', 'closing', 'drained', 'backend_exited', 'refused'}):
            _refuse('invalid writer drain admission')
        if drain['phase'] == 'enrolled' and any(drain[k] is not None for k in ('request', 'receipt', 'backend_exit')):
            _refuse('unrequested writer drain evidence')
        if drain['phase'] != 'enrolled':
            from backend.engine.application_launch_handshake import validate_drain_request, validate_drain_receipt, validate_backend_exit
            if not bootstrap.exists(): _refuse('writer drain has no authenticated backend')
            boot = update._json(update._read(bootstrap))
            validate_drain_request(drain['request'], nonce=nonce, epoch=boot['epoch'], binding=binding,
                writer_id=drain['writer_id'], registration_sha256=drain['registration_sha256'])
            if drain['receipt'] is not None:
                validate_drain_receipt(drain['receipt'], drain['request'], backend_process=boot['backend_process'])
            if drain['phase'] in {'drained', 'backend_exited'} and (drain['receipt'] is None or drain['receipt']['status'] != 'managed_scopes_drained'):
                _refuse('unproved managed backend drain')
            if drain['backend_exit'] is not None:
                validate_backend_exit(drain['backend_exit'], drain['request'], boot['backend_process'])
            if (drain['phase'] == 'backend_exited') != (drain['backend_exit'] is not None):
                _refuse('unbound original backend exit observation')
    if {p.name for p in directory.iterdir()} != expected_members: _refuse('unknown lease history member')
    return record


def _load(root):
    update = _update(); _paths(root); pointer = root/ACTIVE_LEASE; history = root/LEASES
    if not pointer.exists():
        if history.exists() and (not history.is_dir() or any(history.iterdir())): _refuse('lease history has no ownership pointer')
        return None
    current = update._json(update._read(pointer)); _, owner = owned_root(root)
    if (not isinstance(current, dict) or set(current) != {'schema_version', 'installation_id', 'nonce', 'revision', 'record_sha256'}
            or type(current['schema_version']) is not int or current['schema_version'] != 1
            or current['installation_id'] != owner['installation_id'] or not update._hex(current['nonce'], 32)
            or type(current['revision']) is not int or not update._hex(current['record_sha256']) or not history.is_dir()):
        _refuse('invalid ownership pointer')
    active = None
    for directory in history.iterdir():
        update._unlinked(directory)
        if not directory.is_dir() or not update._hex(directory.name, 32): _refuse('unknown ownership history')
        record = _record(root, directory.name)
        if directory.name == current['nonce']:
            if record['revision'] != current['revision'] or update._sha(update._canonical(record)) != current['record_sha256']:
                _refuse('ownership publication interrupted or changed')
            active = record
        elif record['state'] != 'exited': _refuse('another unresolved launch history')
    if active is None: _refuse('current launch history is missing')
    return active


def _publication(record):
    return {'schema_version': 1, 'installation_id': record['binding']['installation_id'],
        'nonce': record['nonce'], 'revision': record['revision'], 'record_sha256': _update()._sha(_update()._canonical(record))}


def _write(root, record, *, initial=False, expected=None):
    update = _update(); directory = root/LEASES/record['nonce']
    if initial:
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        fd = os.open(directory/TRANSITION_LOCK, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            st = os.fstat(fd); record['transition_lock_identity'] = {'device': st.st_dev, 'inode': st.st_ino}
            _validate_lock_stat(st, record['transition_lock_identity']); os.fsync(fd)
        finally: os.close(fd)
        update.migration._sync_directories(root/LEASES, recursive=False)
    else:
        if expected is None: _refuse('missing lease publication compare-and-swap snapshot')
        _checkpoint('before_lease_compare_and_swap')
        if (update._read(directory/'journal.json') != expected['journal']
                or update._read(root/ACTIVE_LEASE) != expected['pointer']):
            _refuse('lease publication changed before compare-and-swap')
        # Validate the next exact member set, including only this operation's
        # bound sidecar. Fresh admissions never tolerate unpublished sidecars.
        _record(root, record['nonce'], record=record)
    update._write(directory/'journal.json', record)
    if initial: _checkpoint('after_reserved_journal')
    else: _checkpoint('after_transition_journal')
    update._write(root/ACTIVE_LEASE, _publication(record))


@contextmanager
def _transition_admission(root, nonce=None):
    """Installation shared admission then the pinned per-lease mutex.

    Reserve/start/cancel retain exclusive installation admission. Live backend
    lifespan can retain its shared fence while the original controller changes
    only its lease journal. Nonblocking acquisition never upgrades or waits.
    """
    if os.name != 'posix': raise LaunchLeaseError('Application launch transitions require POSIX native locks')
    import fcntl
    update = _update()
    with store_admission(root):
        if nonce is None:
            pointer = update._unlinked(root/ACTIVE_LEASE)
            if not pointer.exists():
                _load(root)  # Missing pointer plus history is ambiguous.
                yield
                return
            hint = update._json(update._read(pointer)); nonce = hint.get('nonce') if isinstance(hint, dict) else None
        if not update._hex(nonce, 32): _refuse('invalid transition nonce')
        directory = update._unlinked(root/LEASES/nonce)
        path = update._unlinked(directory/TRANSITION_LOCK)
        try: fd = os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        except OSError as exc: raise LaunchLeaseError('Application launch ownership requires recovery: transition lock is unavailable') from exc
        locked = False
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != 0: _refuse('invalid transition lock')
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    raise LeaseTransitionBusy('Lease transition is busy; retry after it completes') from exc
                raise LaunchLeaseError('Lease transition lock failed; ownership requires recovery') from exc
            locked = True
            # Bounded hints only precede the mutex. Full record/member/pointer
            # validation is performed by the caller after acquisition.
            hint = update._json(update._read(directory/'journal.json'))
            identity = hint.get('transition_lock_identity') if isinstance(hint, dict) else None
            _validate_lock_stat(before, identity); _validate_lock_stat(path.stat(), identity)
            yield
            update._unlinked(path); after = os.fstat(fd); named = path.stat()
            _validate_lock_stat(after, identity); _validate_lock_stat(named, identity)
            fields = ('st_dev', 'st_ino', 'st_nlink', 'st_size', 'st_mode', 'st_uid', 'st_mtime_ns', 'st_ctime_ns')
            if any(getattr(st, field) != getattr(before, field) for st in (after, named) for field in fields):
                _refuse('transition lock changed during publication')
        finally:
            if locked: fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


@contextmanager
def _database_lock(root, *, create=True):
    if os.name != 'posix': raise LaunchLeaseError('Application launch ownership currently requires POSIX native locks')
    import fcntl
    path = _update()._unlinked(root/DATABASE_LOCK)
    if not create and not path.exists():
        yield None; return
    fd = os.open(path, os.O_RDWR | (os.O_CREAT if create else 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), 0o600)
    locked = False
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 1: _refuse('invalid database ownership lock')
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc: raise LaunchLeaseError('Application launch ownership database lock remains held') from exc
        locked = True; current = path.stat()
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino): _refuse('database ownership lock changed')
        yield fd
    finally:
        if locked: fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def assert_quiescent(root):
    """Fail closed under the same reentrant exclusive cutover admission.

    Suitable for owned migration/adoption mutation callers, including the
    pre-owner admission phase. It never creates a lock on a pristine root.
    """
    root = _update()._unlinked(Path(root).absolute()); found, owner = owned_root(root)
    if owner is None:
        if any((root/name).exists() or (root/name).is_symlink() for name in CONTROL_PATHS): _refuse('unowned launch control')
        return {'status': 'quiescent', 'nonce': None}
    if found != root: _refuse('explicit installation root required')
    with store_admission(root, exclusive=True):
        epochs = _update()._unlinked(root/'.application-writer-epochs')
        if epochs.exists():
            if not epochs.is_dir(): _refuse('writer epoch control is not a directory')
            with os.scandir(epochs) as entries:
                if next(entries, None) is not None: _refuse('writer epoch ownership remains unresolved')
        row = _load(root)
        if row is not None and row['state'] != 'exited': _refuse('durable '+row['state']+' lease')
        with _database_lock(root, create=False): pass
        return {'status': 'quiescent', 'nonce': row['nonce'] if row else None}


def _public(row):
    return {**row, 'native_app_handshake_verified': False, 'actual_application_inference_verified': False,
        'known_image_execution_qualified': False, 'model_quality_approved': False, 'release_ready': False}


def inspect_launch(root):
    root, _ = _update()._root(root)
    with _transition_admission(root):
        row = _load(root)
        return _public(row) if row else {'state': 'absent', 'release_ready': False}


class LaunchSupervisor:
    """One local supervisor capability; durable state survives capability loss.

    No API accepts arbitrary arguments, environment, executable or DB paths.
    Readiness/claim are controller binding checks, not a native app handshake.
    A reopened object cannot claim a lost OS child handle or clear its lease.
    """
    def __init__(self, root, nonce):
        self.root, _ = _update()._root(root)
        if not _update()._hex(nonce, 32): raise LaunchLeaseError('Invalid launch nonce')
        self.nonce = nonce; self._process = None; self._lock = None; self._snapshot = None
        self._bootstrap_channel = None
        self._bootstrap_transport = None
        self._writer_epoch = None

    @classmethod
    def reserve(cls, root, authority, *, pinned_authority_sha256):
        update = _update(); root, _ = update._root(root)
        with store_admission(root, exclusive=True):
            assert_quiescent(root)
            binding = update._launch_binding(root, authority, pinned_authority_sha256=pinned_authority_sha256)
            nonce = uuid.uuid4().hex
            row = {'schema_version': 1, 'protocol_version': 3, 'nonce': nonce, 'revision': 1, 'state': 'reserved',
                'binding': binding, 'supervisor': _identity(os.getpid()), 'process': None, 'spawn_attempted': False,
                'claimed': False, 'ready_receipt_sha256': None, 'exit_observation': None,
                'known_image_receipt_sha256': None, 'reason': None, 'transition_lock_identity': None, 'cpu_execution': None}
            _write(root, row, initial=True)
            return cls(root, nonce)

    def _owned(self):
        row = _load(self.root)
        if row is None or row['nonce'] != self.nonce: raise LaunchLeaseError('Launch nonce differs from active owner')
        if row['supervisor'] != _identity(os.getpid()): raise LaunchLeaseError('Original supervisor process birth differs; recovery required')
        update = _update(); journal = update._read(self.root/LEASES/self.nonce/'journal.json'); pointer = update._read(self.root/ACTIVE_LEASE)
        if (update._canonical(update._json(journal)) != update._canonical(row)
                or update._canonical(update._json(pointer)) != update._canonical(_publication(row))):
            _refuse('lease publication changed while capturing snapshot')
        self._snapshot = {'record': update._canonical(row), 'journal': journal, 'pointer': pointer}
        return row

    def _binding(self, row):
        fresh = _update()._launch_binding(self.root, row['binding']['authority_path'], pinned_authority_sha256=row['binding']['authority_sha256'])
        if _update()._canonical(fresh) != _update()._canonical(row['binding']): raise LaunchLeaseError('Committed launch binding changed')

    def _persist(self, row, **changes):
        if self._snapshot is None or self._snapshot['record'] != _update()._canonical(row): _refuse('stale lease publication snapshot')
        row = {**row, **changes, 'revision': row['revision']+1}
        _write(self.root, row, expected=self._snapshot); return _public(row)

    def cancel(self):
        with store_admission(self.root, exclusive=True):
            row = self._owned()
            if row['state'] != 'reserved' or row['spawn_attempted']: raise LaunchLeaseError('A started or ambiguous launch cannot be cancelled')
            if row.get('writer_drain') is not None or self._writer_epoch is not None:
                raise LaunchLeaseError('An enrolled writer epoch cannot be cancelled or released')
            self._binding(row)
            return self._persist(row, state='exited', exit_observation={'never_spawned': True})

    def start(self, *, bootstrap=False):
        if type(bootstrap) is not bool: raise TypeError('Bootstrap must be a boolean')
        with store_admission(self.root, exclusive=True):
            row = self._owned()
            if row['state'] != 'reserved': raise LaunchLeaseError('Only an unspawned reservation can start')
            self._binding(row)
            self._lock = _database_lock(self.root); self._lock.__enter__()
            directory = self.root/LEASES/self.nonce
            _update()._write(directory/'spawn-intent.json', {'schema_version': 1, 'nonce': self.nonce,
                'binding_sha256': _update()._sha(_update()._canonical(row['binding']))})
            row = self._persist(row, state='starting', spawn_attempted=True)
            # Persisted starting admission precedes the only spawn operation.
            _checkpoint('before_spawn')
            child_channel = None
            try:
                environment = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'VISION_AI_STUDIO_USER_DATA_DIR': str(self.root),
                    'VISION_APPLICATION_LAUNCH_NONCE': self.nonce, 'VISION_APPLICATION_GENERATION': row['binding']['application_generation'],
                    'VISION_APPLICATION_DATABASE_GENERATION': row['binding']['database_generation_path']}
                environment.update(_runtime_home_environment(self.root, self.nonce))
                pass_fds = ()
                if bootstrap:
                    self._bootstrap_channel, child_channel = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
                    self._bootstrap_channel.set_inheritable(False)
                    child_channel.set_inheritable(False)
                    child_stat = os.fstat(child_channel.fileno())
                    # macOS reports the socket device sentinel as signed -1 in
                    # Python and uint64 in Node. Decimal strings avoid coercion.
                    self._bootstrap_transport = {'device': str(child_stat.st_dev & ((1 << 64)-1)), 'inode': str(child_stat.st_ino),
                        'family': 'AF_UNIX', 'type': 'SOCK_STREAM', 'anonymous': True}
                    environment['VISION_APPLICATION_LAUNCH_FD'] = str(child_channel.fileno())
                    pass_fds = (child_channel.fileno(),)
                self._process = subprocess.Popen([row['binding']['executable']], env=environment,
                    cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    close_fds=True, pass_fds=pass_fds, **session_isolation())
                _checkpoint('after_spawn')
                identity = _capture_original_child_identity(self._process)
                return self._persist(self._owned(), process=identity)
            except BaseException as exc:
                # Even a pre-exec error retains the intent; no false no-spawn proof.
                current = self._owned()
                self._persist(current, state='recovery_required', reason='Spawn/identity observation interrupted: '+type(exc).__name__)
                if isinstance(exc, psutil.Error): return _public(self._owned())
                raise
            finally:
                if child_channel is not None: child_channel.close()

    def enroll_backend_writer(self):
        """Create-only original controller capability; never reconstruct from JSON."""
        from backend.engine.application_launch_quiescence import WriterEpoch
        with store_admission(self.root, exclusive=True):
            row = self._owned(); self._binding(row)
            if row['state'] != 'reserved' or row['spawn_attempted'] or self._writer_epoch is not None:
                raise LaunchLeaseError('Backend writer enrollment must precede the only main spawn')
            epoch = WriterEpoch.create(self.root, self.nonce, expected_launch_sha256=_update()._sha(_update()._canonical(row)))
            registration = epoch.enroll('backend', expected_registry_sha256=epoch.snapshot()['registry_sha256'])
            snapshot = epoch.snapshot()
            drain = {'writer_id': registration.writer_id, 'registration_sha256': registration.registration_sha256,
                'registration_registry_sha256': snapshot['registry_sha256'], 'phase': 'enrolled',
                'request': None, 'receipt': None, 'backend_exit': None}
            self._writer_epoch = epoch
            return self._persist(self._owned(), protocol_version=4, writer_drain=drain)

    def publish_writer_drain(self, *, phase, request=None, receipt=None, backend_exit=None):
        with _transition_admission(self.root, self.nonce):
            row = self._owned(); self._binding(row)
            if row['protocol_version'] != 4 or self._writer_epoch is None:
                raise LaunchLeaseError('Writer drain requires the original enrolled controller')
            old = row['writer_drain']
            transitions = {'enrolled': {'closing'}, 'closing': {'drained', 'refused'}, 'drained': {'backend_exited'}}
            if phase not in transitions.get(old['phase'], set()): raise LaunchLeaseError('Writer drain replay or phase differs')
            if phase != 'backend_exited': self._live(row, row['process'])
            updated = {**old, 'phase': phase}
            for key, value in (('request', request), ('receipt', receipt), ('backend_exit', backend_exit)):
                if value is not None:
                    if old[key] is not None: raise LaunchLeaseError('Writer drain evidence replay differs')
                    updated[key] = value
            return self._persist(row, writer_drain=updated)

    def _live(self, row, process):
        if self._process is None: raise LaunchLeaseError('Original supervisor OS child handle is unavailable')
        if (process is None or not _identity_shape(process)
                or _update()._canonical(row['process']) != _update()._canonical(process) or self._process.pid != process['pid']):
            raise LaunchLeaseError('Child process identity differs')
        try: observed = _identity(self._process.pid)
        except psutil.Error as exc: raise LaunchLeaseError('Child process ownership is ambiguous') from exc
        if self._process.poll() is not None or observed != process: raise LaunchLeaseError('Child process birth or command changed')
        if os.getsid(process['pid']) != process['pid'] or os.getpgid(process['pid']) != process['pid']:
            raise LaunchLeaseError('Owned child process session changed')

    def claim(self, process):
        with _transition_admission(self.root, self.nonce):
            row = self._owned(); self._binding(row)
            if row['state'] != 'starting': raise LaunchLeaseError('Claim requires starting admission')
            self._live(row, process)
            return self._persist(row, claimed=True)

    def ready(self, binding, process):
        with _transition_admission(self.root, self.nonce):
            row = self._owned(); self._binding(row)
            if row['state'] != 'starting' or not row['claimed']: raise LaunchLeaseError('Readiness requires the claimed starting child')
            if _update()._canonical(binding) != _update()._canonical(row['binding']): raise LaunchLeaseError('Readiness app/database binding differs')
            self._live(row, process)
            receipt = {'nonce': self.nonce, 'binding': binding, 'process': process, 'kind': 'controller_binding_only'}
            return self._persist(row, state='ready', ready_receipt_sha256=_update()._sha(_update()._canonical(receipt)))

    def _publish_bootstrap(self, receipt):
        """Publish only after this original controller verifies private channels."""
        with _transition_admission(self.root, self.nonce):
            row = self._owned(); self._binding(row)
            if row['state'] != 'starting' or not row['claimed'] or self._bootstrap_channel is None:
                raise LaunchLeaseError('Authenticated bootstrap requires the original private channel')
            self._live(row, row['process'])
            path = self.root/LEASES/self.nonce/'bootstrap-receipt.json'
            if path.exists(): _refuse('unpublished bootstrap receipt requires recovery')
            _update()._write(path, receipt)
            _checkpoint('after_bootstrap_receipt')
            return self._persist(row, state='ready', ready_receipt_sha256=_update()._sha(_update()._read(path)))

    def recovery(self, reason):
        with _transition_admission(self.root, self.nonce):
            row = self._owned()
            if row['state'] in {'reserved', 'exited'}: raise LaunchLeaseError('Recovery requires a spawn attempt')
            return self._persist(row, state='recovery_required', reason=str(reason)[:500])

    def _begin_cpu_execution(self, request, capability):
        """Seal the original controller's one request before any CPU dispatch."""
        update = _update()
        with _transition_admission(self.root, self.nonce):
            row=self._owned();self._binding(row);self._live(row,row['process'])
            if row['state']!='ready' or row.get('cpu_execution') is not None or self._bootstrap_channel is None:
                raise LaunchLeaseError('CPU execution requires one original authenticated ready controller')
            directory=self.root/LEASES/self.nonce; path=directory/'cpu-execution-intent.json'
            if path.exists():_refuse('unpublished CPU execution intent requires recovery')
            intent={'schema_version':1,'kind':'owned_cpu_execution_intent','request':request,'capability':capability}
            update._write(path,intent);_checkpoint('after_cpu_execution_intent')
            state={'request_id':request['request_id'],'request_sha256':update._sha(update._canonical(request)),
                'intent_sha256':update._sha(update._read(path)),'receipt_sha256':None}
            self._persist(row,protocol_version=max(3,row['protocol_version']),cpu_execution=state)
            return intent

    def _publish_cpu_execution(self, receipt):
        """Exact publication retry only; no redispatch, repair or quality claim."""
        update=_update()
        with _transition_admission(self.root,self.nonce):
            row=self._owned();self._binding(row);self._live(row,row['process'])
            state=row.get('cpu_execution')
            if row['state']!='ready' or state is None or self._bootstrap_channel is None:
                raise LaunchLeaseError('CPU receipt requires original authenticated ready admission')
            from backend.engine.application_launch_execution import recheck_receipt_artifacts
            recheck_receipt_artifacts(self.root,row,receipt)
            path=self.root/LEASES/self.nonce/'cpu-execution-receipt.json'; prior=state['receipt_sha256']
            raw=update._canonical(receipt)
            if len(raw)>65536:raise LaunchLeaseError('CPU receipt exceeds its bound')
            if prior is not None:
                existing=update._read(path)
                if update._sha(existing)!=prior or update._canonical(update._json(existing))!=raw:
                    raise LaunchLeaseError('Another CPU execution receipt is already bound')
                return prior
            if path.exists():_refuse('unpublished CPU execution receipt requires recovery')
            update._write(path,receipt);_checkpoint('after_cpu_execution_receipt')
            digest=update._sha(update._read(path))
            self._persist(row,cpu_execution={**state,'receipt_sha256':digest})
            return digest

    def observe_exit(self):
        with _transition_admission(self.root, self.nonce):
            row = self._owned()
            if self._process is None: raise LaunchLeaseError('Original supervisor OS child handle is unavailable')
            returncode = self._process.poll()
            if returncode is None: raise LaunchLeaseError('Direct child has not exited')
            return self._persist(row, state='recovery_required', reason='Process-tree exit is unverified',
                exit_observation={'direct_child_pid': self._process.pid, 'direct_child_returncode': returncode,
                    'process_tree_exit_verified': False})

    def known_image(self, receipt, *, expected):
        """Bind a supplied receipt to exact local artifacts; never infer execution."""
        update = _update()
        with _transition_admission(self.root, self.nonce):
            row = self._owned(); self._binding(row)
            if row['state'] != 'ready': raise LaunchLeaseError('Receipt binding requires ready controller admission')
            receipt = update._json(update._canonical(receipt)); expected = update._json(update._canonical(expected))
            fields = {'schema_version', 'status', 'nonce', 'binding', 'process', 'checkpoint', 'input', 'output'}
            if (not isinstance(receipt, dict) or set(receipt) != fields or type(receipt['schema_version']) is not int
                    or receipt['schema_version'] != 1 or receipt['status'] != 'succeeded' or receipt['nonce'] != self.nonce
                    or update._canonical(receipt['binding']) != update._canonical(row['binding'])): raise LaunchLeaseError('Known-image receipt binding differs')
            self._live(row, receipt['process'])
            if not isinstance(expected, dict) or set(expected) != {name+'_sha256' for name in ('checkpoint', 'input', 'output')}:
                raise LaunchLeaseError('Independent checkpoint/input/output pins are required')
            for name in ('checkpoint', 'input', 'output'):
                item = receipt[name]; digest = expected[name+'_sha256']
                if not isinstance(item, dict) or set(item) != {'path', 'sha256'} or not update._hex(digest) or item['sha256'] != digest:
                    raise LaunchLeaseError('Known-image '+name+' pin differs')
                relative = update._safe_path(item['path']); file = update._unlinked(self.root/relative)
                if not file.is_relative_to(self.root) or relative.split('/')[0] in CONTROL_PATHS:
                    raise LaunchLeaseError('Known-image '+name+' path escapes owned artifacts')
                with update._file(file, 1024**3) as (reader, before):
                    hash_ = hashlib.sha256(); total = 0
                    while chunk := reader.read(min(1024**2, before.st_size-total+1)):
                        total += len(chunk)
                        if total > before.st_size: raise LaunchLeaseError('Known-image '+name+' grew while reading')
                        hash_.update(chunk)
                    if total != before.st_size or hash_.hexdigest() != digest: raise LaunchLeaseError('Known-image '+name+' bytes differ')
            raw = update._canonical(receipt)
            if len(raw) > 65536: raise LaunchLeaseError('Known-image receipt is unbounded')
            prior = row['known_image_receipt_sha256']; receipt_path = self.root/LEASES/self.nonce/'known-image-receipt.json'
            if prior is not None:
                existing = update._read(receipt_path)
                if update._sha(existing) != prior or update._canonical(update._json(existing)) != raw:
                    raise LaunchLeaseError('Another known-image receipt is already bound')
                stored = prior
            else:
                update._write(receipt_path, receipt)
                _checkpoint('after_known_image_receipt')
                # Store actual serialized bytes; retries preserve that snapshot.
                stored = update._sha(update._read(receipt_path))
                self._persist(row, known_image_receipt_sha256=stored)
            return {'status': 'artifact_binding_verified', 'nonce': self.nonce, 'receipt_sha256': stored,
                'actual_application_inference_verified': False, 'known_image_execution_qualified': False,
                'native_app_handshake_verified': False, 'model_quality_approved': False, 'release_ready': False}

    def close(self):
        """Release only this supervisor's OS lock; durable ownership remains blocked."""
        # Busy acquisition leaves an in-flight transition's DB handle untouched.
        # Once admitted, ambiguous journal errors still release only this handle.
        with _transition_admission(self.root, self.nonce):
            try:
                if self._process is not None:
                    row = self._owned()
                    if row['state'] != 'recovery_required': self._persist(row, state='recovery_required', reason='Supervisor handle released; process-tree ownership unverified')
            finally:
                if self._bootstrap_channel is not None:
                    self._bootstrap_channel.close(); self._bootstrap_channel = None
                if self._lock is not None:
                    self._lock.__exit__(None, None, None); self._lock = None


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    # Reservation requires the persistent caller to retain its capability and
    # OS handles. A short-lived CLI must not create an immediately orphaned lease.
    parser.add_argument('command', choices=('inspect', 'assert-quiescent'))
    parser.add_argument('--root', required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect_launch(args.root) if args.command == 'inspect' else assert_quiescent(args.root)
        print(json.dumps(result)); return 0
    except (ValueError, OSError, psutil.Error) as exc:
        print(json.dumps({'status': 'refused', 'error': str(exc)})); return 2


if __name__ == '__main__': raise SystemExit(main())
