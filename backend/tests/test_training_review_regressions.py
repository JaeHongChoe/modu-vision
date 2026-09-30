"""Regression evidence for validation isolation and parent reconstruction."""
import json

import pytest
import torch
from PIL import Image


def test_detection_validation_does_not_update_batchnorm_buffers(tmp_path, monkeypatch):
    from backend.engine import trainer as runtime
    from backend.engine.synthetic_generator import generate_synthetic_dataset

    torch.set_num_threads(1)
    generate_synthetic_dataset(output_dir=tmp_path / 'data', num_samples=4,
        modality='pcb', task='detection', image_size=(64, 64), split_ratio=0.5)
    original = runtime.create_detection_model
    validations = []

    def monitored_factory(**kwargs):
        model = original(**kwargs)
        before = {}

        def snapshot(module):
            return {name: value.detach().clone() for name, value in module.named_buffers()
                    if name.endswith(('running_mean', 'running_var', 'num_batches_tracked'))}

        def record_before(module, args):
            if not torch.is_grad_enabled() and len(args) == 2:
                before.update(snapshot(module))

        def record_after(module, args, result):
            if not torch.is_grad_enabled() and len(args) == 2:
                after = snapshot(module)
                validations.append([name for name in before if not torch.equal(before[name], after[name])])

        model.register_forward_pre_hook(record_before)
        model.register_forward_hook(record_after)
        return model

    monkeypatch.setattr(runtime, 'create_detection_model', monitored_factory)
    result = runtime.UnifiedAutoMLTrainer('detection', tmp_path / 'data' / 'detection',
        tmp_path / 'model', device='cpu', config_overrides={'epochs': 1, 'batch_size': 2,
        'image_size': 64, 'backbone': 'yolo26n', 'pretrained': False}).train('job_fixture')
    assert result['status'] == 'completed', result
    assert validations, 'The production trainer must evaluate held-out detection data'
    assert all(not changed for changed in validations), f'Validation updated BatchNorm buffers: {validations}'


def test_portable_parent_rechecks_current_completed_receipt_before_copy(tmp_path):
    from backend.tests.test_warm_start import _parent
    from backend.engine.warm_start import resolve_warm_start_parent, portable_parent
    models, source, checkpoint, _ = _parent(tmp_path)
    parent = resolve_warm_start_parent('job_parent01', models, source, 'classification', 'classification:resnet18')
    receipt_path = checkpoint.parent / 'job_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    receipt['status'] = 'stopped'
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='completed'):
        portable_parent(parent, tmp_path / 'transfer')
    assert not (tmp_path / 'transfer' / 'parent.pt').exists()


@pytest.mark.parametrize('consumer', ['evaluation', 'export'])
def test_explicit_padim_precision_reconstructs_saved_padim_features(tmp_path, monkeypatch, consumer):
    from backend.engine.anomaly import PaDiMDetector
    from backend.engine.anomaly import reconstruction
    from backend.engine import exporter
    from backend.api import routes_evaluation

    torch.set_num_threads(1)
    parent = PaDiMDetector(pretrained=False, target_dim=4)
    parent.fit(torch.utils.data.DataLoader(torch.rand(2, 3, 32, 32), batch_size=2))
    metadata = {'task': 'anomaly', 'detector_type': 'padim', 'preset': 'precision',
        'classes': ['good', 'anomaly'], 'image_size': [32, 32]}
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({**metadata, 'model_state_dict': parent.state_dict()}, checkpoint)

    def wrong_detector(*args, **kwargs):
        raise AssertionError('Saved detector_type=padim must take precedence over precision preset')

    monkeypatch.setattr(reconstruction, 'PatchCoreDetector', wrong_detector)
    if consumer == 'export':
        _, _, restored = exporter.load_checkpoint_and_reconstruct_model(checkpoint)
        assert isinstance(restored, PaDiMDetector)
        assert torch.equal(restored.feature_extractor.backbone.conv1.weight,
                           parent.feature_extractor.backbone.conv1.weight)
    else:
        dataset = tmp_path / 'dataset'
        for label, color in [('good', (20, 20, 20)), ('fixture_defect', (220, 90, 90))]:
            directory = dataset / 'test' / label
            directory.mkdir(parents=True)
            Image.new('RGB', (32, 32), color).save(directory / 'fixture.png')
        result = routes_evaluation._evaluate_anomaly(checkpoint, metadata, dataset, torch.device('cpu'))
        assert len(result['test_predictions']) == 2
        assert result['confusion_matrix']['classes'] == ['good', 'anomaly']
