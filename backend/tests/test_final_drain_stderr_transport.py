"""Owned synthetic OS transports; no native process or emission acceptance.

The full blocking pipe explicitly models the external FD2 sink while retaining
the real descriptor helper and original service. The OS controls below use real
pipes/socketpairs and never change original FD2. A temporary own-process SIGALRM
is only a negative blocking ceiling, never an application clock.
"""
import fcntl
import errno
import os
import signal
import socket
import sys
import time

import pytest

from backend.engine import application_launch_handshake as handshake
from backend.tests.test_application_final_drain_diagnostic import run_original_service
from backend.tests.test_application_terminal_drain_closing import original_terminal
from backend.tests.test_owned_health_diagnostic import own_app


def identity(fd):
    value = os.fstat(fd)
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, fcntl.fcntl(fd, fcntl.F_GETFL))


def unchanged_or_darwin_write_history(before, after):
    # Keep both raw snapshots. Only Darwin's documented monotone kernel history
    # bit may be added by a genuine write; all access/nonblock/other bits match.
    return before == after or (sys.platform == 'darwin' and before[:-1] == after[:-1]
        and not before[-1] & 0x10000 and after[-1] == before[-1] | 0x10000)


def fd_count():
    return len(os.listdir('/dev/fd'))


def full_pipe(*, nonblocking):
    reader, writer = os.pipe()
    os.set_blocking(writer, False)  # Only this newly owned fixture's setup.
    count = 0
    try:
        while True:
            try: count += os.write(writer, b'x'*4096)
            except BlockingIOError: break
            assert count <= 1024*1024
        while True:
            try: count += os.write(writer, b'x')
            except BlockingIOError: break
            assert count <= 1024*1024
        if not nonblocking: os.set_blocking(writer, True)
        return reader, writer, count
    except BaseException:
        os.close(reader); os.close(writer)
        raise


def full_socket(writer):
    count = 0
    stops = []
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    assert previous_timer == (0.0, 0.0) and writer.gettimeout() is None

    def ceiling(_signum, _frame):
        raise AssertionError('Owned socket fill setup exceeded its test ceiling')

    try:
        signal.signal(signal.SIGALRM, ceiling)
        signal.setitimer(signal.ITIMER_REAL, .25)
        writer.setblocking(False)  # NEW fixture setup only, before baseline.
        block_size = 512 if writer.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_DGRAM else 4096
        for block in (b'x'*block_size, b'x'):
            while True:
                try: sent = writer.send(block)
                except OSError as error:
                    if error.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS): raise
                    stops.append(errno.errorcode[error.errno]); break
                if sent == 0:
                    stops.append('ZERO_PROGRESS'); break
                count += sent
                assert count <= 1024*1024
        assert count > 0
        return count, stops
    finally:
        writer.setblocking(True)  # Restore original blocking fixture state.
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


@pytest.fixture
def actual_service(own_app, request):
    # Import/create the original factory before the modeled terminal cache.
    return request.getfixturevalue('original_terminal'), own_app


def test_full_blocking_pipe_never_delays_original_terminal_service(actual_service, monkeypatch, record_property):
    state, owned = actual_service
    _, client, _, _ = owned
    assert client.get('/api/project/current').status_code == 401
    before_fds = fd_count()
    reader, writer, filled = full_pipe(nonblocking=False)
    before = identity(writer)
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    assert previous_timer == (0.0, 0.0)
    alarm_fired = False

    actual_write = handshake._write_final_diagnostic_descriptor
    supplied_fds = []
    def owned_descriptor_boundary(fd, data):
        # Explicit external sink model: production still hardcodes inherited2.
        # This case proves original service ordering plus the real helper's
        # blocking-pipe refusal, not native FD2 emission/custody.
        supplied_fds.append(fd)
        assert fd == 2
        return actual_write(writer, data)

    def ceiling(_signum, _frame):
        nonlocal alarm_fired
        alarm_fired = True
        raise TimeoutError('Owned full-pipe test ceiling; not a product clock')

    try:
        with monkeypatch.context() as boundary:
            boundary.setattr(handshake, '_write_final_diagnostic_descriptor', owned_descriptor_boundary)
            signal.signal(signal.SIGALRM, ceiling)
            signal.setitimer(signal.ITIMER_REAL, .25)
            started = time.perf_counter()
            try:
                receipt = run_original_service(state, owned)
            finally:
                elapsed = time.perf_counter()-started
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_handler)
        assert receipt['status'] == 'managed_scopes_drained'
        assert supplied_fds == [2]
        assert identity(writer) == before
        assert os.read(reader, filled) == b'x'*filled
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        os.close(reader); os.close(writer)
    assert signal.getitimer(signal.ITIMER_REAL) == previous_timer
    assert signal.getsignal(signal.SIGALRM) is previous_handler
    after_fds = fd_count()
    record_property('fd_count_before', before_fds)
    record_property('fd_count_after', after_fds)
    record_property('deliberate_original_peer_close', state.peer.fileno() == -1)
    record_property('descriptor_identity_and_flags_before', str(before))
    record_property('descriptor_identity_and_flags_preserved', True)
    record_property('owned_pipe_filled_bytes', filled)
    record_property('test_alarm_fired', alarm_fired)
    record_property('call_elapsed_seconds', elapsed)
    assert state.peer.fileno() == -1 and after_fds == before_fds - 1
    assert not alarm_fired, 'Original diagnostic write blocked on an owned full pipe'
    assert elapsed < .25


@pytest.mark.parametrize('kind,full,nonblocking,want', [
    ('pipe', False, True, True),
    ('pipe', True, True, False),
    ('pipe', True, False, False),
    ('stream', False, False, False),
    ('stream', True, False, False),
    ('datagram', False, False, False),
    ('datagram', True, False, False),
    ('stream', False, True, True),
    ('stream', True, True, False),
    ('datagram', False, True, True),
    ('datagram', True, True, False),
    ('inet', False, False, False),
])
def test_one_nonblocking_descriptor_attempt_preserves_flags_identity_and_fd_count(
        kind, full, nonblocking, want, record_property):
    before_fds = fd_count(); payload = b'bounded diagnostic only\n'
    sockets = []; reader = writer = None; filled = 0
    try:
        if kind == 'pipe':
            if full: reader, writer, filled = full_pipe(nonblocking=nonblocking)
            else:
                reader, writer = os.pipe()
                os.set_blocking(writer, False)  # New fixture, before baseline.
        elif kind == 'inet':
            endpoint = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets = [endpoint]; writer = endpoint.fileno()
        else:
            receiver, sender = socket.socketpair(socket.AF_UNIX,
                socket.SOCK_STREAM if kind == 'stream' else socket.SOCK_DGRAM)
            sockets = [receiver, sender]; reader, writer = receiver.fileno(), sender.fileno()
            if full:
                filled, stops = full_socket(sender)
                record_property('owned_socket_fill_stops', str(stops))
            if nonblocking: sender.setblocking(False)  # Owned fixture before baseline.
        before = identity(writer)
        write = getattr(handshake, '_write_final_diagnostic_descriptor', None)
        assert callable(write), 'Missing nonblocking original diagnostic transport'
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        assert previous_timer == (0.0, 0.0)
        alarm_fired = False

        def ceiling(_signum, _frame):
            nonlocal alarm_fired
            alarm_fired = True
            raise AssertionError('A diagnostic descriptor operation blocked')

        try:
            signal.signal(signal.SIGALRM, ceiling)
            signal.setitimer(signal.ITIMER_REAL, .25)
            result = write(writer, payload)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
        after = identity(writer)
        assert unchanged_or_darwin_write_history(before, after)
        record_property('descriptor_identity_and_flags_before', str(before))
        record_property('descriptor_identity_and_flags_after', str(after))
        record_property('test_alarm_fired', alarm_fired)
        record_property('owned_filled_bytes', filled)
        record_property('write_result', result)
        assert result is want and not alarm_fired
        assert signal.getitimer(signal.ITIMER_REAL) == previous_timer
        if want:
            if kind == 'pipe': assert os.read(reader, len(payload)) == payload
            else: assert sockets[0].recv(len(payload)) == payload
        elif full and kind == 'pipe':
            assert os.read(reader, filled) == b'x'*filled
    finally:
        if sockets:
            for endpoint in sockets: endpoint.close()
        else:
            if reader is not None: os.close(reader)
            if writer is not None: os.close(writer)
    after_fds = fd_count()
    record_property('fd_count_before', before_fds)
    record_property('fd_count_after', after_fds)
    assert after_fds == before_fds


@pytest.mark.parametrize('timeout', [None, 0.0, .1])
def test_python_global_timeout_cannot_change_borrowed_nonblocking_socket_flags(timeout, record_property):
    before_fds = fd_count(); previous = socket.getdefaulttimeout()
    receiver, sender = socket.socketpair()
    sender.setblocking(False)  # Owned fixture setup before baseline.
    before = identity(sender.fileno()); payload = b'original flags remain\n'
    try:
        socket.setdefaulttimeout(timeout)  # Controlled Python global, not OFD flags.
        assert handshake._write_final_diagnostic_descriptor(sender.fileno(), payload) is True
        assert socket.getdefaulttimeout() == timeout
        assert unchanged_or_darwin_write_history(before, identity(sender.fileno()))
        assert receiver.recv(len(payload)) == payload
        record_property('descriptor_identity_and_flags_before', str(before))
        record_property('descriptor_identity_and_flags_after', str(identity(sender.fileno())))
    finally:
        socket.setdefaulttimeout(previous)
        receiver.close(); sender.close()
    after_fds = fd_count()
    record_property('fd_count_before', before_fds); record_property('fd_count_after', after_fds)
    assert after_fds == before_fds and socket.getdefaulttimeout() == previous


@pytest.mark.parametrize('kind', [socket.SOCK_STREAM, socket.SOCK_DGRAM])
def test_closed_anonymous_peer_is_unavailable_without_sigpipe_or_fd_changes(kind, record_property):
    before_fds = fd_count(); receiver, sender = socket.socketpair(socket.AF_UNIX, kind)
    sender.setblocking(False)  # Owned fixture setup before baseline.
    receiver.close(); before = identity(sender.fileno())
    handler = signal.getsignal(signal.SIGPIPE)
    try:
        assert handshake._write_final_diagnostic_descriptor(sender.fileno(), b'no peer\n') is False
        assert identity(sender.fileno()) == before
        assert signal.getsignal(signal.SIGPIPE) is handler
        record_property('descriptor_identity_and_flags_before', str(before))
        record_property('descriptor_identity_and_flags_after', str(identity(sender.fileno())))
    finally: sender.close()
    after_fds = fd_count()
    record_property('fd_count_before', before_fds); record_property('fd_count_after', after_fds)
    assert after_fds == before_fds


def test_partial_nonblocking_pipe_write_is_unavailable_without_retry(record_property):
    before_fds = fd_count(); reader, writer, filled = full_pipe(nonblocking=True)
    assert os.read(reader, 4096) == b'x'*4096
    before = identity(writer)
    try:
        result = handshake._write_final_diagnostic_descriptor(writer, b'p'*8192)
        assert result is False and identity(writer) == before
        returned = os.read(reader, filled)
        assert returned.startswith(b'x'*(filled-4096))
        sent = len(returned)-(filled-4096)
        assert 0 <= sent < 8192 and returned[filled-4096:] == b'p'*sent
        record_property('partial_bytes', sent)
        record_property('descriptor_identity_and_flags_before', str(before))
        record_property('descriptor_identity_and_flags_after', str(identity(writer)))
    finally: os.close(reader); os.close(writer)
    after_fds = fd_count()
    record_property('fd_count_before', before_fds); record_property('fd_count_after', after_fds)
    assert after_fds == before_fds
