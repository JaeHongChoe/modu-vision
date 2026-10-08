"""No child/source-CPU producer controls; OS authentication is modeled explicitly.

Real cache identity, private socketpair, original locked OFD and admission count
exercise the new producer lifetime. No model, app, listener, Popen or new thread.
Each negative prevents a raw request or uncertain lifetime becoming authority.
"""
import copy
import fcntl
import importlib.util
import os
from pathlib import Path
import pickle
import socket
import subprocess
import threading
from contextlib import contextmanager

import pytest

from backend.engine import application_launch_handshake as h
from backend.engine import application_launch_execution as execution
from backend.engine import application_launch_quiescence as q


def module():
    spec=importlib.util.find_spec('backend.engine.application_owned_cpu_child_relay')
    assert spec is not None, 'Separate original SOURCE CPU producer admission is missing'
    from backend.engine import application_owned_cpu_child_relay as cpu
    return cpu


@pytest.fixture
def original(tmp_path,monkeypatch):
    """Complete modeled authenticated boundary, real private counted resources."""
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('No child permitted'))
    monkeypatch.setattr(threading.Thread,'start',lambda *a,**k:pytest.fail('No thread permitted'))
    anchor=os.open(tmp_path/'original.lock',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
    os.set_inheritable(anchor,False);fcntl.flock(anchor,fcntl.LOCK_SH|fcntl.LOCK_NB)
    left,right=socket.socketpair();admission=h.BackendWorkAdmission();info=os.fstat(anchor)
    context=(('original',),('original-backend',),'source-python',False)
    proof={'nonce':'a'*32,'epoch':'b'*32,'binding_sha256':'c'*64,'frozen':False,
        'process':{'pid':os.getpid(),'created_at':1.0,'command_sha256':'d'*64}}
    frame={'schema_version':1,'kind':'cpu_execution_request','request_id':'e'*32,'nonce':'a'*32,
        'epoch':'b'*32,'binding_sha256':'c'*64,'backend_claim_sha256':'f'*64,
        'workspace_id':'1'*32,'project_id':'2'*32,'plan_sha256':'3'*64,'challenge':'4'*64}
    capability={'plan':{'schema_version':1,'kind':'owned_cpu_ocr_known_image_plan','workspace_id':'1'*32,
        'project_id':'2'*32,'device':'cpu','cpu_threads':1,'deadline_ms':4000,
        'runtime_source_sha256':'5'*64},'plan_sha256':'3'*64,'project_path':str(tmp_path/'project'),'scope_key':'source'}
    intent={'request':copy.deepcopy(frame),'capability':copy.deepcopy(capability)}
    cache={'context':context,'ready':True,'challenge':{'writer':{'writer_id':'6'*32,'registration_sha256':'7'*64}},
        'proof':proof,'socket':left,'admission':admission,'writer_guard':object(),'writer_handle':object(),
        'writer_private_fd':anchor,'writer_fd_identity':(info.st_dev,info.st_ino),
        'original_cpu_execution':{'thread':threading.current_thread(),'done':threading.Event(),
            'result':None,'error':None,'deadline':310.0}}
    clock=[100.0];validation={'protocol_version':4};calls=[]
    monkeypatch.setattr(h,'_CACHE',cache);monkeypatch.setattr(h,'_context',lambda:context)
    monkeypatch.setattr(h,'_root_context',lambda:(tmp_path,{'original':'values'}))
    def validate(root,values,challenge,*,validated=None):
        assert root==tmp_path and values=={'original':'values'} and challenge is cache['challenge']
        calls.append('validate');validated.update(validation);return proof
    monkeypatch.setattr(h,'_validate',validate)
    def request(value,backend,root):
        assert value==frame and backend==proof and root==tmp_path
        calls.append('intent');return copy.deepcopy(intent)
    monkeypatch.setattr(execution,'validate_request',request)
    def plan(root,workspace,project,digest):
        assert root==tmp_path and (workspace,project,digest)==('1'*32,'2'*32,'3'*64)
        calls.append('plan');return copy.deepcopy(capability)
    monkeypatch.setattr(execution,'admit_plan',plan)
    @contextmanager
    def guard(root,nonce,writer,*,expected_registration_sha256):
        assert root==tmp_path and (nonce,writer,expected_registration_sha256)==('a'*32,'6'*32,'7'*64)
        calls.append('guard-enter');yield;calls.append('guard-exit')
    monkeypatch.setattr(q,'writer_guard',guard)
    made=[];realdup=os.dup;realclose=os.close
    def dup(fd):
        result=realdup(fd);made.append(result);return result
    monkeypatch.setattr(os,'dup',dup)
    yield {'root':tmp_path,'frame':frame,'proof':proof,'capability':capability,'intent':intent,'cache':cache,
        'admission':admission,'clock':clock,'calls':calls,'made':made,'close':realclose,'left':left,'right':right,
        'validation':validation}
    # Original test-created handles only. This is not production finish/repair.
    monkeypatch.setattr(os,'close',realclose)
    for fd in made+[anchor]:
        try:realclose(fd)
        except OSError:pass
    left.close();right.close()


def admit(o,monkeypatch):
    cpu=module();monkeypatch.setattr(cpu.time,'monotonic',lambda:o['clock'][0])
    return cpu,cpu.admit_backend_source_cpu(o['frame'],o['proof'],o['root'])


def test_original_cpu_request_is_counted_once_before_source_side_effects(original,monkeypatch):
    cpu,ticket=admit(original,monkeypatch)
    assert type(ticket) is cpu.BackendCpuProducer
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    assert original['calls']==['validate','intent','plan','guard-enter','guard-exit','validate']
    with cpu.producer_transport(ticket) as fds:
        assert len(fds)==1 and fds[0] not in (original['cache']['writer_private_fd'],original['made'][0])
        assert os.fstat(fds[0]).st_ino==os.fstat(original['cache']['writer_private_fd']).st_ino
        assert not os.get_inheritable(fds[0])
        assert original['admission'].snapshot()['active_scopes']==1
    # Result/publication/ACK have not occurred: transport close must not leave.
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    with pytest.raises(h.HandshakeError):cpu.admit_backend_source_cpu(original['frame'],original['proof'],original['root'])
    assert original['admission'].snapshot()['active_scopes']==1


@pytest.mark.parametrize('mutation',['frozen','legacy','missing-anchor','different-proof','foreign-execution-thread','closed-admission','different-intent'])
def test_incomplete_source_admission_never_mints_or_dispatches(original,monkeypatch,mutation):
    cpu=module();monkeypatch.setattr(cpu.time,'monotonic',lambda:100.0)
    if mutation=='frozen':original['proof']['frozen']=True
    elif mutation=='legacy':original['validation']['protocol_version']=3
    elif mutation=='missing-anchor':original['cache']['writer_private_fd']=None
    elif mutation=='different-proof':original['cache']['proof']=dict(original['proof'],nonce='9'*32)
    elif mutation=='foreign-execution-thread':original['cache']['original_cpu_execution']=object()
    elif mutation=='closed-admission':original['admission'].close()
    else:original['intent']['request']['challenge']='9'*64
    with pytest.raises(h.HandshakeError):cpu.admit_backend_source_cpu(original['frame'],original['proof'],original['root'])
    assert original['admission'].snapshot()['active_scopes']==0


@pytest.mark.parametrize('raw',[{},123,None])
def test_raw_data_cannot_expose_original_cpu_transport(original,monkeypatch,raw):
    cpu=module()
    with pytest.raises(h.HandshakeError):
        with cpu.producer_transport(raw):pytest.fail('Raw data conferred CPU authority')
    assert original['admission'].snapshot()['active_scopes']==0


def test_ticket_cannot_be_constructed_copied_serialized_or_substituted(original,monkeypatch):
    cpu,ticket=admit(original,monkeypatch)
    for fn in (lambda:cpu.BackendCpuProducer(),lambda:copy.copy(ticket),lambda:copy.deepcopy(ticket),lambda:pickle.dumps(ticket)):
        with pytest.raises(h.HandshakeError):fn()
    class Foreign(cpu.BackendCpuProducer):pass
    foreign=object.__new__(Foreign)
    with pytest.raises(h.HandshakeError):
        with cpu.producer_transport(foreign):pytest.fail('Subclass conferred CPU authority')
    original['cache']['socket']=original['right']
    with pytest.raises(h.HandshakeError):
        with cpu.producer_transport(ticket):pytest.fail('Changed endpoint conferred CPU authority')
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}


def test_original_deadline_does_not_renew_after_transport(original,monkeypatch):
    cpu,ticket=admit(original,monkeypatch);original['clock'][0]=103.99
    with cpu.producer_transport(ticket):pass
    original['clock'][0]=104.0
    with pytest.raises(h.HandshakeError):
        with cpu.producer_transport(ticket):pytest.fail('Expired producer gained transport')
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}


@pytest.mark.parametrize('close_consumed',[False,True])
def test_ambiguous_transport_close_retains_original_count_and_never_retries(original,monkeypatch,close_consumed):
    cpu,ticket=admit(original,monkeypatch);closed=[];private=[];realclose=original['close']
    def close(fd):
        closed.append(fd)
        if private and fd==private[0]:
            if close_consumed:realclose(fd)
            raise OSError('Original close result is uncertain')
        return realclose(fd)
    monkeypatch.setattr(os,'close',close)
    with pytest.raises(OSError):
        with cpu.producer_transport(ticket) as fds:private.extend(fds)
    assert closed==private
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
    with pytest.raises(h.HandshakeError):
        with cpu.producer_transport(ticket):pytest.fail('Uncertain ticket became reusable')
    assert closed==private


def test_guard_exit_expiry_retains_uncounted_custody_and_original_count(original,monkeypatch):
    cpu=module();monkeypatch.setattr(cpu.time,'monotonic',lambda:original['clock'][0])
    @contextmanager
    def crossing(*a,**k):
        yield;original['clock'][0]=104.0
    monkeypatch.setattr(q,'writer_guard',crossing)
    with pytest.raises(h.HandshakeError):cpu.admit_backend_source_cpu(original['frame'],original['proof'],original['root'])
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
    assert len(original['made'])==1
    assert os.fstat(original['made'][0]).st_ino==os.fstat(original['cache']['writer_private_fd']).st_ino


@pytest.mark.parametrize('mutation',['owner-validation','root-context','challenge-object','foreign-thread','foreign-process','inherited-private'])
def test_admitted_ticket_cannot_outlive_exact_owner_or_context(original,monkeypatch,mutation):
    cpu,ticket=admit(original,monkeypatch)
    if mutation=='owner-validation':
        def changed(*a,**kw):kw['validated'].update(protocol_version=4);return dict(original['proof'],nonce='9'*32)
        monkeypatch.setattr(h,'_validate',changed)
    elif mutation=='root-context':monkeypatch.setattr(h,'_root_context',lambda:(original['root']/'foreign',{}))
    elif mutation=='challenge-object':original['cache']['challenge']=copy.deepcopy(original['cache']['challenge'])
    elif mutation=='foreign-thread':monkeypatch.setattr(cpu.threading,'current_thread',lambda:object())
    elif mutation=='foreign-process':monkeypatch.setattr(cpu.os,'getpid',lambda:1)
    else:os.set_inheritable(original['made'][0],True)
    with pytest.raises(h.HandshakeError):
        with cpu.producer_transport(ticket):pytest.fail('Changed original authority gained transport')
    # Thread/PID mismatch cannot mutate the original thread's retained ticket.
    if mutation not in ('foreign-thread','foreign-process'):
        assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
    else:assert original['admission'].snapshot()['active_scopes']==1


def test_validation_crossing_original_deadline_retains_count_before_dup(original,monkeypatch):
    cpu,ticket=admit(original,monkeypatch);before=len(original['made'])
    def slow(*a,**kw):
        kw['validated'].update(protocol_version=4);original['clock'][0]=104.0;return original['proof']
    monkeypatch.setattr(h,'_validate',slow)
    with pytest.raises(h.HandshakeError):
        with cpu.producer_transport(ticket):pytest.fail('Late validation acquired a new transport')
    assert len(original['made'])==before
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}


def test_private_acquired_before_failed_count_keeps_zero_count_but_sticky_custody(original,monkeypatch):
    cpu=module();monkeypatch.setattr(cpu.time,'monotonic',lambda:100.0)
    original['admission'].close()
    with pytest.raises(h.HandshakeError):cpu.admit_backend_source_cpu(original['frame'],original['proof'],original['root'])
    # Never invent/decrement an unacquired count. The original duplicate still
    # blocks its kernel fence, so zero-count custody must not claim empty drain.
    assert original['admission'].snapshot()=={'active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}
    assert len(original['made'])==1
    assert os.fstat(original['made'][0]).st_ino==os.fstat(original['cache']['writer_private_fd']).st_ino
    assert original['admission'].drain(.01)=={'status':'refused','active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}


@pytest.mark.parametrize('damage',['shorter_execution_deadline','execution_done'])
def test_original_execution_bound_is_decisive_after_producer_admission(original,monkeypatch,damage):
    original['cache']['original_cpu_execution']['deadline']=101.0
    cpu,ticket=admit(original,monkeypatch)
    if damage=='shorter_execution_deadline':original['clock'][0]=101.0
    else:original['cache']['original_cpu_execution']['done'].set()
    with pytest.raises(h.HandshakeError):
        with cpu.producer_transport(ticket):pytest.fail('Ended original execution cannot expose transport')
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}


def cpu_queue(o,monkeypatch):
    cpu=module();cls=getattr(cpu,'CpuRelayQueue',None)
    assert callable(cls),'Separate original CPU sole-reader queue is absent'
    queue=cls(o['cache']);o['cache']['source_cpu_queue']=queue;queue.claim_reader()
    _,ticket=admit(o,monkeypatch)
    return cpu,queue,ticket


def test_cpu_exchange_has_its_own_token_and_count_until_exact_reply(original,monkeypatch):
    cpu,queue,ticket=cpu_queue(original,monkeypatch)
    token=queue.enqueue(ticket,'reserve',{'fixed':True})
    from backend.engine import application_preflight_child_relay as pf
    assert token not in pf._EXCHANGES
    assert queue.take(original['left']) is token
    frame,deadline=queue.outgoing(token)
    assert frame['kind']=='backend_cpu_child_request' and deadline==104.0
    with pytest.raises(h.HandshakeError):queue.enqueue(ticket,'reserve',{'fixed':True})
    answer=cpu._cpu_reply(frame,{'writer_id':'a'*32,'registration_sha256':'b'*64})
    queue.complete(token,answer)
    assert queue.result(token)==answer and original['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    with pytest.raises(h.HandshakeError):queue.result(token)
    with pytest.raises(h.HandshakeError):queue.complete(token,answer)
    with pytest.raises(h.HandshakeError):queue.enqueue(ticket,'reserve',{'fixed':True})


@pytest.mark.parametrize('damage',['nonce','request','action','late_reply','late_consume','endpoint','copied_token','different_cache'])
def test_cpu_queue_refuses_original_reply_or_custody_change(original,monkeypatch,damage):
    cpu,queue,ticket=cpu_queue(original,monkeypatch);token=queue.enqueue(ticket,'reserve',{'fixed':True})
    assert queue.take(original['left']) is token
    frame,_=queue.outgoing(token);answer=cpu._cpu_reply(frame,{'status':'bound'})
    if damage in ('nonce','request','action'):
        answer[{'nonce':'nonce','request':'request_sha256','action':'action'}[damage]]='foreign'
        with pytest.raises(h.HandshakeError):queue.complete(token,answer)
    elif damage=='late_reply':
        original['clock'][0]=104.0
        with pytest.raises(h.HandshakeError):queue.complete(token,answer)
    elif damage=='copied_token':
        fake=object.__new__(type(token))
        with pytest.raises(h.HandshakeError):queue.complete(fake,answer)
        assert original['admission'].snapshot()['active_scopes']==1
        return
    else:
        queue.complete(token,answer)
        if damage=='late_consume':original['clock'][0]=104.0
        elif damage=='endpoint':original['cache']['socket']=original['right']
        elif damage=='different_cache':monkeypatch.setattr(h,'_CACHE',dict(original['cache']))
        with pytest.raises(h.HandshakeError):queue.result(token)
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
    assert queue.unresolved is True


def test_cpu_queue_drain_clamps_only_existing_finish_no_new_admission(original,monkeypatch):
    cpu,queue,ticket=cpu_queue(original,monkeypatch)
    queue.close_admission(101.0)
    for action in ('reserve','bind'):
        with pytest.raises(h.HandshakeError):queue.enqueue(ticket,action,{})
    token=queue.enqueue(ticket,'finish',{'original':True});assert queue.take(original['left']) is token
    frame,deadline=queue.outgoing(token);assert deadline==101.0
    queue.complete(token,cpu._cpu_reply(frame,{'status':'direct_exited'}))
    original['clock'][0]=101.0
    with pytest.raises(h.HandshakeError):queue.result(token)
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}


def prepared_child(o,monkeypatch):
    """Modeled registered snapshot, messages and Popen type; real pipe/OFD."""
    cpu,queue,ticket=cpu_queue(o,monkeypatch)
    reserve=getattr(cpu,'reserve_backend_cpu_child',None)
    assert callable(reserve),'Original SOURCE CPU gated backend reservation is absent'
    snapshot=Path(o['capability']['project_path'])/execution.OUTPUTS/('.owned-cpu-'+o['frame']['request_id'])
    snapshot.mkdir(parents=True,mode=0o700)
    def verify(path,capability):assert path==snapshot and capability==o['capability']
    monkeypatch.setattr(cpu,'_snapshot_current',verify)
    requests=[]
    def exchange(capability,action,payload):
        assert capability is ticket and o['admission'].snapshot()['active_scopes']==1
        requests.append((action,copy.deepcopy(payload)))
        return {'writer_id':'8'*32,'registration_sha256':'9'*64} if action=='reserve' else {'status':'bound'}
    monkeypatch.setattr(cpu,'_backend_cpu_exchange',exchange,raising=False)
    child_fd=os.open(o['root']/'child-original.lock',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
    os.set_inheritable(child_fd,False);fcntl.flock(child_fd,fcntl.LOCK_SH|fcntl.LOCK_NB);o['made'].append(child_fd)
    @contextmanager
    def guard(root,nonce,writer,*,expected_registration_sha256):
        assert root==o['root'] and nonce==o['frame']['nonce'] and writer=='8'*32 and expected_registration_sha256=='9'*64
        yield type('OwnedHandle',(),{'pass_fds':(child_fd,)})()
        o['close'](child_fd)
    monkeypatch.setattr(q,'writer_guard',guard)
    # Track newly opened snapshot descriptor for this no-child fixture only;
    # production retains it until exact output/final ACK, never this teardown.
    opening=os.open
    def opened(*args,**kwargs):
        fd=opening(*args,**kwargs);o['made'].append(fd);return fd
    monkeypatch.setattr(os,'open',opened)
    reserve(ticket,snapshot)
    state=cpu._PRODUCERS[ticket]
    assert state['phase']=='reserved' and requests[0][0]=='reserve'
    class ControlledChild:
        pid=391;returncode=None
        def poll(self):return self.returncode
    child=ControlledChild();identity={'pid':391,'created_at':1.0,'command_sha256':'a'*64}
    from backend.engine import application_launch_lease as lease
    monkeypatch.setattr(cpu,'_ORIGINAL_POPEN_TYPE',ControlledChild,raising=False)
    monkeypatch.setattr(lease,'_identity',lambda pid:identity if pid==child.pid else pytest.fail('Foreign original child'))
    monkeypatch.setattr(cpu,'_live_cpu_child',lambda value,plan,proof:None if value==identity and plan==state['plan'] and proof==o['proof'] else pytest.fail('Foreign original birth/plan/proof'))
    return cpu,queue,ticket,state,child,requests


def test_original_cpu_gated_child_bind_does_not_release_count_or_invent_exit(original,monkeypatch):
    cpu,queue,ticket,state,child,requests=prepared_child(original,monkeypatch)
    capture=getattr(cpu,'_capture_backend_cpu_child',None)
    assert callable(capture),'Original exact Popen capture/startup gate is absent'
    # This direct private test assignment models the result of the sole Popen
    # call. It is not actual child/OS authentication or caller PID adoption.
    state.update(phase='spawning',child=child)
    reading=os.dup(state['pipe'][0]);original['made'].append(reading)
    capture(ticket,child)
    raw=os.read(reading,8192);gate=execution._json(raw)
    assert [action for action,_ in requests]==['reserve','bind']
    assert gate=={'request_id':original['frame']['request_id'],'parent_pid':os.getpid(),'writer_fd':state['guard_fd'],
        'writer_identity':list(state['guard_identity']),'deadline':104.0}
    assert state['phase']=='active_child' and state['pipe'] is None
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    assert os.fstat(state['guard_fd']).st_ino==state['guard_identity'][1]
    for bad in (None,{},391):
        with pytest.raises(h.HandshakeError):capture(ticket,bad)
    assert state['child'] is child and original['admission'].snapshot()['active_scopes']==1


@pytest.mark.parametrize('damage',['expired','guard_identity','gate_write_expiry','gate_close_failure','bind_refusal','child_exited'])
def test_original_cpu_startup_failures_keep_original_handles_and_count(original,monkeypatch,damage):
    cpu,queue,ticket,state,child,requests=prepared_child(original,monkeypatch)
    capture=getattr(cpu,'_capture_backend_cpu_child',None)
    assert callable(capture),'Original SOURCE CPU capture is absent'
    state.update(phase='spawning',child=child);pipe=state['pipe']
    if damage=='expired':original['clock'][0]=104.0
    elif damage=='guard_identity':state['guard_identity']=(0,0)
    elif damage=='gate_write_expiry':
        write=os.write
        def crossing(fd,raw):value=write(fd,raw);original['clock'][0]=104.0;return value
        monkeypatch.setattr(os,'write',crossing)
    elif damage=='gate_close_failure':
        close=os.close
        def fail(fd):
            if fd==pipe[1]:raise OSError('Original gate close outcome is unknown')
            return close(fd)
        monkeypatch.setattr(os,'close',fail)
    elif damage=='bind_refusal':monkeypatch.setattr(cpu,'_backend_cpu_exchange',lambda *args:{'status':'refused'})
    elif damage=='child_exited':child.returncode=0
    with pytest.raises((ValueError,OSError)):capture(ticket,child)
    assert ticket in cpu._UNRESOLVED and state['child'] is child and state['phase']=='unresolved'
    assert original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
    # These original real pipe descriptors belong only to this no-child fixture.
    for fd in pipe:
        try:original['close'](fd)
        except OSError:pass


def test_original_cpu_expired_ticket_never_calls_popen(original,monkeypatch):
    cpu,queue,ticket,state,child,requests=prepared_child(original,monkeypatch)
    launch=getattr(cpu,'launch_backend_cpu_child',None)
    assert callable(launch),'Original SOURCE CPU sole Popen boundary is absent'
    original['clock'][0]=104.0;started=[]
    monkeypatch.setattr(subprocess,'Popen',lambda *args,**kwargs:started.append((args,kwargs)))
    with pytest.raises(h.HandshakeError):launch(ticket,stdout=None,stderr=None,environment={})
    assert started==[] and state['child'] is None and original['admission'].snapshot()['active_scopes']==1
    for fd in state['pipe']:original['close'](fd)


@pytest.mark.parametrize('damage',['none','nonzero','unresolved_group','late_observation'])
def test_exact_source_executor_retains_original_custody_after_exit(original,monkeypatch,damage):
    cpu,queue,ticket,state,child,requests=prepared_child(original,monkeypatch)
    run=getattr(cpu,'execute_source_process',None)
    assert callable(run),'Separate SOURCE fixed-command executor absent'
    observations=[]
    from backend.engine import runtime_deadline as runtime
    def start(capability,**kwargs):
        assert capability is ticket and state['phase']=='reserved'
        state.update(phase='active_child',child=child,child_identity={'pid':391,'created_at':1.0,'command_sha256':'a'*64},ownership=object())
        child.returncode=1 if damage=='nonzero' else 0
        return child
    monkeypatch.setattr(cpu,'launch_backend_cpu_child',start)
    def observed(group,event):
        assert group is state['ownership'] and event is None
        observations.append(group)
        if damage=='late_observation':original['clock'][0]=104.0
        return damage=='unresolved_group'
    monkeypatch.setattr(runtime,'_observe_owned',observed)
    for name in ('kill','killpg'):monkeypatch.setattr(os,name,lambda *args:pytest.fail('No PID/group signals'),raising=False)
    if damage=='none':
        result=run(ticket,environment={})
        assert result['status']=='completed' and result['returncode']==0 and result['pid']==391
        assert observations and state['phase']=='child_exited'
        assert os.fstat(state['guard_fd']).st_ino==state['guard_identity'][1]
    else:
        with pytest.raises(ValueError):run(ticket,environment={})
        assert ticket in cpu._UNRESOLVED
    assert state['child'] is child and original['admission'].snapshot()['active_scopes']==1
    for fd in state['pipe']:original['close'](fd)
    for stream in state.get('streams',[]):stream.close()


@pytest.mark.parametrize('damage',['none','foreign_request','before_completion','late','replay'])
def test_original_cpu_publication_ack_delivered_once_after_completed_frame(original,monkeypatch,damage):
    cpu,queue,ticket=cpu_queue(original,monkeypatch);state=cpu._PRODUCERS[ticket]
    ready=getattr(cpu,'completion_ready',None)
    assert callable(ready),'Original completion/publication handoff absent'
    # Explicitly modeled original child exit/validated public output; this
    # isolates the same original queue and deadline, not result-oracle proof.
    state.update(phase='child_exited',child_identity={'pid':391})
    completion={'schema_version':1,'kind':'cpu_execution_completed','request':original['frame'],
        'backend_proof':original['proof'],'worker_pid':391,'output_path':'owned/result.json','output_sha256':'a'*64}
    ready(ticket,completion)
    if damage!='before_completion':assert queue.completion_outgoing()==completion
    answer={'schema_version':1,'kind':'controller_cpu_publication_ack','nonce':original['frame']['nonce'],
        'request_id':original['frame']['request_id'],'request_sha256':cpu._sha(original['frame']),
        'completion_sha256':cpu._sha(completion),'receipt_sha256':'b'*64}
    if damage=='foreign_request':answer['request_id']='c'*32
    elif damage=='late':original['clock'][0]=104.0
    if damage in ('none','replay'):
        queue.deliver_publication(answer)
        if damage=='replay':
            with pytest.raises(h.HandshakeError):queue.deliver_publication(answer)
        else:
            assert queue.publication_result(ticket)==answer
            with pytest.raises(h.HandshakeError):queue.publication_result(ticket)
    else:
        with pytest.raises(h.HandshakeError):queue.deliver_publication(answer)
    assert original['admission'].snapshot()['active_scopes']==1


@pytest.mark.parametrize('damage',['none','guard_close','finish_ack','private_close','late_final_ack'])
def test_original_cpu_count_survives_publication_until_exact_final_ack(original,monkeypatch,damage):
    cpu,queue,ticket,state,child,requests=prepared_child(original,monkeypatch)
    finish=getattr(cpu,'finish_backend_source_cpu',None)
    assert callable(finish),'Exact original SOURCE CPU final ACK/count closure absent'
    state.update(phase='spawning',child=child);cpu._capture_backend_cpu_child(ticket,child)
    child.returncode=0;state['phase']='child_exited'
    completion={'schema_version':1,'kind':'cpu_execution_completed','request':original['frame'],
        'backend_proof':original['proof'],'worker_pid':391,'output_path':'owned/result.json','output_sha256':'a'*64}
    cpu.completion_ready(ticket,completion);assert queue.completion_outgoing()==completion
    ack={'schema_version':1,'kind':'controller_cpu_publication_ack','nonce':original['frame']['nonce'],
        'request_id':original['frame']['request_id'],'request_sha256':cpu._sha(original['frame']),
        'completion_sha256':cpu._sha(completion),'receipt_sha256':'b'*64}
    queue.deliver_publication(ack);events=[]
    def published(s,answer):
        assert s is state and answer==ack
        assert original['admission'].snapshot()['active_scopes']==1 and not s['guard_closed']
        events.append('independent_receipt')
    monkeypatch.setattr(cpu,'_backend_published_receipt',published,raising=False)
    def exchanged(cap,action,payload):
        assert cap is ticket and action=='finish'
        assert state['guard_closed'] and original['admission'].snapshot()['active_scopes']==1
        assert os.fstat(state['private']).st_ino==state['anchor_identity'][1]
        events.append('final_ack')
        if damage=='late_final_ack':original['clock'][0]=104.0
        return {'status':'refused' if damage=='finish_ack' else 'direct_exited'}
    monkeypatch.setattr(cpu,'_backend_cpu_exchange',exchanged)
    if damage=='guard_close':
        def ambiguous(*args):raise OSError('Original guard close uncertain')
        monkeypatch.setattr(state['guard'],'__exit__',ambiguous)
    elif damage=='private_close':
        close=os.close
        def ambiguous(fd):
            if fd==state['private']:raise OSError('Original private close uncertain')
            close(fd)
        monkeypatch.setattr(os,'close',ambiguous)
    if damage=='none':
        finish(ticket,completion)
        assert events==['independent_receipt','final_ack'] and state['phase']=='finished'
        assert original['admission'].snapshot()=={'active_scopes':0,'unsupported':[]}
        with pytest.raises(h.HandshakeError):finish(ticket,completion)
    else:
        with pytest.raises((ValueError,OSError)):finish(ticket,completion)
        assert ticket in cpu._UNRESOLVED and original['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}


@pytest.mark.parametrize('damage',['none','deadline_before_leave','socket_before_leave','done_before_leave',
    'deadline_after_leave','socket_after_leave','done_after_leave','decrement_then_raise'])
def test_original_cpu_final_count_transition_never_exposes_clean_unknown_drain(original,monkeypatch,damage):
    """Real original admission; no thread/child, callback/OS authority modeled.

    If the original leave consumes its count before raising, that count is not
    recreated. Sticky refusal must be published while its original condition is
    still held, before another drain can observe a clean empty admission.
    """
    cpu,queue,ticket,state,child,requests=prepared_child(original,monkeypatch)
    state.update(phase='spawning',child=child);cpu._capture_backend_cpu_child(ticket,child)
    child.returncode=0;state['phase']='child_exited'
    completion={'schema_version':1,'kind':'cpu_execution_completed','request':original['frame'],
        'backend_proof':original['proof'],'worker_pid':391,'output_path':'owned/result.json','output_sha256':'a'*64}
    cpu.completion_ready(ticket,completion);assert queue.completion_outgoing()==completion
    queue.deliver_publication({'schema_version':1,'kind':'controller_cpu_publication_ack','nonce':original['frame']['nonce'],
        'request_id':original['frame']['request_id'],'request_sha256':cpu._sha(original['frame']),
        'completion_sha256':cpu._sha(completion),'receipt_sha256':'b'*64})
    monkeypatch.setattr(cpu,'_backend_published_receipt',lambda *args:None)
    monkeypatch.setattr(cpu,'_backend_cpu_exchange',lambda *args:{'status':'direct_exited'})
    admission=original['admission'];events=[];real_original=cpu._original;real_leave=admission.leave
    real_uncovered=admission.uncovered;mutated=[False]
    def mutate():
        if damage.startswith('deadline'):original['clock'][0]=104.0
        elif damage.startswith('socket'):original['cache']['socket']=original['right']
        elif damage.startswith('done'):original['cache']['original_cpu_execution']['done'].set()
        mutated[0]=True
    def authenticated(cap):
        if state['phase']=='closing' and admission._condition._is_owned():
            assert queue._condition._is_owned(), 'Final admission and original queue must be held together'
            events.append(('locked_auth',admission.snapshot()['active_scopes']))
            if damage.endswith('before_leave') and not mutated[0]:mutate()
        return real_original(cap)
    def leave():
        assert admission._condition._is_owned(), 'Original final release must be observed atomically'
        assert queue._condition._is_owned(), 'Original final queue publication must remain held'
        events.append(('before_leave',admission.snapshot()['active_scopes']))
        real_leave();events.append(('after_leave',admission.snapshot()['active_scopes']))
        if damage.endswith('after_leave'):mutate()
        elif damage=='decrement_then_raise':raise OSError('Original count decremented before uncertain leave')
    def uncovered(reason):
        if (any(name=='after_leave' for name,count in events)
                and 'cpu_producer_unconfirmed' not in admission.snapshot()['unsupported']):
            assert admission._condition._is_owned(), 'Sticky refusal must precede final admission unlock'
            assert queue._condition._is_owned(), 'Sticky refusal must precede queue settlement unlock'
        real_uncovered(reason)
    monkeypatch.setattr(cpu,'_original',authenticated);monkeypatch.setattr(admission,'leave',leave)
    monkeypatch.setattr(admission,'uncovered',uncovered)
    if damage=='none':
        cpu.finish_backend_source_cpu(ticket,completion)
        assert state['phase']=='finished' and not state['counted'] and ticket not in cpu._ACTIVE
        assert events==[('locked_auth',1),('before_leave',1),('after_leave',0),('locked_auth',0)]
        assert admission.snapshot()=={'active_scopes':0,'unsupported':[]}
    else:
        with pytest.raises((h.HandshakeError,OSError)):cpu.finish_backend_source_cpu(ticket,completion)
        expected=1 if damage.endswith('before_leave') else 0
        assert admission.snapshot()=={'active_scopes':expected,'unsupported':['cpu_producer_unconfirmed']}
        assert ticket in cpu._UNRESOLVED and state['phase']=='unresolved'
        assert state['counted'] is (damage.endswith('before_leave') or damage=='decrement_then_raise')
        assert state.get('count_release_attempted',False) is (not damage.endswith('before_leave'))
        assert state.get('count_release_returned',False) is damage.endswith('after_leave')
        assert sum(name=='before_leave' for name,count in events)==(0 if damage.endswith('before_leave') else 1)
        assert mutated[0] or damage=='decrement_then_raise'
        admission.close();assert admission.drain(.01)['status']=='refused'


def test_backend_known_source_dispatch_uses_original_ticket_through_final_ack(original,monkeypatch):
    cpu,queue=module(),None
    # Dispatch/control path only: full result/oracle is modeled explicitly.
    ticket=object();calls=[];completion={'original':'completion'}
    monkeypatch.setattr(cpu,'admit_backend_source_cpu',lambda frame,proof,root:ticket)
    def admitted(frame,proof,root,fds,*,source_producer=None):
        assert source_producer is ticket and fds==()
        calls.append('validated_output');return completion
    monkeypatch.setattr(execution,'_execute_backend_admitted',admitted)
    monkeypatch.setattr(cpu,'completion_ready',lambda cap,result:calls.append('completed') if cap is ticket and result is completion else pytest.fail('Foreign completion'))
    monkeypatch.setattr(cpu,'finish_backend_source_cpu',lambda cap,result:calls.append('final_ack') if cap is ticket and result is completion else pytest.fail('Foreign finish'))
    assert execution.execute_backend(original['frame'],original['proof'],original['root']) is completion
    assert calls==['validated_output','completed','final_ack']


@pytest.mark.parametrize('damage',['none','preflight_interleave','foreign_child_ack','foreign_publication','late_publication','lost_endpoint'])
def test_original_backend_sole_service_forwards_proof_before_worker_done_and_retains_count(original,monkeypatch,damage):
    """Actual sole service/CPU queue with modeled worker/result/controller.

    No worker Thread or Popen runs. Its original execution object is supplied
    by the service itself; only worker staging of the already-covered result is
    modeled. Real admitted producer, socket/OFD, count, tokens, delivery and
    original service read/write order stay authoritative for this transport test.
    Neither a modeled child nor this proof releases the original counted scope.
    """
    from contextlib import nullcontext
    from backend.engine import migration_guard,application_preflight_child_relay as pf
    cpu=module();o=original;cache=o['cache'];cache['root']=o['root']
    monkeypatch.setattr(cpu.time,'monotonic',lambda:o['clock'][0])
    queue=cpu.CpuRelayQueue(cache);cache['source_cpu_queue']=queue
    preflight=pf.BackendRelayQueue(o['left']);cache['preflight_queue']=preflight
    monkeypatch.setattr(migration_guard,'maintenance_guard',lambda root:nullcontext())
    stop=threading.Event();original_thread=threading.current_thread();pending_frames=[o['frame']]
    reads=[];writes=[];bounds=[];facts={};completion={'schema_version':1,'kind':'cpu_execution_completed',
        'request':o['frame'],'backend_proof':o['proof'],'worker_pid':391,'output_path':'modeled/result.json',
        'output_sha256':'a'*64}
    def staged_start():
        # The service retains its own original callback dictionary before start.
        assert cache['original_cpu_execution']['thread'] is original_thread
        assert not cache['original_cpu_execution']['done'].is_set()
        cap=cpu.admit_backend_source_cpu(o['frame'],o['proof'],o['root']);facts['cap']=cap
        facts['token']=queue.enqueue(cap,'reserve',{'modeled_controller_plan':True})
        if damage=='preflight_interleave':
            facts['preflight_token']=preflight.enqueue({'kind':'backend_preflight_request','action':'finish','request_id':'9'*32},deadline=103.0)
    def staged_thread(**kwargs):
        assert kwargs['name']=='owned-controller-cpu' and kwargs['daemon'] is True
        monkeypatch.setattr(original_thread,'start',staged_start)
        return original_thread
    monkeypatch.setattr(threading,'Thread',staged_thread)
    def validated(actual,deadline):
        assert actual is cache and o['clock'][0]<deadline
        bounds.append(deadline);return o['proof']
    monkeypatch.setattr(h,'_validate_service_action',validated)
    def send(channel,value,*,absolute_deadline):
        assert channel is o['left'] and o['clock'][0]<absolute_deadline
        writes.append(copy.deepcopy(value))
        if value['kind']=='backend_preflight_request':
            assert absolute_deadline==103.0
            pending_frames.append({'kind':'controller_preflight_reply','original':'modeled transport reply'})
        elif value['kind']=='backend_cpu_child_request':
            assert absolute_deadline==104.0 and value['action']=='reserve'
            answer=cpu._cpu_reply(value,{'writer_id':'8'*32,'registration_sha256':'9'*64})
            if damage=='foreign_child_ack':answer['request_id']='f'*32
            if damage=='lost_endpoint':o['left'].close()
            pending_frames.append(answer)
        else:
            assert value==completion and absolute_deadline==104.0
            assert o['admission'].snapshot()['active_scopes']==1
            assert not cache['original_cpu_execution']['done'].is_set()
            answer={'schema_version':1,'kind':'controller_cpu_publication_ack','nonce':o['frame']['nonce'],
                'request_id':o['frame']['request_id'],'request_sha256':cpu._sha(o['frame']),
                'completion_sha256':cpu._sha(completion),'receipt_sha256':'b'*64}
            if damage=='foreign_publication':answer['completion_sha256']='f'*64
            if damage=='late_publication':o['clock'][0]=104.0
            pending_frames.append(answer)
    def read(channel,seconds):
        assert channel is o['left'] and pending_frames and 0<seconds<=10
        value=pending_frames.pop(0);reads.append(value['kind']);return value
    def selected(r,w,x,wait):
        assert r==[o['left']] and not w and not x and wait in (.02,.2)
        token=facts.get('preflight_token')
        if token is not None and pf._EXCHANGES[token]['done'] and not pf._EXCHANGES[token]['used']:
            assert preflight.result(token)['original']=='modeled transport reply'
        token=facts.get('token')
        if token is not None and cpu._CPU_EXCHANGES[token]['done'] and not cpu._CPU_EXCHANGES[token]['used']:
            assert queue.result(token)['payload']['writer_id']=='8'*32
            # Result execution is explicitly modeled; no OS birth, math or core
            # publication is claimed. The already counted ticket stays held.
            cpu._PRODUCERS[facts['cap']].update(phase='child_exited',child_identity={'pid':391,'created_at':1.0,'command_sha256':'a'*64})
            cpu.completion_ready(facts['cap'],completion)
        cap=facts.get('cap')
        if cap is not None and cpu._PRODUCERS[cap].get('publication_answer') is not None:
            assert queue.publication_result(cap)['receipt_sha256']=='b'*64
            assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
            stop.set()
        return (r if pending_frames else [],[],[])
    monkeypatch.setattr(h,'send_frame',send);monkeypatch.setattr(h,'read_frame',read)
    monkeypatch.setattr(h.select,'select',selected)
    if damage in ('none','preflight_interleave'):
        h.backend_execution_service(stop)
        assert stop.is_set() and queue._reader is preflight._reader is original_thread
        assert reads==(['cpu_execution_request','controller_cpu_child_reply','controller_cpu_publication_ack']
            if damage=='none' else ['cpu_execution_request','controller_preflight_reply','controller_cpu_child_reply','controller_cpu_publication_ack'])
        assert len([v for v in writes if v['kind']=='cpu_execution_completed'])==1
        assert not cache['original_cpu_execution']['done'].is_set()
        assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    else:
        with pytest.raises((ValueError,OSError,AssertionError)):h.backend_execution_service(stop)
        assert queue.unresolved and preflight.unresolved and o['left'].fileno()==-1
        assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
    assert bounds and max(bounds)<=110.0  # Initial request-only10s; CPU original104.


@pytest.mark.parametrize('damage',['busy_then_original','busy_expired','busy_socket_changed',
    'authentication_socket_changed','authentication_done','authentication_thread_changed','authentication_late'])
def test_original_producer_authentication_read_never_outlives_endpoint_callback_or_deadline(original,monkeypatch,damage):
    from backend.engine.application_launch_lease import LeaseTransitionBusy
    cpu,ticket=admit(original,monkeypatch);o=original;calls=[];foreign=[];validate=h._validate
    def authenticated(*args,**kwargs):
        calls.append(1)
        if damage.startswith('busy') and len(calls)==1:
            if damage=='busy_expired':o['clock'][0]=104.0
            if damage=='busy_socket_changed':
                a,b=socket.socketpair();foreign.extend((a,b));o['cache']['socket']=a
            raise LeaseTransitionBusy('Original temporary publication contention')
        result=validate(*args,**kwargs)
        if damage=='authentication_socket_changed':
            a,b=socket.socketpair();foreign.extend((a,b));o['cache']['socket']=a
        elif damage=='authentication_done':o['cache']['original_cpu_execution']['done'].set()
        elif damage=='authentication_thread_changed':o['cache']['original_cpu_execution']['thread']=object()
        elif damage=='authentication_late':o['clock'][0]=104.0
        return result
    waits=[]
    def waited(seconds):
        assert 0<seconds<=.005 and o['clock'][0]+seconds<104.0
        waits.append(seconds);o['clock'][0]+=seconds
    monkeypatch.setattr(h,'_validate',authenticated);monkeypatch.setattr(cpu.time,'sleep',waited)
    before=len(o['made'])
    try:
        if damage=='busy_then_original':
            with cpu.producer_transport(ticket) as fds:
                assert len(fds)==1 and os.fstat(fds[0]).st_ino==os.fstat(o['cache']['writer_private_fd']).st_ino
            assert len(calls)==3 and len(waits)==1
            assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
        else:
            with pytest.raises(ValueError):
                with cpu.producer_transport(ticket):pytest.fail('Stale original producer obtained transport')
            assert len(o['made'])==before and calls==[1] and waits==[]
            assert ticket in cpu._UNRESOLVED
            assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
    finally:
        for channel in foreign:channel.close()


@pytest.mark.parametrize('damage',['none','callback_error','lost_endpoint','late_done'])
def test_original_sole_service_waits_for_callback_and_settlement_after_count_reaches_zero(original,monkeypatch,damage):
    """Deterministic count/done scheduling; CPU result/core authentication modeled.

    Original service/queue/admission/Event/frame validators are real. No new
    thread, child, OS enrollment or mathematical result is claimed.
    """
    from contextlib import nullcontext
    from backend.engine import migration_guard,application_preflight_child_relay as pf
    cpu=module();o=original;cache=o['cache'];cache['root']=o['root']
    cache['challenge']['binding']={'modeled_original_binding':True}
    monkeypatch.setattr(cpu.time,'monotonic',lambda:o['clock'][0])
    queue=cpu.CpuRelayQueue(cache);cache['source_cpu_queue']=queue
    preflight=pf.BackendRelayQueue(o['left']);cache['preflight_queue']=preflight
    monkeypatch.setattr(migration_guard,'maintenance_guard',lambda root:nullcontext())
    monkeypatch.setattr(q,'inspect_epoch',lambda root,nonce:{'registry':{'state':'closed'},'registry_sha256':'8'*64})
    stop=threading.Event();original_thread=threading.current_thread();facts={};events=[];writes=[]
    writer=cache['challenge']['writer'];drain={'schema_version':1,'kind':'backend_drain_request','challenge':'9'*64,
        'request_id':'a'*32,'nonce':o['proof']['nonce'],'epoch':o['proof']['epoch'],
        'binding_sha256':cpu._sha(cache['challenge']['binding']),'backend_claim_sha256':cpu._sha(o['proof']),
        'writer_id':writer['writer_id'],'registration_sha256':writer['registration_sha256'],
        'closed_registry_sha256':'8'*64,'budget_ms':4000}
    frames=[o['frame'],drain];real_snapshot=o['admission'].snapshot
    completion={'schema_version':1,'kind':'cpu_execution_completed','request':o['frame'],
        'backend_proof':o['proof'],'worker_pid':391,'output_path':'modeled/result.json','output_sha256':'a'*64}
    def staged_start():
        cap=cpu.admit_backend_source_cpu(o['frame'],o['proof'],o['root']);facts['cap']=cap
    def staged_thread(**kwargs):
        assert kwargs['name']=='owned-controller-cpu' and kwargs['daemon'] is True
        monkeypatch.setattr(original_thread,'start',staged_start);return original_thread
    monkeypatch.setattr(threading,'Thread',staged_thread)
    def snapshot():
        if o['admission']._closed and not facts.get('released'):
            # Finish strictly after service checked done=False but before its
            # admission snapshot. Final result/cleanup is explicitly modeled.
            state=cpu._PRODUCERS[facts['cap']];execution=cache['original_cpu_execution']
            assert not execution['done'].is_set() and real_snapshot()['active_scopes']==1
            o['close'](state['private']);state.update(private_closed=True,snapshot_closed=True,
                guard_closed=True,counted=False,phase='finished',completion=copy.deepcopy(completion),
                publication_answer={'receipt_sha256':'b'*64},settlement_sent=False)
            cpu._ACTIVE.remove(facts['cap']);o['admission'].leave();execution['result']=copy.deepcopy(completion)
            facts['released']=True;events.append('count_zero_done_false')
        return real_snapshot()
    monkeypatch.setattr(o['admission'],'snapshot',snapshot)
    def selected(r,w,x,wait):
        assert r==[o['left']] and not w and not x and wait in (.02,.2)
        if facts.get('released') and not cache['original_cpu_execution']['done'].is_set():
            assert not writes,'No clean drain receipt before original callback settlement'
            if damage=='callback_error':cache['original_cpu_execution']['error']=ValueError('Original modeled callback failed')
            elif damage=='lost_endpoint':o['left'].close()
            elif damage=='late_done':o['clock'][0]=104.0
            cache['original_cpu_execution']['done'].set();events.append('original_done')
        return (r if frames else [],[],[])
    def validated(actual,deadline):
        assert actual is cache and o['clock'][0]<deadline
        return o['proof']
    def read(channel,seconds):
        assert channel is o['left'] and frames and 0<seconds<=10
        return frames.pop(0)
    def send(channel,value,*,absolute_deadline):
        assert channel is o['left'] and absolute_deadline==104.0
        writes.append(copy.deepcopy(value));events.append(value['kind'])
        assert cache['original_cpu_execution']['done'].is_set(),'Drain must retain original callback pending state'
        if value['kind']=='backend_managed_drain':stop.set()
    monkeypatch.setattr(h.select,'select',selected);monkeypatch.setattr(h,'_validate_service_action',validated)
    monkeypatch.setattr(h,'read_frame',read);monkeypatch.setattr(h,'send_frame',send)
    if damage=='none':
        h.backend_execution_service(stop)
        assert events==['count_zero_done_false','original_done','backend_source_cpu_settled','backend_managed_drain']
        assert [value['kind'] for value in writes]==['backend_source_cpu_settled','backend_managed_drain']
        assert writes[-1]['status']=='managed_scopes_drained' and writes[-1]['active_scopes']==0
        assert all(writes[-1][key] is False for key in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))
        assert real_snapshot()=={'active_scopes':0,'unsupported':[]}
    else:
        with pytest.raises((h.HandshakeError,OSError,AssertionError)):h.backend_execution_service(stop)
        assert not writes and queue.unresolved and preflight.unresolved and o['left'].fileno()==-1
        assert real_snapshot()=={'active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}
