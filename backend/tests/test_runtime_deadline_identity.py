"""An observed original exit boundary does not authorize signals or children."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import uuid

import psutil
import pytest

from backend.engine import runtime_deadline as runtime
from backend.tests.test_runtime_deadline_tree import _CHILD, owned_fixture, assert_live, refuse_scope


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Original unreaped POSIX child identity boundary')


@contextmanager
def cooperative_leader(directory):
    directory.mkdir()
    script = directory / 'leader.py'
    script.write_text(_CHILD)
    nonce = uuid.uuid4().hex
    fixture = {'directory': directory, 'nonce': nonce,
               'command': [sys.executable, '-I', '-B', str(script), str(directory), nonce]}
    try:
        yield fixture
    finally:
        owner = fixture.get('owner')
        if owner is not None and owner.process.poll() is None:
            identity = json.loads((directory / 'registered.json').read_text())
            process = psutil.Process(identity['pid'])
            assert process.create_time() == identity['birth']
            # The actual fixture command is checked with the original reader;
            # a controlled parent reader never changes the child command.
            assert fixture['original_cmdline'](process) == identity['command']
            (directory / 'stop.json').write_text(json.dumps({'nonce': nonce}))
            owner.process.wait(timeout=3)
        if owner is not None:
            assert owner.process.returncode == 0
            assert json.loads((directory / 'exit.json').read_text()) == {
                'cause': 'owned_cooperative_stop', 'exit_code': 0, 'nonce': nonce}


def identity_boundary(monkeypatch, fixture, mode, *, stop_on_observation=True):
    """Use actual children/OS reads; model only the declared reader boundary."""
    original_init = runtime._OwnedGroup.__init__
    original_cmdline = psutil.Process.cmdline
    original_birth = psutil.Process.create_time
    original_session = os.getsid
    original_group = runtime._group_members
    fixture.update(original_cmdline=original_cmdline, comparisons=[], observed_rows=[], returned_rows=[])

    def initialize(owner, *args, **kwargs):
        original_init(owner, *args, **kwargs)
        fixture['owner'] = owner
        assert owner.leader is not None and owner.leader[1]

    def selected(pid):
        owner = fixture.get('owner')
        return owner is not None and pid == owner.process.pid and (fixture['directory'] / 'registered.json').exists()

    def command(process):
        actual = original_cmdline(process)
        if not selected(process.pid):
            return actual
        changed = mode == 'changed_command' or (mode == 'empty_then_changed_command' and fixture['comparisons'])
        returned = ['controlled-nonempty-foreign-command'] if changed else []
        fixture['comparisons'].append({'pid': process.pid, 'actual_command': actual, 'returned_command': returned,
                                       'original_returncode': fixture['owner'].process.returncode})
        return returned

    def birth(process):
        actual = original_birth(process)
        return actual + 1 if mode == 'changed_birth' and selected(process.pid) else actual

    def session(pid):
        actual = original_session(pid)
        return actual + 1 if mode == 'changed_session' and selected(pid) else actual

    def group(identifier):
        rows = original_group(identifier)
        owner = fixture.get('owner')
        if owner is not None and selected(owner.process.pid) and (fixture['comparisons'] or mode == 'changed_session'):
            fixture['observed_rows'].append({pid: dict(row) for pid, row in rows.items()})
            if stop_on_observation and (mode != 'empty_then_changed_command' or len(fixture['observed_rows']) >= 2):
                (fixture['directory'] / 'stop.json').write_text(json.dumps({'nonce': fixture['nonce']}))
            if mode == 'observation_error':
                raise PermissionError('controlled original group reader refusal')
            if mode == 'reused_after_reap':
                owner.process.wait(timeout=3)
                # A different generation reusing the original numeric leader
                # after the actual Popen was reaped cannot borrow the fact.
                rows = {owner.process.pid: {'state': 'R', 'session': owner.session}}
            if mode == 'foreign_session_row' and owner.process.pid in rows:
                rows = {**rows, owner.process.pid: {**rows[owner.process.pid], 'session': owner.session + 1}}
            if mode == 'missing_session_row' and owner.process.pid in rows:
                rows = {**rows, owner.process.pid: {'state': rows[owner.process.pid]['state']}}
            fixture['returned_rows'].append({pid: dict(row) for pid, row in rows.items()})
        return rows

    def forbidden_signal(*args, **kwargs):
        pytest.fail('An unavailable command may not authorize group signals')

    monkeypatch.setattr(runtime._OwnedGroup, '__init__', initialize)
    monkeypatch.setattr(psutil.Process, 'cmdline', command)
    monkeypatch.setattr(psutil.Process, 'create_time', birth)
    monkeypatch.setattr(runtime.os, 'getsid', session)
    monkeypatch.setattr(runtime, '_group_members', group)
    monkeypatch.setattr(runtime.os, 'killpg', forbidden_signal)


def execute(fixture, event, *, deadline=2000):
    return runtime.execute_owned_process(fixture['command'], deadline_ms=deadline, cancel_event=event,
        cwd=fixture['directory'], env={'PATH': os.defpath, 'LANG': 'C.UTF-8',
            'PYTHONDONTWRITEBYTECODE': '1', 'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'none'})


def record(fixture, outcome, mode):
    root = os.environ.get('MV_RUNTIME_TREE_RECEIPT_DIR')
    if root:
        value = {'mode': mode, 'outcome': outcome, 'comparisons': fixture['comparisons'],
                 'original_group_rows': fixture['observed_rows'], 'returned_group_rows': fixture['returned_rows'],
                 'reader_boundary_controlled': True,
                 'actual_cooperative_child': True, 'signals_sent': False,
                 'process_tree_qualified': False, 'model_or_native_qualified': False}
        (Path(root) / (mode + '-' + uuid.uuid4().hex + '.json')).write_text(json.dumps(value, indent=2) + '\n')


def test_unavailable_command_at_original_exit_does_not_become_unknown(monkeypatch, tmp_path):
    handle = runtime.CancellableExecution()
    with cooperative_leader(tmp_path / 'original-exit') as fixture:
        identity_boundary(monkeypatch, fixture, 'empty_at_exit')
        with handle.running() as event:
            outcome = execute(fixture, event)
        record(fixture, outcome, 'empty_at_exit')
        assert any(row['returned_command'] == [] and row['original_returncode'] is None
                   and row['actual_command'] == fixture['command'] for row in fixture['comparisons'])
        assert any(fixture['owner'].process.pid in rows for rows in fixture['observed_rows'])
        assert outcome['status'] == 'completed' and outcome['returncode'] == 0
        assert fixture['owner'].process.returncode == 0 and handle._quarantine is None
        assert handle.cancel() is False


def test_persistent_unavailable_command_refuses_deadline_without_signal(monkeypatch, tmp_path):
    handle = runtime.CancellableExecution()
    with cooperative_leader(tmp_path / 'persistent') as fixture:
        identity_boundary(monkeypatch, fixture, 'persistent_empty', stop_on_observation=False)
        with handle.running() as event:
            outcome = execute(fixture, event, deadline=300)
        record(fixture, outcome, 'persistent_empty')
        assert outcome['status'] == 'uncertain' and outcome['returncode'] != 0
        assert outcome['leader_returncode'] is None and outcome['ownership']['termination_attempted'] is False
        assert fixture['owner'].leader_matches() is False
        assert fixture['owner'].process.pid in outcome['ownership']['unknown_members']
        assert handle._active is event
        refuse_scope(handle)
    refuse_scope(handle)  # The prior deadline uncertainty cannot clear on disappearance.


@pytest.mark.parametrize('mode', ['changed_command', 'empty_then_changed_command', 'changed_birth', 'changed_session',
                                 'foreign_session_row', 'missing_session_row', 'observation_error', 'reused_after_reap'])
def test_unavailable_fact_cannot_mask_foreign_or_unproven_identity(monkeypatch, tmp_path, mode):
    handle = runtime.CancellableExecution()
    with cooperative_leader(tmp_path / mode) as fixture:
        identity_boundary(monkeypatch, fixture, mode)
        with handle.running() as event:
            outcome = execute(fixture, event)
        record(fixture, outcome, mode)
        assert outcome['status'] == 'uncertain' and outcome['returncode'] != 0
        assert outcome['leader_returncode'] == 0
        assert outcome['ownership']['observation_failed'] or outcome['pid'] in outcome['ownership']['unknown_members']
        assert handle._active is event
        refuse_scope(handle)


def test_unavailable_original_command_cannot_enroll_observed_child(monkeypatch, tmp_path):
    handle = runtime.CancellableExecution()
    with owned_fixture(tmp_path / 'unregistered-child') as fixture:
        identity_boundary(monkeypatch, fixture, 'empty_with_child', stop_on_observation=False)
        with handle.running() as event:
            outcome = execute(fixture, event)
        identity, _ = assert_live(fixture)
        record(fixture, outcome, 'empty_with_child')
        assert outcome['status'] == 'uncertain' and outcome['returncode'] != 0
        assert identity['pid'] in outcome['ownership']['unknown_members']
        assert identity['pid'] not in [row['pid'] for row in outcome['ownership']['registered_members']]
        assert handle._active is event
        refuse_scope(handle)
        fixture['stop']()
        refuse_scope(handle)
