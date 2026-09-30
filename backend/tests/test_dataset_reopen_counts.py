"""Dataset import/reopen must not treat repeated polygons as extra images."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.mark.parametrize('task,expected', [('segmentation', 2), ('detection', 3)])
def test_flat_labelme_import_keeps_image_and_object_count_contracts(tmp_path, task, expected):
    from backend.main import create_app

    source = tmp_path / 'source'
    source.mkdir()
    for index, labels in enumerate([['Bow', 'Bow', 'Crack'], ['Bow'], []]):
        Image.new('RGB', (32, 32), (index, 0, 0)).save(source / f'{index}.png')
        if labels:
            (source / f'{index}.json').write_text(json.dumps({
                'imagePath': f'{index}.png', 'imageWidth': 32, 'imageHeight': 32,
                'shapes': [{'label': label, 'shape_type': 'polygon',
                            'points': [[1, 1], [10, 1], [10, 10]]} for label in labels],
            }))
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
    app = create_app(project_dir=str(tmp_path / 'projects'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    created = api.post('/api/project/create', json={'name': 'Reopened', 'task': task})
    assert created.status_code == 200, created.text
    project = created.json()
    api.put('/api/project/update', json={'source_dataset_dir': str(source)})
    imported = api.post('/api/dataset/import', json={'folder_path': str(source), 'task': task, 'validate_images': False})
    assert imported.status_code == 200, imported.text
    assert imported.json()['classes'] == {'Bow': expected, 'Crack': 1}
    assert imported.json()['total_images'] == 2
    assert imported.json()['source_images'] == 3
    reopened = api.post('/api/project/open', json={'project_dir': project['project_dir']})
    assert reopened.status_code == 200, reopened.text
    summary = api.get('/api/dataset/metadata/statistics')
    assert summary.status_code == 200, summary.text
    assert summary.json()['classes']['Bow'] == {'count': 2, 'ratio': 2 / 3}
    assert summary.json()['classes']['Crack'] == {'count': 1, 'ratio': 1 / 3}
    assert summary.json()['total'] == 3
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
