"""Real server-secret persistence, project isolation and portable archive refusal."""
import json
import os
from pathlib import Path
import sqlite3

import pytest


def registry(tmp_path,monkeypatch):
    from backend.engine.fleet import FleetRegistry
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'server'))
    project=tmp_path/'project';project.mkdir()
    return FleetRegistry(project)


def test_agent_token_is_not_in_project_bytes_and_reopens_from_server_store(tmp_path,monkeypatch):
    from backend.engine.fleet import FleetRegistry
    item=registry(tmp_path,monkeypatch);token='controlled-test-agent-secret-12345'
    target=item.save_target(name='private field',url='https://field.example.com',token=token)
    assert target['credential_storage']=='server_secret_v1'
    assert token.encode() not in item.path.read_bytes()
    with item.connect() as db:reference=db.execute('SELECT token FROM targets').fetchone()[0]
    assert reference.startswith('server-secret:v1:')
    assert FleetRegistry(item.root.parent).secret(target['target_id'])==token
    assert token not in json.dumps(item.targets())
    import shutil
    copied=tmp_path/'copied';shutil.copytree(item.root.parent,copied)
    with pytest.raises(ValueError,match='unavailable|scope'):FleetRegistry(copied).secret(target['target_id'])


@pytest.mark.skipif(os.name=='nt',reason='POSIX private file permissions; Windows uses current-user DPAPI')
@pytest.mark.parametrize('kind',['linked_file','linked_root','readable','hardlink'])
def test_server_secret_store_rejects_untrusted_private_inputs(tmp_path,monkeypatch,kind):
    from backend.engine.fleet import FleetRegistry
    item=registry(tmp_path,monkeypatch);target=item.save_target(name='private',url='https://field.example.com',token='controlled-private-test-token')
    files=list((tmp_path/'server/fleet_secrets').glob('*/*.secret'));assert len(files)==1
    file=files[0]
    if kind=='linked_file':
        other=tmp_path/'original';file.rename(other);file.symlink_to(other)
    elif kind=='linked_root':
        folder=file.parent;other=folder.with_name('original');folder.rename(other);folder.symlink_to(other,target_is_directory=True)
    elif kind=='readable':file.chmod(0o644)
    else:os.link(file,tmp_path/'linked')
    with pytest.raises(ValueError,match='private|linked'):FleetRegistry(item.root.parent).secret(target['target_id'])


def test_changed_secret_scope_and_control_characters_are_refused(tmp_path,monkeypatch):
    item=registry(tmp_path,monkeypatch)
    for token in ['line\nbreak',' leading','\x00control']:
        with pytest.raises(ValueError,match='token'):item.save_target(name='bad',url='https://field.example.com',token=token)
    target=item.save_target(name='bound',url='https://field.example.com',token='controlled-private-test-token')
    if os.name=='nt':return  # Native DPAPI authentication is separate from POSIX JSON mutation.
    file=next((tmp_path/'server/fleet_secrets').glob('*/*.secret'))
    value=json.loads(file.read_text());value['target_id']='f'*32;file.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='scope'):item.secret(target['target_id'])


def test_legacy_credentials_remain_explicit_until_user_reconfiguration_and_events_are_immutable(tmp_path,monkeypatch):
    from backend.engine.fleet import FleetRegistry
    item=registry(tmp_path,monkeypatch);identifier='c'*32;token='controlled-legacy-token'
    with item.connect() as db:db.execute('INSERT INTO targets VALUES(?,?,?,?)',(identifier,'old','https://field.example.com',token))
    assert item.target(identifier)['credential_storage']=='legacy_project_database'
    assert item.secret(identifier)==token
    item.save_target(name='updated',url='https://field.example.com',token='controlled-new-token',target_id=identifier)
    reopened=FleetRegistry(item.root.parent)
    assert reopened.target(identifier)['credential_storage']=='server_secret_v1'
    assert reopened.secret(identifier)=='controlled-new-token'
    with item.connect() as db:
        event=db.execute('SELECT * FROM credential_events').fetchone()
        assert event['policy_version']==1 and event['actor_id']=='local_developer'
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):db.execute('DELETE FROM credential_events')
    assert 'controlled-new-token' not in json.dumps(dict(event))


def test_archive_sanitization_removes_server_references_and_keeps_original_secret(tmp_path,monkeypatch):
    from backend.engine.archive_credentials import sanitize_fleet_database,check_credentials
    import shutil
    item=registry(tmp_path,monkeypatch);target=item.save_target(name='private',url='https://field.example.com',token='controlled-private-test-token')
    copy=tmp_path/'portable.sqlite3';shutil.copyfile(item.path,copy)
    sanitize_fleet_database(copy);check_credentials(copy,'fleet/agents.sqlite3')
    with sqlite3.connect(copy) as db:assert db.execute('SELECT token FROM targets').fetchone()[0]==''
    assert item.secret(target['target_id'])=='controlled-private-test-token'


def test_explicit_legacy_migration_is_atomic_idempotent_and_audited(tmp_path,monkeypatch):
    item=registry(tmp_path,monkeypatch);identifier='d'*32;token='controlled-legacy-migration-token'
    with item.connect() as db:db.execute('INSERT INTO targets VALUES(?,?,?,?)',(identifier,'Legacy','https://field.example.com',token))
    result=item.migrate_legacy_credentials(identifier,reason='Move this configured target to owned server secret storage')
    assert result['credential_storage']=='server_secret_v1' and item.secret(identifier)==token
    assert token.encode() not in item.path.read_bytes()
    files=list((tmp_path/'server/fleet_secrets').glob('*/*.secret'));assert len(files)==1
    repeated=item.migrate_legacy_credentials(identifier,reason='Repeat the same explicit credential migration')
    assert repeated==result and len(list((tmp_path/'server/fleet_secrets').glob('*/*.secret')))==1
    with item.connect() as db:
        events=db.execute('SELECT * FROM credential_events').fetchall()
    assert len(events)==1 and events[0]['event']=='legacy_credential_migrated'
    assert events[0]['reason'].startswith('Move this configured') and token not in json.dumps(dict(events[0]))


def test_failed_legacy_migration_preserves_current_credentials_and_refuses_missing_secret(tmp_path,monkeypatch):
    from backend.engine.fleet_secret_store import ServerSecretStore
    item=registry(tmp_path,monkeypatch);identifier='e'*32;token='controlled-legacy-migration-token'
    with item.connect() as db:db.execute('INSERT INTO targets VALUES(?,?,?,?)',(identifier,'Legacy','https://field.example.com',token))
    save=ServerSecretStore.save
    monkeypatch.setattr(ServerSecretStore,'save',lambda *a:(_ for _ in ()).throw(OSError('controlled write failure')))
    with pytest.raises(OSError,match='write failure'):item.migrate_legacy_credentials(identifier,reason='Keep the prior credential if new storage fails')
    assert item.secret(identifier)==token
    with item.connect() as db:assert db.execute('SELECT count(*) FROM credential_events').fetchone()[0]==0
    monkeypatch.setattr(ServerSecretStore,'save',save)
    item.migrate_legacy_credentials(identifier,reason='Move the exact configured target after storage is available')
    next((tmp_path/'server/fleet_secrets').glob('*/*.secret')).unlink()
    with pytest.raises(ValueError,match='unavailable'):item.migrate_legacy_credentials(identifier,reason='An absent stored reference cannot qualify as successful migration')


def test_server_secret_replacement_during_read_is_refused(tmp_path,monkeypatch):
    item=registry(tmp_path,monkeypatch);target=item.save_target(name='Bound',url='https://field.example.com',token='controlled-replacement-token')
    if os.name=='nt':return
    file=next((tmp_path/'server/fleet_secrets').glob('*/*.secret'));other=file.with_name('replacement.secret')
    other.write_bytes(file.read_bytes());other.chmod(0o600)
    real=os.fstat;count=0
    def replace(descriptor):
        nonlocal count
        result=real(descriptor);count+=1
        if count==2:other.replace(file)
        return result
    monkeypatch.setattr(os,'fstat',replace)
    with pytest.raises(ValueError,match='identity|changed'):item.secret(target['target_id'])


def test_authenticated_owner_migration_refuses_reviewers_and_retains_session_actor(tmp_path,monkeypatch):
    from backend.tests.test_fleet_emergency_rollback import _shared_sessions
    from backend.tests.test_model_deployments import _fixture
    from backend.engine.fleet import FleetRegistry
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'server'))
    _,project,*_=_fixture(tmp_path);sessions,identities=_shared_sessions(tmp_path,project)
    item=FleetRegistry(project['project_dir']);identifier='f'*32;token='controlled-shared-legacy-token'
    with item.connect() as db:db.execute('INSERT INTO targets VALUES(?,?,?,?)',(identifier,'Shared','https://field.example.com',token))
    url='/api/fleet/targets/'+identifier+'/credentials/migrate'
    payload={'reason':'Move configured credentials to this server outside portable project bytes'}
    for role in ('viewer','reviewer'):
        response=sessions[role].post(url,json=payload);assert response.status_code==403,response.text
    response=sessions['owner'].post(url,json={**payload,'actor_id':'forged'});assert response.status_code==422,response.text
    response=sessions['owner'].post(url,json=payload);assert response.status_code==200,response.text
    assert token not in response.text and response.json()['credential_storage']=='server_secret_v1'
    with item.connect() as db:event=db.execute('SELECT * FROM credential_events').fetchone()
    assert event['actor_id']==identities['owner']['id']


def test_two_concurrent_legacy_migrations_publish_one_reference_and_one_audit(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from backend.engine.fleet import FleetRegistry
    item=registry(tmp_path,monkeypatch);identifier='a'*32
    with item.connect() as db:db.execute('INSERT INTO targets VALUES(?,?,?,?)',(identifier,'Legacy','https://field.example.com','controlled-concurrent-legacy-token'))
    def move(_):
        return FleetRegistry(item.root.parent).migrate_legacy_credentials(identifier,reason='Both callers migrate the same current owned target')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(move,range(2)))
    assert results[0]==results[1]
    assert len(list((tmp_path/'server/fleet_secrets').glob('*/*.secret')))==1
    with item.connect() as db:assert db.execute('SELECT count(*) FROM credential_events').fetchone()[0]==1
