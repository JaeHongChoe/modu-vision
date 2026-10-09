"""Original reserve mutex entry under its already admitted action deadline.

These controls use the existing signed publication fixture's real retained
per-lease OFD and original no-follow artifact reader. Installation, CPU math,
process/Node ownership and the returned durable row remain explicit fixture
models. No child/thread/application is permitted by the original fixture.
The historical hosted artifact-drift callsite is not inferred from this model.
"""
import copy
import fcntl
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend.engine import application_launch_execution as execution
from backend.engine import application_launch_handshake as h
from backend.engine import application_launch_lease as lease
from backend.engine import application_owned_cpu_child_relay as cpu
from backend.engine import runtime_update as update
from backend.tests.test_application_cpu_publication_admission import original

# Capture the real API before the existing original fixture's scoped model.
ORIGINAL_VALIDATE_REQUEST = execution.validate_request


@pytest.fixture
def reserve_request(original, monkeypatch):
    o = original
    monkeypatch.setattr(execution, 'validate_request', ORIGINAL_VALIDATE_REQUEST)
    frame = copy.deepcopy(o['frame'])
    frame['backend_claim_sha256'] = update._sha(update._canonical(o['proof']))
    intent = {'request': frame, 'capability': copy.deepcopy(o['state']['capability'])}
    path = o['root']/lease.LEASES/frame['nonce']/'cpu-execution-intent.json'
    path.write_bytes(update._canonical(intent))
    o['row']['cpu_execution'] = {'request_id': frame['request_id'],
        'request_sha256': update._sha(update._canonical(frame)), 'receipt_sha256': None}
    # This is a modeled original durable row, not a published lease authority.
    original_load = lease._load
    bodies = []
    def load(root):
        assert root == o['root']
        bodies.append(root)
        return original_load(root)
    monkeypatch.setattr(lease, '_load', load)
    return o, frame, intent, bodies


def held_mutex(o):
    fd = os.open(o['lock'], os.O_RDWR)
    os.set_inheritable(fd, False)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    retained = [True]
    def release():
        if retained[0]:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            retained[0] = False
    return release


def release_on_sleep(o, monkeypatch, release, *, mutate=None):
    def sleep(seconds):
        assert 0 < seconds <= .005
        assert o['admission'].snapshot() == {'active_scopes': 1, 'unsupported': []}
        o['sleeps'].append(seconds)
        o['clock'][0] += seconds
        release()
        if mutate is not None:
            mutate()
    monkeypatch.setattr(h.time, 'sleep', sleep)


def test_reserve_real_mutex_busy_does_not_replace_original_artifact_checksum(reserve_request, monkeypatch):
    o, frame, intent, bodies = reserve_request
    reviewed = b'original reviewed bytes'
    path = o['root']/'original-changed-input.bin'
    path.write_bytes(b'changed reviewed bytes')
    expected = update._sha(reviewed)
    errors = []
    checks = []
    def original_artifact_plan(root, workspace, project, digest):
        assert (root, workspace, project, digest) == (o['root'], frame['workspace_id'],
            frame['project_id'], frame['plan_sha256'])
        checks.append(path)
        try:
            execution._read(path, expected=expected)
        except execution.ExecutionError as error:
            errors.append(error)
            raise
        pytest.fail('Changed original artifact was accepted')
    monkeypatch.setattr(execution, 'admit_plan', original_artifact_plan)
    value = {'cpu_request': frame, 'budget_ms': 4000, 'deadline_monotonic': 104.0,
        'workdir': str(o['root']/'original-snapshot'), 'gate_fd': 7, 'command': []}
    before = copy.deepcopy(value)
    release = held_mutex(o)
    release_on_sleep(o, monkeypatch, release)
    try:
        with pytest.raises(execution.ExecutionError, match='Reviewed execution artifact checksum differs') as caught:
            cpu.validate_controller_plan(SimpleNamespace(root=o['root']), value, o['proof'])
        assert errors == [caught.value] and caught.value is errors[0]
        assert checks == [path] and bodies == [o['root']]
        assert o['sleeps'] == [.005] and o['clock'][0] == 100.005
        assert value == before and value['deadline_monotonic'] == 104.0
        assert o['admission'].snapshot() == {'active_scopes': 1, 'unsupported': []}
        assert o['row']['cpu_execution']['receipt_sha256'] is None
    finally:
        release()


def test_generic_request_keeps_original_real_nonblocking_mutex(reserve_request):
    o, frame, intent, bodies = reserve_request
    release = held_mutex(o)
    try:
        with pytest.raises(lease.LeaseTransitionBusy):
            execution.validate_request(frame, o['proof'], o['root'])
        assert bodies == [] and o['sleeps'] == [] and o['clock'][0] == 100.0
    finally:
        release()


@pytest.mark.parametrize('point', ['body', 'post_body'])
def test_bounded_request_never_retries_original_body_or_post_busy(reserve_request, monkeypatch, point):
    o, frame, intent, bodies = reserve_request
    error = lease.LeaseTransitionBusy('Exact original non-entry Busy')
    original_entry = lease._transition_admission
    attempts = []
    @contextmanager
    def entry(root, nonce):
        attempts.append((root, nonce))
        with original_entry(root, nonce):
            yield
            if point == 'post_body':
                raise error
    monkeypatch.setattr(lease, '_transition_admission', entry)
    if point == 'body':
        def body(root):
            bodies.append(root)
            raise error
        monkeypatch.setattr(lease, '_load', body)
    with pytest.raises(lease.LeaseTransitionBusy) as caught:
        execution.validate_request(frame, o['proof'], o['root'], absolute_deadline=104.0)
    assert caught.value is error and attempts == [(o['root'], frame['nonce'])]
    assert bodies == [o['root']] and o['sleeps'] == []
    assert o['row']['cpu_execution']['receipt_sha256'] is None


def test_bounded_request_real_mutex_expires_without_renewal_or_body(reserve_request):
    o, frame, intent, bodies = reserve_request
    release = held_mutex(o)
    try:
        with pytest.raises(h.HandshakeError, match='Original transition entry deadline expired'):
            execution.validate_request(frame, o['proof'], o['root'], absolute_deadline=100.02)
        assert bodies == [] and 100.02 <= o['clock'][0] < 100.0251
        assert o['sleeps'] and all(0 < seconds <= .005 for seconds in o['sleeps'])
        assert o['admission'].snapshot() == {'active_scopes': 1, 'unsupported': []}
    finally:
        release()


@pytest.mark.parametrize('target', ['frame', 'proof'])
def test_bounded_request_rechecks_original_inputs_after_real_mutex_busy(reserve_request, monkeypatch, target):
    o, frame, intent, bodies = reserve_request
    release = held_mutex(o)
    current = frame if target == 'frame' else o['proof']
    release_on_sleep(o, monkeypatch, release, mutate=lambda: current.__setitem__('foreign', True))
    try:
        with pytest.raises(execution.ExecutionError):
            execution.validate_request(frame, o['proof'], o['root'], absolute_deadline=104.0)
        assert bodies == [] and o['sleeps'] == [.005]
    finally:
        release()


@pytest.mark.parametrize('point', ['body', 'post_body'])
def test_bounded_request_keeps_original_deadline_across_body_and_guard_exit(reserve_request, monkeypatch, point):
    o, frame, intent, bodies = reserve_request
    original_entry = lease._transition_admission
    original_load = lease._load
    @contextmanager
    def entry(root, nonce):
        with original_entry(root, nonce):
            yield
            if point == 'post_body':
                o['clock'][0] = 104.0
    def load(root):
        row = original_load(root)
        if point == 'body':
            o['clock'][0] = 104.0
        return row
    monkeypatch.setattr(lease, '_transition_admission', entry)
    monkeypatch.setattr(lease, '_load', load)
    with pytest.raises(h.HandshakeError, match='Original transition entry deadline expired'):
        execution.validate_request(frame, o['proof'], o['root'], absolute_deadline=104.0)
    assert bodies == [o['root']] and o['sleeps'] == []
    assert o['row']['cpu_execution']['receipt_sha256'] is None
