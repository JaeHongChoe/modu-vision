"""Ended remote status readback preserves exact native/remote identity pairs."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.routes_training import JobRecord, _write_job_receipt
from backend.remote import coordinator
from backend.remote.profiles import ComputeProfile


def terminal_run(tmp_path, monkeypatch, task='rotation', status='completed', *, native=True):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    native_id = 'a' * 32
    remote_id = 'job_' + native_id
    output = tmp_path / 'models' / task / (native_id if native else remote_id)
    output.mkdir(parents=True)
    profile = ComputeProfile(id='ended-worker', name='Ended controlled worker',
        ssh_target='worker.invalid', ssh_port=22, remote_root='/owned/worker',
        runtime_kind='python', runtime_value='/usr/bin/python3')
    record = JobRecord(job_id=remote_id, task=task, preset='fast',
        dataset_path=str(output / 'remote_snapshot' / 'data'), output_dir=str(output),
        status=status, phase=status, remote_profile_id=profile.id, remote_profile=profile,
        launch_spec={'operation': 'train', **({'local_model_id': native_id} if native else {}),
                     'dataset_binding': {'dataset_version_id': 'controlled-snapshot'}},
        source_dataset_path=str(tmp_path / 'original'), dataset_fingerprint='v1:controlled',
        current_epoch=3, total_epochs=4, current_step=8, total_steps=10,
        train_loss=.2, val_loss=.3, best_metric=.3,
        metrics={'controlled_metric': .3}, loss_history=[{'epoch': 3, 'train_loss': .2, 'val_loss': .3}],
        dataset_binding={'dataset_version_id': 'controlled-snapshot'})
    coordinator._save_journal({'protocol_version': 1, 'job_id': remote_id,
        'operation': 'train', 'state': status, 'worker_terminal_state': status,
        'worker_exit_confirmed': True, 'task': task, 'preset': 'fast',
        'output_dir': str(output), 'dataset_path': record.dataset_path,
        'profile': profile.model_dump(), 'launch_spec': record.launch_spec,
        'source_dataset_path': record.source_dataset_path,
        'dataset_fingerprint': record.dataset_fingerprint})
    _write_job_receipt(record)
    return record, output


def restore_without_authority(record, output, monkeypatch):
    from backend.remote.ssh_transport import SSHTransport
    monkeypatch.setattr(SSHTransport, 'exec', lambda *_a, **_k: pytest.fail('Terminal readback must not connect'))
    monkeypatch.setattr(coordinator, 'make_remote_runner', lambda *_a, **_k: pytest.fail('Terminal readback must not create a runner'))
    retained = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    restored = []
    manager = SimpleNamespace(get_job=lambda _: None, restore_terminal_job=restored.append,
        start_remote_job=lambda **_: pytest.fail('Terminal readback must not launch'))
    coordinator.recover_remote_jobs(manager)
    assert len(restored) == 1
    assert restored[0].job_id == record.job_id and restored[0].status == record.status
    assert restored[0].thread is None and restored[0].remote_runner is None
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in retained} == retained
    return restored[0]


@pytest.mark.parametrize('task', ['rotation', 'ocr', 'rotated_detection', 'enhancement', 'defect_gan'])
@pytest.mark.parametrize('status', ['completed', 'failed', 'aborted'])
def test_exact_ended_native_remote_pair_restores_metrics_without_rewriting_or_launch(tmp_path, monkeypatch, task, status):
    record, output = terminal_run(tmp_path, monkeypatch, task, status)
    restored = restore_without_authority(record, output, monkeypatch)
    for name in ('current_epoch', 'total_epochs', 'current_step', 'total_steps', 'train_loss',
                 'val_loss', 'best_metric', 'metrics', 'loss_history'):
        assert getattr(restored, name) == getattr(record, name), name


@pytest.mark.parametrize('damage', ['native_id', 'remote_id', 'task', 'status', 'output', 'dataset',
    'profile', 'source', 'fingerprint', 'provenance', 'missing_remote_id', 'missing_launch_id',
    'bad_launch_id', 'non_specialist', 'output_name', 'non_object', 'non_object_launch'])
def test_foreign_or_ambiguous_pair_never_contributes_metrics(tmp_path, monkeypatch, damage):
    record, output = terminal_run(tmp_path, monkeypatch)
    path = output / 'job_receipt.json'
    receipt = json.loads(path.read_text())
    if damage == 'missing_remote_id': receipt.pop('remote_job_id')
    elif damage == 'non_object': receipt = []
    elif damage in ('missing_launch_id', 'bad_launch_id', 'non_specialist', 'output_name', 'non_object_launch'):
        journal_path = output / 'remote_job.json'; journal = json.loads(journal_path.read_text())
        if damage == 'missing_launch_id': journal['launch_spec'].pop('local_model_id')
        elif damage == 'bad_launch_id': journal['launch_spec']['local_model_id'] = 'b' * 32
        elif damage == 'non_object_launch': journal['launch_spec'] = ['invalid launch identity']
        elif damage == 'non_specialist': journal['task'] = record.task = 'classification'; receipt['task'] = 'classification'
        else:
            relocated = output.with_name('foreign-output'); output.rename(relocated); output = relocated
            journal['output_dir'] = record.output_dir = str(output); receipt['output_dir'] = str(output)
            path = output / 'job_receipt.json'
        if damage == 'output_name':
            # Deliberately corrupt both retained copies. The production saver
            # correctly refuses this changed output identity before readback.
            (output / 'remote_job.json').write_text(json.dumps(journal))
            (coordinator._journal_index() / (record.job_id + '.json')).write_text(json.dumps(journal))
        else:
            coordinator._save_journal(journal)
    else:
        field, value = {'native_id': ('job_id', 'b' * 32), 'remote_id': ('remote_job_id', 'job_' + 'b' * 32),
            'task': ('task', 'ocr'), 'status': ('status', 'failed'), 'output': ('output_dir', str(tmp_path / 'other')),
            'dataset': ('dataset_path', str(tmp_path / 'other-data')), 'profile': ('compute_profile_id', 'other-worker'),
            'source': ('source_dataset_path', str(tmp_path / 'other-source')), 'fingerprint': ('dataset_fingerprint', 'v1:other'),
            'provenance': ('training_provenance', {'dataset_version_id': 'foreign-snapshot'})}[damage]
        receipt[field] = value
    path.write_text(json.dumps(receipt))
    restored = restore_without_authority(record, output, monkeypatch)
    assert restored.current_epoch == 0 and restored.train_loss is None
    assert restored.best_metric is None and restored.metrics == {} and restored.loss_history == []


@pytest.mark.parametrize('status', ['completed', 'failed', 'aborted'])
def test_original_core_identity_still_restores_its_saved_terminal_metrics(tmp_path, monkeypatch, status):
    record, output = terminal_run(tmp_path, monkeypatch, 'classification', status, native=False)
    restored = restore_without_authority(record, output, monkeypatch)
    assert restored.current_epoch == 3 and restored.train_loss == .2
    assert restored.loss_history == record.loss_history


def test_core_receipt_cannot_borrow_an_unexpected_remote_alias(tmp_path, monkeypatch):
    record, output = terminal_run(tmp_path, monkeypatch, 'classification', native=False)
    path = output / 'job_receipt.json'; receipt = json.loads(path.read_text())
    receipt['remote_job_id'] = record.job_id
    path.write_text(json.dumps(receipt))
    restored = restore_without_authority(record, output, monkeypatch)
    assert restored.current_epoch == 0 and restored.loss_history == []


@pytest.mark.parametrize('malformed', [[], None, 'not a journal', 7, True])
def test_malformed_unrelated_journal_cannot_hide_an_original_completed_job(tmp_path, monkeypatch, malformed):
    record, output = terminal_run(tmp_path, monkeypatch)
    path = coordinator._journal_index() / 'job_malformed.json'
    raw = json.dumps(malformed).encode(); path.write_bytes(raw)
    restored = restore_without_authority(record, output, monkeypatch)
    assert restored.current_epoch == 3 and restored.loss_history == record.loss_history
    assert path.read_bytes() == raw
