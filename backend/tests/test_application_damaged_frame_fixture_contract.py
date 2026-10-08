"""Exact damaged-frame fixture contracts; no child/application/ASGI/model.

Captured raw refusals are evidence inputs, never authorizing capabilities.
Controller/lease authority is explicitly modeled; original nonce, typed receive,
canonical frame and invalid durable-state guards execute. Socketpairs are held
inert test transports and never listen. No actual fixture body is called.
"""
import ast
from contextlib import nullcontext
import copy
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
from types import SimpleNamespace

import pytest

from backend.tests import test_application_launch_quiescence_bridge as bridge
from backend.engine import application_launch_controller as controller
from backend.engine import application_launch_handshake as handshake
from backend.engine import application_launch_lease as lease
from backend.engine import application_node_writer_authority as node

pytestmark = pytest.mark.no_child
SERVICE = str(Path(bridge.__file__).resolve().parents[2]/'backend/engine/application_launch_handshake.py')
OWNER_REFUSAL = 'Backend challenge has no current spawned main owner'


@pytest.fixture(autouse=True)
def no_child_or_thread(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No child or thread launch belongs to this inert control')
    monkeypatch.setattr(subprocess.Popen, '__init__', forbidden)
    monkeypatch.setattr(threading.Thread, 'start', forbidden)


def captured(damage):
    """Portable inert model of the exact two observed first-cause contracts.

    Original raw captures remain pinned in private evidence; these values are
    deliberately synthetic, never reopened process/registry authority.
    """
    nonce='1'*32;binding={'installation_id':'2'*32,'update_id':'3'*32,'database_pointer':{'fence':1}}
    main={'pid':11001,'created_at':10.0,'command_sha256':'a'*64}
    backend={'pid':11002,'created_at':11.0,'command_sha256':'b'*64}
    cpu={'nonce':nonce,'epoch':'4'*32,'binding':binding,'main_process':main,'backend_process':backend,
         'backend_claim_sha256':'c'*64,'backend_executable':str(Path(bridge.__file__).resolve().parents[2]/'bin/backend_fixture.py')}
    writer={'writer_id':'5'*32,'role':'backend','status':'active','process':copy.deepcopy(backend),
            'registration_sha256':'d'*64,'exit_code':None,'reason_code':None}
    registry={'state':'open' if damage=='changed_nonce' else 'closed',
              'binding':{'nonce':nonce,'launch_binding_sha256':bridge.sha(bridge.canonical(binding))},
              'writers':[writer,{'writer_id':'6'*32,'role':'owned_cpu_worker','status':'unsupported',
                   'reason_code':'uncovered_protocol','process':None,'exit_code':None}]}
    # Keep the raw publication hash distinct from canonical reserialization.
    original_raw=json.dumps(registry).encode();registry_sha256=bridge.sha(original_raw)
    request=None
    if damage=='identical_request_replay':
        request={'schema_version':1,'kind':'backend_drain_request','challenge':'e'*64,'request_id':'7'*32,
                 'nonce':nonce,'epoch':cpu['epoch'],'binding_sha256':bridge.sha(bridge.canonical(binding)),
                 'backend_claim_sha256':cpu['backend_claim_sha256'],'writer_id':writer['writer_id'],
                 'registration_sha256':writer['registration_sha256'],'closed_registry_sha256':registry_sha256,'budget_ms':3953}
    reason=('Original main managed drain request differs or replayed' if damage=='changed_nonce'
            else 'Original controller reply kind differs')
    refusal={'state':'recovery_required','nonce':nonce,'binding':copy.deepcopy(binding),'process':copy.deepcopy(main),
             'reason':'Process-tree ownership is unresolved: '+reason,
             'writer_drain':{'phase':'enrolled' if damage=='changed_nonce' else 'closing',
               'writer_id':writer['writer_id'],'registration_sha256':writer['registration_sha256'],
               'request':request,'receipt':None,'backend_exit':None}}
    result={'main':main['pid'],'backend':backend['pid'],'signal':None,
            'direct_exit':1 if damage=='changed_nonce' else 0,'error':'Error: Original owned backend channel/ownership ended\n    at invalidate (original-owned-fixture)'}
    stderr=''
    if damage=='changed_nonce':
        stderr=('Traceback (most recent call last):\n'
                f'  File "{cpu["backend_executable"]}", line 65, in <module>\n'
                '    backend_execution_service(stop)\n'
                f'  File "{SERVICE}", line 843, in backend_execution_service\n'
                '    proof = _validate_service_action(original_cache, deadline)\n'
                f'  File "{SERVICE}", line 721, in _validate_service_action\n'
                '    proof = _validate(original_cache["root"], context[1], original_cache["challenge"])\n'
                f'  File "{SERVICE}", line 605, in _validate\n'
                f'    raise HandshakeError({OWNER_REFUSAL!r})\n'
                f'backend.engine.application_launch_handshake.HandshakeError: {OWNER_REFUSAL}\n')
    return refusal,result,cpu,registry,stderr,registry_sha256


def fixture_expectations(damage, refusal, result, cpu, registry, stderr, registry_sha256, tmp_path):
    """Run only the original real-fixture expectation AST, never its body.

    Before correction this selects the legacy reason/direct-exit expressions;
    after correction it selects the actual helper calls at those same points.
    It therefore reproduces the original two failed checks with no launch.
    """
    original = ast.parse(inspect.getsource(bridge.test_actual_controller_refuses_changed_and_replayed_original_main_drain_frames))
    body = next(node for node in original.body[0].body if isinstance(node, ast.Try)).body
    selected = []
    for item in body:
        code = ast.unparse(item)
        if (isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id=='expected' for target in item.targets)):
            selected.append(item)
        elif isinstance(item, ast.Assert) and code=="assert expected in refusal['reason']":
            selected.append(item)
        elif isinstance(item, ast.If) and ast.unparse(item.test)=="result['direct_exit'] == 1":
            selected.append(item)
        elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Call) and isinstance(item.value.func, ast.Name) and item.value.func.id.startswith('_assert_damaged_'):
            selected.append(item)
    assert selected, 'Original actual fixture expectation nodes were not found'
    projects = tmp_path/'projects';projects.mkdir()
    (projects/'execution-backend-error.txt').write_text(stderr)
    scope = dict(vars(bridge), damage=damage, refusal=refusal, result=result, cpu=cpu,
                 registry=registry, registry_sha256=registry_sha256, stderr=stderr, projects=projects)
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected,type_ignores=[])),bridge.__file__,'exec'),scope)


@pytest.mark.parametrize('damage',['changed_nonce','identical_request_replay'])
def test_captured_exact_first_refusal_and_original_stop_satisfy_fixture_contract(damage,tmp_path):
    fixture_expectations(damage,*captured(damage),tmp_path)


@pytest.fixture
def nonce_request(monkeypatch,tmp_path):
    nonce='1'*32;proof={'nonce':nonce,'epoch':'2'*32,'binding_sha256':'3'*64,'process':{'pid':321}}
    row={'process':{'pid':123},'writer_drain':{'phase':'enrolled'}};calls=[]
    class Epoch:
        def snapshot(self):
            calls.append('snapshot');raise AssertionError('Bad nonce reached original epoch')
    class Owner:
        root=tmp_path;_authenticated_backend_proof=proof;_writer_epoch=Epoch()
        def _owned(self):return row
        def _binding(self,value):assert value is row
        def _live(self,value,process):assert value is row and process is row['process']
        def publish_writer_drain(self,**kwargs):calls.append('publish');raise AssertionError('Bad nonce published')
    owner=Owner();owner.nonce=nonce
    frame={'schema_version':1,'kind':'main_drain_request','nonce':'0'*32,'epoch':proof['epoch'],
           'binding_sha256':proof['binding_sha256'],'backend_claim_sha256':bridge.sha(handshake._canonical(proof)),
           'request_id':'4'*32,'budget_ms':4000}
    monkeypatch.setattr(lease,'_transition_admission',lambda *_:nullcontext())
    monkeypatch.setattr(controller,'send_frame',lambda *_args,**_kwargs:pytest.fail('Bad nonce sent'))
    monkeypatch.setattr(node,'begin_drain',lambda *_:pytest.fail('Bad nonce enrolled drain'))
    return owner,frame,row,calls


def test_original_bad_nonce_refuses_before_epoch_publication_or_send(nonce_request):
    owner,frame,row,calls=nonce_request;before=copy.deepcopy(row)
    with pytest.raises(handshake.HandshakeError) as error:controller.prepare_drain(owner,frame)
    assert str(error.value)=='Original main managed drain request differs or replayed'
    assert row==before and calls==[]


@pytest.mark.parametrize('kind',['main_drain_request','unrelated_response'])
def test_original_typed_reply_rejects_unexpected_kind_without_publication_or_ack(monkeypatch,kind):
    left,peer=socket.socketpair();authority=node._new(node.NodeBackendAuthority)
    owner=SimpleNamespace(_node_backend_authority=authority,_bootstrap_channel=left)
    state={'channel':left,'failed':False};checks=[];clock=SimpleNamespace(now=10.)
    def fresh(value):
        assert value is authority;checks.append(clock.now);return state,{}
    monkeypatch.setattr(node,'_fresh',fresh)
    monkeypatch.setattr(controller.time,'monotonic',lambda:clock.now)
    reads=[];original=node.read_frame
    def read(channel,seconds):
        assert channel is left and seconds==4.;reads.append(seconds)
        return original(channel,seconds)
    monkeypatch.setattr(node,'read_frame',read)
    monkeypatch.setattr(controller,'send_frame',lambda *_args,**_kwargs:pytest.fail('Unexpected kind ACKed'))
    try:
        frame={'schema_version':1,'kind':kind,'nonce':'1'*32,'request_id':'2'*32}
        handshake.send_frame(peer,frame)
        with pytest.raises(handshake.HandshakeError) as error:controller._controller_reply(owner,14.,'managed_drain_proof')
        assert str(error.value)=='Original controller reply kind differs'
        assert reads==[4.] and checks==[10.,10.,10.] and not state['failed']
        # The held transport yielded a typed event. A decoded dictionary was
        # only a value copy and cannot become a new Node receive authority.
        with pytest.raises(handshake.HandshakeError):node.frame_value(authority,frame)
    finally:left.close();peer.close()


def test_original_typed_reply_does_not_reset_expired_absolute_deadline(monkeypatch):
    owner=SimpleNamespace(_node_backend_authority=object(),_bootstrap_channel=object())
    monkeypatch.setattr(controller.time,'monotonic',lambda:14.)
    monkeypatch.setattr(node,'receive_frame',lambda *_:pytest.fail('Expired original deadline read again'))
    with pytest.raises(handshake.HandshakeError) as error:controller._controller_reply(owner,14.,'managed_drain_proof')
    assert str(error.value)=='Original controller reply deadline expired'


@pytest.mark.parametrize('state',['recovery_required','exited','reserved'])
def test_original_backend_validator_refuses_noncurrent_owner_state_before_dispatch(monkeypatch,tmp_path,state):
    frame={key:None for key in handshake._CHALLENGE_FIELDS}
    frame.update(schema_version=1,kind='backend_challenge',challenge='a'*64,epoch='b'*32,nonce='c'*32,backend_pid=os.getpid())
    record={'nonce':frame['nonce'],'state':state,'spawn_attempted':True,'process':{'pid':123}}
    values={'VISION_APPLICATION_LAUNCH_NONCE':frame['nonce']};calls=[]
    monkeypatch.setattr(handshake,'_arguments',lambda root:calls.append('arguments'))
    monkeypatch.setattr(lease,'_transition_admission',lambda *_:nullcontext())
    monkeypatch.setattr(lease,'_load',lambda root:record)
    before=copy.deepcopy(record)
    with pytest.raises(handshake.HandshakeError) as error:handshake._validate(tmp_path,values,frame)
    assert str(error.value)==OWNER_REFUSAL and calls==['arguments'] and record==before


@pytest.mark.parametrize('mutation',[
 'generic_reason','legacy_reason','foreign_nonce','phase','request','receipt','backend_exit',
 'backend_pid','main_pid','signal','direct_exit','generic_error','stderr_unrelated',
 'stderr_foreign_source','stderr_other_class','stderr_other_reason','stderr_extra_error','stderr_hidden_error',
 'registry_open_closed','cleared_unsupported','backend_finished','wrong_backend_birth','wrong_registration',
 'foreign_registry_nonce','extra_writer'])
def test_exact_fixture_evidence_rejects_unrelated_or_released_state(mutation,tmp_path):
    refusal,result,cpu,registry,stderr,registry_sha256=captured('changed_nonce')
    if mutation=='generic_reason':refusal['reason']='Process-tree ownership is unresolved: unrelated error'
    elif mutation=='legacy_reason':refusal['reason']='Process-tree ownership is unresolved: Original main managed drain forwarding differs'
    elif mutation=='foreign_nonce':refusal['nonce']='0'*32
    elif mutation=='phase':refusal['writer_drain']['phase']='drained'
    elif mutation=='request':refusal['writer_drain']['request']={'kind':'backend_drain_request'}
    elif mutation=='receipt':refusal['writer_drain']['receipt']={}
    elif mutation=='backend_exit':refusal['writer_drain']['backend_exit']={}
    elif mutation=='backend_pid':result['backend']+=1
    elif mutation=='main_pid':result['main']+=1
    elif mutation=='signal':result['signal']='SIGTERM'
    elif mutation=='direct_exit':result['direct_exit']=2
    elif mutation=='generic_error':result['error']='Error: unrelated failure'
    elif mutation=='stderr_unrelated':stderr='Traceback (most recent call last):\nRuntimeError: unrelated'
    elif mutation=='stderr_foreign_source':stderr=stderr.replace(SERVICE,SERVICE+'.foreign')
    elif mutation=='stderr_other_class':stderr=stderr.replace('backend.engine.application_launch_handshake.HandshakeError:','RuntimeError:')
    elif mutation=='stderr_other_reason':stderr=stderr.replace(OWNER_REFUSAL,'arbitrary other error')
    elif mutation=='stderr_extra_error':stderr+='RuntimeError: concealed second failure\n'
    elif mutation=='stderr_hidden_error':stderr=stderr.replace('backend.engine.application_launch_handshake.HandshakeError:','RuntimeError: concealed second failure\nbackend.engine.application_launch_handshake.HandshakeError:')
    elif mutation=='registry_open_closed':registry['state']='closed'
    elif mutation=='cleared_unsupported':registry['writers']=[row for row in registry['writers'] if row['role']!='owned_cpu_worker']
    elif mutation=='backend_finished':next(row for row in registry['writers'] if row['role']=='backend').update(status='direct_exited',exit_code=0)
    elif mutation=='wrong_backend_birth':next(row for row in registry['writers'] if row['role']=='backend')['process']['created_at']+=1
    elif mutation=='wrong_registration':next(row for row in registry['writers'] if row['role']=='backend')['registration_sha256']='0'*64
    elif mutation=='foreign_registry_nonce':registry['binding']['nonce']='0'*32
    elif mutation=='extra_writer':registry['writers'].append(copy.deepcopy(registry['writers'][0]))
    with pytest.raises(AssertionError):fixture_expectations('changed_nonce',refusal,result,cpu,registry,stderr,registry_sha256,tmp_path)


@pytest.mark.parametrize('mutation',['legacy_reason','phase','nonce','request_kind','request_nonce',
 'request_epoch','request_claim','request_registration','receipt','backend_exit','deadline','raw_publication_hash',
 'cleared_unsupported','nonempty_cooperative_stderr'])
def test_exact_replay_refusal_cannot_accept_foreign_forwarding_or_released_state(mutation,tmp_path):
    refusal,result,cpu,registry,stderr,registry_sha256=captured('identical_request_replay')
    if mutation=='legacy_reason':refusal['reason']='Process-tree ownership is unresolved: Original main managed drain forwarding differs'
    elif mutation=='phase':refusal['writer_drain']['phase']='drained'
    elif mutation=='nonce':refusal['nonce']='0'*32
    elif mutation=='request_kind':refusal['writer_drain']['request']['kind']='main_drain_request'
    elif mutation=='request_nonce':refusal['writer_drain']['request']['nonce']='0'*32
    elif mutation=='request_epoch':refusal['writer_drain']['request']['epoch']='0'*32
    elif mutation=='request_claim':refusal['writer_drain']['request']['backend_claim_sha256']='0'*64
    elif mutation=='request_registration':refusal['writer_drain']['request']['registration_sha256']='0'*64
    elif mutation=='receipt':refusal['writer_drain']['receipt']={}
    elif mutation=='backend_exit':refusal['writer_drain']['backend_exit']={}
    elif mutation=='deadline':refusal['writer_drain']['request']['budget_ms']=4001
    elif mutation=='raw_publication_hash':registry_sha256=bridge.sha(bridge.canonical(registry))
    elif mutation=='cleared_unsupported':registry['writers'][1]['status']='direct_exited'
    elif mutation=='nonempty_cooperative_stderr':stderr='RuntimeError: hidden background failure\n'
    with pytest.raises(AssertionError):fixture_expectations('identical_request_replay',refusal,result,cpu,registry,stderr,registry_sha256,tmp_path)
