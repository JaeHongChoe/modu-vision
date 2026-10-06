"""Actual SQLite predecessor objects retain history, authority and original bytes."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from backend.tests.test_global_migration import owned

GOLDEN=json.loads((Path(__file__).parent/'fixtures/historical-control-schemas.json').read_text())


def original(tmp_path):
    root,scopes,ledger,registry,account,actor=owned(tmp_path)
    project_dir=root/'projects/old-registered';project_dir.mkdir()
    project={'id':'old-registered','workspace_id':registry.workspace_id,'project_dir':str(project_dir)}
    (project_dir/'project.json').write_text(json.dumps(project))
    key=registry.register_project(project)
    for version in GOLDEN['versions']:
        path=root/scopes[version['scope']];path.unlink()
        with sqlite3.connect(path) as db:
            for kind,_,_,sql in sorted(version['schema'],key=lambda r:r[0]!='table'):db.execute(sql)
            if version['scope']=='ledger':
                from backend.engine.job_store import spec_digest
                db.execute('INSERT INTO jobs(id,workspace_id,project_key,project_id,actor_id,mode,kind,spec_sha256,spec_json,state,revision,source,created_ns,updated_ns,priority,wait_reason,registry_root,project_dir) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('job_old',registry.workspace_id,key,project['id'],registry.local_actor_id,'local','training',spec_digest({'original':True}),'{"original":true}','completed',3,'original-owned-control',1,2,7,'retained old annotation',str(root/'projects'),str(project_dir)))
                db.execute("INSERT INTO attempts(job_id,number,fencing_token,executor,started_ns,ended_ns) VALUES('job_old',1,9,'original-executor',1,2)")
                db.execute("INSERT INTO events VALUES('job_old',1,3,'finish','running','completed','{}',2)")
            else:db.execute("INSERT INTO devices VALUES('original-host','cpu','original-cpu',NULL,1024)")
    session=account.login('fixture-admin','fixture-password-123')
    files=[root/p for name,p in scopes.items() if name not in {'local_journals','remote_journals'}]+[project_dir/'project.json']
    pins={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return root,scopes,registry,actor,session,pins


def unchanged(pins):
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in pins.items())


def test_exact_drained_predecessors_preserve_history_and_originals_through_cutover_restore_and_forward(tmp_path):
    from backend.engine import global_migration as m
    from backend.engine.global_store_paths import resolve_store_path
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.engine.shared_accounts import AccountStore
    root,scopes,registry,actor,session,pins=original(tmp_path)
    preview=m.preview(root);assert preview['can_apply'],preview['blockers'];unchanged(pins)
    assert set(preview['schema_conversions'])=={'ledger','leases'}
    applied=m.apply(root,expected_source_sha256=preview['source_sha256']);unchanged(pins)
    current=JobStore(root/scopes['ledger']);row=current.record('job_old')
    assert (row['revision'],row['priority'],row['wait_reason'])==(3,7,'retained old annotation')
    assert row['operation_json'] is None and current.attempts('job_old')[0]['fencing_token']==9
    assert len(current.attempts('job_old'))==1 and ResourceLeases(root/scopes['leases']).list()==[]
    accounts=AccountStore(root/scopes['accounts'])
    with pytest.raises(ValueError,match='expired|unavailable'):accounts.authenticate(session['token'])
    assert accounts.login('fixture-admin','fixture-password-123')['user']['id']==actor['id']
    # Login is a new normal target write: restore must no longer overwrite it.
    with pytest.raises(m.GlobalMigrationError,match='write|changed'):m.recover(root,applied['migration_id'],action='restore')
    forward=m.preview_forward(root);assert forward['can_apply'],forward['blockers']
    m.advance(root,expected_source_sha256=forward['source_sha256']);unchanged(pins)
    assert JobStore(root/scopes['ledger']).record('job_old')['priority']==7
    assert ResourceLeases(root/scopes['leases']).devices()[0]['uuid']=='original-cpu'
    assert resolve_store_path(root/scopes['ledger'])!=root/scopes['ledger']


def test_verified_predecessor_backup_restores_to_current_schema_without_session_authority(tmp_path):
    from backend.engine import global_migration as m
    from backend.engine.job_store import JobStore
    from backend.engine.shared_accounts import AccountStore
    root,scopes,registry,actor,session,pins=original(tmp_path)
    p=m.preview(root);assert p['can_apply'],p['blockers']
    first=m.apply(root,expected_source_sha256=p['source_sha256']);restored=m.recover(root,first['migration_id'],action='restore')
    assert restored['status']=='restored';unchanged(pins)
    assert JobStore(root/scopes['ledger']).record('job_old')['wait_reason']=='retained old annotation'
    with pytest.raises(ValueError,match='expired|unavailable'):AccountStore(root/scopes['accounts']).authenticate(session['token'])
    assert m.preview_forward(root)['can_apply']


def test_prepared_predecessor_cutover_can_finish_after_observed_publish_failure(tmp_path,monkeypatch):
    from backend.engine import global_migration as m
    root,scopes,registry,actor,session,pins=original(tmp_path);p=m.preview(root)
    assert p['can_apply'],p['blockers'];publish=m._publish
    monkeypatch.setattr(m,'_publish',lambda *a,**kw:(_ for _ in ()).throw(OSError('Owned controlled pointer interruption')))
    with pytest.raises(OSError,match='interruption'):m.apply(root,expected_source_sha256=p['source_sha256'])
    assert not (root/'global-active.json').exists();unchanged(pins)
    journal=next((root/'.global-migrations').glob('*/journal.json'));record=json.loads(journal.read_text())
    assert record['status']=='prepared';monkeypatch.setattr(m,'_publish',publish)
    assert m.recover(root,record['migration_id'],action='finish')['status']=='applied';unchanged(pins)
    assert m.preview_forward(root)['can_apply']


@pytest.mark.parametrize('damage',['extra_table','extra_trigger','extra_column','foreign_actor','open_attempt','uncertain_lease','changed_source'])
def test_predecessor_conversion_refuses_unknown_or_active_authority_before_activation(tmp_path,damage):
    from backend.engine import global_migration as m
    root,scopes,registry,actor,session,pins=original(tmp_path)
    initial=m.preview(root)
    assert initial['can_apply'],initial['blockers']
    with sqlite3.connect(root/scopes['ledger']) as db:
        if damage=='extra_table':db.execute('CREATE TABLE unreviewed_authority(token TEXT)')
        elif damage=='extra_trigger':db.execute('CREATE TRIGGER unreviewed_side_effect AFTER UPDATE ON jobs BEGIN DELETE FROM events; END')
        elif damage=='extra_column':db.execute('ALTER TABLE jobs ADD COLUMN unreviewed TEXT')
        elif damage=='foreign_actor':db.execute("UPDATE jobs SET actor_id='foreign'")
        elif damage=='open_attempt':db.execute('UPDATE attempts SET ended_ns=NULL')
        elif damage=='changed_source':db.execute("UPDATE jobs SET wait_reason='write after preview'")
    if damage=='uncertain_lease':
        with sqlite3.connect(root/scopes['leases']) as db:db.execute("INSERT INTO leases(job_id,host,selector,owner,expires,remote,uncertain) VALUES('job_old','original-host','all','old-owner',9999999999,1,1)")
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in pins};latest=m.preview(root)
    if damage!='changed_source':assert not latest['can_apply'] and latest['blockers']
    with pytest.raises(m.GlobalMigrationError):m.apply(root,expected_source_sha256=initial['source_sha256'])
    assert not (root/'global-active.json').exists() and not (root/'.global-generations').exists();unchanged(before)


def test_private_converter_refuses_original_or_active_generation_even_in_a_staging_context(tmp_path):
    from backend.engine import global_migration as m
    from backend.engine.historical_control_schema import normalize_staged
    from backend.engine.global_store_paths import staged_construction,active_generation
    root,scopes,registry,actor,session,pins=original(tmp_path);known=m._known_schemas(scopes)
    with pytest.raises(ValueError,match='private staged'):normalize_staged(root,scopes,known)
    with staged_construction(root,root):
        with pytest.raises(ValueError,match='private staged'):normalize_staged(root,scopes,known)
    unchanged(pins);m.apply(root,expected_source_sha256=m.preview(root)['source_sha256'])
    active=active_generation(root)[0];before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in active.rglob('*') if p.is_file()}
    with staged_construction(root,active):
        with pytest.raises(ValueError,match='active generation'):normalize_staged(active,scopes,known)
    unchanged(before);unchanged(pins)


def test_predecessor_invalid_foreign_key_cannot_publish_or_rewrite_original_history(tmp_path):
    from backend.engine import global_migration as m
    root,scopes,registry,actor,session,pins=original(tmp_path)
    with sqlite3.connect(root/scopes['ledger']) as db:db.execute("UPDATE jobs SET parent_id='unregistered-parent'")
    pins[root/scopes['ledger']]=hashlib.sha256((root/scopes['ledger']).read_bytes()).hexdigest()
    preview=m.preview(root);assert preview['can_apply'],preview['blockers']
    with pytest.raises(ValueError,match='integrity|foreign-key'):m.apply(root,expected_source_sha256=preview['source_sha256'])
    assert not (root/'global-active.json').exists();unchanged(pins)
