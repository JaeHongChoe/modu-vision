"""Product dispatcher boundaries, using raw scopes where URL clients normalize paths."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

ORIGIN = 'https://localhost:8443'


@pytest.fixture
def renderer(tmp_path):
    root = tmp_path / 'renderer'
    (root / 'assets').mkdir(parents=True)
    (root / 'index.html').write_text('<html>Studio</html>')
    (root / 'assets/app.js').write_text('window.product = true;')
    (root / 'assets/app.css').write_text('body { color: black; }')
    (root / 'secret.key').write_text('secret')
    (root / '.env').write_text('secret')
    (root / 'assets/app.js.map').write_text('secret')
    (root / 'accounts.sqlite').write_text('secret')
    (root / 'source.py').write_text('secret')
    (root / 'secret.json').write_text('secret')
    return root


class API:
    def __init__(self):
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        if scope['type'] == 'lifespan':
            assert (await receive())['type'] == 'lifespan.startup'
            await send({'type': 'lifespan.startup.complete'})
            assert (await receive())['type'] == 'lifespan.shutdown'
            await send({'type': 'lifespan.shutdown.complete'})
        elif scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 4401})
        else:
            await send({'type': 'http.response.start', 'status': 401, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'guarded API'})


def invoke(app, path='/', *, raw=None, method='GET', scheme='https', host=b'localhost:8443', origin=None, extra=(), kind='http'):
    headers = [(b'host', host), *extra]
    if origin is not None:
        headers.append((b'origin', origin.encode()))
    scope = {'type': kind, 'asgi': {'version': '3.0'}, 'path': path,
             'raw_path': raw if raw is not None else path.encode(), 'root_path': '',
             'query_string': b'', 'method': method, 'scheme': scheme,
             'headers': headers, 'server': ('127.0.0.1', 8443), 'client': ('127.0.0.1', 1234)}
    messages = []
    async def send(message):
        messages.append(message)
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    asyncio.run(app(scope, receive, send))
    return scope, messages


def app_for(renderer):
    from backend.local_web_app import LocalWebApp
    api = API()
    return LocalWebApp(api, renderer, ORIGIN), api


def test_static_public_without_login_but_api_health_ws_and_lifespan_are_preserved(renderer):
    app, api = app_for(renderer)
    _, result = invoke(app)
    assert result[0]['status'] == 200
    assert result[1]['body'] == b'<html>Studio</html>'
    for path in ('/api/context', '/api', '/health', '/ws/telemetry'):
        scope, result = invoke(app, path)
        assert result[0]['status'] == 401
        assert api.scopes[-1] is scope
    scope, messages = invoke(app, '/ws/telemetry', kind='websocket', scheme='wss', origin=ORIGIN)
    assert api.scopes[-1] is scope
    assert messages == [{'type': 'websocket.close', 'code': 4401}]
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get('/').status_code == 200
    assert any(scope['type'] == 'lifespan' for scope in api.scopes)


@pytest.mark.parametrize('host,scheme,origin,extra', [
    (b'evil.test', 'https', None, ()),
    (b'localhost', 'https', None, ()),
    (b'127.0.0.1:8443', 'https', None, ()),
    (b'localhost:8443.evil', 'https', None, ()),
    (b'localhost:8443', 'http', None, ((b'x-forwarded-proto', b'https'),)),
    (b'evil.test', 'https', None, ((b'x-forwarded-host', b'localhost:8443'),)),
    (b'localhost:8443', 'https', 'https://foreign.test', ()),
    (b'localhost:8443', 'https', ORIGIN + '/', ()),
    (b'localhost:8443', 'https', 'null', ()),
    (b'localhost:8443', 'https', None, ((b'host', b'evil.test'),)),
    (b'localhost:8443', 'https', ORIGIN, ((b'origin', b'https://evil.test'),)),
])
def test_exact_authority_guards_cannot_be_overridden_by_forwarded_headers(renderer, host, scheme, origin, extra):
    app, api = app_for(renderer)
    _, messages = invoke(app, '/api/context', host=host, scheme=scheme, origin=origin, extra=extra)
    assert messages[0]['status'] == 403
    assert api.scopes == []


def test_forwarded_headers_do_not_change_valid_request_scope(renderer):
    app, api = app_for(renderer)
    scope, messages = invoke(app, '/health', origin=ORIGIN, extra=((b'forwarded', b'host=evil;proto=http'),))
    assert messages[0]['status'] == 401
    assert api.scopes == [scope]


@pytest.mark.parametrize('metadata', [(), ((b'sec-fetch-site', b'none'),),
    ((b'sec-fetch-site', b'same-site'),), ((b'sec-fetch-site', b'cross-site'),),
    ((b'sec-fetch-site', b'same-origin'), (b'sec-fetch-site', b'same-site'))])
@pytest.mark.parametrize('cookies', [((b'cookie', b'vision_session=synthetic'),),
    ((b'cookie', b'other=value; vision_session="synthetic"'),),
    ((b'cookie', b'other=value'), (b'cookie', b'vision_session=synthetic'))])
def test_cookie_api_without_origin_requires_unambiguous_same_origin_metadata(renderer, metadata, cookies):
    app, api = app_for(renderer)
    _, messages = invoke(app, '/api/evaluation/zero-escape-calibrate', extra=(*cookies, *metadata))
    assert messages[0]['status'] == 403
    assert api.scopes == []


def test_cookie_reads_keep_same_origin_metadata_explicit_origin_and_bearer_paths(renderer):
    app, api = app_for(renderer)
    cookie = (b'cookie', b'vision_session=synthetic')
    for extra, origin in (
        ((cookie, (b'sec-fetch-site', b'same-origin')), None),
        ((cookie,), ORIGIN),
        ((cookie, (b'authorization', b'Bearer synthetic')), None),
    ):
        scope, messages = invoke(app, '/api/accounts/me', extra=extra, origin=origin)
        assert messages[0]['status'] == 401  # synthetic API still owns authentication
        assert api.scopes[-1] is scope


@pytest.mark.parametrize('origin', [None, 'https://foreign.test'])
def test_websocket_requires_its_exact_origin(renderer, origin):
    app, api = app_for(renderer)
    _, messages = invoke(app, '/ws/telemetry', kind='websocket', scheme='wss', origin=origin)
    assert messages == [{'type': 'websocket.close', 'code': 1008}]
    assert api.scopes == []


@pytest.mark.parametrize('path,raw', [
    ('/../outside.js', b'/../outside.js'),
    ('/../outside.js', b'/%2e%2e/outside.js'),
    ('/%2e%2e/outside.js', b'/%252e%252e/outside.js'),
    ('/assets/app.js', b'/assets%2fapp.js'),
    ('/assets/app.js', b'/assets%5capp.js'),
    ('/assets/../index.html', b'/assets/../index.html'),
    ('//index.html', b'//index.html'),
    ('/index.html\x00', b'/index.html%00'),
    ('/index.html', b'/%zzindex.html'),
    ('/assets\\app.js', b'/assets\\app.js'),
])
def test_malformed_or_encoded_traversal_is_refused(renderer, path, raw):
    app, _ = app_for(renderer)
    _, messages = invoke(app, path, raw=raw)
    assert messages[0]['status'] == 400


@pytest.mark.parametrize('path', ['/secret.key', '/.env', '/assets/app.js.map', '/accounts.sqlite', '/source.py', '/secret.json', '/missing.js'])
def test_secrets_source_maps_and_missing_assets_never_get_spa_fallback(renderer, path):
    app, _ = app_for(renderer)
    _, messages = invoke(app, path)
    assert messages[0]['status'] == 404
    assert b'secret' not in messages[-1].get('body', b'')


def test_links_hardlinks_and_linked_parent_files_are_refused(renderer, tmp_path):
    outside = tmp_path / 'private.js'
    outside.write_text('private data')
    (renderer / 'assets/link.js').symlink_to(outside)
    (renderer / 'linked').symlink_to(tmp_path, target_is_directory=True)
    (renderer / 'assets/hard.js').hardlink_to(outside)
    app, _ = app_for(renderer)
    for path in ('/assets/link.js', '/linked/private.js', '/assets/hard.js'):
        _, messages = invoke(app, path)
        assert messages[0]['status'] == 404


def test_head_mime_methods_and_route_fallback(renderer):
    app, _ = app_for(renderer)
    _, head = invoke(app, '/assets/app.js', method='HEAD')
    assert head[0]['status'] == 200
    assert dict(head[0]['headers'])[b'content-type'] == b'text/javascript; charset=utf-8'
    assert head[-1]['body'] == b''
    assert dict(head[0]['headers'])[b'x-content-type-options'] == b'nosniff'
    for method in ('POST', 'PUT', 'DELETE', 'OPTIONS'):
        _, messages = invoke(app, '/', method=method)
        assert messages[0]['status'] == 405
    _, fallback = invoke(app, '/projects/view', extra=((b'accept', b'text/html'),))
    assert fallback[0]['status'] == 200
    _, missing = invoke(app, '/projects/view')
    assert missing[0]['status'] == 404


@pytest.mark.parametrize('origin', [ORIGIN, 'https://localhost'])
def test_real_api_cookie_csrf_and_bootstrap_are_not_bypassed(renderer, tmp_path, monkeypatch, origin):
    from backend.main import create_app, SharedSecurityHeaders
    from backend.local_web_app import LocalWebApp
    monkeypatch.setenv('MODU_BROWSER_ORIGINS', json.dumps([origin]))
    api = create_app(project_dir=str(tmp_path / 'projects'), shared_auth_dir=str(tmp_path / 'auth'))
    api.state.accounts.bootstrap('synthetic-admin', 'synthetic password 123')
    served = SharedSecurityHeaders(LocalWebApp(api, renderer, origin))
    client = TestClient(served, base_url=origin, headers={'Origin': origin})
    assert client.get('/').status_code == 200
    refused = client.get('/api/context')
    assert refused.status_code == 401
    assert refused.headers['x-frame-options'] == 'DENY'
    assert refused.headers['content-security-policy'] == "frame-ancestors 'none'"
    assert client.post('/api/accounts/bootstrap', json={'username': 'attacker', 'password': 'synthetic password 456'}).status_code in (401, 403)
    login = client.post('/api/accounts/login', json={'username': 'synthetic-admin', 'password': 'synthetic password 123', 'transport': 'cookie'})
    assert login.status_code == 200
    accepted = client.get('/api/accounts/me')
    assert accepted.status_code == 200
    assert accepted.headers['x-frame-options'] == 'DENY'
    assert accepted.headers['referrer-policy'] == 'no-referrer'
    metadata_client = TestClient(served, base_url=origin)
    metadata_client.cookies.update(client.cookies)
    for metadata in ({}, {'Sec-Fetch-Site': 'same-site'}, {'Sec-Fetch-Site': 'cross-site'}):
        assert metadata_client.get('/api/accounts/me', headers=metadata).status_code == 403
    assert metadata_client.get('/api/accounts/me', headers={'Sec-Fetch-Site': 'same-origin'}).status_code == 200
    assert client.post('/api/accounts/logout').status_code == 403
    assert client.post('/api/accounts/logout', headers={'X-Vision-CSRF': login.json()['csrf_token']}).status_code == 200


def test_outer_browser_policy_preserves_inner_report_sandbox(renderer):
    from backend.main import SharedSecurityHeaders
    from backend.local_web_app import LocalWebApp
    policy = b"sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"

    async def report(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200,
                    'headers': [(b'content-security-policy', policy)]})
        await send({'type': 'http.response.body', 'body': b'<html>synthetic report</html>'})

    app = SharedSecurityHeaders(LocalWebApp(report, renderer, ORIGIN))
    _, result = invoke(app, '/api/report/view/synthetic')
    headers = dict(result[0]['headers'])
    assert headers[b'content-security-policy'] == policy
    assert headers[b'x-frame-options'] == b'DENY'
    assert headers[b'x-content-type-options'] == b'nosniff'


def test_default_https_port_uses_browser_canonical_http_and_wss_authority(renderer):
    from backend.local_web_app import LocalWebApp
    api = API()
    app = LocalWebApp(api, renderer, 'https://localhost')
    _, response = invoke(app, host=b'localhost')
    assert response[0]['status'] == 200
    scope, response = invoke(app, '/api/context', host=b'localhost', origin='https://localhost')
    assert response[0]['status'] == 401
    assert api.scopes[-1] is scope
    scope, response = invoke(app, '/ws/telemetry', kind='websocket', scheme='wss',
                             host=b'localhost', origin='https://localhost')
    assert response == [{'type': 'websocket.close', 'code': 4401}]
    assert api.scopes[-1] is scope


@pytest.mark.parametrize('host,origin', [
    (b'localhost:443', 'https://localhost'),
    (b'localhost', 'https://localhost:443'),
    (b'localhost', 'https://localhost:8443'),
    (b'127.0.0.1', 'https://localhost'),
    (b'foreign.test', 'https://localhost'),
    (b'localhost', 'https://foreign.test'),
])
def test_canonical_default_port_still_refuses_other_authorities(renderer, host, origin):
    from backend.local_web_app import LocalWebApp
    api = API()
    app = LocalWebApp(api, renderer, 'https://localhost')
    _, response = invoke(app, '/api/context', host=host, origin=origin)
    assert response[0]['status'] == 403
    _, response = invoke(app, '/ws/telemetry', kind='websocket', scheme='wss', host=host, origin=origin)
    assert response == [{'type': 'websocket.close', 'code': 1008}]
    assert api.scopes == []


def test_noncanonical_explicit_default_port_is_not_a_second_allowed_origin(renderer):
    from backend.local_web_app import LocalWebApp
    with pytest.raises(ValueError, match='origin'):
        LocalWebApp(API(), renderer, 'https://localhost:443')
