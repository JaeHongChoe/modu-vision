"""Original shared login's real SQLite/ASGI lifetime; no native/model proof.

The real admission object is supplied to the original factory, as in the
existing shared-denial component fixture. No ownership, route, account method,
database context, ASGI transport or completion witness is mocked.
"""
import asyncio

import httpx


def test_original_owned_shared_login_commits_and_drains(tmp_path, monkeypatch):
    """A completed original login must not leave an unknown writer protocol."""
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    from backend.tests.test_global_migration import owned

    root, _, _, _, _, actor = owned(tmp_path)
    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(root/'projects'),
                          shared_auth_dir=str(root/'auth'))
    assert app.state.accounts is not None
    assert admission._desktop_denials is None

    async def original_login():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            return await client.post('/api/accounts/login', json={
                'username': 'fixture-admin', 'password': 'fixture-password-123'})

    response = asyncio.run(original_login())
    assert response.status_code == 200
    body = response.json()
    assert body['user']['id'] == actor['id']
    assert type(body['token']) is str and body['token']
    # The token is actually committed and accepted by the original SQLite store.
    assert app.state.accounts.authenticate(body['token'])['id'] == actor['id']
    snapshot = observer.snapshot()
    assert len(snapshot['records']) == 1
    row = snapshot['records'][0]
    assert row['completed'] and row['normal_return'] and row['released']
    assert not snapshot['uncertain']

    admission.close()
    # R746 is expected to fail here, after all original login/DB/ASGI assertions.
    assert admission.drain(.03) == {
        'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    final = observer.snapshot()
    assert final['all_settled'] and not final['uncertain']
    assert all(final[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified',
        'can_release_launch_lease'))


import sqlite3

import pytest


def _login_stack(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    from backend.tests.test_global_migration import owned
    root, _, _, _, _, _ = owned(tmp_path)
    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(root/'projects'), shared_auth_dir=str(root/'auth'))
    return app, admission, observer


def _login(app, *, password='fixture-password-123', path='/api/accounts/login'):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            return await client.post(path, json={'username':'fixture-admin', 'password':password})
    return asyncio.run(request())


def _refused(admission):
    admission.close()
    result = admission.drain(.03)
    assert result['status'] == 'refused'
    assert 'uncovered_request_protocol' in result['unsupported']


def test_wrong_password_preserves_original_rejection_and_rate_limit(tmp_path, monkeypatch):
    app, admission, _ = _login_stack(tmp_path, monkeypatch)
    response = _login(app, password='fixture-wrong-password-123')
    assert response.status_code == 401
    assert response.json() == {'detail':'Invalid credentials or account unavailable'}
    with sqlite3.connect(app.state.accounts.path) as db:
        assert db.execute('SELECT count FROM failures WHERE username=?', ('fixture-admin',)).fetchone() == (1,)
        assert db.execute('SELECT COUNT(*) FROM sessions').fetchone() == (0,)
    _refused(admission)


def test_original_login_send_failure_never_settles(tmp_path, monkeypatch):
    import json
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    body = json.dumps({'username':'fixture-admin', 'password':'fixture-password-123'}).encode()
    scope = {'type':'http', 'asgi':{'version':'3.0'}, 'http_version':'1.1',
             'method':'POST', 'scheme':'http', 'path':'/api/accounts/login',
             'raw_path':b'/api/accounts/login', 'query_string':b'', 'root_path':'',
             'headers':[(b'content-type',b'application/json'), (b'content-length',str(len(body)).encode())],
             'client':('127.0.0.1',1), 'server':('owned.invalid',80)}
    async def request():
        sent = False
        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {'type':'http.request', 'body':body, 'more_body':False}
            return {'type':'http.disconnect'}
        async def send(message):
            if message['type'] == 'http.response.body':
                raise OSError('owned fixture response transport failed')
        await app(scope, receive, send)
    with pytest.raises(OSError, match='owned fixture response transport failed'):
        asyncio.run(request())
    assert observer.snapshot()['uncertain']
    _refused(admission)


def test_replaced_login_method_cannot_inherit_original_handler_credit(tmp_path, monkeypatch):
    app, admission, _ = _login_stack(tmp_path, monkeypatch)
    original = app.state.accounts.login
    def replacement(*args, **kwargs):
        # Real login still commits, but an additional caller is not the bound
        # original AccountStore method and must not inherit its lifetime proof.
        return original(*args, **kwargs)
    monkeypatch.setattr(app.state.accounts, 'login', replacement)
    assert _login(app).status_code == 200
    _refused(admission)


def test_unknown_POST_cannot_borrow_login_lifetime(tmp_path, monkeypatch):
    app, admission, _ = _login_stack(tmp_path, monkeypatch)
    assert _login(app, path='/api/owned-unregistered').status_code == 401
    _refused(admission)


@pytest.mark.parametrize('authenticated', [False, True])
def test_original_login_then_current_request_drains(tmp_path, monkeypatch, authenticated):
    from starlette.requests import Request
    from backend.api.routes_project import create_project, ProjectCreateRequest
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    actor = app.state.accounts.users()[0]
    # Source-component data preparation only, outside every observed request.
    # The original API creates a real temporary project and original membership;
    # this constructed setup Request grants no ASGI/handler observation credit.
    setup_request = Request({'type':'http', 'app':app, 'state':{'account_user':actor}})
    project = create_project(ProjectCreateRequest(name='Owned selected project'), setup_request)
    app.state.context_registry.register_project(project)
    assert observer.snapshot()['records'] == []

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            login = await client.post('/api/accounts/login', json={
                'username':'fixture-admin', 'password':'fixture-password-123'})
            assert login.status_code == 200
            headers = {
                'authorization':'Bearer '+login.json()['token'],
                'x-vision-project':project['id']} if authenticated else {}
            current = await client.get('/api/project/current', headers=headers)
            if authenticated:
                assert current.status_code == 200 and current.json()['id'] == project['id']
            else:
                # Isolate the existing typed denial after unrelated lazy-route
                # production; no new authenticated-current protocol is implied.
                assert current.status_code == 401
                assert current.json() == {'detail':'Account session expired or unavailable'}
    asyncio.run(original_sequence())
    records = observer.snapshot()['records']
    assert len(records) == 2 and all(row['completed'] and row['normal_return'] and row['released'] for row in records)
    assert not observer.snapshot()['uncertain']
    admission.close()
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}


def test_original_account_owner_copy_is_refused(tmp_path, monkeypatch):
    import copy
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    owner = admission._account_requests
    with pytest.raises(ValueError, match='cannot be copied'):
        copy.copy(owner)
    assert admission._account_requests is owner
    assert observer.snapshot()['uncertain']
    _refused(admission)


def test_reconstructed_account_owner_cannot_borrow_original_binding(tmp_path, monkeypatch):
    app, admission, _ = _login_stack(tmp_path, monkeypatch)
    owner = admission._account_requests
    reconstructed = object.__new__(type(owner))
    reconstructed.__dict__.update(owner.__dict__)
    monkeypatch.setattr(admission, '_account_requests', reconstructed)
    # The real original login still runs, while the reconstructed owner has no
    # original self-binding and cannot grant request lifetime credit.
    assert _login(app).status_code == 200
    _refused(admission)


def test_replaced_login_attempt_method_cannot_inherit_original_source(tmp_path, monkeypatch):
    from backend.engine import original_owned_account_request as module
    app, admission, _ = _login_stack(tmp_path, monkeypatch)
    original = module._LoginAttempt.worker_return
    def changed(*args, **kwargs):
        return original(*args, **kwargs)
    monkeypatch.setattr(module._LoginAttempt, 'worker_return', changed)
    assert _login(app).status_code == 200
    _refused(admission)


def test_replaced_selected_completion_cannot_inherit_original_source(tmp_path, monkeypatch):
    from backend.engine import original_owned_account_request as module
    from starlette.requests import Request
    from backend.api.routes_project import create_project, ProjectCreateRequest
    app, admission, _ = _login_stack(tmp_path, monkeypatch)
    actor = app.state.accounts.users()[0]
    project = create_project(ProjectCreateRequest(name='Owned exact completion'),
        Request({'type':'http','app':app,'state':{'account_user':actor}}))
    app.state.context_registry.register_project(project)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid') as client:
            login = await client.post('/api/accounts/login',json={'username':'fixture-admin','password':'fixture-password-123'})
            assert login.status_code == 200
            monkeypatch.setattr(module._SelectedCurrentAuth,'complete',lambda self,row: True)
            current = await client.get('/api/project/current',headers={
                'authorization':'Bearer '+login.json()['token'],'x-vision-project':project['id']})
            assert current.status_code == 200 and current.json()['id'] == project['id']
    asyncio.run(request())
    _refused(admission)


def _owned_projects(app, observer, *names):
    from starlette.requests import Request
    from backend.api.routes_project import create_project, ProjectCreateRequest
    actor = app.state.accounts.users()[0]
    projects = []
    for name in names:
        # Original data setup only. This constructed Request is outside the
        # observed ASGI stack and grants no request or database lifetime credit.
        project = create_project(ProjectCreateRequest(name=name), Request({
            'type':'http', 'app':app, 'state':{'account_user':actor}}))
        app.state.context_registry.register_project(project)
        projects.append(project)
    assert observer.snapshot()['records'] == []
    return actor, projects


def test_original_login_me_users_select_commits_and_drains(tmp_path, monkeypatch):
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    actor, (first, second) = _owned_projects(
        app, observer, 'Owned account first', 'Owned account second')
    assert app.state.accounts.project_for(actor['id'])['id'] == second['id']

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            login = await client.post('/api/accounts/login', json={
                'username':'fixture-admin', 'password':'fixture-password-123'})
            assert login.status_code == 200 and login.json()['user']['id'] == actor['id']
            headers = {'authorization':'Bearer '+login.json()['token']}
            me = await client.get('/api/accounts/me', headers=headers)
            assert me.status_code == 200
            body = me.json()
            assert body['user']['id'] == actor['id']
            assert {row['id'] for row in body['projects']} == {first['id'], second['id']}
            assert body['selected_project_id'] == second['id']
            assert body['workspace_id'] == app.state.context_registry.workspace_id
            users = await client.get('/api/accounts/users', headers=headers)
            assert users.status_code == 200
            assert [row['id'] for row in users.json()['users']] == [actor['id']]
            assert all('password' not in row for row in users.json()['users'])
            selected = await client.post('/api/accounts/select-project',
                headers=headers, json={'project_id':first['id']})
            assert selected.status_code == 200 and selected.json() == {'project_id':first['id']}

    asyncio.run(original_sequence())
    # Verify the original selection transaction, not merely the HTTP status.
    assert app.state.accounts.project_for(actor['id'])['id'] == first['id']
    records = observer.snapshot()['records']
    assert len(records) == 4 and len({row['identity'] for row in records}) == 4
    assert all(row['completed'] and row['normal_return'] and row['released'] for row in records)
    admission.close()
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}
    final = observer.snapshot()
    assert final['all_settled'] and not final['uncertain']
    assert all(final[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


@pytest.mark.parametrize('explicit_context', [False, True])
def test_original_selected_current_repeats_with_fresh_binding(tmp_path, monkeypatch, explicit_context):
    import json
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    actor, (project,) = _owned_projects(app, observer, 'Owned repeated current')

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            login = await client.post('/api/accounts/login', json={
                'username':'fixture-admin', 'password':'fixture-password-123'})
            assert login.status_code == 200
            headers = {'authorization':'Bearer '+login.json()['token'],
                       'x-vision-project':project['id']}
            first = await client.get('/api/project/current', headers=headers)
            assert first.status_code == 200 and first.json()['id'] == project['id']
            original_context = first.headers['x-vision-context']
            context = json.loads(original_context)
            assert context['actor_id'] == actor['id']
            assert context['project_id'] == project['id']
            assert context['workspace_id'] == app.state.context_registry.workspace_id
            assert context['mode'] == 'team'
            if explicit_context:
                # Echo the actual original producer's header, without minting a
                # context, binding, or request-completion observation in tests.
                headers['x-vision-context'] = original_context
            for _ in range(2):
                current = await client.get('/api/project/current', headers=headers)
                assert current.status_code == 200 and current.json()['id'] == project['id']
                assert json.loads(current.headers['x-vision-context']) == context

    asyncio.run(original_sequence())
    records = observer.snapshot()['records']
    assert len(records) == 4 and len({row['identity'] for row in records}) == 4
    assert all(row['completed'] and row['normal_return'] and row['released'] for row in records)
    admission.close()
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}
    final = observer.snapshot()
    assert final['all_settled'] and not final['uncertain']
    named = admission._current_project_requests.snapshot()
    assert named['pending'] == 0 and len(named['records']) == 3
    assert all(row['settled'] and not row['failed'] and row['handler_calls'] == 1
               and row['awaited_worker_return'] and row['storage_scopes'] == 1
               for row in named['records'])
    assert all(final[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


def test_original_select_transaction_abort_never_settles(tmp_path, monkeypatch):
    from contextlib import closing
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    actor, (first, second) = _owned_projects(
        app, observer, 'Owned abort first', 'Owned abort second')
    # A real SQLite failure inside the unchanged original mutation transaction.
    # No connection, account method, handler or witness is replaced.
    with closing(sqlite3.connect(app.state.accounts.path)) as database:
        with database:
            database.execute("CREATE TRIGGER owned_selection_abort BEFORE INSERT ON selections "
                "BEGIN SELECT RAISE(ABORT, 'owned fixture selection blocked'); END")

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            login = await client.post('/api/accounts/login', json={
                'username':'fixture-admin', 'password':'fixture-password-123'})
            assert login.status_code == 200
            with pytest.raises(sqlite3.IntegrityError, match='owned fixture selection blocked'):
                await client.post('/api/accounts/select-project',
                    headers={'authorization':'Bearer '+login.json()['token']},
                    json={'project_id':first['id']})

    asyncio.run(original_sequence())
    assert app.state.accounts.project_for(actor['id'])['id'] == second['id']
    records = observer.snapshot()['records']
    assert len(records) == 2 and records[0]['completed'] and records[0]['normal_return']
    assert not records[1]['normal_return'] and observer.snapshot()['uncertain']
    _refused(admission)
    assert all(observer.snapshot()[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


def test_original_me_send_failure_never_settles(tmp_path, monkeypatch):
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    response = _login(app)
    assert response.status_code == 200
    token = response.json()['token']
    scope = {'type':'http', 'asgi':{'version':'3.0'}, 'http_version':'1.1',
             'method':'GET', 'scheme':'http', 'path':'/api/accounts/me',
             'raw_path':b'/api/accounts/me', 'query_string':b'', 'root_path':'',
             'headers':[(b'authorization',('Bearer '+token).encode('ascii'))],
             'client':('127.0.0.1',1), 'server':('owned.invalid',80)}
    async def original_request():
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request', 'body':b'', 'more_body':False}
            return {'type':'http.disconnect'}
        async def send(message):
            if message['type']=='http.response.body':
                raise OSError('owned fixture account response transport failed')
        await app(scope,receive,send)
    with pytest.raises(OSError, match='owned fixture account response transport failed'):
        asyncio.run(original_request())
    records = observer.snapshot()['records']
    assert len(records) == 2 and records[0]['completed'] and records[0]['normal_return']
    assert not records[1]['normal_return'] and observer.snapshot()['uncertain']
    _refused(admission)
    assert all(observer.snapshot()[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


def test_replaced_account_inherited_worker_method_cannot_borrow_source(tmp_path, monkeypatch):
    from backend.engine import original_owned_account_request as module
    app, admission, _ = _login_stack(tmp_path, monkeypatch)
    login = _login(app)
    assert login.status_code == 200
    original = module._AccountAttempt.worker_original
    def delegated(attempt):
        # The real original method still runs. The additional runtime subclass
        # method must not inherit the admitted helper's source identity.
        return original(attempt)
    monkeypatch.setattr(module._AccountAttempt,'worker_original',delegated)
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            return await client.get('/api/accounts/me', headers={
                'authorization':'Bearer '+login.json()['token']})
    response = asyncio.run(original_request())
    assert response.status_code == 200
    assert response.json()['user']['id'] == login.json()['user']['id']
    _refused(admission)
