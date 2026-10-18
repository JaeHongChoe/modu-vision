"""Real isolated ASGI lifetimes and narrowly targeted refusal controls.

Prepared for Root execution only. Positive cases call real handlers/auth/SQLite
and actual original json.load, without replacing any observation supplier.
Negative controls intentionally change only the stated fixture or callee.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import stat
from types import FunctionType
from urllib.parse import urlencode

import httpx
import pytest

from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit


_RAW9 = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_uid', 'st_gid',
         'st_size', 'st_mtime_ns', 'st_ctime_ns')


def _project_files(root):
    rows = {}
    for path in sorted(root.rglob('*')):
        before = path.lstat()
        assert not path.is_symlink()
        if stat.S_ISDIR(before.st_mode):
            rows[str(path.relative_to(root))] = {'directory': True}
            continue
        assert stat.S_ISREG(before.st_mode) and before.st_nlink == 1
        assert before.st_size <= 4 * 1024 * 1024
        raw = path.read_bytes()
        after = path.lstat()
        assert tuple(getattr(after, key) for key in _RAW9) == tuple(getattr(before, key) for key in _RAW9)
        rows[str(path.relative_to(root))] = {
            'raw9': tuple(getattr(before, key) for key in _RAW9),
            'sha256': hashlib.sha256(raw).hexdigest(),
        }
    assert len(rows) <= 256
    return rows


def _refused(admission, observer):
    _no_whole_credit(observer)
    admission.close()
    assert admission.drain(.03)['status'] == 'refused'
    assert 'uncovered_request_protocol' in admission.snapshot()['unsupported']


@pytest.mark.parametrize('case', ('recipe', 'template'))
def test_real_warm_recipe_and_jobless_template_close_original_owned_lifetimes(tmp_path, monkeypatch, case):
    from backend.api import routes_flowchart as flows
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    monkeypatch.setattr(flows, 'DEFAULT_PIPELINE_FILE', legacy / 'pipeline.json')
    app, admission, observer, _, project, graph, _, recipe, _ = _stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            before = _project_files(root)
            assert _project_files(legacy) == {}
            warm = observer.snapshot()
            assert warm['unsupported'] == [] and warm['failures'] == [] and not warm['uncertain']
            params = {'inspection_task': 'segmentation'}
            path = '/api/flowchart/templates/single-segmentation'
            if case == 'recipe':
                path = '/api/flowchart/pipeline'
                params['source_dataset_path'] = project['source_dataset_dir']
            response = await client.get(path, headers=headers, params=params)
            assert response.status_code == 200 and response.json() == graph.model_dump()
            assert json.loads(response.headers['x-vision-context']) == json.loads(headers['x-vision-context'])
            assert _project_files(root) == before and _project_files(legacy) == {}
            row, = [row for row in observer._records.values() if row['ingress'] == {
                'type': 'http', 'method': 'GET', 'path': path}]
            assert row['response_status'] == 200 and row['error_type'] is None
            assert all(row[key] for key in ('completed', 'normal_return', 'released', 'terminal_forwarded'))

    asyncio.run(scenario())
    snapshot = observer.snapshot()
    assert snapshot['failures'] == [] and snapshot['uncertain'] is False
    assert snapshot['pending_requests'] == 0 and snapshot['unsupported'] == []
    _no_whole_credit(observer)
    admission.close()
    assert admission.drain(.03) == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}


@pytest.mark.parametrize('mode', ('recipe_symlink', 'corrupt_recipe', 'legacy_claim',
                                   'broken_claim', 'bound_template', 'delegated_constructor'))
def test_other_original_business_paths_do_not_borrow_warm_or_jobless_completion(tmp_path, monkeypatch, mode):
    from backend.api import routes_flowchart as flows
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    monkeypatch.setattr(flows, 'DEFAULT_PIPELINE_FILE', legacy / 'pipeline.json')
    app, admission, observer, _, project, graph, _, recipe, _ = _stack(tmp_path, monkeypatch)
    if mode == 'recipe_symlink':
        real = recipe.with_suffix('.real')
        recipe.rename(real)
        recipe.symlink_to(real)
    elif mode == 'corrupt_recipe':
        recipe.write_text('not valid original JSON')
    elif mode == 'legacy_claim':
        (legacy / 'pipeline.json').write_text(json.dumps(graph.model_dump()))
    elif mode == 'broken_claim':
        (legacy / '.legacy_flowchart_owner.json').symlink_to(legacy / 'missing-original-claim')

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if mode == 'delegated_constructor':
                original = flows.get_single_segmentation_flowchart
                monkeypatch.setattr(flows, 'get_single_segmentation_flowchart', lambda *a, **k: original(*a, **k))
            params = {'inspection_task': 'segmentation'}
            path = '/api/flowchart/pipeline'
            if mode in {'bound_template', 'delegated_constructor'}:
                path = '/api/flowchart/templates/single-segmentation'
                if mode == 'bound_template':
                    params['job_id'] = 'job_inert_unbound_metadata'
            else:
                params['source_dataset_path'] = project['source_dataset_dir']
            response = await client.get(path, headers=headers, params=params)
            assert response.status_code == (409 if mode == 'corrupt_recipe' else 200)
            if mode == 'bound_template':
                assert any(node['data']['model_job_id'] == 'job_inert_unbound_metadata' for node in response.json()['nodes'])

    asyncio.run(scenario())
    _refused(admission, observer)


@pytest.mark.parametrize('mode', ('glob_selector', 'recipe_validator', 'legacy_root'))
def test_late_replacements_cannot_become_original_warm_recipe_authority(tmp_path, monkeypatch, mode):
    from backend.api import routes_flowchart as flows
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    monkeypatch.setattr(flows, 'DEFAULT_PIPELINE_FILE', legacy / 'pipeline.json')
    app, admission, observer, _, project, graph, _, recipe, _ = _stack(tmp_path, monkeypatch)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if mode == 'glob_selector':
                (legacy / 'pipeline.json').write_text(json.dumps(graph.model_dump()))
                original_glob = Path.glob
                original_selector = Path._glob_selector
                def hide_legacy_selector(path, parts, *args, **kwargs):
                    if path == legacy and parts == ['pipeline*.json']:
                        return lambda root: iter(())
                    return original_selector(path, parts, *args, **kwargs)
                monkeypatch.setattr(Path, '_glob_selector', hide_legacy_selector)
                assert Path.glob is original_glob  # Top-level glob itself is untouched.
            elif mode == 'recipe_validator':
                changed = graph.model_copy(deep=True)
                next(node for node in changed.nodes if node.data.node_type == 'inspection').data.task = 'classification'
                recipe.write_text(json.dumps(changed.model_dump()))
                monkeypatch.setattr(flows, '_pipeline_matches_recipe', lambda *args: True)
            else:
                alternate = tmp_path / 'alternate-legacy'
                alternate.mkdir()
                monkeypatch.setattr(flows, 'DEFAULT_PIPELINE_FILE', alternate / 'pipeline.json')
            assert admission._flow_requests.original() is False
            response = await client.get('/api/flowchart/pipeline', headers=headers, params={
                'inspection_task': 'segmentation', 'source_dataset_path': project['source_dataset_dir']})
            assert response.status_code == 200
            if mode == 'recipe_validator':
                assert any(node['data']['task'] == 'classification' for node in response.json()['nodes'])
            else:
                assert response.json() == graph.model_dump()

    asyncio.run(scenario())
    _refused(admission, observer)


def test_pre_factory_clones_of_both_constructor_aliases_do_not_replace_first_registration(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.engine import flowchart_engine
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    monkeypatch.setattr(flows, 'DEFAULT_PIPELINE_FILE', legacy / 'pipeline.json')
    wrapper = flows.get_single_segmentation_flowchart
    original = flowchart_engine.get_single_segmentation_flowchart
    constructor_clone = FunctionType(original.__code__, original.__globals__, original.__name__,
                                     original.__defaults__, original.__closure__)
    constructor_clone.__kwdefaults__ = original.__kwdefaults__
    assert wrapper.__code__.co_freevars == ('function',)
    def cell(value):
        return (lambda: value).__closure__[0]
    wrapper_clone = FunctionType(wrapper.__code__, wrapper.__globals__, wrapper.__name__,
                                 wrapper.__defaults__, (cell(constructor_clone),))
    wrapper_clone.__wrapped__ = constructor_clone
    monkeypatch.setattr(flowchart_engine, 'get_single_segmentation_flowchart', constructor_clone)
    monkeypatch.setattr(flows, 'get_single_segmentation_flowchart', wrapper_clone)
    app, admission, observer, _, project, graph, _, _, _ = _stack(tmp_path, monkeypatch)
    assert admission._flow_requests.eligible is False

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/flowchart/templates/single-segmentation', headers=headers)
            assert response.status_code == 200 and response.json() == graph.model_dump()
    asyncio.run(scenario())
    _refused(admission, observer)


def test_recipe_removed_after_business_return_refuses_without_new_response_error(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    monkeypatch.setattr(flows, 'DEFAULT_PIPELINE_FILE', legacy / 'pipeline.json')
    app, admission, observer, _, project, graph, _, recipe, _ = _stack(tmp_path, monkeypatch)
    moved = []
    async def transport(scope, receive, send):
        async def forwarded(message):
            if scope.get('path') == '/api/flowchart/pipeline' and message.get('type') == 'http.response.start':
                recipe.rename(recipe.with_suffix('.retained-after-read'))
                moved.append(True)
            await send(message)
        await app(scope, receive, forwarded)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=transport), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/flowchart/pipeline', headers=headers, params={
                'inspection_task': 'segmentation', 'source_dataset_path': project['source_dataset_dir']})
            assert response.status_code == 200 and response.json() == graph.model_dump()
    asyncio.run(scenario())
    assert moved == [True]
    _refused(admission, observer)


@pytest.mark.parametrize('case', ('recipe', 'template'))
def test_original_send_failure_never_becomes_new_GET_completion(tmp_path, monkeypatch, case):
    from backend.api import routes_flowchart as flows
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    monkeypatch.setattr(flows, 'DEFAULT_PIPELINE_FILE', legacy / 'pipeline.json')
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    path = '/api/flowchart/pipeline' if case == 'recipe' else '/api/flowchart/templates/single-segmentation'
    params = {'inspection_task': 'segmentation'}
    if case == 'recipe':
        params['source_dataset_path'] = project['source_dataset_dir']
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': 'GET', 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
                 'query_string': urlencode(params).encode(), 'root_path': '',
                 'headers': [(name.encode(), value.encode()) for name, value in headers.items()],
                 'client': ('127.0.0.1', 1), 'server': ('owned.invalid', 80)}
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            return {'type': 'http.disconnect'}
        async def send(message):
            if message['type'] == 'http.response.body':
                raise OSError('two-GET fixture original response transport failed')
        await app(scope, receive, send)
    with pytest.raises(OSError, match='two-GET fixture original response transport failed'):
        asyncio.run(scenario())
    rows = [row for row in observer._records.values() if row['ingress'] == {'type': 'http', 'method': 'GET', 'path': path}]
    target, = rows
    assert target['response_status'] == 200 and target['transport_failed'] and not target['normal_return']
    _refused(admission, observer)
