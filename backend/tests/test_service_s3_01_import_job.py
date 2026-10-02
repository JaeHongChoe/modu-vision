"""S3-01 slice 3: the durable dataset import job (ledger job -> fenced build -> prepared revision -> explicit accept).

Synthetic images in temporary folders, a temporary ledger and index; sources are hashed before and after.
"""
import hashlib

import pytest
from PIL import Image

from backend.contracts.context import ProjectContext


def _context(project='project-a'):
    return ProjectContext(workspace_id='ws-1', project_id=project, actor_id='actor-a', mode='local')


@pytest.fixture
def imports(tmp_path):
    from backend.engine.dataset_import_job import DatasetImportJobs
    from backend.engine.dataset_index import DatasetIndex, index_path
    from backend.engine.job_store import JobStore
    store, index = JobStore(tmp_path / 'ledger.sqlite3'), DatasetIndex(index_path(tmp_path / 'registry'))
    return DatasetImportJobs(store, index), store, index


def _source(root, per_class=3, corrupt=False):
    for label, color in (('ok', 'white'), ('ng', 'black')):
        (root / 'train' / label).mkdir(parents=True)
        for number in range(per_class):
            Image.new('RGB', (16, 16), color).save(root / 'train' / label / f'{number}.png')
    if corrupt:
        (root / 'train' / 'ok' / 'broken.png').write_bytes(b'not an image')
    return root


def _spec(tmp_path, source, **extra):
    from backend.engine.dataset_import_job import ImportSpec
    return ImportSpec(project_root=str(tmp_path / 'project'), source_root=str(source), task='classification', **extra)


def _snapshot(root):
    return {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_an_import_publishes_a_prepared_revision_that_only_an_explicit_accept_activates(imports, tmp_path):
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    before = _snapshot(source)
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'import-1')
    assert jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'import-1').id == ref.id, 'a retried submit is the same job'
    assert jobs.run(ref.id).state == 'completed'
    assert _snapshot(source) == before, 'an import only reads the source'
    view = jobs.view(ref.id, 'ns:a')
    published = view['result']['revision']
    assert published['image_count'] == 6 and published['state'] == 'prepared'
    assert view['progress']['total'] == 6 and view['progress']['processed'] == 6
    assert index.active('ns:a') is None, 'a finished scan activates nothing'
    assert jobs.accept(ref.id, 'ns:a', published['revision_id'], expected_active=None) == published['revision_id']
    assert index.active('ns:a') == published['revision_id']


def test_only_the_jobs_own_revision_of_its_own_project_can_be_accepted(imports, tmp_path):
    from backend.engine.dataset_import_job import ImportNotAcceptable
    from backend.engine.dataset_index import StaleActiveRevision
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    first = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'one')
    second = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source, verify=True), 'two')
    jobs.run(first.id), jobs.run(second.id)
    first_revision = jobs.view(first.id, 'ns:a')['result']['revision']['revision_id']
    second_revision = jobs.view(second.id, 'ns:a')['result']['revision']['revision_id']
    with pytest.raises(ImportNotAcceptable):
        jobs.accept(first.id, 'ns:a', second_revision, expected_active=None)
    with pytest.raises(KeyError):
        jobs.accept(first.id, 'ns:b', first_revision, expected_active=None)  # another project namespace
    jobs.accept(first.id, 'ns:a', first_revision, expected_active=None)
    with pytest.raises(StaleActiveRevision):
        jobs.accept(second.id, 'ns:a', second_revision, expected_active=None)  # a stale view of the active revision


def test_a_cancel_intent_stops_the_build_between_files_and_records_no_revision(imports, tmp_path, monkeypatch):
    from backend.engine import dataset_import_job, dataset_index
    jobs, store, index = imports
    monkeypatch.setattr(dataset_import_job, '_CANCEL_CHECK_SECONDS', 0)
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'cancel')
    real = dataset_index._read_entry
    seen = []

    def cancel_after_first(path):
        seen.append(path)
        if len(seen) == 1:
            jobs.cancel(ref.id, 'ns:a', 'actor-a')  # the user's stop lands while the first file is read
        return real(path)

    monkeypatch.setattr(dataset_index, '_read_entry', cancel_after_first)
    assert jobs.run(ref.id).state == 'aborted'
    assert len(seen) == 1 and index.revisions('ns:a') == []


def test_a_superseded_attempt_neither_completes_the_job_nor_publishes(imports, tmp_path, monkeypatch):
    from backend.engine import dataset_import_job, dataset_index
    jobs, store, index = imports
    monkeypatch.setattr(dataset_import_job, '_HEARTBEAT_SECONDS', 0)
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'stale')
    real = dataset_index._read_entry

    def newer_attempt(path):
        if len(store.attempts(ref.id)) == 1:
            store.begin_attempt(ref.id, store.get(ref.id).revision, 'other-backend', None, None)  # a newer owner
        return real(path)

    monkeypatch.setattr(dataset_index, '_read_entry', newer_attempt)
    assert jobs.run(ref.id).state == 'running', 'the stale run reports nothing'
    assert index.revisions('ns:a') == [] and jobs.view(ref.id, 'ns:a')['progress']['phase'] == 'superseded'


def test_a_failed_build_ends_the_job_failed_with_its_reason(imports, tmp_path):
    jobs, store, index = imports
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, tmp_path / 'missing'), 'fail')
    assert jobs.run(ref.id).state == 'failed'
    assert 'Source folder does not exist' in jobs.view(ref.id, 'ns:a')['result']['error']['message']
    assert index.revisions('ns:a') == []


def test_the_reject_policy_publishes_a_revision_that_cannot_be_accepted(imports, tmp_path):
    from backend.engine.dataset_index import RevisionNotActivatable
    jobs, store, index = imports
    source = _source(tmp_path / 'source', corrupt=True)
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source, invalid_policy='reject'), 'reject')
    jobs.run(ref.id)
    published = jobs.view(ref.id, 'ns:a')['result']['revision']
    assert published['state'] == 'rejected' and published['error_count'] == 1
    with pytest.raises(RevisionNotActivatable):
        jobs.accept(ref.id, 'ns:a', published['revision_id'], expected_active=None)


def test_imports_left_running_by_a_previous_process_end_interrupted_and_are_not_restarted(imports, tmp_path):
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    running = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'orphan')
    ref = store.transition(running.id, running.revision, 'start')
    store.begin_attempt(running.id, ref.revision, 'local-thread', None, 1)  # its process is gone
    waiting = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source, verify=True), 'never-started')
    assert jobs.recover_orphans() == {'completed': [], 'interrupted': sorted([running.id, waiting.id], key=[running.id, waiting.id].index)}
    assert {store.get(job).state for job in (running.id, waiting.id)} == {'interrupted'}
    assert index.revisions('ns:a') == [], 'nothing is rebuilt automatically'


def test_a_crash_after_the_revision_is_sealed_is_recovered_by_running_the_job_again(imports, tmp_path, monkeypatch):
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'crash')
    real_finish = store.finish

    def crash(*args, **kwargs):
        raise SystemExit('the backend died after sealing the revision')

    monkeypatch.setattr(store, 'finish', crash)
    with pytest.raises(SystemExit):
        jobs.run(ref.id)
    sealed = index.revisions('ns:a')
    assert len(sealed) == 1 and store.get(ref.id).state == 'running'
    monkeypatch.setattr(store, 'finish', real_finish)
    assert jobs.run(ref.id).state == 'completed', 'a new attempt of the same job'
    assert jobs.view(ref.id, 'ns:a')['result']['revision']['revision_id'] == sealed[0]['revision_id']
    assert len(index.revisions('ns:a')) == 1, 'no second revision was built'


# --- Freeze-1 review regressions (default timing: no heartbeat or cancel interval is shortened) ---------------
def test_a_superseded_attempt_never_seals_even_between_heartbeats(imports, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'stale-seal')
    real = dataset_index._read_entry

    def newer_attempt(path):
        if len(store.attempts(ref.id)) == 1:
            store.begin_attempt(ref.id, store.get(ref.id).revision, 'other-backend', None, None)
        return real(path)

    monkeypatch.setattr(dataset_index, '_read_entry', newer_attempt)
    assert jobs.run(ref.id).state == 'running', 'the stale attempt reports nothing'
    assert index.revisions('ns:a') == [], 'and seals nothing: ownership is confirmed right before sealing'


def test_a_crash_after_sealing_is_completed_at_startup_with_the_sealed_revision(imports, tmp_path, monkeypatch):
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'startup-crash')
    real_finish = store.finish
    monkeypatch.setattr(store, 'finish', lambda *args, **kwargs: (_ for _ in ()).throw(SystemExit('died after sealing')))
    with pytest.raises(SystemExit):
        jobs.run(ref.id)
    monkeypatch.setattr(store, 'finish', real_finish)
    sealed = index.revisions('ns:a')[0]['revision_id']
    assert jobs.recover_orphans() == {'completed': [ref.id], 'interrupted': []}
    view = jobs.view(ref.id, 'ns:a')
    assert view['state'] == 'completed' and view['result']['revision']['revision_id'] == sealed
    assert jobs.accept(ref.id, 'ns:a', sealed, expected_active=None) == sealed
    assert len(index.revisions('ns:a')) == 1


def test_a_stop_in_the_start_window_is_honoured(imports, tmp_path):
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'early-stop')
    jobs.cancel(ref.id, 'ns:a', 'actor-a')
    assert jobs.run(ref.id).state == 'aborted' and index.revisions('ns:a') == []


def test_a_ledger_error_ends_the_job_failed_instead_of_running_forever(imports, tmp_path, monkeypatch):
    import sqlite3
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    first = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'busy-heartbeat')
    real_heartbeat = store.heartbeat
    calls = []

    def busy(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError('database is locked')
        return real_heartbeat(*args, **kwargs)

    monkeypatch.setattr(store, 'heartbeat', busy)
    assert jobs.run(first.id).state == 'failed'
    assert 'database is locked' in jobs.view(first.id, 'ns:a')['result']['error']['message']
    second = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source, verify=True), 'busy-attempt')
    monkeypatch.setattr(store, 'begin_attempt', lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError('locked')))
    jobs.start(second.id).join(10)
    assert store.get(second.id).state == 'failed', 'a thread failure before the attempt began is recorded'


def test_a_ledger_failure_after_sealing_never_fails_the_job(imports, tmp_path, monkeypatch):
    import sqlite3
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'sealed-then-busy')
    real_finish = store.finish
    calls = []

    def busy_once(*args, **kwargs):
        calls.append(args[1] if len(args) > 1 else kwargs.get('event'))
        if len(calls) == 1:
            raise sqlite3.OperationalError('database is locked')
        return real_finish(*args, **kwargs)

    monkeypatch.setattr(store, 'finish', busy_once)
    assert jobs.run(ref.id).state == 'completed', 'the completion is written again, never replaced by a failure'
    assert calls == ['complete', 'complete']
    second = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source, verify=True), 'sealed-then-down')
    monkeypatch.setattr(store, 'finish', lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError('locked')))
    assert jobs.run(second.id).state == 'running', 'left active for startup recovery'
    monkeypatch.setattr(store, 'finish', real_finish)
    assert jobs.recover_orphans()['completed'] == [second.id]


def test_a_stop_between_start_and_the_attempt_aborts_instead_of_failing(imports, tmp_path, monkeypatch):
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'window')
    real_transition = store.transition

    def then_cancel(job_id, revision, event, *args, **kwargs):
        moved = real_transition(job_id, revision, event, *args, **kwargs)
        if event == 'start':
            jobs.cancel(job_id, 'ns:a', 'actor-a')  # lands after the start, before the attempt
        return moved

    monkeypatch.setattr(store, 'transition', then_cancel)
    assert jobs.run(ref.id).state == 'aborted'


def test_a_stop_recorded_before_the_start_transition_aborts(imports, tmp_path, monkeypatch):
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'before-start')
    real_get = store.get
    once = []

    def cancel_after_read(job_id):
        current = real_get(job_id)
        if not once and current.state == 'accepted':
            once.append(1)
            jobs.cancel(job_id, 'ns:a', 'actor-a')  # lands after run() read the job, before its start transition
        return current

    monkeypatch.setattr(store, 'get', cancel_after_read)
    assert jobs.run(ref.id).state == 'aborted' and index.revisions('ns:a') == []


def test_a_start_that_keeps_conflicting_fails_with_its_reason_instead_of_staying_accepted(imports, tmp_path, monkeypatch):
    from backend.engine.job_store import StaleRevision
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'conflicting')
    real_transition = store.transition

    def always_stale(job_id, revision, event, *args, **kwargs):
        if event == 'start':
            raise StaleRevision('moved again')
        return real_transition(job_id, revision, event, *args, **kwargs)

    monkeypatch.setattr(store, 'transition', always_stale)
    jobs.start(ref.id).join(10)
    assert store.get(ref.id).state == 'failed'


def test_the_failure_is_recorded_even_when_its_first_write_meets_a_moved_revision(imports, tmp_path, monkeypatch):
    from backend.engine.job_store import StaleRevision
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'fail-write')
    real_transition = store.transition
    fails = []

    def stale_then_real(job_id, revision, event, *args, **kwargs):
        if event == 'start':
            raise StaleRevision('moved again')
        if event == 'fail' and not fails:
            fails.append(revision)
            raise StaleRevision('a cancel intent moved the revision')
        return real_transition(job_id, revision, event, *args, **kwargs)

    monkeypatch.setattr(store, 'transition', stale_then_real)
    jobs.start(ref.id).join(10)
    assert fails and store.get(ref.id).state == 'failed'
