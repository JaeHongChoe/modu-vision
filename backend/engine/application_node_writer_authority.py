"""Original controller's retained Node backend authority, never PID/exit JSON.

This adapter proves a single direct backend child observation from the original
private Node channel. It neither proves descendant exit nor releases any lease.
Its opaque in-memory objects cannot be reconstructed from durable evidence.
"""
import copy
import hmac
import os
import socket
import stat
import subprocess
import threading
import time
import weakref

import psutil

from backend.engine.application_launch_handshake import HandshakeError, read_frame, send_frame

_MINTS = set()
_CONTROLLER_MINTS = set()
_AUTHORITIES = weakref.WeakKeyDictionary()
_EVENTS = weakref.WeakKeyDictionary()


class _Opaque:
    __slots__ = ('__weakref__',)

    def __copy__(self):
        raise HandshakeError('Original Node capability cannot be copied')

    def __deepcopy__(self, memo):
        raise HandshakeError('Original Node capability cannot be copied')

    def __reduce_ex__(self, protocol):
        raise HandshakeError('Original Node capability cannot be serialized')


class NodeBackendAuthority(_Opaque):
    __slots__ = ()

    def __init__(self, *args, _mint=None, **kwargs):
        if args or kwargs or _mint not in _MINTS:
            raise HandshakeError('Original create-only Node backend authority is unavailable')


class NodeBackendEvent(_Opaque):
    __slots__ = ()

    def __init__(self, *args, _mint=None, **kwargs):
        if args or kwargs or _mint not in _MINTS:
            raise HandshakeError('Original retained-channel receive event is unavailable')


def _new(cls):
    mint = object(); _MINTS.add(mint)
    try: return cls(_mint=mint)
    finally: _MINTS.discard(mint)


def _lease():
    from backend.engine import application_launch_lease
    return application_launch_lease


def _update():
    from backend.engine import runtime_update
    return runtime_update


def _now():
    return time.monotonic()


def _parent_pid(pid):
    return psutil.Process(pid).ppid()


def _session_identity(pid):
    return os.getsid(pid), os.getpgid(pid)


def _state(authority):
    state = _AUTHORITIES.get(authority) if type(authority) is NodeBackendAuthority else None
    if (state is None or state['pid'] != os.getpid()
            or state['thread'] is not threading.current_thread()
            or state['owner']._node_backend_authority is not authority
            or state['owner']._writer_epoch is not state['epoch']):
        raise HandshakeError('Original Node controller process/thread capability differs')
    state['epoch']._original()
    if state['failed']:
        raise HandshakeError('Original Node observation was interrupted; it cannot be repaired')
    return state


def _mint_original_controller(owner, capability):
    # Only controller.run holds this short-lived token around the pre-spawn
    # create operation. A reopened supervisor/registry cannot mint an adapter.
    # The gate lives here rather than in the controller entrypoint: python -m
    # executes that entrypoint as __main__, while its imported copy is distinct.
    if capability not in _CONTROLLER_MINTS:
        raise HandshakeError('Original controller pre-spawn mint is unavailable')
    row = owner._owned(); owner._binding(row); epoch = owner._writer_epoch
    if (epoch is None or owner._process is not None or row['state'] != 'reserved'
            or row['spawn_attempted'] or row['protocol_version'] != 4
            or hasattr(owner, '_node_backend_authority')):
        raise HandshakeError('Original Node authority must precede the only main spawn')
    epoch._original(); snap = epoch.snapshot(); drain = row['writer_drain']
    registrations = [r for r in snap['registry']['writers'] if r['writer_id'] == drain['writer_id']]
    if (drain['phase'] != 'enrolled' or len(registrations) != 1
            or registrations[0]['role'] != 'backend' or registrations[0]['status'] != 'reserved'
            or registrations[0]['registration_sha256'] != drain['registration_sha256']
            or snap['registry_sha256'] != drain['registration_registry_sha256']):
        raise HandshakeError('Original pre-spawn Node registration differs')
    authority = _new(NodeBackendAuthority)
    _AUTHORITIES[authority] = {'owner': owner, 'epoch': epoch, 'pid': os.getpid(),
        'thread': threading.current_thread(), 'writer_id': drain['writer_id'],
        'registration_sha256': drain['registration_sha256'], 'binding': copy.deepcopy(row['binding']),
        'main': None, 'main_identity': None, 'channel': None, 'endpoint': None, 'transport': None,
        'challenge': None, 'proof': None, 'phase': 'created', 'deadline': None,
        'exit_attempted': False, 'failed': False}
    owner._node_backend_authority = authority
    return authority


def _endpoint(channel):
    if (type(channel) is not socket.socket or channel.family != socket.AF_UNIX
            or channel.type != socket.SOCK_STREAM or channel.get_inheritable()):
        raise HandshakeError('Original anonymous private Node socket differs')
    info = os.fstat(channel.fileno())
    if not stat.S_ISSOCK(info.st_mode):
        raise HandshakeError('Original Node endpoint is not a socket')
    return (channel.fileno(), info.st_dev, info.st_ino, info.st_mode, info.st_uid)


def capture_original_main(authority, row):
    state = _state(authority); owner = state['owner']
    if (state['phase'] != 'created' or type(owner._process) is not subprocess.Popen
            or owner._bootstrap_channel is None or row['state'] != 'starting'):
        raise HandshakeError('Only the original main start can capture Node transport')
    state.update(main=owner._process, main_identity=copy.deepcopy(row['process']),
        channel=owner._bootstrap_channel, endpoint=_endpoint(owner._bootstrap_channel),
        transport=copy.deepcopy(owner._bootstrap_transport), phase='captured')
    try: _fresh(authority)
    except BaseException:
        state['failed'] = True
        raise


def _fresh(authority):
    state = _state(authority); owner = state['owner']; process = state['main']
    if (process is None or owner._process is not process or type(process) is not subprocess.Popen
            or owner._bootstrap_channel is not state['channel']
            or _endpoint(state['channel']) != state['endpoint']
            or owner._bootstrap_transport != state['transport']):
        raise HandshakeError('Original retained main handle or private Node endpoint changed')
    row = owner._owned(); owner._binding(row); owner._live(row, state['main_identity'])
    if (row['state'] not in {'starting', 'ready'} or row['protocol_version'] != 4
            or row['binding'] != state['binding'] or process.poll() is not None
            or process.pid != state['main_identity']['pid']
            or _parent_pid(process.pid) != state['pid']
            or _session_identity(process.pid) != (process.pid, process.pid)):
        raise HandshakeError('Original live main birth/command/parent/session binding differs')
    drain = row.get('writer_drain')
    if (drain is None or drain['writer_id'] != state['writer_id']
            or drain['registration_sha256'] != state['registration_sha256']):
        raise HandshakeError('Original Node writer registration changed')
    return state, row


def send_authentication_challenge(authority, frame):
    state, row = _fresh(authority)
    if (state['phase'] != 'captured' or state['challenge'] is not None
            or frame.get('kind') != 'main_challenge' or frame.get('nonce') != row['nonce']
            or frame.get('binding') != row['binding'] or frame.get('process') != row['process']
            or frame.get('transport') != state['transport'] or not _update()._hex(frame.get('challenge'))):
        raise HandshakeError('Original Node authentication challenge differs or replayed')
    state['challenge'] = frame['challenge']
    send_frame(state['channel'], frame)
    _fresh(authority)


def receive_frame(authority, timeout):
    state, _ = _fresh(authority)
    try:
        value = read_frame(state['channel'], timeout)
        _fresh(authority)
        event = _new(NodeBackendEvent)
        _EVENTS[event] = {'authority': authority, 'frame': value, 'used': False}
        return event
    except BaseException:
        # Lost endpoint/partial frame/dead original main is never reopened or
        # retried as a fresh capability by this adapter.
        state['failed'] = True
        raise


def _event(authority, event):
    _fresh(authority)
    value = _EVENTS.get(event) if type(event) is NodeBackendEvent else None
    if value is None or value['authority'] is not authority or value['used']:
        raise HandshakeError('Original one-use Node receive event differs or replayed')
    return value


def frame_value(authority, event):
    return copy.deepcopy(_event(authority, event)['frame'])


def consume_preflight_receive(authority, event):
    """One original channel event; raw request dictionaries never mint a relay."""
    state, row = _fresh(authority); received = _event(authority, event)
    if state['phase'] != 'bound' or state['proof'] is None:
        raise HandshakeError('Original preflight relay is unavailable')
    frame = received['frame']
    if (type(frame) is not dict or set(frame) != {'schema_version', 'kind', 'nonce', 'request'}
            or type(frame['schema_version']) is not int or frame['schema_version'] != 1
            or frame['kind'] != 'main_preflight_request' or frame['nonce'] != row['nonce']):
        raise HandshakeError('Original preflight forwarding event differs')
    if (state['deadline'] is not None and (frame['request'].get('action') != 'finish'
            or _now() >= state['deadline'])):
        raise HandshakeError('Original preflight admission closed or drain deadline expired')
    backend = state['proof']['process']
    if _lease()._identity(backend['pid']) != backend or _parent_pid(backend['pid']) != state['main'].pid:
        raise HandshakeError('Original preflight backend birth/command/parent differs')
    _fresh(authority)
    received['used'] = True  # Consume before any enrollment/publication.
    return copy.deepcopy(frame['request']), copy.deepcopy(state['proof'])


def seal_authentication(authority, event):
    from backend.engine import application_launch_controller as controller
    state, row = _fresh(authority); received = _event(authority, event)
    if state['phase'] != 'captured' or row['state'] != 'ready':
        raise HandshakeError('Original Node authentication cannot be rebound')
    frame = received['frame']
    if (set(frame) != {'schema_version', 'kind', 'challenge', 'nonce', 'claim_b64', 'ready_b64'}
            or type(frame['schema_version']) is not int or frame['schema_version'] != 1
            or frame['kind'] != 'backend_proof' or frame['nonce'] != row['nonce']
            or not _update()._hex(frame['challenge'])
            or not hmac.compare_digest(frame['challenge'], state['challenge'])):
        raise HandshakeError('Original private Node authentication event differs')
    first = controller._proof(frame['claim_b64']); ready = controller._proof(frame['ready_b64'])
    if (first['kind'] != 'backend_claim' or ready['kind'] != 'backend_ready'
            or {**first, 'kind': 'backend_ready'} != ready or first['nonce'] != row['nonce']
            or first['binding_sha256'] != _update()._sha(_update()._canonical(state['binding']))
            or _lease()._identity(first['process']['pid']) != first['process']
            or _parent_pid(first['process']['pid']) != state['main'].pid):
        raise HandshakeError('Original Node backend identity or parent differs')
    controller._backend_artifact(state['owner'].root, state['binding'], first['executable'],
        first['executable_sha256'], first['build_identity_sha256'], first['frozen'], first['process']['pid'])
    received['used'] = True; state['proof'] = first; state['phase'] = 'authenticated'
    try:
        state['epoch'].bind_authenticated_node_backend(state['writer_id'], authority,
            expected_registry_sha256=state['epoch'].snapshot()['registry_sha256'])
        state['phase'] = 'bound'
    except BaseException:
        state['failed'] = True
        raise


def _core_binding(authority, epoch, writer_id):
    state, _ = _fresh(authority)
    if (state['epoch'] is not epoch or state['writer_id'] != writer_id
            or state['phase'] != 'authenticated' or state['proof'] is None):
        raise HandshakeError('Original authenticated Node backend gateway differs')
    proof = state['proof']; process = proof['process']
    if _lease()._identity(process['pid']) != process or _parent_pid(process['pid']) != state['main'].pid:
        raise HandshakeError('Original authenticated Node backend changed before binding')
    _fresh(authority)
    return copy.deepcopy(process), state['registration_sha256']


def begin_drain(authority, frame, started):
    state, row = _fresh(authority)
    if (state['phase'] != 'bound' or state['deadline'] is not None
            or row['writer_drain']['phase'] != 'enrolled'
            or type(frame['budget_ms']) is not int or not 0 < frame['budget_ms'] <= 4000):
        raise HandshakeError('Original Node drain cannot restart or acquire a new budget')
    state['deadline'] = started + frame['budget_ms']/1000
    if _now() >= state['deadline']:
        raise HandshakeError('Original Node shutdown budget expired')


def _core_exit(authority, event, epoch, writer_id):
    from backend.engine.application_launch_handshake import validate_backend_exit
    state, row = _fresh(authority); received = _event(authority, event)
    if (state['epoch'] is not epoch or state['writer_id'] != writer_id or state['phase'] != 'bound'
            or state['exit_attempted'] or state['deadline'] is None or _now() >= state['deadline']
            or row['writer_drain']['phase'] != 'drained'):
        raise HandshakeError('Original Node exit lacks its live admitted drain and budget')
    validate_backend_exit(received['frame'], row['writer_drain']['request'], state['proof']['process'])
    _fresh(authority)
    if _now() >= state['deadline']:
        raise HandshakeError('Original Node shutdown budget expired before exit publication')
    # Consume before publication. A crash/partial CAS never allows a new event
    # or caller JSON to repair the interrupted original observation.
    received['used'] = True; state['exit_attempted'] = True
    return copy.deepcopy(state['proof']['process'])


def finish_exit_publication(authority):
    state, _ = _fresh(authority)
    if (not state['exit_attempted'] or state['phase'] != 'bound'
            or state['deadline'] is None or _now() >= state['deadline']):
        raise HandshakeError('Original Node exit publication is late or replayed')
    state['phase'] = 'exited'


def assert_exit_acknowledgement(authority):
    state, row = _fresh(authority)
    if (state['phase'] != 'exited' or row['writer_drain']['phase'] != 'backend_exited'
            or _now() >= state['deadline']):
        raise HandshakeError('Original Node exit acknowledgement is late or uncommitted')
