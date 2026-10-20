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
    from backend.engine.model_backbones import _verified_weights
    with pytest.raises(ValueError, match='safetensors 형식인데'):
        _verified_weights(renamed, None)


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
