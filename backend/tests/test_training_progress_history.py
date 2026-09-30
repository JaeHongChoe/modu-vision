import json

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
