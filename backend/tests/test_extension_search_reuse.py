"""Completed measurements are reusable with frozen search inputs."""
from pathlib import Path
from PIL import Image
import pytest
import torch


def setup(tmp_path, monkeypatch):
    import backend.engine.trainer as trainer
    import backend.engine.exporter as exporter
    source = tmp_path / 'source'
    for split in ('train', 'val'):
        for label, color in [('good', 'white'), ('defect', 'black')]:
            folder = source / split / label; folder.mkdir(parents=True)
            Image.new('RGB', (32, 32), color).save(folder / 'a.png')
    calls = []
    def factory(backbone, **kwargs):
        calls.append(backbone)
        return torch.nn.Sequential(torch.nn.AdaptiveAvgPool2d(1), torch.nn.Flatten(), torch.nn.Linear(3, 2))
    monkeypatch.setattr(trainer, 'create_classification_model', factory)
    def reconstruct(path):
        payload = torch.load(path, weights_only=True)
        instance = torch.nn.Sequential(torch.nn.AdaptiveAvgPool2d(1), torch.nn.Flatten(), torch.nn.Linear(3, 2))
        instance.load_state_dict(payload['model_state_dict']); return instance.eval(), payload, None
    monkeypatch.setattr(exporter, 'load_checkpoint_and_reconstruct_model', reconstruct)
    torch.set_num_threads(1)
    return {'task': 'classification', 'dataset_path': source, 'models_dir': tmp_path / 'models',
            'base_config': {'image_size': 32, 'batch_size': 1, 'pretrained': False}, 'epochs_per_trial': 1,
            'search_space': {'architectures': ['dinov3_vits16', 'dinov3_vitb16'], 'learning_rates': [.003],
                             'weight_decays': [.01], 'image_sizes': [32], 'batch_sizes': [1], 'augmentation_profiles': ['none']}}, calls


def test_reuse_spends_epochs_only_on_new_trials_and_preserves_checkpoints(tmp_path, monkeypatch):
    from backend.engine.automated_trials import run_automated_training
    options, calls = setup(tmp_path, monkeypatch)
    first = run_automated_training(**options, seed=7, budget={'max_trials': 1, 'max_total_epochs': 1, 'max_seconds': 30})
    assert first['status'] == 'completed', first
    winner = first['winner']; before = Path(winner['checkpoint_path']).read_bytes()
    reused = run_automated_training(**options, seed=7, reuse_search_id=first['search_id'],
                                   budget={'max_trials': 2, 'max_total_epochs': 1, 'max_seconds': 30})
    assert reused['status'] == 'completed', reused
    assert calls == ['dinov3_vits16', 'dinov3_vitb16']
    assert len(reused['trials']) == 2 and reused['epochs_consumed'] == 1
    assert reused['trials'][0]['reused_from_search_id'] == first['search_id']
    assert reused['trials'][0]['checkpoint_sha256'] == winner['checkpoint_sha256']
    assert Path(winner['checkpoint_path']).read_bytes() == before


def test_reuse_rejects_changed_seed_or_measured_checkpoint(tmp_path, monkeypatch):
    from backend.engine.automated_trials import run_automated_training
    options, _ = setup(tmp_path, monkeypatch)
    first = run_automated_training(**options, seed=7, budget={'max_trials': 1, 'max_total_epochs': 1, 'max_seconds': 30})
    with pytest.raises(ValueError, match='identity|seed'):
        run_automated_training(**options, seed=8, reuse_search_id=first['search_id'])
    Path(first['winner']['checkpoint_path']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='checkpoint|hash'):
        run_automated_training(**options, seed=7, reuse_search_id=first['search_id'])
