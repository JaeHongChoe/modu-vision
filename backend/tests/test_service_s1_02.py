"""S1-02: persistent job ledger and idempotent submission (store, state machine and training route).

Everything runs on CPU in tmp dirs: no training process, GPU or existing job is touched.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.contracts.context import ProjectContext


def _context(actor='actor-a', project='project-a'):
    return ProjectContext(workspace_id='ws-1', project_id=project, actor_id=actor, mode='local')


SPEC = {'task': 'classification', 'preset': 'fast', 'dataset_fingerprint': 'f' * 64}


@pytest.fixture
def store(tmp_path):
    from backend.engine.job_store import JobStore
    return JobStore(tmp_path / 'ledger.sqlite3')


def test_same_key_and_spec_return_the_same_job_and_a_different_spec_conflicts(store):
    from backend.engine.job_store import JobConflict
    first = store.submit(_context(), 'key-a', 'training', SPEC, 'submit-1')
    again = store.submit(_context(), 'key-a', 'training', dict(reversed(list(SPEC.items()))), 'submit-1')
    assert (again.id, again.created, first.created) == (first.id, False, True), 'key order does not change the spec digest'
    with pytest.raises(JobConflict):
        store.submit(_context(), 'key-a', 'training', {**SPEC, 'preset': 'precision'}, 'submit-1')


def test_keys_are_scoped_to_project_namespace_and_actor(store):
    a = store.submit(_context(), 'key-a', 'training', SPEC, 'k')
    other_actor = store.submit(_context(actor='actor-b'), 'key-a', 'training', SPEC, 'k')
    other_project = store.submit(_context(project='project-b'), 'key-b', 'training', SPEC, 'k')
    assert len({a.id, other_actor.id, other_project.id}) == 3


def test_concurrent_submissions_reserve_once(store):
    results = []
    threads = [threading.Thread(target=lambda: results.append(store.submit(_context(), 'key-a', 'training', SPEC, 'same')))
               for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len({ref.id for ref in results}) == 1
    assert sum(ref.created for ref in results) == 1


def test_transitions_need_the_current_revision_and_append_ordered_events(store):
    from backend.engine.job_store import StaleRevision
    ref = store.submit(_context(), 'key-a', 'training', SPEC, None)
    running = store.transition(ref.id, ref.revision, 'start')
    assert running.revision == ref.revision + 1
    with pytest.raises(StaleRevision):
        store.transition(ref.id, ref.revision, 'complete')
    store.transition(ref.id, running.revision, 'complete')
    events = store.events(ref.id)
    assert [row['event'] for row in events] == ['submit', 'start', 'complete']
    assert [row['seq'] for row in events] == sorted(row['seq'] for row in events)


def test_state_machine_rejects_illegal_transitions():
    from backend.engine.job_state import IllegalTransition, transition
    assert transition('accepted', 'start') == 'running'
    assert transition('running', 'detach') == 'detached'
    assert transition('detached', 'reattach') == 'running'
    with pytest.raises(IllegalTransition):
        transition('completed', 'start')


def test_attempt_is_recorded_before_launch_and_stale_fencing_tokens_are_rejected(store):
    from backend.engine.job_store import StaleFencingToken
    ref = store.submit(_context(), 'key-a', 'training', SPEC, None)
    first = store.begin_attempt(ref.id, ref.revision, 'local', 'boot-1', 4242)
    current = store.get(ref.id)
    second = store.begin_attempt(ref.id, current.revision, 'local', 'boot-2', 4343)
    assert (first.number, second.number) == (1, 2)
    assert second.fencing_token > first.fencing_token
    with pytest.raises(StaleFencingToken):
        store.transition(ref.id, store.get(ref.id).revision, 'complete', fencing_token=first.fencing_token)


def test_cancel_intent_persists_across_store_instances(tmp_path):
    from backend.engine.job_store import JobStore
    first = JobStore(tmp_path / 'ledger.sqlite3')
    ref = first.submit(_context(), 'key-a', 'training', SPEC, None)
    first.request_cancel(ref.id, 'actor-a', 'operator stop while the worker was unreachable')
    reopened = JobStore(tmp_path / 'ledger.sqlite3')
    assert reopened.cancel_intent(ref.id)['actor_id'] == 'actor-a'


def test_unlaunched_jobs_become_interrupted_on_recovery_and_are_never_relaunched(tmp_path):
    from backend.engine.job_store import JobStore
    first = JobStore(tmp_path / 'ledger.sqlite3')
    accepted = first.submit(_context(), 'key-a', 'training', SPEC, 'a')
    reopened = JobStore(tmp_path / 'ledger.sqlite3')
    recovered = reopened.recover_unlaunched('backend restarted before launch')
    assert [ref.id for ref in recovered] == [accepted.id]
    assert reopened.get(accepted.id).state == 'interrupted'
    assert reopened.attempts(accepted.id) == []


def test_legacy_roots_migrate_without_deleting_or_rewriting_them(tmp_path):
    from backend.engine.job_store import JobStore
    legacy = tmp_path / 'legacy' / '.modu_vision' / 'local_jobs'
    legacy.mkdir(parents=True)
    journal = legacy / 'job_1_abcdef.json'
    journal.write_text(json.dumps({'job_id': 'job_1_abcdef', 'status': 'completed', 'output_dir': str(tmp_path / 'models' / 'job_1_abcdef')}))
    before = hashlib.sha256(journal.read_bytes()).hexdigest()
    store = JobStore(tmp_path / 'ledger.sqlite3')
    receipt = store.migrate_legacy([legacy], tmp_path / 'migration-receipt.json')
    assert receipt['migrated'] == ['job_1_abcdef']
    assert hashlib.sha256(journal.read_bytes()).hexdigest() == before
    assert store.migrate_legacy([legacy], tmp_path / 'migration-receipt-2.json')['migrated'] == [], 'a second run is a no-op'


# Training route: real /api/training/start with the owned CLI replaced by a CPU fake.

@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    from backend.api import routes_training
    from backend.engine import local_training_worker
    import backend.main as main

    manager = routes_training.TrainingJobManager()
    monkeypatch.setattr(routes_training, 'training_job_manager', manager)
    monkeypatch.setattr(main, 'training_job_manager', manager)
    release = threading.Event()
    calls = []

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
    yield {'app': app, 'manager': manager, 'calls': calls, 'release': release, 'source': source, 'tmp': tmp_path}
    # Workers started by this test finish before the next test replaces the fake CLI.
    release.set()
    for record in manager.list_jobs():
        if record.thread is not None:
            record.thread.join(30)


def _client(harness):
    return TestClient(harness['app'], headers={'X-Vision-Token': harness['app'].state.api_token})


def _project(api, source):
    project = api.post('/api/project/create', json={'name': 'Ledger'}).json()
    assert api.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    return project


def _job_dirs(project):
    return sorted(path.name for path in Path(project['models_dir']).iterdir() if path.name.startswith('job_'))


def _start(api, source, key, **extra):
    return api.post('/api/training/start', headers={'Idempotency-Key': key},
                    json={'task': 'classification', 'dataset_path': str(source), 'preset': 'fast', **extra})


def _wait_finished(manager, job_id):
    for _ in range(200):
        record = next((row for row in manager.list_jobs() if row.job_id == job_id), None)
        if record is not None and record.status not in manager.ACTIVE_STATES:
            return record
        threading.Event().wait(0.05)
    raise AssertionError(f'{job_id} did not finish')


def test_same_key_and_spec_returns_the_reserved_job_without_new_side_effects(harness):
    api = _client(harness)
    project = _project(api, harness['source'])
    harness['release'].set()
    first = _start(api, harness['source'], 'submit-1')
    assert first.status_code == 200, first.text
    _wait_finished(harness['manager'], first.json()['job_id'])
    second = _start(api, harness['source'], 'submit-1')
    assert second.status_code == 200, second.text
    assert second.json()['job_id'] == first.json()['job_id'], 'a retried submission names the original job'
    assert harness['calls'] == [first.json()['job_id']], 'the worker starts once'
    assert _job_dirs(project) == [first.json()['job_id']], 'no second job folder'


def test_same_key_with_a_different_spec_conflicts_before_any_side_effect(harness):
    api = _client(harness)
    project = _project(api, harness['source'])
    harness['release'].set()
    first = _start(api, harness['source'], 'submit-2')
    assert first.status_code == 200, first.text
    _wait_finished(harness['manager'], first.json()['job_id'])
    conflict = _start(api, harness['source'], 'submit-2', preset='precision')
    assert conflict.status_code == 409, conflict.text
    assert _job_dirs(project) == [first.json()['job_id']]
    assert harness['calls'] == [first.json()['job_id']]


def test_concurrent_submissions_with_one_key_reserve_one_job(harness):
    api = _client(harness)
    project = _project(api, harness['source'])
    responses = []

    def submit():
        responses.append(_start(api, harness['source'], 'submit-3'))

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert [response.status_code for response in responses] == [200, 200], [response.text for response in responses]
    assert len({response.json()['job_id'] for response in responses}) == 1
    assert len(_job_dirs(project)) == 1
    for _ in range(200):
        if harness['calls']:
            break
        threading.Event().wait(0.05)
    harness['release'].set()
    _wait_finished(harness['manager'], responses[0].json()['job_id'])
    assert len(harness['calls']) == 1


def test_normal_shutdown_detaches_a_local_job_instead_of_aborting_it(harness):
    with _client(harness) as api:
        _project(api, harness['source'])
        started = _start(api, harness['source'], 'submit-4')
        assert started.status_code == 200, started.text
        job_id = started.json()['job_id']
        for _ in range(200):  # the worker has launched before the app quits
            if harness['calls']:
                break
            threading.Event().wait(0.05)
    record = next(row for row in harness['manager'].list_jobs() if row.job_id == job_id)
    try:
        assert record.status == 'running', 'a normal shutdown records intent and detaches; it does not abort'
        assert not record.preparation_cancel.is_set()
        from backend.api import routes_training
        ledger = routes_training.job_ledger()
        assert ledger.get(job_id).state == 'detached'
        assert [row for row in ledger.events(job_id) if row['event'] == 'detach'][0]['payload'] == {'reason': 'normal shutdown'}
        # The SIGTERM handler and the lifespan shutdown both detach: the second call adds nothing.
        harness['manager'].detach_all_for_shutdown()
        assert [row['event'] for row in ledger.events(job_id)].count('detach') == 1
        assert 'shutdown_intent' not in [row['event'] for row in ledger.events(job_id)]
    finally:
        harness['release'].set()


def test_the_same_key_in_another_project_names_another_job(harness):
    api = _client(harness)
    harness['release'].set()
    first_project = _project(api, harness['source'])
    first = _start(api, harness['source'], 'shared-key')
    assert first.status_code == 200, first.text
    _wait_finished(harness['manager'], first.json()['job_id'])
    other_source = harness['tmp'] / 'other-source'
    for split in ('train', 'val'):
        for label in ('ok', 'ng'):
            (other_source / split / label).mkdir(parents=True)
            for index in range(2):
                Image.new('RGB', (32, 32), (90 + index, 90, 90)).save(other_source / split / label / f'{label}-{index}.png')
    second_project = api.post('/api/project/create', json={'name': 'Ledger B'}).json()
    assert api.put('/api/project/update', json={'source_dataset_dir': str(other_source)}).status_code == 200
    second = _start(api, other_source, 'shared-key')
    assert second.status_code == 200, second.text
    assert second.json()['job_id'] != first.json()['job_id'], 'idempotency keys are scoped to the project namespace and actor'
    assert first_project['id'] != second_project['id']




def test_a_job_accepted_but_never_launched_is_listed_as_interrupted_after_a_restart(harness, monkeypatch):
    from backend.api import routes_training
    import backend.main as main
    api = _client(harness)
    project = _project(api, harness['source'])
    context = api.get('/api/context').json()['project_context']
    key = harness['app'].state.context_registry.project_key(routes_training_context(context))
    routes_training.job_ledger().submit(context, key, 'training', {'task': 'classification', 'preset': 'fast'},
                                        'crashed-before-launch', job_id='job_1_crash0',
                                        output_dir=str(Path(project['models_dir']) / 'job_1_crash0'))
    restarted = routes_training.TrainingJobManager()
    monkeypatch.setattr(routes_training, 'training_job_manager', restarted)
    monkeypatch.setattr(main, 'training_job_manager', restarted)
    app = main.create_app(project_dir=str(harness['tmp'] / 'projects'))
    # Entering the client runs the production startup: remote and local worker recovery, then the ledger.
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        jobs = {row['job_id']: row['status'] for row in client.get('/api/training/jobs').json()['jobs']}
    assert jobs.get('job_1_crash0') == 'interrupted', 'an accepted job is never lost'
    assert harness['calls'] == [], 'and never launched by the restart'
    assert routes_training.job_ledger().attempts('job_1_crash0') == []


def test_an_interrupted_job_stays_readable_after_a_second_restart_in_its_own_project_only(harness, monkeypatch):
    from types import SimpleNamespace
    from backend.api import routes_training
    import backend.main as main
    launched = []
    api = _client(harness)
    own = _project(api, harness['source'])
    other = api.post('/api/project/create', json={'name': 'Other'}).json()
    assert api.post('/api/project/open', json={'project_dir': own['project_dir']}).status_code == 200
    monkeypatch.setattr(harness['manager'], 'start_job', lambda **kwargs: launched.append(kwargs['job_id'])
                        or SimpleNamespace(job_id=kwargs['job_id'], status='running'))
    started = _start(api, harness['source'], 'readback-1')
    assert started.status_code == 200, started.text
    job_id = started.json()['job_id']
    for restart in (1, 2):
        manager = routes_training.TrainingJobManager()
        monkeypatch.setattr(routes_training, 'training_job_manager', manager)
        monkeypatch.setattr(main, 'training_job_manager', manager)
        app = main.create_app(project_dir=str(harness['tmp'] / 'projects'))
        with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:  # production startup recovery
            assert client.post('/api/project/open', json={'project_dir': own['project_dir']}).status_code == 200
            status = client.get('/api/training/status', params={'job_id': job_id}).json()
            jobs = {row['job_id']: row['status'] for row in client.get('/api/training/jobs').json()['jobs']}
            assert (status['job_id'], status['status']) == (job_id, 'interrupted'), (restart, status)
            assert 'accepted but not launched' in status['error']['message']
            assert jobs.get(job_id) == 'interrupted', (restart, jobs)
            # Another project in the same workspace never reads this project's job back.
            assert client.post('/api/project/open', json={'project_dir': other['project_dir']}).status_code == 200
            foreign = client.get('/api/training/status', params={'job_id': job_id}).json()
            assert (foreign['job_id'], foreign['status']) == (None, 'idle'), (restart, foreign)
            assert job_id not in [row['job_id'] for row in client.get('/api/training/jobs').json()['jobs']], restart
    store = routes_training.job_ledger()
    assert launched == [job_id], 'a restart never launches the job again'
    assert store.attempts(job_id) == []
    assert [row['event'] for row in store.events(job_id)] == ['submit', 'interrupt']


_FIRST_BACKEND = """
import json, os, sys, time
from pathlib import Path
from backend.api import routes_training
job_id, source, output = sys.argv[1:4]
store = routes_training.job_ledger()
first = routes_training.TrainingJobManager()
first.start_job(job_id, 'classification', source, output, device='cpu',
                config_overrides={'pretrained': False, 'backbone': 'resnet18', 'epochs': 3,
                                  'image_size': 32, 'batch_size': 2, 'num_workers': 0},
                ledger=routes_training.TrainingLedgerLink(store, job_id))
journal = Path(output) / 'local_job.json'
for _ in range(1200):
    if journal.is_file() and json.loads(journal.read_text()).get('owner_pid'):
        break
    time.sleep(0.05)
first.detach_all_for_shutdown()
print(json.dumps({'worker_pid': json.loads(journal.read_text())['owner_pid']}), flush=True)
os._exit(0)  # the first backend process ends; its worker is no longer its child
"""


def test_a_restart_reattaches_the_live_owned_worker_under_its_attempt_without_launching_again(tmp_path, monkeypatch):
    """Real tiny CPU worker: the first backend process detaches it and ends; a restarted backend reattaches it."""
    import os
    import subprocess
    import sys
    from backend.api import routes_training
    from backend.engine import local_training_worker as worker
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    source, output = tmp_path / 'source', tmp_path / 'output'
    for split in ('train', 'val'):
        for label in ('OK', 'NG'):
            folder = source / split / label
            folder.mkdir(parents=True)
            for index in range(2):
                Image.new('RGB', (32, 32), 'red' if label == 'NG' else 'blue').save(folder / f'{index}.png')
    store = routes_training.job_ledger()
    job_id = 'job_ledger_live_worker'
    store.submit(_context(), 'key-a', 'training', SPEC, 'live-worker', job_id=job_id, output_dir=str(output))
    # A separate process plays the first backend, so this process can never reap the worker during reconnection.
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'PYTHONPATH': str(repo), 'PYTHONDONTWRITEBYTECODE': '1', 'CUDA_VISIBLE_DEVICES': ''}
    first = subprocess.run([sys.executable, '-c', _FIRST_BACKEND, job_id, str(source), str(output)],
                           cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180)
    assert first.returncode == 0, first.stderr[-2000:]
    worker_pid = json.loads(first.stdout.strip().splitlines()[-1])['worker_pid']
    assert store.get(job_id).state == 'detached', store.events(job_id)
    second = routes_training.TrainingJobManager()
    worker.recover_local_jobs(second)  # production startup recovery, including the ledger reconciliation
    recovered = second.get_job(job_id)
    assert recovered is not None
    recovered.thread.join(120)
    assert recovered.process.pid == worker_pid, 'the same owned worker, not a new launch'
    assert recovered.status == 'completed', recovered.error
    assert len(store.attempts(job_id)) == 1, 'the restart never starts the job twice'
    events = [row['event'] for row in store.events(job_id)]
    assert events[:4] == ['submit', 'attempt', 'start', 'detach'] and 'reattach' in events, events
    assert store.get(job_id).state == 'completed', events


def test_the_attempt_is_recorded_before_the_worker_starts_and_the_end_state_is_kept(harness):
    from backend.engine.job_store import JobStore
    api = _client(harness)
    _project(api, harness['source'])
    seen = []
    import backend.engine.local_training_worker as worker
    original = worker.run_owned_training
    def spy(record, callback, **kwargs):
        seen.append(JobStore().attempts(record.job_id))
        return original(record, callback, **kwargs)
    worker.run_owned_training = spy
    try:
        harness['release'].set()
        started = _start(api, harness['source'], 'attempted')
        assert started.status_code == 200, started.text
        job_id = started.json()['job_id']
        _wait_finished(harness['manager'], job_id)
    finally:
        worker.run_owned_training = original
    assert len(seen) == 1 and len(seen[0]) == 1, 'one attempt exists before the worker runs'
    ledger = JobStore()
    for _ in range(100):
        if ledger.get(job_id).state == 'completed':
            break
        threading.Event().wait(0.05)
    assert ledger.get(job_id).state == 'completed'
    assert [row['event'] for row in ledger.events(job_id)] == ['submit', 'attempt', 'start', 'complete']


def routes_training_context(value):
    from backend.contracts.context import ProjectContext
    return ProjectContext.model_validate(value)


# Training engine runs (independent of the desktop's project state) use the same ledger.

def test_engine_runs_reserve_their_key_before_writing_and_record_the_attempt_and_end(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    from backend.engine import training_engine as engine
    from backend.engine.job_store import JobConflict, ledger
    from backend.tests.test_training_engine import fixture
    source, _labels, rows = fixture(tmp_path)
    output = tmp_path / 'engine'
    engine.prepare(task='rotation', source_dataset_path=str(source), output_dir=str(output), labels={'samples': rows})
    first = engine.create_run(output_dir=str(output), mode='quick', epochs_per_trial=1, config={'width': 8, 'image_size': 32},
                              idempotency_key='engine-1')
    again = engine.create_run(output_dir=str(output), mode='quick', epochs_per_trial=1, config={'image_size': 32, 'width': 8},
                              idempotency_key='engine-1')
    assert again['run_id'] == first['run_id']
    assert len(list((output / 'runs').iterdir())) == 1, 'a retried request writes no second run'
    with pytest.raises(JobConflict):
        engine.create_run(output_dir=str(output), mode='quick', epochs_per_trial=1, config={'width': 16, 'image_size': 32},
                          idempotency_key='engine-1')
    run = engine.execute_run(output, first['run_id'])
    assert run['status'] == 'completed', run
    store = ledger()
    assert [row['event'] for row in store.events(first['run_id'])] == ['submit', 'attempt', 'start', 'complete']
    assert len(store.attempts(first['run_id'])) == 1


def test_engine_cancel_records_a_durable_intent(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    from backend.engine import training_engine as engine
    from backend.engine.job_store import ledger
    from backend.tests.test_training_engine import fixture
    source, _labels, rows = fixture(tmp_path)
    output = tmp_path / 'engine'
    engine.prepare(task='rotation', source_dataset_path=str(source), output_dir=str(output), labels={'samples': rows})
    queued = engine.create_run(output_dir=str(output), mode='quick', epochs_per_trial=1, idempotency_key='engine-cancel')
    assert engine.cancel_run(output, queued['run_id'])['status'] == 'cancelled'
    store = ledger()
    assert store.cancel_intent(queued['run_id']) is not None
    assert store.get(queued['run_id']).state == 'aborted'


def test_a_stop_request_stores_a_durable_cancel_intent_for_the_requesting_actor(harness):
    from backend.api import routes_training
    api = _client(harness)
    _project(api, harness['source'])
    started = _start(api, harness['source'], 'to-stop')
    assert started.status_code == 200, started.text
    job_id = started.json()['job_id']
    actor = api.get('/api/context').json()['project_context']['actor_id']
    stopped = api.post('/api/training/stop', json={'job_id': job_id})
    assert stopped.status_code == 200, stopped.text
    intent = routes_training.job_ledger().cancel_intent(job_id)
    assert intent is not None and intent['actor_id'] == actor


def test_a_finished_job_links_its_receipt_as_an_artifact_reference(harness):
    import hashlib as _hashlib
    from backend.api import routes_training
    from backend.contracts.context import ArtifactRef
    api = _client(harness)
    project = _project(api, harness['source'])
    harness['release'].set()
    started = _start(api, harness['source'], 'with-receipt')
    assert started.status_code == 200, started.text
    job_id = started.json()['job_id']
    _wait_finished(harness['manager'], job_id)
    store = routes_training.job_ledger()
    for _ in range(100):
        links = store.artifacts(job_id)
        if links:
            break
        threading.Event().wait(0.05)
    receipt = Path(project['models_dir']) / job_id / 'job_receipt.json'
    assert [row['role'] for row in links] == ['receipt']
    assert links[0]['sha256'] == _hashlib.sha256(receipt.read_bytes()).hexdigest()
    ref = ArtifactRef(id=links[0]['artifact_id'], revision=links[0]['revision'], sha256=links[0]['sha256'])
    response = api.get(f"/api/context/artifacts/{ref.id}", params={'revision': ref.revision, 'sha256': ref.sha256})
    assert response.status_code == 200, response.text


def test_the_explicit_migration_command_reads_legacy_roots_and_keeps_them(tmp_path):
    import os
    import subprocess
    import sys
    from backend.engine.job_store import legacy_roots
    # Without --root the command reads the canonical folder and both legacy home folders (paths only, no I/O here).
    assert Path.home() / '.modu_vision' / 'local_jobs' in legacy_roots()
    assert Path.home() / '.modu-vision' / 'remote_jobs' in legacy_roots()
    user_data = tmp_path / 'user_data'
    legacy = tmp_path / 'legacy' / '.modu_vision' / 'local_jobs'
    legacy.mkdir(parents=True)
    journal = legacy / 'job_2_legacy.json'
    journal.write_text(json.dumps({'job_id': 'job_2_legacy', 'status': 'failed', 'output_dir': str(tmp_path / 'm' / 'job_2_legacy')}))
    before = hashlib.sha256(journal.read_bytes()).hexdigest()
    env = {**os.environ, 'VISION_AI_STUDIO_USER_DATA_DIR': str(user_data), 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': str(Path(__file__).resolve().parents[2])}
    result = subprocess.run([sys.executable, '-m', 'backend.engine.job_store', 'migrate-legacy', '--receipt', str(tmp_path / 'receipt.json'),
                             '--root', str(legacy)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['migrated'] == 1
    assert hashlib.sha256(journal.read_bytes()).hexdigest() == before, 'the legacy journal is left unchanged'
    from backend.engine.job_store import JobStore
    assert JobStore(user_data / 'jobs' / 'ledger.sqlite3').get('job_2_legacy').state == 'failed'


def _scoped_job_with_receipt(harness, api, project, job_id, **overrides):
    """A job reserved the way the route reserves it, with a written receipt and no live request."""
    from backend.api import routes_training
    context = api.get('/api/context').json()['project_context']
    registry = harness['app'].state.context_registry
    scope = {'registry_root': str(registry.root), 'project_dir': str(Path(project['project_dir']).resolve()),
             'project_key': registry.project_key(routes_training_context(context))}
    scope.update(overrides)
    output = Path(project['models_dir']) / job_id
    output.mkdir(parents=True)
    store = routes_training.job_ledger()
    store.submit(context, scope['project_key'], 'training', {'task': 'classification'}, None, job_id=job_id,
                 output_dir=str(output), registry_root=scope['registry_root'], project_dir=scope['project_dir'])
    (output / 'job_receipt.json').write_text(json.dumps({'job_id': job_id, 'status': 'completed'}))
    ref = store.get(job_id)
    store.transition(job_id, ref.revision, 'start')
    return store, context


def test_a_recovered_job_registers_its_receipt_in_the_scope_verified_at_submission(harness):
    from backend.api import routes_training
    api = _client(harness)
    first = _project(api, harness['source'])
    store, first_context = _scoped_job_with_receipt(harness, api, first, 'job_3_recovered')
    second = api.post('/api/project/create', json={'name': 'Selected later'}).json()  # the global selection moves to B
    second_context = api.get('/api/context').json()['project_context']
    assert second_context['project_id'] == second['id'] != first['id']
    routes_training._existing_ledger_link('job_3_recovered').finished('completed')  # no request: restart recovery
    links = store.artifacts('job_3_recovered')
    assert [row['role'] for row in links] == ['receipt']
    query = {'revision': links[0]['revision'], 'sha256': links[0]['sha256']}
    in_first = api.get(f"/api/context/artifacts/{links[0]['artifact_id']}", params=query,
                       headers={'X-Vision-Context': json.dumps(first_context)})
    in_second = api.get(f"/api/context/artifacts/{links[0]['artifact_id']}", params=query,
                        headers={'X-Vision-Context': json.dumps(second_context)})
    assert in_first.status_code == 200, in_first.text
    assert in_second.status_code == 404, 'never registered in the project selected later'


@pytest.mark.parametrize('broken', ['missing_registry', 'changed_namespace'])
def test_an_unresolvable_submission_scope_is_a_retained_failure_not_a_new_reference(harness, tmp_path, broken):
    from backend.api import routes_training
    api = _client(harness)
    project = _project(api, harness['source'])
    override = ({'registry_root': str(tmp_path / 'no-registry-here')} if broken == 'missing_registry'
                else {'project_key': 'not-the-recorded-namespace'})
    store, _context = _scoped_job_with_receipt(harness, api, project, f'job_4_{broken}', **override)
    routes_training._existing_ledger_link(f'job_4_{broken}').finished('completed')
    assert store.artifacts(f'job_4_{broken}') == []
    failures = [row for row in store.events(f'job_4_{broken}') if row['event'] == 'receipt_link_failed']
    assert len(failures) == 1 and failures[0]['payload']['reason']
    assert store.get(f'job_4_{broken}').state == 'completed', 'the job result itself is kept'
    assert not (tmp_path / 'no-registry-here').exists(), 'no registry is created to hide the failure'


@pytest.fixture
def engine_http(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    from backend.tests.test_training_engine import fixture
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert api.post('/api/project/create', json={'name': 'Engine'}).status_code == 200
    source, _labels, rows = fixture(tmp_path)
    output = tmp_path / 'engine'
    prepared = api.post('/api/engine/prepare', json={'task': 'rotation', 'source_dataset_path': str(source),
                                                     'output_dir': str(output), 'labels': {'samples': rows}})
    assert prepared.status_code == 200, prepared.text
    return api, output


def _engine_train(api, output, key, background, config=None):
    return api.post('/api/engine/train', headers={'Idempotency-Key': key}, json={
        'output_dir': str(output), 'mode': 'quick', 'epochs_per_trial': 1,
        'config': config or {'width': 8, 'image_size': 32}, 'background': background})


def test_the_engine_http_route_replays_the_reserved_run_and_refuses_a_different_spec(engine_http):
    from backend.engine.job_store import ledger
    api, output = engine_http
    first = _engine_train(api, output, 'engine-http-1', background=False)
    assert first.status_code == 200 and first.json()['status'] == 'completed', first.text
    again = _engine_train(api, output, 'engine-http-1', background=False)
    assert again.status_code == 200 and again.json()['run_id'] == first.json()['run_id'], again.text
    conflict = _engine_train(api, output, 'engine-http-1', background=False, config={'width': 16, 'image_size': 32})
    assert conflict.status_code == 409, conflict.text
    assert len(list((output / 'runs').iterdir())) == 1
    assert len(ledger().attempts(first.json()['run_id'])) == 1, 'the replay never executes the run again'


def test_concurrent_engine_http_submissions_with_one_key_start_one_run(engine_http):
    from backend.engine.job_store import ledger
    api, output = engine_http
    responses = []
    threads = [threading.Thread(target=lambda: responses.append(_engine_train(api, output, 'engine-http-2', background=True)))
               for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert [response.status_code for response in responses] == [200, 200], [response.text for response in responses]
    run_ids = {response.json()['run_id'] for response in responses}
    assert len(run_ids) == 1 and len(list((output / 'runs').iterdir())) == 1
    run_id = run_ids.pop()
    for _ in range(600):
        status = api.get('/api/engine/status', params={'output_dir': str(output), 'run_id': run_id}).json()
        if status['status'] not in ('queued', 'running', 'stopping'):
            break
        threading.Event().wait(0.1)
    assert status['status'] == 'completed', status
    assert len(ledger().attempts(run_id)) == 1


class _BrokenLedger:
    """A ledger whose database fails, as on a read-only folder, a disk error or a held lock."""
    def __getattr__(self, name):
        import sqlite3
        def fail(*_args, **_kwargs):
            raise sqlite3.DatabaseError('file is not a database')
        return fail


def test_a_ledger_database_error_never_blocks_job_bookkeeping_leases_recovery_or_shutdown(harness):
    import sqlite3
    from backend.api import routes_training
    manager = harness['manager']
    harness['release'].set()
    broken = routes_training.TrainingLedgerLink(_BrokenLedger(), 'job_broken_ledger')
    broken.launched = lambda executor: None  # launching itself refuses without a ledger; here the end is under test
    record = manager.start_job('job_broken_ledger', 'classification', str(harness['source']), str(harness['tmp'] / 'out-broken'),
                               device='cpu', ledger=broken)
    record.thread.join(30)
    assert record.status == 'completed', record.error
    assert not [row for row in manager._leases.list() if row['job_id'] == 'job_broken_ledger'], 'the lease is released'
    detaching = routes_training.TrainingJobManager()
    detaching._jobs['job_live'] = routes_training.JobRecord('job_live', 'classification', 'fast', '', '', 'running', ledger=broken)
    detaching.detach_all_for_shutdown()  # must not raise: the SIGTERM handler still sets should_exit
    finished = []
    recovered = routes_training.JobRecord('job_recovered_broken', 'classification', 'fast', '', str(harness['tmp'] / 'out-r'), 'running',
                                          ledger=broken)
    class Leases:
        def heartbeat(self, _job): pass
        def release(self, _job, terminal=False): finished.append('released')
        def mark_uncertain(self, _job): pass
    manager.restore_local_job(recovered, runner=lambda callback: {'status': 'completed'}, leases=Leases())
    assert recovered.thread is not None, 'the recovered monitor starts despite the ledger error'
    recovered.thread.join(10)
    assert recovered.status == 'completed' and finished == ['released']
    original = routes_training.job_ledger
    routes_training.job_ledger = lambda: _BrokenLedger()
    try:
        assert routes_training._existing_ledger_link('job_unknown') is None
    finally:
        routes_training.job_ledger = original


@pytest.mark.parametrize('recovered_state', ['queued', 'launched'])
def test_a_remote_job_resumed_after_a_restart_records_one_attempt(harness, recovered_state):
    """Startup relaunch of a queued remote job records its first attempt; a launched one reattaches under its token."""
    from backend.api import routes_training
    store = routes_training.job_ledger()
    job_id = f'job_remote_{recovered_state}'
    store.submit(_context(), 'key-remote', 'training', SPEC, None, job_id=job_id, output_dir=str(harness['tmp'] / job_id))
    if recovered_state == 'queued':
        store.transition(job_id, store.get(job_id).revision, 'queue')
    else:
        attempt = store.begin_attempt(job_id, store.get(job_id).revision, 'remote', 'boot-before', 1234)
        store.transition(job_id, store.get(job_id).revision, 'start', fencing_token=attempt.fencing_token)
        store.transition(job_id, store.get(job_id).revision, 'detach', fencing_token=attempt.fencing_token)
    manager = routes_training.TrainingJobManager()
    record = manager.start_remote_job(job_id=job_id, task='classification', dataset_path=str(harness['source']),
                                      output_dir=str(harness['tmp'] / job_id), remote_profile_id='fixture-remote',
                                      remote_runner=lambda _record: {'status': 'completed'}, recovery_state=recovered_state)
    record.thread.join(30)
    # This synthetic job was submitted without a project registry, so its receipt link is a retained failure.
    events = [row['event'] for row in store.events(job_id) if row['event'] != 'receipt_link_failed']
    assert len(store.attempts(job_id)) == 1, events
    assert store.get(job_id).state == 'completed', events
    if recovered_state == 'queued':
        assert events[-3:] == ['attempt', 'start', 'complete'], events
    else:
        assert events[-2:] == ['reattach', 'complete'], events


def test_a_retry_after_the_source_data_changed_still_names_the_original_job(harness):
    api = _client(harness)
    _project(api, harness['source'])
    harness['release'].set()
    first = _start(api, harness['source'], 'retry-after-change')
    assert first.status_code == 200, first.text
    _wait_finished(harness['manager'], first.json()['job_id'])
    Image.new('RGB', (32, 32), (10, 200, 10)).save(harness['source'] / 'train' / 'ok' / 'added.png')
    again = _start(api, harness['source'], 'retry-after-change')
    assert again.status_code == 200, again.text
    assert again.json()['job_id'] == first.json()['job_id'], 'the key identifies the client request, not server-derived state'


def test_legacy_migration_skips_a_journal_whose_job_is_still_active(tmp_path):
    from backend.engine.job_store import JobStore
    root = tmp_path / 'local_jobs'
    root.mkdir()
    journal = root / 'job_5_live.json'
    journal.write_text(json.dumps({'job_id': 'job_5_live', 'status': 'running'}))
    store = JobStore(tmp_path / 'ledger.sqlite3')
    first = store.migrate_legacy([root], tmp_path / 'receipt-1.json')
    assert first['migrated'] == [] and first['skipped'][0]['reason'] == 'job still active; migrate after it ends'
    journal.write_text(json.dumps({'job_id': 'job_5_live', 'status': 'completed'}))
    assert store.migrate_legacy([root], tmp_path / 'receipt-2.json')['migrated'] == ['job_5_live']
    assert store.get('job_5_live').state == 'completed'


def test_two_processes_upgrading_an_older_ledger_at_once_do_not_collide(tmp_path):
    import sqlite3
    from backend.engine import job_store
    path = tmp_path / 'ledger.sqlite3'
    old = sqlite3.connect(path)
    old.executescript(job_store.SCHEMA.replace(', registry_root TEXT, project_dir TEXT);', ');'))
    old.close()
    errors = []
    def open_store():
        try:
            job_store.JobStore(path)
        except Exception as exc:  # noqa: BLE001 - the test reports any failure
            errors.append(exc)
    threads = [threading.Thread(target=open_store) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    columns = {row[1] for row in sqlite3.connect(path).execute('PRAGMA table_info(jobs)')}
    assert {'registry_root', 'project_dir'} <= columns


def test_a_stale_attempt_publishes_nothing_and_the_current_attempt_publishes_its_receipt(harness):
    import sqlite3
    from backend.api import routes_training
    api = _client(harness)
    project = _project(api, harness['source'])
    store, _context = _scoped_job_with_receipt(harness, api, project, 'job_6_fenced')
    store.transition('job_6_fenced', store.get('job_6_fenced').revision, 'detach')
    old = routes_training.TrainingLedgerLink(store, 'job_6_fenced')
    old.launched('local')  # reattaches under the first token
    stale = store.begin_attempt('job_6_fenced', store.get('job_6_fenced').revision, 'local', 'second-boot', 4321)
    receipt = Path(project['models_dir']) / 'job_6_fenced' / 'job_receipt.json'
    receipt.write_text(json.dumps({'worker': 'stale'}))
    old.fencing_token = stale.fencing_token - 1
    old.finished('completed')
    registry = sqlite3.connect(harness['app'].state.context_registry.path)
    published = registry.execute("SELECT COUNT(*) FROM artifacts WHERE relative_path='job_6_fenced/job_receipt.json'").fetchone()[0]
    assert published == 0, 'a stale attempt never publishes into the project registry'
    assert store.artifacts('job_6_fenced') == [] and store.get('job_6_fenced').state != 'completed'
    receipt.write_text(json.dumps({'worker': 'current'}))
    current = routes_training.TrainingLedgerLink(store, 'job_6_fenced')
    current.fencing_token = stale.fencing_token
    current.finished('completed')
    assert store.get('job_6_fenced').state == 'completed'
    assert [row['role'] for row in store.artifacts('job_6_fenced')] == ['receipt']
    assert not [row for row in store.events('job_6_fenced') if row['event'] == 'receipt_link_failed']
    assert not hasattr(store, 'link_artifact'), 'finish() is the only writer of artifact rows'


def test_both_shutdown_paths_record_one_intent_for_a_job_not_yet_launched(harness):
    from backend.api import routes_training
    store = routes_training.job_ledger()
    store.submit(_context(), 'key-a', 'training', SPEC, None, job_id='job_7_unlaunched')
    manager = harness['manager']
    manager.local_execution = 'subprocess'  # owned CLI workers detach on a normal quit
    manager._jobs['job_7_unlaunched'] = routes_training.JobRecord(
        'job_7_unlaunched', 'classification', 'fast', '', '', 'running',
        ledger=routes_training.TrainingLedgerLink(store, 'job_7_unlaunched'))
    manager.detach_all_for_shutdown()  # SIGTERM handler
    manager.detach_all_for_shutdown()  # lifespan shutdown
    assert [row['event'] for row in store.events('job_7_unlaunched')] == ['submit', 'shutdown_intent']
    assert store.get('job_7_unlaunched').state == 'accepted'


def test_a_child_records_only_a_verified_completed_parent_in_its_own_scope(store):
    done = store.submit(_context(), 'key-a', 'training', SPEC, None, job_id='parent-done')
    store.transition('parent-done', done.revision, 'complete')
    store.submit(_context(), 'key-a', 'training', SPEC, None, job_id='parent-running')
    other = store.submit(_context(project='project-b'), 'key-b', 'training', SPEC, None, job_id='parent-elsewhere')
    store.transition('parent-elsewhere', other.revision, 'complete')
    cases = {'parent-done': 'parent-done', 'parent-running': None, 'parent-elsewhere': None, 'legacy-model-only': None}
    for index, (claimed, expected) in enumerate(cases.items()):
        child = store.submit(_context(), 'key-a', 'training', {**SPEC, 'n': index}, None, parent_id=claimed)
        assert store.record(child.id)['parent_id'] == expected, claimed
        submit_event = store.events(child.id)[0]['payload']
        assert submit_event['claimed_parent'] == claimed and submit_event['parent_verified'] is (expected is not None)
