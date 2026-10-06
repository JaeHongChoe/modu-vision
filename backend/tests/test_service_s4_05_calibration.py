"""Actual normal-distance fitting must pin a separate validation calibration."""
import copy
import hashlib
import io
import json

import numpy as np
import pytest
import torch
from PIL import Image, PngImagePlugin
from torch.utils.data import DataLoader, TensorDataset

from backend.engine.anomaly.padim import PaDiMDetector
from backend.engine.anomaly.patchcore import PatchCoreDetector
from backend.engine.dataset_loaders import AnomalyDataset
from backend.engine.score_contract import calibrated_score_spec


@pytest.fixture(autouse=True)
def bounded_cpu_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def sources(tmp_path):
    rng = np.random.default_rng(115)
    for split, labels in [('train', ['good', 'good']), ('val', ['good', 'good', 'defect']), ('test', ['good', 'defect'])]:
        for index, label in enumerate(labels):
            directory = tmp_path / split / label
            directory.mkdir(parents=True, exist_ok=True)
            pixels = rng.integers(90, 180 if split == 'val' else 150, (48, 64, 3), dtype=np.uint8)
            Image.fromarray(pixels).save(directory / f'{split}_{index}.png')
    training = AnomalyDataset(tmp_path, split='train', image_size=(32, 32))
    validation = AnomalyDataset(tmp_path, split='val', image_size=(32, 32))
    calibration = copy.copy(validation)
    calibration.samples = [row for row in validation.samples if row[1] == 0]
    return training, validation, calibration


def detector(method, seed=42):
    if method == 'padim':
        return PaDiMDetector(pretrained=False, target_dim=4, device='cpu', seed=seed)
    return PatchCoreDetector(pretrained=False, max_coreset_size=8, device='cpu', seed=seed)


@pytest.mark.parametrize('method', ['padim', 'patchcore'])
def test_actual_fit_uses_only_independent_validation_normal_scores_and_restores_identity(tmp_path, method):
    training, validation, calibration = sources(tmp_path)
    model = detector(method)
    model.fit(DataLoader(training, batch_size=2), calibration_dataset=calibration)
    scores = [model.predict_anomaly_map(calibration[index][0])[1] for index in range(len(calibration))]
    expected = round(float(np.mean(scores) + 3 * np.std(scores)), 4)
    assert model.threshold == expected
    assert model.calibration['method'] == 'heldout_normal_mean_plus_3_std'
    assert model.calibration['split'] == 'val'
    assert model.calibration['normal_image_count'] == 2
    assert model.calibration['comparison'] == '>='
    assert {row['source_sha256'] for row in model.calibration['images']} == {hashlib.sha256(row[0].read_bytes()).hexdigest() for row in calibration.samples}
    assert not any(row[0].name in repr(model.calibration) for row in validation.samples)
    buffer = io.BytesIO(); torch.save(model.state_dict(), buffer); buffer.seek(0)
    state = torch.load(buffer, weights_only=True)
    restored = detector(method); restored.load_state_dict(state)
    assert restored.calibration == model.calibration
    assert restored.state_dict()['score_spec'] == state['score_spec']
    assert restored.threshold == expected
    for index in range(len(calibration)):
        actual_map, actual_score = restored.predict_anomaly_map(calibration[index][0])
        expected_map, expected_score = model.predict_anomaly_map(calibration[index][0])
        np.testing.assert_array_equal(actual_map, expected_map)
        assert actual_score == expected_score


@pytest.mark.parametrize('method', ['padim', 'patchcore'])
@pytest.mark.parametrize('invalid', ['test_split', 'training_path', 'decoded_copy', 'defect', 'empty'])
def test_calibration_refuses_leakage_non_normal_and_absent_sources(tmp_path, method, invalid):
    training, validation, calibration = sources(tmp_path)
    if invalid == 'test_split': calibration.split = 'test'
    elif invalid == 'training_path': calibration.samples[0] = training.samples[0]
    elif invalid == 'decoded_copy':
        target = calibration.samples[0][0]
        with Image.open(training.samples[0][0]) as image:
            metadata = PngImagePlugin.PngInfo(); metadata.add_text('fixture', 'separate encoding same decoded pixels')
            image.save(target, pnginfo=metadata, compress_level=1)
        assert target.read_bytes() != training.samples[0][0].read_bytes()
    elif invalid == 'defect': calibration.samples = validation.samples
    else: calibration.samples = []
    with pytest.raises(ValueError):
        detector(method).fit(DataLoader(training, batch_size=2), calibration_dataset=calibration)


@pytest.mark.parametrize('method', ['padim', 'patchcore'])
def test_even_legacy_tensor_fit_refuses_non_normal_labels(method):
    dataset = TensorDataset(torch.rand(2, 3, 32, 32), torch.tensor([0, 1]))
    with pytest.raises(ValueError, match='normal'):
        detector(method).fit(DataLoader(dataset, batch_size=2))


@pytest.mark.parametrize('method', ['padim', 'patchcore'])
def test_validation_source_replacement_during_fit_is_refused(tmp_path, monkeypatch, method):
    training, _, calibration = sources(tmp_path); model = detector(method)
    forward = model.feature_extractor.forward
    changed = False
    def replace_then_forward(images):
        nonlocal changed
        if not changed:
            Image.new('RGB', (64, 48), 'black').save(calibration.samples[0][0]); changed = True
        return forward(images)
    monkeypatch.setattr(model.feature_extractor, 'forward', replace_then_forward)
    with pytest.raises(ValueError, match='changed'):
        model.fit(DataLoader(training, batch_size=2), calibration_dataset=calibration)


@pytest.mark.parametrize('method', ['padim', 'patchcore'])
def test_old_checkpoint_without_calibration_keeps_exact_score_identity(tmp_path, method):
    training, _, _ = sources(tmp_path); model = detector(method, seed=115)
    model.fit(DataLoader(training, batch_size=2)); state = model.state_dict()
    state.pop('calibration', None)
    unit = 'mahalanobis_distance' if method == 'padim' else 'euclidean_distance'
    state['score_spec'] = calibrated_score_spec(state, unit)
    restored = detector(method); restored.load_state_dict(state)
    assert restored.state_dict()['score_spec'] == state['score_spec']
    assert 'calibration' not in restored.state_dict()


@pytest.fixture(scope='module')
def explicit_feature_weights(tmp_path_factory):
    from torchvision.models import resnet18
    torch.manual_seed(115)
    state = resnet18(weights=None).state_dict()
    path = tmp_path_factory.mktemp('owned-anomaly-weights') / 'resnet18.pth'
    torch.save(state, path)
    yield path, hashlib.sha256(path.read_bytes()).hexdigest(), state
    path.unlink()


@pytest.mark.parametrize('factory', [PaDiMDetector, PatchCoreDetector])
def test_explicit_normal_feature_checkpoint_is_used_without_fetching(factory, explicit_feature_weights, monkeypatch):
    path, digest, state = explicit_feature_weights
    def refuse_download(*args, **kwargs):
        raise AssertionError('Explicit feature checkpoints cannot fetch alternate weights')
    monkeypatch.setattr(torch.hub, 'download_url_to_file', refuse_download)
    model = factory(pretrained=True, pretrained_checkpoint=str(path), pretrained_sha256=digest)
    actual = model.feature_extractor.backbone.state_dict()
    assert set(actual) == set(state)
    assert all(torch.equal(actual[key], state[key]) for key in state)
    assert model.model_metadata['pretrained_sha256'] == digest


@pytest.mark.parametrize('factory', [PaDiMDetector, PatchCoreDetector])
def test_explicit_normal_feature_checkpoint_hash_mismatch_refuses(factory, explicit_feature_weights):
    path, _, _ = explicit_feature_weights
    with pytest.raises(ValueError, match='hash'):
        factory(pretrained=True, pretrained_checkpoint=str(path), pretrained_sha256='0' * 64)


@pytest.mark.parametrize('method', ['padim', 'patchcore'])
def test_production_trainer_persists_independent_calibration_and_feature_origin(tmp_path, method, explicit_feature_weights):
    from backend.engine.trainer import UnifiedAutoMLTrainer
    training, _, calibration = sources(tmp_path / 'source')
    path, digest, _ = explicit_feature_weights
    output = tmp_path / 'model'
    trainer = UnifiedAutoMLTrainer('anomaly', training.root_dir, output, device='cpu', config_overrides={
        'anomaly_method': method, 'pretrained': True, 'pretrained_checkpoint': str(path),
        'pretrained_sha256': digest, 'image_size': 32, 'batch_size': 2, 'seed': 115})
    try:
        result = trainer.train('independent-calibration')
        assert result['status'] == 'completed'
        state = torch.load(output / 'best_model.pt', weights_only=True)
        meta = json.loads((output / 'model_meta.json').read_text())
        assert meta['calibration']['split'] == 'val'
        assert meta['calibration']['normal_image_count'] == len(calibration)
        assert meta['calibration'] == state['model_state_dict']['calibration']
        assert meta['map_semantics'] == 'pixel_score'
        assert meta['pretrained_sha256'] == digest
        assert meta['quality_approved'] is False
    finally:
        for artifact in output.glob('*.pt'):
            artifact.unlink()


@pytest.mark.parametrize('options', [
    {'anomaly_method': 'dino_synthetic', 'anomaly_mode': 'segmentation'},
    {'anomaly_method': 'padim', 'anomaly_mode': 'unknown'},
    {'anomaly_method': 'unsupported', 'anomaly_mode': 'classification'},
])
def test_anomaly_controls_refuse_unsupported_purpose_before_execution(options):
    from backend.engine.model_backbones import validate_training_controls
    with pytest.raises(ValueError):
        validate_training_controls('anomaly', 'fast', options)


def _probability_anomaly_test(tmp_path):
    predictions = []
    for label, score in [('good', .12), ('anomaly', .88)]:
        path = tmp_path / f'{label}.png'
        Image.new('RGB', (8, 8), (12, 20, 30) if label == 'good' else (150, 10, 30)).save(path)
        predictions.append({'file_path': str(path), 'ground_truth': label, 'predicted_class': label,
                            'confidence': score, 'defect_score': score})
    payload = {'evaluation_contract_version': 2, 'task': 'anomaly', 'test_predictions': predictions,
               'metrics': {'evaluated_split': 'test', 'selection_overlap': False, 'anomaly_mode': 'classification',
                           'score_spec': {'domain': 'probability', 'unit': 'probability',
                                          'direction': 'higher_is_defect', 'calibration_id': 'owned-fixture'}}}
    file = tmp_path / 'eval_results.json'
    file.write_text(json.dumps(payload))
    return file


def test_heldout_anomaly_test_cannot_replace_persisted_calibration(tmp_path):
    from fastapi import HTTPException
    from backend.api.routes_evaluation import run_zero_escape_calibration
    file = _probability_anomaly_test(tmp_path); original = file.read_bytes()
    with pytest.raises(HTTPException) as caught:
        run_zero_escape_calibration(job_id=str(tmp_path))
    assert caught.value.status_code == 422
    assert 'test' in caught.value.detail.lower()
    assert file.read_bytes() == original


def test_anomaly_test_probability_diagnostics_remain_analysis_only(tmp_path):
    from backend.api.routes_evaluation import run_zero_escape_calibration
    file = _probability_anomaly_test(tmp_path); original = file.read_bytes()
    result = run_zero_escape_calibration(job_id=str(tmp_path), apply_to_eval_results=False)
    assert result['total_defects'] == result['total_normals'] == 1
    assert file.read_bytes() == original


@pytest.mark.parametrize('method', ['padim', 'patchcore'])
def test_heldout_evaluation_keeps_fixed_calibration_and_refuses_decoded_overlap(tmp_path, method):
    from fastapi import HTTPException
    from backend.api.routes_evaluation import _evaluate_anomaly
    training, _, calibration = sources(tmp_path / 'source')
    model = detector(method)
    model.fit(DataLoader(training, batch_size=2), calibration_dataset=calibration)
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({'model_state_dict': model.state_dict(), 'detector_type': method}, checkpoint)
    meta = {'image_size': [32, 32], 'anomaly_mode': 'classification'}
    try:
        result = _evaluate_anomaly(checkpoint, meta, training.root_dir, torch.device('cpu'))
        assert result['metrics']['active_threshold'] == model.threshold
        assert result['metrics']['calibration'] == model.calibration
        assert result['metrics']['threshold_basis'] == 'heldout_normal_calibration'
        test_normal = training.root_dir / 'test' / 'good' / 'test_0.png'
        with Image.open(calibration.samples[0][0]) as image:
            metadata = PngImagePlugin.PngInfo(); metadata.add_text('fixture', 'independently encoded duplicate')
            image.save(test_normal, pnginfo=metadata, compress_level=1)
        with pytest.raises(HTTPException) as caught:
            _evaluate_anomaly(checkpoint, meta, training.root_dir, torch.device('cpu'))
        assert caught.value.status_code == 422
        assert 'overlap' in caught.value.detail.lower()
    finally:
        checkpoint.unlink()
