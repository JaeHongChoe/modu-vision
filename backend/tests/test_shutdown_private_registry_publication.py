"""Real private registry publication at the bounded shutdown transport boundary.

These tests run in repository CI. They exercise real WriterEpoch disk publication,
not an installed app, model, or child process. Full signed-asset validation and
transport ownership are separate admission boundaries in the controlled fixture.
"""
import copy
import hashlib
import json
import os
import socket
import types

import pytest

from backend.engine import application_launch_handshake as h
from backend.engine import application_launch_lease as lease
from backend.engine import application_launch_quiescence as quiescence
from backend.engine import application_shutdown_transaction as t
from backend.engine import runtime_process_control as codec
from backend.engine import runtime_update as u
from backend.engine.application_preflight_child_relay import BackendRelayQueue
from backend.engine.migration_guard import maintenance_guard
from backend.tests.test_application_launch_quiescence import epoch

@pytest.fixture
def shutdown_transport(tmp_path, monkeypatch):
    left, right = socket.socketpair()
    writer = tmp_path/'writer.lock'; writer.write_bytes(b''); writer.chmod(0o600)
    fd = os.open(writer, os.O_RDWR); private = os.dup(fd)
    guard = object(); handle = types.SimpleNamespace(pass_fds=(fd,))
    queue = BackendRelayQueue(left); queue.claim_reader()
    identity = lambda pid: {'pid': pid, 'created_at': 946684800.0, 'command_sha256': '5'*64}
    main, backend = identity(os.getppid()), identity(os.getpid())
    binding = {'installation_id': '1'*32, 'update_id': '2'*32, 'application_generation': '2'*32,
               'database_pointer': {'fence': 3}, 'database_generation_path': '/database'}
    binding_sha = hashlib.sha256(h._canonical(binding)).hexdigest()
    proof = {'schema_version': 1, 'kind': 'backend_claim', 'challenge': '3'*64, 'epoch': '4'*32,
             'nonce': '6'*32, 'binding_sha256': binding_sha, 'process': backend,
             'executable': '/installed/backend', 'executable_sha256': '7'*64,
             'build_identity_sha256': '8'*64, 'frozen': True}
    writer_pin = {'writer_id': '9'*32, 'registration_sha256': 'a'*64, 'registration_registry_sha256': 'b'*64}
    challenge = {'writer': writer_pin, 'binding': binding, 'main_process': main, 'nonce': proof['nonce'], 'epoch': proof['epoch']}
    cache = {'context': ('original-context',), 'root': tmp_path, 'socket': left, 'ready': True,
             'challenge': challenge, 'proof': proof, 'writer_guard': guard, 'writer_handle': handle,
             'writer_private_fd': private, 'writer_fd_identity': (os.fstat(fd).st_dev, os.fstat(fd).st_ino),
             'admission': h.BackendWorkAdmission(), 'preflight_queue': queue}
    row = {'nonce': proof['nonce'], 'revision': 10, 'protocol_version': 4, 'state': 'ready',
           'spawn_attempted': True, 'binding': binding, 'process': main, 'supervisor': identity(123),
           'writer_drain': {'phase': 'enrolled', **writer_pin, 'request': None, 'receipt': None, 'backend_exit': None}}
    registry = {'schema_version': 1, 'revision': 5, 'state': 'open', 'binding': {'nonce': proof['nonce']}, 'writers': []}
    controls = {'row': row, 'epoch': {'registry': registry, 'registry_sha256': hashlib.sha256(codec.private_json_bytes(registry)).hexdigest()}}
    monkeypatch.setattr(h, '_CACHE', cache)
    monkeypatch.setattr(h, '_context', lambda: cache['context'])
    monkeypatch.setattr(h, '_root_context', lambda: (tmp_path, {'original': 'environment'}))
    monkeypatch.setattr(lease, '_identity', identity)
    monkeypatch.setattr(h, '_backend_shutdown_controls', lambda value: copy.deepcopy(controls), raising=False)
    now = [100.0]; monkeypatch.setattr(h.time, 'monotonic', lambda: now[0])
    full = []
    def validate(root, values, frame, **kwargs):
        full.append(kwargs.get('absolute_deadline')); return copy.deepcopy(proof)
    monkeypatch.setattr(h, '_validate', validate)
    asset = tmp_path/'asset'; asset.write_bytes(b'approved')
    def custody(root, binding):
        info = asset.stat()
        return ((str(asset), tuple(getattr(info, k) for k in ('st_dev','st_ino','st_mode','st_uid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))),)
    monkeypatch.setattr(t, 'asset_custody', custody)
    prep = {'schema_version': 1, 'kind': 'backend_shutdown_prepare', 'nonce': proof['nonce'], 'epoch': proof['epoch'],
            'binding_sha256': binding_sha, 'backend_claim_sha256': hashlib.sha256(h._canonical(proof)).hexdigest(),
            'request_id': 'c'*32, 'budget_ms': 30000}
    with maintenance_guard(tmp_path):
        yield types.SimpleNamespace(cache=cache, controls=controls, now=now, full=full, asset=asset,
                                    prep=prep, left=left, right=right, handle=handle, fd=fd, private=private)
    left.close(); right.close(); os.close(fd); os.close(private)



def gate_for(live,actual):
    live.now[0]=101.0;live.controls['epoch']=copy.deepcopy(actual)
    request={'schema_version':1,'kind':'backend_drain_request','challenge':'d'*64,
        'request_id':'c'*32,'nonce':'6'*32,'epoch':'4'*32,
        'binding_sha256':live.prep['binding_sha256'],'backend_claim_sha256':live.prep['backend_claim_sha256'],
        'writer_id':'9'*32,'registration_sha256':'a'*64,'closed_registry_sha256':actual['registry_sha256'],'budget_ms':4000}
    live.controls['row']['revision']+=1
    live.controls['row']['writer_drain'].update(phase='closing',request=copy.deepcopy(request))
    return request


@pytest.mark.parametrize('writers',['empty','reserved','unsupported'])
def test_real_owned_epoch_close_matches_backend_private_disk_guard(shutdown_transport,epoch,monkeypatch,writers):
    _,root,owner,authority=epoch
    if writers=='reserved':authority.enroll('backend',expected_registry_sha256=authority.snapshot()['registry_sha256'])
    if writers=='unsupported':authority.block_unsupported('local_training',expected_registry_sha256=authority.snapshot()['registry_sha256'])
    original=authority.snapshot();shutdown_transport.controls['epoch']=copy.deepcopy(original)
    cap=h._prepare_backend_shutdown(shutdown_transport.cache, shutdown_transport.prep, started=100.0);cap.prepared_frame()
    assert h._BACKEND_SHUTDOWNS[cap]['controls']['epoch']==original
    closed=authority.close_epoch(expected_registry_sha256=original['registry_sha256'])
    registry_path=root/quiescence.EPOCHS/owner.nonce/'registry.json'
    raw=registry_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==closed['registry_sha256']
    assert codec.private_json_bytes(closed['registry'])==raw
    assert h._canonical(closed['registry'])!=raw
    request=gate_for(shutdown_transport,closed)
    cap.begin_drain(request,started=101.0)
    assert h._BACKEND_SHUTDOWNS[cap]['deadline']==105.0
    assert shutdown_transport.full==[130.0]


def test_late_real_writer_publication_is_not_the_planned_close(shutdown_transport, epoch):
    _, _, _, authority = epoch
    original = authority.snapshot()
    shutdown_transport.controls['epoch'] = copy.deepcopy(original)
    cap = h._prepare_backend_shutdown(shutdown_transport.cache, shutdown_transport.prep, started=100.0)
    cap.prepared_frame()
    # This is an independently valid disk publication, but was not the one
    # transition retained by the already prepared shutdown capability.
    authority.enroll('backend', expected_registry_sha256=original['registry_sha256'])
    changed = authority.snapshot()
    closed = authority.close_epoch(expected_registry_sha256=changed['registry_sha256'])
    request = gate_for(shutdown_transport, closed)
    with pytest.raises(h.HandshakeError, match='unplanned transition'):
        cap.begin_drain(request, started=101.0)
    assert shutdown_transport.cache['admission']._closed is False
    assert shutdown_transport.full == [130.0]


def test_real_epoch_inspection_refuses_same_value_byte_reencoding(epoch):
    _, root, owner, authority = epoch
    original = authority.snapshot()
    closed = authority.close_epoch(expected_registry_sha256=original['registry_sha256'])
    path = root / quiescence.EPOCHS / owner.nonce / 'registry.json'
    before = path.read_bytes()
    reencoded = u._canonical(closed['registry'])
    assert json.loads(reencoded) == json.loads(before)
    assert reencoded != before
    path.write_bytes(reencoded)
    with pytest.raises(quiescence.QuiescenceError):
        quiescence.inspect_epoch(root, owner.nonce)
    # Refusal must not repair or silently adopt a different byte publication.
    assert path.read_bytes() == reencoded


@pytest.mark.parametrize('change',['exact','foreign-writer','wrong-status','wrong-revision','same-value-reencoded','retained-copy'])
def test_original_exit_accepts_only_real_exact_disk_publication(tmp_path,monkeypatch,change):
    writer='9'*32
    registry={'schema_version':1,'revision':4,'state':'closed','binding':{'nonce':'6'*32},
        'writers':[{'writer_id':writer,'status':'active','exit_code':None,'role':'backend'}]}
    path=tmp_path/'registry.json';codec.atomic_private_json(path,registry)
    original={'registry':copy.deepcopy(registry),'registry_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    state={'authority':object(),'post_verified':True,'phase':'verified','registry':u._canonical(original),
        'registry_snapshot':copy.deepcopy(original),'owner':type('Owner',(),{'_owned':lambda self:{'writer_drain':{'writer_id':writer}}})()}
    epoch_value=copy.deepcopy(original)
    state['epoch']=type('Epoch',(),{'snapshot':lambda self:copy.deepcopy(epoch_value)})()
    monkeypatch.setattr(t,'_state',lambda cap:state)
    monkeypatch.setattr(t,'_update',lambda:u)
    closed=[];monkeypatch.setattr(t,'current_controller',lambda *args:closed.append(args))
    expected=copy.deepcopy(registry);expected['writers'][0].update(status='direct_exited',exit_code=0);expected['revision']+=1
    if change=='foreign-writer':expected['writers'].append({'foreign':True})
    if change=='wrong-status':expected['writers'][0]['status']='unsupported'
    if change=='wrong-revision':expected['revision']+=1
    if change=='retained-copy':state['registry_snapshot']['registry']['writers'][0]['role']='foreign'
    codec.atomic_private_json(path,expected)
    if change=='same-value-reencoded':path.write_bytes(u._canonical(expected))
    epoch_value.update(registry=json.loads(path.read_bytes()),registry_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if change=='exact':
        t.accept_original_registry_exit(object(),state['authority'])
        assert state['registry_snapshot']==epoch_value and len(closed)==1
    else:
        with pytest.raises(ValueError):t.accept_original_registry_exit(object(),state['authority'])
        assert closed==[]


def test_private_codec_matches_real_streaming_publication_and_retains_order(tmp_path):
    rows = [
        {'z': None, 'a': [1, False], '한글': '보존'},
        {'b': 1, 'a': 2},
        {'a': 2, 'b': 1},
        {'number': 1.25, 'nested': {'z': 1, 'a': 2}},
    ]
    emitted = []
    for index, value in enumerate(rows):
        path = tmp_path / (str(index) + '.json')
        codec.atomic_private_json(path, value)
        raw = path.read_bytes()
        emitted.append(raw)
        assert codec.private_json_bytes(value) == raw
        assert (path.stat().st_mode & 0o777) == 0o600
    assert emitted[1] != emitted[2]
    assert json.loads(emitted[1]) == json.loads(emitted[2])
    with pytest.raises(ValueError):
        codec.private_json_bytes({'value': float('nan')})
