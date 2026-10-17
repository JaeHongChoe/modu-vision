"""Real prepare response matches the saved metadata consumed before UI reload."""
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


def _files(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def test_prepare_response_contains_the_complete_saved_dataset_metadata(tmp_path):
    from backend.main import create_app

    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    try:
        created = client.post('/api/project/create', json={'name': 'Prepared metadata', 'task': 'segmentation'})
        assert created.status_code == 200, created.text
        source = tmp_path / 'source'
        source.mkdir()
        for index in range(3):
            image = source / f'{index}.png'
            Image.new('RGB', (32, 32), (index, 10, 20)).save(image)
            image.with_suffix('.json').write_text(json.dumps({
                'imagePath': image.name, 'imageWidth': 32, 'imageHeight': 32,
                'shapes': [{'label': 'chip', 'shape_type': 'rectangle', 'points': [[0, 0], [8, 8]]}],
            }))
        original = _files(source)
        updated = client.put('/api/project/update', json={'source_dataset_dir': str(source)})
        assert updated.status_code == 200, updated.text
        response = client.post('/api/patch-classification/prepare', json={
            'patch_size': 16, 'stride': 16, 'normal_class': 'NORMAL', 'minimum_overlap': .05,
        })
        assert response.status_code == 200, response.text
        prepared = response.json()
        dataset = Path(prepared['dataset_path'])
        assert dataset.is_relative_to(Path(created.json()['dataset_dir']))
        stored_before_reads = _files(Path(created.json()['project_dir']))
        manifest_response = client.get('/api/patch-classification/manifest', params={'dataset_path': str(dataset)})
        datasets_response = client.get('/api/patch-classification/datasets')
        assert manifest_response.status_code == datasets_response.status_code == 200
        manifest = manifest_response.json()
        assert datasets_response.json() == {'datasets': [manifest]}
        fields = ('dataset_path', 'classes', 'normal_class', 'patch_size', 'stride', 'patch_count', 'provenance')
        # The renderer installs this exact POST response directly in its dataset state.
        assert {key: prepared.get(key) for key in fields} == manifest
        assert manifest['classes'] == ['NORMAL', 'chip']
        assert manifest['normal_class'] == 'NORMAL'
        assert manifest['patch_size'] == manifest['stride'] == 16
        assert manifest['patch_count'] == 12
        assert prepared['split_counts'] == manifest['provenance']['split_counts'] == {'train': 4, 'val': 4, 'test': 4}
        assert prepared['warnings'] == [] and prepared['source_dataset_path'] == str(source.resolve())
        saved = json.loads((dataset / 'patches.json').read_text())
        assert hashlib.sha256((dataset / 'patches.json').read_bytes()).hexdigest() == manifest['provenance']['manifest_sha256']
        assert len(saved['patches']) == 12
        for relative, digest in manifest['provenance']['source_sha256'].items():
            copied = dataset / relative
            assert hashlib.sha256(copied.read_bytes()).hexdigest() == digest == original[copied.name]
        assert _files(source) == original and len(original) == 6
        assert _files(Path(created.json()['project_dir'])) == stored_before_reads
        jobs = client.get('/api/training/jobs')
        assert jobs.status_code == 200 and jobs.json()['jobs'] == []
    finally:
        client.close()
