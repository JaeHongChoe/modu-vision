"""Early private-descriptor binding for an owned POSIX application backend.

This verifies controller transport and the committed application/database pair.
It grants no packaged-native, inference, or release acceptance. The original
controller remains responsible for durable launch ownership and recovery.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import select
import socket
import sys
import threading
import time
import weakref
from contextlib import contextmanager

MAX_FRAME = 65536
MAX_DEADLINE = 210
_CONTEXT_NAMES = ('VISION_AI_STUDIO_USER_DATA_DIR', 'VISION_APPLICATION_LAUNCH_NONCE',
    'VISION_APPLICATION_GENERATION', 'VISION_APPLICATION_DATABASE_GENERATION',
    'VISION_APPLICATION_BACKEND_FD', 'VISION_APPLICATION_LAUNCH_FD')
_CHALLENGE_FIELDS = {'schema_version', 'kind', 'challenge', 'epoch', 'nonce', 'binding',
    'main_process', 'backend_pid', 'backend_executable', 'backend_build_identity_sha256'}
_CACHE = None
_FIXED_CPU_PRODUCER = object()
_PREFLIGHT_TICKETS = weakref.WeakSet()
_PREFLIGHT_UNRESOLVED = set()


class BackendWorkAdmission:
    """Only the reviewed foreground scopes; no background/tree exit authority."""
    def __init__(self):
        self._condition = threading.Condition()
        self._closed = False
        self._active = 0
        self._unsupported = set()

    def enter(self, scope):
        with self._condition:
            if self._closed: return False
            self._active += 1
            # Even GET can create projects or recover jobs. Uncovered requests
            # still work normally, but cannot qualify a clean managed drain.
            if not (scope.get('type') == 'http' and scope.get('path') in {'/health', '/api/errors'}):
                self._unsupported.add('uncovered_request_protocol')
            return True

    def leave(self):
        with self._condition:
            if self._active < 1: raise HandshakeError('Accepted scope lifetime is unbalanced')
            self._active -= 1
            self._condition.notify_all()

    def response(self, status):
        if status == 202: self.uncovered('accepted_background_work')

    def uncovered(self, reason):
        if reason not in {'uncovered_request_protocol', 'accepted_background_work', 'startup_background_protocols', 'cpu_producer_unconfirmed'}:
            raise HandshakeError('Unknown uncovered writer protocol')
        with self._condition: self._unsupported.add(reason)

    @contextmanager
    def producer(self):
        self._begin_producer()
        try: yield
        except BaseException:
            self.uncovered('cpu_producer_unconfirmed')
            raise
        finally: self.leave()

    def _begin_producer(self):
        with self._condition:
            if self._closed: raise HandshakeError('Original backend producer admission is closed')
            self._active += 1

    def close(self):
        with self._condition: self._closed = True

    def snapshot(self):
        with self._condition:
            return {'active_scopes': self._active, 'unsupported': sorted(self._unsupported)}

    def drain(self, seconds):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 4:
            raise HandshakeError('Managed drain budget must be positive and at most four seconds')
        deadline = time.monotonic() + seconds
        with self._condition:
            if not self._closed: raise HandshakeError('Managed admission must close before draining')
            while self._active and not self._unsupported:
                remaining = deadline - time.monotonic()
                if remaining <= 0: break
                self._condition.wait(remaining)
            return {'status': 'refused' if self._active or self._unsupported else 'managed_scopes_drained',
                'active_scopes': self._active, 'unsupported': sorted(self._unsupported)}


def backend_work_admission():
    return _CACHE.get('admission') if _CACHE is not None else None


class _PreflightWriterTicket:
    """One original admitted lifetime, not reconstructable launch authority.

    No contextmanager or maintenance ContextVar crosses threads. The original
    request validates OPEN once; its private OFD duplicate and producer count
    remain until the exact dispatched thread completes its cleanup. The ticket
    grants no enrollment, process-tree exit, or release authority.
    """
    def __init__(self):
        raise HandshakeError('An original preflight ticket must be minted')

    def _original(self):
        if (self not in _PREFLIGHT_TICKETS or os.getpid() != self._pid
                or _CACHE is not self._cache or _context() != self._context):
            raise HandshakeError('Original preflight ticket context is unavailable')

    def claim(self):
        with self._lock:
            self._original()
            if self._phase not in {'created', 'dispatched'}:
                raise HandshakeError('Preflight ticket is already used or finished')
            if threading.current_thread() is not self._thread:
                raise HandshakeError('Original preflight ticket thread differs')
            self._phase = 'active'

    def dispatch_to(self, thread):
        with self._lock:
            self._original()
            if (threading.current_thread() is not self._creator or self._phase != 'created'
                    or not isinstance(thread, threading.Thread) or thread.ident is not None):
                raise HandshakeError('Preflight ticket requires one original new thread')
            if self._admission is not None: self._admission.uncovered('accepted_background_work')
            self._thread, self._phase = thread, 'dispatched'

    @contextmanager
    def transport(self, *, device):
        with self._lock:
            self._original()
            if self._phase != 'active' or threading.current_thread() is not self._thread:
                raise HandshakeError('Original preflight ticket is inactive or belongs to another thread')
            if type(device) is not str or device != self._device:
                raise HandshakeError('Original preflight ticket device differs')
            try: private = tuple(os.dup(fd) for fd in self._transport)
            except BaseException:
                if self._admission is not None: self._admission.uncovered('cpu_producer_unconfirmed')
                raise
            self._using_transport += 1
        try: yield private
        except BaseException:
            self.unconfirmed()
            raise
        finally:
            try:
                for fd in private: os.close(fd)
            except BaseException:
                # close may have consumed the descriptor before raising. Never
                # retry it or release this in-flight lifetime on that ambiguity.
                self.retain_unconfirmed()
                raise
            else:
                with self._lock: self._using_transport -= 1

    def unconfirmed(self):
        with self._lock:
            self._original()
            if threading.current_thread() not in (self._creator, self._thread):
                raise HandshakeError('Original preflight ticket cleanup thread differs')
            if self._admission is not None: self._admission.uncovered('cpu_producer_unconfirmed')

    def retain_unconfirmed(self):
        """Unknown cleanup retains custody; no observation repairs this ticket."""
        with self._lock:
            self._original()
            if (threading.current_thread() not in (self._creator, self._thread)
                    or self._phase == 'finished'):
                raise HandshakeError('Original preflight ticket cleanup differs')
            self._retain_locked()

    def _retain_locked(self):
        if self._admission is not None: self._admission.uncovered('cpu_producer_unconfirmed')
        self._phase = 'unresolved'
        # Keep original custody/count after any uncertain close or final write.
        # The descriptor is never inspected, retried, or exposed again.
        _PREFLIGHT_UNRESOLVED.add(self)

    def _release(self, before_leave):
        self._phase = 'settling'
        try:
            for fd in self._transport: os.close(fd)  # Never LOCK_UN the inherited OFD.
            # Reservation release and final state publication remain counted,
            # and only happen after every original ticket close returned.
            if before_leave is not None: before_leave()
            if self._admission is not None: self._admission.leave()
        except BaseException:
            self._retain_locked()
            raise
        self._phase = 'finished'

    def finish(self, *, before_leave=None):
        with self._lock:
            self._original()
            if (self._phase not in {'created', 'active', 'cancelled'} or self._using_transport
                    or threading.current_thread() is not self._thread):
                raise HandshakeError('Original preflight ticket is inactive or belongs to another thread')
            self._release(before_leave)

    def cancel_dispatch(self):
        """Abort before claim, or retain custody after ambiguous Thread.start."""
        with self._lock:
            self._original()
            if threading.current_thread() is not self._creator:
                raise HandshakeError('Only the original dispatcher can cancel its ticket')
            if self._admission is not None: self._admission.uncovered('cpu_producer_unconfirmed')
            if self._phase == 'dispatched':
                self._phase, self._thread = 'cancelled', self._creator
                return True  # Retain custody through the dispatcher's cleanup.
            if self._phase in {'active', 'finished'}:
                return False  # Never close a capability already held by work.
            raise HandshakeError('Original preflight ticket has not been dispatched')


def _mint_preflight_ticket(device, transport, admission, *, unresolved=False):
    ticket = object.__new__(_PreflightWriterTicket)
    ticket._pid, ticket._cache, ticket._context = os.getpid(), _CACHE, _context()
    ticket._creator = ticket._thread = threading.current_thread()
    ticket._device, ticket._transport, ticket._admission = device, transport, admission
    ticket._phase, ticket._lock, ticket._using_transport = ('unresolved' if unresolved else 'created'), threading.Lock(), 0
    _PREFLIGHT_TICKETS.add(ticket)
    if unresolved: _PREFLIGHT_UNRESOLVED.add(ticket)
    return ticket


def create_preflight_writer_ticket(*, device):
    """Acquire before planning/writes; retain the original capability on dispatch.

    Device selection stays the preflight's own explicit CPU/CUDA/MPS contract.
    This adapter does not discover or execute an accelerator at admission.
    """
    if type(device) is not str or device not in {'cpu', 'cuda', 'mps'}:
        raise HandshakeError('Preflight ticket requires an explicit supported device')
    context = _root_context()
    if _CACHE is None:
        if context is not None: raise HandshakeError('Owned preflight has no original cached writer admission')
        return _mint_preflight_ticket(device, (), None)
    if context is None or _context() != _CACHE['context'] or not _CACHE['ready']:
        raise HandshakeError('Original preflight writer capability is unavailable')
    root, values = context
    validated = {}
    if _validate(root, values, _CACHE['challenge'], validated=validated) != _CACHE['proof']:
        raise HandshakeError('Original preflight process binding changed')
    admission = _CACHE['admission']
    fields = {'writer_guard', 'writer_handle', 'writer_private_fd', 'writer_fd_identity'}
    if 'writer' not in _CACHE['challenge']:
        if (validated.get('protocol_version') != 3 or not fields.issubset(_CACHE)
                or any(_CACHE[k] is not None for k in fields)):
            raise HandshakeError('Original legacy preflight capability is partial or unsupported')
        admission._begin_producer()
        try: return _mint_preflight_ticket(device, (), admission)
        except BaseException: admission.leave(); raise
    if validated.get('protocol_version') != 4 or any(_CACHE.get(k) is None for k in fields):
        raise HandshakeError('Original enrolled preflight capability is partial')
    writer = _CACHE['challenge']['writer']
    from backend.engine.application_launch_quiescence import writer_guard
    # Finish every validation/registry/maintenance context on this thread.
    private = None; counted = False
    try:
        with writer_guard(root, _CACHE['proof']['nonce'], writer['writer_id'],
                expected_registration_sha256=writer['registration_sha256']):
            anchor = _CACHE['writer_private_fd']; info = os.fstat(anchor)
            if (info.st_dev, info.st_ino) != _CACHE['writer_fd_identity']:
                raise HandshakeError('Original backend writer descriptor identity changed')
            private = os.dup(anchor)
            admission._begin_producer(); counted = True
        # Mint only after the validation guard's final identity check succeeds.
        return _mint_preflight_ticket(device, (private,), admission)
    except BaseException:
        if counted: admission.uncovered('cpu_producer_unconfirmed')
        try:
            if private is not None: os.close(private)
        except BaseException:
            # Guard validation failed, so no usable ticket may escape. Retain
            # opaque original custody without retrying a possibly consumed fd.
            admission.uncovered('cpu_producer_unconfirmed')
            _mint_preflight_ticket(device, (private,), admission, unresolved=True)
            raise
        else:
            if counted: admission.leave()
        raise


@contextmanager
def _preflight_ticket_transport(ticket, *, device):
    if type(ticket) is not _PreflightWriterTicket or ticket not in _PREFLIGHT_TICKETS:
        raise HandshakeError('An original minted preflight ticket is required')
    with ticket.transport(device=device) as transport: yield transport


@contextmanager
def owned_cpu_writer_scope():
    """One fixed CPU producer; duplicate original OFD, never expose its anchor.

    The bootstrap private duplicate is the capability. Inode checks detect
    storage changes, not OFD equivalence. Arbitrary trusted-process descriptor
    table mutation is outside this private in-process contract.
    """
    with _owned_cpu_producer_scope() as transport:
        yield transport


@contextmanager
def owned_flow_cpu_writer_scope(*, device):
    """Selected CPU package worker only; unowned SDK devices keep defaults."""
    with _owned_cpu_producer_scope(flow_device=device) as transport:
        yield transport


@contextmanager
def _owned_cpu_producer_scope(*, flow_device=_FIXED_CPU_PRODUCER):
    context = _root_context()
    if _CACHE is None:
        if context is not None: raise HandshakeError('Owned CPU producer has no original cached writer admission')
        yield ()  # Ordinary unowned/export compatibility, no owned authority.
        return
    if flow_device is not _FIXED_CPU_PRODUCER and (type(flow_device) is not str or flow_device != 'cpu'):
        raise HandshakeError('Owned flow producer admission supports only selected CPU execution')
    if context is None or _context() != _CACHE['context'] or not _CACHE['ready']:
        raise HandshakeError('Original owned CPU producer capability is unavailable')
    root, values = context
    validated = {}
    if _validate(root, values, _CACHE['challenge'], validated=validated) != _CACHE['proof']:
        raise HandshakeError('Original owned CPU producer process binding changed')
    if 'writer' not in _CACHE['challenge']:
        fields = {'writer_guard', 'writer_handle', 'writer_private_fd', 'writer_fd_identity'}
        if (validated.get('protocol_version') != 3 or not fields.issubset(_CACHE)
                or any(_CACHE[k] is not None for k in fields)):
            raise HandshakeError('Original legacy CPU producer capability is partial or unsupported')
        # Original authenticated protocol3 compatibility: count the accepted
        # producer, but do not invent an enrolled lock or descendant fence.
        with _CACHE['admission'].producer(): yield ()
        return
    if any(_CACHE.get(k) is None for k in ('writer_guard', 'writer_handle', 'writer_private_fd', 'writer_fd_identity')):
        raise HandshakeError('Original enrolled CPU producer capability is partial')
    writer = _CACHE['challenge']['writer']
    from backend.engine.application_launch_quiescence import writer_guard
    with writer_guard(root, _CACHE['proof']['nonce'], writer['writer_id'],
            expected_registration_sha256=writer['registration_sha256']):
        # Temporary guard linearizes OPEN admission. The exposed Guard number
        # cannot become authority after close/reuse; duplicate only the private
        # anchor that bootstrap obtained directly from the acquired original OFD.
        anchor = _CACHE['writer_private_fd']
        info = os.fstat(anchor)
        if (info.st_dev, info.st_ino) != _CACHE['writer_fd_identity']:
            raise HandshakeError('Original backend writer descriptor identity changed')
        with _CACHE['admission'].producer():
            transport = os.dup(anchor)
            try: yield (transport,)
            finally: os.close(transport)


class HandshakeError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8', 'strict')


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length and all(c in '0123456789abcdef' for c in value)


def validate_drain_request(frame, *, nonce, epoch, binding, writer_id, registration_sha256):
    names = {'schema_version', 'kind', 'challenge', 'request_id', 'nonce', 'epoch', 'binding_sha256',
        'backend_claim_sha256', 'writer_id', 'registration_sha256', 'closed_registry_sha256', 'budget_ms'}
    if (not isinstance(frame, dict) or set(frame) != names or type(frame['schema_version']) is not int
            or frame['schema_version'] != 1 or frame['kind'] != 'backend_drain_request'
            or not _hex(frame['challenge']) or not _hex(frame['request_id'], 32)
            or frame['nonce'] != nonce or frame['epoch'] != epoch
            or frame['binding_sha256'] != hashlib.sha256(_canonical(binding)).hexdigest()
            or not _hex(frame['backend_claim_sha256']) or frame['writer_id'] != writer_id
            or frame['registration_sha256'] != registration_sha256 or not _hex(frame['closed_registry_sha256'])
            or type(frame['budget_ms']) is not int or not 0 < frame['budget_ms'] <= 4000):
        raise HandshakeError('Foreign or invalid managed drain request')
    return frame


def validate_drain_receipt(frame, request, *, backend_process):
    names = {'schema_version', 'kind', 'request', 'backend_proof', 'status', 'active_scopes', 'unsupported',
        'scope', 'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'}
    if (not isinstance(frame, dict) or set(frame) != names or type(frame['schema_version']) is not int
            or frame['schema_version'] != 1 or frame['kind'] != 'backend_managed_drain'
            or _canonical(frame['request']) != _canonical(request) or not isinstance(frame['backend_proof'], dict)
            or not _same_process(frame['backend_proof'].get('process'), backend_process)
            or hashlib.sha256(_canonical(frame['backend_proof'])).hexdigest() != request['backend_claim_sha256']
            or not isinstance(frame['status'], str) or frame['status'] not in {'managed_scopes_drained', 'refused'} or type(frame['active_scopes']) is not int
            or frame['active_scopes'] < 0 or not isinstance(frame['unsupported'], list)
            or any(not isinstance(x, str) for x in frame['unsupported']) or frame['unsupported'] != sorted(set(frame['unsupported']))
            or any(x not in {'uncovered_request_protocol', 'accepted_background_work', 'startup_background_protocols', 'cpu_producer_unconfirmed'} for x in frame['unsupported'])
            or frame['scope'] != 'reviewed_foreground_scopes_only'
            or any(frame[x] is not False for x in ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
            or frame['status'] == 'managed_scopes_drained' and (frame['active_scopes'] != 0 or frame['unsupported'])):
        raise HandshakeError('Managed drain proof is changed or exceeds its scope')
    return frame


def validate_backend_exit(frame, request, backend_process):
    expected = {'schema_version': 1, 'kind': 'main_backend_exit', 'nonce': request['nonce'],
        'epoch': request['epoch'], 'request_id': request['request_id'], 'challenge': request['challenge'],
        'backend_process': backend_process, 'returncode': 0, 'signal': None}
    if (not isinstance(frame, dict) or set(frame) != set(expected)
            or not _same_process(frame['backend_process'], backend_process)
            or type(frame['schema_version']) is not int or type(frame['returncode']) is not int
            or any(frame[k] != v for k, v in expected.items() if k != 'backend_process')):
        raise HandshakeError('Original Node backend handle exit frame differs')
    return frame


def _deadline(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= MAX_DEADLINE:
        raise HandshakeError('Private descriptor deadline must be positive and at most 210 seconds')
    return time.monotonic() + seconds


def read_frame(sock, deadline_seconds):
    """Read one canonical bounded UTF-8 JSON object; leave later frames unread."""
    deadline = _deadline(deadline_seconds); raw = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([sock], [], [], max(0, remaining))[0]:
                raise HandshakeError('Private descriptor timeout before complete frame')
            chunk = sock.recv(1)
            if not chunk: raise HandshakeError('Private descriptor EOF before complete frame')
            raw.extend(chunk)
            if len(raw) > MAX_FRAME: raise HandshakeError('Private descriptor frame exceeds 65536 bytes')
            if chunk == b'\n': break
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value: raise HandshakeError('Duplicate private descriptor field')
                value[key] = item
            return value
        value = json.loads(bytes(raw[:-1]).decode('utf-8', 'strict'), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(HandshakeError('Nonfinite private descriptor number')))
        if not isinstance(value, dict) or _canonical(value) + b'\n' != raw:
            raise HandshakeError('Private descriptor frame is not a canonical JSON object')
        return value
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Invalid private descriptor frame: ' + str(exc)) from exc


def send_frame(sock, value, *, absolute_deadline=None):
    """Write one bounded canonical object without an unbounded peer wait."""
    try:
        deadline = _deadline(10)
        if absolute_deadline is not None:
            if type(absolute_deadline) not in (int, float) or not math.isfinite(absolute_deadline):
                raise HandshakeError('Original private send deadline differs')
            deadline = min(deadline, absolute_deadline)
        if not isinstance(value, dict): raise HandshakeError('Private descriptor frame must be an object')
        raw = _canonical(value) + b'\n'
        if len(raw) > MAX_FRAME: raise HandshakeError('Private descriptor frame exceeds 65536 bytes')
        offset = 0
        while offset < len(raw):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [sock], [], max(0, remaining))[1]:
                raise HandshakeError('Private descriptor timeout while sending frame')
            sent = sock.send(raw[offset:], getattr(socket, 'MSG_DONTWAIT', 0))
            if sent <= 0: raise HandshakeError('Private descriptor closed while sending frame')
            offset += sent
        if time.monotonic() >= deadline: raise HandshakeError('Original private send completed after its deadline')
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Cannot send private descriptor frame: ' + str(exc)) from exc


def _context():
    return (tuple(os.environ.get(name) for name in _CONTEXT_NAMES), tuple(sys.argv),
            sys.executable, bool(getattr(sys, 'frozen', False)))


def _root_context():
    """Read context without discovery, creation, or application imports."""
    from backend.engine.global_store_paths import owned_root
    values = {name: os.environ.get(name) for name in _CONTEXT_NAMES}
    launch = [values[name] for name in _CONTEXT_NAMES[1:]]
    if values['VISION_APPLICATION_LAUNCH_FD'] is not None:
        raise HandshakeError('Main launch descriptor must be stripped before backend startup')
    configured = values[_CONTEXT_NAMES[0]]
    if configured is None:
        if any(value is not None for value in launch): raise HandshakeError('Partial owned backend launch context')
        return None
    if not configured or not Path(configured).is_absolute() or str(Path(configured)) != configured:
        raise HandshakeError('Owned backend root must be an explicit canonical absolute path')
    root, owner = owned_root(configured)
    if root is None:
        if any(value is not None for value in launch): raise HandshakeError('Backend launch root has no original ownership')
        return None
    if str(root) != configured: raise HandshakeError('Backend launch root differs from original ownership')
    present = any(value is not None for value in launch)
    if not present:
        if (root/'application-active.json').exists() or any((root/name).exists() for name in
                ('application-launch-lease.json', '.application-launches', 'application-database-ownership.lock')):
            raise HandshakeError('Owned application pair requires its private backend descriptor')
        return None
    if (not _hex(values['VISION_APPLICATION_LAUNCH_NONCE'], 32)
            or not _hex(values['VISION_APPLICATION_GENERATION'], 32)
            or not values['VISION_APPLICATION_DATABASE_GENERATION']
            or values['VISION_APPLICATION_BACKEND_FD'] is None):
        raise HandshakeError('Incomplete owned backend launch context')
    return root, values


def _transport(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or str(int(value)) != value or not 3 <= int(value) <= 8192:
        raise HandshakeError('Backend descriptor must be a canonical inherited descriptor from 3 to 8192')
    fd = int(value)
    try:
        os.set_inheritable(fd, False)
        sock = socket.socket(fileno=fd)
        if (os.name != 'posix' or sock.family != socket.AF_UNIX or sock.type != socket.SOCK_STREAM
                or sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
                or sock.getsockname() not in ('', b'') or sock.getpeername() not in ('', b'')):
            raise HandshakeError('Backend descriptor must be an anonymous connected POSIX stream socket')
        return sock
    except (OSError, ValueError) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Backend private descriptor is unavailable') from exc


def _arguments(root):
    expected = {'--project-dir': str(root/'projects'), '--shared-auth-dir': str(root/'auth')}
    for flag, path in expected.items():
        for argument in sys.argv[1:]:
            option = argument.split('=', 1)[0]
            if option.startswith('--') and option != flag and flag.startswith(option):
                raise HandshakeError('Abbreviated owned backend path option is ambiguous')
        matches = [i for i, argument in enumerate(sys.argv[1:], 1) if argument == flag or argument.startswith(flag+'=')]
        if len(matches) != 1: raise HandshakeError('Owned backend requires exactly one ' + flag)
        i = matches[0]; arg = sys.argv[i]
        value = arg.split('=', 1)[1] if '=' in arg else sys.argv[i+1] if i+1 < len(sys.argv) else None
        if value != path: raise HandshakeError('Owned backend ' + flag + ' differs from original root')


def _same_process(value, actual):
    from backend.engine.application_launch_lease import _identity_shape
    return _identity_shape(value) and value['pid'] == actual['pid'] and value['created_at'] == actual['created_at'] and value['command_sha256'] == actual['command_sha256']


def _executable(root, binding, frame):
    from backend.engine import runtime_update as update
    _, _, manifest = update._validated_intent(root, binding['update_id'])
    application = root/update.GENERATIONS/binding['application_generation']/'application'
    executable = frame['backend_executable']
    if not isinstance(executable, str) or not Path(executable).is_absolute() or str(Path(executable)) != executable:
        raise HandshakeError('Backend executable must be an absolute current application row')
    path = update._unlinked(executable)
    try: relative = path.relative_to(application).as_posix()
    except ValueError as exc: raise HandshakeError('Backend executable is outside current application') from exc
    rows = [row for row in manifest['files'] if row['path'] == relative and row['executable']]
    if len(rows) != 1: raise HandshakeError('Backend executable is not an approved current application row')
    row = rows[0]; update._check_file(path, row)
    import psutil
    command = psutil.Process(os.getpid()).cmdline()
    frozen = bool(getattr(sys, 'frozen', False)); build = frame['backend_build_identity_sha256']
    if frozen:
        if sys.executable != executable or not command or command[0] != executable or not _hex(build):
            raise HandshakeError('Frozen backend executable or build identity differs')
        receipt_path = path.parent/'backend-release.json'
        receipt_rows = [item for item in manifest['files'] if item['path'] == receipt_path.relative_to(application).as_posix()]
        if len(receipt_rows) != 1: raise HandshakeError('Frozen backend release receipt is not an approved application row')
        update._check_file(receipt_path, receipt_rows[0]); receipt = update._json(update._read(receipt_path, 8*1024**2))
        inventory = update._json(update._read(Path(getattr(sys, '_MEIPASS', ''))/'backend-build-inventory.json', 8*1024**2))
        if (not isinstance(receipt, dict) or receipt.get('schema_version') != 1 or receipt.get('executable') != path.name
                or receipt.get('executable_sha256') != row['sha256'] or not isinstance(inventory, dict)
                or inventory.get('build_identity_sha256') != build
                or update._canonical(receipt.get('inventory')) != update._canonical(inventory)):
            raise HandshakeError('Frozen backend release receipt or embedded inventory differs')
    else:
        if build is not None or not sys.argv or sys.argv[0] != executable or executable not in command[1:2]:
            raise HandshakeError('Source backend must execute its approved script and cannot claim a frozen build')
    return {'executable': executable, 'executable_sha256': row['sha256'],
            'build_identity_sha256': build, 'frozen': frozen}


def _validate(root, values, frame, *, validated=None):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    expected_fields = _CHALLENGE_FIELDS | ({'writer'} if 'writer' in frame else set())
    if (set(frame) != expected_fields or type(frame['schema_version']) is not int or frame['schema_version'] != 1
            or frame['kind'] != 'backend_challenge' or not _hex(frame['challenge']) or not _hex(frame['epoch'], 32)
            or frame['nonce'] != values['VISION_APPLICATION_LAUNCH_NONCE'] or type(frame['backend_pid']) is not int
            or frame['backend_pid'] != os.getpid()): raise HandshakeError('Invalid or foreign backend challenge')
    _arguments(root)
    with lease._transition_admission(root, frame['nonce']):
        record = lease._load(root)
        if (record is None or record['nonce'] != frame['nonce'] or record['state'] not in {'starting', 'ready'}
                or not record['spawn_attempted'] or record['process'] is None):
            raise HandshakeError('Backend challenge has no current spawned main owner')
        binding = record['binding']
        if 'writer' in frame:
            drain = record.get('writer_drain')
            expected = {k: drain[k] for k in ('writer_id', 'registration_sha256', 'registration_registry_sha256')} if drain else None
            if expected is None or _canonical(frame['writer']) != _canonical(expected):
                raise HandshakeError('Backend writer registration differs from original controller')
        elif record['protocol_version'] == 4:
            raise HandshakeError('Enrolled backend requires exact private writer registration')
        if (_canonical(frame['binding']) != _canonical(binding)
                or values['VISION_APPLICATION_GENERATION'] != binding['application_generation']
                or values['VISION_APPLICATION_DATABASE_GENERATION'] != binding['database_generation_path']):
            raise HandshakeError('Backend challenge application/database binding differs')
        fresh = update._launch_binding(root, binding['authority_path'], pinned_authority_sha256=binding['authority_sha256'])
        if _canonical(fresh) != _canonical(binding): raise HandshakeError('Committed backend launch pair changed')
        try: main = lease._identity(os.getppid())
        except Exception as exc:
            import psutil
            if not isinstance(exc, psutil.Error): raise
            raise HandshakeError('Backend parent process identity is unavailable') from exc
        if not _same_process(frame['main_process'], main) or not _same_process(record['process'], main):
            raise HandshakeError('Backend challenge main process birth or parent identity differs')
        executable = _executable(root, binding, frame)
        if validated is not None: validated['protocol_version'] = record['protocol_version']
        return {'schema_version': 1, 'kind': 'backend_claim', 'challenge': frame['challenge'], 'epoch': frame['epoch'],
            'nonce': frame['nonce'], 'binding_sha256': hashlib.sha256(_canonical(binding)).hexdigest(),
            'process': lease._identity(os.getpid()), **executable}


def _no_replay(sock, *, ready=False):
    if select.select([sock], [], [], 0)[0]:
        raw = sock.recv(1, socket.MSG_PEEK | getattr(socket, 'MSG_DONTWAIT', 0))
        if raw: raise HandshakeError('Unexpected replay on backend private descriptor')
        if not ready: raise HandshakeError('Backend private descriptor closed before readiness')


def early_backend_bootstrap():
    """Authenticate once before application imports; recheck every local reuse."""
    global _CACHE
    try:
        context = _root_context()
        if _CACHE is not None:
            if context is None or _context() != _CACHE['context']:
                raise HandshakeError('Verified backend process context changed')
            root, values = context
            _no_replay(_CACHE['socket'], ready=_CACHE['ready'])
            proof = _validate(root, values, _CACHE['challenge'])
            if proof != _CACHE['proof']: raise HandshakeError('Verified backend process binding changed')
            return dict(proof)
        if context is None: return None
        root, values = context; sock = _transport(values['VISION_APPLICATION_BACKEND_FD'])
        frame = read_frame(sock, 210); proof = _validate(root, values, frame)
        guard = None; handle = None; fd_identity = None; private_fd = None
        if 'writer' in frame:
            from backend.engine.application_launch_quiescence import writer_guard, inspect_epoch
            writer = frame['writer']; snapshot = inspect_epoch(root, frame['nonce'])
            if snapshot['registry_sha256'] != writer['registration_registry_sha256']:
                raise HandshakeError('Backend initial writer registry changed before admission')
            guard = writer_guard(root, frame['nonce'], writer['writer_id'], expected_registration_sha256=writer['registration_sha256'])
            handle = guard.__enter__()  # Retained before claim/mutable imports.
            info = os.fstat(handle.pass_fds[0]); fd_identity = (info.st_dev, info.st_ino)
            private_fd = os.dup(handle.pass_fds[0])  # Original acquired OFD, never exported.
        _no_replay(sock); send_frame(sock, proof)
        _CACHE = {'context': _context(), 'root': root, 'socket': sock, 'challenge': frame, 'proof': proof,
            'ready': False, 'writer_guard': guard, 'writer_handle': handle, 'writer_private_fd': private_fd, 'writer_fd_identity': fd_identity,
            'admission': BackendWorkAdmission()}
        from backend.engine.application_preflight_child_relay import BackendRelayQueue
        _CACHE['preflight_queue'] = BackendRelayQueue(sock)
        return dict(proof)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Owned backend bootstrap refused: ' + str(exc)) from exc


def backend_bootstrap_ready():
    """Publish binding readiness only after recovery under shared lifespan admission."""
    proof = early_backend_bootstrap()
    if proof is None: return None
    from backend.engine.migration_guard import shared_admitted
    root = _CACHE['root']
    if not shared_admitted(root):
        raise HandshakeError('Backend readiness requires admitted shared application lifespan')
    if not _CACHE['ready']:
        send_frame(_CACHE['socket'], {**proof, 'kind': 'backend_ready'}); _CACHE['ready'] = True
    return {**proof, 'kind': 'backend_ready'}


def _validate_service_action(original_cache, deadline):
    """Read-only exact binding checks within one existing channel deadline.

    Publication mutex contention changes no authority or record. Only that
    named transient can wait, on the same cached endpoint/context and absolute
    budget; every structural failure immediately refuses without repair.
    """
    from backend.engine.application_launch_lease import LeaseTransitionBusy
    from backend.engine.application_preflight_child_relay import BackendRelayQueue
    original_queue = original_cache.get('preflight_queue')
    original_socket = original_cache.get('socket')
    if type(original_queue) is not BackendRelayQueue:
        raise HandshakeError('Original service action queue is unavailable')
    def endpoint():
        if (original_cache.get('preflight_queue') is not original_queue
                or original_cache.get('socket') is not original_socket):
            raise HandshakeError('Original service action queue/socket changed')
        original_queue._fresh(original_socket, reader=True)
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise HandshakeError('Original service action deadline is invalid')
    while True:
        context = _root_context()
        if (_CACHE is not original_cache or context is None
                or _context() != original_cache['context'] or not original_cache['ready']):
            raise HandshakeError('Original service action cache/context differs')
        endpoint()
        if time.monotonic() >= deadline:
            raise HandshakeError('Original service binding verification deadline expired')
        try:
            proof = _validate(original_cache['root'], context[1], original_cache['challenge'])
        except LeaseTransitionBusy:
            endpoint()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise HandshakeError('Original service binding verification deadline expired')
            time.sleep(min(.005, remaining))
            continue
        if (_CACHE is not original_cache or _context() != original_cache['context']
                or proof != original_cache['proof']):
            raise HandshakeError('Original service action binding changed')
        endpoint()
        if time.monotonic() >= deadline:
            raise HandshakeError('Original service binding verification completed late')
        return proof


def backend_execution_service(stop_event=None):
    """Consume at most one controller-origin CPU request after admitted ready.

    Idle waiting has no bootstrap deadline. A request has a hard worker budget;
    EOF, replay or an interrupted execution closes the epoch instead of retrying.
    The daemon caller retains its shared installation lifespan throughout.
    """
    if _CACHE is None or not _CACHE['ready']:raise HandshakeError('CPU consumer requires original admitted backend readiness')
    from backend.engine.migration_guard import maintenance_guard
    from backend.engine.application_launch_execution import execute_backend
    original_cache = _CACHE
    root=original_cache['root'];sock=original_cache['socket'];proof=original_cache['proof'];executed=False;drained=False
    from backend.engine.application_preflight_child_relay import BackendRelayQueue
    queue = _CACHE['preflight_queue']
    if type(queue) is not BackendRelayQueue: raise HandshakeError('Original backend sole reader queue differs')
    queue.claim_reader(); pending = None; cpu = None; drain = None
    try:
        with maintenance_guard(root):
            while stop_event is None or not stop_event.is_set():
                context=_root_context()
                if _CACHE is not original_cache or context is None or _context()!=original_cache['context']:raise HandshakeError('CPU backend process context changed')
                # Idle polling holds no publication mutex. Exact endpoint and
                # context remain fresh; full binding reads surround each real
                # authenticated channel action inside its original deadline.
                queue._fresh(sock, reader=True)
                if pending is None:
                    pending = queue.take(sock)
                    if pending is not None:
                        outgoing, deadline = queue.outgoing(pending)
                        if (time.monotonic() >= deadline or drained and outgoing['action'] != 'finish'):
                            raise HandshakeError('Original preflight exchange is late or admission is closed')
                        deadline = min(deadline, _CACHE.get('preflight_drain_deadline', float('inf')))
                        proof = _validate_service_action(original_cache, deadline)
                        send_frame(sock, outgoing, absolute_deadline=deadline)
                        _validate_service_action(original_cache, deadline)
                if cpu is not None and cpu['done'].is_set():
                    if cpu['error'] is not None: raise HandshakeError('Original CPU execution callback failed') from cpu['error']
                    proof = _validate_service_action(original_cache, cpu['deadline'])
                    send_frame(sock, cpu['result'], absolute_deadline=cpu['deadline'])
                    _validate_service_action(original_cache, cpu['deadline']); cpu = None
                if drain is not None:
                    # Keep this sole reader available for an already accepted
                    # child's final ACK while admission waits. One original
                    # deadline; no second socket reader or refreshed budget.
                    snapshot = _CACHE['admission'].snapshot()
                    if not snapshot['active_scopes'] or snapshot['unsupported'] or time.monotonic() >= drain['deadline']:
                        completed = {'schema_version': 1, 'kind': 'backend_managed_drain', 'request': drain['frame'],
                            'backend_proof': proof, 'status': 'refused' if snapshot['active_scopes'] or snapshot['unsupported'] else 'managed_scopes_drained',
                            **snapshot, 'scope': 'reviewed_foreground_scopes_only', 'whole_writer_coverage': False,
                            'process_tree_exit_verified': False, 'can_release_launch_lease': False}
                        proof = _validate_service_action(original_cache, drain['deadline'])
                        validate_drain_receipt(completed, drain['frame'], backend_process=proof['process'])
                        send_frame(sock, completed, absolute_deadline=drain['deadline'])
                        _validate_service_action(original_cache, drain['deadline']); drain = None
                if pending is not None:
                    _, deadline = queue.outgoing(pending)
                    if time.monotonic() >= deadline: raise HandshakeError('Original preflight exchange deadline expired')
                wait = .02 if pending is not None or cpu is not None or drain is not None else .2
                if not select.select([sock],[],[],wait)[0]:continue
                budget = 10
                if pending is not None: budget = min(budget, queue.outgoing(pending)[1]-time.monotonic())
                if drain is not None: budget = min(budget, drain['deadline']-time.monotonic())
                if budget <= 0: raise HandshakeError('Original private service request deadline expired')
                deadline = time.monotonic()+budget
                proof = _validate_service_action(original_cache, deadline)
                frame=read_frame(sock,deadline-time.monotonic())
                proof = _validate_service_action(original_cache, deadline)
                if frame.get('kind') == 'controller_preflight_reply':
                    if pending is None: raise HandshakeError('Original preflight reply is unsolicited or replayed')
                    queue.complete(pending, frame); pending = None
                    continue
                if frame.get('kind') == 'backend_drain_request':
                    if drained:raise HandshakeError('Managed drain request replay requires recovery')
                    started = time.monotonic()
                    writer = _CACHE['challenge'].get('writer')
                    if writer is None or _CACHE['writer_guard'] is None:
                        raise HandshakeError('Managed drain requires original retained writer admission')
                    validate_drain_request(frame, nonce=proof['nonce'], epoch=proof['epoch'], binding=_CACHE['challenge']['binding'],
                        writer_id=writer['writer_id'], registration_sha256=writer['registration_sha256'])
                    if frame['backend_claim_sha256'] != hashlib.sha256(_canonical(proof)).hexdigest():
                        raise HandshakeError('Managed drain original backend claim changed')
                    from backend.engine.application_launch_quiescence import inspect_epoch
                    snapshot = inspect_epoch(root, frame['nonce'])
                    if snapshot['registry']['state'] != 'closed' or snapshot['registry_sha256'] != frame['closed_registry_sha256']:
                        raise HandshakeError('Managed drain requires original closed writer epoch')
                    state = _CACHE['admission']; state.close(); drained = True
                    drain = {'frame': frame, 'deadline': started+frame['budget_ms']/1000}
                    _CACHE['preflight_drain_deadline'] = drain['deadline']
                    queue.close_admission(drain['deadline'])
                else:
                    if executed or drained:raise HandshakeError('CPU private request replay or closed admission requires recovery')
                    executed=True
                    cpu = {'done': threading.Event(), 'result': None, 'error': None, 'deadline': time.monotonic()+210}
                    execution = cpu
                    def work(request=frame, authenticated=proof, state=execution):
                        try: state['result'] = execute_backend(request,authenticated,root)
                        except BaseException as exc:
                            _CACHE['admission'].uncovered('cpu_producer_unconfirmed'); state['error'] = exc
                        finally: state['done'].set()
                    execution['thread'] = threading.Thread(target=work, name='owned-controller-cpu', daemon=True)
                    _CACHE['original_cpu_execution'] = execution  # Strong retained original callback, never receipt adoption.
                    execution['thread'].start()
    except BaseException:
        queue.abandon(HandshakeError('Original backend private service is unresolved'))
        _CACHE['admission'].uncovered('cpu_producer_unconfirmed')
        # Closing this original endpoint tells main/controller to retain durable
        # recovery. It never proves worker descendants exited or clears a lease.
        try:sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        sock.close()
        raise
