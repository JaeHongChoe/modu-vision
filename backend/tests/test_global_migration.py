"""Owned drained installation generations; no installed or active worker adoption."""
import hashlib,json,sqlite3
from pathlib import Path
import pytest

def owned(tmp_path):
    from backend.engine.global_migration import initialize_owned
    from backend.engine.job_store import JobStore
    from backend.engine.shared_accounts import AccountStore
    from backend.contracts.context import ContextRegistry
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.remote.profiles import ProfileStore
    root=tmp_path/'owned'; root.mkdir()
    scopes={'ledger':'jobs/ledger.sqlite3','leases':'resource_leases.sqlite3','profiles':'compute_profiles.json',
            'accounts':'auth/accounts.sqlite','context':'projects/.context.sqlite3',
            'local_journals':'local_jobs','remote_journals':'remote_jobs'}
    initialize_owned(root,scopes=scopes)
    ledger=JobStore(root/scopes['ledger']); registry=ContextRegistry(root/'projects')
    account=AccountStore(root/scopes['accounts']); actor=account.bootstrap('fixture-admin','fixture-password-123')
    account.bind_workspace(registry.workspace_id)
    ResourceLeases(root/scopes['leases'])
    (root/scopes['profiles']).write_text(json.dumps({'profiles':[],'selected':None}),encoding='utf-8')
    (root/'projects/labels.json').write_bytes(b'{"label":"original"}')
    return root,scopes,ledger,registry,account,actor

def test_owned_preview_and_activation_preserve_identity_and_revoke_copied_sessions(tmp_path):
    from backend.engine.global_migration import preview,apply
    from backend.engine.global_store_paths import resolve_store_path
    root,scopes,ledger,registry,account,actor=owned(tmp_path)
    session=account.login('fixture-admin','fixture-password-123')
    before=(root/scopes['accounts']).read_bytes()
    p=preview(root); assert p['can_apply']
    result=apply(root,expected_source_sha256=p['source_sha256'])
    assert result['status']=='applied'
    assert (root/scopes['accounts']).read_bytes()==before
    target=resolve_store_path(root/scopes['accounts']);assert target!=root/scopes['accounts']
    from backend.engine.shared_accounts import AccountStore
    migrated=AccountStore(root/scopes['accounts']);assert migrated.users()[0]['id']==actor['id']
    with pytest.raises(ValueError,match='expired|unavailable'):migrated.authenticate(session['token'])
    assert migrated.login('fixture-admin','fixture-password-123')['user']['id']==actor['id']
    assert (root/'projects/labels.json').read_bytes()==b'{"label":"original"}'
    assert resolve_store_path(root/scopes['ledger']).parent.parent==target.parent.parent

def test_active_jobs_and_leases_refuse_before_pointer_or_generation(tmp_path):
    from backend.engine.global_migration import preview,apply,GlobalMigrationError
    root,scopes,*_=owned(tmp_path)
    with sqlite3.connect(root/scopes['ledger']) as db:
        db.execute("INSERT INTO jobs(id,workspace_id,project_key,project_id,actor_id,mode,kind,spec_sha256,spec_json,state,revision,source,created_ns,updated_ns) VALUES('j','w','p','p','a','local','training','h','{}','running',1,'fixture',1,1)")
    p=preview(root);assert not p['can_apply']
    with pytest.raises(GlobalMigrationError):apply(root,expected_source_sha256=p['source_sha256'])
    assert not (root/'global-active.json').exists()
    assert not (root/'.global-generations').exists()

def test_source_cas_and_later_target_write_refuse_recovery(tmp_path):
    from backend.engine.global_migration import preview,apply,recover,GlobalMigrationError
    from backend.engine.global_store_paths import resolve_store_path
    root,scopes,*_=owned(tmp_path);p=preview(root)
    (root/'projects/labels.json').write_bytes(b'changed source')
    with pytest.raises(GlobalMigrationError,match='changed'):apply(root,expected_source_sha256=p['source_sha256'])
    result=apply(root,expected_source_sha256=preview(root)['source_sha256'])
    target=resolve_store_path(root/scopes['profiles']);target.write_text('{"profiles":[],"selected":null,"new_write":true}',encoding='utf-8')
    with pytest.raises(GlobalMigrationError,match='write|changed'):recover(root,result['migration_id'],action='restore')
    assert json.loads(target.read_text())['new_write']

def test_foreign_owner_and_unknown_schema_refuse(tmp_path):
    from backend.engine.global_migration import initialize_owned,preview,GlobalMigrationError
    root,scopes,*_=owned(tmp_path)
    with sqlite3.connect(root/scopes['ledger']) as db:db.execute('CREATE TABLE arbitrary_execution_authority(token TEXT)')
    assert not preview(root)['can_apply']
    nonempty=tmp_path/'installed';nonempty.mkdir();(nonempty/'userData').write_bytes(b'existing')
    with pytest.raises(GlobalMigrationError):initialize_owned(nonempty,scopes=scopes)

def test_mismatched_account_authority_is_not_silently_rebound(tmp_path):
    from backend.engine.global_migration import preview
    root,scopes,*_=owned(tmp_path)
    with sqlite3.connect(root/scopes['accounts']) as db:
        db.execute("UPDATE account_meta SET value='foreign' WHERE name='workspace_id'")
    before=(root/scopes['accounts']).read_bytes()
    assert not preview(root)['can_apply']
    assert (root/scopes['accounts']).read_bytes()==before

def test_stale_store_cannot_mutate_original_after_cutover(tmp_path):
    from backend.engine.global_migration import preview,apply
    root,scopes,ledger,registry,account,actor=owned(tmp_path)
    result=apply(root,expected_source_sha256=preview(root)['source_sha256'])
    before=(root/scopes['accounts']).read_bytes()
    with pytest.raises(ValueError,match='restart|generation'):
        account.create_user('late-user','fixture-password-456')
    assert (root/scopes['accounts']).read_bytes()==before

def test_second_apply_requires_fresh_generation_source(tmp_path):
    from backend.engine.global_migration import preview,apply,GlobalMigrationError
    from backend.engine.global_store_paths import resolve_store_path
    root,scopes,*_=owned(tmp_path)
    apply(root,expected_source_sha256=preview(root)['source_sha256'])
    from backend.engine.shared_accounts import AccountStore
    fresh=AccountStore(root/scopes['accounts'])
    new=fresh.create_user('new-current','fixture-password-456')
    pointer=(root/'global-active.json').read_bytes()
    with pytest.raises(GlobalMigrationError,match='active|generation'):
        apply(root,expected_source_sha256=preview(root)['source_sha256'])
    assert (root/'global-active.json').read_bytes()==pointer
    assert new['id'] in {r['id'] for r in fresh.users()}

def test_fresh_backend_is_blocked_before_constructors_during_maintenance(tmp_path,monkeypatch):
    from backend.engine.global_store_paths import store_admission
    from backend.main import create_app
    import threading
    root,scopes,*_=owned(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    failures=[]
    def start():
        try:create_app(project_dir=str(root/'projects'),shared_auth_dir=str(root/'auth'))
        except Exception as exc:failures.append(exc)
    with store_admission(root,exclusive=True):
        thread=threading.Thread(target=start);thread.start();thread.join(timeout=5)
        assert not thread.is_alive()
    assert failures and 'admission' in str(failures[0])
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}==before

def test_owned_current_generation_fresh_backend_uses_same_five_stores(tmp_path,monkeypatch):
    from backend.engine.global_migration import preview,apply
    from backend.engine.global_store_paths import resolve_store_path
    from backend.engine.job_store import ledger
    from backend.engine.shared_scheduler import shared_leases
    from backend.remote.profiles import get_profile_store
    from backend.main import create_app
    root,scopes,*_=owned(tmp_path)
    apply(root,expected_source_sha256=preview(root)['source_sha256'])
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    app=create_app(project_dir=str(root/'projects'),shared_auth_dir=str(root/'auth'))
    paths=[app.state.accounts.path,app.state.context_registry.path,ledger().path,shared_leases().path,get_profile_store().path]
    assert paths==[resolve_store_path(root/scopes[k]) for k in ('accounts','context','ledger','leases','profiles')]
    assert len({p.relative_to(root).parts[1] for p in paths})==1

@pytest.mark.parametrize('after_pointer',[False,True])
def test_prepared_crash_finishes_exact_generation_without_source_changes(tmp_path,monkeypatch,after_pointer):
    from backend.engine import global_migration as migration
    root,scopes,*_=owned(tmp_path);before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    publish=migration._publish;writer=migration.atomic_private_json
    if after_pointer:
        def fail(path,value):
            if path.name=='journal.json' and value.get('status')=='applied':raise OSError('controlled receipt interruption')
            return writer(path,value)
        monkeypatch.setattr(migration,'atomic_private_json',fail)
    else:
        monkeypatch.setattr(migration,'_publish',lambda *a,**k:(_ for _ in ()).throw(OSError('controlled cutover interruption')))
    with pytest.raises(OSError,match='interruption'):migration.apply(root,expected_source_sha256=migration.preview(root)['source_sha256'])
    journal=next((root/'.global-migrations').glob('*/journal.json'));identifier=journal.parent.name
    assert all((root/name).read_bytes()==content for name,content in before.items())
    monkeypatch.setattr(migration,'_publish',publish);monkeypatch.setattr(migration,'atomic_private_json',writer)
    result=migration.recover(root,identifier,action='finish');assert result['status']=='applied'
    pointer=(root/'global-active.json').read_bytes()
    assert migration.recover(root,identifier,action='finish')['status']=='applied'
    assert (root/'global-active.json').read_bytes()==pointer
    assert all((root/name).read_bytes()==content for name,content in before.items())

def test_restore_keeps_original_authority_revoked_and_preserves_project_bytes(tmp_path):
    from backend.engine.global_migration import preview,apply,recover
    from backend.engine.shared_accounts import AccountStore
    root,scopes,ledger,registry,account,actor=owned(tmp_path)
    session=account.login('fixture-admin','fixture-password-123')
    result=apply(root,expected_source_sha256=preview(root)['source_sha256'])
    restored=recover(root,result['migration_id'],action='restore');assert restored['fence']==2
    fresh=AccountStore(root/scopes['accounts'])
    with pytest.raises(ValueError,match='expired|unavailable'):fresh.authenticate(session['token'])
    assert fresh.login('fixture-admin','fixture-password-123')['user']['id']==actor['id']
    assert (root/'projects/labels.json').read_bytes()==b'{"label":"original"}'

def test_historical_journal_and_partial_startup_paths_refused(tmp_path,monkeypatch):
    from backend.engine.global_migration import preview
    from backend.main import create_app
    root,scopes,*_=owned(tmp_path)
    directory=root/scopes['local_journals'];directory.mkdir()
    (directory/'unbound.json').write_text('{"job_id":"old","state":"completed"}')
    assert not preview(root)['can_apply']
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='scopes'):create_app(project_dir=str(root/'other-projects'),shared_auth_dir=str(root/'auth'))
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}==before

@pytest.mark.parametrize('scope,value',[('profiles',None),('context','renamed/registry.sqlite'),('leases','auth/accounts.sqlite')])
def test_unsupported_scope_descriptor_refused_without_marker(tmp_path,scope,value):
    from backend.engine.global_migration import initialize_owned,GlobalMigrationError
    root,scopes,*_=owned(tmp_path);empty=tmp_path/'fresh';empty.mkdir();scopes[scope]=value
    with pytest.raises(GlobalMigrationError):initialize_owned(empty,scopes=scopes)
    assert list(empty.iterdir())==[]

@pytest.mark.parametrize('name',['.global-generations','.global-migrations'])
def test_control_parent_link_refused_before_external_write(tmp_path,name):
    from backend.engine.global_migration import preview,apply,GlobalMigrationError
    root,scopes,*_=owned(tmp_path);before=preview(root)
    external=tmp_path/'external';external.mkdir();(external/'sentinel').write_bytes(b'untouched')
    (root/name).symlink_to(external,target_is_directory=True)
    with pytest.raises((GlobalMigrationError,ValueError),match='link'):apply(root,expected_source_sha256=before['source_sha256'])
    assert {p.name:p.read_bytes() for p in external.iterdir()}=={'sentinel':b'untouched'}
    assert not (root/'global-active.json').exists()

def test_invalid_pointer_fence_refuses_store_attachment(tmp_path):
    from backend.engine.global_migration import preview,apply
    from backend.engine.global_store_paths import resolve_store_path
    root,scopes,*_=owned(tmp_path);apply(root,expected_source_sha256=preview(root)['source_sha256'])
    pointer=root/'global-active.json';record=json.loads(pointer.read_bytes());record['fence']=False;pointer.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='pointer|fence'):resolve_store_path(root/scopes['accounts'])

def test_terminal_ledger_preserves_only_original_actor_namespace(tmp_path):
    from backend.engine.global_migration import preview
    from backend.contracts.context import ProjectContext
    root,scopes,ledger,registry,account,actor=owned(tmp_path)
    project=root/'projects/p';project.mkdir();key=registry.register_project({'id':'p','project_dir':str(project)})
    context=ProjectContext(workspace_id=registry.workspace_id,project_id='p',actor_id=registry.local_actor_id,mode='local')
    ref=ledger.submit(context,key,'fixture',{},job_id='terminal-fixture');
    with sqlite3.connect(root/scopes['ledger']) as db:db.execute("UPDATE jobs SET state='completed' WHERE id=?",(ref.id,))
    assert preview(root)['can_apply']
    with sqlite3.connect(root/scopes['ledger']) as db:db.execute("UPDATE jobs SET actor_id='foreign' WHERE id=?",(ref.id,))
    assert not preview(root)['can_apply']

def test_explicit_cli_preview_apply_finish_keeps_credentials_out_of_output(tmp_path):
    import subprocess,sys
    root,scopes,*_=owned(tmp_path)
    def invoke(*args):
        result=subprocess.run([sys.executable,'-m','backend.engine.global_migration',*args,'--root',str(root)],cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr+result.stdout
        assert 'fixture-password' not in result.stdout
        return json.loads(result.stdout)
    before=invoke('preview');assert before['can_apply']
    applied=invoke('apply','--expected-source-sha256',before['source_sha256'])
    assert applied['status']=='applied'
    assert invoke('recover','--migration-id',applied['migration_id'],'--action','finish')['status']=='applied'

def test_linked_declared_control_file_cannot_be_attached(tmp_path):
    from backend.remote.profiles import ProfileStore
    root,scopes,*_=owned(tmp_path)
    outside=tmp_path/'outside-profiles.json';outside.write_text('{"profiles":[],"selected":null}')
    original=root/scopes['profiles'];original.rename(original.with_suffix('.preserved'))
    original.symlink_to(outside)
    with pytest.raises(ValueError,match='link'):ProfileStore(original).list()
    assert outside.read_text()=='{"profiles":[],"selected":null}'

def test_forward_generation_preserves_new_writes_and_never_reactivates_old_sessions(tmp_path):
    from backend.engine.global_migration import preview,apply,preview_forward,advance,recover,GlobalMigrationError
    from backend.engine.global_store_paths import resolve_store_path
    from backend.engine.shared_accounts import AccountStore
    root,scopes,*_=owned(tmp_path)
    first=apply(root,expected_source_sha256=preview(root)['source_sha256'])
    account=AccountStore(root/scopes['accounts'])
    added=account.create_user('post-cutover-user','fixture-password-456')
    session=account.login('fixture-admin','fixture-password-123')
    prior_path=resolve_store_path(root/scopes['accounts']);prior_bytes=prior_path.read_bytes()
    review=preview_forward(root);assert review['can_apply']
    second=advance(root,expected_source_sha256=review['source_sha256'])
    assert second['fence']==2
    assert prior_path.read_bytes()==prior_bytes
    fresh=AccountStore(root/scopes['accounts']);assert added['id'] in {r['id'] for r in fresh.users()}
    with pytest.raises(ValueError,match='expired|unavailable'):fresh.authenticate(session['token'])
    with pytest.raises(ValueError,match='restart|generation'):account.create_user('retired-write','fixture-password-456')
    # Once the new generation has written, rolling back cannot lose that user.
    fresh.create_user('latest-user','fixture-password-456')
    with pytest.raises(GlobalMigrationError,match='writes|changed|forward'):recover(root,second['migration_id'],action='restore')
    assert added['id'] in {r['id'] for r in fresh.users()}


def test_forward_generation_refuses_stale_preview_and_live_authority(tmp_path):
    from backend.engine.global_migration import preview,apply,preview_forward,advance,GlobalMigrationError
    from backend.engine.global_store_paths import resolve_store_path
    root,scopes,*_=owned(tmp_path);apply(root,expected_source_sha256=preview(root)['source_sha256'])
    review=preview_forward(root)
    resolve_store_path(root/scopes['profiles']).write_text('{"profiles":[],"selected":null,"new":true}')
    with pytest.raises(GlobalMigrationError,match='changed'):advance(root,expected_source_sha256=review['source_sha256'])
    # Current-generation drain validation still applies after user writes.
    ledger=resolve_store_path(root/scopes['ledger'])
    with sqlite3.connect(ledger) as db:
        db.execute("INSERT INTO jobs(id,workspace_id,project_key,project_id,actor_id,mode,kind,spec_sha256,spec_json,state,revision,source,created_ns,updated_ns) VALUES('j','w','p','p','a','local','training','h','{}','running',1,'fixture',1,1)")
    blocked=preview_forward(root);assert not blocked['can_apply']
    pointer=(root/'global-active.json').read_bytes()
    with pytest.raises(GlobalMigrationError):advance(root,expected_source_sha256=blocked['source_sha256'])
    assert (root/'global-active.json').read_bytes()==pointer

def test_prepared_forward_recovery_finishes_exact_snapshot_and_restore_sanitizes(tmp_path,monkeypatch):
    from backend.engine import global_migration as migration
    from backend.engine.global_store_paths import resolve_store_path
    from backend.engine.shared_accounts import AccountStore
    root,scopes,*_=owned(tmp_path)
    migration.apply(root,expected_source_sha256=migration.preview(root)['source_sha256'])
    account=AccountStore(root/scopes['accounts'])
    user=account.create_user('preserved-forward-user','fixture-password-456')
    session=account.login('fixture-admin','fixture-password-123')
    before=set(p.name for p in (root/'.global-migrations').iterdir())
    with monkeypatch.context() as patch:
        patch.setattr(migration,'_publish',lambda *a,**k:(_ for _ in ()).throw(OSError('injected before pointer cutover')))
        with pytest.raises(OSError):migration.advance(root,expected_source_sha256=migration.preview_forward(root)['source_sha256'])
    new=set(p.name for p in (root/'.global-migrations').iterdir())-before;assert len(new)==1
    identifier=new.pop()
    assert migration.recover(root,identifier,action='finish')['status']=='applied'
    restored=migration.recover(root,identifier,action='restore');assert restored['fence']==3
    fresh=AccountStore(root/scopes['accounts']);assert user['id'] in {r['id'] for r in fresh.users()}
    with pytest.raises(ValueError,match='expired|unavailable'):fresh.authenticate(session['token'])
    assert (root/'projects/labels.json').read_bytes()==b'{"label":"original"}'
