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
    cpu_stack, start_cpu_stack, wait_receipt, stop_stack, controlled_canary_publication,
)
from backend.tests.test_service_s6_04 import canonical, sha


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Owned bridge uses POSIX descriptors/lifetime locks')


def test_real_controller_enrolls_backend_before_actual_cpu_and_retains_blocking_row(tmp_path):
    """The existing source controller lacked a writer epoch despite real CPU success."""
    values = cpu_stack(tmp_path)
    root = values[0]
    child, ack = start_cpu_stack(values, prefix=['-c', _source_cpu_settlement_fixture_prefix()])
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
    scope = {'type': 'http', 'path': '/api/errors', 'method': 'GET'}
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
        path = '/api/errors' if case == 'accepted_202' else '/api/training/start' if case == 'spawn_route' else '/unreviewed-writer'
        assert state.enter({'type': 'http', 'method': 'POST', 'path': path})
        if case == 'accepted_202': state.response(202)
        state.leave()
    state.close()
    first = state.drain(.1)
    assert first['status'] == 'refused' and first['unsupported']
    assert state.drain(.1) == first


def test_busy_scope_refuses_with_original_budget_and_never_reopens():
    state = admission()
    assert state.enter({'type': 'http', 'method': 'GET', 'path': '/api/errors'})
    state.close()
    start = time.monotonic()
    outcome = state.drain(.03)
    assert time.monotonic() - start < .3
    assert outcome['status'] == 'refused' and outcome['active_scopes'] == 1
    state.leave()
    assert not state.enter({'type': 'http', 'method': 'GET', 'path': '/api/errors'})


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
    backend = ('#!'+sys.executable+'\n'+fixtures._fixture_json_publisher_source()+f'''
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
 def observed_leave():
  original_leave()
  if state.snapshot()['active_scopes']==0:
   (projects/'managed-http-idle.json').write_text(json.dumps({{'pid':os.getpid(),**state.snapshot()}}))
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
 @app.get('/api/errors')
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
launch.frames.send=async(frame)=>{{if(frame.kind==='managed_drain_proof')fs.writeFileSync(path.join(launch.projects,'managed-forwarded-drain.json'),Buffer.from(frame.proof_b64,'base64'));
if(!damage||frame.kind!=='main_drain_request')return originalSend(frame);
if(damage==='changed_nonce')return originalSend({{...frame,nonce:'0'.repeat(32)}});
await originalSend(frame);await originalSend(frame);}};
const prepare=launch.prepareBackendDrain.bind(launch);launch.prepareBackendDrain=async(...args)=>{{const receipt=await prepare(...args);fs.writeFileSync(path.join(launch.projects,'managed-drain-received.json'),JSON.stringify(receipt));return receipt;}};
fs.writeFileSync(path.join(launch.projects,'execution-stack-ready.json'),JSON.stringify({{main:process.pid,backend:backend.pid}}));
const refusal=damage?setInterval(()=>{{try{{const row=JSON.parse(fs.readFileSync(path.join(launch.root,'.application-launches',launch.nonce,'journal.json')));
if(row.state==='recovery_required'){{fs.writeFileSync(path.join(launch.projects,'managed-controller-refusal.json'),JSON.stringify(row));launch.refuse();clearInterval(refusal);}}}}catch(e){{}}}},10):null;
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
    return fixtures.cpu_stack(tmp_path)


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
    return None


def finish_managed(child, root):
    # Cooperative only for this test-owned backend/main; original controller
    # Popen may be stopped only after its authenticated original main is reaped.
    if child.returncode is not None:return
    projects=root/'projects';(projects/'drain.trigger').touch()
    # An already-authenticated original main exit cannot publish a later
    # ordinary-stop file. Observe that fact only inside the original first5s.
    from backend.engine.application_launch_lease import LeaseTransitionBusy
    first_deadline=time.monotonic()+5
    while not (projects/'ordinary-stop-requested.json').is_file():
        if time.monotonic()>=first_deadline:
            pytest.fail('Missing controlled original fixture output: '+str(projects/'ordinary-stop-requested.json'))
        try:row=_fixture_readonly_managed_launch(root)
        except LeaseTransitionBusy:
            time.sleep(max(0,min(.05,first_deadline-time.monotonic())));continue
        if row is not None and _fixture_terminal_main_exited(child,row,first_deadline):
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


@pytest.mark.parametrize('uncovered',[False,True])
def test_actual_source_cpu_then_managed_drain_and_original_node_exit_keeps_lease_blocked(tmp_path,monkeypatch,uncovered):
    import urllib.request,urllib.error
    values=managed_stack(tmp_path,monkeypatch,uncovered=uncovered);root=values[0];projects=root/'projects'
    child,ack=start_cpu_stack(values)
    try:
        _,cpu=wait_receipt(root,ack['nonce'],child)
        assert cpu['semantic_output']['final_verdict']=='OK'
        server=json.loads(wait_file(projects/'managed-server.json'))
        fd=json.loads(wait_file(projects/'original-writer-fd.json'))
        environment=json.loads(wait_file(projects/'managed-private-environment.json'))
        assert environment['HOME']==str(root/'.application-runtime-homes'/ack['nonce'])
        assert fd['pid']==cpu['backend_process']['pid']==server['pid']
        with urllib.request.urlopen(f"http://127.0.0.1:{server['port']}/api/errors",timeout=1) as response:
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
            urllib.request.urlopen(f"http://127.0.0.1:{server['port']}/api/errors",timeout=1)
        assert error.value.code==409
        import fcntl
        lock=root/'.application-writer-epochs'/ack['nonce']/'writers'/registered['writer_id']/'ownership.lock'
        probe=os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(probe)
        ordinary=json.loads(wait_file(projects/'ordinary-stop-requested.json',seconds=3))
        assert ordinary=={'pid':server['pid'],'requested':True}
        (projects/'exit.trigger').touch()
        result=json.loads(wait_file(projects/'managed-stop-result.json',seconds=5))
        assert result['direct_exit']==0 and result['signal'] is None, _managed_stop_diagnostic(projects, result)
        assert (result['error'] is not None)==uncovered
        finish_managed(child,root)  # Original direct-main observation settles publication.
        snapshot=inspect_epoch(root,ack['nonce']);assert snapshot['registry']['state']=='closed'
        backend=next(r for r in snapshot['registry']['writers'] if r['role']=='backend')
        assert backend['status']==('active' if uncovered else 'direct_exited')
        assert backend['exit_code']==(None if uncovered else 0)
        assert backend['process']==cpu['backend_process']
        assert backend['lock_identity']=={'device':fd['device'],'inode':fd['inode']}
        assert {p:p.read_bytes() for p in controls}==controls
        from backend.engine.application_launch_lease import _load,assert_quiescent
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            row=_load(root)
            if row['state']=='recovery_required':break
            time.sleep(.01)
        assert row['state']=='recovery_required'
        drain=row['writer_drain']
        assert drain['phase']==('refused' if uncovered else 'backend_exited')
        assert drain['receipt']['whole_writer_coverage'] is False
        assert drain['receipt']['can_release_launch_lease'] is False
        assert (drain['backend_exit'] is None)==uncovered
        with pytest.raises(ValueError):assert_quiescent(root)
        (projects/'managed-drain-proof.json').write_bytes(canonical({'scope':'controlled_source_backend_without_normal_startup_services',
            'cpu':cpu,'writer':snapshot,'drain':drain,'stop':result,'native':False,'whole_tree':False,'lease_release':False}))
    finally:finish_managed(child,root)


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
    descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'wb') as output:output.write(raw)


def _source_cpu_settlement_fixture_prefix():
    """Original controller executes once; only its returned typed state is noted."""
    return '''from backend.engine import application_launch_controller as controller
from backend.tests import test_application_launch_quiescence_bridge as fixture
original_execute_cpu=controller.execute_cpu
def execute_cpu(owner, capability):
    receipt=original_execute_cpu(owner, capability)
    fixture._record_returned_cpu_settlement(owner, receipt)
    return receipt
controller.execute_cpu=execute_cpu
raise SystemExit(controller.main())
'''


def _wait_returned_cpu_settlement(root, nonce, child, cpu, deadline):
    """Bounded read-only fixture barrier; observations cannot become authority."""
    import stat
    from backend.engine.application_launch_lease import inspect_launch, _identity, LeaseTransitionBusy
    target=root/'projects'/'source-cpu-controller-returned.json'
    witness=getattr(child,'_original_cpu_fixture_supervisor',None)
    assert type(deadline) in (int,float) and math.isfinite(deadline)
    assert getattr(child,'_original_cpu_fixture_nonce',None)==nonce and witness is not None
    assert child.pid==witness['pid']
    while time.monotonic()<deadline:
        assert child.poll() is None, 'Original controller died before CPU settlement observation'
        assert _identity(child.pid)==witness and cpu['nonce']==nonce
        assert time.monotonic()<deadline
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
    remaining=deadline-time.monotonic();assert remaining>0
    target,cpu=wait_receipt(root,nonce,child,timeout=remaining)
    assert time.monotonic()<deadline
    _wait_returned_cpu_settlement(root,nonce,child,cpu,deadline)
    return target,cpu


def _assert_damaged_refusal(refusal, damage, cpu):
    """Exact captured protocol rejection, never a generic failure allowance."""
    assert damage in {'changed_nonce','identical_request_replay'}
    cause = ('Original main managed drain request differs or replayed' if damage=='changed_nonce'
             else 'Original controller reply kind differs')
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
        # After the exact durable bad-frame refusal, original _validate must
        # reject recovery_required ownership. It must not be relaxed to ready.
        import re
        service=str(Path(__file__).resolve().parents[2]/'backend/engine/application_launch_handshake.py')
        rows=re.findall(r'^  File "([^"\n]+)", line [1-9][0-9]*, in ([^\n]+)$',stderr,re.M)
        stdlib=str(Path(contextmanager.__code__.co_filename).resolve())
        assert rows==[(cpu['backend_executable'],'<module>'),(service,'backend_execution_service'),
                      (service,'_validate_service_action'),(service,'_validate'),(stdlib,'__enter__'),
                      (service,'_validated_backend_admission'),(service,'original_owner')]
        assert stderr.startswith('Traceback (most recent call last):\n')
        assert stderr.splitlines()[-1]=='backend.engine.application_launch_handshake.HandshakeError: Backend challenge has no current spawned main owner'
        assert stderr.count('Traceback (most recent call last):')==1
        terminal=[line for line in stderr.splitlines() if re.match(r'^[A-Za-z_][A-Za-z_0-9.]*: ',line)]
        assert terminal==[stderr.splitlines()[-1]]


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
    child,ack=start_cpu_stack(values,prefix=['-c',_source_cpu_settlement_fixture_prefix()])
    try:
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
        ordinary=json.loads(wait_file(projects/'ordinary-stop-requested.json',seconds=3))
        assert ordinary['pid']==cpu['backend_process']['pid']
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
    finally:finish_managed(child,root)
