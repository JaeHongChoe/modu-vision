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
                             remote_root='/srv/modu-vision-test/server', runtime_kind='python', runtime_value='python3')  # a server path, POSIX on every OS
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


def test_a_late_acknowledgement_never_reopens_a_finished_run(tmp_path):
    import json
    from backend.remote.worker import _StatusWriter
    for terminal in ('completed', 'failed', 'aborted'):
        run = tmp_path / terminal
        run.mkdir()
        writer = _StatusWriter(run, f'job-{terminal}')
        writer.update(status=terminal)
        result = writer.acknowledge_cancel(123.0)
        assert (result['status'], result['cancel_acknowledged_at']) == (terminal, 123.0)
        assert json.loads((run / 'status.json').read_text(encoding='utf-8'))['status'] == terminal
    running = tmp_path / 'running'
    running.mkdir()
    writer = _StatusWriter(running, 'job-running')
    writer.update(status='running')
    acknowledged = writer.acknowledge_cancel()
    assert acknowledged['status'] == 'stopping' and isinstance(acknowledged['cancel_acknowledged_at'], float)


def test_a_cancel_present_when_the_worker_starts_is_acknowledged_and_ends_aborted(tmp_path, monkeypatch):
    import json
    import signal
    from backend.engine import local_training_worker
    monkeypatch.setattr(signal, 'signal', lambda *args: None)  # the worker installs handlers meant for its own process
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    root = tmp_path / 'run'
    root.mkdir()
    spec = {'protocol_version': 1, 'output_dir': str(root), 'job_id': 'job_start', 'task': 'classification', 'preset': 'fast',
            'dataset_path': str(tmp_path / 'data'), 'lease_path': str(tmp_path / 'leases.sqlite3'), 'lease_owner': 'test'}
    (root / 'basic_spec.json').write_text(json.dumps(spec), encoding='utf-8')
    (root / 'local_cancel.json').write_text(json.dumps({'job_id': 'job_start'}), encoding='utf-8')
    result = local_training_worker.execute_basic(root / 'basic_spec.json')
    status = json.loads((root / 'status.json').read_text(encoding='utf-8'))
    assert result['status'] == status['status'] == 'aborted'
    assert isinstance(status['cancel_acknowledged_at'], float), 'a cancel seen before training starts is still acknowledged'


def test_an_uncertain_reservation_is_released_only_on_an_operators_recorded_confirmation(tmp_path, monkeypatch):
    import time
    from pathlib import Path
    from backend.api import routes_training
    from backend.engine import local_training_worker
    from backend.engine.job_store import ledger
    from backend.tests.test_service_s1_05 import _devices_app
    api, body = _devices_app(tmp_path, monkeypatch, lambda *args, **kwargs: {'status': 'aborted', 'worker_exit_confirmed': True})
    job_id = api.post('/api/training/start', json=body).json()['job_id']
    manager = routes_training.training_job_manager
    _wait_ended(manager, job_id)
    release = lambda **extra: api.post('/api/training/reservations/confirm-release',
                                      json={'job_id': job_id, 'confirm': True, 'reason': 'the PC was rebooted', **extra})
    assert api.post('/api/training/reservations/confirm-release', json={'job_id': job_id, 'reason': 'rebooted'}).status_code == 422
    assert release(reason='no').status_code == 422, 'a reason is required'
    assert release().status_code == 409, 'no reservation to release'
    manager._leases.acquire(job_id, 'local-compute', 'all')
    assert 'not uncertain' in release().text, 'a reservation that will be returned on exit evidence is left alone'
    manager._leases.mark_uncertain_local(job_id)
    refreshed = release()
    assert refreshed.status_code == 409 and 'refreshed this reservation' in refreshed.text, 'a heartbeat moments ago may be a live worker'
    _expire(manager, job_id)
    assert release(fence=7).status_code == 409, 'only the reservation the operator saw'
    output = Path(manager.get_job(job_id).output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'local_job.json').write_text('{"owner_pid": 1}', encoding='utf-8')
    monkeypatch.setattr(local_training_worker, '_liveness', lambda journal: True)
    assert 'still running' in release().text, 'a provably alive worker keeps its device'
    assert any(row['job_id'] == job_id for row in manager._leases.list()), 'a refused release removes nothing'
    monkeypatch.setattr(local_training_worker, '_liveness', lambda journal: None)
    released = release()
    assert released.status_code == 200 and released.json()['liveness'] == 'unknown', released.text
    assert released.json()['outcome_recorded'] is True
    assert all(row['job_id'] != job_id for row in manager._leases.list())
    confirmed = _release_events(ledger(), job_id, 'reservation_release_confirmed')
    assert len(confirmed) == 1 and confirmed[0]['reason'] == 'the PC was rebooted' and confirmed[0]['liveness'] == 'unknown'
    assert len(_release_events(ledger(), job_id, 'reservation_released')) == 1
    assert release().status_code == 409, 'a second confirmation finds nothing to release'
    assert len(_release_events(ledger(), job_id, 'reservation_release_confirmed')) == 1, 'a refused check records nothing'


def _release_events(store, job_id, name):
    return [event['payload'] for event in store.events(job_id) if event['event'] == name]


def _expire(manager, job_id):
    """The reservation's last refresh is older than its lease: no worker has kept it alive since."""
    import time
    with manager._leases.connect() as conn:
        conn.execute('UPDATE leases SET expires=? WHERE job_id=?', (time.time() - 1, job_id))


def _wait_ended(manager, job_id):
    """Wait for a launched job to end, then for its thread, which records the end in the job ledger after the in-memory
    status changes; a slow runner (hosted Windows) can take seconds to prepare even a stub launch."""
    import time
    deadline = time.monotonic() + 60
    while manager.get_job(job_id).status in ('queued', 'preparing', 'running') and time.monotonic() < deadline:
        time.sleep(0.02)
    job = manager.get_job(job_id)
    assert job.status not in ('queued', 'preparing', 'running'), (job.status, job.phase, job.error)
    if getattr(job, 'thread', None) is not None:
        job.thread.join(30)
        assert not job.thread.is_alive(), 'the job thread did not finish recording its end'


def _ended_job(tmp_path, monkeypatch, *, remote=False):
    """A finished local job (the launcher is a stub) holding an uncertain reservation no worker refreshes."""
    import time
    from backend.api import routes_training
    from backend.tests.test_service_s1_05 import _devices_app
    api, body = _devices_app(tmp_path, monkeypatch, lambda *args, **kwargs: {'status': 'aborted', 'worker_exit_confirmed': True})
    job_id = api.post('/api/training/start', json=body).json()['job_id']
    manager = routes_training.training_job_manager
    _wait_ended(manager, job_id)
    manager._leases.acquire(job_id, 'server-host' if remote else 'local-compute', 'all', remote=remote)
    manager._leases.mark_uncertain(job_id) if remote else manager._leases.mark_uncertain_local(job_id)
    _expire(manager, job_id)
    return api, manager, job_id


def test_the_ledger_never_records_a_release_that_did_not_happen(tmp_path, monkeypatch):
    import threading
    import time
    from fastapi import HTTPException
    from backend.api import routes_training
    from backend.engine.job_store import ledger
    api, manager, job_id = _ended_job(tmp_path, monkeypatch)
    body = {'job_id': job_id, 'confirm': True, 'reason': 'the PC was rebooted'}
    # The reservation changes between the checks and the removal (a reattach restamps it): refused, and said so.
    real_release = manager._leases.release_uncertain
    monkeypatch.setattr(manager._leases, 'release_uncertain', lambda *args: False)
    changed = api.post('/api/training/reservations/confirm-release', json=body)
    assert changed.status_code == 409 and 'changed while it was being released' in changed.text
    assert any(row['job_id'] == job_id for row in manager._leases.list())
    assert len(_release_events(ledger(), job_id, 'reservation_release_confirmed')) == 1
    refused = _release_events(ledger(), job_id, 'reservation_release_refused')
    assert len(refused) == 1 and 'changed' in refused[0]['reason']
    assert _release_events(ledger(), job_id, 'reservation_released') == []
    # Two confirmations at once: one releases; the other is answered after it and records nothing.
    def slow_release(*args):
        time.sleep(0.2)
        return real_release(*args)
    monkeypatch.setattr(manager._leases, 'release_uncertain', slow_release)
    outcomes = []
    def confirm():
        try:
            outcomes.append(routes_training.confirm_reservation_release(routes_training.ReservationReleaseRequest(**body)))
        except HTTPException as exc:
            outcomes.append(exc.status_code)
    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(type(outcome).__name__ for outcome in outcomes) == ['dict', 'int'] and 409 in outcomes, outcomes
    assert len(_release_events(ledger(), job_id, 'reservation_released')) == 1
    assert len(_release_events(ledger(), job_id, 'reservation_release_confirmed')) == 2, 'the refused change above and this release'


def test_a_reservation_of_a_job_known_only_to_the_ledger_can_be_released_in_its_own_project(tmp_path, monkeypatch):
    from backend.engine.job_store import ledger
    api, manager, job_id = _ended_job(tmp_path, monkeypatch)
    assert ledger().record(job_id)['state'] == 'aborted'
    manager._jobs.pop(job_id)  # after a restart whose run journal could not be recovered, only the ledger knows the job
    body = {'job_id': job_id, 'confirm': True, 'reason': 'the run folder was lost'}
    released = api.post('/api/training/reservations/confirm-release', json=body)
    assert released.status_code == 200 and released.json()['liveness'] == 'unknown', released.text
    assert all(row['job_id'] != job_id for row in manager._leases.list())
    manager._leases.acquire(job_id, 'local-compute', 'all')
    manager._leases.mark_uncertain_local(job_id)
    _expire(manager, job_id)
    assert api.post('/api/project/create', json={'name': 'Other'}).status_code == 200
    assert api.post('/api/training/reservations/confirm-release', json=body).status_code == 404, 'another project cannot release it'
    assert any(row['job_id'] == job_id for row in manager._leases.list())


@pytest.mark.parametrize('journal_copy', ['run folder', 'user data folder'])
def test_a_real_worker_journal_keeps_the_reservation_until_its_process_has_exited(tmp_path, monkeypatch, journal_copy):
    import json
    import os
    import subprocess
    import sys
    import time
    import uuid
    from pathlib import Path
    import psutil
    from backend.engine import local_training_worker
    api, manager, job_id = _ended_job(tmp_path, monkeypatch)
    token = uuid.uuid4().hex
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True,
                             env=dict(os.environ, MODU_VISION_LOCAL_WORKER_TOKEN=token))
    try:
        process = psutil.Process(child.pid)
        deadline = time.monotonic() + 30
        while process.environ().get('MODU_VISION_LOCAL_WORKER_TOKEN') != token:  # the child has executed with its token
            assert time.monotonic() < deadline
            time.sleep(0.05)
        journal = {'job_id': job_id, 'owner_pid': child.pid, 'owner_created_at': process.create_time(), 'owner_token': token,
                   'owner_session': child.pid, 'owner_username': process.username(),
                   'owner_boot_id': local_training_worker._boot_id()}
        # The run folder copy, or only the copy kept in the user data folder (the run folder lost its journal).
        folder = Path(manager.get_job(job_id).output_dir) if journal_copy == 'run folder' else local_training_worker._index()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ('local_job.json' if journal_copy == 'run folder' else f'{job_id}.json')).write_text(json.dumps(journal), encoding='utf-8')
        body = {'job_id': job_id, 'confirm': True, 'reason': 'checked the device'}
        alive = api.post('/api/training/reservations/confirm-release', json=body)
        assert alive.status_code == 409 and 'still running' in alive.text, alive.text
    finally:
        child.kill()  # only the process this test started
        child.wait(30)
    # Proof that the worker exited outweighs a reservation expiry ahead of the clock (a stepped clock, a skewed store).
    with manager._leases.connect() as conn:
        conn.execute('UPDATE leases SET expires=? WHERE job_id=?', (time.time() + 600, job_id))
    released = api.post('/api/training/reservations/confirm-release', json=body)
    assert released.status_code == 200 and released.json()['liveness'] == 'gone', released.text


def test_a_recent_refresh_is_named_with_its_age_and_the_lock_table_empties(tmp_path, monkeypatch):
    import time
    from backend.api import routes_training
    api, manager, job_id = _ended_job(tmp_path, monkeypatch)
    with manager._leases.connect() as conn:  # refreshed 7 s ago under the 30 s lease
        conn.execute('UPDATE leases SET expires=? WHERE job_id=?', (time.time() + manager._leases.lease_seconds - 7, job_id))
    refused = api.post('/api/training/reservations/confirm-release', json={'job_id': job_id, 'confirm': True, 'reason': 'checked'})
    assert refused.status_code == 409 and ('refreshed this reservation 7 s ago' in refused.text
                                          or 'refreshed this reservation 8 s ago' in refused.text), refused.text
    assert routes_training._RESERVATION_RELEASE_LOCKS == {}, 'only jobs with a confirmation in progress hold a lock'


def test_a_ledger_read_back_keeps_its_compute_profile(tmp_path, monkeypatch):
    import json
    from backend.engine.job_store import ledger
    api, manager, job_id = _ended_job(tmp_path, monkeypatch)
    manager._jobs.pop(job_id)
    store = ledger()
    with store._connect() as db:  # the stored submission named a server profile
        spec = json.loads(db.execute('SELECT spec_json FROM jobs WHERE id=?', (job_id,)).fetchone()[0])
        db.execute('UPDATE jobs SET spec_json=? WHERE id=?', (json.dumps({**spec, 'compute_profile_id': 'server-a'}), job_id))
    rows = [row for row in api.get('/api/training/jobs').json()['jobs'] if row['job_id'] == job_id]
    assert rows and rows[0]['compute_profile_id'] == 'server-a', rows


def test_releasing_a_reservation_needs_training_rights_in_the_jobs_own_team_project(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from PIL import Image
    from backend.contracts.authentication import permission_action
    assert permission_action('/api/training/reservations/confirm-release', 'POST') == 'training.execute'
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    from backend.api import routes_training
    from backend.contracts import capabilities
    from backend.engine import local_training_worker
    manager = routes_training.TrainingJobManager()
    monkeypatch.setattr(routes_training, 'training_job_manager', manager)
    monkeypatch.setattr(main, 'training_job_manager', manager)
    monkeypatch.setattr(capabilities, 'local_device_kinds', lambda *args, **kwargs: ['cpu'])
    monkeypatch.setattr(local_training_worker, 'run_owned_training', lambda *args, **kwargs: {'status': 'aborted', 'worker_exit_confirmed': True})
    app = main.create_app(str(tmp_path / 'shared-registry'), shared_auth_dir=str(tmp_path / 'accounts'))
    store = app.state.accounts
    admin = store.bootstrap('owner', 'long password 123')
    viewer_account = store.create_user('viewer', 'long password 456')
    client = lambda name, password: TestClient(app, headers={'Authorization': 'Bearer ' + store.login(name, password)['token']})
    owner = client('owner', 'long password 123')
    project = owner.post('/api/project/create', json={'name': 'Reserved', 'task': 'classification'}).json()
    source = tmp_path / 'team-source'
    for split in ('train', 'val'):
        for label in ('ok', 'ng'):
            (source / split / label).mkdir(parents=True)
            for index in range(2):
                Image.new('RGB', (32, 32), (200 if label == 'ok' else 40, 0, index)).save(source / split / label / f'{index}.png')
    assert owner.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    started = owner.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source), 'preset': 'fast'})
    assert started.status_code == 200, started.text
    job_id = started.json()['job_id']
    _wait_ended(manager, job_id)
    manager._leases.acquire(job_id, 'local-compute', 'all')
    manager._leases.mark_uncertain_local(job_id)
    _expire(manager, job_id)
    body = {'job_id': job_id, 'confirm': True, 'reason': 'checked the device'}
    store.set_membership(project['id'], viewer_account['id'], 'viewer', admin['id'])
    store.select_project(viewer_account['id'], project['id'])
    assert client('viewer', 'long password 456').post('/api/training/reservations/confirm-release', json=body).status_code == 403
    owner.post('/api/project/create', json={'name': 'Elsewhere', 'task': 'classification'})
    assert owner.post('/api/training/reservations/confirm-release', json=body).status_code == 404, 'not from another project'
    store.select_project(admin['id'], project['id'])
    released = owner.post('/api/training/reservations/confirm-release', json=body)
    assert released.status_code == 200 and released.json()['recorded_by'] != 'local', released.text


def test_an_operators_release_keeps_the_jobs_own_end_reason(tmp_path, monkeypatch):
    from backend.api import routes_training
    from backend.engine.job_store import ledger
    api, manager, job_id = _ended_job(tmp_path, monkeypatch)
    manager._jobs.pop(job_id)
    before = routes_training._ledger_end_message(ledger(), ledger().record(job_id))
    body = {'job_id': job_id, 'confirm': True, 'reason': 'checked the machine'}
    assert api.post('/api/training/reservations/confirm-release', json=body).status_code == 200
    after = routes_training._ledger_end_message(ledger(), ledger().record(job_id))
    assert after == before and 'checked the machine' not in after, (before, after)


def test_a_release_outcome_or_reservation_store_failure_is_answered_truthfully(tmp_path, monkeypatch):
    import sqlite3
    from backend.engine import job_store
    from backend.engine.job_store import ledger
    api, manager, job_id = _ended_job(tmp_path, monkeypatch)
    body = {'job_id': job_id, 'confirm': True, 'reason': 'checked the machine'}
    real_release = manager._leases.release_uncertain
    def broken(*args):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(manager._leases, 'release_uncertain', broken)
    failed = api.post('/api/training/reservations/confirm-release', json=body)
    assert failed.status_code == 503 and 'nothing was released' in failed.text, failed.text
    refused = _release_events(ledger(), job_id, 'reservation_release_refused')
    assert len(refused) == 1 and 'could not be written' in refused[0]['reason']
    assert any(row['job_id'] == job_id for row in manager._leases.list())
    monkeypatch.setattr(manager._leases, 'release_uncertain', real_release)
    real_record = job_store.JobStore.record_event
    def outcome_fails(self, job, event, payload=None):
        if event == 'reservation_released':
            raise sqlite3.OperationalError('disk I/O error')
        return real_record(self, job, event, payload)
    monkeypatch.setattr(job_store.JobStore, 'record_event', outcome_fails)
    released = api.post('/api/training/reservations/confirm-release', json=body)
    assert released.status_code == 200 and released.json()['outcome_recorded'] is False, released.text
    assert all(row['job_id'] != job_id for row in manager._leases.list())
    assert _release_events(ledger(), job_id, 'reservation_released') == [], 'the confirmation alone never claims a release'


def test_a_ledger_only_server_job_is_released_without_a_local_process_check(tmp_path, monkeypatch):
    from backend.engine import local_training_worker
    api, manager, job_id = _ended_job(tmp_path, monkeypatch, remote=True)
    manager._jobs.pop(job_id)
    monkeypatch.setattr(local_training_worker, '_liveness', lambda journal: pytest.fail('a server job is not probed locally'))
    released = api.post('/api/training/reservations/confirm-release', json={'job_id': job_id, 'confirm': True, 'reason': 'server checked'})
    assert released.status_code == 200 and released.json()['liveness'] == 'not_checked', released.text


def test_the_observation_says_whether_any_worker_journal_was_recorded(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from backend.api import routes_training
    from backend.engine import local_training_worker
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    def observed(job_id, folder):
        record = SimpleNamespace(job_id=job_id, status='failed', output_dir=str(folder), error=None, remote_profile_id=None)
        return routes_training._job_observation(record, set())['worker_recorded']
    never = tmp_path / 'never'
    never.mkdir()
    assert observed('job_never', never) is False, 'failed while preparing: no worker was ever recorded'
    copy = tmp_path / 'copy'
    copy.mkdir()
    local_training_worker._index().mkdir(parents=True)
    (local_training_worker._index() / 'job_copy.json').write_text(json.dumps({'job_id': 'job_copy', 'worker_exit_confirmed': True}))
    assert observed('job_copy', copy) is True, 'the copy in the user data folder records the worker'
    damaged = tmp_path / 'damaged'
    damaged.mkdir()
    (damaged / 'local_job.json').write_text('{broken')
    assert observed('job_damaged', damaged) is None, 'an unreadable journal leaves it unknown'


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
