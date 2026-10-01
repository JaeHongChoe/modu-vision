"""Basic app training uses a job-owned CLI process and durable receipts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest
from PIL import Image

from backend.api import routes_training


@pytest.fixture
def local_data(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    source = tmp_path / 'source'
    for split in ('train', 'val'):
        for label in ('OK', 'NG'):
            directory = source / split / label
            directory.mkdir(parents=True)
            for index in range(2):
                Image.new('RGB', (32, 32), 'red' if label == 'NG' else 'blue').save(directory / f'{index}.png')
    return source, tmp_path / 'output'


def test_default_manager_runs_tiny_cpu_training_in_owned_cli_and_keeps_progress(local_data):
    source, output = local_data
    manager = routes_training.TrainingJobManager()
    record = manager.start_job('job_owned_local', 'classification', str(source), str(output), device='cpu',
                               config_overrides={'pretrained': False, 'backbone': 'resnet18', 'epochs': 1,
                                                 'image_size': 32, 'batch_size': 2, 'num_workers': 0})
    record.thread.join(40)
    assert record.status == 'completed', record.error
    journal = json.loads((output / 'local_job.json').read_text())
    assert journal['owner_pid'] != os.getpid()
    assert journal['status'] == 'completed' and journal['optimizer_resume'] is False
    assert record.current_epoch == 1 and len(record.loss_history) == 1
    receipt = json.loads((output / 'job_receipt.json').read_text())
    assert receipt['status'] == 'completed' and receipt['checkpoint_sha256']
    assert not manager._leases.list()


def test_local_cancel_intent_is_durable_and_escalation_reaps_only_owned_child(local_data, monkeypatch):
    from backend.engine import local_training_worker
    source, output = local_data
    monkeypatch.setattr(local_training_worker, 'CANCEL_GRACE_SECONDS', 0)
    monkeypatch.setattr(local_training_worker, 'CANCEL_TERMINATE_SECONDS', 0)
    manager = routes_training.TrainingJobManager()
    record = manager.start_job('job_local_cancel', 'classification', str(source), str(output), device='cpu',
                               config_overrides={'pretrained': False, 'backbone': 'resnet18', 'epochs': 1, 'num_workers': 0})
    deadline = time.monotonic() + 5
    while not (output / 'local_job.json').is_file() and time.monotonic() < deadline:
        time.sleep(.01)
    assert manager.abort_job(record.job_id)
    record.thread.join(15)
    assert record.status == 'aborted', record.error
    journal = json.loads((output / 'local_job.json').read_text())
    assert journal['cancel_requested_at'] > 0 and journal['worker_exit_confirmed'] is True
    assert record.process.poll() is not None
    assert not manager._leases.list()


def test_preparation_failure_never_launches_local_cli_or_keeps_reservation(local_data):
    source, output = local_data
    manager = routes_training.TrainingJobManager()
    def failed(cancel):
        raise ValueError('Prepared labels changed')
    record = manager.start_job('job_local_prepare_failed', 'classification', str(source), str(output),
                               device='cpu', prepare_dataset=failed, config_overrides={'pretrained': False})
    record.thread.join(5)
    assert record.status == 'failed'
    assert getattr(record, 'process', None) is None
    assert not (output / 'local_job.json').exists()
    assert not manager._leases.list()


def test_dead_local_worker_preserves_interrupted_state_and_releases_reservation(local_data):
    source, output = local_data
    manager = routes_training.TrainingJobManager()
    record = manager.start_job('job_local_interrupted', 'classification', str(source), str(output), device='cpu',
                               config_overrides={'pretrained': False, 'backbone': 'resnet18', 'epochs': 1, 'num_workers': 0})
    deadline = time.monotonic() + 5
    while not (output / 'local_job.json').is_file() and time.monotonic() < deadline:
        time.sleep(.01)
    record.process.kill()
    record.thread.join(10)
    assert record.status == 'interrupted'
    assert json.loads((output / 'job_receipt.json').read_text())['status'] == 'interrupted'
    assert not manager._leases.list()


def test_expired_uncertain_local_claim_requires_reconciliation_before_reuse(tmp_path):
    from backend.engine.shared_scheduler import ResourceLeases
    first = ResourceLeases(tmp_path / 'leases.sqlite3', owner='first', lease_seconds=-1)
    assert first.acquire('job_uncertain_local', 'local-compute', 'all')
    first.mark_uncertain('job_uncertain_local')
    second = ResourceLeases(first.path, owner='second')
    assert second.acquire('job_new', 'local-compute', 'all') is False
    assert first.list()[0]['job_id'] == 'job_uncertain_local'


@pytest.mark.skipif(os.name == 'nt', reason='Unix private session ownership')
def test_local_owned_session_survives_leader_exit_until_marked_child_is_reaped(tmp_path):
    import subprocess
    import sys
    import psutil
    from backend.engine import local_training_worker as worker
    from backend.engine.runtime_process_control import command_sha256
    marker = tmp_path / 'child.pid'
    handoff = tmp_path / 'exit'
    token = 'f' * 32
    code = ("import subprocess,sys,pathlib,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            f"pathlib.Path({str(marker)!r}).write_text(str(child.pid)); "
            f"path=pathlib.Path({str(handoff)!r}); "
            "exec('while not path.exists(): time.sleep(.01)')")
    command = [sys.executable, '-c', code]
    leader = subprocess.Popen(command, start_new_session=True, env=dict(os.environ, MODU_VISION_LOCAL_WORKER_TOKEN=token))
    journal = {'owner_pid': leader.pid, 'owner_created_at': psutil.Process(leader.pid).create_time(),
               'owner_command_sha256': command_sha256(command), 'owner_session': leader.pid, 'owner_token': token}
    child = None
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        child = psutil.Process(int(marker.read_text()))
        handoff.touch(); leader.wait(timeout=3)
        assert worker._liveness(journal) is True
        assert worker._stop_owned(journal, force=True) is True
        deadline = time.monotonic() + 3
        while worker._liveness(journal) is True and time.monotonic() < deadline:
            time.sleep(.01)
        assert worker._liveness(journal) is False
    finally:
        if leader.poll() is None:
            leader.kill(); leader.wait(timeout=3)
        if child and child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
            child.kill()


@pytest.mark.parametrize('cancel', [False, True])
def test_startup_restores_confirmed_dead_local_worker_as_interrupted(local_data, monkeypatch, cancel):
    import hashlib
    from backend.engine import local_training_worker as worker
    from backend.engine.shared_scheduler import ResourceLeases
    source, output = local_data
    output.mkdir()
    manager = routes_training.TrainingJobManager()
    leases = ResourceLeases(manager._leases.path, owner='closed-app', lease_seconds=-1)
    job_id = 'job_recover_dead_local'
    assert leases.acquire(job_id, 'local-compute', 'all')
    leases.mark_uncertain(job_id)
    spec = {'protocol_version': 1, 'job_id': job_id, 'task': 'classification', 'preset': 'fast',
            'dataset_path': str(source), 'output_dir': str(output), 'lease_path': str(leases.path), 'lease_owner': leases.owner}
    spec_path = output / 'local_spec.json'
    spec_path.write_text(json.dumps(spec))
    worker._save({'protocol_version': 1, 'job_id': job_id, 'task': 'classification', 'preset': 'fast',
                  'status': 'running', 'output_dir': str(output), 'spec_path': str(spec_path),
                  'spec_sha256': hashlib.sha256(spec_path.read_bytes()).hexdigest(), 'owner_pid': 99999999,
                  'owner_created_at': 0., 'owner_command_sha256': 'absent', 'optimizer_resume': False})
    original_liveness = worker._liveness
    monkeypatch.setattr(worker, '_liveness', lambda journal: None)
    worker.recover_local_jobs(manager)
    uncertain = manager.get_job(job_id)
    assert uncertain.status == 'disconnected'
    assert manager._leases.list()[0]['uncertain'] == 1
    if cancel:
        assert manager.abort_job(job_id)
    monkeypatch.setattr(worker, '_liveness', original_liveness)
    record = manager.reconnect_local_job(job_id)
    record.thread.join(5)
    expected = 'aborted' if cancel else 'interrupted'
    assert record.status == expected
    assert record.result['optimizer_resume'] is False
    assert not manager._leases.list()
    assert json.loads((output / 'job_receipt.json').read_text())['status'] == expected


def test_startup_reconnects_exact_live_basic_worker_without_launching_again(local_data):
    from backend.engine import local_training_worker as worker
    source, output = local_data
    first = routes_training.TrainingJobManager()
    record = first.start_job('job_recover_live_local', 'classification', str(source), str(output), device='cpu',
                            config_overrides={'pretrained': False, 'backbone': 'resnet18', 'epochs': 1,
                                              'image_size': 32, 'batch_size': 2, 'num_workers': 0})
    deadline = time.monotonic() + 5
    while not (output / 'local_job.json').is_file() and time.monotonic() < deadline:
        time.sleep(.01)
    second = routes_training.TrainingJobManager()
    worker.recover_local_jobs(second)
    recovered = second.get_job(record.job_id)
    assert recovered is not None
    recovered.thread.join(40)
    record.thread.join(5)
    assert recovered.process.pid == record.process.pid
    assert recovered.status == 'completed', recovered.error
    assert recovered.result['optimizer_resume'] is False
    assert len(recovered.loss_history) == 1
    assert not second._leases.list()


def test_recovered_monitor_error_stops_owned_child_before_releasing_claim(local_data, monkeypatch):
    from backend.engine import local_training_worker as worker
    source, output = local_data
    first = routes_training.TrainingJobManager()
    record = first.start_job('job_recover_invalid_status', 'classification', str(source), str(output), device='cpu',
                            config_overrides={'pretrained': False, 'backbone': 'resnet18', 'epochs': 1,
                                              'image_size': 32, 'batch_size': 2, 'num_workers': 0})
    deadline = time.monotonic() + 5
    while not (output / 'local_job.json').is_file() and time.monotonic() < deadline:
        time.sleep(.01)
    observe = worker._monitor_owned_training
    def corrupt_recovered(row, *args):
        if row is record:
            return observe(row, *args)
        raise ValueError('Recovered progress file differs from immutable spec')
    monkeypatch.setattr(worker, '_monitor_owned_training', corrupt_recovered)
    second = routes_training.TrainingJobManager()
    try:
        worker.recover_local_jobs(second)
        restored = second.get_job(record.job_id)
        restored.thread.join(10)
        assert restored.status == 'failed', restored.error
        assert restored.process.poll() is not None
        assert not second._leases.list()
    finally:
        first.abort_job(record.job_id)
        record.thread.join(15)
