"""Normal-only synthetic DINO training stays explicit at the product boundaries."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
import torch
from fastapi import HTTPException
from PIL import Image
from torch import nn

from backend.api import routes_training
from backend.engine.warm_start import architecture_for, load_parent_weights, resolve_warm_start_parent


OPTIONS = {'anomaly_method': 'dino_synthetic', 'anomaly_backbone': 'dinov3_vits16',
           'patch_size': 256, 'stride': 128, 'patches_per_image': 8, 'inference_batch_size': 32}
SIGNATURE = 'anomaly:dino_synthetic:dinov3_vits16:p256:s128:head_v1'


def _normal_source(tmp_path):
    source = tmp_path / 'source'
    for split, label in (('train', 'good'), ('val', 'good'), ('test', 'anomaly')):
        folder = source / split / label
        folder.mkdir(parents=True)
        Image.new('RGB', (256, 256), (10, len(split), 0)).save(folder / 'image.png')
    return source


def test_synthetic_options_accept_explicit_geometry_and_large_backbone():
    options = routes_training.TrainingConfigOverrides.model_validate({**OPTIONS, 'anomaly_backbone': 'dinov3_vitl16'})
    assert options.model_dump(exclude_none=True) == {**OPTIONS, 'anomaly_backbone': 'dinov3_vitl16'}


def test_normal_only_start_preserves_original_source_and_never_promotes_ng(tmp_path, monkeypatch):
    source = _normal_source(tmp_path)
    captured = []
    monkeypatch.setattr(routes_training.training_job_manager, 'start_job',
                        lambda **kw: captured.append(kw) or SimpleNamespace(status='running', phase=None))
    request = routes_training.TrainingStartRequest(task='anomaly', dataset_path=str(source),
        output_dir=str(tmp_path / 'models'), config_overrides={'anomaly_method': 'dino_synthetic'})
    result = routes_training.start_training(request)
    assert result['status'] == 'started'
    assert captured[0]['dataset_path'] == str(source)
    assert captured[0]['config_overrides'] == OPTIONS


def test_synthetic_start_rejects_defect_training_source_before_candidate_creation(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    (source / 'train/NG').mkdir(parents=True)
    Image.new('RGB', (256, 256), (255, 0, 0)).save(source / 'train/NG/image.png')
    output = tmp_path / 'models'
    captured = []
    monkeypatch.setattr(routes_training.training_job_manager, 'start_job',
                        lambda **kw: captured.append(kw) or SimpleNamespace(status='running', phase=None))
    with pytest.raises(HTTPException) as rejected:
        routes_training.start_training(routes_training.TrainingStartRequest(task='anomaly',
            dataset_path=str(source), output_dir=str(output), config_overrides=OPTIONS))
    assert rejected.value.status_code == 422
    assert not captured and not output.exists()


@pytest.mark.parametrize('layout', ['train', 'test_crop_output'])
def test_synthetic_start_requires_explicit_normal_label_for_ambiguous_legacy_sources(tmp_path, monkeypatch, layout):
    source = tmp_path / 'source'
    (source / layout).mkdir(parents=True)
    Image.new('RGB', (256, 256)).save(source / layout / 'unverified.png')
    captured = []
    monkeypatch.setattr(routes_training.training_job_manager, 'start_job',
                        lambda **kw: captured.append(kw) or SimpleNamespace(status='running', phase=None))
    with pytest.raises(HTTPException, match='normal') as rejected:
        routes_training.start_training(routes_training.TrainingStartRequest(task='anomaly',
            dataset_path=str(source), output_dir=str(tmp_path / 'models'), config_overrides=OPTIONS))
    assert rejected.value.status_code == 422 and not captured


def test_synthetic_preflight_checks_manifest_selected_train_labels(tmp_path, monkeypatch):
    from backend.api.routes_dataset import _write_split_manifest
    from backend.engine.dataset_loaders import set_request_split_root, reset_request_split_root
    source = _normal_source(tmp_path)
    captured = []
    monkeypatch.setattr(routes_training.training_job_manager, 'start_job',
                        lambda **kw: captured.append(kw) or SimpleNamespace(status='running', phase=None))
    token = set_request_split_root(tmp_path / 'splits')
    try:
        _write_split_manifest(source, {'train/good/image.png': 'val', 'val/good/image.png': 'val',
                                      'test/anomaly/image.png': 'train'}, 42)
        with pytest.raises(HTTPException, match='normal') as rejected:
            routes_training.start_training(routes_training.TrainingStartRequest(task='anomaly',
                dataset_path=str(source), output_dir=str(tmp_path / 'models'), config_overrides=OPTIONS))
        assert rejected.value.status_code == 422 and not captured
    finally:
        reset_request_split_root(token)


def test_synthetic_preflight_rejects_small_originals_without_resizing(tmp_path, monkeypatch):
    source = _normal_source(tmp_path)
    captured = []
    monkeypatch.setattr(routes_training.training_job_manager, 'start_job',
                        lambda **kw: captured.append(kw) or SimpleNamespace(status='running', phase=None))
    with pytest.raises(HTTPException, match='patch_size') as rejected:
        routes_training.start_training(routes_training.TrainingStartRequest(task='anomaly',
            dataset_path=str(source), output_dir=str(tmp_path / 'models'),
            config_overrides={**OPTIONS, 'patch_size': 512}))
    assert rejected.value.status_code == 422 and not captured


@pytest.mark.parametrize('changed', [
    {'patch_size': 255}, {'patch_size': 0}, {'stride': 257}, {'stride': 0},
    {'patches_per_image': 0}, {'inference_batch_size': 129}, {'anomaly_backbone': 'resnet18'},
])
def test_synthetic_invalid_options_rejected_before_candidate_creation(tmp_path, monkeypatch, changed):
    source = _normal_source(tmp_path)
    output = tmp_path / 'models'
    captured = []
    monkeypatch.setattr(routes_training.training_job_manager, 'start_job',
                        lambda **kw: captured.append(kw) or SimpleNamespace(status='running', phase=None))
    with pytest.raises(HTTPException) as rejected:
        routes_training.start_training(routes_training.TrainingStartRequest(task='anomaly',
            dataset_path=str(source), output_dir=str(output), config_overrides={**OPTIONS, **changed}))
    assert rejected.value.status_code == 422
    assert not captured and not output.exists()


def test_synthetic_remote_gate_requires_selected_dino_without_resnet_cache():
    from backend.remote.ssh_transport import require_training_runtime
    probe = {'runtime_ready': True, 'checks': {'runtime_dependencies': {
        'timm': True, 'safetensors': True, 'huggingface_hub': True},
        'model_dependencies': {'dinov3_vitl16': True}, 'pretrained_weights': {}}}
    require_training_runtime(probe, 'anomaly', 'fast', {**OPTIONS, 'anomaly_backbone': 'dinov3_vitl16'})
    probe['checks']['runtime_dependencies']['timm'] = False
    with pytest.raises(ValueError, match='timm'):
        require_training_runtime(probe, 'anomaly', 'fast', OPTIONS)


def test_synthetic_pretrained_transfer_selects_anomaly_backbone(tmp_path, monkeypatch):
    from backend.remote.coordinator import _local_pretrained_weights
    from backend.engine import model_backbones
    path = tmp_path / 'approved.safetensors'
    path.write_bytes(b'controlled transfer fixture')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = []
    monkeypatch.setattr(model_backbones, '_dino_weights', lambda name, explicit, expected:
                        calls.append((name, explicit, expected)) or (path, digest, 'local:approved'))
    receipt = _local_pretrained_weights('anomaly', 'fast', {**OPTIONS, 'anomaly_backbone': 'dinov3_vitb16',
                                      'pretrained_checkpoint': str(path), 'pretrained_sha256': digest})
    assert receipt == (path, digest, 'local:approved', 'dinov3_vitb16')
    assert calls == [('dinov3_vitb16', str(path), digest)]


def _synthetic_parent(tmp_path, monkeypatch):
    from backend.engine.anomaly import dino_synthetic
    from backend.tests.test_dino_synthetic_anomaly import TinyTaskModel
    monkeypatch.setattr(dino_synthetic, 'DinoTaskModel', TinyTaskModel)
    source = tmp_path / 'source'
    source.mkdir()
    models = tmp_path / 'project/models'
    directory = models / 'job_synthetic_parent'
    directory.mkdir(parents=True)
    model = dino_synthetic.DinoSyntheticDetector(pretrained=True, patch_size=256, stride=128,
                                               epochs=1, batch_size=4, learning_rate=0.001)
    with torch.no_grad():
        for parameter in model.head.parameters():
            parameter.fill_(0.123)
    metadata = {'task': 'anomaly', 'classes': ['good', 'anomaly'], 'preset': 'fast',
                'detector_type': 'dino_synthetic', 'anomaly_backbone': 'dinov3_vits16',
                'feature_backbone': 'dinov3_vits16', 'patch_size': 256, 'stride': 128,
                'head_version': 1, 'map_semantics': 'patch_score', 'pretrained': True, 'pretrained_sha256': 'a' * 64}
    checkpoint = directory / 'best_model.pt'
    torch.save({**metadata, 'model_state_dict': model.state_dict()}, checkpoint)
    (directory / 'model_meta.json').write_text(json.dumps(metadata))
    (directory / 'job_receipt.json').write_text(json.dumps({'job_id': directory.name,
        'status': 'completed', 'task': 'anomaly', 'source_dataset_path': str(source),
        'dataset_fingerprint': 'v1:normal-source'}))
    return models, source, model


def test_synthetic_parent_initializes_full_weights_without_feature_stats(tmp_path, monkeypatch):
    from backend.engine.anomaly.dino_synthetic import DinoSyntheticDetector
    models, source, original = _synthetic_parent(tmp_path, monkeypatch)
    assert architecture_for('anomaly', 'fast', OPTIONS) == SIGNATURE
    parent = resolve_warm_start_parent('job_synthetic_parent', models, source, 'anomaly', SIGNATURE)
    assert parent.semantics == 'weight_initialization'
    candidate = DinoSyntheticDetector(pretrained=False, epochs=3, batch_size=8, learning_rate=0.002,
                                      patches_per_image=12, inference_batch_size=9)
    load_parent_weights(candidate, parent, ['good', 'anomaly'])
    for name, tensor in original.feature_extractor.state_dict().items():
        assert torch.equal(candidate.feature_extractor.state_dict()[name], tensor)
    for name, tensor in original.head.state_dict().items():
        assert torch.equal(candidate.head.state_dict()[name], tensor)
    assert candidate.model_metadata['pretrained_sha256'] == 'a' * 64
    assert (candidate.epochs, candidate.batch_size, candidate.learning_rate) == (3, 8, 0.002)
    assert candidate.patches_per_image == 12 and candidate.inference_batch_size == 9
    assert candidate.calibration['method'] == 'uncalibrated' and not candidate.fitted


def test_synthetic_parent_remote_roundtrip_preserves_full_weight_initialization(tmp_path, monkeypatch):
    from backend.engine.warm_start import portable_parent, restore_portable_parent
    models, source, _ = _synthetic_parent(tmp_path, monkeypatch)
    parent = resolve_warm_start_parent('job_synthetic_parent', models, source, 'anomaly', SIGNATURE)
    run = tmp_path / 'remote_run'
    envelope = portable_parent(parent, run)
    restored = restore_portable_parent(run, envelope, 'anomaly')
    assert restored.semantics == 'weight_initialization'
    assert restored.lineage() == parent.lineage()
    with pytest.raises(ValueError, match='semantics'):
        restore_portable_parent(run, {**envelope, 'semantics': 'statistical_refit'}, 'anomaly')


@pytest.mark.parametrize('backbone', ['dinov3_vitb16', 'dinov3_vitl16'])
def test_synthetic_worker_selects_hash_bound_anomaly_backbone(tmp_path, backbone):
    from backend.remote.worker import _read_train_spec
    from backend.tests.test_remote_worker import _spec
    run, spec, _ = _spec(tmp_path)
    weights = run / 'pretrained.safetensors'
    weights.write_bytes(b'controlled worker transfer bytes')
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    payload = json.loads(spec.read_text())
    payload['task'] = 'anomaly'
    payload['config_overrides'] = {**OPTIONS, 'anomaly_backbone': backbone,
                                  'pretrained_checkpoint': weights.name, 'pretrained_sha256': digest}
    payload['pretrained_weights'] = {'checkpoint': weights.name, 'sha256': digest,
                                    'source': 'local:approved.safetensors', 'model': backbone}
    spec.write_text(json.dumps(payload))
    result = _read_train_spec(spec, run)
    assert result['config_overrides']['pretrained_checkpoint'] == str(weights)
    assert result['config_overrides']['anomaly_backbone'] == backbone
    payload['pretrained_weights']['model'] = 'dinov3_vits16'
    spec.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='architecture'):
        _read_train_spec(spec, run)


@pytest.mark.parametrize('architecture', [
    'anomaly:dino_synthetic:dinov3_vitb16:p256:s128:head_v1',
    'anomaly:dino_synthetic:dinov3_vits16:p512:s128:head_v1',
    'anomaly:dino_synthetic:dinov3_vits16:p256:s64:head_v1',
    'anomaly:dino_synthetic:dinov3_vits16:p256:s128:head_v2',
])
def test_synthetic_parent_rejects_backbone_geometry_or_head_mismatch(tmp_path, monkeypatch, architecture):
    models, source, _ = _synthetic_parent(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='architecture'):
        resolve_warm_start_parent('job_synthetic_parent', models, source, 'anomaly', architecture)


@pytest.mark.parametrize('semantics', [None, 'pixel_mask'])
def test_synthetic_parent_requires_recorded_patch_score_semantics(tmp_path, monkeypatch, semantics):
    models, source, _ = _synthetic_parent(tmp_path, monkeypatch)
    path = models / 'job_synthetic_parent/model_meta.json'
    metadata = json.loads(path.read_text())
    metadata['map_semantics'] = semantics
    path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='architecture'):
        resolve_warm_start_parent('job_synthetic_parent', models, source, 'anomaly', SIGNATURE)


@pytest.mark.parametrize('tamper', ['geometry', 'head'])
def test_synthetic_parent_rejects_nested_state_mismatch_before_any_weight_mutation(tmp_path, monkeypatch, tamper):
    from backend.engine.anomaly.dino_synthetic import DinoSyntheticDetector
    models, source, _ = _synthetic_parent(tmp_path, monkeypatch)
    checkpoint = models / 'job_synthetic_parent/best_model.pt'
    payload = torch.load(checkpoint, weights_only=True)
    if tamper == 'geometry':
        payload['model_state_dict']['config']['stride'] = 64
    else:
        key = next(iter(payload['model_state_dict']['head_state_dict']))
        payload['model_state_dict']['head_state_dict'][key] = torch.zeros(1)
    torch.save(payload, checkpoint)
    parent = resolve_warm_start_parent('job_synthetic_parent', models, source, 'anomaly', SIGNATURE)
    candidate = DinoSyntheticDetector(pretrained=False)
    original = {name: tensor.clone() for name, tensor in candidate.feature_extractor.state_dict().items()}
    original_head = {name: tensor.clone() for name, tensor in candidate.head.state_dict().items()}
    with pytest.raises(ValueError):
        load_parent_weights(candidate, parent, ['good', 'anomaly'])
    assert all(torch.equal(candidate.feature_extractor.state_dict()[name], value) for name, value in original.items())
    assert all(torch.equal(candidate.head.state_dict()[name], value) for name, value in original_head.items())


def test_catalog_distinguishes_synthetic_weight_init_from_statistical_refit():
    from backend.engine.model_catalog import model_family_catalog
    family = next(row for row in model_family_catalog()['families'] if row['task'] == 'anomaly')
    methods = {row['method']: row for row in family['methods']}
    assert methods['dino_synthetic']['continuation'] == 'weight_initialization'
    assert methods['dino_synthetic']['architectures'] == ['dinov3_vits16', 'dinov3_vitb16', 'dinov3_vitl16']
    assert methods['dino_synthetic']['map_semantics'] == 'patch_score'
    assert methods['padim']['continuation'] == 'statistical_refit'
