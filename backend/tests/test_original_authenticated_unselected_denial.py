"""Real original auth/SQLite/no-project denial; no browser/native credit.

Only the existing original app/admission supplier is controlled. Positive
cases retain the product's original DB observations, not a profile hook.
"""
import asyncio
import json
import sqlite3
import time

import httpx
import pytest

from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects


async def login(client):
    response = await client.post('/api/accounts/login', json={
        'username':'fixture-admin', 'password':'fixture-password-123'})
    assert response.status_code == 200
    return response.json()['token']


def finish(admission):
    admission.close()
    return admission.drain(3.903)


def refused(admission):
    result = finish(admission)
    assert result['status'] == 'refused'
    assert 'uncovered_request_protocol' in result['unsupported']
    return result


@pytest.mark.parametrize('path', ['/api/compute/reservations', '/api/compute/targets',
    '/api/compute/jobs', '/api/compute/inputs?task=classification', '/api/dataset/revisions'])
def test_five_original_authenticated_unselected_reads_close_exact_denial(path, tmp_path, monkeypatch):
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            token = await login(client)
            assert not admission._unsupported
            return await client.get(path, headers={'authorization':'Bearer ' + token})
    response = asyncio.run(scenario())
    assert response.status_code == 409
    assert response.json() == {'detail':'Select an authorized project first'}
    owner = admission._shared_denials
    assert len(owner.records) == 1
    attempt = owner.records[0]
    assert attempt.settled and not attempt.failed and attempt.auth_returned
    auth = attempt.auth
    assert auth.middleware_calls == auth.middleware_returns == 1
    assert auth.methods == ['bind_workspace', 'authenticate']
    assert [name for name, _ in auth.databases] == ['bind_workspace', 'authenticate', 'project_for']
    assert auth.database_exits == 3
    assert auth.project_frame.f_locals['selected'] is None
    assert auth.project_frame.f_locals['project_id'] is None
    assert auth.authorization_frame.f_locals['project'] is None
    for _, connection in auth.databases:
        with pytest.raises(sqlite3.ProgrammingError):
            _ = connection.in_transaction
    assert not owner.pending
    assert finish(admission) == {'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    final = observer.snapshot()
    assert len(final['records']) == 2
    assert all(row['completed'] and row['normal_return'] and row['released'] for row in final['records'])
    assert final['all_settled'] and not final['uncertain'] and final['failures'] == []
    assert all(final[key] is False for key in ('can_release_launch_lease','process_tree_exit_verified','whole_writer_coverage'))


@pytest.mark.parametrize('variant', ['invalid_session', 'unreviewed_path', 'selected_header', 'stored_selection'])
def test_unselected_denial_does_not_cover_other_authority_or_routes(variant, tmp_path, monkeypatch):
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    if variant == 'stored_selection':
        actor, (project,) = _owned_projects(app, observer, 'Owned existing selection')
        app.state.accounts.select_project(actor['id'], project['id'])
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            token = await login(client)
            headers = {'authorization':'Bearer ' + ('owned-invalid-session' if variant == 'invalid_session' else token)}
            if variant == 'selected_header': headers['x-vision-project'] = 'owned-unavailable-project'
            path = '/api/onboarding' if variant == 'unreviewed_path' else '/api/compute/reservations'
            return await client.get(path, headers=headers)
    response = asyncio.run(scenario())
    assert response.status_code == {'invalid_session':401, 'unreviewed_path':409,
        'selected_header':403, 'stored_selection':200}[variant]
    result = refused(admission)
    if variant in {'unreviewed_path','selected_header'}:
        assert admission._shared_denials.records == []
    else:
        assert len(admission._shared_denials.pending) == 1
        assert result['active_scopes'] >= 1


@pytest.mark.parametrize('variant', ['headers', 'query', 'missing_project_db', 'fake_response',
    'instance_project_copy', 'project_code', 'replay', 'late', 'send_error'])
def test_missing_changed_replayed_late_or_failed_witness_never_settles(variant, tmp_path, monkeypatch):
    from backend.tests.test_original_desktop_auth_denial import original_get
    from backend.api.shared_authorization import SharedAuthorizationMiddleware
    from starlette.responses import JSONResponse
    app, admission, _ = _login_stack(tmp_path, monkeypatch)
    # Direct data setup is outside admitted requests; no credential is persisted.
    token = app.state.accounts.login('fixture-admin', 'fixture-password-123')['token']
    if variant == 'missing_project_db':
        monkeypatch.setattr(type(app.state.accounts), 'project_for', lambda self, user_id, project_id=None: None)
    elif variant == 'instance_project_copy':
        from types import FunctionType, MethodType
        function = type(app.state.accounts).project_for
        copied = FunctionType(function.__code__, function.__globals__, function.__name__, function.__defaults__, function.__closure__)
        monkeypatch.setattr(app.state.accounts, 'project_for', MethodType(copied, app.state.accounts))
    elif variant == 'project_code':
        function = type(app.state.accounts).project_for
        def foreign(self, user_id, project_id=None): return None
        monkeypatch.setattr(function, '__code__', foreign.__code__)
    elif variant == 'fake_response':
        async def foreign(self, scope, receive, send):
            await JSONResponse({'detail':'Select an authorized project first'}, status_code=409)(scope, receive, send)
        monkeypatch.setattr(SharedAuthorizationMiddleware, '__call__', foreign)
    async def on_send(scope, message):
        if message['type'] != 'http.response.body': return
        if variant == 'headers': scope['headers'] = list(scope['headers'])
        elif variant == 'query': scope['query_string'] = b'owned-changed=1'
        elif variant == 'late': admission._shared_denials.deadline = time.monotonic() - 1
        elif variant == 'send_error': raise OSError('Owned original denial transport failed')
        elif variant == 'replay':
            from backend.engine.original_shared_auth_denial import original_shared_denial_authentication
            from starlette.datastructures import Headers
            async def receive(): return {'type':'http.request','body':b'','more_body':False}
            async def send(message): pass
            with original_shared_denial_authentication(admission._shared_denials.auth, scope,
                    receive, send, app.state.accounts, Headers(scope=scope)):
                pass
    async def scenario():
        return await original_get(app, path='/api/compute/reservations',
            headers=[(b'authorization', ('Bearer ' + token).encode('ascii'))], on_send=on_send)
    if variant == 'send_error':
        with pytest.raises(OSError, match='Owned original denial transport failed'):
            asyncio.run(scenario())
    else:
        frames = asyncio.run(scenario())
        assert frames[0]['status'] == 409
        assert json.loads(frames[-1]['body']) == {'detail':'Select an authorized project first'}
    refused(admission)
