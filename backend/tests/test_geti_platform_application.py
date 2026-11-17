"""Geti joins the existing production account and project middleware stack."""
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path):
    from backend.main import create_app
    value = create_app(project_dir=str(tmp_path / 'projects'), shared_auth_dir=str(tmp_path / 'accounts'))
    value.state.accounts.bootstrap('platform-owner', secrets.token_urlsafe(24))
    return value


def session(app, username, password):
    client = TestClient(app)
    response = client.post('/api/accounts/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.text
    client.headers['Authorization'] = 'Bearer ' + response.json()['token']
    return client


def test_factory_wires_lazy_platform_under_existing_project_storage(app):
    from backend.platforms.geti.runtime import GetiRuntimeManager
    from backend.platforms.geti.bindings import PlatformStore
    assert isinstance(app.state.geti_runtime, GetiRuntimeManager)
    assert isinstance(app.state.geti_store, PlatformStore)
    assert app.state.geti_store.root.is_relative_to(app.state.project_dir)
    assert app.state.geti_runtime._instances == {}
    assert TestClient(app).get('/api/platform/geti/status').status_code == 401


def test_registered_existing_account_can_read_platform_but_viewer_cannot_start(app, tmp_path):
    password = secrets.token_urlsafe(24)
    owner = app.state.accounts.create_user('project-owner', password, administrator=True)
    client = session(app, 'project-owner', password)
    created = client.post('/api/project/create', json={'name': 'Existing project', 'task': 'classification'})
    assert created.status_code == 200, created.text
    project = created.json()
    viewer = app.state.accounts.create_user('platform-viewer', password)
    app.state.accounts.set_membership(project['id'], viewer['id'], 'viewer', owner['id'])
    member = session(app, 'platform-viewer', password)
    assert member.post('/api/accounts/select-project', json={'project_id': project['id']}).status_code == 200
    result = member.get('/api/platform/geti/status')
    assert result.status_code == 200, result.text
    assert result.json()['project']['name'] == 'Existing project'
    assert result.json()['permissions']['can_train'] is False
    assert member.post('/api/platform/geti/start', json={}).status_code == 403
    assert app.state.geti_runtime._instances == {}


@pytest.mark.parametrize(('path', 'action'), [
    ('/api/platform/geti/start', 'training.execute'),
    ('/api/platform/geti/train', 'training.execute'),
    ('/api/platform/geti/adopt-labels', 'review.approve'),
])
def test_platform_actions_use_existing_permission_vocabulary(path, action):
    from backend.contracts.authentication import permission_action
    assert permission_action(path, 'POST') == action


def test_typed_cli_discovers_platform_and_only_status_is_safe_observation(app):
    from backend.automation.catalog import operation_catalog
    document = operation_catalog(app, group='geti-platform', limit=1000, schemas=True)
    operations = {row['id']: row for row in document['operations']}
    assert operations['GET /api/platform/geti/status']['assistant']['mode'] == 'read'
    assert operations['POST /api/platform/geti/train']['assistant']['mode'] == 'manual'
    described = operation_catalog(app, operation='POST /api/platform/geti/train', schemas=True)
    assert described['operations'][0]['input']['request_body']['required'] is True
    assert app.state.geti_runtime._instances == {}
