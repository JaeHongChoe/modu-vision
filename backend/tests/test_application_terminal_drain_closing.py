"""Terminal service closing controls; modeled authority, no child or runtime.

Only controller/registry authentication is modeled. The held Event, socketpair,
relay queue, admission counts, drain request/receipt validators are original.
"""
from contextlib import nullcontext
import copy
import hashlib
import socket
import threading
from types import SimpleNamespace

import pytest

from backend.engine import application_launch_handshake as handshake
from backend.engine import application_launch_quiescence as quiescence
from backend.engine import application_preflight_child_relay as relay
from backend.engine import migration_guard

pytestmark = pytest.mark.no_child


@pytest.fixture
def original_terminal(monkeypatch, tmp_path):
    left, peer = socket.socketpair()
    queue = relay.BackendRelayQueue(left)
    admission = handshake.BackendWorkAdmission()
    event = threading.Event()
    clock = SimpleNamespace(now=10.0)
    context = ('original-model-context',)
    values = {'original': 'values'}
    binding = {'original': 'binding'}
    process = {'pid': 1234, 'created_at': 10.0, 'command_sha256': 'a'*64}
    proof = {'nonce': 'b'*32, 'epoch': 'c'*32, 'process': process}
    writer = {'writer_id': 'd'*32, 'registration_sha256': 'e'*64}
    cache = {'ready': True, 'root': tmp_path, 'context': context,
             'socket': left, 'proof': proof, 'challenge': {'writer': writer, 'binding': binding},
             'writer_guard': object(), 'admission': admission, 'preflight_queue': queue}
    frame = {'schema_version': 1, 'kind': 'backend_drain_request', 'challenge': 'f'*64,
             'request_id': '1'*32, 'nonce': proof['nonce'], 'epoch': proof['epoch'],
             'binding_sha256': hashlib.sha256(handshake._canonical(binding)).hexdigest(),
             'backend_claim_sha256': hashlib.sha256(handshake._canonical(proof)).hexdigest(),
             'writer_id': writer['writer_id'], 'registration_sha256': writer['registration_sha256'],
             'closed_registry_sha256': '2'*64, 'budget_ms': 200}
    state = SimpleNamespace(cache=cache, queue=queue, admission=admission, event=event,
                            clock=clock, frame=frame, left=left, peer=peer, reads=[], sends=[],
                            validates=[], waits=[], terminal=False, on_terminal=lambda: None,
                            on_wait=lambda: None, values=values, context=context,
                            frames=[frame], terminal_after=4, auto_stop=True)
    monkeypatch.setattr(handshake, '_CACHE', cache)
    monkeypatch.setattr(handshake, '_context', lambda: context)
    monkeypatch.setattr(handshake, '_root_context', lambda: (tmp_path, values))
    monkeypatch.setattr(handshake.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(migration_guard, 'maintenance_guard', lambda root: nullcontext())
    monkeypatch.setattr(quiescence, 'inspect_epoch', lambda root, nonce:
                        {'registry': {'state': 'closed'}, 'registry_sha256': '2'*64})

    def validated(original_cache, deadline):
        state.validates.append(deadline)
        assert original_cache is cache and clock.now < deadline
        if state.terminal:
            raise handshake.HandshakeError('Backend challenge has no current spawned main owner')
        if len(state.validates) == state.terminal_after:
            state.terminal = True
            peer.close()  # Genuine held socket identity survives original peer EOF.
            state.on_terminal()
        return proof

    def read(channel, seconds):
        state.reads.append(seconds)
        assert channel is left
        if len(state.reads) > len(state.frames):
            raise handshake.HandshakeError('No new authority action after terminal receipt')
        return copy.deepcopy(state.frames[len(state.reads)-1])

    def send(channel, value, *, absolute_deadline=None):
        assert channel is left and absolute_deadline == 10.2
        state.sends.append(copy.deepcopy(value))

    def wait(seconds):
        state.waits.append(seconds)
        assert state.terminal and 0 < seconds <= .02
        assert len(state.reads) == len(state.frames) and len(state.sends) == 1
        assert len(state.validates) == state.terminal_after
        state.on_wait()
        clock.now += min(seconds, .01)
        if state.auto_stop and len(state.waits) == 2:
            event.set()
        return event.is_set()

    monkeypatch.setattr(handshake, '_validate_service_action', validated)
    monkeypatch.setattr(handshake, 'read_frame', read)
    monkeypatch.setattr(handshake, 'send_frame', send)
    monkeypatch.setattr(handshake.select, 'select', lambda r, w, x, seconds: (r, [], []))
    monkeypatch.setattr(event, 'wait', wait)
    try:
        yield state
    finally:
        left.close()
        peer.close()


@pytest.mark.parametrize('unsupported', [False, True])
def test_original_terminal_receipt_waits_for_original_stop_without_another_authority_action(original_terminal, unsupported):
    state = original_terminal
    if unsupported:
        state.admission.uncovered('accepted_background_work')
    before = state.admission.snapshot()
    handshake.backend_execution_service(state.event)
    assert state.event.is_set() and len(state.waits) == 2
    assert state.clock.now < 10.2
    assert len(state.validates) == 4 and len(state.reads) == 1 and len(state.sends) == 1
    receipt = state.sends[0]
    handshake.validate_drain_receipt(receipt, state.frame, backend_process=state.cache['proof']['process'])
    assert receipt['status'] == ('refused' if unsupported else 'managed_scopes_drained')
    assert receipt['unsupported'] == before['unsupported']
    assert state.admission.snapshot() == before
    assert state.admission._closed and state.queue._closed_deadline == 10.2
    assert state.cache['preflight_drain_deadline'] == 10.2
    assert state.queue._active is None and state.queue.unresolved is False
    assert state.left.fileno() >= 0
    assert all(receipt[key] is False for key in ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


def assert_sticky_refusal(state, match):
    with pytest.raises(handshake.HandshakeError, match=match):
        handshake.backend_execution_service(state.event)
    assert len(state.validates) == state.terminal_after
    assert len(state.reads) == len(state.frames) and len(state.sends) == 1
    assert state.queue.unresolved and state.left.fileno() == -1
    assert 'cpu_producer_unconfirmed' in state.admission.snapshot()['unsupported']
    assert state.sends[0]['can_release_launch_lease'] is False


@pytest.mark.parametrize('damage', ['none', 'subclass', 'duck'])
def test_terminal_closing_requires_original_exact_stop_event(original_terminal, damage):
    state = original_terminal
    if damage == 'none':
        state.event = None
    elif damage == 'subclass':
        class EventSubclass(threading.Event):
            pass
        state.event = EventSubclass()
    else:
        state.event = SimpleNamespace(is_set=lambda: False, wait=lambda seconds: True)
    assert_sticky_refusal(state, 'closing context changed')
    assert state.waits == []


@pytest.mark.parametrize('timing', ['at_terminal', 'wait_false', 'wait_true'])
def test_original_stop_cannot_renew_or_cross_admitted_terminal_deadline(original_terminal, timing):
    state = original_terminal
    state.auto_stop = False
    def expire():
        state.clock.now = 10.2
        if timing == 'wait_true':
            state.event.set()
    if timing == 'at_terminal':
        state.on_terminal = expire
    else:
        state.on_wait = expire
    assert_sticky_refusal(state, 'closing deadline expired')
    assert state.queue._closed_deadline == state.cache['preflight_drain_deadline'] == 10.2


@pytest.mark.parametrize('damage', ['cache', 'context', 'root', 'ready', 'proof', 'challenge',
                                   'socket', 'queue', 'admission', 'drain_deadline', 'queue_deadline',
                                   'reader', 'socket_closed', 'socket_inheritable', 'queue_unresolved'])
@pytest.mark.parametrize('stop_set', [False, True])
def test_terminal_wait_freshness_damage_never_dispatches_or_returns(original_terminal, monkeypatch, damage, stop_set):
    state = original_terminal
    state.admission.uncovered('accepted_background_work')
    foreign_sockets = []
    def replace():
        if stop_set:
            state.event.set()
        if damage == 'cache':
            monkeypatch.setattr(handshake, '_CACHE', dict(state.cache))
        elif damage == 'context':
            monkeypatch.setattr(handshake, '_context', lambda: ('foreign-context',))
        elif damage == 'root':
            state.cache['root'] = state.cache['root']/'foreign'
        elif damage == 'ready':
            state.cache['ready'] = False
        elif damage in {'proof', 'challenge'}:
            state.cache[damage] = {**state.cache[damage], 'foreign': True}
        elif damage == 'socket':
            a, b = socket.socketpair()
            foreign_sockets.extend((a, b))
            state.cache['socket'] = a
        elif damage == 'queue':
            state.cache['preflight_queue'] = relay.BackendRelayQueue(state.left)
        elif damage == 'admission':
            state.cache['admission'] = handshake.BackendWorkAdmission()
        elif damage == 'drain_deadline':
            state.cache['preflight_drain_deadline'] += .01
        elif damage == 'queue_deadline':
            state.queue._closed_deadline += .01
        elif damage == 'reader':
            state.queue._reader = object()
        elif damage == 'socket_closed':
            state.left.close()
        elif damage == 'socket_inheritable':
            state.left.set_inheritable(True)
        elif damage == 'queue_unresolved':
            state.queue.unresolved = True
    state.on_wait = replace
    try:
        assert_sticky_refusal(state, 'closing context changed|reader/channel|private channel|channel is unavailable')
        assert state.admission.snapshot()['unsupported'] == ['accepted_background_work', 'cpu_producer_unconfirmed']
        if damage == 'admission':
            assert state.cache['admission'].snapshot() == {'active_scopes': 0, 'unsupported': []}
    finally:
        for value in foreign_sockets:
            value.close()


@pytest.mark.parametrize('when', ['before_stop', 'with_stop'])
def test_late_original_child_finish_is_retained_and_cannot_make_terminal_closing_succeed(original_terminal, when):
    state = original_terminal
    state.admission.uncovered('accepted_background_work')
    tokens = []
    def enqueue():
        if tokens:
            return
        tokens.append(state.queue.enqueue({'kind': 'backend_preflight_request', 'action': 'finish',
                                          'request_id': '3'*32}, deadline=10.2))
        if when == 'with_stop':
            state.event.set()
    state.on_wait = enqueue
    assert_sticky_refusal(state, 'closing still has accepted work')
    assert state.queue._active is tokens[0]
    exchange = relay._EXCHANGES[tokens[0]]
    assert not exchange['taken'] and not exchange['done'] and not exchange['used']
    assert exchange['answer'] is None and exchange['deadline'] == 10.2
    assert exchange['error'] is not None


def test_active_original_producer_count_is_never_dropped_for_terminal_closing(original_terminal):
    state = original_terminal
    state.admission._begin_producer()
    state.admission.uncovered('accepted_background_work')
    assert_sticky_refusal(state, 'closing still has accepted work')
    assert state.waits == [] and state.admission.snapshot()['active_scopes'] == 1
    assert state.sends[0]['active_scopes'] == 1 and state.sends[0]['status'] == 'refused'


def test_pending_original_cpu_callback_cannot_enter_terminal_closing(original_terminal, monkeypatch):
    state = original_terminal
    starts = []
    class InertOriginalCallback:
        def __init__(self, *, target, name, daemon):
            assert name == 'owned-controller-cpu' and daemon is True
            self.target = target
        def start(self):
            starts.append(self)
    monkeypatch.setattr(handshake.threading, 'Thread', InertOriginalCallback)
    state.frames = [{'kind': 'controller_cpu_execute'}, state.frame]
    state.terminal_after = 6
    state.admission.uncovered('accepted_background_work')
    assert_sticky_refusal(state, 'closing still has accepted work')
    assert len(starts) == 1 and state.waits == []
    retained = state.cache['original_cpu_execution']
    assert retained['thread'] is starts[0] and retained['done'].is_set() is False
    assert retained['result'] is None and retained['error'] is None and retained['deadline'] == 220.0


@pytest.mark.parametrize('damage', ['reopened', 'unsupported_cleared'])
def test_terminal_original_admission_cannot_be_reopened_or_clear_unsupported(original_terminal, damage):
    state = original_terminal
    state.admission.uncovered('accepted_background_work')
    def damage_state():
        state.event.set()
        if damage == 'reopened':
            state.admission._closed = False
        else:
            state.admission._unsupported.clear()
    state.on_wait = damage_state
    assert_sticky_refusal(state, 'closing still has accepted work')
    assert state.sends[0]['unsupported'] == ['accepted_background_work']


@pytest.mark.parametrize('unsupported', [False, True])
def test_original_event_already_set_after_validated_terminal_has_no_extra_read(original_terminal, unsupported):
    state = original_terminal
    if unsupported:
        state.admission.uncovered('accepted_background_work')
    before = state.admission.snapshot()
    state.on_terminal = state.event.set
    handshake.backend_execution_service(state.event)
    assert state.waits == [] and state.admission.snapshot() == before
    assert len(state.validates) == 4 and len(state.reads) == 1 and len(state.sends) == 1
    assert state.queue.unresolved is False and state.left.fileno() >= 0
