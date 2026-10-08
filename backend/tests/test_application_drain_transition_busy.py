"""A transient original lease read must not destroy a bounded drain receipt."""
import base64
from contextlib import contextmanager
import errno
import hashlib
import os
from types import SimpleNamespace

import pytest

from backend.engine import application_launch_controller as controller
from backend.engine import application_launch_handshake as handshake
from backend.engine import application_launch_lease as lease
from backend.tests.test_application_launch_lease import controlled_canary_publication


class Clock:
    now = 10.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert 0 < seconds <= .02
        self.now += seconds


@pytest.fixture
def drain(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(controller.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(controller.time, 'sleep', clock.sleep)
    nonce, epoch = '1'*32, '2'*32
    process = {'pid': 321, 'created_at': 1.0, 'command_sha256': '3'*64}
    proof = {'nonce': nonce, 'epoch': epoch, 'binding_sha256': '4'*64, 'process': process}
    row = {'process': process, 'writer_drain': {'phase': 'enrolled', 'writer_id': '5'*32,
           'registration_sha256': '6'*64}}
    events, sent = [], []
    @contextmanager
    def admitted(*_):
        yield
    monkeypatch.setattr(lease, '_transition_admission', admitted)
    original_hash = hashlib.sha256(handshake._canonical(proof)).hexdigest()
    frame = {'schema_version': 1, 'kind': 'main_drain_request', 'nonce': nonce, 'epoch': epoch,
             'binding_sha256': proof['binding_sha256'], 'backend_claim_sha256': original_hash,
             'request_id': '7'*32, 'budget_ms': 50}
    class Epoch:
        def snapshot(self):
            return {'registry_sha256': '8'*64}
        def close_epoch(self, **kwargs):
            events.append(('epoch_closed', kwargs))
    class Owner:
        root = '/controlled-root'
        _authenticated_backend_proof = proof
        _writer_epoch = Epoch()
        _bootstrap_channel = object()
        def _owned(self):
            events.append(('owned_checked', None))
            return row
        def _binding(self, value):
            assert value is row
            events.append(('binding_checked', None))
        def _live(self, value, actual):
            assert value is row and actual is process
        def publish_writer_drain(self, **kwargs):
            events.append(('publication', kwargs))
    owner = Owner()
    owner.nonce = nonce
    def send(channel, value, *, absolute_deadline):
        assert channel is owner._bootstrap_channel
        # Both request and ACK retain the same original fake-clock deadline;
        # this fixture must refuse a renewed or already expired send budget.
        assert absolute_deadline == 10.05 and clock.now < absolute_deadline
        sent.append(value)
    def read(channel, seconds):
        # The sole-reader now passes absolute-deadline minus current clock.
        # Check the resulting original bound, avoiding subtraction roundoff
        # (10.05 - 10 is 0.05000000000000071) without adding any tolerance.
        assert channel is owner._bootstrap_channel and 0 < seconds
        assert clock.now + seconds <= 10.05
        request = sent[-1]
        receipt = {'schema_version': 1, 'kind': 'backend_managed_drain', 'request': request,
                   'backend_proof': proof, 'status': 'managed_scopes_drained', 'active_scopes': 0,
                   'unsupported': [], 'scope': 'reviewed_foreground_scopes_only',
                   'whole_writer_coverage': False, 'process_tree_exit_verified': False,
                   'can_release_launch_lease': False}
        return {'schema_version': 1, 'kind': 'managed_drain_proof', 'nonce': nonce,
                'proof_b64': base64.b64encode(handshake._canonical(receipt)).decode()}
    monkeypatch.setattr(controller, 'send_frame', send)
    monkeypatch.setattr(controller, 'read_frame', read)
    return SimpleNamespace(clock=clock, owner=owner, frame=frame, events=events, sent=sent)


def busy():
    cls = getattr(lease, 'LeaseTransitionBusy', lease.LaunchLeaseError)
    return cls('Lease transition is busy; retry after it completes')


def publisher(drain, attempts):
    original = drain.owner.publish_writer_drain
    def publish(**kwargs):
        if kwargs['phase'] == 'drained':
            attempts.append(kwargs)
            if len(attempts) <= 2:
                raise busy()
        return original(**kwargs)
    drain.owner.publish_writer_drain = publish


def test_original_drain_retries_transient_publication_and_acknowledges_once(drain):
    attempts = []
    publisher(drain, attempts)
    controller.prepare_drain(drain.owner, drain.frame)
    assert len(attempts) == 3 and attempts[0] == attempts[1] == attempts[2]
    assert [s['kind'] for s in drain.sent] == ['backend_drain_request', 'managed_drain_admitted']
    assert sum(e[0] == 'epoch_closed' for e in drain.events) == 1
    assert drain.clock.now <= 10.05


def test_busy_publisher_stops_at_original_remaining_budget_without_ack(drain):
    attempts = []
    original = drain.owner.publish_writer_drain
    def publish(**kwargs):
        if kwargs['phase'] == 'drained':
            attempts.append(kwargs)
            raise busy()
        return original(**kwargs)
    drain.owner.publish_writer_drain = publish
    with pytest.raises(handshake.HandshakeError, match='budget expired'):
        controller.prepare_drain(drain.owner, drain.frame)
    assert len(attempts) > 1 and drain.clock.now <= 10.05
    assert [s['kind'] for s in drain.sent] == ['backend_drain_request']


@pytest.mark.parametrize('error', [lease.LaunchLeaseError('Lease transition is busy; retry after it completes'),
                                  lease.LaunchLeaseError('Committed launch binding changed'),
                                  OSError(errno.EIO, 'controlled fatal IO')])
def test_non_typed_errors_are_not_retried_or_acknowledged(drain, error):
    attempts = []
    original = drain.owner.publish_writer_drain
    def publish(**kwargs):
        if kwargs['phase'] == 'drained':
            attempts.append(kwargs)
            raise error
        return original(**kwargs)
    drain.owner.publish_writer_drain = publish
    with pytest.raises(type(error), match=str(error).replace('[', r'\[').replace(']', r'\]')):
        controller.prepare_drain(drain.owner, drain.frame)
    assert len(attempts) == 1 and drain.clock.now == 10
    assert [s['kind'] for s in drain.sent] == ['backend_drain_request']


def test_retry_revalidates_changed_original_publication_instead_of_acknowledging(drain):
    attempts = []
    original = drain.owner.publish_writer_drain
    def publish(**kwargs):
        if kwargs['phase'] == 'drained':
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise busy()
            raise lease.LaunchLeaseError('Writer drain replay or phase differs')
        return original(**kwargs)
    drain.owner.publish_writer_drain = publish
    with pytest.raises(lease.LaunchLeaseError, match='phase differs'):
        controller.prepare_drain(drain.owner, drain.frame)
    assert len(attempts) == 2
    assert [s['kind'] for s in drain.sent] == ['backend_drain_request']


def test_publication_cannot_renew_budget_or_send_late_ack(drain):
    original = drain.owner.publish_writer_drain
    def publish(**kwargs):
        if kwargs['phase'] == 'drained':
            drain.clock.now += .06
        return original(**kwargs)
    drain.owner.publish_writer_drain = publish
    with pytest.raises(handshake.HandshakeError, match='budget expired'):
        controller.prepare_drain(drain.owner, drain.frame)
    assert [s['kind'] for s in drain.sent] == ['backend_drain_request']


@pytest.mark.skipif(os.name != 'posix', reason='Original POSIX transition mutex')
def test_actual_transition_contention_has_typed_error_and_fatal_flock_does_not(tmp_path, monkeypatch):
    import fcntl
    from backend.tests.test_application_launch_lease import installed, reserve
    root, value, _ = installed(tmp_path)
    owner = reserve(root, value)
    lock = root/lease.LEASES/owner.nonce/lease.TRANSITION_LOCK
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(lease.LaunchLeaseError) as raised:
            with lease._transition_admission(root, owner.nonce):
                pytest.fail('Original held mutex was admitted')
        cls = getattr(lease, 'LeaseTransitionBusy', None)
        assert cls is not None and type(raised.value) is cls
        assert raised.value.__cause__.errno in {errno.EAGAIN, errno.EWOULDBLOCK}
    finally:
        os.close(fd)
    original_flock = fcntl.flock
    identity = (lock.stat().st_dev, lock.stat().st_ino)
    def broken(target, operation):
        info = os.fstat(target)
        if (info.st_dev, info.st_ino) == identity:
            raise OSError(errno.EIO, 'controlled flock IO')
        return original_flock(target, operation)
    monkeypatch.setattr(fcntl, 'flock', broken)
    with pytest.raises(lease.LaunchLeaseError) as raised:
        with lease._transition_admission(root, owner.nonce):
            pytest.fail('Fatal flock was admitted')
    assert type(raised.value) is lease.LaunchLeaseError
    assert raised.value.__cause__.errno == errno.EIO
