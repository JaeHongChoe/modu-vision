"""Original authenticated preflight metadata-list lifetimes, Root execution only.

Data-only PreflightStore.save fixtures exercise list projection/filtering; they
are not generated preflight evidence or a claim about report validity. No model,
device, remote check, report-read or POST-preflight action runs in these cases.
"""
import asyncio
import hashlib
import json
from pathlib import Path
from types import FunctionType

import httpx
import pytest

from backend.engine.flow_preflight import PreflightStore
from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit


PATH = '/api/export/flow/preflights'
RAW9 = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size',
        'st_mtime_ns', 'st_ctime_ns', 'st_uid', 'st_gid')


def _pin(paths):
    rows = {}
    for path in paths:
        before = path.lstat()
        assert not path.is_symlink()
        raw = path.read_bytes()
        identity = tuple(getattr(before, key) for key in RAW9)
        assert identity == tuple(getattr(path.lstat(), key) for key in RAW9)
        rows[path] = (identity, hashlib.sha256(raw).hexdigest())
    return rows


def _metadata(report_id, version, checked_at):
    return {'report_id': report_id, 'checked_at': checked_at, 'status': 'blocked',
            'recipe_release': {'kind': 'saved_flow', 'version_id': version},
            'target_identity': {'kind': 'edge', 'os': 'linux', 'architecture': 'x86_64'},
            'counts': {'missing': 1}, 'not_a_list_field': 'not returned'}


def _projection(report_id, version, checked_at):
    return {'report_id': report_id, 'checked_at': checked_at, 'status': 'blocked',
            'recipe_release': {'kind': 'saved_flow', 'version_id': version},
            'target_identity': {'kind': 'edge', 'os': 'linux', 'architecture': 'x86_64'},
            'counts': {'missing': 1}}


def _refused(admission, observer):
    _no_whole_credit(observer)
    admission.close()
    assert admission.drain(.03)['status'] == 'refused'
    assert 'uncovered_request_protocol' in admission.snapshot()['unsupported']


@pytest.mark.parametrize('mode', ('absent', 'empty', 'populated', 'filtered'))
def test_original_preflight_list_metadata_and_closed_HTTP_drain(tmp_path, monkeypatch, mode):
    from backend.api import routes_flowchart as flows
    app, admission, observer, _, project, _, seed, recipe, _ = _stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    folder = root / 'deployment_preflight'
    protected = [root / 'project.json', recipe, flows._active_flow_file(root),
                 flows._version_dir(root) / (seed + '.json')]
    if mode == 'empty':
        folder.mkdir()
    elif mode in ('populated', 'filtered'):
        store = PreflightStore(folder)
        protected += [store.save(_metadata('a' * 32, seed, '2026-10-19T00:00:01Z')),
                      store.save(_metadata('b' * 32, '0' * 32, '2026-10-19T00:00:02Z'))]
    older = _projection('a' * 32, seed, '2026-10-19T00:00:01Z')
    newer = _projection('b' * 32, '0' * 32, '2026-10-19T00:00:02Z')
    wanted = {'reports': [older] if mode == 'filtered' else [newer, older]
              if mode == 'populated' else []}

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            before = _pin(protected)
            response = await client.get(PATH, headers=headers,
                                        params={'version_id': seed} if mode == 'filtered' else {})
            assert response.status_code == 200 and response.json() == wanted
            assert json.loads(response.headers['x-vision-context']) == json.loads(headers['x-vision-context'])
            assert _pin(protected) == before
    asyncio.run(scenario())
    row, = [row for row in observer._records.values()
            if row['ingress'] == {'type': 'http', 'method': 'GET', 'path': PATH}]
    assert all(row[key] for key in ('completed', 'normal_return', 'released', 'start_forwarded', 'terminal_forwarded'))
    assert row['response_status'] == 200 and not row['framing_error'] and not row['transport_failed']
    snapshot = observer.snapshot()
    assert snapshot['failures'] == [] and not snapshot['uncertain'] and snapshot['pending_requests'] == 0
    _no_whole_credit(observer)
    admission.close()
    assert admission.drain(.03) == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}


@pytest.mark.parametrize('mode', ('corrupt', 'linked-file', 'linked-folder', 'clone-before-factory',
                                  'delegate-after-factory', 'result-after-return', 'file-after-return'))
def test_unproved_list_sources_or_mutated_custody_cannot_borrow_completion(tmp_path, monkeypatch, mode):
    if mode == 'clone-before-factory':
        original = PreflightStore.list
        cloned = FunctionType(original.__code__, original.__globals__, original.__name__,
                              original.__defaults__, original.__closure__)
        monkeypatch.setattr(PreflightStore, 'list', cloned)
    app, admission, observer, _, project, _, seed, _, _ = _stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    folder = root / 'deployment_preflight'
    wanted = {'reports': []}
    report_path = None
    if mode in ('linked-file', 'linked-folder'):
        outside = tmp_path / 'other-project-metadata'
        external = PreflightStore(outside).save(_metadata('c' * 32, seed, '2026-10-19T00:00:01Z'))
        if mode == 'linked-folder':
            folder.symlink_to(outside, target_is_directory=True)
        else:
            folder.mkdir()
            (folder / external.name).symlink_to(external)
        wanted = {'reports': [_projection('c' * 32, seed, '2026-10-19T00:00:01Z')]}
    elif mode == 'corrupt':
        folder.mkdir()
        (folder / ('d' * 32 + '.json')).write_text('{', encoding='utf-8')
    elif mode == 'file-after-return':
        report_path = PreflightStore(folder).save(_metadata('e' * 32, seed, '2026-10-19T00:00:01Z'))
        wanted = {'reports': [_projection('e' * 32, seed, '2026-10-19T00:00:01Z')]}
    mutated = []
    async def transport(scope, receive, send):
        async def forwarded(message):
            if scope.get('path') == PATH and message.get('type') == 'http.response.start':
                if mode == 'result-after-return':
                    attempt, = [item for item in admission._UI_ancillary_requests.pending.values() if item.scope is scope]
                    attempt.result['reports'].append({'changed': True})
                    mutated.append(mode)
                elif mode == 'file-after-return':
                    report_path.write_text('{}', encoding='utf-8')
                    mutated.append(mode)
            await send(message)
        await app(scope, receive, forwarded)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=transport), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if mode == 'delegate-after-factory':
                original = PreflightStore.list
                def delegated(store):
                    return original(store)
                monkeypatch.setattr(PreflightStore, 'list', delegated)
            response = await client.get(PATH, headers=headers)
            assert response.status_code == 200 and response.json() == wanted
    asyncio.run(scenario())
    if mode.endswith('after-return'):
        assert mutated == [mode]
    _refused(admission, observer)


def test_foreign_explicit_context_keeps_original_denial_and_never_enters_store(tmp_path, monkeypatch):
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            context = json.loads(headers['x-vision-context'])
            context['actor_id'] = 'f' * 32
            headers['x-vision-context'] = json.dumps(context)
            response = await client.get(PATH, headers=headers)
            assert response.status_code == 403
            assert response.json() == {'detail': 'Explicit context does not match the authenticated account workspace'}
    asyncio.run(scenario())
    attempts = [item for item in admission._UI_ancillary_requests.records if item.name == 'list_flow_preflights']
    assert len(attempts) == 1 and attempts[0].preflight_store is None
    _refused(admission, observer)


def test_original_preflight_list_send_failure_is_not_completion(tmp_path, monkeypatch):
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'GET',
                 'scheme': 'http', 'path': PATH, 'raw_path': PATH.encode(), 'query_string': b'',
                 'root_path': '', 'headers': [(key.encode(), value.encode()) for key, value in headers.items()],
                 'client': ('127.0.0.1', 1), 'server': ('owned.invalid', 80)}
        used = False
        async def receive():
            nonlocal used
            if not used:
                used = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            return {'type': 'http.disconnect'}
        async def send(message):
            if message['type'] == 'http.response.body':
                raise OSError('original preflight list send failed')
        await app(scope, receive, send)
    with pytest.raises(OSError, match='original preflight list send failed'):
        asyncio.run(scenario())
    row, = [row for row in observer._records.values()
            if row['ingress'] == {'type': 'http', 'method': 'GET', 'path': PATH}]
    assert row['response_status'] == 200 and row['transport_failed'] and not row['normal_return']
    _refused(admission, observer)
