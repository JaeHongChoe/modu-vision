"""Separate pending denial-ownership probe, not part of canonical-context fix.

Uses the original permission rejection and original durable decision journal.
No handler, model, AccountStore, database or authority witness is replaced.
"""
import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import httpx
import pytest


def test_genuine_viewer_preview_denial_needs_its_own_closed_ownership(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects

    monkeypatch.chdir(tmp_path)
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    actor, (project,) = _owned_projects(app, observer, 'Viewer denial')
    viewer = app.state.accounts.create_user('preview-viewer', 'fixture-password-123')
    app.state.accounts.set_membership(project['id'], viewer['id'], 'viewer', actor['id'])
    protected = (Path(project['project_dir'])/'project.json').read_bytes()
    with closing(sqlite3.connect(app.state.accounts.path)) as db:
        before = db.execute('SELECT COALESCE(MAX(seq),0) FROM permission_audit').fetchone()[0]

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            login = await client.post('/api/accounts/login', json={
                'username':'preview-viewer', 'password':'fixture-password-123'})
            assert login.status_code == 200 and login.json()['user']['id'] == viewer['id']
            headers = {'authorization':'Bearer '+login.json()['token'], 'x-vision-project':project['id']}
            current = await client.get('/api/project/current', headers=headers)
            assert current.status_code == 200 and current.json()['id'] == project['id']
            headers['x-vision-context'] = current.headers['x-vision-context']
            assert not observer.snapshot()['uncertain']
            # The actual middleware rejects before route/model/body validation.
            response = await client.post('/api/flowchart/pipeline/diff', headers=headers, json={})
            assert response.status_code == 403 and response.json() == {
                'detail':'This project role cannot perform the requested action'}
            assert response.is_closed and response.is_stream_consumed
            attempt = admission._UI_ancillary_requests.records[-1]
            assert attempt.name == 'preview_pipeline_change' and attempt.entries == 0
            row = observer._records[attempt.activity.token]
            assert row['response_status'] == 403 and row['normal_return'] and row['completed'] and row['released']

    asyncio.run(original_sequence())
    assert (Path(project['project_dir'])/'project.json').read_bytes() == protected
    with closing(sqlite3.connect(app.state.accounts.path)) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute('SELECT * FROM permission_audit WHERE seq>?', (before,))]
    assert len(rows) == 1
    row = rows[0]
    assert row['actor_id'] == viewer['id'] and row['project_id'] == project['id']
    assert row['action'] == 'flow.execute' and row['decision'] == 'denied'
    assert json.loads(row['details'])['reason'] == 'role_or_membership_required'
    reasons = list(observer._failures)
    print('viewer denial original reasons:', reasons)
    print('viewer denial reason SHA256:', [hashlib.sha256(reason.encode()).hexdigest() for reason in reasons])
    assert not observer.snapshot()['uncertain'], reasons
    admission.close()
    assert admission.drain(.03) == {'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}


def _audits(app):
    with closing(sqlite3.connect(app.state.accounts.path.as_uri()+'?mode=ro', uri=True)) as db:
        return tuple(db.execute('SELECT * FROM permission_audit ORDER BY seq')), tuple(db.execute('SELECT * FROM audit ORDER BY rowid'))


def _fixture(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects
    monkeypatch.chdir(tmp_path)
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    actor, (project,) = _owned_projects(app, observer, 'Closed viewer preview')
    viewer = app.state.accounts.create_user('preview-viewer', 'fixture-password-123')
    app.state.accounts.set_membership(project['id'], viewer['id'], 'viewer', actor['id'])
    return app, admission, observer, actor, project, viewer


async def _viewer_headers(client, project):
    login = await client.post('/api/accounts/login', json={'username':'preview-viewer', 'password':'fixture-password-123'})
    assert login.status_code == 200
    headers = {'authorization':'Bearer '+login.json()['token'], 'x-vision-project':project['id']}
    current = await client.get('/api/project/current', headers=headers)
    assert current.status_code == 200 and current.json()['id'] == project['id']
    return {**headers, 'x-vision-context':current.headers['x-vision-context']}


def _refused(admission, observer):
    admission.close()
    result = admission.drain(.03)
    assert result['status'] == 'refused' and 'uncovered_request_protocol' in result['unsupported']
    assert not any(row.settled for row in admission._UI_ancillary_requests.records)
    assert all(observer.snapshot()[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


def test_repeated_viewer_denials_close_real_preload_and_both_mutable_audits(tmp_path, monkeypatch):
    app, admission, observer, actor, project, viewer = _fixture(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    protected = {path:path.read_bytes() for path in (root/'project.json', root/'labelsets.json')}
    before = _audits(app)
    receives = []
    async def original_app(scope, receive, send):
        async def observed_receive():
            if scope.get('path') == '/api/flowchart/pipeline/diff': receives.append(True)
            return await receive()
        await app(scope, observed_receive, send)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_app), base_url='http://owned.invalid') as client:
            headers = await _viewer_headers(client, project)
            for _ in range(2):
                result = await client.post('/api/flowchart/pipeline/diff', headers=headers, json={'unused':'unconsumed'})
                assert result.status_code == 403 and result.json() == {'detail':'This project role cannot perform the requested action'}
                assert result.is_closed and result.is_stream_consumed
    asyncio.run(original_sequence())
    assert receives == [] and all(path.read_bytes() == raw for path, raw in protected.items())
    after = _audits(app)
    assert after[0][:len(before[0])] == before[0] and after[1][:len(before[1])] == before[1]
    permission = after[0][len(before[0]):]; legacy = after[1][len(before[1]):]
    assert len(permission) == len(legacy) == 2
    for row, old in zip(permission, legacy):
        assert row[1] == old[0] and row[3] == app.state.context_registry.workspace_id
        assert row[4:8] == (project['id'], viewer['id'], 'flow.execute', 'denied')
        assert json.loads(row[8]) == {'reason':'role_or_membership_required', 'membership_revision':1,
            'resource_revision':None, 'resource_revision_verified':False}
        assert old[2:4] == (viewer['id'], 'flow.execute')
        assert json.loads(old[4]) == {'project':project['id'], 'decision':'denied', **json.loads(row[8])}
    assert not observer.snapshot()['uncertain'], list(observer._failures)
    attempts = admission._UI_ancillary_requests.records
    assert len(attempts) == 2 and all(attempt.entries == attempt.storage == 0 and not attempt.worker_live for attempt in attempts)
    for attempt in attempts:
        assert len(attempt.auth.denial_databases) == 7
        for _, connection in attempt.auth.denial_databases:
            with pytest.raises(sqlite3.ProgrammingError): connection.in_transaction
        preload = attempt.auth.denial_preload
        assert preload is not None and preload['returned'] is attempt.scope['state']['scoped_project']
        assert all(handle.closed for handle, _, _ in preload['reads'])
        manager, handle, record = preload['guard']
        assert manager.gen.gi_frame is None and handle.closed and record['active'] is False
        assert 'project_context' not in attempt.scope['state'] and 'permission_decision' not in attempt.scope['state']
    admission.close()
    assert admission.drain(.03) == {'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}


def test_real_denied_audit_abort_preserves_rollback_and_refuses(tmp_path, monkeypatch):
    app, admission, observer, actor, project, viewer = _fixture(tmp_path, monkeypatch)
    before = _audits(app)
    with closing(sqlite3.connect(app.state.accounts.path)) as db:
        with db:
            db.execute("CREATE TRIGGER denied_audit_abort BEFORE INSERT ON permission_audit WHEN NEW.action='flow.execute' AND NEW.decision='denied' BEGIN SELECT RAISE(ABORT,'denied audit fixture rollback'); END")
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _viewer_headers(client, project)
            with pytest.raises(sqlite3.IntegrityError, match='denied audit fixture rollback'):
                await client.post('/api/flowchart/pipeline/diff', headers=headers, json={})
    asyncio.run(original_sequence())
    assert _audits(app) == before
    _refused(admission, observer)


def test_real_denial_send_failure_keeps_committed_audit_but_refuses(tmp_path, monkeypatch):
    app, admission, observer, actor, project, viewer = _fixture(tmp_path, monkeypatch)
    before = _audits(app)
    async def original_app(scope, receive, send):
        async def original_send(message):
            if scope.get('path') == '/api/flowchart/pipeline/diff' and message['type'] == 'http.response.body':
                raise OSError('real denied response send fixture')
            await send(message)
        await app(scope, receive, original_send)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_app), base_url='http://owned.invalid') as client:
            headers = await _viewer_headers(client, project)
            with pytest.raises(OSError, match='real denied response send fixture'):
                await client.post('/api/flowchart/pipeline/diff', headers=headers, json={})
    asyncio.run(original_sequence())
    after = _audits(app)
    assert len(after[0]) == len(before[0])+1 and len(after[1]) == len(before[1])+1
    _refused(admission, observer)


@pytest.mark.parametrize('when', ('before-factory', 'after-real-current'))
def test_static_audit_delegate_cannot_borrow_original_denial(when, tmp_path, monkeypatch):
    from backend.engine.shared_accounts import AccountStore
    original = AccountStore._audit
    def foreign(*args, **kwargs): return original(*args, **kwargs)
    if when == 'before-factory': monkeypatch.setattr(AccountStore, '_audit', staticmethod(foreign))
    app, admission, observer, actor, project, viewer = _fixture(tmp_path, monkeypatch)
    before = _audits(app)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _viewer_headers(client, project)
            if when == 'after-real-current': monkeypatch.setattr(AccountStore, '_audit', staticmethod(foreign))
            result = await client.post('/api/flowchart/pipeline/diff', headers=headers, json={})
            assert result.status_code == 403 and result.json() == {'detail':'This project role cannot perform the requested action'}
    asyncio.run(original_sequence())
    after = _audits(app)
    assert len(after[0]) == len(before[0])+1 and len(after[1]) == len(before[1])+1
    _refused(admission, observer)


@pytest.mark.parametrize('changed', ('binding', 'malformed-normalization'))
def test_changed_denial_binding_cannot_claim_real_403(changed, tmp_path, monkeypatch):
    app, admission, observer, actor, project, viewer = _fixture(tmp_path, monkeypatch)
    async def original_app(scope, receive, send):
        async def original_send(message):
            if scope.get('path') == '/api/flowchart/pipeline/diff' and message['type'] == 'http.response.start':
                attempt = next(row for row in admission._UI_ancillary_requests.records if row.scope is scope)
                if changed == 'binding': monkeypatch.setattr(attempt.auth, 'denial_binding', object(), raising=False)
                else: attempt.auth.denial_preload['normal'] = object()
            await send(message)
        await app(scope, receive, original_send)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_app), base_url='http://owned.invalid') as client:
            headers = await _viewer_headers(client, project)
            result = await client.post('/api/flowchart/pipeline/diff', headers=headers, json={})
            assert result.status_code == 403 and result.json() == {'detail':'This project role cannot perform the requested action'}
    asyncio.run(original_sequence())
    _refused(admission, observer)


@pytest.mark.parametrize('cold', ('missing-labelsets', 'legacy-manifest'))
def test_original_cold_preload_writes_are_not_readonly_denial_credit(cold, tmp_path, monkeypatch):
    app, admission, observer, actor, project, viewer = _fixture(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _viewer_headers(client, project)
            if cold == 'missing-labelsets': (root/'labelsets.json').unlink()
            else:
                saved = json.loads((root/'project.json').read_bytes()); del saved['schema_version']
                (root/'project.json').write_text(json.dumps(saved))
            result = await client.post('/api/flowchart/pipeline/diff', headers=headers, json={})
            assert result.status_code == 403 and result.json() == {'detail':'This project role cannot perform the requested action'}
    asyncio.run(original_sequence())
    if cold == 'missing-labelsets': assert json.loads((root/'labelsets.json').read_bytes())['active_id'] == 'default'
    else:
        assert json.loads((root/'project.json').read_bytes())['schema_version'] == 1
        assert list((root/'.migrations').glob('*/receipt.json'))
    _refused(admission, observer)
