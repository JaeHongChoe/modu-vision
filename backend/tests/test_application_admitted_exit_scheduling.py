"""No-child scheduler controls; OS/authentication/storage are explicitly modeled.

The original controller run/receive/exit publication functions execute. These
controls cannot prove a real process exit, registry authority, or old race cause.
"""
from contextlib import nullcontext
from types import SimpleNamespace
import copy
import subprocess

import pytest

from backend.engine import application_launch_controller as controller
from backend.engine.application_launch_handshake import HandshakeError


MAIN = {'pid': 11001, 'created_at': 123.5, 'command_sha256': 'a' * 64}
BACKEND = {'pid': 11002, 'created_at': 124.5, 'command_sha256': 'b' * 64}
ORIGINAL_NODE_FRAME_VALUE = controller.node.frame_value


class LoopFinished(BaseException):
    pass


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


class Event:
    """Inert one-use event at the modeled authenticated transport boundary."""
    def __init__(self, frame):
        self.frame = frame
        self.used = False


def exit_frame():
    return {'schema_version': 1, 'kind': 'main_backend_exit', 'nonce': 'c' * 32,
        'epoch': 'd' * 32, 'request_id': 'e' * 32, 'challenge': 'f' * 64,
        'backend_process': copy.deepcopy(BACKEND), 'returncode': 0, 'signal': None}


def modeled_owner():
    frame = exit_frame()
    request = {key: frame[key] for key in ('nonce', 'epoch', 'request_id', 'challenge')}
    row = {'state': 'ready', 'nonce': frame['nonce'], 'process': copy.deepcopy(MAIN),
        'writer_drain': {'phase': 'drained', 'request': request,
            'receipt': {'status': 'managed_scopes_drained'}, 'writer_id': '1' * 32,
            'backend_exit': None}}
    owner = SimpleNamespace(root=None, nonce=frame['nonce'], _bootstrap_channel=object(),
        _node_backend_authority=object(), _authenticated_backend_proof={'process': BACKEND},
        _owned=lambda: row, _binding=lambda value: None, _live=lambda value, process: None)
    owner.row = row
    return owner


@pytest.fixture
def inert_boundary(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(controller.time, 'monotonic', clock.monotonic)
    def forbidden(*args, **kwargs):
        raise AssertionError('No actual child or process action is allowed')
    monkeypatch.setattr(subprocess.Popen, '__init__', forbidden)
    monkeypatch.setattr(controller.lease, '_transition_admission', lambda *args: nullcontext())
    def frame_value(authority, event):
        if type(event) is not Event or event.used:
            raise HandshakeError('Modeled original one-use event differs or replayed')
        return copy.deepcopy(event.frame)
    monkeypatch.setattr(controller.node, 'frame_value', frame_value)
    return clock


def install_run_boundary(monkeypatch, tmp_path, clock, *, admitted=True, already_changed=False,
                         receive_error=None, receive_advance=0.0, exit_kind='main_backend_exit'):
    owner = modeled_owner(); owner.root = tmp_path
    owner.row['state'] = 'starting'; owner.row['writer_drain']['phase'] = 'enrolled'
    trace = []
    first = Event({'kind': 'main_drain_request'})
    later = Event({**exit_frame(), 'kind': exit_kind})
    ready = [first]
    original = owner._node_backend_authority
    process = SimpleNamespace(pid=MAIN['pid'], poll=lambda: None)
    owner._process = process
    owner.enroll_backend_writer = lambda: None
    owner.start = lambda **kwargs: owner.row
    owner.recovery = lambda reason: trace.append(('recovery', reason))
    owner.observe_exit = lambda: trace.append(('main_exit',))
    binding = {'installation_id': '2' * 32, 'update_id': '3' * 32,
        'database_pointer': {'fence': 1}}
    monkeypatch.setattr(controller.update, '_root', lambda root: (tmp_path, None))
    monkeypatch.setattr(controller.update, '_launch_binding', lambda *args, **kwargs: binding)
    monkeypatch.setattr(controller, 'store_admission', lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(controller, '_expected', lambda *args: None)
    monkeypatch.setattr(controller, '_cpu_capability', lambda *args: None)
    monkeypatch.setattr(controller.lease.LaunchSupervisor, 'reserve', lambda *args, **kwargs: owner)
    monkeypatch.setattr(controller.node, '_mint_original_controller', lambda *args: original)
    monkeypatch.setattr(controller.node, 'capture_original_main', lambda *args: None)
    monkeypatch.setattr(controller, '_emit', lambda *args: None)
    monkeypatch.setattr(controller, 'authenticate', lambda *args: BACKEND)
    monkeypatch.setattr(controller.select, 'select', lambda *args: ([owner._bootstrap_channel] if ready else [], [], []))
    def receive(authority, timeout):
        assert authority is original
        trace.append(('receive', timeout))
        if ready:
            return ready.pop(0)
        if receive_error:
            raise receive_error
        clock.now += receive_advance
        return later
    monkeypatch.setattr(controller.node, 'receive_frame', receive)
    def prepare(value, frame):
        trace.append(('prepare',))
        if not admitted:
            raise HandshakeError('Backend managed scope retains uncovered/active writers')
        value.row['writer_drain']['phase'] = 'drained'
        return 104.0
    monkeypatch.setattr(controller, 'prepare_drain', prepare)
    def observe(value, event, *, absolute_deadline=None):
        assert event is later and not event.used
        assert absolute_deadline == 104.0 and clock.now < absolute_deadline
        event.used = True
        value.row['writer_drain']['phase'] = 'backend_exited'
        trace.append(('exact_exit', absolute_deadline))
    monkeypatch.setattr(controller, 'observe_backend_exit', observe)
    def identity(expected, pid):
        if pid == MAIN['pid']:
            return True
        trace.append(('backend_birth_check',))
        return not (already_changed or owner.row['writer_drain']['phase'] == 'drained')
    monkeypatch.setattr(controller, '_same_identity', identity)
    monkeypatch.setattr(controller.psutil, 'Process', lambda pid: SimpleNamespace(ppid=lambda: MAIN['pid']))
    monkeypatch.setattr(controller.time, 'sleep', lambda seconds: (_ for _ in ()).throw(LoopFinished()))
    args = SimpleNamespace(root=str(tmp_path), authority='owned', pinned_authority_sha256='4' * 64)
    return args, owner, trace, ready, later


def test_original_run_consumes_admitted_exit_before_backend_birth_poll(inert_boundary, monkeypatch, tmp_path):
    args, owner, trace, _, event = install_run_boundary(monkeypatch, tmp_path, inert_boundary)
    with pytest.raises(LoopFinished):
        controller.run(args)
    assert event.used
    assert owner.row['writer_drain']['phase'] == 'backend_exited'
    assert trace == [('receive', 4), ('prepare',), ('receive', 4.0), ('exact_exit', 104.0)]


def test_original_ready_backend_change_without_admitted_drain_still_refuses(inert_boundary, monkeypatch, tmp_path):
    args, _, trace, ready, event = install_run_boundary(monkeypatch, tmp_path, inert_boundary, already_changed=True)
    ready.clear()
    with pytest.raises(LoopFinished):
        controller.run(args)
    assert not event.used
    assert trace == [('backend_birth_check',), ('recovery', 'Process-tree ownership is unresolved: Authenticated backend ownership changed')]


def test_original_refused_drain_never_waits_for_or_adopts_exit(inert_boundary, monkeypatch, tmp_path):
    args, _, trace, _, event = install_run_boundary(monkeypatch, tmp_path, inert_boundary, admitted=False)
    with pytest.raises(LoopFinished):
        controller.run(args)
    assert not event.used
    assert trace == [('receive', 4), ('prepare',), ('recovery', 'Process-tree ownership is unresolved: Backend managed scope retains uncovered/active writers')]


@pytest.mark.parametrize('advance,error,kind', [(4.0, None, 'main_backend_exit'),
    (0.0, HandshakeError('Private descriptor timeout before complete frame'), 'main_backend_exit'),
    (0.0, HandshakeError('Original main/channel/binding changed'), 'main_backend_exit'),
    (0.0, None, 'main_drain_request')])
def test_original_admitted_wait_refuses_late_absent_invalid_and_wrong_event(inert_boundary, monkeypatch, tmp_path, advance, error, kind):
    args, owner, trace, _, event = install_run_boundary(monkeypatch, tmp_path, inert_boundary,
        receive_error=error, receive_advance=advance, exit_kind=kind)
    with pytest.raises(LoopFinished):
        controller.run(args)
    assert not event.used and owner.row['writer_drain']['phase'] == 'drained'
    assert any(row[0] == 'recovery' for row in trace)
    assert not any(row[0] in ('backend_birth_check', 'exact_exit') for row in trace)


def direct_wait(monkeypatch, owner, events):
    read = []
    def receive(authority, timeout):
        assert authority is owner._node_backend_authority
        read.append(timeout)
        return events.pop(0)
    monkeypatch.setattr(controller.node, 'receive_frame', receive)
    return read


def test_existing_original_preflight_finish_keeps_same_remaining_exit_bound(inert_boundary, monkeypatch):
    owner = modeled_owner()
    finish = Event({'kind': 'main_preflight_request', 'request': {'action': 'finish'}})
    exit_event = Event(exit_frame()); read = direct_wait(monkeypatch, owner, [finish, exit_event])
    used = []
    def dispatch(value, event):
        assert value is owner and event is finish and not event.used
        event.used = True; used.append(event); inert_boundary.now += 1.5
    monkeypatch.setattr(controller, '_preflight_controller_event', dispatch)
    actual = controller._receive_admitted_backend_exit(owner, 104.0)
    assert actual is exit_event and used == [finish] and read == [4.0, 2.5]


@pytest.mark.parametrize('action', ['reserve', 'bind', 'unknown'])
def test_admitted_exit_wait_never_admits_new_preflight_registration(inert_boundary, monkeypatch, action):
    owner = modeled_owner(); event = Event({'kind': 'main_preflight_request', 'request': {'action': action}})
    direct_wait(monkeypatch, owner, [event])
    monkeypatch.setattr(controller, '_preflight_controller_event', lambda *args: pytest.fail('New registration entered'))
    with pytest.raises(HandshakeError):
        controller._receive_admitted_backend_exit(owner, 104.0)
    assert not event.used


@pytest.mark.parametrize('phase,status', [('closing', 'managed_scopes_drained'), ('drained', 'refused'), ('backend_exited', 'managed_scopes_drained')])
def test_no_typed_receive_without_original_admitted_drain(inert_boundary, monkeypatch, phase, status):
    owner = modeled_owner(); owner.row['writer_drain']['phase'] = phase; owner.row['writer_drain']['receipt']['status'] = status
    monkeypatch.setattr(controller.node, 'receive_frame', lambda *args: pytest.fail('Unadmitted receive entered'))
    with pytest.raises(HandshakeError):
        controller._receive_admitted_backend_exit(owner, 104.0)


@pytest.mark.parametrize('deadline', [100.0, 99.0, 105.0, float('nan'), float('inf'), True])
def test_original_exit_wait_never_renews_invalid_or_expired_bound(inert_boundary, monkeypatch, deadline):
    owner = modeled_owner()
    monkeypatch.setattr(controller.node, 'receive_frame', lambda *args: pytest.fail('Invalid bounded receive entered'))
    with pytest.raises(HandshakeError):
        controller._receive_admitted_backend_exit(owner, deadline)


def install_exit_publication(monkeypatch, owner, clock, *, publication_advance=0.0):
    observed = []
    def observe(writer_id, event, **kwargs):
        if event.used:
            raise HandshakeError('Modeled original event replayed')
        event.used = True; observed.append(('core_exit',))
    owner._writer_epoch = SimpleNamespace(observe_authenticated_node_backend_exit=observe,
        snapshot=lambda: {'registry_sha256': '5' * 64})
    def publish(**kwargs):
        assert kwargs['phase'] == 'backend_exited'
        owner.row['writer_drain'].update(kwargs); observed.append(('publish',)); clock.now += publication_advance
    owner.publish_writer_drain = publish
    monkeypatch.setattr(controller.node, 'assert_exit_acknowledgement', lambda authority: observed.append(('ack_authority',)))
    def send(channel, frame, *, absolute_deadline=None):
        assert channel is owner._bootstrap_channel
        observed.append(('ack_send', absolute_deadline, frame['kind']))
    monkeypatch.setattr(controller, 'send_frame', send)
    return observed


def test_original_exit_ack_send_uses_same_absolute_drain_deadline(inert_boundary, monkeypatch):
    owner = modeled_owner(); event = Event(exit_frame())
    observed = install_exit_publication(monkeypatch, owner, inert_boundary)
    controller.observe_backend_exit(owner, event, absolute_deadline=104.0)
    assert event.used
    assert observed == [('core_exit',), ('publish',), ('ack_authority',), ('ack_send', 104.0, 'backend_exit_observed')]


def test_deadline_crossing_after_original_consumption_keeps_publication_no_ack_or_replay(inert_boundary, monkeypatch):
    owner = modeled_owner(); event = Event(exit_frame())
    observed = install_exit_publication(monkeypatch, owner, inert_boundary, publication_advance=4.0)
    with pytest.raises(HandshakeError):
        controller.observe_backend_exit(owner, event, absolute_deadline=104.0)
    assert event.used and owner.row['writer_drain']['phase'] == 'backend_exited'
    assert observed == [('core_exit',), ('publish',)]
    with pytest.raises(HandshakeError):
        controller.observe_backend_exit(owner, event, absolute_deadline=104.0)
    assert observed == [('core_exit',), ('publish',)]


@pytest.mark.parametrize('key,value', [('nonce', '0' * 32), ('returncode', 1), ('signal', 'SIGTERM'), ('backend_process', MAIN)])
def test_real_exit_frame_validator_refuses_changed_witness_without_consumption(inert_boundary, monkeypatch, key, value):
    owner = modeled_owner(); frame = exit_frame(); frame[key] = value; event = Event(frame)
    observed = install_exit_publication(monkeypatch, owner, inert_boundary)
    with pytest.raises(HandshakeError):
        controller.observe_backend_exit(owner, event, absolute_deadline=104.0)
    assert not event.used and observed == [] and owner.row['writer_drain']['backend_exit'] is None


def install_original_finish_dispatch(monkeypatch, owner, clock, *, producer_deadline=160.0,
                                     drain_budget_ms=4000, finish_advance=0.0,
                                     send_advance=0.0, expected_send_deadline=104.0):
    """Execute original dispatcher and relay; OS/channel/core CAS are modeled.

    Typed tables are explicitly initialized as an already admitted active row;
    they are not authenticated registration or real child proof. Original Node
    begin_drain/_state/consume and relay request/phase/deadline/sticky code run.
    """
    import os
    import threading
    import weakref
    from backend.engine import application_preflight_child_relay as relay
    monkeypatch.setattr(relay, '_CONTROLLERS', weakref.WeakKeyDictionary())
    monkeypatch.setattr(relay, '_ACTIVE', set())
    monkeypatch.setattr(relay, '_UNRESOLVED', set())
    monkeypatch.setattr(controller.node, '_AUTHORITIES', weakref.WeakKeyDictionary())
    monkeypatch.setattr(controller.node, '_EVENTS', weakref.WeakKeyDictionary())
    authority = controller.node._new(controller.node.NodeBackendAuthority)
    owner._node_backend_authority = authority
    cap = relay._new(relay.ControllerPreflightRelay)
    proof = {'nonce': owner.nonce, 'epoch': 'd' * 32, 'binding_sha256': 'a' * 64,
             'process': copy.deepcopy(BACKEND)}
    identifier = '2' * 32
    registration = SimpleNamespace(writer_id='3' * 32, registration_sha256='4' * 64)
    plan = {'deadline_monotonic': producer_deadline}
    trace = []
    def observe(writer_id, capability, **kwargs):
        assert writer_id == registration.writer_id and capability is cap
        assert relay._CONTROLLERS[cap]['phase'] == 'finishing'
        trace.append(('original_finish_publication',))
        clock.now += finish_advance
    epoch = SimpleNamespace(_original=lambda: None, observe_authenticated_preflight_child_exit=observe,
        snapshot=lambda: {'registry_sha256': '5' * 64})
    owner._writer_epoch = epoch; owner._preflight_relays = {identifier: cap}
    state = {'pid': os.getpid(), 'thread': threading.current_thread(), 'owner': owner,
        'epoch': epoch, 'node': owner._node_backend_authority, 'proof': proof,
        'request_id': identifier, 'plan': plan, 'phase': 'active', 'failed': False,
        'registration': registration, 'child': copy.deepcopy(BACKEND)}
    relay._CONTROLLERS[cap] = state; relay._ACTIVE.add(cap)
    request = {'schema_version': 1, 'kind': 'backend_preflight_request', 'action': 'finish',
        'nonce': proof['nonce'], 'epoch': proof['epoch'], 'binding_sha256': proof['binding_sha256'],
        'backend_claim_sha256': relay._sha(proof), 'request_id': identifier,
        'payload': {'registration': {'writer_id': registration.writer_id,
            'registration_sha256': registration.registration_sha256}, 'child': copy.deepcopy(BACKEND),
            'plan_sha256': relay._sha(plan), 'returncode': 0, 'cleanup_confirmed': True}}
    event = controller.node._new(controller.node.NodeBackendEvent)
    controller.node._EVENTS[event] = {'authority': authority, 'used': False,
        'frame': {'schema_version': 1, 'kind': 'main_preflight_request', 'nonce': owner.nonce, 'request': request}}
    original_state = {'owner': owner, 'epoch': epoch, 'pid': os.getpid(),
        'thread': threading.current_thread(), 'failed': False, 'phase': 'bound',
        'deadline': None, 'channel': owner._bootstrap_channel, 'proof': proof,
        'main': SimpleNamespace(pid=MAIN['pid'])}
    controller.node._AUTHORITIES[authority] = original_state
    # Only OS/channel liveness and core persistence are modeled. The original
    # Node typed table checks and original drain deadline are never bypassed.
    monkeypatch.setattr(controller.node, '_fresh', lambda value: (controller.node._state(value), owner.row))
    monkeypatch.setattr(controller.node, 'frame_value', ORIGINAL_NODE_FRAME_VALUE)
    monkeypatch.setattr(controller.node, '_parent_pid', lambda pid: MAIN['pid'])
    monkeypatch.setattr(controller.lease, '_identity', lambda pid: copy.deepcopy(BACKEND))
    owner.row['writer_drain']['phase'] = 'enrolled'
    controller.node.begin_drain(authority, {'budget_ms': drain_budget_ms}, 100.0)
    owner.row['writer_drain']['phase'] = 'drained'
    assert controller.node._state(authority)['deadline'] == 100.0+drain_budget_ms/1000
    def send(channel, frame, *, absolute_deadline=None):
        assert channel is owner._bootstrap_channel
        assert frame == relay._reply(request, {'status': 'direct_exited'})
        trace.append(('original_finish_ACK', absolute_deadline))
        assert absolute_deadline == expected_send_deadline
        clock.now += send_advance
    monkeypatch.setattr(controller, 'send_frame', send)
    return relay, cap, state, event, trace


def original_node_event(owner, frame):
    value = controller.node._new(controller.node.NodeBackendEvent)
    controller.node._EVENTS[value] = {'authority': owner._node_backend_authority,
        'used': False, 'frame': copy.deepcopy(frame)}
    return value


@pytest.mark.parametrize('start', [100.0, 103.75])
def test_original_finish_ACK_is_bounded_by_admitted_drain_not_sixty_second_plan(inert_boundary, monkeypatch, start):
    owner = modeled_owner(); inert_boundary.now = start
    relay, cap, state, finish, trace = install_original_finish_dispatch(monkeypatch, owner, inert_boundary)
    exit_event = original_node_event(owner, exit_frame()); read = direct_wait(monkeypatch, owner, [finish, exit_event])
    assert controller._receive_admitted_backend_exit(owner, 104.0) is exit_event
    assert controller.node._EVENTS[finish]['used'] and not controller.node._EVENTS[exit_event]['used'] and state['phase'] == 'finished'
    assert cap not in relay._ACTIVE and cap not in relay._UNRESOLVED
    assert trace == [('original_finish_publication',), ('original_finish_ACK', 104.0)]
    assert read == [104.0-start, 104.0-start]


@pytest.mark.parametrize('producer,budget,expected', [(102.0, 4000, 102.0), (160.0, 3000, 103.0)])
def test_original_finish_ACK_keeps_shorter_producer_or_Node_bound(inert_boundary, monkeypatch, producer, budget, expected):
    owner = modeled_owner()
    relay, cap, state, finish, trace = install_original_finish_dispatch(monkeypatch, owner, inert_boundary,
        producer_deadline=producer, drain_budget_ms=budget, expected_send_deadline=expected)
    direct_wait(monkeypatch, owner, [finish, original_node_event(owner, exit_frame())])
    controller._receive_admitted_backend_exit(owner, 104.0)
    assert trace[-1] == ('original_finish_ACK', expected) and cap not in relay._UNRESOLVED


def test_original_finish_publication_crossing_drain_bound_retains_without_ACK(inert_boundary, monkeypatch):
    owner = modeled_owner()
    relay, cap, state, finish, trace = install_original_finish_dispatch(monkeypatch, owner, inert_boundary, finish_advance=4.0)
    direct_wait(monkeypatch, owner, [finish])
    with pytest.raises(HandshakeError): controller._receive_admitted_backend_exit(owner, 104.0)
    assert controller.node._EVENTS[finish]['used'] and state['phase'] == 'finished' and state['failed']
    assert cap in relay._UNRESOLVED and cap in relay._ACTIVE
    assert trace == [('original_finish_publication',)]


def test_original_finish_ACK_returning_after_drain_bound_retains_original_row(inert_boundary, monkeypatch):
    owner = modeled_owner()
    relay, cap, state, finish, trace = install_original_finish_dispatch(monkeypatch, owner, inert_boundary, send_advance=4.0)
    direct_wait(monkeypatch, owner, [finish])
    with pytest.raises(HandshakeError): controller._receive_admitted_backend_exit(owner, 104.0)
    assert controller.node._EVENTS[finish]['used'] and state['phase'] == 'finished' and state['failed']
    assert cap in relay._UNRESOLVED and cap not in relay._ACTIVE
    assert trace == [('original_finish_publication',), ('original_finish_ACK', 104.0)]


@pytest.mark.parametrize('outer,expected', [(104.0, 104.0), (310.0, 104.0)])
def test_original_controller_reply_dispatch_keeps_CPU_or_drain_outer_bound(inert_boundary, monkeypatch, outer, expected):
    owner = modeled_owner()
    relay, cap, state, finish, trace = install_original_finish_dispatch(monkeypatch, owner, inert_boundary, expected_send_deadline=expected)
    reply = original_node_event(owner, {'kind': 'managed_drain_proof'})
    direct_wait(monkeypatch, owner, [finish, reply])
    assert controller._controller_reply(owner, outer, 'managed_drain_proof') == {'kind': 'managed_drain_proof'}
    assert trace[-1] == ('original_finish_ACK', expected) and cap not in relay._UNRESOLVED


def test_original_dispatch_refuses_nonactive_finish_without_send_or_adoption(inert_boundary, monkeypatch):
    owner = modeled_owner()
    relay, cap, state, finish, trace = install_original_finish_dispatch(monkeypatch, owner, inert_boundary)
    state['phase'] = 'reserved'
    direct_wait(monkeypatch, owner, [finish])
    with pytest.raises(HandshakeError): controller._receive_admitted_backend_exit(owner, 104.0)
    assert controller.node._EVENTS[finish]['used'] and state['phase'] == 'reserved' and state['failed']
    assert cap in relay._UNRESOLVED and trace == []


@pytest.mark.parametrize('action', ['reserve', 'bind'])
def test_original_Node_drain_refuses_new_registration_before_receive_consumption(inert_boundary, monkeypatch, action):
    owner = modeled_owner()
    relay, cap, state, finish, trace = install_original_finish_dispatch(monkeypatch, owner, inert_boundary)
    controller.node._EVENTS[finish]['frame']['request']['action'] = action
    with pytest.raises(HandshakeError): controller._preflight_controller_event(owner, finish)
    assert not controller.node._EVENTS[finish]['used'] and trace == [] and state['phase'] == 'active'
    assert cap in relay._UNRESOLVED and state['failed']


def test_original_Node_drain_deadline_cannot_restart_or_renew(inert_boundary, monkeypatch):
    owner = modeled_owner()
    install_original_finish_dispatch(monkeypatch, owner, inert_boundary)
    before = controller.node._state(owner._node_backend_authority)['deadline']
    owner.row['writer_drain']['phase'] = 'enrolled'; inert_boundary.now += 1
    with pytest.raises(HandshakeError): controller.node.begin_drain(owner._node_backend_authority, {'budget_ms': 4000}, inert_boundary.now)
    assert controller.node._state(owner._node_backend_authority)['deadline'] == before == 104.0
