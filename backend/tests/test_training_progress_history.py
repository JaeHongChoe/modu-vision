import json
from types import SimpleNamespace

from backend.api import routes_training
from backend.remote.worker import _StatusWriter, _TrainingStatusCallback


def test_remote_epoch_history_survives_step_updates(tmp_path):
    writer = _StatusWriter(tmp_path, 'job_test', 'train', 'a' * 64)
    callback = _TrainingStatusCallback(writer)
    callback.on_epoch_end(0, 12, 0.8, 0.9, 0.001, {'iou': 0.2})
    callback.on_step_end(4, 48, 0.7, 1)
    callback.on_epoch_end(1, 12, 0.6, 0.8, 0.0009, {'iou': 0.3})
    record = json.loads((tmp_path / 'status.json').read_text())
    assert record['current_epoch'] == 2
    assert record['loss_history'] == [
        {'epoch': 1, 'train_loss': 0.8, 'val_loss': 0.9, 'lr': 0.001},
        {'epoch': 2, 'train_loss': 0.6, 'val_loss': 0.8, 'lr': 0.0009},
    ]
    assert record['metrics'] == {'iou': 0.3}


def test_local_synthetic_epoch_retains_absent_validation_loss_through_real_callback(tmp_path, monkeypatch):
    callbacks = []
    monkeypatch.setattr(routes_training, 'UnifiedAutoMLTrainer',
                        lambda **kwargs: callbacks.append(kwargs['callback']) or SimpleNamespace())
    monkeypatch.setattr(routes_training.threading, 'Thread',
                        lambda **kwargs: SimpleNamespace(start=lambda: None))
    manager = routes_training.TrainingJobManager()
    manager._leases = SimpleNamespace(acquire=lambda *args: True, release=lambda *args, **kw: None)
    record = manager.start_job('job_synthetic', 'anomaly', str(tmp_path), str(tmp_path))
    callbacks[0].on_epoch_end(0, 2, 0.4, None, 0.001, {'val_image_auroc': None})
    assert record.current_epoch == 1 and record.val_loss is None
    assert record.loss_history == [{'epoch': 1, 'train_loss': 0.4, 'val_loss': None, 'lr': 0.001}]
    record.status = 'completed'
    routes_training._write_job_receipt(record)
    saved = json.loads((tmp_path / 'job_receipt.json').read_text())
    assert saved['current_val_loss'] is None and saved['loss_history'][0]['val_loss'] is None


def test_remote_synthetic_epoch_retains_absent_validation_loss(tmp_path):
    writer = _StatusWriter(tmp_path, 'job_synthetic', 'train', 'a' * 64)
    _TrainingStatusCallback(writer).on_epoch_end(0, 2, 0.4, None, 0.001, {'val_image_auroc': None})
    saved = json.loads((tmp_path / 'status.json').read_text())
    assert saved['val_loss'] is None and saved['loss_history'][0]['val_loss'] is None


def test_remote_abort_preserves_displayed_epoch(tmp_path):
    writer = _StatusWriter(tmp_path, 'job_test', 'train', 'a' * 64)
    callback = _TrainingStatusCallback(writer)
    callback.on_step_end(4, 48, 0.7, 2)
    callback.on_training_aborted(2, 'cancelled')
    record = json.loads((tmp_path / 'status.json').read_text())
    assert record['current_epoch'] == 3
    assert record['current_step'] == 5
    assert record['status'] == 'stopping'


def test_remote_abort_before_first_step_remains_epoch_zero(tmp_path):
    writer = _StatusWriter(tmp_path, 'job_test', 'train', 'a' * 64)
    _TrainingStatusCallback(writer).on_training_aborted(0, 'cancelled')
    assert json.loads((tmp_path / 'status.json').read_text())['current_epoch'] == 0


def test_terminal_receipt_retains_progress_and_poll_history(tmp_path, monkeypatch):
    record = routes_training.JobRecord('job_test', 'segmentation', 'fast', str(tmp_path), str(tmp_path), 'completed')
    record.current_epoch = 2
    record.total_epochs = 12
    record.loss_history = [{'epoch': 1, 'train_loss': .8, 'val_loss': .9, 'lr': .001}]
    routes_training._write_job_receipt(record)
    saved = json.loads((tmp_path / 'job_receipt.json').read_text())
    assert saved['loss_history'] == record.loss_history
    assert saved['current_epoch'] == 2
    monkeypatch.setattr(routes_training.training_job_manager, 'get_job', lambda _: record)
    assert routes_training.get_training_status('job_test')['loss_history'] == record.loss_history
