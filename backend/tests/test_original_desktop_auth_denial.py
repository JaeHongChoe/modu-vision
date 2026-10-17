"""Causal whole-factory desktop denial; no fake route/status admission."""
import asyncio
import hashlib
import httpx


def test_original_desktop_get_denial_closes_exact_owned_envelope(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    root = tmp_path / 'owned-projects'
    app = main.create_app(project_dir=str(root))
    assert app.state.accounts is None
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob('*') if p.is_file()}
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            return await client.get('/api/onboarding')
    response = asyncio.run(original_request())
    assert response.status_code == 401
    assert response.json() == {'detail': 'Desktop session authorization required'}
    assert {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()} == before
    rows = observer.snapshot()['records']
    assert len(rows) == 1 and rows[0]['completed'] and rows[0]['normal_return']
    admission.close()
    assert admission.drain(.03) == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    final = observer.snapshot()
    assert final['all_settled'] and not final['uncertain']
    assert all(final[k] is False for k in ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


import copy
import time
import pytest


@pytest.fixture
def owned(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(tmp_path / 'owned-projects'))
    app.middleware_stack = app.build_middleware_stack()
    return app, admission, observer


async def original_get(app, *, path='/api/onboarding', query=b'', method='GET', headers=(), on_send=None):
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'scheme': 'http',
        'method': method, 'path': path, 'raw_path': path.encode(), 'query_string': query,
        'headers': list(headers), 'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', 2)}
    frames = []
    async def receive(): return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(message):
        if on_send is not None: await on_send(scope, message)
        frames.append(message)
    await app(scope, receive, send)
    return frames


def refused(admission, *, active=None):
    admission.close()
    result = admission.drain(.03)
    assert result['status'] == 'refused' and 'uncovered_request_protocol' in result['unsupported']
    if active is not None: assert result['active_scopes'] == active


def test_wrong_token_current_and_unknown_paths_are_exact_original_denials(owned):
    app, admission, observer = owned
    async def scenario():
        for path in ('/api/project/current', '/owned-unknown-path'):
            frames = await original_get(app, path=path, headers=[(b'x-vision-token', b'owned-invalid-token')])
            assert frames[0]['status'] == 401 and frames[-1]['body'] == b'{"detail":"Desktop session authorization required"}'
    asyncio.run(scenario())
    assert not (app.state.project_dir / 'default_project').exists()
    assert admission._current_project_requests.snapshot()['records'] == []
    assert [r['original_denial_calls'] for r in admission._desktop_denials.snapshot()['records']] == [1, 1]
    admission.close()
    assert admission.drain(.03) == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    assert observer.snapshot()['all_settled']


@pytest.mark.parametrize('kwargs', [{'query': b'unqualified=1'}, {'method': 'POST'}, {'method': 'HEAD'}])
def test_other_queries_or_methods_stay_uncovered(owned, kwargs):
    app, admission, _ = owned
    asyncio.run(original_get(app, **kwargs))
    assert admission._desktop_denials.snapshot()['records'] == []
    refused(admission, active=0)


def test_valid_current_still_uses_only_original_current_effect_adapter(owned):
    app, admission, observer = owned
    frames = asyncio.run(original_get(app, path='/api/project/current', headers=[(b'x-vision-token', app.state.api_token.encode())]))
    assert frames[0]['status'] == 200 and admission._desktop_denials.snapshot()['records'] == []
    assert admission._current_project_requests.snapshot()['records'][0]['settled']
    admission.close()
    assert admission.drain(.03)['status'] == 'managed_scopes_drained'
    assert observer.snapshot()['all_settled']


def test_valid_token_other_get_remains_uncovered(owned):
    app, admission, _ = owned
    frames = asyncio.run(original_get(app, path='/owned-unknown-path', headers=[(b'x-vision-token', app.state.api_token.encode())]))
    assert frames[0]['status'] == 404 and admission._desktop_denials.snapshot()['records'] == []
    refused(admission, active=0)


def test_preexisting_unsupported_is_not_erased_by_original_denial(owned):
    app, admission, _ = owned
    admission.uncovered('startup_background_protocols')
    assert asyncio.run(original_get(app))[0]['status'] == 401
    assert admission._desktop_denials.snapshot()['records'][0]['settled']
    admission.close()
    assert admission.drain(.03) == {'status': 'refused', 'active_scopes': 0, 'unsupported': ['startup_background_protocols']}


@pytest.mark.parametrize('error_type', [OSError, asyncio.CancelledError])
def test_original_send_error_or_cancellation_retains_pending_lifetime(owned, error_type):
    app, admission, observer = owned
    async def fail(scope, message):
        if message['type'] == 'http.response.body': raise error_type('owned response transport failed')
    with pytest.raises(error_type): asyncio.run(original_get(app, on_send=fail))
    row = admission._desktop_denials.snapshot()
    assert row['pending'] == 1 and row['records'][0]['failed'] and not row['records'][0]['settled']
    assert observer.snapshot()['records'][0]['completed'] is False
    refused(admission, active=1)


def test_manual_context_hook_cannot_mint_original_branch(owned):
    app, admission, _ = owned
    from backend.engine.original_desktop_auth_denial import original_desktop_denial_response
    async def manual(scope, message):
        if message['type'] == 'http.response.start':
            async def unused_receive(): return {'type': 'http.request'}
            async def unused_send(message): pass
            with original_desktop_denial_response(admission._desktop_denials.auth, scope, unused_receive, unused_send): pass
    assert asyncio.run(original_get(app, on_send=manual))[0]['status'] == 401
    refused(admission, active=1)


def test_foreign_task_and_copy_do_not_reuse_original_branch(owned):
    app, admission, _ = owned
    from backend.engine.original_desktop_auth_denial import _ACTIVE, DesktopDenialObservationError
    async def foreign(scope, message):
        if message['type'] == 'http.response.start':
            attempt = _ACTIVE.get()
            with pytest.raises(DesktopDenialObservationError): copy.copy(attempt)
            async def other(): assert not attempt.original()
            await asyncio.create_task(other())
    assert asyncio.run(original_get(app, on_send=foreign))[0]['status'] == 401
    refused(admission, active=1)


def test_late_original_return_retains_pending_without_extending_clock(owned):
    app, admission, _ = owned
    async def expire(scope, message):
        if message['type'] == 'http.response.start': admission._desktop_denials.deadline = time.monotonic() - 1
    assert asyncio.run(original_get(app, on_send=expire))[0]['status'] == 401
    refused(admission, active=1)


@pytest.mark.parametrize('changed', ['token', 'headers', 'accounts'])
def test_owned_source_configuration_mutation_refuses_settlement(owned, changed):
    app, admission, _ = owned
    async def mutate(scope, message):
        if message['type'] == 'http.response.start':
            if changed == 'token': admission._desktop_denials.auth.api_token = 'owned-changed-token'
            elif changed == 'headers': scope['headers'].append((b'x-owned-mutation', b'changed'))
            else: app.state.accounts = object()
    assert asyncio.run(original_get(app, on_send=mutate))[0]['status'] == 401
    refused(admission, active=1)


def test_foreign_status_only_401_is_not_an_original_auth_denial(owned, monkeypatch):
    from backend import main
    app, admission, _ = owned
    async def foreign(middleware, scope, receive, send):
        return await main.JSONResponse({'detail': 'Desktop session authorization required'}, status_code=401)(scope, receive, send)
    monkeypatch.setattr(main.DesktopApiAuthMiddleware, '__call__', foreign)
    assert asyncio.run(original_get(app))[0]['status'] == 401
    assert admission._desktop_denials.snapshot()['records'] == []
    refused(admission, active=0)


def test_no_observer_admission_keeps_original_sticky_behavior(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    admission = BackendWorkAdmission()
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(tmp_path / 'unobserved'))
    assert asyncio.run(original_get(app))[0]['status'] == 401
    assert admission._desktop_denials is None
    refused(admission, active=0)


def test_shared_account_401_does_not_use_desktop_denial_adapter(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(tmp_path / 'shared-projects'), shared_auth_dir=str(tmp_path / 'shared-auth'))
    assert app.state.accounts is not None and admission._desktop_denials is None
    assert asyncio.run(original_get(app))[0]['status'] == 401
    admission.close()
    assert admission.drain(.03) == {'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}
    assert admission._shared_denials.snapshot()['records'][0]['settled']


def test_unowned_default_none_preserves_original_fixed401(tmp_path, monkeypatch):
    from backend import main
    monkeypatch.setattr(main, 'backend_work_admission', lambda: None)
    app = main.create_app(project_dir=str(tmp_path / 'unowned-projects'))
    frames = asyncio.run(original_get(app))
    assert frames[0]['status'] == 401
    assert frames[-1]['body'] == b'{"detail":"Desktop session authorization required"}'


def test_websocket_original1008_remains_uncovered(owned):
    app, admission, _ = owned
    async def scenario():
        scope = {'type': 'websocket', 'asgi': {'version': '3.0'}, 'scheme': 'ws', 'path': '/api/onboarding',
            'raw_path': b'/api/onboarding', 'query_string': b'', 'headers': [],
            'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', 2), 'subprotocols': []}
        frames = []
        async def receive(): return {'type': 'websocket.connect'}
        async def send(message): frames.append(message)
        await app(scope, receive, send)
        assert frames == [{'type': 'websocket.close', 'code': 1008}]
    asyncio.run(scenario())
    assert admission._desktop_denials.snapshot()['records'] == []
    refused(admission, active=0)



def test_equal_header_container_replacement_after_original_hook_is_not_original(owned):
    app, admission, _ = owned
    async def replace(scope, message):
        if message['type'] == 'http.response.start':
            before = scope['headers']
            scope['headers'] = list(before)
            assert scope['headers'] == before and scope['headers'] is not before
    assert asyncio.run(original_get(app, on_send=replace))[0]['status'] == 401
    assert admission._desktop_denials.snapshot()['records'][0]['original_denial_calls'] == 1
    refused(admission, active=1)
