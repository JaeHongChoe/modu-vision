"""Owned SDK fixtures; no collector, provider, server lifetime or model."""
import json
from pathlib import Path


def store():
    assert Path('backend/engine/local_trace.py').exists(), 'embedded Python SDK exporter exists'
    from backend.engine.local_trace import LocalTraceStore
    return LocalTraceStore()


def context(actor='actor', project='project'):
    from backend.contracts.context import ProjectContext
    return ProjectContext(workspace_id='workspace', actor_id=actor, project_id=project, mode='local')


def test_real_sdk_explicit_parent_and_private_scope():
    s = store(); ctx = context(); epoch = 'a' * 32
    h = s.begin(ctx, epoch, 'api.training', '00-' + '1' * 32 + '-' + '2' * 16 + '-01')
    h.end('ok', 200)
    doc = s.read(ctx, epoch)
    assert doc['sdk'] == 'opentelemetry-python-1.45.2'
    row = doc['spans'][0]
    assert row['trace_id'] == '1' * 32 and row['parent_span_id'] == '2' * 16
    assert row['duration_ms'] >= 0 and row['end_ms'] >= row['start_ms']
    assert row['coverage'] == 'explicit_parent'
    assert not any(x in json.dumps(doc) for x in ('workspace', 'actor', 'project', 'resource', 'hostname', 'stack', 'traceparent'))
    assert s.read(context('other'), epoch)['spans'] == []
    assert s.read(ctx, 'b' * 32)['spans'] == []


def test_bounds_privacy_no_synthetic_worker_link_and_malformed_parent():
    s = store(); ctx = context(); epoch = 'a' * 32
    for i in range(300): s.begin(ctx, epoch, '/private?token=secret', 'invalid').end('error', 503)
    doc = s.read(ctx, epoch)
    assert len(doc['spans']) == 256 and doc['dropped_spans'] == 44
    assert all(r['operation'] == 'api.app_request' and r['parent_span_id'] is None for r in doc['spans'])
    assert doc['async_jobs'] == 'unlinked'
    assert len(json.dumps(doc).encode()) <= 1048576
    for i in range(20): s.begin(context(project=str(i)), epoch, 'api.training').end('ok')
    assert s.read(ctx, epoch)['dropped_scopes'] >= 13
    assert s.provider.resource.attributes == {}


def test_export_failure_does_not_replace_thrown_identity():
    s = store(); h = s.begin(context(), 'a' * 32, 'api.training')
    s.exporter.export = lambda spans: (_ for _ in ()).throw(RuntimeError('secret exporter failure'))
    original = ValueError('private')
    try:
        try: raise original
        finally: h.end('error')
    except ValueError as got: assert got is original
    assert s.read(context(), 'a' * 32)['export_errors'] == 1


def test_existing_authenticated_request_chain_passively_records_then_scoped_get_reads_back(tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app)
    epoch = 'a' * 32
    headers = {'X-Vision-Token': app.state.api_token, 'X-Vision-Trace-Scope': epoch}
    assert client.get('/api/local-trace').status_code == 401
    created = client.post('/api/project/create', headers=headers, json={'name':'Owned trace fixture','task':'classification'})
    assert created.status_code == 200
    ctx = created.headers['x-vision-context']
    headers['X-Vision-Context'] = ctx
    headers['traceparent'] = '00-' + '1' * 32 + '-' + '2' * 16 + '-01'
    response = client.get('/api/project/labelsets', headers=headers)
    assert response.status_code == 200
    headers.pop('traceparent')
    diagnostic = client.get('/api/local-trace', headers=headers)
    assert diagnostic.status_code == 200
    rows = diagnostic.json()['spans']
    assert any(row['trace_id'] == '1' * 32 and row['parent_span_id'] == '2' * 16 for row in rows)
    assert client.get('/api/local-trace', headers={**headers, 'X-Vision-Trace-Scope':'b' * 32}).json()['spans'] == []
    assert client.get('/api/local-trace', headers={'X-Vision-Token':app.state.api_token}).status_code == 400
    assert client.options('/api/local-trace', headers={'Origin':'null', 'Access-Control-Request-Method':'GET', 'Access-Control-Request-Headers':'traceparent,x-vision-trace-scope,x-vision-token'}).status_code == 200


def test_shared_trace_read_uses_authenticated_actor_role_and_project_not_supplied_epoch(tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    app = create_app(project_dir=str(tmp_path / 'projects'), shared_auth_dir=str(tmp_path / 'accounts'))
    client = TestClient(app)
    owner = app.state.accounts.bootstrap('owned-admin', 'owned-password-123')
    login = client.post('/api/accounts/login', json={'username':'owned-admin', 'password':'owned-password-123'})
    assert login.status_code == 200
    headers = {'Authorization':'Bearer ' + login.json()['token'], 'X-Vision-Trace-Scope':'a' * 32}
    created = client.post('/api/project/create', headers=headers, json={'name':'Owned shared fixture','task':'classification'})
    assert created.status_code == 200
    project = created.json()['id']
    headers['X-Vision-Project'] = project
    headers['X-Vision-Context'] = created.headers['x-vision-context']
    assert client.get('/api/project/labelsets', headers=headers).status_code == 200
    assert client.get('/api/local-trace', headers=headers).json()['spans']
    viewer = app.state.accounts.create_user('owned-viewer', 'owned-password-123')
    app.state.accounts.set_membership(project, viewer['id'], 'viewer', owner['id'])
    other_login = client.post('/api/accounts/login', json={'username':'owned-viewer', 'password':'owned-password-123'})
    viewer_headers = {**headers, 'Authorization':'Bearer ' + other_login.json()['token']}
    assert client.get('/api/local-trace', headers=viewer_headers).status_code == 403
    viewer_headers.pop('X-Vision-Context')
    readback = client.get('/api/local-trace', headers=viewer_headers)
    assert readback.status_code == 200 and readback.json()['spans'] == []
    viewer_headers['X-Vision-Project'] = 'foreign-project'
    assert client.get('/api/local-trace', headers=viewer_headers).status_code == 403
