import pytest
from backend.tests.test_service_s3_01_import_job import imports, _context, _source, _spec
from backend.engine.dataset_import_job import DatasetImportJobs, ImportNotAcceptable


def test_same_identity_resume_preserves_progress_and_receipt(imports, tmp_path):
    jobs, store, index = imports
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, _source(tmp_path / 'source')), 'key')
    store.transition(ref.id, ref.revision, 'start')
    jobs._set_progress(ref.id, {'phase': 'reading', 'processed': 2, 'total': 6, 'total_known': True})
    jobs.recover_orphans()
    reopened = DatasetImportJobs(store, index)
    assert reopened.view(ref.id, 'ns:a')['progress']['processed'] == 2
    assert reopened.resume(ref.id, 'ns:a', 'actor-a').id == ref.id
    assert reopened.run(ref.id).state == 'completed'
    view = DatasetImportJobs(store, index).view(ref.id, 'ns:a')
    assert view['operation']['result_ref']['sha256'] == view['result']['revision']['manifest_sha256']
    assert view['operation']['result_ref']['count'] == 6
    assert reopened.resume(ref.id, 'ns:a', 'actor-a').state == 'completed'


def test_resume_refuses_source_drift_and_foreign_actor(imports, tmp_path):
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source))
    jobs.recover_orphans()
    with pytest.raises(KeyError):
        jobs.resume(ref.id, 'ns:a', 'other')
    next(source.rglob('*.png')).write_bytes(b'changed')
    with pytest.raises(ImportNotAcceptable, match='source'):
        jobs.resume(ref.id, 'ns:a', 'actor-a')


def test_source_mutation_during_scan_never_seals(imports, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source))
    original = dataset_index._read_entry
    def changing(path):
        value = original(path)
        path.write_bytes(b'changed after read')
        return value
    monkeypatch.setattr(dataset_index, '_read_entry', changing)
    assert jobs.run(ref.id).state == 'failed'
    assert index.revisions('ns:a') == []


def test_cancelled_job_cannot_resume(imports, tmp_path):
    jobs, store, index = imports
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, _source(tmp_path / 'source')))
    jobs.cancel(ref.id, 'ns:a', 'actor-a')
    jobs.recover_orphans()
    with pytest.raises(ImportNotAcceptable):
        jobs.resume(ref.id, 'ns:a', 'actor-a')


def test_resume_refuses_expected_target_revision_drift(imports, tmp_path):
    jobs, store, index = imports
    source = _source(tmp_path / 'source')
    interrupted = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'first')
    jobs.recover_orphans()
    newer = jobs.submit(_context(), 'ns:a', _spec(tmp_path, source), 'second')
    jobs.run(newer.id)
    receipt = jobs.view(newer.id, 'ns:a')['result']['revision']
    jobs.accept(newer.id, 'ns:a', receipt['revision_id'], None)
    with pytest.raises(ImportNotAcceptable, match='target revision'):
        jobs.resume(interrupted.id, 'ns:a', 'actor-a')


def test_cancel_during_final_import_snapshot_never_seals(imports, tmp_path, monkeypatch):
    jobs, store, index = imports
    ref = jobs.submit(_context(), 'ns:a', _spec(tmp_path, _source(tmp_path / 'source')))
    snapshot = jobs._snapshot
    def cancel_snapshot(spec):
        result = snapshot(spec)
        store.request_cancel(ref.id, 'actor-a', 'cancel during final verification')
        return result
    monkeypatch.setattr(jobs, '_snapshot', cancel_snapshot)
    assert jobs.run(ref.id).state == 'aborted'
    assert index.revisions('ns:a') == []
