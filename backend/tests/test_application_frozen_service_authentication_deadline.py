"""Behavioral deadline separation model; no native/compiler/model acceptance.

The real service loop and generic service validator execute. OS socket, signed
artifact validation, clock, and CPU callback are explicit deterministic models.
Root alone runs this file against the untouched baseline before the overlay.
"""
import copy
from contextlib import nullcontext
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace

import pytest
from backend.engine import application_launch_handshake as h
from backend.engine import application_launch_execution as execution
from backend.engine import application_preflight_child_relay as relay
from backend.engine import application_owned_cpu_child_relay as cpu_relay
from backend.engine import migration_guard


class Clock:
    def __init__(self):
        self.now = 100.0
    def monotonic(self):
        return self.now
    def sleep(self, amount):
        self.now += amount


@pytest.fixture
def service(monkeypatch):
    clock = Clock()
    stop = threading.Event()
    observations = {'auth': [], 'read': [], 'peek': [], 'consumed': [],
                    'execute': [], 'abandon': [], 'thread': []}
    root = Path('/modeled-original-install')
    context = ('original-process', 'original-context')
    original_proof = {'frozen': True, 'nonce': 'n', 'epoch': 'e',
                      'binding_sha256': 'b' * 64}
    challenge = {'kind': 'backend_challenge', 'binding': {'generation': 'g'}}
    class Socket:
        def recv(self, size, flags=0):
            assert self is sock and size == h.MAX_FRAME + 1
            assert flags == socket.MSG_PEEK | getattr(socket, 'MSG_DONTWAIT', 0)
            assert not state.frame_consumed
            # The original classifier peeks the SAME next frame as read_frame
            # below. Peek neither consumes that frame nor advances its IO clock.
            raw = h._canonical(state.frame) + b'\n'
            observations['peek'].append({'at': clock.now, 'raw': raw,
                                         'reads_before': len(observations['read'])})
            return raw[:size]
        def shutdown(self, how):
            observations['shutdown'] = how
        def close(self):
            observations['closed'] = True
    sock = Socket()
    class Queue:
        def claim_reader(self):
            observations['reader'] = True
        def _fresh(self, endpoint, *, reader):
            assert endpoint is sock and reader is True
        def take(self, endpoint):
            assert endpoint is sock
            return None
        def abandon(self, error):
            observations['abandon'].append(error)
    queue = Queue()
    class Admission:
        def uncovered(self, reason):
            observations['uncovered'] = reason
    admission = Admission()
    cache = {'root': root, 'context': context, 'socket': sock,
             'proof': copy.deepcopy(original_proof), 'challenge': challenge,
             'preflight_queue': queue, 'admission': admission, 'ready': True}
    state = SimpleNamespace(clock=clock, stop=stop, observations=observations,
                            root=root, context=context, proof=original_proof,
                            challenge=challenge, queue=queue, admission=admission,
                            socket=sock, cache=cache, pre_seconds=12.0,
                            post_seconds=12.0, io_seconds=0.0, mutate=None, frame_consumed=False,
                            frame={'schema_version': 1, 'kind': 'cpu_execution_request',
                                   'request_id': 'r' * 32})
    def validate(current_root, values, frame):
        assert current_root is root and frame is challenge
        index = len(observations['auth'])
        observations['auth'].append({'start': clock.now,
                                     'deadline_observed': None})
        clock.now += state.pre_seconds if index == 0 else state.post_seconds
        if state.mutate is not None:
            state.mutate(index)
        return copy.deepcopy(original_proof)
    original_service_validator = h._validate_service_action
    def service_validator(original, deadline):
        index = len(observations['auth'])
        result = original_service_validator(original, deadline)
        observations['auth'][index]['deadline_observed'] = deadline
        return result
    def read(endpoint, timeout):
        assert endpoint is sock and 0 < timeout <= 10.0
        assert not state.frame_consumed and observations['peek']
        raw = h._canonical(state.frame) + b'\n'
        assert observations['peek'][-1]['raw'] == raw
        assert observations['peek'][-1]['reads_before'] == len(observations['read'])
        state.frame_consumed = True
        observations['consumed'].append(raw)
        observations['read'].append({'at': clock.now, 'timeout': timeout})
        clock.now += state.io_seconds
        return copy.deepcopy(state.frame)
    def execute(request, authenticated, current_root):
        assert current_root is root and authenticated == original_proof
        action = cache['original_cpu_execution']
        observations['execute'].append({'request': request,
                                         'deadline': action['deadline'],
                                         'at': clock.now,
                                         'record': action})
        stop.set()
        return {'kind': 'modeled_cpu_complete'}
    class Thread:
        def __init__(self, *, target, name, daemon):
            assert name == 'owned-controller-cpu' and daemon is True
            self.target = target
            observations['thread'].append(self)
        def start(self):
            self.target()
    monkeypatch.setattr(h, '_CACHE', cache)
    monkeypatch.setattr(h, '_context', lambda: state.context)
    monkeypatch.setattr(h, '_root_context', lambda: (state.root, {}))
    monkeypatch.setattr(h, '_validate', validate)
    monkeypatch.setattr(h, '_validate_service_action', service_validator)
    monkeypatch.setattr(h, 'read_frame', read)
    monkeypatch.setattr(h, 'time', clock)
    monkeypatch.setattr(h, 'threading', SimpleNamespace(Event=threading.Event, Thread=Thread))
    monkeypatch.setattr(h, 'select', SimpleNamespace(select=lambda *a: ([sock], [], [])))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(relay, 'BackendRelayQueue', Queue)
    monkeypatch.setattr(migration_guard, 'maintenance_guard', lambda root: nullcontext())
    monkeypatch.setattr(execution, 'execute_backend', execute)
    state.run = lambda: h.backend_execution_service(stop)
    return state


def test_frozen_initial_cpu_keeps_both_full_authentications_and_one_action_deadline(service):
    service.run()
    observations = service.observations
    assert len(observations['auth']) == 2
    assert [row['deadline_observed'] for row in observations['auth']] == [310.0, 310.0]
    assert observations['read'] == [{'at': 112.0, 'timeout': 10.0}]
    assert len(observations['execute']) == 1
    assert observations['execute'][0]['deadline'] == 310.0
    assert observations['execute'][0]['at'] == 124.0
    assert observations['execute'][0]['record'] is service.cache['original_cpu_execution']
    assert observations['abandon'] == []
    raw = h._canonical(service.frame) + b'\n'
    assert observations['peek'] == [{'at': 100.0, 'raw': raw, 'reads_before': 0}]
    assert observations['consumed'] == [raw] and service.frame_consumed is True


def test_original_io_deadline_is_ten_seconds_after_fresh_preauth(service):
    service.io_seconds = 9.0
    service.run()
    assert service.observations['read'] == [{'at': 112.0, 'timeout': 10.0}]
    assert service.observations['execute'][0]['at'] == 133.0
    assert service.observations['execute'][0]['deadline'] == 310.0


def test_io_is_also_bounded_by_original_cpu_action_remaining(service):
    service.pre_seconds = 205.0
    service.post_seconds = 1.0
    service.io_seconds = 1.0
    service.run()
    assert service.observations['read'] == [{'at': 305.0, 'timeout': 5.0}]
    assert service.observations['execute'][0]['deadline'] == 310.0


@pytest.mark.parametrize('point', ['preauth', 'postauth', 'io'])
def test_captured_cpu_deadline_cannot_be_renewed(service, point):
    if point == 'preauth':
        service.pre_seconds = 210.0
    elif point == 'postauth':
        service.post_seconds = 199.0
    else:
        service.pre_seconds = 205.0
        service.io_seconds = 5.0
    with pytest.raises(h.HandshakeError):
        service.run()
    assert service.observations['execute'] == []
    assert 'original_cpu_execution' not in service.cache


def test_late_io_return_never_dispatches_even_with_action_time_remaining(service):
    service.io_seconds = 10.01
    with pytest.raises(h.HandshakeError):
        service.run()
    assert service.observations['execute'] == []


@pytest.mark.parametrize('kind', ['backend_drain_request', 'controller_preflight_reply',
                                 'controller_cpu_child_reply', 'unknown'])
def test_non_cpu_frame_cannot_borrow_captured_cpu_deadline(service, kind):
    service.frame['kind'] = kind
    with pytest.raises(h.HandshakeError):
        service.run()
    assert len(service.observations['auth']) == 2
    assert service.observations['execute'] == []
    assert service.observations['thread'] == []
    assert 'original_cpu_execution' not in service.cache


@pytest.mark.parametrize('gate', ['sys_source', 'proof_source', 'source_queue',
                                 'old_cpu', 'old_drain'])
def test_noninitial_or_nonfrozen_context_keeps_original_ten_second_authentication(service, monkeypatch, gate):
    if gate == 'sys_source':
        monkeypatch.setattr(sys, 'frozen', False)
    elif gate == 'proof_source':
        service.proof['frozen'] = False
        service.cache['proof']['frozen'] = False
    elif gate == 'source_queue':
        class SourceQueue:
            def claim_reader(self): pass
            def _fresh(self, endpoint, *, reader): pass
            def take(self, endpoint): return None
            def completion_outgoing(self): return None
            def abandon(self, error): pass
        monkeypatch.setattr(cpu_relay, 'CpuRelayQueue', SourceQueue)
        service.cache['source_cpu_queue'] = SourceQueue()
    elif gate == 'old_cpu':
        service.cache['original_cpu_execution'] = {'retained': 'old'}
    else:
        service.cache['preflight_drain_deadline'] = 150.0
    with pytest.raises(h.HandshakeError):
        service.run()
    assert len(service.observations['auth']) == 1
    assert service.observations['read'] == []
    assert service.observations['execute'] == []


@pytest.mark.parametrize('point', ['preauth', 'postauth'])
@pytest.mark.parametrize('target', ['cache', 'context_reference', 'root', 'socket',
                                   'queue', 'admission', 'proof_reference',
                                   'challenge_reference', 'proof_value',
                                   'challenge_value', 'source_queue', 'old_cpu',
                                   'drain_deadline', 'ready'])
def test_original_reference_and_authority_drift_refuses_before_dispatch(service, monkeypatch, point, target):
    def mutate(index):
        if index != (0 if point == 'preauth' else 1): return
        cache = service.cache
        if target == 'cache':monkeypatch.setattr(h, '_CACHE', dict(cache))
        elif target == 'context_reference':cache['context'] = tuple(list(cache['context']))
        elif target == 'root':cache['root'] = Path(str(cache['root']))
        elif target == 'socket':cache['socket'] = object()
        elif target == 'queue':cache['preflight_queue'] = object()
        elif target == 'admission':cache['admission'] = object()
        elif target == 'proof_reference':cache['proof'] = dict(cache['proof'])
        elif target == 'challenge_reference':cache['challenge'] = copy.deepcopy(cache['challenge'])
        elif target == 'proof_value':cache['proof']['nonce'] = 'changed'
        elif target == 'challenge_value':cache['challenge']['binding']['generation'] = 'changed'
        elif target == 'source_queue':cache['source_cpu_queue'] = object()
        elif target == 'old_cpu':cache['original_cpu_execution'] = {'copied': True}
        elif target == 'drain_deadline':cache['preflight_drain_deadline'] = 310.0
        else:cache['ready'] = False
    service.mutate = mutate
    with pytest.raises(h.HandshakeError):
        service.run()
    assert service.observations['execute'] == []
    assert service.observations['thread'] == []


def test_post_authentication_still_refuses_wrong_fresh_signed_binding(service, monkeypatch):
    original = h._validate
    def validate(*args, **kwargs):
        result = original(*args, **kwargs)
        if len(service.observations['auth']) == 2:result['binding_sha256'] = 'x' * 64
        return result
    monkeypatch.setattr(h, '_validate', validate)
    with pytest.raises(h.HandshakeError):
        service.run()
    assert len(service.observations['auth']) == 2
    assert service.observations['execute'] == []
