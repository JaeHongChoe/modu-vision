"""Per-image classification classes chosen in step 2 (audit A2 / F-04).

The class a user picks for an image is what the gallery shows, what step 1 counts and what training reads; the file
is never moved, and a change is a data change for staleness. Box labels never replace the class shown.
"""
from pathlib import Path

from PIL import Image


def _setup(tmp_path, task='classification'):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert client.post('/api/project/create', json={'name': 'Classes', 'task': task}).status_code == 200
    source = tmp_path / 'source'
    for cls, shade in (('OK', 0), ('NG', 200)):
        (source / cls).mkdir(parents=True)
        for i in range(6):
            Image.new('RGB', (32, 32), (shade, i * 9, 0)).save(source / cls / f'{cls.lower()}_{i:02d}.png')
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    assert client.post('/api/dataset/import', json={'folder_path': str(source), 'task': task, 'validate_images': True}).status_code == 200
    return client, source


def _listing(client, source):
    rows = client.get('/api/dataset/images', params={'folder_path': str(source), 'task': 'classification', 'limit': 100}).json()['items']
    return {Path(row['file_path']).name: row for row in rows}


def test_a_chosen_class_reaches_the_gallery_counts_and_training_without_moving_the_file(tmp_path):
    client, source = _setup(tmp_path)
    image = source / 'OK' / 'ok_00.png'
    before = image.read_bytes()
    response = client.put('/api/dataset/metadata/class-override', json={'image_path': str(image), 'class_name': 'NG', 'actor': 'Kim'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert {key: body[key] for key in ('relative_path', 'class', 'class_source', 'folder_class', 'changed')} == {
        'relative_path': 'OK/ok_00.png', 'class': 'NG', 'class_source': 'override', 'folder_class': 'OK', 'changed': True}
    assert body['metadata']['relative_path'] == 'OK/ok_00.png' and body['metadata']['audit'][-1]['action'] == 'class_changed'
    assert image.read_bytes() == before and image.parent.name == 'OK', 'the file stays where it was, unchanged'

    row = _listing(client, source)['ok_00.png']
    assert (row['label'], row['class_source'], row['folder_label']) == ('NG', 'override', 'OK')
    counts = client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification',
                                                      'validate_images': False}).json()['classes']
    assert counts == {'OK': 5, 'NG': 7}

    # Training reads the same classes inside the project scope (the scope the worker re-enters).
    from backend.engine.annotation_storage import (set_request_project_root, reset_request_project_root,
                                                   set_request_annotation_root, reset_request_annotation_root)
    from backend.engine.dataset_loaders import ClassificationDataset
    project = client.get('/api/project/current').json()
    ptoken = set_request_project_root(Path(project['project_dir']))
    atoken = set_request_annotation_root(Path(project['annotations_dir']))
    try:
        dataset = ClassificationDataset(source, split=None)
        labels = {path.name: dataset.classes[label] for path, label in dataset.samples}
    finally:
        reset_request_annotation_root(atoken)
        reset_request_project_root(ptoken)
    assert labels['ok_00.png'] == 'NG' and labels['ok_01.png'] == 'OK'


def test_returning_to_the_folder_class_and_staleness(tmp_path):
    from backend.engine.annotation_storage import (set_request_project_root, reset_request_project_root,
                                                   set_request_annotation_root, reset_request_annotation_root)
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    client, source = _setup(tmp_path)
    project = client.get('/api/project/current').json()

    def fingerprint():
        ptoken = set_request_project_root(Path(project['project_dir']))
        atoken = set_request_annotation_root(Path(project['annotations_dir']))
        try:
            return fingerprint_dataset(source)
        finally:
            reset_request_annotation_root(atoken)
            reset_request_project_root(ptoken)

    original = fingerprint()
    image = source / 'NG' / 'ng_03.png'
    assert client.put('/api/dataset/metadata/class-override', json={'image_path': str(image), 'class_name': 'OK'}).status_code == 200
    changed = fingerprint()
    assert changed != original, 'a chosen class is a data change (models and evaluations become stale)'
    back = client.put('/api/dataset/metadata/class-override', json={'image_path': str(image), 'class_name': 'NG'}).json()
    assert back['class_source'] == 'folder' and back['changed'] is True
    assert _listing(client, source)['ng_03.png']['class_source'] == 'folder'


def test_box_labels_never_replace_the_class_shown_for_a_classification_image(tmp_path):
    client, source = _setup(tmp_path)
    image = source / 'NG' / 'ng_00.png'
    saved = client.post('/api/annotations/save', json={'image_id': 'ng_00', 'image_path': str(image), 'actor': 'Lee',
                                                       'image_width': 32, 'image_height': 32,
                                                       'annotations': [{'type': 'bbox', 'label': 'Solder Bridge', 'bbox': [2, 2, 12, 12]}]})
    assert saved.status_code == 200, saved.text
    row = _listing(client, source)['ng_00.png']
    assert row['label'] == 'NG' and row['class_source'] == 'folder'


def test_class_choice_is_refused_outside_classification_and_outside_the_folder(tmp_path):
    client, source = _setup(tmp_path, task='anomaly')
    refused = client.put('/api/dataset/metadata/class-override', json={'image_path': str(source / 'OK' / 'ok_00.png'), 'class_name': 'NG'})
    assert refused.status_code == 409
    client2, source2 = _setup(tmp_path / 'b')
    outside = tmp_path / 'outside.png'
    Image.new('RGB', (8, 8)).save(outside)
    assert client2.put('/api/dataset/metadata/class-override', json={'image_path': str(outside), 'class_name': 'NG'}).status_code == 422
    assert client2.put('/api/dataset/metadata/class-override', json={'image_path': str(source2 / 'OK' / 'ok_00.png'),
                                                                     'class_name': 'a/b'}).status_code == 422


def test_a_class_change_hands_the_editor_the_new_revision_so_its_next_label_save_is_not_refused(tmp_path):
    """Live check (2026-10-21): choosing a class and then drawing a region failed to save as another worker's edit,
    because the editor still held the revision from before the class change."""
    client, source = _setup(tmp_path)
    image = source / 'NG' / 'ng_00.png'
    opened = client.get('/api/annotations/ng_00', params={'file_path': str(image)}).json()['metadata']
    chosen = client.put('/api/dataset/metadata/class-override', json={'image_path': str(image), 'class_name': 'OK', 'actor': 'Kim'}).json()
    back = client.put('/api/dataset/metadata/class-override', json={'image_path': str(image), 'class_name': None, 'actor': 'Kim'}).json()
    assert back['metadata']['revision'] == chosen['metadata']['revision'] + 1 == opened['revision'] + 2
    box = {'id': 'b1', 'type': 'bbox', 'label': 'scratch', 'bbox': [2, 2, 12, 12]}
    stale = client.post('/api/annotations/save', json={'image_id': 'ng_00', 'image_path': str(image), 'annotations': [box],
                                                        'image_width': 32, 'image_height': 32, 'expected_revision': opened['revision']})
    assert stale.status_code == 409, 'a revision from before the change is still refused'
    saved = client.post('/api/annotations/save', json={'image_id': 'ng_00', 'image_path': str(image), 'annotations': [box],
                                                        'image_width': 32, 'image_height': 32, 'expected_revision': back['metadata']['revision']})
    assert saved.status_code == 200, saved.text
