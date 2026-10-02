"""S1-04: cancel and failure states from evidence only, each with one exact next action.

Pure classification over recorded evidence (run journal fields, ledger cancel intent, reservation rows, exit codes,
OS error numbers and error text). No process is started or signalled.
"""
import errno

import pytest

from backend.engine.job_observation import cancellation_evidence, classify_observation


def test_each_cancellation_step_needs_its_own_evidence():
    assert cancellation_evidence({}).stage == 'none'
    requested = {'cancel_requested_at': 10.0}
    assert cancellation_evidence(requested).stage == 'requested'
    assert cancellation_evidence({**requested, 'cancel_acknowledged_at': 11.0}).stage == 'acknowledged'
    signalled = {**requested, 'cancel_signal_sent_at': 12.0, 'cancel_terminate_sent_at': 20.0}
    assert cancellation_evidence(signalled).signals == ('cooperative', 'terminate')
    exited = {**signalled, 'worker_exit_confirmed': True, 'worker_exit_code': -15}
    assert cancellation_evidence(exited, reservation_present=True).stage == 'exited'
    assert cancellation_evidence(exited, reservation_present=True).complete is False, 'the device is still reserved'
    released = cancellation_evidence(exited, reservation_present=False)
    assert (released.stage, released.complete, released.exit_code) == ('released', True, -15)
    assert cancellation_evidence(exited).complete is False, 'an unobservable reservation is never assumed released'


def test_a_ledger_intent_alone_counts_as_requested():
    evidence = cancellation_evidence({}, intent={'job_id': 'j', 'actor_id': 'a', 'reason': 'user', 'requested_ns': 5_000_000_000})
    assert evidence.stage == 'requested' and evidence.requested_at == 5.0


def test_a_lost_connection_never_reads_as_a_finished_cancellation():
    cancel = cancellation_evidence({'cancel_requested_at': 1.0, 'cancel_signal_sent_at': 2.0})
    state = classify_observation('disconnected', connection_lost=True, cancel=cancel)
    assert (state.state, state.cause) == ('disconnected', 'cancel_unconfirmed')
    assert '유지' in state.next_action and state.retryable is False
    lost = classify_observation('running', connection_lost=True)
    assert lost.cause == 'network_lost' and '다시 관찰' in lost.next_action


@pytest.mark.parametrize('evidence', [
    {'os_errno': errno.ENOSPC}, {'winerror': 112}, {'winerror': 39},
    {'error': "OSError: [Errno 28] No space left on device: 'best_model.pt'"},
    {'error': 'There is not enough space on the disk'},
])
def test_disk_full_is_named_with_its_next_action(evidence):
    state = classify_observation('failed', **evidence)
    assert state.cause == 'disk_full' and state.retryable and '여유 공간' in state.next_action


@pytest.mark.parametrize('evidence', [
    {'error': 'RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB'}, {'error': 'MemoryError'},
    {'os_errno': errno.ENOMEM}, {'winerror': 8}, {'winerror': 14}, {'exit_code': 0xC0000017}, {'exit_code': -1073741801},
    {'error': "DefaultCPUAllocator: can't allocate memory: you tried to allocate 123 bytes"},
])
def test_out_of_memory_is_named_with_its_next_action(evidence):
    state = classify_observation('failed', **evidence)
    assert state.cause == 'out_of_memory' and '배치 크기' in state.next_action


def test_a_kill_this_backend_sent_while_cancelling_is_a_cancellation_not_memory():
    cancel = cancellation_evidence({'cancel_requested_at': 1.0, 'cancel_kill_sent_at': 9.0, 'worker_exit_confirmed': True,
                                    'worker_exit_code': -9}, reservation_present=False)
    assert classify_observation('aborted', exit_code=-9, cancel=cancel).cause == 'cancelled'
    assert classify_observation('interrupted', exit_code=-9, cancel=cancel).cause != 'killed'
    assert classify_observation('interrupted', exit_code=137).cause == 'killed', 'an outside kill is reported as such'


def test_a_reused_process_number_is_uncertain_and_nothing_is_signalled():
    state = classify_observation('running', identity_mismatch=True)
    assert (state.state, state.cause, state.retryable) == ('uncertain', 'process_identity_changed', False)
    assert '아무 신호도 보내지 않았' in state.next_action


def test_an_unprovable_worker_state_is_not_called_a_reused_process_number():
    for text in ('Local training worker ownership remains uncertain', 'Owned session cleanup remains unprovable after leader exit'):
        state = classify_observation('failed', error=text)
        assert state.cause == 'worker_state_unknown' and '아무 신호도' not in state.next_action
    local = classify_observation('disconnected', remote=False)
    assert local.cause == 'worker_state_unknown', 'a local worker is not reached over a network'
    assert classify_observation('disconnected', remote=True).cause == 'network_lost'


def test_restart_runtime_limit_and_plain_failures_have_distinct_causes():
    assert classify_observation('interrupted', app_restarted=True).cause == 'app_restarted'
    assert classify_observation('interrupted').cause == 'worker_exited'
    budget = cancellation_evidence({'cancel_requested_at': 1.0})
    assert classify_observation('aborted', cancel=budget, cancel_reason='runtime budget exceeded').cause == 'time_limit'
    failed = classify_observation('failed', error='ValueError: unknown backbone')
    assert failed.cause == 'worker_failed' and failed.evidence['error'] == 'ValueError: unknown backbone'
    assert classify_observation('completed').next_action is None
    pending = classify_observation('running', cancel=budget)
    assert pending.cause == 'cancel_pending' and '기다리' in pending.next_action


# Remote: the worker's acknowledgement is recorded apart from the signals and the confirmed exit.
@pytest.fixture
def remote_run(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    from backend.remote import coordinator
    from backend.remote.profiles import ComputeProfile
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    monkeypatch.setattr(coordinator, 'POLL_INTERVAL_SECONDS', 0)
    monkeypatch.setattr(coordinator, 'CANCEL_GRACE_SECONDS', 3600)  # no escalation inside this test
    profile = ComputeProfile(id='test', name='Test', ssh_target='host', ssh_port=22,
                             remote_root=str(tmp_path / 'server'), runtime_kind='python', runtime_value='python3')
    output = tmp_path / 'output'
    output.mkdir()
    record = SimpleNamespace(job_id='job_owned', output_dir=str(output), preparation_cancel=threading.Event(),
                             phase='running', best_metric=None)
    coordinator._save_journal({'protocol_version': 1, 'job_id': record.job_id, 'operation': 'train', 'state': 'launched',
                               'remote_handle': '12345', 'profile': profile.model_dump(), 'dataset_path': str(tmp_path / 'data'),
                               'output_dir': str(output), 'task': 'classification'})
    return coordinator, record, profile


class StoppingWorker:
    """A remote worker that has seen the cancel file: it reports 'stopping', then its process exits."""
    def __init__(self, acknowledged_at):
        self.acknowledged_at, self.reads, self.running, self.signals = acknowledged_at, 0, True, []

    def exec(self, profile, argv, **kwargs):
        import json
        import subprocess
        if argv[-1].endswith('terminal_status.json'):
            return subprocess.CompletedProcess(argv, 1, '', '')
        self.reads += 1
        if self.reads > 2:
            self.running = False
        payload = {'protocol_version': 1, 'job_id': 'job_owned', 'operation': 'train', 'status': 'stopping'}
        if self.acknowledged_at is not None:
            payload['cancel_acknowledged_at'] = self.acknowledged_at
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), '')

    def is_running(self, profile, run_id, handle):
        return self.running

    def touch_cancel(self, profile, run_id):
        import subprocess
        return subprocess.CompletedProcess([], 0, '', '')

    def stop_owned(self, profile, run_id, handle, *, force=False):
        self.signals.append(force)
        return True


@pytest.mark.parametrize('acknowledged_at', [123.0, None])
def test_a_remote_worker_acknowledgement_is_recorded_before_its_exit_is_confirmed(remote_run, acknowledged_at):
    import json
    from pathlib import Path
    coordinator, record, profile = remote_run
    coordinator.request_remote_cancellation(record)
    worker = StoppingWorker(acknowledged_at)
    result = coordinator.run_remote_training(record, profile, transport=worker, resume=True)
    assert result['status'] == 'aborted' and worker.signals == [], 'the cooperative cancel was enough'
    journal = json.loads((Path(record.output_dir) / 'remote_job.json').read_text())
    if acknowledged_at is not None:
        assert journal['cancel_acknowledged_at'] == acknowledged_at
    else:
        assert journal['cancel_acknowledged_at'] > 0, 'an older worker that reports stopping has acknowledged'
    evidence = cancellation_evidence(journal, reservation_present=False)
    assert (evidence.stage, evidence.complete) == ('released', True)
    assert evidence.acknowledged_at == journal['cancel_acknowledged_at'] and evidence.signals == ('cooperative',)


# Local: a real owned worker acknowledges the cancel file before it exits (tiny CPU training, no GPU).
def test_a_local_worker_acknowledges_a_cancel_before_its_exit_is_confirmed(tmp_path, monkeypatch):
    import json
    import time
    from PIL import Image
    from backend.api import routes_training
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    source, output = tmp_path / 'source', tmp_path / 'output'
    for split in ('train', 'val'):
        for label in ('OK', 'NG'):
            folder = source / split / label
            folder.mkdir(parents=True)
            for index in range(4):
                Image.new('RGB', (32, 32), 'red' if label == 'NG' else 'blue').save(folder / f'{index}.png')
    manager = routes_training.TrainingJobManager()
    record = manager.start_job('job_ack_local', 'classification', str(source), str(output), device='cpu',
                               config_overrides={'pretrained': False, 'backbone': 'resnet18', 'epochs': 1000, 'image_size': 32,
                                                 'batch_size': 2, 'num_workers': 0})
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = output / 'status.json'
        if status.is_file() and json.loads(status.read_text()).get('status') == 'running':
            break
        time.sleep(0.05)
    assert manager.abort_job(record.job_id)
    record.thread.join(60)
    journal = json.loads((output / 'local_job.json').read_text())
    assert record.status == 'aborted' and journal['worker_exit_confirmed'] is True
    assert journal['cancel_acknowledged_at'] >= journal['cancel_requested_at'], 'the worker saw the request, then exited'
    evidence = cancellation_evidence(journal, reservation_present=bool(manager._leases.list()))
    assert (evidence.stage, evidence.complete) == ('released', True)
    assert classify_observation('aborted', cancel=evidence).cause == 'cancelled'


def test_the_jobs_listing_carries_the_observation_and_its_next_action(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from backend.api import routes_training
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    cancelled = tmp_path / 'cancelled'
    cancelled.mkdir()
    (cancelled / 'local_job.json').write_text(json.dumps({'job_id': 'j1', 'cancel_requested_at': 1.0, 'cancel_acknowledged_at': 1.2,
                                                          'worker_exit_confirmed': True, 'worker_exit_code': 0}))
    done = routes_training._job_observation(SimpleNamespace(job_id='j1', status='aborted', output_dir=str(cancelled), error=None), set())
    assert (done['cause'], done['cancel']['stage'], done['cancel']['complete']) == ('cancelled', 'released', True)
    held = routes_training._job_observation(SimpleNamespace(job_id='j1', status='aborted', output_dir=str(cancelled), error=None), {'j1'})
    assert held['cancel']['stage'] == 'exited' and held['cancel']['complete'] is False, 'the device is still reserved'
    lost = routes_training._job_observation(SimpleNamespace(job_id='j2', status='disconnected', output_dir=str(tmp_path / 'none'),
                                                            error=None, remote_profile_id='server'), None)
    assert lost['cause'] == 'network_lost' and lost['cancel']['reservation_released'] is None
    full = routes_training._job_observation(SimpleNamespace(job_id='j3', status='failed', output_dir=None,
                                                            error={'message': 'OSError: [Errno 28] No space left on device'}), set())
    assert full['cause'] == 'disk_full' and '여유 공간' in full['next_action']


def test_the_production_failure_payload_of_a_local_job_is_classified(tmp_path):
    from types import SimpleNamespace
    from backend.api import routes_training
    from backend.utils.error_catalog import classify_exception
    oom_error, full_error = RuntimeError('CUDA out of memory. Tried to allocate 2.00 GiB'), OSError(28, 'No space left on device')
    oom = classify_exception(oom_error, details=str(oom_error)).to_ws_payload()  # as TrainingJobManager._worker stores it
    full = classify_exception(full_error, details=str(full_error)).to_ws_payload()
    assert 'message' not in oom, 'the real payload has no message key'
    for payload, cause in ((oom, 'out_of_memory'), (full, 'disk_full')):
        record = SimpleNamespace(job_id='j', status='failed', output_dir=None, error=payload, remote_profile_id=None)
        assert routes_training._job_observation(record, set())['cause'] == cause, payload


def test_the_task_center_source_survives_an_unreadable_reservation_table(tmp_path, monkeypatch):
    import os
    import pytest as _pytest
    from fastapi.testclient import TestClient
    if os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0):
        _pytest.skip('needs POSIX permissions enforced for this user')
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    from backend.api import routes_training
    from backend.engine.shared_scheduler import shared_leases
    manager = routes_training.TrainingJobManager()
    monkeypatch.setattr(routes_training, 'training_job_manager', manager)
    monkeypatch.setattr(main, 'training_job_manager', manager)
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    client.post('/api/project/create', json={'name': 'Leases', 'task': 'classification'})
    lease_db = shared_leases().path
    lease_db.chmod(0)
    try:
        response = client.get('/api/training-workspace/tasks')
    finally:
        lease_db.chmod(0o644)
    assert response.status_code == 200, response.text
    assert response.json()['reservations'] is None and any(row['kind'] == 'reservations' for row in response.json()['errors'])
