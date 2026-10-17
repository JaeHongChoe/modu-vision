"""Real temporary original-controller store-writer closure component tests.

No Electron, ASGI, model, GPU or network server is started. The existing signed
inert Node/backend fixture, original authentication/finalizer/run, actual retained
Popen and inherited OFD locks are used. Only controlled preactivation canary
publication and the two named controller-close/fault injection seams below are
fixture suppliers; no new helper authority/caller/code/one-use predicate is patched.
A passing source component is not installed/native or complete OS-tree acceptance.
"""
import contextlib
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
import time
import zipfile

import pytest

from backend.engine import application_launch_controller as controller
from backend.engine import application_launch_lease as lease
from backend.engine import application_launch_quiescence as q
from backend.tests.test_application_launch_controller import controller_arguments
from backend.tests.test_application_launch_lease import controlled_canary_publication, installed, reserve
from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture, plan, canonical, sha

SOURCE_REPOSITORY = Path(lease.__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Original lifetime protocol uses POSIX OFD locks')


def closure_api():
    return importlib.import_module('backend.engine.application_store_writer_closure')


def _files(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mode)
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def _wait(path, child, seconds=10):
    deadline = time.monotonic()+seconds
    while not path.exists():
        if child.poll() is not None:
            raise AssertionError('Original component controller exited before '+str(path)+': '+str(child.returncode))
        if time.monotonic() >= deadline:
            raise AssertionError('Original component marker budget expired: '+str(path))
        time.sleep(.01)
    return path


def _prepared_source_stack(tmp_path):
    """Signed inert Node/backend, cooperative file exit, no process signals."""
    from backend.engine.runtime_update import install_update
    repository = SOURCE_REPOSITORY
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
  void (async()=>{{await launch.prepareBackendShutdown(backend);await launch.prepareBackendDrain(backend,4000);fs.writeFileSync(path.join(project,'backend-exit'),'cooperative');
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


@contextlib.contextmanager
def component(tmp_path, *, fault=None, pause_close=False):
    root, value, current = _prepared_source_stack(tmp_path)
    # Only the preactivation worker is a disclosed legacy controlled supplier.
    # Original Node authentication, finalizer/run and closure gates are unpatched.
    argv=[sys.executable,'-B',str(Path(__file__).resolve()),'--closure-component-child',fault or '-', 'hold' if pause_close else 'normal',*controller_arguments(root,value,current)]
    child_environment=dict(os.environ);child_environment['PYTHONPATH']=str(SOURCE_REPOSITORY)
    log=(tmp_path/'component-controller.log').open('wb')
    child=subprocess.Popen(argv,cwd=SOURCE_REPOSITORY,env=child_environment,stdout=log,stderr=subprocess.STDOUT)
    child._source_fixture_value=value
    try:
        _wait(root/'projects/ready.json',child)
        yield root, child
    finally:
        # Cooperative own-fixture completion only. A forced cleanup fails this
        # component and never manufactures a successful production close.
        project=root/'projects'
        for name in ['escrow-release','backend-exit','main-exit','release-controller-close']:
            (project/name).write_text('cooperative owned component cleanup')
        forced=False
        try:child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            forced=True;child.kill();child.wait(timeout=5)
        log.close()
        assert not forced, 'Owned component did not close normally; controller log retained'


def _drain(root, child):
    (root/'projects/request-drain').write_text('same original prepared drain')
    deadline=time.monotonic()+10
    while True:
        if (root/'projects/node-ack.json').exists():return
        refused=root/'projects/node-refusal.txt'
        if refused.exists():raise AssertionError(refused.read_text())
        if child.poll() is not None:raise AssertionError('Controller left before original Node drain ACK')
        if time.monotonic()>=deadline:raise AssertionError('Original Node drain ACK absent')
        time.sleep(.01)


def _finish(root, child):
    _drain(root,child)
    (root/'projects/main-exit').write_text('cooperative exact original main')
    assert child.wait(timeout=10)==0
    row=lease.inspect_launch(root)
    assert row['protocol_version']==5 and row['state']=='writer_store_closed', row
    return row


def _receipt(root,row):
    result=closure_api().inspect_store_closure(root,row)
    assert result['schema']=='modu-vision.original-store-writer-closure/v1'
    assert result['scope']=='original_managed_store_writers'
    assert result['nonce']==row['nonce']
    assert result['managed_scopes_drained'] is True and result['unsupported']==[]
    assert result['all_enrolled_writers_direct_exited'] is True
    assert result['all_writer_lock_references_closed'] is True
    for name in ['process_tree_exit_verified','whole_writer_coverage','release_ready','model_quality_approved','publisher_approved','parent_acceptance']:
        assert result[name] is False
    return result


def test_original_prepared_shutdown_closes_only_store_writer_scope(tmp_path):
    with component(tmp_path) as (root,child):
        before={n:(root/n).read_bytes()for n in ['application-active.json','global-active.json']}
        row=_finish(root,child)
        receipt=_receipt(root,row)
        assert row['writer_store_closure_sha256']==sha((root/'.application-writer-closures'/row['nonce']/'receipt.json').read_bytes())
        assert all((root/n).read_bytes()==body for n,body in before.items())
        assert not(root/q.EPOCHS/row['nonce']).exists()
        archive=root/'.application-writer-archives'/row['nonce']
        snapshot=_files(archive)
        registry=json.loads((archive/'registry.json').read_bytes())
        assert registry['state']=='closed' and registry['writers']
        assert all(r['status']=='direct_exited' and r['exit_code']==0 for r in registry['writers'])
        assert lease.assert_quiescent(root)['scope']=='original_managed_store_mutation_only'
        assert _files(archive)==snapshot
        assert receipt['process_tree_exit_verified'] is False


def test_manual_foreign_or_reopened_controller_cannot_prepare_or_finish(tmp_path):
    module=closure_api()
    root,value,_=installed(tmp_path);owner=reserve(root,value)
    other=lease.LaunchSupervisor(root,owner.nonce)
    before=_files(root)
    try:
        for candidate in [owner,other,object()]:
            with pytest.raises((ValueError,TypeError,AttributeError)):
                module.prepare_original_controller(candidate,object())
            with pytest.raises((ValueError,TypeError,AttributeError)):
                module.finish_original_controller(candidate)
        assert _files(root)==before
    finally:owner.cancel();owner.close()


def test_live_original_controller_still_blocks_mutation_after_terminal_publication(tmp_path):
    with component(tmp_path,pause_close=True)as(root,child):
        _drain(root,child);(root/'projects/main-exit').write_text('cooperative original main')
        _wait(root/'projects/controller-terminal-held',child)
        row=lease.inspect_launch(root)
        assert row['protocol_version']==5 and row['state']=='writer_store_closed'
        _receipt(root,row)
        with pytest.raises(ValueError):lease.assert_quiescent(root)
        (root/'projects/release-controller-close').write_text('release own original handle')
        assert child.wait(timeout=10)==0
        assert lease.assert_quiescent(root)['scope']=='original_managed_store_mutation_only'


@pytest.mark.parametrize('damage',['receipt','unknown-archive-member','missing-database-lock'])
def test_terminal_closure_refuses_missing_corrupt_or_unknown_evidence_without_repair(tmp_path,damage):
    with component(tmp_path)as(root,child):
        row=_finish(root,child);_receipt(root,row)
        if damage=='receipt':
            target=root/'.application-writer-closures'/row['nonce']/'receipt.json'
            target.write_bytes(target.read_bytes()+b' ')
        elif damage=='unknown-archive-member':
            (root/'.application-writer-archives'/row['nonce']/'unreviewed.bin').write_bytes(b'unknown')
        else:(root/lease.DATABASE_LOCK).unlink()
        before=_files(root)
        with pytest.raises(ValueError):lease.assert_quiescent(root)
        assert _files(root)==before


@pytest.mark.parametrize('point',['after_store_closure_intent','after_store_closure_manifest','after_store_epoch_archive','before_store_closure_lease_publication'])
def test_interrupted_archive_or_publication_never_qualifies_or_repairs(tmp_path,point):
    with component(tmp_path,fault=point)as(root,child):
        _drain(root,child);(root/'projects/main-exit').write_text('cooperative original main')
        child.wait(timeout=10)
        before=_files(root)
        with pytest.raises(ValueError):lease.assert_quiescent(root)
        row=lease.inspect_launch(root)
        assert row['protocol_version']==4 and row['state']=='recovery_required'
        assert _files(root)==before


def test_inherited_original_writer_OFD_blocks_closure_after_direct_backend_exit(tmp_path):
    with component(tmp_path)as(root,child):
        (root/'projects/escrow-request').write_text('explicit original OFD descendant')
        _wait(root/'projects/escrow-held.json',child)
        # The original final ACK declares managed drain and explicitly grants
        # no lease/tree release. Keep the actual inherited FD open through the
        # retained main0 and controller finish to test the store-closure fence.
        _drain(root,child)
        held=json.loads((root/'projects/escrow-held.json').read_bytes())
        (root/'projects/main-exit').write_text('cooperative own original main')
        assert child.wait(timeout=10)==0
        assert not(root/'projects/escrow-exited.json').exists()
        row=lease.inspect_launch(root)
        assert row['protocol_version']==4 and row['state']=='recovery_required'
        assert not(root/'.application-writer-archives'/row['nonce']).exists()
        assert not(root/'.application-writer-closures'/row['nonce']).exists()
        import fcntl
        lock=root/q.EPOCHS/row['nonce']/'writers'/row['writer_drain']['writer_id']/'ownership.lock'
        probe=os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
        try:
            info=os.fstat(probe)
            assert [info.st_dev,info.st_ino]==held['lock']
            with pytest.raises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(probe)
        assert (root/q.EPOCHS/row['nonce']).exists()
        with pytest.raises(ValueError):lease.assert_quiescent(root)
        (root/'projects/escrow-release').write_text('normal close of original inherited FD')
        until=time.monotonic()+5
        while not(root/'projects/escrow-exited.json').exists():
            if time.monotonic()>=until:raise AssertionError('Controlled descriptor child did not close')
            time.sleep(.01)
        with pytest.raises(ValueError):lease.assert_quiescent(root)


def test_committed_database_successor_can_follow_closure_then_new_reservation(tmp_path):
    with component(tmp_path)as(root,child):
        row=_finish(root,child);_receipt(root,row)
        from backend.engine import runtime_update as update
        value=child._source_fixture_value
        original=lease.inspect_launch(root)
        second=fixture(tmp_path,version='1.1.0',key=value['key'],authority=value['authority'])
        second['target']['current_version']='1.0.0'
        proposal=plan(root,second)
        result=update.install_update(root,proposal)
        assert result['status']=='committed'
        assert lease.inspect_launch(root)['nonce']==original['nonce']
        assert lease.assert_quiescent(root)['scope']=='original_managed_store_mutation_only'
        fresh=reserve(root,second)
        try:assert fresh.nonce!=row['nonce']
        finally:fresh.cancel();fresh.close()



def test_protocol2_3_and_unqualified4_refusals_do_not_gain_terminal_authority(tmp_path):
    root,value,_=installed(tmp_path);owner=reserve(root,value)
    try:
        original=lease.inspect_launch(root)
        assert original['protocol_version']==3 and original['state']=='reserved'
        with pytest.raises(ValueError):lease.assert_quiescent(root)
        owner.enroll_backend_writer();before=_files(root)
        with pytest.raises(ValueError):lease.assert_quiescent(root)
        assert _files(root)==before
        assert lease.inspect_launch(root)['protocol_version']==4
    finally:owner.close()


if __name__=='__main__':
    assert sys.argv[1]=='--closure-component-child'
    fault,hold=sys.argv[2:4];arguments=sys.argv[4:]
    if fault!='-':
        module=closure_api()
        def injected(point):
            if point==fault:raise OSError('controlled closure publication interruption')
        module._checkpoint=injected
    if hold=='hold':
        oldclose=lease.LaunchSupervisor.close
        def controlled_close(self):
            row=self._owned()
            if row.get('protocol_version')==5:
                project=self.root/'projects'
                (project/'controller-terminal-held').write_text('actual terminal controller alive')
                deadline=time.monotonic()+20
                while not(project/'release-controller-close').exists():
                    if time.monotonic()>=deadline:raise RuntimeError('controlled close hold expired')
                    time.sleep(.01)
            return oldclose(self)
        lease.LaunchSupervisor.close=controlled_close
    # Same disclosed original controlled preactivation supplier in this separate
    # source controller too; it grants no actual canary/installed acceptance.
    from backend.tests.test_staged_update_canary import controlled_proof
    canary_patch=pytest.MonkeyPatch();controlled_proof(canary_patch)
    try:raise SystemExit(controller.main(arguments))
    finally:canary_patch.undo()
