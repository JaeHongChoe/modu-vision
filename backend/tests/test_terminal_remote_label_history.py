"""Confirmed ended label proposals migrate as pending archival content only."""
import json

import pytest

from backend.tests.test_terminal_remote_history import remote_history, digest


def label_history(tmp_path, state='completed'):
    from backend.engine.job_store import spec_digest
    result = remote_history(tmp_path, state)
    root, scopes, ledger, registry, key, source, output, journal = result
    journal['operation'] = 'label'; journal['launch_spec']['operation'] = 'label'
    spec_path = output / 'remote_spec.json'; spec = json.loads(spec_path.read_text())
    spec['operation'] = 'label'; spec_path.write_text(json.dumps(spec))
    journal['transfers'][0].update(size=spec_path.stat().st_size, sha256=digest(spec_path))
    raw = json.dumps(journal).encode(); source.write_bytes(raw); (output / 'remote_job.json').write_bytes(raw)
    imported = {'legacy_source': str(source), 'legacy_sha256': digest(source)}
    with ledger._tx() as db:
        db.execute('UPDATE jobs SET spec_json=?,spec_sha256=?', (json.dumps(imported), spec_digest(imported)))
    labels = {'job_id': journal['job_id'], 'input_manifest_sha256': journal['input_manifest_sha256'],
              'automatically_approved': False, 'results': [{'image_path': 'batch/part.png',
                  'image_sha256': 'c' * 64, 'candidates': [{'id': 'candidate_1',
                      'annotation': {'bbox': [12, 8, 20, 16], 'category_id': 7},
                      'confidence': .8, 'provenance': {'provider': 'controlled-transport'}}],
                  'review_state': 'pending'}]}
    path = output / 'label_results.json'; path.write_text(json.dumps(labels))
    manifest = {'protocol_version': 1, 'job_id': journal['job_id'], 'operation': 'label',
                'input_manifest_sha256': journal['input_manifest_sha256'], 'artifacts': [
                    {'path': 'outputs/label_results.json', 'size': path.stat().st_size, 'sha256': digest(path)}]}
    (output / 'remote_artifacts.json').write_text(json.dumps(manifest))
    return result


@pytest.mark.parametrize('state', ['completed', 'failed', 'aborted'])
def test_ended_label_history_preserves_pending_candidates_without_remote_authority(tmp_path, monkeypatch, state):
    from backend.engine import historical_job_binding as binding, global_migration as migration
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.remote.ssh_transport import SSHTransport
    from backend.remote.coordinator import recover_remote_jobs
    from types import SimpleNamespace
    root, scopes, ledger, registry, key, source, output, journal = label_history(tmp_path, state)
    before = {p: digest(p) for p in [source, *output.iterdir()] if p.is_file()}
    monkeypatch.setattr(SSHTransport, 'exec', lambda *_a, **_k: pytest.fail('Label history must not connect'))
    plan = binding.preview(root); assert plan['can_apply'], plan['blockers']
    saved = binding.apply(root, expected_preview_sha256=plan['preview_sha256'],
                          reason='Reviewed original ended pending label candidates')
    assert saved['worker_authority_created'] is False
    for preview, apply in [(migration.preview, migration.apply), (migration.preview_forward, migration.advance)]:
        plan = preview(root); assert plan['can_apply'], plan['blockers']
        apply(root, expected_source_sha256=plan['source_sha256'])
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(root)); restored = []
    manager = SimpleNamespace(get_job=lambda _: None, restore_terminal_job=restored.append,
                              start_remote_job=lambda **_: pytest.fail('Ended labels must not launch'))
    recover_remote_jobs(manager)
    assert len(restored) == 1 and restored[0].status == state
    assert (restored[0].launch_spec or {})['operation'] == 'label'
    assert ResourceLeases(root / scopes['leases']).list() == []
    assert all(digest(p) == sha for p, sha in before.items())
    proposals = json.loads((output / 'label_results.json').read_text())
    assert proposals['automatically_approved'] is False
    assert proposals['results'][0]['review_state'] == 'pending'
    assert proposals['results'][0]['candidates'][0]['annotation']['bbox'] == [12, 8, 20, 16]


@pytest.mark.parametrize('damage', ['wrong_job', 'wrong_input', 'auto_approved', 'approved_row',
    'absolute_image', 'traversal_image', 'backslash_image', 'duplicate_image', 'bad_image_sha',
    'untyped_candidates', 'missing_results', 'empty_results', 'extra_artifact', 'changed_bytes',
    'epoch_artifact', 'operation_mismatch', 'missing_approval', 'numeric_approval',
    'noncanonical_image', 'null_image', 'numeric_image_sha', 'untyped_result'])
def test_invalid_label_history_never_binds_or_gains_review_authority(tmp_path, damage):
    from backend.engine.historical_job_binding import preview, apply
    root, scopes, ledger, registry, key, source, output, journal = label_history(tmp_path)
    path = output / 'label_results.json'; labels = json.loads(path.read_text())
    manifest_path = output / 'remote_artifacts.json'; manifest = json.loads(manifest_path.read_text())
    if damage == 'extra_artifact':
        manifest['artifacts'].append(dict(manifest['artifacts'][0]))
    elif damage == 'epoch_artifact':
        (output / 'latest_training_state.pt').write_bytes(b'Unexpected optimizer state in label history')
    elif damage == 'operation_mismatch':
        spec_path = output / 'remote_spec.json'; spec = json.loads(spec_path.read_text()); spec['operation'] = 'train'
        spec_path.write_text(json.dumps(spec))
        journal['transfers'][0].update(size=spec_path.stat().st_size, sha256=digest(spec_path))
        raw = json.dumps(journal).encode(); source.write_bytes(raw); (output / 'remote_job.json').write_bytes(raw)
        from backend.engine.job_store import spec_digest
        imported = {'legacy_source': str(source), 'legacy_sha256': digest(source)}
        with ledger._tx() as db:
            db.execute('UPDATE jobs SET spec_json=?,spec_sha256=?', (json.dumps(imported), spec_digest(imported)))
    else:
        if damage == 'wrong_job': labels['job_id'] = 'job_other'
        elif damage == 'wrong_input': labels['input_manifest_sha256'] = 'b' * 64
        elif damage == 'auto_approved': labels['automatically_approved'] = True
        elif damage == 'approved_row': labels['results'][0]['review_state'] = 'approved'
        elif damage == 'absolute_image': labels['results'][0]['image_path'] = '/other/part.png'
        elif damage == 'traversal_image': labels['results'][0]['image_path'] = '../part.png'
        elif damage == 'backslash_image': labels['results'][0]['image_path'] = 'batch\\part.png'
        elif damage == 'duplicate_image': labels['results'].append(dict(labels['results'][0]))
        elif damage == 'bad_image_sha': labels['results'][0]['image_sha256'] = 'unbound'
        elif damage == 'untyped_candidates': labels['results'][0]['candidates'] = {}
        elif damage == 'missing_results': labels.pop('results')
        elif damage == 'empty_results': labels['results'] = []
        elif damage == 'missing_approval': labels.pop('automatically_approved')
        elif damage == 'numeric_approval': labels['automatically_approved'] = 0
        elif damage == 'noncanonical_image': labels['results'][0]['image_path'] = 'batch/./part.png'
        elif damage == 'null_image': labels['results'][0]['image_path'] = 'batch/part\x00.png'
        elif damage == 'numeric_image_sha': labels['results'][0]['image_sha256'] = int('3' * 64)
        elif damage == 'untyped_result': labels['results'][0] = []
        path.write_text(json.dumps(labels))
        if damage != 'changed_bytes':
            manifest['artifacts'][0].update(size=path.stat().st_size, sha256=digest(path))
        else: path.write_bytes(path.read_bytes() + b'changed')
    manifest_path.write_text(json.dumps(manifest))
    previous = ledger.record('job_legacy'); plan = preview(root)
    assert not plan['can_apply'] and plan['blockers']
    with pytest.raises(ValueError):
        apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed ended label history')
    assert ledger.record('job_legacy') == previous


@pytest.mark.parametrize('state', ['failed', 'aborted'])
def test_ended_label_without_final_candidates_can_archive_without_connecting(tmp_path, monkeypatch, state):
    from backend.engine.historical_job_binding import preview, apply
    from backend.remote.ssh_transport import SSHTransport
    root, scopes, ledger, registry, key, source, output, journal = label_history(tmp_path, state)
    (output / 'label_results.json').unlink(); (output / 'remote_artifacts.json').unlink()
    monkeypatch.setattr(SSHTransport, 'exec', lambda *_a, **_k: pytest.fail('Ended labels must not connect'))
    plan = preview(root); assert plan['can_apply'], plan['blockers']
    result = apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed ended label without final proposals')
    assert result['worker_authority_created'] is False and ledger.record('job_legacy')['state'] == state


@pytest.mark.parametrize('damage', ['linked_results', 'duplicate_key', 'oversized_results'])
def test_label_history_rejects_unbounded_or_linked_proposals(tmp_path, damage):
    from backend.engine.historical_job_binding import preview
    root, scopes, ledger, registry, key, source, output, journal = label_history(tmp_path)
    path = output / 'label_results.json'
    if damage == 'linked_results':
        original = output / 'original_labels.json'; path.rename(original); path.symlink_to(original)
        plan = preview(root); assert not plan['can_apply'] and plan['blockers']
        return
    if damage == 'duplicate_key': path.write_text('{"results":[],"results":[]}')
    else: path.write_bytes(b' ' * (1024 * 1024 + 1))
    manifest_path = output / 'remote_artifacts.json'; manifest = json.loads(manifest_path.read_text())
    manifest['artifacts'][0].update(size=path.stat().st_size, sha256=digest(path))
    manifest_path.write_text(json.dumps(manifest))
    plan = preview(root); assert not plan['can_apply'] and plan['blockers']


def test_changed_pending_label_bytes_after_preview_cannot_apply(tmp_path):
    from backend.engine.historical_job_binding import preview, apply
    root, scopes, ledger, registry, key, source, output, journal = label_history(tmp_path)
    plan = preview(root); assert plan['can_apply'], plan['blockers']
    previous = ledger.record('job_legacy')
    path = output / 'label_results.json'; labels = json.loads(path.read_text())
    labels['results'][0]['candidates'][0]['annotation']['bbox'] = [1, 2, 3, 4]
    path.write_text(json.dumps(labels))
    with pytest.raises(ValueError):
        apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Previously reviewed pending proposals')
    assert ledger.record('job_legacy') == previous


def test_real_worker_coordinator_and_receipt_paths_archive_pending_label_outputs(tmp_path, monkeypatch):
    """Real snapshot/worker/receipt paths with a controlled model and transport."""
    from pathlib import Path
    import uuid
    from PIL import Image
    from backend.api.routes_training import JobRecord, _write_job_receipt
    from backend.engine import foundation_labeling, historical_job_binding as binding, global_migration as migration
    from backend.remote.coordinator import persist_queued_remote_job, run_remote_training
    from backend.remote.profiles import ComputeProfile
    from backend.remote.worker import run_label
    from backend.tests.test_historical_job_binding import history
    from backend.tests.test_remote_coordinator import FakeRemote
    root, scopes, ledger, registry, key, old, output = history(tmp_path)
    with ledger._tx() as db:
        for table in ['events', 'migrations', 'jobs']: db.execute('DELETE FROM ' + table)
    old.unlink(); monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(root))
    source = output.parent.parent / 'training'; source.mkdir()
    image = source / 'part.png'; Image.new('RGB', (32, 24), 'white').save(image)
    monkeypatch.setattr(foundation_labeling, 'foundation_readiness', lambda *_a, **_k: {'ready': True})
    monkeypatch.setattr(foundation_labeling, 'foundation_candidates', lambda *_a, **_k: [{
        'id': 'candidate_1', 'annotation': {'bbox': [12, 8, 10, 8], 'category_id': 7},
        'confidence': .8, 'provenance': {'provider': 'controlled-provider'}}])
    profile = ComputeProfile(id='archived-label-worker', name='Controlled label transport',
        ssh_target='worker.invalid', ssh_port=22, remote_root=str(root / 'controlled-server'),
        runtime_kind='python', runtime_value='/usr/bin/python3')
    launch = {'operation': 'label', 'label_images': ['part.png'], 'labeling': {'prompt': 'scratch'}}
    record = JobRecord(job_id='job_legacy', task='classification', preset='fast',
        dataset_path=str(source), output_dir=str(output), status='running',
        remote_profile_id=profile.id, launch_spec=launch)
    class ControlledTransport(FakeRemote):
        def launch(self, profile, argv, run_id):
            spec_path = self.root / 'runs' / run_id / 'spec.json'
            assert run_label(spec_path)['status'] == 'completed'
            return '123:' + uuid.uuid4().hex
    persist_queued_remote_job(record, profile, launch)
    result = run_remote_training(record, profile, transport=ControlledTransport(Path(profile.remote_root)), device='cpu')
    assert result['status'] == 'completed' and result['worker_exit_confirmed']
    record.status = 'completed'; _write_job_receipt(record)
    labels = json.loads((output / 'label_results.json').read_text())
    assert labels['results'][0]['image_sha256'] == digest(image)
    assert labels['results'][0]['review_state'] == 'pending' and labels['automatically_approved'] is False
    original = (output / 'label_results.json').read_bytes()
    ledger.migrate_legacy([root / scopes['remote_journals']], root / 'label-producer-import.json')
    plan = binding.preview(root); assert plan['can_apply'], plan['blockers']
    receipt = binding.apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed actual producer format with controlled model')
    assert receipt['worker_authority_created'] is False
    cutover = migration.preview(root); assert cutover['can_apply'], cutover['blockers']
    migration.apply(root, expected_source_sha256=cutover['source_sha256'])
    assert migration.preview_forward(root)['can_apply']
    assert (output / 'label_results.json').read_bytes() == original
    from backend.engine.job_store import JobStore
    assert not list(source.glob('*.json')) and not JobStore(root / scopes['ledger']).attempts('job_legacy')
