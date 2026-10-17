"""Real current-project ASGI discovery, filesystem and SQLite effects only."""
import asyncio
import json
import copy
from pathlib import Path
import threading

import httpx
import pytest


@pytest.fixture
def owned_app(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    root = tmp_path / 'owned-projects'
    app = main.create_app(project_dir=str(root))
    return root, app, admission, observer


async def current(app, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
        return await client.get('/api/project/current', headers={'x-vision-token': app.state.api_token}, **kwargs)


def adapter(admission):
    assert admission._current_project_requests is not None
    return admission._current_project_requests


def refused(admission):
    admission.close()
    result = admission.drain(.03)
    assert result['status'] == 'refused'
    assert 'uncovered_request_protocol' in result['unsupported']
    return result


def test_fresh_current_project_real_publications_and_registration_can_settle_named_scope(owned_app):
    root, app, admission, observer = owned_app
    with app.state.context_registry._db() as db:
        assert db.execute('SELECT COUNT(*) FROM projects').fetchone()[0] == 0
    response = asyncio.run(current(app))
    assert response.status_code == 200
    project = response.json()
    assert project['project_dir'] == str(root / 'default_project')
    assert json.loads((root / 'default_project/project.json').read_text()) == project
    assert (root / 'default_project/labelsets.json').is_file()
    assert json.loads((root / '.active_project.json').read_text())['project_dir'] == project['project_dir']
    assert json.loads((root / '.recent_projects.json').read_text())[0]['id'] == project['id']
    with app.state.context_registry._db() as db:
        assert db.execute('SELECT COUNT(*) FROM projects').fetchone()[0] == 1
    assert observer.snapshot()['records'][0]['completed']
    admission.close()
    # Removing the exact named handler/effect adapter must make this fail.
    assert admission.drain(.03) == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    snap = observer.snapshot()
    assert snap['all_settled'] and not snap['uncertain']
    assert all(snap[key] is False for key in ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
    named = adapter(admission).snapshot()
    assert named['pending'] == 0 and named['records'] == [{'settled': True, 'failed': False,
        'handler_calls': 1, 'awaited_worker_return': True, 'storage_scopes': 1,
        'resolution_calls': 3, 'publications': 4, 'database_context_exits': 3}]


@pytest.mark.parametrize('method,path', [('POST', '/api/project/current'), ('GET', '/api/errors'),
    ('GET', '/api/errors/ERR_001'), ('GET', '/uncovered-path'), ('OPTIONS', '/api/project/current'),
    ('GET', '/api/project/current?unqualified=1')])
def test_other_real_http_methods_paths_and_query_are_sticky(owned_app, method, path):
    _, app, admission, _ = owned_app
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            await client.request(method, path, headers={'x-vision-token': app.state.api_token})
    asyncio.run(scenario())
    assert adapter(admission).snapshot()['records'] == []
    assert refused(admission)['active_scopes'] == 0


def test_original_websocket_remains_uncovered(owned_app):
    _, app, admission, _ = owned_app
    async def scenario():
        scope = {'type': 'websocket', 'asgi': {'version': '3.0'}, 'scheme': 'ws', 'path': '/api/project/current',
            'raw_path': b'/api/project/current', 'query_string': b'', 'headers': [(b'x-vision-token', app.state.api_token.encode())],
            'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', 2), 'subprotocols': []}
        async def receive(): return {'type': 'websocket.connect'}
        frames = []
        async def send(message): frames.append(message)
        await app(scope, receive, send)
        assert frames[-1]['type'] == 'websocket.close'
    asyncio.run(scenario())
    assert adapter(admission).snapshot()['records'] == []
    refused(admission)


def test_prior_unsupported_reason_is_never_cleared_by_named_success(owned_app):
    _, app, admission, _ = owned_app
    admission.uncovered('startup_background_protocols')
    assert asyncio.run(current(app)).status_code == 200
    assert adapter(admission).snapshot()['records'][0]['settled']
    admission.close()
    assert admission.drain(.03) == {'status': 'refused', 'active_scopes': 0, 'unsupported': ['startup_background_protocols']}


def test_second_cached_request_is_a_separate_uncovered_branch(owned_app):
    _, app, admission, _ = owned_app
    async def scenario():
        first = await current(app)
        second = await current(app)
        assert first.status_code == second.status_code == 200 and first.json() == second.json()
    asyncio.run(scenario())
    assert len(adapter(admission).snapshot()['records']) == 1
    refused(admission)


def test_closed_discovery_rejects_before_project_effects(owned_app):
    root, app, admission, observer = owned_app
    admission.close()
    response = asyncio.run(current(app))
    assert response.status_code == 409 and not (root / 'default_project').exists()
    assert adapter(admission).snapshot()['records'] == []
    assert admission.snapshot() == {'active_scopes': 0, 'unsupported': []}
    # Preserve the original observer's refusal of a bind after close.
    assert observer.snapshot()['records'] == [] and observer.snapshot()['uncertain']


def test_plain_admission_or_no_observer_preserves_original_uncovered_behavior(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    admission = BackendWorkAdmission()
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(tmp_path / 'plain'))
    assert asyncio.run(current(app)).status_code == 200
    assert admission._current_project_requests is None
    refused(admission)
    direct = BackendWorkAdmission()
    assert direct.enter({'type': 'http', 'method': 'GET', 'path': '/api/project/current'})
    direct.leave()
    refused(direct)


def test_swallowed_real_history_publication_failure_is_not_a_success(owned_app, monkeypatch):
    from backend.api import routes_project
    root, app, admission, _ = owned_app
    original = routes_project.os.replace
    def failed(source, destination):
        if Path(destination) == root / '.recent_projects.json': raise OSError('owned history publication failed')
        return original(source, destination)
    monkeypatch.setattr(routes_project.os, 'replace', failed)
    response = asyncio.run(current(app))
    assert response.status_code == 200 and not (root / '.recent_projects.json').exists()
    assert (root / 'default_project/project.json').is_file()
    assert refused(admission)['active_scopes'] == 1
    assert adapter(admission).snapshot()['records'][0]['failed']


def test_real_sqlite_registration_trigger_failure_stays_pending(owned_app):
    import sqlite3
    _, app, admission, _ = owned_app
    with app.state.context_registry._db() as db:
        db.execute("CREATE TRIGGER original_failure BEFORE INSERT ON projects BEGIN SELECT RAISE(ABORT,'owned registration rejected'); END")
    with pytest.raises(sqlite3.IntegrityError, match='owned registration rejected'):
        asyncio.run(current(app))
    with app.state.context_registry._db() as db:
        assert db.execute('SELECT COUNT(*) FROM projects').fetchone()[0] == 0
    assert refused(admission)['active_scopes'] == 1


async def raw_current(app, on_start=None):
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'scheme': 'http', 'method': 'GET',
        'path': '/api/project/current', 'raw_path': b'/api/project/current', 'query_string': b'',
        'headers': [(b'x-vision-token', app.state.api_token.encode())], 'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', 2)}
    async def receive(): return {'type': 'http.request', 'body': b'', 'more_body': False}
    frames = []
    async def send(message):
        if message['type'] == 'http.response.start' and on_start is not None: await on_start(scope)
        frames.append(message)
    await app(scope, receive, send)
    return frames


@pytest.mark.parametrize('mode', ['route', 'registry', 'registry_path', 'storage_app', 'scope', 'copy', 'foreign_task'])
def test_actual_original_owner_substitution_copy_or_foreign_effect_cannot_settle(owned_app, mode, monkeypatch):
    from backend.engine.original_current_project_request import CurrentProjectObservationError
    from backend.api import routes_project
    root, app, admission, _ = owned_app
    held = adapter(admission)
    async def at_start(scope):
        attempt = held.records[0]
        assert admission.snapshot()['active_scopes'] == 2
        if mode == 'route': monkeypatch.setattr(held.route, 'endpoint', lambda request: attempt.result)
        elif mode == 'registry': app.state.context_registry = object()
        elif mode == 'registry_path': held.registry.path = Path(str(held.registry.path))
        elif mode == 'storage_app': held.storage.project_app = object()
        elif mode == 'scope': scope['method'] = 'POST'
        elif mode == 'copy':
            with pytest.raises(CurrentProjectObservationError): copy.copy(attempt)
        elif mode == 'foreign_task':
            async def write(): routes_project._write_json(root / 'unqualified-extra.json', {'actual': True})
            await asyncio.create_task(write())
    asyncio.run(raw_current(app, at_start))
    assert refused(admission)['active_scopes'] == 1
    assert held.snapshot()['records'][0]['failed'] and not held.snapshot()['records'][0]['settled']


def test_real_final_body_keeps_pending_until_original_storage_finally_and_asgi_return(owned_app):
    _, app, admission, observer = owned_app
    async def at_start(scope):
        assert observer.snapshot()['pending_requests'] == 1
        assert admission.snapshot()['active_scopes'] == 2
        assert adapter(admission).snapshot()['records'][0]['storage_scopes'] == 0
    asyncio.run(raw_current(app, at_start))
    assert admission.snapshot() == {'active_scopes': 0, 'unsupported': []}


def test_replay_of_settled_original_attempt_is_sticky(owned_app):
    _, app, admission, _ = owned_app
    assert asyncio.run(current(app)).status_code == 200
    attempt = adapter(admission).records[0]
    with admission._condition: assert not attempt.original()
    assert refused(admission)['active_scopes'] == 0


def test_unbalanced_anonymous_leave_cannot_consume_named_pending(owned_app):
    from backend.engine.application_launch_handshake import HandshakeError
    _, app, admission, observer = owned_app
    async def at_start(scope): admission.leave()
    with pytest.raises(HandshakeError, match='unbalanced'): asyncio.run(raw_current(app, at_start))
    assert admission.snapshot()['active_scopes'] == 1
    assert observer.snapshot()['uncertain'] and adapter(admission).snapshot()['pending'] == 1


@pytest.mark.parametrize('mode', ['cancel', 'late', 'original_worker_failure'])
def test_original_awaited_worker_cancellation_failure_or_late_return_is_not_settlement(owned_app, monkeypatch, mode):
    from starlette.datastructures import State
    _, app, admission, _ = owned_app
    started, release, returned = threading.Event(), threading.Event(), threading.Event()
    original = State.__getattr__
    def held(state, key):
        if key == 'scoped_project' and threading.current_thread() is not threading.main_thread():
            started.set()
            try:
                assert release.wait(2), 'owned worker test release missing'
                if mode == 'original_worker_failure': raise ValueError('owned original worker failed')
            finally: returned.set()
        return original(state, key)
    monkeypatch.setattr(State, '__getattr__', held)
    async def scenario():
        task = asyncio.create_task(current(app))
        assert await asyncio.to_thread(started.wait, 1)
        assert admission.snapshot()['active_scopes'] == 2
        if mode == 'cancel': task.cancel()
        elif mode == 'late':
            admission.close()
            result = await asyncio.to_thread(admission.drain, .025)
            assert result['status'] == 'refused' and result['active_scopes'] == 2
        release.set()
        if mode == 'cancel':
            with pytest.raises(asyncio.CancelledError): await task
        elif mode == 'original_worker_failure':
            with pytest.raises(ValueError, match='owned original worker failed'): await task
        else: assert (await task).status_code == 200
        assert await asyncio.to_thread(returned.wait, 1)
    asyncio.run(scenario())
    if mode == 'late':
        result = admission.drain(.03)
        assert result['status'] == 'refused' and result['active_scopes'] == 1
        assert 'uncovered_request_protocol' in result['unsupported']
    else: assert refused(admission)['active_scopes'] == 1


def test_unqualified_real_background_middleware_does_not_gain_named_coverage(owned_app):
    from starlette.background import BackgroundTask
    root, app, admission, _ = owned_app
    class ExtraBackground:
        def __init__(self, app): self.app = app
        async def __call__(self, scope, receive, send):
            await self.app(scope, receive, send)
            await BackgroundTask(lambda: (root / 'unqualified-background.txt').write_text('real background'))()
    app.add_middleware(ExtraBackground)
    assert asyncio.run(current(app)).status_code == 200
    assert (root / 'unqualified-background.txt').read_text() == 'real background'
    assert adapter(admission).snapshot()['records'] == []
    refused(admission)


def test_invalid_open_drain_attempt_does_not_start_a_named_deadline(owned_app):
    from backend.engine.application_launch_handshake import HandshakeError
    _, app, admission, _ = owned_app
    with pytest.raises(HandshakeError, match='close before draining'): admission.drain(.001)
    assert asyncio.run(current(app)).status_code == 200
    admission.close()
    assert admission.drain(.03)['status'] == 'managed_scopes_drained'


@pytest.mark.parametrize('kind', ['maintenance', 'cors', 'exception', 'router'])
@pytest.mark.parametrize('phase', ['before_ingress', 'after_enrollment'])
def test_changed_original_chain_callable_with_real_extra_write_never_settles(owned_app, monkeypatch, kind, phase):
    from backend.engine.migration_guard import ProjectMaintenanceMiddleware
    from fastapi.routing import APIRouter
    from starlette.middleware.cors import CORSMiddleware
    from starlette.middleware.exceptions import ExceptionMiddleware
    root, app, admission, observer = owned_app
    held = adapter(admission)
    app.middleware_stack = app.build_middleware_stack()
    assert held.original()
    chain = tuple(held.chain)
    router = app.router
    cls = {'maintenance': ProjectMaintenanceMiddleware, 'cors': CORSMiddleware,
        'exception': ExceptionMiddleware, 'router': APIRouter}[kind]
    original = cls.__call__
    marker = root / ('actual-' + kind + '-' + phase + '-write.txt')
    calls = []
    async def replacement(self, scope, receive, send):
        calls.append(admission.snapshot()['active_scopes'])
        marker.write_text('real owned unqualified ' + kind + ' replacement write')
        return await original(self, scope, receive, send)
    if phase == 'before_ingress':
        monkeypatch.setattr(cls, '__call__', replacement)
        response = asyncio.run(current(app))
        assert response.status_code == 200 and marker.is_file() and calls
    else:
        async def at_start(scope):
            assert admission.snapshot()['active_scopes'] == 2
            assert observer.snapshot()['pending_requests'] == 1
            monkeypatch.setattr(cls, '__call__', replacement)
            # The original request has already entered this callable. The real
            # request-owned control writes the marker after substitution, with
            # its source body/handler/storage evidence otherwise unchanged.
            marker.write_text('real owned post-enrollment callable substitution')
        asyncio.run(raw_current(app, at_start))
        assert marker.is_file()
    assert tuple(held.chain) == chain and app.router is router
    assert all(type(node) is entry[1] and node.app is entry[2]
        and (entry[3] is None or node.project_app is entry[3]) for entry in chain for node in [entry[0]])
    # The old Source01 causally gave a false clean drain for Maintenance,
    # CORS, Exception and Router, despite a real extra filesystem write.
    result = refused(admission)
    named = held.snapshot()
    if phase == 'before_ingress':
        assert result['active_scopes'] == 0 and named['pending'] == 0 and named['records'] == []
        assert calls == [1]
    else:
        assert result['active_scopes'] == 1 and named['pending'] == 1
        assert named['records'][0]['failed'] and not named['records'][0]['settled']


@pytest.mark.parametrize('kind', ['selected_handle', 'selected_matches', 'inherited_matches', 'earlier_matches'])
@pytest.mark.parametrize('phase', ['before_ingress', 'after_enrollment'])
def test_changed_original_selected_dispatch_with_real_extra_write_never_settles(owned_app, monkeypatch, kind, phase):
    from fastapi.routing import APIRoute
    from starlette.routing import Route
    root, app, admission, observer = owned_app
    held = adapter(admission)
    app.middleware_stack = app.build_middleware_stack()
    assert held.original()
    route = held.route
    original_endpoint, original_app, original_dependency = route.endpoint, route.app, route.dependant.call
    chain = tuple(held.chain)
    marker = root / ('actual-dispatch-' + kind + '-' + phase + '.txt')
    calls = []
    if kind == 'selected_handle':
        original = APIRoute.handle
        async def replacement(self, scope, receive, send):
            if self is route:
                calls.append(admission.snapshot()['active_scopes'])
                marker.write_text('real original selected route handle replacement')
            return await original(self, scope, receive, send)
        target, name = APIRoute, 'handle'
    elif kind in {'selected_matches', 'inherited_matches'}:
        target = APIRoute if kind == 'selected_matches' else Route
        name = 'matches'
        original = target.matches
        def replacement(self, scope):
            if self is route:
                calls.append(admission.snapshot()['active_scopes'])
                marker.write_text('real selected or inherited route matches replacement')
            return original(self, scope)
    else:
        earlier = app.router.routes[0]
        assert earlier is not route and app.router.routes.index(earlier) < app.router.routes.index(route)
        target, name, original = earlier, 'matches', earlier.matches
        def replacement(scope):
            calls.append(admission.snapshot()['active_scopes'])
            marker.write_text('real earlier original route matches replacement')
            return original(scope)
    if phase == 'before_ingress':
        monkeypatch.setattr(target, name, replacement)
        response = asyncio.run(current(app))
        assert response.status_code == 200 and marker.is_file() and calls
    else:
        async def at_start(scope):
            assert admission.snapshot()['active_scopes'] == 2
            assert observer.snapshot()['pending_requests'] == 1
            monkeypatch.setattr(target, name, replacement)
            marker.write_text('real original response-start dispatch substitution')
        asyncio.run(raw_current(app, at_start))
        assert marker.is_file()
    assert route is held.route and type(route) is APIRoute
    assert route.endpoint is original_endpoint and route.app is original_app and route.dependant.call is original_dependency
    assert tuple(held.chain) == chain
    result = refused(admission)
    named = held.snapshot()
    if phase == 'before_ingress':
        assert result['active_scopes'] == 0 and named['records'] == [] and named['pending'] == 0
        assert calls == [1]
    else:
        assert result['active_scopes'] == 1 and named['pending'] == 1
        assert named['records'][0]['failed'] and not named['records'][0]['settled']


@pytest.mark.parametrize('method', ['_db', 'transaction', 'register_project', 'context'])
@pytest.mark.parametrize('phase', ['before_ingress', 'after_enrollment'])
def test_changed_original_registry_bound_method_with_real_write_never_settles(owned_app, monkeypatch, method, phase):
    from backend.contracts.context import ContextRegistry
    root, app, admission, observer = owned_app
    held = adapter(admission)
    app.middleware_stack = app.build_middleware_stack()
    assert held.original()
    registry = held.registry
    original_class = getattr(ContextRegistry, method)
    original_bound = getattr(registry, method)
    marker = root / ('actual-registry-' + method + '-' + phase + '.txt')
    calls = []
    def replacement(*args, **kwargs):
        calls.append(admission.snapshot()['active_scopes'])
        marker.write_text('real original registry instance override write')
        return original_bound(*args, **kwargs)
    if phase == 'before_ingress':
        monkeypatch.setattr(registry, method, replacement)
        assert asyncio.run(current(app)).status_code == 200
        assert marker.is_file() and calls
    else:
        async def at_start(scope):
            assert admission.snapshot()['active_scopes'] == 2 and observer.snapshot()['pending_requests'] == 1
            monkeypatch.setattr(registry, method, replacement)
            marker.write_text('real response-start registry instance substitution')
        asyncio.run(raw_current(app, at_start))
        assert marker.is_file()
    assert app.state.context_registry is registry and getattr(ContextRegistry, method) is original_class
    result = refused(admission)
    named = held.snapshot()
    if phase == 'before_ingress':
        assert result['active_scopes'] == 0 and named['records'] == [] and named['pending'] == 0
        assert calls and all(value == 1 for value in calls)
    else:
        assert result['active_scopes'] == 1 and named['pending'] == 1
        assert named['records'][0]['failed'] and not named['records'][0]['settled']
