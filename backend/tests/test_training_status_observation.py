"""Core polling must preserve evidence, not infer cancellation from a status string."""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def status_job(tmp_path, monkeypatch):
    from backend.main import create_app
    from backend.api import routes_training as routes
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name':'Observed core job','task':'classification'}).json()
    output = Path(project['models_dir']) / 'job_core_observation'
    output.mkdir()
    record = routes.JobRecord(job_id=output.name, task='classification', preset='fast',
        dataset_path=str(tmp_path/'synthetic'), output_dir=str(output), status='aborted')
    monkeypatch.setattr(routes.training_job_manager, 'get_job', lambda job_id: record if job_id == record.job_id else None)
    monkeypatch.setattr(routes.training_job_manager, 'list_jobs', lambda: [record])
    monkeypatch.setattr(routes.training_job_manager._leases, 'list', lambda: [])
    monkeypatch.setattr(routes, '_ledger_readback', lambda *args: [])
    (output/'local_job.json').write_text(json.dumps({'job_id':record.job_id,'cancel_requested_at':1,
        'cancel_acknowledged_at':2,'worker_exit_confirmed':True,'worker_exit_code':0}))
    return client, routes, record

def test_core_polling_carries_the_same_cancel_and_reservation_observation_as_the_list(status_job):
    client, _, record = status_job
    listed = client.get('/api/training/jobs').json()['jobs'][0]
    polled = client.get('/api/training/status', params={'job_id':record.job_id}).json()
    assert polled['observation'] == listed['observation']
    assert polled['observation']['cancel']['complete'] is True

def test_unreadable_reservation_is_unknown_in_core_polling(status_job, monkeypatch):
    client, routes, record = status_job
    monkeypatch.setattr(routes.training_job_manager._leases, 'list', lambda: (_ for _ in ()).throw(OSError('Controlled lease outage')))
    polled = client.get('/api/training/status', params={'job_id':record.job_id}).json()
    assert polled['status'] == 'aborted'
    assert polled['observation']['cancel']['exit_confirmed'] is True
    assert polled['observation']['cancel']['reservation_released'] is None
    assert polled['observation']['cancel']['complete'] is False

def test_core_queue_observation_uses_the_scheduler_project_order(status_job, monkeypatch):
    client, routes, record = status_job
    record.status = record.phase = 'queued'
    monkeypatch.setattr(routes.JobScheduler, 'queue_view', lambda self, project_key: [
        {'job_id':'higher-priority','position':1,'wait_reason':'priority'},
        {'job_id':record.job_id,'position':2,'wait_reason':'device_reserved'}])
    polled = client.get('/api/training/status', params={'job_id':record.job_id}).json()
    assert polled['queue_position'] == 2
    assert polled['wait_reason'] == 'device_reserved'

def test_an_unreadable_queue_has_no_invented_position_or_wait_reason(status_job, monkeypatch):
    client, routes, record = status_job
    record.status = record.phase = 'queued'
    monkeypatch.setattr(routes.JobScheduler, 'queue_view', lambda *args: (_ for _ in ()).throw(OSError('Controlled queue outage')))
    polled = client.get('/api/training/status', params={'job_id':record.job_id}).json()
    assert polled['status'] == 'queued'
    assert polled['queue_position'] is None and polled['wait_reason'] is None
