"""Offline terminal history acquires explicit local context, never launch authority."""
import hashlib
import json
import sqlite3

import pytest

from backend.tests.test_global_migration import owned


def history(tmp_path, job_id='job_legacy'):
    root, scopes, ledger, registry, account, actor = owned(tmp_path)
    directory = root / 'projects' / 'history-project'
    models = directory / 'models'; models.mkdir(parents=True)
    project = {'id': 'history-project', 'workspace_id': registry.workspace_id,
               'project_dir': str(directory), 'models_dir': str(models)}
    (directory / 'project.json').write_text(json.dumps(project))
    key = registry.register_project(project)
    source = root / 'historical_inputs'; source.mkdir()
    output = models / job_id; output.mkdir()
    (output / 'original.bin').write_bytes(b'preserved trained artifact')
    file = source / 'job_legacy.json'
    file.write_text(json.dumps({'job_id': job_id, 'status': 'completed', 'output_dir': str(output)}))
    ledger.migrate_legacy([source], root / 'original-import-receipt.json')
    return root, scopes, ledger, registry, key, file, output


def test_bound_history_preserves_originals_and_can_cut_over_without_worker_authority(tmp_path):
    from backend.engine.historical_job_binding import preview, apply, read_receipt
    from backend.engine import global_migration
    root, scopes, ledger, registry, key, file, output = history(tmp_path)
    original = file.read_bytes(); model = (output / 'original.bin').read_bytes()
    assert not global_migration.preview(root)['can_apply']
    plan = preview(root)
    assert plan['can_apply'] and plan['rows'][0]['target_context']['actor_id'] == registry.local_actor_id
    result = apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Explicitly reviewed local terminal history')
    saved = read_receipt(root, result['binding_id'])
    row = ledger.record('job_legacy')
    assert row['workspace_id'] == registry.workspace_id and row['project_key'] == key
    assert row['actor_id'] == registry.local_actor_id and row['state'] == 'completed'
    assert row['spec_sha256'] == plan['rows'][0]['spec_sha256']
    assert saved['worker_authority_created'] is False and saved['rows'][0]['previous_context']['actor_id'] == 'legacy'
    assert file.read_bytes() == original and (output / 'original.bin').read_bytes() == model
    assert ledger.attempts('job_legacy') == []
    clean = global_migration.preview(root); assert clean['can_apply']
    global_migration.apply(root, expected_source_sha256=clean['source_sha256'])
    assert file.read_bytes() == original and (output / 'original.bin').read_bytes() == model
    assert read_receipt(root, result['binding_id'])['binding_id'] == result['binding_id']


def test_source_cas_and_changed_origin_refuse_before_binding(tmp_path):
    from backend.engine.historical_job_binding import preview, apply
    root, scopes, ledger, registry, key, file, output = history(tmp_path)
    plan = preview(root); previous = ledger.record('job_legacy')
    file.write_text(file.read_text() + '\n')
    with pytest.raises(ValueError, match='changed|origin|source'):
        apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed terminal record')
    assert ledger.record('job_legacy') == previous


@pytest.mark.parametrize('change', ['foreign_source', 'traversal_source', 'foreign_output', 'linked_output', 'missing_project', 'live_attempt', 'uncertain_lease'])
def test_unbound_or_live_control_never_gains_context(tmp_path, change):
    from backend.engine.historical_job_binding import preview, apply
    root, scopes, ledger, registry, key, file, output = history(tmp_path)
    if change in {'foreign_source', 'traversal_source'}:
        foreign = tmp_path / 'copied-foreign.json'; foreign.write_bytes(file.read_bytes())
        with ledger._tx() as db:
            source = foreign if change == 'foreign_source' else root / '..' / foreign.name
            spec = {'legacy_source': str(source), 'legacy_sha256': hashlib.sha256(foreign.read_bytes()).hexdigest()}
            from backend.engine.job_store import spec_digest
            db.execute('UPDATE jobs SET spec_json=?,spec_sha256=?', (json.dumps(spec), spec_digest(spec)))
    elif change == 'foreign_output':
        foreign = tmp_path / 'unregistered-output'; foreign.mkdir()
        with ledger._tx() as db: db.execute('UPDATE jobs SET output_dir=?', (str(foreign),))
    elif change == 'linked_output':
        (output / 'original.bin').unlink(); output.rmdir()
        foreign = tmp_path / 'linked-private'; foreign.mkdir(); output.symlink_to(foreign, target_is_directory=True)
    elif change == 'missing_project':
        (output.parent.parent / 'project.json').unlink()
    elif change == 'live_attempt':
        with ledger._tx() as db:
            db.execute("INSERT INTO attempts(job_id,number,fencing_token,executor,started_ns) VALUES('job_legacy',1,1,'unknown-worker',1)")
    else:
        from backend.engine.shared_scheduler import ResourceLeases
        leases = ResourceLeases(root / scopes['leases']); assert leases.acquire('job_legacy', 'host', 'all', remote=True)
        leases.mark_uncertain('job_legacy')
    before = ledger.record('job_legacy'); plan = preview(root)
    assert not plan['can_apply'] and plan['blockers']
    with pytest.raises(ValueError): apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed terminal record')
    assert ledger.record('job_legacy') == before


def test_noncanonical_legacy_job_identity_is_preserved_unbound(tmp_path):
    from backend.engine.historical_job_binding import preview, apply
    job_id = 'job_noncanonical!'
    root, scopes, ledger, registry, key, file, output = history(tmp_path, job_id)
    before = ledger.record(job_id); plan = preview(root)
    assert not plan['can_apply'] and any('identity is invalid' in reason for reason in plan['blockers'])
    with pytest.raises(ValueError):
        apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed terminal record')
    assert ledger.record(job_id) == before


def test_repeat_returns_saved_history_without_overwriting_new_writes(tmp_path):
    from backend.engine.historical_job_binding import preview, apply
    root, scopes, ledger, registry, key, file, output = history(tmp_path)
    plan = preview(root)
    first = apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed terminal record')
    with ledger._tx() as db: db.execute("UPDATE jobs SET wait_reason='later retained metadata' WHERE id='job_legacy'")
    second = apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Repeated reviewed request')
    assert second['binding_id'] == first['binding_id'] and second['replayed'] is True
    assert ledger.record('job_legacy')['wait_reason'] == 'later retained metadata'


def test_team_or_modified_manifest_is_not_silently_rebound(tmp_path):
    from backend.engine.historical_job_binding import preview
    root, scopes, ledger, registry, key, file, output = history(tmp_path)
    with ledger._tx() as db: db.execute("UPDATE jobs SET mode='team'")
    assert not preview(root)['can_apply']
    with ledger._tx() as db: db.execute("UPDATE jobs SET mode='local'")
    manifest = output.parent.parent / 'project.json'; value = json.loads(manifest.read_text()); value['id'] = 'foreign-project'
    manifest.write_text(json.dumps(value))
    assert not preview(root)['can_apply']


def test_interrupted_worker_history_and_unknown_schema_never_mutate_or_gain_authority(tmp_path):
    from backend.engine.historical_job_binding import preview, apply
    root, scopes, ledger, registry, key, file, output = history(tmp_path)
    with ledger._tx() as db: db.execute("UPDATE jobs SET state='interrupted'")
    plan = preview(root); assert not plan['can_apply']
    with pytest.raises(ValueError): apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed terminal record')
    with sqlite3.connect(root / scopes['ledger']) as db: db.execute('CREATE TABLE unknown_authority(owner TEXT)')
    original = (root / scopes['ledger']).read_bytes()
    plan = preview(root); assert not plan['can_apply']
    with pytest.raises(ValueError): apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed terminal record')
    assert (root / scopes['ledger']).read_bytes() == original
