"""Enhancement preserves the explicit device passed by specialist execution."""
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
import torch

from backend.engine import enhancement
from backend.engine.automated_trials import run_measured_candidate


@pytest.fixture
def paired_dataset(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for index in range(3):
        pixels = np.random.default_rng(index).integers(30, 210, (24, 32, 3), dtype=np.uint8)
        Image.fromarray(pixels).save(source / f'{index}.png')
    paired = tmp_path / 'paired'
    enhancement.prepare_enhancement(source, paired, seed=7)
    return paired


class DeviceBoundaryReached(Exception):
    def __init__(self, device):
        self.device = str(device)


@pytest.mark.parametrize('requested', ['cuda:0', 'cuda:2'])
def test_specialist_enhancement_accepts_indexed_cuda_without_changing_index(paired_dataset, tmp_path, monkeypatch, requested):
    # CUDA allocation is the only external boundary. The candidate dispatcher,
    # specialist adapter, manifest validation and enhancement parser stay real.
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: True)
    monkeypatch.setattr(torch.cuda, 'device_count', lambda: 3)

    def stop_before_cuda_allocation(self, device):
        raise DeviceBoundaryReached(device)

    monkeypatch.setattr(enhancement.RGBDenoiser, 'to', stop_before_cuda_allocation)
    with pytest.raises(DeviceBoundaryReached) as reached:
        run_measured_candidate(task='enhancement', dataset_path=paired_dataset,
            output_dir=tmp_path / 'candidate', job_id='candidate', device=requested,
            config_overrides={'epochs': 1, 'batch_size': 2, 'learning_rate': .001})
    assert reached.value.device == requested
    assert not (tmp_path / 'candidate').exists()


@pytest.mark.parametrize('requested', ['cuda', 'cuda:0', 'cuda:2'])
def test_enhancement_device_accepts_available_cuda_and_preserves_selection(monkeypatch, requested):
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: True)
    monkeypatch.setattr(torch.cuda, 'device_count', lambda: 3)
    assert str(enhancement._device(requested)) == requested


@pytest.mark.parametrize(('available', 'count', 'requested'), [
    (False, 0, 'cuda:0'),
    (True, 1, 'cuda:1'),
    (True, 0, 'cuda'),
])
def test_enhancement_refuses_unavailable_cuda_before_model_or_output(paired_dataset, tmp_path, monkeypatch, available, count, requested):
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: available)
    monkeypatch.setattr(torch.cuda, 'device_count', lambda: count)

    def reject_unavailable_allocation(self, device):
        raise AssertionError(f'Unavailable CUDA reached model allocation: {device}')

    monkeypatch.setattr(enhancement.RGBDenoiser, 'to', reject_unavailable_allocation)
    with pytest.raises(ValueError, match='unavailable'):
        enhancement.train_enhancement(paired_dataset, tmp_path / 'candidate', device=requested)
    assert not (tmp_path / 'candidate').exists()


@pytest.mark.parametrize('requested', ['auto', 'CUDA', 'cuda:-1', 'cuda:', 'cuda:1:0', 'cpu:0', 'mps:0'])
def test_enhancement_rejects_invalid_device_without_cpu_fallback(paired_dataset, tmp_path, requested):
    with pytest.raises(ValueError):
        enhancement.train_enhancement(paired_dataset, tmp_path / 'candidate', device=requested)
    assert not (tmp_path / 'candidate').exists()


def test_enhancement_mps_contract_preserves_available_target_and_refuses_unavailable(monkeypatch):
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: True)
    assert str(enhancement._device('mps')) == 'mps'
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: False)
    with pytest.raises(ValueError, match='unavailable'):
        enhancement._device('mps')


def test_real_cpu_specialist_enhancement_fit_produces_measured_checkpoint(paired_dataset, tmp_path, monkeypatch):
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: False)
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: False)
    before = {str(path.relative_to(paired_dataset)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paired_dataset.rglob('*') if path.is_file()}
    result = run_measured_candidate(task='enhancement', dataset_path=paired_dataset,
        output_dir=tmp_path / 'candidate', job_id='candidate', device='cpu',
        config_overrides={'epochs': 1, 'batch_size': 2, 'learning_rate': .001})
    assert result['status'] == 'completed'
    assert result['epochs_completed'] == 1
    assert result['latency_ms'] > 0
    assert np.isfinite(result['metrics']['output_mse'])
    checkpoint = Path(result['model_path'])
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == result['checkpoint_sha256']
    assert before == {str(path.relative_to(paired_dataset)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in paired_dataset.rglob('*') if path.is_file()}
