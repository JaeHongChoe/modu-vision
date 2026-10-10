"""Mismatch diagnostics only; OS observations are modeled, no process is spawned.

The real production predicate and original ValueError run before every new
assertion. This cannot reproduce or qualify the hosted Linux CI failure.
"""
import json
from types import SimpleNamespace

import pytest

from backend.engine import runtime_process_control as control


MESSAGE = 'Spawned runtime command differs from its owned state'


def command(state, frozen=False):
    entry = ['python', '--inspection-service'] if frozen else ['python', '-m', 'backend.engine.inspection_service']
    return entry + ['--state-dir', str(state)]


@pytest.fixture
def observation(monkeypatch, tmp_path):
    state = tmp_path / 'private-state'
    counts = {'cmdline': 0, 'birth': 0, 'status': 0, 'poll': 0}
    value = {'observed': command(state), 'expected': command(state), 'birth': 42.5,
             'status': 'sleeping', 'poll': None, 'status_error': None, 'birth_error': None, 'poll_error': None}
    def read(key):
        counts[key] += 1
        fault = value.get(key + '_error')
        if fault is not None:
            raise fault
        return value[{'cmdline': 'observed', 'birth': 'birth'}.get(key, key)]
    owner = SimpleNamespace(pid=731, cmdline=lambda: read('cmdline'), create_time=lambda: read('birth'), status=lambda: read('status'))
    process = SimpleNamespace(pid=731, args=value['expected'], poll=lambda: read('poll'))
    monkeypatch.setattr(control.psutil, 'Process', lambda pid: owner if pid == 731 else pytest.fail('Unexpected PID observation'))
    return SimpleNamespace(state=state, process=process, owner=owner, value=value, counts=counts)


@pytest.mark.parametrize('fault', ['empty', 'wrong-entry', 'wrong-state', 'duplicate-state'])
def test_original_mismatch_refusal_has_bounded_safe_observation_without_retry(observation, fault):
    o = observation
    if fault == 'empty':
        o.value['observed'] = []
    elif fault == 'wrong-entry':
        o.value['observed'] = ['python', 'foreign.py', '--state-dir', str(o.state)]
    elif fault == 'wrong-state':
        o.value['observed'] = command(o.state.parent / 'foreign-state')
    else:
        o.value['observed'] = command(o.state) + ['--state-dir', str(o.state)]
    with pytest.raises(ValueError) as caught:
        control.process_identity(o.process, o.state)
    error = caught.value
    assert str(error) == MESSAGE  # Original refusal must happen before new diagnostics.
    row = getattr(error, 'runtime_spawn_diagnostic', None)
    assert isinstance(row, dict), 'Original rejection lacks retained diagnostic data'
    assert row['schema'] == 'modu-vision.runtime-spawn-observation/v1'
    assert row['non_atomic'] is True and row['ownership_verified'] is False
    assert row['expected']['sha256'] == control.command_sha256(o.process.args)
    assert row['observed']['sha256'] == control.command_sha256(o.value['observed'])
    assert row['expected']['state_matches'] is True
    assert row['observed']['state_matches'] is (fault == 'wrong-entry')
    assert row['observed']['state_flag_count'] == (0 if fault == 'empty' else 2 if fault == 'duplicate-state' else 1)
    assert row['process']['pid'] == 731 and row['process']['birth'] == 42.5
    assert row['process']['status'] == 'sleeping' and row['process']['poll_exit_code'] is None
    assert o.counts == {'cmdline': 1, 'birth': 1, 'status': 1, 'poll': 1}
    notes = getattr(error, '__notes__', [])
    if hasattr(error, 'add_note'):
        assert len(notes) == 1 and notes[0].startswith('Runtime spawn diagnostic: ')
        assert len(notes[0].encode('utf-8')) <= 4096
        assert json.loads(notes[0].split(': ', 1)[1]) == row


@pytest.mark.parametrize('frozen', [False, True])
def test_original_valid_identity_has_no_diagnostic_queries_or_ownership_change(observation, frozen):
    o = observation
    o.value['observed'] = command(o.state, frozen)
    o.process.args = command(o.state, frozen)
    result = control.process_identity(o.process, o.state)
    assert result == {'pid': 731, 'process_created_at': 42.5,
                      'process_command_sha256': control.command_sha256(o.value['observed'])}
    assert o.counts == {'cmdline': 1, 'birth': 1, 'status': 0, 'poll': 0}


def test_observation_errors_cannot_replace_the_original_command_refusal(observation):
    o = observation
    o.value.update(observed=[], status_error=control.psutil.NoSuchProcess(731),
                   birth_error=control.psutil.AccessDenied(731), poll_error=RuntimeError('PRIVATE-ERROR'))
    with pytest.raises(ValueError) as caught:
        control.process_identity(o.process, o.state)
    assert str(caught.value) == MESSAGE
    row = getattr(caught.value, 'runtime_spawn_diagnostic', None)
    assert isinstance(row, dict), 'Original rejection lacks retained diagnostic data'
    assert row['process']['status'] == {'unavailable': 'NoSuchProcess'}
    assert row['process']['birth'] == {'unavailable': 'AccessDenied'}
    assert row['process']['poll_exit_code'] == {'unavailable': 'observation_error'}
    assert 'PRIVATE-ERROR' not in json.dumps(row)
    assert o.counts == {'cmdline': 1, 'birth': 1, 'status': 1, 'poll': 1}


def test_private_argv_values_are_digest_only_in_the_retained_exception(observation):
    o = observation
    private = ['/Users/private-company/private-model.pt', 'rtsp://private-user:SECRET@camera/private', 'SECRET-SESSION']
    o.process.args = command(o.state) + ['--package', private[0], '--camera-source', private[1], '--unknown', private[2]]
    o.value['observed'] = ['python', 'wrong-entry.py', '--state-dir', str(o.state), *private]
    with pytest.raises(ValueError) as caught:
        control.process_identity(o.process, o.state)
    assert str(caught.value) == MESSAGE
    row = getattr(caught.value, 'runtime_spawn_diagnostic', None)
    assert isinstance(row, dict), 'Original rejection lacks retained diagnostic data'
    retained = json.dumps(row) + ''.join(getattr(caught.value, '__notes__', []))
    for text in [*private, str(o.state), 'wrong-entry.py']:
        assert text not in retained
    assert 'SECRET' not in retained and '/Users/' not in retained
    assert row['expected']['argc'] == len(o.process.args)
    assert row['observed']['sha256'] == control.command_sha256(o.value['observed'])


def test_hostile_expected_args_and_observation_values_are_not_string_coerced(observation):
    o = observation
    class Hostile:
        def __str__(self):
            pytest.fail('Diagnostic must not stringify hostile values')
        def __repr__(self):
            pytest.fail('Diagnostic must not repr hostile values')
    o.process.args = [Hostile()]
    o.value.update(observed=[], birth=Hostile(), status=Hostile(), poll=Hostile())
    with pytest.raises(ValueError) as caught:
        control.process_identity(o.process, o.state)
    assert str(caught.value) == MESSAGE
    row = getattr(caught.value, 'runtime_spawn_diagnostic', None)
    assert isinstance(row, dict), 'Original rejection lacks retained diagnostic data'
    assert row['expected'] == {'unavailable': 'command_shape'}
    for key in ['birth', 'status', 'poll_exit_code']:
        assert row['process'][key] == {'unavailable': 'observation_shape'}


def test_large_expected_vectors_still_produce_a_bounded_traceback_note(observation):
    o = observation
    o.process.args = ['not-an-authority'] * 65
    o.value['observed'] = []
    with pytest.raises(ValueError) as caught:
        control.process_identity(o.process, o.state)
    assert str(caught.value) == MESSAGE
    row = getattr(caught.value, 'runtime_spawn_diagnostic', None)
    assert isinstance(row, dict), 'Original rejection lacks retained diagnostic data'
    assert row['expected'] == {'unavailable': 'command_bounds', 'argc': 65}
    assert len(json.dumps(row).encode('utf-8')) < 4096


def test_diagnostic_construction_failure_keeps_the_original_error(observation, monkeypatch):
    o = observation
    o.value['observed'] = []
    def failed(*args):
        raise SystemExit('PRIVATE-DIAGNOSTIC-FAILURE')
    monkeypatch.setattr(control, '_runtime_spawn_diagnostic', failed)
    with pytest.raises(ValueError) as caught:
        control.process_identity(o.process, o.state)
    assert str(caught.value) == MESSAGE
    assert o.counts == {'cmdline': 1, 'birth': 0, 'status': 0, 'poll': 0}
    assert 'PRIVATE-DIAGNOSTIC-FAILURE' not in ''.join(getattr(caught.value, '__notes__', []))
