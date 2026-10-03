"""Damaged headers bind no source labels; temporary read failures stay visible."""
import errno
import io
import json

import pytest
from PIL import Image

from backend.engine import grouped_dataset_views as views


def labelme_source(tmp_path):
    image = tmp_path / '검사.png'
    Image.new('RGB', (8, 8), 'white').save(image)
    labels = image.with_suffix('.json')
    labels.write_text(json.dumps({'shapes': [{'label': '찍힘', 'shape_type': 'rectangle',
        'points': [[1, 1], [4, 4]]}]}, ensure_ascii=False), encoding='utf-8')
    return image, labels


def test_real_truncated_png_header_without_labelme_size_binds_no_labels(tmp_path):
    image, labels = labelme_source(tmp_path)
    image.write_bytes(image.read_bytes()[:24])
    before = image.read_bytes(), labels.read_bytes()

    # Prove this fixture reaches Pillow's distinct header failure, rather than
    # accidentally exercising UnidentifiedImageError already covered elsewhere.
    with pytest.raises(OSError, match='^Truncated File Read$') as raised:
        Image.open(io.BytesIO(before[0]))
    assert type(raised.value) is OSError and raised.value.errno is None

    assert views._annotations(tmp_path, image) == ([], None)
    assert (image.read_bytes(), labels.read_bytes()) == before


def windows_sharing_error():
    failure = OSError('Truncated File Read')
    failure.winerror = 32
    return failure


@pytest.mark.parametrize('failure', [
    PermissionError('Truncated File Read'),
    FileNotFoundError('Truncated File Read'),
    OSError(errno.EIO, 'Truncated File Read'),
    OSError('share disconnected'),
    OSError('Truncated File Read: share disconnected'),
    pytest.param(windows_sharing_error(), id='Windows-sharing-violation'),
])
def test_temporary_or_unknown_read_failure_is_never_an_empty_annotation(tmp_path, monkeypatch, failure):
    image, labels = labelme_source(tmp_path)
    before = image.read_bytes(), labels.read_bytes()

    def fail(_path):
        raise failure

    monkeypatch.setattr(views, 'open_source_image', fail)
    with pytest.raises(type(failure)) as raised:
        views._annotations(tmp_path, image)
    assert raised.value is failure
    assert (image.read_bytes(), labels.read_bytes()) == before


def test_valid_korean_labelme_without_size_keeps_the_original_regions(tmp_path):
    image, labels = labelme_source(tmp_path)
    before = image.read_bytes(), labels.read_bytes()
    annotations, mask = views._annotations(tmp_path, image)
    assert mask is None
    assert len(annotations) == 1
    assert annotations[0]['label'] == '찍힘'
    assert annotations[0]['bbox'] == [1, 1, 4, 4]
    assert (image.read_bytes(), labels.read_bytes()) == before


def test_saved_split_gallery_keeps_truncated_source_visible_without_geometry(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    source = tmp_path / 'source'
    source.mkdir()
    broken, _ = labelme_source(source)
    broken.write_bytes(broken.read_bytes()[:24])
    for index in range(6):
        image = source / f'{index}.png'
        Image.new('RGB', (8, 8), 'white').save(image)
        image.with_suffix('.json').write_text(json.dumps({'imageWidth': 8, 'imageHeight': 8,
            'shapes': [{'label': '찍힘', 'shape_type': 'rectangle', 'points': [[1, 1], [4, 4]]}]},
            ensure_ascii=False), encoding='utf-8')
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user-data'))
    app = create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        assert client.post('/api/project/create', json={'name': 'Truncated source', 'task': 'detection'}).status_code == 200
        assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
        split = client.post('/api/dataset/split', json={'folder_path': str(source), 'task': 'detection',
            'train_ratio': 0.5, 'val_ratio': 0.5})
        assert split.status_code == 200, split.text
        gallery = client.get('/api/dataset/images', params={'folder_path': str(source), 'task': 'detection', 'limit': 50})
        assert gallery.status_code == 200, gallery.text
        rows = {row['file_name']: row for row in gallery.json()['items']}
        assert len(rows) == 7
        assert rows[broken.name]['width'] is None
        assert rows['0.png']['width'] == 8
        assert '찍힘' in rows['0.png']['labels']
    assert all((source / name).read_bytes() == content for name, content in before.items())
