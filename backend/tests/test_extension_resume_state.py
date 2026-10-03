"""Exact continuation includes stochastic inputs, Adam moments, AMP and LR state."""
import random
import numpy as np
import pytest
import torch


def seed():
    random.seed(83); np.random.seed(83); torch.manual_seed(83)


def components():
    from backend.engine.trainer import EarlyStopping
    model = torch.nn.Sequential(torch.nn.Linear(3, 5), torch.nn.Dropout(.3), torch.nn.Linear(5, 2))
    optimizer = torch.optim.AdamW(model.parameters(), lr=.03)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=.8)
    scaler = torch.amp.GradScaler('cpu', init_scale=16)
    return model, optimizer, scheduler, scaler, EarlyStopping()


def step(model, optimizer, scheduler, scaler, early):
    optimizer.zero_grad()
    loss = model(torch.randn(4, 3)).square().mean() * (random.random() + np.random.random())
    scaler.scale(loss).backward(); scaler.step(optimizer); scaler.update(); scheduler.step()
    early.step(float(loss.detach()), scheduler.last_epoch)
    return float(loss.detach())


def test_exact_resume_reproduces_next_stochastic_step_and_all_optimizer_state(tmp_path):
    from backend.engine.training_resume import save_training_state, restore_training_state
    seed(); original = components()
    step(*original); step(*original)
    path = tmp_path / 'latest_training_state.pt'
    identity = {'task': 'classification', 'dataset_fingerprint': 'frozen', 'recipe': {'epochs': 4}}
    save_training_state(path, *original[:4], identity=identity, next_epoch=2, global_step=2, early_stopping=original[4])
    expected_loss = step(*original)
    expected_weights = {name: value.clone() for name, value in original[0].state_dict().items()}
    seed(); restored = components()
    restored[3].scale(torch.tensor(1.))
    restored[3].update(new_scale=4.)
    state = restore_training_state(path, *restored[:4], identity=identity, early_stopping=restored[4])
    assert state['next_epoch'] == 2 and state['global_step'] == 2
    assert restored[3].get_scale() == 16
    assert step(*restored) == expected_loss
    for name, value in restored[0].state_dict().items():
        torch.testing.assert_close(value, expected_weights[name], rtol=0, atol=0)
    assert restored[2].state_dict() == original[2].state_dict()
    for original_state, restored_state in zip(original[1].state.values(), restored[1].state.values()):
        for name in original_state:
            torch.testing.assert_close(original_state[name], restored_state[name], rtol=0, atol=0)


@pytest.mark.parametrize('identity', [
    {'task': 'classification', 'dataset_fingerprint': 'changed', 'recipe': {'epochs': 4}},
    {'task': 'classification', 'dataset_fingerprint': 'frozen', 'recipe': {'epochs': 8}},
    {'task': 'segmentation', 'dataset_fingerprint': 'frozen', 'recipe': {'epochs': 4}},
])
def test_identity_change_rejects_exact_resume_before_loading_weights(tmp_path, identity):
    from backend.engine.training_resume import save_training_state, restore_training_state
    seed(); original = components(); step(*original)
    path = tmp_path / 'state.pt'
    save_training_state(path, *original[:4], identity={'task': 'classification', 'dataset_fingerprint': 'frozen', 'recipe': {'epochs': 4}},
                        next_epoch=1, global_step=1, early_stopping=original[4])
    restored = components(); before = restored[0][0].weight.clone()
    with pytest.raises(ValueError, match='identity'):
        restore_training_state(path, *restored[:4], identity=identity, early_stopping=restored[4])
    torch.testing.assert_close(restored[0][0].weight, before)


def test_legacy_model_checkpoint_is_warm_start_only(tmp_path):
    from backend.engine.training_resume import restore_training_state
    state = components(); path = tmp_path / 'best_model.pt'
    torch.save({'model_state_dict': state[0].state_dict(), 'epoch': 2}, path)
    with pytest.raises(ValueError, match='warm.start|training state'):
        restore_training_state(path, *state[:4], identity={}, early_stopping=state[4])


def test_trainer_continues_only_after_completed_epoch_without_replaying_steps(tmp_path, monkeypatch):
    from PIL import Image
    import backend.engine.trainer as module
    from backend.engine.trainer import UnifiedAutoMLTrainer, TrainingCallback
    torch.set_num_threads(1)
    def factory(**kwargs):
        return torch.nn.Sequential(torch.nn.Flatten(), torch.nn.Linear(3 * 32 * 32, 2), torch.nn.Dropout(.2))
    monkeypatch.setattr(module, 'create_classification_model', factory)
    data = tmp_path / 'dataset'
    for split in ('train', 'val'):
        for label, color in [('good', 'white'), ('defect', 'black')]:
            folder = data / split / label; folder.mkdir(parents=True)
            for index in range(2): Image.new('RGB', (32, 32), color).save(folder / f'{index}.png')
    config = {'epochs': 3, 'pretrained': False, 'batch_size': 2, 'image_size': 32, 'augmentation_profile': 'none'}
    seed(); full = UnifiedAutoMLTrainer('classification', data, tmp_path / 'full', device='cpu', config_overrides=config)
    full.train()
    class StopAfterEpoch(TrainingCallback):
        def on_epoch_end(self, epoch, *args, **kwargs): partial.abort()
    seed(); partial = UnifiedAutoMLTrainer('classification', data, tmp_path / 'partial', device='cpu',
        config_overrides=config, callback=StopAfterEpoch())
    assert partial.train()['status'] == 'aborted'
    checkpoint = tmp_path / 'partial' / 'latest_training_state.pt'
    assert checkpoint.is_file(), 'Completed epoch must retain exact continuation state'
    class RecordSteps(TrainingCallback):
        steps = []
        def on_step_end(self, step, *args): self.steps.append(step)
    callback = RecordSteps()
    resumed = UnifiedAutoMLTrainer('classification', data, tmp_path / 'resumed', device='cpu',
        config_overrides={**config, 'resume_checkpoint': str(checkpoint)}, callback=callback)
    assert resumed.train()['status'] == 'completed'
    assert callback.steps == [2, 3, 4, 5]
    expected = torch.load(tmp_path / 'full' / 'latest_training_state.pt', weights_only=True)
    actual = torch.load(tmp_path / 'resumed' / 'latest_training_state.pt', weights_only=True)
    assert actual['next_epoch'] == 3 and actual['global_step'] == 6
    for name, value in actual['model_state_dict'].items():
        torch.testing.assert_close(value, expected['model_state_dict'][name], rtol=0, atol=0)
    metadata = __import__('json').loads((tmp_path / 'resumed' / 'model_meta.json').read_text())
    assert metadata['resume']['semantics'] == 'exact_resume'
    assert metadata['resume']['boundary'] == 'epoch'


def test_resume_identity_pins_backend_numeric_flags(tmp_path):
    from backend.engine.training_resume import build_identity
    model = torch.nn.Linear(2, 2)
    args = dict(task='classification', preset='fast', recipe={'backbone': 'dinov3_vits16'}, dataset_path=tmp_path,
        classes=['a', 'b'], model=model, device='cpu')
    old = torch.backends.cudnn.benchmark
    try:
        baseline = build_identity(**args)
        torch.backends.cudnn.benchmark = not old
        changed = build_identity(**args)
        assert baseline != changed, 'Exact continuation must pin cudnn/TF32 numeric configuration'
        assert 'cuda_matmul_allow_tf32' in baseline['backend_flags']
    finally: torch.backends.cudnn.benchmark = old
