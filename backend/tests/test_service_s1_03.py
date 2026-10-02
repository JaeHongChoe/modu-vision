"""S1-03: scheduler and job ownership - queue, priority, fairness, quota, device reservations, fencing, expiry.

CPU only, tmp folders, fake device inventory. No process is ever signalled by the scheduler. Stores are isolated
with VISION_AI_STUDIO_USER_DATA_DIR / explicit paths; HOME is never changed.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.contracts.context import ProjectContext


def _context(project='project-a', actor='actor-a'):
    return ProjectContext(workspace_id='ws-1', project_id=project, actor_id=actor, mode='local')


@pytest.fixture
def stores(tmp_path):
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import ResourceLeases
    return JobStore(tmp_path / 'ledger.sqlite3'), ResourceLeases(tmp_path / 'resource_leases.sqlite3', owner='new-app')


def _queued(store, scheduler, job_id, project='project-a', priority=0, resources=None):
    ref = store.submit(_context(project), f'ns:{project}', 'training', {'job': job_id}, job_id, job_id=job_id)
    scheduler.enqueue(ref.id, ref.revision, priority=priority, resources=resources)
    return ref.id


# 1. Production route: a second local job waits in the ledger queue instead of being refused.
@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    from backend.api import routes_training
    from backend.engine import local_training_worker
    import backend.main as main
    manager = routes_training.TrainingJobManager()
    monkeypatch.setattr(routes_training, 'training_job_manager', manager)
    monkeypatch.setattr(main, 'training_job_manager', manager)
    release, calls = threading.Event(), []

    def fake_owned_training(record, callback, **_kwargs):
        calls.append(record.job_id)
        release.wait(30)
        return {'status': 'completed'}

    monkeypatch.setattr(local_training_worker, 'run_owned_training', fake_owned_training)
    source = tmp_path / 'source'
    for split in ('train', 'val'):
        for label, shade in (('ok', 200), ('ng', 40)):
            folder = source / split / label
            folder.mkdir(parents=True)
            for index in range(2):
                Image.new('RGB', (32, 32), (shade + index, shade, shade)).save(folder / f'{label}-{index}.png')
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    yield {'app': app, 'manager': manager, 'calls': calls, 'release': release, 'source': source}
    release.set()
    for record in manager.list_jobs():
        if record.thread is not None:
            record.thread.join(30)


def test_a_second_local_job_waits_in_the_ledger_queue_with_a_reason(harness):
    from backend.api import routes_training
    api = TestClient(harness['app'], headers={'X-Vision-Token': harness['app'].state.api_token})
    api.post('/api/project/create', json={'name': 'Queue'})
    assert api.put('/api/project/update', json={'source_dataset_dir': str(harness['source'])}).status_code == 200
    body = {'task': 'classification', 'dataset_path': str(harness['source']), 'preset': 'fast'}
    first = api.post('/api/training/start', json=body, headers={'Idempotency-Key': 'q-1'})
    second = api.post('/api/training/start', json={**body, 'preset': 'precision'}, headers={'Idempotency-Key': 'q-2'})
    assert first.status_code == 200, first.text
    assert second.status_code == 200 and second.json()['status'] == 'queued', second.text
    rows = {row['job_id']: row for row in api.get('/api/training/jobs').json()['jobs']}
    waiting = rows[second.json()['job_id']]
    assert waiting['status'] == 'queued' and waiting['wait_reason'] == 'device_reserved', waiting
    store = routes_training.job_ledger()
    assert store.get(second.json()['job_id']).state == 'queued' and store.attempts(second.json()['job_id']) == []
    harness['release'].set()  # the first ends; the scheduler claims the second under its own attempt
    for _ in range(200):
        if store.attempts(second.json()['job_id']):
            break
        time.sleep(0.05)
    assert [len(store.attempts(job['job_id'])) for job in (first.json(), second.json())] == [1, 1]


# 2-5. Claim contract: one attempt per job, deterministic order, quota, device reservations.
def test_two_workers_claiming_at_once_get_one_attempt_per_job(stores, tmp_path):
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import ResourceLeases
    store, leases = stores
    jobs = [_queued(store, JobScheduler(store, leases), f'job-{index}') for index in range(6)]
    claimed, lock = [], threading.Lock()

    def worker(name):
        # Separate connections, as two backend processes would have.
        scheduler = JobScheduler(JobStore(tmp_path / 'ledger.sqlite3'),
                                 ResourceLeases(tmp_path / 'resource_leases.sqlite3', owner=name))
        while (lease := scheduler.claim_job(name, {'hosts': []})) is not None:
            with lock:
                claimed.append((lease.job_id, lease.fence))

    threads = [threading.Thread(target=worker, args=(f'worker-{n}',)) for n in range(2)]
    [thread.start() for thread in threads]
    [thread.join(30) for thread in threads]
    assert sorted(job for job, _ in claimed) == sorted(jobs), 'every job claimed exactly once'
    assert all(store.attempts(job)[-1]['fencing_token'] == fence for job, fence in claimed), 'each lease holds its job fence'
    assert all(len(store.attempts(job)) == 1 for job in jobs)


def test_claims_follow_priority_then_project_fairness_then_age(stores):
    from backend.engine.job_scheduler import JobScheduler
    store, leases = stores
    scheduler = JobScheduler(store, leases)
    a1 = _queued(store, scheduler, 'a1', 'project-a')
    a2 = _queued(store, scheduler, 'a2', 'project-a')
    b1 = _queued(store, scheduler, 'b1', 'project-b')
    c1 = _queued(store, scheduler, 'c1', 'project-c', priority=5)
    order = [scheduler.claim_job('worker-1', {'hosts': []}).job_id for _ in range(4)]
    assert order == [c1, a1, b1, a2], 'priority first; then the project with fewer running attempts; then age'


def test_a_project_quota_keeps_the_second_job_waiting_with_its_reason(stores):
    from backend.engine.job_scheduler import JobScheduler
    store, leases = stores
    scheduler = JobScheduler(store, leases)
    scheduler.set_quota('ns:project-a', max_running=1)
    a1, a2, b1 = (_queued(store, scheduler, job, project) for job, project in
                  (('a1', 'project-a'), ('a2', 'project-a'), ('b1', 'project-b')))
    assert [scheduler.claim_job('worker-1', {'hosts': []}).job_id for _ in range(2)] == [a1, b1]
    assert scheduler.claim_job('worker-1', {'hosts': []}) is None
    view = {row['job_id']: row for row in scheduler.queue_view('ns:project-a')}
    assert view[a2]['wait_reason'] == 'project_quota' and store.get(a2).state == 'queued'


def test_device_reservations_respect_mig_children_and_the_whole_gpu(stores):
    from backend.engine.job_scheduler import JobScheduler
    store, leases = stores
    leases.configure_devices('local-compute', [
        {'selector': '0', 'uuid': 'GPU-a', 'memory_mb': 40000},
        {'selector': 'MIG-a1', 'uuid': 'MIG-a1', 'parent_uuid': 'GPU-a', 'memory_mb': 20000},
        {'selector': 'MIG-a2', 'uuid': 'MIG-a2', 'parent_uuid': 'GPU-a', 'memory_mb': 20000},
    ])
    scheduler = JobScheduler(store, leases)
    mig1 = _queued(store, scheduler, 'mig1', resources={'host': 'local-compute', 'selector': 'MIG-a1'})
    mig2 = _queued(store, scheduler, 'mig2', resources={'host': 'local-compute', 'selector': 'MIG-a2'})
    whole = _queued(store, scheduler, 'whole', resources={'host': 'local-compute', 'selector': '0'})
    capable = {'hosts': ['local-compute']}
    assert {scheduler.claim_job('w', capable).job_id for _ in range(2)} == {mig1, mig2}
    assert scheduler.claim_job('w', capable) is None
    view = {row['job_id']: row for row in scheduler.queue_view('ns:project-a')}
    assert view[whole]['wait_reason'] == 'device_reserved'
    assert {row['job_id'] for row in leases.list()} == {mig1, mig2}
    assert all(row['fence'] for row in leases.list()), 'each reservation names the attempt fence that holds it'


# 6. Ownership: an expired lease without exit evidence stays reserved; heartbeat and publish are fenced.
def test_an_expired_lease_without_exit_evidence_stays_reserved_and_is_not_requeued(tmp_path):
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.job_store import JobStore, StaleFencingToken
    from backend.engine.shared_scheduler import ResourceLeases
    store = JobStore(tmp_path / 'ledger.sqlite3')
    leases = ResourceLeases(tmp_path / 'resource_leases.sqlite3', owner='new-app', lease_seconds=0.05)
    scheduler = JobScheduler(store, leases)
    held = _queued(store, scheduler, 'held', resources={'host': 'local-compute', 'selector': 'all'})
    other = _queued(store, scheduler, 'other', resources={'host': 'local-compute', 'selector': 'all'})
    lease = scheduler.claim_job('worker-1', {'hosts': ['local-compute']})
    assert lease.job_id == held
    time.sleep(0.15)  # no heartbeat
    scheduler.expire_leases(observe=lambda _lease: 'unknown')
    assert store.get(held).state == 'disconnected', 'not requeued, not interrupted'
    assert [row['job_id'] for row in leases.list()] == [held] and leases.list()[0]['uncertain'] == 1
    assert scheduler.claim_job('worker-2', {'hosts': ['local-compute']}) is None
    assert {row['job_id']: row['wait_reason'] for row in scheduler.queue_view('ns:project-a')}[other] == 'uncertain_reservation'
    scheduler.expire_leases(observe=lambda _lease: 'exited')  # exit evidence releases the device
    assert store.get(held).state == 'interrupted'
    with pytest.raises(StaleFencingToken):
        scheduler.heartbeat(lease)
    assert scheduler.claim_job('worker-2', {'hosts': ['local-compute']}).job_id == other


def test_reattach_rotates_the_fence_so_the_old_backend_cannot_publish(tmp_path, monkeypatch):
    """Current production path (S1-02 open P3): reconcile keeps the old token, so the old backend can still end the job."""
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    from backend.api import routes_training
    store = routes_training.job_ledger()
    job_id = store.submit(_context(), 'ns:project-a', 'training', {'job': 1}, 'k', job_id='job_fence').id
    old = routes_training.TrainingLedgerLink(store, job_id)
    old.launched('local')
    first_fence = old.fencing_token
    old.detached('normal shutdown')
    record = routes_training.JobRecord(job_id, 'classification', 'fast', '', '', 'running')

    class Restarted:
        def list_jobs(self):
            return [record]

        def abort_job(self, _job_id):
            raise AssertionError('no cancel intent was recorded')

    routes_training.reconcile_job_ledger(Restarted())  # the restarted backend reattaches the live worker
    assert store.attempts(job_id)[-1]['fencing_token'] != first_fence, 'reattach issues a new fence'
    old.finished('completed')  # the old backend is still alive and tries to end the job
    assert store.get(job_id).state == 'running', 'only the reattached owner may end the job'
    assert store.artifacts(job_id) == []


# 7. Upgrade with an older app still live: shared reservation DB, no migration, drain, fail closed.
_OLDER_APP_INSERT = ('INSERT INTO leases(job_id,host,selector,owner,expires,remote,memory_budget_mb,allow_sharing,task,project_id,account_id)'
                     ' VALUES(?,?,?,?,?,?,?,?,?,?,?)')  # the statement an app version before S1-03 runs


def test_a_live_older_app_sharing_the_reservation_db_is_respected_without_migration(stores, tmp_path):
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.shared_scheduler import ResourceLeases
    store, leases = stores
    path = tmp_path / 'resource_leases.sqlite3'
    with sqlite3.connect(path) as older:
        older.execute(_OLDER_APP_INSERT, ('legacy-job', 'local-compute', '0', 'old-app', time.time() + 60, 0, 0, 0, None, None, None))
    before = [row for row in leases.list() if row['job_id'] == 'legacy-job']
    scheduler = JobScheduler(store, leases)
    overlapping = _queued(store, scheduler, 'needs-all', resources={'host': 'local-compute', 'selector': 'all'})
    assert scheduler.claim_job('worker-1', {'hosts': ['local-compute']}) is None
    assert {row['job_id']: row['wait_reason'] for row in scheduler.queue_view('ns:project-a')}[overlapping] == 'legacy_app_active'
    assert [row for row in leases.list() if row['job_id'] == 'legacy-job'] == before, 'the older app row is untouched'
    tables = {row[0] for row in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables <= {'leases', 'devices'}, 'no migration tables or markers in the shared reservation DB'
    with sqlite3.connect(path) as older:  # the older app ends its job
        older.execute("DELETE FROM leases WHERE job_id='legacy-job' AND owner='old-app'")
    lease = scheduler.claim_job('worker-1', {'hosts': ['local-compute']})
    assert lease.job_id == overlapping
    # The older app decides with the same overlap rule over the same rows, so it sees the new reservation.
    assert not ResourceLeases(path, owner='old-app').available('local-compute', '0')


def test_a_family_job_reservation_of_this_app_is_waited_for_as_external(stores, tmp_path):
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.shared_scheduler import ResourceLeases
    store, leases = stores
    family = ResourceLeases(tmp_path / 'resource_leases.sqlite3', owner='family-job')
    assert family.acquire('flow_preview', 'local-compute', 'all')
    scheduler = JobScheduler(store, leases)
    job = _queued(store, scheduler, 'gpu-job', resources={'host': 'local-compute', 'selector': 'all'})
    assert scheduler.claim_job('worker-1', {'hosts': ['local-compute']}) is None
    assert {row['job_id']: row['wait_reason'] for row in scheduler.queue_view('ns:project-a')}[job] == 'external_reservation'
    family.release('flow_preview', terminal=True)
    assert scheduler.claim_job('worker-1', {'hosts': ['local-compute']}).job_id == job


def test_an_unavailable_reservation_store_fails_closed(stores, tmp_path):
    from backend.engine.job_scheduler import JobScheduler
    store, leases = stores
    scheduler = JobScheduler(store, leases)
    job = _queued(store, scheduler, 'gpu-job', resources={'host': 'local-compute', 'selector': 'all'})
    blocker = sqlite3.connect(leases.path, timeout=0)
    blocker.execute('BEGIN IMMEDIATE')  # another process holds the reservation DB write lock
    try:
        assert scheduler.claim_job('worker-1', {'hosts': ['local-compute']}) is None
    finally:
        blocker.rollback()
        blocker.close()
    assert store.get(job).state == 'queued' and store.attempts(job) == []
    assert {row['job_id']: row['wait_reason'] for row in scheduler.queue_view('ns:project-a')}[job] == 'reservation_store_unavailable'


def test_the_scheduler_never_signals_a_process(stores, monkeypatch):
    import os
    import psutil
    from backend.engine.job_scheduler import JobScheduler

    def refuse(*_args, **_kwargs):
        raise AssertionError('the scheduler must never signal a process')

    monkeypatch.setattr(os, 'kill', refuse)
    for name in ('terminate', 'kill', 'send_signal'):
        monkeypatch.setattr(psutil.Process, name, refuse)
    store, leases = stores
    scheduler = JobScheduler(store, leases)
    job = _queued(store, scheduler, 'budgeted', resources=None)
    scheduler.set_budget(job, max_runtime_s=0.01)
    lease = scheduler.claim_job('worker-1', {'hosts': []})
    time.sleep(0.05)
    scheduler.enforce_budgets()
    assert store.cancel_intent(job)['reason'] == 'runtime budget exceeded', 'a budget becomes a cancel intent only'
    assert store.get(lease.job_id).state == 'running'


# --- Review round 1 regressions ---------------------------------------------------------------------------
@pytest.fixture
def shared(tmp_path, monkeypatch):
    """The production ledger and reservation DB of one backend's user-data folder."""
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    from backend.api import routes_training
    from backend.engine.shared_scheduler import shared_leases
    return routes_training, routes_training.job_ledger(), shared_leases()


def test_a_claim_whose_worker_never_launched_is_freed_at_the_next_start(shared):
    from backend.engine.job_scheduler import JobScheduler
    routes_training, store, leases = shared
    scheduler = JobScheduler(store, leases)
    job = _queued(store, scheduler, 'claimed-then-crashed', resources={'host': 'local-compute', 'selector': 'all'})
    assert scheduler.claim_job('worker-1', {'hosts': ['local-compute']}).job_id == job  # the backend dies before launch

    class Restarted:
        _leases = leases

        def list_jobs(self):
            return []

        def abort_job(self, _job_id):
            raise AssertionError('nothing to abort')

    routes_training.reconcile_job_ledger(Restarted())
    assert store.get(job).state == 'interrupted'
    assert [row['job_id'] for row in leases.list()] == [job], 'kept while its refresh could still come from a live worker'
    _stale(leases, job)  # nothing refreshed it for a whole lease period
    routes_training._ledger_maintenance_tick(Restarted(), store)
    assert [row['job_id'] for row in leases.list()] == [], 'the ended claim frees the device'
    assert leases.acquire('next-job', 'local-compute', 'all')


def _stale(leases, job_id):
    with sqlite3.connect(leases.path) as conn:
        conn.execute('UPDATE leases SET expires=? WHERE job_id=?', (time.time() - 1, job_id))


def test_a_stop_that_wins_the_claim_race_ends_the_claim_and_frees_the_device(harness, monkeypatch):
    from backend.api import routes_training
    from backend.engine.job_scheduler import JobScheduler
    api = TestClient(harness['app'], headers={'X-Vision-Token': harness['app'].state.api_token})
    api.post('/api/project/create', json={'name': 'Race'})
    assert api.put('/api/project/update', json={'source_dataset_dir': str(harness['source'])}).status_code == 200
    body = {'task': 'classification', 'dataset_path': str(harness['source']), 'preset': 'fast'}
    first = api.post('/api/training/start', json=body, headers={'Idempotency-Key': 'race-1'}).json()['job_id']
    second = api.post('/api/training/start', json={**body, 'preset': 'precision'}, headers={'Idempotency-Key': 'race-2'}).json()['job_id']
    real_claim = JobScheduler.claim_job

    def claim_then_stop(self, worker_id, capabilities):
        lease = real_claim(self, worker_id, capabilities)
        if lease is not None and lease.job_id == second:
            harness['manager'].abort_job(second)  # the user's stop lands between the claim and the launch
        return lease

    monkeypatch.setattr(JobScheduler, 'claim_job', claim_then_stop)
    harness['release'].set()
    store = routes_training.job_ledger()
    for _ in range(200):
        if store.get(second).state in ('aborted', 'completed', 'failed'):
            break
        time.sleep(0.05)
    assert store.get(second).state == 'aborted'
    assert harness['calls'] == [first], 'the stopped job never launched'
    assert second not in [row['job_id'] for row in harness['manager']._leases.list()], 'its reservation is freed'


def test_a_lost_reservation_returns_the_claim_to_the_queue(stores, monkeypatch):
    from backend.engine.job_scheduler import JobScheduler
    store, leases = stores
    scheduler = JobScheduler(store, leases)
    job = _queued(store, scheduler, 'stalled-claimer', resources={'host': 'local-compute', 'selector': 'all'})
    monkeypatch.setattr(leases, 'stamp_fence', lambda *_args: False)  # the reservation lapsed while the claimer stalled
    assert scheduler.claim_job('worker-1', {'hosts': ['local-compute']}) is None
    assert store.get(job).state == 'queued'
    assert store.attempts(job)[0]['ended_ns'] is not None, 'the abandoned attempt is closed'
    assert {row['job_id']: row['wait_reason'] for row in scheduler.queue_view('ns:project-a')}[job] == 'reservation_lost'
    assert leases.list() == []


def test_lease_expiry_continues_past_a_job_another_owner_moved(tmp_path, monkeypatch):
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.job_store import JobStore, StaleFencingToken
    from backend.engine.shared_scheduler import ResourceLeases
    store = JobStore(tmp_path / 'ledger.sqlite3')
    scheduler = JobScheduler(store, ResourceLeases(tmp_path / 'leases.sqlite3', lease_seconds=0.05))
    moved, idle = (_queued(store, scheduler, name) for name in ('moved', 'idle'))
    scheduler.claim_job('worker-1', {'hosts': []}), scheduler.claim_job('worker-1', {'hosts': []})
    time.sleep(0.15)
    real_transition = store.transition

    def transition(job_id, *args, **kwargs):
        if job_id == moved:
            raise StaleFencingToken('another owner reattached it')
        return real_transition(job_id, *args, **kwargs)

    monkeypatch.setattr(store, 'transition', transition)
    assert scheduler.expire_leases(observe=lambda _lease: 'unknown') == [idle]
    assert store.get(idle).state == 'disconnected'


def test_exit_evidence_never_requeues_a_stopped_job(tmp_path):
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import ResourceLeases
    store = JobStore(tmp_path / 'ledger.sqlite3')
    scheduler = JobScheduler(store, ResourceLeases(tmp_path / 'leases.sqlite3', lease_seconds=0.05))
    job = _queued(store, scheduler, 'stopped')
    scheduler.set_budget(job, max_attempts=3)
    scheduler.claim_job('worker-1', {'hosts': []})
    store.request_cancel(job, 'actor-a', 'user stop')
    time.sleep(0.15)
    scheduler.expire_leases(observe=lambda _lease: 'exited')
    assert store.get(job).state == 'aborted', 'a stop request is honoured, not retried'


def test_reattach_moves_the_reservation_fence_and_extends_the_attempt_lease(shared):
    from backend.engine.job_scheduler import JobScheduler
    routes_training, store, leases = shared
    scheduler = JobScheduler(store, leases)
    job = _queued(store, scheduler, 'recovered', resources={'host': 'local-compute', 'selector': 'all'})
    lease = scheduler.claim_job('worker-1', {'hosts': ['local-compute']})
    before = store.attempts(job)[-1]['lease_expires_ns']
    time.sleep(0.01)
    link = routes_training.TrainingLedgerLink(store, job)
    link.reattached()  # the restarted backend
    assert link.fencing_token != lease.fence
    assert [row['fence'] for row in leases.list() if row['job_id'] == job] == [link.fencing_token]
    assert store.attempts(job)[-1]['lease_expires_ns'] > before


def test_two_workers_never_hold_one_device(stores, tmp_path):
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import ResourceLeases
    store, leases = stores
    jobs = [_queued(store, JobScheduler(store, leases), f'gpu-{n}', resources={'host': 'local-compute', 'selector': 'all'}) for n in range(3)]
    claimed, lock = [], threading.Lock()

    def worker(name):
        scheduler = JobScheduler(JobStore(tmp_path / 'ledger.sqlite3'), ResourceLeases(tmp_path / 'resource_leases.sqlite3', owner=name))
        for _ in range(5):
            lease = scheduler.claim_job(name, {'hosts': ['local-compute']})
            if lease is not None:
                with lock:
                    claimed.append(lease.job_id)

    threads = [threading.Thread(target=worker, args=(f'worker-{n}',)) for n in range(2)]
    [thread.start() for thread in threads]
    [thread.join(30) for thread in threads]
    assert len(claimed) == 1, f'one device, one claim: {claimed}'
    reasons = {row['job_id']: row['wait_reason'] for row in JobScheduler(store, leases).queue_view('ns:project-a')}
    assert all(reasons[job] == 'device_reserved' for job in jobs if job not in claimed)


def test_a_job_reconnecting_twice_runs_after_each_reconnect(shared):
    routes_training, store, _leases = shared
    job = store.submit(_context(), 'ns:project-a', 'training', {'job': 'remote'}, 'remote', job_id='remote-job').id
    link = routes_training.TrainingLedgerLink(store, job)
    link.launched('remote')
    for _ in range(2):
        ref = store.get(job)
        store.transition(job, ref.revision, 'disconnect', None, fencing_token=link.fencing_token)
        link.reattached()
        assert store.get(job).state == 'running'


def test_the_queue_api_shows_priority_position_budget_and_wait_reason(harness):
    api = TestClient(harness['app'], headers={'X-Vision-Token': harness['app'].state.api_token})
    api.post('/api/project/create', json={'name': 'Queue view'})
    assert api.put('/api/project/update', json={'source_dataset_dir': str(harness['source'])}).status_code == 200
    body = {'task': 'classification', 'dataset_path': str(harness['source']), 'preset': 'fast'}
    assert api.post('/api/training/start', json=body, headers={'Idempotency-Key': 'view-1'}).status_code == 200
    waiting = api.post('/api/training/start', json={**body, 'preset': 'precision', 'priority': 3, 'max_runtime_s': 60},
                       headers={'Idempotency-Key': 'view-2'}).json()
    rows = api.get('/api/training/queue').json()['jobs']
    assert rows == [{'job_id': waiting['job_id'], 'priority': 3, 'position': 1, 'wait_reason': 'device_reserved',
                     'budget': {'max_runtime_s': 60.0, 'max_attempts': 1}}]


def test_queue_false_keeps_the_immediate_refusal(harness):
    api = TestClient(harness['app'], headers={'X-Vision-Token': harness['app'].state.api_token})
    api.post('/api/project/create', json={'name': 'No queue'})
    assert api.put('/api/project/update', json={'source_dataset_dir': str(harness['source'])}).status_code == 200
    body = {'task': 'classification', 'dataset_path': str(harness['source']), 'preset': 'fast'}
    assert api.post('/api/training/start', json=body, headers={'Idempotency-Key': 'nq-1'}).status_code == 200
    refused = api.post('/api/training/start', json={**body, 'preset': 'precision', 'queue': False}, headers={'Idempotency-Key': 'nq-2'})
    assert refused.status_code == 409, refused.text


# --- Review round 2 regressions ---------------------------------------------------------------------------
def test_a_quit_before_the_worker_launched_leaves_an_ordinary_expiring_reservation(shared, tmp_path):
    routes_training, store, leases = shared
    manager = routes_training.TrainingJobManager()
    preparing = threading.Event()
    job = store.submit(_context(), 'ns:project-a', 'training', {'job': 'prep'}, 'prep', job_id='job_preparing').id
    manager.start_job(job, 'classification', str(tmp_path / 'data'), str(tmp_path / 'out' / job),
                      prepare_dataset=lambda cancel: preparing.wait(10), ledger=routes_training.TrainingLedgerLink(store, job))
    try:
        manager.detach_all_for_shutdown()
        rows = [row for row in leases.list() if row['job_id'] == job]
        assert rows and rows[0]['uncertain'] == 0, 'no worker launched, so nothing outlives this backend'
    finally:
        preparing.set()
        manager.get_job(job).thread.join(20)


def test_a_backend_that_does_not_own_the_data_folder_interrupts_and_frees_nothing(shared, tmp_path):
    import subprocess
    import sys
    from backend.engine.job_scheduler import JobScheduler
    routes_training, store, leases = shared
    job = _queued(store, JobScheduler(store, leases), 'another-backends-job', resources={'host': 'local-compute', 'selector': 'all'})
    JobScheduler(store, leases).claim_job('backend-a', {'hosts': ['local-compute']})
    hold = ('import msvcrt,sys,time;f=open(sys.argv[1],"a+b");f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)' if os.name == 'nt'
            else 'import fcntl,sys,time;f=open(sys.argv[1],"a+b");fcntl.flock(f.fileno(),fcntl.LOCK_EX)')
    holder = subprocess.Popen([sys.executable, '-c', hold + ';print("held",flush=True);time.sleep(30)',
                               str(store.path.parent / 'owner.lock')], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'held'  # a live backend A owns the folder

        class BackendB:
            _leases = leases

            def list_jobs(self):
                return []

        assert routes_training.reconcile_job_ledger(BackendB()) == []
        assert store.get(job).state == 'running', "backend B does not interrupt backend A's job"
        assert [row['job_id'] for row in leases.list()] == [job], 'nor free its reservation'
    finally:
        holder.kill()
        holder.wait(10)


def test_the_sweep_keeps_unknown_uncertain_and_remote_reservations(stores, tmp_path):
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.shared_scheduler import ResourceLeases
    store, leases = stores
    other_app = ResourceLeases(leases.path, owner='other-ledger')
    assert other_app.acquire_for_job('other-ledger-job', 'gpu-host', 'all')[0]
    other_app.stamp_fence('other-ledger-job', 7)
    assert leases.acquire('remote-job', 'ssh:remote', 'all', remote=True)
    leases.stamp_fence('remote-job', 3)
    leases.mark_uncertain('remote-job')
    assert JobScheduler(store, leases).sweep_orphaned_reservations() == []
    assert {row['job_id'] for row in leases.list()} == {'other-ledger-job', 'remote-job'}


def test_reattach_never_restamps_a_remote_reservation(shared):
    routes_training, store, leases = shared
    job = store.submit(_context(), 'ns:project-a', 'training', {'job': 'remote'}, 'r', job_id='remote-held').id
    link = routes_training.TrainingLedgerLink(store, job)
    link.launched('remote')
    assert leases.acquire(job, 'ssh:remote', 'all', remote=True)
    leases.mark_uncertain(job)
    ref = store.get(job)
    store.transition(job, ref.revision, 'disconnect', None, fencing_token=link.fencing_token)
    routes_training.TrainingLedgerLink(store, job).reattached()
    row = [row for row in leases.list() if row['job_id'] == job][0]
    assert (row['fence'], row['uncertain']) == (None, 1), 'the uncertain remote reservation keeps waiting for exit evidence'


def test_a_spent_runtime_budget_becomes_one_cancel_intent_and_the_owner_stops_the_job(shared, monkeypatch):
    routes_training, store, _leases = shared
    job = store.submit(_context(), 'ns:project-a', 'training', {'job': 'budget'}, 'b', job_id='budgeted').id
    store.set_budget(job, {'max_runtime_s': 0.01, 'max_attempts': 1})
    link = routes_training.TrainingLedgerLink(store, job)
    link.launched('local')
    time.sleep(0.05)
    manager = routes_training.TrainingJobManager()
    stopped = []
    monkeypatch.setattr(manager, 'abort_job', lambda job_id: stopped.append(job_id) or True)
    record = routes_training.JobRecord(job, 'classification', 'fast', '', '', 'running', ledger=link)
    manager._enforce_budget(record)
    manager._enforce_budget(record)
    assert stopped == [job], 'stopped once, through the normal stop path'
    assert store.cancel_intent(job)['reason'] == 'runtime budget exceeded'


def test_a_stamp_error_returns_the_claim_to_the_queue(stores, monkeypatch):
    import sqlite3
    from backend.engine.job_scheduler import JobScheduler
    store, leases = stores
    scheduler = JobScheduler(store, leases)
    job = _queued(store, scheduler, 'locked-stamp', resources={'host': 'local-compute', 'selector': 'all'})

    def locked(*_args):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(leases, 'stamp_fence', locked)
    assert scheduler.claim_job('worker-1', {'hosts': ['local-compute']}) is None
    assert store.get(job).state == 'queued'


def test_queue_positions_count_within_the_project_only(stores):
    from backend.engine.job_scheduler import JobScheduler
    store, leases = stores
    scheduler = JobScheduler(store, leases)
    for name in ('b1', 'b2'):
        _queued(store, scheduler, name, 'project-b', priority=5)
    mine = _queued(store, scheduler, 'a1', 'project-a')
    assert [(row['job_id'], row['position']) for row in scheduler.queue_view('ns:project-a')] == [(mine, 1)]


# --- Review round 3 regressions ---------------------------------------------------------------------------
def test_a_reservation_a_live_worker_still_refreshes_is_kept_after_its_job_ended_in_the_ledger(stores):
    from backend.engine.job_scheduler import JobScheduler
    store, leases = stores
    scheduler = JobScheduler(store, leases)
    job = _queued(store, scheduler, 'unrecoverable-journal', resources={'host': 'local-compute', 'selector': 'all'})
    lease = scheduler.claim_job('backend-a', {'hosts': ['local-compute']})
    record = store.get(job)
    store.transition(job, record.revision, 'interrupt', {'reason': 'journal could not be recovered'})
    leases.heartbeat_fenced(job, lease.fence)  # the worker is still running and refreshing its row
    assert scheduler.sweep_orphaned_reservations() == [] and [row['job_id'] for row in leases.list()] == [job]
    _stale(leases, job)  # the worker exited: nothing refreshes the row any more
    assert scheduler.sweep_orphaned_reservations() == [job]


def test_a_backend_that_started_second_takes_over_the_folder_once_the_owner_exits(shared, monkeypatch):
    import subprocess
    import sys
    from backend.engine.job_scheduler import JobScheduler
    routes_training, store, leases = shared
    monkeypatch.setattr(routes_training, '_MAINTENANCE_SECONDS', 0.1)
    job = _queued(store, JobScheduler(store, leases), 'owner-died-preparing', resources={'host': 'local-compute', 'selector': 'all'})
    JobScheduler(store, leases).claim_job('backend-a', {'hosts': ['local-compute']})
    hold = ('import msvcrt,sys,time;f=open(sys.argv[1],"a+b");f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)' if os.name == 'nt'
            else 'import fcntl,sys,time;f=open(sys.argv[1],"a+b");fcntl.flock(f.fileno(),fcntl.LOCK_EX)')
    holder = subprocess.Popen([sys.executable, '-c', hold + ';print("held",flush=True);sys.stdin.read()',
                               str(store.path.parent / 'owner.lock')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    manager = routes_training.TrainingJobManager()
    try:
        assert holder.stdout.readline().strip() == 'held'  # backend A owns the folder
        assert routes_training.reconcile_job_ledger(manager) == []
        time.sleep(0.5)
        assert store.get(job).state == 'running', 'A is alive: its job is not interrupted'
        holder.stdin.close()  # A exits normally; the OS releases its lock
        assert holder.wait(10) == 0
        _stale(leases, job)  # A's refresh stopped with it
        for _ in range(100):
            if store.get(job).state == 'interrupted' and not leases.list():
                break
            time.sleep(0.05)
        assert store.get(job).state == 'interrupted', 'the surviving backend reconciles once it owns the folder'
        assert leases.list() == [] and leases.acquire('next-job', 'local-compute', 'all'), 'and the device is free again'
    finally:
        manager._shutdown.set()
        if holder.poll() is None:
            holder.stdin.close()
            holder.wait(10)


def test_a_waiting_job_is_not_claimed_after_a_normal_quit(harness):
    from backend.api import routes_training
    api = TestClient(harness['app'], headers={'X-Vision-Token': harness['app'].state.api_token})
    api.post('/api/project/create', json={'name': 'Quit'})
    assert api.put('/api/project/update', json={'source_dataset_dir': str(harness['source'])}).status_code == 200
    body = {'task': 'classification', 'dataset_path': str(harness['source']), 'preset': 'fast'}
    first = api.post('/api/training/start', json=body, headers={'Idempotency-Key': 'quit-1'}).json()['job_id']
    second = api.post('/api/training/start', json={**body, 'preset': 'precision'}, headers={'Idempotency-Key': 'quit-2'}).json()['job_id']
    harness['manager'].detach_all_for_shutdown()
    harness['release'].set()  # the running job ends during the quit
    harness['manager'].get_job(first).thread.join(30)
    time.sleep(1.5)  # longer than the local queue watcher's period
    store = routes_training.job_ledger()
    assert store.get(second).state == 'queued' and store.attempts(second) == [], 'it waits in the ledger for the next start'
    assert harness['calls'] == [first]


# --- Review round 4 regressions ---------------------------------------------------------------------------
def test_one_failing_maintenance_tick_never_ends_maintenance(shared, monkeypatch):
    routes_training, store, leases = shared
    monkeypatch.setattr(routes_training, '_MAINTENANCE_SECONDS', 0.02)
    calls = []

    def flaky(manager, ledger):
        calls.append(len(calls))
        if len(calls) == 1:
            raise PermissionError(13, 'owner.lock is held by an antivirus scan')
        return True

    monkeypatch.setattr(routes_training, '_ledger_maintenance_tick', flaky)
    manager = routes_training.TrainingJobManager()
    try:
        routes_training._start_ledger_maintenance(manager, store)
        for _ in range(200):
            if len(calls) >= 3:
                break
            time.sleep(0.01)
        assert len(calls) >= 3 and manager._maintenance.is_alive(), 'the thread survives the failed tick'
    finally:
        manager._shutdown.set()


def test_a_lock_file_that_cannot_be_opened_means_not_the_owner(shared, tmp_path, monkeypatch):
    routes_training, store, leases = shared

    def locked(*args, **kwargs):
        raise PermissionError(13, 'The process cannot access the file because it is being used by another process')

    monkeypatch.setattr(routes_training, 'open', locked, raising=False)
    assert routes_training._own_data_folder(tmp_path / 'held-by-scanner') is False
