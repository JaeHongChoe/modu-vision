"""Real ASGI prepared-data scope; tiny declared checkpoint, no training/inference."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def _files(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


@pytest.fixture
def prepared_scope(tmp_path):
    from backend.main import create_app
    import torch

    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    created = client.post('/api/project/create', json={'name': 'Prepared parent scope', 'task': 'segmentation'})
    assert created.status_code == 200, created.text
    project = created.json()
    source = tmp_path / 'original'
    source.mkdir()
    for i in range(3):
        image = source / f'{i}.png'
        Image.new('RGB', (32, 32), (i, 10, 20)).save(image)
        image.with_suffix('.json').write_text(json.dumps({'imagePath': image.name, 'imageWidth': 32, 'imageHeight': 32,
            'shapes': [{'label': 'chip', 'shape_type': 'rectangle', 'points': [[0, 0], [8, 8]]}]}))
    updated = client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    assert updated.status_code == 200, updated.text
    response = client.post('/api/patch-classification/prepare', json={'patch_size': 16, 'stride': 16})
    assert response.status_code == 200, response.text
    prepared = Path(response.json()['dataset_path'])
    assert response.json()['patch_count'] == 12
    directory = Path(project['models_dir']) / 'job_parent01'
    directory.mkdir()
    # This proves request scoping and the original completed-parent reader only.
    # The one-tensor fixture is explicitly not a trained/authentic DINO model.
    checkpoint = directory / 'best_model.pt'
    torch.save({'task': 'patch_classification', 'classes': ['OK', 'chip'], 'backbone': 'dinov3_vits16',
                'model_state_dict': {'fixture_weight': torch.zeros(1)}}, checkpoint)
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    (directory / 'model_meta.json').write_text(json.dumps({'task': 'patch_classification', 'classes': ['OK', 'chip'],
        'backbone': 'dinov3_vits16', 'checkpoint_sha256': digest}))
    (directory / 'job_receipt.json').write_text(json.dumps({'job_id': 'job_parent01', 'task': 'patch_classification',
        'status': 'completed', 'source_dataset_path': str(source), 'dataset_fingerprint': 'v1:synthetic-fixture',
        'checkpoint_sha256': digest}))
    before = {'source': _files(source), 'project': _files(Path(project['project_dir']))}
    try:
        yield client, project, source, prepared, digest
    finally:
        try:
            assert _files(source) == before['source']
            # Negatives may create declared additional fixtures, but no original
            # file may be modified or removed by a candidate lookup.
            after = _files(Path(project['project_dir']))
            assert all(after.get(name) == digest for name, digest in before['project'].items())
        finally:
            client.close()


def _parents(client, path, task='patch_classification'):
    return client.get('/api/training/warm-start-parents', params={'dataset_path': str(path),
        'task': task, 'preset': 'fast', 'backbone': 'dinov3_vits16'})


def test_patch_parent_list_accepts_owned_prepared_classes_and_original_source_receipt(prepared_scope):
    client, project, source, prepared, digest = prepared_scope
    assert prepared.is_relative_to(Path(project['dataset_dir'])) and prepared != source
    before = _files(Path(project['project_dir']))
    response = _parents(client, prepared)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['total'] == 1
    assert [(p['job_id'], p['classes'], p['architecture'], p['checkpoint_sha256'], p['dataset_fingerprint'])
            for p in value['parents']] == [('job_parent01', ['OK', 'chip'], 'patch_classification:dinov3_vits16',
                                          digest, 'v1:synthetic-fixture')]
    assert _files(Path(project['project_dir'])) == before


@pytest.mark.parametrize('kind', ['foreign_root', 'raw_symlink', 'foreign_manifest_source'])
def test_patch_parent_list_refuses_foreign_or_linked_prepared_data(prepared_scope, tmp_path, kind):
    client, project, source, prepared, _ = prepared_scope
    if kind == 'foreign_root':
        requested = tmp_path / 'foreign-project' / 'dataset' / 'patch' / 'prepared'
        shutil.copytree(prepared, requested)
        expected = 'Patch prepared data must belong to the active project'
    elif kind == 'raw_symlink':
        requested = prepared.with_name('raw-linked-prepared')
        requested.symlink_to(prepared, target_is_directory=True)
        expected = 'Patch prepared data must belong to the active project'
    else:
        requested = prepared.with_name('different-source-prepared')
        shutil.copytree(prepared, requested)
        other_source = tmp_path / 'other-original'
        shutil.copytree(source, other_source)
        manifest = requested / 'patches.json'
        value = json.loads(manifest.read_text())
        value['source_dataset_path'] = str(other_source)
        manifest.write_text(json.dumps(value))
        expected = 'Patch input must match the active project source'
    before = _files(Path(project['project_dir']))
    response = _parents(client, requested)
    assert response.status_code == 422, response.text
    assert response.json() == {'detail': expected}
    assert _files(Path(project['project_dir'])) == before


def test_general_parent_source_scope_does_not_adopt_patch_prepared_paths(prepared_scope):
    client, project, _, prepared, _ = prepared_scope
    before = _files(Path(project['project_dir']))
    response = _parents(client, prepared, task='classification')
    assert response.status_code == 409, response.text
    assert response.json() == {'detail': "Warm-start source must match the current project's dataset"}
    assert _files(Path(project['project_dir'])) == before
