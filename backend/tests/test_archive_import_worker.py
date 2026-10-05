"""ZIP work belongs to the fenced import attempt, with durable progress/cancel/restart evidence."""
import hashlib
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.tests.test_service_s3_01_import_api import _uploaded, _zip_bytes, _png_bytes
from backend.engine.dataset_import_job import DatasetImportJobs, ImportSpec, ImportNotAcceptable


@pytest.fixture
def archive_jobs(tmp_path):
    from backend.contracts.context import ContextRegistry
    from backend.engine.artifact_store import ArtifactStore
    from backend.engine.dataset_index import DatasetIndex
    from backend.engine.job_store import JobStore
    project = tmp_path / 'project'
    (project / 'annotations').mkdir(parents=True)
    registry = ContextRegistry(tmp_path / 'registry')
    context = registry.context({'id': 'archive-project', 'project_dir': str(project)}, None)
    storage = ArtifactStore(registry)
    data = _zip_bytes([('OK/a.png', _png_bytes('white')), ('NG/b.png', _png_bytes('black'))])
    artifact = storage.put_verified(context, io.BytesIO(data), hashlib.sha256(data).hexdigest(), len(data))
    store, index = JobStore(tmp_path / 'jobs.sqlite3'), DatasetIndex(tmp_path / 'index.sqlite3')
    jobs = DatasetImportJobs(store, index, artifact_store=storage)
    spec = ImportSpec(project_root=str(project), source_root=str(project / 'dataset_imports' / artifact.sha256),
                      task='classification', artifact=artifact.model_dump(), annotation_root=str(project / 'annotations'),
                      archive_pending=True)
    return jobs, store, index, context, registry.project_key(context), spec


def test_submission_and_idempotent_retry_do_no_extraction_or_extracted_source_snapshot(tmp_path, monkeypatch):
    from backend.engine import dataset_archive_input
    from backend.engine.job_store import ledger
    import backend.main as main
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user-data'))
    extraction, snapshots = [], []
    original_extract, original_snapshot = dataset_archive_input.extract_dataset_archive, DatasetImportJobs._snapshot
    def extract(*args, **kwargs):
        extraction.append(True)
        return original_extract(*args, **kwargs)
    def snapshot(spec, **kwargs):
        snapshots.append(spec.source_root)
        return original_snapshot(spec, **kwargs)
    monkeypatch.setattr(dataset_archive_input, 'extract_dataset_archive', extract)
    monkeypatch.setattr(DatasetImportJobs, '_snapshot', staticmethod(snapshot))
    monkeypatch.setattr(DatasetImportJobs, 'start', lambda *args, **kwargs: None)
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        project = client.post('/api/project/create', json={'name': 'Queued archive', 'task': 'classification'}).json()
        artifact = _uploaded(client, _zip_bytes([('OK/a.png', _png_bytes())]))
        target = str(Path(project['project_dir']) / 'dataset_imports' / artifact['sha256'])
        body = {'task': 'classification', 'artifact': artifact}
        response = client.post('/api/dataset/imports/archive', json=body, headers={'Idempotency-Key': 'queued'})
        assert response.status_code == 200, response.text
        assert extraction == [] and target not in snapshots
        assert response.json()['state'] == 'accepted' and not Path(target).exists()
        job = response.json()['job_id']
        again = client.post('/api/dataset/imports/archive', json=body, headers={'Idempotency-Key': 'queued'})
        assert again.json()['job_id'] == job and again.json()['idempotent_replay'] and extraction == []
        assert json.loads(ledger().record(job)['spec_json'])['archive_pending']
        assert app.state.dataset_imports.run(job).state == 'completed'
        assert extraction == [True] and target in snapshots
        assert client.get('/api/dataset/revisions').json()['active_revision'] is None


def test_archive_extraction_progress_is_durable_and_not_image_progress(archive_jobs, monkeypatch):
    jobs, store, index, context, key, spec = archive_jobs
    ref = jobs.submit(context, key, spec)
    phases = []
    original = jobs._set_progress
    def progress(job, value, *args, **kwargs):
        original(job, value, *args, **kwargs)
        phases.append(store.checkpoint_value(job)['progress'].copy())
    monkeypatch.setattr(jobs, '_set_progress', progress)
    assert jobs.run(ref.id).state == 'completed'
    assert any(p['phase'] == 'extracting' and p.get('unit') == 'byte' and p.get('processed', 0) > 0 for p in phases)
    assert index.active(key) is None
    reopened = DatasetImportJobs(store, index, artifact_store=jobs.artifact_store)
    view = reopened.view(ref.id, key)
    assert view['source']['root'] == spec.source_root and view['operation']['archive_source_snapshot']
    assert view['result']['revision']['image_count'] == 2


@pytest.mark.parametrize('superseded', [False, True])
def test_cancel_or_new_fence_before_extraction_promotion_publishes_no_dataset(archive_jobs, monkeypatch, superseded):
    from backend.engine import dataset_archive_input
    jobs, store, index, context, key, spec = archive_jobs
    ref = jobs.submit(context, key, spec)
    original = dataset_archive_input.extract_dataset_archive
    def extraction(*args, **kwargs):
        before = kwargs['before_publish']
        def stop():
            if superseded:
                store.begin_attempt(ref.id, store.get(ref.id).revision, 'replacement', None, None)
            else:
                jobs.cancel(ref.id, key, context.actor_id)
            before()
        return original(*args, **{**kwargs, 'before_publish': stop})
    monkeypatch.setattr(dataset_archive_input, 'extract_dataset_archive', extraction)
    assert jobs.run(ref.id).state == ('running' if superseded else 'aborted')
    assert not Path(spec.source_root).exists() and index.revisions(key) == []
    assert list(Path(spec.source_root).parent.glob('*.partial')) == []


def test_restart_before_extraction_resumes_same_job_but_released_reference_is_refused(archive_jobs):
    from backend.contracts.context import ArtifactRef
    jobs, store, index, context, key, spec = archive_jobs
    ref = jobs.submit(context, key, spec)
    jobs.recover_orphans()
    restarted = DatasetImportJobs(store, index, artifact_store=jobs.artifact_store)
    assert restarted.view(ref.id, key)['resumable']
    assert restarted.resume(ref.id, key, context.actor_id).id == ref.id
    assert restarted.run(ref.id).state == 'completed'
    second = jobs.submit(context, key, spec)
    jobs.recover_orphans()
    jobs.artifact_store.release(context, ArtifactRef(**spec.artifact))
    with pytest.raises(ImportNotAcceptable, match='artifact|Archive'):
        restarted.resume(second.id, key, context.actor_id)


def test_restart_after_extraction_retains_bound_snapshot_and_refuses_source_drift(archive_jobs, monkeypatch):
    jobs, store, index, context, key, spec = archive_jobs
    ref = jobs.submit(context, key, spec)
    def crash(*args, **kwargs):
        raise KeyboardInterrupt('simulated process loss after extraction checkpoint')
    monkeypatch.setattr(index, 'build_revision', crash)
    with pytest.raises(KeyboardInterrupt):
        jobs.run(ref.id)
    assert store.checkpoint_value(ref.id)['archive_source_snapshot'] and Path(spec.source_root).is_dir()
    jobs.recover_orphans()
    target = Path(spec.source_root) / 'OK' / 'a.png'
    target.write_bytes(b'changed')
    with pytest.raises(ImportNotAcceptable, match='source'):
        DatasetImportJobs(store, index, artifact_store=jobs.artifact_store).resume(ref.id, key, context.actor_id)
    assert index.revisions(key) == []


def test_cancel_during_a_zip_chunk_aborts_and_preserves_owned_source(tmp_path):
    from backend.engine.dataset_archive_input import extract_dataset_archive
    data = _zip_bytes([('OK/a.png', b'x' * (2 * 1024 * 1024))])
    calls = []
    def progress(done, total):
        calls.append((done, total))
    def cancel():
        if calls and calls[-1][0] > 0:
            raise InterruptedError('cancelled after first chunk')
    with pytest.raises(InterruptedError):
        extract_dataset_archive(io.BytesIO(data), tmp_path / 'dataset', check=cancel, progress=progress)
    assert calls[-1] == (1024 * 1024, 2 * 1024 * 1024) and not (tmp_path / 'dataset').exists()
    assert not list(tmp_path.glob('*.partial'))


@pytest.mark.parametrize('failure', [InterruptedError('cancel'), RuntimeError('stale attempt')])
def test_verified_object_copy_preserves_job_check_exception_and_object(archive_jobs, failure):
    from backend.contracts.context import ArtifactRef
    jobs, _store, _index, context, _key, spec = archive_jobs
    ref = ArtifactRef(**spec.artifact)
    def stop():
        raise failure
    with pytest.raises(type(failure)) as raised:
        with jobs.artifact_store.open(context, ref, check=stop):
            pytest.fail('a cancelled verified copy must not yield')
    assert raised.value is failure
    with jobs.artifact_store.open(context, ref) as handle:
        assert hashlib.sha256(handle.read()).hexdigest() == ref.sha256
    assert jobs.artifact_store.reference(context, ref)['sha256'] == ref.sha256


def test_restart_after_extraction_reuses_verified_source_and_same_job(archive_jobs, monkeypatch):
    from backend.engine import dataset_archive_input
    jobs, store, index, context, key, spec = archive_jobs
    ref = jobs.submit(context, key, spec)
    original = index.build_revision
    monkeypatch.setattr(index, 'build_revision', lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        jobs.run(ref.id)
    source = Path(spec.source_root) / 'OK' / 'a.png'
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    snapshot = store.checkpoint_value(ref.id)['archive_source_snapshot']
    jobs.recover_orphans()
    monkeypatch.setattr(index, 'build_revision', original)
    monkeypatch.setattr(dataset_archive_input, 'extract_dataset_archive', lambda *args, **kwargs: pytest.fail('verified extraction must be reused'))
    restarted = DatasetImportJobs(store, index, artifact_store=jobs.artifact_store)
    assert restarted.resume(ref.id, key, context.actor_id).id == ref.id
    assert restarted.run(ref.id).state == 'completed'
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    assert store.checkpoint_value(ref.id)['archive_source_snapshot'] == snapshot
    assert len(index.revisions(key)) == 1 and index.active(key) is None


@pytest.mark.parametrize('change', ['annotation', 'authority', 'reference'])
def test_change_during_archive_indexing_never_seals(archive_jobs, monkeypatch, change):
    from backend.engine import dataset_index
    from backend.contracts.context import ArtifactRef
    jobs, _store, index, context, key, spec = archive_jobs
    allowed = [True]
    def authorize(_context):
        if not allowed[0]:
            raise ImportNotAcceptable('Archive permission changed')
    jobs.authorize_archive = authorize
    ref = jobs.submit(context, key, spec)
    read = dataset_index._read_entry
    def changing(path):
        value = read(path)
        if change == 'annotation':
            (Path(spec.annotation_root) / 'changed.json').write_text('{}')
        elif change == 'authority':
            allowed[0] = False
        else:
            jobs.artifact_store.release(context, ArtifactRef(**spec.artifact))
        return value
    monkeypatch.setattr(dataset_index, '_read_entry', changing)
    assert jobs.run(ref.id).state == 'failed'
    assert index.revisions(key) == [] and index.active(key) is None
    assert (Path(spec.source_root) / 'OK' / 'a.png').read_bytes() == _png_bytes('white')


def test_archive_output_symlink_is_preserved_and_never_followed(archive_jobs, tmp_path):
    jobs, _store, index, context, key, spec = archive_jobs
    outside = tmp_path / 'outside'
    outside.mkdir()
    (Path(spec.project_root) / 'dataset_imports').symlink_to(outside, target_is_directory=True)
    ref = jobs.submit(context, key, spec)
    assert jobs.run(ref.id).state == 'failed'
    assert list(outside.iterdir()) == [] and index.revisions(key) == []
    assert (Path(spec.project_root) / 'dataset_imports').is_symlink()


def test_archive_no_space_is_durable_failure_with_no_output(archive_jobs, monkeypatch):
    from types import SimpleNamespace
    from backend.engine import dataset_archive_input
    jobs, _store, index, context, key, spec = archive_jobs
    monkeypatch.setattr(dataset_archive_input.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    ref = jobs.submit(context, key, spec)
    assert jobs.run(ref.id).state == 'failed'
    assert 'ArchiveNoSpace' in jobs.view(ref.id, key)['result']['error']['message']
    assert not Path(spec.source_root).exists() and index.revisions(key) == []
    assert not list(Path(spec.source_root).parent.glob('*.partial'))


@pytest.mark.parametrize('role', ['labeler', 'trainer', 'reviewer', 'owner', 'viewer', None])
def test_archive_worker_rechecks_existing_label_write_policy(archive_jobs, role):
    from types import SimpleNamespace
    from backend.api.routes_dataset_imports import _configure_archive_jobs
    from backend.engine.shared_accounts import ACTIONS
    jobs, _store, _index, context, _key, _spec = archive_jobs
    decisions = []
    class Accounts:
        def authorize(self, actor, action, project):
            decisions.append((actor, action, project))
            return SimpleNamespace(allowed=role in ACTIONS[action])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(accounts=Accounts())))
    _configure_archive_jobs(jobs, request, jobs.artifact_store)
    if role in ACTIONS['label.write']:
        jobs.authorize_archive(context)
    else:
        with pytest.raises(ImportNotAcceptable):
            jobs.authorize_archive(context)
    assert decisions == [(context.actor_id, 'label.write', context.project_id)]
