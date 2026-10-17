"""Bounded bootstrap controls; no native/owner/activation credit.

Reuse the existing publication fixture's explicit installation, journal, OS,
executable and lifespan-admission models. Its real socketpair, canonical
handshake validator and original per-lease flock remain real. A protocol-3
challenge omits the unrelated modeled CPU writer; no CPU/body/thread/child is
executed. Clocks and failed/late IO-readiness are disclosed boundary models.
Neither _validate nor its proof result is replaced with a successful stub.
"""
import copy
import errno
import hashlib
import os

import pytest

from backend.engine import application_launch_handshake as h
from backend.engine import application_launch_lease as lease
from backend.engine import migration_guard
from backend.engine.application_preflight_child_relay import BackendRelayQueue
from backend.tests.test_application_cpu_publication_admission import original
from backend.tests.test_application_transition_entry_deadline import held_original_mutex


@pytest.fixture
def bootstrap(original, monkeypatch):
    o = original
    o['row'].update(state='starting', protocol_version=3)
    challenge = copy.deepcopy(o['challenge']); challenge.pop('writer')
    o['values']['VISION_APPLICATION_BACKEND_FD'] = str(o['left'].fileno())
    # Transfer this fixture's sole original endpoint to the original transport
    # constructor, avoiding two socket objects owning the same descriptor.
    descriptor = os.fstat(o['left'].fileno())
    identity = (descriptor.st_dev, descriptor.st_ino, descriptor.st_mode)
    fd = o['left'].detach()
    channels = []; extras = []
    observations = {'root': [], 'read': [], 'validate': [], 'send': []}
    first = o['clock'][0]; expected_deadline = first + 210
    root_context = h._root_context; transport = h._transport
    read = h.read_frame; validate = h._validate; send = h.send_frame
    original_select = h.select.select

    def context():
        observations['root'].append(o['clock'][0])
        if len(observations['root']) == 1:
            o['clock'][0] += 2  # First validation consumes the original bound.
        return root_context()

    def endpoint(value):
        result = transport(value)
        channels.append(result)
        return result

    def receive(channel, seconds):
        if channel not in channels:
            return read(channel, seconds)
        observations['read'].append({'at': o['clock'][0], 'seconds': seconds})
        result = read(channel, seconds)
        o['clock'][0] += 3  # Complete original frame decoding consumes time too.
        return result

    def full_validate(*args, **kwargs):
        observations['validate'].append({'at': o['clock'][0],
                                         'deadline': kwargs.get('absolute_deadline')})
        return validate(*args, **kwargs)

    def transmit(channel, value, **kwargs):
        if channel not in channels:
            return send(channel, value, **kwargs)
        row = {'kind': value['kind'], 'deadline': kwargs.get('absolute_deadline'),
               'at': o['clock'][0], 'returned': False}
        observations['send'].append(row)
        result = send(channel, value, **kwargs)
        row['returned'] = True
        return result

    monkeypatch.setattr(h, '_CACHE', None)
    monkeypatch.setattr(h, '_root_context', context)
    monkeypatch.setattr(h, '_transport', endpoint)
    monkeypatch.setattr(h, 'read_frame', receive)
    monkeypatch.setattr(h, '_validate', full_validate)
    monkeypatch.setattr(h, 'send_frame', transmit)
    # Same explicit installation/lifespan model as the reused no-child fixture;
    # this is not evidence of actual application shared-lifespan admission.
    monkeypatch.setattr(migration_guard, 'shared_admitted', lambda root: root == o['root'])
    state = {'o': o, 'observations': observations, 'deadline': expected_deadline,
             'extras': extras, 'select': original_select, 'read': read}

    def start():
        send(o['right'], challenge)
        proof = h.early_backend_bootstrap()
        cache = h._CACHE
        assert cache['startup_deadline'] == expected_deadline
        assert cache['ready'] is False and cache['root'] == o['root']
        assert read(o['right'], .1) == proof
        assert cache['proof'] == proof and cache['challenge'] == challenge
        assert observations['root'][0] == first
        assert observations['read'] == [{'at': first + 2, 'seconds': 208}]
        assert observations['validate'] == [{'at': first + 5, 'deadline': expected_deadline}]
        assert observations['send'] == [{'kind': 'backend_claim', 'deadline': expected_deadline,
                                         'at': first + 5, 'returned': True}]
        state.update(cache=cache, channel=cache['socket'], proof=copy.deepcopy(proof))
        return state

    state['start'] = start
    try:
        yield state
    finally:
        for channel in channels + extras:
            channel.close()
        if not channels:
            try:
                current = os.fstat(fd)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
            else:
                assert (current.st_dev, current.st_ino, current.st_mode) == identity
                os.close(fd)  # Only the fixture's still-original unwrapped FD.


@pytest.mark.parametrize('boundary', ['cached_entry', 'ready_send_failed', 'ready_send_late'])
def test_cached_pre_ready_bootstrap_cannot_renew_first_startup_deadline(bootstrap, monkeypatch, boundary):
    b = bootstrap['start'](); o = b['o']; cache = b['cache']; deadline = b['deadline']
    assert h.early_backend_bootstrap() == b['proof']
    assert cache['startup_deadline'] == deadline
    assert all(row['deadline'] == deadline for row in b['observations']['validate'])
    original_proof = h._canonical(cache['proof']); original_challenge = h._canonical(cache['challenge'])
    if boundary == 'cached_entry':
        release = held_original_mutex(o)
        o['clock'][0] = deadline - .011
        try:
            with pytest.raises(h.HandshakeError, match='deadline expired'):
                h.early_backend_bootstrap()
        finally:
            release()
        assert o['sleeps'] and o['clock'][0] == deadline
        assert [row['kind'] for row in b['observations']['send']] == ['backend_claim']
    else:
        def io_ready(readers, writers, errors, timeout):
            if not readers and writers == [b['channel']]:
                if boundary == 'ready_send_failed':
                    return [], [], []  # Explicit failed IO-readiness model.
                ready = b['select'](readers, writers, errors, timeout)
                assert ready[1] == writers
                o['clock'][0] = deadline  # Real send returns at the original bound.
                return ready
            return b['select'](readers, writers, errors, timeout)
        monkeypatch.setattr(h.select, 'select', io_ready)
        with pytest.raises(h.HandshakeError, match='timeout|deadline'):
            h.backend_bootstrap_ready()
        assert b['observations']['send'][-1] == {
            'kind': 'backend_ready', 'deadline': deadline, 'at': 105.0, 'returned': False}
        if boundary == 'ready_send_late':
            # Late bytes are retained as late bytes, never called a READY pass.
            assert b['read'](o['right'], .1) == {**b['proof'], 'kind': 'backend_ready'}
        else:
            assert b['select']([o['right']], [], [], 0)[0] == []
    assert h._CACHE is cache and cache['ready'] is False
    assert cache['startup_deadline'] == deadline
    assert h._canonical(cache['proof']) == original_proof
    assert h._canonical(cache['challenge']) == original_challenge
    assert all(row['deadline'] == deadline for row in b['observations']['validate'])


@pytest.mark.parametrize('damage', ['cache', 'context', 'root', 'socket', 'socket_closed',
    'socket_fd_reused', 'queue', 'canonical_proof', 'canonical_challenge', 'ready',
    'deadline', 'replay', 'EOF'])
def test_cached_pre_ready_wait_refuses_original_cache_or_transport_drift(bootstrap, monkeypatch, damage):
    b = bootstrap['start'](); o = b['o']; cache = b['cache']; channel = b['channel']
    release = held_original_mutex(o); calls = []; checks = o['checks'].count('executable')

    def after_original_busy(seconds):
        assert 0 < seconds <= .005 and calls == []
        calls.append(seconds); o['clock'][0] += seconds
        if damage == 'cache': monkeypatch.setattr(h, '_CACHE', dict(cache))
        elif damage == 'context': monkeypatch.setattr(h, '_context', lambda: ('foreign',))
        elif damage == 'root': monkeypatch.setattr(h, '_root_context', lambda: (o['root'] / 'foreign', o['values']))
        elif damage == 'socket': cache['socket'] = o['right']
        elif damage == 'socket_closed': channel.close()
        elif damage == 'socket_fd_reused':
            import socket
            left, right = socket.socketpair(); b['extras'].extend([left, right])
            os.dup2(left.fileno(), channel.fileno(), inheritable=False)
        elif damage == 'queue': cache['preflight_queue'] = BackendRelayQueue(o['right'])
        elif damage == 'canonical_proof': cache['proof']['binding_sha256'] = 'f' * 64
        elif damage == 'canonical_challenge': cache['challenge']['challenge'] = 'e' * 64
        elif damage == 'ready': cache['ready'] = True
        elif damage == 'deadline': cache['startup_deadline'] += 1
        elif damage == 'replay': o['right'].sendall(h._canonical(cache['challenge']) + b'\n')
        elif damage == 'EOF':
            import socket
            o['right'].shutdown(socket.SHUT_WR)
        else: raise AssertionError(damage)
        release()

    monkeypatch.setattr(h.time, 'sleep', after_original_busy)
    try:
        with pytest.raises(h.HandshakeError):
            h.early_backend_bootstrap()
        assert calls == [.005]
        assert o['checks'].count('executable') == checks, 'Drift reached full owner body after failed entry'
        assert [row['kind'] for row in b['observations']['send']] == ['backend_claim']
    finally:
        release()


def test_ready_cache_reuse_outlives_startup_deadline_without_refreshing_it(bootstrap):
    b = bootstrap['start'](); o = b['o']; cache = b['cache']; deadline = b['deadline']
    ready = h.backend_bootstrap_ready()
    assert ready == {**b['proof'], 'kind': 'backend_ready'}
    assert b['read'](o['right'], .1) == ready and cache['ready'] is True
    queue = cache['preflight_queue']; context = cache['context']
    proof = h._canonical(cache['proof']); challenge = h._canonical(cache['challenge'])
    reads = list(b['observations']['read']); sends = copy.deepcopy(b['observations']['send'])
    before = len(b['observations']['validate']); checks = o['checks'].count('executable')
    o['clock'][0] = deadline + 1
    assert h.early_backend_bootstrap() == b['proof']
    assert h.backend_bootstrap_ready() == ready
    assert len(b['observations']['validate']) == before + 2
    assert all(row['deadline'] is None for row in b['observations']['validate'][before:])
    assert o['checks'].count('executable') == checks + 4  # Full before/after owner validation twice.
    assert h._CACHE is cache and cache['ready'] is True and cache['startup_deadline'] == deadline
    assert cache['socket'] is b['channel'] and cache['preflight_queue'] is queue
    assert cache['context'] is context and queue._reader is None
    assert h._canonical(cache['proof']) == proof and h._canonical(cache['challenge']) == challenge
    assert b['observations']['read'] == reads and b['observations']['send'] == sends
    release = held_original_mutex(o); sleeps = list(o['sleeps']); clock = o['clock'][0]
    try:
        with pytest.raises(h.HandshakeError) as refused:
            h.early_backend_bootstrap()
        assert isinstance(refused.value.__cause__, lease.LeaseTransitionBusy)
        assert o['sleeps'] == sleeps and o['clock'][0] == clock
    finally:
        release()


@pytest.mark.parametrize('position', ['before_READY', 'after_successful_READY_send'])
def test_ready_publication_preserves_next_phase_bytes_only_after_successful_send(bootstrap, monkeypatch, position):
    b = bootstrap['start'](); o = b['o']; cache = b['cache']
    # Canonical next-phase shape, not an admitted plan or dispatch capability.
    # This test never calls the service parser or grants execution acceptance.
    next_frame = {'schema_version': 1, 'kind': 'cpu_execution_request',
        'challenge': 'c' * 64, 'request_id': 'd' * 32, 'nonce': b['proof']['nonce'],
        'epoch': b['proof']['epoch'], 'binding_sha256': b['proof']['binding_sha256'],
        'backend_claim_sha256': hashlib.sha256(h._canonical(b['proof'])).hexdigest(),
        'workspace_id': 'e' * 32, 'project_id': 'f' * 32, 'plan_sha256': '1' * 64}
    raw = h._canonical(next_frame) + b'\n'
    if position == 'before_READY':
        o['right'].sendall(raw)
        with pytest.raises(h.HandshakeError, match='replay'):
            h.backend_bootstrap_ready()
        assert cache['ready'] is False
        assert [row['kind'] for row in b['observations']['send']] == ['backend_claim']
    else:
        transmit = h.send_frame; peer_received = []

        def next_phase_after_original_send(channel, value, **kwargs):
            result = transmit(channel, value, **kwargs)
            if channel is b['channel'] and value['kind'] == 'backend_ready':
                assert cache['ready'] is False
                actual_ready = b['read'](o['right'], .1)
                assert actual_ready == {**b['proof'], 'kind': 'backend_ready'}
                peer_received.append(actual_ready)
                o['right'].sendall(raw)  # Peer can respond before local ready=True.
            return result

        monkeypatch.setattr(h, 'send_frame', next_phase_after_original_send)
        assert h.backend_bootstrap_ready() == {**b['proof'], 'kind': 'backend_ready'}
        assert peer_received == [{**b['proof'], 'kind': 'backend_ready'}]
        assert cache['ready'] is True
        assert [row['kind'] for row in b['observations']['send']] == ['backend_claim', 'backend_ready']
    # Original startup checks only peek before READY; neither branch consumes
    # these bytes. The actual service validator remains responsible for them.
    assert b['read'](b['channel'], .1) == next_frame
    assert cache['preflight_queue']._reader is None
