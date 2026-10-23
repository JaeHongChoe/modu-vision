"""Data changes since training, told in words with a way back (audit F-09 / A3 / F-14).

The refusals stay as strict as before; these tests check that they name the data change, and that the on-demand
report sorts the difference into added, removed and changed images, label files, split and per-image classes.
"""
import json
from pathlib import Path

from PIL import Image


def _setup(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert client.post('/api/project/create', json={'name': 'Changes', 'task': 'classification'}).status_code == 200
    source = tmp_path / 'source'
    for cls, shade in (('OK', 0), ('NG', 200)):
        (source / cls).mkdir(parents=True)
        for i in range(6):
            Image.new('RGB', (32, 32), (shade, i * 9, 0)).save(source / cls / f'{cls.lower()}_{i:02d}.png')
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    assert client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).status_code == 200
    assert client.post('/api/dataset/split', json={'folder_path': str(source), 'task': 'classification', 'train_ratio': .5,
                                                  'val_ratio': .25, 'test_ratio': .25, 'seed': 7}).status_code == 200
    project = client.get('/api/project/current').json()
    return client, project, source


def _trained_model(project, source, job='job_changes'):
    """A model directory bound to the current data version, as training writes it (no weights are needed here)."""
    from backend.engine.annotation_storage import (set_request_project_root, reset_request_project_root,
                                                   set_request_annotation_root, reset_request_annotation_root)
    from backend.engine.dataset_loaders import set_request_split_root, reset_request_split_root
    from backend.engine.training_provenance import bind_training_version
    tokens = (set_request_project_root(Path(project['project_dir'])), set_request_annotation_root(Path(project['annotations_dir'])),
              set_request_split_root(Path(project['dataset_dir']) / 'splits'))
    try:
        binding = bind_training_version(project, source)
    finally:
        reset_request_split_root(tokens[2])
        reset_request_annotation_root(tokens[1])
        reset_request_project_root(tokens[0])
    model = Path(project['models_dir']) / job
    model.mkdir(parents=True)
    (model / 'best_model.pt').write_bytes(b'checkpoint')
    (model / 'model_meta.json').write_text(json.dumps({'task': 'classification', 'classes': ['OK', 'NG'],
                                                       'source_dataset_path': str(source), 'training_provenance': binding}))
    return job, binding


def test_the_report_names_added_images_and_says_a_version_restore_cannot_bring_them_back(tmp_path):
    client, project, source = _setup(tmp_path)
    job, binding = _trained_model(project, source)
    current = client.get('/api/provenance/data-changes', params={'job_id': job}).json()
    assert current['state'] == 'current' and current['dataset_version_id'] == binding['dataset_version_id']

    Image.new('RGB', (32, 32), (1, 2, 3)).save(source / 'OK' / 'ok_new.png')
    changed = client.get('/api/provenance/data-changes', params={'job_id': job}).json()
    assert changed['state'] == 'changed'
    assert changed['counts'] == {'added': 1, 'removed': 0, 'modified': 0, 'label_files': 0}
    assert changed['examples']['added'] == ['OK/ok_new.png']
    assert changed['restorable'] is False and '다시 학습' in changed['reason']
    # The impact report agrees, and an evaluation of this model is no longer current (F-14 is covered in
    # test_workflow_impact); the report itself never edits anything.
    impact = client.get('/api/provenance/impact').json()
    assert [row['data_state'] for row in impact['models'] if row['job_id'] == job] == ['changed']


def test_label_split_and_class_changes_are_restorable_through_the_data_version(tmp_path):
    client, project, source = _setup(tmp_path)
    job, _ = _trained_model(project, source)
    image = source / 'OK' / 'ok_00.png'
    assert client.put('/api/dataset/metadata/class-override', json={'image_path': str(image), 'class_name': 'NG'}).status_code == 200
    assert client.post('/api/dataset/split', json={'folder_path': str(source), 'task': 'classification', 'train_ratio': .5,
                                                  'val_ratio': .25, 'test_ratio': .25, 'seed': 99}).status_code == 200
    body = client.get('/api/provenance/data-changes', params={'job_id': job}).json()
    assert body['state'] == 'changed' and body['classes_changed'] is True and body['split_changed'] is True
    assert body['counts']['added'] == body['counts']['removed'] == body['counts']['modified'] == 0
    assert body['restorable'] is True


def test_refusals_name_the_data_change_only_for_the_same_folder_and_task(tmp_path):
    from backend.api.routes_evaluation import _source_mismatch_detail
    from backend.engine.data_changes import DATA_CHANGED_PREFIX
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'model_meta.json').write_text(json.dumps({'task': 'classification', 'source_dataset_path': str(tmp_path / 'source')}))
    assert _source_mismatch_detail(model, str(tmp_path / 'source'), 'classification', 'job_a').startswith(DATA_CHANGED_PREFIX)
    assert not _source_mismatch_detail(model, str(tmp_path / 'other'), 'classification', 'job_a').startswith(DATA_CHANGED_PREFIX)
    assert not _source_mismatch_detail(model, str(tmp_path / 'source'), 'segmentation', 'job_a').startswith(DATA_CHANGED_PREFIX)


def test_unknown_or_unsafe_models_are_refused(tmp_path):
    client, _, _ = _setup(tmp_path)
    assert client.get('/api/provenance/data-changes', params={'job_id': 'job_missing'}).status_code == 404
    assert client.get('/api/provenance/data-changes', params={'job_id': '../x'}).status_code == 422
