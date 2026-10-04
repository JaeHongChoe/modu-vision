"""Production start route scheduling; all stores are private synthetic CPU fixtures.

Managers/probes are observed at launch without creating a worker or contacting a server.
Existing owner cancellation is exercised separately by service_s1_03 and the actual browser CPU test.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def launch(tmp_path, monkeypatch):
    from backend.main import create_app
    from backend.api import routes_training as training
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name': 'Budget contract', 'task': 'classification'}).json()
    source = tmp_path / 'source'
    for split in ('train', 'val'):
        for label, color in (('good', 'white'), ('defect', 'black')):
            folder = source / split / label
            folder.mkdir(parents=True)
            Image.new('RGB', (32, 32), color).save(folder / 'part.png')
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    profile = {'id': 'synthetic-server', 'name': 'Synthetic', 'ssh_target': 'fixture.invalid', 'ssh_port': 22,
               'remote_root': '/srv/modu-vision-budget-fixture', 'runtime_kind': 'python', 'runtime_value': 'python3'}
    assert client.post('/api/compute/profiles', json=profile).status_code == 201
    monkeypatch.setattr('backend.remote.ssh_transport.SSHTransport.probe', lambda *args: {'ready': True})
    monkeypatch.setattr('backend.remote.ssh_transport.require_training_runtime', lambda *args, **kwargs: None)
    calls = []

    def capture(**kwargs):
        ledger = kwargs['ledger']
        calls.append({'budget': json.loads(ledger.store.record(kwargs['job_id']).get('budget_json') or '{}'),
                      'arguments': kwargs})
        return SimpleNamespace(status='running', phase='running')

    monkeypatch.setattr(training.training_job_manager, 'start_job', capture)
    monkeypatch.setattr(training.training_job_manager, 'start_remote_job', capture)
    body = {'task': 'classification', 'dataset_path': str(source), 'device': 'cpu',
            'config_overrides': {'backbone': 'resnet18', 'image_size': 32}}
    return client, project, calls, body


@pytest.mark.parametrize('remote', [False, True])
def test_budget_is_durable_before_local_or_remote_launch(launch, remote):
    client, _, calls, body = launch
    if remote:
        body['compute_profile_id'] = 'synthetic-server'
    response = client.post('/api/training/start', json={**body, 'max_runtime_s': 90})
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]['budget'] == {'max_runtime_s': 90, 'max_attempts': 1}


@pytest.mark.parametrize('remote', [False, True])
def test_budget_storage_failure_prevents_any_worker_launch(launch, monkeypatch, remote):
    from backend.engine.job_store import JobStore
    client, project, calls, body = launch
    if remote:
        body['compute_profile_id'] = 'synthetic-server'
    monkeypatch.setattr(JobStore, 'set_budget', lambda *args: (_ for _ in ()).throw(OSError('Controlled ledger outage')))
    response = client.post('/api/training/start', json={**body, 'max_runtime_s': 60})
    assert response.status_code == 503, response.text
    assert calls == []
    assert not list(Path(project['models_dir']).glob('job_*'))


@pytest.mark.parametrize('options', [{'queue': False}, {'priority': 1}])
def test_remote_unsupported_queue_options_are_refused_before_probe_or_launch(launch, monkeypatch, options):
    client, _, calls, body = launch
    monkeypatch.setattr('backend.remote.ssh_transport.SSHTransport.probe', lambda *args: (_ for _ in ()).throw(AssertionError('No remote probe for unsupported scheduling')))
    response = client.post('/api/training/start', json={**body, 'compute_profile_id': 'synthetic-server', **options})
    assert response.status_code == 422, response.text
    assert 'queue' in str(response.json()).lower()
    assert calls == []
