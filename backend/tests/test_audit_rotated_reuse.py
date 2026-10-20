"""Step-2 rotated boxes reach the rotated detection workbench (audit F-16 / B3).

The endpoint only reads; the workbench's own prepare path still validates and copies. Angles fold into [-90, 90).
"""
from PIL import Image


def _setup(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert client.post('/api/project/create', json={'name': 'Rotated', 'task': 'detection'}).status_code == 200
    source = tmp_path / 'source'
    for split in ('train', 'val'):
        (source / split).mkdir(parents=True)
        for i in range(2):
            Image.new('RGB', (64, 48), (i * 40, 0, 0)).save(source / split / f'{split}_{i}.png')
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    return client, source


def _save(client, image, annotations):
    response = client.post('/api/annotations/save', json={'image_id': image.stem, 'image_path': str(image), 'actor': 'Kim',
                                                          'image_width': 64, 'image_height': 48, 'annotations': annotations})
    assert response.status_code == 200, response.text


def test_step2_rotated_boxes_become_workbench_rows_with_folded_angles(tmp_path):
    client, source = _setup(tmp_path)
    _save(client, source / 'train' / 'train_0.png', [
        {'type': 'rotated_bbox', 'label': 'chip', 'rotated_bbox': [20, 20, 10, 6, 120]},
        {'type': 'bbox', 'label': 'other', 'bbox': [1, 1, 5, 5]}])
    _save(client, source / 'val' / 'val_1.png', [{'type': 'rotated_bbox', 'label': 'chip', 'rotated_bbox': [30, 24, 8, 4, -30], 'direction_deg': 45}])
    response = client.get('/api/dataset/rotated-boxes')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['counts'] == {'images': 2, 'objects': 2} and body['split_source'] == 'folders' and body['unlabeled_images'] == 2
    rows = {row['image']: row for row in body['samples']}
    assert rows['train/train_0.png']['split'] == 'train'
    assert rows['train/train_0.png']['objects'] == [{'label': 'chip', 'box': {'cx': 20.0, 'cy': 20.0, 'width': 10.0, 'height': 6.0, 'angle_deg': -60.0}}]
    # Only one of the two boxes has a direction, so directions are left out rather than guessed for the other.
    assert body['directions_dropped'] == 1 and 'direction_deg' not in rows['val/val_1.png']['objects'][0]
    assert rows['val/val_1.png']['split'] == 'val'
    # The rows are exactly what the workbench prepares from: the existing endpoint accepts them unchanged.
    prepared = client.post('/api/rotated-detection/prepare', json={'source_dataset_path': str(source), 'samples': body['samples']})
    assert prepared.status_code == 200, prepared.text


def test_without_a_source_the_reuse_is_refused(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert client.post('/api/project/create', json={'name': 'Empty', 'task': 'detection'}).status_code == 200
    assert client.get('/api/dataset/rotated-boxes').status_code == 409
