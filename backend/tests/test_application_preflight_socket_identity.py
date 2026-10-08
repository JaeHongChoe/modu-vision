"""Private socket identity controls; no app, child, ASGI or model execution."""
import os
import socket
import stat
import threading
import time
from types import SimpleNamespace

import pytest

from backend.engine import application_preflight_child_relay as relay
from backend.engine import application_launch_handshake as handshake

pytestmark = pytest.mark.no_child


@pytest.fixture
def channel():
    left, right = socket.socketpair()
    try:
        yield left, right
    finally:
        left.close()
        right.close()


def test_original_endpoint_keeps_fixed_type_when_private_peer_close_changes_permission_bits(channel):
    left, right = channel
    original_fd = left.fileno()
    before_stat = os.fstat(original_fd)
    before = relay._endpoint(left)
    right.close()
    after_stat = os.fstat(original_fd)
    after = relay._endpoint(left)
    assert before_stat.st_dev == after_stat.st_dev
    assert before_stat.st_ino == after_stat.st_ino
    assert before_stat.st_uid == after_stat.st_uid
    assert stat.S_IFMT(before_stat.st_mode) == stat.S_IFMT(after_stat.st_mode) == stat.S_IFSOCK
    assert before == after == (original_fd, before_stat.st_dev, before_stat.st_ino, stat.S_IFSOCK, before_stat.st_uid)
    assert left.get_inheritable() is False
    assert left.recv(1) == b''


def test_peer_close_fresh_identity_does_not_accept_eof_or_release_pending_exchange(channel):
    left, right = channel
    queue = relay.BackendRelayQueue(left)
    queue.claim_reader()
    deadline = time.monotonic() + 0.2
    frame = {'kind': 'backend_preflight_request', 'action': 'reserve', 'request_id': 'a'*32}
    token = queue.enqueue(frame, deadline=deadline)
    assert queue.take(left) is token
    state = relay._EXCHANGES[token]
    right.close()
    queue._fresh(left, reader=True)
    assert queue._active is token and state['deadline'] == deadline and state['used'] is False
    with pytest.raises(handshake.HandshakeError) as failed:
        handshake.read_frame(left, 0.2)
    queue.abandon(failed.value)
    with pytest.raises(handshake.HandshakeError):
        queue.result(token)
    assert queue.unresolved and queue._active is token
    assert state['used'] is False and state['deadline'] == deadline and state['answer'] is None
    with pytest.raises(handshake.HandshakeError):
        queue.enqueue({'request_id': 'b'*32}, deadline=deadline)


@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_uid', 'st_mode'])
def test_mutated_fixed_fstat_identity_refuses_and_retains_original_queue(channel, monkeypatch, field):
    left, _ = channel
    queue = relay.BackendRelayQueue(left)
    queue.claim_reader()
    original = os.fstat
    original_fd = left.fileno()
    def changed(fd):
        info = original(fd)
        if fd != original_fd:
            return info
        values = {name: getattr(info, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid')}
        values[field] = stat.S_IFREG if field == 'st_mode' else values[field] + 1
        return SimpleNamespace(**values)
    monkeypatch.setattr(relay.os, 'fstat', changed)
    with pytest.raises(handshake.HandshakeError):
        queue._fresh(left, reader=True)
    assert queue._channel is left and queue._reader is threading.current_thread()
    if field == 'st_mode':
        assert queue.unresolved


def test_same_fd_replaced_by_owned_foreign_socket_refuses(channel):
    left, _ = channel
    other, peer = socket.socketpair()
    try:
        queue = relay.BackendRelayQueue(left)
        queue.claim_reader()
        original_fd = left.fileno()
        original_tuple = queue._endpoint
        os.dup2(other.fileno(), original_fd, inheritable=False)
        assert left.fileno() == original_fd
        assert relay._endpoint(left) != original_tuple
        with pytest.raises(handshake.HandshakeError):
            queue._fresh(left, reader=True)
        assert queue._endpoint == original_tuple and queue._channel is left
    finally:
        other.close()
        peer.close()


@pytest.mark.parametrize('damage', ['foreign_object', 'original_closed', 'inheritable', 'wrong_pid', 'foreign_reader', 'sticky_unresolved'])
def test_original_channel_process_reader_and_sticky_guards_remain(channel, monkeypatch, damage):
    left, right = channel
    queue = relay.BackendRelayQueue(left)
    queue.claim_reader()
    argument = left
    if damage == 'foreign_object':
        argument = right
    elif damage == 'original_closed':
        left.close()
    elif damage == 'inheritable':
        left.set_inheritable(True)
    elif damage == 'wrong_pid':
        pid = os.getpid()
        monkeypatch.setattr(relay.os, 'getpid', lambda: pid + 1)
    elif damage == 'foreign_reader':
        monkeypatch.setattr(relay.threading, 'current_thread', lambda: object())
    elif damage == 'sticky_unresolved':
        queue.unresolved = True
    with pytest.raises(handshake.HandshakeError):
        queue._fresh(argument, reader=True)
    assert queue._channel is left


def test_duplicate_original_ofd_socket_object_is_not_adopted(channel):
    left, _ = channel
    duplicate = left.dup()
    try:
        queue = relay.BackendRelayQueue(left)
        queue.claim_reader()
        assert os.fstat(left.fileno()).st_ino == os.fstat(duplicate.fileno()).st_ino
        with pytest.raises(handshake.HandshakeError):
            queue._fresh(duplicate, reader=True)
        assert queue._channel is left
    finally:
        duplicate.close()


@pytest.mark.parametrize('kind', ['tcp', 'datagram', 'subclass', 'raw_fd'])
def test_endpoint_requires_exact_original_private_unix_stream_socket(kind, channel):
    class SocketSubclass(socket.socket):
        pass
    if kind == 'raw_fd':
        with pytest.raises(handshake.HandshakeError):
            relay._endpoint(channel[0].fileno())
        return
    value = SocketSubclass() if kind == 'subclass' else socket.socket(socket.AF_INET if kind == 'tcp' else socket.AF_UNIX, socket.SOCK_STREAM if kind == 'tcp' else socket.SOCK_DGRAM)
    try:
        with pytest.raises(handshake.HandshakeError):
            relay._endpoint(value)
    finally:
        value.close()
