"""Initial owned-child argv admission; real cooperative POSIX children only.

Only declared parent-side OS-reader responses are controlled. The original
Popen, process census, wait/deadline loop and cooperative child remain real.
These controls do not establish the cause of a historical hosted failure.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import psutil
import pytest

from backend.engine import runtime_deadline as runtime
from backend.tests.test_runtime_deadline_tree import publish_owned_stop


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Original POSIX child admission boundary')

_COOPERATIVE = '''from pathlib import Path
import json,os,psutil,sys,time
root=Path(sys.argv[1]);nonce=sys.argv[2];me=psutil.Process()
def write(name,value):
 p=root/name;t=p.with_suffix('.tmp');t.write_text(json.dumps(value));os.replace(t,p)
write('registered.json',{'pid':me.pid,'birth':me.create_time(),'command':me.cmdline(),
 'parent':me.ppid(),'group':os.getpgid(me.pid),'session':os.getsid(me.pid),'nonce':nonce})
end=time.monotonic()+6;cause='fixture_deadline'
while time.monotonic()<end:
 stop=root/'stop.json'
 if stop.exists() and json.loads(stop.read_text())=={'nonce':nonce}:
  cause='owned_cooperative_stop';break
 time.sleep(.005)
write('exit.json',{'cause':cause,'code':0,'nonce':nonce})
print('owned cooperative exit',flush=True)
'''


_WITH_CHILD = '''from pathlib import Path
import json,subprocess,sys,time
root=Path(sys.argv[1]);nonce=sys.argv[2]
child=subprocess.Popen([sys.executable,'-I','-B',str(root/'child.py'),str(root),nonce],
 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
(root/'child-pid.json').write_text(json.dumps({'pid':child.pid,'nonce':nonce}))
child.wait(timeout=6)
print('owned cooperative exit',flush=True)
'''


@contextmanager
def cooperative_case(directory, *, child=False):
    directory.mkdir()
    script = directory / 'cooperative.py'
    script.write_text(_WITH_CHILD if child else _COOPERATIVE)
    if child:
        (directory / 'child.py').write_text(_COOPERATIVE)
    case = SimpleNamespace(directory=directory, nonce=uuid.uuid4().hex, owner=None,
                           original_cmdline=psutil.Process.cmdline)
    case.command = [sys.executable, '-I', '-B', str(script), str(directory), case.nonce]
    def stop():
        publish_owned_stop(directory, case.nonce)
    case.stop = stop
    try:
        yield case
    finally:
        if case.owner is not None:
            process = case.owner.process
            if process.poll() is None:
                actual = psutil.Process(process.pid)
                assert case.original_cmdline(actual) == case.command
                assert actual.ppid() == os.getpid()
                case.stop()
                process.wait(timeout=3)
            case.stop()
            assert process.returncode == 0
            assert json.loads((directory / 'exit.json').read_text()) == {
                'cause': 'owned_cooperative_stop', 'code': 0, 'nonce': case.nonce}


def initial_empty_reader(monkeypatch, case, empty_count, *, mode='restore'):
    """Keep real reader side effects; change only root argv's declared samples."""
    original_init = runtime._OwnedGroup.__init__
    original_group = runtime._group_members
    original_matches = runtime._OwnedGroup.leader_matches
    original_pgid, original_sid = os.getpgid, os.getsid
    state = SimpleNamespace(calls=0, restored=0, comparisons=[], census=0,
                            initialized=False, faults=set(), empty_live=[], observed_child=False)
    def fault(name):
        if state.initialized and mode == name and name not in state.faults:
            state.faults.add(name)
            return True
        return False
    def initialize(owner, process, module):
        case.owner = owner
        arguments = process.args
        if mode in ('expected_foreign', 'expected_empty', 'expected_string', 'expected_nonstring'):
            process.args = {'expected_foreign': ['different-original-launch'], 'expected_empty': [],
                            'expected_string': 'different-original-launch', 'expected_nonstring': [False]}[mode]
        original_init(owner, process, module)
        if mode == 'mutated_args_alias':
            case.command = list(arguments)
            arguments[:] = ['later-mutated-args-alias']
        process.args = ['later-mutated-args'] if mode == 'mutated_args' else arguments
        if mode in ('initial_status_args_alias', 'initial_parent_args_alias'):
            process.args = list(case.command)
        state.initialized = True
    def owner(pid):
        if fault('reader_error'):
            raise psutil.AccessDenied(pid)
        original = psutil.Process(pid)
        def birth():
            actual = original.create_time()
            if mode in ('initial_bad_birth', 'initial_inf_birth', 'initial_zero_birth', 'initial_bool_birth') and not state.initialized:
                return {'initial_bad_birth': float('nan'), 'initial_inf_birth': float('inf'),
                        'initial_zero_birth': 0, 'initial_bool_birth': True}[mode]
            return actual + 1 if fault('changed_birth') else actual
        def parent():
            mutate_initial_alias('initial_parent_args_alias')
            actual = original.ppid()
            if mode == 'initial_wrong_parent' and not state.initialized:
                return actual + 1
            return actual + 1 if fault('changed_parent') else actual
        def status():
            mutate_initial_alias('initial_status_args_alias')
            if fault('pending_zombie'):
                return psutil.STATUS_ZOMBIE
            if fault('pending_dead'):
                return psutil.STATUS_DEAD
            return original.status()
        def mutate_initial_alias(boundary):
            if mode == boundary and not state.initialized and boundary not in state.faults:
                state.faults.add(boundary)
                process = case.owner.process
                actual_arguments = list(process.args)
                process.args[:] = ['mutated-during-initial-reader']
                process.args = list(actual_arguments)
                case.command = actual_arguments
        def command():
            actual = case.original_cmdline(original)
            if case.owner is None or pid != case.owner.process.pid:
                return actual
            state.calls += 1
            value = [] if state.calls <= empty_count else actual
            if mode == 'child_then_exact' and not state.observed_child:
                value = []
            if fault('foreign_command'):
                value = ['different-nonempty-command']
            if mode == 'final_foreign_command' and state.calls == 3:
                value = ['changed-at-admission-publication']
            if value:
                state.restored += 1
            state.comparisons.append((len(actual), len(value)))
            return value
        return SimpleNamespace(pid=pid, create_time=birth,
                               cmdline=command, ppid=parent, status=status,
                               children=original.children)
    module = SimpleNamespace(Process=owner, Error=psutil.Error, NoSuchProcess=psutil.NoSuchProcess,
                             STATUS_ZOMBIE=psutil.STATUS_ZOMBIE, STATUS_DEAD=psutil.STATUS_DEAD)
    def census(group):
        state.census += 1
        if fault('census_error'):
            raise PermissionError('Controlled census refusal')
        rows = original_group(group)
        if any(pid != case.owner.process.pid for pid in rows):
            state.observed_child = True
        if mode in ('missing_row_session', 'foreign_row_session') and case.owner.process.pid in rows:
            row = rows[case.owner.process.pid]
            rows[case.owner.process.pid] = ({'state': row['state']} if mode == 'missing_row_session'
                                           else {**row, 'session': group + 1})
        ready = (case.directory / 'registered.json').exists()
        if mode == 'final_foreign_command' and state.census == 1:
            assert case.owner.leader is None, 'Pending publication admitted changed final argv'
        if mode in ('restore', 'mutated_args', 'mutated_args_alias',
                    'initial_status_args_alias', 'initial_parent_args_alias') and state.restored and ready:
            case.stop()
        elif mode == 'cancel' and state.census >= 3 and ready:
            case.cancel.set()
        elif mode != 'deadline' and state.census >= 3 and ready:
            case.stop()
        if mode == 'empty_rows':
            return {}
        if mode == 'reused_after_reap' and case.owner.process.poll() is not None:
            return {case.owner.process.pid: {'state': 'R', 'session': group}}
        return rows
    def matches(owner):
        process = owner.process
        if fault('changed_pid'):
            def forbidden_poll():
                pytest.fail('Changed numeric Popen PID was polled before custody refusal')
            owner.process = SimpleNamespace(pid=process.pid + 1, args=process.args,
                                            returncode=process.returncode, poll=forbidden_poll)
        try:
            actual = original_matches(owner)
        finally:
            owner.process = process
        if state.calls <= empty_count:
            state.empty_live.append(actual)
        return actual
    def group(pid):
        actual = original_pgid(pid)
        return actual + 1 if fault('changed_group') else actual
    def session(pid):
        actual = original_sid(pid)
        return actual + 1 if fault('changed_session') else actual
    def forbidden_signal(*_args, **_kwargs):
        pytest.fail('An initial argv observation must not authorize a signal')
    monkeypatch.setattr(runtime._OwnedGroup, '__init__', initialize)
    monkeypatch.setattr(runtime._OwnedGroup, 'leader_matches', matches)
    monkeypatch.setattr(runtime, '_optional_psutil', lambda: module)
    monkeypatch.setattr(runtime, '_group_members', census)
    monkeypatch.setattr(runtime.os, 'killpg', forbidden_signal)
    monkeypatch.setattr(runtime.os, 'getpgid', group)
    monkeypatch.setattr(runtime.os, 'getsid', session)
    return state


def execute(case, event, deadline=2000):
    return runtime.execute_owned_process(case.command, deadline_ms=deadline,
        cwd=case.directory, cancel_event=event,
        env={'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
             'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'none'})


def test_fixture_stop_marker_is_complete_at_first_child_visibility(tmp_path, monkeypatch):
    """A real reader must never see the first publication as truncated JSON."""
    with cooperative_case(tmp_path / 'stop-publication') as case:
        initial_empty_reader(monkeypatch, case, 0)
        source = case.directory / 'cooperative.py'
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        stop = case.directory / 'stop.json'
        original_open = Path.open
        observation = {'direct_stop_open': False}

        def publication_boundary(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get('mode', 'r')
            if path == stop and mode == 'w' and not observation['direct_stop_open']:
                # The old direct publication has opened/truncated the real
                # destination but has not written a byte yet. Let the original
                # owned reader observe that exact boundary, inside its frame.
                observation['direct_stop_open'] = True
                case.owner.process.wait(timeout=1)
            return handle

        monkeypatch.setattr(Path, 'open', publication_boundary)
        handle = runtime.CancellableExecution()
        with handle.running() as event:
            outcome = execute(case, event)
        proof = {**observation, 'actual_returncode': case.owner.process.returncode,
                 'actual_JSONDecodeError': 'JSONDecodeError' in outcome['stderr'],
                 'fixture_source_unchanged': hashlib.sha256(source.read_bytes()).hexdigest() == source_sha}
        (case.directory / 'stop-publication-probe.json').write_text(json.dumps(proof))
        assert proof['fixture_source_unchanged']
        if observation['direct_stop_open']:
            assert proof['actual_returncode'] == 1 and proof['actual_JSONDecodeError']
        assert outcome['status'] == 'completed' and outcome['returncode'] == 0, proof
        assert json.loads(stop.read_text()) == {'nonce': case.nonce}


def test_shared_stop_marker_never_reopens_or_replaces_an_existing_publication(tmp_path, monkeypatch):
    publish_owned_stop(tmp_path, 'owned-idempotent-nonce')
    marker = tmp_path / 'stop.json'
    before = marker.stat()
    original_open, original_replace = Path.open, os.replace
    def open_once(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get('mode', 'r')
        assert path != marker or mode not in ('w', 'x', 'a'), 'Existing stop marker reopened for writing'
        return original_open(path, *args, **kwargs)
    def replace_once(source, target, *args, **kwargs):
        assert target != marker, 'Existing stop marker replaced again'
        return original_replace(source, target, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_once)
    monkeypatch.setattr(os, 'replace', replace_once)
    publish_owned_stop(tmp_path, 'owned-idempotent-nonce')
    after = marker.stat()
    assert (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size) == (
        before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size)
    assert json.loads(marker.read_text()) == {'nonce': 'owned-idempotent-nonce'}


@pytest.mark.parametrize('empty_count,mode', [(1, 'restore'), (3, 'restore'),
    (1, 'mutated_args'), (1, 'mutated_args_alias'),
    (1, 'initial_status_args_alias'), (1, 'initial_parent_args_alias')])
def test_initial_empty_argv_admits_only_later_exact_actual_owned_command(tmp_path, monkeypatch, empty_count, mode):
    # A regression restoring the first empty tuple as the comparison baseline
    # makes this real executor return uncertain despite its actual child exit0.
    with cooperative_case(tmp_path / 'initial-empty') as case:
        state = initial_empty_reader(monkeypatch, case, empty_count, mode=mode)
        handle = runtime.CancellableExecution()
        with handle.running() as event:
            outcome = execute(case, event)
        assert state.comparisons[:empty_count] == [(6, 0)] * empty_count
        assert state.restored and state.census
        assert not any(state.empty_live), 'Repeated empty argv became live identity'
        assert outcome['status'] == 'completed' and outcome['returncode'] == 0
        assert outcome['stdout'] == 'owned cooperative exit\n'
        assert case.owner.process.returncode == 0
        assert handle._quarantine is None and handle.cancel() is False
        assert case.owner.unknown == set() and case.owner.members == {}


@pytest.mark.parametrize('mode', ['foreign_command', 'final_foreign_command', 'expected_foreign', 'expected_empty',
    'expected_string', 'expected_nonstring', 'initial_bad_birth', 'changed_birth',
    'initial_inf_birth', 'initial_zero_birth', 'initial_bool_birth', 'initial_wrong_parent',
    'changed_parent', 'changed_group', 'changed_session', 'changed_pid', 'reader_error',
    'pending_zombie', 'pending_dead', 'census_error', 'missing_row_session', 'foreign_row_session'])
def test_initial_pending_identity_fault_is_sticky_and_never_rebased(tmp_path, monkeypatch, mode):
    with cooperative_case(tmp_path / mode) as case:
        initial_empty_reader(monkeypatch, case,
            3 if mode in ('census_error', 'missing_row_session', 'foreign_row_session') else 1, mode=mode)
        handle = runtime.CancellableExecution()
        with handle.running() as event:
            outcome = execute(case, event)
        assert outcome['status'] == 'uncertain' and outcome['returncode'] != 0
        assert outcome['leader_returncode'] == 0
        assert outcome['ownership']['termination_attempted'] is False
        assert case.owner.members == {} and case.owner.leader is None
        assert handle._active is event
        with pytest.raises(RuntimeError, match='unreconciled'):
            with handle.running():
                pytest.fail('Unproved initial custody admitted a new call')


@pytest.mark.parametrize('mode', ['empty_forever', 'empty_rows', 'reused_after_reap'])
def test_unresolved_initial_argv_never_clears_on_exit_or_empty_census(tmp_path, monkeypatch, mode):
    with cooperative_case(tmp_path / mode) as case:
        state = initial_empty_reader(monkeypatch, case, 100000, mode=mode)
        handle = runtime.CancellableExecution()
        with handle.running() as event:
            outcome = execute(case, event)
        assert not any(state.empty_live)
        assert outcome['status'] == 'uncertain' and outcome['returncode'] != 0
        assert outcome['leader_returncode'] == 0
        assert outcome['ownership']['leader_exit_confirmed'] is True
        assert outcome['ownership']['termination_attempted'] is False
        assert case.owner.members == {} and case.owner.leader is None
        assert handle._active is event


@pytest.mark.parametrize('mode', ['empty_forever', 'child_then_exact'])
def test_pending_empty_argv_cannot_enroll_actual_observed_child(tmp_path, monkeypatch, mode):
    with cooperative_case(tmp_path / 'unregistered-child', child=True) as case:
        state = initial_empty_reader(monkeypatch, case, 100000 if mode == 'empty_forever' else 1, mode=mode)
        handle = runtime.CancellableExecution()
        with handle.running() as event:
            outcome = execute(case, event)
        child = json.loads((case.directory / 'registered.json').read_text())
        assert child['parent'] == case.owner.process.pid
        assert child['group'] == child['session'] == case.owner.process.pid
        assert state.observed_child
        assert not any(state.empty_live)
        assert outcome['status'] == 'uncertain' and outcome['leader_returncode'] == 0
        assert child['pid'] in outcome['ownership']['unknown_members']
        assert case.owner.members == {} and case.owner.leader is None


@pytest.mark.parametrize('mode', ['deadline', 'cancel'])
def test_pending_identity_does_not_grant_deadline_or_cancel_signals(tmp_path, monkeypatch, mode):
    with cooperative_case(tmp_path / mode) as case:
        state = initial_empty_reader(monkeypatch, case, 100000, mode=mode)
        handle = runtime.CancellableExecution()
        with handle.running() as event:
            case.cancel = event
            outcome = execute(case, event, deadline=200 if mode == 'deadline' else 2000)
        assert not any(state.empty_live)
        assert outcome['status'] == 'uncertain' and outcome['returncode'] != 0
        assert outcome['leader_returncode'] is None
        assert outcome['ownership']['leader_exit_confirmed'] is False
        assert outcome['ownership']['termination_attempted'] is False
        assert outcome['ownership']['termination_failed'] is False
        assert case.owner.members == {} and case.owner.leader is None
        assert handle._active is event
        if mode == 'deadline':
            assert outcome['elapsed_ms'] >= 200
