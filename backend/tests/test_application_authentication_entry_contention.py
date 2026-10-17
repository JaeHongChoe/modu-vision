"""Real original Node/main and backend proof with real mutex contention.

Fixture-only pre-claim/pre-ACK cooperative exit watchers make refusal cleanup
bounded. They exit their own original processes on owning marker files, never
signal PIDs or clear durable lease/writer ownership. No native/inference credit.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from argparse import Namespace
from contextlib import ExitStack, contextmanager

import pytest

from backend.engine import application_launch_controller as controller
from backend.engine import application_launch_lease as lease
from backend.engine import application_node_writer_authority as node
from backend.engine.application_launch_handshake import _transition_admission_before_deadline
from backend.tests.test_application_launch_controller import controller_arguments
from backend.tests.test_application_launch_lease import controlled_canary_publication
from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture, plan, canonical, sha


class StopControlledRun(BaseException):
    pass


def contention_source_stack(tmp_path):
    """Signed inert Node/backend, cooperative file exit, no process signals."""
    from backend.engine.runtime_update import install_update
    repository = Path(__file__).resolve().parents[2]
    compiler = "const fs=require('fs'),ts=require('typescript');process.stdout.write(ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText)"
    compiled = subprocess.check_output(['node', '-e', compiler, str(repository/'src/main/applicationLaunch.ts')])
    root, *_ = owned(tmp_path); value = fixture(tmp_path)
    backend = ('#!'+sys.executable+'\n'+f'''import os,sys,threading,time,json
from pathlib import Path
sys.path.insert(0,{str(repository)!r})
from backend.engine import application_launch_handshake as handshake
from backend.engine.application_launch_handshake import early_backend_bootstrap,backend_bootstrap_ready,backend_execution_service
from backend.engine.migration_guard import maintenance_guard
root=Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']);stop=threading.Event();service_started=threading.Event()
def cooperative_exit():
 escrow=None
 while not (root/'projects'/'backend-exit').exists():
  if escrow is None and (root/'projects'/'escrow-request').exists():
   import subprocess
   anchor=handshake._CACHE['writer_private_fd']
   code="import os,sys,time,json;from pathlib import Path;p=Path(sys.argv[1]);fd=int(sys.argv[2]);s=os.fstat(fd);(p/'escrow-held.json').write_text(json.dumps({{'pid':os.getpid(),'lock':[s.st_dev,s.st_ino]}}));\\nwhile not (p/'escrow-release').exists():time.sleep(.01)\\nos.close(fd);(p/'escrow-exited.json').write_text(json.dumps({{'returncode':0}}))"
   escrow=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(root/'projects'),str(anchor)],pass_fds=(anchor,),start_new_session=True)
   (root/'projects'/'escrow-original-handle.json').write_text(json.dumps({{'pid':escrow.pid,'returncode':escrow.poll()}}))
  time.sleep(.01)
 if not service_started.is_set():
  (root/'projects'/'cooperative-pre-ack-backend-exit.json').write_text(json.dumps({{'pid':os.getpid(),'code':0,'signal':None}}))
  os._exit(0)
 stop.set()
threading.Thread(target=cooperative_exit,daemon=True).start()
early_backend_bootstrap()
anchor=handshake._CACHE['writer_private_fd'];info=os.fstat(anchor)
(root/'projects'/'fixture-custody.json').write_text(json.dumps({{'transport_fd':int(os.environ['VISION_APPLICATION_BACKEND_FD']),'guard_retained':handshake._CACHE['writer_guard'] is not None,'private_anchor_fd':anchor,'private_anchor_identity':[info.st_dev,info.st_ino]}}))
with maintenance_guard(root):
 backend_bootstrap_ready();service_started.set()
 (root/'projects'/'cooperative-service-started.json').write_text(json.dumps({{'pid':os.getpid(),'bootstrap_ready_returned':True}}))
 backend_execution_service(stop)
''').encode()
    main = f'''const fs=require('fs'),path=require('path'),{{spawn}}=require('child_process');
const {{authenticateMainLaunch}}=require('../bridge.cjs');
let originalBackend=null;const fixtureProject=path.join(process.env.VISION_AI_STUDIO_USER_DATA_DIR,'projects');
setInterval(()=>{{if(fs.existsSync(path.join(fixtureProject,'main-exit'))&&(originalBackend===null||originalBackend.exitCode!==null)){{
 if(originalBackend===null)fs.writeFileSync(path.join(fixtureProject,'cooperative-pre-claim-main-exit.json'),JSON.stringify({{pid:process.pid,code:0,signal:null}}));
 process.exit(0);
}}}},10);
(async()=>{{const launch=await authenticateMainLaunch(),project=launch.projects;
const executable=path.join(path.dirname(process.argv[1]),'backend_fixture.py');
const backend=originalBackend=spawn({json.dumps(sys.executable)},[executable,'--project-dir',project,'--shared-auth-dir',launch.auth],{{cwd:launch.root,env:launch.backendEnvironment({{PATH:'/usr/bin:/bin'}}),stdio:['pipe','ignore','pipe','pipe']}});
backend.stderr.on('data',raw=>fs.appendFileSync(path.join(project,'backend-stderr.log'),raw));
backend.once('exit',(code,signal)=>fs.writeFileSync(path.join(project,'original-backend-exit.json'),JSON.stringify({{code,signal,pid:backend.pid}})));
await launch.bindBackend(backend,executable,null);
fs.writeFileSync(path.join(project,'ready.json'),JSON.stringify({{main:process.pid,backend:backend.pid}}));
let stopping=false;setInterval(()=>{{
 if(fs.existsSync(path.join(project,'main-exit'))&&backend.exitCode!==null)process.exit(0);
 if(!stopping&&fs.existsSync(path.join(project,'request-drain'))){{stopping=true;
  void (async()=>{{await launch.prepareBackendDrain(backend,4000);fs.writeFileSync(path.join(project,'backend-exit'),'cooperative');
   if(backend.exitCode===null)await new Promise(resolve=>backend.once('exit',resolve));
   await launch.confirmBackendExit(backend);fs.writeFileSync(path.join(project,'node-ack.json'),'accepted');
  }})().catch(error=>fs.writeFileSync(path.join(project,'node-refusal.txt'),String(error)));
 }}
}},10);
}})().catch(error=>{{fs.writeFileSync(path.join(process.env.VISION_AI_STUDIO_USER_DATA_DIR,'projects','main-error.txt'),String(error.stack));process.exit(3);}});'''
    files = {'bin/app': ('#!'+shutil.which('node')+'\n'+main).encode(),
             'bin/backend_fixture.py': backend, 'bridge.cjs': compiled}
    manifest = {'schema_version': 1, 'version': '1.0.0', 'platform': value['target']['platform'],
        'arch': value['target']['arch'], 'entrypoint': 'bin/app',
        'files': [{'path': name, 'size': len(raw), 'sha256': sha(raw), 'executable': name.startswith('bin/')} for name, raw in files.items()]}
    archive = value['directory']/'application.zip'
    with zipfile.ZipFile(archive, 'w') as writer:
        writer.writestr('portable-application.json', canonical(manifest))
        for name, raw in files.items(): writer.writestr(name, raw)
    value['payload'].update(sha256=sha(archive.read_bytes()), size=archive.stat().st_size)
    value['payload']['artifacts'][0].update(sha256=value['payload']['sha256'], size=value['payload']['size'])
    value['sign'](value['payload']); current = install_update(root, plan(root, value))
    return root, value, current


def controlled_entry(tmp_path, monkeypatch, stage, mode):
    root, value, current = contention_source_stack(tmp_path)
    arguments = controller_arguments(root, value, current)
    args = Namespace(**{arguments[i][2:].replace('-', '_'): arguments[i + 1]
                       for i in range(0, len(arguments), 2)}, inspect=False)
    captured = {'sent': [], 'received': [], 'challenges': 0, 'busy_entries': 0,
                'claims': 0, 'claim_publications': 0, 'artifacts': 0, 'artifact_entry_bodies': 0,
                'artifact_validations': 0, 'artifact_documents': {}, 'proof_documents': {}, 'proof_decodes': 0}
    entered = threading.Event(); release = threading.Event(); holder_errors = []
    holder = None; target_started = False; artifact_body_active = False; controller_thread = threading.get_ident()
    authenticate = controller.authenticate; claim = lease.LaunchSupervisor.claim
    persist = lease.LaunchSupervisor._persist; recover = lease.LaunchSupervisor.recovery
    send = controller.send_frame; challenge = node.send_authentication_challenge
    receive = node.receive_frame; frame_value = node.frame_value; proof = controller._proof
    artifact = controller._backend_artifact; transition = lease._transition_admission

    def start_holder(owner):
        nonlocal holder, target_started
        assert holder is None, 'The original stage cannot be restarted'
        def hold_original_mutex():
            try:
                with transition(root, owner.nonce):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('Controlled real holder release was not bounded')
            except BaseException as error:
                holder_errors.append(error); entered.set()
        holder = threading.Thread(target=hold_original_mutex)
        holder.start()
        assert entered.wait(2) and not holder_errors
        target_started = True

    @contextmanager
    def observe_real_entry(directory, nonce=None):
        with ExitStack() as stack:
            try:
                stack.enter_context(transition(directory, nonce))
            except lease.LeaseTransitionBusy:
                if target_started and threading.get_ident() == controller_thread:
                    captured['busy_entries'] += 1
                    if mode == 'changed_proof':
                        if stage == 'claim':
                            captured['main_claim']['challenge'] = '0' * 64
                        else:
                            captured['ready_proof']['epoch'] = '0' * 32
                        release.set()
                    elif mode == 'release':
                        release.set()
                raise
            yield

    def original_send(channel, frame, **kwargs):
        captured['sent'].append(copy.deepcopy(frame))
        if frame['kind'] == 'main_admitted': captured['main_admitted_deadline'] = kwargs.get('absolute_deadline')
        return send(channel, frame, **kwargs)

    def original_challenge(authority, frame):
        captured['challenges'] += 1
        return challenge(authority, frame)

    def original_receive(authority, seconds):
        event = receive(authority, seconds)
        captured['received'].append(frame_value(authority, event)['kind'])
        return event

    def original_value(authority, event):
        frame = frame_value(authority, event)
        if frame['kind'] == 'main_claim' and 'main_claim' not in captured:
            captured['main_claim'] = frame
        return frame

    def original_claim(owner, process, **kwargs):
        captured['claims'] += 1
        if stage == 'claim':
            start_holder(owner)
            captured['original_deadline'] = kwargs.get('absolute_deadline')
            if mode == 'expire' and 'absolute_deadline' in kwargs:
                bound = time.monotonic() + .5
                assert kwargs['absolute_deadline'] > bound
                kwargs['absolute_deadline'] = bound; captured['short_deadline'] = bound
        return claim(owner, process, **kwargs)

    def original_persist(owner, row, **changes):
        if row['claimed'] is False and changes.get('claimed') is True: captured['claim_publications'] += 1
        return persist(owner, row, **changes)

    def original_proof(raw):
        result = proof(raw); captured['proof_decodes'] += 1
        # seal_authentication revalidates these same two documents. A decode
        # is not a second descriptor receive or a repeated admission body.
        digest = sha(raw.encode())
        document = {'kind': result['kind'], 'canonical_sha256': sha(canonical(result))}
        if digest in captured['proof_documents']:
            assert captured['proof_documents'][digest] == document
        else:
            captured['proof_documents'][digest] = document
        if result['kind'] == 'backend_ready' and 'ready_proof' not in captured:
            captured['ready_proof'] = result
            if stage == 'artifact': start_holder(captured['owner'])
        return result

    def original_artifact(*args):
        captured['artifact_validations'] += 1
        document = sha(canonical([str(args[0]), *args[1:]]))
        captured['artifact_documents'][document] = captured['artifact_documents'].get(document, 0) + 1
        if artifact_body_active: captured['artifacts'] += 1
        return artifact(*args)

    @contextmanager
    def original_artifact_entry(directory, nonce, deadline, *, before_attempt=None):
        nonlocal artifact_body_active
        if stage == 'artifact' and target_started:
            captured['original_deadline'] = deadline
            if mode == 'expire':
                bound = time.monotonic() + .5
                assert deadline > bound
                deadline = bound; captured['short_deadline'] = bound
        with _transition_admission_before_deadline(directory, nonce, deadline,
                before_attempt=before_attempt):
            assert not artifact_body_active
            artifact_body_active = True; captured['artifact_entry_bodies'] += 1
            try:
                yield
            finally:
                artifact_body_active = False

    def original_authentication(owner, row):
        captured['owner'] = owner
        try:
            captured['backend'] = authenticate(owner, row)
        except Exception as error:
            captured['error'] = error
            raise
        raise StopControlledRun()

    def retain_recovery(owner, reason):
        release.set()
        if holder is not None:
            holder.join(timeout=2); assert not holder.is_alive()
        result = recover(owner, reason); captured['recovery'] = result
        raise StopControlledRun()

    try:
        with monkeypatch.context() as patch:
            patch.setattr(controller, 'authenticate', original_authentication)
            patch.setattr(controller, '_emit', lambda value: None)
            patch.setattr(controller, 'send_frame', original_send)
            patch.setattr(controller, '_proof', original_proof)
            patch.setattr(controller, '_backend_artifact', original_artifact)
            # The old one-shot artifact source does not contain this symbol.
            # It still enters the same real transition and must fail RED.
            patch.setattr(controller, '_transition_admission_before_deadline', original_artifact_entry, raising=False)
            patch.setattr(node, 'send_authentication_challenge', original_challenge)
            patch.setattr(node, 'receive_frame', original_receive)
            patch.setattr(node, 'frame_value', original_value)
            patch.setattr(lease.LaunchSupervisor, 'claim', original_claim)
            patch.setattr(lease.LaunchSupervisor, '_persist', original_persist)
            patch.setattr(lease.LaunchSupervisor, 'recovery', retain_recovery)
            patch.setattr(lease, '_transition_admission', observe_real_entry)
            with pytest.raises(StopControlledRun): controller.run(args)
        release.set()
        if holder is not None:
            holder.join(timeout=2); assert not holder.is_alive()
        assert not holder_errors
        owner = captured['owner']
        if 'error' not in captured:
            until = time.monotonic() + 3
            ready_path = root / 'projects' / 'ready.json'
            service_path = root / 'projects' / 'cooperative-service-started.json'
            while (not ready_path.exists() or not service_path.exists()) and time.monotonic() < until:
                time.sleep(.01)
            observed = json.loads(ready_path.read_bytes())
            service = json.loads(service_path.read_bytes())
            assert observed == {'main': owner._process.pid, 'backend': captured['backend']['pid']}
            assert service == {'pid': captured['backend']['pid'], 'bootstrap_ready_returned': True}
        captured['final'] = lease.inspect_launch(root)
        captured['final_registry'] = owner._writer_epoch.snapshot()
        path = root / lease.LEASES / owner.nonce / 'bootstrap-receipt.json'
        captured['bootstrap_exists'] = path.exists()
        captured['quiescent'] = None
        try: lease.assert_quiescent(root)
        except ValueError as error: captured['quiescent'] = str(error)
        return captured
    finally:
        release.set()
        if holder is not None:
            holder.join(timeout=2)
            assert not holder.is_alive(), 'Original controlled mutex holder remains unresolved'
        owner = captured.get('owner')
        if owner is not None:
            # Original fixture self-exit only: never signal/adopt/release a PID.
            (root / 'projects' / 'backend-exit').write_text('cooperative original fixture')
            (root / 'projects' / 'main-exit').write_text('cooperative original fixture')
            owner._process.wait(timeout=5)
            if stage == 'claim' and 'error' in captured:
                marker = root / 'projects' / 'cooperative-pre-claim-main-exit.json'
                assert json.loads(marker.read_bytes()) == {'pid': owner._process.pid, 'code': 0, 'signal': None}
                assert not (root / 'projects' / 'original-backend-exit.json').exists()
                assert owner._process.returncode == 0
            else:
                marker = root / 'projects' / 'original-backend-exit.json'
                until = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < until: time.sleep(.01)
                observed = json.loads(marker.read_bytes())
                assert observed['code'] == 0 and observed['signal'] is None
                if 'error' in captured:
                    early_path = root / 'projects' / 'cooperative-pre-ack-backend-exit.json'
                    if early_path.exists():
                        early = json.loads(early_path.read_bytes())
                        assert early == {'pid': observed['pid'], 'code': 0, 'signal': None}
                    else:
                        # bootstrap_ready can return before the controller's
                        # registration ACK. Its normal fixture service loop
                        # then exits cooperatively on the same owning marker.
                        service = json.loads((root / 'projects' / 'cooperative-service-started.json').read_bytes())
                        assert service == {'pid': observed['pid'], 'bootstrap_ready_returned': True}
                    assert owner._process.returncode in (0, 3)
                else:
                    assert owner._process.returncode == 0
            owner.close()


@pytest.mark.parametrize('stage', ['claim', 'artifact'])
def test_original_authentication_survives_brief_real_mutex_contention(tmp_path, monkeypatch, stage):
    result = controlled_entry(tmp_path, monkeypatch, stage, 'release')
    assert 'error' not in result, repr(result.get('error'))
    assert result['final']['state'] == 'ready' and result['bootstrap_exists']
    assert result['busy_entries'] >= 1 and result['original_deadline'] is not None
    assert result['main_admitted_deadline'] is not None
    assert result['claims'] == result['claim_publications'] == result['artifacts'] == result['challenges'] == 1
    assert result['artifact_entry_bodies'] == 1
    assert len(result['artifact_documents']) == 1 and result['artifact_validations'] == 2
    assert len(result['proof_documents']) == 2
    assert sorted(row['kind'] for row in result['proof_documents'].values()) == ['backend_claim', 'backend_ready']
    assert result['proof_decodes'] >= 2
    assert result['received'] == ['main_claim', 'backend_proof', 'backend_registration_admitted']
    assert [row['kind'] for row in result['sent']] == ['main_admitted', 'backend_registered']
    writers = result['final_registry']['registry']['writers']
    assert len(writers) == 1 and writers[0]['status'] == 'active'
    assert writers[0]['process'] == result['backend']
    assert result['quiescent'] is not None


@pytest.mark.parametrize('stage', ['claim', 'artifact'])
def test_expired_original_entry_bound_retains_recovery_without_replay(tmp_path, monkeypatch, stage):
    result = controlled_entry(tmp_path, monkeypatch, stage, 'expire')
    assert 'Original transition entry deadline expired' in str(result['error'])
    assert result['busy_entries'] >= 1
    assert result['final']['state'] == 'recovery_required' and not result['bootstrap_exists']
    assert result['final']['claimed'] is (stage == 'artifact')
    assert result['final']['writer_drain']['phase'] == 'enrolled'
    assert result['challenges'] == result['claims'] == 1 and result['artifacts'] == 0
    assert result['artifact_entry_bodies'] == result['artifact_validations'] == 0
    assert result['received'] == (['main_claim'] if stage == 'claim' else ['main_claim', 'backend_proof'])
    assert [row['kind'] for row in result['sent']] == ([] if stage == 'claim' else ['main_admitted'])
    assert result['quiescent'] is not None


@pytest.mark.parametrize('stage', ['claim', 'artifact'])
def test_changed_original_proof_refuses_after_real_busy_entry(tmp_path, monkeypatch, stage):
    result = controlled_entry(tmp_path, monkeypatch, stage, 'changed_proof')
    message = 'Original main claim changed' if stage == 'claim' else 'Original backend proof changed'
    assert message in str(result['error'])
    assert result['busy_entries'] == 1
    assert result['final']['state'] == 'recovery_required' and not result['bootstrap_exists']
    assert result['final']['claimed'] is (stage == 'artifact')
    assert result['final']['writer_drain']['phase'] == 'enrolled'
    assert result['challenges'] == result['claims'] == 1 and result['artifacts'] == 0
    assert result['artifact_entry_bodies'] == result['artifact_validations'] == 0
    assert result['received'] == (['main_claim'] if stage == 'claim' else ['main_claim', 'backend_proof'])
    assert [row['kind'] for row in result['sent']] == ([] if stage == 'claim' else ['main_admitted'])
    assert result['quiescent'] is not None
