"""Real owned flow read/save ASGI lifetimes; source draft, not executed.

Data-only setup publishes one unbound, structurally valid segmentation graph
using the original helpers, outside every observed request. No model/checkpoint
is constructed, loaded or run. No handler, account, SQLite connection, lock,
publication function or ownership witness is replaced. Export is deliberately
separate: this unbound graph cannot stand in for verified export checkpoints.
"""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import stat

import httpx


def _stack(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.engine.config_audit import ConfigAuditStore
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects

    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    actor, (project,) = _owned_projects(app, observer, 'Owned saved flow')
    root = Path(project['project_dir'])
    source = Path(project['dataset_dir']).resolve()
    project.update(task='segmentation', source_dataset_dir=str(source))
    flows._write_json(root/'project.json', project)
    graph = get_single_segmentation_flowchart()
    assert all(node.data.model_job_id is None for node in graph.nodes)
    seed = flows._save_version(graph, 'segmentation', str(source), root)
    recipe = flows._recipe_file('segmentation', str(source), root)
    flows._write_pipeline(recipe, graph)
    flows._write_json(flows._active_flow_file(root), {
        'version_id':seed, 'project_id':project['id'],
        'labelset_id':project.get('active_labelset_id','default'),
        'recipe_task':'segmentation', 'source_dataset_path':str(source)})
    audit = ConfigAuditStore(root)
    with closing(audit._connect()):
        pass
    assert audit.verify() == {'intact':True, 'rows':0}
    assert observer.snapshot()['records'] == []
    return app, admission, observer, actor, project, graph, seed, recipe, audit


async def _login_current(client, app, project):
    login = await client.post('/api/accounts/login', json={
        'username':'fixture-admin', 'password':'fixture-password-123'})
    assert login.status_code == 200
    headers = {'authorization':'Bearer '+login.json()['token'],
               'x-vision-project':project['id']}
    current = await client.get('/api/project/current', headers=headers)
    assert current.status_code == 200 and current.json()['id'] == project['id']
    context = json.loads(current.headers['x-vision-context'])
    assert context['actor_id'] == login.json()['user']['id']
    assert context['workspace_id'] == app.state.context_registry.workspace_id
    assert context['project_id'] == project['id'] and context['mode'] == 'team'
    headers['x-vision-context'] = current.headers['x-vision-context']
    return headers


def _edited(graph):
    changed = graph.model_copy(deep=True)
    next(node for node in changed.nodes if node.data.node_type=='inspection').data.threshold = .73
    return changed


def _save_params(project, seed):
    return {'recipe_task':'segmentation', 'source_dataset_path':project['source_dataset_dir'],
            'expected_version_id':seed, 'change_reason':'Owned original lifetime threshold control'}


def _file_basis(root, recipe, seed):
    from backend.api import routes_flowchart as flows
    paths = (recipe, flows._active_flow_file(root), flows._version_dir(root)/f'{seed}.json')
    return {path:path.read_bytes() for path in paths}


def _no_whole_credit(observer):
    assert all(observer.snapshot()[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


def test_original_owned_saved_graph_read_save_reopen_drains(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    app, admission, observer, actor, project, graph, seed, recipe, audit = _stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    seed_bytes = (flows._version_dir(root)/f'{seed}.json').read_bytes()
    changed = _edited(graph)

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            listed = await client.get('/api/flowchart/pipelines', headers=headers,
                params={'source_dataset_path':project['source_dataset_dir']})
            assert listed.status_code == 200
            assert listed.json()['total'] == 1
            assert listed.json()['pipelines'][0]['version_id'] == seed
            assert listed.json()['pipelines'][0]['is_active'] is True
            active = await client.get('/api/flowchart/pipeline/active/record', headers=headers,
                params={'source_dataset_path':project['source_dataset_dir']})
            assert active.status_code == 200
            assert active.json() == {'version_id':seed, 'pipeline':graph.model_dump()}
            opened = await client.get(f'/api/flowchart/pipelines/{seed}', headers=headers)
            assert opened.status_code == 200 and opened.json() == graph.model_dump()
            saved = await client.post('/api/flowchart/pipeline', headers=headers,
                params=_save_params(project, seed), json=changed.model_dump())
            assert saved.status_code == 200
            body = saved.json()
            assert body['status'] == 'saved' and body['pipeline_id'] == graph.id
            assert body['version_id'] != seed and len(body['version_id']) == 32
            assert body['layout_only'] is False and body['change_id']
            reopened = await client.get('/api/flowchart/pipelines/'+body['version_id'], headers=headers)
            assert reopened.status_code == 200 and reopened.json() == changed.model_dump()
            active = await client.get('/api/flowchart/pipeline/active/record', headers=headers,
                params={'source_dataset_path':project['source_dataset_dir']})
            assert active.status_code == 200
            assert active.json() == {'version_id':body['version_id'], 'pipeline':changed.model_dump()}
            return body

    saved = asyncio.run(original_sequence())
    # These are original durable outcomes, not synthetic request receipts.
    assert (flows._version_dir(root)/f'{seed}.json').read_bytes() == seed_bytes
    version = json.loads((flows._version_dir(root)/(saved['version_id']+'.json')).read_bytes())
    assert version['pipeline'] == changed.model_dump()
    assert version['source_dataset_path'] == project['source_dataset_dir']
    assert json.loads(recipe.read_bytes()) == changed.model_dump()
    assert json.loads(flows._active_flow_file(root).read_bytes())['version_id'] == saved['version_id']
    assert audit.verify() == {'intact':True, 'rows':1}
    rows = audit.list()
    assert len(rows) == 1 and rows[0]['change_id'] == saved['change_id']
    assert rows[0]['actor'] == {'kind':'account', 'id':actor['id'], 'name':actor['username']}
    assert rows[0]['subject']['project_id'] == project['id']
    assert rows[0]['parent_revision'] == seed and rows[0]['next_revision'] == saved['version_id']
    assert rows[0]['action'] == 'save' and rows[0]['layout_only'] is False
    assert flows._FLOW_SAVE_LOCK.acquire(blocking=False)
    flows._FLOW_SAVE_LOCK.release()
    records = observer.snapshot()['records']
    assert len(records) == 8
    assert all(row['completed'] and row['normal_return'] and row['released'] for row in records)
    admission.close()
    # Expected causal RED: all original HTTP/data/audit assertions pass first.
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}
    assert observer.snapshot()['all_settled'] and not observer.snapshot()['uncertain']
    _no_whole_credit(observer)


def test_original_flow_audit_transaction_failure_refuses_after_rollback(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    app, admission, observer, _, project, graph, seed, recipe, audit = _stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    before = _file_basis(root,recipe,seed)
    with closing(sqlite3.connect(audit.path)) as database:
        with database:
            database.execute('CREATE TRIGGER owned_flow_audit_abort BEFORE INSERT ON configuration_changes '
                "BEGIN SELECT RAISE(ABORT, 'owned fixture audit blocked'); END")

    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            return await client.post('/api/flowchart/pipeline', headers=headers,
                params=_save_params(project,seed), json=_edited(graph).model_dump())
    response = asyncio.run(original_request())
    assert response.status_code == 500
    assert _file_basis(root,recipe,seed) == before
    assert {path.name for path in flows._version_dir(root).iterdir()} == {seed+'.json'}
    assert audit.verify() == {'intact':True, 'rows':0}
    assert audit.list() == []
    assert flows._FLOW_SAVE_LOCK.acquire(blocking=False)
    flows._FLOW_SAVE_LOCK.release()
    _refused(admission)
    _no_whole_credit(observer)


def test_original_flow_publication_failure_keeps_recovery_evidence_and_refuses(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    app, admission, observer, _, project, graph, seed, recipe, audit = _stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    before = _file_basis(root,recipe,seed)
    parent = recipe.parent
    original_mode = stat.S_IMODE(parent.stat().st_mode)
    # The separate versions directory remains writable. The real original save
    # writes its version, then cannot publish a recipe tempfile in this parent.
    # No publication/restore function or OS operation is monkeypatched.
    parent.chmod(0o500)
    try:
        async def original_request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://owned.invalid') as client:
                headers = await _login_current(client, app, project)
                return await client.post('/api/flowchart/pipeline', headers=headers,
                    params=_save_params(project,seed), json=_edited(graph).model_dump())
        response = asyncio.run(original_request())
        assert response.status_code == 500
        assert response.json()['detail']['recovery_incomplete'] is True
    finally:
        parent.chmod(original_mode)
    assert _file_basis(root,recipe,seed) == before
    # Original recovery keeps the new immutable version when its recipe cannot
    # be rolled back. Do not delete/repair it or invent a completed save.
    versions = {path.name for path in flows._version_dir(root).iterdir()}
    assert seed+'.json' in versions and len(versions) == 2
    assert audit.verify() == {'intact':True, 'rows':0}
    assert audit.list() == []
    assert flows._FLOW_SAVE_LOCK.acquire(blocking=False)
    flows._FLOW_SAVE_LOCK.release()
    _refused(admission)
    _no_whole_credit(observer)


def test_original_flow_audit_delegate_cannot_inherit_original_source(tmp_path, monkeypatch):
    from backend.engine.config_audit import ConfigAuditStore
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    from backend.tests.test_original_owned_flow_lifetime import (
        _stack, _login_current, _edited, _save_params, _no_whole_credit,
    )
    app, admission, observer, actor, project, graph, seed, _, audit = _stack(tmp_path,monkeypatch)
    original = ConfigAuditStore.append
    def delegated(store, **values):
        # The real original SQLite transaction and connection close still run;
        # an additional runtime callee cannot inherit their source authority.
        return original(store, **values)
    monkeypatch.setattr(ConfigAuditStore,'append',delegated)
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client,app,project)
            return await client.post('/api/flowchart/pipeline',headers=headers,
                params=_save_params(project,seed),json=_edited(graph).model_dump())
    response = asyncio.run(original_request())
    assert response.status_code == 200 and response.json()['status'] == 'saved'
    assert audit.verify() == {'intact':True,'rows':1}
    row = audit.list()[0]
    assert row['change_id'] == response.json()['change_id']
    assert row['next_revision'] == response.json()['version_id']
    assert row['actor']['id'] == actor['id']
    _refused(admission)
    _no_whole_credit(observer)

def test_original_flow_production_without_owned_request_never_becomes_late_authority(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    app, admission, observer, _, project, graph, seed, _, _ = _stack(tmp_path,monkeypatch)
    # Build the actual original middleware stack before inspecting its owner.
    # The real original producer is then called with no active owned request.
    app.middleware_stack = app.build_middleware_stack()
    owner = admission._flow_requests
    assert owner.original()
    assert owner.included is not None, 'This control requires the original modern dispatcher'
    assert owner.included.minted == []
    produced = owner.included.branch.effective_candidates()
    assert produced and owner.failed
    assert owner.included.minted == []
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client,app,project)
            return await client.get('/api/flowchart/pipelines/'+seed,headers=headers)
    response = asyncio.run(original_request())
    assert response.status_code == 200 and response.json() == graph.model_dump()
    assert owner.records == [] and owner.pending == {}
    _refused(admission)
    _no_whole_credit(observer)


def test_original_flow_instance_completion_delegate_refuses(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    app, admission, observer, _, project, graph, seed, _, _ = _stack(tmp_path, monkeypatch)
    delegated_calls = []
    inserted = []

    async def observed_transport(scope, receive, send):
        async def original_send(message):
            if (scope.get('path') == '/api/flowchart/pipelines/'+seed
                    and message.get('type') == 'http.response.start'):
                owner = admission._flow_requests
                attempts = [a for a in owner.pending.values() if a.scope is scope]
                assert len(attempts) == 1
                attempt = attempts[0]
                original = attempt.complete
                def delegated(row):
                    delegated_calls.append(True)
                    return original(row)
                monkeypatch.setattr(attempt, 'complete', delegated)
                inserted.append(True)
            await send(message)
        await app(scope, receive, original_send)

    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observed_transport),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            return await client.get('/api/flowchart/pipelines/'+seed, headers=headers)

    response = asyncio.run(original_request())
    assert response.status_code == 200 and response.json() == graph.model_dump()
    assert inserted == [True]
    assert len(observer.snapshot()['records']) == 3
    _refused(admission)
    _no_whole_credit(observer)


def test_original_flow_context_alias_delegate_refuses(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    app, admission, observer, _, project, graph, seed, _, _ = _stack(tmp_path, monkeypatch)
    original = flows.get_current_project
    delegated_calls = []
    def delegated(*args, **kwargs):
        delegated_calls.append(True)
        return original(*args, **kwargs)
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            monkeypatch.setattr(flows, 'get_current_project', delegated)
            return await client.get('/api/flowchart/pipelines/'+seed, headers=headers)
    response = asyncio.run(original_request())
    assert response.status_code == 200 and response.json() == graph.model_dump()
    assert delegated_calls
    _refused(admission)
    _no_whole_credit(observer)


import pytest

@pytest.mark.parametrize('delegate_kind', ['decision_instance', 'audit_subclass', 'store_subclass'])
def test_original_flow_save_callee_source_cannot_be_inherited(tmp_path, monkeypatch, delegate_kind):
    from backend.engine import config_audit
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _edited, _save_params, _no_whole_credit
    app, admission, observer, actor, project, graph, seed, _, audit = _stack(tmp_path, monkeypatch)
    delegated_calls = []
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if delegate_kind == 'decision_instance':
                store = admission._account_requests.store
                original = store.record_decision
                def delegated(*args, **kwargs):
                    delegated_calls.append(True)
                    return original(*args, **kwargs)
                monkeypatch.setattr(store, 'record_decision', delegated)
            elif delegate_kind == 'store_subclass':
                store = admission._account_requests.store
                class InheritedStore(type(store)):
                    pass
                monkeypatch.setattr(store, '__class__', InheritedStore)
            else:
                class InheritedAudit(config_audit.ConfigAuditStore):
                    pass
                monkeypatch.setattr(config_audit, 'ConfigAuditStore', InheritedAudit)
            return await client.post('/api/flowchart/pipeline', headers=headers,
                params=_save_params(project, seed), json=_edited(graph).model_dump())
    response = asyncio.run(original_request())
    assert response.status_code == 200 and response.json()['status'] == 'saved'
    assert audit.verify() == {'intact': True, 'rows': 1}
    record = audit.list()[0]
    assert record['actor']['id'] == actor['id']
    assert record['next_revision'] == response.json()['version_id']
    if delegate_kind == 'decision_instance':
        assert delegated_calls
    _refused(admission)
    _no_whole_credit(observer)


@pytest.mark.parametrize('delegate_kind', ['account_original', 'included_handle', 'included_handle_code', 'route_handle'])
def test_original_flow_dispatch_binding_replacement_refuses(tmp_path, monkeypatch, delegate_kind):
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    app, admission, observer, _, project, graph, seed, _, _ = _stack(tmp_path, monkeypatch)
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            owner = admission._flow_requests
            if delegate_kind == 'account_original':
                original = admission._account_requests.original
                def delegated():
                    return original()
                monkeypatch.setattr(admission._account_requests, 'original', delegated)
            elif delegate_kind in ('included_handle', 'included_handle_code'):
                if owner.included is None:
                    pytest.skip('No IncludedRouter in this qualified legacy runtime')
                branch = owner.included.branch
                if delegate_kind == 'included_handle_code':
                    function = type(branch).handle
                    monkeypatch.setattr(function, '__code__', function.__code__.replace())
                else:
                    original = branch.handle
                    async def delegated(*args, **kwargs):
                        return await original(*args, **kwargs)
                    monkeypatch.setattr(branch, 'handle', delegated)
            else:
                route = owner.routes['get_saved_pipeline_version']
                original = route.handle
                async def delegated(*args, **kwargs):
                    return await original(*args, **kwargs)
                monkeypatch.setattr(route, 'handle', delegated)
            return await client.get('/api/flowchart/pipelines/'+seed, headers=headers)
    response = asyncio.run(original_request())
    assert response.status_code == 200 and response.json() == graph.model_dump()
    _refused(admission)
    _no_whole_credit(observer)
