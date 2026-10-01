"""Mixed model comparisons retain reviewed truth boundaries and dependencies."""
import json
from pathlib import Path

import pytest
import torch
from PIL import Image

from backend.api.routes_model_comparisons import _fingerprint
from backend.engine import image_truth
from backend.engine.segmentation import build_segmentation_model
from backend.tests.test_model_comparisons import _client, _checkpoint


@pytest.fixture
def mixed_models(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _client(tmp_path)
    source = tmp_path / 'source'
    image = source / 'test' / 'OK' / 'part.png'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (32, 32), 'white').save(image)
    project = client.post('/api/project/create', json={'name': 'Mixed truth', 'task': 'classification'}).json()
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    project['source_dataset_dir'] = str(source)
    fingerprint = _fingerprint(source)
    models = Path(project['models_dir'])
    _checkpoint(models / 'job_cls', source, fingerprint, 0)
    model = build_segmentation_model(model_name='unet', num_classes=2, preset='fast', pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters(): parameter.zero_()
        last = [module for module in model.modules() if isinstance(module, torch.nn.Conv2d)][-1]
        last.bias[1] = 10
    directory = models / 'job_seg'; directory.mkdir()
    torch.save({'model_state_dict': model.state_dict(), 'model_name': 'unet', 'classes': ['background', 'defect']},
               directory / 'best_model.pt')
    (directory / 'model_meta.json').write_text(json.dumps({'task': 'segmentation', 'model_name': 'unet',
        'classes': ['background', 'defect'], 'image_size': [32, 32]}))
    (directory / 'job_receipt.json').write_text(json.dumps({'status': 'completed', 'task': 'segmentation',
        'source_dataset_path': str(source), 'dataset_fingerprint': fingerprint, 'dataset_path': str(source)}))
    request = {'source_dataset_path': str(source), 'task': 'classification', 'incumbent_job_id': 'job_cls',
               'candidate_job_id': 'job_seg', 'incumbent_task': 'classification', 'candidate_task': 'segmentation',
               'full_test': True}
    return client, project, image, request


def _declare(client, image, task, verdict='UNKNOWN', classes=None, roles=None):
    names = classes or (['OK', 'NG'] if task == 'classification' else ['background', 'defect'])
    params = {'image_path': str(image), 'task': task, 'classes': names}
    if roles is not None: params['class_roles'] = json.dumps(roles)
    current = client.get('/api/image-truth', params=params)
    assert current.status_code == 200, current.text
    current = current.json()
    response = client.put('/api/image-truth', json={
        'image_path': str(image), 'task': task, 'classes': names, 'class_roles': roles,
        'verdict': verdict, 'defect_classes': [names[-1]] if verdict == 'NG' else [],
        'reviewer': 'Reviewer', 'expected_revision': current['truth_revision'],
        'expected_image_revision': current['image_revision']})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('declared_task', ['classification', 'segmentation'])
def test_mixed_http_comparison_keeps_participating_reviewed_unknown(mixed_models, declared_task):
    client, project, image, request = mixed_models
    _declare(client, image, declared_task)
    response = client.post('/api/evaluation/model-comparisons', json=request)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report['images'][0]['ground_truth_verdict'] is None
    assert report['summary']['unknown_truth_images'] == 1
    assert report['summary']['known_ok_images'] == report['summary']['known_ng_images'] == 0
    assert report['truth_binding']['participating_tasks'] == ['classification', 'segmentation']


@pytest.mark.parametrize('verdict', ['OK', 'NG'])
def test_mixed_comparison_does_not_transplant_other_task_known_truth(mixed_models, verdict):
    client, project, image, request = mixed_models
    _declare(client, image, 'classification', verdict)
    response = client.post('/api/evaluation/model-comparisons', json=request)
    assert response.status_code == 200, response.text
    assert response.json()['images'][0]['ground_truth_verdict'] is None


def test_compatible_explicit_mixed_declaration_takes_precedence(mixed_models):
    client, project, image, request = mixed_models
    _declare(client, image, 'classification')
    roles = {'OK': 'normal', 'NG': 'defect', 'background': 'normal', 'defect': 'defect'}
    _declare(client, image, 'mixed', 'OK', list(roles), roles)
    response = client.post('/api/evaluation/model-comparisons', json=request)
    assert response.status_code == 200, response.text
    assert response.json()['images'][0]['ground_truth_verdict'] == 'OK'
    assert response.json()['summary']['known_ok_images'] == 1


@pytest.mark.parametrize('declared_task', ['classification', 'segmentation'])
@pytest.mark.parametrize('verdict', ['UNKNOWN', 'NG'])
def test_newer_participating_review_requires_mixed_rereview_despite_clock_rollback(mixed_models, monkeypatch, declared_task, verdict):
    from backend.engine import dataset_metadata
    client, project, image, request = mixed_models
    roles = {'OK': 'normal', 'NG': 'defect', 'background': 'normal', 'defect': 'defect'}
    monkeypatch.setattr(dataset_metadata, '_now', lambda: '2030-01-01T00:00:00Z')
    _declare(client, image, declared_task, 'NG')
    _declare(client, image, 'mixed', 'OK', list(roles), roles)
    first = client.post('/api/evaluation/model-comparisons', json=request)
    assert first.status_code == 200, first.text
    assert first.json()['images'][0]['ground_truth_verdict'] == 'OK'
    monkeypatch.setattr(dataset_metadata, '_now', lambda: '2020-01-01T00:00:00Z')
    _declare(client, image, declared_task, verdict)
    second = client.post('/api/evaluation/model-comparisons', json=request)
    assert second.status_code == 200, second.text
    assert second.json()['images'][0]['ground_truth_verdict'] is None
    assert second.json()['summary']['known_ok_images'] == second.json()['summary']['known_ng_images'] == 0
    assert second.json()['summary']['unknown_truth_images'] == 1
    _declare(client, image, 'mixed', 'OK', list(roles), roles)
    third = client.post('/api/evaluation/model-comparisons', json=request)
    assert third.status_code == 200, third.text
    assert third.json()['images'][0]['ground_truth_verdict'] == 'OK'


@pytest.mark.parametrize('declared_task', ['classification', 'segmentation'])
def test_new_participating_declaration_during_real_http_execution_invalidates(mixed_models, monkeypatch, declared_task):
    from backend.engine.flowchart_engine import FlowchartEngine
    client, project, image, request = mixed_models
    execute = FlowchartEngine.execute
    calls = 0
    def with_concurrent_review(self, *args, **kwargs):
        nonlocal calls
        result = execute(self, *args, **kwargs)
        calls += 1
        if calls == 1: _declare(client, image, declared_task)
        return result
    monkeypatch.setattr(FlowchartEngine, 'execute', with_concurrent_review)
    response = client.post('/api/evaluation/model-comparisons', json=request)
    assert response.status_code == 409, response.text
    assert 'truth' in response.text.lower()
    assert not list((Path(project['reports_dir']) / 'model_comparisons').glob('comparison_*.json'))


def test_mixed_legacy_segmentation_scope_freezes_channel_roles(mixed_models):
    client, project, image, request = mixed_models
    directory = Path(project['models_dir']) / 'job_seg'
    checkpoint = directory / 'best_model.pt'
    payload = torch.load(checkpoint, weights_only=True); payload['classes'] = ['background', 'good']
    torch.save(payload, checkpoint)
    metadata = json.loads((directory / 'model_meta.json').read_text()); metadata['classes'] = payload['classes']
    (directory / 'model_meta.json').write_text(json.dumps(metadata))
    response = client.post('/api/evaluation/model-comparisons', json=request)
    assert response.status_code == 200, response.text
    assert response.json()['truth_binding']['scope']['class_semantics']['roles']['good'] == 'defect'


def test_homogeneous_truth_hash_is_unchanged_with_mixed_dependency_option(mixed_models):
    client, project, image, request = mixed_models
    _declare(client, image, 'classification')
    before = image_truth.read_truth(project, str(image), task='classification', classes=['OK', 'NG'])
    after = image_truth.read_truth(project, str(image), task='classification', classes=['OK', 'NG'],
                                   participating_tasks=['classification', 'segmentation'])
    assert after == before


def test_http_mixed_truth_context_matches_execution_after_participating_review(mixed_models):
    client, project, image, request = mixed_models
    roles = {'OK': 'normal', 'NG': 'defect', 'background': 'normal', 'defect': 'defect'}
    _declare(client, image, 'mixed', 'OK', list(roles), roles)
    _declare(client, image, 'classification')
    params = {'image_path': str(image), 'task': 'mixed', 'classes': list(roles),
              'class_roles': json.dumps(roles), 'participating_tasks': ['classification', 'segmentation']}
    read = client.get('/api/image-truth', params=params)
    assert read.status_code == 200, read.text
    current = read.json()
    assert current['verdict'] == 'UNKNOWN'
    assert current['invalidated'] is True
    assert current['unknown_reason'] == 'participating_task_review_changed'
    assert current['participating_tasks'] == current['scope']['participating_tasks'] == params['participating_tasks']
    reviewed = client.put('/api/image-truth', json={
        'image_path': str(image), 'task': 'mixed', 'classes': list(roles), 'class_roles': roles,
        'participating_tasks': params['participating_tasks'], 'verdict': 'OK', 'reviewer': 'Reviewer',
        'expected_revision': current['truth_revision'], 'expected_image_revision': current['image_revision']})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()['verdict'] == 'OK'
    assert reviewed.json()['participating_tasks'] == reviewed.json()['scope']['participating_tasks'] == params['participating_tasks']
    reread = client.get('/api/image-truth', params=params)
    assert reread.json()['truth_sha256'] == reviewed.json()['truth_sha256']
    report = client.post('/api/evaluation/model-comparisons', json=request)
    assert report.status_code == 200, report.text
    assert report.json()['images'][0]['ground_truth_verdict'] == 'OK'
    assert report.json()['images'][0]['truth_sha256'] == reviewed.json()['truth_sha256']
