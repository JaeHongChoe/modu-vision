"""Controlled source-only admission/drain proof; no full app/tree/lease claim."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time
import zipfile

import pytest

from backend.engine import application_launch_handshake as handshake
from backend.tests.test_application_launch_execution import (
    cpu_stack, start_cpu_stack, wait_receipt, stop_stack, controlled_canary_publication, _atomic_fixture_json,
)
from backend.tests.test_service_s6_04 import canonical, sha
from backend.tests import _source_cpu_completion_boundary as completion_boundary


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Owned bridge uses POSIX descriptors/lifetime locks')


def test_real_controller_enrolls_backend_before_actual_cpu_and_retains_blocking_row(tmp_path):
    """The existing source controller lacked a writer epoch despite real CPU success."""
    values = cpu_stack(tmp_path)
    root = values[0]
    child, ack = _start_ready_cpu_stack(values, prefix=['-c', _source_cpu_settlement_fixture_prefix()])
    try:
        _, cpu = _wait_receipt_and_cpu_settlement(root, ack['nonce'], child)
        assert cpu['semantic_output']['final_verdict'] == 'OK'
        directory = root / '.application-writer-epochs' / ack['nonce']
        assert (directory / 'registry.json').is_file(), 'Actual controller never enrolled its backend before spawn'
        from backend.engine.application_launch_quiescence import inspect_epoch
        snapshot = inspect_epoch(root, ack['nonce'])
        rows = snapshot['registry']['writers']
        assert len([r for r in rows if r['role'] == 'backend']) == 1
        row = next(r for r in rows if r['role'] == 'backend')
        assert row['status'] == 'active' and row['process'] == cpu['backend_process']
        assert row['process']['pid'] != child.pid
        # A Node child receipt must never stand for a retained Python Popen.
        assert row['status'] != 'direct_exited'
        assert len(rows)==2
        cpu_writer=next(r for r in rows if r['role']=='owned_cpu_worker')
        assert cpu_writer['status']=='direct_exited' and cpu_writer['exit_code']==0 and cpu_writer['reason_code'] is None
        assert cpu_writer['process']['pid']==cpu['worker_pid'] and cpu_writer['process']!=row['process']
        journal = json.loads((root / '.application-launches' / ack['nonce'] / 'journal.json').read_bytes())
        assert row['process']['pid'] != journal['process']['pid'] == cpu['main_process']['pid']
        assert journal['writer_drain']['registration_sha256'] == row['registration_sha256']
        assert journal['writer_drain']['writer_id'] == row['writer_id']
    finally:
        stop_stack(child, root)


def admission():
    cls = getattr(handshake, 'BackendWorkAdmission', None)
    assert cls is not None, 'Owned backend has no all-method work admission/drain state'
    return cls()


@pytest.mark.parametrize('scope_type,method', [('http', 'GET'), ('http', 'POST'), ('http', 'OPTIONS'), ('websocket', None)])
def test_closed_work_admission_refuses_all_http_and_websocket_without_entering_writer(scope_type, method):
    state = admission()
    state.close()
    scope = {'type': scope_type, 'path': '/api/errors', 'method': method}
    assert state.enter(scope) is False
    assert state.snapshot()['active_scopes'] == 0


def test_accepted_scope_holds_drain_until_real_request_lifetime_finishes():
    state = admission()
    scope = {'type': 'http', 'path': '/health', 'method': 'GET'}
    assert state.enter(scope)
    state.close()
    finished = threading.Event()
    result = {}
    thread = threading.Thread(target=lambda: (result.update(state.drain(.5)), finished.set()))
    thread.start()
    try:
        assert not finished.wait(.03), 'Drain returned while its original accepted scope remained active'
        state.leave()
        assert finished.wait(.5)
        assert result == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    finally:
        thread.join(timeout=1)
        assert not thread.is_alive()


@pytest.mark.parametrize('case', ['accepted_202', 'spawn_route', 'startup', 'unknown_route'])
def test_uncovered_background_and_spawn_protocols_sticky_refuse(case):
    state = admission()
    if case == 'startup':
        state.uncovered('startup_background_protocols')
    else:
        path = '/health' if case == 'accepted_202' else '/api/training/start' if case == 'spawn_route' else '/unreviewed-writer'
        assert state.enter({'type': 'http', 'method': 'POST', 'path': path})
        if case == 'accepted_202': state.response(202)
        state.leave()
    state.close()
    first = state.drain(.1)
    assert first['status'] == 'refused' and first['unsupported']
    assert state.drain(.1) == first


def test_busy_scope_refuses_with_original_budget_and_never_reopens():
    state = admission()
    assert state.enter({'type': 'http', 'method': 'GET', 'path': '/health'})
    state.close()
    start = time.monotonic()
    outcome = state.drain(.03)
    assert time.monotonic() - start < .3
    assert outcome['status'] == 'refused' and outcome['active_scopes'] == 1
    state.leave()
    assert not state.enter({'type': 'http', 'method': 'GET', 'path': '/health'})


def test_failed_cpu_producer_stays_unconfirmed_after_its_scope_returns():
    state = admission()
    assert hasattr(state, 'producer'), 'Backend has no counted original CPU producer lifetime'
    with pytest.raises(RuntimeError, match='controlled uncertain helper'):
        with state.producer():
            assert state.snapshot()['active_scopes'] == 1
            raise RuntimeError('controlled uncertain helper')
    state.close()
    assert state.drain(.1) == {'status': 'refused', 'active_scopes': 0, 'unsupported': ['cpu_producer_unconfirmed']}
    with pytest.raises(handshake.HandshakeError):
        with state.producer(): pytest.fail('Closed producer was admitted')


def test_actual_middleware_closed_gate_never_enters_inner_writer_for_any_transport():
    from backend.main import OwnedWorkAdmissionMiddleware
    async def exercise():
        state=admission();state.close();entered=[]
        async def inner(scope,receive,send): entered.append(scope)
        middleware=OwnedWorkAdmissionMiddleware(inner,admission=state)
        for kind,method in [('http','GET'),('http','POST'),('http','OPTIONS'),('websocket',None)]:
            sent=[]
            async def receive(): raise AssertionError('Closed middleware read writer input')
            async def send(frame): sent.append(frame)
            await middleware({'type':kind,'path':'/api/errors','method':method},receive,send)
            if kind=='websocket':assert sent==[{'type':'websocket.close','code':1013}]
            else:
                assert sent[0]['type']=='http.response.start' and sent[0]['status']==409
                assert json.loads(sent[1]['body'])=={'detail':'Owned backend admission is closed for drain'}
        assert entered==[] and state.snapshot()['active_scopes']==0
    asyncio.run(exercise())


def test_original_private_lock_reference_survives_exposed_guard_fd_reuse(tmp_path,monkeypatch):
    """Real flock/OFD controls; process-binding reader is isolated at its boundary."""
    import fcntl
    from backend.engine import application_launch_quiescence as q,application_launch_lease as lease,runtime_update as update
    from backend.tests.test_application_launch_lease import installed,reserve
    root,value,_=installed(tmp_path);owner=reserve(root,value)
    original=q.WriterEpoch.create(root,owner.nonce,expected_launch_sha256=update._sha(update._canonical(lease._load(root))))
    registration=original.enroll('backend',expected_registry_sha256=original.snapshot()['registry_sha256'])
    child=None
    retained_guard=q.writer_guard(root,owner.nonce,registration.writer_id,expected_registration_sha256=registration.registration_sha256)
    with retained_guard as retained:
        exposed=retained.pass_fds[0];private=os.dup(exposed);info=os.fstat(private)
        proof={'nonce':owner.nonce};context=(root,{})
        monkeypatch.setattr(handshake,'_root_context',lambda:context)
        monkeypatch.setattr(handshake,'_context',lambda:())
        def validate(*_,validated=None,absolute_deadline=None,before_acquire=None):
            # Model the original bounded-entry arguments, never owner authentication.
            if absolute_deadline is not None:
                assert type(absolute_deadline) in (int,float) and math.isfinite(absolute_deadline)
                assert handshake.time.monotonic() < absolute_deadline
            if before_acquire is not None:
                assert callable(before_acquire)
                before_acquire()
            if absolute_deadline is not None: assert handshake.time.monotonic() < absolute_deadline
            if validated is not None:validated['protocol_version']=4
            return proof
        monkeypatch.setattr(handshake,'_validate',validate)
        monkeypatch.setattr(handshake,'_CACHE',{'context':(),'ready':True,'proof':proof,
            'challenge':{'writer':{'writer_id':registration.writer_id,'registration_sha256':registration.registration_sha256}},
            'writer_guard':retained_guard,'writer_handle':retained,'writer_private_fd':private,'writer_fd_identity':(info.st_dev,info.st_ino),'admission':admission()})
        lock=root/q.EPOCHS/owner.nonce/'writers'/registration.writer_id/'ownership.lock'
        os.close(exposed);replacement=os.open(lock,os.O_RDWR)
        if replacement!=exposed:os.dup2(replacement,exposed);os.close(replacement);replacement=exposed
        try:
            with handshake.owned_cpu_writer_scope() as passed:
                assert passed[0] not in (exposed,private), 'Producer reused an exposed descriptor instead of duplicating original OFD'
                assert os.fstat(passed[0]).st_ino==info.st_ino
                # Unlock only the replacement OFD. It must not affect the
                # passed original reference or the privately retained anchor.
                fcntl.flock(replacement,fcntl.LOCK_UN)
                probe=os.open(lock,os.O_RDWR)
                try:
                    with pytest.raises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
                finally:os.close(probe)
                child=subprocess.Popen([sys.executable,'-I','-B','-c',"import os;print('ready',flush=True);os.read(0,1)"],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=passed)
                assert select.select([child.stdout],[],[],3)[0] and child.stdout.readline()==b'ready\n'
            with pytest.raises(OSError):os.fstat(passed[0])
        finally:
            # Restore the original OFD number for core context cleanup; this
            # test never asks production to repair a moved lock.
            os.dup2(private,exposed);os.close(private)
    try:
        probe=os.open(lock,os.O_RDWR)
        try:
            assert child is not None
            # Only the child's inherited original OFD remains: parent scope,
            # original Guard, and private anchor have all closed normally.
            with pytest.raises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(probe)
    finally:
        if child is not None:
            child.stdin.write(b'x');child.stdin.close();child.wait(timeout=3)
            assert child.returncode==0
        owner.cancel();owner.close()


def test_enrolled_never_spawned_backend_cannot_cancel_its_writer_authority(tmp_path):
    from backend.tests.test_application_launch_lease import installed,reserve
    from backend.engine import application_launch_lease as lease
    root,value,_=installed(tmp_path);owner=reserve(root,value)
    owner.enroll_backend_writer();before=lease._load(root)
    try:
        with pytest.raises(lease.LaunchLeaseError,match='writer|epoch'):owner.cancel()
        assert lease._load(root)==before
        with pytest.raises(lease.LaunchLeaseError):lease.assert_quiescent(root)
    finally:owner.close()


def test_existing_writer_epoch_cannot_be_ignored_by_exited_legacy_lease(tmp_path):
    from backend.tests.test_application_launch_lease import installed,reserve
    from backend.engine import application_launch_lease as lease,application_launch_quiescence as q,runtime_update as update
    root,value,_=installed(tmp_path);owner=reserve(root,value)
    q.WriterEpoch.create(root,owner.nonce,expected_launch_sha256=update._sha(update._canonical(lease._load(root))))
    owner.cancel();before=lease._load(root)
    try:
        with pytest.raises(lease.LaunchLeaseError,match='writer|epoch'):lease.assert_quiescent(root)
        assert lease._load(root)==before
    finally:owner.close()


def test_protocol_four_reader_never_accepts_invented_never_spawned_writer_release(tmp_path):
    from copy import deepcopy
    from backend.tests.test_application_launch_lease import installed,reserve
    from backend.engine import application_launch_lease as lease
    root,value,_=installed(tmp_path);owner=reserve(root,value);owner.enroll_backend_writer()
    try:
        forged=deepcopy(lease._load(root));forged.update(state='exited',exit_observation={'never_spawned':True})
        with pytest.raises(lease.LaunchLeaseError,match='writer|epoch'):
            lease._record(root,owner.nonce,record=forged)
    finally:owner.close()


@pytest.mark.parametrize('protocol',[2,3])
def test_actual_authenticated_legacy_three_producer_has_counted_empty_transport_and_closed_refusal(tmp_path,monkeypatch,protocol):
    """Real legacy descriptor proof; no registered lock/native authority claim."""
    from backend.tests import test_application_launch_handshake as fixtures
    original_fixture=fixtures.fixture
    code=b'''from backend.engine.application_launch_handshake import owned_cpu_writer_scope,backend_work_admission,HandshakeError
from backend.engine import application_launch_handshake as h
state=backend_work_admission()
'''+(b'''try:
 with owned_cpu_writer_scope():raise AssertionError('unsupported protocol2 CPU scope admitted')
except HandshakeError:pass
''' if protocol==2 else b'''
with owned_cpu_writer_scope() as transport:
 assert transport==() and state.snapshot()['active_scopes']==1
assert state.snapshot()['active_scopes']==0
for name in ('writer_guard','writer_handle','writer_private_fd','writer_fd_identity'):
 h._CACHE[name]=object()
 try:
  with owned_cpu_writer_scope():raise AssertionError('partial legacy writer capability admitted')
 except HandshakeError:pass
 finally:h._CACHE[name]=None
state.close()
try:
 with owned_cpu_writer_scope():raise AssertionError('closed legacy producer admitted')
except HandshakeError:pass
''')
    def fixture(folder):
        value=original_fixture(folder);sign=value['sign']
        def add_producer(payload):
            archive=value['directory']/'application.zip'
            with zipfile.ZipFile(archive) as z:files={name:z.read(name) for name in z.namelist()}
            manifest=json.loads(files.pop('portable-application.json'))
            script=files['bin/vision_backend.py'];at=script.index(b'print(json.dumps(')
            files['bin/vision_backend.py']=script[:at]+code+script[at:]
            manifest['files']=[{**row,'size':len(files[row['path']]),'sha256':sha(files[row['path']])} for row in manifest['files']]
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('portable-application.json',canonical(manifest))
                for name,raw in files.items():z.writestr(name,raw)
            payload.update(size=archive.stat().st_size,sha256=sha(archive.read_bytes()))
            payload['artifacts'][0].update(size=payload['size'],sha256=payload['sha256'])
            return sign(payload)
        value['sign']=add_producer;return value
    monkeypatch.setattr(fixtures,'fixture',fixture)
    if protocol==2:
        original_installed=fixtures.installed_backend
        def old_protocol(folder):
            from backend.engine import application_launch_lease as lease,runtime_update as update
            root,record,backend=original_installed(folder)
            record.update(protocol_version=2);record.pop('cpu_execution')
            update._write(root/lease.LEASES/record['nonce']/'journal.json',record)
            update._write(root/lease.ACTIVE_LEASE,lease._publication(record))
            return root,record,backend
        monkeypatch.setattr(fixtures,'installed_backend',old_protocol)
    root,record,backend,channel,child,challenge=fixtures.launch(tmp_path)
    try:
        assert record['protocol_version']==protocol and 'writer_drain' not in record
        assert fixtures.api().read_frame(channel,10)['kind']=='backend_claim'
        assert fixtures.api().read_frame(channel,10)['kind']=='backend_ready'
        stdout,stderr=fixtures.finish(channel,child)
        (tmp_path/'legacy-source.stdout').write_text(stdout);(tmp_path/'legacy-source.stderr').write_text(stderr)
        assert child.returncode==0,stderr
        assert json.loads(stdout)=={'verified':True,'fd_inheritable':False}
    finally:
        if child.poll() is None:fixtures.finish(channel,child)


def test_actual_full_lifespan_declares_uncovered_startup_protocols(monkeypatch):
    from types import SimpleNamespace
    from backend import main
    state = admission()
    monkeypatch.setattr('backend.remote.coordinator.recover_remote_jobs', lambda _: None)
    monkeypatch.setattr('backend.engine.local_training_worker.recover_local_jobs', lambda _: None)
    monkeypatch.setattr('backend.api.routes_dataset_imports.recover_imports_at_startup', lambda _: None)
    monkeypatch.setattr('backend.engine.worker_preflight.sweep_stale_runs', lambda: None)
    monkeypatch.setattr(main.broadcaster, 'start', lambda _: None)
    async def shutdown(): pass
    monkeypatch.setattr(main.broadcaster, 'shutdown', shutdown)
    monkeypatch.setattr(main.training_job_manager, 'detach_all_for_shutdown', lambda: None)
    monkeypatch.setattr('backend.api.routes_workers.stop_for_shutdown', lambda: None)
    monkeypatch.setattr(main, 'clear_device_cache', lambda: None)
    monkeypatch.setattr(main, 'backend_bootstrap_ready', lambda: None)
    async def exercise():
        async with main._admitted_lifespan(SimpleNamespace(state=SimpleNamespace(owned_work_admission=state))):
            state.close()
            outcome = state.drain(.1)
            assert outcome['status'] == 'refused'
            assert 'startup_background_protocols' in outcome['unsupported']
    asyncio.run(exercise())


@pytest.mark.parametrize('case', ['late_prepare', 'rejected_prepare', 'exit_confirmation', 'forced_sync_exit', 'refusal_error'])
def test_actual_supervisor_drain_uses_original_shutdown_budget_and_child_handle(case):
    script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),Module=require('node:module'),path=require('node:path'),ts=require('typescript'),{EventEmitter}=require('node:events');
const file=path.resolve('src/main/supervisor.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));
m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);
const S=m.exports.BackendSupervisor;S.prototype.registerProcessHooks=()=>{};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
let release,confirm,prepareCalls=0,confirmCalls=0,refusals=0;const pending=new Promise(r=>release=r),confirmation=new Promise(r=>confirm=r);
const p=new EventEmitter();Object.assign(p,{pid:'controlled-no-process',killed:false,exitCode:null,signalCode:null,signals:[],closes:0});
const exit=()=>{p.exitCode=0;p.emit('exit',0,null);};
p.stdin={end(){p.closes++;if(!['late_prepare','forced_sync_exit'].includes(process.argv[1]))setImmediate(exit);}};p.kill=signal=>{p.signals.push(signal);p.killed=true;if(process.argv[1]==='forced_sync_exit'&&signal==='SIGKILL')exit();return true;};
const rejected=['rejected_prepare','refusal_error'].includes(process.argv[1]);
const owned={requiresWriterDrain:true,prepareBackendDrain(proc,budget){assert.equal(proc,p);assert(budget>0&&budget<=80);prepareCalls++;return rejected?Promise.reject(Error('controlled uncovered writer')):pending;},confirmBackendExit(proc){assert.equal(proc,p);confirmCalls++;return confirmation;},refuse(){refusals++;if(process.argv[1]==='refusal_error')throw Error('controlled refusal transport error');}};
const s=new S({autoRestart:false,gracefulShutdownTimeoutMs:80,ownedApplicationLaunch:owned});s.childProcess=p;s.state='HEALTHY';
const start=performance.now();let settled=false;const stopped=s.stopBackend();stopped.then(()=>settled=true,()=>settled=true);
await sleep(8);assert.equal(prepareCalls,1);assert.equal(p.closes,rejected?1:0);
if(['late_prepare','forced_sync_exit'].includes(process.argv[1])){
 await assert.rejects(stopped,/unverified|budget|deadline/i);assert(performance.now()-start<200);assert.equal(s.childProcess,process.argv[1]==='forced_sync_exit'?null:p);
 release({});await sleep(10);assert.equal(p.closes,0);assert.deepEqual(p.signals,['SIGKILL']);if(process.argv[1]==='late_prepare')exit();
}else if(rejected){
 await assert.rejects(stopped,/uncovered/i);assert.equal(refusals,1);assert.equal(confirmCalls,0);assert.equal(s.childProcess,null);assert.deepEqual(p.signals,['SIGTERM']);
}else{
 release({});await sleep(8);assert.equal(confirmCalls,1);assert.equal(settled,false);assert.equal(s.childProcess,p);confirm();await stopped;assert.equal(s.childProcess,null);
}
assert.equal(s.childProcess,null);console.log(JSON.stringify({case:process.argv[1],prepareCalls,confirmCalls,refusals,elapsed_ms:performance.now()-start,modeled_handles_only:true}));
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', script, case], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr


def test_actual_node_dispatcher_refuses_identical_exit_ack_replay():
    """Actual readers, genuine modeled admitted drain/exit/ACK; no application.

    The former fixture directly fabricated backendExitFrame, exitConfirmation
    and exitResolve. Those values cannot replace original admitted lifecycle.
    Storage/process validation remains explicitly modeled, while real source
    Frames and bindBackend perform the drain, exact retained exit and one-use
    ACK branches. Replay must retain one callback and invalidate the channel.
    """
    from backend.tests.test_application_backend_end_lifetime import CONTROL
    source=Path(__file__).resolve().parents[2]/'src/main/applicationLaunch.ts'
    result=subprocess.run(['node','-e',CONTROL,str(source),'replayed-exit-ack'],
        cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stdout+result.stderr
    receipt=json.loads(result.stdout)
    assert receipt['identical_ack_replay_refused'] is True
    assert receipt['resolve_calls']==1,'Identical acknowledgement was consumed twice'
    assert receipt['actual_dispatcher'] is True and receipt['modeled_handles_only'] is True
    assert receipt['application'] is False and receipt['cpu'] is False


def test_replay_fixture_barrier_requires_both_observations_inside_the_original_deadline(tmp_path, monkeypatch):
    """A refusal marker alone cannot stand for the ordinary stop flow or renew its budget."""
    refusal=tmp_path/'refusal.json';ordinary=tmp_path/'ordinary.json';refusal.write_text('{}')
    clock=[10.0];sleeps=[]
    monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    def sleep(seconds):
        assert 0<seconds<=.01;sleeps.append(seconds);clock[0]+=seconds
    monkeypatch.setattr(time,'sleep',sleep)
    with pytest.raises(AssertionError,match='original.*deadline'):
        _fixture_wait_before_original_deadline([refusal,ordinary],absolute_deadline=10.025)
    assert clock[0]==10.025 and sleeps and not ordinary.exists()
    ordinary.write_text('{}');sleeps.clear()
    with pytest.raises(AssertionError,match='original.*deadline'):
        _fixture_wait_before_original_deadline([refusal,ordinary],absolute_deadline=10.025)
    assert not sleeps
    _fixture_wait_before_original_deadline([refusal,ordinary],absolute_deadline=10.026)
    assert not sleeps and clock[0]==10.025


def _fixture_wait_before_original_deadline(paths, *, absolute_deadline):
    """Fixture scheduling only: require every marker without creating a budget."""
    import math
    import time
    assert type(absolute_deadline) in (int,float) and math.isfinite(absolute_deadline)
    while time.monotonic()<absolute_deadline:
        if all(path.is_file() for path in paths):
            assert time.monotonic()<absolute_deadline, 'Fixture barrier exceeded its original absolute deadline'
            return
        remaining=absolute_deadline-time.monotonic()
        if remaining>0:time.sleep(min(.01,remaining))
    raise AssertionError('Fixture barrier exceeded its original absolute deadline')


def test_replay_fixture_profile_targets_only_the_original_draining_guard_and_preserves_its_error(tmp_path,monkeypatch):
    """The fixture schedules before the original guard; it neither calls nor catches that guard."""
    class OriginalGuard:
        def fresh(self, deadline):
            raise ValueError('unchanged original guard')
    guard=OriginalGuard();states={guard:{'phase':'prepared'}}
    (tmp_path/'managed-controller-refusal.json').write_text('{}')
    (tmp_path/'ordinary-stop-requested.json').write_text('{}')
    observed=[]
    monkeypatch.setitem(globals(),'_fixture_validate_recovery_publication',lambda root,path,*,absolute_deadline:observed.append((root,path,absolute_deadline)))
    callback=_fixture_original_draining_guard_barrier(tmp_path,OriginalGuard,states)
    previous=sys.getprofile();deadline=time.monotonic()+1
    try:
        sys.setprofile(callback)
        with pytest.raises(ValueError,match='unchanged original guard'):guard.fresh(deadline)
        assert not (tmp_path/'managed-replay-proof-boundary.json').exists() and sys.getprofile() is callback
        states[guard]['phase']='draining'
        with pytest.raises(ValueError,match='unchanged original guard') as caught:guard.fresh(deadline)
        assert sys.getprofile() is None
        boundary=json.loads((tmp_path/'managed-replay-proof-boundary.json').read_bytes())
        assert boundary=={'pid':os.getpid(),'original_deadline':deadline}
        assert observed==[(tmp_path.parent,tmp_path/'managed-controller-refusal.json',deadline)]
        frames=[];trace=caught.value.__traceback__
        while trace is not None:frames.append(trace.tb_frame.f_code.co_name);trace=trace.tb_next
        assert frames==['test_replay_fixture_profile_targets_only_the_original_draining_guard_and_preserves_its_error','fresh']
    finally:sys.setprofile(previous)


@pytest.mark.parametrize('ignored',['foreign_object','wrong_code','wrong_thread','wrong_phase','before_prepare_send'])
def test_nonce_fixture_profile_waits_only_at_the_sent_original_prepared_guard(tmp_path,monkeypatch,ignored):
    """A prepared observer must neither block preparation nor select foreign calls."""
    calls=[]
    class OriginalGuard:
        def fresh(self, deadline):
            calls.append(self)
            raise ValueError('unchanged prepared guard')
    class OtherGuard:
        def fresh(self, deadline):
            raise ValueError('unchanged other code')
    guard=OriginalGuard();states={guard:{'phase':'prepared','sent':True}}
    refusal=tmp_path/'managed-controller-refusal.json';refusal.write_text('{}')
    ordinary=tmp_path/'ordinary-stop-requested.json'
    boundary=tmp_path/'managed-nonce-proof-boundary.json'
    clock=[10.0];sleeps=[];observed=[]
    monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    def sleep(seconds):
        assert 0<seconds<=.01 and boundary.is_file()
        sleeps.append(seconds);clock[0]+=seconds
        if len(sleeps)==2:ordinary.write_text('{}')
    monkeypatch.setattr(time,'sleep',sleep)
    monkeypatch.setitem(globals(),'_fixture_validate_recovery_publication',lambda root,path,*,absolute_deadline:observed.append((root,path,absolute_deadline)))
    callback=_fixture_original_draining_guard_barrier(tmp_path,OriginalGuard,states,
        intended_phase='prepared',boundary_name=boundary.name)
    previous=sys.getprofile();deadline=10.03
    def ignored_call():
        target=OriginalGuard() if ignored=='foreign_object' else OtherGuard() if ignored=='wrong_code' else guard
        with pytest.raises(ValueError):target.fresh(deadline)
        assert sys.getprofile() is callback
    try:
        if ignored=='wrong_phase':states[guard]['phase']='draining'
        if ignored=='before_prepare_send':states[guard]['sent']=False
        if ignored=='wrong_thread':
            errors=[]
            def foreign_thread():
                try:
                    sys.setprofile(callback);ignored_call()
                except BaseException as error:errors.append(error)
                finally:sys.setprofile(None)
            thread=threading.Thread(target=foreign_thread);thread.start();thread.join(1)
            assert not thread.is_alive() and not errors
        else:
            sys.setprofile(callback);ignored_call()
        assert not boundary.exists() and not ordinary.exists() and not sleeps and not observed
        states[guard].update(phase='prepared',sent=True)
        sys.setprofile(callback)
        with pytest.raises(ValueError,match='unchanged prepared guard') as caught:guard.fresh(deadline)
        assert sys.getprofile() is None and ordinary.is_file() and len(sleeps)==2
        assert json.loads(boundary.read_bytes())=={'pid':os.getpid(),'original_deadline':10.03}
        assert observed==[(tmp_path.parent,refusal,10.03)]
        frames=[];trace=caught.value.__traceback__
        while trace is not None:frames.append(trace.tb_frame.f_code.co_name);trace=trace.tb_next
        assert frames==['test_nonce_fixture_profile_waits_only_at_the_sent_original_prepared_guard','fresh']
        before=boundary.read_bytes();count=len(calls)
        with pytest.raises(ValueError,match='unchanged prepared guard'):guard.fresh(deadline)
        assert len(calls)==count+1 and boundary.read_bytes()==before and len(sleeps)==2 and len(observed)==1
    finally:sys.setprofile(previous)


def test_nonce_fixture_profile_cannot_renew_the_original_prepared_deadline(tmp_path,monkeypatch):
    """Missing ordinary stop exhausts the same guard budget before its body executes."""
    calls=[]
    class OriginalGuard:
        def fresh(self, deadline):calls.append(deadline)
    guard=OriginalGuard();states={guard:{'phase':'prepared','sent':True}}
    (tmp_path/'managed-controller-refusal.json').write_text('{}')
    clock=[10.0];sleeps=[]
    monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    def sleep(seconds):
        assert 0<seconds<=.01;sleeps.append(seconds);clock[0]+=seconds
    monkeypatch.setattr(time,'sleep',sleep)
    callback=_fixture_original_draining_guard_barrier(tmp_path,OriginalGuard,states,
        intended_phase='prepared',boundary_name='managed-nonce-proof-boundary.json')
    previous=sys.getprofile()
    try:
        sys.setprofile(callback)
        with pytest.raises(AssertionError,match='original.*deadline'):guard.fresh(10.025)
        assert sys.getprofile() is None and clock[0]==10.025 and sleeps and not calls
        assert json.loads((tmp_path/'managed-nonce-proof-boundary.json').read_bytes())['original_deadline']==10.025
        assert not (tmp_path/'ordinary-stop-requested.json').exists()
    finally:sys.setprofile(previous)


def test_replay_fixture_recovery_coherence_preserves_python_integral_float_and_refuses_changed_pointer(tmp_path,monkeypatch):
    """The original Python hash, including created_at .0, validates the candidate."""
    from backend.engine import application_launch_lease as lease,runtime_update as update
    from backend.tests.test_application_launch_lease import installed,reserve,stop_fixture
    identity=lease._identity
    monkeypatch.setattr(lease,'_identity',lambda pid:{**identity(pid),'created_at':float(int(identity(pid)['created_at']))} if pid==os.getpid() else identity(pid))
    root,value,_=installed(tmp_path,looping=True);owner=reserve(root,value)
    try:
        owner.start();owner.recovery('Controlled coherent fixture refusal')
        row=lease._load(root);assert type(row['supervisor']['created_at']) is float and row['supervisor']['created_at'].is_integer()
        target=tmp_path/'candidate.json';target.write_bytes((root/lease.LEASES/owner.nonce/'journal.json').read_bytes())
        _fixture_validate_recovery_publication(root,target,absolute_deadline=time.monotonic()+1)
        pointer=root/lease.ACTIVE_LEASE;original=pointer.read_bytes();changed=json.loads(original);changed['record_sha256']='0'*64
        update._write(pointer,changed)
        try:
            with pytest.raises(lease.LaunchLeaseError,match='ownership publication interrupted or changed'):
                _fixture_validate_recovery_publication(root,target,absolute_deadline=time.monotonic()+1)
        finally:update._write(pointer,json.loads(original))
    finally:stop_fixture(owner)


def _fixture_validate_recovery_publication(root, observation, *, absolute_deadline):
    """Use the original Python validator; fixture JSON is only an observation."""
    import json
    import time
    from backend.engine.application_launch_lease import _load
    from backend.engine.runtime_update import _canonical
    assert time.monotonic()<absolute_deadline, 'Fixture coherence exceeded its original absolute deadline'
    row=_load(root);candidate=json.loads(observation.read_bytes())
    assert row is not None and row['state']=='recovery_required' and _canonical(row)==_canonical(candidate)
    assert time.monotonic()<absolute_deadline, 'Fixture coherence exceeded its original absolute deadline'


def _fixture_original_draining_guard_barrier(projects, original_class, retained_states, *,
        intended_phase='draining', boundary_name='managed-replay-proof-boundary.json'):
    """One test-only call boundary; original fresh runs after this returns."""
    import os
    import sys
    import threading
    original_code=original_class.fresh.__code__;original_thread=threading.get_ident()
    def profile(frame,event,arg):
        if event!='call' or frame.f_code is not original_code or threading.get_ident()!=original_thread:return
        state=retained_states.get(frame.f_locals.get('self'))
        if state is None or state['phase']!=intended_phase:return
        # prepared_frame calls fresh before sending its genuine response. Only
        # its later service call can wait for the controller's damaged request.
        if intended_phase=='prepared' and state.get('sent') is not True:return
        sys.setprofile(None)  # No wrapper is added to a genuine guard traceback.
        deadline=frame.f_locals['deadline']
        _atomic_fixture_json(projects/boundary_name,{'pid':os.getpid(),'original_deadline':deadline})
        _fixture_wait_before_original_deadline([projects/'managed-controller-refusal.json',projects/'ordinary-stop-requested.json'],absolute_deadline=deadline)
        _fixture_validate_recovery_publication(projects.parent,projects/'managed-controller-refusal.json',absolute_deadline=deadline)
    return profile


def managed_stack(tmp_path, monkeypatch, *, uncovered=False, damage=None):
    """Signed test-only layout: actual bridge/stop/CPU, controlled small ASGI app.

    No ordinary startup recovery, preflight, training or inspection services
    are run. Their actual lifespan refusal is exercised separately above.
    """
    from backend.tests import test_application_launch_execution as fixtures
    repository = Path(__file__).resolve().parents[2]
    original_fixture = fixtures.fixture
    compiler = "const fs=require('fs'),ts=require('typescript');process.stdout.write(ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText)"
    supervisor = subprocess.check_output(['node', '-e', compiler, str(repository/'src/main/supervisor.ts')])
    scheduling_source=''.join(__import__('inspect').getsource(helper) for helper in (_fixture_wait_before_original_deadline,_fixture_validate_recovery_publication,_fixture_original_draining_guard_barrier,_fixture_cpu_completion_profile))
    backend = ('#!'+sys.executable+'\n'+fixtures._fixture_json_publisher_source()+scheduling_source+f'''
import sys,os,time,json,threading,signal,socket
from pathlib import Path
sys.path.insert(0,{str(repository)!r})
from backend.engine.application_launch_handshake import early_backend_bootstrap,backend_bootstrap_ready,backend_execution_service,backend_work_admission,owned_cpu_writer_scope
early_backend_bootstrap()
from backend.main import OwnedWorkAdmissionMiddleware
from backend.engine.migration_guard import maintenance_guard
from fastapi import FastAPI
import uvicorn
root=Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']);projects=root/'projects'
stop=threading.Event();requested=threading.Event()
signal.signal(signal.SIGTERM,lambda *_:requested.set())
def stdin():
 while os.read(0,65536):pass
 requested.set()
threading.Thread(target=stdin,daemon=True).start()
def stop_requested():
 requested.wait()
 _atomic_fixture_json(projects/'ordinary-stop-requested.json',{{'pid':os.getpid(),'requested':True}})
threading.Thread(target=stop_requested,daemon=True).start()
def cooperative_exit():
 while not stop.is_set():
  if requested.is_set() and (projects/'exit.trigger').exists():stop.set();return
  time.sleep(.01)
threading.Thread(target=cooperative_exit,daemon=True).start()
with maintenance_guard(root):
 backend_bootstrap_ready();state=backend_work_admission()
 original_leave=state.leave
 def observed_leave(*,activity=None,normal_return=False):
  original_leave(activity=activity,normal_return=normal_return)
  if state.snapshot()['active_scopes']==0:
   _atomic_fixture_json(projects/'managed-http-idle.json',{{'pid':os.getpid(),**state.snapshot()}})
 state.leave=observed_leave
 home=Path(os.environ['HOME']);cache=Path(os.environ['XDG_CACHE_HOME']);temporary=Path(os.environ['TMPDIR'])
 assert home==root/'.application-runtime-homes'/os.environ['VISION_APPLICATION_LAUNCH_NONCE']
 assert cache==home/'cache' and temporary==home/'tmp'
 assert os.environ['CFFIXED_USER_HOME']==str(home)
 (projects/'managed-private-environment.json').write_text(json.dumps({{'HOME':str(home),'XDG_CACHE_HOME':str(cache),'TMPDIR':str(temporary),'CFFIXED_USER_HOME':os.environ['CFFIXED_USER_HOME']}}))
 from backend.engine import application_launch_handshake as h
 fds=h._CACHE['writer_handle'].pass_fds
 info=os.fstat(fds[0]);(projects/'original-writer-fd.json').write_text(json.dumps({{'pid':os.getpid(),'fd':fds[0],'device':info.st_dev,'inode':info.st_ino}}))
 app=FastAPI()
 @app.get('/health')
 def errors():
  with owned_cpu_writer_scope() as admitted:
   info=os.fstat(admitted[0]);return {{'controlled':True,'fd':admitted[0],'device':info.st_dev,'inode':info.st_ino}}
 app.add_middleware(OwnedWorkAdmissionMiddleware,admission=state)
 if {uncovered!r}:state.uncovered('accepted_background_work')
 listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
 server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
 server_thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);server_thread.start()
 while not server.started:time.sleep(.01)
 (projects/'managed-server.json').write_text(json.dumps({{'port':listener.getsockname()[1],'pid':os.getpid()}}))
 def late_probe():
  while not stop.is_set():
   if state._closed:
    (projects/'managed-closed-state.json').write_text(json.dumps(state.snapshot()))
    try:
     with owned_cpu_writer_scope():raise AssertionError('late producer admitted')
    except ValueError as e:(projects/'late-producer-refusal.txt').write_text(str(e));return
   time.sleep(.01)
 threading.Thread(target=late_probe,daemon=True).start()
 original_profile=None
 if {damage!r} in ('changed_nonce','identical_request_replay'):
  # Select the genuine post-send prepared guard for changed nonce, or the
  # draining guard for replay. Original calls and exception stacks stay intact.
  phase='prepared' if {damage!r}=='changed_nonce' else 'draining'
  boundary='managed-nonce-proof-boundary.json' if {damage!r}=='changed_nonce' else 'managed-replay-proof-boundary.json'
  original_profile=_fixture_original_draining_guard_barrier(projects,h._BackendShutdown,h._BACKEND_SHUTDOWNS,intended_phase=phase,boundary_name=boundary)
 sys.setprofile(_fixture_cpu_completion_profile(projects,original_profile))
 backend_execution_service(stop)
 server.should_exit=True;server_thread.join(2);listener.close()
 assert not server_thread.is_alive()
 (projects/'backend-cooperative-exit.json').write_text(json.dumps({{'pid':os.getpid(),'requested':requested.is_set()}}))
''').encode()
    main = f'''const fs=require('fs'),path=require('path'),{{spawn}}=require('node:child_process');
const {{authenticateMainLaunch}}=require('../bridge.cjs'),{{BackendSupervisor}}=require('../supervisor.cjs');
(async()=>{{const launch=await authenticateMainLaunch(),executable=path.join(path.dirname(process.argv[1]),'backend_fixture.py');
const errors=fs.openSync(path.join(launch.projects,'execution-backend-error.txt'),'wx');
const backend=spawn({json.dumps(sys.executable)},[executable,'--project-dir',launch.projects,'--shared-auth-dir',launch.auth],{{cwd:launch.root,env:launch.backendEnvironment(process.env),stdio:['pipe','ignore',errors,'pipe']}});fs.closeSync(errors);
await launch.bindBackend(backend,executable,null);
const supervisor=new BackendSupervisor({{autoRestart:false,gracefulShutdownTimeoutMs:4000,ownedApplicationLaunch:launch}});supervisor.childProcess=backend;supervisor.state='HEALTHY';
const damage={json.dumps(damage)},originalSend=launch.frames.send.bind(launch.frames);
launch.frames.send=async(frame,...originalArgs)=>{{if(frame.kind==='managed_drain_proof')fs.writeFileSync(path.join(launch.projects,'managed-forwarded-drain.json'),Buffer.from(frame.proof_b64,'base64'));
if(!damage||frame.kind!=='main_drain_request')return originalSend(frame,...originalArgs);
if(damage==='changed_nonce')return originalSend({{...frame,nonce:'0'.repeat(32)}},...originalArgs);
await originalSend(frame,...originalArgs);
const deadline=originalArgs[0];if(!Number.isFinite(deadline))throw new Error('Controlled replay lacks its original deadline');
while(!fs.existsSync(path.join(launch.projects,'managed-replay-proof-boundary.json'))){{const remaining=deadline-performance.now();if(remaining<=0)throw new Error('Controlled replay boundary exceeded its original deadline');await new Promise(resolve=>setTimeout(resolve,Math.min(10,remaining)));}}
if(performance.now()>=deadline)throw new Error('Controlled replay boundary exceeded its original deadline');
await originalSend(frame,...originalArgs);}};
const prepare=launch.prepareBackendDrain.bind(launch);launch.prepareBackendDrain=async(...args)=>{{const receipt=await prepare(...args);fs.writeFileSync(path.join(launch.projects,'managed-drain-received.json'),JSON.stringify(receipt));return receipt;}};
fs.writeFileSync(path.join(launch.projects,'execution-stack-ready.json'),JSON.stringify({{main:process.pid,backend:backend.pid}}));
const refusal=damage?setInterval(()=>{{try{{const raw=fs.readFileSync(path.join(launch.root,'.application-launches',launch.nonce,'journal.json')),row=JSON.parse(raw),pointer=JSON.parse(fs.readFileSync(path.join(launch.root,'application-launch-lease.json')));
if(row.state==='recovery_required'&&pointer.nonce===row.nonce&&pointer.revision===row.revision){{fs.writeFileSync(path.join(launch.projects,'managed-controller-refusal.json'),raw);launch.refuse();clearInterval(refusal);}}}}catch(e){{}}}},10):null;
let started=false;const timer=setInterval(async()=>{{if(started||!fs.existsSync(path.join(launch.projects,'drain.trigger')))return;started=true;let error=null;
try{{await supervisor.stopBackend();}}catch(e){{error=String(e.stack);}}
fs.writeFileSync(path.join(launch.projects,'managed-stop-result.json'),JSON.stringify({{main:process.pid,backend:backend.pid,error,direct_exit:backend.exitCode,signal:backend.signalCode}}));clearInterval(timer);process.exit(error?3:0);
}},10);
}})().catch(error=>{{fs.writeFileSync(path.join(process.env.VISION_AI_STUDIO_USER_DATA_DIR,'projects','execution-stack-error.txt'),String(error.stack));process.exit(3);}});'''
    def fixture(folder):
        value = original_fixture(folder); sign = value['sign']
        def changed_layout(payload):
            archive=value['directory']/'application.zip'
            with zipfile.ZipFile(archive) as z: files={name:z.read(name) for name in z.namelist()}
            manifest=json.loads(files.pop('portable-application.json'))
            files.update({'bin/app': ('#!'+__import__('shutil').which('node')+'\n'+main).encode(),
                'bin/backend_fixture.py':backend,'supervisor.cjs':supervisor,'applicationLaunch.js':files['bridge.cjs']})
            manifest['files']=[{'path':name,'size':len(raw),'sha256':sha(raw),'executable':name.startswith('bin/')} for name,raw in files.items()]
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('portable-application.json',canonical(manifest))
                for name,raw in files.items():z.writestr(name,raw)
            payload.update(sha256=sha(archive.read_bytes()),size=archive.stat().st_size)
            payload['artifacts'][0].update(sha256=payload['sha256'],size=payload['size'])
            return sign(payload)
        value['sign']=changed_layout;return value
    monkeypatch.setattr(fixtures,'fixture',fixture)
    values=fixtures.cpu_stack(tmp_path)
    _atomic_fixture_json(values[0]/'projects'/completion_boundary.CONFIG,
        {'token':__import__('uuid').uuid4().hex,'root':str(values[0])})
    return values


def _fixture_cpu_completion_profile(projects, original_profile):
    from backend.tests import _source_cpu_completion_boundary as boundary
    from backend.tests.test_application_launch_quiescence_bridge import _cpu_contention_backend
    profile=boundary.backend_profile(projects, original_profile)
    if 'contention' in boundary.read(projects,boundary.CONFIG):
        return _cpu_contention_backend(projects,profile)
    return profile


def _start_ready_cpu_stack(values, **kwargs):
    """Capture the original receipt budget once in the parent before startup."""
    projects=values[0]/'projects'
    if (projects/completion_boundary.CONFIG).exists():
        metadata=completion_boundary.read(projects,completion_boundary.CONFIG)
        assert 'deadline' not in metadata, 'Fixture startup cannot renew its deadline'
        _atomic_fixture_json(projects/completion_boundary.CONFIG,{**metadata,'deadline':time.monotonic()+50})
    return start_cpu_stack(values,**kwargs)


def wait_file(path, *, seconds=5, absolute_deadline=None):
    deadline=time.monotonic()+seconds
    if absolute_deadline is not None:deadline=min(deadline,absolute_deadline)
    while time.monotonic()<deadline:
        if path.is_file():
            raw=path.read_bytes()
            if absolute_deadline is not None:assert time.monotonic()<deadline
            return raw
        time.sleep(.01 if absolute_deadline is None else max(0,min(.01,deadline-time.monotonic())))
    pytest.fail('Missing controlled original fixture output: '+str(path))


def _fixture_terminal_main_exited(child, row, deadline):
    """Inspector data only; the retained original Popen is cleanup authority."""
    from backend.engine.application_launch_lease import _identity
    assert time.monotonic()<deadline
    nonce=getattr(child,'_original_cpu_fixture_nonce',None)
    witness=getattr(child,'_original_cpu_fixture_supervisor',None)
    assert type(nonce) is str and len(nonce)==32 and witness is not None
    assert row['nonce']==nonce and row['supervisor']==witness and child.pid==witness['pid']
    assert _identity(child.pid)==witness
    assert time.monotonic()<deadline
    observed=row.get('exit_observation')
    if observed is None:return False
    assert row['state']=='recovery_required' and row['claimed'] is True
    assert type(row['ready_receipt_sha256']) is str and len(row['ready_receipt_sha256'])==64
    assert set(observed)=={'direct_child_pid','direct_child_returncode','process_tree_exit_verified'}
    assert type(observed['direct_child_pid']) is int and observed['direct_child_pid']==row['process']['pid']
    assert type(observed['direct_child_returncode']) is int and observed['process_tree_exit_verified'] is False
    assert time.monotonic()<deadline
    return True


def _fixture_readonly_managed_launch(root):
    """Observe exact publication without taking the controller transition mutex.

    This fixture observation cannot edit/recover a lease or grant PID authority.
    A changing two-file publication is not usable; only the caller's existing
    absolute observation budget may try a later coherent read.
    """
    from backend.engine.application_launch_controller import _inspection_snapshot
    from backend.engine.application_launch_lease import _load, _public, LaunchLeaseError
    from backend.engine.application_launch_handshake import HandshakeError
    from backend.engine.runtime_update import UpdateError
    try:
        with _inspection_snapshot(root):
            row = _load(root)
        return _public(row) if row else {'state': 'absent', 'release_ready': False}
    except HandshakeError as exc:
        if str(exc) not in {
                'Launch publication changed during read-only inspection',
                'Launch journal or receipt changed during read-only inspection',
                'Launch sidecar publication changed during read-only inspection'}:
            raise
    except LaunchLeaseError as exc:
        if str(exc) != 'Application launch ownership requires recovery: ownership publication interrupted or changed':
            raise
    except UpdateError as exc:
        if str(exc) != 'Update input is not a bounded unlinked regular file':
            raise
    return None


def wait_ordinary_stop(projects, child, *, seconds, boundary_name=None):
    """The backend fixture writes this when the main asks it to stop. When it never comes, say how the controller and
    the main ended (exit code, bounded stop diagnostic) instead of only that the file is missing."""
    # Either backend thread may publish first. Optional boundary observation
    # shares the ordinary marker's original budget instead of starting another.
    deadline=time.monotonic()+seconds if boundary_name is not None else None
    try:ordinary=wait_file(projects/'ordinary-stop-requested.json',seconds=seconds,absolute_deadline=deadline)
    except pytest.fail.Exception:
        stop=projects/'managed-stop-result.json'
        try:result=json.loads(stop.read_bytes()) if stop.is_file() else None
        except ValueError:result={'error':'stop result not complete JSON'}
        pytest.fail(f'No ordinary stop request reached the backend fixture within {seconds}s; controller exit code '
                    f'{child.poll()!r}; main stop result '+(_managed_stop_diagnostic(projects,result) if result else 'absent'))
    if boundary_name is not None:wait_file(projects/boundary_name,seconds=seconds,absolute_deadline=deadline)
    return ordinary


def test_ordinary_stop_boundary_can_publish_after_the_ordinary_marker(tmp_path,monkeypatch):
    """The ordinary observer can win the race with the original service thread."""
    ordinary=tmp_path/'ordinary-stop-requested.json';ordinary.write_text('{"pid":123}')
    boundary=tmp_path/'managed-nonce-proof-boundary.json'
    clock=[10.0];sleeps=[]
    monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    def sleep(seconds):
        assert 0<seconds<=.01 and ordinary.is_file() and not boundary.exists()
        sleeps.append(seconds);clock[0]+=seconds
        boundary.write_text('{"pid":123,"original_deadline":14.0}')
    monkeypatch.setattr(time,'sleep',sleep)
    observed=json.loads(wait_ordinary_stop(tmp_path,None,seconds=3,boundary_name=boundary.name))
    proof=json.loads(boundary.read_bytes())
    assert observed=={'pid':123} and proof=={'pid':123,'original_deadline':14.0}
    assert len(sleeps)==1 and 10.0<clock[0]<13.0


def test_ordinary_stop_boundary_cannot_renew_the_shared_observation_deadline(tmp_path,monkeypatch):
    """A late ordinary marker leaves only the remainder of its original 3s."""
    ordinary=tmp_path/'ordinary-stop-requested.json'
    boundary=tmp_path/'managed-nonce-proof-boundary.json'
    clock=[10.0];sleeps=[]
    monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    def sleep(seconds):
        assert 0<seconds<=.01;sleeps.append(seconds);clock[0]+=seconds
        if clock[0]>=12.98 and not ordinary.exists():ordinary.write_text('{"pid":123}')
        if clock[0]>=13.005 and not boundary.exists():boundary.write_text('{"pid":123,"original_deadline":14.0}')
    monkeypatch.setattr(time,'sleep',sleep)
    with pytest.raises(pytest.fail.Exception,match='Missing controlled original fixture output: .*managed-nonce-proof-boundary.json'):
        wait_ordinary_stop(tmp_path,None,seconds=3,boundary_name=boundary.name)
    assert clock[0]==13.0 and sleeps and ordinary.is_file() and not boundary.exists()


def finish_managed(child, root):
    # Cooperative only for this test-owned backend/main; original controller
    # Popen may be stopped only after its authenticated original main is reaped.
    # poll() reaps only this test's own controller handle (no signal). A controller that already ended needs no stop;
    # returncode alone stays None until a poll, and an exited but unreaped controller reads as a zombie below.
    if child.poll() is not None:return
    import psutil
    projects=root/'projects';(projects/'drain.trigger').touch()
    # An already-authenticated original main exit cannot publish a later
    # ordinary-stop file. Observe that fact only inside the original first5s.
    from backend.engine.application_launch_lease import LeaseTransitionBusy
    first_deadline=time.monotonic()+5
    while not (projects/'ordinary-stop-requested.json').is_file():
        if time.monotonic()>=first_deadline:
            pytest.fail('Missing controlled original fixture output: '+str(projects/'ordinary-stop-requested.json'))
        if child.poll() is not None:return
        try:row=_fixture_readonly_managed_launch(root)
        except LeaseTransitionBusy:
            time.sleep(max(0,min(.05,first_deadline-time.monotonic())));continue
        try:exited=row is not None and _fixture_terminal_main_exited(child,row,first_deadline)
        except psutil.NoSuchProcess:
            # It ended between the poll above and the identity read. Only this handle's own reap may say so; a live
            # process still has to match the retained identity exactly.
            if child.poll() is None:raise
            return
        if exited:
            child.terminate();child.wait(timeout=5);return
        time.sleep(max(0,min(.01,first_deadline-time.monotonic())))
    wait_file(projects/'ordinary-stop-requested.json',seconds=5,absolute_deadline=first_deadline)
    (projects/'exit.trigger').touch()
    wait_file(projects/'managed-stop-result.json',seconds=7)
    deadline=time.monotonic()+5
    from backend.engine.application_launch_lease import LeaseTransitionBusy
    while time.monotonic()<deadline:
        try: row=_fixture_readonly_managed_launch(root)
        except LeaseTransitionBusy:
            # Only the original nonblocking publication mutex may be retried,
            # within this same absolute five-second observation deadline.
            time.sleep(.05);continue
        if row is not None and row.get('exit_observation',{}):
            assert row['exit_observation']['direct_child_pid']==row['process']['pid']
            assert row['exit_observation']['process_tree_exit_verified'] is False
            child.terminate();child.wait(timeout=5);return
        time.sleep(.05)
    pytest.fail('Original controller did not record its retained main Popen exit; retain this scope')


def _managed_stop_diagnostic(projects, result):
    """Failure-only, bounded controlled fixture facts; no authority or secrets."""
    try:
        import re
        import stat
        def safe_text(value, limit):
            if value is None:
                return None
            if type(value) is not str:
                return {'type': type(value).__name__[:80]}
            # Synthetic fixture stderr still redacts paths and credential-shaped
            # data. Never read token files or the process environment here.
            text = value[-limit:]
            text = re.sub(r'(?im)^.*\b(?:authorization|password|secret|token|nonce|cookie|api[_-]?key)\b\s*(?:=|:)[^\n]*$', '<redacted credential-shaped line>', text)
            text = re.sub(r'(?i)\b(?:bearer|token|authorization|password|secret|nonce|cookie|api[_-]?key)\b\s*(?::|=)?\s*[^\s,;]+', '<redacted>', text)
            text = re.sub(r'[A-Za-z]:[\\/][^\s:]+|(?:/[^\s/:]+)+', '<path>', text)
            text = re.sub(r'\b[A-Za-z0-9_+=-]{32,}\b', '<opaque>', text)
            return ''.join(c if c in '\n\t' or 32 <= ord(c) < 127 else '?' for c in text)
        details = {'direct_exit': result.get('direct_exit'),
                   'signal': safe_text(result.get('signal'), 128),
                   'error': safe_text(result.get('error'), 4096)}
        path = Path(projects) / 'execution-backend-error.txt'
        descriptor = None
        stderr_error = None
        try:
            named = path.lstat()
            if not stat.S_ISREG(named.st_mode):
                raise ValueError('Controlled backend stderr is not a regular file')
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                raise ValueError('Controlled backend stderr identity changed')
            raw = os.read(descriptor, 8193)
            details['backend_stderr'] = {'observed_size': opened.st_size,
                'read_size': len(raw), 'truncated': opened.st_size > len(raw) or len(raw) > 8192,
                'read_sha256': hashlib.sha256(raw).hexdigest(),
                'text_prefix': safe_text(raw[:8192].decode('utf-8', errors='replace'), 8192)}
        except BaseException as error:
            stderr_error = type(error).__name__[:80]
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    details['stderr_close_error_type'] = type(error).__name__[:80]
        if stderr_error is not None:
            details['stderr_read_error_type'] = stderr_error
        message = json.dumps(details, ensure_ascii=True, sort_keys=True)
        if len(message.encode('utf-8')) > 16384:
            return 'Controlled stop diagnostic exceeded its byte bound'
        return message
    except BaseException as error:
        return 'Controlled stop diagnostic unavailable: ' + type(error).__name__[:80]


@contextmanager
def _managed_fixture_cleanup(child, root):
    """Run original cleanup without replacing an earlier test failure."""
    primary=None
    try:
        yield
    except BaseException as error:
        primary=error
        raise
    finally:
        try:finish_managed(child, root)
        except BaseException as error:
            if primary is None:raise
            try:primary.add_note('Controlled managed fixture cleanup also failed: '+type(error).__name__+': '+str(error))
            except BaseException:pass


@pytest.mark.parametrize('uncovered',[False,True])
def test_actual_source_cpu_then_managed_drain_and_original_node_exit_keeps_lease_blocked(tmp_path,monkeypatch,uncovered):
    import urllib.request,urllib.error
    values=managed_stack(tmp_path,monkeypatch,uncovered=uncovered);root=values[0];projects=root/'projects'
    child,ack=_start_ready_cpu_stack(values,prefix=['-c',_source_cpu_settlement_fixture_prefix()])
    with _managed_fixture_cleanup(child, root):
        _,cpu=_wait_receipt_and_cpu_settlement(root,ack['nonce'],child)
        assert cpu['semantic_output']['final_verdict']=='OK'
        server=json.loads(wait_file(projects/'managed-server.json'))
        fd=json.loads(wait_file(projects/'original-writer-fd.json'))
        environment=json.loads(wait_file(projects/'managed-private-environment.json'))
        assert environment['HOME']==str(root/'.application-runtime-homes'/ack['nonce'])
        assert fd['pid']==cpu['backend_process']['pid']==server['pid']
        with urllib.request.urlopen(f"http://127.0.0.1:{server['port']}/health",timeout=1) as response:
            assert response.status==200
            admitted=json.load(response)
            assert all(admitted[k]==fd[k] for k in ('device','inode'))
            assert admitted['fd']!=fd['fd']
        idle=json.loads(wait_file(projects/'managed-http-idle.json'))
        assert idle['pid']==server['pid'] and idle['active_scopes']==0
        from backend.engine.application_launch_quiescence import inspect_epoch
        before=inspect_epoch(root,ack['nonce']);assert before['registry']['state']=='open'
        registered=next(r for r in before['registry']['writers'] if r['role']=='backend')
        controls={p:p.read_bytes() for p in [root/'application-active.json',root/'global-active.json']}
        (projects/'drain.trigger').touch()
        if not uncovered:
            received=json.loads(wait_file(projects/'managed-drain-received.json',seconds=3))
            assert received['status']=='managed_scopes_drained' and received['scope']=='reviewed_foreground_scopes_only'
        # Both positive scope and refused unsupported work close admission.
        wait_file(projects/'late-producer-refusal.txt',seconds=3)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"http://127.0.0.1:{server['port']}/health",timeout=1)
        assert error.value.code==409
        import fcntl
        lock=root/'.application-writer-epochs'/ack['nonce']/'writers'/registered['writer_id']/'ownership.lock'
        probe=os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(probe)
        ordinary=json.loads(wait_ordinary_stop(projects,child,seconds=3))
        assert ordinary=={'pid':server['pid'],'requested':True}
        (projects/'exit.trigger').touch()
        result=json.loads(wait_file(projects/'managed-stop-result.json',seconds=5))
        assert result['direct_exit']==0 and result['signal'] is None, _managed_stop_diagnostic(projects, result)
        assert (result['error'] is not None)==uncovered
        if uncovered:
            finish_managed(child,root)  # Original refused/uncovered path stays blocked.
            snapshot=inspect_epoch(root,ack['nonce'])
        else:
            # Same original final five-second observation cap, without the
            # failure-only finalizer's controller termination racing closure.
            assert child.wait(timeout=5)==0
            from backend.engine.application_launch_lease import _load
            from backend.engine import application_store_writer_closure as closure
            from backend.engine import runtime_update as update
            row=_load(root)
            assert row['protocol_version']==5 and row['state']=='writer_store_closed'
            closed=closure.inspect_store_closure(root,row)
            assert closed['scope']=='original_managed_store_writers'
            assert closed['nonce']==ack['nonce'] and closed['unsupported']==[]
            assert closed['managed_scopes_drained'] is True
            assert closed['all_enrolled_writers_direct_exited'] is True
            assert closed['all_writer_lock_references_closed'] is True
            assert all(closed[name] is False for name in closure.FALSE_FLAGS)
            archived=root/closure.ARCHIVES/ack['nonce']
            registry_raw=update._read(archived/'registry.json',128*1024)
            assert sha(registry_raw)==closed['registry_sha256']
            snapshot={'registry':update._json(registry_raw),'registry_sha256':sha(registry_raw)}
            assert not (root/'.application-writer-epochs'/ack['nonce']).exists()
        assert snapshot['registry']['state']=='closed'
        backend=next(r for r in snapshot['registry']['writers'] if r['role']=='backend')
        assert backend['status']==('active' if uncovered else 'direct_exited')
        assert backend['exit_code']==(None if uncovered else 0)
        assert backend['process']==cpu['backend_process']
        assert backend['lock_identity']=={'device':fd['device'],'inode':fd['inode']}
        assert {p:p.read_bytes() for p in controls}==controls
        from backend.engine.application_launch_lease import _load,assert_quiescent
        if uncovered:
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                row=_load(root)
                if row['state']=='recovery_required':break
                time.sleep(.01)
            assert row['state']=='recovery_required'
        else:
            assert row['protocol_version']==5 and row['state']=='writer_store_closed'
        drain=row['writer_drain']
        assert drain['phase']==('refused' if uncovered else 'backend_exited')
        assert drain['receipt']['whole_writer_coverage'] is False
        assert drain['receipt']['can_release_launch_lease'] is False
        assert (drain['backend_exit'] is None)==uncovered
        if uncovered:
            with pytest.raises(ValueError):assert_quiescent(root)
        else:
            assert assert_quiescent(root)['scope']=='original_managed_store_mutation_only'
        (projects/'managed-drain-proof.json').write_bytes(canonical({'scope':'controlled_source_backend_without_normal_startup_services',
            'cpu':cpu,'writer':snapshot,'drain':drain,'stop':result,'native':False,'whole_tree':False,'lease_release':False}))


def test_managed_idle_callback_never_exposes_a_partial_final_json_name(tmp_path, monkeypatch):
    """Exercise the generated fixture callback, not an application/owner proof."""
    import ast
    import inspect
    import io
    from types import SimpleNamespace
    from backend.tests.test_application_launch_execution import _atomic_fixture_json

    tree=ast.parse(inspect.getsource(managed_stack))
    script=next(node for node in ast.walk(tree) if isinstance(node,ast.JoinedStr)
        and any(isinstance(part,ast.Constant) and 'managed-http-idle.json' in str(part.value)
                for part in node.values))
    # Interpolated repository/boolean inputs are irrelevant to this exact
    # extracted callback; preserve its actual literal body and helper call.
    literal=''.join(part.value if isinstance(part,ast.Constant) else 'None' for part in script.values)
    callback=next(node for node in ast.walk(ast.parse(literal))
        if isinstance(node,ast.FunctionDef) and node.name=='observed_leave')
    target=tmp_path/'managed-http-idle.json'
    fact={'active_scopes':0,'unsupported':[]};calls=[];visible_partial=[]
    real_open=io.open
    def observe_open(name,mode='r',*args,**kwargs):
        opened=real_open(name,mode,*args,**kwargs)
        if os.fspath(name)==os.fspath(target) and ('w' in mode or 'x' in mode):
            with real_open(target,'rb') as reader:visible_partial.append(reader.read())
        return opened
    monkeypatch.setattr(io,'open',observe_open)
    def original_leave(*,activity=None,normal_return=False):
        calls.append((activity,normal_return))
    namespace={'original_leave':original_leave,'state':SimpleNamespace(snapshot=lambda:dict(fact)),
        'projects':tmp_path,'json':json,'os':os,'_atomic_fixture_json':_atomic_fixture_json}
    exec(compile(ast.Module(body=[callback],type_ignores=[]),'<actual managed idle fixture callback>','exec'),namespace)
    namespace['observed_leave'](activity='controlled-request',normal_return=True)
    assert calls==[('controlled-request',True)]
    assert json.loads(target.read_bytes())=={'pid':os.getpid(),**fact}
    assert visible_partial==[], 'Reader could observe the empty final marker before JSON was written'
    assert sorted(path.name for path in tmp_path.iterdir())==['managed-http-idle.json']


@pytest.mark.parametrize('primary_type',[RuntimeError,KeyboardInterrupt,SystemExit])
def test_managed_cleanup_retains_primary_and_reports_secondary(monkeypatch,primary_type):
    """Modeled cleanup errors only; no process or lifecycle credit."""
    primary=primary_type('original controlled body failure')
    secondary=ValueError('controlled cleanup lock busy')
    calls=[];child=object();root=object()
    def cleanup(actual_child,actual_root):
        assert actual_child is child and actual_root is root
        calls.append('original-cleanup');raise secondary
    monkeypatch.setitem(globals(),'finish_managed',cleanup)
    with pytest.raises(primary_type) as caught:
        with _managed_fixture_cleanup(child,root):raise primary
    assert caught.value is primary
    assert calls==['original-cleanup']
    assert getattr(primary,'__notes__',[])==[
        'Controlled managed fixture cleanup also failed: ValueError: controlled cleanup lock busy']


def test_managed_cleanup_without_primary_propagates_exact_cleanup_error(monkeypatch):
    secondary=ValueError('controlled cleanup lock busy')
    def cleanup(*args):raise secondary
    monkeypatch.setitem(globals(),'finish_managed',cleanup)
    with pytest.raises(ValueError) as caught:
        with _managed_fixture_cleanup(object(),object()):pass
    assert caught.value is secondary


def test_managed_cleanup_success_retains_original_body_error_or_result(monkeypatch):
    calls=[]
    monkeypatch.setitem(globals(),'finish_managed',lambda *args:calls.append('cleanup'))
    primary=RuntimeError('original controlled body failure')
    with pytest.raises(RuntimeError) as caught:
        with _managed_fixture_cleanup(object(),object()):raise primary
    assert caught.value is primary and not getattr(primary,'__notes__',[])
    with _managed_fixture_cleanup(object(),object()):calls.append('body')
    assert calls==['cleanup','body','cleanup']


class _ModeledControllerHandle:
    """Modeled Popen only: poll answers come from a list and terminate is recorded, never sent anywhere."""
    def __init__(self, polls):
        self.polls=list(polls);self.calls=[]
    def poll(self):
        self.calls.append('poll');return self.polls.pop(0) if len(self.polls)>1 else self.polls[0]
    def terminate(self):self.calls.append('terminate')
    def wait(self, timeout=None):self.calls.append('wait');return 0


def test_finish_managed_reaps_a_controller_that_already_ended_without_stopping_it(tmp_path):
    (tmp_path/'projects').mkdir();child=_ModeledControllerHandle([1])
    finish_managed(child,tmp_path)
    assert child.calls==['poll'] and not (tmp_path/'projects'/'drain.trigger').exists()


@pytest.mark.parametrize('still_running',[False,True])
def test_finish_managed_identity_read_race_needs_the_handles_own_exit(tmp_path,monkeypatch,still_running):
    """An exited but unreaped controller reads as a zombie; only its own handle's exit lets cleanup end quietly."""
    import psutil
    (tmp_path/'projects').mkdir()
    child=_ModeledControllerHandle([None,None,None] if still_running else [None,None,0])
    monkeypatch.setitem(globals(),'_fixture_readonly_managed_launch',lambda root:{'state':'claimed'})
    def zombie(*args):raise psutil.ZombieProcess(os.getpid())  # an exception value only; no process is read or signalled
    monkeypatch.setitem(globals(),'_fixture_terminal_main_exited',zombie)
    if still_running:
        with pytest.raises(psutil.ZombieProcess):finish_managed(child,tmp_path)
    else:finish_managed(child,tmp_path)
    assert child.calls==['poll','poll','poll'] and 'terminate' not in child.calls


def test_missing_ordinary_stop_names_how_the_controller_and_main_ended(tmp_path):
    projects=tmp_path/'projects';projects.mkdir();child=_ModeledControllerHandle([3])
    (projects/'managed-stop-result.json').write_text(json.dumps({'direct_exit':None,'signal':'SIGKILL',
        'error':'Error: Original backend exit is unverified after the shutdown deadline'}))
    with pytest.raises(pytest.fail.Exception) as caught:wait_ordinary_stop(projects,child,seconds=.05)
    message=str(caught.value)
    assert message.startswith('No ordinary stop request reached the backend fixture within 0.05s; controller exit code 3;')
    assert 'unverified after the shutdown deadline' in message and 'SIGKILL' in message
    with pytest.raises(pytest.fail.Exception) as caught:wait_ordinary_stop(projects/'absent',child,seconds=.05)
    assert str(caught.value).endswith('main stop result absent')


def _assert_writer_snapshot_raw(snapshot, registry_raw, publication_raw):
    """Exact evidence bytes only; decoded values never replace durable pins."""
    assert type(snapshot) is dict and set(snapshot)=={'registry','registry_sha256'}
    assert type(registry_raw) is bytes and 0<len(registry_raw)<=65536
    assert type(publication_raw) is bytes and 0<len(publication_raw)<=65536
    def exact_pairs(pairs):
        value={}
        for key,item in pairs:
            assert key not in value;value[key]=item
        return value
    registry=json.loads(registry_raw,object_pairs_hook=exact_pairs)
    publication=json.loads(publication_raw,object_pairs_hook=exact_pairs)
    assert registry==snapshot['registry'] and sha(registry_raw)==snapshot['registry_sha256']
    assert type(registry['revision']) is int and registry['revision']>0
    assert type(publication['schema_version']) is int and publication['schema_version']==1
    assert type(publication['revision']) is int
    assert publication=={'schema_version':1,'nonce':registry['binding']['nonce'],
        'revision':registry['revision'],'registry_sha256':snapshot['registry_sha256']}


def _writer_snapshot_raw_evidence(root, nonce):
    """Read original validated publication before/after; never mint authority."""
    from backend.engine import application_launch_quiescence as quiescence
    snapshot=quiescence.inspect_epoch(root,nonce)
    directory=quiescence._path(root,nonce);update=quiescence._update()
    registry_raw=update._read(directory/'registry.json',quiescence.DOCUMENT_LIMIT)
    publication_raw=update._read(directory/'publication.json')
    assert quiescence.inspect_epoch(root,nonce)==snapshot
    _assert_writer_snapshot_raw(snapshot,registry_raw,publication_raw)
    return snapshot,registry_raw,publication_raw


def _assert_cpu_receipt_raw(receipt, receipt_raw, receipt_sha256):
    """Durable receipt bytes only; request/completion hashes stay canonical."""
    assert type(receipt_raw) is bytes and 0<len(receipt_raw)<=65536
    assert type(receipt_sha256) is str and len(receipt_sha256)==64
    def exact_pairs(pairs):
        value={}
        for key,item in pairs:
            assert key not in value;value[key]=item
        return value
    assert json.loads(receipt_raw,object_pairs_hook=exact_pairs)==receipt
    assert sha(receipt_raw)==receipt_sha256


def _cpu_receipt_raw_evidence(root, nonce, receipt):
    """Read original validated journal before/after without remint or writes."""
    from backend.engine import application_launch_lease as lease
    journal=lease.inspect_launch(root)
    receipt_raw=lease._update()._read(root/lease.LEASES/nonce/'cpu-execution-receipt.json')
    assert lease.inspect_launch(root)==journal
    assert journal['nonce']==receipt['nonce']==nonce
    execution=journal['cpu_execution']
    assert execution['request_id']==receipt['request_id'] and execution['request_sha256']==receipt['request_sha256']
    _assert_cpu_receipt_raw(receipt,receipt_raw,execution['receipt_sha256'])
    return receipt_raw


def _assert_returned_cpu_settlement(observation, cpu, controller, *, registry_raw, publication_raw, cpu_receipt_raw):
    """Fixture evidence only; original typed settlement is never reminted here."""
    assert type(observation) is dict and set(observation)=={'schema_version','kind','controller_process',
        'receipt','request','completion','settlement','writer_snapshot'}
    assert type(observation['schema_version']) is int and observation['schema_version']==1
    assert observation['kind']=='original_source_cpu_controller_returned'
    assert observation['controller_process']==controller and observation['receipt']==cpu
    assert cpu['status']=='succeeded' and cpu['actual_cpu_execution_verified'] is True
    assert cpu['backend_frozen'] is False and cpu['execution_scope']=='controlled_source_backend'
    assert cpu['actual_application_inference_verified'] is False and cpu['release_ready'] is False
    request=observation['request'];completion=observation['completion']
    assert set(request)=={'schema_version','kind','challenge','request_id','nonce','epoch',
        'binding_sha256','backend_claim_sha256','workspace_id','project_id','plan_sha256'}
    assert type(request['schema_version']) is int and request['schema_version']==1
    assert request['kind']=='cpu_execution_request' and sha(canonical(request))==cpu['request_sha256']
    for name in ('request_id','nonce','epoch','backend_claim_sha256','workspace_id','project_id','plan_sha256'):
        assert request[name]==cpu[name]
    assert request['binding_sha256']==sha(canonical(cpu['binding']))
    assert set(completion)=={'schema_version','kind','request','backend_proof','output_path','output_sha256',
        'semantic_output','worker_pid','runtime_source_sha256'}
    assert type(completion['schema_version']) is int and completion['schema_version']==1
    assert completion['kind']=='cpu_execution_completed' and completion['request']==request
    assert sha(canonical(completion['backend_proof']))==cpu['backend_claim_sha256']
    assert completion['backend_proof']['process']==cpu['backend_process']
    for name in ('output_path','output_sha256','semantic_output','worker_pid','runtime_source_sha256'):
        assert completion[name]==cpu[name]
    _assert_cpu_receipt_raw(cpu,cpu_receipt_raw,observation['settlement']['receipt_sha256'])
    assert observation['settlement']=={'schema_version':1,'kind':'source_cpu_settled','nonce':cpu['nonce'],
        'request_id':cpu['request_id'],'request_sha256':cpu['request_sha256'],
        'completion_sha256':sha(canonical(completion)),'receipt_sha256':sha(cpu_receipt_raw)}
    snapshot=observation['writer_snapshot'];assert set(snapshot)=={'registry','registry_sha256'}
    _assert_writer_snapshot_raw(snapshot,registry_raw,publication_raw)
    registry=snapshot['registry']
    assert registry['state']=='open' and registry['binding']['nonce']==cpu['nonce']
    assert registry['binding']['controller']==controller
    assert registry['binding']['launch_binding_sha256']==sha(canonical(cpu['binding']))
    writers=registry['writers'];assert len(writers)==2
    backend=next(row for row in writers if row['role']=='backend')
    assert backend['status']=='active' and backend['process']==cpu['backend_process'] and backend['exit_code'] is None
    finished=next(row for row in writers if row['role']=='owned_cpu_worker')
    assert finished['status']=='direct_exited' and finished['reason_code'] is None and finished['exit_code']==0
    assert finished['process']['pid']==cpu['worker_pid'] and finished['process']!=backend['process']


def _record_returned_cpu_settlement(owner, receipt):
    """Called only after the original execute_cpu returned, not receipt publish."""
    from backend.engine import application_owned_cpu_child_relay as relay
    from backend.engine.application_launch_lease import _identity
    capability=owner._cpu_relays[receipt['request_id']]
    assert type(capability) is relay.ControllerCpuRelay
    state=relay._CONTROLLERS[capability]
    assert state['owner'] is owner and state['node'] is owner._node_backend_authority
    assert state['epoch'] is owner._writer_epoch and state['pid']==os.getpid()
    assert state['thread'] is threading.current_thread() and state['failed'] is False
    assert state['phase']=='finished' and state['settled'] is True
    publication=state['publication'];assert publication['receipt']==receipt
    cpu_receipt_raw=_cpu_receipt_raw_evidence(owner.root,receipt['nonce'],receipt)
    _assert_cpu_receipt_raw(receipt,cpu_receipt_raw,publication['receipt_sha256'])
    assert publication['completion_sha256']==sha(canonical(publication['completion']))
    snapshot,registry_raw,publication_raw=_writer_snapshot_raw_evidence(owner.root,receipt['nonce'])
    assert registry_raw==owner._writer_epoch._raw and publication_raw==owner._writer_epoch._pointer
    assert owner._writer_epoch.snapshot()==snapshot
    observation={'schema_version':1,'kind':'original_source_cpu_controller_returned',
        'controller_process':_identity(os.getpid()),'receipt':receipt,'request':state['plan']['cpu_request'],
        'completion':publication['completion'],'settlement':owner._cpu_settlement,
        'writer_snapshot':snapshot}
    _assert_returned_cpu_settlement(observation,receipt,observation['controller_process'],
        registry_raw=registry_raw,publication_raw=publication_raw,cpu_receipt_raw=cpu_receipt_raw)
    finished=next(row for row in observation['writer_snapshot']['registry']['writers']
        if row['writer_id']==state['registration'].writer_id)
    assert finished['process']==state['child'] and finished['role']=='owned_cpu_worker'
    raw=canonical(observation);assert len(raw)<=65536
    target=owner.root/'projects'/'source-cpu-controller-returned.json'
    from backend.tests.test_application_launch_execution import _atomic_fixture_json
    _atomic_fixture_json(target,observation,exclusive=True,sort_keys=True)
    # This empty marker follows full write/fsync and temporary-link cleanup.
    # It is publication metadata; the original reader still verifies all facts.
    complete=Path(str(target)+'.complete')
    descriptor=os.open(complete,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    os.close(descriptor)


def _source_cpu_settlement_fixture_prefix():
    """Original controller executes once; only its returned typed state is noted."""
    return '''from backend.engine import application_launch_controller as controller
from backend.tests import test_application_launch_quiescence_bridge as fixture
original_execute_cpu=controller.execute_cpu
def execute_cpu(owner, capability):
    receipt=fixture._execute_ready_source_cpu(original_execute_cpu, owner, capability)
    fixture._record_returned_cpu_settlement(owner, receipt)
    return receipt
controller.execute_cpu=execute_cpu
raise SystemExit(controller.main())
'''


def _execute_ready_source_cpu(original,owner,capability):
    try:
        with completion_boundary.controller_boundary(owner,original.__code__):
            projects=owner.root/'projects'
            if (projects/completion_boundary.CONFIG).exists() and 'contention' in completion_boundary.read(projects,completion_boundary.CONFIG):
                return _cpu_contention_execute(original,owner,capability)
            return original(owner,capability)
    except BaseException as error:
        _record_cpu_fixture_failure(owner.root/'projects',error)
        raise


def _record_cpu_fixture_failure(projects,error):
    """Bounded code/phase/type and hashes only; never exception text or locals."""
    try:
        frames=[];tb=error.__traceback__
        while tb is not None and len(frames)<12:
            frames.append({'function':tb.tb_frame.f_code.co_name[:80],'line':tb.tb_lineno});tb=tb.tb_next
        markers={}
        for name in (completion_boundary.CONFIG,completion_boundary.PENDING,completion_boundary.READY,'cpu-gate-held.json','cpu-gate-enter.json'):
            path=projects/name
            markers[name]={'exists':path.is_file()}
            if path.is_file():
                with path.open('rb') as stream:raw=stream.read(4097)
                if len(raw)<=4096:markers[name]['sha256']=sha(raw)
        completion_boundary.write(projects,'source-cpu-fixture-error.json',{'phase':'completion_before_controller_return',
            'exception':type(error).__name__[:80],'frames':frames,'markers':markers})
    except BaseException:pass  # Diagnostics cannot replace the original failure.


def _wait_returned_cpu_settlement(root, nonce, child, cpu, deadline):
    """Bounded read-only fixture barrier; observations cannot become authority."""
    import stat
    from backend.engine.application_launch_lease import inspect_launch, _identity, LeaseTransitionBusy
    target=root/'projects'/'source-cpu-controller-returned.json'
    complete=Path(str(target)+'.complete')
    witness=getattr(child,'_original_cpu_fixture_supervisor',None)
    assert type(deadline) in (int,float) and math.isfinite(deadline)
    assert getattr(child,'_original_cpu_fixture_nonce',None)==nonce and witness is not None
    assert child.pid==witness['pid']
    while time.monotonic()<deadline:
        assert child.poll() is None, 'Original controller died before CPU settlement observation'
        assert _identity(child.pid)==witness and cpu['nonce']==nonce
        assert time.monotonic()<deadline
        try:
            published=complete.lstat()
        except FileNotFoundError:
            time.sleep(max(0,min(.01,deadline-time.monotonic())));continue
        assert stat.S_ISREG(published.st_mode) and published.st_nlink==1 and published.st_size==0
        try:
            named=target.lstat()
        except FileNotFoundError:
            time.sleep(max(0,min(.01,deadline-time.monotonic())));continue
        assert stat.S_ISREG(named.st_mode) and named.st_nlink==1 and 0<named.st_size<=65536
        descriptor=os.open(target,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            held=os.fstat(descriptor);assert (held.st_dev,held.st_ino,held.st_size)==(named.st_dev,named.st_ino,named.st_size)
            with os.fdopen(descriptor,'rb',closefd=False) as file:raw=file.read(65537)
            assert len(raw)==held.st_size and len(raw)<=65536
        finally:os.close(descriptor)
        def exact_pairs(pairs):
            value={}
            for key,item in pairs:
                assert key not in value;value[key]=item
            return value
        observation=json.loads(raw,object_pairs_hook=exact_pairs)
        try:
            journal=inspect_launch(root);snapshot,registry_raw,publication_raw=_writer_snapshot_raw_evidence(root,nonce)
            cpu_receipt_raw=_cpu_receipt_raw_evidence(root,nonce,cpu)
        except LeaseTransitionBusy:
            # Only original read-only entry busy; retain the original 50 s cap.
            time.sleep(max(0,min(.01,deadline-time.monotonic())));continue
        assert time.monotonic()<deadline
        _assert_returned_cpu_settlement(observation,cpu,witness,registry_raw=registry_raw,publication_raw=publication_raw,cpu_receipt_raw=cpu_receipt_raw)
        assert journal['state']=='ready' and journal['nonce']==nonce and journal['supervisor']==witness
        assert journal['binding']==cpu['binding'] and journal['process']==cpu['main_process']
        assert journal['writer_drain']['phase']=='enrolled'
        assert snapshot==observation['writer_snapshot']
        backend=next(row for row in snapshot['registry']['writers'] if row['role']=='backend')
        assert journal['writer_drain']['writer_id']==backend['writer_id']
        assert journal['writer_drain']['registration_sha256']==backend['registration_sha256']
        execution=journal['cpu_execution']
        assert execution['request_id']==cpu['request_id'] and execution['request_sha256']==cpu['request_sha256']
        directory=root/'.application-launches'/nonce
        receipt_raw=(directory/'cpu-execution-receipt.json').read_bytes();assert receipt_raw==cpu_receipt_raw
        assert execution['receipt_sha256']==sha(receipt_raw)==observation['settlement']['receipt_sha256']
        assert json.loads(receipt_raw)==cpu
        intent_raw=(directory/'cpu-execution-intent.json').read_bytes()
        assert execution['intent_sha256']==sha(intent_raw)
        assert json.loads(intent_raw)['request']==observation['request']
        assert target.read_bytes()==raw and _identity(child.pid)==witness and child.poll() is None
        assert time.monotonic()<deadline
        return
    pytest.fail('Original typed SOURCE CPU settlement did not return within the original receipt deadline')


def _wait_receipt_and_cpu_settlement(root, nonce, child):
    deadline=time.monotonic()+50
    if (root/'projects'/completion_boundary.CONFIG).exists():
        deadline=completion_boundary.read(root/'projects',completion_boundary.CONFIG)['deadline']
    remaining=deadline-time.monotonic();assert remaining>0
    target,cpu=wait_receipt(root,nonce,child,timeout=remaining)
    assert time.monotonic()<deadline
    _wait_returned_cpu_settlement(root,nonce,child,cpu,deadline)
    return target,cpu


def _finish_returned_source_cpu_fixture(root, child):
    """Positive counterpart of the original five/seven/five-second cleanup.

    The caller already observed the returned typed CPU settlement. Let the
    original controller finish its successful publication and exit naturally;
    the failure-only finalizer must not race it with controller termination.
    """
    projects=root/'projects'
    (projects/'drain.trigger').touch()
    ordinary=json.loads(wait_file(projects/'ordinary-stop-requested.json',seconds=5))
    assert type(ordinary['pid']) is int and ordinary['requested'] is True
    (projects/'exit.trigger').touch()
    stopped=json.loads(wait_file(projects/'managed-stop-result.json',seconds=7))
    assert stopped['backend']==ordinary['pid'] and stopped['error'] is None, _managed_stop_diagnostic(projects,stopped)
    assert stopped['direct_exit']==0 and stopped['signal'] is None
    assert child.wait(timeout=5)==0


def _closed_source_cpu_fixture_epoch(root, nonce, cpu):
    """Validate the original protocol5 archive; no native or tree approval."""
    from backend.engine import application_launch_lease as lease, application_store_writer_closure as closure
    from backend.engine import runtime_update as update
    row=lease._load(root)
    assert row['protocol_version']==5 and row['state']=='writer_store_closed' and row['nonce']==nonce
    closed=closure.inspect_store_closure(root,row)
    assert closed['scope']=='original_managed_store_writers' and closed['nonce']==nonce
    assert closed['unsupported']==[] and closed['managed_scopes_drained'] is True
    assert closed['all_enrolled_writers_direct_exited'] is True and closed['all_writer_lock_references_closed'] is True
    assert all(closed[name] is False for name in closure.FALSE_FLAGS)
    drain=row['writer_drain'];receipt=drain['receipt']
    assert drain['phase']=='backend_exited' and receipt['status']=='managed_scopes_drained'
    assert receipt['active_scopes']==0 and receipt['unsupported']==[]
    assert all(receipt[name] is False for name in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))
    assert drain['backend_exit']['backend_process']==cpu['backend_process']
    assert drain['backend_exit']['returncode']==0 and drain['backend_exit']['signal'] is None
    archived=root/closure.ARCHIVES/nonce
    registry_raw=update._read(archived/'registry.json',128*1024)
    assert sha(registry_raw)==closed['registry_sha256']
    snapshot={'registry':update._json(registry_raw),'registry_sha256':sha(registry_raw)}
    assert snapshot['registry']['state']=='closed' and not (root/'.application-writer-epochs'/nonce).exists()
    writers=snapshot['registry']['writers']
    assert [(writer['role'],writer['status'],writer['exit_code'],writer['reason_code']) for writer in writers]==[
        ('backend','direct_exited',0,None),('owned_cpu_worker','direct_exited',0,None)]
    assert writers[0]['process']==cpu['backend_process'] and writers[1]['process']['pid']==cpu['worker_pid']
    mutation=lease.assert_quiescent(root)
    assert mutation['scope']=='original_managed_store_mutation_only' and mutation['nonce']==nonce
    assert all(mutation[name] is False for name in closure.FALSE_FLAGS)
    return row,snapshot


def _assert_damaged_refusal(refusal, damage, cpu):
    """Exact captured protocol rejection, never a generic failure allowance."""
    assert damage in {'changed_nonce','identical_request_replay'}
    cause = ('Original main managed drain request differs or replayed' if damage=='changed_nonce'
             else 'Prepared shutdown reply kind differs')
    assert refusal['state']=='recovery_required'
    assert refusal['reason']=='Process-tree ownership is unresolved: '+cause
    assert refusal['nonce']==cpu['nonce'] and refusal['binding']==cpu['binding']
    assert refusal['process']==cpu['main_process']
    drain=refusal['writer_drain']
    assert drain['phase']==('enrolled' if damage=='changed_nonce' else 'closing')
    assert drain['receipt'] is None and drain['backend_exit'] is None
    if damage=='changed_nonce':assert drain['request'] is None
    else:
        request=drain['request']
        assert set(request)=={'schema_version','kind','challenge','request_id','nonce','epoch',
            'binding_sha256','backend_claim_sha256','writer_id','registration_sha256','closed_registry_sha256','budget_ms'}
        assert type(request['schema_version']) is int and request['schema_version']==1
        assert request['kind']=='backend_drain_request' and request['nonce']==cpu['nonce'] and request['epoch']==cpu['epoch']
        assert request['binding_sha256']==sha(canonical(cpu['binding']))
        assert request['backend_claim_sha256']==cpu['backend_claim_sha256']
        assert request['writer_id']==drain['writer_id'] and request['registration_sha256']==drain['registration_sha256']
        from backend.engine.runtime_update import _hex
        assert _hex(request['challenge']) and _hex(request['request_id'],32) and _hex(request['closed_registry_sha256'])
        assert type(request['budget_ms']) is int and 0<request['budget_ms']<=4000


def _assert_damaged_stop(result, damage, cpu, stderr):
    """Retained original identities and exact invalid-owner traceback only."""
    assert damage in {'changed_nonce','identical_request_replay'}
    assert set(result)=={'main','backend','error','direct_exit','signal'}
    assert result['main']==cpu['main_process']['pid'] and result['backend']==cpu['backend_process']['pid']
    assert result['signal'] is None and type(result['direct_exit']) is int and result['direct_exit'] in {0,1}
    assert isinstance(result['error'],str) and result['error']
    assert result['error'].splitlines()[0]=='Error: Original owned backend channel/ownership ended'
    if result['direct_exit']==0:
        assert stderr==''  # Cooperative exit cannot hide a background exception.
    else:
        # The completed original preparation makes these current exact guards
        # authoritative. Durable bad-frame refusal must never restore ready.
        import re
        repository=Path(__file__).resolve().parents[2]
        service=str(repository/'backend/engine/application_launch_handshake.py')
        quiescence=str(repository/'backend/engine/application_launch_quiescence.py')
        lease=str(repository/'backend/engine/application_launch_lease.py')
        rows=re.findall(r'^  File "([^"\n]+)", line [1-9][0-9]*, in ([^\n]+)$',stderr,re.M)
        assert stderr.startswith('Traceback (most recent call last):\n')
        terminal=[line for line in stderr.splitlines() if re.match(r'^[A-Za-z_][A-Za-z_0-9.]*: ',line)]
        prepared_guard=[(cpu['backend_executable'],'<module>'),(service,'backend_execution_service'),
                        (service,'fresh'),(service,'_current'),(service,'_backend_shutdown_controls')]
        if rows==prepared_guard:
            assert damage in {'changed_nonce','identical_request_replay'}
            assert stderr.count('Traceback (most recent call last):')==1
            assert terminal==['backend.engine.application_launch_handshake.HandshakeError: Original shutdown launch/writer controls changed']
        else:
            assert damage=='identical_request_replay'
            assert rows==[(quiescence,'_launch'),(lease,'_load'),(lease,'_refuse'),
                          (cpu['backend_executable'],'<module>'),(service,'backend_execution_service'),
                          (quiescence,'inspect_epoch'),(quiescence,'_load'),(quiescence,'_launch')]
            assert stderr.count('Traceback (most recent call last):')==2
            assert terminal==['backend.engine.application_launch_lease.LaunchLeaseError: Application launch ownership requires recovery: ownership publication interrupted or changed',
                              'backend.engine.application_launch_quiescence.QuiescenceError: Owned writer epoch refuses foreign installation/database binding']
        assert stderr.splitlines()[-1]==terminal[-1]


def _assert_damaged_registry(registry, damage, refusal, cpu, registry_sha256):
    """Already finished typed CPU stays exact; damaged backend cannot exit."""
    assert registry['state']==('open' if damage=='changed_nonce' else 'closed')
    assert registry['binding']['nonce']==cpu['nonce']
    assert registry['binding']['launch_binding_sha256']==sha(canonical(cpu['binding']))
    writers=registry['writers'];assert len(writers)==2
    backend=next(row for row in writers if row['role']=='backend')
    assert backend['status']=='active' and backend['process']==cpu['backend_process'] and backend['exit_code'] is None
    assert backend['writer_id']==refusal['writer_drain']['writer_id']
    assert backend['registration_sha256']==refusal['writer_drain']['registration_sha256']
    finished=next(row for row in writers if row['role']=='owned_cpu_worker')
    assert finished['status']=='direct_exited' and finished['reason_code'] is None
    assert finished['exit_code']==0 and finished['process']['pid']==cpu['worker_pid']
    assert finished['process']!=backend['process']
    if damage=='identical_request_replay':
        assert refusal['writer_drain']['request']['closed_registry_sha256']==registry_sha256


@pytest.mark.parametrize('damage',['changed_nonce','identical_request_replay'])
def test_actual_controller_refuses_changed_and_replayed_original_main_drain_frames(tmp_path,monkeypatch,damage):
    """Owned real source channel; only the test-origin frame is intentionally damaged."""
    values=managed_stack(tmp_path,monkeypatch,damage=damage);root=values[0];projects=root/'projects'
    child,ack=_start_ready_cpu_stack(values,prefix=['-c',_source_cpu_settlement_fixture_prefix()])
    # The cleanup keeps this body's own failure; a cleanup error is added as a note instead of replacing it.
    with _managed_fixture_cleanup(child,root):
        _,cpu=_wait_receipt_and_cpu_settlement(root,ack['nonce'],child)
        assert cpu['semantic_output']['final_verdict']=='OK'
        controls={p:p.read_bytes() for p in [root/'application-active.json',root/'global-active.json']}
        (projects/'drain.trigger').touch()
        refusal=json.loads(wait_file(projects/'managed-controller-refusal.json',seconds=3))
        assert refusal['state']=='recovery_required'
        _assert_damaged_refusal(refusal,damage,cpu)
        assert refusal['writer_drain']['receipt'] is None and refusal['writer_drain']['backend_exit'] is None
        assert refusal['writer_drain']['phase']==('enrolled' if damage=='changed_nonce' else 'closing')
        assert not (projects/'managed-drain-received.json').exists()
        boundary_name='managed-nonce-proof-boundary.json' if damage=='changed_nonce' else 'managed-replay-proof-boundary.json'
        ordinary=json.loads(wait_ordinary_stop(projects,child,seconds=3,boundary_name=boundary_name))
        assert ordinary['pid']==cpu['backend_process']['pid']
        boundary=json.loads((projects/boundary_name).read_bytes())
        assert boundary['pid']==cpu['backend_process']['pid']
        assert type(boundary['original_deadline']) in (int,float) and math.isfinite(boundary['original_deadline'])
        (projects/'exit.trigger').touch()
        result=json.loads(wait_file(projects/'managed-stop-result.json',seconds=5))
        assert result['signal'] is None and result['error'] is not None
        stderr=(projects/'execution-backend-error.txt').read_text()
        _assert_damaged_stop(result,damage,cpu,stderr)
        finish_managed(child,root)
        from backend.engine.application_launch_quiescence import inspect_epoch
        snapshot=inspect_epoch(root,ack['nonce'])
        registry=snapshot['registry'];registry_sha256=snapshot['registry_sha256']
        _assert_damaged_registry(registry,damage,refusal,cpu,registry_sha256)
        assert {p:p.read_bytes() for p in controls}==controls
        from backend.engine.application_launch_lease import assert_quiescent
        with pytest.raises(ValueError):assert_quiescent(root)
        (projects/'managed-damaged-frame-proof.json').write_bytes(canonical({'damage':damage,'cpu':cpu,'refusal':refusal,
            'stop':result,'registry':snapshot,'backend_stderr':stderr,'private_fixture_channel_close_after_durable_refusal':True,'native':False,'whole_tree':False,'lease_release':False}))


# These controlled schedules retain the original nonblocking admission. They
# deliberately hold the actual post-send validator's pinned flock, never a mock.
def _cpu_gate_wait(projects,name,deadline):
    while time.monotonic()<deadline:
        if (projects/'cpu-gate-abort.json').exists():raise AssertionError('Owned contention gate aborted')
        if (projects/name).exists():return completion_boundary.read(projects,name)
        time.sleep(min(.005,max(0,deadline-time.monotonic())))
    raise AssertionError('Owned contention gate exceeded original deadline')


def _cpu_contention_backend(projects,boundary):
    import dis
    from backend.engine import application_launch_lease as lease
    metadata=completion_boundary.read(projects,completion_boundary.CONFIG)
    schedule=metadata['contention'];held=False
    lockcode=lease._transition_admission.__wrapped__.__code__
    def profile(frame,event,arg):
        nonlocal held
        boundary(frame,event,arg)
        action=boundary.action
        if frame is action and event=='call' and schedule=={'entry':'publish','mode':'busy'}:
            _cpu_gate_wait(projects,'cpu-gate-target.json',min(metadata['deadline'],boundary.deadline))
        if held or event!='return' or frame.f_code is not lockcode or frame.f_locals.get('locked') is not True:return
        ancestor=frame.f_back
        while ancestor is not None and ancestor is not action:ancestor=ancestor.f_back
        if ancestor is None:return
        instructions=list(dis.get_instructions(frame.f_code))
        index=next(i for i,item in enumerate(instructions) if item.offset==frame.f_lasti)
        assert instructions[index].opname=='YIELD_VALUE' or (instructions[index].opname=='RESUME' and instructions[index-1].opname=='YIELD_VALUE')
        held=True;info=os.fstat(frame.f_locals['fd'])
        completion_boundary.write(projects,'cpu-gate-held.json',{'device':info.st_dev,'inode':info.st_ino,
            'time':time.monotonic(),'deadline':min(metadata['deadline'],boundary.deadline)})
        expected='cpu-gate-busy.json' if schedule['mode']=='busy' else 'cpu-gate-verify.json'
        _cpu_gate_wait(projects,expected,min(metadata['deadline'],boundary.deadline))
    return profile


def _cpu_contention_execute(original,owner,capability):
    from backend.engine import application_launch_execution as execution,application_launch_lease as lease
    projects=owner.root/'projects';metadata=completion_boundary.read(projects,completion_boundary.CONFIG)
    schedule=metadata['contention'];previous=sys.getprofile();thread=threading.get_ident()
    target=execution.verify_completion.__code__ if schedule['entry']=='verify' else lease.LaunchSupervisor._publish_cpu_execution.__code__
    def profile(frame,event,arg):
        exact=event=='call' and threading.get_ident()==thread and frame.f_back is not None and frame.f_back.f_code is original.__code__
        if exact and frame.f_code is execution.verify_completion.__code__ and frame.f_locals.get('owner') is owner:
            completion_boundary.write(projects,'cpu-gate-verify.json',{'time':time.monotonic()})
        if exact and frame.f_code is target and frame.f_locals.get('owner',frame.f_locals.get('self')) is owner:
            completion_boundary.write(projects,'cpu-gate-target.json',{'time':time.monotonic()})
            if schedule['mode']=='busy':
                held=_cpu_gate_wait(projects,'cpu-gate-held.json',metadata['deadline'])
                info=(owner.root/lease.LEASES/owner.nonce/lease.TRANSITION_LOCK).stat()
                assert (info.st_dev,info.st_ino)==(held['device'],held['inode'])
            if schedule['mode']=='busy':
                completion_boundary.write(projects,'cpu-gate-enter.json',{'time':time.monotonic(),
                    'function':frame.f_code.co_name,'device':info.st_dev,'inode':info.st_ino})
            sys.setprofile(None)
        # Busy controls intentionally enter while held. Released controls use
        # the real readiness observer, then the unchanged target functions.
        if schedule['mode']=='released' and previous is not None:
            previous(frame,event,arg)
            if exact and frame.f_code is target:
                completion_boundary.write(projects,'cpu-gate-enter.json',{'time':time.monotonic(),'function':frame.f_code.co_name})
            if exact and frame.f_code is execution.verify_completion.__code__ and schedule['entry']=='publish':sys.setprofile(profile)
    sys.setprofile(profile)
    try:return original(owner,capability)
    except lease.LeaseTransitionBusy as exc:
        completion_boundary.write(projects,'cpu-gate-busy.json',{'type':type(exc).__name__,
            'cause_type':type(exc.__cause__).__name__,'cause_errno':getattr(exc.__cause__,'errno',None),'time':time.monotonic()})
        raise
    finally:sys.setprofile(previous)


def _cpu_contention_cleanup(root,child,primary):
    first=None
    for cleanup in (lambda:completion_boundary.write(root/'projects','cpu-gate-abort.json',{'released':True}),
                    lambda:stop_stack(child,root) if child is not None else None):
        try:cleanup()
        except BaseException as error:
            if primary is None:
                if first is None:first=error
            else:
                try:primary.add_note('Owned CPU gate cleanup also failed: '+type(error).__name__)
                except BaseException:pass
    if first is not None:raise first


@pytest.mark.parametrize('entry,mode',[('verify','busy'),('publish','busy'),('verify','released'),('publish','released')])
def test_source_cpu_completion_real_contention_and_readiness(tmp_path,monkeypatch,entry,mode):
    """Removing readiness breaks released success; weakening real flock breaks busy refusal."""
    import errno
    original_stack=managed_stack;roots=[];child=None
    def stack(*args,**kwargs):
        values=original_stack(*args,**kwargs);root=values[0];roots.append(root)
        metadata=completion_boundary.read(root/'projects',completion_boundary.CONFIG)
        _atomic_fixture_json(root/'projects'/completion_boundary.CONFIG,{**metadata,'contention':{'entry':entry,'mode':mode}})
        return values
    monkeypatch.setattr(sys.modules[__name__],'managed_stack',stack)
    try:
        if mode=='released':
            test_actual_controller_refuses_changed_and_replayed_original_main_drain_frames(tmp_path,monkeypatch,'identical_request_replay')
            projects=roots[0]/'projects';ready=completion_boundary.read(projects,completion_boundary.READY)
            entered=completion_boundary.read(projects,'cpu-gate-enter.json')
            assert ready['normal_return'] is True and ready['time']<=entered['time']<ready['deadline']
            assert not (projects/'cpu-gate-busy.json').exists()
            assert (projects/'source-cpu-controller-returned.json.complete').is_file()
        else:
            values=stack(tmp_path,monkeypatch,damage='identical_request_replay');root=values[0];projects=root/'projects'
            child,ack=_start_ready_cpu_stack(values,prefix=['-c',_source_cpu_settlement_fixture_prefix()])
            deadline=completion_boundary.read(projects,completion_boundary.CONFIG)['deadline']
            with pytest.raises(pytest.fail.Exception,match='CPU execution refused: Private bootstrap or execution refused: Lease transition is busy; retry after it completes'):
                wait_receipt(root,ack['nonce'],child,timeout=max(0,deadline-time.monotonic()))
            busy=completion_boundary.read(projects,'cpu-gate-busy.json');held=completion_boundary.read(projects,'cpu-gate-held.json')
            entered=completion_boundary.read(projects,'cpu-gate-enter.json')
            assert busy['type']=='LeaseTransitionBusy' and busy['cause_type']=='BlockingIOError'
            assert busy['cause_errno'] in {errno.EAGAIN,errno.EWOULDBLOCK}
            assert entered['function']==('verify_completion' if entry=='verify' else '_publish_cpu_execution')
            assert held['time']<=entered['time']<=busy['time']<held['deadline']
            assert (held['device'],held['inode'])==(entered['device'],entered['inode'])
            row=json.loads((root/'.application-launches'/ack['nonce']/'journal.json').read_bytes())
            assert row['state']=='recovery_required' and row['cpu_execution']['receipt_sha256'] is None
            assert row['reason']=='Private bootstrap or execution refused: Lease transition is busy; retry after it completes'
            assert not (root/'.application-launches'/ack['nonce']/'cpu-execution-receipt.json').exists()
            assert not (projects/'drain.trigger').exists() and not (projects/'source-cpu-controller-returned.json.complete').exists()
    finally:
        if roots:_cpu_contention_cleanup(roots[0],child,sys.exc_info()[1])


@pytest.mark.parametrize('negative',['code','thread','cache','queue','request','token','pre_send','phase','unsent','expired','nonfinite','root','nonce'])
def test_source_cpu_completion_selector_refuses_foreign_context(negative):
    code=object();cache={};queue=object();request={'nonce':'original'}
    values=dict(actual_code=code,code=code,actual_thread=1,thread=1,actual_cache=cache,cache=cache,
        actual_queue=queue,queue=queue,request=request,retained_request=request.copy(),token='a',retained_token='a',
        completion_sent=True,phase='waiting_publication',deadline=11.,now=10.,actual_callsite=20,callsite=20,
        actual_root=Path('/owned'),root=Path('/owned'),nonce='original')
    assert completion_boundary.selected(**values)
    changes={'code':('actual_code',object()),'thread':('actual_thread',2),'cache':('actual_cache',{}),
        'queue':('actual_queue',object()),'request':('request',{'nonce':'foreign'}),'token':('token','b'),
        'pre_send':('actual_callsite',19),'phase':('phase','active'),'unsent':('completion_sent',False),
        'expired':('now',11.),'nonfinite':('deadline',float('inf')),'root':('actual_root',Path('/foreign')),'nonce':('nonce','foreign')}
    key,value=changes[negative];values[key]=value
    assert not completion_boundary.selected(**values)


def test_source_cpu_completion_normal_return_excludes_original_exception():
    events=[]
    def normal():return {'proof':'synthetic'}
    def error():raise ValueError('Original fixture error')
    def profile(frame,event,arg):
        if frame.f_code in {normal.__code__,error.__code__} and event=='return':events.append(completion_boundary.normal_return(frame,event))
    previous=sys.getprofile()
    try:
        sys.setprofile(profile);assert normal()=={'proof':'synthetic'}
        with pytest.raises(ValueError,match='Original fixture error') as caught:error()
    finally:sys.setprofile(previous)
    assert events==[True,False]
    assert caught.value.__traceback__.tb_next.tb_frame.f_code is error.__code__


@pytest.mark.parametrize('negative',['expired_parent','expired_action','foreign_token','foreign_request','foreign_root','foreign_nonce','exceptional','nonfinite'])
def test_source_cpu_completion_ready_marker_cannot_grant_authority(tmp_path,negative):
    projects=tmp_path/'projects';projects.mkdir();now=time.monotonic()
    metadata={'root':str(tmp_path),'token':'synthetic','deadline':now+1};request={'nonce':'original'}
    pending={'root':str(tmp_path),'token':'synthetic','nonce':'original',
        'request_sha256':completion_boundary.request_hash(request),'deadline':now+.5}
    ready={**pending,'normal_return':True,'time':now}
    if negative=='expired_parent':metadata['deadline']=now-1
    elif negative=='expired_action':pending['deadline']=ready['deadline']=now-1
    elif negative=='foreign_token':ready['token']='foreign'
    elif negative=='foreign_request':ready['request_sha256']='0'*64
    elif negative=='foreign_root':ready['root']=str(tmp_path/'foreign')
    elif negative=='foreign_nonce':ready['nonce']='foreign'
    elif negative=='exceptional':ready['normal_return']=False
    else:ready['deadline']=float('inf')
    completion_boundary.write(projects,completion_boundary.PENDING,pending)
    completion_boundary.write(projects,completion_boundary.READY,ready)
    with pytest.raises(AssertionError):completion_boundary.wait_ready(projects,metadata,request)


def test_source_cpu_completion_gate_cleanup_releases_without_masking_primary(tmp_path):
    completion_boundary.write(tmp_path,'cpu-gate-abort.json',{'released':True})
    with pytest.raises(AssertionError,match='Owned contention gate aborted'):
        _cpu_gate_wait(tmp_path,'missing.json',time.monotonic()+1)


@pytest.mark.parametrize('with_primary',[False,True])
def test_source_cpu_completion_cleanup_keeps_original_failure_and_releases_gate(tmp_path,monkeypatch,with_primary):
    projects=tmp_path/'projects';projects.mkdir();secondary=ValueError('cleanup');primary=RuntimeError('original') if with_primary else None
    def failed_stop(*args):raise secondary
    monkeypatch.setitem(globals(),'stop_stack',failed_stop)
    if with_primary:
        _cpu_contention_cleanup(tmp_path,object(),primary)
        assert primary.__notes__==['Owned CPU gate cleanup also failed: ValueError']
    else:
        with pytest.raises(ValueError) as caught:_cpu_contention_cleanup(tmp_path,object(),None)
        assert caught.value is secondary
    assert completion_boundary.read(projects,'cpu-gate-abort.json')=={'released':True}
    with pytest.raises(AssertionError,match='Owned contention gate aborted'):
        _cpu_gate_wait(projects,'absent.json',time.monotonic()+1)


def test_source_cpu_completion_foreign_controller_preserves_original_profile_and_error(tmp_path):
    from types import SimpleNamespace
    from backend.engine import application_launch_controller as controller,application_launch_execution as execution
    projects=tmp_path/'projects';projects.mkdir()
    completion_boundary.write(projects,completion_boundary.CONFIG,{'root':str(tmp_path),'token':'synthetic','deadline':time.monotonic()+1})
    owner=SimpleNamespace(root=tmp_path,nonce='original');foreign=SimpleNamespace(root=tmp_path,nonce='original')
    events=[]
    def previous(frame,event,arg):
        if frame.f_code is execution.verify_completion.__code__ and event in {'call','return'}:events.append(event)
    saved=sys.getprofile()
    try:
        sys.setprofile(previous)
        with completion_boundary.controller_boundary(owner,controller.execute_cpu.__code__):
            with pytest.raises(execution.ExecutionError,match='CPU completion differs from original request') as caught:
                execution.verify_completion(foreign,{}, {})
        assert sys.getprofile() is previous
    finally:sys.setprofile(saved)
    assert events==['call','return']
    assert caught.value.__traceback__.tb_next.tb_frame.f_code is execution.verify_completion.__code__
    assert not (projects/completion_boundary.READY).exists()


def test_source_cpu_completion_failure_diagnostics_contain_only_bounded_code_and_hashes(tmp_path):
    completion_boundary.write(tmp_path,completion_boundary.CONFIG,{'token':'private synthetic token'})
    try:raise ValueError('private bootstrap or proof must never be copied')
    except ValueError as error:_record_cpu_fixture_failure(tmp_path,error)
    raw=(tmp_path/'source-cpu-fixture-error.json').read_bytes();value=json.loads(raw)
    assert b'private' not in raw and len(raw)<4096
    assert value['exception']=='ValueError' and value['phase']=='completion_before_controller_return'
    assert len(value['frames'])==1 and value['frames'][0]['function'].startswith('test_source_cpu_completion_failure_diagnostics_')
    assert len(value['frames'][0]['function'])<=80 and type(value['frames'][0]['line']) is int
    assert value['markers'][completion_boundary.CONFIG]['sha256']==sha((tmp_path/completion_boundary.CONFIG).read_bytes())
    assert value['markers'][completion_boundary.READY]=={'exists':False}


def test_source_cpu_completion_expired_observer_restores_only_its_previous_profile(tmp_path):
    from types import SimpleNamespace
    from backend.engine import application_launch_execution as execution
    projects=tmp_path/'projects';projects.mkdir();request={'nonce':'original'}
    completion_boundary.write(projects,completion_boundary.CONFIG,{'root':str(tmp_path),'token':'synthetic','deadline':time.monotonic()-1})
    owner=SimpleNamespace(root=tmp_path,nonce='original')
    def original(owner,capability):return execution.verify_completion(owner,{'request':request},{'request':request})
    def previous(frame,event,arg):pass
    saved=sys.getprofile()
    try:
        sys.setprofile(previous)
        with pytest.raises(AssertionError,match='SOURCE completion readiness exceeded its original deadline'):
            with completion_boundary.controller_boundary(owner,original.__code__):original(owner,None)
        assert sys.getprofile() is previous
    finally:sys.setprofile(saved)
    assert not (projects/completion_boundary.READY).exists()


def test_source_cpu_completion_gate_cleanup_attempts_stop_after_marker_failure(tmp_path,monkeypatch):
    first=OSError('marker failure');stopped=[];child=object()
    def failed_write(*args,**kwargs):raise first
    def stop(actual_child,root):stopped.append((actual_child,root))
    monkeypatch.setattr(completion_boundary,'write',failed_write)
    monkeypatch.setitem(globals(),'stop_stack',stop)
    with pytest.raises(OSError) as caught:_cpu_contention_cleanup(tmp_path,child,None)
    assert caught.value is first
    assert stopped==[(child,tmp_path)]
