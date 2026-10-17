"""One real original shared factory/SQLite/ASGI denial, not native proof.

Only the owned-admission supplier is controlled, as in the original desktop
component test. The actual AccountStore/auth body, middleware chain, response
and ASGI transport run unchanged. No app lifespan/server/model/UI is launched.
"""
import asyncio

import httpx


def test_original_owned_shared_onboarding_denial_closes_exact_lifetime(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver

    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(tmp_path/'owned-projects'),
                          shared_auth_dir=str(tmp_path/'owned-auth'))
    assert app.state.accounts is not None
    # The actual installed shared lane must not masquerade as desktop auth.
    assert admission._desktop_denials is None

    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            # Empty query deliberately isolates shared auth from query guards.
            return await client.get('/api/onboarding')

    response = asyncio.run(original_request())
    assert response.status_code == 401
    assert response.json() == {'detail':'Account session expired or unavailable'}
    rows = observer.snapshot()['records']
    assert len(rows) == 1 and rows[0]['completed'] and rows[0]['normal_return']
    assert not observer.snapshot()['uncertain']

    admission.close()
    # RED is expected here on immutable R742: its shared lane has no matching
    # original typed denial lifetime. A mere completed status401 is insufficient.
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}
    final = observer.snapshot()
    assert final['all_settled'] and not final['uncertain']
    assert all(final[key] is False for key in (
        'whole_writer_coverage', 'process_tree_exit_verified',
        'can_release_launch_lease'))


import copy
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def shared_denial_app(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(tmp_path/'projects'), shared_auth_dir=str(tmp_path/'auth'))
    app.middleware_stack = app.build_middleware_stack()
    return app, admission, observer


def closed_result(admission):
    admission.close()
    return admission.drain(.03)


def denied(admission, active=None):
    value = closed_result(admission)
    assert value['status'] == 'refused'
    assert 'uncovered_request_protocol' in value['unsupported']
    if active is not None: assert value['active_scopes'] == active


def test_sequential_current_onboarding_and_immutable_query_are_distinct_original_denials(shared_denial_app):
    app, admission, observer = shared_denial_app
    paths = ['/api/project/current', '/api/onboarding', '/api/project/current',
             '/api/onboarding?owned_fixture=1', '/api/owned-unregistered?limit=5']
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            for path in paths:
                response = await client.get(path, headers={'x-vision-token':app.state.api_token})
                assert response.status_code == 401
                assert response.json() == {'detail':'Account session expired or unavailable'}
    asyncio.run(scenario())
    assert closed_result(admission) == {'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    adapter = admission._shared_denials
    assert len(adapter.records) == len(paths) and len({id(r) for r in adapter.records}) == len(paths)
    assert all(r.settled and not r.failed for r in adapter.records)
    assert all(r.auth.methods == ['bind_workspace','authenticate'] and r.auth.database_exits == 2
               and r.auth.authentication_rejections == 1 for r in adapter.records)
    assert admission._current_project_requests.snapshot()['records'] == []
    assert observer.snapshot()['all_settled'] and not observer.snapshot()['uncertain']




def test_public_shared_route_without_auth_slice_witness_stays_uncovered(shared_denial_app):
    app, admission, _ = shared_denial_app
    from backend.tests.test_original_desktop_auth_denial import original_get
    assert asyncio.run(original_get(app,path='/api/accounts/config'))[0]['status'] == 200
    denied(admission, active=1)
    assert admission._shared_denials.records[0].auth is None




def test_fake_status401_without_original_auth_source_cannot_settle(shared_denial_app, monkeypatch):
    app, admission, _ = shared_denial_app
    from backend.api.shared_authorization import SharedAuthorizationMiddleware
    from starlette.responses import JSONResponse
    from backend.tests.test_original_desktop_auth_denial import original_get
    async def foreign(self,scope,receive,send):
        await JSONResponse({'detail':'Account session expired or unavailable'},status_code=401)(scope,receive,send)
    monkeypatch.setattr(SharedAuthorizationMiddleware,'__call__',foreign)
    assert asyncio.run(original_get(app))[0]['status'] == 401
    denied(admission, active=0)
    assert admission._shared_denials.snapshot()['records'] == []






def test_actual_SQLite_exception_propagates_and_never_closes_pending(shared_denial_app):
    app, admission, observer = shared_denial_app
    from backend.tests.test_original_desktop_auth_denial import original_get
    with sqlite3.connect(app.state.accounts.path) as db:
        db.execute("CREATE TRIGGER owned_deny_workspace BEFORE INSERT ON account_meta WHEN NEW.name='context_workspace_id' BEGIN SELECT RAISE(ABORT,'owned fixture DB failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='owned fixture DB failure'):
        asyncio.run(original_get(app))
    denied(admission,active=1)
    assert observer.snapshot()['uncertain']




@pytest.mark.parametrize('changed',['query','equal_headers','copy'])
def test_inflight_query_header_identity_or_attempt_copy_never_settles(shared_denial_app,changed):
    app, admission, _ = shared_denial_app
    from backend.tests.test_original_desktop_auth_denial import original_get
    async def on_send(scope,message):
        if message['type'] != 'http.response.body':return
        if changed == 'query':scope['query_string']=b'changed-owned=1'
        elif changed == 'equal_headers':scope['headers']=list(scope['headers'])
        else:
            with pytest.raises(ValueError):copy.copy(admission._shared_denials.records[0])
    assert asyncio.run(original_get(app,on_send=on_send))[0]['status'] == 401
    denied(admission,active=1)


@pytest.mark.parametrize('foreign_task',[False,True])
def test_manual_replayed_auth_context_has_no_original_caller_authority(shared_denial_app,foreign_task):
    app, admission, _ = shared_denial_app
    from backend.engine.original_shared_auth_denial import original_shared_denial_authentication
    from backend.tests.test_original_desktop_auth_denial import original_get
    async def manual(scope):
        async def receive():return {'type':'http.request','body':b'','more_body':False}
        async def send(message):pass
        from starlette.datastructures import Headers
        with original_shared_denial_authentication(admission._shared_denials.auth,scope,receive,send,
                app.state.accounts,Headers(raw=list(scope['headers']))):
            pass
    async def on_send(scope,message):
        if message['type'] != 'http.response.body':return
        if foreign_task:await asyncio.create_task(manual(scope))
        else:await manual(scope)
    assert asyncio.run(original_get(app,on_send=on_send))[0]['status'] == 401
    denied(admission,active=1)


def test_authenticated_get_and_logout_writer_are_not_shared_denial_coverage(shared_denial_app):
    app, admission, _ = shared_denial_app
    store=app.state.accounts
    store.bootstrap('owned-admin','owned-password-123')
    store.bind_workspace(app.state.context_registry.workspace_id)
    token=store.login('owned-admin','owned-password-123')['token']
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid')as client:
            headers={'authorization':'Bearer '+token}
            created=await client.post('/api/project/create',headers=headers,json={'name':'Owned authenticated current'})
            assert created.status_code==200
            current=await client.get('/api/project/current',headers=headers)
            assert current.status_code==200 and current.json()['id']==created.json()['id']
            assert (await client.get('/api/accounts/me',headers=headers)).status_code==200
            response=await client.post('/api/accounts/logout',headers=headers)
            assert response.status_code==200 and response.json()=={'logged_out':True}
    asyncio.run(scenario())
    assert admission._shared_denials.snapshot()['records']==[]
    denied(admission,active=0)
    with pytest.raises(ValueError):store.authenticate(token)


@pytest.mark.parametrize('method',['HEAD','POST'])
def test_other_original_shared_methods_are_not_denial_protocols(shared_denial_app,method):
    app,admission,_=shared_denial_app
    from backend.tests.test_original_desktop_auth_denial import original_get
    assert asyncio.run(original_get(app,method=method))[0]['status']==401
    assert admission._shared_denials.snapshot()['records']==[]
    denied(admission,active=0)


def test_original_shared_websocket1008_is_not_new_HTTP_coverage(shared_denial_app):
    app,admission,_=shared_denial_app
    async def scenario():
        scope={'type':'websocket','asgi':{'version':'3.0'},'http_version':'1.1','scheme':'ws',
            'path':'/api/onboarding','raw_path':b'/api/onboarding','query_string':b'',
            'headers':[],'subprotocols':[],'client':('127.0.0.1',1),'server':('127.0.0.1',2)}
        frames=[]
        async def receive():return {'type':'websocket.connect'}
        async def send(message):frames.append(message)
        await app(scope,receive,send)
        assert frames==[{'type':'websocket.close','code':1008}]
    asyncio.run(scenario())
    assert admission._shared_denials.snapshot()['records']==[]
    denied(admission,active=0)
