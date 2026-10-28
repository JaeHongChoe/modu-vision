"""Authenticated account metadata identifies composition, never helper readiness.

Contract producer: backend/api/routes_accounts.py (direct fast-CI dependency).
"""
from types import SimpleNamespace
import secrets

import pytest


ORIGIN = 'https://localhost:8443'


@pytest.fixture
def account_app(tmp_path, monkeypatch):
    # The production app imports module-level stores. Select only disposable
    # paths before that import, in addition to backend/conftest.py's isolation.
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user-data'))
    monkeypatch.setenv('MODU_BROWSER_ORIGINS', '["https://localhost:8443"]')
    monkeypatch.chdir(tmp_path)
    from backend.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(project_dir=str(tmp_path / 'projects'),
                     shared_auth_dir=str(tmp_path / 'accounts'))
    password = secrets.token_urlsafe(32)
    owner = app.state.accounts.bootstrap('fixture-owner', password)

    def client(transport, username='fixture-owner'):
        value = TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN})
        response = value.post('/api/accounts/login', json={
            'username': username, 'password': password, 'transport': transport})
        assert response.status_code == 200
        session = response.json()
        if transport == 'bearer':
            value.headers['Authorization'] = 'Bearer ' + session['token']
        else:
            assert 'token' not in session
        return value

    return SimpleNamespace(app=app, owner=owner, password=password,
                           client=client, root=tmp_path, TestClient=TestClient)


@pytest.mark.parametrize('transport', ['bearer', 'cookie'])
@pytest.mark.parametrize(('composition', 'expected'), [
    ({}, 'shared'),
    ({'local_host': None}, 'local_host'),
    ({'local_projects': None}, 'local_host'),
    ({'local_host': None, 'local_projects': None}, 'local_host'),
    ({'local_host': False}, 'local_host'),
    ({'local_host': SimpleNamespace(ready=False, closed=True)}, 'local_host'),
    ({'local_projects': SimpleNamespace(ready=False)}, 'local_host'),
])
def test_me_browser_mode_uses_composition_presence_even_when_local_is_unavailable(
        account_app, transport, composition, expected):
    for name, value in composition.items():
        setattr(account_app.app.state, name, value)
    client = account_app.client(transport)
    response = client.get('/api/accounts/me')
    assert response.status_code == 200
    payload = response.json()
    assert payload['browser_project_mode'] == expected
    assert payload['user']['id'] == account_app.owner['id']
    assert payload['user']['administrator'] in (True, 1)
    assert payload['projects'] == []
    assert payload['selected_project_id'] is None
    assert isinstance(payload['workspace_id'], str) and payload['workspace_id']
    assert isinstance(payload['organization_id'], str) and payload['organization_id']
    assert set(payload) == {'user', 'projects', 'selected_project_id',
                            'workspace_id', 'organization_id', 'browser_project_mode'}
    assert 'token' not in payload and 'csrf_token' not in payload


@pytest.mark.parametrize('transport', ['bearer', 'cookie'])
@pytest.mark.parametrize('local_boundary', [False, True])
def test_me_browser_mode_keeps_exact_memberships_role_and_selected_project(
        account_app, transport, local_boundary):
    accounts = account_app.app.state.accounts
    member = accounts.create_user('fixture-reviewer', account_app.password,
                                  administrator=False)
    own = account_app.root / 'owned-project'
    other = account_app.root / 'foreign-project'
    own.mkdir()
    other.mkdir()
    accounts.register_project('owned-fixture', str(own), account_app.owner['id'])
    accounts.register_project('foreign-fixture', str(other), account_app.owner['id'])
    accounts.set_membership('owned-fixture', member['id'], 'reviewer',
                            account_app.owner['id'])
    if local_boundary:
        # A configured but absent controller cannot downgrade the account view.
        account_app.app.state.local_host = None
    client = account_app.client(transport, 'fixture-reviewer')
    before = client.get('/api/accounts/me')
    assert before.status_code == 200
    payload = before.json()
    assert payload['browser_project_mode'] == ('local_host' if local_boundary else 'shared')
    assert payload['user']['id'] == member['id']
    assert payload['user']['administrator'] in (False, 0)
    assert payload['projects'] == [
        {'id': 'owned-fixture', 'path': str(own), 'role': 'reviewer'}]
    assert payload['selected_project_id'] is None
    if transport == 'cookie':
        # The read-only DTO neither consumes nor replaces the cookie proof.
        session = accounts.login('fixture-reviewer', account_app.password,
                                 transport='cookie')
        client.cookies.set('vision_session', session['token'])
        headers = {'X-Vision-CSRF': session['csrf_token']}
    else:
        headers = {}
    assert client.post('/api/accounts/select-project',
                       json={'project_id': 'foreign-fixture'}, headers=headers).status_code == 403
    assert client.post('/api/accounts/select-project',
                       json={'project_id': 'owned-fixture'}, headers=headers).status_code == 200
    selected = client.get('/api/accounts/me').json()
    assert selected['selected_project_id'] == 'owned-fixture'
    assert selected['projects'] == payload['projects']
    assert selected['browser_project_mode'] == payload['browser_project_mode']


@pytest.mark.parametrize('composition', [{}, {'local_host': None}, {'local_projects': None}])
def test_me_browser_mode_does_not_make_account_metadata_public(account_app, composition):
    for name, value in composition.items():
        setattr(account_app.app.state, name, value)
    response = account_app.TestClient(account_app.app, base_url=ORIGIN).get('/api/accounts/me')
    assert response.status_code == 401
    assert 'browser_project_mode' not in response.json()
