import json
from backend.tests.test_service_s3_01_import_api import api

from pathlib import Path
import pytest
from backend.contracts.context import ProjectContext
from backend.engine.job_store import JobStore


def test_backup_recovery_receipt_and_missing_expired_output(tmp_path):
    from backend.engine.data_backup_job import DataBackupJobs
    root = tmp_path / 'project'; root.mkdir()
    project = {'id': 'p', 'name': 'QA', 'project_dir': str(root), 'source_dataset_dir': None}
    (root / 'project.json').write_text(json.dumps(project))
    jobs = DataBackupJobs(JobStore(tmp_path / 'ledger.sqlite3'))
    ctx = ProjectContext(workspace_id='w', project_id='p', actor_id='a', mode='local')
    ref = jobs.submit(ctx, 'ns', project, 'backup')
    assert jobs.submit(ctx, 'ns', project, 'backup').id == ref.id
    jobs.recover_orphans()
    assert jobs.resume(ref.id, 'ns', 'a').id == ref.id
    assert jobs.run(ref.id).state == 'completed'
    view = jobs.view(ref.id, 'ns', 'a')
    assert view['downloadable'] and view['result_ref']['sha256']
    Path(view['result_ref']['path']).unlink()
    assert not jobs.view(ref.id, 'ns', 'a')['downloadable']
    with pytest.raises(KeyError): jobs.view(ref.id, 'ns', 'foreign')


def test_backup_expiry_and_source_drift_refuse_resume(tmp_path):
    from backend.engine.data_backup_job import DataBackupJobs
    from backend.engine.dataset_import_job import ImportNotAcceptable
    root = tmp_path / 'project'; root.mkdir()
    project = {'id': 'p', 'name': 'QA', 'project_dir': str(root), 'source_dataset_dir': None}
    (root / 'project.json').write_text(json.dumps(project))
    store = JobStore(tmp_path / 'ledger.sqlite3'); jobs = DataBackupJobs(store)
    ctx = ProjectContext(workspace_id='w', project_id='p', actor_id='a', mode='local')
    ref = jobs.submit(ctx, 'ns', project, 'backup')
    assert jobs.run(ref.id).state == 'completed'
    operation = store.checkpoint_value(ref.id); operation['expires_at'] = 1; store.checkpoint(ref.id, operation)
    assert not jobs.view(ref.id, 'ns', 'a')['downloadable']
    ref = jobs.submit(ctx, 'ns', project, 'backup-2'); jobs.recover_orphans()
    (root / 'changed.txt').write_text('drift')
    with pytest.raises(ImportNotAcceptable, match='changed'): jobs.resume(ref.id, 'ns', 'a')


def test_backup_cancel_while_writer_finishes_never_offers_download(tmp_path, monkeypatch):
    from backend.engine import data_backup_job
    root = tmp_path / 'project'; root.mkdir()
    project = {'id': 'p', 'name': 'QA', 'project_dir': str(root), 'source_dataset_dir': None}
    (root / 'project.json').write_text(json.dumps(project))
    store = JobStore(tmp_path / 'ledger.sqlite3'); jobs = data_backup_job.DataBackupJobs(store)
    ctx = ProjectContext(workspace_id='w', project_id='p', actor_id='a', mode='local')
    ref = jobs.submit(ctx, 'ns', project)
    writer = data_backup_job.create_archive
    def cancelled(project, destination):
        result = writer(project, destination)
        store.request_cancel(ref.id, 'a', 'cancel')
        return result
    monkeypatch.setattr(data_backup_job, 'create_archive', cancelled)
    assert jobs.run(ref.id).state == 'aborted'
    assert not jobs.view(ref.id, 'ns', 'a')['downloadable']


def test_production_backup_api_and_task_center(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from backend.main import create_app
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user-data'))
    app = create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        project = client.post('/api/project/create', json={'name': 'Backup QA', 'task': 'classification'}).json()
        submitted = client.post('/api/dataset/operations/backups', headers={'Idempotency-Key': 'backup-api'})
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()['job_id']
        for _ in range(100):
            view = client.get(f'/api/dataset/operations/backups/{job_id}').json()
            if view['state'] in ('completed', 'failed'): break
            time.sleep(.05)
        assert view['state'] == 'completed', view
        assert client.post('/api/dataset/operations/backups', headers={'Idempotency-Key': 'backup-api'}).json()['job_id'] == job_id
        tasks = client.get('/api/training-workspace/tasks').json()['tasks']
        assert any(row['job_id'] == job_id and row['kind'] == 'project_backup' for row in tasks)
        assert client.get(f'/api/dataset/operations/backups/{job_id}/download').status_code == 200
        assert client.post(f'/api/dataset/operations/backups/{job_id}/restore').status_code == 409
        client.post('/api/project/create', json={'name': 'Other', 'task': 'classification'})
        assert client.get(f'/api/dataset/operations/backups/{job_id}').status_code == 404


def test_import_api_refuses_foreign_actor(api, monkeypatch):
    from backend.contracts import context as contexts
    from backend.tests.test_service_s3_01_import_api import _finished
    client, project, source = api
    job_id = client.post('/api/dataset/imports', json={'task': 'classification'}).json()['job_id']
    view = _finished(client, job_id)
    original = contexts.get_project_context
    monkeypatch.setattr(contexts, 'get_project_context', lambda request: original(request).model_copy(update={'actor_id': 'foreign'}))
    assert client.get(f'/api/dataset/imports/{job_id}').status_code == 404
    assert client.post(f'/api/dataset/imports/{job_id}/cancel').status_code == 404
    assert client.post(f'/api/dataset/imports/{job_id}/resume').status_code == 404


def test_verified_backup_crash_reconciles_same_output_without_rewriting(tmp_path, monkeypatch):
    from backend.engine.data_backup_job import DataBackupJobs
    root = tmp_path / 'project'; root.mkdir()
    project = {'id': 'p', 'name': 'QA', 'project_dir': str(root), 'source_dataset_dir': None}
    (root / 'project.json').write_text(json.dumps(project))
    store = JobStore(tmp_path / 'ledger.sqlite3'); jobs = DataBackupJobs(store)
    ctx = ProjectContext(workspace_id='w', project_id='p', actor_id='a', mode='local')
    ref = jobs.submit(ctx, 'ns', project)
    original = store.finish
    def lost_completion(job, event, *args, **kwargs):
        if event == 'complete': raise OSError('completion database unavailable')
        return original(job, event, *args, **kwargs)
    monkeypatch.setattr(store, 'finish', lost_completion)
    assert jobs.run(ref.id).state == 'running'
    receipt = store.checkpoint_value(ref.id)['result_ref']
    assert DataBackupJobs(store).recover_orphans()['completed'] == [ref.id]
    assert store.checkpoint_value(ref.id)['result_ref'] == receipt


def test_cancel_during_final_backup_snapshot_never_publishes_result(tmp_path, monkeypatch):
    from backend.engine.data_backup_job import DataBackupJobs
    root = tmp_path / 'project'; root.mkdir()
    project = {'id': 'p', 'name': 'QA', 'project_dir': str(root), 'source_dataset_dir': None}
    (root / 'project.json').write_text(json.dumps(project))
    store = JobStore(tmp_path / 'ledger.sqlite3'); jobs = DataBackupJobs(store)
    ctx = ProjectContext(workspace_id='w', project_id='p', actor_id='a', mode='local')
    ref = jobs.submit(ctx, 'ns', project)
    snapshot = jobs.snapshot; calls = []
    def cancel_snapshot(project):
        result = snapshot(project); calls.append(1)
        if len(calls) == 2: store.request_cancel(ref.id, 'a', 'cancel during final verification')
        return result
    monkeypatch.setattr(jobs, 'snapshot', cancel_snapshot)
    assert jobs.run(ref.id).state == 'aborted'
    assert not jobs.view(ref.id, 'ns', 'a')['downloadable']


def test_backup_submit_replay_does_not_read_changed_source(tmp_path, monkeypatch):
    from backend.engine.data_backup_job import DataBackupJobs
    root = tmp_path / 'project'; root.mkdir()
    project = {'id': 'p', 'name': 'QA', 'project_dir': str(root), 'source_dataset_dir': None}
    (root / 'project.json').write_text(json.dumps(project))
    store = JobStore(tmp_path / 'ledger.sqlite3'); jobs = DataBackupJobs(store)
    ctx = ProjectContext(workspace_id='w', project_id='p', actor_id='a', mode='local')
    ref = jobs.submit(ctx, 'ns', project, 'same')
    monkeypatch.setattr(jobs, 'snapshot', lambda project: (_ for _ in ()).throw(OSError('source unavailable')))
    assert jobs.submit(ctx, 'ns', project, 'same').id == ref.id


def test_backup_snapshot_detects_committed_wal_only_mutation(tmp_path):
    import sqlite3
    from backend.engine.data_backup_job import DataBackupJobs
    root = tmp_path / 'project'; root.mkdir()
    project = {'id': 'p', 'name': 'QA', 'project_dir': str(root), 'source_dataset_dir': None}
    (root / 'project.json').write_text(json.dumps(project))
    with sqlite3.connect(root / 'state.sqlite3') as db:
        db.execute('PRAGMA journal_mode=WAL'); db.execute('CREATE TABLE records(value TEXT)'); db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        before = DataBackupJobs.snapshot(project)
        db.execute("INSERT INTO records VALUES('committed after snapshot')"); db.commit()
        assert DataBackupJobs.snapshot(project) != before
