"""Startup sampling never makes a transient empty argv durable ownership."""
import hashlib
import json
import os
from types import SimpleNamespace

import psutil
import pytest

from backend.engine import application_launch_lease as lease
from backend.tests.test_application_launch_controller import application_fixture
from backend.tests.test_application_launch_lease import controlled_canary_publication, reserve


def test_original_start_waits_for_nonempty_identity_without_rewriting_history(tmp_path, monkeypatch):
    code = '''import os,time
from pathlib import Path
root=Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR'])
while not (root/'projects'/'exit-request').exists():time.sleep(.01)
'''
    root, value, _ = application_fixture(tmp_path, code)
    owner = reserve(root, value); original_identity = lease._identity; observations = []
    empty = hashlib.sha256(lease._update()._canonical([])).hexdigest()
    def transient(pid):
        result = original_identity(pid)
        if owner._process is not None and pid == owner._process.pid:
            observations.append(result)
            if len(observations) == 1: return {**result, 'command_sha256': empty}
        return result
    monkeypatch.setattr(lease, '_identity', transient)
    try:
        row = owner.start(); child = owner._process
        assert row['state'] == 'starting' and row['process']['pid'] == child.pid
        assert row['process']['command_sha256'] != empty
        assert row['process'] == original_identity(child.pid)
        assert len(observations) >= 3
        journal = root/lease.LEASES/owner.nonce/'journal.json'
        before = journal.read_bytes()
        assert lease.inspect_launch(root)['state'] == 'starting'
        assert journal.read_bytes() == before
        assert json.loads(before)['process'] == row['process']
        with pytest.raises(ValueError, match='launch ownership'): lease.assert_quiescent(root)
    finally:
        (root/'projects'/'exit-request').write_text('original private child only')
        if owner._process is not None:
            owner._process.wait(timeout=5)
            assert owner._process.returncode == 0
        owner.close()


def test_empty_start_identity_keeps_durable_recovery_and_original_handle(tmp_path, monkeypatch):
    code = '''import os,time
from pathlib import Path
root=Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR'])
while not (root/'projects'/'exit-request').exists():time.sleep(.01)
'''
    root, value, _ = application_fixture(tmp_path, code)
    owner = reserve(root, value); original = lease._identity
    empty = hashlib.sha256(lease._update()._canonical([])).hexdigest()
    def always_empty(pid):
        result = original(pid)
        return {**result, 'command_sha256': empty} if owner._process is not None and pid == owner._process.pid else result
    monkeypatch.setattr(lease, '_identity', always_empty)
    try:
        with pytest.raises(lease.LaunchLeaseError, match='identity.*deadline'): owner.start()
        assert owner._process.poll() is None
        row = lease.inspect_launch(root)
        assert row['state'] == 'recovery_required' and row['process'] is None
        journal = root/lease.LEASES/owner.nonce/'journal.json'
        before = journal.read_bytes()
        assert json.loads(before)['spawn_attempted'] is True
        assert (journal.parent/'spawn-intent.json').is_file()
        with pytest.raises(ValueError): owner.start()
        with pytest.raises(ValueError): owner.cancel()
        with pytest.raises(ValueError): lease.assert_quiescent(root)
        assert journal.read_bytes() == before
    finally:
        (root/'projects'/'exit-request').write_text('original private child only')
        if owner._process is not None:
            owner._process.wait(timeout=5)
            assert owner._process.returncode == 0
        owner.close()


@pytest.fixture
def sampler(monkeypatch):
    class Clock:
        now = 0.0
        def monotonic(self): return self.now
        def sleep(self, seconds):
            assert 0 < seconds <= .01
            self.now += seconds
    clock = Clock(); state = {'birth': 12.0, 'parent': os.getpid(), 'returncode': None,
                             'session': 321, 'group': 321, 'commands': [], 'reads': 0}
    child = SimpleNamespace(pid=321, poll=lambda: state['returncode'])
    monkeypatch.setattr(lease, 'time', clock, raising=False)
    monkeypatch.setattr(lease.psutil, 'Process', lambda pid: SimpleNamespace(
        create_time=lambda: state['birth'], ppid=lambda: state['parent']))
    monkeypatch.setattr(lease.os, 'getsid', lambda pid: state['session'])
    monkeypatch.setattr(lease.os, 'getpgid', lambda pid: state['group'])
    def identity(pid):
        state['reads'] += 1
        command = state['commands'].pop(0) if len(state['commands']) > 1 else state['commands'][0]
        if isinstance(command, BaseException): raise command
        if callable(command): command = command()
        return {'pid': pid, 'created_at': state['birth'],
                'command_sha256': hashlib.sha256(lease._update()._canonical(command)).hexdigest()}
    monkeypatch.setattr(lease, '_identity', identity)
    return child, state, clock


def test_sampler_accepts_only_repeated_nonempty_original_birth(sampler):
    child, state, clock = sampler
    state['commands'] = [[], [], ['/bin/sh', '/owned/app'], ['/bin/sh', '/owned/app']]
    result = lease._capture_original_child_identity(child)
    assert result['pid'] == child.pid and result['created_at'] == 12.0
    assert state['reads'] == 4 and 0 < clock.now < .5


def test_empty_command_expires_at_original_sampling_deadline(sampler):
    child, state, clock = sampler; state['commands'] = [[]]
    with pytest.raises(lease.LaunchLeaseError, match='identity.*deadline'):
        lease._capture_original_child_identity(child)
    assert .5 <= clock.now < .501


@pytest.mark.parametrize('damage', ['birth', 'parent', 'session', 'group', 'command', 'empty_after_nonempty'])
def test_changed_original_child_is_refused_without_sampling_a_replacement(sampler, damage):
    child, state, clock = sampler
    def changed():
        if damage == 'birth': state['birth'] += 1
        if damage == 'parent': state['parent'] += 1
        if damage == 'session': state['session'] += 1
        if damage == 'group': state['group'] += 1
        return [] if damage == 'empty_after_nonempty' else ['/foreign'] if damage == 'command' else ['/owned']
    state['commands'] = [['/owned'], changed]
    with pytest.raises(lease.LaunchLeaseError): lease._capture_original_child_identity(child)
    assert state['reads'] == 2 and clock.now < .5


def test_original_handle_exit_is_not_adopted_by_pid(sampler):
    child, state, clock = sampler
    state['commands'] = [lambda: state.update(returncode=0) or ['/owned']]
    with pytest.raises(psutil.NoSuchProcess): lease._capture_original_child_identity(child)
    assert state['reads'] == 1 and clock.now == 0


def test_arbitrary_reader_failure_is_not_retried(sampler):
    child, state, clock = sampler; state['commands'] = [OSError('controlled denied observation')]
    with pytest.raises(OSError, match='denied'): lease._capture_original_child_identity(child)
    assert state['reads'] == 1 and clock.now == 0


def test_identity_observation_finishing_after_deadline_is_refused(sampler):
    child, state, clock = sampler
    def late():
        clock.now = .501
        return ['/owned']
    state['commands'] = [['/owned'], late]
    with pytest.raises(lease.LaunchLeaseError, match='identity.*deadline'):
        lease._capture_original_child_identity(child)
    assert state['reads'] == 2
