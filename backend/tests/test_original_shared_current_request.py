"""Original denied shared-current scopes use real owned SQLite and ASGI."""
import asyncio
import sqlite3
import copy
import time
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def shared_app(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(tmp_path / 'projects'), shared_auth_dir=str(tmp_path / 'auth'))
    return app, admission, observer


async def current(app, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
        return await client.get('/api/project/current', headers={'x-vision-token': app.state.api_token}, **kwargs)


def test_original_denied_shared_current_closes_real_workspace_and_auth_db_contexts(shared_app):
    app, admission, observer = shared_app
    response = asyncio.run(current(app))
    assert response.status_code == 401
    assert response.json() == {'detail': 'Account session expired or unavailable'}
    # Check only the test's own temporary data. No real account store is used.
    with sqlite3.connect(app.state.accounts.path) as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
        workspace = db.execute("SELECT value FROM account_meta WHERE name='context_workspace_id'").fetchone()[0]
    assert workspace == app.state.context_registry.workspace_id
    assert not (app.state.project_dir / 'default_project').exists()
    admission.close()
    # Removing the original shared DB/401 observation makes this refusal fail.
    assert admission.drain(.03) == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    assert observer.snapshot()['all_settled']
    shared = admission._shared_denials.owner.snapshot()
    assert shared['records'] == [{'settled': True, 'middleware_calls': 1,
        'workspace_returns': 1, 'authentication_rejections': 1, 'database_context_exits': 2}]
    assert all(shared[k] is False for k in ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


def test_original_keyword_authenticate_without_observer_retains_value_error(tmp_path):
    from backend.engine.shared_accounts import AccountStore
    store = AccountStore(tmp_path / 'accounts.sqlite')
    with pytest.raises(ValueError, match='Session expired or unavailable'):
        store.authenticate(token='')


def test_original_keyword_bind_workspace_without_observer_retains_write_and_refusal(tmp_path):
    from backend.engine.shared_accounts import AccountStore
    store = AccountStore(tmp_path / 'accounts.sqlite')
    assert store.bind_workspace(workspace_id='own-keyword-workspace') is None
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT value FROM account_meta WHERE name='context_workspace_id'").fetchone()[0] == 'own-keyword-workspace'
    with pytest.raises(ValueError, match='Account workspace differs from the configured server'):
        store.bind_workspace(workspace_id='changed-own-keyword-workspace')


def refused(admission):
    admission.close()
    result = admission.drain(.03)
    assert result['status'] == 'refused'
    assert 'uncovered_request_protocol' in result['unsupported']
    return result


@pytest.mark.parametrize('headers,query', [({'authorization': 'Bearer owned-invalid'}, ''),
    ({'cookie': 'vision_session=owned-invalid'}, ''), ({'x-vision-project': 'owned'}, ''),
    ({'x-vision-context': 'owned'}, ''), ({}, '?owned=1')])
def test_other_credential_selector_and_query_paths_remain_uncovered(shared_app, headers, query):
    app, admission, _ = shared_app
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            await client.get('/api/project/current' + query, headers=headers)
    asyncio.run(scenario())
    assert admission._current_project_requests.shared.snapshot()['records'] == []
    if query:
        # A query is not a writer exemption. This new protocol observes the
        # actual rejection BEFORE any query parsing or downstream call.
        admission.close()
        assert admission.drain(.03) == {'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
        record = admission._shared_denials.records[0]
        assert record.settled and record.auth.database_exits == 2 and record.auth.authentication_rejections == 1
    else:
        assert admission._shared_denials.snapshot()['records'] == []
        refused(admission)


def test_second_denied_current_is_not_rebound_to_the_first_attempt(shared_app):
    app, admission, _ = shared_app
    async def scenario():
        assert (await current(app)).status_code == 401
        assert (await current(app)).status_code == 401
    asyncio.run(scenario())
    assert admission._current_project_requests.shared.snapshot()['records'] == []
    records = admission._shared_denials.records
    assert len(records) == 2 and records[0] is not records[1] and records[0].auth is not records[1].auth
    assert all(row.settled and row.auth.database_exits == 2 and row.auth.authentication_rejections == 1 for row in records)
    admission.close()
    assert admission.drain(.03) == {'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}


def test_prior_unsupported_reason_survives_shared_denied_settlement(shared_app):
    app, admission, _ = shared_app
    admission.uncovered('startup_background_protocols')
    assert asyncio.run(current(app)).status_code == 401
    admission.close()
    assert admission.drain(.03) == {'status': 'refused', 'active_scopes': 0,
        'unsupported': ['startup_background_protocols']}


@pytest.mark.parametrize('changed', ['store', 'path_object', 'authenticate', 'db', 'class_code', 'sqlite_connect', 'shared_method', 'workspace', 'file_inode'])
def test_changed_account_store_function_and_path_cannot_settle(shared_app, monkeypatch, changed):
    app, admission, _ = shared_app
    from backend.engine.shared_accounts import AccountStore
    from backend.engine.original_shared_current_project import _SharedAttempt
    store = app.state.accounts
    if changed == 'store':
        monkeypatch.setattr(app.state, 'accounts', AccountStore(store.path.parent / 'foreign.sqlite'))
    elif changed == 'path_object': monkeypatch.setattr(store, 'path', Path(str(store.path)))
    elif changed in {'authenticate', 'db'}:
        name = 'authenticate' if changed == 'authenticate' else '_db'
        original = getattr(AccountStore, name)
        def replacement(self, *args): return original(self, *args)
        monkeypatch.setattr(AccountStore, name, replacement)
    elif changed == 'class_code':
        def changed_code(self, value): raise ValueError('owned test refusal')
        monkeypatch.setattr(AccountStore.authenticate.__wrapped__, '__code__', changed_code.__code__)
    elif changed == 'sqlite_connect':
        original = sqlite3.connect
        monkeypatch.setattr(sqlite3, 'connect', lambda *args, **kwargs: original(*args, **kwargs))
    elif changed == 'shared_method':
        original = _SharedAttempt.complete
        monkeypatch.setattr(_SharedAttempt, 'complete', lambda self, row: original(self, row))
    elif changed == 'workspace':
        monkeypatch.setattr(app.state.context_registry, 'workspace_id', 'new-owned-test-workspace')
    else:
        previous = store.path.with_name('original-preserved.sqlite')
        store.path.rename(previous)
        AccountStore(store.path)
    response = asyncio.run(current(app))
    assert response.status_code == 401
    assert admission._current_project_requests.shared.snapshot()['records'] == []
    refused(admission)


def test_shared_observer_copy_is_sticky_before_request(shared_app):
    app, admission, _ = shared_app
    with pytest.raises(ValueError): copy.copy(admission._current_project_requests.shared)
    assert asyncio.run(current(app)).status_code == 401
    refused(admission)


def test_real_original_workspace_binding_failure_keeps_the_pending_scope(shared_app):
    app, admission, observer = shared_app
    # Original bind_workspace rejects mismatched persistent identity inside its
    # actual DB context. Authentication and workspace security are unchanged.
    with sqlite3.connect(app.state.accounts.path) as db:
        db.execute("INSERT INTO account_meta VALUES('context_workspace_id',?)", ('foreign-owned-test',))
    assert asyncio.run(current(app)).status_code == 401
    assert refused(admission)['active_scopes'] == 1
    assert observer.snapshot()['uncertain']


@pytest.mark.parametrize('failure', ['send', 'cancel', 'late'])
def test_original_response_error_cancellation_and_late_return_never_settle(shared_app, failure):
    app, admission, observer = shared_app
    async def scenario():
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
            'method': 'GET', 'scheme': 'http', 'path': '/api/project/current',
            'raw_path': b'/api/project/current', 'query_string': b'',
            'headers': [(b'x-vision-token', app.state.api_token.encode())],
            'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', 2)}
        async def receive(): return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            if message['type'] == 'http.response.body':
                if failure == 'send': raise OSError('owned transport failed')
                if failure == 'cancel': raise asyncio.CancelledError()
                admission.close()
                observed = await asyncio.to_thread(admission.drain, .025)
                assert observed['status'] == 'refused' and observed['active_scopes'] == 2
        if failure == 'late': await app(scope, receive, send)
        else:
            with pytest.raises(OSError if failure == 'send' else asyncio.CancelledError):
                await app(scope, receive, send)
    asyncio.run(scenario())
    assert refused(admission)['active_scopes'] == 1
    assert observer.snapshot()['uncertain']
