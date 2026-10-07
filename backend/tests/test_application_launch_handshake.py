"""Private descriptor/process fixtures; no native application or model execution."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import zipfile

import pytest

from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture, plan, canonical, sha


def api():
    try:
        from backend.engine import application_launch_handshake
    except ImportError:
        pytest.fail('Early descriptor-authenticated backend startup contract is missing')
    return application_launch_handshake


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}\n', b'{"a":NaN}\n', b'[]\n', b'{ "a":1}\n', b'{}\r\n', b'\xff\n', b'{"a":"'+b'x'*65536+b'"}\n'])
def test_private_frame_rejects_ambiguous_noncanonical_or_unbounded_json(raw):
    left, right = socket.socketpair()
    try:
        reader=api()
        writer=threading.Thread(target=lambda: left.sendall(raw),daemon=True);writer.start()
        with pytest.raises(ValueError): reader.read_frame(right, .2)
    finally: left.close(); right.close()


def test_private_frame_roundtrip_fragment_eof_and_deadline():
    left, right = socket.socketpair()
    try:
        value = {'kind': 'controlled', 'schema_version': 1}
        api().send_frame(left, value)
        assert api().read_frame(right, .2) == value
        with pytest.raises(ValueError, match='deadline|timeout'): api().read_frame(right, .02)
        left.sendall(b'{'); left.shutdown(socket.SHUT_WR)
        with pytest.raises(ValueError, match='EOF|closed'): api().read_frame(right, .2)
        with pytest.raises(ValueError): api().read_frame(right, 211)
    finally: left.close(); right.close()


def installed_backend(tmp_path):
    from backend.engine import runtime_update as update, application_launch_lease as lease
    root, *_ = owned(tmp_path)
    value = fixture(tmp_path)
    script = b'''import json,os,sys\nfrom backend.engine.application_launch_handshake import early_backend_bootstrap,backend_bootstrap_ready\nfrom backend.engine.global_store_paths import store_admission\nproof=early_backend_bootstrap();assert early_backend_bootstrap()==proof\nmode=os.environ.get('CONTROLLED_HANDSHAKE_CASE','valid')\nif mode=='changed_env':\n os.environ['VISION_APPLICATION_LAUNCH_NONCE']='f'*32;early_backend_bootstrap()\nif mode=='changed_argv':\n sys.argv.append('--project-dir');sys.argv.append('/foreign');early_backend_bootstrap()\nif mode=='frozen_spoof':\n sys.frozen=True;early_backend_bootstrap()\nif mode=='no_admission': backend_bootstrap_ready()\nwith store_admission(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']):\n ready=backend_bootstrap_ready();assert backend_bootstrap_ready()==ready\nprint(json.dumps({'verified':True,'fd_inheritable':os.get_inheritable(int(os.environ['VISION_APPLICATION_BACKEND_FD']))}),flush=True)\n'''
    app=b'#!/bin/sh\nexit 0\n'
    manifest={'schema_version':1,'version':'1.0.0','platform':value['target']['platform'],'arch':value['target']['arch'],
              'entrypoint':'bin/app','files':[{'path':'bin/app','size':len(app),'sha256':sha(app),'executable':True},
              {'path':'bin/vision_backend.py','size':len(script),'sha256':sha(script),'executable':True}]}
    archive=value['directory']/'application.zip'
    with zipfile.ZipFile(archive,'w') as out:
        out.writestr('portable-application.json',canonical(manifest));out.writestr('bin/app',app);out.writestr('bin/vision_backend.py',script)
    value['payload'].update(sha256=sha(archive.read_bytes()),size=archive.stat().st_size)
    value['payload']['artifacts'][0].update(sha256=value['payload']['sha256'],size=value['payload']['size']);value['sign'](value['payload'])
    update.install_update(root,plan(root,value))
    supervisor=lease.LaunchSupervisor.reserve(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])
    # Current real parent identity is a controlled source transport fixture,
    # never a claim that the signed inert app was executed.
    journal=root/lease.LEASES/supervisor.nonce/'journal.json'; pointer=root/lease.ACTIVE_LEASE
    record=json.loads(journal.read_bytes()); record.update(state='starting',spawn_attempted=True,process=lease._identity(os.getpid()),revision=2)
    update._write(journal.parent/'spawn-intent.json',{'schema_version':1,'nonce':supervisor.nonce,'binding_sha256':sha(canonical(record['binding']))})
    update._write(journal,record);update._write(pointer,lease._publication(record))
    backend=Path(record['binding']['executable']).parent/'vision_backend.py'
    return root,record,backend


def launch(tmp_path, *, change=None, mode='valid', replay=False, args_suffix=()):
    root, record, backend=installed_backend(tmp_path)
    left,right=socket.socketpair();right.set_inheritable(True)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(Path(__file__).resolve().parents[2]),
        VISION_AI_STUDIO_USER_DATA_DIR=str(root),VISION_APPLICATION_LAUNCH_NONCE=record['nonce'],
        VISION_APPLICATION_GENERATION=record['binding']['application_generation'],
        VISION_APPLICATION_DATABASE_GENERATION=record['binding']['database_generation_path'],
        VISION_APPLICATION_BACKEND_FD=str(right.fileno()),CONTROLLED_HANDSHAKE_CASE=mode)
    env.pop('VISION_APPLICATION_LAUNCH_FD',None)
    args=[sys.executable,str(backend),'--project-dir',str(root/'projects'),'--shared-auth-dir',str(root/'auth'),*args_suffix]
    child=subprocess.Popen(args,env=env,pass_fds=(right.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    right.close()
    challenge={'schema_version':1,'kind':'backend_challenge','challenge':'a'*64,'epoch':'b'*32,'nonce':record['nonce'],
        'binding':record['binding'],'main_process':record['process'],'backend_pid':child.pid,
        'backend_executable':str(backend),'backend_build_identity_sha256':None}
    if change: change(challenge)
    raw=canonical(challenge)+b'\n';left.sendall(raw+(raw if replay else b''))
    return root,record,backend,left,child,challenge


def finish(left, child):
    try:
        return child.communicate(timeout=15)
    finally:
        left.close()
        if child.poll() is None:child.kill();child.wait(timeout=5)


def test_real_private_descriptor_binds_source_child_and_emits_exact_claim_ready(tmp_path):
    root, record, backend, channel, child, challenge=launch(tmp_path)
    try:
        claim=api().read_frame(channel,10);ready=api().read_frame(channel,10)
        assert claim['kind']=='backend_claim' and ready['kind']=='backend_ready'
        for frame in [claim,ready]:
            assert set(frame)=={'schema_version','kind','challenge','epoch','nonce','binding_sha256','process','executable','executable_sha256','build_identity_sha256','frozen'}
            assert frame['challenge']==challenge['challenge'] and frame['epoch']==challenge['epoch']
            assert frame['nonce']==record['nonce'] and frame['process']['pid']==child.pid
            assert frame['binding_sha256']==sha(canonical(record['binding']))
            assert frame['executable']==str(backend) and frame['executable_sha256']==sha(backend.read_bytes())
            assert frame['frozen'] is False and frame['build_identity_sha256'] is None
        stdout,stderr=finish(channel,child)
        assert child.returncode==0,stderr
        assert json.loads(stdout)=={'verified':True,'fd_inheritable':False}
    finally:
        if child.poll() is None:finish(channel,child)


@pytest.mark.parametrize('damage',['extra','nonce','epoch','binding','parent_pid','parent_birth','backend_pid','backend_executable','build_identity'])
def test_unknown_or_foreign_backend_challenge_refuses_without_claim(tmp_path,damage):
    def change(frame):
        if damage=='extra':frame['unexpected']=True
        elif damage=='nonce':frame['nonce']='f'*32
        elif damage=='epoch':frame['epoch']=True
        elif damage=='binding':frame['binding']['database_pointer']['fence']=float(frame['binding']['database_pointer']['fence'])
        elif damage=='parent_pid':frame['main_process']['pid']+=1
        elif damage=='parent_birth':frame['main_process']['created_at']+=1
        elif damage=='backend_pid':frame['backend_pid']+=1
        elif damage=='backend_executable':frame['backend_executable']=sys.executable
        else:frame['backend_build_identity_sha256']='c'*64
    root,record,backend,channel,child,challenge=launch(tmp_path,change=change)
    stdout,stderr=finish(channel,child)
    assert child.returncode!=0 and 'HandshakeError' in stderr
    assert not stdout


@pytest.mark.parametrize('mode',['changed_env','changed_argv','frozen_spoof','no_admission'])
def test_process_local_proof_cannot_skip_changed_context(tmp_path,mode):
    root,record,backend,channel,child,challenge=launch(tmp_path,mode=mode)
    try:assert api().read_frame(channel,10)['kind']=='backend_claim'
    finally:stdout,stderr=finish(channel,child)
    assert child.returncode!=0 and 'HandshakeError' in stderr and not stdout


def test_duplicate_challenge_is_not_an_idempotent_process_proof(tmp_path):
    root,record,backend,channel,child,challenge=launch(tmp_path,replay=True)
    stdout,stderr=finish(channel,child)
    assert child.returncode!=0 and 'HandshakeError' in stderr and not stdout


@pytest.mark.parametrize('target',['backend.main','frozen'])
def test_owned_pair_without_fd_refuses_before_application_imports_or_mutation(tmp_path,target):
    root,record,backend=installed_backend(tmp_path)
    before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    source=Path(__file__).resolve().parents[2]
    script="import sys,runpy,importlib.abc;\nclass RejectHeavy(importlib.abc.MetaPathFinder):\n def find_spec(self,name,path=None,target=None):\n  if name in ('uvicorn','fastapi') or name.startswith('backend.api'):raise AssertionError('unverified heavy import')\nsys.meta_path.insert(0,RejectHeavy());\n"
    script+= "runpy.run_module('backend.main',run_name='__main__')" if target=='backend.main' else 'runpy.run_path('+repr(str(source/'scripts/frozen_backend_entry.py'))+",run_name='__main__')"
    env=dict(os.environ,PYTHONPATH=str(source),PYTHONDONTWRITEBYTECODE='1',VISION_AI_STUDIO_USER_DATA_DIR=str(root))
    for key in list(env):
        if key.startswith('VISION_APPLICATION_'):env.pop(key)
    result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode!=0 and 'HandshakeError' in result.stderr and 'unverified heavy import' not in result.stderr
    assert before=={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('flag',['--project-d','--shared-auth-d'])
def test_argparse_abbreviation_cannot_override_verified_original_paths(tmp_path,flag):
    root,record,backend,channel,child,challenge=launch(tmp_path,args_suffix=(flag,'/foreign'))
    stdout,stderr=finish(channel,child)
    assert child.returncode!=0 and 'HandshakeError' in stderr and not stdout


@pytest.mark.parametrize('value',['0','2','03','8193','-1','x','',None])
def test_backend_descriptor_number_is_explicit_canonical_and_bounded(value):
    with pytest.raises(api().HandshakeError):api()._transport(value)


def test_backend_descriptor_refuses_regular_file_and_datagram(tmp_path):
    file=tmp_path/'transport';file.write_bytes(b'{}\n')
    fd=os.open(file,os.O_RDONLY)
    try:
        with pytest.raises(api().HandshakeError):api()._transport(str(fd))
    finally:
        try:os.close(fd)
        except OSError:pass
    left,right=socket.socketpair(type=socket.SOCK_DGRAM)
    try:
        detached=right.detach()
        with pytest.raises(api().HandshakeError):api()._transport(str(detached))
    finally:
        left.close()
        try:os.close(detached)
        except OSError:pass


def test_legacy_unowned_no_context_does_not_create_store(tmp_path,monkeypatch):
    module=api();monkeypatch.setattr(module,'_CACHE',None)
    for name in module._CONTEXT_NAMES:monkeypatch.delenv(name,raising=False)
    before=list(tmp_path.iterdir())
    assert module.early_backend_bootstrap() is None and module.backend_bootstrap_ready() is None
    assert list(tmp_path.iterdir())==before


@pytest.mark.parametrize('case',['nonce_only','fd_only','main_fd','generation_only'])
def test_partial_backend_context_cannot_authorize_owned_pair(tmp_path,monkeypatch,case):
    root,record,backend=installed_backend(tmp_path)
    module=api();monkeypatch.setattr(module,'_CACHE',None)
    for name in module._CONTEXT_NAMES:monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    name={'nonce_only':'VISION_APPLICATION_LAUNCH_NONCE','fd_only':'VISION_APPLICATION_BACKEND_FD',
          'main_fd':'VISION_APPLICATION_LAUNCH_FD','generation_only':'VISION_APPLICATION_GENERATION'}[case]
    monkeypatch.setenv(name,record['nonce'] if 'NONCE' in name or 'GENERATION' in name else '3')
    before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(module.HandshakeError):module.early_backend_bootstrap()
    assert before=={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_process_birth_numeric_shape_survives_node_integral_float_reencoding():
    actual={'pid':12,'created_at':123.0,'command_sha256':'a'*64}
    assert api()._same_process({**actual,'created_at':123},actual)
    assert not api()._same_process({**actual,'pid':True},actual)
    assert not api()._same_process({**actual,'created_at':True},actual)


@pytest.fixture(autouse=True)
def controlled_canary_publication(monkeypatch):
    """Scope this legacy lifecycle fixture to controlled canary proof only.

    Actual staged source CPU math is tested independently; these tests retain
    their original signed-layout, admission and recovery assertions.
    """
    from backend.tests.test_staged_update_canary import controlled_proof
    controlled_proof(monkeypatch)
