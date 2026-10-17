"""Owned source registration ordering only; no native/model/tree/lease release claim."""
from argparse import Namespace
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

import pytest

from backend.engine import application_launch_controller as controller
from backend.engine import application_launch_lease as lease
from backend.engine import application_launch_quiescence as q
from backend.engine import application_node_writer_authority as node
from backend.tests.test_application_launch_controller import controller_arguments
from backend.tests.test_application_launch_lease import controlled_canary_publication
from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture, plan, canonical, sha


class StopOwnedControllerLoop(BaseException):
    pass


def _source_registration_pair(tmp_path):
    """Signed inert Node/main and original Python backend; cooperative 30 s caps."""
    from backend.engine.runtime_update import install_update
    assert os.name == 'posix'
    repository = Path(__file__).resolve().parents[2]
    executable_node = shutil.which('node')
    assert executable_node, 'Owned registration fixture requires Node'
    compiler = "const fs=require('fs'),ts=require('typescript');process.stdout.write(ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText)"
    compiled = subprocess.check_output([executable_node, '-e', compiler,
        str(repository/'src/main/applicationLaunch.ts')], timeout=15)
    root, *_ = owned(tmp_path); value = fixture(tmp_path)
    backend = ('#!'+sys.executable+'\n'+f'''import os,sys,threading,time
from pathlib import Path
sys.path.insert(0,{str(repository)!r})
from backend.engine.application_launch_handshake import early_backend_bootstrap,backend_bootstrap_ready,backend_execution_service
from backend.engine.migration_guard import maintenance_guard
root=Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']);stop=threading.Event()
def cooperative_exit():
 deadline=time.monotonic()+30
 while not (root/'projects'/'backend-exit').exists():
  if time.monotonic()>=deadline:break
  time.sleep(.01)
 stop.set()
threading.Thread(target=cooperative_exit,daemon=True).start()
early_backend_bootstrap()
with maintenance_guard(root):
 backend_bootstrap_ready();backend_execution_service(stop)
''').encode()
    main = f'''const fs=require('fs'),path=require('path'),{{spawn}}=require('child_process');
const {{authenticateMainLaunch}}=require('../bridge.cjs');
(async()=>{{const launch=await authenticateMainLaunch(),project=launch.projects;
const executable=path.join(path.dirname(process.argv[1]),'backend_fixture.py');
const backend=spawn({json.dumps(sys.executable)},[executable,'--project-dir',project,'--shared-auth-dir',launch.auth],{{cwd:launch.root,env:launch.backendEnvironment({{PATH:'/usr/bin:/bin'}}),stdio:['pipe','ignore','pipe','pipe']}});
backend.stderr.on('data',raw=>fs.appendFileSync(path.join(project,'backend-stderr.log'),raw));
backend.once('exit',(code,signal)=>fs.writeFileSync(path.join(project,'original-backend-exit.json'),JSON.stringify({{code,signal,pid:backend.pid}})));
fs.writeFileSync(path.join(project,'original-spawn.json'),JSON.stringify({{main:process.pid,backend:backend.pid}}));
// Install the owned exit trigger before waiting for registration. No signal or
// fresh process lookup is needed even if the ACK is correctly withheld.
let expired=false;setTimeout(()=>{{expired=true;fs.writeFileSync(path.join(project,'backend-exit'),'owned fixture deadline');fs.writeFileSync(path.join(project,'main-exit'),'owned fixture deadline');}},30000);
setInterval(()=>{{if(fs.existsSync(path.join(project,'main-exit'))&&backend.exitCode!==null)process.exit(expired?9:0);}},10);
try{{await launch.bindBackend(backend,executable,null);
 fs.writeFileSync(path.join(project,'ready.json'),JSON.stringify({{main:process.pid,backend:backend.pid}}));
}}catch(error){{fs.writeFileSync(path.join(project,'bind-error.txt'),String(error.stack));}}
}})().catch(error=>{{fs.writeFileSync(path.join(process.env.VISION_AI_STUDIO_USER_DATA_DIR,'projects','main-error.txt'),String(error.stack));process.exit(3);}});'''
    files = {'bin/app': ('#!'+executable_node+'\n'+main).encode(),
        'bin/backend_fixture.py': backend, 'bridge.cjs': compiled}
    manifest = {'schema_version': 1, 'version': '1.0.0', 'platform': value['target']['platform'],
        'arch': value['target']['arch'], 'entrypoint': 'bin/app',
        'files': [{'path': name, 'size': len(raw), 'sha256': sha(raw),
            'executable': name.startswith('bin/')} for name, raw in files.items()]}
    archive = value['directory']/'application.zip'
    with zipfile.ZipFile(archive, 'w') as writer:
        writer.writestr('portable-application.json', canonical(manifest))
        for name, raw in files.items(): writer.writestr(name, raw)
    value['payload'].update(sha256=sha(archive.read_bytes()), size=archive.stat().st_size)
    value['payload']['artifacts'][0].update(sha256=value['payload']['sha256'], size=value['payload']['size'])
    value['sign'](value['payload']); current = install_update(root, plan(root, value))
    return root, value, current


def _wait_file(path, seconds):
    deadline = time.monotonic()+seconds
    while not path.exists() and time.monotonic()<deadline: time.sleep(.01)
    assert path.exists(), 'Owned fixture did not publish '+path.name
    return json.loads(path.read_bytes())


@contextmanager
def _authenticate_owned_pair(tmp_path, monkeypatch, seal_observer):
    root, value, current = _source_registration_pair(tmp_path)
    arguments = controller_arguments(root, value, current)
    args = Namespace(**{arguments[i][2:].replace('-', '_'): arguments[i+1]
        for i in range(0, len(arguments), 2)}, inspect=False)
    captured = {'frames': []}
    authenticate = controller.authenticate; seal = node.seal_authentication
    captured['original_seal_authentication'] = seal
    send = controller.send_frame; receive = node.receive_frame
    def authenticate_once(owner, row):
        captured['owner'] = owner
        try: authenticate(owner, row)
        except Exception as exc: captured['failure'] = exc
        raise StopOwnedControllerLoop()
    def observed_seal(authority, event):
        captured['proof_event'] = event
        return seal_observer(root, captured['owner'], authority, event, seal, captured)
    def observed_send(channel, frame, **kwargs):
        result = send(channel, frame, **kwargs)
        captured['frames'].append(frame)
        if frame.get('kind') == 'backend_registered':
            captured['registered_snapshot'] = q.inspect_epoch(root, captured['owner'].nonce)
            captured['registered_journal'] = captured['owner']._owned()
        return result
    def observed_receive(authority, timeout):
        event = receive(authority, timeout)
        frame = node.frame_value(authority, event)
        if frame.get('kind') == 'backend_registration_admitted':
            captured['admitted_event'] = event
            captured['admitted_frame'] = frame
        return event
    try:
        with monkeypatch.context() as patch:
            patch.setattr(controller, 'authenticate', authenticate_once)
            patch.setattr(controller, '_emit', lambda value: None)
            patch.setattr(controller, 'send_frame', observed_send)
            patch.setattr(node, 'seal_authentication', observed_seal)
            patch.setattr(node, 'receive_frame', observed_receive)
            with pytest.raises(StopOwnedControllerLoop): controller.run(args)
            yield root, captured
    finally:
        primary = sys.exc_info()[1]
        owner = captured.get('owner')
        if owner is not None:
            try:
                (root/'projects'/'backend-exit').write_text('original owned backend cooperative exit')
                (root/'projects'/'main-exit').write_text('original owned main cooperative exit')
                assert owner._process.wait(timeout=5) == 0
                observed = _wait_file(root/'projects'/'original-backend-exit.json', 1)
                spawned = json.loads((root/'projects'/'original-spawn.json').read_bytes())
                assert observed == {'code': 0, 'signal': None, 'pid': spawned['backend']}
                owner.observe_exit(); owner.close()
                final = owner._owned()
                assert final['state'] == 'recovery_required'
                assert final['exit_observation']['process_tree_exit_verified'] is False
                with pytest.raises(lease.LaunchLeaseError): lease.assert_quiescent(root)
            except BaseException as cleanup:
                if primary is None: raise
                primary.add_note('Owned registration fixture cleanup also failed: '+repr(cleanup))


def test_owned_backend_registration_is_durable_before_ready_and_ack(tmp_path, monkeypatch):
    def before_seal(root, owner, authority, event, original, captured):
        row = owner._owned()
        assert row['state'] == 'starting' and row['claimed'], \
            'Controller published readiness before durable backend writer registration'
        assert not (root/lease.LEASES/owner.nonce/'bootstrap-receipt.json').exists()
        before = owner._writer_epoch.snapshot()
        backend = next(r for r in before['registry']['writers'] if r['role'] == 'backend')
        assert backend['status'] == 'reserved' and backend['process'] is None
        until = time.monotonic()+.25
        while time.monotonic()<until:
            assert not (root/'projects'/'ready.json').exists(), \
                'Node fixture published readiness before durable backend writer registration'
            time.sleep(.01)
        original(authority, event)
        captured['sealed_snapshot'] = owner._writer_epoch.snapshot()
    with _authenticate_owned_pair(tmp_path, monkeypatch, before_seal) as (root, captured):
        if 'failure' in captured: raise captured['failure']
        owner = captured['owner']; ready = _wait_file(root/'projects'/'ready.json', 3)
        registered = [f for f in captured['frames'] if f.get('kind') == 'backend_registered']
        assert len(registered) == 1
        ack = registered[0]; snap = captured['sealed_snapshot']
        backend = next(r for r in snap['registry']['writers'] if r['role'] == 'backend')
        assert backend['status'] == 'active' and backend['process'] == owner._authenticated_backend_proof['process']
        assert backend['process']['pid'] == ready['backend'] != ready['main'] == owner._process.pid
        assert ack == {'schema_version': 1, 'kind': 'backend_registered', 'nonce': owner.nonce,
            'epoch': owner._authenticated_backend_proof['epoch'],
            'binding_sha256': sha(canonical(owner._owned()['binding'])),
            'backend_claim_sha256': sha(canonical(owner._authenticated_backend_proof)),
            'main_process': owner._owned()['process'], 'backend_process': backend['process'],
            'writer_id': backend['writer_id'], 'registration_sha256': backend['registration_sha256'],
            'active_registry_sha256': snap['registry_sha256']}
        assert captured['registered_snapshot'] == snap == owner._writer_epoch.snapshot()
        assert captured['registered_journal']['state'] == 'starting'
        assert captured['admitted_frame'] == {**ack, 'kind': 'backend_registration_admitted'}
        assert owner._owned()['state'] == 'ready'
        assert (root/lease.LEASES/owner.nonce/'bootstrap-receipt.json').is_file()
        # Actual one-use original event and already-ready state cannot mint a
        # second registration/admission; both refusals preserve durable bytes.
        before = owner._writer_epoch.snapshot()
        with pytest.raises(ValueError):
            node.seal_registration_admission(owner._node_backend_authority, captured['admitted_event'], ack)
        with pytest.raises(ValueError):
            captured['original_seal_authentication'](owner._node_backend_authority, captured['proof_event'])
        assert owner._writer_epoch.snapshot() == before
        assert owner._owned()['exit_observation'] is None


def test_interrupted_backend_registration_withholds_ack_and_readiness(tmp_path, monkeypatch):
    def interrupted_seal(root, owner, authority, event, original, captured):
        directory = root/q.EPOCHS/owner.nonce
        captured['publication_before'] = (directory/'publication.json').read_bytes()
        def interrupt(point):
            if point == 'after_registry_journal':
                raise OSError('Owned backend registration publication interrupted')
        with monkeypatch.context() as fault:
            fault.setattr(q, '_checkpoint', interrupt)
            original(authority, event)
    with _authenticate_owned_pair(tmp_path, monkeypatch, interrupted_seal) as (root, captured):
        error = captured.get('failure')
        assert isinstance(error, OSError) and str(error) == 'Owned backend registration publication interrupted'
        until = time.monotonic()+.25
        while time.monotonic()<until: time.sleep(.01)
        assert not (root/'projects'/'ready.json').exists(), \
            'Node fixture published readiness after failed backend writer registration'
        assert not any(f.get('kind') == 'backend_registered' for f in captured['frames'])
        assert 'admitted_frame' not in captured
        owner = captured['owner']; row = owner._owned()
        assert row['state'] == 'starting' and row['claimed']
        assert not (root/lease.LEASES/owner.nonce/'bootstrap-receipt.json').exists()
        directory = root/q.EPOCHS/owner.nonce
        assert (directory/'publication.json').read_bytes() == captured['publication_before']
        with pytest.raises(q.QuiescenceError, match='partial registry publication'):
            owner._writer_epoch.snapshot()
        retained = {name: (directory/name).read_bytes() for name in ('registry.json', 'publication.json')}
        owner.recovery('Owned fixture registration failure retains original custody')
        assert owner._owned()['state'] == 'recovery_required'
        with pytest.raises(ValueError):
            node.seal_authentication(owner._node_backend_authority, captured['proof_event'])
        assert {name: (directory/name).read_bytes() for name in retained} == retained
