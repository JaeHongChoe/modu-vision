"""Dataset import/reopen must not treat repeated polygons as extra images."""
import hashlib
import json
import base64
import io
from pathlib import Path

import pytest
import numpy as np
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


def _brush(label, category_id, *, start=2, empty=False):
    rgba = np.zeros((32, 32, 4), dtype=np.uint8)
    if not empty:
        rgba[start:start + 4, start:start + 4] = [255, 100, 0, 255]
    stream = io.BytesIO()
    Image.fromarray(rgba).save(stream, format='PNG')
    return {'type': 'brush_mask', 'label': label, 'category_id': category_id,
            'mask_rle': 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()}


def _brush_project(tmp_path):
    from backend.main import create_app
    source = tmp_path / 'source'
    source.mkdir()
    image = source / 'part.png'
    Image.new('RGB', (32, 32), 'white').save(image)
    image.with_suffix('.json').write_text(json.dumps({'imagePath': image.name,
        'imageWidth': 32, 'imageHeight': 32, 'shapes': [{'label': 'Original',
        'shape_type': 'rectangle', 'points': [[1, 1], [5, 5]]}]}))
    app = create_app(project_dir=str(tmp_path / 'projects'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    result = api.post('/api/project/create', json={'name': 'Brush counts', 'task': 'segmentation'})
    assert result.status_code == 200, result.text
    project = result.json()
    assert api.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    return api, project, source, image


def _save_brushes(api, image, annotations, **extra):
    result = api.post('/api/annotations/save', json={'image_id': image.stem,
        'image_path': str(image), 'image_width': 32, 'image_height': 32,
        'annotations': annotations, **extra})
    assert result.status_code == 200, result.text
    result = api.get('/api/annotations/part', params={'file_path': str(image)})
    assert result.status_code == 200, result.text
    return Path(result.json()['mask_file'])


def _protected_hashes(source, project):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in (source, Path(project['project_dir']))
            for p in root.rglob('*') if p.is_file() and p.suffix in {'.png', '.json'}}


def _assert_brush_counts(api, project, source, expected):
    # A deliberately rewritten palette-only fixture must reconcile its external
    # annotation hash before the read-only baseline, including review metadata.
    primed = api.get('/api/dataset/metadata')
    assert primed.status_code == 200, primed.text
    before = _protected_hashes(source, project)
    for reopen in (False, True):
        if reopen:
            response = api.post('/api/project/open', json={'project_dir': project['project_dir']})
            assert response.status_code == 200, response.text
        imported = api.post('/api/dataset/import', json={'folder_path': str(source),
            'task': 'segmentation', 'validate_images': False})
        assert imported.status_code == 200, imported.text
        assert imported.json()['classes'] == expected
        current = api.get('/api/dataset/current-summary', params={'folder_path': str(source), 'task': 'segmentation'})
        assert current.status_code == 200, current.text
        assert current.json()['classes'] == expected
        statistics = api.get('/api/dataset/metadata/statistics')
        assert statistics.status_code == 200, statistics.text
        assert statistics.json()['classes'] == {name: {'count': count, 'ratio': count}
                                                for name, count in expected.items()}
        assert statistics.json()['labeling']['labeled']['count'] == 1
    assert _protected_hashes(source, project) == before


@pytest.mark.parametrize('case,expected', [
    ('brush', {'Scratch': 1}),
    ('mixed', {'Box': 1, 'Scratch': 1, 'Particle': 1}),
    ('zero', {}),
    ('palette', {'Scratch': 1, 'Particle': 1}),
])
def test_saved_brush_foreground_counts_survive_import_statistics_and_reopen(tmp_path, case, expected):
    api, project, source, image = _brush_project(tmp_path)
    annotations = [_brush('Scratch', 2, empty=case == 'zero')]
    if case in {'mixed', 'palette'}:
        annotations += [_brush('Scratch', 2, start=10), _brush('Particle', 3, start=20)]
    if case == 'mixed':
        annotations += [{'type': 'bbox', 'label': 'Box', 'category_id': 1, 'bbox': [1, 20, 5, 25]}]
    mask = _save_brushes(api, image, annotations, mask_classes=[
        {'id': 0, 'name': 'background', 'color': '#000000'},
        {'id': 2, 'name': 'Scratch', 'color': '#ff8800'},
        {'id': 3, 'name': 'Particle', 'color': '#0088ff'},
        {'id': 4, 'name': 'Unused', 'color': '#8800ff'}])
    if case == 'palette':
        overlay = mask.parent.parent / 'part.json'
        saved = json.loads(overlay.read_text())
        saved['annotations'] = []
        overlay.write_text(json.dumps(saved))
    _assert_brush_counts(api, project, source, expected)


def test_brush_counts_follow_active_labelset_raster_and_palette(tmp_path):
    api, project, source, image = _brush_project(tmp_path)
    _save_brushes(api, image, [_brush('Scratch', 2)])
    second = api.post('/api/project/labelsets', json={'name': 'Other brush review'}).json()['id']
    assert api.put(f'/api/project/labelsets/{second}/activate').status_code == 200
    _save_brushes(api, image, [_brush('Particle', 3)])
    _assert_brush_counts(api, project, source, {'Particle': 1})
    assert api.put('/api/project/labelsets/default/activate').status_code == 200
    _assert_brush_counts(api, project, source, {'Scratch': 1})
