"""Refusals on data review and package export explain the cause and a usable next step.

These requests reach real route guards against temporary images and project files. A regression to English-only
refusals or a guard that starts writing before refusing fails without running training, conversion or export.
"""
import hashlib
import json
from pathlib import Path
import re

import pytest
from PIL import Image

from backend.tests.test_dataset_metadata_api import client_workspace


def _guidance(response, status, reason, action):
    assert response.status_code == status, response.text
    assert set(response.json()) == {'detail'}
    detail = response.json()['detail']
    assert isinstance(detail, str)
    assert reason in detail and action in detail, detail
    assert re.search('[가-힣]', detail) and '. ' in detail and detail.endswith('세요.'), detail


@pytest.mark.parametrize('case,status,reason,action', [
    ('bulk_approval', 422, '개별', '선택'),
    ('bulk_duplicate', 422, '중복', '선택'),
    ('unknown_image', 404, '찾을 수 없', '선택'),
    ('stale_split', 409, '바뀌', '미리보기'),
    ('import_missing', 422, '없', '선택'),
    ('import_dimensions', 422, '크기', '확인'),
    ('invalid_download', 422, '식별자', '내보내기'),
    ('missing_download', 404, '찾을 수 없', '내보내기'),
])
def test_metadata_refusals_explain_the_problem_without_changing_images(client_workspace, case, status, reason, action):
    client, project, source = client_workspace
    originals = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}
    row = client.get('/api/dataset/metadata').json()['items'][0]
    item = {'image_uuid': row['image_uuid'], 'expected_revision': row['revision']}
    if case == 'bulk_approval':
        response = client.post('/api/dataset/metadata/bulk', json={'actor': 'Kim', 'items': [item], 'changes': {'workflow_state': 'approved'}})
    elif case == 'bulk_duplicate':
        response = client.post('/api/dataset/metadata/bulk', json={'actor': 'Kim', 'items': [item, item], 'changes': {'tags': ['review']}})
    elif case == 'unknown_image':
        response = client.patch('/api/dataset/metadata/missing-image', json={'actor': 'Kim', 'expected_revision': 1, 'changes': {'tags': ['review']}})
    elif case == 'stale_split':
        response = client.post('/api/dataset/metadata/split', json={'group_by': ['lot'], 'expected_qualification_sha256': '0' * 64})
    elif case.startswith('import_'):
        document = {'imagePath': 'outside.png' if case == 'import_missing' else 'a.png', 'imageWidth': 101, 'imageHeight': 80, 'shapes': []}
        response = client.post('/api/dataset/formats/import', json={'format': 'labelme', 'payload': document})
    else:
        export_id = 'bad' if case == 'invalid_download' else 'annotations_' + 'a' * 32
        response = client.get('/api/dataset/formats/download/' + export_id)
    _guidance(response, status, reason, action)
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source.iterdir()} == originals
    assert not (Path(project['project_dir']) / 'exports').exists()


@pytest.mark.parametrize('case,status,reason,action', [
    ('task', 422, '종류', '선택'),
    ('source', 422, '폴더', '선택'),
    ('version', 422, '식별자', '선택'),
    ('unsaved', 409, '저장', '저장'),
    ('verification_missing', 422, '이미지', '선택'),
    ('verification_unreadable', 422, '읽을 수 없', '선택'),
    ('cohort_outside', 422, '데이터', '선택'),
    ('preflight_missing', 404, '점검', '점검'),
])
def test_export_refusals_explain_the_problem_before_building_a_package(client_workspace, case, status, reason, action):
    from backend.api import routes_export
    client, project, source = client_workspace
    client.app.include_router(routes_export.router)
    body = {'source_dataset_path': str(source), 'recipe_task': 'detection', 'package_name': 'refused'}
    if case == 'task':
        body['recipe_task'] = 'unknown'
    elif case == 'source':
        body['source_dataset_path'] = str(source / 'absent')
    elif case == 'version':
        body['version_id'] = 'invalid'
    elif case == 'verification_missing':
        body['verification_image_path'] = str(source / 'absent.png')
    elif case == 'verification_unreadable':
        (source / 'broken.png').write_bytes(b'invalid image')
        body['verification_image_path'] = str(source / 'broken.png')
    elif case == 'cohort_outside':
        outside = source.parent / 'outside.png'
        Image.new('RGB', (2, 2)).save(outside)
        body.update(parity_images=[{'path': str(outside)}, {'path': str(source / 'a.png')}], parity_device='cpu')
    if case == 'preflight_missing':
        response = client.get('/api/export/flow/preflights/' + 'a' * 32, params={'source_dataset_path': str(source)})
    else:
        response = client.post('/api/export/flow', json=body)
    _guidance(response, status, reason, action)
    assert not (Path(project['project_dir']) / 'exports').exists()


def test_runtime_controls_without_a_project_name_the_same_recovery_step(client_workspace, monkeypatch):
    from backend.api import routes_export
    client, _, _ = client_workspace
    monkeypatch.setattr(routes_export, 'get_current_project', lambda _: None)
    client.app.include_router(routes_export.router)
    prefix = '/api/export/flow/optimization-jobs/' + 'a' * 32
    for method, suffix in [('get', ''), ('get', '/approval-prerequisites'), ('get', '/heldout-results/0'), ('post', '/cancel')]:
        _guidance(getattr(client, method)(prefix + suffix), 409, '프로젝트', '여세요.')


@pytest.mark.parametrize('case,reason,action', [
    ('missing_selection', '선택', '선택'),
    ('missing_folder', '폴더', '선택'),
    ('linked_file', '링크', '확인'),
    ('too_large', '128', '나누'),
    ('coco', 'COCO', '선택'),
    ('yolo', 'classes.txt', '선택'),
])
def test_annotation_folder_validation_is_actionable_before_import(client_workspace, case, reason, action):
    client, _, source = client_workspace
    directory = source.parent / 'labels'
    directory.mkdir()
    body = {'format': 'labelme', 'import_dir': str(directory)}
    if case == 'missing_selection':
        body.pop('import_dir')
    elif case == 'missing_folder':
        body['import_dir'] = str(directory / 'absent')
    elif case == 'linked_file':
        (directory / 'linked.png').symlink_to(source / 'a.png')
    elif case == 'too_large':
        with (directory / 'large.bin').open('wb') as writer:
            writer.truncate(128 * 1024 * 1024 + 1)
    else:
        body['format'] = case
    _guidance(client.post('/api/dataset/formats/import', json=body), 422, reason, action)
    assert not list(directory.glob('*.json'))


@pytest.mark.parametrize('extra,field', [
    ({'compute_profile_id': 'remote'}, 'parity_images'),
    ({'target_os': 'linux'}, 'edge_cpu'),
    ({'parity_device': 'cpu'}, 'parity_images'),
    ({'parity_images': [{'path': 'a.png'}, {'path': 'b.png'}]}, 'parity_device'),
    ({'parity_images': [{'path': 'a.png'}, {'path': 'b.png'}], 'parity_device': 'cpu', 'verification_image_path': 'a.png'}, 'verification_image_path'),
    ({'parity_images': [{'path': 'a.png'}, {'path': 'b.png'}], 'parity_device': 'openvino:CPU'}, 'OpenVINO'),
    ({'parity_images': [{'path': 'a.png'}, {'path': 'b.png'}], 'parity_device': 'mps'}, 'parity_device'),
])
def test_export_request_validation_keeps_field_names_and_explains_recovery(client_workspace, extra, field):
    from backend.api import routes_export
    client, project, source = client_workspace
    client.app.include_router(routes_export.router)
    body = {'source_dataset_path': str(source), 'recipe_task': 'detection', 'package_name': 'refused', **extra}
    response = client.post('/api/export/flow', json=body)
    assert response.status_code == 422
    [detail] = response.json()['detail']
    assert detail['type'] == 'value_error' and detail['loc'] == ['body']
    assert field in detail['msg'] and re.search('[가-힣]', detail['msg']), detail
    assert '. ' in detail['msg'] and detail['msg'].endswith('세요.'), detail
    assert not (Path(project['project_dir']) / 'exports').exists()


@pytest.mark.parametrize('field,value', [('holdout_reviewed', 1), ('maximum_absolute_drift', '0.1')])
def test_precision_approval_validation_names_the_missing_explicit_input(client_workspace, field, value):
    from backend.api import routes_export
    client, _, _ = client_workspace
    client.app.include_router(routes_export.router)
    body = {'reviewer': 'Kim', 'reason': 'Reviewed native heldout output', 'holdout_reviewed': True,
            'maximum_absolute_drift': .001, 'approval_revision_ids': {}, field: value}
    response = client.post('/api/export/flow/optimization-jobs/' + 'a' * 32 + '/approve', json=body)
    assert response.status_code == 422
    [detail] = response.json()['detail']
    assert detail['type'] == 'value_error' and detail['loc'] == ['body', field]
    assert re.search('[가-힣]', detail['msg']) and detail['msg'].endswith('세요.'), detail


def test_unfinished_optimization_has_actionable_refusals_for_review_and_evidence(client_workspace):
    from backend.api import routes_export
    client, project, _ = client_workspace
    client.app.include_router(routes_export.router)
    job_id = 'a' * 32
    journal = Path(project['project_dir']) / 'exports' / 'optimization_jobs' / (job_id + '.json')
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({'job_id': job_id, 'status': 'failed', 'result': None}))
    prefix = '/api/export/flow/optimization-jobs/' + job_id
    _guidance(client.get(prefix + '/approval-prerequisites'), 409, '완료', '최적화')
    _guidance(client.get(prefix + '/heldout-results/0'), 409, '완료', '최적화')
    assert json.loads(journal.read_text()) == {'job_id': job_id, 'status': 'failed', 'result': None}


def test_annotation_import_keeps_the_two_existing_e2e_protocol_refusals(client_workspace):
    client, project, source = client_workspace
    directory = source.parent / 'not-labelme'
    directory.mkdir()
    empty = client.post('/api/dataset/formats/import', json={'format': 'labelme', 'import_dir': str(directory)})
    assert empty.status_code == 422 and empty.json() == {'detail': 'No LabelMe documents found'}
    document = {'imagePath': 'a.png', 'imageWidth': 100, 'imageHeight': 80, 'shapes': []}
    stale = client.post('/api/dataset/formats/import', json={'format': 'labelme', 'payload': document, 'mode': 'apply'})
    assert stale.status_code == 409 and stale.json() == {'detail': 'Preview is outdated or missing: a.png'}
    assert not list(Path(project['annotations_dir']).rglob('a.json'))
