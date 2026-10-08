"""Inert original Node/channel controls; no CPU math or full app/tree claim."""
import base64
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from argparse import Namespace

import pytest

from backend.engine import application_launch_controller as controller
from backend.engine import application_launch_lease as lease
from backend.engine import application_launch_quiescence as q
from backend.tests.test_application_launch_controller import controller_arguments
from backend.tests.test_application_launch_lease import controlled_canary_publication
from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture, plan, canonical, sha


class StopControlledRun(BaseException):
    pass


def source_stack(tmp_path):
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
root=Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']);stop=threading.Event()
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
 stop.set()
threading.Thread(target=cooperative_exit,daemon=True).start()
early_backend_bootstrap()
anchor=handshake._CACHE['writer_private_fd'];info=os.fstat(anchor)
(root/'projects'/'fixture-custody.json').write_text(json.dumps({{'transport_fd':int(os.environ['VISION_APPLICATION_BACKEND_FD']),'guard_retained':handshake._CACHE['writer_guard'] is not None,'private_anchor_fd':anchor,'private_anchor_identity':[info.st_dev,info.st_ino]}}))
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


@pytest.fixture
def stack(tmp_path, monkeypatch):
    root, value, current = source_stack(tmp_path)
    arguments = controller_arguments(root, value, current)
    args = Namespace(**{arguments[i][2:].replace('-', '_'): arguments[i+1] for i in range(0,len(arguments),2)}, inspect=False)
    original = controller.authenticate; captured = {}
    def authenticated(owner, row):
        captured['owner'] = owner
        try: original(owner, row)
        except Exception as exc: captured['authentication_failure'] = exc
        raise StopControlledRun()
    # Delegate original authentication and stop only the infinite fixture
    # controller loop. Creation/start/authentication remain production code.
    with monkeypatch.context() as patch:
        patch.setattr(controller, 'authenticate', authenticated)
        patch.setattr(controller, '_emit', lambda value: None)
        try:
            with pytest.raises(StopControlledRun): controller.run(args)
            assert 'authentication_failure' not in captured, str(captured.get('authentication_failure'))
            until = time.monotonic()+3
            while not (root/'projects'/'ready.json').exists() and time.monotonic()<until: time.sleep(.01)
            assert (root/'projects'/'ready.json').exists()
            yield root, captured['owner']
        finally:
            owner = captured.get('owner')
            if owner is not None:
                (root/'projects'/'backend-exit').write_text('cooperative exact fixture backend')
                (root/'projects'/'escrow-release').write_text('cooperative original descriptor descendant')
                (root/'projects'/'main-exit').write_text('cooperative exact fixture main')
                owner._process.wait(timeout=5)
                owner.close()
                assert owner._process.returncode == 0
                exit_row = json.loads((root/'projects'/'original-backend-exit.json').read_bytes())
                assert exit_row['code'] == 0 and exit_row['signal'] is None


def snapshot(owner):
    return owner._writer_epoch.snapshot()


def backend_row(owner):
    return next(row for row in snapshot(owner)['registry']['writers'] if row['role']=='backend')


def api():
    try: return importlib.import_module('backend.engine.application_node_writer_authority')
    except ModuleNotFoundError as exc:
        if exc.name == 'backend.engine.application_node_writer_authority':
            pytest.fail('Original private Node backend authority is missing')
        raise


def test_original_private_authentication_binds_backend_distinct_from_main(stack):
    root, owner = stack
    row = backend_row(owner)
    observed = json.loads((root/'projects'/'ready.json').read_bytes())
    assert row['status'] == 'active', 'Original private authenticated Node backend remained reserved'
    assert row['process']['pid'] == observed['backend'] != owner._process.pid == observed['main']
    assert row['process'] == owner._authenticated_backend_proof['process']
    assert owner._process.poll() is None and row['exit_code'] is None
    # Lifetime locking remains a separate real kernel observation.
    import fcntl
    lock = root/q.EPOCHS/owner.nonce/'writers'/row['writer_id']/'ownership.lock'
    fd = os.open(lock, os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError): fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally: os.close(fd)


@pytest.mark.parametrize('raw', ['pid', 'proof', 'main_handle'])
def test_raw_values_cannot_bind_or_complete_node_writer(stack, raw):
    root, owner = stack; module = api(); before = snapshot(owner)
    row = backend_row(owner)
    value = {'pid': row['process']['pid'], 'returncode': 0} if raw == 'pid' else copy.deepcopy(owner._authenticated_backend_proof) if raw == 'proof' else owner._process
    with pytest.raises((ValueError,TypeError)):
        module.NodeBackendAuthority(value)
    bind = getattr(owner._writer_epoch, 'bind_authenticated_node_backend', None)
    observe = getattr(owner._writer_epoch, 'observe_authenticated_node_backend_exit', None)
    assert callable(bind) and callable(observe), 'Core has no separate typed original Node gateway'
    with pytest.raises((ValueError,TypeError)):
        bind(row['writer_id'], value, expected_registry_sha256=before['registry_sha256'])
    with pytest.raises((ValueError,TypeError)):
        observe(row['writer_id'], value, expected_registry_sha256=before['registry_sha256'])
    assert snapshot(owner) == before
    expected = 'already bound or unresolved' if raw == 'main_handle' else 'original child OS handle'
    with pytest.raises(q.QuiescenceError,match=expected):
        owner._writer_epoch.bind_original_child(row['writer_id'], value,
            expected_registry_sha256=before['registry_sha256'])


def original_drain(stack):
    root, owner = stack; module = api()
    authority = owner._node_backend_authority
    (root/'projects'/'request-drain').write_text('original fixture drain')
    event = module.receive_frame(authority, 4)
    request = module.frame_value(authority, event)
    assert request['kind'] == 'main_drain_request'
    controller.prepare_drain(owner, request)
    assert owner._owned()['writer_drain']['phase'] == 'drained'
    event = module.receive_frame(authority, 4)
    assert module.frame_value(authority, event)['kind'] == 'main_backend_exit'
    return root, owner, module, authority, event


def test_original_typed_receive_completes_only_backend_and_cannot_replay(stack):
    root, owner, module, authority, event = original_drain(stack)
    before = snapshot(owner)
    controller.observe_backend_exit(owner, event)
    row = backend_row(owner)
    assert row['status'] == 'direct_exited' and row['exit_code'] == 0
    assert owner._process.poll() is None
    assert owner._owned()['writer_drain']['phase'] == 'backend_exited'
    until = time.monotonic()+2
    while not (root/'projects'/'node-ack.json').exists() and time.monotonic()<until: time.sleep(.01)
    assert (root/'projects'/'node-ack.json').exists()
    after = snapshot(owner)
    assert after['registry']['state'] == 'closed'
    assert after['registry']['revision'] == before['registry']['revision']+1
    with pytest.raises((ValueError, TypeError)):
        controller.observe_backend_exit(owner, event)
    assert snapshot(owner) == after
    with owner._writer_epoch.enrolled_writer_fence(expected_registry_sha256=after['registry_sha256']) as fence:
        assert fence['enrolled_writer_count'] == 1
        assert fence['can_release_launch_lease'] is False
        assert fence['process_tree_exit_verified'] is False


@pytest.mark.parametrize('damage', ['decoded', 'forged', 'copy', 'main_changed', 'main_exited', 'channel_changed', 'late', 'cas'])
def test_terminal_authority_refuses_damage_without_registry_repair(stack, monkeypatch, damage):
    root, owner, module, authority, event = original_drain(stack)
    before = snapshot(owner); original = owner._process; channel = owner._bootstrap_channel
    replacement = None
    try:
        if damage == 'decoded': event = module.frame_value(authority, event)
        elif damage == 'forged': event = object.__new__(module.NodeBackendEvent)
        elif damage == 'copy':
            with pytest.raises((ValueError,TypeError)): copy.copy(event)
            assert snapshot(owner) == before
            return
        elif damage == 'main_changed': owner._process = None
        elif damage == 'main_exited':
            (root/'projects'/'main-exit').write_text('cooperative original main')
            assert original.wait(timeout=3)==0
        elif damage == 'channel_changed':
            import socket
            replacement, peer = socket.socketpair(); peer.close()
            owner._bootstrap_channel = replacement
        elif damage == 'late': monkeypatch.setattr(module, '_now', lambda: time.monotonic()+10)
        elif damage == 'cas':
            with pytest.raises((ValueError,TypeError)):
                owner._writer_epoch.observe_authenticated_node_backend_exit(backend_row(owner)['writer_id'], event,
                    expected_registry_sha256='a'*64)
            assert snapshot(owner) == before
            return
        with pytest.raises((ValueError,TypeError,OSError)):
            controller.observe_backend_exit(owner, event)
    finally:
        owner._process = original; owner._bootstrap_channel = channel
        if replacement is not None: replacement.close()
    assert snapshot(owner) == before
    assert owner._owned()['writer_drain']['phase'] == 'drained'


@pytest.mark.parametrize('damage', ['main_birth', 'main_command', 'main_parent', 'main_session', 'transport', 'thread', 'authority_copy'])
def test_retained_channel_requires_fresh_original_main(stack, monkeypatch, damage):
    root, owner = stack; module = api(); authority = owner._node_backend_authority
    before = snapshot(owner)
    if damage in {'main_birth','main_command'}:
        original = lease._identity
        def drift(pid):
            result = original(pid)
            if pid != owner._process.pid:return result
            return {**result, 'created_at':result['created_at']+1} if damage=='main_birth' else {**result,'command_sha256':'0'*64}
        monkeypatch.setattr(lease, '_identity', drift)
    elif damage == 'main_parent':
        original = module._parent_pid
        monkeypatch.setattr(module, '_parent_pid', lambda pid: original(pid)+1 if pid == owner._process.pid else original(pid))
    elif damage == 'main_session':
        original = module._session_identity
        monkeypatch.setattr(module, '_session_identity', lambda pid: (pid+1,pid) if pid == owner._process.pid else original(pid))
    elif damage == 'transport': owner._bootstrap_transport = {**owner._bootstrap_transport, 'inode':'0'}
    elif damage == 'authority_copy':
        with pytest.raises((ValueError,TypeError)): copy.copy(authority)
        assert snapshot(owner) == before
        return
    if damage == 'thread':
        errors=[]
        def foreign():
            try: module.receive_frame(authority,.01)
            except Exception as exc: errors.append(exc)
        thread=threading.Thread(target=foreign);thread.start();thread.join()
        assert len(errors)==1 and isinstance(errors[0],ValueError)
    else:
        with pytest.raises((ValueError,TypeError,OSError)): module.receive_frame(authority,.01)
    # No publication is performed by a failed transport observation.
    assert q.inspect_epoch(owner.root,owner.nonce)==before


def test_live_main_is_rechecked_after_original_receive(stack,monkeypatch):
    root,owner=stack;module=api();authority=owner._node_backend_authority
    before=snapshot(owner);original=module.read_frame
    def changed_after_read(channel,timeout):
        value=original(channel,timeout)
        owner._bootstrap_transport={**owner._bootstrap_transport,'inode':'0'}
        return value
    monkeypatch.setattr(module,'read_frame',changed_after_read)
    (root/'projects'/'request-drain').write_text('actual original frame')
    with pytest.raises(ValueError,match='endpoint changed'):
        module.receive_frame(authority,4)
    assert snapshot(owner)==before
    with pytest.raises(ValueError,match='cannot be repaired'):
        module.receive_frame(authority,.01)


def test_interrupted_core_exit_publication_never_repairs_or_acknowledges(stack,monkeypatch):
    root,owner,module,authority,event=original_drain(stack)
    before=snapshot(owner)
    def interrupted(point):
        if point=='after_registry_journal':raise OSError('controlled publication interruption')
    monkeypatch.setattr(q,'_checkpoint',interrupted)
    with pytest.raises(OSError,match='interruption'):controller.observe_backend_exit(owner,event)
    with pytest.raises(q.QuiescenceError,match='partial registry publication'):
        snapshot(owner)
    with pytest.raises(ValueError):controller.observe_backend_exit(owner,event)
    assert owner._owned()['writer_drain']['phase']=='drained'
    assert not (root/'projects'/'node-ack.json').exists()
    # The exact original pointer stays at its old revision. No partial-journal
    # repair, fresh event, caller JSON, or lease-release observation is minted.
    pointer=json.loads((root/q.EPOCHS/owner.nonce/'publication.json').read_bytes())
    assert pointer['revision']==before['registry']['revision']


def test_original_node_direct_exit_does_not_erase_escaped_descriptor_custody(stack):
    root,owner=stack;projects=root/'projects'
    try:
        (projects/'escrow-request').write_text('original backend anchor only')
        until=time.monotonic()+3
        while not (projects/'escrow-held.json').exists() and time.monotonic()<until:time.sleep(.01)
        custody=json.loads((projects/'fixture-custody.json').read_bytes())
        held=json.loads((projects/'escrow-held.json').read_bytes())
        handle=json.loads((projects/'escrow-original-handle.json').read_bytes())
        assert held['lock']==custody['private_anchor_identity']
        assert held['pid']==handle['pid'] and handle['returncode'] is None
        root,owner,module,authority,event=original_drain(stack)
        controller.observe_backend_exit(owner,event)
        assert backend_row(owner)['status']=='direct_exited'
        snap=snapshot(owner)
        with pytest.raises(q.QuiescenceError,match='writer lock reference remains held'):
            with owner._writer_epoch.enrolled_writer_fence(expected_registry_sha256=snap['registry_sha256']):
                pytest.fail('Original Node exit erased an escaped descriptor reference')
        assert snapshot(owner)==snap
    finally:(projects/'escrow-release').write_text('cooperative own descendant exit')
    until=time.monotonic()+3
    while not (projects/'escrow-exited.json').exists() and time.monotonic()<until:time.sleep(.01)
    assert json.loads((projects/'escrow-exited.json').read_bytes())['returncode']==0
    while True:
        try:
            with owner._writer_epoch.enrolled_writer_fence(expected_registry_sha256=snap['registry_sha256']) as proof:
                assert proof['can_release_launch_lease'] is False
                assert proof['process_tree_exit_verified'] is False
            break
        except q.QuiescenceError:
            if time.monotonic()>=until:raise
            time.sleep(.01)
