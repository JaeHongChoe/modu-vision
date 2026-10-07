"""Relocated ended specialists retain original bytes, never launch authority."""
import hashlib
import json
from pathlib import Path
import uuid

import pytest
import torch

from backend.tests.test_historical_job_binding import history


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_unsupported_sparse_model_archive_refuses_without_crashing_preview():
    from backend.remote.artifact_relocation import matches_path_relocation
    sparse = torch.sparse_coo_tensor(torch.tensor([[0]]), torch.tensor([1.]), (2,))
    assert matches_path_relocation({'weight': sparse}, {'weight': sparse}, '/remote', '/local') is False


def specialist_history(tmp_path, monkeypatch, family='rotation'):
    from backend.api.routes_training import JobRecord, _write_job_receipt
    from backend.remote.coordinator import _copy_artifacts
    from backend.remote.profiles import ComputeProfile
    from backend.tests.test_remote_coordinator import FakeRemote
    root, scopes, ledger, registry, key, old, initial = history(tmp_path)
    with ledger._tx() as db:
        for table in ('events', 'migrations', 'jobs'):
            db.execute('DELETE FROM ' + table)
    old.unlink(); (initial / 'original.bin').unlink(); initial.rmdir()
    local_id = 'a' * 32; job_id = 'job_' + local_id
    output = initial.parent / family / local_id; output.mkdir(parents=True)
    profile = ComputeProfile(id='archive', name='Controlled archived specialist',
        ssh_target='worker.invalid', ssh_port=22, remote_root=str(root / 'server'),
        runtime_kind='python', runtime_value='/usr/bin/python3')
    remote_data = f'{profile.remote_root}/runs/{job_id}/input/data'
    remote = Path(profile.remote_root) / 'runs' / job_id
    artifacts = remote / 'outputs'; artifacts.mkdir(parents=True)
    torch.save({'task': family, 'model_state_dict': {'weight': torch.tensor([1., 2.])},
                'dataset_path': remote_data, 'nested': [{'image': remote_data + '/a.png'}]},
               artifacts / 'best_model.pt')
    (artifacts / 'model_meta.json').write_text(json.dumps({'task': family,
        'dataset_path': remote_data, 'checkpoint_sha256': digest(artifacts / 'best_model.pt')}))
    manifest = dict(protocol_version=1, job_id=job_id, operation='train', input_manifest_sha256='a' * 64,
        artifacts=[dict(path='outputs/' + p.name, size=p.stat().st_size, sha256=digest(p))
                   for p in sorted(artifacts.iterdir())])
    (remote / 'artifacts.json').write_text(json.dumps(manifest))
    launch = {'operation': 'train', 'local_model_id': local_id}
    spec = dict(protocol_version=1, job_id=job_id, operation='train', task=family,
                preset='controlled', input_manifest_sha256='a' * 64)
    path = output / 'remote_spec.json'; path.write_text(json.dumps(spec))
    journal = dict(protocol_version=1, job_id=job_id, operation='train', task=family,
        preset='controlled', state='completed', worker_terminal_state='completed',
        worker_exit_confirmed=True, output_dir=str(output), dataset_path=str(root / 'data'),
        profile=profile.model_dump(), remote_handle='123:' + uuid.uuid4().hex,
        launch_spec=launch, input_manifest_sha256='a' * 64,
        transfers=[dict(source=str(path), target='spec.json', size=path.stat().st_size, sha256=digest(path))])
    _copy_artifacts(FakeRemote(Path(profile.remote_root)), profile, journal, output)
    source = root / scopes['remote_journals'] / (job_id + '.json'); source.parent.mkdir(exist_ok=True)
    raw = json.dumps(journal).encode(); source.write_bytes(raw); (output / 'remote_job.json').write_bytes(raw)
    record = JobRecord(job_id=job_id, task=family, preset='controlled', dataset_path=journal['dataset_path'],
        output_dir=str(output), status='completed', remote_profile_id=profile.id, launch_spec=launch,
        current_epoch=1, total_epochs=1, train_loss=.25, val_loss=.5)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(root))
    _write_job_receipt(record)
    ledger.migrate_legacy([source.parent], root / 'specialist-import.json')
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(root))
    return root, scopes, ledger, registry, key, source, output, journal, artifacts


def test_coordinator_retains_both_original_received_files_before_relocation(tmp_path, monkeypatch):
    *_, output, journal, originals = specialist_history(tmp_path, monkeypatch)
    for name in ('best_model.pt', 'model_meta.json'):
        retained = output / 'remote_received' / name
        assert retained.is_file(), 'Relocation lost its original received bytes'
        assert retained.read_bytes() == (originals / name).read_bytes()
    assert (output / 'best_model.pt').read_bytes() != (originals / 'best_model.pt').read_bytes()


@pytest.mark.parametrize('family', ['rotation', 'ocr', 'rotated_detection', 'enhancement', 'defect_gan'])
def test_specialist_alias_and_relocation_survive_owned_cutover_and_forward_readback(tmp_path, monkeypatch, family):
    from types import SimpleNamespace
    from backend.engine import historical_job_binding as binding, global_migration as migration
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.remote.coordinator import recover_remote_jobs
    from backend.remote.ssh_transport import SSHTransport
    root, scopes, ledger, registry, key, source, output, journal, _ = specialist_history(tmp_path, monkeypatch, family)
    before = {p: digest(p) for p in output.rglob('*') if p.is_file()}
    monkeypatch.setattr(SSHTransport, 'exec', lambda *_a, **_k: pytest.fail('Archive must never connect'))
    reviewed = binding.preview(root); assert reviewed['can_apply'], reviewed['blockers']
    result = binding.apply(root, expected_preview_sha256=reviewed['preview_sha256'], reason='Reviewed retained ended specialist')
    assert result['worker_authority_created'] is False
    first = migration.preview(root); assert first['can_apply'], first['blockers']
    migration.apply(root, expected_source_sha256=first['source_sha256'])
    forward = migration.preview_forward(root); assert forward['can_apply'], forward['blockers']
    migration.advance(root, expected_source_sha256=forward['source_sha256'])
    restored = []
    recover_remote_jobs(SimpleNamespace(get_job=lambda _: None, restore_terminal_job=restored.append,
        start_remote_job=lambda **_: pytest.fail('Archive must never launch')))
    assert len(restored) == 1 and restored[0].job_id == journal['job_id']
    assert restored[0].current_epoch == 1 and restored[0].val_loss == .5
    assert ResourceLeases(root / scopes['leases']).list() == []
    from backend.engine.job_store import JobStore
    assert JobStore(root / scopes['ledger']).attempts(journal['job_id']) == []
    assert {p: digest(p) for p in before} == before


@pytest.mark.parametrize('damage', ['missing_original', 'original_bytes', 'changed_tensor', 'changed_nested_path',
    'wrong_remote_root', 'wrong_local_root', 'received_identity', 'received_checksum', 'alias', 'output_namespace',
    'linked_original', 'linked_parent', 'extra_manifest_row', 'extra_received_row'])
def test_changed_specialist_relocation_never_binds_history(tmp_path, monkeypatch, damage):
    from backend.engine.historical_job_binding import preview
    root, scopes, ledger, registry, key, source, output, journal, _ = specialist_history(tmp_path, monkeypatch)
    retained = output / 'remote_received' / 'best_model.pt'
    if damage == 'missing_original': retained.unlink(missing_ok=True)
    elif damage == 'original_bytes': retained.write_bytes(b'changed original')
    elif damage in ('changed_tensor', 'changed_nested_path'):
        file = output / 'best_model.pt'; value = torch.load(file, weights_only=True)
        if damage == 'changed_tensor': value['model_state_dict']['weight'][0] = 3.
        else: value['nested'][0]['image'] = str(output / 'remote_snapshot/data/wrong.png')
        torch.save(value, file)
        manifest = json.loads((output / 'remote_artifacts.json').read_text())
        row = next(r for r in manifest['artifacts'] if r['path'] == 'outputs/best_model.pt')
        row.update(size=file.stat().st_size, sha256=digest(file))
        (output / 'remote_artifacts.json').write_text(json.dumps(manifest))
        receipt = json.loads((output / 'job_receipt.json').read_text()); receipt['checkpoint_sha256'] = digest(file)
        (output / 'job_receipt.json').write_text(json.dumps(receipt))
        metadata = json.loads((output / 'model_meta.json').read_text()); metadata['checkpoint_sha256'] = digest(file)
        (output / 'model_meta.json').write_text(json.dumps(metadata))
        metadata_row = next(r for r in manifest['artifacts'] if r['path'] == 'outputs/model_meta.json')
        metadata_row.update(size=(output / 'model_meta.json').stat().st_size, sha256=digest(output / 'model_meta.json'))
        (output / 'remote_artifacts.json').write_text(json.dumps(manifest))
    elif damage in ('wrong_remote_root', 'wrong_local_root', 'received_checksum', 'extra_manifest_row'):
        file = output / 'remote_artifacts.json'; value = json.loads(file.read_text())
        if damage.startswith('wrong_'): value['relocation']['remote_dataset_root' if damage == 'wrong_remote_root' else 'local_dataset_root'] = '/other'
        elif damage == 'received_checksum': value['artifacts'][0]['received_sha256'] = '0' * 64
        else: value['artifacts'].append(dict(value['artifacts'][0], path='outputs/extra.pt'))
        file.write_text(json.dumps(value))
    elif damage in ('received_identity', 'extra_received_row'):
        file = output / 'remote_received_artifacts.json'; value = json.loads(file.read_text())
        if damage == 'received_identity': value['job_id'] = 'job_other'
        else: value['artifacts'].append(dict(value['artifacts'][0], path='outputs/extra.pt'))
        file.write_text(json.dumps(value))
    elif damage == 'alias':
        file = output / 'job_receipt.json'; value = json.loads(file.read_text()); value['remote_job_id'] = 'job_other'; file.write_text(json.dumps(value))
    elif damage == 'output_namespace':
        file = output.parent.parent.parent / 'project.json'; value = json.loads(file.read_text()); value['models_dir'] = str(root); file.write_text(json.dumps(value))
    elif damage == 'linked_original':
        if retained.exists(): retained.unlink()
        retained.parent.mkdir(exist_ok=True); retained.symlink_to(output / 'best_model.pt')
    else:
        directory = output / 'remote_received'; destination = output / 'retired-originals'
        if directory.exists(): directory.rename(destination)
        else: destination.mkdir()
        directory.symlink_to(destination, target_is_directory=True)
    previous = ledger.record(journal['job_id']); plan = preview(root)
    assert not plan['can_apply'] and plan['blockers']
    assert ledger.record(journal['job_id']) == previous
