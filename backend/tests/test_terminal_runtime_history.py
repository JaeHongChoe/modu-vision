"""Ended owned local recovery indexes migrate as history, never live authority."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import psutil
import pytest

from backend.tests.test_historical_job_binding import history


def runtime_history(tmp_path):
    root, scopes, ledger, registry, key, original, output = history(tmp_path)
    # The generic importer must see only the actual recovery index this time.
    with ledger._tx() as db:
        db.execute('DELETE FROM events')
        db.execute('DELETE FROM migrations')
        db.execute('DELETE FROM jobs')
    original.unlink()
    token = uuid.uuid4().hex
    env = dict(os.environ, MODU_VISION_LOCAL_WORKER_TOKEN=token)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             env=env, start_new_session=True)
    process = psutil.Process(child.pid)
    created, username = process.create_time(), process.username()
    child.terminate(); child.wait(timeout=5)
    spec = {'protocol_version': 1, 'job_id': 'job_legacy', 'task': 'classification',
            'preset': 'controlled', 'dataset_path': str(output.parent.parent),
            'output_dir': str(output), 'lease_path': str(root / scopes['leases']),
            'lease_owner': 'ended-original-owner'}
    spec_path = output / 'local_spec.json'
    spec_path.write_text(json.dumps(spec))
    journal = {'protocol_version': 1, 'job_id': 'job_legacy', 'task': 'classification',
               'preset': 'controlled', 'status': 'completed', 'output_dir': str(output),
               'spec_path': str(spec_path), 'spec_sha256': hashlib.sha256(spec_path.read_bytes()).hexdigest(),
               'owner_pid': child.pid, 'owner_created_at': created, 'owner_username': username,
               'owner_session': child.pid, 'owner_token': token, 'worker_exit_confirmed': True}
    index = root / scopes['local_journals']; index.mkdir()
    source = index / 'job_legacy.json'
    raw = json.dumps(journal).encode()
    source.write_bytes(raw); (output / 'local_job.json').write_bytes(raw)
    ledger.migrate_legacy([index], root / 'runtime-import-receipt.json')
    return root, scopes, ledger, registry, key, source, output, journal


def test_terminal_runtime_index_binds_and_survives_apply_forward_and_fresh_recovery(tmp_path, monkeypatch):
    from backend.engine import historical_job_binding as binding, global_migration as migration
    from backend.engine.global_store_paths import resolve_store_path
    from backend.engine.local_training_worker import _index, _recover_local_journals
    from backend.engine.shared_scheduler import ResourceLeases
    root, scopes, ledger, registry, key, source, output, journal = runtime_history(tmp_path)
    original = source.read_bytes(); model = (output / 'original.bin').read_bytes()
    plan = binding.preview(root)
    assert plan['can_apply'], plan['blockers']
    bound = binding.apply(root, expected_preview_sha256=plan['preview_sha256'],
                          reason='Reviewed ended owned recovery history only')
    assert not bound['worker_authority_created']
    current = migration.preview(root); assert current['can_apply'], current['blockers']
    migration.apply(root, expected_source_sha256=current['source_sha256'])
    first_index = resolve_store_path(root / scopes['local_journals'])
    assert first_index != source.parent and (first_index / source.name).read_bytes() == original
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(root))
    assert _index() == first_index
    forward = migration.preview_forward(root); assert forward['can_apply'], forward['blockers']
    migration.advance(root, expected_source_sha256=forward['source_sha256'])
    assert _index() != first_index and (_index() / source.name).read_bytes() == original
    restored = []
    manager = SimpleNamespace(_leases=ResourceLeases(root / scopes['leases']),
                              get_job=lambda _: None,
                              restore_local_job=lambda record, **kwargs: restored.append((record, kwargs)))
    # Avoid creating unrelated model metrics in this archival fixture.
    (output / 'job_receipt.json').write_text('{"historical_fixture":true}')
    _recover_local_journals(manager)
    assert len(restored) == 1
    record, kwargs = restored[0]
    assert record.status == 'completed' and kwargs == {}
    assert record.result['optimizer_resume'] is False
    assert manager._leases.list() == []
    assert source.read_bytes() == original and (output / 'original.bin').read_bytes() == model


@pytest.mark.parametrize('damage', ['unconfirmed', 'spec_changed', 'copy_changed', 'foreign_lease', 'linked_source', 'unknown_file'])
def test_unconfirmed_or_changed_runtime_history_stays_unbound(tmp_path, damage):
    from backend.engine.historical_job_binding import preview
    root, scopes, ledger, registry, key, source, output, journal = runtime_history(tmp_path)
    if damage == 'unconfirmed':
        journal['worker_exit_confirmed'] = False
        raw = json.dumps(journal).encode(); source.write_bytes(raw); (output / 'local_job.json').write_bytes(raw)
        with ledger._tx() as db:
            from backend.engine.job_store import spec_digest
            spec = {'legacy_source': str(source), 'legacy_sha256': hashlib.sha256(raw).hexdigest()}
            db.execute('UPDATE jobs SET spec_json=?,spec_sha256=?', (json.dumps(spec), spec_digest(spec)))
    elif damage == 'spec_changed': (output / 'local_spec.json').write_text('{}')
    elif damage == 'copy_changed': (output / 'local_job.json').write_bytes(b'changed')
    elif damage == 'foreign_lease':
        value = json.loads((output / 'local_spec.json').read_text()); value['lease_path'] = str(tmp_path / 'foreign.sqlite')
        (output / 'local_spec.json').write_text(json.dumps(value))
    elif damage == 'linked_source':
        copy = root / 'copied.json'; copy.write_bytes(source.read_bytes()); source.unlink(); source.symlink_to(copy)
    else: (source.parent / 'unknown.json').write_text('{}')
    before = ledger.record('job_legacy'); plan = preview(root)
    assert not plan['can_apply'] and plan['blockers']
    assert ledger.record('job_legacy') == before


def test_terminal_marker_cannot_hide_actual_live_owned_process(tmp_path):
    from backend.engine.historical_job_binding import preview
    from backend.engine.job_store import spec_digest
    root, scopes, ledger, registry, key, source, output, journal = runtime_history(tmp_path)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             env=dict(os.environ, MODU_VISION_LOCAL_WORKER_TOKEN=journal['owner_token']),
                             start_new_session=True)
    try:
        journal.update(owner_pid=child.pid, owner_session=child.pid,
                       owner_created_at=psutil.Process(child.pid).create_time())
        raw = json.dumps(journal).encode(); source.write_bytes(raw); (output / 'local_job.json').write_bytes(raw)
        spec = {'legacy_source': str(source), 'legacy_sha256': hashlib.sha256(raw).hexdigest()}
        with ledger._tx() as db:
            db.execute('UPDATE jobs SET spec_json=?,spec_sha256=?', (json.dumps(spec), spec_digest(spec)))
        plan = preview(root)
        assert not plan['can_apply'] and any('live' in reason.lower() for reason in plan['blockers'])
        assert child.poll() is None
    finally: child.terminate(); child.wait(timeout=5)


@pytest.mark.parametrize('scope',['ledger','context'])
def test_missing_declared_store_remains_a_preview_refusal(tmp_path,scope):
    from backend.engine.global_migration import preview
    root,scopes,*_=runtime_history(tmp_path)
    (root/scopes[scope]).unlink()
    result=preview(root)
    assert result['can_apply'] is False and result['blockers']


def test_current_index_children_resolve_and_retired_index_writes_refuse(tmp_path):
    from backend.engine import historical_job_binding as binding, global_migration as migration
    from backend.engine.global_store_paths import resolve_store_path, store_admission
    root, scopes, ledger, registry, key, source, output, journal = runtime_history(tmp_path)
    plan = binding.preview(root)
    binding.apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed owned ended history')
    migration.apply(root, expected_source_sha256=migration.preview(root)['source_sha256'])
    first = resolve_store_path(source)
    assert first != source and first.read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match='generation|restart'):
        with store_admission(source): source.write_bytes(b'forbidden stale write')
    forward = migration.preview_forward(root)
    migration.advance(root, expected_source_sha256=forward['source_sha256'])
    with pytest.raises(ValueError, match='retired|restart'):
        with store_admission(first): first.write_bytes(b'forbidden retired write')
    assert resolve_store_path(source).read_bytes() == source.read_bytes()


def test_new_remote_journal_after_cutover_uses_current_generation_and_still_blocks_forward(tmp_path, monkeypatch):
    from backend.tests.test_global_migration import owned
    from backend.engine import global_migration as migration
    from backend.engine.global_store_paths import resolve_store_path
    from backend.remote.coordinator import _journal_index, _save_journal
    root, scopes, ledger, registry, account, actor = owned(tmp_path)
    migration.apply(root, expected_source_sha256=migration.preview(root)['source_sha256'])
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(root))
    output = root / 'projects' / 'queued-output'; output.mkdir()
    _save_journal({'job_id': 'job_queued', 'output_dir': str(output), 'state': 'queued'})
    assert _journal_index() == resolve_store_path(root / scopes['remote_journals'])
    assert (_journal_index() / 'job_queued.json').is_file()
    assert not (root / scopes['remote_journals']).exists()
    assert not migration.preview_forward(root)['can_apply']


def test_actual_owned_cpu_training_journal_migrates_without_retraining(tmp_path, monkeypatch):
    """Actual randomly initialized model; no manufacturing-quality approval."""
    from PIL import Image
    from backend.tests.test_global_migration import owned
    from backend.api.routes_training import TrainingJobManager
    from backend.engine import historical_job_binding as binding, global_migration as migration
    from backend.engine.local_training_worker import _index, _recover_local_journals
    from backend.engine.job_store import JobStore
    from backend.contracts.context import ContextRegistry
    from backend.engine.global_store_paths import resolve_store_path
    root, scopes, ledger, registry, accounts, actor = owned(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(root))
    monkeypatch.delenv('VISION_RESOURCE_LEASE_DB', raising=False)
    directory = root / 'projects' / 'actual-project'
    source = directory / 'source'; models = directory / 'models'; models.mkdir(parents=True)
    for split in ('train', 'val'):
        for label, color in [('OK', 'blue'), ('NG', 'red')]:
            folder = source / split / label; folder.mkdir(parents=True)
            for index in range(2): Image.new('RGB', (32, 32), color).save(folder / f'{index}.png')
    project = {'id': 'actual-project', 'project_dir': str(directory), 'models_dir': str(models),
               'workspace_id': registry.workspace_id, 'source_dataset_dir': str(source)}
    (directory / 'project.json').write_text(json.dumps(project)); registry.register_project(project)
    manager = TrainingJobManager(); output = models / 'job_actual_runtime'
    record = manager.start_job('job_actual_runtime', 'classification', str(source), str(output), device='cpu',
                              config_overrides={'pretrained': False, 'backbone': 'resnet18', 'epochs': 1,
                                                'image_size': 32, 'batch_size': 2, 'num_workers': 0})
    record.thread.join(45)
    assert record.status == 'completed', record.error
    journal = _index() / 'job_actual_runtime.json'
    retained = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in [journal, output / 'best_model.pt',
                output / 'local_spec.json', output / 'local_job.json', output / 'job_receipt.json']}
    assert json.loads(journal.read_text())['worker_exit_confirmed'] is True
    assert manager._leases.list() == []
    ledger.migrate_legacy([_index()], root / 'actual-runtime-import.json')
    plan = binding.preview(root); assert plan['can_apply'], plan['blockers']
    binding.apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Actual owned terminal CPU history review')
    migration.apply(root, expected_source_sha256=migration.preview(root)['source_sha256'])
    # Another owned current generation retains the canonical lease reference.
    forward = migration.preview_forward(root)
    migration.advance(root, expected_source_sha256=forward['source_sha256'])
    fresh = TrainingJobManager(); _recover_local_journals(fresh)
    restored = fresh.get_job(record.job_id)
    assert restored is not None and restored.status == 'completed' and restored.thread is None
    assert restored.result['optimizer_resume'] is False and fresh._leases.list() == []
    assert JobStore(root / scopes['ledger']).record(record.job_id)['actor_id'] == registry.local_actor_id
    assert ContextRegistry(root / 'projects').workspace_id == registry.workspace_id
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in retained} == retained
    receipt = {'status': 'actual_cpu_terminal_history_recovered', 'job_id': record.job_id,
               'checkpoint_sha256': retained[output / 'best_model.pt'],
               'original_journal_sha256': retained[journal], 'current_journal_sha256': hashlib.sha256(resolve_store_path(journal).read_bytes()).hexdigest(),
               'parent_control_and_model_bytes_preserved': True, 'training_executions': 1,
               'fresh_recovery_started_worker': False, 'optimizer_resume': False, 'human_quality_approved': False}
    (root / 'actual-terminal-followup.json').write_text(json.dumps(receipt, indent=2) + '\n')
