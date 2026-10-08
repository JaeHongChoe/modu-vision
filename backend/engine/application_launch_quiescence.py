"""Enrollment authority for an owned launch's managed writer lifetime locks.

This opt-in core does not clear a launch lease, attest application shutdown or
prove an entire process tree exited. Only the original in-memory authority can
mutate its registry or retain/reap its original Python child handles. Native
Node handles require a separate authenticated adapter, never caller exit JSON.
An adapter must enroll before spawning and keep writer_guard around every write
and explicitly propagate its descriptor to descendants. Uncovered writers must
be recorded as blocking, including deliberately detached jobs and services.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess
import threading
import uuid
import weakref

from backend.engine.global_store_paths import store_admission

EPOCHS = '.application-writer-epochs'
MAX_WRITERS = 128
DOCUMENT_LIMIT = 128 * 1024
ROLES = frozenset({'backend', 'owned_cpu_worker', 'preflight', 'local_training',
    'inspection_service', 'operations_worker', 'fleet_agent', 'distributed_training', 'sdk_parity'})
STATUSES = frozenset({'reserved', 'active', 'direct_exited', 'unsupported', 'uncertain'})
REASONS = frozenset({'handoff_interrupted', 'ownership_unknown', 'child_failed', 'uncovered_protocol'})
_MINTED = set()
_AUTHORITIES = weakref.WeakValueDictionary()


class QuiescenceError(ValueError):
    pass


def _update():
    from backend.engine import runtime_update
    return runtime_update


def _lease():
    from backend.engine import application_launch_lease
    return application_launch_lease


def _identity(pid):
    return _lease()._identity(pid)


def _checkpoint(point):
    """Durable test fault boundary; no process operation or recovery."""


def _refuse(reason):
    raise QuiescenceError('Owned writer epoch refuses: ' + reason)


def _launch(root, nonce):
    u = _update()
    if not u._hex(nonce, 32): _refuse('invalid launch nonce')
    try:
        root, owner = u._root(root)
        row = _lease()._load(root)
        if row is None or row['nonce'] != nonce: _refuse('launch nonce binding differs')
        binding = row['binding']; pointer = u._pointer(root)
        if (pointer is None or pointer['installation_id'] != owner['installation_id']
                or pointer['update_id'] != binding['update_id']
                or pointer['application_generation'] != binding['application_generation']
                or u._canonical(pointer['database_pointer']) != u._canonical(binding['database_pointer'])):
            _refuse('committed application/database binding differs')
        value = {'installation_id': owner['installation_id'], 'nonce': nonce,
            'update_id': binding['update_id'], 'application_generation': binding['application_generation'],
            'database_generation_id': binding['database_pointer']['generation_id'],
            'database_fence': binding['database_pointer']['fence'],
            'launch_binding_sha256': u._sha(u._canonical(binding)), 'controller': row['supervisor']}
        return root, value, u._sha(u._canonical(row))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, QuiescenceError): raise
        raise QuiescenceError('Owned writer epoch refuses foreign installation/database binding') from exc


def _path(root, nonce):
    u = _update(); directory = u._unlinked(Path(root) / EPOCHS / nonce)
    if not directory.is_dir(): _refuse('partial or missing writer epoch')
    for p in (directory.parent, directory):
        info = p.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            _refuse('writer epoch directory is not private')
    return directory


def _lock_stat(info, expected=None):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
        _refuse('invalid original writer lock')
    identity = {'device': info.st_dev, 'inode': info.st_ino}
    if expected is not None and identity != expected: _refuse('original writer lock identity changed')
    return identity


def _create_lock(path):
    fd = os.open(_update()._unlinked(path), os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        identity = _lock_stat(os.fstat(fd)); os.fsync(fd)
        return identity
    finally: os.close(fd)


def _registration(binding, row):
    return {'binding_sha256': _update()._sha(_update()._canonical(binding)),
        'writer_id': row['writer_id'], 'role': row['role'], 'lock_identity': row['lock_identity']}


def _members(directory, expected):
    # Path.iterdir may eagerly collect every entry. Delegate the real directory
    # stream directly, rejecting the first unknown entry within the closed set.
    seen = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in expected or entry.name in seen or len(seen) >= len(expected):
                _refuse('unknown writer registry member or reviewed bound exceeded')
            seen.add(entry.name)
    if seen != expected: _refuse('partial writer registry member')


def _shape(value, binding, directory):
    u = _update()
    fields = {'schema_version', 'binding', 'revision', 'state', 'authority_lock_identity', 'writers'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['schema_version']) is not int
            or value['schema_version'] != 1 or u._canonical(value['binding']) != u._canonical(binding)
            or type(value['revision']) is not int or value['revision'] < 1
            or value['state'] not in {'open', 'closed'} or not isinstance(value['writers'], list)
            or len(value['writers']) > MAX_WRITERS):
        _refuse('invalid or foreign writer registry binding')
    names = {'registry.json', 'publication.json', 'authority.lock', 'writers'}
    _members(directory, names)
    lock = u._unlinked(directory/'authority.lock')
    _lock_stat(lock.stat(), value['authority_lock_identity'])
    writers = u._unlinked(directory/'writers')
    if not writers.is_dir(): _refuse('partial writer member directory')
    seen = set()
    for row in value['writers']:
        row_fields = {'writer_id', 'role', 'status', 'lock_identity', 'registration_sha256',
            'process', 'exit_code', 'reason_code'}
        if (not isinstance(row, dict) or set(row) != row_fields or not u._hex(row['writer_id'], 32)
                or row['writer_id'] in seen or row['role'] not in ROLES or row['status'] not in STATUSES
                or not u._hex(row['registration_sha256'])
                or row['process'] is not None and not _lease()._identity_shape(row['process'])
                or row['exit_code'] is not None and type(row['exit_code']) is not int
                or row['reason_code'] is not None and row['reason_code'] not in REASONS):
            _refuse('invalid writer registry row')
        if (row['status'] == 'reserved' and any(row[x] is not None for x in ('process', 'exit_code', 'reason_code'))
                or row['status'] == 'active' and (row['process'] is None or row['exit_code'] is not None or row['reason_code'] is not None)
                or row['status'] == 'direct_exited' and (row['process'] is None or row['exit_code'] != 0 or row['reason_code'] is not None)
                or row['status'] in {'unsupported', 'uncertain'} and row['reason_code'] is None):
            _refuse('contradictory writer ownership state')
        child_dir = u._unlinked(writers/row['writer_id'])
        if not child_dir.is_dir(): _refuse('partial writer member')
        _members(child_dir, {'ownership.lock'})
        _lock_stat(u._unlinked(child_dir/'ownership.lock').stat(), row['lock_identity'])
        if u._sha(u._canonical(_registration(binding, row))) != row['registration_sha256']:
            _refuse('writer registration pin differs')
        seen.add(row['writer_id'])
    _members(writers, seen)


def _load(root, nonce):
    u = _update(); root, binding, _ = _launch(root, nonce); directory = _path(root, nonce)
    try:
        raw = u._read(directory/'registry.json', DOCUMENT_LIMIT)
        publication = u._read(directory/'publication.json')
        value = u._json(raw); pointer = u._json(publication)
        _shape(value, binding, directory)
        expected = {'schema_version': 1, 'nonce': nonce, 'revision': value['revision'], 'registry_sha256': u._sha(raw)}
        if u._canonical(pointer) != u._canonical(expected): _refuse('partial registry publication or CAS changed')
        if (u._read(directory/'registry.json', DOCUMENT_LIMIT) != raw
                or u._read(directory/'publication.json') != publication):
            _refuse('registry publication changed during inspection')
        return value, raw, publication
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, QuiescenceError): raise
        raise QuiescenceError('Owned writer epoch refuses partial or changed registry publication') from exc


@contextmanager
def _locked(path, expected, *, exclusive):
    if os.name != 'posix': _refuse('writer lifetime lock protocol requires POSIX')
    import fcntl
    u = _update(); path = u._unlinked(path)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd); _lock_stat(before, expected)
        try: fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except OSError as exc: raise QuiescenceError('Original writer lock reference remains held') from exc
        _lock_stat(path.stat(), expected)
        yield fd
        u._unlinked(path); after = os.fstat(fd); named = path.stat()
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if any(getattr(s, k) != getattr(before, k) for s in (after, named) for k in fields):
            _refuse('writer lock changed during admission')
    finally:
        # No explicit LOCK_UN: forked or explicitly passed descendant references
        # retain this same open-file-description lock until their last close.
        os.close(fd)


def inspect_epoch(root, nonce):
    """Read only; concurrent publication refuses instead of repairing or locking."""
    with store_admission(root):
        value, raw, publication = _load(root, nonce)
        fresh, raw_after, pointer_after = _load(root, nonce)
        if raw != raw_after or publication != pointer_after: _refuse('registry inspection changed')
        return {'registry': value, 'registry_sha256': _update()._sha(raw)}


@dataclass(frozen=True)
class Registration:
    writer_id: str
    registration_sha256: str


@contextmanager
def writer_guard(root, nonce, writer_id, *, expected_registration_sha256):
    """Admit before any writer import/side effect; pass_fds is explicit, never env."""
    u = _update()
    if not u._hex(writer_id, 32) or not u._hex(expected_registration_sha256): _refuse('invalid writer registration pin')
    with store_admission(root):
        value, _, _ = _load(root, nonce); directory = _path(root, nonce)
        with _locked(directory/'authority.lock', value['authority_lock_identity'], exclusive=True):
            value, _, _ = _load(root, nonce)
            if value['state'] != 'open': _refuse('writer epoch is closed')
            row = next((r for r in value['writers'] if r['writer_id'] == writer_id), None)
            if row is None or row['registration_sha256'] != expected_registration_sha256: _refuse('writer registration pin differs')
            if row['status'] not in {'reserved', 'active'}: _refuse('writer registration is unresolved or already exited')
            lifetime = _locked(directory/'writers'/writer_id/'ownership.lock', row['lock_identity'], exclusive=False)
            fd = lifetime.__enter__()
    # The registry mutex and installation admission are released; this original
    # lock remains held through the writer's entire mutable lifetime.
    class Guard:
        pass_fds = (fd,)
    try: yield Guard()
    finally: lifetime.__exit__(None, None, None)


class WriterEpoch:
    """Original authority only. No reopen, implicit repair or expired reservation."""
    def __init__(self, root, nonce, binding, value, raw, pointer, *, _creation_capability=None):
        # Registry bytes and a fresh caller PID are not a capability. Only the
        # create operation can mint an instance, while still checking the exact
        # original controller birth/command in the committed launch binding.
        if (_creation_capability not in _MINTED
                or binding['controller'] != _identity(os.getpid())):
            _refuse('original create-only authority capability is unavailable')
        self.root = root; self.nonce = nonce; self.snapshot_binding = binding
        self._pid = os.getpid(); self._thread = threading.current_thread()
        self._raw = raw; self._pointer = pointer; self._handles = {}; self._node_handles = {}

    @classmethod
    def create(cls, root, nonce, *, expected_launch_sha256):
        if os.name != 'posix': _refuse('writer epoch protocol requires POSIX')
        u = _update()
        if not u._hex(expected_launch_sha256): _refuse('invalid launch CAS snapshot')
        with store_admission(root, exclusive=True):
            root, binding, fresh_sha = _launch(root, nonce)
            if fresh_sha != expected_launch_sha256: _refuse('launch CAS snapshot changed')
            if binding['controller'] != _identity(os.getpid()): _refuse('only original controller may create a writer epoch')
            parent = u._unlinked(root/EPOCHS)
            if parent.exists():
                if not parent.is_dir() or any(parent.iterdir()): _refuse('existing/partial writer authority cannot reopen')
            else: parent.mkdir(mode=0o700)
            directory = parent/nonce; directory.mkdir(mode=0o700)
            (directory/'writers').mkdir(mode=0o700)
            identity = _create_lock(directory/'authority.lock')
            value = {'schema_version': 1, 'binding': binding, 'revision': 1, 'state': 'open',
                'authority_lock_identity': identity, 'writers': []}
            u._write(directory/'registry.json', value)
            raw = u._read(directory/'registry.json', DOCUMENT_LIMIT)
            u._write(directory/'publication.json', {'schema_version': 1, 'nonce': nonce,
                'revision': 1, 'registry_sha256': u._sha(raw)})
            u.migration._sync_directories(parent, recursive=False)
            value, raw, pointer = _load(root, nonce)
            capability = object(); _MINTED.add(capability)
            try:
                authority = cls(root, nonce, binding, value, raw, pointer, _creation_capability=capability)
                _AUTHORITIES[(str(root), nonce)] = authority
                return authority
            finally: _MINTED.discard(capability)

    def _original(self):
        if (os.getpid() != self._pid or threading.current_thread() is not self._thread
                or _AUTHORITIES.get((str(self.root), self.nonce)) is not self):
            _refuse('original authority process/thread capability is unavailable')

    def snapshot(self):
        self._original()
        return inspect_epoch(self.root, self.nonce)

    @contextmanager
    def _admit(self, expected, *, exclusive=False):
        self._original(); u = _update()
        if not u._hex(expected): _refuse('invalid registry CAS snapshot')
        with store_admission(self.root, exclusive=exclusive):
            value, raw, pointer = _load(self.root, self.nonce)
            directory = _path(self.root, self.nonce)
            with _locked(directory/'authority.lock', value['authority_lock_identity'], exclusive=True):
                value, raw, pointer = _load(self.root, self.nonce)
                if raw != self._raw or pointer != self._pointer or u._sha(raw) != expected:
                    _refuse('registry CAS snapshot changed')
                if u._canonical(value['binding']) != u._canonical(self.snapshot_binding): _refuse('authority binding changed')
                yield value, directory

    def _publish(self, value, directory):
        u = _update()
        if (u._read(directory/'registry.json', DOCUMENT_LIMIT) != self._raw
                or u._read(directory/'publication.json') != self._pointer): _refuse('registry CAS changed before publication')
        value = {**value, 'revision': value['revision'] + 1}
        if len(u._canonical(value)) > DOCUMENT_LIMIT: _refuse('writer registry exceeds reviewed bound')
        # Existing pointer still references old bytes until the separate publish
        # operation. Interruption is intentionally unrepairable by this core.
        u._write(directory/'registry.json', value); _checkpoint('after_registry_journal')
        raw = u._read(directory/'registry.json', DOCUMENT_LIMIT)
        u._write(directory/'publication.json', {'schema_version': 1, 'nonce': self.nonce,
            'revision': value['revision'], 'registry_sha256': u._sha(raw)})
        _, raw, pointer = _load(self.root, self.nonce)
        self._raw = raw; self._pointer = pointer
        return {'registry': value, 'registry_sha256': u._sha(raw)}

    def _enroll(self, role, expected, *, unsupported):
        if role not in ROLES: _refuse('unsupported writer role cannot acquire authority')
        with self._admit(expected) as (value, directory):
            if value['state'] != 'open': _refuse('writer epoch is closed')
            if len(value['writers']) >= MAX_WRITERS: _refuse('writer count exceeds reviewed bound')
            identifier = uuid.uuid4().hex; child = directory/'writers'/identifier; child.mkdir(mode=0o700)
            identity = _create_lock(child/'ownership.lock'); _checkpoint('after_writer_lock')
            row = {'writer_id': identifier, 'role': role, 'status': 'unsupported' if unsupported else 'reserved',
                'lock_identity': identity, 'registration_sha256': None, 'process': None, 'exit_code': None,
                'reason_code': 'uncovered_protocol' if unsupported else None}
            row['registration_sha256'] = _update()._sha(_update()._canonical(_registration(value['binding'], row)))
            self._publish({**value, 'writers': [*value['writers'], row]}, directory)
            return Registration(identifier, row['registration_sha256'])

    def enroll(self, role, *, expected_registry_sha256):
        return self._enroll(role, expected_registry_sha256, unsupported=False)

    def block_unsupported(self, role, *, expected_registry_sha256):
        return self._enroll(role, expected_registry_sha256, unsupported=True)

    def _replace(self, value, directory, identifier, **changes):
        rows = [dict(r) for r in value['writers']]
        row = next((r for r in rows if r['writer_id'] == identifier), None)
        if row is None: _refuse('unknown original writer registration')
        row.update(changes)
        return self._publish({**value, 'writers': rows}, directory)

    def bind_original_child(self, writer_id, process, *, expected_registry_sha256):
        if type(process) is not subprocess.Popen: _refuse('original child OS handle is required; caller exit JSON is not authority')
        with self._admit(expected_registry_sha256) as (value, directory):
            row = next((r for r in value['writers'] if r['writer_id'] == writer_id), None)
            if value['state'] != 'open': _refuse('writer epoch is closed')
            if row is None or row['status'] != 'reserved' or writer_id in self._handles: _refuse('original writer handle already bound or unresolved')
            if process.poll() is not None: _refuse('original live child must be captured before exit')
            import psutil
            identity = _identity(process.pid)
            if psutil.Process(process.pid).ppid() != self._pid: _refuse('original handle is not this authority child')
            result = self._replace(value, directory, writer_id, status='active', process=identity)
            self._handles[writer_id] = (process, identity)
            return result

    def observe_original_child_exit(self, writer_id, *, expected_registry_sha256):
        with self._admit(expected_registry_sha256) as (value, directory):
            original = self._handles.get(writer_id)
            if original is None: _refuse('original retained child OS handle is unavailable')
            row = next((r for r in value['writers'] if r['writer_id'] == writer_id), None)
            process, identity = original
            if row is None or row['process'] != identity or process.pid != identity['pid']:
                _refuse('original child handle binding changed')
            code = process.poll()
            if code is None: _refuse('original child is still active')
            if row['status'] == 'direct_exited' and code == 0:
                return {'registry': value, 'registry_sha256': _update()._sha(self._raw)}
            if row['status'] != 'active': _refuse('uncertain original child cannot be repaired')
            return self._replace(value, directory, writer_id, status='direct_exited' if code == 0 else 'uncertain',
                exit_code=code, reason_code=None if code == 0 else 'child_failed')

    def bind_authenticated_node_backend(self, writer_id, authority, *, expected_registry_sha256):
        """Separate original Node transport capability; never substitute main Popen."""
        from backend.engine import application_node_writer_authority as node
        if type(authority) is not node.NodeBackendAuthority:
            _refuse('original typed Node backend authority is required')
        with self._admit(expected_registry_sha256) as (value, directory):
            row = next((r for r in value['writers'] if r['writer_id'] == writer_id), None)
            if (value['state'] != 'open' or row is None or row['role'] != 'backend'
                    or row['status'] != 'reserved' or writer_id in self._node_handles
                    or writer_id in self._handles):
                _refuse('original Node backend registration already bound or unresolved')
            identity, registration = node._core_binding(authority, self, writer_id)
            if row['registration_sha256'] != registration:
                _refuse('original Node backend registration pin differs')
            result = self._replace(value, directory, writer_id, status='active', process=identity)
            self._node_handles[writer_id] = (authority, identity)
            return result

    def observe_authenticated_node_backend_exit(self, writer_id, event, *, expected_registry_sha256):
        """Consume one original receive event; direct child is not a whole tree."""
        from backend.engine import application_node_writer_authority as node
        if type(event) is not node.NodeBackendEvent:
            _refuse('original one-use Node receive event is required')
        with self._admit(expected_registry_sha256) as (value, directory):
            original = self._node_handles.get(writer_id)
            row = next((r for r in value['writers'] if r['writer_id'] == writer_id), None)
            if (original is None or value['state'] != 'closed' or row is None
                    or row['role'] != 'backend' or row['status'] != 'active'):
                _refuse('original active Node backend handle is unavailable')
            authority, identity = original
            if row['process'] != identity or node._core_exit(authority, event, self, writer_id) != identity:
                _refuse('original Node backend identity differs')
            result = self._replace(value, directory, writer_id, status='direct_exited', exit_code=0)
            node.finish_exit_publication(authority)
            return result

    def mark_uncertain(self, writer_id, *, reason_code, expected_registry_sha256):
        if reason_code not in REASONS: _refuse('unsupported uncertainty reason code')
        with self._admit(expected_registry_sha256) as (value, directory):
            return self._replace(value, directory, writer_id, status='uncertain', reason_code=reason_code)

    def close_epoch(self, *, expected_registry_sha256):
        with self._admit(expected_registry_sha256) as (value, directory):
            if value['state'] == 'closed':
                return {'registry': value, 'registry_sha256': _update()._sha(self._raw)}
            return self._publish({**value, 'state': 'closed'}, directory)

    @contextmanager
    def enrolled_writer_fence(self, *, expected_registry_sha256):
        """Hold every original named lock exclusively; never release launch lease.

        A future reconciler must separately prove complete adapter coverage,
        authenticated backend drain and original main/backend-handle exits.
        """
        from contextlib import ExitStack
        with self._admit(expected_registry_sha256, exclusive=True) as (value, directory):
            if value['state'] != 'closed': _refuse('writer epoch must be closed')
            for row in value['writers']:
                if row['status'] != 'direct_exited': _refuse('unresolved '+row['status']+' writer blocks fence')
            with ExitStack() as stack:
                for row in value['writers']:
                    stack.enter_context(_locked(directory/'writers'/row['writer_id']/'ownership.lock',
                        row['lock_identity'], exclusive=True))
                before = (self._raw, self._pointer)
                yield {'schema_version': 1, 'scope': 'enrolled_writer_lifetime_locks_only',
                    'binding': value['binding'], 'registry_sha256': _update()._sha(self._raw),
                    'enrolled_writer_count': len(value['writers']), 'all_enrolled_lock_references_closed': True,
                    'can_release_launch_lease': False, 'process_tree_exit_verified': False,
                    'application_shutdown_verified': False}
                _, raw, pointer = _load(self.root, self.nonce)
                if before != (raw, pointer): _refuse('fenced registry publication changed')
