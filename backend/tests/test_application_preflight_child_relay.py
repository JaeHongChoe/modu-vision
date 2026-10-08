"""No-child relay controls precede any original app/child/CPU gate.

Protocol and queue controls use real anonymous sockets; OS authentication in
later no-child core controls is explicitly modeled, never actual app proof.
"""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from backend.tests.test_application_launch_quiescence import epoch, digest

pytestmark = pytest.mark.no_child


def api():
    try:
        return importlib.import_module('backend.engine.application_preflight_child_relay')
    except ModuleNotFoundError as exc:
        if exc.name == 'backend.engine.application_preflight_child_relay':
            pytest.fail('The original preflight child relay is missing')
        raise


def fixed_plan(tmp_path):
    return {'task': 'classification', 'device': 'cpu', 'stages': ['train'],
        'workdir': str(tmp_path), 'source_sha256': hashlib.sha256((Path(__file__).parents[1]/'engine/worker_preflight.py').read_bytes()).hexdigest(),
        'budget_ms': 30000, 'deadline_monotonic': time.monotonic()+30,
        'command': [sys.executable, '-m', 'backend.engine.worker_preflight', '--task', 'classification', '--device', 'cpu',
            '--stages', 'train', '--workdir', str(tmp_path), '--exit-with-parent', '--deadline', '60',
            '--writer-gate-fd', '7', '--writer-request', 'a'*32]}


def test_fixed_plan_accepts_explicit_existing_device_without_model_import(tmp_path):
    relay = api(); value = fixed_plan(tmp_path)
    verified = relay.validate_plan(value, request_id='a'*32)
    assert verified == value
    assert 'torch' not in sys.modules


@pytest.mark.parametrize('case',['exit_after_initial_read','ordinary','expired','foreign_exit','late_exit'])
def test_fixture_cleanup_races_original_stop_and_durable_exit_within_same_first_budget(tmp_path,monkeypatch,case):
    from backend.tests import test_application_preflight_child_relay_actual as actual
    from backend.engine import application_launch_lease as lease
    read=getattr(actual,'wait_original_fixture_stop',None)
    assert read is not None,'Original cleanup does not observe durable main exit while waiting for ordinary stop'
    now=[10.0];calls=[];ack={'nonce':'a'*32};binding={'original':True};deadline=10.02
    monkeypatch.setattr(actual.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(actual.time,'sleep',lambda value:now.__setitem__(0,now[0]+value))
    projects=tmp_path/'projects';projects.mkdir()
    original_backend={'pid':32,'created_at':1.0,'command_sha256':'b'*64}
    if case=='ordinary':(projects/'ordinary-stop-requested.json').write_text(json.dumps({'pid':32,'requested':True}))
    def load(root):
        assert root==tmp_path;calls.append(now[0]);row={'nonce':ack['nonce'],'binding':binding,'process':{'pid':31}}
        if case in ('exit_after_initial_read','foreign_exit','late_exit') and len(calls)>1:
            row['exit_observation']={'direct_child_pid':32 if case=='foreign_exit' else 31,'direct_child_returncode':0,'process_tree_exit_verified':False}
            if case=='late_exit':now[0]=deadline
        return row
    monkeypatch.setattr(lease,'_load',load)
    if case in ('expired','foreign_exit','late_exit'):
        with pytest.raises((ValueError,AssertionError)):read(tmp_path,ack,binding,deadline,original_backend=original_backend)
        assert all(value<deadline for value in calls) and now[0]<=deadline
    else:
        kind,row=read(tmp_path,ack,binding,deadline,original_backend=original_backend)
        assert kind==('exit' if case=='exit_after_initial_read' else 'ordinary')
        assert row['process']['pid']==31 and now[0]<deadline
        assert len(calls)==(2 if case=='exit_after_initial_read' else 1)
    assert 'torch' not in sys.modules


@pytest.mark.parametrize('stopped_pid',[32,31,33])
def test_fixture_ordinary_stop_requires_original_distinct_backend_not_main_or_foreign_pid(tmp_path,monkeypatch,stopped_pid):
    from backend.tests import test_application_preflight_child_relay_actual as actual
    from backend.engine import application_launch_lease as lease
    ack={'nonce':'a'*32};binding={'original':True};now=[10.0];deadline=10.02
    original_backend={'pid':32,'created_at':1.0,'command_sha256':'b'*64}
    row={'nonce':ack['nonce'],'binding':binding,'process':{'pid':31}}
    monkeypatch.setattr(actual.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(actual.time,'sleep',lambda value:now.__setitem__(0,now[0]+value))
    monkeypatch.setattr(lease,'_load',lambda root:row)
    projects=tmp_path/'projects';projects.mkdir()
    (projects/'ordinary-stop-requested.json').write_text(json.dumps({'pid':stopped_pid,'requested':True}))
    if stopped_pid==original_backend['pid']:
        kind,observed=actual.wait_original_fixture_stop(tmp_path,ack,binding,deadline,original_backend=original_backend)
        assert kind=='ordinary' and observed is row and now[0]<deadline
        assert original_backend['pid']!=observed['process']['pid']
    else:
        with pytest.raises((ValueError,AssertionError)):
            actual.wait_original_fixture_stop(tmp_path,ack,binding,deadline,original_backend=original_backend)
        assert now[0]<deadline
    assert 'torch' not in sys.modules


@pytest.mark.parametrize('original_backend',[None,{'pid':31,'created_at':1.0,'command_sha256':'b'*64}])
def test_fixture_ordinary_stop_without_retained_distinct_backend_refuses(tmp_path,monkeypatch,original_backend):
    from backend.tests import test_application_preflight_child_relay_actual as actual
    from backend.engine import application_launch_lease as lease
    now=[10.0];ack={'nonce':'a'*32};binding={'original':True}
    monkeypatch.setattr(actual.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(lease,'_load',lambda root:{'nonce':ack['nonce'],'binding':binding,'process':{'pid':31}})
    projects=tmp_path/'projects';projects.mkdir()
    (projects/'ordinary-stop-requested.json').write_text(json.dumps({'pid':31,'requested':True}))
    with pytest.raises(AssertionError):
        actual.wait_original_fixture_stop(tmp_path,ack,binding,10.02,original_backend=original_backend)
    assert now[0]==10.0 and 'torch' not in sys.modules


@pytest.mark.parametrize('damage', ['shell', 'module', 'task', 'device', 'stages', 'path', 'budget', 'late', 'source', 'extra'])
def test_invalid_plan_refuses_before_enrollment_or_popen(tmp_path, damage):
    relay = api(); value = fixed_plan(tmp_path)
    if damage == 'shell': value['command'][0:1] = ['/bin/sh', '-c']
    if damage == 'module': value['command'][2] = 'backend.main'
    if damage == 'task': value['task'] = 'unknown'
    if damage == 'device': value['device'] = 'automatic'
    if damage == 'stages': value['stages'] = ['train', 'unknown']
    if damage == 'path': value['workdir'] = str(tmp_path/'../escape')
    if damage == 'budget': value['budget_ms'] = True
    if damage == 'late': value['deadline_monotonic'] = time.monotonic()-1
    if damage == 'source': value['source_sha256'] = 'b'*64
    if damage == 'extra': value['arbitrary_command'] = True
    with pytest.raises(ValueError): relay.validate_plan(value, request_id='a'*32)


@pytest.mark.parametrize('name', ['ControllerPreflightRelay', 'BackendPreflightChild'])
def test_raw_constructor_and_forged_copy_do_not_mint_authority(name):
    relay = api(); cls = getattr(relay, name)
    for value in ({'pid': os.getpid()}, os.getpid(), None):
        with pytest.raises(ValueError): cls(value)
    forged = object.__new__(cls)
    with pytest.raises(ValueError): copy.copy(forged)
    with pytest.raises(ValueError): copy.deepcopy(forged)
    with pytest.raises(ValueError): relay.assert_original(forged)


def test_queue_binds_original_socket_and_allows_only_one_reader(tmp_path):
    relay = api(); left, right = socket.socketpair()
    try:
        queue = relay.BackendRelayQueue(left)
        queue.claim_reader()
        errors = []
        def foreign():
            try: queue.claim_reader()
            except ValueError as exc: errors.append(str(exc))
        other = threading.Thread(target=foreign); other.start(); other.join(2)
        assert len(errors) == 1
        with pytest.raises(ValueError): queue.claim_reader()
        with pytest.raises(ValueError): queue.take(right)
        left.close()
        with pytest.raises(ValueError): queue.take(left)
    finally:
        left.close(); right.close()


def test_one_exchange_is_counted_until_exact_reply_and_cannot_replay(tmp_path):
    relay = api(); left, right = socket.socketpair()
    queue = relay.BackendRelayQueue(left); queue.claim_reader()
    frame = {'kind': 'backend_preflight_request', 'action': 'reserve', 'request_id': 'a'*32}
    token = queue.enqueue(frame, deadline=time.monotonic()+5)
    try:
        assert queue.take(left) is token
        with pytest.raises(ValueError): queue.enqueue(frame, deadline=time.monotonic()+5)
        queue.complete(token, {'request_id': 'a'*32})
        assert queue.result(token) == {'request_id': 'a'*32}
        with pytest.raises(ValueError): queue.complete(token, {'request_id': 'a'*32})
        with pytest.raises(ValueError): queue.result(token)
        with pytest.raises(ValueError): queue.enqueue(frame, deadline=time.monotonic()+5)
    finally:
        left.close(); right.close()


def test_queue_failure_retains_unresolved_exchange_without_retry(tmp_path):
    relay = api(); left, right = socket.socketpair()
    queue = relay.BackendRelayQueue(left); queue.claim_reader()
    token = queue.enqueue({'kind': 'backend_preflight_request', 'action': 'reserve', 'request_id': 'a'*32}, deadline=time.monotonic()+5)
    try:
        assert queue.take(left) is token
        queue.fail(token, RuntimeError('Original callback failed'))
        with pytest.raises(ValueError): queue.result(token)
        assert queue.unresolved is True
        with pytest.raises(ValueError): queue.enqueue({'request_id': 'b'*32}, deadline=time.monotonic()+5)
        with pytest.raises(ValueError): queue.complete(token, {'request_id': 'a'*32})
    finally:
        left.close(); right.close()


@pytest.fixture
def modeled_origin(epoch, monkeypatch, tmp_path):
    """Real epoch/CAS, explicitly modeled Node OS authentication, no Popen."""
    from backend.engine import application_node_writer_authority as node, application_launch_lease as lease
    q, root, _, authority = epoch
    registration = authority.enroll('backend', expected_registry_sha256=digest(authority))
    binding = authority.snapshot_binding
    proof = {'nonce': authority.nonce, 'epoch': 'c'*32, 'binding_sha256': hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'process': lease._identity(os.getpid()), 'kind': 'backend_claim'}
    owner = SimpleNamespace(root=root, nonce=authority.nonce, _writer_epoch=authority, _authenticated_backend_proof=proof)
    owner._owned = lambda: {'state': 'ready', 'protocol_version': 4, 'writer_drain': {'phase': 'enrolled'}, 'nonce': owner.nonce}
    cap = node._new(node.NodeBackendAuthority); owner._node_backend_authority = cap
    state = {'owner': owner, 'epoch': authority, 'pid': os.getpid(), 'thread': threading.current_thread(),
        'phase': 'authenticated', 'deadline': None, 'failed': False, 'proof': proof,
        'writer_id': registration.writer_id, 'registration_sha256': registration.registration_sha256,
        'main': SimpleNamespace(pid=os.getppid())}
    node._AUTHORITIES[cap] = state
    monkeypatch.setattr(node, '_fresh', lambda c: (node._state(c), owner._owned()))
    monkeypatch.setattr(node, '_parent_pid', lambda pid: os.getppid())
    authority.bind_authenticated_node_backend(registration.writer_id, cap, expected_registry_sha256=digest(authority))
    state['phase'] = 'bound'
    def event(action, payload, request_id='a'*32):
        value = {'schema_version': 1, 'kind': 'backend_preflight_request', 'action': action,
            'nonce': owner.nonce, 'epoch': proof['epoch'], 'binding_sha256': proof['binding_sha256'],
            'backend_claim_sha256': hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'request_id': request_id, 'payload': payload}
        received = node._new(node.NodeBackendEvent)
        node._EVENTS[received] = {'authority': cap, 'frame': {'schema_version': 1, 'kind': 'main_preflight_request', 'nonce': owner.nonce, 'request': value}, 'used': False}
        return received
    yield q, owner, authority, node, event


def reserve_api():
    relay = api()
    assert callable(getattr(relay, 'process_controller_event', None)), 'Original controller relay publication is missing'
    return relay


def test_original_event_enrolls_preflight_once_and_cannot_replay(modeled_origin, tmp_path):
    q, owner, authority, node, event = modeled_origin; relay = reserve_api()
    original = event('reserve', fixed_plan(tmp_path))
    reply = relay.process_controller_event(owner, original)
    rows = authority.snapshot()['registry']['writers']
    assert [r['role'] for r in rows] == ['backend', 'preflight']
    assert rows[1]['status'] == 'reserved' and reply['payload']['writer_id'] == rows[1]['writer_id']
    before = digest(authority)
    with pytest.raises(ValueError): relay.process_controller_event(owner, original)
    assert digest(authority) == before
    with pytest.raises(ValueError): relay.process_controller_event(owner, event('reserve', fixed_plan(tmp_path)))
    assert digest(authority) == before


@pytest.mark.parametrize('damage', ['raw', 'copy', 'foreign_thread', 'closed', 'nonce', 'expired_plan'])
def test_original_controller_rejects_forged_foreign_and_late_reservation(modeled_origin, tmp_path, damage):
    q, owner, authority, node, event = modeled_origin; relay = reserve_api()
    original = event('reserve', fixed_plan(tmp_path)); before = digest(authority)
    if damage == 'raw': original = node.frame_value(owner._node_backend_authority, original)
    if damage == 'copy': original = object.__new__(node.NodeBackendEvent)
    if damage == 'closed':
        authority.close_epoch(expected_registry_sha256=before); before = digest(authority)
    if damage == 'nonce': node._EVENTS[original]['frame']['request']['nonce'] = 'b'*32
    if damage == 'expired_plan': node._EVENTS[original]['frame']['request']['payload']['deadline_monotonic'] = time.monotonic()-1
    if damage == 'foreign_thread':
        errors = []
        def foreign():
            try: relay.process_controller_event(owner, original)
            except ValueError as exc: errors.append(str(exc))
        other = threading.Thread(target=foreign); other.start(); other.join(3)
        assert len(errors) == 1
    else:
        with pytest.raises(ValueError): relay.process_controller_event(owner, original)
    assert digest(authority) == before
    assert len(authority.snapshot()['registry']['writers']) == 1


def test_partial_reservation_publication_is_retained_and_never_repaired(modeled_origin, tmp_path, monkeypatch):
    q, owner, authority, node, event = modeled_origin; relay = reserve_api()
    def fail(point):
        if point == 'after_registry_journal': raise OSError('Original publication interrupted')
    monkeypatch.setattr(q, '_checkpoint', fail)
    with pytest.raises(OSError): relay.process_controller_event(owner, event('reserve', fixed_plan(tmp_path)))
    cap = owner._preflight_relays['a'*32]
    assert cap in relay._UNRESOLVED
    with pytest.raises(ValueError): relay.process_controller_event(owner, event('reserve', fixed_plan(tmp_path)))
    with pytest.raises(ValueError): authority.snapshot()


@pytest.mark.parametrize('boundary',['reserve_lock','bind_identity','finish_identity'])
def test_original_producer_deadline_crossing_refuses_before_registry_publication(modeled_origin,tmp_path,monkeypatch,boundary):
    q,owner,authority,node,event=modeled_origin;relay=bind_api();plan=fixed_plan(tmp_path)
    clock=[time.monotonic()];monkeypatch.setattr(relay.time,'monotonic',lambda:clock[0])
    if boundary=='reserve_lock':
        before=digest(authority)
        def checkpoint(point):
            if point=='after_writer_lock':clock[0]=plan['deadline_monotonic']
        monkeypatch.setattr(q,'_checkpoint',checkpoint)
        original=event('reserve',plan)
    else:
        registration=relay.process_controller_event(owner,event('reserve',plan))['payload']
        child=modeled_child_binding(relay,monkeypatch,plan)
        if boundary=='bind_identity':
            visits=[]
            def live(*args):
                visits.append(1)
                if len(visits)==2:clock[0]=plan['deadline_monotonic']
            monkeypatch.setattr(relay,'_live_child',live)
            original=event('bind',{'registration':registration,'child':child,'plan_sha256':relay._sha(plan)})
        else:
            relay.process_controller_event(owner,event('bind',{'registration':registration,'child':child,'plan_sha256':relay._sha(plan)}))
            core_exit=relay._core_child_exit
            def expires(*args):
                value=core_exit(*args);clock[0]=plan['deadline_monotonic'];return value
            monkeypatch.setattr(relay,'_core_child_exit',expires)
            original=event('finish',{'registration':registration,'child':child,'plan_sha256':relay._sha(plan),'returncode':0,'cleanup_confirmed':True})
        before=digest(authority)
    directory=authority.root/'.application-writer-epochs'/authority.nonce
    protected={name:(directory/name).read_bytes() for name in ('registry.json','publication.json')}
    with pytest.raises(ValueError):relay.process_controller_event(owner,original)
    assert protected=={name:(directory/name).read_bytes() for name in protected},'Expired producer plan was published before refusal'
    cap=owner._preflight_relays['a'*32]
    assert cap in relay._UNRESOLVED
    with pytest.raises(ValueError):relay.process_controller_event(owner,original)
    assert protected=={name:(directory/name).read_bytes() for name in protected}
    if boundary=='reserve_lock':
        # The created but never registered writer lock is retained; registry
        # validation must refuse the unknown member, never delete or adopt it.
        with pytest.raises(ValueError):authority.snapshot()
    else:assert digest(authority)==before


@pytest.mark.parametrize('boundary',['already_expired','ofd_read','original_drain'])
def test_expired_original_backend_plan_prevents_popen_and_retains_count(modeled_backend_holder,monkeypatch,boundary):
    import subprocess
    relay,handshake,holder,ticket,cache,admission=modeled_backend_holder
    state=relay.assert_original(holder);state.update(phase='reserved',guard_fd=7,pipe=(8,9))
    deadline=state['plan']['deadline_monotonic'];clock=[time.monotonic()]
    monkeypatch.setattr(relay.time,'monotonic',lambda:clock[0])
    if boundary=='already_expired':clock[0]=deadline
    elif boundary=='original_drain':
        queue=relay.BackendRelayQueue(cache['socket']);queue.claim_reader();cache['preflight_queue']=queue
        queue.close_admission(clock[0])
    else:
        fstat=relay.os.fstat
        def crossing(fd):
            value=fstat(fd)
            if fd==cache['writer_private_fd']:clock[0]=deadline
            return value
        monkeypatch.setattr(relay.os,'fstat',crossing)
    started=[]
    def forbidden(*args,**kwargs):started.append(args);raise AssertionError('An expired original plan invoked Popen')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    with pytest.raises(ValueError):relay.launch_backend_child(holder,log=None,environment={},inherited=())
    assert started==[] and state['phase']=='reserved'
    assert holder in relay._UNRESOLVED and ticket in handshake._PREFLIGHT_UNRESOLVED
    assert admission.snapshot()['active_scopes']==1


@pytest.mark.parametrize('action',['reserve','bind','finish'])
def test_expiry_during_original_registry_write_retains_partial_bytes_and_never_acknowledges(modeled_origin,tmp_path,monkeypatch,action):
    q,owner,authority,node,event=modeled_origin;relay=bind_api();plan=fixed_plan(tmp_path)
    clock=[time.monotonic()];monkeypatch.setattr(relay.time,'monotonic',lambda:clock[0])
    if action=='reserve':original=event('reserve',plan)
    else:
        registration=relay.process_controller_event(owner,event('reserve',plan))['payload']
        child=modeled_child_binding(relay,monkeypatch,plan)
        payload={'registration':registration,'child':child,'plan_sha256':relay._sha(plan)}
        if action=='finish':
            relay.process_controller_event(owner,event('bind',payload))
            payload={**payload,'returncode':0,'cleanup_confirmed':True}
        original=event(action,payload)
    directory=authority.root/'.application-writer-epochs'/authority.nonce
    journal=(directory/'registry.json').read_bytes();pointer=(directory/'publication.json').read_bytes()
    def checkpoint(point):
        if point=='after_registry_journal':clock[0]=plan['deadline_monotonic']
    monkeypatch.setattr(q,'_checkpoint',checkpoint)
    with pytest.raises(ValueError):relay.process_controller_event(owner,original)
    assert (directory/'registry.json').read_bytes()!=journal
    assert (directory/'publication.json').read_bytes()==pointer,'Expired producer published the second CAS record'
    cap=owner._preflight_relays['a'*32];assert cap in relay._UNRESOLVED
    with pytest.raises(ValueError):authority.snapshot()
    with pytest.raises(ValueError):relay.process_controller_event(owner,original)
    assert (directory/'publication.json').read_bytes()==pointer


def test_reservation_final_original_drain_expiry_never_acknowledges(modeled_origin,tmp_path,monkeypatch):
    q,owner,authority,node,event=modeled_origin;relay=bind_api();plan=fixed_plan(tmp_path)
    original_node=node._AUTHORITIES[owner._node_backend_authority];fresh=node._fresh
    def crossing(cap):
        value=fresh(cap);held=getattr(owner,'_preflight_relays',{}).get('a'*32)
        if held is not None and relay._CONTROLLERS[held]['phase']=='reserved':original_node['deadline']=time.monotonic()
        return value
    monkeypatch.setattr(node,'_fresh',crossing)
    with pytest.raises(ValueError):relay.process_controller_event(owner,event('reserve',plan))
    held=owner._preflight_relays['a'*32]
    assert held in relay._UNRESOLVED and authority.snapshot()['registry']['writers'][1]['status']=='reserved'


def test_backend_reservation_guard_acquisition_expiry_retains_without_return(modeled_backend_holder,tmp_path,monkeypatch):
    relay,handshake,holder,ticket,cache,admission=modeled_backend_holder
    from backend.engine import application_launch_quiescence as q
    cache.update(challenge={'writer':{}},root=tmp_path)
    clock=[time.monotonic()];deadline=clock[0]+30;monkeypatch.setattr(relay.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(relay,'_mint_backend_holder',lambda original:holder)
    monkeypatch.setattr(relay,'_backend_exchange',lambda *args:{'writer_id':'b'*32,'registration_sha256':'c'*64})
    class Guard:
        def __enter__(self):clock[0]=deadline;return SimpleNamespace(pass_fds=(cache['writer_private_fd'],))
        def __exit__(self,*args):raise AssertionError('Ambiguous reservation guard must remain held')
    monkeypatch.setattr(q,'writer_guard',lambda *args,**kwargs:Guard())
    try:
        with pytest.raises(ValueError):relay.reserve_backend_child(ticket,task='classification',device='cpu',stages=['train'],workdir=tmp_path,limit=30,deadline=deadline)
        assert holder in relay._UNRESOLVED and ticket in handshake._PREFLIGHT_UNRESOLVED
        assert relay._CHILDREN[holder]['phase']=='creating' and admission.snapshot()['active_scopes']==1
    finally:
        # Controlled fixture only: no producer/child exists and these exact
        # original pipe handles were never closed by the tested path.
        for fd in relay._CHILDREN[holder]['pipe']:os.close(fd)


def test_backend_startup_gate_post_write_expiry_never_promotes_active(modeled_backend_holder,monkeypatch):
    """Modeled child identity only; original real local pipe, no Popen started."""
    relay,handshake,holder,ticket,cache,admission=modeled_backend_holder
    from backend.engine import application_launch_lease as lease
    class ControlledChild:
        pid=31
        def poll(self):return None
    child=ControlledChild();state=relay.assert_original(holder);pipe=os.pipe();identity={'pid':31}
    info=os.fstat(cache['writer_private_fd']);state.update(phase='spawning',child=child,guard_fd=cache['writer_private_fd'],guard_identity=(info.st_dev,info.st_ino),pipe=pipe)
    monkeypatch.setattr(relay,'_ORIGINAL_POPEN_TYPE',ControlledChild)
    monkeypatch.setattr(lease,'_identity',lambda pid:identity)
    monkeypatch.setattr(relay,'_live_child',lambda *args:None)
    monkeypatch.setattr(relay,'_backend_exchange',lambda *args:{'status':'bound'})
    clock=[time.monotonic()];deadline=state['plan']['deadline_monotonic'];monkeypatch.setattr(relay.time,'monotonic',lambda:clock[0])
    write=relay.os.write;close=relay.os.close;writes=[];closed=[]
    def crossing(fd,raw):
        value=write(fd,raw);writes.append(json.loads(raw));clock[0]=deadline;return value
    def original_close(fd):
        value=close(fd);closed.append(fd);return value
    monkeypatch.setattr(relay.os,'write',crossing);monkeypatch.setattr(relay.os,'close',original_close)
    try:
        with pytest.raises(ValueError):relay._capture_backend_child(holder,child)
        assert state['phase']=='spawning' and holder in relay._UNRESOLVED
        assert ticket in handshake._PREFLIGHT_UNRESOLVED and admission.snapshot()['active_scopes']==1
        assert len(writes)==1 and writes[0]['deadline']==deadline
    finally:
        for fd in pipe:
            if fd not in closed:close(fd)


def bind_api():
    relay = reserve_api()
    assert callable(getattr(relay, '_core_child_binding', None)), 'Original backend child binding is missing'
    return relay


def modeled_child_binding(relay, monkeypatch, plan):
    identity = {'pid': 123456, 'created_at': 1000.0, 'command_sha256': hashlib.sha256(json.dumps(plan['command'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    monkeypatch.setattr(relay, '_live_child', lambda value, expected, backend: None)
    return identity


def test_child_binding_and_cleanup_finalization_remain_separate_from_direct_popen(modeled_origin, tmp_path, monkeypatch):
    q, owner, authority, node, event = modeled_origin; relay = bind_api(); plan = fixed_plan(tmp_path)
    registration = relay.process_controller_event(owner, event('reserve', plan))['payload']
    child = modeled_child_binding(relay, monkeypatch, plan)
    bound = relay.process_controller_event(owner, event('bind', {'registration': registration, 'child': child, 'plan_sha256': relay._sha(plan)}))
    assert bound['payload'] == {'status': 'bound'}
    assert authority.snapshot()['registry']['writers'][1]['status'] == 'active'
    assert registration['writer_id'] not in authority._handles
    authority.close_epoch(expected_registry_sha256=digest(authority))
    finished = relay.process_controller_event(owner, event('finish', {'registration': registration, 'child': child,
        'plan_sha256': relay._sha(plan), 'returncode': 0, 'cleanup_confirmed': True}))
    assert finished['payload'] == {'status': 'direct_exited'}
    assert authority.snapshot()['registry']['writers'][1]['status'] == 'direct_exited'
    with pytest.raises(ValueError): relay.process_controller_event(owner, event('finish', {'registration': registration, 'child': child,
        'plan_sha256': relay._sha(plan), 'returncode': 0, 'cleanup_confirmed': True}))


@pytest.mark.parametrize('damage', ['nonzero', 'unconfirmed_cleanup', 'changed_child', 'late', 'copied_cap', 'closed_before_bind', 'changed_deadline_pin', 'changed_source_pin'])
def test_uncertain_child_and_cleanup_never_promote_or_repair(modeled_origin, tmp_path, monkeypatch, damage):
    q, owner, authority, node, event = modeled_origin; relay = bind_api(); plan = fixed_plan(tmp_path)
    registration = relay.process_controller_event(owner, event('reserve', plan))['payload']
    child = modeled_child_binding(relay, monkeypatch, plan)
    if damage == 'closed_before_bind':
        authority.close_epoch(expected_registry_sha256=digest(authority))
        with pytest.raises(ValueError): relay.process_controller_event(owner, event('bind', {'registration': registration, 'child': child, 'plan_sha256': relay._sha(plan)}))
        assert authority.snapshot()['registry']['writers'][1]['status'] == 'reserved'
        return
    relay.process_controller_event(owner, event('bind', {'registration': registration, 'child': child, 'plan_sha256': relay._sha(plan)}))
    payload = {'registration': registration, 'child': child, 'plan_sha256': relay._sha(plan), 'returncode': 0, 'cleanup_confirmed': True}
    cap = owner._preflight_relays['a'*32]
    if damage == 'nonzero': payload['returncode'] = 1
    if damage == 'unconfirmed_cleanup': payload['cleanup_confirmed'] = False
    if damage == 'changed_child': payload['child'] = {**child, 'created_at': 1001.0}
    if damage == 'late': relay._CONTROLLERS[cap]['plan']['deadline_monotonic'] = time.monotonic()-1
    if damage in ('changed_deadline_pin','changed_source_pin'):
        changed=copy.deepcopy(plan)
        if damage=='changed_deadline_pin':changed['deadline_monotonic']+=1
        else:changed['source_sha256']='d'*64
        payload['plan_sha256']=relay._sha(changed)
    if damage == 'copied_cap':
        owner._preflight_relays['a'*32] = object.__new__(relay.ControllerPreflightRelay)
    with pytest.raises(ValueError): relay.process_controller_event(owner, event('finish', payload))
    assert authority.snapshot()['registry']['writers'][1]['status'] == 'active'
    if damage != 'copied_cap':
        assert cap in relay._UNRESOLVED
        with pytest.raises(ValueError): relay.process_controller_event(owner, event('finish', {**payload, 'returncode': 0, 'cleanup_confirmed': True}))


def test_partial_child_exit_publication_consumes_original_event_and_retains(modeled_origin, tmp_path, monkeypatch):
    q, owner, authority, node, event = modeled_origin; relay = bind_api(); plan = fixed_plan(tmp_path)
    registration = relay.process_controller_event(owner, event('reserve', plan))['payload']
    child = modeled_child_binding(relay, monkeypatch, plan)
    relay.process_controller_event(owner, event('bind', {'registration': registration, 'child': child, 'plan_sha256': relay._sha(plan)}))
    original = event('finish', {'registration': registration, 'child': child, 'plan_sha256': relay._sha(plan), 'returncode': 0, 'cleanup_confirmed': True})
    def fail(point):
        if point == 'after_registry_journal': raise OSError('Original exit publication interrupted')
    monkeypatch.setattr(q, '_checkpoint', fail)
    with pytest.raises(OSError): relay.process_controller_event(owner, original)
    assert owner._preflight_relays['a'*32] in relay._UNRESOLVED
    with pytest.raises(ValueError): relay.process_controller_event(owner, original)
    with pytest.raises(ValueError): authority.snapshot()


def test_queue_reply_cannot_be_consumed_after_original_channel_close():
    relay = api(); left, right = socket.socketpair()
    try:
        queue = relay.BackendRelayQueue(left); queue.claim_reader()
        token = queue.enqueue({'request_id': 'a'*32, 'action': 'reserve'}, deadline=time.monotonic()+5)
        queue.take(left); queue.complete(token, {'status': 'reserved'})
        left.close()
        with pytest.raises(ValueError): queue.result(token)
        assert queue.unresolved
    finally: left.close(); right.close()


@pytest.fixture
def modeled_backend_holder(monkeypatch, tmp_path):
    """Models backend ticket/cache only; real socket, no Popen or app launch."""
    from backend.engine import application_launch_handshake as handshake
    relay = api()
    assert callable(getattr(relay, '_mint_backend_holder', None)), 'Original backend child holder is missing'
    left, right = socket.socketpair(); anchor = os.open(tmp_path/'anchor', os.O_CREAT|os.O_RDWR, 0o600)
    admission = handshake.BackendWorkAdmission(); admission._begin_producer()
    cache = {'socket': left, 'writer_private_fd': anchor, 'writer_fd_identity': (os.fstat(anchor).st_dev, os.fstat(anchor).st_ino),
        'proof': {'nonce': 'd'*32, 'epoch': 'c'*32, 'binding_sha256': 'e'*64, 'process': {'pid': os.getpid()}}, 'ready': True}
    monkeypatch.setattr(handshake, '_CACHE', cache); monkeypatch.setattr(handshake, '_context', lambda: ())
    ticket = handshake._mint_preflight_ticket('cpu', (), admission); ticket.claim()
    holder = relay._mint_backend_holder(ticket)
    relay.assert_original(holder).update(plan=fixed_plan(tmp_path), request_id='a'*32,
        registration={'writer_id': 'b'*32, 'registration_sha256': 'c'*64})
    yield relay, handshake, holder, ticket, cache, admission
    # Explicit controlled fixture ownership only. Strong unresolved references
    # remain in tables; never interpret fixture cleanup as authority completion.
    left.close(); right.close(); os.close(anchor)


@pytest.mark.parametrize('damage', ['dict', 'pid', 'subclass', 'copy', 'foreign_thread', 'callback', 'close'])
def test_backend_exact_holder_failure_retains_count_and_never_reuses(modeled_backend_holder, monkeypatch, damage):
    relay, handshake, holder, ticket, cache, admission = modeled_backend_holder
    state = relay.assert_original(holder)
    # No-child controlled terminal data, explicitly not an original Popen proof.
    state.update(phase='cleanup', child=object(), returncode=0, child_identity={'pid': 123456}, cleanup_confirmed=True)
    closed = []
    def release():
        closed.append(1)
        if damage == 'close': raise OSError('Original OFD close was interrupted')
    state['close_guard'] = release
    def exchange(cap, action, payload):
        if damage == 'callback': raise RuntimeError('Original relay callback failed')
        return {'status': 'direct_exited'}
    monkeypatch.setattr(relay, '_backend_exchange', exchange)
    invalid = holder
    if damage == 'dict': invalid = {'pid': os.getpid()}
    if damage == 'pid': invalid = os.getpid()
    if damage == 'copy': invalid = object.__new__(relay.BackendPreflightChild)
    if damage == 'subclass':
        class Sub(relay.BackendPreflightChild): pass
        invalid = object.__new__(Sub)
    if damage == 'foreign_thread':
        errors = []
        def foreign():
            try: relay.finish_backend_child(holder)
            except ValueError: errors.append(True)
        thread = threading.Thread(target=foreign); thread.start(); thread.join(2)
        assert errors == [True] and closed == []
    else:
        with pytest.raises((ValueError, OSError, RuntimeError)): relay.finish_backend_child(invalid)
    assert admission.snapshot()['active_scopes'] == 1
    if damage in ('close', 'callback'):
        assert holder in relay._UNRESOLVED and ticket in handshake._PREFLIGHT_UNRESOLVED
        with pytest.raises(ValueError): relay.finish_backend_child(holder)
        assert closed == [1]


def test_already_enrolled_finish_uses_original_drain_deadline(modeled_origin, tmp_path, monkeypatch):
    q, owner, authority, node, event = modeled_origin; relay = bind_api(); plan = fixed_plan(tmp_path)
    registration = relay.process_controller_event(owner, event('reserve', plan))['payload']
    child = modeled_child_binding(relay, monkeypatch, plan)
    relay.process_controller_event(owner, event('bind', {'registration': registration, 'child': child, 'plan_sha256': relay._sha(plan)}))
    authority.close_epoch(expected_registry_sha256=digest(authority))
    original = node._AUTHORITIES[owner._node_backend_authority]; original['deadline'] = time.monotonic()+4
    before = digest(authority)
    with pytest.raises(ValueError): relay.process_controller_event(owner, event('reserve', fixed_plan(tmp_path), 'b'*32))
    assert digest(authority) == before
    relay.process_controller_event(owner, event('finish', {'registration': registration, 'child': child,
        'plan_sha256': relay._sha(plan), 'returncode': 0, 'cleanup_confirmed': True}))
    assert authority.snapshot()['registry']['writers'][1]['status'] == 'direct_exited'


def test_queue_exchange_is_opaque_and_immutable():
    relay = api(); left, right = socket.socketpair()
    try:
        queue = relay.BackendRelayQueue(left); queue.claim_reader()
        token = queue.enqueue({'request_id': 'a'*32, 'action': 'reserve'}, deadline=time.monotonic()+5)
        with pytest.raises(ValueError): copy.copy(token)
        with pytest.raises(ValueError): copy.deepcopy(token)
        with pytest.raises(AttributeError): token.owner = threading.current_thread()
        with pytest.raises(ValueError): queue.result({'request_id': 'a'*32})
        assert queue.take(left) is token
        queue.complete(token, {'status': 'reserved'})
        assert queue.result(token) == {'status': 'reserved'}
    finally: left.close(); right.close()


def test_original_controller_demux_interleaves_relay_under_one_deadline(modeled_origin, tmp_path, monkeypatch):
    from backend.engine import application_launch_controller as controller
    q, owner, authority, node, event = modeled_origin
    assert callable(getattr(controller, '_controller_reply', None)), 'Original controller single reader demux is missing'
    first = event('reserve', fixed_plan(tmp_path)); second = node._new(node.NodeBackendEvent)
    node._EVENTS[second] = {'authority': owner._node_backend_authority,
        'frame': {'schema_version': 1, 'kind': 'cpu_execution_proof', 'nonce': owner.nonce}, 'used': False}
    events = [first, second]; timeouts = []; acknowledgements = []
    def receive(cap, timeout):
        timeouts.append(timeout); return events.pop(0)
    monkeypatch.setattr(node, 'receive_frame', receive)
    monkeypatch.setattr(controller, '_preflight_controller_event', lambda current, original:
        acknowledgements.append(api().process_controller_event(current, original)))
    deadline = time.monotonic()+10
    reply = controller._controller_reply(owner, deadline, 'cpu_execution_proof')
    assert reply['kind'] == 'cpu_execution_proof' and len(acknowledgements) == 1
    assert len(timeouts) == 2 and 0 < timeouts[1] <= timeouts[0] < 10
    assert len(authority.snapshot()['registry']['writers']) == 2


@pytest.mark.parametrize('damage', ['valid', 'nonce', 'identity', 'late', 'parent', 'extra'])
def test_child_startup_gate_checks_original_pipe_data_before_stage_import(tmp_path, monkeypatch, damage):
    from backend.engine import worker_preflight as worker
    assert callable(getattr(worker, '_wait_writer_gate', None)), 'Child startup gate is missing'
    monkeypatch.chdir(tmp_path)
    read, write = os.pipe(); anchor = os.open(tmp_path/'original-lock', os.O_CREAT|os.O_RDWR, 0o600)
    info = os.fstat(anchor)
    gate = {'request_id': 'a'*32, 'parent_pid': os.getppid(), 'writer_fd': anchor,
        'writer_identity': [info.st_dev, info.st_ino], 'deadline': time.monotonic()+5}
    if damage == 'nonce': gate['request_id'] = 'b'*32
    if damage == 'identity': gate['writer_identity'][1] += 1
    if damage == 'late': gate['deadline'] = time.monotonic()-1
    if damage == 'parent': gate['parent_pid'] = os.getpid()
    if damage == 'extra': gate['command'] = 'unreviewed'
    os.write(write, json.dumps(gate).encode()+b'\n'); os.close(write)
    try:
        if damage == 'valid': worker._wait_writer_gate(read, 'a'*32, tmp_path, 5)
        else:
            with pytest.raises(ValueError): worker._wait_writer_gate(read, 'a'*32, tmp_path, 5)
        assert 'torch' not in sys.modules
    finally:
        # The child implementation owns one close; this fixture's original
        # writer reference is separate and is never authority reconstructed.
        if not getattr(worker, '_wait_writer_gate', None): os.close(read)
        os.close(anchor)


@pytest.mark.parametrize('damage', ['expired', 'saturated', 'late_callback'])
def test_send_frame_never_borrows_a_new_budget_after_original_deadline(monkeypatch, damage):
    import inspect
    from backend.engine import application_launch_handshake as handshake
    assert 'absolute_deadline' in inspect.signature(handshake.send_frame).parameters, 'Original absolute send bound is missing'
    left, right = socket.socketpair()
    try:
        right.setblocking(False)
        if damage == 'expired':
            with pytest.raises(ValueError): handshake.send_frame(left, {'kind': 'fixed'}, absolute_deadline=time.monotonic()-1)
            with pytest.raises(BlockingIOError): right.recv(1)
        elif damage == 'saturated':
            left.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096); left.setblocking(False)
            while True:
                try: left.send(b'x'*4096)
                except BlockingIOError: break
            started = time.monotonic()
            with pytest.raises(ValueError): handshake.send_frame(left, {'kind': 'fixed'}, absolute_deadline=started+.03)
            assert time.monotonic()-started < .2
        else:
            clock = [100.0]; monkeypatch.setattr(handshake.time, 'monotonic', lambda: clock[0])
            class LateSocket:
                def send(self, raw, flags): clock[0] = 102.0; return len(raw)
            monkeypatch.setattr(handshake.select, 'select', lambda _r,w,_x,_t: ([],w,[]))
            with pytest.raises(ValueError): handshake.send_frame(LateSocket(), {'kind': 'fixed'}, absolute_deadline=101.0)
    finally: left.close(); right.close()


def test_idle_service_does_not_acquire_publication_mutex_before_a_channel_action(tmp_path,monkeypatch):
    """Original socket is real; idle service must not contend with ticket admission.

    No app, model, thread, child, CPU request, or runtime root is started. Full
    binding verification is forbidden only when no channel action is ready.
    """
    from contextlib import nullcontext
    from backend.engine import application_launch_handshake as h,migration_guard
    relay=api();left,right=socket.socketpair();queue=relay.BackendRelayQueue(left)
    class Stop:
        visits=0
        def is_set(self):
            self.visits+=1
            return self.visits>1
    context=(tmp_path,{})
    cache={'ready':True,'root':tmp_path,'socket':left,'context':('original',),
        'challenge':{},'proof':{},'preflight_queue':queue,'admission':h.BackendWorkAdmission()}
    monkeypatch.setattr(h,'_CACHE',cache)
    monkeypatch.setattr(h,'_root_context',lambda:context)
    monkeypatch.setattr(h,'_context',lambda:('original',))
    monkeypatch.setattr(migration_guard,'maintenance_guard',lambda root:nullcontext())
    monkeypatch.setattr(h.select,'select',lambda *args:([],[],[]))
    validations=[]
    def idle_validate(*args,**kwargs):
        validations.append(args)
        raise AssertionError('Idle service contended for the publication mutex')
    monkeypatch.setattr(h,'_validate',idle_validate)
    try:
        h.backend_execution_service(Stop())
        assert validations==[] and queue.unresolved is False
        assert left.fileno()>=0 and cache['admission'].snapshot()['active_scopes']==0
        assert 'torch' not in sys.modules
    finally:left.close();right.close()


@pytest.mark.parametrize('case',['busy_then_original','expired','busy_expired','changed_proof','changed_cache'])
def test_action_binding_wait_is_read_only_and_bounded_by_original_deadline(tmp_path,monkeypatch,case):
    from backend.engine import application_launch_handshake as h,application_launch_lease as lease
    verify=getattr(h,'_validate_service_action',None)
    assert verify is not None,'Original action has no bounded read-only binding verification'
    clock=[10.0];calls=[];proof={'original':True};context=(tmp_path,{})
    relay=api();left,right=socket.socketpair();queue=relay.BackendRelayQueue(left);queue.claim_reader()
    original={'ready':True,'root':tmp_path,'context':('original',),'challenge':{},'proof':proof,'socket':left,'preflight_queue':queue}
    monkeypatch.setattr(h,'_CACHE',original)
    monkeypatch.setattr(h,'_root_context',lambda:context)
    monkeypatch.setattr(h,'_context',lambda:('original',))
    monkeypatch.setattr(h.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(h.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def validate(*args,**kwargs):
        calls.append(clock[0])
        if case=='changed_cache':
            monkeypatch.setattr(h,'_CACHE',dict(original))
            raise lease.LeaseTransitionBusy('controlled read mutex busy')
        if case=='changed_proof':return {'original':False}
        if case=='busy_expired' or case=='busy_then_original' and len(calls)<3:
            raise lease.LeaseTransitionBusy('controlled read mutex busy')
        return proof
    monkeypatch.setattr(h,'_validate',validate)
    deadline=9.0 if case=='expired' else 10.011
    if case=='busy_then_original':
        assert verify(original,deadline)==proof
        assert len(calls)==3 and clock[0]<deadline
    else:
        with pytest.raises(h.HandshakeError):verify(original,deadline)
        if case=='expired':assert calls==[]
        elif case in ('changed_cache','changed_proof'):assert len(calls)==1
        else:assert clock[0]==deadline and all(value<deadline for value in calls)
    assert original['proof'] is proof and 'torch' not in sys.modules
    left.close();right.close()


@pytest.mark.parametrize('damage',['before_send','after_send','late_after_send'])
def test_service_action_binding_failure_retains_original_pending_count(tmp_path,monkeypatch,damage):
    from contextlib import nullcontext
    from backend.engine import application_launch_handshake as h,migration_guard
    relay=api();left,right=socket.socketpair();queue=relay.BackendRelayQueue(left)
    admission=h.BackendWorkAdmission();admission._begin_producer()
    cache={'ready':True,'root':tmp_path,'socket':left,'context':('original',),
        'challenge':{},'proof':{'original':True},'preflight_queue':queue,'admission':admission}
    deadline=time.monotonic()+1
    token=queue.enqueue({'request_id':'c'*32,'action':'reserve'},deadline=deadline)
    context=(tmp_path,{})
    monkeypatch.setattr(h,'_CACHE',cache)
    monkeypatch.setattr(h,'_root_context',lambda:context)
    monkeypatch.setattr(h,'_context',lambda:('original',))
    monkeypatch.setattr(migration_guard,'maintenance_guard',lambda root:nullcontext())
    calls=[];sent=[]
    def validate(*args,**kwargs):
        calls.append(args)
        if damage=='before_send' or damage=='after_send' and len(calls)==2:return {'original':False}
        if damage=='late_after_send' and len(calls)==2:monkeypatch.setattr(h.time,'monotonic',lambda:deadline)
        return cache['proof']
    monkeypatch.setattr(h,'_validate',validate)
    monkeypatch.setattr(h,'send_frame',lambda sock,value,**kwargs:sent.append((sock,value,kwargs['absolute_deadline'])))
    try:
        with pytest.raises(h.HandshakeError):h.backend_execution_service()
        assert len(sent)==(0 if damage=='before_send' else 1)
        assert all(row[0] is left and row[2]==deadline for row in sent)
        assert queue.unresolved and queue._active is token
        assert admission.snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
        assert 'torch' not in sys.modules
    finally:left.close();right.close()


@pytest.mark.parametrize('damage',['closed','socket_replaced','queue_replaced'])
def test_action_binding_busy_wait_refuses_changed_original_endpoint(tmp_path,monkeypatch,damage):
    from backend.engine import application_launch_handshake as h,application_launch_lease as lease
    relay=api();left,right=socket.socketpair();queue=relay.BackendRelayQueue(left);queue.claim_reader()
    cache={'ready':True,'root':tmp_path,'context':('original',),'challenge':{},'proof':{},'socket':left,'preflight_queue':queue}
    monkeypatch.setattr(h,'_CACHE',cache);monkeypatch.setattr(h,'_root_context',lambda:(tmp_path,{}))
    monkeypatch.setattr(h,'_context',lambda:('original',))
    calls=[]
    def validate(*args,**kwargs):
        calls.append(args)
        if len(calls)==1:
            if damage=='closed':left.close()
            elif damage=='socket_replaced':cache['socket']=right
            else:cache['preflight_queue']=relay.BackendRelayQueue(right)
            raise lease.LeaseTransitionBusy('controlled mutex busy then original endpoint loss')
        return {}
    monkeypatch.setattr(h,'_validate',validate)
    try:
        with pytest.raises(h.HandshakeError):h._validate_service_action(cache,time.monotonic()+1)
        assert len(calls)==1,'Changed endpoint was read again after the original transport failed'
        assert 'torch' not in sys.modules
    finally:left.close();right.close()


@pytest.mark.parametrize('case',['busy_then_original','expired','busy_expired','foreign_nonce','foreign_binding','structural'])
def test_fixture_cleanup_read_preserves_same_original_epoch_and_first_budget(tmp_path,monkeypatch,case):
    from backend.tests import test_application_preflight_child_relay_actual as actual
    from backend.engine import application_launch_lease as lease
    read=getattr(actual,'read_original_fixture_lease',None)
    assert read is not None,'Original fixture first cleanup observation lacks its fixed epoch/budget reader'
    now=[10.0];calls=[];ack={'nonce':'a'*32};binding={'original':True}
    monkeypatch.setattr(actual.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(actual.time,'sleep',lambda value:now.__setitem__(0,now[0]+value))
    def load(root):
        assert root==tmp_path;calls.append(now[0])
        if case=='structural':raise lease.LaunchLeaseError('controlled structural ownership failure')
        if case=='busy_expired' or case=='busy_then_original' and len(calls)<2:
            raise lease.LaunchLeaseError('Application launch ownership requires recovery: ownership publication interrupted or changed')
        return {'nonce':'b'*32 if case=='foreign_nonce' else ack['nonce'],
            'binding':{'original':False} if case=='foreign_binding' else binding}
    monkeypatch.setattr(lease,'_load',load)
    deadline=9.0 if case=='expired' else 10.015
    if case=='busy_then_original':
        assert read(tmp_path,ack,binding,deadline)=={'nonce':ack['nonce'],'binding':binding}
        assert len(calls)==2 and now[0]<deadline
    else:
        with pytest.raises((ValueError,AssertionError)):read(tmp_path,ack,binding,deadline)
        if case=='expired':assert calls==[]
        elif case in ('foreign_nonce','foreign_binding','structural'):assert len(calls)==1
        else:assert now[0]==deadline and all(value<deadline for value in calls)
    assert 'torch' not in sys.modules
