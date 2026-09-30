"""Authentic pretrained adapters: task outputs, class indexing, offline reconstruction."""
from pathlib import Path

import pytest
import torch

from backend.engine.classification import create_classification_model
from backend.engine.segmentation import build_segmentation_model
from backend.engine.detection import create_detection_model


def test_dino_classification_frozen_encoder_trains_head_without_download(monkeypatch):
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    model = create_classification_model(backbone='dinov3_vits16', num_classes=3, pretrained=False)
    model.train()
    logits = model(torch.rand(2, 3, 48, 64))
    assert logits.shape == (2, 3)
    torch.nn.functional.cross_entropy(logits, torch.tensor([0, 2])).backward()
    assert model.head.weight.grad is not None
    assert all(p.grad is None and not p.requires_grad for p in model.encoder.parameters())
    assert not model.encoder.training


def test_dino_segmentation_preserves_rectangular_nonpatch_size_and_head_gradient(monkeypatch):
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    model = build_segmentation_model(model_name='dinov3_vits16', num_classes=3, pretrained=False)
    x = torch.rand(2, 3, 35, 51)
    logits = model(x)
    assert logits.shape == (2, 3, 35, 51)
    torch.nn.functional.cross_entropy(logits, torch.zeros(2, 35, 51, dtype=torch.long)).backward()
    assert any(p.grad is not None for p in model.head.parameters())
    assert all(p.grad is None and not p.requires_grad for p in model.encoder.parameters())


@pytest.mark.parametrize('task', ['classification', 'segmentation', 'detection'])
def test_explicit_missing_pretrained_checkpoint_fails_without_random_substitute(tmp_path, task):
    kwargs = {'pretrained': True, 'pretrained_checkpoint': str(tmp_path / 'missing.pt')}
    with pytest.raises((FileNotFoundError, ValueError), match='(?i)(checkpoint|weight)'):
        if task == 'classification':
            create_classification_model(backbone='dinov3_vits16', **kwargs)
        elif task == 'segmentation':
            build_segmentation_model(model_name='dinov3_vits16', **kwargs)
        else:
            create_detection_model(backbone='yolo26n', num_classes=3, **kwargs)


def test_yolo_training_loss_and_foreground_labels_on_input_dimensions(monkeypatch):
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    model = create_detection_model(backbone='yolo26n', num_classes=3, pretrained=False)
    images = [torch.rand(3, 64, 96), torch.rand(3, 64, 96)]
    targets = [dict(boxes=torch.tensor([[8., 8., 30., 40.]]), labels=torch.tensor([2])) for _ in images]
    model.train()
    losses = model(images, targets)
    assert isinstance(losses, dict) and losses
    loss = sum(losses.values())
    assert torch.isfinite(loss) and loss.requires_grad
    loss.backward()
    assert any(p.grad is not None for p in model.parameters())
    model.eval()
    outputs = model(images)
    assert len(outputs) == 2
    for output in outputs:
        assert output['boxes'].shape[-1] == 4
        assert ((output['labels'] >= 1) & (output['labels'] <= 2)).all()
        assert (output['boxes'][:, [0, 2]] <= 96).all()
        assert (output['boxes'][:, [1, 3]] <= 64).all()
        assert (output['boxes'] >= 0).all()


def test_yolo_target_conversion_maps_classes_and_normalizes_boxes():
    model = create_detection_model(backbone='yolo26n', num_classes=3, pretrained=False)
    images = [torch.zeros(3, 64, 96)]
    batch, shapes = model.prepare_batch(images, [dict(boxes=torch.tensor([[12., 8., 36., 40.]]), labels=torch.tensor([2]))])
    assert batch['cls'].flatten().tolist() == [1.]
    assert torch.allclose(batch['bboxes'], torch.tensor([[.25, .375, .25, .5]]))
    assert shapes == [(64, 96)]


def test_yolo_rejects_background_targets():
    model = create_detection_model(backbone='yolo26n', num_classes=3, pretrained=False)
    with pytest.raises(ValueError, match='foreground'):
        model.prepare_batch([torch.rand(3, 64, 64)], [dict(boxes=torch.tensor([[1., 2., 6., 8.]]), labels=torch.tensor([0]))])


def test_actual_cached_dino_checkpoint_records_hash_and_reconstructs_offline(tmp_path, monkeypatch):
    import hashlib
    cache = Path.home() / '.cache/huggingface/hub/models--timm--vit_small_patch16_dinov3.lvd1689m/snapshots'
    weights = next(cache.glob('*/model.safetensors'), None)
    if weights is None:
        pytest.skip('Authentic public DINOv3 cache unavailable')
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    model = create_classification_model(backbone='dinov3_vits16', num_classes=2, pretrained=True,
                                        pretrained_checkpoint=str(weights), pretrained_sha256=digest).eval()
    assert model.model_metadata['pretrained_sha256'] == digest
    assert model.model_metadata['pretrained'] is True
    x = torch.rand(1, 3, 64, 80)
    with torch.no_grad():
        expected = model(x)
    checkpoint = tmp_path / 'model.pt'
    torch.save({'task': 'classification', 'classes': ['OK', 'defect'],
                **model.model_metadata, 'model_state_dict': model.state_dict()}, checkpoint)
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    from backend.engine.exporter import load_checkpoint_and_reconstruct_model
    rebuilt, meta, _ = load_checkpoint_and_reconstruct_model(checkpoint)
    with torch.no_grad():
        assert torch.allclose(rebuilt(x), expected, atol=1e-6)
    assert meta['pretrained_sha256'] == digest
    with pytest.raises(ValueError, match='(?i)hash|sha256'):
        create_classification_model(backbone='dinov3_vits16', pretrained=True,
                                    pretrained_checkpoint=str(weights), pretrained_sha256='0' * 64)


def test_yolo_checkpoint_reconstructs_same_class_head_without_network(tmp_path, monkeypatch):
    model = create_detection_model(backbone='yolo26n', num_classes=3, pretrained=False).eval()
    checkpoint = tmp_path / 'model.pt'
    torch.save({'task': 'detection', 'classes': ['scratch', 'dent'],
                **model.model_metadata, 'model_state_dict': model.state_dict()}, checkpoint)
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    from backend.engine.exporter import load_checkpoint_and_reconstruct_model
    rebuilt, meta, _ = load_checkpoint_and_reconstruct_model(checkpoint)
    assert rebuilt.num_classes == 3
    assert list(model.state_dict()) == list(rebuilt.state_dict())
    for key, value in model.state_dict().items():
        assert torch.equal(value, rebuilt.state_dict()[key])


def test_yolo_inference_maps_zero_based_classes_and_clips_padding(monkeypatch):
    model = create_detection_model(backbone='yolo26n', num_classes=3, pretrained=False).eval()
    # Deterministic native prediction checks the adapter boundary; the native
    # module's actual loss/forward is separately exercised above.
    native = torch.tensor([[[12., 8., 36., 40., .9, 1.], [-4., 0., 90., 100., .8, 0.]]])
    monkeypatch.setattr(model.detector, 'forward', lambda *_: (native, {}))
    output = model([torch.rand(3, 35, 51)])[0]
    assert output['labels'].tolist() == [2, 1]
    assert torch.equal(output['boxes'], torch.tensor([[12., 8., 36., 35.], [0., 0., 51., 35.]]))
    assert torch.allclose(output['scores'], torch.tensor([.9, .8]))


@pytest.mark.parametrize('task', ['segmentation', 'detection'])
def test_unknown_requested_architecture_is_not_silently_replaced(task):
    with pytest.raises(ValueError, match='Unsupported'):
        if task == 'segmentation':
            build_segmentation_model(model_name='dinov3_missing', pretrained=False)
        else:
            create_detection_model(backbone='yolo_missing', pretrained=False)


def test_dino_actual_runtime_export_keeps_weight_receipt_and_offline_forward(tmp_path, monkeypatch):
    cache = Path.home() / '.cache/huggingface/hub/models--timm--vit_small_patch16_dinov3.lvd1689m/snapshots'
    weights = next(cache.glob('*/model.safetensors'), None)
    if weights is None:
        pytest.skip('Authentic public DINOv3 cache unavailable')
    model = create_classification_model(backbone='dinov3_vits16', num_classes=2, pretrained=True,
                                        pretrained_checkpoint=str(weights)).eval()
    checkpoint = tmp_path / 'model.pt'
    torch.save({'task': 'classification', 'classes': ['OK', 'defect'], 'image_size': [64, 64],
                **model.model_metadata, 'model_state_dict': model.state_dict()}, checkpoint)
    from backend.engine import exporter
    monkeypatch.setattr(exporter, 'locate_checkpoint', lambda *_: checkpoint)
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    result = exporter.export_runtime_package(job_id='job_test', export_format='torchscript',
                                             resolution=64, output_base_dir=tmp_path / 'packages')
    import json
    config = json.loads((Path(result['package_path']) / 'config.json').read_text())
    assert config['architecture'] == 'classification:dinov3_vits16'
    assert config['pretrained_sha256'] == model.model_metadata['pretrained_sha256']
    artifact = torch.jit.load(str(Path(result['package_path']) / 'model.pt'))
    x = torch.rand(2, 3, 64, 64)
    with torch.no_grad():
        assert torch.allclose(artifact(x), model(x), atol=1e-5)


def test_yolo_authentic_pretrained_encoder_transfer_and_custom_head_gradient(monkeypatch):
    import os
    weights = Path(os.environ.get('MODU_YOLO_PRETRAINED_CHECKPOINT', str(Path(torch.hub.get_dir()) / 'checkpoints' / 'yolo26n.pt')))
    if not weights.is_file():
        pytest.skip('Set MODU_YOLO_PRETRAINED_CHECKPOINT to the official YOLO26n weights')
    from ultralytics.nn.tasks import torch_safe_load
    payload, _ = torch_safe_load(str(weights))
    source = (payload.get('ema') or payload['model']).float()
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    model = create_detection_model(backbone='yolo26n', num_classes=3, pretrained=True,
                                    pretrained_checkpoint=str(weights))
    assert torch.equal(model.detector.state_dict()['model.0.conv.weight'], source.state_dict()['model.0.conv.weight'])
    assert model.model_metadata['pretrained'] is True
    assert len(model.model_metadata['pretrained_sha256']) == 64
    model.train()
    x = [torch.rand(3, 64, 96), torch.rand(3, 64, 96)]
    targets = [{'boxes': torch.tensor([[10., 8., 40., 44.]]), 'labels': torch.tensor([2])} for _ in x]
    loss = sum(model(x, targets).values())
    assert torch.isfinite(loss)
    loss.backward()
    assert any(p.grad is not None for p in model.detector.model[-1].parameters())


@pytest.mark.skipif(not torch.backends.mps.is_available(), reason='Metal device unavailable')
def test_dino_segmentation_dense_head_backward_on_mps():
    model = build_segmentation_model(model_name='dinov3_vits16', num_classes=2, pretrained=False).to('mps')
    logits = model(torch.rand(2, 3, 64, 80, device='mps'))
    assert logits.shape == (2, 2, 64, 80)
    torch.nn.functional.cross_entropy(logits, torch.zeros(2, 64, 80, device='mps', dtype=torch.long)).backward()
    assert all(p.grad is None for p in model.encoder.parameters())
    assert any(p.grad is not None for p in model.head.parameters())


def test_yolo_runtime_export_trace_checks_and_prediction_parity(tmp_path, monkeypatch):
    model = create_detection_model(backbone='yolo26n', num_classes=3, pretrained=False).eval()
    checkpoint = tmp_path / 'model.pt'
    torch.save({'task': 'detection', 'classes': ['scratch', 'dent'],
                **model.model_metadata, 'model_state_dict': model.state_dict()}, checkpoint)
    from backend.engine import exporter
    monkeypatch.setattr(exporter, 'locate_checkpoint', lambda *_: checkpoint)
    result = exporter.export_runtime_package(job_id='job_yolo_test', export_format='torchscript',
                                             resolution=96, output_base_dir=tmp_path / 'packages')
    artifact = torch.jit.load(str(Path(result['package_path']) / 'model.pt'))
    for x in (torch.rand(1, 3, 96, 96), torch.zeros(1, 3, 96, 96)):
        with torch.no_grad():
            expected = model(x)[0]
            actual = artifact(x)
        assert torch.allclose(actual[0], expected['boxes'], atol=1e-5)
        assert torch.allclose(actual[1], expected['scores'], atol=1e-5)
        assert torch.equal(actual[2], expected['labels'])


def test_yolo_default_pretrained_download_uses_cache_outside_working_tree(tmp_path, monkeypatch):
    import shutil
    from ultralytics.utils import downloads
    import os
    original = Path(os.environ.get('MODU_YOLO_PRETRAINED_CHECKPOINT', str(Path(torch.hub.get_dir()) / 'checkpoints' / 'yolo26n.pt')))
    if not original.is_file():
        pytest.skip('Authentic official YOLO26n fixture unavailable')
    cache = tmp_path / 'cache'
    observed = []
    def fake_download(path):
        destination = Path(path)
        observed.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        return str(destination)
    monkeypatch.setattr(torch.hub, 'get_dir', lambda: str(cache))
    monkeypatch.setattr(downloads, 'attempt_download_asset', fake_download)
    monkeypatch.chdir(tmp_path)
    model = create_detection_model(backbone='yolo26n', num_classes=3, pretrained=True)
    assert model.model_metadata['pretrained'] is True
    assert observed and all(path == cache / 'checkpoints' / 'yolo26n.pt' for path in observed)
    assert not (tmp_path / 'yolo26n.pt').exists()


@pytest.mark.parametrize('task', ['classification', 'segmentation', 'detection'])
def test_flow_reconstruction_uses_saved_adapter_state_offline(tmp_path, monkeypatch, task):
    if task == 'classification':
        source = create_classification_model(backbone='dinov3_vits16', num_classes=2, pretrained=False).eval()
        classes = ['OK', 'defect']
    elif task == 'segmentation':
        source = build_segmentation_model(model_name='dinov3_vits16', num_classes=2, pretrained=False).eval()
        classes = ['background', 'defect']
    else:
        source = create_detection_model(backbone='yolo26n', num_classes=3, pretrained=False).eval()
        classes = ['scratch', 'dent']
    checkpoint = tmp_path / 'model.pt'
    torch.save({'task': task, 'classes': classes, 'image_size': [96, 64],
                **source.model_metadata, 'model_state_dict': source.state_dict()}, checkpoint)
    from backend.engine import model_backbones
    def download_forbidden(*_):
        raise AssertionError('Flow reconstruction must not resolve pretrained weights')
    monkeypatch.setattr(model_backbones, '_dino_weights', download_forbidden)
    monkeypatch.setattr(model_backbones, '_yolo_weights', download_forbidden)
    from backend.engine.flowchart_engine import FlowchartEngine
    engine = FlowchartEngine(device='cpu', checkpoint_resolver=lambda *_: checkpoint)
    if task == 'detection':
        rebuilt, trained = engine._get_detection_model('saved_adapter')
    else:
        rebuilt, trained = engine._get_inspection_model(task, 'saved_adapter')
    assert trained
    x = torch.rand(1, 3, 64, 96)
    with torch.no_grad():
        expected, actual = source(x), rebuilt(x)
    if task == 'detection':
        for key in ('boxes', 'scores', 'labels'):
            assert torch.equal(expected[0][key], actual[0][key])
    else:
        assert torch.equal(expected, actual)
