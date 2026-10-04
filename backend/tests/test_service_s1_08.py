import json,sqlite3
import pytest
from backend.engine import project_migration as migration


@pytest.fixture(autouse=True)
def isolated_installation(tmp_path, monkeypatch):
    # Other suites deliberately retain synthetic active jobs. A migration must
    # inspect its own installation while still refusing fixtures it owns here.
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'installation'))
    monkeypatch.delenv('VISION_RESOURCE_LEASE_DB', raising=False)


def project(tmp_path):
    root=tmp_path/'project';root.mkdir();(root/'project.json').write_text(json.dumps({'id':'p','name':'legacy','extension':{'kept':True}}))
    for path,data in [('labels/annotation.json',b'{"truth":1}'),('models/checkpoint.bin',b'weights'),('flows/approved.json',b'{"approved":true}')]:
        target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    return root


def test_dryrun_binds_all_preserved_files_and_changes_refuse_apply(tmp_path):
    root=project(tmp_path);preview=migration.preview_migration(root)
    assert preview['inventory']['file_count']==4
    assert preview['source_snapshot']['sha256']
    (root/'labels/annotation.json').write_text('{"truth":2}')
    with pytest.raises(migration.MigrationError,match='source.*changed'):
        migration.apply_migration(root,preview['manifest_sha256'],expected_source_sha256=preview['source_snapshot']['sha256'])
    assert 'schema_version' not in json.loads((root/'project.json').read_text())


def test_backup_restores_only_unchanged_transaction_output(tmp_path):
    root=project(tmp_path);before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    preview=migration.preview_migration(root)
    result=migration.apply_migration(root,preview['manifest_sha256'],expected_source_sha256=preview['source_snapshot']['sha256'])
    assert result['receipt']['backup_verified']['file_count']==4
    assert result['receipt']['status']=='applied'
    (root/'labels/annotation.json').write_text('{"later":true}')
    with pytest.raises(migration.MigrationError,match='changed|writes'):
        migration.restore_migration(root,result['receipt']['migration_id'])
    assert (root/'labels/annotation.json').read_text()=='{"later":true}'


def test_live_job_blocks_migration_without_opening_writer_or_adopting(tmp_path):
    root=project(tmp_path);db=root/'local_jobs.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.execute('create table jobs(job_id text,state text)');conn.execute("insert into jobs values('held','running')")
    preview=migration.preview_migration(root)
    assert not preview['can_apply'] and any('job' in b.lower() for b in preview['blockers'])
    with pytest.raises(migration.MigrationError,match='drain|ownership|job'):
        migration.apply_migration(root,preview['manifest_sha256'])
    assert 'schema_version' not in json.loads((root/'project.json').read_text())


def test_restore_original_then_replay_is_idempotent_and_hash_bound(tmp_path):
    root=project(tmp_path);original=(root/'project.json').read_bytes();preview=migration.preview_migration(root)
    result=migration.apply_migration(root,preview['manifest_sha256'],expected_source_sha256=preview['source_snapshot']['sha256'])
    restored=migration.restore_migration(root,result['receipt']['migration_id'])
    assert restored['status']=='restored' and (root/'project.json').read_bytes()==original
    assert migration.restore_migration(root,result['receipt']['migration_id'])['status']=='restored'


def test_logical_wal_change_invalidates_dryrun_and_backups_include_committed_rows(tmp_path):
    root=project(tmp_path);db=root/'accounts.sqlite'
    conn=sqlite3.connect(db);conn.execute('pragma journal_mode=WAL');conn.execute('create table users(id text,password_hash text)')
    conn.commit();conn.execute('pragma wal_checkpoint(TRUNCATE)')
    first=migration.preview_migration(root)
    conn.execute("insert into users values('fixture-account','fixture-hash')");conn.commit()
    changed=migration.preview_migration(root)
    assert changed['source_snapshot']!=first['source_snapshot']
    assert next(row for row in changed['inventory']['files'] if row['path']=='accounts.sqlite')['tables']['users']['count']==1
    result=migration.apply_migration(root,changed['manifest_sha256'],expected_source_sha256=changed['source_snapshot']['sha256'])
    backed=root/'.migrations'/result['receipt']['migration_id']/'full_backup'/'project'/'accounts.sqlite'
    with sqlite3.connect(backed) as backup:assert backup.execute('select count(*) from users').fetchone()[0]==1
    conn.close()


def test_global_dryrun_never_adopts_tokens_or_uncertain_lease(tmp_path):
    root=tmp_path/'global';root.mkdir();db=root/'resource_leases.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.execute('create table leases(job_id text,owner text,uncertain integer,fence integer)')
        conn.execute("insert into leases values('held','unknown-installation',1,9)")
    (root/'compute_profiles.json').write_text('{"schema_version":1,"profiles":[]}')
    (root/'api-token').write_bytes(b'private-fixture-value')
    auth=root/'accounts.sqlite'
    with sqlite3.connect(auth) as conn:
        conn.execute('create table users(id text,password_hash text)');conn.execute("insert into users values('fixture','fixture-password-hash')")
        conn.execute('create table members(project_id text,user_id text,role text)');conn.execute("insert into members values('p','fixture','owner')")
        conn.execute('create table sessions(token_hash text,user_id text)');conn.execute("insert into sessions values('fixture-session-token-hash','fixture')")
    before={p.name:p.read_bytes() for p in root.iterdir()}
    report=migration.preview_global_migration(root)
    assert report['can_apply'] is False and report['activation_supported'] is False
    assert any('lease' in row.lower() for row in report['blockers'])
    serialized=json.dumps(report)
    assert 'private-fixture-value' not in serialized and 'fixture-password-hash' not in serialized and 'fixture-session-token-hash' not in serialized
    tables=next(row for row in report['inventory']['files'] if row['path']=='accounts.sqlite')['tables']
    assert tables['users']['count']==tables['members']['count']==tables['sessions']['count']==1
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before


def test_maintenance_guard_rejects_existing_writer_and_blocks_new_api_mutations(tmp_path):
    from backend.engine.migration_guard import maintenance_guard,ProjectMaintenanceMiddleware
    from types import SimpleNamespace
    from starlette.testclient import TestClient
    from starlette.responses import JSONResponse
    root=project(tmp_path);preview=migration.preview_migration(root)
    with maintenance_guard(root):
        with pytest.raises(migration.MigrationError,match='writers|drain'):
            migration.apply_migration(root,preview['manifest_sha256'])
    seen=[]
    async def app(scope,receive,send):
        seen.append(scope['method']);await JSONResponse({'ok':True})(scope,receive,send)
    guarded=ProjectMaintenanceMiddleware(app,SimpleNamespace(state=SimpleNamespace(current_project={'project_dir':str(root)})))
    client=TestClient(guarded)
    with maintenance_guard(root,exclusive=True):
        assert client.post('/api/dataset/mutate').status_code==423
        assert client.get('/api/dataset/read').status_code==200
    assert client.post('/api/dataset/mutate').status_code==200
    assert seen==['GET','POST']


def test_prepared_cutover_restart_recovers_only_verified_output(tmp_path,monkeypatch):
    root=project(tmp_path);writer=migration._atomic_json
    def fail(path,value):
        if path.name=='journal.json' and value['status']=='applied':raise OSError('fixture receipt crash')
        return writer(path,value)
    monkeypatch.setattr(migration,'_atomic_json',fail)
    with pytest.raises(migration.MigrationError,match='receipt crash'):migration.apply_migration(root)
    assert json.loads((root/'project.json').read_bytes())['schema_version']==1
    monkeypatch.setattr(migration,'_atomic_json',writer)
    recovered=migration.apply_migration(root)
    assert recovered['receipt']['recovered'] and recovered['receipt']['status']=='applied'
    assert recovered['receipt']['backup_verified']['file_count']==4


def test_scoped_api_requires_full_preview_binding_and_exposes_actionable_recovery(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from backend.api import routes_project
    root=project(tmp_path);request=SimpleNamespace(state=SimpleNamespace(account_user=None))
    preview=routes_project.compatibility_preview(routes_project.ProjectOpenRequest(project_dir=str(root)),request)
    req=routes_project.CompatibilityApplyRequest(project_dir=str(root),expected_manifest_sha256=preview['manifest_sha256'],expected_source_sha256=preview['source_snapshot']['sha256'])
    applied=routes_project.compatibility_apply(req,request)
    status=routes_project.compatibility_preview(routes_project.ProjectOpenRequest(project_dir=str(root)),request)
    assert status['transactions'][0]['can_restore']
    recovery=routes_project.CompatibilityRecoveryRequest(project_dir=str(root),migration_id=applied['receipt']['migration_id'],action='restore')
    assert routes_project.compatibility_recover(recovery,request)['status']=='restored'


def test_interrupted_restore_finalizes_exact_original_without_rewriting(tmp_path,monkeypatch):
    root=project(tmp_path);result=migration.apply_migration(root);writer=migration._atomic_json
    def fail(path,value):
        if path.name=='journal.json' and value['status']=='restored':raise OSError('fixture restore receipt crash')
        return writer(path,value)
    monkeypatch.setattr(migration,'_atomic_json',fail)
    with pytest.raises(migration.MigrationError,match='restore receipt crash'):
        migration.restore_migration(root,result['receipt']['migration_id'])
    original=(root/'project.json').read_bytes()
    monkeypatch.setattr(migration,'_atomic_json',writer)
    recovered=migration.restore_migration(root,result['receipt']['migration_id'])
    assert recovered['status']=='restored' and recovered['receipt']['recovered']
    assert (root/'project.json').read_bytes()==original


def test_global_job_journal_blocks_project_cutover_without_worker_adoption(tmp_path,monkeypatch):
    root=project(tmp_path);global_root=tmp_path/'installation';jobs=global_root/'local_jobs';jobs.mkdir(parents=True)
    journal=jobs/'held.json';journal.write_text(json.dumps({'job_id':'held','state':'running','worker_pid':12345,'fencing_token':9}))
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(global_root))
    before=journal.read_bytes();preview=migration.preview_migration(root)
    assert not preview['can_apply'] and any('Drain jobs' in row for row in preview['blockers'])
    with pytest.raises(migration.MigrationError,match='Drain jobs'):migration.apply_migration(root)
    assert journal.read_bytes()==before and 'schema_version' not in json.loads((root/'project.json').read_bytes())


def test_actual_main_auth_context_and_writer_admission(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.engine.migration_guard import maintenance_guard
    monkeypatch.setenv('VISION_AI_STUDIO_API_TOKEN','fixture-process-token')
    app=create_app(project_dir=str(tmp_path/'projects'));client=TestClient(app,headers={'X-Vision-Token':'fixture-process-token'})
    created=client.post('/api/project/create',json={'name':'Migration QA','task':'classification'})
    assert created.status_code==200,created.text
    root=__import__('pathlib').Path(created.json()['project_dir'])
    with maintenance_guard(root,exclusive=True):
        assert client.put('/api/project/update',json={'description':'blocked'}).status_code==423
        # Exact read-only compatibility handler is not accidentally blocked by ordinary writer admission.
        response=client.post('/api/project/compatibility/preview',json={'project_dir':str(root)})
        assert response.status_code==200,response.text
    assert client.put('/api/project/update',json={'description':'accepted'}).status_code==200
    assert TestClient(app).put('/api/project/update',json={'description':'unauthorized'}).status_code==401


def test_actual_http_concurrent_writer_and_cutover_have_exclusive_admission(tmp_path,monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.engine import migration_inventory
    monkeypatch.setenv('VISION_AI_STUDIO_API_TOKEN','fixture-process-token')
    app=create_app(project_dir=str(tmp_path/'projects'));client=TestClient(app,headers={'X-Vision-Token':'fixture-process-token'})
    created=client.post('/api/project/create',json={'name':'Concurrent migration QA','task':'classification'});assert created.status_code==200
    root=__import__('pathlib').Path(created.json()['project_dir']);manifest=json.loads((root/'project.json').read_bytes());manifest.pop('schema_version');(root/'project.json').write_text(json.dumps(manifest))
    preview=client.post('/api/project/compatibility/preview',json={'project_dir':str(root)}).json()
    writer_entered=threading.Event();writer_release=threading.Event()
    @app.post('/api/fixture-held-writer')
    async def held_writer():
        writer_entered.set();assert await __import__('asyncio').to_thread(writer_release.wait,5)
        return {'completed':True}
    result={}
    thread=threading.Thread(target=lambda:result.update(writer=client.post('/api/fixture-held-writer')));thread.start();assert writer_entered.wait(5)
    body={'project_dir':str(root),'expected_manifest_sha256':preview['manifest_sha256'],'expected_source_sha256':preview['source_snapshot']['sha256']}
    rejected=client.post('/api/project/compatibility/apply',json=body)
    assert rejected.status_code==409 and 'writers' in rejected.json()['detail']
    assert 'schema_version' not in json.loads((root/'project.json').read_bytes())
    writer_release.set();thread.join(5);assert not thread.is_alive() and result['writer'].status_code==200
    backup_entered=threading.Event();backup_release=threading.Event();original=migration_inventory.verified_backup
    def paused(*args,**kwargs):
        backup_entered.set();assert backup_release.wait(5);return original(*args,**kwargs)
    monkeypatch.setattr(migration_inventory,'verified_backup',paused)
    apply_thread=threading.Thread(target=lambda:result.update(apply=client.post('/api/project/compatibility/apply',json=body)))
    apply_thread.start();assert backup_entered.wait(5)
    blocked=client.put('/api/project/update',json={'description':'must not enter cutover'})
    assert blocked.status_code==423
    backup_release.set();apply_thread.join(5);assert not apply_thread.is_alive()
    assert result['apply'].status_code==200,result['apply'].text
    assert result['apply'].json()['receipt']['status']=='applied'


def test_relative_external_source_is_included_in_verified_backup_and_restore_binding(tmp_path):
    root=project(tmp_path);source=tmp_path/'capture';source.mkdir();(source/'source.bin').write_bytes(b'fixture pixels')
    manifest=json.loads((root/'project.json').read_bytes());manifest['source_dataset_dir']='../capture';(root/'project.json').write_text(json.dumps(manifest))
    preview=migration.preview_migration(root);assert preview['scopes']['source']['file_count']==1
    applied=migration.apply_migration(root,preview['manifest_sha256'],expected_source_sha256=preview['source_snapshot']['sha256'])
    assert applied['receipt']['backup_verified']['file_count']==5
    (source/'source.bin').write_bytes(b'later capture')
    with pytest.raises(migration.MigrationError,match='changed|writes'):
        migration.restore_migration(root,applied['receipt']['migration_id'])
    assert (source/'source.bin').read_bytes()==b'later capture'


def test_opening_another_project_cannot_bypass_target_maintenance(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.engine.migration_guard import maintenance_guard
    from backend.tests.test_project_migration import manifest
    monkeypatch.setenv('VISION_AI_STUDIO_API_TOKEN','fixture-process-token')
    app=create_app(project_dir=str(tmp_path/'projects'));client=TestClient(app,headers={'X-Vision-Token':'fixture-process-token'})
    assert client.post('/api/project/create',json={'name':'Selected','task':'classification'}).status_code==200
    other=tmp_path/'other';manifest(other);original=(other/'project.json').read_bytes()
    with maintenance_guard(other,exclusive=True):
        response=client.post('/api/project/open',json={'project_dir':str(other)})
        assert response.status_code==423,response.text
    assert (other/'project.json').read_bytes()==original
    assert client.post('/api/project/open',json={'project_dir':str(other)}).status_code==200


def test_unreadable_sqlite_schema_is_actionable_before_cutover(tmp_path):
    root=project(tmp_path);(root/'jobs.sqlite3').write_bytes(b'SQLite format 3\x00'+b'broken'*20)
    original=(root/'project.json').read_bytes()
    with pytest.raises(migration.MigrationError,match='SQLite'):
        migration.preview_migration(root)
    assert (root/'project.json').read_bytes()==original and not (root/'.migrations').exists()


def test_external_lease_pointer_blocks_even_without_default_global_store(tmp_path,monkeypatch):
    root=project(tmp_path);monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'absent-installation'))
    monkeypatch.setenv('VISION_RESOURCE_LEASE_DB',str(tmp_path/'external-leases.sqlite3'))
    preview=migration.preview_migration(root)
    assert not preview['can_apply'] and any('external resource lease' in row for row in preview['blockers'])
