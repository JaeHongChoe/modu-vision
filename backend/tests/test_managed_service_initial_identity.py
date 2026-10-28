"""Initial-child observation only: modeled clock/processes, no real worker.

An empty argv never authorizes ownership. These controls demonstrate the
bounded startup contract, not the cause of any historical hosted failure.
Producer contract: backend/engine/managed_service.py.
"""
from types import SimpleNamespace

import psutil
import pytest

from backend.engine import managed_service as service
from backend.engine import runtime_process_control as control


@pytest.fixture
def child(monkeypatch, tmp_path):
    state = tmp_path / 'state'
    arguments = ['python', '-m', 'backend.engine.inspection_service',
                 '--state-dir', str(state), '--port', '12345']
    value = dict(now=100.0, argv=[arguments], birth=42.5,
                 parent=service.os.getpid(), status=psutil.STATUS_RUNNING,
                 exit=None, observation_error=None, query_cost=0.0)
    events = []
    owners = []

    def observe(name):
        events.append(name)
        value['now'] += value['query_cost']
        if value['observation_error'] is not None:
            raise value['observation_error']
        return value[name]

    def cmdline():
        events.append('argv')
        vector = value['argv'].pop(0) if len(value['argv']) > 1 else value['argv'][0]
        return vector

    def owner(pid):
        assert pid == 731
        instance = SimpleNamespace(pid=pid, create_time=lambda: observe('birth'),
                                   ppid=lambda: observe('parent'), status=lambda: observe('status'),
                                   cmdline=cmdline)
        owners.append(instance)
        return instance

    def sleep(seconds):
        assert 0 < seconds <= .1
        events.append(('sleep', seconds))
        value['now'] += seconds

    process = SimpleNamespace(pid=731, args=arguments, poll=lambda: observe('exit'))
    monkeypatch.setattr(service.psutil, 'Process', owner)
    monkeypatch.setattr(service.time, 'monotonic', lambda: value['now'])
    monkeypatch.setattr(service.time, 'sleep', sleep)
    return SimpleNamespace(state=state, arguments=arguments, value=value,
                           events=events, owners=owners, process=process)


def identify(child, deadline=120.0):
    return service._initial_process_identity(child.process, child.state,
                                            control.command_sha256(child.arguments), service.os.getpid(), deadline)


@pytest.mark.parametrize('empty_count', [0, 1, 3])
def test_only_strict_actual_command_becomes_identity_after_empty_observations(child, empty_count):
    child.value['argv'] = [[] for _ in range(empty_count)] + [child.arguments]
    actual = identify(child)
    assert actual == dict(pid=731, process_created_at=42.5,
                         process_command_sha256=control.command_sha256(child.arguments))
    assert sum(isinstance(event, tuple) for event in child.events) == empty_count
    assert len({id(owner) for owner in child.owners}) == len(child.owners)
    assert child.value['now'] < 120.0


def test_empty_forever_is_unverified_and_consumes_only_original_deadline(child):
    child.value['argv'] = [[]]
    with pytest.raises(TimeoutError, match='identity') as caught:
        identify(child, 100.25)
    assert child.value['now'] == pytest.approx(100.25)
    assert isinstance(caught.value.__cause__, ValueError)
    assert caught.value.__cause__.runtime_spawn_diagnostic['ownership_verified'] is False
    assert child.events.count('argv') == 3


@pytest.mark.parametrize('fault', ['entry', 'state', 'duplicate-state', 'other-port'])
def test_nonempty_wrong_command_is_not_observed_again(child, fault):
    command = list(child.arguments)
    if fault == 'entry': command[2] = 'foreign.module'
    elif fault == 'state': command[4] = '/foreign-state'
    elif fault == 'duplicate-state': command += ['--state-dir', str(child.state)]
    else: command[-1] = '54321'  # Valid old state predicate, different actual full command.
    child.value['argv'] = [command, child.arguments]
    with pytest.raises(ValueError): identify(child)
    assert child.events.count('argv') == 1
    assert not any(isinstance(event, tuple) for event in child.events)


@pytest.mark.parametrize('fault', ['exit', 'zombie', 'dead', 'parent', 'birth-shape', 'pid'])
def test_fresh_child_refusals_cannot_authorize_or_wait(child, fault):
    if fault == 'exit': child.value['exit'] = 0
    elif fault == 'zombie': child.value['status'] = psutil.STATUS_ZOMBIE
    elif fault == 'dead': child.value['status'] = psutil.STATUS_DEAD
    elif fault == 'parent': child.value['parent'] += 1
    elif fault == 'birth-shape': child.value['birth'] = float('nan')
    else: child.process.pid = True
    with pytest.raises((ValueError, RuntimeError)): identify(child)
    assert 'argv' not in child.events
    assert not any(isinstance(event, tuple) for event in child.events)


@pytest.mark.parametrize('error', [psutil.NoSuchProcess(731), psutil.AccessDenied(731),
                                 psutil.ZombieProcess(731)])
def test_missing_or_unreadable_process_refuses_without_observation_retry(child, error):
    child.value['observation_error'] = error
    with pytest.raises(type(error)): identify(child)
    assert not any(isinstance(event, tuple) for event in child.events)


@pytest.mark.parametrize('fault', ['birth', 'parent', 'exit', 'zombie'])
def test_pending_child_custody_change_refuses_before_second_command(child, monkeypatch, fault):
    child.value['argv'] = [[], child.arguments]
    original_sleep = service.time.sleep
    def change(seconds):
        original_sleep(seconds)
        key, value = {'birth': ('birth', 43.5), 'parent': ('parent', service.os.getpid() + 1),
                      'exit': ('exit', 1), 'zombie': ('status', psutil.STATUS_ZOMBIE)}[fault]
        child.value[key] = value
    monkeypatch.setattr(service.time, 'sleep', change)
    with pytest.raises((ValueError, RuntimeError)): identify(child)
    assert child.events.count('argv') == 1
    assert sum(isinstance(event, tuple) for event in child.events) == 1


def test_final_actual_identity_cannot_publish_after_deadline(child, monkeypatch):
    original = service.process_identity
    def late(process, state):
        actual = original(process, state)
        child.value['now'] = 120.0
        return actual
    monkeypatch.setattr(service, 'process_identity', late)
    with pytest.raises(TimeoutError, match='identity'): identify(child)


@pytest.mark.parametrize('fault', ['birth', 'parent', 'exit'])
def test_final_fresh_snapshot_rechecks_custody_after_strict_identity(child, monkeypatch, fault):
    original = service.process_identity
    def changed(process, state):
        actual = original(process, state)
        key, value = {'birth': ('birth', 43.5), 'parent': ('parent', service.os.getpid() + 1),
                      'exit': ('exit', 0)}[fault]
        child.value[key] = value
        return actual
    monkeypatch.setattr(service, 'process_identity', changed)
    with pytest.raises((ValueError, RuntimeError)): identify(child)


def test_budget_expired_during_snapshot_never_queries_command(child):
    child.value['query_cost'] = 21.0
    with pytest.raises(TimeoutError, match='identity'): identify(child)
    assert 'argv' not in child.events


def test_missing_or_malformed_empty_diagnostic_does_not_enable_wait(child, monkeypatch):
    def refused(*_): raise ValueError('Spawned runtime command differs from its owned state')
    monkeypatch.setattr(service, 'process_identity', refused)
    with pytest.raises(ValueError): identify(child)
    assert not any(isinstance(event, tuple) for event in child.events)


@pytest.mark.parametrize('fault', ['observed-hash', 'bool-argc', 'expected-hash', 'pid',
                                 'birth', 'verified', 'atomic', 'extra-observed-field'])
def test_forged_empty_diagnostic_is_a_hard_refusal(child, monkeypatch, fault):
    child.value['argv'] = [[]]
    try: control.process_identity(child.process, child.state)
    except ValueError as error: original = error
    row = original.runtime_spawn_diagnostic
    if fault == 'observed-hash': row['observed']['sha256'] = '0' * 64
    elif fault == 'bool-argc': row['observed']['argc'] = False
    elif fault == 'expected-hash': row['expected']['sha256'] = '0' * 64
    elif fault == 'pid': row['process']['pid'] += 1
    elif fault == 'birth': row['process']['birth'] += 1
    elif fault == 'verified': row['ownership_verified'] = True
    elif fault == 'atomic': row['non_atomic'] = False
    else: row['observed']['extra'] = 'not-canonical'
    def refused(*_): raise original
    monkeypatch.setattr(service, 'process_identity', refused)
    with pytest.raises(ValueError) as caught: identify(child)
    assert caught.value is original
    assert not any(isinstance(event, tuple) for event in child.events)


@pytest.fixture
def managed_start(child, monkeypatch, tmp_path):
    item = service.ManagedService(tmp_path)
    release = dict(package_path=str(tmp_path / 'synthetic-package'),
                   release_policy=str(tmp_path / 'synthetic-policy'), device='cpu')
    calls = dict(popen=0, save=[], readback=0, terminate=0, wait=[], stop=0, popen_cost=0)
    def popen(arguments, **kwargs):
        calls['popen'] += 1
        child.events.append('Popen')
        child.arguments[:] = arguments
        child.value['now'] += calls['popen_cost']
        return child.process
    def saved(config):
        child.events.append('save'); calls['save'].append(dict(config))
    def readback():
        child.events.append('readback'); calls['readback'] += 1
        return {'status': 'ready'}
    child.process.terminate = lambda: calls.update(terminate=calls['terminate'] + 1)
    child.process.wait = lambda *, timeout: calls['wait'].append(timeout)
    monkeypatch.setattr(item, 'validate_accepted_device', lambda *_a, **_k: None)
    monkeypatch.setattr(service, 'verify_flow_package', lambda _: ({}, {}))
    monkeypatch.setattr(service, '_verify_release_policy', lambda *_a, **_k: None)
    monkeypatch.setattr(service, 'resolve_runtime_device', lambda device: device)
    monkeypatch.setattr(item, 'owned_process', lambda: None)
    monkeypatch.setattr(item, 'input_arguments', lambda _: [])
    monkeypatch.setattr(service.subprocess, 'Popen', popen)
    monkeypatch.setattr(item, 'save', saved)
    monkeypatch.setattr(item, 'readback', readback)
    monkeypatch.setattr(item, 'stop', lambda: calls.update(stop=calls['stop'] + 1))
    return SimpleNamespace(item=item, release=release, calls=calls, child=child)


def test_start_saves_actual_identity_once_only_after_empty_metadata_is_verified(managed_start):
    start = managed_start; child = start.child
    child.value['argv'] = [[], [], child.arguments]
    assert start.item.start(start.release, recover=False) == {'status': 'ready'}
    assert start.calls['popen'] == 1 and start.calls['readback'] == 1
    assert len(start.calls['save']) == 1 and start.calls['terminate'] == 0
    assert start.calls['save'][0]['process_command_sha256'] == control.command_sha256(child.arguments)
    assert child.events.index('save') > max(i for i, value in enumerate(child.events) if value == 'argv')
    assert child.events.index('readback') > child.events.index('save')


@pytest.mark.parametrize('popen_cost', [0, 19.9, 21])
def test_start_has_one_total_budget_and_cleans_only_its_original_child(managed_start, popen_cost):
    start = managed_start; child = start.child
    start.calls['popen_cost'] = popen_cost
    child.value['argv'] = [[]]
    with pytest.raises(TimeoutError, match='identity'):
        start.item.start(start.release, recover=False)
    assert start.calls['popen'] == 1 and start.calls['readback'] == 0
    assert start.calls['save'] == [] and start.calls['terminate'] == 1
    assert start.calls['wait'] == [10] and start.calls['stop'] == 0
    assert child.value['now'] == pytest.approx(100.0 + max(20, popen_cost))
    assert start.item.config['pid'] is None
    if popen_cost > 20: assert 'argv' not in child.events


def test_start_does_not_reset_remaining_readiness_budget_or_accept_late_ready(managed_start, monkeypatch):
    start = managed_start; child = start.child
    start.calls['popen_cost'] = 19.9
    def late_ready():
        start.calls['readback'] += 1
        child.value['now'] = 120.0
        return {'status': 'ready'}
    monkeypatch.setattr(start.item, 'readback', late_ready)
    with pytest.raises(TimeoutError, match='readiness'):
        start.item.start(start.release, recover=False)
    assert start.calls['popen'] == 1 and start.calls['readback'] == 1
    assert len(start.calls['save']) == 1 and start.calls['stop'] == 1


def test_start_final_custody_loss_never_saves_or_reads_http(managed_start, monkeypatch):
    start = managed_start; child = start.child
    strict = service.process_identity
    def changed(process, state):
        actual = strict(process, state)
        child.value['parent'] += 1
        return actual
    monkeypatch.setattr(service, 'process_identity', changed)
    with pytest.raises(ValueError, match='parent'):
        start.item.start(start.release, recover=False)
    assert start.calls['save'] == [] and start.calls['readback'] == 0
    assert start.calls['popen'] == 1 and start.calls['terminate'] == 1
    assert start.calls['wait'] == [10]
