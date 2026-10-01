"""Anomaly statistics must run with their original feature space, never random features."""
import hashlib
from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image
from torchvision import models


def _legacy(tmp_path):
    from backend.engine.anomaly import PaDiMDetector
    torch.set_num_threads(1)
    parent = PaDiMDetector(pretrained=False, target_dim=4)
    parent.fit(torch.utils.data.DataLoader(torch.rand(2, 3, 32, 32), batch_size=2))
    state = parent.state_dict()
    state.pop('feature_extractor_state_dict')
    state.pop('score_spec', None)  # Historical files predate the fitted-state calibration binding.
    metadata = {'task': 'anomaly', 'detector_type': 'padim', 'feature_backbone': 'resnet18',
        'classes': ['good', 'anomaly'], 'image_size': [32, 32]}
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({**metadata, 'model_state_dict': state}, checkpoint)
    dataset = tmp_path / 'dataset' / 'test' / 'good'
    dataset.mkdir(parents=True)
    Image.new('RGB', (32, 32), (50, 70, 90)).save(dataset / 'fixture.png')
    return checkpoint, metadata


def _consume(consumer, checkpoint, metadata, tmp_path, monkeypatch):
    if consumer == 'export':
        from backend.engine.exporter import load_checkpoint_and_reconstruct_model
        return load_checkpoint_and_reconstruct_model(checkpoint)
    if consumer == 'evaluation':
        from backend.api.routes_evaluation import _evaluate_anomaly
        return _evaluate_anomaly(checkpoint, metadata, tmp_path / 'dataset', torch.device('cpu'))
    if consumer == 'infer':
        from backend.engine.trainer import infer
        return infer('anomaly', checkpoint, np.full((32, 32, 3), 80, dtype=np.uint8), device='cpu')
    from backend.engine.flowchart_engine import FlowchartEngine
    engine = FlowchartEngine(device='cpu')
    monkeypatch.setattr(engine, '_resolve_checkpoint', lambda *args: checkpoint)
    return engine._get_inspection_model('anomaly', 'job_fixture')


@pytest.mark.parametrize('consumer', ['export', 'evaluation', 'infer', 'flow'])
def test_legacy_statistics_without_original_cache_fail_without_download(tmp_path, monkeypatch, consumer):
    checkpoint, metadata = _legacy(tmp_path)
    monkeypatch.setattr(torch.hub, 'get_dir', lambda: str(tmp_path / 'empty_cache'))
    original = models.resnet18
    def offline_only(*args, **kwargs):
        assert kwargs.get('weights') is None, 'Reconstruction attempted a pretrained download'
        return original(*args, **kwargs)
    monkeypatch.setattr(models, 'resnet18', offline_only)
    with pytest.raises((ValueError, RuntimeError), match='verified local pretrained'):
        _consume(consumer, checkpoint, metadata, tmp_path, monkeypatch)


def test_legacy_statistics_reject_corrupt_original_cache(tmp_path, monkeypatch):
    checkpoint, metadata = _legacy(tmp_path)
    cache = tmp_path / 'cache' / 'checkpoints'
    cache.mkdir(parents=True)
    (cache / Path(models.ResNet18_Weights.DEFAULT.url).name).write_bytes(b'corrupt cached weights')
    monkeypatch.setattr(torch.hub, 'get_dir', lambda: str(cache.parent))
    with pytest.raises(ValueError, match='SHA-256'):
        _consume('export', checkpoint, metadata, tmp_path, monkeypatch)


def test_legacy_statistics_use_verified_original_cached_weights(tmp_path, monkeypatch):
    checkpoint, metadata = _legacy(tmp_path)
    cache = Path(torch.hub.get_dir()) / 'checkpoints' / Path(models.ResNet18_Weights.DEFAULT.url).name
    if not cache.is_file():
        pytest.skip('This offline functional check needs the existing official ResNet18 cache')
    expected = torch.load(cache, map_location='cpu', weights_only=True)
    metadata['pretrained_sha256'] = hashlib.sha256(cache.read_bytes()).hexdigest()
    payload = torch.load(checkpoint, weights_only=True)
    torch.save({**payload, **metadata}, checkpoint)
    original = models.resnet18
    def offline_only(*args, **kwargs):
        assert kwargs.get('weights') is None, 'Reconstruction attempted a pretrained download'
        return original(*args, **kwargs)
    monkeypatch.setattr(models, 'resnet18', offline_only)
    _, _, restored = _consume('export', checkpoint, metadata, tmp_path, monkeypatch)
    actual = restored.feature_extractor.backbone.state_dict()
    assert set(expected).issubset(actual)
    assert all(torch.equal(actual[key], expected[key]) for key in expected)
    # Original torchvision weights predate tracking counters; torch restores those to zero.
    assert all(key.endswith('num_batches_tracked') and value.item() == 0
               for key, value in actual.items() if key not in expected)


def test_saved_full_feature_state_honors_resnet50_backbone_offline(tmp_path, monkeypatch):
    from backend.engine.anomaly import PaDiMDetector
    from backend.engine.exporter import load_checkpoint_and_reconstruct_model
    torch.set_num_threads(1)
    parent = PaDiMDetector(backbone_name='resnet50', pretrained=False, target_dim=4)
    parent.mean = torch.zeros(1, 1, 1, 4)
    parent.cov_inv = torch.eye(4).reshape(1, 1, 4, 4)
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({'task': 'anomaly', 'detector_type': 'padim', 'feature_backbone': 'resnet50',
        'model_state_dict': parent.state_dict()}, checkpoint)
    monkeypatch.setattr(torch.hub, 'get_dir', lambda: str(tmp_path / 'empty_cache'))
    original = models.resnet50
    def offline_only(*args, **kwargs):
        assert kwargs.get('weights') is None, 'Saved features must reconstruct without a download'
        return original(*args, **kwargs)
    monkeypatch.setattr(models, 'resnet50', offline_only)
    _, _, restored = load_checkpoint_and_reconstruct_model(checkpoint)
    assert restored.backbone_name == 'resnet50'
    assert torch.equal(restored.feature_extractor.backbone.conv1.weight,
                       parent.feature_extractor.backbone.conv1.weight)
