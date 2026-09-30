"""Exported patch inference preserves native source geometry and bounded batches."""
import importlib.util
import json
import subprocess
import sys

import numpy as np
import pytest
import torch
from PIL import Image


class NativePatchFixture(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.scale = torch.nn.Parameter(torch.tensor(4.0))
        self.batch_sizes = []

    def forward(self, images):
        if not torch.jit.is_tracing():
            self.batch_sizes.append(len(images))
        red = images[:, 0].mean(dim=(1, 2))
        blue = images[:, 2].mean(dim=(1, 2))
        return torch.stack((red * self.scale, 2 - red * self.scale, blue * self.scale), dim=1)


def _fixture(tmp_path, monkeypatch):
    from backend.engine import exporter, patch_classification
    torch.set_num_threads(1)
    model = NativePatchFixture()
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({'model_state_dict': model.state_dict()}, checkpoint)
    metadata = {'task': 'patch_classification', 'classes': ['mark', 'reference', 'scratch'],
        'normal_class': 'reference', 'backbone': 'fixture', 'patch_size': 64, 'stride': 48,
        'image_size': [24, 16], 'optimal_threshold': 0.55}
    (tmp_path / 'model_meta.json').write_text(json.dumps(metadata))
    height, width = 1041, 1053
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[..., 0] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
    image[..., 2] = np.linspace(255, 0, height, dtype=np.uint8)[:, None]
    path = tmp_path / 'source.png'
    Image.fromarray(image).save(path)
    created = []
    def factory(**kwargs):
        candidate = NativePatchFixture()
        created.append(candidate)
        return candidate
    monkeypatch.setattr(patch_classification, 'create_classification_model', factory)
    monkeypatch.setattr(exporter, 'create_classification_model', factory)
    monkeypatch.setattr(exporter, 'locate_checkpoint', lambda job_id: checkpoint)
    return checkpoint, path, metadata, created


def test_direct_native_patch_grid_exceeds_256_with_bounded_batches(tmp_path, monkeypatch):
    from backend.engine.patch_classification import predict_patch_classification
    checkpoint, image, _, created = _fixture(tmp_path, monkeypatch)
    result = predict_patch_classification(checkpoint, image, device='cpu')
    assert len(result['patches']) > 256
    assert max(created[-1].batch_sizes) <= 32
    assert result['patches'][-1]['box'] == [989, 977, 1053, 1041]
    chunked = predict_patch_classification(checkpoint, image, device='cpu', max_patches=None, batch_size=7)
    assert max(created[-1].batch_sizes) <= 7
    assert chunked['patches'] == result['patches']
    with pytest.raises(ValueError, match='limit is 256'):
        predict_patch_classification(checkpoint, image, device='cpu', max_patches=256)


@pytest.mark.parametrize('export_format', ['torchscript', 'onnx'])
def test_exported_patch_runner_preserves_native_grid_scores_and_default_paths(tmp_path, monkeypatch, export_format):
    from backend.engine import exporter
    from backend.engine.patch_classification import predict_patch_classification
    checkpoint, image, metadata, _ = _fixture(tmp_path, monkeypatch)
    package = exporter.export_runtime_package('job_fixture', export_format=export_format,
        resolution=256, package_name='native_patch_fixture', output_base_dir=tmp_path / 'packages')
    package_dir = tmp_path / 'packages' / package['package_name']
    config = json.loads((package_dir / 'config.json').read_text())
    assert {key: config[key] for key in ('normal_class', 'patch_size', 'stride', 'image_size')} == {
        key: metadata[key] for key in ('normal_class', 'patch_size', 'stride', 'image_size')}
    direct = predict_patch_classification(checkpoint, image, device='cpu', threshold=0.55, max_patches=None)
    monkeypatch.syspath_prepend(str(package_dir))
    spec = importlib.util.spec_from_file_location('native_patch_export_' + export_format, package_dir / 'infer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    inspector = module.StandaloneInspector()
    inspector.cfg['patch_batch_size'] = 7
    batches = []
    if export_format == 'onnx':
        original_run = inspector.session.run
        def monitored_run(output_names, inputs):
            batches.append(len(inputs[inspector.input_name]))
            return original_run(output_names, inputs)
        monkeypatch.setattr(inspector.session, 'run', monitored_run)
    else:
        original_model = inspector.torch_model
        def monitored_model(images):
            batches.append(len(images))
            return original_model(images)
        inspector.torch_model = monitored_model
    exported = inspector.inspect(str(image))
    assert max(batches) <= 7 and sum(batches) == len(direct['patches'])
    assert [row['box'] for row in exported['patches']] == [row['box'] for row in direct['patches']]
    assert [row['defect_score'] for row in exported['patches']] == pytest.approx(
        [row['defect_score'] for row in direct['patches']], abs=1e-6)
    assert exported['defect_score'] == pytest.approx(direct['max_defect_score'], abs=1e-6)
    assert exported['verdict'] == ('NG' if direct['decision'] == 'FAIL' else 'OK')
    assert 'defect_mask' not in exported
    inspector.cfg['max_patches'] = 256
    previous_batches = list(batches)
    with pytest.raises(ValueError, match='limit is 256'):
        inspector.inspect(str(image))
    assert batches == previous_batches
    output = tmp_path / 'cli_result.json'
    run = subprocess.run([sys.executable, str(package_dir / 'infer.py'), '--image', str(image),
        '--output', str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stderr + run.stdout
    assert json.loads(output.read_text())['patches'][-1]['box'] == [989, 977, 1053, 1041]


def test_patch_tensor_memory_guard_fails_before_allocating_model(tmp_path, monkeypatch):
    from backend.engine.patch_classification import predict_patch_classification
    checkpoint, image, metadata, created = _fixture(tmp_path, monkeypatch)
    (tmp_path / 'model_meta.json').write_text(json.dumps({**metadata, 'image_size': [100000, 100000]}))
    with pytest.raises(ValueError, match='memory'):
        predict_patch_classification(checkpoint, image, device='cpu', max_patches=None)
    assert not created


def test_native_grid_guards_total_work_before_materializing_coordinates():
    from backend.engine.native_patches import native_grid
    with pytest.raises(ValueError, match='safety limit'):
        native_grid(2048, 2048, 64, 1)
    with pytest.raises(ValueError, match='pixels'):
        native_grid(20000, 20000, 64, 48)
