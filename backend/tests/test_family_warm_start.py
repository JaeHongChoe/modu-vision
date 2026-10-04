"""Continuation verifies frozen parents before training a separate candidate.

All text, rotated boxes and defect crops here are labelled fixtures.
"""
import hashlib
import json
import threading
from pathlib import Path

import pytest
import torch
from PIL import Image

from backend.engine import warm_start


def test_detection_parent_accepts_actual_factory_signature(tmp_path):
    from backend.tests.test_warm_start import _parent
    models, source, checkpoint, model = _parent(tmp_path, task="detection")
    metadata = {"task": "detection", "classes": ["OK", "NG"], "detector_preset": "fast"}
    checkpoint.with_name("model_meta.json").write_text(json.dumps(metadata))
    torch.save({**metadata, "model_state_dict": model.state_dict()}, checkpoint)
    architecture = warm_start.architecture_for("detection", "fast", {'backbone': 'fasterrcnn_mobilenet_v3_large_fpn'})
    parent = warm_start.resolve_warm_start_parent("job_parent01", models, source, "detection", architecture)
    candidate = torch.nn.Linear(3, 2)
    warm_start.load_parent_weights(candidate, parent, ["OK", "NG"])
    assert torch.equal(candidate.weight, model.weight)


@pytest.mark.parametrize("detector", ["padim", "patchcore"])
def test_anomaly_checkpoint_preserves_frozen_feature_extractor(detector):
    from backend.engine.anomaly import PaDiMDetector, PatchCoreDetector
    factory = PaDiMDetector if detector == "padim" else PatchCoreDetector
    parent = factory(pretrained=False)
    with torch.no_grad():
        parent.feature_extractor.backbone.conv1.weight.fill_(0.3125)
    candidate = factory(pretrained=False)
    state = parent.state_dict()
    candidate.load_state_dict(state)
    assert torch.equal(candidate.feature_extractor.backbone.conv1.weight,
                       parent.feature_extractor.backbone.conv1.weight)


def _images(root):
    root.mkdir()
    for index in range(6):
        Image.new("RGB", (32, 32), (25 + index * 30, 10, index)).save(root / f"{index}.png")


def _family(root, task):
    _images(root)
    splits = ["train", "train", "val", "val", "test", "test"]
    if task == "ocr":
        from backend.engine.ocr import write_ocr_manifest, train_ocr
        write_ocr_manifest(root, [{"image": f"{i}.png", "text": "AB", "split": split}
                                  for i, split in enumerate(splits)])
        return train_ocr, {"image_size": (16, 32), "batch_size": 2}
    if task == "rotated_detection":
        from backend.engine.rotated_detection import write_rotated_manifest, train_rotated_detector
        write_rotated_manifest(root, [{"image": f"{i}.png", "label": "fixture", "split": split,
                                      "box": {"cx": 16, "cy": 16, "width": 12, "height": 8, "angle_deg": 10}}
                                     for i, split in enumerate(splits)])
        return train_rotated_detector, {"image_size": 32, "batch_size": 2}
    if task == "defect_gan":
        from backend.engine.defect_gan import write_defect_gan_manifest, train_defect_gan
        write_defect_gan_manifest(root, [{"image": f"{i}.png", "bbox": [2, 2, 26, 26], "split": split}
                                       for i, split in enumerate(splits)])
        return train_defect_gan, {"base_channels": 8, "batch_size": 2}
    from backend.engine.enhancement import prepare_enhancement, train_enhancement
    prepare_enhancement(root, root.parent / "pairs", image_paths=[f"{i}.png" for i in range(6)])
    return train_enhancement, {"batch_size": 2}


@pytest.mark.parametrize("task", ["ocr", "rotated_detection", "defect_gan", "enhancement"])
def test_specialist_candidate_loads_parent_and_keeps_immutable_lineage(tmp_path, task):
    from backend.engine import specialized_warm_start as family
    torch.set_num_threads(1)
    source = tmp_path / "source"
    runner, options = _family(source, task)
    dataset = tmp_path / "pairs" if task == "enhancement" else source
    models = tmp_path / "project" / "models"
    parent_dir = models / task / ("a" * 32)
    runner(dataset, parent_dir, epochs=1, **options)
    checkpoint = parent_dir / "best_model.pt"
    frozen = checkpoint.read_bytes()
    digest = hashlib.sha256(frozen).hexdigest()
    (parent_dir / "job_receipt.json").write_text(json.dumps({"job_id": "a" * 32,
        "task": task, "status": "completed", "source_dataset_path": str(source),
        "dataset_fingerprint": "v1:fixture", "checkpoint_sha256": digest}))
    parent = family.resolve_family_parent(models, "a" * 32, task, source, dataset, options)
    candidate_dir = models / task / ("b" * 32)
    runner(dataset, candidate_dir, epochs=1, warm_start=parent, **options)
    payload = torch.load(candidate_dir / "best_model.pt", weights_only=True, map_location="cpu")
    metadata = json.loads((candidate_dir / "model_meta.json").read_text())
    assert payload["warm_start"]["parent_job_id"] == "a" * 32
    assert payload["warm_start"]["parent_checkpoint_sha256"] == digest
    assert metadata["warm_start"] == payload["warm_start"]
    assert checkpoint.read_bytes() == frozen
    event = threading.Event(); event.set()
    from backend.engine.rotated_detection import RotatedTrainingCancelled
    with pytest.raises((InterruptedError, RotatedTrainingCancelled)):
        runner(dataset, models / task / ("c" * 32), epochs=1, warm_start=parent, cancel_event=event, **options)
    assert not (models / task / ("c" * 32) / "best_model.pt").exists()
    with pytest.raises(ValueError, match="new candidate"):
        runner(dataset, parent_dir, epochs=1, warm_start=parent, **options)
    assert checkpoint.read_bytes() == frozen
    with pytest.raises(ValueError, match="source"):
        family.resolve_family_parent(models, "a" * 32, task, tmp_path, dataset, options)
    altered = {**options, 'image_size': (32, 64) if task == 'ocr' else 64, 'base_channels': 16}
    if task != 'enhancement':
        with pytest.raises(ValueError, match="architecture"):
            family.resolve_family_parent(models, "a" * 32, task, source, dataset, altered)
    checkpoint.write_bytes(frozen + b'tamper')
    with pytest.raises(ValueError, match="SHA-256"):
        runner(dataset, models / task / ("d" * 32), epochs=1, warm_start=parent, **options)


@pytest.mark.parametrize('task,prefix', [('ocr', 'ocr'), ('rotated_detection', 'rotated-detection'),
                                     ('defect_gan', 'defect-gan'), ('enhancement', 'enhancement')])
def test_specialist_parent_api_returns_only_verified_compatible_completed_candidates(tmp_path, monkeypatch, task, prefix):
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import routes_project, routes_ocr, routes_rotated_detection, routes_defect_gan, routes_enhancement
    original_init = httpx.Client.__init__
    monkeypatch.setattr(httpx.Client, '__init__', lambda self, *args, app=None, **kw: original_init(self, *args, **kw))
    app = FastAPI(); app.state.project_dir = tmp_path / 'projects'
    app.include_router(routes_project.router)
    for module in (routes_ocr, routes_rotated_detection, routes_defect_gan, routes_enhancement):
        app.include_router(module.router)
    client = TestClient(app)
    source = tmp_path / 'source'
    runner, options = _family(source, task)
    project_dir = tmp_path / 'project'
    assert client.post('/api/project/create', json={'name': 'Fixture continuation', 'task': 'classification', 'project_dir': str(project_dir)}).status_code == 200
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    dataset = tmp_path / 'pairs' if task == 'enhancement' else source
    directory = project_dir / 'models' / task / ('a' * 32)
    runner(dataset, directory, epochs=1, **options)
    receipt = {'job_id': 'a' * 32, 'task': task, 'status': 'completed', 'source_dataset_path': str(source),
               'dataset_fingerprint': 'v1:fixture', 'checkpoint_sha256': hashlib.sha256((directory / 'best_model.pt').read_bytes()).hexdigest()}
    (directory / 'job_receipt.json').write_text(json.dumps(receipt))
    corrupt = directory.with_name('b' * 32)
    corrupt.mkdir()
    (corrupt / 'best_model.pt').write_bytes(b'malformed checkpoint')
    digest = hashlib.sha256((corrupt / 'best_model.pt').read_bytes()).hexdigest()
    metadata = json.loads((directory / 'model_meta.json').read_text())
    (corrupt / 'model_meta.json').write_text(json.dumps({**metadata, 'checkpoint_sha256': digest}))
    (corrupt / 'job_receipt.json').write_text(json.dumps({**receipt, 'job_id': 'b' * 32,
                                                        'checkpoint_sha256': digest}))
    params = {'dataset_path': str(dataset)}
    if task == 'ocr': params.update(image_height=16, image_width=32)
    if task == 'rotated_detection': params.update(image_size=32)
    if task == 'defect_gan': params.update(base_channels=8)
    response = client.get(f'/api/{prefix}/warm-start-parents', params=params)
    assert response.status_code == 200, response.text
    assert [p['job_id'] for p in response.json()['parents']] == ['a' * 32]
    summary = response.json()['parents'][0]['summary']
    assert summary['completed_at'] is None
    if task == 'ocr': assert summary['training_metrics']['saved_val_loss'] == metadata['best_validation_loss']
    if task == 'rotated_detection': assert summary['training_metrics']['saved_mean_oriented_iou'] == metadata['validation']['mean_oriented_iou']
    if task == 'defect_gan': assert summary['training_metrics']['last_generator_loss'] == metadata['last_generator_loss']
    if task == 'enhancement': assert summary['model_recorded_at'] is not None
    (directory / 'job_receipt.json').write_text(json.dumps({**receipt, 'status': 'stopped'}))
    assert client.get(f'/api/{prefix}/warm-start-parents', params=params).json()['parents'] == []
    rejected = client.post(f'/api/{prefix}/train', json={**params, 'epochs': 1, 'batch_size': 2, 'warm_start_job_id': 'a' * 32})
    assert rejected.status_code in (409, 422), rejected.text
    assert set((project_dir / 'models' / task).iterdir()) == {directory, corrupt}


def test_specialist_late_load_reports_malformed_checkpoint_before_mutation(tmp_path):
    from backend.engine.specialized_warm_start import load_family_weights
    checkpoint = tmp_path / 'best_model.pt'
    checkpoint.write_bytes(b'malformed checkpoint')
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    parent = warm_start.WarmStartParent('a' * 32, checkpoint, digest, 'ocr',
        'ocr:small_cnn_bigru_ctc:16x32', ('A', 'B'), 'v1:fixture')
    (tmp_path / 'job_receipt.json').write_text(json.dumps({'job_id': parent.job_id, 'task': 'ocr',
        'status': 'completed', 'checkpoint_sha256': digest, 'dataset_fingerprint': 'v1:fixture',
        'source_dataset_path': str(tmp_path)}))
    candidate = torch.nn.Linear(3, 2)
    before = candidate.weight.detach().clone()
    with pytest.raises(ValueError, match='checkpoint'):
        load_family_weights({'model_state_dict': candidate}, parent, (parent.architecture, parent.classes))
    assert torch.equal(candidate.weight, before)


def test_specialist_parent_rejects_completed_receipt_hash_tamper(tmp_path):
    from backend.engine import specialized_warm_start as family
    torch.set_num_threads(1)
    source = tmp_path / "source"
    runner, options = _family(source, "ocr")
    models = tmp_path / "project" / "models"
    directory = models / "ocr" / ("a" * 32)
    runner(source, directory, epochs=1, **options)
    (directory / "job_receipt.json").write_text(json.dumps({"job_id": "a" * 32,
        "task": "ocr", "status": "completed", "source_dataset_path": str(source),
        "dataset_fingerprint": "v1:fixture", "checkpoint_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="hash|SHA-256"):
        family.resolve_family_parent(models, "a" * 32, "ocr", source, source, options)


@pytest.mark.parametrize('task,preset,overrides,signature', [
    ('classification', 'fast', {}, 'classification:dinov3_vits16'),
    ('classification', 'precision', {'backbone': 'dinov3_vitb16'}, 'classification:dinov3_vitb16'),
    ('segmentation', 'fast', {}, 'segmentation:dinov3_vits16'),
    ('detection', 'precision', {}, 'detection:yolo26n'),
    ('detection', 'fast', {'backbone': 'yolo26s'}, 'detection:yolo26s'),
    ('anomaly', 'fast', {'anomaly_method': 'patchcore'}, 'anomaly:patchcore:resnet18'),
])
def test_parent_signature_matches_selected_default_and_explicit_factory(task, preset, overrides, signature):
    assert warm_start.architecture_for(task, preset, overrides) == signature


def test_statistical_parent_reuses_features_and_refits_statistics_without_optimizer_state(tmp_path):
    from backend.engine.anomaly import PaDiMDetector
    source = tmp_path / 'source'; source.mkdir()
    models = tmp_path / 'project' / 'models'
    directory = models / 'job_anomaly'; directory.mkdir(parents=True)
    model = PaDiMDetector(pretrained=False)
    with torch.no_grad(): model.feature_extractor.backbone.conv1.weight.fill_(0.125)
    model.mean = torch.ones(1, 1, 1, 100)
    model.cov_inv = torch.eye(100).reshape(1, 1, 100, 100)
    model.threshold = 123.0
    metadata = {'task': 'anomaly', 'classes': ['good', 'anomaly'], 'preset': 'fast',
                'detector_type': 'padim', 'feature_backbone': 'resnet18'}
    checkpoint = directory / 'best_model.pt'
    torch.save({**metadata, 'model_state_dict': model.state_dict()}, checkpoint)
    (directory / 'model_meta.json').write_text(json.dumps(metadata))
    (directory / 'job_receipt.json').write_text(json.dumps({'job_id': 'job_anomaly', 'status': 'completed',
        'task': 'anomaly', 'source_dataset_path': str(source), 'dataset_fingerprint': 'v1:fixture'}))
    parent = warm_start.resolve_warm_start_parent('job_anomaly', models, source, 'anomaly', 'anomaly:padim:resnet18')
    candidate = PaDiMDetector(pretrained=False)
    warm_start.load_parent_weights(candidate, parent, ['good', 'anomaly'])
    assert torch.equal(candidate.feature_extractor.backbone.conv1.weight, model.feature_extractor.backbone.conv1.weight)
    assert torch.equal(candidate.sub_dims, model.sub_dims)
    assert candidate.mean is None and candidate.cov_inv is None and candidate.threshold == 0
    assert parent.lineage()['semantics'] == 'statistical_refit'
    from backend.engine.warm_start import portable_parent, restore_portable_parent
    envelope = portable_parent(parent, tmp_path / 'transfer')
    restored = restore_portable_parent(tmp_path / 'transfer', envelope, 'anomaly')
    assert restored.lineage()['semantics'] == 'statistical_refit'


def test_parent_api_rejects_changed_class_mapping_before_creating_candidate(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.tests.test_warm_start import _parent
    from backend.api import routes_training, routes_project
    from types import SimpleNamespace
    monkeypatch.setattr(routes_training.training_job_manager, 'start_job',
                        lambda **kw: SimpleNamespace(job_id=kw['job_id'], status='running', phase=None))
    original_init = httpx.Client.__init__
    monkeypatch.setattr(httpx.Client, '__init__', lambda self, *args, app=None, **kw: original_init(self, *args, **kw))
    models, source, _, _ = _parent(tmp_path)
    for split in ('train', 'val'):
        for label in ('OK', 'NG', 'new_defect'):
            folder = source / split / label; folder.mkdir(parents=True)
            Image.new('RGB', (16, 16), (10, 20, 30)).save(folder / 'fixture.png')
    app = FastAPI(); app.state.project_dir = tmp_path / 'projects'
    app.include_router(routes_project.router); app.include_router(routes_training.router)
    client = TestClient(app)
    assert client.post('/api/project/create', json={'name': 'Fixture', 'task': 'classification', 'project_dir': str(models.parent)}).status_code == 200
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    response = client.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source),
        'warm_start_job_id': 'job_parent01', 'config_overrides': {'backbone': 'resnet18', 'epochs': 1, 'image_size': 64}})
    assert response.status_code == 422, response.text
    assert 'classes' in response.text.lower()
    assert [p.name for p in models.iterdir()] == ['job_parent01']


@pytest.mark.parametrize('task', ['padim', 'patchcore'])
def test_flow_anomaly_uses_saved_features_without_loading_external_pretrained_weights(tmp_path, monkeypatch, task):
    from backend.engine.anomaly import PaDiMDetector, PatchCoreDetector
    from backend.engine.flowchart_engine import FlowchartEngine
    from torchvision import models
    factory = PaDiMDetector if task == 'padim' else PatchCoreDetector
    parent = factory(pretrained=False)
    if task == 'padim':
        parent.mean = torch.zeros(1, 1, 1, 100)
        parent.cov_inv = torch.eye(100).reshape(1, 1, 100, 100)
    else:
        parent.coreset = torch.ones(2, 384)
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({'task': 'anomaly', 'detector_type': task, 'model_state_dict': parent.state_dict()}, checkpoint)
    original = models.resnet18
    def offline_only(*args, **kwargs):
        if kwargs.get('weights') is not None:
            raise RuntimeError('Checkpoint reconstruction attempted external pretrained initialization')
        return original(*args, **kwargs)
    monkeypatch.setattr(models, 'resnet18', offline_only)
    engine = FlowchartEngine(device='cpu')
    monkeypatch.setattr(engine, '_resolve_checkpoint', lambda *args: checkpoint)
    model, is_trained = engine._get_inspection_model('anomaly', 'job_fixture')
    assert is_trained
    assert torch.equal(model.feature_extractor.backbone.conv1.weight, parent.feature_extractor.backbone.conv1.weight)


def test_queued_parent_rechecks_completed_receipt_before_mutating_candidate(tmp_path):
    from backend.tests.test_warm_start import _parent
    models, source, checkpoint, _ = _parent(tmp_path)
    parent = warm_start.resolve_warm_start_parent('job_parent01', models, source, 'classification', 'classification:resnet18')
    receipt = json.loads((checkpoint.parent / 'job_receipt.json').read_text())
    receipt['status'] = 'stopped'
    (checkpoint.parent / 'job_receipt.json').write_text(json.dumps(receipt))
    candidate = torch.nn.Linear(3, 2)
    before = candidate.weight.detach().clone()
    with pytest.raises(ValueError, match='completed'):
        warm_start.load_parent_weights(candidate, parent, ['OK', 'NG'])
    assert torch.equal(candidate.weight, before)


def test_architecture_signature_distinguishes_deeplab_encoders_selected_by_preset():
    fast = warm_start.architecture_for('segmentation', 'fast', {'model_name': 'deeplabv3'})
    precision = warm_start.architecture_for('segmentation', 'precision', {'model_name': 'deeplabv3'})
    assert fast != precision
    assert fast == 'segmentation:deeplab:mobilenet_v3'
    assert precision == 'segmentation:deeplab:resnet50'


def test_dino_alias_parent_signature_matches_factory_canonical_model():
    assert warm_start.architecture_for('classification', 'fast', {'backbone': 'dinov3'}) == 'classification:dinov3_vits16'
