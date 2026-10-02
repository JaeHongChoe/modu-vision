"""Factory-level browser transport configuration and desktop compatibility."""
import json
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app


def application(tmp_path):
    return create_app(project_dir=str(tmp_path / 'projects'),
                      shared_auth_dir=str(tmp_path / 'accounts'))


def preflight(client, origin):
    return client.options('/api/accounts/select-project', headers={
        'Origin': origin, 'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type,x-vision-csrf,x-vision-context'})


def test_factory_configures_exact_https_cookie_origin_and_csrf_headers(tmp_path, monkeypatch):
    monkeypatch.setenv('MODU_BROWSER_ORIGINS', json.dumps(['https://browser.test']))
    app = application(tmp_path)
    assert app.state.browser_origins == frozenset({'https://browser.test'})
    bootstrap = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    password = 'browser fixture password 123'
    created = bootstrap.post('/api/accounts/bootstrap', json={
        'username': 'administrator', 'password': password})
    assert created.status_code == 200, created.text
    client = TestClient(app, base_url='https://browser.test',
                        headers={'Origin': 'https://browser.test'})
    cors = preflight(client, 'https://browser.test')
    assert cors.status_code == 200, cors.text
    assert cors.headers['access-control-allow-origin'] == 'https://browser.test'
    assert cors.headers['access-control-allow-credentials'] == 'true'
    login = client.post('/api/accounts/login', json={
        'username': 'administrator', 'password': password, 'transport': 'cookie'})
    assert login.status_code == 200, login.text
    assert 'httponly' in login.headers['set-cookie'].lower()
    assert 'secure' in login.headers['set-cookie'].lower()
    assert client.get('/api/accounts/me').status_code == 200
    assert client.post('/api/accounts/logout').status_code == 403
    assert client.post('/api/accounts/logout', headers={
        'X-Vision-CSRF': login.json()['csrf_token']}).status_code == 200
    assert client.get('/api/accounts/me').status_code == 401
    for forbidden in ('null', 'http://localhost:5173', 'https://foreign.test'):
        rejected = preflight(client, forbidden)
        assert rejected.status_code == 400
        assert 'access-control-allow-origin' not in rejected.headers


@pytest.mark.parametrize('configuration', [None, '', '[]'])
def test_default_transport_keeps_noncredentialed_desktop_origins(tmp_path, monkeypatch, configuration):
    if configuration is None:
        monkeypatch.delenv('MODU_BROWSER_ORIGINS', raising=False)
    else:
        monkeypatch.setenv('MODU_BROWSER_ORIGINS', configuration)
    app = create_app(project_dir=str(tmp_path / 'desktop-projects'))
    client = TestClient(app)
    response = client.options('/api/context', headers={
        'Origin': 'null', 'Access-Control-Request-Method': 'GET',
        'Access-Control-Request-Headers': 'x-vision-token'})
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == 'null'
    assert 'access-control-allow-credentials' not in response.headers
    assert client.get('/api/context').status_code == 401
    created = client.post('/api/project/create', json={'name': 'Desktop fixture'},
                          headers={'X-Vision-Token': app.state.api_token})
    assert created.status_code == 200, created.text
    assert client.get('/api/context', headers={
        'X-Vision-Token': app.state.api_token}).status_code == 200
    assert not getattr(app.state, 'browser_origins', ())


@pytest.mark.parametrize('configuration', [
    'not-json', '{}', '"https://browser.test"', '[1]', '[true]',
    '["null"]', '["*"]', '["http://browser.test"]',
    '["https://browser.test/path"]', '["https://user@browser.test"]',
    '["https://*.test"]', '["https://browser.test?query=1"]',
    '["https://browser.test:invalid"]', '["https://browser.test:70000"]',
    '["https://browser.test:-1"]', '["https://browser.test:"]',
])
def test_invalid_origin_configuration_refuses_startup(tmp_path, monkeypatch, configuration):
    monkeypatch.setenv('MODU_BROWSER_ORIGINS', configuration)
    with pytest.raises(ValueError, match='MODU_BROWSER_ORIGINS'):
        application(tmp_path)


def test_each_factory_captures_its_own_immutable_origin_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('MODU_BROWSER_ORIGINS', '["https://first.test"]')
    first = application(tmp_path / 'first')
    monkeypatch.setenv('MODU_BROWSER_ORIGINS', '["https://second.test"]')
    second = application(tmp_path / 'second')
    assert first.state.browser_origins == frozenset({'https://first.test'})
    assert second.state.browser_origins == frozenset({'https://second.test'})
    assert preflight(TestClient(first), 'https://second.test').status_code == 400
    assert preflight(TestClient(second), 'https://first.test').status_code == 400
