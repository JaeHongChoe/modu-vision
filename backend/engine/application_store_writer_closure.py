"""Original managed writer closure; never whole operating-system tree release.

The live finalizer supplies an in-memory, one-use provisional capability. Only
its original controller can publish after the retained main has exited zero.
Historical data alone cannot mint that capability. Later mutation admission
independently checks the controller's exit and the original database OFD lock.
"""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import copy
import inspect
import os
from pathlib import Path
import stat
import threading
import time
import weakref

import psutil

from backend.engine import application_launch_lease as lease
from backend.engine import application_shutdown_transaction as shutdown
from backend.engine import application_launch_quiescence as epoch
from backend.engine.global_store_paths import active_generation, store_admission

ARCHIVES = '.application-writer-archives'
CLOSURES = '.application-writer-closures'
SCOPE = 'original_managed_store_writers'
MUTATION_SCOPE = 'original_managed_store_mutation_only'
DOCUMENT_LIMIT = 128 * 1024
CONTROLLER_FINISH_SECONDS = 10
FALSE_FLAGS = ('process_tree_exit_verified', 'whole_writer_coverage', 'release_ready',
    'model_quality_approved', 'publisher_approved', 'parent_acceptance')
PUBLIC_FALSE_FIELDS = frozenset({'native_app_handshake_verified', 'actual_application_inference_verified',
    'known_image_execution_qualified', 'model_quality_approved', 'release_ready'})
_CANDIDATES = weakref.WeakKeyDictionary()
_FINALIZER = shutdown.finalize_controller
_ORIGINAL_METHODS = {name: getattr(lease.LaunchSupervisor, name)
    for name in ('_owned', '_persist', 'observe_exit')}
_VALIDATION_DEADLINE = ContextVar('original_store_closure_validation_deadline', default=None)
_CODES = {function: function.__code__ for function in (shutdown.finalize_controller,
    shutdown._state, shutdown.asset_custody, lease.LaunchSupervisor._owned,
    lease.LaunchSupervisor.observe_exit, lease.LaunchSupervisor._persist)}


class StoreWriterClosureError(ValueError):
    pass


class _OriginalClosure:
    __slots__ = ('__weakref__',)

    def __copy__(self):
        raise StoreWriterClosureError('Original store closure cannot be copied')

    def __deepcopy__(self, memo):
        raise StoreWriterClosureError('Original store closure cannot be copied')

    def __reduce_ex__(self, protocol):
        raise StoreWriterClosureError('Original store closure cannot be serialized')


def _u():
    return lease._update()


def _refuse(message):
    raise StoreWriterClosureError('Original store writer closure refuses: ' + message)


def _checkpoint(point):
    """Durable fault boundary, without repair, process operations or authority."""


def _deadline(deadline):
    if type(deadline) not in (int, float) or time.monotonic() >= deadline:
        _refuse('original controller finish deadline expired')


def _codes():
    if (shutdown.finalize_controller is not _FINALIZER
            or any(getattr(lease.LaunchSupervisor, name) is not function
                for name, function in _ORIGINAL_METHODS.items())
            or any(function.__code__ is not code for function, code in _CODES.items())):
        _refuse('original shutdown producer code changed')


@contextmanager
def _validation_budget(deadline):
    token = _VALIDATION_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _VALIDATION_DEADLINE.reset(token)


def _read_deadline():
    deadline = _VALIDATION_DEADLINE.get()
    return time.monotonic()+CONTROLLER_FINISH_SECONDS if deadline is None else deadline


def _epoch_binding(row):
    binding = row['binding']
    return {'installation_id': binding['installation_id'], 'nonce': row['nonce'],
        'update_id': binding['update_id'], 'application_generation': binding['application_generation'],
        'database_generation_id': binding['database_pointer']['generation_id'],
        'database_fence': binding['database_pointer']['fence'],
        'launch_binding_sha256': _u()._sha(_u()._canonical(binding)), 'controller': row['supervisor']}


def _managed(row):
    drain = row.get('writer_drain')
    if (row.get('protocol_version') != 4 or not isinstance(drain, dict)
            or drain['phase'] != 'backend_exited' or drain['backend_exit'] is None
            or drain['receipt'] is None or drain['receipt']['status'] != 'managed_scopes_drained'
            or drain['receipt']['unsupported']):
        _refuse('exact original managed drain and backend exit are required')
    # The original lease decoder performs the complete typed request, counts,
    # backend identity and receipt validation. No HTTP/status inference is used.
    return drain


def _private_directory(path, *, create=False):
    path = _u()._unlinked(Path(path))
    if create:
        path.mkdir(mode=0o700, exist_ok=False)
        _u().migration._sync_directories(path.parent, recursive=False)
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid() or stat.S_IMODE(info.st_mode) != 0o700):
        _refuse('closure namespace must be an original private directory')
    from backend.engine import application_store_writer_archive as archive
    archive._entry(path, directory=True, deadline=_read_deadline())
    return path


def _parent(root, name):
    path = _u()._unlinked(root/name)
    if not path.exists():
        _private_directory(path, create=True)
    return _private_directory(path)


def _exclusive_json(path, value, deadline):
    _deadline(deadline)
    raw = _u()._canonical(value)
    if not 0 < len(raw) <= DOCUMENT_LIMIT:
        _refuse('closure document exceeds its bound')
    fd = os.open(_u()._unlinked(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as writer:
            writer.write(raw); writer.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    _u().migration._sync_directories(path.parent, recursive=False)
    _deadline(deadline)
    if _read_document(path) != (value, raw):
        _refuse('closure publication readback changed')
    return _u()._sha(raw)


def _read_document(path):
    u = _u(); path = u._unlinked(path)
    from backend.engine import application_store_writer_archive as archive
    deadline = _read_deadline()
    original = archive._entry(path, directory=False, deadline=deadline)
    with u._file(path, DOCUMENT_LIMIT) as (reader, before):
        if (before.st_uid != os.geteuid() or before.st_gid != os.getegid()
                or stat.S_IMODE(before.st_mode) != 0o600 or _raw9(before) != original):
            _refuse('closure document security changed')
        raw = reader.read(DOCUMENT_LIMIT+1)
        if len(raw) != before.st_size:
            _refuse('closure document changed while reading')
    if archive._entry(path, directory=False, deadline=deadline) != original:
        _refuse('closure document metadata changed while reading')
    value = u._json(raw)
    if u._canonical(value) != raw:
        _refuse('closure document is not exact canonical JSON')
    return value, raw


def _raw9(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
        info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def prepare_original_controller(owner, shutdown_cap):
    """Provisional mint only in the original verified final-send live frame."""
    caller = inspect.currentframe().f_back
    _codes(); s = shutdown._state(shutdown_cap); u = _u()
    if (caller.f_code is not _FINALIZER.__code__ or caller.f_globals is not _FINALIZER.__globals__
            or caller.f_locals.get('cap') is not shutdown_cap or caller.f_locals.get('s') is not s
            or caller.f_locals.get('owner') is not owner or s['owner'] is not owner
            or s['phase'] != 'verified' or s['failed'] or not s['post_verified']
            or owner in _CANDIDATES or getattr(owner, '_store_writer_closure', None) is not None):
        _refuse('only the original verified final-send caller may prepare')
    row = owner._owned(); drain = _managed(row)
    final = caller.f_locals.get('final')
    expected = {'schema_version': 1, 'kind': 'shutdown_validation_final', 'nonce': s['nonce'],
        'request_id': s['request_id'], 'receipt_sha256': u._sha(u._canonical(s['receipt'])),
        'exit_sha256': u._sha(u._canonical(s['exit_frame'])), 'status': 'managed_scopes_drained',
        'whole_writer_coverage': False, 'process_tree_exit_verified': False,
        'can_release_launch_lease': False}
    if (row['state'] != 'ready' or row['reason'] is not None or row['exit_observation'] is not None
            or u._canonical(row) != s['row'] or final != expected
            or drain['receipt'] != s['receipt'] or drain['backend_exit'] != s['exit_frame']
            or s['main'] is not owner._process or s['main'].poll() is not None
            or owner._writer_epoch is not s['epoch'] or owner._database_fd != s['database_fd']
            or shutdown._stat(os.fstat(s['database_fd'])) != s['database_fd_stat']):
        _refuse('original final receipt, handles or provisional custody differs')
    snapshot = s['epoch'].snapshot()
    registry = snapshot['registry']
    if (snapshot != s['registry_snapshot'] or u._canonical(snapshot) != s['registry']
            or registry['state'] != 'closed' or not registry['writers']
            or any(r['status'] != 'direct_exited' or r['exit_code'] != 0 for r in registry['writers'])):
        _refuse('original closed registry has unresolved writers')
    candidate = _OriginalClosure()
    from backend.engine import application_launch_controller as controller
    _CANDIDATES[owner] = {'candidate': candidate, 'used': False, 'pid': os.getpid(),
        'thread': threading.current_thread(), 'cap': shutdown_cap, 's': s,
        'main': owner._process, 'epoch': owner._writer_epoch, 'root': owner.root,
        'row': copy.deepcopy(row), 'snapshot': copy.deepcopy(snapshot),
        'final': final, 'final_bytes': u._canonical(final), 'run': controller.run,
        'run_code': controller.run.__code__, 'assets': s['assets'],
        'database_fd': owner._database_fd, 'database_lock': owner._lock,
        'database_fd_stat': shutdown._stat(os.fstat(owner._database_fd)),
        'database_lock_raw9': _raw9(os.fstat(owner._database_fd))}
    owner._store_writer_closure = candidate
    return candidate


def _post_main_controls(owner, c, row):
    """Exact custody/CAS after exit, without the live-main or full hash gate."""
    u = _u(); s = shutdown._state(c['cap'])
    if (s is not c['s'] or s['phase'] != 'final' or s['failed'] or not s['post_verified']
            or c['final'] != u._json(c['final_bytes']) or c['row']['binding'] != row['binding']
            or s['row'] != u._canonical(c['row']) or s['assets'] != c['assets']
            or owner.root is not c['root'] or owner._writer_epoch is not c['epoch']
            or owner._bootstrap_channel is not s['channel'] or owner._process is not c['main']
            or owner._database_fd != c['database_fd'] or owner._lock is not c['database_lock']
            or shutdown._stat(os.fstat(c['database_fd'])) != c['database_fd_stat']
            or _raw9(os.fstat(c['database_fd'])) != c['database_lock_raw9']
            or lease._identity(os.getpid()) != row['supervisor']):
        _refuse('original post-main typed handles or final activation differs')
    from backend.engine import application_store_writer_archive as archive
    if archive._entry(owner.root/lease.DATABASE_LOCK, directory=False,
            deadline=_read_deadline()) != c['database_lock_raw9']:
        _refuse('named original database ownership lock differs from retained OFD')
    root, identity = u._root(owner.root); u.validate_attachment(root)
    pointer = u._pointer(root); current = active_generation(root); binding = row['binding']
    if (identity['installation_id'] != binding['installation_id'] or pointer is None
            or pointer['update_id'] != binding['update_id']
            or pointer['application_generation'] != binding['application_generation']
            or pointer['database_pointer'] != binding['database_pointer']
            or current is None or current[1] != binding['database_pointer']
            or str(current[0]) != binding['database_generation_path']
            or shutdown.asset_custody(root, binding) != c['assets']):
        _refuse('original signed asset custody or committed pair fence changed')


def finish_original_controller(owner):
    """Consume after the original run's retained Popen0 and observe_exit CAS."""
    caller = inspect.currentframe().f_back
    c = _CANDIDATES.get(owner)
    if (c is None or c['used'] or getattr(owner, '_store_writer_closure', None) is not c['candidate']
            or os.getpid() != c['pid'] or threading.current_thread() is not c['thread']
            or caller.f_code is not c['run_code'] or c['run'].__code__ is not c['run_code']
            or caller.f_globals is not c['run'].__globals__ or caller.f_locals.get('owner') is not owner
            or caller.f_locals.get('direct_exit_recorded') is not True
            or caller.f_locals.get('failed') is not True
            or owner._process is not c['main'] or c['main'].poll() != 0):
        _refuse('only the original observed-zero controller can finish once')
    c['used'] = True  # Any late, interrupted or partial attempt remains blocked.
    _codes(); u = _u()
    started = caller.f_locals.get('direct_exit_started')
    if type(started) not in (int, float):
        _refuse('original direct-exit clock is absent')
    deadline = started + CONTROLLER_FINISH_SECONDS
    _deadline(deadline)
    with _validation_budget(deadline), store_admission(owner.root, exclusive=True), lease._transition_admission(owner.root, owner.nonce):
        row = owner._owned()
        expected = {**c['row'], 'revision': c['row']['revision']+1, 'state': 'recovery_required',
            'reason': 'Process-tree exit is unverified', 'exit_observation': {
                'direct_child_pid': c['main'].pid, 'direct_child_returncode': 0,
                'process_tree_exit_verified': False}}
        if row != expected:
            _refuse('original observe_exit was not the sole lease transition')
        _post_main_controls(owner, c, row); _deadline(deadline)
        snapshot = c['epoch'].snapshot()
        if snapshot != c['snapshot']:
            _refuse('original closed epoch changed after finalization')
        from backend.engine import application_store_writer_archive as archive
        active = u._unlinked(owner.root/epoch.EPOCHS/owner.nonce)
        # The original fence's own live-path postcheck must finish BEFORE the
        # rename. The outer original store EX admission remains held throughout.
        with c['epoch'].enrolled_writer_fence(expected_registry_sha256=snapshot['registry_sha256']):
            manifest = archive.capture_epoch(active, _epoch_binding(row), deadline=deadline)
            if manifest['registry_sha256'] != snapshot['registry_sha256']:
                _refuse('archive captured another closed registry')
        _deadline(deadline)
        closures = _parent(owner.root, CLOSURES); archives = _parent(owner.root, ARCHIVES)
        directory = _private_directory(closures/owner.nonce, create=True)
        target = u._unlinked(archives/owner.nonce)
        if target.exists():
            _refuse('epoch archive already exists; no retry or replacement')
        drain = _managed(row)
        intent = {'schema': 'modu-vision.original-store-writer-closure-intent/v1',
            'installation_id': row['binding']['installation_id'], 'nonce': owner.nonce,
            'binding_sha256': u._sha(u._canonical(row['binding'])),
            'controller': row['supervisor'], 'main': row['process'], 'main_returncode': 0,
            'database_lock_raw9': c['database_lock_raw9'],
            'lease_journal_sha256': u._sha(owner._snapshot['journal']),
            'lease_pointer_sha256': u._sha(owner._snapshot['pointer']),
            'drain_sha256': u._sha(u._canonical(drain['receipt'])),
            'backend_exit_sha256': u._sha(u._canonical(drain['backend_exit'])),
            'registry_sha256': snapshot['registry_sha256'],
            'archive_path': ARCHIVES+'/'+owner.nonce,
            'archive_manifest_sha256': u._sha(u._canonical(manifest))}
        intent_sha = _exclusive_json(directory/'intent.json', intent, deadline)
        _checkpoint('after_store_closure_intent')
        manifest_sha = _exclusive_json(directory/'archive-manifest.json', manifest, deadline)
        _checkpoint('after_store_closure_manifest'); _deadline(deadline)
        # Original atomic no-replace primitive; no delete/replace fallback.
        from backend.engine.runtime_pack import _publish_directory
        _publish_directory(active, target)
        u.migration._sync_directories(active.parent, recursive=False)
        u.migration._sync_directories(target.parent, recursive=False)
        _checkpoint('after_store_epoch_archive'); _deadline(deadline)
        after = _raw9(target.stat())
        archive.validate_epoch_archive(target, manifest, archive_root_after_raw9=after, deadline=deadline)
        receipt = {'schema': 'modu-vision.original-store-writer-closure/v1', 'scope': SCOPE,
            'installation_id': row['binding']['installation_id'], 'nonce': owner.nonce,
            'binding_sha256': intent['binding_sha256'], 'controller': row['supervisor'],
            'main': row['process'], 'main_returncode': 0, 'intent_sha256': intent_sha,
            'database_lock_raw9': c['database_lock_raw9'],
            'archive_manifest_sha256': manifest_sha, 'drain_sha256': intent['drain_sha256'],
            'backend_exit_sha256': intent['backend_exit_sha256'],
            'registry_sha256': snapshot['registry_sha256'], 'archive_path': intent['archive_path'],
            'archive_root_after_raw9': after, 'managed_scopes_drained': True, 'unsupported': [],
            'all_enrolled_writers_direct_exited': True, 'all_writer_lock_references_closed': True,
            **{name: False for name in FALSE_FLAGS}}
        digest = _exclusive_json(directory/'receipt.json', receipt, deadline)
        _checkpoint('before_store_closure_lease_publication'); _deadline(deadline)
        # _persist's original journal/pointer CAS also validates the new complete
        # protocol5 record through the unchanged protocol4 shadow validator.
        result = owner._persist(row, protocol_version=5, state='writer_store_closed',
            writer_store_closure_sha256=digest)
        # The final CAS was admitted inside this single original deadline.
        # Never invent a rollback if a completed fsync returns late. The real
        # controller join still has its original10s cap; later store admission
        # independently requires this controller gone and the original DB EX.
        return result


def _raw_projection(row):
    if isinstance(row, dict) and PUBLIC_FALSE_FIELDS.intersection(row):
        if not PUBLIC_FALSE_FIELDS.issubset(row) or any(row[name] is not False for name in PUBLIC_FALSE_FIELDS):
            _refuse('original public lease false projection differs')
        return {name: value for name, value in row.items() if name not in PUBLIC_FALSE_FIELDS}
    return row


def _prior_row(row):
    row = _raw_projection(row)
    if (not isinstance(row, dict) or type(row.get('protocol_version')) is not int
            or row['protocol_version'] != 5
            or row.get('state') != 'writer_store_closed'
            or not _u()._hex(row.get('writer_store_closure_sha256'))):
        _refuse('exact terminal protocol5 record is required')
    prior = {k: copy.deepcopy(v) for k, v in row.items() if k != 'writer_store_closure_sha256'}
    prior.update(protocol_version=4, state='recovery_required', revision=row['revision']-1)
    if (prior['revision'] < 1 or prior['reason'] != 'Process-tree exit is unverified'
            or prior['process'] is None or prior['exit_observation'] != {
                'direct_child_pid': prior['process']['pid'], 'direct_child_returncode': 0,
                'process_tree_exit_verified': False}
            or type(prior['exit_observation']['direct_child_returncode']) is not int
            or type(prior['exit_observation']['process_tree_exit_verified']) is not bool):
        _refuse('original retained zero exit publication differs')
    _managed(prior)
    return prior


def inspect_store_closure(root, row):
    """Read only archived evidence, bound to historical data, not live pointers."""
    try:
        with _validation_budget(_read_deadline()):
            return _inspect_store_closure(root, row)
    except OSError as exc:
        raise StoreWriterClosureError('Original store writer closure evidence is missing or unreadable') from exc


def _inspect_store_closure(root, row):
    row = _raw_projection(row)
    u = _u(); root, owner = u._root(root); prior = _prior_row(row)
    if row['binding']['installation_id'] != owner['installation_id']:
        _refuse('closure belongs to another installation')
    nonce = row['nonce']; directory = _private_directory(_private_directory(root/CLOSURES)/nonce)
    epoch._members(directory, {'intent.json', 'archive-manifest.json', 'receipt.json'})
    intent, intent_raw = _read_document(directory/'intent.json')
    manifest, manifest_raw = _read_document(directory/'archive-manifest.json')
    receipt, receipt_raw = _read_document(directory/'receipt.json')
    if u._sha(receipt_raw) != row['writer_store_closure_sha256']:
        _refuse('terminal receipt hash differs')
    drain = prior['writer_drain']; registry_sha = manifest.get('registry_sha256') if isinstance(manifest, dict) else None
    from backend.engine.runtime_process_control import private_json_bytes
    expected_intent = {'schema': 'modu-vision.original-store-writer-closure-intent/v1',
        'installation_id': row['binding']['installation_id'], 'nonce': nonce,
        'binding_sha256': u._sha(u._canonical(row['binding'])), 'controller': row['supervisor'],
        'main': row['process'], 'main_returncode': 0,
        'database_lock_raw9': receipt.get('database_lock_raw9') if isinstance(receipt, dict) else None,
        'lease_journal_sha256': u._sha(private_json_bytes(prior)),
        'lease_pointer_sha256': u._sha(private_json_bytes(lease._publication(prior))),
        'drain_sha256': u._sha(u._canonical(drain['receipt'])),
        'backend_exit_sha256': u._sha(u._canonical(drain['backend_exit'])),
        'registry_sha256': registry_sha, 'archive_path': ARCHIVES+'/'+nonce,
        'archive_manifest_sha256': u._sha(manifest_raw)}
    expected_receipt = {'schema': 'modu-vision.original-store-writer-closure/v1', 'scope': SCOPE,
        'installation_id': row['binding']['installation_id'], 'nonce': nonce,
        'binding_sha256': expected_intent['binding_sha256'], 'controller': row['supervisor'],
        'main': row['process'], 'main_returncode': 0, 'intent_sha256': u._sha(intent_raw),
        'database_lock_raw9': expected_intent['database_lock_raw9'],
        'archive_manifest_sha256': u._sha(manifest_raw), 'drain_sha256': expected_intent['drain_sha256'],
        'backend_exit_sha256': expected_intent['backend_exit_sha256'], 'registry_sha256': registry_sha,
        'archive_path': ARCHIVES+'/'+nonce,
        'archive_root_after_raw9': receipt.get('archive_root_after_raw9') if isinstance(receipt, dict) else None,
        'managed_scopes_drained': True, 'unsupported': [], 'all_enrolled_writers_direct_exited': True,
        'all_writer_lock_references_closed': True, **{name: False for name in FALSE_FLAGS}}
    if (intent != expected_intent or receipt != expected_receipt or not u._hex(registry_sha)
            or type(receipt.get('main_returncode')) is not int
            or type(intent.get('main_returncode')) is not int
            or any(type(receipt.get(name)) is not bool for name in (*FALSE_FLAGS,
                'managed_scopes_drained', 'all_enrolled_writers_direct_exited',
                'all_writer_lock_references_closed'))
            or manifest.get('epoch_binding') != _epoch_binding(row) or manifest.get('nonce') != nonce):
        _refuse('closure receipt, intent or historical binding differs')
    archive_path = _private_directory(_private_directory(root/ARCHIVES)/nonce)
    if u._unlinked(root/epoch.EPOCHS/nonce).exists():
        _refuse('terminal epoch still has a live path')
    from backend.engine import application_store_writer_archive as archive
    deadline = _VALIDATION_DEADLINE.get()
    if deadline is None:
        deadline = time.monotonic()+CONTROLLER_FINISH_SECONDS
    _deadline(deadline)
    lock_raw9 = receipt['database_lock_raw9']
    if (not archive._raw_shape(lock_raw9)
            or archive._entry(root/lease.DATABASE_LOCK, directory=False, deadline=deadline) != lock_raw9):
        _refuse('original retained database ownership lock identity or metadata changed')
    archive.validate_epoch_archive(archive_path, manifest,
        archive_root_after_raw9=receipt['archive_root_after_raw9'], deadline=deadline)
    # A second canonical read rejects a changed publication during inspection.
    for name, raw in (('intent.json', intent_raw), ('archive-manifest.json', manifest_raw), ('receipt.json', receipt_raw)):
        if _read_document(directory/name)[1] != raw:
            _refuse('closure documents changed during inspection')
    return receipt


def _controller_gone(identity):
    try:
        process = psutil.Process(identity['pid'])
        birth = process.create_time()
        if birth == identity['created_at']:
            _refuse('original controller is still present or unresolved')
        if process.create_time() != birth:
            _refuse('process identity changed during exit observation')
        # A separately observed different birth does not authorize that process;
        # it establishes only that the recorded original controller is gone.
    except psutil.NoSuchProcess:
        return
    except psutil.Error as exc:
        raise StoreWriterClosureError('Original controller exit is unresolved') from exc


def _closed_history(root):
    rows = []
    with os.scandir(_u()._unlinked(root/lease.LEASES)) as entries:
        for entry in entries:
            row = lease._record(root, entry.name)
            if row.get('protocol_version') == 5:
                rows.append(row)
    expected = {row['nonce'] for row in rows}
    for name in (ARCHIVES, CLOSURES):
        parent = _u()._unlinked(root/name)
        if not parent.exists():
            if expected:
                _refuse('terminal archive history is missing')
        else:
            epoch._members(_private_directory(parent), expected)
    return rows


def assert_store_quiescent(root, row):
    """Original managed store admission only; no whole-process assertion."""
    try:
        return _assert_store_quiescent(root, row)
    except OSError as exc:
        raise StoreWriterClosureError('Original store writer closure admission evidence is unavailable') from exc


def _assert_store_quiescent(root, row):
    root, _ = _u()._root(root)
    with store_admission(root, exclusive=True):
        rows = _closed_history(root)
        if (not rows or row is None or row.get('state') not in {'writer_store_closed', 'exited'}
                or row.get('state') == 'writer_store_closed' and row not in rows):
            _refuse('current launch and complete closed history differ')
        epochs = _u()._unlinked(root/epoch.EPOCHS)
        if epochs.exists():
            epoch._members(_private_directory(epochs), set())
        with ExitStack() as locks:
            for old in rows:
                inspect_store_closure(root, old); _controller_gone(old['supervisor'])
                directory = root/ARCHIVES/old['nonce']
                registry = _u()._json(_u()._read(directory/'registry.json', epoch.DOCUMENT_LIMIT))
                locks.enter_context(epoch._locked(directory/'authority.lock', registry['authority_lock_identity'], exclusive=True))
                for writer in registry['writers']:
                    locks.enter_context(epoch._locked(directory/'writers'/writer['writer_id']/'ownership.lock', writer['lock_identity'], exclusive=True))
            with lease._database_lock(root, create=False) as fd:
                if fd is None:
                    _refuse('original database ownership lock is missing')
                for old in rows:
                    receipt = inspect_store_closure(root, old); _controller_gone(old['supervisor'])
                    if _raw9(os.fstat(fd)) != receipt['database_lock_raw9']:
                        _refuse('reacquired database OFD is not the original bound lock')
                return {'status': 'quiescent', 'nonce': row['nonce'], 'scope': MUTATION_SCOPE,
                    **{name: False for name in FALSE_FLAGS}}
