"""Test-only pre-admission preparation; production identity refusal stays real.

OS observations and monotonic time are modeled to expose the startup metadata
window deterministically. No daemon, model, HTTP request or credential is used.
"""
from types import SimpleNamespace

import pytest

from backend.tests import test_inspection_daemon_recovery as recovery


@pytest.fixture
def pending_child(monkeypatch):
    command = ['python', '-m', 'backend.engine.inspection_service', '--state-dir', '/private/state']
    value = {'now': 0.0, 'birth': 42.5, 'parent': 9001, 'group': 733, 'session': 733,
             'poll': None, 'status': 'disk-sleep', 'commands': [[], command], 'after_yield': None,
             'crossing': None}
    reads, sleeps = [], []
    process = SimpleNamespace(pid=733, args=command)

    def read(key, answer):
        reads.append(key)
        if value['crossing'] == key:
            value['now'] = 10.001
        result = value.get(key, answer)
        if isinstance(result, BaseException):
            raise result
        return result

    def arguments():
        result = value['commands'][0]
        if len(value['commands']) > 1:
            value['commands'].pop(0)
        return read('command', result)

    owner = SimpleNamespace(pid=733, create_time=lambda: read('birth', None),
                            ppid=lambda: read('parent', None), cmdline=arguments,
                            status=lambda: read('status', None))

    def lookup(pid):
        assert pid == 733
        return read('lookup', owner)

    def pause(seconds):
        assert 0 < seconds <= .025
        sleeps.append(seconds)
        value['now'] += seconds
        if value['after_yield']:
            value['after_yield']()

    process.poll = lambda: read('poll', None)
    monkeypatch.setattr(recovery, 'psutil', SimpleNamespace(Process=lookup))
    monkeypatch.setattr(recovery, 'os', SimpleNamespace(
        getpid=lambda: 9001, getpgid=lambda pid: read('group', None),
        getsid=lambda pid: read('session', None)))
    monkeypatch.setattr(recovery, 'time', SimpleNamespace(monotonic=lambda: value['now'], sleep=pause))
    return SimpleNamespace(process=process, command=command, value=value, reads=reads, sleeps=sleeps)


def prepare(o, deadline=10.0):
    function = getattr(recovery, '_prepare_daemon_command', None)
    assert callable(function), 'Missing pre-admission command readiness preparation'
    return function(o.process, o.command, deadline)


def test_empty_metadata_waits_for_exact_original_child_command(pending_child):
    o = pending_child
    assert prepare(o) is None
    assert o.reads.count('command') == 2
    assert o.sleeps == [.025]
    assert o.value['now'] == .025


@pytest.mark.parametrize('command', [
    ['python', 'foreign.py', '--state-dir', '/private/state'],
    ['python', '-m', 'backend.engine.inspection_service', '--state-dir', '/other/state'],
    ['python', '-m', 'backend.engine.inspection_service', '--state-dir', '/private/state', '--state-dir', '/private/state'],
    ['python', '-m', 'backend.engine.inspection_service', '--state-dir', '/private/state', '--foreign'],
])
def test_first_nonempty_mismatch_is_refused_without_waiting(pending_child, command):
    o = pending_child
    o.value['commands'] = [command, o.command]
    with pytest.raises(AssertionError):
        prepare(o)
    assert o.reads.count('command') == 1 and o.sleeps == []


@pytest.mark.parametrize('command', [None, '', (), {}])
def test_only_an_empty_argv_list_is_pending(pending_child, command):
    o = pending_child
    o.value['commands'] = [command, o.command]
    with pytest.raises(AssertionError):
        prepare(o)
    assert o.reads.count('command') == 1 and o.sleeps == []


@pytest.mark.parametrize('key,wrong', [('birth', 43.5), ('parent', 9002), ('group', 734),
                                        ('session', 734), ('poll', 0), ('status', 'zombie')])
def test_changed_child_identity_cannot_finish_preparation(pending_child, key, wrong):
    o = pending_child
    o.value['after_yield'] = lambda: o.value.update({key: wrong})
    with pytest.raises(AssertionError):
        prepare(o)
    assert o.reads.count('command') == 1
    assert len(o.sleeps) == 1


def test_changed_original_handle_pid_is_refused_before_another_lookup(pending_child):
    o = pending_child
    o.value['after_yield'] = lambda: setattr(o.process, 'pid', 734)
    with pytest.raises(AssertionError):
        prepare(o)
    assert o.reads.count('lookup') == 1


def test_wrong_group_refuses_before_reading_session_or_command(pending_child):
    o = pending_child
    o.value['group'] = 734
    with pytest.raises(AssertionError):
        prepare(o)
    assert o.reads[-1] == 'group'
    assert 'session' not in o.reads and 'command' not in o.reads


def test_unavailable_observation_propagates_without_waiting(pending_child):
    o = pending_child
    failure = PermissionError('modeled observation unavailable')
    o.value['birth'] = failure
    with pytest.raises(PermissionError) as caught:
        prepare(o)
    assert caught.value is failure and o.sleeps == []
    assert o.reads.count('command') == 0


def test_expired_frame_performs_no_os_observation(pending_child):
    o = pending_child
    with pytest.raises(AssertionError):
        prepare(o, deadline=0.0)
    assert o.reads == [] and o.sleeps == []


def test_persistent_empty_metadata_consumes_only_the_original_frame(pending_child):
    o = pending_child
    o.value['commands'] = [[]]
    with pytest.raises(AssertionError):
        prepare(o, deadline=.04)
    assert o.value['now'] == .04
    assert o.sleeps == [.025, .015]
    assert o.reads.count('command') == 2


@pytest.mark.parametrize('crossing', ['lookup', 'birth', 'parent', 'group', 'session', 'status', 'command'])
def test_an_os_read_crossing_the_frame_cannot_succeed_or_start_another_read(pending_child, crossing):
    o = pending_child
    o.value['commands'] = [o.command]
    o.value['crossing'] = crossing
    with pytest.raises(AssertionError):
        prepare(o)
    assert o.reads[-1] == crossing
    assert o.sleeps == []


def test_preparation_does_not_invoke_or_retry_original_identity(pending_child, monkeypatch):
    o = pending_child
    calls = []
    original = ValueError('original strict refusal')

    def refuse(*_args):
        calls.append('strict')
        raise original

    monkeypatch.setattr(recovery, 'process_identity', refuse)
    monkeypatch.setattr(recovery, '_add_identity_failure_note', lambda *_: None)
    prepare(o)
    assert calls == []
    with pytest.raises(ValueError) as caught:
        recovery._identity_for_daemon_test(o.process, '/private/state', 10.0)
    assert caught.value is original and calls == ['strict']
