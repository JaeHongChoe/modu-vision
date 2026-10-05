"""Reusable setup and portable archive refusal preserve original authority/data."""
import hashlib
import json
import pytest
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
from backend.main import create_app


def client(tmp_path):
    app = create_app(project_dir=str(tmp_path / 'projects'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = api.post('/api/project/create', json={'name': 'Original setup', 'task': 'segmentation', 'active_preset': 'precision'}).json()
    return api, Path(project['project_dir']), project


def test_template_is_setup_only_and_new_identity_survives_reopen(tmp_path):
    api, root, original = client(tmp_path)
    from backend.engine.project_preferences import update_preferences
    update_preferences(root, expected_revision=0, actor='template-fixture', changes={'tag_colors': {'검수': '#123456'}, 'model_flags': {'old-model': ['held']}})
    assert api.put('/api/project/retention/policy', json={'retention_days': 15, 'trash_days': 7, 'quota_bytes': 1000000}).status_code == 200
    exported = api.get('/api/project/template')
    assert exported.status_code == 200, exported.text
    template = exported.json()
    assert template['tag_colors'] == {'검수': '#123456'}
    assert 'old-model' not in json.dumps(template) and 'project_dir' not in template
    created = api.post('/api/project/create', json={'name': 'From template', 'template': template})
    assert created.status_code == 200, created.text
    new = created.json()
    assert new['id'] != original['id'] and new['task'] == 'segmentation' and new['active_preset'] == 'precision'
    assert new['source_dataset_dir'] is None
    assert list(Path(new['models_dir']).iterdir()) == []
    assert api.get('/api/project/retention').json()['policy'] == template['retention_policy']
    from backend.engine.project_preferences import read_preferences
    prefs = read_preferences(Path(new['project_dir']))
    assert prefs['tag_colors'] == template['tag_colors'] and prefs['model_flags'] == {}
    assert api.post('/api/project/open', json={'project_dir': str(root)}).status_code == 200
    assert api.post('/api/project/open', json={'project_dir': new['project_dir']}).json()['id'] == new['id']
    assert api.get('/api/project/template').json() == template


def test_invalid_template_never_creates_or_activates_a_project(tmp_path):
    api, root, original = client(tmp_path)
    exported = api.get('/api/project/template')
    assert exported.status_code == 200
    template = exported.json()
    template['source_dataset_dir'] = 'forbidden-resource-binding'
    target = tmp_path / 'invalid-template'
    refused = api.post('/api/project/create', json={'name': 'Invalid', 'project_dir': str(target), 'template': template})
    assert refused.status_code == 422 and not target.exists()
    assert api.get('/api/project/current').json()['id'] == original['id']


def test_changed_template_hash_and_non_integer_policy_refuse_before_writes(tmp_path):
    api, _, original = client(tmp_path)
    template = api.get('/api/project/template').json()
    template['task'] = 'anomaly'
    target = tmp_path / 'changed'
    assert api.post('/api/project/create', json={'name': 'Changed', 'project_dir': str(target), 'template': template}).status_code == 422
    assert not target.exists() and api.get('/api/project/current').json()['id'] == original['id']
    from backend.engine.project_templates import _digest
    template['retention_policy']['retention_days'] = True
    template['content_sha256'] = _digest(template)
    assert api.post('/api/project/create', json={'name': 'Changed', 'project_dir': str(target), 'template': template}).status_code == 422
    assert not target.exists()


def test_backup_refuses_secret_without_publishing_archive_or_echoing_value(tmp_path):
    api, root, original = client(tmp_path)
    sensitive = root / 'reports' / 'credentials.json'
    sensitive.write_text(json.dumps({'password': 'reserved-local-fixture-secret'}))
    before = hashlib.sha256(sensitive.read_bytes()).hexdigest()
    refused = api.post('/api/project/backup', json={'destination_dir': str(tmp_path / 'backups')})
    assert refused.status_code == 422, refused.text
    assert 'reserved-local-fixture-secret' not in refused.text
    assert not list((tmp_path / 'backups').glob('*.mvision.zip'))
    assert hashlib.sha256(sensitive.read_bytes()).hexdigest() == before
    assert api.get('/api/project/current').json()['id'] == original['id']


def test_portable_service_archive_removes_host_secrets_and_restores_fresh_inactive_service(tmp_path):
    api, root, _ = client(tmp_path)
    directory = root / 'runtime_service'; directory.mkdir()
    service = {'pid': None, 'port': 43111, 'token': 'reserved-host-fixture-secret', 'native_label': 'owned.fixture', 'package_path': str(root / 'packages' / 'candidate')}
    original = json.dumps(service).encode(); (directory / 'service.json').write_bytes(original)
    install = directory / 'install'; install.mkdir(); (install / 'owned.plist').write_text('reserved-host-fixture-secret')
    response = api.post('/api/project/backup', json={'destination_dir': str(tmp_path / 'backups')})
    assert response.status_code == 200, response.text
    with ZipFile(response.json()['archive_path']) as archive:
        saved = json.loads(archive.read('project/runtime_service/service.json'))
        assert 'token' not in saved and 'native_label' not in saved
        assert not any(name.startswith('project/runtime_service/install/') for name in archive.namelist())
        manifest = json.loads(archive.read('backup-manifest.json'))
        assert manifest['portability']['host_service_credentials_removed'] is True
    target = tmp_path / 'restored'
    restored = api.post('/api/project/restore', json={'archive_path': response.json()['archive_path'], 'target_dir': str(target)})
    assert restored.status_code == 200, restored.text
    new_service = json.loads((target / 'runtime_service/service.json').read_text())
    assert new_service['token'] != service['token'] and new_service['pid'] is None
    assert (directory / 'service.json').read_bytes() == original


def test_fleet_snapshot_erases_token_bytes_but_preserves_target_and_original(tmp_path):
    api, root, _ = client(tmp_path)
    from backend.engine.fleet import FleetRegistry
    fleet = FleetRegistry(root)
    target = fleet.save_target(name='Owned fixture', url='http://127.0.0.1:43210', token='reserved-fleet-fixture-capability')
    original = fleet.path.read_bytes()
    response = api.post('/api/project/backup', json={'destination_dir': str(tmp_path / 'backups')})
    assert response.status_code == 200, response.text
    with ZipFile(response.json()['archive_path']) as archive:
        assert b'reserved-fleet-fixture-capability' not in archive.read('project/fleet/agents.sqlite3')
    restored_root = tmp_path / 'fleet-restored'
    assert api.post('/api/project/restore', json={'archive_path': response.json()['archive_path'], 'target_dir': str(restored_root)}).status_code == 200
    restored_fleet = FleetRegistry(restored_root)
    restored = restored_fleet.targets()
    assert restored[0]['target_id'] == target['target_id'] and restored[0]['token_set'] is False
    with pytest.raises(ValueError, match='credentials are unavailable'):
        restored_fleet.secret(target['target_id'])
    assert fleet.path.read_bytes() == original


def test_database_credentials_refuse_and_legacy_restore_cannot_activate_secret_records(tmp_path):
    import sqlite3
    api, root, original = client(tmp_path)
    database = root / 'reports' / 'local-auth.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE credentials(password TEXT)')
        db.execute("INSERT INTO credentials VALUES('reserved-local-fixture-secret')")
    refused = api.post('/api/project/backup', json={'destination_dir': str(tmp_path / 'backups')})
    assert refused.status_code == 422 and 'reserved-local-fixture-secret' not in refused.text
    # An externally supplied v1 inventory can be self-consistent but unsafe.
    # Refuse before activation and remove only the owned restoration staging.
    archive_path = tmp_path / 'unsafe.mvision.zip'
    project_bytes = (root / 'project.json').read_bytes()
    secret_bytes = json.dumps({'access_token': 'reserved-restored-fixture-secret'}).encode()
    files = {'project/project.json': project_bytes, 'project/reports/unsafe.json': secret_bytes}
    manifest = {'format': 'modu-project-backup-v1', 'project_id': original['id'],
                'original_project_dir': str(root), 'original_source_dir': None,
                'files': [{'member': member, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()} for member, content in files.items()]}
    with ZipFile(archive_path, 'w') as archive:
        for member, content in files.items(): archive.writestr(member, content)
        archive.writestr('backup-manifest.json', json.dumps(manifest))
    before = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    target = tmp_path / 'unsafe-restored'
    rejected = api.post('/api/project/restore', json={'archive_path': str(archive_path), 'target_dir': str(target)})
    assert rejected.status_code == 422 and 'reserved-restored-fixture-secret' not in rejected.text
    assert not target.exists() and not list(tmp_path.glob('.unsafe-restored.restore-*'))
    assert api.get('/api/project/current').json()['id'] == original['id']
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == before


def test_relocated_prepared_ocr_verification_reads_bound_labels_not_unmodified_original(tmp_path):
    import torch
    from PIL import Image
    from backend.engine.ocr import SmallCTCOCR, load_ocr_manifest
    from backend.engine.training_provenance import bind_family_training, persist_model_binding
    from backend.engine.specialized_models import resolve_specialized_checkpoint
    api, root, _ = client(tmp_path)
    source = tmp_path / 'source'; source.mkdir()
    rows = []
    for index, split in enumerate(('train', 'val', 'test')):
        Image.new('RGB', (64, 32), (40 + index * 60, 80, 100)).save(source / f'{index}.png')
        rows.append({'image': f'{index}.png', 'text': 'A', 'split': split})
    project = api.put('/api/project/update', json={'source_dataset_dir': str(source)}).json()
    prepared = api.post('/api/ocr/prepare', json={'source_dataset_path': str(source), 'samples': rows}).json()
    dataset = Path(prepared['dataset_path']); manifest = load_ocr_manifest(dataset)
    binding = bind_family_training(project, dataset, 'ocr')
    job = 'e' * 32; directory = root / 'models/ocr' / job; directory.mkdir(parents=True)
    payload = {'task': 'ocr', 'version': 1, 'alphabet': 'A', 'image_size': [32, 64],
               'model_state_dict': SmallCTCOCR(1).state_dict(), 'dataset_provenance': manifest.provenance}
    torch.save(payload, directory / 'best_model.pt')
    (directory / 'model_meta.json').write_text(json.dumps({'task': 'ocr', 'dataset_path': str(dataset), 'dataset_provenance': manifest.provenance}))
    persist_model_binding(directory, binding)
    checkpoint, _ = resolve_specialized_checkpoint(root / 'models', job, 'ocr', source)
    expected = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    backup = api.post('/api/project/backup', json={'destination_dir': str(tmp_path / 'backups')}).json()
    target = tmp_path / 'ocr-restored'
    assert api.post('/api/project/restore', json={'archive_path': backup['archive_path'], 'target_dir': str(target)}).status_code == 200
    new_source = target / 'dataset/restored_source'
    assert not (source / 'ocr.json').exists() and not (new_source / 'ocr.json').exists()
    restored, metadata = resolve_specialized_checkpoint(target / 'models', job, 'ocr', new_source)
    assert hashlib.sha256(restored.read_bytes()).hexdigest() == expected
    assert Path(metadata['dataset_path']).is_relative_to(target / 'dataset/ocr')
    changed = new_source / '0.png'; changed.write_bytes(b'changed original')
    with pytest.raises((ValueError, OSError)):
        resolve_specialized_checkpoint(target / 'models', job, 'ocr', new_source)
