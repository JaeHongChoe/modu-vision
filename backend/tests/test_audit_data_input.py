"""Data input checks found by the 2026-10-20 functional audit (F-01, F-02, F-03, F-05, F-06).

Each test drives the real API on a temporary project and synthetic images; nothing outside tmp_path is touched.
"""
from pathlib import Path

from PIL import Image


def _client(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert client.post('/api/project/create', json={'name': 'Audit'}).status_code == 200
    return client


def _images(folder: Path, count: int, shade: int = 0):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        Image.new('RGB', (32, 32), (shade, i * 7 % 255, 0)).save(folder / f'{folder.name.lower()}_{i:02d}.png')


def _use_source(client, source: Path):
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200


def _import(client, source: Path, task='classification', validate=True):
    return client.post('/api/dataset/import', json={'folder_path': str(source), 'task': task, 'validate_images': validate})


def test_undecodable_images_are_really_excluded_and_return_once_they_decode(tmp_path):
    client = _client(tmp_path)
    source = tmp_path / 'source'
    _images(source / 'OK', 6)
    _images(source / 'NG', 6, shade=200)
    corrupt = source / 'OK' / 'corrupt.png'
    corrupt.write_bytes(b'not really a png' * 20)
    _use_source(client, source)

    response = _import(client, source)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [Path(row['file_path']).name for row in body['corrupted_images']] == ['corrupt.png']
    assert body['excluded_undecodable'] == {'excluded': ['OK/corrupt.png'], 'restored': [], 'recorded': True}
    # The counts, classes and split no longer include the file the message says was excluded.
    assert body['total_images'] == 12
    assert body['classes'] == {'OK': 6, 'NG': 6}
    assert sum(body['split'].values()) == 12
    assert corrupt.read_bytes() == b'not really a png' * 20, 'the source file is never rewritten'

    # The user replaces the file with a readable image: the next import makes it active again.
    Image.new('RGB', (32, 32), (9, 9, 9)).save(corrupt, format='PNG')
    again = _import(client, source).json()
    assert again['excluded_undecodable']['restored'] == ['OK/corrupt.png']
    assert again['total_images'] == 13


def test_import_without_project_context_says_the_exclusion_was_not_recorded(tmp_path):
    from backend.api.routes_dataset import DatasetImportRequest, import_dataset
    source = tmp_path / 'loose'
    _images(source / 'OK', 3)
    _images(source / 'NG', 3, shade=200)
    (source / 'NG' / 'broken.png').write_bytes(b'')
    body = import_dataset(DatasetImportRequest(folder_path=str(source), task='classification', validate_images=True))
    assert body['excluded_undecodable']['recorded'] is False
    assert len(body['corrupted_images']) == 1


def test_split_state_tells_automatic_saved_and_stale_apart(tmp_path):
    client = _client(tmp_path)
    source = tmp_path / 'source'
    _images(source / 'OK', 10)
    _images(source / 'NG', 10, shade=200)
    _use_source(client, source)

    assert _import(client, source).json()['split_state'] == 'automatic'
    split = client.post('/api/dataset/split', json={'folder_path': str(source), 'task': 'classification',
                                                    'train_ratio': .7, 'val_ratio': .2, 'test_ratio': .1, 'seed': 42})
    assert split.status_code == 200, split.text
    assert _import(client, source).json()['split_state'] == 'saved'

    Image.new('RGB', (32, 32), (1, 2, 3)).save(source / 'NG' / 'ng_extra.png')
    stale = _import(client, source).json()
    assert stale['split_state'] == 'stale'
    assert stale['total_images'] == 21


def test_split_state_for_source_train_val_test_folders(tmp_path):
    client = _client(tmp_path)
    source = tmp_path / 'source'
    for part in ('train', 'val', 'test'):
        _images(source / part / 'OK', 2)
        _images(source / part / 'NG', 2, shade=200)
    _use_source(client, source)
    assert _import(client, source).json()['split_state'] == 'source_folders'


def test_one_class_classification_is_refused_before_training_starts(tmp_path):
    client = _client(tmp_path)
    source = tmp_path / 'source'
    _images(source / 'OK', 12)
    (source / 'NG').mkdir()
    _use_source(client, source)
    assert _import(client, source).status_code == 200
    assert client.post('/api/dataset/split', json={'folder_path': str(source), 'task': 'classification',
                                                   'train_ratio': .7, 'val_ratio': .2, 'test_ratio': .1,
                                                   'seed': 42}).status_code == 200
    response = client.post('/api/training/start', json={'task': 'classification', 'preset': 'fast',
                                                       'dataset_path': str(source), 'device': 'cpu'})
    assert response.status_code == 422, response.text
    detail = response.json()['detail']
    assert '2개 이상' in detail and "'OK' 하나뿐" in detail and '빈 클래스 폴더: NG' in detail


def test_stale_saved_split_is_explained_in_korean_at_training_start(tmp_path):
    client = _client(tmp_path)
    source = tmp_path / 'source'
    _images(source / 'OK', 10)
    _images(source / 'NG', 10, shade=200)
    _use_source(client, source)
    assert client.post('/api/dataset/split', json={'folder_path': str(source), 'task': 'classification',
                                                   'train_ratio': .7, 'val_ratio': .2, 'test_ratio': .1,
                                                   'seed': 42}).status_code == 200
    Image.new('RGB', (32, 32), (4, 5, 6)).save(source / 'OK' / 'ok_extra.png')
    response = client.post('/api/training/start', json={'task': 'classification', 'preset': 'fast',
                                                       'dataset_path': str(source), 'device': 'cpu'})
    assert response.status_code == 422, response.text
    detail = response.json()['detail']
    assert '저장한 분할과 지금 폴더의 이미지가 다릅니다' in detail and 'Check classification split' not in detail


def test_wrong_layout_names_the_expected_layout_instead_of_too_few_samples(tmp_path):
    client = _client(tmp_path)
    source = tmp_path / 'classes'
    _images(source / 'OK', 4)
    _images(source / 'NG', 4, shade=200)
    response = _import(client, source, task='detection', validate=False)
    assert response.status_code == 422
    detail = response.json()['detail']
    assert detail['error_code'] == 'ERR_DATA_LAYOUT' and detail['canonical_code'] == 'ERR_009'
    assert "'객체 검출' 작업과 맞지 않습니다" in detail['message_ko'] and 'annotations_train.json' in detail['expected_layout']
    assert 'Synthetic Generator' not in detail['remediation_kr'] and detail['action'] == 'show_expected_layout'
    assert detail['details'], 'the loader message stays available as the technical detail'


def test_segmentation_folder_without_masks_imports_but_is_flagged(tmp_path):
    client = _client(tmp_path)
    source = tmp_path / 'plain'
    _images(source / 'OK', 3)
    _images(source / 'NG', 3, shade=200)
    body = _import(client, source, task='segmentation', validate=False).json()
    assert body['labels_missing'] is True
    assert 'masks/' in body['expected_layout']


def test_training_threads_and_workers_apply_the_requests_image_usage_policy(tmp_path):
    """Threads do not inherit the request context; the captured scope keeps not-used images out of training."""
    import json
    import threading
    from backend.engine.annotation_storage import (set_request_project_root, reset_request_project_root,
                                                   set_request_annotation_root, reset_request_annotation_root)
    from backend.engine.dataset_loaders import ClassificationDataset
    from backend.engine import dataset_metadata as dm
    from backend.engine.local_training_worker import _data_scope_fields, worker_data_scope
    project = tmp_path / 'project'
    project.mkdir()
    source = tmp_path / 'source'
    _images(source / 'OK', 3)
    _images(source / 'NG', 3, shade=200)
    annotations = project / 'annotations'
    annotations.mkdir()
    (project / 'project.json').write_text(json.dumps({'source_dataset_dir': str(source)}))
    ptoken = set_request_project_root(project)
    atoken = set_request_annotation_root(annotations)
    try:
        row = dm.metadata_for_path(project, source, source / 'OK' / 'ok_01.png', annotations)
        dm.update_metadata(project, source, row['image_uuid'], row['revision'], 'operator', {'usage_state': 'not_used'}, annotations)
        scope = _data_scope_fields()
        assert scope == {'project_root': str(project.resolve()), 'annotation_root': str(annotations.resolve())}
    finally:
        reset_request_annotation_root(atoken)
        reset_request_project_root(ptoken)

    seen = {}

    def run():
        seen['bare'] = len(ClassificationDataset(source, split=None))
        with worker_data_scope(scope):
            seen['scoped'] = sorted(p.name for p, _ in ClassificationDataset(source, split=None).samples)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    assert seen['bare'] == 6, 'without the scope a thread sees every image (the bug this guards against)'
    assert 'ok_01.png' not in seen['scoped'] and len(seen['scoped']) == 5
