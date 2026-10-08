"""Real private socket controls; no app, child, service, ASGI or model launch.

The freshness fixture explicitly models the controller registry, OS process
identity and an inert exact Popen instance. Its table entry is test-only and
does not prove original authentication, enrollment, actual process exit, or
writer/tree/lease release. Endpoint/read/event/deadline guards run unchanged.
"""
import copy
import os
import socket
import stat
import subprocess
import threading
from types import SimpleNamespace

import pytest

from backend.engine import application_node_writer_authority as node
from backend.engine.application_launch_handshake import HandshakeError, send_frame

pytestmark = pytest.mark.no_child


@pytest.fixture(autouse=True)
def forbid_child_creation(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('This module never launches an OS child')
    monkeypatch.setattr(subprocess.Popen, '__init__', denied)


@pytest.fixture
def channel():
    left, right = socket.socketpair()
    try:
        yield left, right
    finally:
        left.close()
        right.close()


@pytest.fixture
def modeled_bound_authority(channel, monkeypatch):
    """No authority promotion: only a modeled local state for guard controls."""
    left, right = channel
    main_identity = {'pid': 606060, 'created_at': 10.5, 'command_sha256': 'a'*64}
    backend_identity = {'pid': 606061, 'created_at': 11.5, 'command_sha256': 'b'*64}
    process = object.__new__(subprocess.Popen)
    process._child_created = False
    process.pid = main_identity['pid']
    process.returncode = None
    process.poll = lambda: process.returncode
    original_pid = os.getpid()
    original_thread = threading.current_thread()
    epoch_valid = [True]
    def original_epoch():
        if not epoch_valid[0]:
            raise HandshakeError('Modeled original epoch changed')
    epoch = SimpleNamespace(_original=original_epoch)
    binding = {'controlled_binding': 'original'}
    request = {'nonce': 'c'*64, 'epoch': 'd'*32, 'request_id': 'e'*32, 'challenge': 'f'*64}
    row = {'state': 'ready', 'protocol_version': 4, 'nonce': request['nonce'],
        'binding': copy.deepcopy(binding), 'process': copy.deepcopy(main_identity),
        'writer_drain': {'writer_id': 'w'*32, 'registration_sha256': '1'*64,
                         'phase': 'enrolled', 'request': request}}
    owner = SimpleNamespace(_process=process, _bootstrap_channel=left,
        _bootstrap_transport={'controlled_transport': 'original'}, _writer_epoch=epoch)
    owner._owned = lambda: row
    def original_binding(current):
        if current is not row or current['binding'] != binding:
            raise HandshakeError('Modeled original binding changed')
    def original_live(current, identity):
        if current is not row or current['process'] != main_identity or identity != main_identity:
            raise HandshakeError('Modeled original birth or command changed')
    owner._binding = original_binding
    owner._live = original_live
    authority = node._new(node.NodeBackendAuthority)
    owner._node_backend_authority = authority
    state = {'owner': owner, 'epoch': epoch, 'pid': original_pid, 'thread': original_thread,
        'writer_id': 'w'*32, 'registration_sha256': '1'*64, 'binding': copy.deepcopy(binding),
        'main': process, 'main_identity': copy.deepcopy(main_identity), 'channel': left,
        'endpoint': node._endpoint(left), 'transport': copy.deepcopy(owner._bootstrap_transport),
        'challenge': None, 'proof': {'process': backend_identity}, 'phase': 'bound',
        'deadline': None, 'exit_attempted': False, 'failed': False}
    node._AUTHORITIES[authority] = state
    monkeypatch.setattr(node, '_parent_pid', lambda pid: original_pid if pid == 606060 else 606060 if pid == 606061 else -1)
    monkeypatch.setattr(node, '_session_identity', lambda pid: (606060, 606060) if pid == 606060 else (-1, -1))
    monkeypatch.setattr(node, '_lease', lambda: SimpleNamespace(_identity=lambda pid: copy.deepcopy(backend_identity) if pid == 606061 else None))
    monkeypatch.setattr(node, '_now', lambda: 100.0)
    model = SimpleNamespace(authority=authority, state=state, owner=owner, row=row,
        process=process, channel=left, peer=right, epoch=epoch, epoch_valid=epoch_valid,
        original_pid=original_pid, original_thread=original_thread)
    try:
        assert node._fresh(authority) == (state, row)
        yield model
    finally:
        node._AUTHORITIES.pop(authority, None)


def test_original_private_peer_close_preserves_fixed_endpoint_identity(channel, record_property):
    left, right = channel
    fd = left.fileno()
    before_stat = os.fstat(fd)
    before = node._endpoint(left)
    right.close()
    after_stat = os.fstat(fd)
    after = node._endpoint(left)
    record_property('original_st_mode', before_stat.st_mode)
    record_property('peer_closed_st_mode', after_stat.st_mode)
    assert before_stat.st_dev == after_stat.st_dev
    assert before_stat.st_ino == after_stat.st_ino
    assert before_stat.st_uid == after_stat.st_uid
    assert stat.S_IFMT(before_stat.st_mode) == stat.S_IFMT(after_stat.st_mode) == stat.S_IFSOCK
    assert before == after == (fd, before_stat.st_dev, before_stat.st_ino, stat.S_IFSOCK, before_stat.st_uid)
    assert left.get_inheritable() is False
    assert left.recv(1) == b''


def test_real_peer_close_keeps_modeled_freshness_without_accepting_exit(modeled_bound_authority):
    m = modeled_bound_authority
    m.peer.close()
    assert node._fresh(m.authority) == (m.state, m.row)
    assert m.state['phase'] == 'bound' and not m.state['exit_attempted']
    assert m.row['writer_drain']['phase'] == 'enrolled'
    assert m.state['deadline'] is None and not m.state['failed']


def test_peer_close_eof_marks_interrupted_authority_without_receive_event(modeled_bound_authority):
    m = modeled_bound_authority
    original_events = set(node._EVENTS)
    m.state['deadline'] = 104.0
    m.peer.close()
    with pytest.raises(HandshakeError, match='EOF before complete frame'):
        node.receive_frame(m.authority, 0.2)
    assert set(node._EVENTS) == original_events
    assert m.state['failed'] and m.state['phase'] == 'bound'
    assert m.state['deadline'] == 104.0 and not m.state['exit_attempted']
    assert m.row['writer_drain']['phase'] == 'enrolled'
    with pytest.raises(HandshakeError, match='cannot be repaired'):
        node.receive_frame(m.authority, 0.2)


@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_uid', 'st_mode'])
def test_changed_fixed_endpoint_component_still_refuses(modeled_bound_authority, monkeypatch, field):
    m = modeled_bound_authority
    original = os.fstat
    fd = m.channel.fileno()
    def changed(current_fd):
        info = original(current_fd)
        if current_fd != fd:
            return info
        values = {key: getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid')}
        values[field] = stat.S_IFREG if field == 'st_mode' else values[field]+1
        return SimpleNamespace(**values)
    monkeypatch.setattr(node.os, 'fstat', changed)
    with pytest.raises(HandshakeError):
        node._fresh(m.authority)
    assert m.state['channel'] is m.channel and m.state['phase'] == 'bound'
    assert not m.state['exit_attempted']


def test_same_fd_foreign_socket_is_not_adopted(modeled_bound_authority):
    m = modeled_bound_authority
    other, peer = socket.socketpair()
    try:
        fd = m.channel.fileno()
        original_endpoint = m.state['endpoint']
        os.dup2(other.fileno(), fd, inheritable=False)
        assert m.channel.fileno() == fd
        with pytest.raises(HandshakeError):
            node._fresh(m.authority)
        assert m.state['endpoint'] == original_endpoint and m.state['channel'] is m.channel
    finally:
        other.close()
        peer.close()


def test_same_ofd_duplicate_socket_object_is_not_adopted(modeled_bound_authority):
    m = modeled_bound_authority
    duplicate = m.channel.dup()
    try:
        assert os.fstat(duplicate.fileno()).st_ino == os.fstat(m.channel.fileno()).st_ino
        m.owner._bootstrap_channel = duplicate
        with pytest.raises(HandshakeError):
            node._fresh(m.authority)
        assert m.state['channel'] is m.channel
    finally:
        duplicate.close()


@pytest.mark.parametrize('damage', ['foreign_channel', 'closed', 'inheritable', 'foreign_popen',
    'main_exited', 'main_pid', 'authority_pid', 'authority_thread', 'authority_owner',
    'epoch', 'epoch_unresolved', 'sticky_failed', 'transport', 'birth', 'command',
    'parent', 'session', 'group', 'registration'])
def test_original_object_process_thread_birth_and_binding_guards(modeled_bound_authority, monkeypatch, damage):
    m = modeled_bound_authority
    if damage == 'foreign_channel': m.owner._bootstrap_channel = m.peer
    elif damage == 'closed': m.channel.close()
    elif damage == 'inheritable': m.channel.set_inheritable(True)
    elif damage == 'foreign_popen': m.owner._process = object.__new__(subprocess.Popen); m.owner._process._child_created = False
    elif damage == 'main_exited': m.process.returncode = 0
    elif damage == 'main_pid': m.process.pid += 1
    elif damage == 'authority_pid': m.state['pid'] += 1
    elif damage == 'authority_thread': m.state['thread'] = object()
    elif damage == 'authority_owner': m.owner._node_backend_authority = None
    elif damage == 'epoch': m.owner._writer_epoch = object()
    elif damage == 'epoch_unresolved': m.epoch_valid[0] = False
    elif damage == 'sticky_failed': m.state['failed'] = True
    elif damage == 'transport': m.owner._bootstrap_transport = {'controlled_transport': 'changed'}
    elif damage == 'birth': m.row['process']['created_at'] += 1
    elif damage == 'command': m.row['process']['command_sha256'] = '0'*64
    elif damage == 'parent': monkeypatch.setattr(node, '_parent_pid', lambda pid: m.original_pid+1)
    elif damage == 'session': monkeypatch.setattr(node, '_session_identity', lambda pid: (606059, 606060))
    elif damage == 'group': monkeypatch.setattr(node, '_session_identity', lambda pid: (606060, 606059))
    elif damage == 'registration': m.row['writer_drain']['registration_sha256'] = '0'*64
    with pytest.raises((HandshakeError, OSError)):
        node._fresh(m.authority)
    assert m.state['phase'] == 'bound' and not m.state['exit_attempted']


@pytest.mark.parametrize('kind', ['tcp', 'datagram', 'subclass', 'raw_fd'])
def test_endpoint_requires_exact_noninheritable_unix_stream(channel, kind):
    class SocketSubclass(socket.socket):
        pass
    if kind == 'raw_fd':
        with pytest.raises(HandshakeError): node._endpoint(channel[0].fileno())
        return
    value = SocketSubclass() if kind == 'subclass' else socket.socket(
        socket.AF_INET if kind == 'tcp' else socket.AF_UNIX,
        socket.SOCK_STREAM if kind == 'tcp' else socket.SOCK_DGRAM)
    try:
        with pytest.raises(HandshakeError): node._endpoint(value)
    finally:
        value.close()


def test_original_receive_is_one_use_raw_frames_and_unregistered_events_refuse(modeled_bound_authority):
    m = modeled_bound_authority
    frame = {'schema_version': 1, 'kind': 'main_preflight_request', 'nonce': m.row['nonce'],
             'request': {'action': 'reserve', 'request_id': '2'*32}}
    send_frame(m.peer, frame)
    event = node.receive_frame(m.authority, 0.2)
    assert node.frame_value(m.authority, event) == frame
    for raw in (frame, object.__new__(node.NodeBackendEvent), m.process):
        with pytest.raises(HandshakeError): node.consume_preflight_receive(m.authority, raw)
    assert node._EVENTS[event]['used'] is False
    request, proof = node.consume_preflight_receive(m.authority, event)
    assert request == frame['request'] and proof == m.state['proof']
    assert node._EVENTS[event]['used'] is True
    with pytest.raises(HandshakeError): node.consume_preflight_receive(m.authority, event)
    assert not m.state['exit_attempted'] and m.state['phase'] == 'bound'


@pytest.mark.parametrize('action,now', [('reserve', 100.0), ('bind', 100.0), ('finish', 104.0)])
def test_original_drain_deadline_refuses_new_or_late_preflight_without_consumption(modeled_bound_authority, monkeypatch, action, now):
    m = modeled_bound_authority
    node.begin_drain(m.authority, {'budget_ms': 4000}, 100.0)
    send_frame(m.peer, {'schema_version': 1, 'kind': 'main_preflight_request', 'nonce': m.row['nonce'],
                        'request': {'action': action, 'request_id': '3'*32}})
    event = node.receive_frame(m.authority, 0.2)
    monkeypatch.setattr(node, '_now', lambda: now)
    with pytest.raises(HandshakeError): node.consume_preflight_receive(m.authority, event)
    assert node._EVENTS[event]['used'] is False and m.state['deadline'] == 104.0
    assert m.state['phase'] == 'bound' and not m.state['exit_attempted']


def test_finish_inside_original_drain_and_no_budget_renewal(modeled_bound_authority):
    m = modeled_bound_authority
    node.begin_drain(m.authority, {'budget_ms': 4000}, 100.0)
    with pytest.raises(HandshakeError): node.begin_drain(m.authority, {'budget_ms': 4000}, 101.0)
    assert m.state['deadline'] == 104.0
    send_frame(m.peer, {'schema_version': 1, 'kind': 'main_preflight_request', 'nonce': m.row['nonce'],
                        'request': {'action': 'finish', 'request_id': '4'*32}})
    event = node.receive_frame(m.authority, 0.2)
    assert node.consume_preflight_receive(m.authority, event)[0]['action'] == 'finish'
    assert node._EVENTS[event]['used'] and m.state['deadline'] == 104.0
    assert not m.state['exit_attempted']


@pytest.mark.parametrize('now_values', [[104.0], [100.0, 104.0]])
def test_exit_expired_before_or_after_validation_never_consumes_event(modeled_bound_authority, monkeypatch, now_values):
    m = modeled_bound_authority
    m.state['deadline'] = 104.0
    m.row['writer_drain']['phase'] = 'drained'
    request = m.row['writer_drain']['request']
    send_frame(m.peer, {'schema_version': 1, 'kind': 'main_backend_exit', **request,
        'backend_process': m.state['proof']['process'], 'returncode': 0, 'signal': None})
    event = node.receive_frame(m.authority, 0.2)
    clock = iter(now_values)
    monkeypatch.setattr(node, '_now', lambda: next(clock))
    with pytest.raises(HandshakeError): node._core_exit(m.authority, event, m.epoch, m.state['writer_id'])
    assert not node._EVENTS[event]['used'] and not m.state['exit_attempted']
    assert m.state['deadline'] == 104.0 and m.state['phase'] == 'bound'
