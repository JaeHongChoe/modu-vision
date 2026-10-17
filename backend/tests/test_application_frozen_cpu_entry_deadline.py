"""Native14 frozen CPU admission clock regression; no app, child or model.

The existing fixture runs the real original lease OFD and canonical backend
validator. Installation, signed artifact/OS identity and expensive hash duration
are explicit models. These tests confer no frozen/native acceptance.
"""
import copy
import fcntl
import os
import sys
import threading
from contextlib import contextmanager

import pytest

from backend.engine import application_launch_handshake as h
from backend.engine import application_launch_lease as lease
from backend.engine import runtime_update as update
from backend.tests.test_application_cpu_publication_admission import original


@pytest.fixture
def frozen_original(original, monkeypatch):
    o = original
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    old_executable = h._executable

    def frozen_executable(*args):
        value = old_executable(*args)
        return dict(value, frozen=True, build_identity_sha256='c' * 64)

    monkeypatch.setattr(h, '_executable', frozen_executable)
    o['challenge']['backend_build_identity_sha256'] = 'c' * 64
    context = (('original',), ('original-backend',), 'modeled-frozen-backend', True)
    o['cache']['context'] = context
    monkeypatch.setattr(h, '_context', lambda: context)
    o['proof'] = h._validate(o['root'], o['values'], o['challenge'])
    o['cache']['proof'] = o['proof']
    # This is the same private factory shape, not an argument/token passed to
    # the producer. No thread is started; current_thread is a strong object.
    action = o['cache']['original_cpu_execution']
    assert action['thread'] is threading.current_thread()
    assert not action['done'].is_set()
    assert action['deadline'] == 310.0
    o['checks'].clear()
    return o


def slow_full_auth(o, monkeypatch, costs=(1.25, 1.25), after_first=None):
    original_pair = update._backend_launch_binding
    calls = []

    def pair(*args, **kwargs):
        value = original_pair(*args, **kwargs)
        calls.append(True)
        o['clock'][0] += costs[min(len(calls) - 1, len(costs) - 1)]
        if len(calls) == 1 and after_first is not None:
            after_first()
        return value

    monkeypatch.setattr(update, '_backend_launch_binding', pair)
    return calls


def hold_mutex(o):
    fd = os.open(o['lock'], os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def assert_no_producer(o, bodies):
    assert bodies == []
    assert o['admission'].snapshot() == {'active_scopes': 1, 'unsupported': []}
    # The real original lease mutex has been released, including on refusal.
    with lease._transition_admission(o['root'], o['challenge']['nonce']):
        pass


def test_original_frozen_cpu_full_auth_over_one_second_keeps_two_fresh_checks(frozen_original, monkeypatch):
    o = frozen_original
    action = o['cache']['original_cpu_execution']
    calls = slow_full_auth(o, monkeypatch)
    bodies, exposed = [], None
    with h.owned_cpu_writer_scope() as descriptors:
        bodies.append(True)
        assert len(descriptors) == 1
        exposed = descriptors[0]
        assert os.fstat(exposed).st_ino == os.fstat(o['cache']['writer_private_fd']).st_ino
        assert o['admission'].snapshot() == {'active_scopes': 2, 'unsupported': []}
        assert o['clock'][0] == 102.5
        assert o['cache']['original_cpu_execution'] is action
        assert action['deadline'] == 310.0
    assert bodies == [True]
    assert len(calls) == o['checks'].count('executable') == 2
    assert o['admission'].snapshot() == {'active_scopes': 1, 'unsupported': []}
    assert action['deadline'] == 310.0
    with pytest.raises(OSError):
        os.fstat(exposed)


def test_original_frozen_cpu_busy_entry_still_expires_at_one_second(frozen_original):
    o = frozen_original
    fd, bodies = hold_mutex(o), []
    try:
        with pytest.raises(h.HandshakeError):
            with h.owned_cpu_writer_scope():
                bodies.append(True)
        assert bodies == [] and 101.0 <= o['clock'][0] < 101.0051
        assert o['checks'].count('executable') == 0
        assert o['admission'].snapshot() == {'active_scopes': 1, 'unsupported': []}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_original_frozen_cpu_expired_action_never_attempts_mutex_or_auth(frozen_original, monkeypatch):
    o = frozen_original
    o['cache']['original_cpu_execution']['deadline'] = 100.0
    attempts, bodies = [], []
    original_entry = lease._transition_admission

    @contextmanager
    def entry(*args):
        attempts.append(True)
        with original_entry(*args):
            yield

    monkeypatch.setattr(lease, '_transition_admission', entry)
    with pytest.raises(h.HandshakeError):
        with h.owned_cpu_writer_scope():
            bodies.append(True)
    assert attempts == [] and o['checks'].count('executable') == 0
    assert_no_producer(o, bodies)


@pytest.mark.parametrize('where', ['pre-auth', 'post-auth', 'normal-exit'])
def test_original_frozen_cpu_auth_and_exit_cannot_renew_action_deadline(frozen_original, monkeypatch, where):
    o = frozen_original
    action = o['cache']['original_cpu_execution']
    costs = (211.0, 0.0) if where == 'pre-auth' else ((0.0, 211.0) if where == 'post-auth' else (1.25, 1.25))
    slow_full_auth(o, monkeypatch, costs)
    original_entry = lease._transition_admission

    @contextmanager
    def entry(*args):
        with original_entry(*args):
            yield
        if where == 'normal-exit':
            o['clock'][0] = 310.0

    monkeypatch.setattr(lease, '_transition_admission', entry)
    bodies = []
    with pytest.raises(h.HandshakeError):
        with h.owned_cpu_writer_scope():
            bodies.append(True)
    assert action['deadline'] == 310.0
    assert_no_producer(o, bodies)


def test_original_frozen_cpu_slow_successful_mutex_enter_refuses_before_hash(frozen_original, monkeypatch):
    o = frozen_original
    original_entry = lease._transition_admission
    attempts, bodies = [], []

    @contextmanager
    def entry(*args):
        attempts.append(True)
        with original_entry(*args):
            o['clock'][0] = 101.1
            yield

    monkeypatch.setattr(lease, '_transition_admission', entry)
    with pytest.raises(h.HandshakeError):
        with h.owned_cpu_writer_scope():
            bodies.append(True)
    assert attempts == [True]
    assert o['checks'].count('executable') == 0
    assert_no_producer(o, bodies)


@pytest.mark.parametrize('damage', [
    'copied-record', 'deadline', 'done-object', 'done-set', 'same-name-foreign-thread',
    'cache', 'context', 'socket', 'socket-closed', 'ofd', 'proof',
])
def test_original_frozen_cpu_custody_changes_during_full_auth_refuse(frozen_original, monkeypatch, damage):
    o = frozen_original
    action = o['cache']['original_cpu_execution']
    extra = []

    def change():
        if damage == 'copied-record':
            o['cache']['original_cpu_execution'] = dict(action)
        elif damage == 'deadline':
            action['deadline'] = 311.0
        elif damage == 'done-object':
            action['done'] = threading.Event()
        elif damage == 'done-set':
            action['done'].set()
        elif damage == 'same-name-foreign-thread':
            action['thread'] = threading.Thread(name=threading.current_thread().name)
        elif damage == 'cache':
            monkeypatch.setattr(h, '_CACHE', dict(o['cache']))
        elif damage == 'context':
            monkeypatch.setattr(h, '_context', lambda: ('foreign',))
        elif damage == 'socket':
            o['cache']['socket'] = o['right']
        elif damage == 'socket-closed':
            o['left'].close()
        elif damage == 'ofd':
            fd = os.open(o['root'] / 'original-writer.lock', os.O_RDWR)
            extra.append(fd)
            assert os.fstat(fd).st_ino == os.fstat(o['cache']['writer_private_fd']).st_ino
            o['cache']['writer_private_fd'] = fd
        elif damage == 'proof':
            o['cache']['proof'] = dict(o['proof'], nonce='f' * 32)
        else:
            raise AssertionError(damage)

    slow_full_auth(o, monkeypatch, after_first=change)
    bodies = []
    try:
        with pytest.raises((ValueError, OSError)):
            with h.owned_cpu_writer_scope():
                bodies.append(True)
        assert_no_producer(o, bodies)
    finally:
        for fd in extra:
            os.close(fd)


def test_original_frozen_cpu_changed_pair_on_post_auth_is_not_cached(frozen_original, monkeypatch):
    o = frozen_original
    original_pair = update._backend_launch_binding
    calls, bodies = [], []

    def pair(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            monkeypatch.setattr(update, '_launch_binding',
                lambda *a, **k: dict(o['binding'], authority_sha256='0' * 64))
        value = original_pair(*args, **kwargs)
        o['clock'][0] += 1.25
        return value

    monkeypatch.setattr(update, '_backend_launch_binding', pair)
    with pytest.raises(h.HandshakeError, match='binding|pair|authority'):
        with h.owned_cpu_writer_scope():
            bodies.append(True)
    assert len(calls) == 2
    assert_no_producer(o, bodies)


def test_source_http_producer_cannot_borrow_frozen_action_budget(original, monkeypatch):
    o = original
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    slow_full_auth(o, monkeypatch)
    bodies = []
    with pytest.raises(h.HandshakeError):
        with h.owned_cpu_writer_scope():
            bodies.append(True)
    assert_no_producer(o, bodies)


def test_flow_producer_cannot_borrow_fixed_frozen_cpu_action_budget(frozen_original, monkeypatch):
    o = frozen_original
    slow_full_auth(o, monkeypatch)
    bodies = []
    with pytest.raises(h.HandshakeError):
        with h.owned_flow_cpu_writer_scope(device='cpu'):
            bodies.append(True)
    assert_no_producer(o, bodies)
