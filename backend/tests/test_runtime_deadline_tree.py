"""Bounded observed-group ownership; this does not qualify an escaped tree."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import psutil
import pytest

from backend.engine import runtime_deadline as runtime
from backend.tests.test_runtime_deadline_sdk import real_package
from backend.tests.test_gan_source_composition import generator


_CHILD = '''from pathlib import Path
import json,os,psutil,sys,time
root=Path(sys.argv[1]);nonce=sys.argv[2];me=psutil.Process()
def write(name,value):
 p=root/name;t=p.with_suffix('.tmp');t.write_text(json.dumps(value));os.replace(t,p)
write('registered.json',{'pid':me.pid,'birth':me.create_time(),'command':me.cmdline(),
 'parent_pid':me.ppid(),'pgid':os.getpgid(me.pid),'sid':os.getsid(me.pid),'nonce':nonce})
end=time.monotonic()+6;sequence=0;cause='fixture_deadline'
while time.monotonic()<end:
 sequence+=1;write('activity.json',{'sequence':sequence,'wall_ns':time.time_ns(),'nonce':nonce})
 stop=root/'stop.json'
 if stop.exists() and json.loads(stop.read_text())=={'nonce':nonce}:
  cause='owned_cooperative_stop';break
 time.sleep(.01)
write('exit.json',{'cause':cause,'exit_code':0,'nonce':nonce})
'''

_LEADER = '''from pathlib import Path
import json,os,psutil,subprocess,sys,time
root=Path(sys.argv[1]);nonce=sys.argv[2];me=psutil.Process()
command=[sys.executable,'-I','-B',str(root/'child.py'),str(root),nonce]
child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
 stderr=subprocess.DEVNULL,close_fds=True)
end=time.monotonic()+2
while not (root/'registered.json').exists():
 assert time.monotonic()<end;time.sleep(.005)
identity=json.loads((root/'registered.json').read_text());observed=psutil.Process(child.pid)
assert identity['pid']==child.pid and identity['birth']==observed.create_time()
assert identity['command']==observed.cmdline()==command and observed.ppid()==me.pid
assert identity['pgid']==os.getpgid(me.pid)==me.pid and identity['sid']==me.pid
(root/'leader.json').write_text(json.dumps({'pid':me.pid,'birth':me.create_time(),
 'command':me.cmdline(),'registered_before_exit':identity}))
# Give the actual ownership observer a bounded registration window.
time.sleep(.25)
print('owned leader result',flush=True)
'''


@contextmanager
def owned_fixture(directory):
    directory.mkdir()
    (directory/'child.py').write_text(_CHILD)
    (directory/'leader.py').write_text(_LEADER)
    nonce=uuid.uuid4().hex
    command=[sys.executable,'-I','-B',str(directory/'leader.py'),str(directory),nonce]
    fixture={'directory':directory,'nonce':nonce,'command':command}
    def registered():
        value=json.loads((directory/'registered.json').read_text())
        assert value['nonce']==nonce
        return value
    def stop():
        if not (directory/'registered.json').exists():return
        identity=registered()
        if not (directory/'exit.json').exists():
            child=psutil.Process(identity['pid'])
            assert child.create_time()==identity['birth'] and child.cmdline()==identity['command']
            (directory/'stop.json').write_text(json.dumps({'nonce':nonce}))
            child.wait(timeout=3)
        assert json.loads((directory/'exit.json').read_text())=={
            'cause':'owned_cooperative_stop','exit_code':0,'nonce':nonce}
        assert not psutil.pid_exists(identity['pid'])
    fixture.update(registered=registered,stop=stop)
    try:yield fixture
    finally:stop()


def execute(fixture,event):
    return runtime.execute_owned_process(fixture['command'],deadline_ms=2000,cancel_event=event,
        cwd=fixture['directory'],env={'PATH':os.defpath,'LANG':'C.UTF-8',
        'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none'})


def assert_live(fixture):
    identity=fixture['registered']();child=psutil.Process(identity['pid'])
    assert child.create_time()==identity['birth'] and child.cmdline()==identity['command']
    assert child.is_running() and child.status()!=psutil.STATUS_ZOMBIE
    leader=json.loads((fixture['directory']/'leader.json').read_text())
    assert leader['registered_before_exit']==identity and identity['parent_pid']==leader['pid']
    returned=time.time_ns();end=time.monotonic()+1
    while True:
        activity=json.loads((fixture['directory']/'activity.json').read_text())
        if activity['wall_ns']>returned:break
        assert time.monotonic()<end;time.sleep(.01)
    return identity,activity


def refuse_scope(handle):
    with pytest.raises(RuntimeError,match='unreconciled'):
        with handle.running():pytest.fail('An unreconciled owner admitted another call')


def receipt(fixture,outcome,activity,*,reconciled):
    root=os.environ.get('MV_RUNTIME_TREE_RECEIPT_DIR')
    if root:
        import tempfile
        directory=Path(tempfile.mkdtemp(prefix='observed-group-',dir=root))
        value={'outcome':outcome,'registered_before_exit':fixture['registered'](),
            'activity_after_helper_return':activity,'known_member_reconciled':reconciled,
            'cooperative_exit':json.loads((fixture['directory']/'exit.json').read_text()),
            'signals_sent':False,'gpu_used':False,'process_tree_qualified':False}
        (directory/'execution.json').write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')


pytestmark=pytest.mark.skipif(os.name!='posix',reason='Actual fixture qualifies the supported POSIX group only')


def test_registered_survivor_refuses_success_and_next_call_until_known_exit(tmp_path,monkeypatch):
    def forbidden_signal(*args,**kwargs):pytest.fail('A cached group was signalled after leader exit')
    monkeypatch.setattr(runtime.os,'killpg',forbidden_signal)
    handle=runtime.CancellableExecution()
    with owned_fixture(tmp_path/'known') as fixture:
        with handle.running() as event:outcome=execute(fixture,event)
        identity,activity=assert_live(fixture)
        assert outcome['status']=='uncertain' and outcome['returncode']!=0
        assert outcome['leader_returncode']==0
        assert outcome['rejection_reason']=='OWNED_PROCESS_GROUP_UNRECONCILED'
        assert outcome['ownership']['process_tree_exit_verified'] is False
        assert identity['pid'] in [row['pid'] for row in outcome['ownership']['registered_members']]
        assert handle._active is event and handle.cancel() is True
        refuse_scope(handle)
        fixture['stop']()
        with handle.running() as next_event:assert next_event is not event
        assert handle.cancel() is False
        receipt(fixture,outcome,activity,reconciled=True)


def test_stdlib_observation_without_identity_cannot_clear_quarantine(tmp_path,monkeypatch):
    monkeypatch.setattr(runtime,'_optional_psutil',lambda:None,raising=False)
    handle=runtime.CancellableExecution()
    with owned_fixture(tmp_path/'unknown') as fixture:
        with handle.running() as event:outcome=execute(fixture,event)
        _,activity=assert_live(fixture)
        assert outcome['status']=='uncertain' and outcome['returncode']!=0
        assert outcome['ownership']['registered_members']==[]
        assert outcome['ownership']['unknown_members']
        assert handle._active is event
        refuse_scope(handle)
        fixture['stop']()
        refuse_scope(handle)  # Empty scan is not an enrollment/exit receipt.
        assert handle._active is event
        receipt(fixture,outcome,activity,reconciled=False)


def test_ordinary_leader_completion_needs_no_optional_dependency(monkeypatch):
    monkeypatch.setattr(runtime,'_optional_psutil',lambda:None,raising=False)
    handle=runtime.CancellableExecution()
    for _ in range(2):
        with handle.running() as event:
            outcome=runtime.execute_owned_process([sys.executable,'-I','-B','-c','print("ordinary")'],
                deadline_ms=2000,cancel_event=event)
        assert outcome['status']=='completed' and outcome['returncode']==0 and outcome['stdout']=='ordinary\n'
        assert handle.cancel() is False


def test_reused_leader_number_cannot_clear_original_quarantine(monkeypatch):
    read=runtime._group_members
    def foreign_generation(group):
        rows=read(group)
        # A controlled OS-reader response models a different generation reusing
        # the old group/leader number after the actual original leader exited.
        if group not in rows:rows[group]={'state':'S','session':group}
        return rows
    monkeypatch.setattr(runtime,'_group_members',foreign_generation)
    def forbidden_signal(*args,**kwargs):pytest.fail('A reused numeric group was signalled')
    monkeypatch.setattr(runtime.os,'killpg',forbidden_signal)
    handle=runtime.CancellableExecution()
    with handle.running() as event:
        outcome=runtime.execute_owned_process([sys.executable,'-I','-B','-c','print("original")'],
            deadline_ms=2000,cancel_event=event)
    assert outcome['status']=='uncertain' and outcome['returncode']!=0
    assert outcome['leader_returncode']==0
    assert outcome['pid'] in outcome['ownership']['unknown_members']
    assert handle._active is event
    refuse_scope(handle)


@pytest.mark.parametrize('ownership_reader',['live_identity_failure','initial_group_failure'])
def test_unproven_live_leader_does_not_claim_timeout_termination(tmp_path,monkeypatch,ownership_reader):
    # A failed identity reader may not turn a still active owned leader into a
    # terminated timeout. Its registered fixture exits cooperatively, not by PID.
    directory=tmp_path/'unproven';directory.mkdir();nonce=uuid.uuid4().hex
    script=directory/'leader.py'
    script.write_text(_CHILD)
    command=[sys.executable,'-I','-B',str(script),str(directory),nonce]
    if ownership_reader=='initial_group_failure':
        def unavailable(pid):raise PermissionError('Controlled initial group capture refusal')
        monkeypatch.setattr(runtime.os,'getpgid',unavailable)
    else:
        original=runtime._OwnedGroup.leader_matches
        monkeypatch.setattr(runtime._OwnedGroup,'leader_matches',
            lambda self:False if (directory/'registered.json').exists() else original(self))
    monkeypatch.setattr(runtime,'_group_members',lambda group:{})
    def forbidden_signal(*args,**kwargs):pytest.fail('An unproven leader was signalled')
    monkeypatch.setattr(runtime.os,'killpg',forbidden_signal)
    handle=runtime.CancellableExecution()
    try:
        with handle.running() as event:
            outcome=runtime.execute_owned_process(command,deadline_ms=200,cancel_event=event,
                cwd=directory,env={'PATH':os.defpath,'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none'})
        identity=json.loads((directory/'registered.json').read_text());child=psutil.Process(identity['pid'])
        assert child.create_time()==identity['birth'] and child.cmdline()==identity['command']
        assert child.is_running() and child.status()!=psutil.STATUS_ZOMBIE
        assert outcome['status']=='uncertain' and outcome['returncode']!=0
        assert outcome['leader_returncode'] is None
        assert outcome['ownership']['leader_exit_confirmed'] is False
        assert outcome['ownership']['termination_attempted'] is False
        assert handle._active is event
        refuse_scope(handle)
    finally:
        if (directory/'registered.json').exists():
            identity=json.loads((directory/'registered.json').read_text());child=psutil.Process(identity['pid'])
            assert child.create_time()==identity['birth'] and child.cmdline()==identity['command']
            (directory/'stop.json').write_text(json.dumps({'nonce':nonce}));child.wait(timeout=3)
            assert json.loads((directory/'exit.json').read_text())['cause']=='owned_cooperative_stop'


def test_flow_consumer_refuses_report_while_registered_descendant_remains(real_package,tmp_path,monkeypatch):
    from backend.engine.flow_package_runtime import Predictor
    package,image=real_package;predictor=Predictor(package,deadline_ms=2000,cpu_threads=1)
    owned_execute=runtime.execute_owned_process;observed={}
    with owned_fixture(tmp_path/'flow-survivor') as fixture:
        def transport(command,**kwargs):
            # A controlled valid report is ready. Actual source rejection must
            # precede its read, using the real owned CPU lifecycle outcome.
            output=Path(command[-1]);output.write_text(json.dumps({'final_verdict':'CONTROLLED','image_id':'controlled-report'}))
            observed['report_ready']=output.is_file()
            observed['outcome']=owned_execute(fixture['command'],**{**kwargs,'cwd':fixture['directory']})
            return observed['outcome']
        monkeypatch.setattr(runtime,'execute_owned_process',transport)
        with pytest.raises(RuntimeError,match='Owned inference failed: Owned process group remains unreconciled'):
            predictor.predict(image,'controlled-report')
        _,activity=assert_live(fixture)
        assert observed['report_ready'] is True and observed['outcome']['returncode']!=0
        assert predictor.cancel() is True
        refuse_scope(predictor._execution)
        fixture['stop']()
        with predictor._execution.running():pass
        receipt(fixture,observed['outcome'],activity,reconciled=True)


def test_generator_consumer_publishes_no_candidate_with_registered_survivor(generator,tmp_path,monkeypatch):
    from backend.engine.gan_package_runtime import build_generator_package,GeneratorExecutor
    package=build_generator_package(generator,tmp_path/'generator-package')
    executor=GeneratorExecutor(package,deadline_ms=2000);output=tmp_path/'unpublished'
    owned_execute=runtime.execute_owned_process;observed={}
    with owned_fixture(tmp_path/'generator-survivor') as fixture:
        def transport(command,**kwargs):
            payload=json.loads(Path(command[-2]).read_text());staged=Path(payload['output_dir']);staged.mkdir()
            candidate=staged/'controlled.txt';candidate.write_text('controlled candidate')
            Path(command[-1]).write_text(json.dumps({'candidates':[{'path':str(candidate),'status':'synthetic_unreviewed'}]}))
            observed['candidate_ready']=candidate.is_file()
            observed['outcome']=owned_execute(fixture['command'],**{**kwargs,'cwd':fixture['directory']})
            return observed['outcome']
        monkeypatch.setattr(runtime,'execute_owned_process',transport)
        with pytest.raises(RuntimeError,match='Owned generation failed: Owned process group remains unreconciled'):
            executor.execute({'output_dir':str(output),'count':1})
        _,activity=assert_live(fixture)
        assert observed['candidate_ready'] is True and not output.exists()
        assert observed['outcome']['returncode']!=0
        assert executor.cancel() is True
        refuse_scope(executor._execution)
        fixture['stop']()
        with executor._execution.running():pass
        receipt(fixture,observed['outcome'],activity,reconciled=True)


def test_failed_initial_group_capture_cannot_claim_completion(monkeypatch):
    def unavailable(pid):raise ProcessLookupError('Controlled initial ownership reader failure')
    monkeypatch.setattr(runtime.os,'getpgid',unavailable)
    handle=runtime.CancellableExecution()
    with handle.running() as event:
        outcome=runtime.execute_owned_process([sys.executable,'-I','-B','-c','print("original")'],
            deadline_ms=2000,cancel_event=event)
    assert outcome['status']=='uncertain' and outcome['returncode']!=0
    assert outcome['leader_returncode']==0 and outcome['ownership']['observation_failed'] is True
    assert handle._active is event
    refuse_scope(handle)


def test_timeout_cleanup_after_leader_exit_never_signals_cached_group(tmp_path,monkeypatch):
    terminate=runtime._terminate_owned
    def delayed_cleanup(process,ownership=None):
        # The original leader exits in the bounded scheduling gap between a
        # deadline decision and cleanup. Recheck actual identity at cleanup.
        process.wait(timeout=2)
        return terminate(process,ownership)
    monkeypatch.setattr(runtime,'_terminate_owned',delayed_cleanup)
    def forbidden_signal(*args,**kwargs):pytest.fail('A departed leader authorized a cached group signal')
    monkeypatch.setattr(runtime.os,'killpg',forbidden_signal)
    handle=runtime.CancellableExecution()
    with owned_fixture(tmp_path/'timeout-survivor') as fixture:
        with handle.running() as event:
            outcome=runtime.execute_owned_process(fixture['command'],deadline_ms=180,
                cancel_event=event,cwd=fixture['directory'])
        _,activity=assert_live(fixture)
        assert outcome['status']=='uncertain' and outcome['returncode']!=0 and outcome['leader_returncode']==0
        assert outcome['ownership']['termination_attempted'] is False
        assert handle._active is event
        refuse_scope(handle)
        fixture['stop']()
        with handle.running():pass
        receipt(fixture,outcome,activity,reconciled=True)


@pytest.mark.parametrize('interrupt',[False,True])
def test_cleanup_failure_retains_owned_work_and_refuses_next_scope(tmp_path,monkeypatch,interrupt):
    def failed_cleanup(process,ownership=None):raise subprocess.TimeoutExpired('controlled owned cleanup',5)
    monkeypatch.setattr(runtime,'_terminate_owned',failed_cleanup)
    def forbidden_signal(*args,**kwargs):pytest.fail('The failure fixture must stop cooperatively')
    monkeypatch.setattr(runtime.os,'killpg',forbidden_signal)
    handle=runtime.CancellableExecution();original_launch=subprocess.Popen
    with owned_fixture(tmp_path/'cleanup-failure') as fixture:
        if interrupt:
            observe=runtime._OwnedGroup.observe
            def interrupted_observation(ownership):
                if ownership.termination_failed:raise KeyboardInterrupt('controlled cleanup observation interruption')
                return observe(ownership)
            monkeypatch.setattr(runtime._OwnedGroup,'observe',interrupted_observation)
            def launch(command,**kwargs):
                process=original_launch(command,**kwargs)
                if command!=fixture['command']:return process
                wait=process.wait;interrupted=False
                def interrupt_wait(*args,**kwargs):
                    nonlocal interrupted
                    if not interrupted and (fixture['directory']/'registered.json').exists():
                        interrupted=True;raise KeyboardInterrupt('controlled wait interruption')
                    return wait(*args,**kwargs)
                process.wait=interrupt_wait
                return process
            monkeypatch.setattr(subprocess,'Popen',launch)
        with handle.running() as event:
            if interrupt:
                with pytest.raises(KeyboardInterrupt,match='controlled wait interruption'):execute(fixture,event)
            else:
                outcome=runtime.execute_owned_process(fixture['command'],deadline_ms=180,
                    cancel_event=event,cwd=fixture['directory'])
                assert outcome['status']=='uncertain' and outcome['returncode']!=0
                assert outcome['ownership']['termination_failed'] is True
        assert handle._active is event and handle._quarantine is not None
        assert handle._quarantine.process.poll() is None
        refuse_scope(handle)
        fixture['stop']()
        handle._quarantine.process.wait(timeout=2)
        refuse_scope(handle)  # A failed cleanup still lacks group reconciliation.


def test_diagnostic_read_failure_cannot_release_registered_survivor(tmp_path,monkeypatch):
    import tempfile
    original=tempfile.TemporaryFile
    class FailedReader:
        def __init__(self,file):self.file=file
        def __getattr__(self,name):return getattr(self.file,name)
        def read(self,*args,**kwargs):raise OSError('controlled diagnostic read refusal')
    @contextmanager
    def broken_reader(*args,**kwargs):
        with original(*args,**kwargs) as file:yield FailedReader(file)
    monkeypatch.setattr(tempfile,'TemporaryFile',broken_reader)
    def forbidden_signal(*args,**kwargs):pytest.fail('A diagnostic failure may not signal a departed leader')
    monkeypatch.setattr(runtime.os,'killpg',forbidden_signal)
    handle=runtime.CancellableExecution()
    with owned_fixture(tmp_path/'diagnostic-survivor') as fixture:
        with handle.running() as event:
            with pytest.raises(OSError,match='controlled diagnostic read refusal'):execute(fixture,event)
        identity,_=assert_live(fixture)
        assert handle._active is event and handle._quarantine is not None
        assert identity['pid'] in handle._quarantine.remaining
        refuse_scope(handle)
        fixture['stop']()
        with handle.running():pass
