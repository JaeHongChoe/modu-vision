"""One original backend-owned preflight child; no whole writer/tree release.

Wire dictionaries are bounded data. Only the original authenticated Node event
and backend's create-only retained Popen holder can confer adapter authority.
The controller remains the only writer-registry mutator.
"""
import copy
import hashlib
import math
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import threading
import time
import weakref

from backend.engine.application_launch_handshake import HandshakeError

_MINTS = set()
_CONTROLLERS = weakref.WeakKeyDictionary()
_CHILDREN = weakref.WeakKeyDictionary()
_UNRESOLVED = set()
_ACTIVE = set()
_EXCHANGES = weakref.WeakKeyDictionary()
_ORIGINAL_POPEN_TYPE = subprocess.Popen


class _Opaque:
    __slots__ = ('__weakref__',)
    def __init__(self, *args, _mint=None, **kwargs):
        if args or kwargs or _mint not in _MINTS:
            raise HandshakeError('Original create-only preflight capability is unavailable')
    def __copy__(self): raise HandshakeError('Original preflight capability cannot be copied')
    def __deepcopy__(self, memo): raise HandshakeError('Original preflight capability cannot be copied')
    def __reduce_ex__(self, protocol): raise HandshakeError('Original preflight capability cannot be serialized')


class ControllerPreflightRelay(_Opaque):
    __slots__ = ()


class BackendPreflightChild(_Opaque):
    __slots__ = ()


class _Exchange(_Opaque):
    __slots__ = ()


def _new(cls):
    mint = object(); _MINTS.add(mint)
    try: return cls(_mint=mint)
    finally: _MINTS.discard(mint)


def assert_original(capability):
    if type(capability) not in (ControllerPreflightRelay, BackendPreflightChild):
        raise HandshakeError('Original typed preflight capability is required')
    table = _CONTROLLERS if type(capability) is ControllerPreflightRelay else _CHILDREN
    value = table.get(capability)
    if (value is None or value['pid'] != os.getpid() or value['thread'] is not threading.current_thread()
            or value.get('failed') or capability not in _ACTIVE):
        raise HandshakeError('Original preflight process/thread capability is unavailable')
    return value


def _canonical(value):
    import json
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def _sha(value): return hashlib.sha256(_canonical(value)).hexdigest()


def _retain(capability):
    table = _CONTROLLERS if type(capability) is ControllerPreflightRelay else _CHILDREN
    state = table.get(capability)
    if state is not None:
        state['failed'] = True
        _UNRESOLVED.add(capability)
        if type(capability) is BackendPreflightChild:
            state['ticket'].retain_unconfirmed()


def _request(value, proof):
    names = {'schema_version', 'kind', 'action', 'nonce', 'epoch', 'binding_sha256', 'backend_claim_sha256', 'request_id', 'payload'}
    if (type(value) is not dict or set(value) != names or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] != 'backend_preflight_request' or value['action'] not in ('reserve', 'bind', 'finish')
            or value['nonce'] != proof['nonce'] or value['epoch'] != proof['epoch']
            or value['binding_sha256'] != proof['binding_sha256'] or value['backend_claim_sha256'] != _sha(proof)
            or not _hex(value['request_id'], 32) or type(value['payload']) is not dict
            or len(_canonical(value)) > 16384):
        raise HandshakeError('Original preflight request binding differs')
    return copy.deepcopy(value)


def _reply(request, payload):
    return {'schema_version': 1, 'kind': 'controller_preflight_reply', 'nonce': request['nonce'],
        'epoch': request['epoch'], 'request_id': request['request_id'], 'action': request['action'],
        'request_sha256': _sha(request), 'payload': payload}


def process_controller_event(owner, event):
    """Original controller thread only; typed receive consumed before CAS."""
    from backend.engine import application_node_writer_authority as node
    authority = owner._node_backend_authority
    request, proof = node.consume_preflight_receive(authority, event)
    request = _request(request, proof)
    relays = getattr(owner, '_preflight_relays', None)
    if relays is None: owner._preflight_relays = relays = {}
    identifier = request['request_id']
    if request['action'] != 'reserve':
        capability = relays.get(identifier)
        if type(capability) is not ControllerPreflightRelay:
            raise HandshakeError('Original preflight child capability is unavailable')
        try:
            state = assert_original(capability)
            if (state['owner'] is not owner or state['epoch'] is not owner._writer_epoch
                    or state['node'] is not authority or state['proof'] != proof):
                raise HandshakeError('Original controller relay binding changed')
            _deadline(state)
            registration = state['registration']
            pin = {'writer_id': registration.writer_id, 'registration_sha256': registration.registration_sha256}
            payload = request['payload']
            names = {'registration', 'child', 'plan_sha256'}
            if request['action'] == 'finish': names |= {'returncode', 'cleanup_confirmed'}
            if (set(payload) != names or payload['registration'] != pin
                    or payload['plan_sha256'] != _sha(state['plan'])):
                raise HandshakeError('Original preflight child publication pin differs')
            if request['action'] == 'bind':
                if state['phase'] != 'reserved': raise HandshakeError('Original preflight child cannot rebind')
                _live_child(payload['child'], state['plan'], proof['process'])
                state.update(phase='binding', child=copy.deepcopy(payload['child']))
                owner._writer_epoch.bind_authenticated_preflight_child(registration.writer_id, capability,
                    expected_registry_sha256=owner._writer_epoch.snapshot()['registry_sha256'])
                state['phase'] = 'active'; status = 'bound'
            else:
                if (state['phase'] != 'active' or payload['child'] != state['child']
                        or type(payload['returncode']) is not int or payload['returncode'] != 0
                        or payload['cleanup_confirmed'] is not True):
                    raise HandshakeError('Original preflight cleanup is incomplete or differs')
                state['phase'] = 'finishing'
                owner._writer_epoch.observe_authenticated_preflight_child_exit(registration.writer_id, capability,
                    expected_registry_sha256=owner._writer_epoch.snapshot()['registry_sha256'])
                state['phase'] = 'finished'; status = 'direct_exited'
            node._fresh(authority); _deadline(state)
            if status == 'direct_exited': _ACTIVE.remove(capability)
            return _reply(request, {'status': status})
        except BaseException:
            _retain(capability)
            raise
    if identifier in relays: raise HandshakeError('Original preflight reservation is replayed or unresolved')
    plan = validate_plan(request['payload'], request_id=identifier)
    epoch = owner._writer_epoch; epoch._original()
    capability = _new(ControllerPreflightRelay)
    _CONTROLLERS[capability] = {'pid': os.getpid(), 'thread': threading.current_thread(), 'owner': owner,
        'epoch': epoch, 'node': authority, 'proof': proof, 'request_id': identifier, 'plan': plan,
        'phase': 'creating', 'registration': None, 'failed': False}
    relays[identifier] = capability; _ACTIVE.add(capability)
    try:
        registration = epoch.enroll_authenticated_preflight_child(capability,
            expected_registry_sha256=epoch.snapshot()['registry_sha256'])
        state = assert_original(capability); state['registration'] = registration; state['phase'] = 'reserved'
        node._fresh(authority)
        _deadline(state)
        return _reply(request, {'writer_id': registration.writer_id, 'registration_sha256': registration.registration_sha256})
    except BaseException:
        _retain(capability)
        raise


def _deadline(state):
    from backend.engine import application_node_writer_authority as node
    original = node._state(state['node'])
    limit = state['plan']['deadline_monotonic']
    if original['deadline'] is not None: limit = min(limit, original['deadline'])
    if time.monotonic() >= limit: raise HandshakeError('Original preflight publication deadline expired')
    return limit


def _core_publication_deadline(capability, epoch, *, phase=None):
    state=assert_original(capability)
    if (type(capability) is not ControllerPreflightRelay or state['epoch'] is not epoch
            or state['owner']._writer_epoch is not epoch
            or state['phase'] not in ('creating','binding','finishing')
            or phase is not None and state['phase'] != phase):
        raise HandshakeError('Original preflight publication capability differs')
    from backend.engine import application_node_writer_authority as node
    _deadline(state);node._fresh(state['node']);_deadline(state)


def _live_child(value, plan, backend):
    from backend.engine import application_launch_lease as lease
    import psutil
    if (type(value) is not dict or set(value) != {'pid', 'created_at', 'command_sha256'}
            or type(value['pid']) is not int or value['pid'] <= 0
            or value != lease._identity(value['pid'])):
        raise HandshakeError('Original preflight child birth or command differs')
    process = psutil.Process(value['pid'])
    if (process.ppid() != backend['pid'] or process.cmdline() != plan['command']
            or process.cwd() != plan['workdir'] or os.getsid(value['pid']) != value['pid']
            or os.getpgid(value['pid']) != value['pid']):
        raise HandshakeError('Original preflight child parent/command/session differs')


def _core_child_binding(capability, epoch, writer_id):
    state = assert_original(capability)
    if (state['epoch'] is not epoch or state['registration'].writer_id != writer_id
            or state['phase'] != 'binding'):
        raise HandshakeError('Original preflight child binding capability differs')
    _deadline(state)
    _live_child(state['child'], state['plan'], state['proof']['process'])
    return copy.deepcopy(state['child']), state['registration'].registration_sha256


def _core_child_exit(capability, epoch, writer_id):
    state = assert_original(capability)
    if (state['epoch'] is not epoch or state['registration'].writer_id != writer_id
            or state['phase'] != 'finishing'):
        raise HandshakeError('Original preflight child cleanup capability differs')
    # Exit data came only from the consumed original authenticated backend event.
    # Do not reopen a PID after exit, turn a receipt into authority, or retry CAS.
    _deadline(state)
    return copy.deepcopy(state['child'])


def _hex(value, count=64):
    return type(value) is str and len(value) == count and all(c in '0123456789abcdef' for c in value)


def validate_plan(value, *, request_id):
    names = {'task', 'device', 'stages', 'workdir', 'source_sha256', 'budget_ms', 'deadline_monotonic', 'command'}
    from backend.engine.worker_preflight import PREFLIGHT_TASKS, PREFLIGHT_STAGES
    if (type(value) is not dict or set(value) != names or not _hex(request_id, 32)
            or type(value['task']) is not str or value['task'] not in PREFLIGHT_TASKS
            or value['device'] not in ('cpu', 'cuda', 'mps') or type(value['device']) is not str
            or type(value['stages']) is not list or not value['stages'] or len(value['stages']) > 4
            or any(type(s) is not str or s not in PREFLIGHT_STAGES for s in value['stages'])
            or len(set(value['stages'])) != len(value['stages'])
            or type(value['budget_ms']) is not int or not 0 < value['budget_ms'] <= 900000
            or type(value['deadline_monotonic']) not in (float, int) or not math.isfinite(value['deadline_monotonic'])
            or not 0 < value['deadline_monotonic']-time.monotonic() <= value['budget_ms']/1000+.05
            or type(value['workdir']) is not str or not Path(value['workdir']).is_absolute()
            or str(Path(value['workdir'])) != value['workdir'] or '..' in Path(value['workdir']).parts
            or any(p.is_symlink() for p in [Path(value['workdir']), *Path(value['workdir']).parents])):
        raise HandshakeError('Fixed preflight plan differs or its original budget expired')
    source = Path(__file__).with_name('worker_preflight.py')
    if value['source_sha256'] != hashlib.sha256(source.read_bytes()).hexdigest():
        raise HandshakeError('Original preflight source differs')
    command = value['command']
    if (type(command) is not list or len(command) != 18 or any(type(s) is not str for s in command)
            or not command[15].isdecimal() or not 3 <= int(command[15]) <= 8192):
        raise HandshakeError('Fixed preflight command differs')
    expected = [sys.executable, '-m', 'backend.engine.worker_preflight', '--task', value['task'], '--device', value['device'],
        '--stages', ','.join(value['stages']), '--workdir', value['workdir'], '--exit-with-parent', '--deadline',
        f"{value['budget_ms']/1000+30:.0f}", '--writer-gate-fd', command[15], '--writer-request', request_id]
    if command != expected: raise HandshakeError('Fixed preflight command differs')
    if len(_canonical(value)) > 8192: raise HandshakeError('Preflight plan exceeds its bound')
    return copy.deepcopy(value)


def _endpoint(channel):
    try:
        if (type(channel) is not socket.socket or channel.family != socket.AF_UNIX
                or channel.type != socket.SOCK_STREAM or channel.get_inheritable()):
            raise HandshakeError('Original preflight private channel differs')
        info = os.fstat(channel.fileno())
    except OSError as exc:
        raise HandshakeError('Original preflight private channel is unavailable') from exc
    if not stat.S_ISSOCK(info.st_mode): raise HandshakeError('Original preflight endpoint differs')
    # Darwin clears socket permission bits when the original peer closes.
    # File type, held descriptor identity and owner remain the identity pin.
    return channel.fileno(), info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_uid


class BackendRelayQueue:
    """In-memory exchange only. Its single service reader owns all socket IO."""
    def __init__(self, channel):
        self._channel = channel; self._endpoint = _endpoint(channel); self._pid = os.getpid()
        self._reader = None; self._active = None; self._seen = set(); self._condition = threading.Condition()
        self.unresolved = False; self._closed_deadline = None

    def _fresh(self, channel=None, *, reader=False):
        try: endpoint = _endpoint(self._channel)
        except BaseException:
            self.unresolved = True
            raise
        if (self._pid != os.getpid() or self.unresolved or channel is not None and channel is not self._channel
                or endpoint != self._endpoint
                or reader and self._reader is not threading.current_thread()):
            raise HandshakeError('Original preflight exchange reader/channel differs or is unresolved')

    def claim_reader(self):
        with self._condition:
            self._fresh()
            if self._reader is not None: raise HandshakeError('Original preflight channel already has its reader')
            self._reader = threading.current_thread()

    def enqueue(self, frame, *, deadline):
        with self._condition:
            self._fresh()
            if self._closed_deadline is not None:
                if frame.get('action') != 'finish': raise HandshakeError('Original preflight admission is closed')
                deadline = min(deadline, self._closed_deadline)
            key = (frame.get('request_id'), frame.get('action'))
            if (self._active is not None or key in self._seen or len(self._seen) >= 384
                    or not _hex(frame.get('request_id'), 32) or not math.isfinite(deadline) or deadline <= time.monotonic()):
                raise HandshakeError('Preflight exchange is active, replayed or late')
            token = _new(_Exchange)
            _EXCHANGES[token] = {'queue': self, 'frame': copy.deepcopy(frame), 'deadline': deadline, 'owner': threading.current_thread(),
                'taken': False, 'done': False, 'used': False, 'answer': None, 'error': None}
            self._seen.add(key); self._active = token; self._condition.notify_all()
            return token

    def close_admission(self, deadline):
        with self._condition:
            self._fresh(reader=True)
            if self._closed_deadline is not None or type(deadline) not in (float, int) or not math.isfinite(deadline):
                raise HandshakeError('Original preflight queue drain is replayed or invalid')
            self._closed_deadline = deadline
            if self._active is not None:
                state = self._token(self._active)
                state['deadline'] = min(state['deadline'], deadline)
            self._condition.notify_all()

    def _token(self, token):
        state = _EXCHANGES.get(token) if type(token) is _Exchange else None
        if state is None or state['queue'] is not self:
            raise HandshakeError('Original preflight exchange capability differs')
        return state

    def outgoing(self, token):
        with self._condition:
            self._fresh(reader=True); state = self._token(token)
            if token is not self._active or not state['taken'] or state['done'] or state['used']:
                raise HandshakeError('Original preflight outgoing exchange differs')
            return copy.deepcopy(state['frame']), state['deadline']

    def take(self, channel):
        with self._condition:
            self._fresh(channel, reader=True)
            token = self._active
            if token is None: return None
            state = self._token(token)
            if state['taken']: return None
            state['taken'] = True
            return token

    def complete(self, token, answer):
        with self._condition:
            self._fresh(reader=True)
            state = self._token(token)
            if token is not self._active or not state['taken'] or state['done'] or state['used']:
                raise HandshakeError('Original preflight reply is foreign or replayed')
            if time.monotonic() >= state['deadline']:
                self.fail(token, HandshakeError('Original preflight exchange deadline expired'))
                raise HandshakeError('Original preflight exchange deadline expired')
            state['answer'] = copy.deepcopy(answer); state['done'] = True; self._condition.notify_all()

    def fail(self, token, error):
        with self._condition:
            state = self._token(token)
            if token is not self._active or state['done'] or state['used']:
                raise HandshakeError('Original preflight failure is foreign or replayed')
            self.unresolved = True; state['error'] = error; self._condition.notify_all()

    def abandon(self, error):
        with self._condition:
            self.unresolved = True
            if self._active is not None:
                self._token(self._active)['error'] = error
            self._condition.notify_all()

    def result(self, token):
        with self._condition:
            state = self._token(token)
            if token is not self._active or state['owner'] is not threading.current_thread() or state['used']:
                raise HandshakeError('Original preflight exchange belongs to another thread or was consumed')
            self._fresh()
            while not state['done'] and not state['error']:
                remaining = state['deadline']-time.monotonic()
                if remaining <= 0:
                    self.fail(token, HandshakeError('Original preflight exchange deadline expired'))
                    break
                self._condition.wait(remaining)
            if state['error']: raise HandshakeError('Original preflight exchange is unresolved') from state['error']
            self._fresh()
            if time.monotonic() >= state['deadline']:
                self.abandon(HandshakeError('Original reply consumption deadline expired'))
                raise HandshakeError('Original reply consumption deadline expired')
            state['used'] = True; self._active = None
            return copy.deepcopy(state['answer'])


def _mint_backend_holder(ticket):
    from backend.engine import application_launch_handshake as handshake
    if (type(ticket) is not handshake._PreflightWriterTicket or ticket not in handshake._PREFLIGHT_TICKETS
            or ticket._phase != 'active' or ticket._thread is not threading.current_thread()
            or hasattr(ticket, '_relay_child')):
        raise HandshakeError('Original backend preflight ticket is missing or already used')
    ticket._original(); cache = handshake._CACHE
    if cache is None or cache is not ticket._cache or not cache['ready']:
        raise HandshakeError('Original authenticated backend child cache is unavailable')
    anchor = cache['writer_private_fd']; info = os.fstat(anchor)
    if (info.st_dev, info.st_ino) != cache['writer_fd_identity']:
        raise HandshakeError('Original cached backend OFD changed')
    cap = _new(BackendPreflightChild)
    _CHILDREN[cap] = {'pid': os.getpid(), 'thread': threading.current_thread(), 'ticket': ticket,
        'cache': cache, 'proof': copy.deepcopy(cache['proof']), 'endpoint': _endpoint(cache['socket']),
        'phase': 'creating', 'failed': False, 'guard': None, 'close_guard': None,
        'pipe': None, 'child': None, 'registration': None, 'cleanup_confirmed': False}
    _ACTIVE.add(cap); ticket._relay_child = cap
    return cap


def _backend_original(capability):
    from backend.engine import application_launch_handshake as handshake
    state = assert_original(capability)
    if type(capability) is not BackendPreflightChild: raise HandshakeError('Original backend child capability required')
    def original_deadline():
        if 'plan' not in state:return
        limit=state['plan']['deadline_monotonic']
        queue=state['cache'].get('preflight_queue')
        if type(queue) is BackendRelayQueue and queue._closed_deadline is not None:
            limit=min(limit,queue._closed_deadline)
        if time.monotonic()>=limit:raise HandshakeError('Original backend preflight producer deadline expired')
    original_deadline()
    ticket = state['ticket']; ticket._original(); cache = handshake._CACHE
    if (cache is not state['cache'] or ticket._cache is not cache or cache['proof'] != state['proof']
            or _endpoint(cache['socket']) != state['endpoint'] or ticket._thread is not threading.current_thread()):
        raise HandshakeError('Original backend child channel/cache changed')
    info = os.fstat(cache['writer_private_fd'])
    if (info.st_dev, info.st_ino) != cache['writer_fd_identity']:
        raise HandshakeError('Original cached backend OFD changed')
    original_deadline()
    return state


def _backend_exchange(capability, action, payload):
    state = _backend_original(capability); proof = state['proof']; plan = state['plan']
    if time.monotonic() >= plan['deadline_monotonic']: raise HandshakeError('Original preflight exchange deadline expired')
    queue = state['cache'].get('preflight_queue')
    if type(queue) is not BackendRelayQueue: raise HandshakeError('Original backend sole reader is unavailable')
    request = {'schema_version': 1, 'kind': 'backend_preflight_request', 'action': action,
        'nonce': proof['nonce'], 'epoch': proof['epoch'], 'binding_sha256': proof['binding_sha256'],
        'backend_claim_sha256': _sha(proof), 'request_id': state['request_id'], 'payload': payload}
    token = queue.enqueue(request, deadline=plan['deadline_monotonic'])
    answer = queue.result(token)
    names = {'schema_version', 'kind', 'nonce', 'epoch', 'request_id', 'action', 'request_sha256', 'payload'}
    if (type(answer) is not dict or set(answer) != names or answer != _reply(request, answer['payload'])):
        raise HandshakeError('Original controller preflight acknowledgement differs')
    _backend_original(capability)
    return answer['payload']


def reserve_backend_child(ticket, *, task, device, stages, workdir, limit, deadline):
    """Create-only, before Popen; no caller receipt/PID can capture a child."""
    from backend.engine import application_launch_handshake as handshake
    if handshake._CACHE is None: return None  # Ordinary unowned compatibility has no owned authority.
    if 'writer' not in handshake._CACHE['challenge']: return None  # Original legacy protocol3 remains uncovered.
    cap = _mint_backend_holder(ticket); state = assert_original(cap)
    try:
        import secrets
        state['request_id'] = identifier = secrets.token_hex(16)
        state['pipe'] = os.pipe()
        command = [sys.executable, '-m', 'backend.engine.worker_preflight', '--task', task, '--device', device,
            '--stages', ','.join(stages), '--workdir', str(workdir), '--exit-with-parent', '--deadline', f'{limit+30:.0f}',
            '--writer-gate-fd', str(state['pipe'][0]), '--writer-request', identifier]
        state['plan'] = validate_plan({'task': task, 'device': device, 'stages': list(stages), 'workdir': str(workdir),
            'source_sha256': hashlib.sha256(Path(__file__).with_name('worker_preflight.py').read_bytes()).hexdigest(),
            'budget_ms': int(limit*1000), 'deadline_monotonic': deadline, 'command': command}, request_id=identifier)
        registration = _backend_exchange(cap, 'reserve', state['plan'])
        if (type(registration) is not dict or set(registration) != {'writer_id', 'registration_sha256'}
                or not _hex(registration['writer_id'], 32) or not _hex(registration['registration_sha256'])):
            raise HandshakeError('Original child reservation acknowledgement differs')
        state['registration'] = registration
        from backend.engine.application_launch_quiescence import writer_guard
        guard = writer_guard(state['cache']['root'], state['proof']['nonce'], registration['writer_id'],
            expected_registration_sha256=registration['registration_sha256'])
        state['guard'] = guard
        handle = guard.__enter__(); state['guard_fd'] = handle.pass_fds[0]
        state['close_guard'] = lambda: guard.__exit__(None, None, None)
        info = os.fstat(state['guard_fd']); state['guard_identity'] = (info.st_dev, info.st_ino)
        _backend_original(cap)
        state['phase'] = 'reserved'
        return cap
    except BaseException:
        _retain(cap)
        raise


def child_launch(capability):
    try:
        state = _backend_original(capability)
        if state['phase'] != 'reserved': raise HandshakeError('Original preflight spawn capability was already consumed')
        state['phase'] = 'spawning'  # Before invoking Popen; no spawn retry on ambiguity.
        return list(state['plan']['command']), (state['guard_fd'], state['pipe'][0])
    except BaseException:
        _retain(capability)
        raise


def launch_backend_child(capability, *, log, environment, inherited):
    """Invoke exactly one original Popen here; public PID/handle adoption is absent."""
    import subprocess
    from backend.engine import worker_preflight as worker
    from backend.engine.process_isolation import session_isolation
    command, transport = child_launch(capability)
    state = assert_original(capability)
    try:
        _backend_original(capability)
        child = subprocess.Popen(command, cwd=state['plan']['workdir'], stdin=subprocess.PIPE,
            stdout=log, stderr=subprocess.STDOUT, env=environment, close_fds=True,
            pass_fds=tuple(inherited)+transport, **session_isolation())
        state['child'] = child  # Before any identity read or callback can fail.
        with worker._CHILDREN_LOCK: worker._CHILDREN.add(child)
        _capture_backend_child(capability, child)
        return child
    except BaseException:
        _retain(capability)
        # The original holder keeps this exact handle even when bootstrap fails.
        # The existing child stdin/deadline watchdog ends it without PID signals.
        child = state['child']
        if child is not None and child.stdin is not None:
            try: child.stdin.close()
            except BaseException: pass
        raise


def _capture_backend_child(capability, child):
    import subprocess
    from backend.engine import application_launch_lease as lease
    state = _backend_original(capability)
    if state['phase'] != 'spawning' or type(child) is not _ORIGINAL_POPEN_TYPE or state['child'] is not child:
        raise HandshakeError('Original exact backend Popen capture is unavailable')
    state['child'] = child
    try:
        if child.poll() is not None: raise HandshakeError('Original startup-gated child already exited')
        identity = lease._identity(child.pid); _live_child(identity, state['plan'], state['proof']['process'])
        state['child_identity'] = identity
        reply = _backend_exchange(capability, 'bind', {'registration': state['registration'], 'child': identity,
            'plan_sha256': _sha(state['plan'])})
        if reply != {'status': 'bound'}: raise HandshakeError('Original child binding acknowledgement differs')
        if child.poll() is not None: raise HandshakeError('Original startup-gated child exited before acknowledgement')
        _live_child(identity, state['plan'], state['proof']['process'])
        _backend_original(capability)
        gate = {'request_id': state['request_id'], 'parent_pid': os.getpid(), 'writer_fd': state['guard_fd'],
            'writer_identity': list(state['guard_identity']), 'deadline': state['plan']['deadline_monotonic']}
        raw = _canonical(gate)+b'\n'
        if os.write(state['pipe'][1], raw) != len(raw): raise HandshakeError('Original child startup gate write was partial')
        for fd in state['pipe']: os.close(fd)  # A failed close retains opaque custody; no probe/retry.
        state['pipe'] = None
        _backend_original(capability)
        state['phase'] = 'active'
    except BaseException:
        _retain(capability)
        raise


def record_backend_child_cleanup(capability, child, *, workspace_removed):
    state = _backend_original(capability)
    if (state['phase'] != 'active' or state['child'] is not child or child.poll() != 0
            or workspace_removed is not True):
        _retain(capability)
        raise HandshakeError('Original child handle/workspace cleanup is incomplete')
    state.update(phase='cleanup', returncode=0, cleanup_confirmed=True)


def retain_ticket_child(ticket):
    capability = getattr(ticket, '_relay_child', None)
    if capability is not None: _retain(capability)


def finish_backend_child(capability):
    state = assert_original(capability)
    try:
        _backend_original(capability)
        if state['phase'] != 'cleanup' or state.get('returncode') != 0 or state['cleanup_confirmed'] is not True:
            raise HandshakeError('Original preflight child cleanup is unavailable or already used')
        state['phase'] = 'closing'
        state['close_guard']()  # Never inspect/retry a possibly consumed original descriptor.
        answer = _backend_exchange(capability, 'finish', {'registration': state['registration'],
            'child': state['child_identity'], 'plan_sha256': _sha(state['plan']), 'returncode': 0, 'cleanup_confirmed': True})
        if answer != {'status': 'direct_exited'}: raise HandshakeError('Original child final acknowledgement differs')
        _backend_original(capability)
        state['phase'] = 'finished'; _ACTIVE.remove(capability)
    except BaseException:
        _retain(capability)
        raise


def finish_ticket_child(ticket):
    capability = getattr(ticket, '_relay_child', None)
    if capability is not None: finish_backend_child(capability)
