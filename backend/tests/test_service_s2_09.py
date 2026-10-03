"""S2-09: every model family reports a job the app no longer runs the same way.

A job whose process ended with the app (a restart) is interrupted, not failed, in every family: no candidate was
registered and the same settings can run again. Rotated detection used to call it failed.
"""
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(project_dir=str(tmp_path / 'workspaces'))
    return TestClient(app, headers={'X-Vision-Token': app.state.api_token})


@pytest.mark.parametrize('left', ['running', 'stopping'])
def test_a_rotated_job_left_running_by_a_restart_reads_as_interrupted_like_every_family(tmp_path, left):
    client = _client(tmp_path)
    project = client.post('/api/project/create', json={'name': 'Interrupted rotated job', 'task': 'detection'})
    assert project.status_code == 200, project.text
    job_id = uuid.uuid4().hex
    directory = Path(project.json()['models_dir']) / 'rotated_detection' / job_id
    directory.mkdir(parents=True)
    saved = {'job_id': job_id, 'status': left, 'epochs_completed': 2, 'total_epochs': 5, 'result': None}
    (directory / 'job_state.json').write_text(json.dumps(saved), encoding='utf-8')
    row = client.get(f'/api/rotated-detection/jobs/{job_id}')
    assert row.status_code == 200, row.text
    assert row.json()['status'] == 'interrupted'
    assert row.json()['error'] == 'Application stopped before training completed'
    assert row.json()['epochs_completed'] == 2, 'the progress it reached is kept'
    listed = [item for item in client.get('/api/rotated-detection/jobs').json()['jobs'] if item['job_id'] == job_id]
    assert [item['status'] for item in listed] == ['interrupted']
    assert json.loads((directory / 'job_state.json').read_text(encoding='utf-8')) == saved, 'reading never rewrites the record'


def _server_job(tmp_path, monkeypatch, outcomes):
    """A server job whose runner is a fake (nothing is launched or contacted); each run returns the next outcome."""
    from backend.api.routes_training import TrainingJobManager
    from backend.remote.profiles import ComputeProfile
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    manager, runs = TrainingJobManager(), []

    def runner(record):
        runs.append(record.status)
        return {'status': outcomes[min(len(runs), len(outcomes)) - 1]}

    profile = ComputeProfile(id='server', name='server', ssh_target='operator@gpu-host', ssh_port=22, remote_root='/srv/modu-vision-test/server',
                             runtime_kind='python', runtime_value='python3', gpu_selector='0')
    record = manager.start_remote_job(job_id='job_server', task='segmentation', dataset_path=str(tmp_path / 'data'),
                                      output_dir=str(tmp_path / 'job_server'), remote_profile_id=profile.id, profile=profile,
                                      remote_runner=runner, launch_spec={'preparation': 'none', 'config_overrides': {}, 'device': None})
    record.thread.join(timeout=5)
    assert record.status == 'disconnected'
    return manager, record, runs


def test_cancelling_a_disconnected_server_job_delivers_the_stop_by_observing_it_again(tmp_path, monkeypatch):
    """The workbench offers cancel for a disconnected server job; the stop must reach the server, not leave the job
    stopping with nothing watching it."""
    import types
    from backend.api import routes_compute
    manager, record, runs = _server_job(tmp_path, monkeypatch, ['disconnected', 'aborted'])
    monkeypatch.setattr(routes_compute, '_job', lambda job_id, request: (record, manager))
    request = types.SimpleNamespace(state=types.SimpleNamespace())
    routes_compute.cancel_job('job_server', request)
    record.thread.join(timeout=5)
    assert runs == ['running', 'stopping'], 'the server was observed again with the stop pending'
    assert record.status == 'aborted'


def test_a_server_job_left_stopping_while_disconnected_can_be_reconnected(tmp_path, monkeypatch):
    import types
    from backend.api import routes_compute
    manager, record, runs = _server_job(tmp_path, monkeypatch, ['disconnected', 'aborted'])
    assert manager.abort_job('job_server') is True and record.status == 'stopping'  # a stop recorded without a reconnect
    monkeypatch.setattr(routes_compute, '_job', lambda job_id, request: (record, manager))
    routes_compute.reconnect_job('job_server', types.SimpleNamespace(state=types.SimpleNamespace()))
    record.thread.join(timeout=5)
    assert record.status == 'aborted'


def test_a_reconnect_the_job_manager_refuses_is_reported_not_accepted(tmp_path, monkeypatch):
    """A server job whose runner is gone (the app restarted) cannot be observed again; the route says so."""
    import types
    from fastapi import HTTPException
    from backend.api import routes_compute
    manager, record, runs = _server_job(tmp_path, monkeypatch, ['disconnected'])
    record.remote_runner = None
    monkeypatch.setattr(routes_compute, '_job', lambda job_id, request: (record, manager))
    with pytest.raises(HTTPException) as refused:
        routes_compute.reconnect_job('job_server', types.SimpleNamespace(state=types.SimpleNamespace()))
    assert refused.value.status_code == 409 and record.status == 'disconnected'
