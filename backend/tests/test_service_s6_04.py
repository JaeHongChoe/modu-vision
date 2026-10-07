"""Signed portable updates use actual files, admission and SQLite generations.

Signing keys and the tiny executable are controlled qualification fixtures;
these tests do not establish a real publisher, OS installer or model approval.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import shutil
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from backend.tests.test_global_migration import owned


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(tmp_path, *, version='1.0.0', key=None, authority=None, extra=None):
    directory=tmp_path/('bundle-'+version);directory.mkdir()
    key=key or Ed25519PrivateKey.generate()
    matrix={'api_context':1,'worker':1,'runtime':1,'dataset_index':1}
    authority=authority or tmp_path/'authority.json'
    if not authority.exists():
        public=key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        authority.write_bytes(canonical({'schema_version':1,'publisher':'Qualification fixture',
            'keys':{'fixture':base64.b64encode(public).decode()},'revoked_key_ids':[],
            'allowed_origins':['https://releases.example.test'],'compatibility':matrix}))
    executable=b'#!/bin/sh\nprintf "portable-qualified\\n"\n'
    files=[{'path':'bin/app','size':len(executable),'sha256':sha(executable),'executable':True}]
    portable={'schema_version':1,'version':version,'platform':'darwin' if os.uname().sysname=='Darwin' else 'linux',
        'arch':'arm64' if os.uname().machine=='arm64' else 'x64','entrypoint':'bin/app','files':files}
    archive=directory/'application.zip'
    with zipfile.ZipFile(archive,'w') as writer:
        writer.writestr('portable-application.json',canonical(portable))
        writer.writestr('bin/app',executable)
        if extra:writer.writestr(extra,b'unexpected')
    raw=archive.read_bytes()
    payload={'version':version,'channel':'stable','platform':portable['platform'],'arch':portable['arch'],
        'url':'https://releases.example.test/application.zip','sha256':sha(raw),'size':len(raw),
        'publisher':'Qualification fixture','compatibility':matrix,
        'artifacts':[{'path':archive.name,'kind':'installer','sha256':sha(raw),'size':len(raw)}]}
    envelope=tmp_path/('envelope-'+version+'.json')
    def sign(value):
        body=canonical(value)
        envelope.write_bytes(canonical({'schema_version':1,'key_id':'fixture',
            'payload_b64':base64.b64encode(body).decode(),'signature_b64':base64.b64encode(key.sign(body)).decode()}))
    sign(payload)
    target={'platform':portable['platform'],'arch':portable['arch'],'channel':'stable',
        'current_version':'0.0.0','origin':'https://releases.example.test'}
    return dict(directory=directory,envelope=envelope,authority=authority,
        pinned_authority_sha256=sha(authority.read_bytes()),target=target,key=key,payload=payload,sign=sign)


def plan(root, value):
    from backend.engine.runtime_update import plan_update
    return plan_update(root, value['directory'], value['envelope'], value['authority'],
        pinned_authority_sha256=value['pinned_authority_sha256'],target=value['target'])


def test_signed_offline_portable_install_launch_and_next_update(tmp_path):
    from backend.engine.runtime_update import install_update, launch_plan, recover_update
    from backend.engine.global_store_paths import active_generation
    root,scopes,*_=owned(tmp_path)
    first=fixture(tmp_path);result=install_update(root,plan(root,first))
    assert result['status']=='committed'
    command=launch_plan(root,first['authority'],pinned_authority_sha256=first['pinned_authority_sha256'])
    assert command['database_pointer']==active_generation(root)[1]
    process=subprocess.run(command['argv'],capture_output=True,text=True,timeout=5)
    assert process.returncode==0 and process.stdout=='portable-qualified\n'
    second=fixture(tmp_path,version='1.1.0',key=first['key'],authority=first['authority'])
    second['target']['current_version']='1.0.0'
    installed=install_update(root,plan(root,second))
    assert installed['status']=='committed' and active_generation(root)[1]['fence']==2
    assert recover_update(root,installed['update_id'],action='finish')['status']=='committed'
    assert launch_plan(root,second['authority'],pinned_authority_sha256=second['pinned_authority_sha256'])['version']=='1.1.0'
    assert (root/'projects/labels.json').read_bytes()==b'{"label":"original"}'


@pytest.mark.parametrize('failure',['before_database','database_prepared','after_database','after_application','after_receipt'])
def test_interruption_blocks_attachment_then_finishes_exact_pair(tmp_path,monkeypatch,failure):
    from backend.engine import runtime_update as update
    from backend.engine.global_store_paths import store_admission,active_generation
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path)
    original=update._checkpoint
    def stop(point):
        if point==failure:raise KeyboardInterrupt('controlled power interruption')
    monkeypatch.setattr(update,'_checkpoint',stop)
    with pytest.raises(KeyboardInterrupt):update.install_update(root,plan(root,value))
    pending=json.loads((root/'application-update-pending.json').read_bytes())
    with pytest.raises(ValueError,match='update|recovery'): 
        with store_admission(root):pass
    monkeypatch.setattr(update,'_checkpoint',original)
    result=update.recover_update(root,pending['update_id'],action='finish')
    assert result['status']=='committed'
    command=update.launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])
    assert command['database_pointer']==active_generation(root)[1]
    assert not (root/'application-update-pending.json').exists()


def test_forward_recovery_preserves_post_cutover_data_and_refuses_stale_owner(tmp_path):
    from backend.engine.runtime_update import install_update,recover_update,launch_plan
    from backend.engine.global_store_paths import active_generation,resolve_store_path
    from backend.engine.shared_accounts import AccountStore
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path)
    installed=install_update(root,plan(root,value));prior=active_generation(root)[1]
    fresh=AccountStore(root/scopes['accounts']);user=fresh.create_user('after-cutover','fixture-password-456')
    profile=resolve_store_path(root/scopes['profiles']);profile.write_bytes(b'{"profiles":[],"selected":null,"new_write":true}')
    assert recover_update(root,installed['update_id'],action='finish')['status']=='committed'
    repaired=recover_update(root,installed['update_id'],action='forward')
    assert repaired['status']=='committed' and active_generation(root)[1]['fence']==prior['fence']+1
    assert json.loads(resolve_store_path(root/scopes['profiles']).read_bytes())['new_write']
    assert user['id'] in {row['id'] for row in AccountStore(root/scopes['accounts']).users()}
    with pytest.raises(ValueError,match='retired|generation'):fresh.users()
    assert launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])['database_pointer']==active_generation(root)[1]


@pytest.mark.parametrize('change',['signature','revoked','publisher','protocol','downgrade','additional_file','linked_file','zip_traversal'])
def test_untrusted_or_incompatible_input_never_changes_installation(tmp_path,change):
    from backend.engine.runtime_update import UpdateError
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path,extra='../escape' if change=='zip_traversal' else None)
    if change=='signature':
        envelope=json.loads(value['envelope'].read_bytes());envelope['signature_b64']=base64.b64encode(b'x'*64).decode();value['envelope'].write_bytes(canonical(envelope))
    elif change=='revoked':
        data=json.loads(value['authority'].read_bytes());data['revoked_key_ids']=['fixture'];value['authority'].write_bytes(canonical(data));value['pinned_authority_sha256']=sha(value['authority'].read_bytes())
    elif change=='publisher':value['payload']['publisher']='Foreign fixture';value['sign'](value['payload'])
    elif change=='protocol':value['payload']['compatibility']['worker']=2;value['sign'](value['payload'])
    elif change=='downgrade':value['target']['current_version']='2.0.0'
    elif change=='additional_file':(value['directory']/'extra').write_bytes(b'extra')
    elif change=='linked_file':
        source=value['directory']/'application.zip';source.rename(tmp_path/'original.zip');source.symlink_to(tmp_path/'original.zip')
    with pytest.raises((UpdateError,ValueError)):plan(root,value)
    assert not (root/'application-active.json').exists()
    assert not (root/'global-active.json').exists()
    assert not (root/'.application-updates').exists()


def test_changed_bundle_or_source_after_preview_is_refused(tmp_path):
    from backend.engine.runtime_update import install_update
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path);proposal=plan(root,value)
    (value['directory']/'application.zip').write_bytes(b'changed after preview')
    with pytest.raises(ValueError):install_update(root,proposal)
    assert not (root/'global-active.json').exists()


def test_active_training_refuses_without_staging_or_process_stop(tmp_path):
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path)
    with sqlite3.connect(root/scopes['ledger']) as db:
        db.execute("INSERT INTO jobs(id,workspace_id,project_key,project_id,actor_id,mode,kind,spec_sha256,spec_json,state,revision,source,created_ns,updated_ns) VALUES('j','w','p','p','a','local','training','h','{}','running',1,'fixture',1,1)")
    with pytest.raises(ValueError,match='Drain|active|drain'):plan(root,value)
    assert not (root/'.application-updates').exists()


def test_launch_refuses_changed_application_and_database_pointer(tmp_path):
    from backend.engine.runtime_update import install_update,launch_plan
    from backend.engine import global_migration
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path);installed=install_update(root,plan(root,value))
    command=launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])
    executable=Path(command['argv'][0]);executable.chmod(0o600);executable.write_bytes(b'changed executable')
    with pytest.raises(ValueError,match='checksum|integrity|size'):launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])
    # A separate migration must never silently attach an otherwise valid app.
    with zipfile.ZipFile(value['directory']/'application.zip') as archive:executable.write_bytes(archive.read('bin/app'))
    executable.chmod(0o500)
    global_migration.advance(root,expected_source_sha256=global_migration.preview_forward(root)['source_sha256'])
    with pytest.raises(ValueError,match='database|generation|pair'):launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])


def test_control_link_cannot_redirect_update_writes(tmp_path):
    from backend.engine.runtime_update import install_update
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path);proposal=plan(root,value)
    external=tmp_path/'outside';external.mkdir();(external/'sentinel').write_bytes(b'unchanged')
    (root/'.application-updates').symlink_to(external,target_is_directory=True)
    with pytest.raises(ValueError,match='link'):install_update(root,proposal)
    assert {p.name:p.read_bytes() for p in external.iterdir()}=={'sentinel':b'unchanged'}


@pytest.mark.parametrize('failure',['database_prepared','after_application'])
def test_abrupt_subprocess_exit_keeps_recoverable_application_database_pair(tmp_path,failure):
    import sys
    from backend.engine.runtime_update import recover_update,launch_plan
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path)
    arguments={k:str(value[k]) for k in ('directory','envelope','authority','pinned_authority_sha256')}
    arguments.update(target=value['target'],root=str(root),failure=failure)
    script='''
import json,os,sys
from backend.engine import runtime_update as update
v=json.loads(sys.argv[1])
def power_loss(point):
    if point==v['failure']:os._exit(17)
update._checkpoint=power_loss
proposal=update.plan_update(v['root'],v['directory'],v['envelope'],v['authority'],pinned_authority_sha256=v['pinned_authority_sha256'],target=v['target'])
update.install_update(v['root'],proposal)
'''
    process=subprocess.run([sys.executable,'-c',script,json.dumps(arguments)],cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=30)
    assert process.returncode==17,process.stdout+process.stderr
    pending=json.loads((root/'application-update-pending.json').read_bytes())
    assert recover_update(root,pending['update_id'],action='finish')['status']=='committed'
    assert launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])['version']=='1.0.0'


def test_stale_update_recovery_does_not_block_new_application(tmp_path):
    from backend.engine.runtime_update import install_update,recover_update,launch_plan
    root,scopes,*_=owned(tmp_path);first=fixture(tmp_path);previous=install_update(root,plan(root,first))
    second=fixture(tmp_path,version='1.1.0',key=first['key'],authority=first['authority']);second['target']['current_version']='1.0.0'
    install_update(root,plan(root,second))
    with pytest.raises(ValueError,match='pair|generation'):recover_update(root,previous['update_id'],action='finish')
    assert not (root/'application-update-pending.json').exists()
    assert launch_plan(root,second['authority'],pinned_authority_sha256=second['pinned_authority_sha256'])['version']=='1.1.0'


def test_pre_database_abort_retains_prior_files_and_releases_admission(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update
    from backend.engine.global_store_paths import store_admission
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path)
    def stop(point):
        if point=='before_database':raise KeyboardInterrupt()
    monkeypatch.setattr(update,'_checkpoint',stop)
    with pytest.raises(KeyboardInterrupt):update.install_update(root,plan(root,value))
    pending=json.loads((root/'application-update-pending.json').read_bytes())
    assert update.recover_update(root,pending['update_id'],action='abort')['status']=='aborted'
    assert not (root/'global-active.json').exists() and not (root/'application-active.json').exists()
    with store_admission(root):pass
    assert (root/'.application-updates'/pending['update_id']/'bundle/application.zip').exists()


def test_tampered_staged_application_refuses_recovery_and_keeps_original_database(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path)
    def stop(point):
        if point=='database_prepared':raise KeyboardInterrupt()
    monkeypatch.setattr(update,'_checkpoint',stop)
    with pytest.raises(KeyboardInterrupt):update.install_update(root,plan(root,value))
    pending=json.loads((root/'application-update-pending.json').read_bytes());identifier=pending['update_id']
    executable=root/'.application-generations'/identifier/'application/bin/app';executable.chmod(0o600);executable.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='checksum|size'):update.recover_update(root,identifier,action='finish')
    assert (root/'application-update-pending.json').exists() and not (root/'global-active.json').exists()


def test_source_changed_after_plan_refuses_without_install_staging(tmp_path):
    from backend.engine.runtime_update import install_update
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path);proposal=plan(root,value)
    (root/'projects/labels.json').write_bytes(b'new source data')
    with pytest.raises(ValueError,match='changed'):install_update(root,proposal)
    assert not (root/'.application-updates').exists()


def test_nested_application_control_filename_stays_in_data_snapshot(tmp_path):
    from backend.engine.global_migration import preview
    root,scopes,*_=owned(tmp_path)
    file=root/'projects/application-active.json';file.write_bytes(b'project-specific data')
    before=preview(root)
    assert 'projects/application-active.json' in {row['path'] for row in before['inventory']['files']}
    file.write_bytes(b'changed project-specific data')
    assert preview(root)['source_sha256']!=before['source_sha256']


def test_malformed_runtime_authority_cannot_be_ignored_as_an_artifact(tmp_path):
    from backend.engine.global_migration import preview
    root,scopes,*_=owned(tmp_path)
    (root/'projects/runtime-state.json').write_bytes(b'{unreadable process identity')
    result=preview(root)
    assert not result['can_apply'] and any('Unreadable' in reason for reason in result['blockers'])
    assert 'projects/runtime-state.json' in {row['path'] for row in result['inventory']['files']}


def test_pending_update_blocks_fresh_backend_before_store_construction(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update
    from backend.main import create_app
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path)
    def stop(point):
        if point=='after_database':raise KeyboardInterrupt()
    monkeypatch.setattr(update,'_checkpoint',stop)
    with pytest.raises(KeyboardInterrupt):update.install_update(root,plan(root,value))
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    with pytest.raises(ValueError,match='update|recovery'):create_app(project_dir=str(root/'projects'),shared_auth_dir=str(root/'auth'))


def test_runtime_pack_inventory_is_verified_and_retained_without_execution(tmp_path):
    from backend.engine.runtime_update import install_update,launch_plan
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path)
    raw=b'controlled inert runtime pack';(value['directory']/'cpu-pack.zip').write_bytes(raw)
    value['payload']['artifacts'].append({'path':'cpu-pack.zip','kind':'runtime_pack','sha256':sha(raw),'size':len(raw)})
    value['sign'](value['payload']);installed=install_update(root,plan(root,value))
    assert launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])['runtime_packs']==['cpu-pack.zip']
    assert (root/'.application-updates'/installed['update_id']/'bundle/cpu-pack.zip').read_bytes()==raw


def test_direct_launcher_dispatch_uses_explicit_offline_update_cli(tmp_path):
    import sys
    root,scopes,*_=owned(tmp_path);value=fixture(tmp_path);target=tmp_path/'target.json';target.write_bytes(canonical(value['target']))
    repository=Path(__file__).resolve().parents[2]
    env={**os.environ,'PYTHONPATH':str(repository)}
    result=subprocess.run([sys.executable,str(repository/'scripts/frozen_backend_entry.py'),'--offline-application-update','install',
        '--root',str(root),'--bundle',str(value['directory']),'--envelope',str(value['envelope']),
        '--authority',str(value['authority']),'--pinned-authority-sha256',value['pinned_authority_sha256'],
        '--target-file',str(target)],env=env,cwd=repository,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['status']=='committed'
    assert 'fixture-password' not in result.stdout


@pytest.mark.parametrize('change',['valid','signature','publisher','revoked','protocol','downgrade','origin','port_zero','beta_numeric','beta_downgrade','unknown_key','duplicate_artifact'])
def test_portable_verifier_matches_actual_electron_trust_boundary(tmp_path,change):
    from backend.engine.runtime_update import verify_release
    node=shutil.which('node')
    if node is None:pytest.skip('Electron source conformance requires the Node runtime')
    value=fixture(tmp_path)
    if change=='publisher':value['payload']['publisher']='Another publisher'
    elif change=='protocol':value['payload']['compatibility']['worker']=2
    elif change=='downgrade':value['target']['current_version']='2.0.0'
    elif change=='origin':value['payload']['url']='https://foreign.example.test/application.zip'
    elif change=='port_zero':value['payload']['url']='https://releases.example.test:0/application.zip'
    elif change=='duplicate_artifact':value['payload']['artifacts']*=2
    elif change in ('beta_numeric','beta_downgrade'):
        value['payload'].update(channel='beta',version='1.0.0-beta.10' if change=='beta_numeric' else '1.0.0-beta.2')
        value['target'].update(channel='beta',current_version='1.0.0-beta.2' if change=='beta_numeric' else '1.0.0-beta.10')
    if change=='revoked':
        authority=json.loads(value['authority'].read_bytes());authority['revoked_key_ids']=['fixture']
        value['authority'].write_bytes(canonical(authority));value['pinned_authority_sha256']=sha(value['authority'].read_bytes())
    value['sign'](value['payload'])
    if change in ('signature','unknown_key'):
        envelope=json.loads(value['envelope'].read_bytes())
        if change=='signature':envelope['signature_b64']=base64.b64encode(b'x'*64).decode()
        else:envelope['key_id']='unknown'
        value['envelope'].write_bytes(canonical(envelope))
    accepted=True
    try:verify_release(value['envelope'],value['authority'],value['pinned_authority_sha256'],value['target'])
    except ValueError:accepted=False
    repository=Path(__file__).resolve().parents[2]
    script='''
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const file=path.resolve('src/main/releaseTrust.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));
m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);
const envelope=JSON.parse(fs.readFileSync(process.argv[1],'utf8')),authority=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),target=JSON.parse(process.argv[3]);
try{m.exports.verifySignedRelease(envelope,authority,target);process.exit(0);}catch(error){process.exit(2);}
'''
    result=subprocess.run([node,'-e',script,str(value['envelope']),str(value['authority']),json.dumps(value['target'])],
        cwd=repository,capture_output=True,text=True,timeout=15)
    assert result.returncode in (0,2),result.stderr
    assert accepted==(result.returncode==0)
    assert accepted==(change in ('valid','beta_numeric'))
