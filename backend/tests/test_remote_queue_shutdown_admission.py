"""The real manager's original completion path must preserve its queue after quit.

Only the external remote runner is controlled. Scheduling, original Python
threads, SQLite leases, training ledger attempts and terminal receipts are real.
There is no SSH/GPU/model work, PID signal, lease deletion or quiescence claim.
"""
from __future__ import annotations

import copy
import json
import threading

import pytest


def _setup(tmp_path, monkeypatch):
    from backend.api import routes_training as training
    from backend.contracts.context import ProjectContext
    from backend.engine.job_store import JobStore
    from backend.engine import shared_scheduler
    from backend.remote.profiles import ComputeProfile

    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'data'))
    leases = shared_scheduler.ResourceLeases(tmp_path / 'leases.sqlite', owner='controlled-original-owner')
    monkeypatch.setattr(shared_scheduler, 'shared_leases', lambda: leases)
    store = JobStore(tmp_path / 'ledger.sqlite')
    manager = training.TrainingJobManager()
    profile = ComputeProfile(id='queue-regression', name='Controlled runner only',
        ssh_target='fixture.invalid', ssh_port=22, remote_root='/owned-queue-regression',
        runtime_kind='python', runtime_value='python3', gpu_selector='0')
    entered = {name: threading.Event() for name in ('first', 'waiting')}
    release = {name: threading.Event() for name in ('first', 'waiting')}
    context = ProjectContext(workspace_id='fixture-workspace', project_id='fixture-project',
        actor_id='fixture-actor', mode='local')
    records = {}
    for name in ('first', 'waiting'):
        job_id = 'job_queue_shutdown_' + name
        output = tmp_path / 'outputs' / job_id
        spec = {'task': 'classification', 'preset': 'fast', 'dataset_path': str(tmp_path / 'source'),
            'immutable_fixture_recipe': name, 'priority': 2 if name == 'waiting' else 0}
        ref = store.submit(context, 'fixture-project-key', 'training', spec, None,
            job_id=job_id, output_dir=str(output))
        store.transition(job_id, ref.revision, 'queue')
        link = training.TrainingLedgerLink(store, job_id)
        def runner(record, original_name=name, original_id=job_id):
            assert record.job_id == original_id
            assert record.remote_profile is profile
            entered[original_name].set()
            assert release[original_name].wait(10), 'Controlled external runner was not released'
            return {'status': 'completed', 'best_metric': .25}
        records[name] = training.JobRecord(job_id, 'classification', 'fast', str(tmp_path / 'source'),
            str(output), 'queued', phase='queued', remote_profile_id=profile.id, remote_profile=profile,
            remote_runner=runner, launch_spec=copy.deepcopy(spec), ledger=link)
    # A reservation owned by an unrelated run must not be released by this gate.
    assert leases.acquire('fixture-unrelated-run', 'ssh:other.fixture.invalid:22', '0', remote=True)
    with manager._lock:
        manager._jobs.update({record.job_id: record for record in records.values()})
        manager._remote_queue.append(records['waiting'].job_id)
    def queued_state():
        waiting = records['waiting']
        return {'queue': list(manager._remote_queue), 'status': waiting.status, 'phase': waiting.phase,
            'launch_spec': copy.deepcopy(waiting.launch_spec), 'thread': waiting.thread,
            'cancel': waiting.preparation_cancel.is_set(), 'ledger': store.record(waiting.job_id),
            'events': store.events(waiting.job_id), 'attempts': store.attempts(waiting.job_id)}
    return training, manager, leases, store, profile, records, entered, release, queued_state


def _join_original_records(records, release):
    for event in release.values():
        event.set()
    for record in records.values():
        if record.thread is not None:
            record.thread.join(10)
            assert not record.thread.is_alive(), 'Original controlled manager worker did not close'


@pytest.mark.parametrize('original_completion', ['fresh', 'reconnect'])
@pytest.mark.parametrize('closing', [True, False])
def test_original_remote_completion_respects_shutdown_without_changing_normal_dispatch(
        tmp_path, monkeypatch, original_completion, closing):
    training, manager, leases, store, profile, records, entered, release, queued_state = _setup(tmp_path, monkeypatch)
    first, waiting = records['first'], records['waiting']
    unrelated = next(row for row in leases.list() if row['job_id'] == 'fixture-unrelated-run')
    try:
        if original_completion == 'fresh':
            with manager._lock:
                first.status = 'running'; first.phase = 'preparing'
                manager._active_job_id = first.job_id
                manager._launch_remote_worker_locked(first)
        else:
            first.ledger.launched('remote')
            first.ledger.detached('controlled earlier observer')
            first.status = 'disconnected'; first.phase = 'disconnected'
            assert leases.acquire(first.job_id, manager._lease_host(profile), '0', remote=True)
            assert manager.reconnect_remote_job(first.job_id) is first
        assert entered['first'].wait(5), 'Original manager runner did not enter'
        before = queued_state()
        first_reservation = next(row for row in leases.list() if row['job_id'] == first.job_id)
        if closing:
            manager.detach_all_for_shutdown()
            assert manager._shutdown.is_set()
            assert queued_state() == before
            # Shutdown must preserve the current remote worker and its original reservation.
            assert first.thread.is_alive() and not first.preparation_cancel.is_set()
            assert next(row for row in leases.list() if row['job_id'] == first.job_id) == first_reservation
        release['first'].set()
        first.thread.join(10)
        assert not first.thread.is_alive()
        assert first.status == first.phase == 'completed'
        first_receipt = json.loads((tmp_path / 'outputs' / first.job_id / 'job_receipt.json').read_bytes())
        assert first_receipt['job_id'] == first.job_id and first_receipt['status'] == 'completed'
        if closing:
            assert queued_state() == before, 'Original completion dispatched or mutated the queue after shutdown'
            assert not entered['waiting'].is_set()
            assert not (tmp_path / 'outputs' / waiting.job_id).exists()
            assert not any(row['job_id'] == waiting.job_id for row in leases.list())
        else:
            assert entered['waiting'].wait(5), 'Normal original completion did not dispatch its queued run'
            assert waiting.thread is not None and waiting.thread.is_alive()
            assert waiting.status == 'running' and waiting.phase == 'preparing'
            assert manager._remote_queue == []
            assert len(store.attempts(waiting.job_id)) == 1
            assert store.get(waiting.job_id).state == 'running'
            assert next(row for row in leases.list() if row['job_id'] == waiting.job_id)['remote'] == 1
            release['waiting'].set()
            waiting.thread.join(10)
            assert not waiting.thread.is_alive() and waiting.status == 'completed'
            assert store.get(waiting.job_id).state == 'completed'
        assert next(row for row in leases.list() if row['job_id'] == 'fixture-unrelated-run') == unrelated
    finally:
        _join_original_records(records, release)


def test_shutdown_during_original_slot_probe_keeps_queue_and_ledger_unchanged(tmp_path, monkeypatch):
    training, manager, leases, store, profile, records, entered, release, queued_state = _setup(tmp_path, monkeypatch)
    before = queued_state()
    probe_entered = threading.Event(); probe_release = threading.Event(); failures = []
    original_probe = manager._remote_slot_busy
    def probe(original_profile):
        assert original_profile is profile
        busy = original_probe(original_profile)
        assert busy is False
        probe_entered.set()
        assert probe_release.wait(10), 'Original controlled slot probe was not released'
        return busy
    monkeypatch.setattr(manager, '_remote_slot_busy', probe)
    def dispatch():
        try:
            with manager._lock:
                manager._start_waiting_remote_jobs_locked()
        except BaseException as error:
            failures.append(error)
    def close():
        try:
            manager.detach_all_for_shutdown()
        except BaseException as error:
            failures.append(error)
    dispatcher = threading.Thread(target=dispatch, name='original-slot-dispatch-regression')
    closer = threading.Thread(target=close, name='original-manager-close-regression')
    try:
        dispatcher.start()
        assert probe_entered.wait(5)
        closer.start()
        # The existing production shutdown closes its Event before acquiring this same lock.
        assert manager._shutdown.wait(5)
        probe_release.set()
        dispatcher.join(10); closer.join(10)
        assert not dispatcher.is_alive() and not closer.is_alive()
        assert not failures, [repr(error) for error in failures]
        assert queued_state() == before, 'Slot-probe return launched work after the original close Event'
        assert not entered['waiting'].is_set()
        assert not any(row['job_id'] == records['waiting'].job_id for row in leases.list())
        assert not (tmp_path / 'outputs' / records['waiting'].job_id).exists()
    finally:
        probe_release.set()
        for thread in (dispatcher, closer):
            if thread.ident is not None:
                thread.join(10)
                assert not thread.is_alive(), 'Original regression control thread did not close'
        _join_original_records(records, release)
