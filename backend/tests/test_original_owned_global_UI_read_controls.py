"""Root-only real-ASGI controls for the finite GLOBAL3 ancillary extension.

The existing real factory/account/context fixture and original handlers run.
No handler result, DB, ownership record or transport completion is fabricated.
The only source substitutions are deliberately late instance delegates; the DB
fault is a real SQLite trigger and the send failure is the actual ASGI sender.
"""
import asyncio
from pathlib import Path
from types import MethodType

import httpx
import pytest

from backend.tests.test_original_owned_flow_lifetime import (
    _stack, _login_current, _no_whole_credit,
)


def _fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'runtime-user-data'))
    result = _stack(tmp_path, monkeypatch)
    app, admission, observer, actor, project, graph, seed, recipe, audit = result
    from backend.api import routes_flowchart as flows
    root = Path(project['project_dir'])
    protected = {file:file.read_bytes() for file in (
        root/'project.json', recipe, flows._active_flow_file(root), flows._version_dir(root)/(seed+'.json'))}
    return result, protected


def _refused(admission, observer):
    admission.close()
    result = admission.drain(.03)
    assert result['status'] == 'refused' and result['active_scopes'] >= 1
    assert 'uncovered_request_protocol' in result['unsupported']
    _no_whole_credit(observer)


@pytest.mark.parametrize('path,method', (
    ('/api/project/list', 'projects_for'),
    ('/api/job-events', 'newest_event'),
))
def test_global_original_resource_instance_delegate_after_real_result_refuses(path, method, tmp_path, monkeypatch):
    (app, admission, observer, actor, project, graph, seed, recipe, audit), protected = _fixture(tmp_path, monkeypatch)
    changes = []

    async def original_app(scope, receive, send):
        async def actual_send(message):
            if scope.get('path') == path and message['type'] == 'http.response.start':
                owner = admission._UI_ancillary_requests
                attempt = next(row for row in owner.records if row.scope is scope)
                resource = owner.accounts.store if method == 'projects_for' else attempt.global_store
                original = getattr(resource, method)
                def delegate(self, *args, **kwargs):
                    return original(*args, **kwargs)
                monkeypatch.setattr(resource, method, MethodType(delegate, resource))
                changes.append((resource, original))
            await send(message)
        await app(scope, receive, actual_send)

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get(path, headers=headers)
            assert response.status_code == 200
            body = response.json()
            if path == '/api/project/list':
                assert [row['id'] for row in body['projects']] == [project['id']]
            else:
                assert body['events'] == [] and body['reset'] is True and body['reason'] == 'initial'
    asyncio.run(original_sequence())
    assert len(changes) == 1
    assert all(file.read_bytes() == raw for file, raw in protected.items())
    attempt = next(row for row in admission._UI_ancillary_requests.records if row.activity.ingress['path'] == path)
    row = observer._records[attempt.activity.token]
    assert row['carrier'] is attempt.activity
    assert row['response_status'] == 200 and row['released'] and row['normal_return'] and row['terminal_forwarded']
    _refused(admission, observer)


def test_initial_job_cursor_real_sqlite_abort_never_borrows_transaction_completion(tmp_path, monkeypatch):
    (app, admission, observer, actor, project, graph, seed, recipe, audit), protected = _fixture(tmp_path, monkeypatch)
    from backend.engine import job_store
    store = job_store.ledger()
    with store._connect() as db:
        db.execute("DELETE FROM meta WHERE key='ledger_id'")
        db.execute("""CREATE TRIGGER refuse_original_cursor_identity BEFORE INSERT ON meta
            WHEN NEW.key='ledger_id' BEGIN SELECT RAISE(ABORT,'original cursor fixture refusal'); END""")

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/job-events', headers=headers)
            assert response.status_code == 503
            assert response.json()['detail'].startswith('The job ledger could not be read:')
    asyncio.run(original_sequence())
    with store._connect() as db:
        assert db.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone() is None
    assert all(file.read_bytes() == raw for file, raw in protected.items())
    _refused(admission, observer)


def test_initial_job_cursor_real_asgi_send_failure_keeps_completed_db_scope_unsettled(tmp_path, monkeypatch):
    (app, admission, observer, actor, project, graph, seed, recipe, audit), protected = _fixture(tmp_path, monkeypatch)
    sent = []

    async def original_app(scope, receive, send):
        async def actual_send(message):
            if scope.get('path') == '/api/job-events':
                sent.append(message['type'])
                if message['type'] == 'http.response.body':
                    raise OSError('original global cursor ASGI send fixture')
            await send(message)
        await app(scope, receive, actual_send)

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            with pytest.raises(OSError, match='original global cursor ASGI send fixture'):
                await client.get('/api/job-events', headers=headers)
    asyncio.run(original_sequence())
    assert sent == ['http.response.start', 'http.response.body']
    attempt = next(row for row in admission._UI_ancillary_requests.records if row.name == 'job_events')
    assert [name for name, _ in attempt.global_jobs] == ['ledger_id', 'newest_event']
    assert all(closed for _, _, closed in attempt.global_databases)
    assert all(file.read_bytes() == raw for file, raw in protected.items())
    _refused(admission, observer)
