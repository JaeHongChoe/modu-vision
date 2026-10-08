"""No-child CPU controller controls: real original epoch/CAS and fixed files.

Node OS authentication and registered project/plan authentication are explicitly
modeled. The original Node event table, all actual epoch publication checks and
bounded snapshot/command checks are exercised. No CPU math or child is started.
"""
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from backend.tests.test_application_launch_quiescence import epoch, digest
from backend.tests.test_application_backend_intent_composition import _allow_original_intent_validator_workers
from backend.engine import application_owned_cpu_child_relay as cpu

pytestmark=pytest.mark.no_child


def required(name):
    value=getattr(cpu,name,None)
    assert callable(value),'Original SOURCE CPU '+name+' is absent'
    return value


@pytest.fixture
def original_cpu(epoch,tmp_path,monkeypatch):
    from backend.engine import application_node_writer_authority as node, application_launch_lease as lease
    from backend.engine import application_launch_execution as execution
    q,root,_,core=epoch
    def forbidden(*args,**kwargs):raise AssertionError('No child/thread/model runtime is authorized')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    _allow_original_intent_validator_workers(monkeypatch)
    registration=core.enroll('backend',expected_registry_sha256=digest(core))
    binding=core.snapshot_binding
    sha=lambda value:hashlib.sha256(cpu._canonical(value)).hexdigest()
    proof={'nonce':core.nonce,'epoch':'c'*32,'binding_sha256':sha(binding),
        'process':lease._identity(os.getpid()),'kind':'backend_claim','frozen':False}
    owner=SimpleNamespace(root=root,nonce=core.nonce,_writer_epoch=core,_authenticated_backend_proof=proof)
    owner._owned=lambda:{'state':'ready','protocol_version':4,'writer_drain':{'phase':'enrolled'},'nonce':core.nonce}
    authority=node._new(node.NodeBackendAuthority);owner._node_backend_authority=authority
    state={'owner':owner,'epoch':core,'pid':os.getpid(),'thread':threading.current_thread(),
        'phase':'authenticated','deadline':None,'failed':False,'proof':proof,
        'writer_id':registration.writer_id,'registration_sha256':registration.registration_sha256,
        'main':SimpleNamespace(pid=os.getppid())}
    node._AUTHORITIES[authority]=state
    monkeypatch.setattr(node,'_fresh',lambda cap:(node._state(cap),owner._owned()))
    monkeypatch.setattr(node,'_parent_pid',lambda pid:os.getppid())
    core.bind_authenticated_node_backend(registration.writer_id,authority,expected_registry_sha256=digest(core))
    state['phase']='bound'
    request={'schema_version':1,'kind':'cpu_execution_request','request_id':'a'*32,'nonce':core.nonce,
        'epoch':proof['epoch'],'binding_sha256':proof['binding_sha256'],'backend_claim_sha256':sha(proof),
        'challenge':'d'*64,'workspace_id':'e'*32,'project_id':'f'*32,'plan_sha256':'1'*64}
    project=tmp_path/'project';snapshot=project/execution.OUTPUTS/('.owned-cpu-'+request['request_id'])
    (snapshot/'package').mkdir(parents=True);(snapshot/'runtime/backend').mkdir(parents=True)
    image=b'original bounded known image';source=b'# explicitly modeled reviewed source row\n'
    (snapshot/'input.png').write_bytes(image);(snapshot/'runtime/backend/controlled.py').write_bytes(source)
    manifest=cpu._canonical({'files':[{'path':'pipeline.json','size':2,'sha256':hashlib.sha256(b'{}').hexdigest()}]})
    (snapshot/'package/manifest.json').write_bytes(manifest);(snapshot/'package/pipeline.json').write_bytes(b'{}')
    (snapshot/'request.json').write_bytes(cpu._canonical({'package':str(snapshot/'package'),'image':str(snapshot/'input.png')}))
    plan={'kind':'owned_cpu_ocr_known_image_plan','device':'cpu','cpu_threads':1,'deadline_ms':4000,
        'package_manifest_sha256':hashlib.sha256(manifest).hexdigest(),'input_sha256':hashlib.sha256(image).hexdigest(),
        'runtime_source_sha256':'2'*64}
    capability={'plan':plan,'plan_sha256':request['plan_sha256'],'project_path':str(project),'scope_key':'3'*32}
    intent={'request':request,'capability':capability}
    def validate(frame,claim,actualroot):
        assert frame==request and claim==proof and actualroot==root
        return copy.deepcopy(intent)
    def admit(actualroot,workspace,projectid,digest):
        assert (actualroot,workspace,projectid,digest)==(root,request['workspace_id'],request['project_id'],request['plan_sha256'])
        return copy.deepcopy(capability)
    monkeypatch.setattr(execution,'validate_request',validate)
    monkeypatch.setattr(execution,'admit_plan',admit)
    # This isolates the snapshot read/copy validator; it is not an actual
    # authentication or full runtime-source inventory claim.
    rows={'backend/controlled.py':{'size':len(source),'sha256':hashlib.sha256(source).hexdigest()}}
    if hasattr(cpu,'_source_rows'):monkeypatch.setattr(cpu,'_source_rows',lambda expected:rows if expected=='2'*64 else {})
    clock=[100.0];monkeypatch.setattr(cpu.time,'monotonic',lambda:clock[0])
    def fixed():
        builder=required('source_command')
        command=builder(snapshot,7,104.0,request['request_id'])
        return {'cpu_request':copy.deepcopy(request),'budget_ms':4000,'deadline_monotonic':104.0,
            'workdir':str(snapshot),'gate_fd':7,'command':command}
    def event(action,payload):
        wire={'schema_version':1,'kind':'backend_cpu_child_request','action':action,'nonce':core.nonce,
            'epoch':proof['epoch'],'binding_sha256':proof['binding_sha256'],'backend_claim_sha256':sha(proof),
            'request_id':request['request_id'],'payload':payload}
        received=node._new(node.NodeBackendEvent)
        node._EVENTS[received]={'authority':authority,'frame':{'schema_version':1,'kind':'main_cpu_child_request',
            'nonce':core.nonce,'request':wire},'used':False}
        return received
    yield SimpleNamespace(q=q,root=root,core=core,owner=owner,node=node,node_state=state,proof=proof,
        request=request,capability=capability,intent=intent,snapshot=snapshot,fixed=fixed,event=event,clock=clock,sha=sha)


def test_original_cpu_reservation_is_distinct_one_use_epoch_authority(original_cpu):
    c=original_cpu;process=required('process_cpu_controller_event');plan=c.fixed();event=c.event('reserve',plan)
    answer=process(c.owner,event);rows=c.core.snapshot()['registry']['writers']
    assert [r['role'] for r in rows]==['backend','owned_cpu_worker']
    assert rows[1]['status']=='reserved' and answer['payload']['writer_id']==rows[1]['writer_id']
    from backend.engine import application_preflight_child_relay as pf
    cap=c.owner._cpu_relays[c.request['request_id']]
    assert type(cap) is cpu.ControllerCpuRelay and cap not in pf._ACTIVE and cap not in pf._CONTROLLERS
    before=digest(c.core)
    with pytest.raises(ValueError):process(c.owner,event)
    with pytest.raises(ValueError):process(c.owner,c.event('reserve',plan))
    assert digest(c.core)==before


@pytest.mark.parametrize('damage',['raw','forged_event','closed','nonce','frozen','expired','command','budget','request','extra','package','input','runtime','unlisted','symlink'])
def test_original_cpu_bad_reservation_cannot_write_registry(original_cpu,damage):
    c=original_cpu;process=required('process_cpu_controller_event');plan=c.fixed();event=c.event('reserve',plan)
    before=digest(c.core)
    if damage=='raw':event=c.node.frame_value(c.owner._node_backend_authority,event)
    elif damage=='forged_event':event=object.__new__(c.node.NodeBackendEvent)
    elif damage=='closed':c.core.close_epoch(expected_registry_sha256=before);before=digest(c.core)
    elif damage=='nonce':c.node._EVENTS[event]['frame']['request']['nonce']='b'*32
    elif damage=='frozen':c.proof['frozen']=True
    elif damage=='expired':plan['deadline_monotonic']=100.0
    elif damage=='command':plan['command'][0]='/bin/sh'
    elif damage=='budget':plan['budget_ms']=True
    elif damage=='request':plan['cpu_request']['challenge']='e'*64
    elif damage=='extra':plan['arbitrary_command']=True
    elif damage=='package':(c.snapshot/'package/pipeline.json').write_bytes(b'{changed}')
    elif damage=='input':(c.snapshot/'input.png').write_bytes(b'changed')
    elif damage=='runtime':(c.snapshot/'runtime/backend/controlled.py').write_bytes(b'changed')
    elif damage=='unlisted':(c.snapshot/'runtime/backend/extra.py').write_bytes(b'changed')
    elif damage=='symlink':
        (c.snapshot/'input.png').unlink();(c.snapshot/'input.png').symlink_to(c.snapshot/'request.json')
    if damage in ('expired','command','budget','request','extra'):c.node._EVENTS[event]['frame']['request']['payload']=plan
    with pytest.raises((ValueError,AssertionError)):process(c.owner,event)
    assert digest(c.core)==before


@pytest.mark.parametrize('boundary',['writer_lock','journal','node_final'])
def test_original_cpu_deadline_never_publishes_late_cas_ack(original_cpu,monkeypatch,boundary):
    c=original_cpu;process=required('process_cpu_controller_event');plan=c.fixed()
    directory=c.root/'.application-writer-epochs'/c.owner.nonce
    journal=(directory/'registry.json').read_bytes();pointer=(directory/'publication.json').read_bytes()
    if boundary=='node_final':
        fresh=c.node._fresh
        def cross(cap):
            result=fresh(cap)
            relay=getattr(c.owner,'_cpu_relays',{}).get(c.request['request_id'])
            if relay is not None and cpu._CONTROLLERS[relay]['phase']=='reserved':c.clock[0]=104.0
            return result
        monkeypatch.setattr(c.node,'_fresh',cross)
    else:
        def checkpoint(point):
            if point==('after_writer_lock' if boundary=='writer_lock' else 'after_registry_journal'):c.clock[0]=104.0
        monkeypatch.setattr(c.q,'_checkpoint',checkpoint)
    with pytest.raises(ValueError):process(c.owner,c.event('reserve',plan))
    cap=c.owner._cpu_relays[c.request['request_id']]
    assert cap in cpu._UNRESOLVED
    if boundary=='writer_lock':assert (directory/'registry.json').read_bytes()==journal and (directory/'publication.json').read_bytes()==pointer
    elif boundary=='journal':assert (directory/'registry.json').read_bytes()!=journal and (directory/'publication.json').read_bytes()==pointer
    else:assert c.core.snapshot()['registry']['writers'][1]['status']=='reserved'


def bound(c,monkeypatch):
    """Actual core rows; child birth/command is explicitly modeled, no Popen."""
    process=required('process_cpu_controller_event');plan=c.fixed()
    registration=process(c.owner,c.event('reserve',plan))['payload']
    child={'pid':123,'created_at':1.0,'command_sha256':'9'*64}
    def live(value,received,proof):assert value==child and received==plan and proof==c.proof
    monkeypatch.setattr(cpu,'_live_cpu_child',live,raising=False)
    payload={'registration':registration,'child':child,'plan_sha256':c.sha(plan)}
    answer=process(c.owner,c.event('bind',payload))
    assert answer['payload']=={'status':'bound'}
    return plan,registration,child,payload


def publication(c,monkeypatch,child):
    from backend.engine import application_launch_execution as execution,application_launch_lease as lease
    output_path=execution.OUTPUTS+'/'+c.request['request_id']+'.json'
    completion={'request':c.request,'worker_pid':child['pid'],'output_path':output_path,'output_sha256':'8'*64,
        'runtime_source_sha256':c.capability['plan']['runtime_source_sha256']}
    receipt={'request_id':c.request['request_id'],'worker_pid':child['pid'],'output_path':output_path,
        'output_sha256':'8'*64,'execution_scope':'controlled_source_backend','backend_frozen':False}
    # The epoch fixture deliberately has no ready main/bootstrap/CPU lease.
    # Model only this receipt pathname lookup so the never-started original
    # lease is not corrupted by an otherwise unlisted CPU history member.
    # Stable descriptor reads and before/after byte comparisons remain real.
    canonical=c.root/lease.LEASES/c.owner.nonce/'cpu-execution-receipt.json'
    folder=c.snapshot/'modeled-controller-publication';folder.mkdir()
    (folder/'cpu-execution-receipt.json').write_bytes(cpu._canonical(receipt))
    original_read=execution._read
    def receipt_read(path,*args,**kwargs):
        return original_read(folder/'cpu-execution-receipt.json' if Path(path)==canonical else path,*args,**kwargs)
    monkeypatch.setattr(execution,'_read',receipt_read)
    digest_=hashlib.sha256((folder/'cpu-execution-receipt.json').read_bytes()).hexdigest()
    row=c.owner._owned();row['cpu_execution']={'request_id':c.request['request_id'],'request_sha256':c.sha(c.request),
        'receipt_sha256':digest_}
    c.owner._owned=lambda:row
    # Independent result/oracle/registered-plan checks are explicitly modeled
    # here; original stable receipt bytes and exact request/child/output binding
    # are real and this test does not claim model output acceptance.
    monkeypatch.setattr(execution,'recheck_receipt_artifacts',lambda root,actualrow,value:
        c.capability if root==c.root and actualrow is row and value==receipt else pytest.fail('Foreign publication'))
    required('admit_controller_publication')(c.owner,completion,receipt,digest_)
    return completion,receipt,digest_,folder,row


def test_original_cpu_bound_row_requires_published_output_before_exact_finish(original_cpu,monkeypatch):
    c=original_cpu;plan,registration,child,payload=bound(c,monkeypatch)
    assert c.core.snapshot()['registry']['writers'][1]['status']=='active'
    completion,receipt,digest_,_,_=publication(c,monkeypatch,child)
    finish={**payload,'returncode':0,'completion_sha256':c.sha(completion),'receipt_sha256':digest_}
    answer=cpu.process_cpu_controller_event(c.owner,c.event('finish',finish))
    assert answer['payload']=={'status':'direct_exited'}
    row=c.core.snapshot()['registry']['writers'][1]
    assert row['process']==child and row['status']=='direct_exited' and row['exit_code']==0
    before=digest(c.core)
    with pytest.raises(ValueError):cpu.process_cpu_controller_event(c.owner,c.event('finish',finish))
    assert digest(c.core)==before


@pytest.mark.parametrize('boundary',['before_publication','after_receipt'])
def test_publication_ack_cannot_outlive_original_node_channel(original_cpu,monkeypatch,boundary):
    c=original_cpu;_,_,child,_=bound(c,monkeypatch)
    if boundary=='before_publication':
        def lost(cap):raise ValueError('Original Node endpoint lost')
        monkeypatch.setattr(c.node,'_fresh',lost)
    else:
        read=required('_receipt_current')
        def loss(state):
            value=read(state)
            def lost(cap):raise ValueError('Original Node endpoint lost after receipt')
            monkeypatch.setattr(c.node,'_fresh',lost)
            return value
        monkeypatch.setattr(cpu,'_receipt_current',loss)
    with pytest.raises(ValueError):publication(c,monkeypatch,child)
    cap=c.owner._cpu_relays[c.request['request_id']]
    assert cap in cpu._UNRESOLVED and cpu._CONTROLLERS[cap]['failed'] is True


def test_original_controller_sole_reader_dispatches_cpu_before_expected_proof(original_cpu,monkeypatch):
    from backend.engine import application_launch_controller as controller
    c=original_cpu;first=c.event('reserve',c.fixed());channel=object();c.node_state['channel']=channel
    last=c.node._new(c.node.NodeBackendEvent)
    expected={'kind':'cpu_execution_proof','original':'opaque data'}
    c.node._EVENTS[last]={'authority':c.owner._node_backend_authority,'frame':expected,'used':False}
    events=iter([first,last]);sent=[]
    monkeypatch.setattr(c.node,'receive_frame',lambda authority,remaining:next(events))
    def send(actual,answer,*,absolute_deadline):
        assert actual is channel and absolute_deadline==104.0
        assert answer['kind']=='controller_cpu_child_reply' and answer['action']=='reserve'
        sent.append(answer)
    monkeypatch.setattr(controller,'send_frame',send)
    assert controller._controller_reply(c.owner,104.0,'cpu_execution_proof')==expected
    assert len(sent)==1 and c.node._EVENTS[first]['used'] is True


@pytest.mark.parametrize('damage',['no_publication','foreign_child','exit1','raw_receipt','changed_receipt','changed_row','expired','drain_expired'])
def test_original_cpu_finish_refuses_without_original_publication_and_budget(original_cpu,monkeypatch,damage):
    c=original_cpu;plan,registration,child,payload=bound(c,monkeypatch)
    finish={**payload,'returncode':0,'completion_sha256':'7'*64,'receipt_sha256':'8'*64}
    if damage!='no_publication':
        completion,receipt,digest_,folder,row=publication(c,monkeypatch,child)
        finish.update(completion_sha256=c.sha(completion),receipt_sha256=digest_)
        if damage=='foreign_child':finish['child']={**child,'pid':124}
        elif damage=='exit1':finish['returncode']=1
        elif damage=='raw_receipt':finish['receipt_sha256']='f'*64
        elif damage=='changed_receipt':(folder/'cpu-execution-receipt.json').write_bytes(b'{}')
        elif damage=='changed_row':row['cpu_execution']['receipt_sha256']='e'*64
        elif damage=='expired':c.clock[0]=104.0
        elif damage=='drain_expired':c.node_state['deadline']=100.0
    before=digest(c.core)
    with pytest.raises(ValueError):cpu.process_cpu_controller_event(c.owner,c.event('finish',finish))
    assert digest(c.core)==before and c.core.snapshot()['registry']['writers'][1]['status']=='active'


@pytest.mark.parametrize('boundary',['bind_identity','finish_artifact','publication_journal'])
def test_original_cpu_bind_finish_expiry_cannot_advance_pointer(original_cpu,monkeypatch,boundary):
    c=original_cpu;process=required('process_cpu_controller_event');plan=c.fixed()
    if boundary=='bind_identity':
        registration=process(c.owner,c.event('reserve',plan))['payload'];child={'pid':123,'created_at':1.0,'command_sha256':'9'*64}
        calls=[]
        def live(*args):
            calls.append(1)
            if len(calls)==2:c.clock[0]=104.0
        monkeypatch.setattr(cpu,'_live_cpu_child',live,raising=False)
        event=c.event('bind',{'registration':registration,'child':child,'plan_sha256':c.sha(plan)})
    else:
        plan,registration,child,payload=bound(c,monkeypatch)
        completion,receipt,digest_,_,_=publication(c,monkeypatch,child)
        event=c.event('finish',{**payload,'returncode':0,'completion_sha256':c.sha(completion),'receipt_sha256':digest_})
        if boundary=='finish_artifact':
            original=required('_receipt_current')
            def crossing(state):result=original(state);c.clock[0]=104.0;return result
            monkeypatch.setattr(cpu,'_receipt_current',crossing)
        else:
            monkeypatch.setattr(c.q,'_checkpoint',lambda point:c.clock.__setitem__(0,104.0) if point=='after_registry_journal' else None)
    folder=c.root/'.application-writer-epochs'/c.owner.nonce
    journal=(folder/'registry.json').read_bytes();pointer=(folder/'publication.json').read_bytes()
    with pytest.raises(ValueError):process(c.owner,event)
    assert (folder/'publication.json').read_bytes()==pointer
    assert ((folder/'registry.json').read_bytes()!=journal)==(boundary=='publication_journal')
    assert c.owner._cpu_relays[c.request['request_id']] in cpu._UNRESOLVED


@pytest.mark.parametrize('damage',['none','request','completion','receipt','extra','active_row','replay',
    'expired','drain_expired','channel_before','channel_after','read_late','receipt_changed'])
def test_original_finished_cpu_settlement_requires_full_fresh_publication(original_cpu,monkeypatch,damage):
    c=original_cpu;_,_,child,payload=bound(c,monkeypatch)
    completion,receipt,digest_,folder,row=publication(c,monkeypatch,child)
    if damage!='active_row':
        cpu.process_cpu_controller_event(c.owner,c.event('finish',{**payload,'returncode':0,
            'completion_sha256':c.sha(completion),'receipt_sha256':digest_}))
    frame={'schema_version':1,'kind':'source_cpu_settled','nonce':c.owner.nonce,
        'request_id':c.request['request_id'],'request_sha256':c.sha(c.request),
        'completion_sha256':c.sha(completion),'receipt_sha256':digest_}
    cap=c.owner._cpu_relays[c.request['request_id']];state=cpu._CONTROLLERS[cap]
    if damage in ('request','completion','receipt'):
        key={'request':'request_sha256','completion':'completion_sha256','receipt':'receipt_sha256'}[damage]
        frame[key]='f'*64
    elif damage=='extra':frame['can_release_launch_lease']=True
    elif damage=='replay':assert cpu.admit_controller_settlement(c.owner,frame)==frame
    elif damage=='expired':c.clock[0]=104.0
    elif damage=='drain_expired':c.node_state['deadline']=100.0
    elif damage=='receipt_changed':(folder/'cpu-execution-receipt.json').write_bytes(b'{}')
    elif damage in ('channel_before','channel_after','read_late'):
        def lost(cap):raise ValueError('Original Node endpoint lost during settlement')
        if damage=='channel_before':monkeypatch.setattr(c.node,'_fresh',lost)
        else:
            original_read=cpu._receipt_current
            def crossing(value):
                answer=original_read(value)
                if damage=='read_late':c.clock[0]=104.0
                else:monkeypatch.setattr(c.node,'_fresh',lost)
                return answer
            monkeypatch.setattr(cpu,'_receipt_current',crossing)
    before=digest(c.core)
    if damage=='none':
        assert cpu.admit_controller_settlement(c.owner,frame)==frame
        assert state['settled'] is True and cap not in cpu._ACTIVE
        assert c.core.snapshot()['registry']['writers'][1]['status']=='direct_exited'
    else:
        with pytest.raises(ValueError):cpu.admit_controller_settlement(c.owner,frame)
        assert state.get('settled',False)==(damage=='replay')
        if damage!='active_row':assert cap in cpu._UNRESOLVED and state['failed'] is True
    assert digest(c.core)==before


def test_original_sole_controller_reader_consumes_settlement_before_managed_drain_proof(original_cpu,monkeypatch):
    from backend.engine import application_launch_controller as controller
    c=original_cpu;_,_,child,payload=bound(c,monkeypatch)
    completion,receipt,digest_,_,_=publication(c,monkeypatch,child)
    cpu.process_cpu_controller_event(c.owner,c.event('finish',{**payload,'returncode':0,
        'completion_sha256':c.sha(completion),'receipt_sha256':digest_}))
    frame={'schema_version':1,'kind':'source_cpu_settled','nonce':c.owner.nonce,
        'request_id':c.request['request_id'],'request_sha256':c.sha(c.request),
        'completion_sha256':c.sha(completion),'receipt_sha256':digest_}
    c.owner._cpu_awaiting_settlement=c.request['request_id']
    events=[]
    for value in (frame,{'kind':'managed_drain_proof','original':'independent receipt'}):
        event=c.node._new(c.node.NodeBackendEvent)
        c.node._EVENTS[event]={'authority':c.owner._node_backend_authority,'frame':value,'used':False}
        events.append(event)
    reads=[]
    def read(cap,remaining):
        assert cap is c.owner._node_backend_authority and remaining==4.0
        reads.append(remaining);return events.pop(0)
    monkeypatch.setattr(c.node,'receive_frame',read)
    answer=controller._controller_reply(c.owner,104.0,'managed_drain_proof')
    assert answer=={'kind':'managed_drain_proof','original':'independent receipt'}
    assert c.owner._cpu_settlement==frame and len(reads)==2 and not events
