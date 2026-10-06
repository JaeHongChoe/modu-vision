"""Budgeted trial execution, measured selection and real trainer boundary coverage."""
import json
import threading
from pathlib import Path

import pytest
import torch
from PIL import Image
from torch import nn


def data(root):
    for split in ('train', 'val'):
        for label, color in [('normal', (20, 30, 40)), ('defect', (200, 100, 50))]:
            directory = root / split / label; directory.mkdir(parents=True)
            for i in range(2):
                image = Image.new('RGB', (32, 32), color)
                image.putpixel((0, 0), (i + (20 if split == 'val' else 1), 0, 0))
                image.save(directory / f'{i}.png')


class TinyProbe(nn.Module):
    def __init__(self, backbone):
        super().__init__()
        channels = 3 if backbone.endswith('s16') else 6
        self.encoder = nn.Conv2d(3, channels, 1); self.encoder.requires_grad_(False)
        self.head = nn.Linear(channels, 2)
        self.model_metadata = {'backbone': backbone, 'pretrained': True, 'pretrained_sha256': 'a' * 64}
    def forward(self, x):
        return self.head(self.encoder(x).mean((2, 3)))


def test_fast_retraining_keeps_recorded_rectangular_primary_input(tmp_path,monkeypatch):
    from backend.engine.automated_trials import run_automated_training
    import backend.engine.trainer as trainer
    import backend.engine.exporter as exporter
    monkeypatch.setattr(trainer,'create_classification_model',lambda backbone,**kwargs:TinyProbe(backbone))
    def reconstruct(path):
        payload=torch.load(path,weights_only=True);model=TinyProbe(payload['backbone']);model.load_state_dict(payload['model_state_dict'])
        return model.eval(),payload,None
    monkeypatch.setattr(exporter,'load_checkpoint_and_reconstruct_model',reconstruct)
    source=tmp_path/'source';data(source)
    initial=run_automated_training(task='classification',dataset_path=source,models_dir=tmp_path/'models',mode='quick',
        base_config={'image_size':[32,48],'batch_size':2},epochs_per_trial=1)
    assert initial['status']=='completed',initial
    parent=initial['winner'];before=Path(parent['checkpoint_path']).read_bytes()
    reused=run_automated_training(task='classification',dataset_path=source,models_dir=tmp_path/'models',mode='fast_retrain',
        parent_job_id=parent['trial_id'],epochs_per_trial=1)
    assert reused['status']=='completed',reused
    assert reused['winner']['config']['image_size']==[32,48]
    assert Path(parent['checkpoint_path']).read_bytes()==before


def test_search_executes_structures_hyperparameters_and_augmentations_with_real_optimizer(tmp_path, monkeypatch):
    from backend.engine.automated_trials import run_automated_training
    import backend.engine.trainer as trainer
    import backend.engine.exporter as exporter
    from backend.engine.augmentations import create_industrial_transforms
    seen = []
    def factory(backbone, **kwargs):
        seen.append(backbone); return TinyProbe(backbone)
    monkeypatch.setattr(trainer, 'create_classification_model', factory)
    monkeypatch.setattr(exporter, 'load_checkpoint_and_reconstruct_model', lambda path: (TinyProbe(torch.load(path, weights_only=True)['backbone']).eval(), torch.load(path, weights_only=True), None))
    source = tmp_path / 'data'; data(source); torch.set_num_threads(1)
    result = run_automated_training(task='classification', dataset_path=source, models_dir=tmp_path / 'models', device='cpu',
        budget={'max_trials': 2, 'max_total_epochs': 2, 'max_seconds': 60},
        search_space={'architectures': ['dinov3_vits16', 'dinov3_vitb16'], 'learning_rates': [.01, .003],
            'weight_decays': [0, .02], 'image_sizes': [32], 'batch_sizes': [2], 'augmentation_profiles': ['none', 'photometric']}, epochs_per_trial=1)
    assert seen == ['dinov3_vits16', 'dinov3_vitb16']
    assert result['status'] == 'completed'
    trials = result['trials']; assert len(trials) == 2
    assert {t['config']['augmentation_profile'] for t in trials} == {'none', 'photometric'}
    assert {t['config']['learning_rate'] for t in trials} == {.01, .003}
    assert all(t['status'] == 'completed' and t['latency_ms'] > 0 for t in trials)
    assert result['winner']['objective'] == min(t['objective'] for t in trials)
    for trial in trials:
        payload = torch.load(trial['checkpoint_path'], weights_only=True)
        assert payload['training_config']['augmentation_profile'] == trial['config']['augmentation_profile']
        assert payload['training_config']['weight_decay'] == trial['config']['weight_decay']
        assert (tmp_path / 'models' / trial['trial_id'] / 'job_receipt.json').exists()
    saved = json.loads((tmp_path / 'models' / 'automated_training' / result['search_id'] / 'search.json').read_text())
    assert saved['winner']['checkpoint_sha256'] == result['winner']['checkpoint_sha256']
    plain = create_industrial_transforms(profile='none')
    image = torch.rand(3, 32, 32); torch.testing.assert_close(plain(image), image)


def test_search_validates_budget_architecture_and_cancel_without_false_winner(tmp_path):
    from backend.engine.automated_trials import run_automated_training
    source = tmp_path / 'data'; data(source)
    with pytest.raises(ValueError, match='budget|positive'):
        run_automated_training(task='classification', dataset_path=source, models_dir=tmp_path / 'models', budget={'max_trials': 0})
    with pytest.raises(ValueError, match='DINO|architecture'):
        run_automated_training(task='classification', dataset_path=source, models_dir=tmp_path / 'models', search_space={'architectures': ['resnet18']})
    event = threading.Event(); event.set()
    result = run_automated_training(task='classification', dataset_path=source, models_dir=tmp_path / 'cancel', cancel_event=event)
    assert result['status'] == 'cancelled' and result['winner'] is None and result['trials'] == []


def test_augmentation_profile_is_applied_and_config_persisted(tmp_path, monkeypatch):
    from backend.engine.augmentations import create_industrial_transforms
    plain = create_industrial_transforms(profile='none')
    photo = create_industrial_transforms(profile='photometric')
    assert not plain.is_training
    assert not photo.flip_horizontal and not photo.flip_vertical and photo.max_rotation_deg == 0
    with pytest.raises(ValueError, match='augmentation'):
        create_industrial_transforms(profile='invented')


def test_fast_retrain_reuses_completed_parent_config_and_preserves_bytes(tmp_path, monkeypatch):
    from backend.engine.automated_trials import run_automated_training
    import backend.engine.trainer as trainer
    import backend.engine.exporter as exporter
    monkeypatch.setattr(trainer, 'create_classification_model', lambda backbone, **kwargs: TinyProbe(backbone))
    def reconstruct(path):
        payload = torch.load(path, weights_only=True); model = TinyProbe(payload['backbone'])
        model.load_state_dict(payload['model_state_dict']); return model.eval(), payload, None
    monkeypatch.setattr(exporter, 'load_checkpoint_and_reconstruct_model', reconstruct)
    source = tmp_path / 'data'; data(source); models = tmp_path / 'models'
    initial = run_automated_training(task='classification', dataset_path=source, models_dir=models, mode='quick', epochs_per_trial=1,
        base_config={'image_size': 32, 'batch_size': 2, 'learning_rate': .003, 'augmentation_profile': 'photometric'})
    parent = initial['winner']; assert parent is not None
    before = open(parent['checkpoint_path'], 'rb').read()
    reused = run_automated_training(task='classification', dataset_path=source, models_dir=models, mode='fast_retrain', epochs_per_trial=1, parent_job_id=parent['trial_id'])
    assert reused['status'] == 'completed', reused
    config = reused['winner']['config']
    assert config['image_size'] == 32 and config['learning_rate'] == .003 and config['augmentation_profile'] == 'photometric'
    saved = torch.load(reused['winner']['checkpoint_path'], weights_only=True)
    assert saved['warm_start']['parent_checkpoint_sha256'] == parent['checkpoint_sha256']
    receipt=json.loads(Path(reused['winner']['checkpoint_path']).with_name('job_receipt.json').read_text())
    assert receipt.get('warm_start')==saved['warm_start'], 'completed candidate receipt must preserve actual warm-start lineage'
    assert reused['configuration_parent']['configuration_sha256']
    assert open(parent['checkpoint_path'], 'rb').read() == before


def test_trial_timeout_cancels_owned_child_and_never_selects_unmeasured_model(tmp_path):
    from backend.engine.automated_trials import run_automated_training, register_task_runner, _RUNNERS
    import time
    observed = []
    def slow(context):
        while not context.cancel_event.wait(.01): pass
        observed.append(True); raise InterruptedError('owned child cancelled')
    register_task_runner('bounded_fixture', slow, ['bounded'])
    try:
        source = tmp_path / 'data'; data(source)
        result = run_automated_training(task='bounded_fixture', dataset_path=source, models_dir=tmp_path / 'models', epochs_per_trial=1,
            budget={'max_seconds': .05, 'max_trials': 2, 'max_total_epochs': 2})
        assert observed and result['winner'] is None and result['stop_reason'] == 'time_budget'
        assert result['trials'][0]['status'] == 'cancelled'
    finally: _RUNNERS.pop('bounded_fixture')


def test_empty_validation_cannot_become_zero_loss_winner(tmp_path, monkeypatch):
    from backend.engine.automated_trials import run_automated_training
    import backend.engine.trainer as trainer
    import shutil
    source = tmp_path / 'data'; data(source); shutil.rmtree(source / 'val')
    monkeypatch.setattr(trainer, 'create_classification_model', lambda backbone, **kwargs: TinyProbe(backbone))
    result = run_automated_training(task='classification', dataset_path=source, models_dir=tmp_path / 'models', mode='quick', epochs_per_trial=1,
        base_config={'image_size': 32})
    assert result['winner'] is None and result['trials'][0]['status'] == 'failed'
    assert 'nonempty' in result['trials'][0]['error']
