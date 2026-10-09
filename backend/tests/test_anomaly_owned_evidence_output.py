"""Common Anomaly evaluation owns evidence without changing a saved model.

The genuine CPU control uses fixed test distribution statistics and saved full
ResNet features, not training, downloaded weights or model-quality approval.
Root executes these controls; this Source candidate has not been executed.
"""
import copy
import hashlib
import json
from pathlib import Path
from threading import Event

import numpy as np
import pytest
import torch
from PIL import Image
from pydantic import ValidationError

from backend.api import routes_evaluation
from backend.remote import evaluation_cohort as cohort


def raw_tree(root):
    root = Path(root)
    directories = sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_dir())
    files = {p.relative_to(root).as_posix(): (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
             for p in root.rglob('*') if p.is_file()}
    return directories, files


@pytest.fixture
def saved_anomaly(tmp_path):
    from backend.engine.anomaly import PaDiMDetector
    old_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        model = PaDiMDetector(pretrained=False, target_dim=4, device='cpu')
        # This bounded saved fixture has complete features and fixed valid
        # distribution tensors; it neither fits nor trains a model.
        model.mean = torch.zeros(1, 1, 1, 4)
        model.cov_inv = torch.eye(4).reshape(1, 1, 4, 4)
        model.threshold = 0.5
        parent = tmp_path / 'saved-model'
        parent.mkdir()
        checkpoint = parent / 'best_model.pt'
        metadata = {'task': 'anomaly', 'detector_type': 'padim', 'feature_backbone': 'resnet18',
                    'classes': ['good', 'anomaly'], 'image_size': [32, 32], 'anomaly_mode': 'classification'}
        torch.save({**metadata, 'model_state_dict': model.state_dict()}, checkpoint)
        (parent / 'model_meta.json').write_text(json.dumps(metadata))
        data = tmp_path / 'heldout'
        (data / 'val').mkdir(parents=True)
        (data / 'val/.saved-empty-partition').write_bytes(b'')
        samples = []
        for index, (directory, label) in enumerate([('good', 'good'), ('defect', 'anomaly')]):
            image = data / 'test' / directory / ('image_' + str(index) + '.png')
            image.parent.mkdir(parents=True)
            Image.new('RGB', (32, 32), (30 + index * 100, 45, 60)).save(image)
            samples.append({'sample_id': 'sample_' + str(index), 'path': image.relative_to(data).as_posix(),
                            'sha256': hashlib.sha256(image.read_bytes()).hexdigest(), 'truth': {'label': label}})
        descriptor = {'classes': ['good', 'anomaly'], 'ordered_samples': samples,
                      'class_semantics': routes_evaluation._evaluation_class_semantics(metadata, 'anomaly'),
                      'cohort_sha256': 'a' * 64}
        spec = {'task': 'anomaly', 'job_id': 'job_saved_fixture', 'device': 'cpu', 'execution_target': 'local',
                'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                'metadata_sha256': hashlib.sha256((parent / 'model_meta.json').read_bytes()).hexdigest(),
                'evaluation_cohort': {'dataset_version_id': 'version_fixture', 'archive_sha256': 'b' * 64,
                                      'snapshot_manifest_sha256': 'c' * 64, 'version_manifest_sha256': 'd' * 64},
                'compute_profile_id': None, 'compute_profile_name': None, 'compute_gpu_selector': None,
                'execution_profile_sha256': None, 'evaluation_binding_sha256': 'e' * 64,
                'input_manifest_sha256': 'f' * 64}
        yield checkpoint, metadata, data, descriptor, spec, tmp_path / 'owned-run'
    finally:
        torch.set_num_threads(old_threads)


def run_worker(values):
    checkpoint, metadata, data, descriptor, spec, run = values
    return cohort.worker_evaluate(spec, checkpoint, metadata, data, descriptor, run, Event())


def test_common_worker_runs_saved_cpu_model_without_mutating_checkpoint_namespace(saved_anomaly):
    checkpoint, metadata, data, descriptor, spec, run = saved_anomaly
    before = raw_tree(checkpoint.parent)
    result, artifacts = run_worker(saved_anomaly)
    assert raw_tree(checkpoint.parent) == before
    assert not (checkpoint.parent / 'evaluation_maps').exists()
    assert result['device'] == result['resolved_device'] == 'cpu'
    assert result['runtime_device_identity']['device'] == 'cpu'
    assert result['common_cohort']['image_count'] == 2
    assert {r['ground_truth'] for r in result['test_predictions']} == {'good', 'anomaly'}
    assert len(artifacts) == 2 and artifacts[0] == 'outputs/eval_results.json'
    evidence_names = {row['pixel_evidence']['file_path'] for row in result['test_predictions']}
    assert evidence_names == {artifacts[1]}
    assert Path(artifacts[1]).parent.as_posix() == 'outputs/evidence'
    evidence_path = run / artifacts[1]
    with np.load(evidence_path, allow_pickle=False) as arrays:
        for row, sample in zip(result['test_predictions'], descriptor['ordered_samples']):
            assert row['sample_id'] == sample['sample_id']
            assert row['input_sha256'] == sample['sha256']
            assert row['truth_sha256'] == cohort.digest(sample['truth'])
            assert hashlib.sha256(evidence_path.read_bytes()).hexdigest() == row['pixel_evidence']['sha256']
            heatmap = arrays[row['pixel_evidence']['heatmap_key']]
            assert heatmap.shape == (32, 32) and np.isfinite(heatmap).all()


def test_direct_evaluator_default_preserves_legacy_model_local_evidence(saved_anomaly):
    checkpoint, metadata, data, *_ = saved_anomaly
    result = routes_evaluation._evaluate_anomaly(checkpoint, metadata, data, torch.device('cpu'))
    evidence = result['test_predictions'][0]['pixel_evidence']
    output = Path(evidence['file_path'])
    assert output.parent == checkpoint.parent / 'evaluation_maps'
    assert hashlib.sha256(output.read_bytes()).hexdigest() == evidence['sha256']
    with np.load(output, allow_pickle=False) as arrays:
        assert arrays[evidence['heatmap_key']].shape == (32, 32)


def test_internal_explicit_evidence_directory_preserves_original_model(saved_anomaly):
    checkpoint, metadata, data, *_ = saved_anomaly
    output = data.parent / 'owned-evidence'
    output.mkdir()
    before = raw_tree(checkpoint.parent)
    result = routes_evaluation._evaluate_anomaly(checkpoint, metadata, data, torch.device('cpu'),
                                                 evidence_output_dir=output)
    assert raw_tree(checkpoint.parent) == before
    assert {Path(row['pixel_evidence']['file_path']).parent for row in result['test_predictions']} == {output}


@pytest.mark.parametrize('shape', ['evidence_link', 'outputs_link', 'existing_directory', 'existing_file'])
def test_common_worker_refuses_unowned_or_used_evidence_root_before_evaluator(saved_anomaly, monkeypatch, shape):
    *_, run = saved_anomaly
    run.mkdir()
    foreign = run.parent / 'foreign'
    foreign.mkdir()
    if shape == 'outputs_link':
        (run / 'outputs').symlink_to(foreign, target_is_directory=True)
    else:
        (run / 'outputs').mkdir()
        path = run / 'outputs/evidence'
        if shape == 'evidence_link': path.symlink_to(foreign, target_is_directory=True)
        elif shape == 'existing_directory': path.mkdir()
        else: path.write_bytes(b'prior owned file')
    calls = []
    monkeypatch.setattr(routes_evaluation, '_evaluate_anomaly', lambda *args, **kwargs: calls.append(kwargs))
    before = raw_tree(foreign)
    with pytest.raises((ValueError, FileExistsError)):
        run_worker(saved_anomaly)
    assert calls == [] and raw_tree(foreign) == before


@pytest.mark.parametrize('shape', ['foreign_path', 'linked_file', 'wrong_hash', 'nested_file'])
def test_common_worker_refuses_evaluator_evidence_outside_exact_owned_root(saved_anomaly, monkeypatch, shape):
    checkpoint, metadata, data, descriptor, spec, run = saved_anomaly
    foreign = checkpoint.parent / 'retained.npz'
    foreign.write_bytes(b'original retained evidence')
    before = raw_tree(checkpoint.parent)
    def controlled_evaluator(*args, evidence_output_dir, **kwargs):
        path = evidence_output_dir / 'map.npz'
        if shape == 'foreign_path': path = foreign
        elif shape == 'linked_file': path.symlink_to(foreign)
        elif shape == 'nested_file':
            path = evidence_output_dir / 'nested/map.npz'
            path.parent.mkdir(); path.write_bytes(b'owned but wrong parent')
        else: path.write_bytes(b'owned test evidence')
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        return {'test_predictions': [{'file_path': str(data / descriptor['ordered_samples'][0]['path']),
                                     'pixel_evidence': {'file_path': str(path),
                                                       'sha256': '0' * 64 if shape == 'wrong_hash' else checksum}}]}
    monkeypatch.setattr(routes_evaluation, '_evaluate_anomaly', controlled_evaluator)
    with pytest.raises(ValueError, match='outside|linked|binding'):
        run_worker(saved_anomaly)
    assert raw_tree(checkpoint.parent) == before
    assert not (run / 'outputs/evidence/retained.npz').exists()


@pytest.mark.parametrize('shape', ['linked', 'missing'])
def test_internal_evaluator_refuses_invalid_output_before_checkpoint_load(saved_anomaly, monkeypatch, shape):
    checkpoint, metadata, data, *_ = saved_anomaly
    output = data.parent / 'invalid-output'
    if shape == 'linked': output.symlink_to(data, target_is_directory=True)
    calls = []
    monkeypatch.setattr(torch, 'load', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match='linked|unavailable'):
        routes_evaluation._evaluate_anomaly(checkpoint, metadata, data, torch.device('cpu'), evidence_output_dir=output)
    assert calls == []


def test_external_core_request_cannot_choose_anomaly_evidence_output():
    from backend.engine.core_evaluation_recipe import CoreEvaluationRequest
    with pytest.raises(ValidationError):
        CoreEvaluationRequest(job_id='job_saved_fixture', dataset_path='owned-data',
                              evaluation_dataset_version_id='version_fixture', evidence_output_dir='/foreign')
