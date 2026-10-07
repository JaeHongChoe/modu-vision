"""Descriptor/controller fixtures prove software binding, never native inference."""
import hashlib
import base64
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import zipfile
import shutil
import select

import psutil
import pytest

from backend.tests.test_application_launch_lease import installed, reserve, stop_fixture
from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture, plan, canonical, sha


def controller_arguments(root, value, current):
    return ['--root', str(root), '--authority', str(value['authority']),
        '--pinned-authority-sha256', value['pinned_authority_sha256'],
        '--expected-installation-id', json.loads((root/'.global-migration-owner.json').read_bytes())['installation_id'],
        '--expected-update-id', current['update_id'],
        '--expected-database-fence', str(current['database_pointer']['fence'])]


def application_fixture(tmp_path, application):
    from backend.engine.runtime_update import install_update
    root, *_ = owned(tmp_path); value = fixture(tmp_path)
    executable = ('#!'+sys.executable+'\n'+application).encode()
    archive = value['directory']/'application.zip'
    manifest = {'schema_version': 1, 'version': '1.0.0', 'platform': value['target']['platform'],
        'arch': value['target']['arch'], 'entrypoint': 'bin/app',
        'files': [{'path': 'bin/app', 'size': len(executable), 'sha256': sha(executable), 'executable': True}]}
    with zipfile.ZipFile(archive, 'w') as writer:
        writer.writestr('portable-application.json', canonical(manifest)); writer.writestr('bin/app', executable)
    value['payload'].update(sha256=sha(archive.read_bytes()), size=archive.stat().st_size)
    value['payload']['artifacts'][0].update(sha256=value['payload']['sha256'], size=value['payload']['size'])
    value['sign'](value['payload']); current = install_update(root, plan(root, value))
    return root, value, current


def test_controller_inspection_is_bounded_absent_and_never_reserves(tmp_path):
    from backend.engine import application_launch_lease as lease
    root, value, current = installed(tmp_path)
    process = subprocess.run([sys.executable, '-m', 'backend.engine.application_launch_controller',
        '--inspect', *controller_arguments(root, value, current)],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result['status'] == 'absent' and result['nonce'] is None
    assert result['bootstrap_binding_verified'] is False and result['readiness'] == 'unverified'
    assert result['native_app_handshake_verified'] is False and result['backend_handshake_verified'] is False
    assert result['actual_application_inference_verified'] is False and result['release_ready'] is False
    assert not (root/lease.ACTIVE_LEASE).exists() and not (root/lease.LEASES).exists()


@pytest.mark.parametrize('field', ['--expected-installation-id', '--expected-update-id', '--expected-database-fence'])
def test_wrong_expected_pair_refuses_before_reservation_or_spawn(tmp_path, field):
    from backend.engine import application_launch_lease as lease
    root, value, current = installed(tmp_path); args = controller_arguments(root, value, current)
    args[args.index(field)+1] = '999' if field.endswith('fence') else 'f'*32
    process = subprocess.run([sys.executable, '-m', 'backend.engine.application_launch_controller', *args],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=15)
    assert process.returncode == 2, process.stderr
    result = json.loads(process.stdout)
    assert set(result) == {'schema_version', 'status', 'error'} and result['status'] == 'refused'
    assert len(result['error']) <= 2048
    assert not (root/lease.ACTIVE_LEASE).exists() and not (root/lease.LEASES).exists()


def test_lease_bootstrap_uses_only_an_internal_anonymous_stream_descriptor(tmp_path):
    from backend.engine import application_launch_lease as lease
    code = '''import os,socket,stat,time,json
from pathlib import Path
fd=int(os.environ['VISION_APPLICATION_LAUNCH_FD'])
channel=socket.socket(fileno=fd)
row={'socket':stat.S_ISSOCK(os.fstat(fd).st_mode),'anonymous':channel.getsockname()=='','nonce':os.environ['VISION_APPLICATION_LAUNCH_NONCE']}
Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR'],'projects','descriptor-fixture.json').write_text(json.dumps(row))
while True:time.sleep(.05)
'''
    root, value, _ = application_fixture(tmp_path, code); owner = reserve(root, value)
    try:
        row = owner.start(bootstrap=True)
        assert owner._bootstrap_channel is not None and owner._bootstrap_channel.get_inheritable() is False
        marker = root/'projects'/'descriptor-fixture.json'; deadline = time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)
        observed = json.loads(marker.read_bytes())
        assert observed == {'socket': True, 'anonymous': True, 'nonce': row['nonce']}
        assert lease.inspect_launch(root)['state'] == 'starting'
        assert row['native_app_handshake_verified'] is False
    finally: stop_fixture(owner)


def test_original_controller_rejects_arbitrary_bootstrap_descriptor_parameters(tmp_path):
    root, value, _ = installed(tmp_path); owner = reserve(root, value)
    with pytest.raises((TypeError, ValueError)): owner.start(bootstrap=17)
    assert owner._process is None
    owner.cancel()


def stack_fixture(tmp_path, *, malicious=False):
    """Signed inert Node/Python children; no Electron, uvicorn, inference or keys."""
    from backend.engine.runtime_update import install_update
    repository = Path(__file__).resolve().parents[2]
    compiler = "const fs=require('fs'),ts=require('typescript');process.stdout.write(ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText)"
    compiled = subprocess.check_output(['node', '-e', compiler, str(repository/'src/main/applicationLaunch.ts')])
    root, *_ = owned(tmp_path); value = fixture(tmp_path)
    backend = ('#!'+sys.executable+'\nimport sys,time\nsys.path.insert(0,'+repr(str(repository))+')\n'
        'from backend.engine.application_launch_handshake import early_backend_bootstrap,backend_bootstrap_ready\n'
        'from backend.engine.migration_guard import maintenance_guard\nimport os\n'
        'early_backend_bootstrap()\ntime.sleep(.8)\n'
        "with maintenance_guard(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']):\n backend_bootstrap_ready()\n while True:time.sleep(.05)\n").encode()
    if malicious:
        main = "const net=require('net');const socket=new net.Socket({fd:Number(process.env.VISION_APPLICATION_LAUNCH_FD),readable:true,writable:true});socket.once('data',()=>socket.write('{\"schema_version\":1,\"schema_version\":1}\\n'));setInterval(()=>{},100);"
    else:
        main = f"""const fs=require('fs'),path=require('path'),{{spawn}}=require('child_process');
const {{authenticateMainLaunch}}=require('../bridge.cjs');
(async()=>{{const launch=await authenticateMainLaunch();
const executable=path.join(path.dirname(process.argv[1]),'backend_fixture.py');
const backend=spawn({json.dumps(sys.executable)},[executable,'--project-dir',launch.projects,'--shared-auth-dir',launch.auth],{{cwd:launch.root,env:launch.backendEnvironment({{PATH:'/usr/bin:/bin'}}),stdio:['pipe','ignore','ignore','pipe']}});
await launch.bindBackend(backend,executable,null);
fs.writeFileSync(path.join(launch.projects,'controlled-stack-ready.json'),JSON.stringify({{main:process.pid,backend:backend.pid}}));
setInterval(()=>{{}},100);
}})().catch(error=>{{fs.writeFileSync(path.join(process.env.VISION_AI_STUDIO_USER_DATA_DIR,'projects','controlled-stack-error.txt'),String(error.stack));process.exit(3);}});"""
    files = {'bin/app': ('#!'+shutil.which('node')+'\n'+main).encode(), 'bin/backend_fixture.py': backend, 'bridge.cjs': compiled}
    manifest = {'schema_version':1,'version':'1.0.0','platform':value['target']['platform'],'arch':value['target']['arch'],
        'entrypoint':'bin/app','files':[{'path':name,'size':len(raw),'sha256':sha(raw),'executable':name.startswith('bin/')} for name,raw in files.items()]}
    archive=value['directory']/'application.zip'
    with zipfile.ZipFile(archive,'w') as writer:
        writer.writestr('portable-application.json',canonical(manifest))
        for name,raw in files.items():writer.writestr(name,raw)
    value['payload'].update(sha256=sha(archive.read_bytes()),size=archive.stat().st_size)
    value['payload']['artifacts'][0].update(sha256=value['payload']['sha256'],size=value['payload']['size'])
    value['sign'](value['payload']); current=install_update(root,plan(root,value))
    return root,value,current


def controller_fixture(root,value,current,*,prefix=None):
    child=subprocess.Popen([sys.executable,*(prefix or ['-m','backend.engine.application_launch_controller']),*controller_arguments(root,value,current)],
        cwd=Path(__file__).resolve().parents[2],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    assert select.select([child.stdout],[],[],10)[0], 'No bounded controller acknowledgement'
    raw=child.stdout.readline(65537)
    assert raw.endswith(b'\n') and len(raw)<=65536
    acknowledgement=json.loads(raw)
    child.stdout.close();child.stderr.close()  # The updater intentionally disconnects.
    return child,acknowledgement


def stop_controlled_stack(child,root):
    from backend.engine.application_launch_lease import inspect_launch,_identity
    row=inspect_launch(root); process=row.get('process')
    if process is not None:
        try:
            assert _identity(process['pid'])==process and psutil.Process(process['pid']).ppid()==child.pid
            assert os.getpgid(process['pid'])==process['pid']
            os.killpg(process['pid'],signal.SIGTERM)  # Only the fixture's proven private group.
        except psutil.NoSuchProcess:pass
    if child.poll() is None:child.terminate()
    child.wait(timeout=10)


def wait_lifecycle(root,value,current,status,timeout=15):
    deadline=time.monotonic()+timeout; result=None
    while time.monotonic()<deadline:
        read=subprocess.run([sys.executable,'-m','backend.engine.application_launch_controller','--inspect',*controller_arguments(root,value,current)],
            cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=10)
        result=json.loads(read.stdout)
        if result.get('status')==status:return result
        time.sleep(.05)
    pytest.fail('Expected '+status+'; actual '+repr(result))


def test_persistent_real_descriptor_stack_survives_updater_disconnect_and_never_qualifies_inference(tmp_path):
    from backend.engine.application_launch_lease import assert_quiescent
    root,value,current=stack_fixture(tmp_path);child,ack=controller_fixture(root,value,current)
    try:
        assert ack['status']=='starting' and ack['bootstrap_binding_verified'] is False
        ready=wait_lifecycle(root,value,current,'ready')
        assert ready['bootstrap_binding_verified'] is True and ready['readiness']=='authenticated_controller_binding_only'
        assert child.poll() is None
        assert all(ready[key] is False for key in ('native_app_handshake_verified','backend_handshake_verified','actual_application_inference_verified','release_ready'))
        assert set(ready)=={'schema_version','status','nonce','installation_id','update_id','database_fence','bootstrap_binding_verified','readiness','native_app_handshake_verified','backend_handshake_verified','actual_application_inference_verified','release_ready'}
        with pytest.raises(ValueError):assert_quiescent(root)
        marker=json.loads((root/'projects'/'controlled-stack-ready.json').read_bytes())
        backend=psutil.Process(marker['backend']); birth=backend.create_time()
        assert backend.ppid()==marker['main'] and psutil.Process(marker['main']).ppid()==child.pid
        backend.terminate()  # Proven fixture handle, never a guessed process.
        observed=wait_lifecycle(root,value,current,'recovery_required')
        assert observed['bootstrap_binding_verified'] is False and child.poll() is None
        with pytest.raises(ValueError):assert_quiescent(root)
    finally:stop_controlled_stack(child,root)


def test_duplicate_private_claim_stays_durably_recovery_required_after_pipe_loss(tmp_path):
    from backend.engine.application_launch_lease import assert_quiescent
    root,value,current=stack_fixture(tmp_path,malicious=True);child,ack=controller_fixture(root,value,current)
    try:
        assert ack['status']=='starting'
        observed=wait_lifecycle(root,value,current,'recovery_required')
        assert observed['bootstrap_binding_verified'] is False and child.poll() is None
        with pytest.raises(ValueError):assert_quiescent(root)
    finally:stop_controlled_stack(child,root)


def test_live_controller_keeps_original_handles_when_recovery_transition_mutex_is_busy(tmp_path):
    from backend.engine.application_launch_lease import _transition_admission
    root,value,current=stack_fixture(tmp_path);child,ack=controller_fixture(root,value,current)
    try:
        wait_lifecycle(root,value,current,'ready')
        marker=json.loads((root/'projects'/'controlled-stack-ready.json').read_bytes())
        backend=psutil.Process(marker['backend']);assert backend.ppid()==marker['main']
        with _transition_admission(root,ack['nonce']):
            backend.terminate()
            time.sleep(.8)  # Multiple real nonblocking recovery collisions.
            assert child.poll() is None
        recovery=wait_lifecycle(root,value,current,'recovery_required')
        assert recovery['bootstrap_binding_verified'] is False and child.poll() is None
    finally:stop_controlled_stack(child,root)


def test_bootstrap_sidecar_interruption_is_not_repaired_and_controller_remains_persistent(tmp_path):
    from backend.engine.application_launch_lease import ACTIVE_LEASE,LEASES,assert_quiescent
    root,value,current=stack_fixture(tmp_path)
    script="from backend.engine import application_launch_controller as c,application_launch_lease as l; import sys; l._checkpoint=lambda point: (_ for _ in ()).throw(OSError('controlled publication fault')) if point=='after_bootstrap_receipt' else None; raise SystemExit(c.main(sys.argv[1:]))"
    child,ack=controller_fixture(root,value,current,prefix=['-c',script])
    try:
        sidecar=root/LEASES/ack['nonce']/'bootstrap-receipt.json';deadline=time.monotonic()+10
        while not sidecar.exists() and time.monotonic()<deadline:time.sleep(.02)
        assert sidecar.exists();original=sidecar.read_bytes();time.sleep(.6)
        assert child.poll() is None and sidecar.read_bytes()==original
        read=subprocess.run([sys.executable,'-m','backend.engine.application_launch_controller','--inspect',*controller_arguments(root,value,current)],cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=10)
        assert read.returncode==2 and json.loads(read.stdout)['status']=='refused'
        with pytest.raises(ValueError):assert_quiescent(root)
    finally:
        # An interrupted journal cannot be inspected; the original pointer
        # still binds our own spawned fixture handle for scoped cleanup.
        journal=json.loads((root/LEASES/ack['nonce']/'journal.json').read_bytes()); process=journal['process']
        from backend.engine.application_launch_lease import _identity
        if process is not None:
            try:
                assert _identity(process['pid'])==process and psutil.Process(process['pid']).ppid()==child.pid
                assert os.getpgid(process['pid'])==process['pid'];os.killpg(process['pid'],signal.SIGTERM)
            except psutil.NoSuchProcess:pass
        child.terminate();child.wait(timeout=10)


def test_controller_birth_loss_never_unlocks_a_live_bound_application(tmp_path):
    from backend.engine.application_launch_lease import assert_quiescent,inspect_launch,_identity
    root,value,current=stack_fixture(tmp_path);child,ack=controller_fixture(root,value,current)
    try:
        ready=wait_lifecycle(root,value,current,'ready'); row=inspect_launch(root);main=row['process']; assert _identity(main['pid'])==main
        child.terminate();child.wait(timeout=10)
        observed=wait_lifecycle(root,value,current,'recovery_required')
        assert observed['bootstrap_binding_verified'] is False
        with pytest.raises(ValueError):assert_quiescent(root)
    finally:
        # These processes originated from this test's exact signed fixture.
        try:
            row=inspect_launch(root);main=row['process'];assert _identity(main['pid'])==main
            assert os.getpgid(main['pid'])==main['pid'];os.killpg(main['pid'],signal.SIGTERM)
        except psutil.NoSuchProcess:pass
        if child.poll() is None:child.terminate();child.wait(timeout=10)


@pytest.mark.parametrize('changes', [{'process':None},{'process':{}},{'process':{'pid':True,'created_at':1.0,'command_sha256':'a'*64}},
    {'frozen':1},{'executable':[]},{'build_identity_sha256':False},{'nonce':'foreign'},{'kind':[]},{'kind':{}}])
def test_nested_backend_proof_shapes_refuse_before_indexing_or_process_observation(changes):
    from backend.engine.application_launch_controller import _proof
    from backend.engine.application_launch_handshake import HandshakeError
    value={'schema_version':1,'kind':'backend_ready','challenge':'a'*64,'epoch':'b'*32,'nonce':'c'*32,
        'binding_sha256':'d'*64,'process':{'pid':17,'created_at':1.0,'command_sha256':'e'*64},
        'executable':'/controlled/executable','executable_sha256':'f'*64,'build_identity_sha256':None,'frozen':False,**changes}
    raw=base64.b64encode(canonical(value)).decode()
    with pytest.raises(HandshakeError):_proof(raw)


def test_deeply_nested_base64_proof_is_a_bounded_refusal():
    from backend.engine.application_launch_controller import _proof
    from backend.engine.application_launch_handshake import HandshakeError
    raw=base64.b64encode(b'{"nested":'+b'['*1500+b'0'+b']'*1500+b'}').decode()
    with pytest.raises(HandshakeError):_proof(raw)


def test_readonly_inspection_does_not_take_the_live_backend_transition_mutex(tmp_path):
    from backend.engine.application_launch_lease import _transition_admission
    root,value,current=installed(tmp_path,looping=True);owner=reserve(root,value)
    try:
        row=owner.start()
        with _transition_admission(root,row['nonce']):
            read=subprocess.run([sys.executable,'-m','backend.engine.application_launch_controller','--inspect',*controller_arguments(root,value,current)],cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=10)
            assert read.returncode==0,read.stdout
            assert json.loads(read.stdout)['status']=='starting'
    finally:stop_fixture(owner)


def test_readonly_inspection_refuses_a_valid_controller_transition_during_its_snapshot(tmp_path,monkeypatch):
    from backend.engine import application_launch_controller as controller
    from argparse import Namespace
    root,value,current=installed(tmp_path,looping=True);owner=reserve(root,value)
    try:
        owner.start();args=controller_arguments(root,value,current)
        args=Namespace(**{args[i][2:].replace('-','_'):args[i+1] for i in range(0,len(args),2)})
        actual=controller._same_identity;changed=False
        def race(expected,pid):
            nonlocal changed
            if not changed:owner.recovery('Controlled concurrent transition');changed=True
            return actual(expected,pid)
        monkeypatch.setattr(controller,'_same_identity',race)
        with pytest.raises(ValueError,match='changed during read-only'):controller.inspect(args)
    finally:stop_fixture(owner)


@pytest.fixture(autouse=True)
def controlled_canary_publication(monkeypatch):
    """Scope this legacy lifecycle fixture to controlled canary proof only.

    Actual staged source CPU math is tested independently; these tests retain
    their original signed-layout, admission and recovery assertions.
    """
    from backend.tests.test_staged_update_canary import controlled_proof
    controlled_proof(monkeypatch)
