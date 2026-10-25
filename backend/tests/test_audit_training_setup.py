"""Training setup found by the 2026-10-20 functional audit: resume reasons (F-07/A7), the device "auto" really
resolves to (F-20), weight file formats (F-13) and pre-downloading weights with progress (F-12/A5).

Nothing here downloads: the prefetch tests pass a local downloader, and the other tests read synthetic files.
"""
import time
from pathlib import Path

import pytest
import torch
from PIL import Image

from backend.tests.test_extension_training_api import client, resume_checkpoint


def _states(api, source, preset='fast'):
    return api.get('/api/training/resume-states', params={'dataset_path': str(source), 'task': 'classification', 'preset': preset}).json()


def test_stopped_runs_that_cannot_resume_are_listed_with_the_reason(tmp_path):
    api, project, source = client(tmp_path)
    checkpoint = resume_checkpoint(project, source)
    body = _states(api, source)
    assert len(body['states']) == 1 and body['excluded'] == []
    assert _states(api, source, 'precision')['excluded'][0]['reason'].startswith('학습 방식이 다릅니다')

    payload = torch.load(checkpoint, weights_only=True)
    payload['identity']['device'] = 'mps'
    torch.save(payload, checkpoint)
    body = _states(api, source)
    assert body['states'] == []
    assert body['excluded'] == [{'job_id': 'stopped', 'next_epoch': 1, 'epochs': 3, 'device': 'mps',
                                 'reason': 'Mac GPU(MPS)로 학습한 작업은 정확히 이어갈 수 없습니다. 새로 학습하거나 완료 모델의 가중치로 시작하세요'}]

    payload['identity']['device'] = 'cpu'
    torch.save(payload, checkpoint)
    Image.new('RGB', (64, 64), 'red').save(source / 'train' / 'good' / 'b.png')
    assert _states(api, source)['excluded'][0]['reason'] == '학습한 뒤 데이터가 바뀌었습니다'


def test_auto_device_readiness_reports_the_device_training_will_use(tmp_path):
    from backend.engine.device import get_device
    api, _, _ = client(tmp_path)
    body = api.post('/api/training-workspace/readiness', json={'task': 'classification', 'model': 'dinov3_vits16', 'device': 'auto'}).json()
    expected = get_device('auto').type
    assert body['runtime']['auto'] is True and body['runtime']['device'].split(':')[0] == expected


def test_weight_files_whose_content_and_name_disagree_are_named_in_korean(tmp_path):
    from backend.engine.weights_format import sniff_weights_format, weights_file_problem
    from safetensors.torch import save_file
    safetensors = tmp_path / 'weights.safetensors'
    save_file({'w': torch.zeros(2)}, str(safetensors))
    renamed = tmp_path / 'weights'
    renamed.write_bytes(safetensors.read_bytes())
    torch_file = tmp_path / 'weights.pt'
    torch.save({'w': torch.zeros(2)}, torch_file)
    junk = tmp_path / 'junk.pth'
    junk.write_bytes(b'not weights at all')
    assert sniff_weights_format(safetensors) == 'safetensors' and weights_file_problem(safetensors) is None
    assert sniff_weights_format(torch_file) == 'torch_zip' and weights_file_problem(torch_file) is None
    assert '.safetensors로 바꾼 뒤' in weights_file_problem(renamed)
    assert '읽을 수 없습니다' in weights_file_problem(junk)


def test_training_start_names_a_mismatched_weight_file_and_failures_read_in_korean(tmp_path):
    from safetensors.torch import save_file
    from backend.utils.error_catalog import classify_exception
    api, _, source = client(tmp_path)
    renamed = tmp_path / 'weights'
    save_file({'w': torch.zeros(2)}, str(tmp_path / 'weights.safetensors'))
    renamed.write_bytes((tmp_path / 'weights.safetensors').read_bytes())
    refused = api.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source), 'device': 'cpu',
                                                   'config_overrides': {'pretrained_checkpoint': str(renamed)}})
    assert refused.status_code == 422 and 'safetensors 형식인데' in refused.json()['detail']
    for message in ('Authentic DINOv3 pretrained weights are unavailable for timm/x.', 'UnpicklingError: invalid load key, \'x\'.'):
        item = classify_exception(RuntimeError(message))
        assert item.code == 'ERR_010' and '가중치 미리 받기' in item.remediation_kr


def _wait(model, state):
    from backend.engine.weights_prefetch import prefetch_status
    for _ in range(200):
        status = prefetch_status(model)
        if status['state'] == state:
            return status
        time.sleep(.02)
    raise AssertionError(prefetch_status(model))


def test_prefetch_reports_progress_and_a_korean_failure(tmp_path, monkeypatch):
    from backend.engine import weights_prefetch
    monkeypatch.setattr(weights_prefetch, '_jobs', {})

    def downloader(model, progress):
        target = tmp_path / f'{model}.safetensors'
        progress(0, 300)
        target.write_bytes(b'x' * 300)
        progress(300, 300)
        return target

    weights_prefetch.start_prefetch('dinov3_vits16', downloader)
    done = _wait('dinov3_vits16', 'done')
    assert done['bytes'] == done['total'] == 300 and Path(done['path']).name == 'dinov3_vits16.safetensors'

    def offline(model, progress):
        raise OSError('network unreachable')
    weights_prefetch.start_prefetch('yolo26n', offline)
    failed = _wait('yolo26n', 'failed')
    assert '인터넷 연결' in failed['error'] and '(OSError)' in failed['error']
    with pytest.raises(ValueError, match='미리 받기를 지원하지 않습니다'):
        weights_prefetch.start_prefetch('resnet18', downloader)


def test_prefetch_endpoints_refuse_unsupported_models_and_report_idle(tmp_path, monkeypatch):
    from backend.engine import weights_prefetch
    monkeypatch.setattr(weights_prefetch, '_jobs', {})
    api, _, _ = client(tmp_path)
    assert api.post('/api/training-workspace/prefetch-weights', json={'model': 'resnet18'}).status_code == 422
    assert api.get('/api/training-workspace/prefetch-weights', params={'model': 'dinov3_vits16'}).json()['state'] == 'idle'


def test_the_card_can_read_local_readiness_without_a_write(tmp_path):
    api, _, _ = client(tmp_path)
    read = api.get('/api/training-workspace/readiness', params={'task': 'classification', 'model': 'dinov3_vits16'})
    posted = api.post('/api/training-workspace/readiness', json={'task': 'classification', 'model': 'dinov3_vits16', 'device': 'auto'})
    assert read.status_code == posted.status_code == 200
    assert {k: read.json()[k] for k in ('runtime', 'weights', 'dependencies')} == {k: posted.json()[k] for k in ('runtime', 'weights', 'dependencies')}


def test_a_detection_project_without_a_saved_split_gets_a_korean_next_action(tmp_path):
    # A new detection project from a plain image folder has no saved split yet: the loader's own English ValueError used
    # to reach the step-3 card as the person's next action.
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.api import routes_training_workspace as workspace
    app = create_app(project_dir=str(tmp_path / 'projects'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert api.post('/api/project/create', json={'name': 'detection', 'task': 'detection'}).status_code == 200
    source = tmp_path / 'plain'
    source.mkdir()
    for index in range(3):
        Image.new('RGB', (64, 64), 'gray').save(source / f'part_{index:02d}.png')
    assert api.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    body = api.get('/api/training-workspace/readiness', params={'task': 'detection', 'model': 'yolo26n'}).json()
    assert body['ready'] is False
    assert workspace.DATA_UNREADABLE in body['next_actions']
    assert not any('must be specified' in action or 'DetectionDataset' in action for action in body['next_actions'])
    assert all(workspace._HANGUL.search(action) or action.isidentifier() for action in body['next_actions'])


def test_readiness_keeps_a_korean_refusal_and_replaces_an_english_one():
    from fastapi import HTTPException
    from backend.api import routes_training_workspace as workspace
    assert workspace.readable_action(ValueError('저장된 분할의 원본 이미지가 없습니다. 데이터·분할을 다시 확인하세요.'), 'fallback').startswith('저장된 분할')
    assert workspace.readable_action(ValueError('images_dir and annotation_file must be specified'), workspace.DATA_UNREADABLE) == workspace.DATA_UNREADABLE
    assert workspace.readable_action(HTTPException(409, '이전 모델이 다른 라벨 세트로 학습됐습니다. 새로 학습하세요.'), 'fallback').startswith('이전 모델')
    assert workspace.readable_action(HTTPException(409, 'Parent checkpoint changed'), workspace.PARENT_UNCHECKED) == workspace.PARENT_UNCHECKED
    # A server's missing package or path stays readable after the Korean sentence; a local loader message does not.
    assert workspace.readable_action(ValueError('missing packages: timm'), workspace.SERVER_RUNTIME_UNCHECKED, detail=True) == f'{workspace.SERVER_RUNTIME_UNCHECKED} (missing packages: timm)'
    for text in (workspace.DATA_UNREADABLE, workspace.PARENT_UNCHECKED, workspace.SERVER_RUNTIME_UNCHECKED, workspace.SERVER_WEIGHTS_UNCHECKED):
        assert text.endswith('세요.') and workspace._HANGUL.search(text)
