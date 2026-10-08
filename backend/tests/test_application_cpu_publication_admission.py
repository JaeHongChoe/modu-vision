"""Real original publication mutex/OFD/count; no child, model or application.

OS parent/executable identity, installation admission, plan and math are explicit
models. The canonical handshake validator and original lease transition flock
run unchanged, exposing nested-open self-contention at the real publication.
"""
import copy
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import socket
import subprocess
import threading

import pytest

from backend.engine import application_launch_handshake as h
from backend.engine import application_launch_lease as lease
from backend.engine import application_launch_execution as execution
from backend.engine import application_launch_quiescence as q
from backend.engine import application_owned_cpu_child_relay as cpu
from backend.engine import runtime_update as update


@pytest.fixture
def original(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('No child permitted'))
    monkeypatch.setattr(threading.Thread, 'start', lambda *a, **k: pytest.fail('No thread permitted'))
    nonce='a'*32; epoch='b'*32
    directory=tmp_path/lease.LEASES/nonce; directory.mkdir(parents=True)
    lock=directory/lease.TRANSITION_LOCK
    lock.touch(mode=0o600); info=lock.stat()
    identity={'device':info.st_dev,'inode':info.st_ino}
    (directory/'journal.json').write_bytes(update._canonical({'transition_lock_identity':identity}))
    @contextmanager
    def installation(*a, **k):
        # Global installation boundary is modeled; per-lease OFD flock is real.
        yield
    monkeypatch.setattr(lease, 'store_admission', installation)
    parent={'pid':321,'created_at':1.0,'command_sha256':'d'*64}
    backend={'pid':os.getpid(),'created_at':2.0,'command_sha256':'e'*64}
    binding={'application_generation':'modeled-app','database_generation_path':'modeled-db',
             'authority_path':'modeled-authority','authority_sha256':'f'*64}
    writer={'writer_id':'1'*32,'registration_sha256':'2'*64,'registration_registry_sha256':'3'*64}
    row={'nonce':nonce,'state':'ready','spawn_attempted':True,'process':parent,
         'protocol_version':4,'binding':binding,'writer_drain':writer}
    values={'VISION_APPLICATION_LAUNCH_NONCE':nonce,'VISION_APPLICATION_GENERATION':binding['application_generation'],
            'VISION_APPLICATION_DATABASE_GENERATION':binding['database_generation_path']}
    challenge={'schema_version':1,'kind':'backend_challenge','challenge':'4'*64,'epoch':epoch,'nonce':nonce,
               'binding':binding,'main_process':parent,'backend_pid':os.getpid(),
               'backend_executable':'modeled-backend','backend_build_identity_sha256':None,'writer':writer}
    checks=[]
    monkeypatch.setattr(h, '_arguments', lambda root: checks.append('arguments'))
    monkeypatch.setattr(lease, '_load', lambda root: row)
    monkeypatch.setattr(lease, '_identity', lambda pid: backend if pid==os.getpid() else parent)
    monkeypatch.setattr(update, '_launch_binding', lambda *a, **k: copy.deepcopy(binding))
    def executable(*a):
        checks.append('executable')
        return {'executable':'modeled-backend','executable_sha256':'5'*64,
                'build_identity_sha256':None,'frozen':False}
    monkeypatch.setattr(h, '_executable', executable)
    proof=h._validate(tmp_path, values, challenge)
    anchor=os.open(tmp_path/'original-writer.lock',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
    os.set_inheritable(anchor,False); fcntl.flock(anchor,fcntl.LOCK_SH|fcntl.LOCK_NB)
    stat=os.fstat(anchor);left,right=socket.socketpair();admission=h.BackendWorkAdmission()
    context=(('original',),('original-backend',),'source-python',False)
    clock=[100.0]
    monkeypatch.setattr(cpu.time, 'monotonic', lambda:clock[0])
    sleeps=[]
    def sleep(seconds):
        assert 0 < seconds <= .005
        sleeps.append(seconds);clock[0]+=seconds
    monkeypatch.setattr(cpu.time, 'sleep', sleep)
    cache={'context':context,'ready':True,'challenge':challenge,'proof':proof,'socket':left,
           'admission':admission,'writer_guard':object(),'writer_handle':object(),
           'writer_private_fd':anchor,'writer_fd_identity':(stat.st_dev,stat.st_ino),
           'original_cpu_execution':{'thread':threading.current_thread(),'done':threading.Event(),
                                     'result':None,'error':None,'deadline':310.0}}
    monkeypatch.setattr(h, '_CACHE', cache)
    monkeypatch.setattr(h, '_context', lambda:context)
    monkeypatch.setattr(h, '_root_context', lambda:(tmp_path,values))
    frame={'schema_version':1,'kind':'cpu_execution_request','request_id':'6'*32,'nonce':nonce,'epoch':epoch,
           'binding_sha256':proof['binding_sha256'],'backend_claim_sha256':'7'*64,'workspace_id':'8'*32,
           'project_id':'9'*32,'plan_sha256':'a'*64,'challenge':'b'*64}
    semantic={'modeled_math':True};project=tmp_path/'project'
    capability={'plan':{'schema_version':1,'kind':'owned_cpu_ocr_known_image_plan','workspace_id':'8'*32,
                 'project_id':'9'*32,'device':'cpu','cpu_threads':1,'deadline_ms':4000,
                 'runtime_source_sha256':'c'*64,'semantic_output_sha256':update._sha(update._canonical(semantic))},
                'plan_sha256':'a'*64,'project_path':str(project),'scope_key':'source'}
    intent={'request':copy.deepcopy(frame),'capability':copy.deepcopy(capability)}
    monkeypatch.setattr(execution, 'validate_request', lambda *a:copy.deepcopy(intent))
    monkeypatch.setattr(execution, 'admit_plan', lambda *a,**k:copy.deepcopy(capability))
    monkeypatch.setattr(execution, 'validate_result', lambda *a:semantic)
    bootstrap=directory/'bootstrap-receipt.json';bootstrap.write_text('{}')
    monkeypatch.setattr(execution, '_live_origin', lambda *a: checks.append('live-origin'))
    monkeypatch.setattr(execution, '_checkpoint', lambda stage: checks.append(stage))
    @contextmanager
    def guard(*a, **k):
        yield
    monkeypatch.setattr(q,'writer_guard',guard)
    ticket=cpu.admit_backend_source_cpu(frame,proof,tmp_path)
    state=cpu._PRODUCERS[ticket]
    monkeypatch.setattr(cpu, 'reserve_backend_cpu_child', lambda cap,path: state.update(snapshot=Path(path),phase='reserved'))
    def math(cap, *, environment):
        assert cap is ticket and environment['CUDA_VISIBLE_DEVICES']==''
        # Model the executor's original reserved -> active_child -> child_exited
        # transition; no real child, OS/handle or mathematical proof is claimed.
        assert state['phase']=='reserved'
        state['phase']='active_child'
        (state['snapshot']/'result.json').write_bytes(update._canonical({'modeled_math':True}))
        state['phase']='child_exited'
        return {'status':'completed','returncode':0,'pid':456,'elapsed_ms':1}
    monkeypatch.setattr(cpu, 'execute_source_process', math)
    # Standalone publication controls begin at the explicitly modeled original
    # successful-child exit; execution controls reset to initial active below.
    state['phase']='child_exited'
    try:
        yield {'root':tmp_path,'row':row,'binding':binding,'challenge':challenge,'values':values,
               'frame':frame,'proof':proof,'ticket':ticket,'state':state,'cache':cache,'admission':admission,
               'left':left,'right':right,'clock':clock,'sleeps':sleeps,'checks':checks,'lock':lock,
               'project':project,'semantic':semantic}
    finally:
        # Only original fixture-created descriptors; no production finish/repair.
        os.close(state['private']);os.close(anchor);left.close();right.close()


def test_original_source_publication_uses_one_real_transition_without_self_wait(original):
    o=original
    o['state']['phase']='active'
    result=execution._execute_backend_admitted(o['frame'],o['proof'],o['root'],(),source_producer=o['ticket'])
    assert result['kind']=='cpu_execution_completed'
    assert result['semantic_output']==o['semantic']
    assert (o['project']/result['output_path']).is_file()
    assert o['sleeps']==[] and o['clock'][0]==100.0
    assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    assert o['state']['phase']=='child_exited'


def retained(o):
    assert o['ticket'] in cpu._UNRESOLVED
    assert o['state']['phase']=='unresolved'
    assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
    assert os.fstat(o['state']['private']).st_ino==os.fstat(o['cache']['writer_private_fd']).st_ino


def test_typed_publication_retains_one_original_lock_full_auth_and_count(original):
    o=original;o['checks'].clear()
    with cpu.source_publication_admission(o['ticket']):
        assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
        with pytest.raises(lease.LeaseTransitionBusy):
            with lease._transition_admission(o['root'],o['frame']['nonce']):pytest.fail('Original mutex dropped')
        assert o['checks'].count('executable')==1
    assert o['checks'].count('executable')==2
    with lease._transition_admission(o['root'],o['frame']['nonce']):pass
    assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    assert o['clock'][0]==100.0 and o['sleeps']==[]


@pytest.mark.parametrize('phase',['active','reserved','spawning','active_child','waiting_publication','closing','finishing','finished','unresolved'])
@pytest.mark.parametrize('when',['before','after'])
def test_publication_accepts_only_exact_child_exited_phase_before_and_after(original,phase,when):
    o=original;entered=[]
    assert o['state']['phase']=='child_exited'
    if when=='before':o['state']['phase']=phase
    with pytest.raises(h.HandshakeError):
        with cpu.source_publication_admission(o['ticket']):
            entered.append(True)
            if when=='after':o['state']['phase']=phase
            else:pytest.fail('Non-exited original phase admitted publication')
    assert entered==([True] if when=='after' else [])
    retained(o)


@pytest.mark.parametrize('raw',[{},123,None,True])
def test_raw_data_never_confer_a_publication_admission(original,raw):
    with pytest.raises(h.HandshakeError):
        with cpu.source_publication_admission(raw):pytest.fail('Raw authority admitted')
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}


def test_unminted_copy_subclass_and_foreign_thread_cannot_publish(original):
    o=original
    for fn in (lambda:copy.copy(o['ticket']),lambda:copy.deepcopy(o['ticket'])):
        with pytest.raises(h.HandshakeError):fn()
    class Foreign(cpu.BackendCpuProducer):pass
    for raw in (object.__new__(cpu.BackendCpuProducer),object.__new__(Foreign)):
        with pytest.raises(h.HandshakeError):
            with cpu.source_publication_admission(raw):pytest.fail('Unminted authority admitted')
    o['state']['thread']=object()
    with pytest.raises(h.HandshakeError):
        with cpu.source_publication_admission(o['ticket']):pytest.fail('Foreign thread admitted')
    retained(o)


def mutate(o,monkeypatch,kind):
    if kind=='cache':monkeypatch.setattr(h,'_CACHE',dict(o['cache']))
    elif kind=='root':monkeypatch.setattr(h,'_root_context',lambda:(o['root']/'foreign',o['values']))
    elif kind=='context':monkeypatch.setattr(h,'_context',lambda:('foreign',))
    elif kind=='socket':o['cache']['socket']=o['right']
    elif kind=='proof':o['cache']['proof']=dict(o['proof'],nonce='f'*32)
    elif kind=='done':o['cache']['original_cpu_execution']['done'].set()
    elif kind=='execution-deadline':o['cache']['original_cpu_execution']['deadline']+=1
    elif kind=='anchor':o['cache']['writer_fd_identity']=(0,0)
    elif kind=='deadline':o['clock'][0]=104.0
    elif kind=='phase':o['state']['phase']='reserved'
    elif kind=='count':o['state']['counted']=False
    elif kind=='nonce':o['challenge']['nonce']='f'*32
    else:raise AssertionError(kind)


@pytest.mark.parametrize('kind',['cache','root','context','socket','proof','done','execution-deadline','anchor','deadline','phase','count','nonce'])
@pytest.mark.parametrize('when',['before','after'])
def test_original_producer_drift_refuses_without_count_release(original,monkeypatch,kind,when):
    o=original;entered=[]
    if when=='before':mutate(o,monkeypatch,kind)
    with pytest.raises(h.HandshakeError):
        with cpu.source_publication_admission(o['ticket']):
            entered.append(True)
            if when=='after':mutate(o,monkeypatch,kind)
    assert entered==([True] if when=='after' else [])
    retained(o)


@pytest.mark.parametrize('kind',['owner','binding','writer','parent','executable','arguments','challenge-kind'])
def test_full_owner_authentication_runs_after_body_under_original_lock(original,monkeypatch,kind):
    o=original
    with pytest.raises(h.HandshakeError):
        with cpu.source_publication_admission(o['ticket']):
            if kind=='owner':o['row']['state']='recovery_required'
            elif kind=='binding':o['row']['binding']=dict(o['binding'],database_generation_path='foreign')
            elif kind=='writer':o['row']['writer_drain']=dict(o['row']['writer_drain'],writer_id='f'*32)
            elif kind=='parent':monkeypatch.setattr(lease,'_identity',lambda pid:{'pid':pid,'created_at':99.0,'command_sha256':'f'*64})
            elif kind=='executable':monkeypatch.setattr(h,'_executable',lambda *a:{'executable':'foreign','frozen':False})
            elif kind=='arguments':monkeypatch.setattr(h,'_arguments',lambda *a:(_ for _ in ()).throw(h.HandshakeError('Foreign arguments')))
            else:o['challenge']['kind']='foreign'
    retained(o)


def test_external_contention_only_waits_inside_original_deadline(original):
    o=original;fd=os.open(o['lock'],os.O_RDWR)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(h.HandshakeError,match='deadline expired'):
            with cpu.source_publication_admission(o['ticket']):pytest.fail('Busy lock admitted')
        assert o['clock'][0]==104.0 and o['sleeps']
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)  # Same original handle retained.
        retained(o)
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


@pytest.mark.parametrize('error',[ValueError('Original publication body failed'),lease.LeaseTransitionBusy('Original body busy')])
def test_body_failure_never_retries_or_releases_and_preserves_first_error(original,error):
    o=original;entries=[]
    with pytest.raises(type(error)) as caught:
        with cpu.source_publication_admission(o['ticket']):
            entries.append(True);o['row']['state']='recovery_required';raise error
    assert caught.value is error and entries==[True] and o['sleeps']==[]
    assert any('post-body authentication also refused' in n for n in error.__notes__)
    retained(o)


@pytest.mark.parametrize('failure',['post-error-string','body-error-add-note'])
def test_advisory_note_failure_cannot_replace_original_body_error(original,monkeypatch,failure):
    class Unprintable(ValueError):
        def __str__(self):raise RuntimeError('Advisory formatting failed')
    class UnsafeNote(ValueError):
        def add_note(self,note):raise RuntimeError('Advisory add_note failed')
    original_error=ValueError('Original publication failed') if failure=='post-error-string' else UnsafeNote('Original publication failed')
    post_error=Unprintable() if failure=='post-error-string' else h.HandshakeError('Post-owner refused')
    with pytest.raises(BaseException) as caught:
        with cpu.source_publication_admission(original['ticket']):
            def post(*a):raise post_error
            monkeypatch.setattr(h,'_arguments',post)
            raise original_error
    assert caught.value is original_error
    retained(original)


def test_original_named_lock_replacement_is_refused_without_adoption(original):
    o=original
    with pytest.raises(lease.LaunchLeaseError):
        with cpu.source_publication_admission(o['ticket']):
            o['lock'].unlink();o['lock'].touch(mode=0o600)
    retained(o)


@pytest.mark.parametrize('failure',['sync-error','late-sync','owner-loss'])
def test_partial_create_only_output_keeps_original_count_and_custody(original,monkeypatch,failure):
    o=original;o['state']['phase']='active';sync=update.migration._sync_directories
    def fail(path,**kwargs):
        sync(path,**kwargs)
        output=o['project']/execution.OUTPUTS/(o['frame']['request_id']+'.json')
        if Path(path)==o['project']/execution.OUTPUTS and output.exists():
            if failure=='sync-error':raise OSError('Original output sync failed')
            elif failure=='late-sync':o['clock'][0]=104.0
            else:o['row']['state']='recovery_required'
    monkeypatch.setattr(update.migration,'_sync_directories',fail)
    with pytest.raises((h.HandshakeError,OSError)):
        execution._execute_backend_admitted(o['frame'],o['proof'],o['root'],(),source_producer=o['ticket'])
    retained(o)
    output=o['project']/execution.OUTPUTS/(o['frame']['request_id']+'.json')
    assert output.exists()  # Retained original failed artifact, never overwritten/repaired.
    with pytest.raises(h.HandshakeError):
        with cpu.source_publication_admission(o['ticket']):pytest.fail('Failed publication reused')


def test_generic_validate_remains_full_strict_wrapper(original):
    o=original;validated={};o['checks'].clear()
    assert h._validate(o['root'],o['values'],o['challenge'],validated=validated)==o['proof']
    assert validated=={'protocol_version':4} and o['checks'].count('executable')==2
    o['row']['state']='recovery_required'
    with pytest.raises(h.HandshakeError,match='current spawned main owner'):
        h._validate(o['root'],o['values'],o['challenge'])


def test_legacy_publication_keeps_original_transition_without_source_auth(original,monkeypatch):
    from backend.engine import runtime_deadline
    o=original;o['checks'].clear()
    def math(command,**kwargs):
        assert kwargs['pass_fds']==() and kwargs['deadline_ms']==4000
        (Path(kwargs['cwd'])/'result.json').write_bytes(update._canonical({'modeled_math':True}))
        return {'status':'completed','returncode':0,'pid':456,'elapsed_ms':1}
    monkeypatch.setattr(runtime_deadline,'execute_owned_process',math)
    result=execution._execute_backend_admitted(o['frame'],o['proof'],o['root'],())
    assert result['kind']=='cpu_execution_completed' and o['checks'].count('executable')==0
    assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
