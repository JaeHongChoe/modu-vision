"""A corrupt image in the registered source is listed as corrupt; the gallery and statistics keep working."""
from fastapi.testclient import TestClient
from PIL import Image


def test_one_corrupt_image_does_not_break_the_gallery_or_the_statistics(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    source = tmp_path / 'dataset'
    for label, color in (('ok', 'white'), ('ng', 'black')):
        (source / label).mkdir(parents=True)
        Image.new('RGB', (32, 32), color).save(source / label / f'sample-{label}.png')
    (source / 'ng' / 'broken.png').write_bytes(b'not an image')
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        client.post('/api/project/create', json={'name': 'Corrupt', 'task': 'classification'})
        assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
        gallery = client.get('/api/dataset/images', params={'folder_path': str(source), 'task': 'classification', 'limit': 50})
        assert gallery.status_code == 200, gallery.text
        rows = {row['file_name']: row for row in gallery.json()['items']}
        assert set(rows) == {'sample-ok.png', 'sample-ng.png', 'broken.png'}
        assert rows['broken.png']['width'] is None and rows['sample-ok.png']['width'] == 32
        statistics = client.get('/api/dataset/metadata/statistics', params={'folder_path': str(source)})
        assert statistics.status_code == 200, statistics.text


def _client(tmp_path, monkeypatch, source, task):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    client.__enter__()
    client.post('/api/project/create', json={'name': 'Large', 'task': task})
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    return client


def test_an_image_above_the_pixel_limit_is_listed_without_geometry(tmp_path, monkeypatch):
    # Pillow refuses images above twice MAX_IMAGE_PIXELS with DecompressionBombError (not an OSError); the limit is
    # lowered so a 64x64 image stands in for a 20000x10000 one.
    source = tmp_path / 'dataset'
    for label in ('ok', 'ng'):
        (source / label).mkdir(parents=True)
        Image.new('RGB', (8, 8), 'white').save(source / label / f'small-{label}.png')
    Image.new('RGB', (64, 64), 'black').save(source / 'ng' / 'huge.png')
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 1000)
    client = _client(tmp_path, monkeypatch, source, 'classification')
    try:
        gallery = client.get('/api/dataset/images', params={'folder_path': str(source), 'task': 'classification', 'limit': 50})
        assert gallery.status_code == 200, gallery.text
        rows = {row['file_name']: row for row in gallery.json()['items']}
        assert rows['huge.png']['width'] is None and rows['small-ok.png']['width'] == 8
        statistics = client.get('/api/dataset/metadata/statistics', params={'folder_path': str(source)})
        assert statistics.status_code == 200, statistics.text
    finally:
        client.__exit__(None, None, None)


def test_the_split_manifest_listing_keeps_a_corrupt_image_without_geometry(tmp_path, monkeypatch):
    import json
    source = tmp_path / 'labelme'
    source.mkdir()
    for index in range(6):
        Image.new('RGB', (16, 16), 'white').save(source / f'{index}.png')
        (source / f'{index}.json').write_text(json.dumps({'imagePath': f'{index}.png', 'imageWidth': 16, 'imageHeight': 16,
                                                          'shapes': [{'label': 'scratch', 'shape_type': 'rectangle',
                                                                      'points': [[1, 1], [5, 5]]}]}))
    (source / '5.png').write_bytes(b'not an image')  # its annotation stays: the listing binds it before opening
    other = tmp_path / 'registered'
    other.mkdir()
    client = _client(tmp_path, monkeypatch, other, 'detection')
    try:
        split = client.post('/api/dataset/split', json={'folder_path': str(source), 'task': 'detection', 'train_ratio': 0.5,
                                                        'val_ratio': 0.5})
        assert split.status_code == 200, split.text
        gallery = client.get('/api/dataset/images', params={'folder_path': str(source), 'task': 'detection', 'limit': 50})
        assert gallery.status_code == 200, gallery.text
        rows = {row['file_name']: row for row in gallery.json()['items']}
        assert '5.png' in rows and rows['5.png']['width'] is None and rows['0.png']['width'] == 16
    finally:
        client.__exit__(None, None, None)


def test_an_export_names_the_image_that_has_no_size():
    import pytest
    from backend.engine.annotation_formats import export_annotations
    from backend.engine.mask_exchange import annotation_pixels
    row = {'file_name': 'lot/broken.png', 'width': None, 'height': None, 'annotations': []}
    with pytest.raises(ValueError, match='lot/broken.png'):
        export_annotations([row], 'coco')
    with pytest.raises(ValueError, match='lot/broken.png'):
        annotation_pixels(row)
