"""Finite source-ASGI startup controls; no installed request attribution.

The original owned temporary account/project fixture supplies the unchanged
factory, router, authorization, SQLite, locks and request observer. No models
or worker processes are created or invoked.
"""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest


def _durable_graphs(project, recipe, seed):
    from backend.api import routes_flowchart as flows
    root = Path(project['project_dir'])
    paths = (root/'project.json', recipe, flows._active_flow_file(root),
             flows._version_dir(root)/(seed+'.json'))
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _facts(observer, admission):
    snap = observer.snapshot()
    return {
        'failure_tags': snap['failures'], 'uncertain': snap['uncertain'],
        'unsupported': snap['unsupported'], 'pending_requests': snap['pending_requests'],
        'first_origin': observer.first_unsupported_origin_diagnostic(),
        'records': [{k: row[k] for k in ('identity', 'ingress', 'response_status',
                    'entered_sequence', 'released_sequence', 'completed',
                    'normal_return', 'released', 'error_type', 'canceled')}
                    for row in observer._records.values()],
    }


def test_original_typed_open_reads_remain_managed(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current
    app, admission, observer, _, project, graph, seed, recipe, _ = _stack(tmp_path, monkeypatch)
    before = _durable_graphs(project, recipe, seed)

    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            source = {'source_dataset_path': project['source_dataset_dir']}
            catalog = await client.get('/api/flowchart/models/catalog', headers=headers, params=source)
            assert catalog.status_code == 200 and catalog.json()['models'] == []
            listed = await client.get('/api/flowchart/pipelines', headers=headers, params=source)
            assert listed.status_code == 200 and listed.json()['total'] == 1
            draft = await client.get('/api/flowchart/draft', headers=headers)
            assert draft.status_code == 404
            active = await client.get('/api/flowchart/pipeline/active/record', headers=headers, params=source)
            assert active.status_code == 200 and active.json() == {'version_id': seed, 'pipeline': graph.model_dump()}

    asyncio.run(sequence())
    assert _durable_graphs(project, recipe, seed) == before
    admission.close()
    drained = admission.drain(.03)
    facts = _facts(observer, admission)
    print('TYPED_OPEN_CONTROL ' + json.dumps({'facts': facts, 'drain': drained}, sort_keys=True))
    assert drained == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    assert facts['failure_tags'] == [] and facts['uncertain'] is False
    assert facts['first_origin']['first'] is None


@pytest.mark.parametrize('case', ['cold', 'named_revision', 'success', 'late_method',
                                 'pre_factory_method', 'foreign_actor', 'foreign_workspace',
                                 'memberless', 'late_cache', 'send_failure', 'inspection_import',
                                 'pre_factory_helper_delegate', 'pre_factory_helper_copy'])
def test_library_other_paths_cannot_borrow_no_revision_lifetime(tmp_path, monkeypatch, case):
    from backend.engine.dataset_index import DatasetIndex
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current
    if case in {'pre_factory_helper_delegate', 'pre_factory_helper_copy'}:
        from backend.api import routes_image_library
        original_revision = routes_image_library._revision
        if case == 'pre_factory_helper_copy':
            from types import FunctionType
            replacement = FunctionType(original_revision.__code__, original_revision.__globals__,
                original_revision.__name__, original_revision.__defaults__, original_revision.__closure__)
            assert replacement is not original_revision
        else:
            def replacement(request, revision_id):
                return original_revision(request, revision_id)
        monkeypatch.setattr(routes_image_library, '_revision', replacement)
    if case == 'pre_factory_method':
        original_active = DatasetIndex.active
        def delegated(index, key):
            return original_active(index, key)
        monkeypatch.setattr(DatasetIndex, 'active', delegated)
    app, admission, observer, _, project, _, seed, recipe, _ = _stack(tmp_path, monkeypatch)
    before = _durable_graphs(project, recipe, seed)
    outsider = app.state.accounts.create_user('library-outsider', 'fixture-password-123') if case == 'memberless' else None

    async def original_app(scope, receive, send):
        async def observed_send(message):
            if scope.get('path') == '/api/dataset/library/images' and message['type'] == 'http.response.body':
                if case == 'late_cache':
                    # The actual handler and both real SQLite reads already
                    # returned; a different coordinator cannot inherit them.
                    from backend.engine.dataset_import_job import DatasetImportJobs
                    held = app.state.dataset_imports
                    app.state.dataset_imports = DatasetImportJobs(held.store, held.index)
                if case == 'send_failure':
                    raise OSError('owned source response transport failed')
            return await send(message)
        await app(scope, receive, observed_send)

    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            catalog = await client.get('/api/flowchart/models/catalog', headers=headers,
                params={'source_dataset_path': project['source_dataset_dir']})
            assert catalog.status_code == 200
            if case != 'cold':
                revisions = await client.get('/api/dataset/revisions', headers=headers)
                assert revisions.status_code == 200 and revisions.json() == {'active_revision': None, 'revisions': []}
            if case == 'late_method':
                original_active = DatasetIndex.active
                def delegated(index, key):
                    return original_active(index, key)
                monkeypatch.setattr(DatasetIndex, 'active', delegated)
            if case == 'inspection_import':
                # Existing route/classes retain their real original globals;
                # only the new optional lookup cannot resolve its module.
                import sys
                monkeypatch.setitem(sys.modules, 'backend.engine.dataset_index', None)
            if case == 'success':
                # Original temporary-source decoding/index publication only;
                # this is no trained model or completed import-job claim.
                from PIL import Image
                from backend.contracts.context import declared_context
                from starlette.datastructures import Headers
                path = Path(project['source_dataset_dir'])/'train'/'OK'/'one.png'
                path.parent.mkdir(parents=True)
                Image.new('RGB', (32, 32), (16, 32, 64)).save(path)
                jobs = app.state.dataset_imports
                context = declared_context(Headers(headers=headers))
                key = app.state.context_registry.project_key(context)
                revision = jobs.index.build_revision(key, project['project_dir'],
                    project['source_dataset_dir'], 'classification', verify=True)
                jobs.index.activate(key, revision.revision_id, None)
            if case in {'foreign_actor', 'foreign_workspace', 'memberless'}:
                context = json.loads(headers['x-vision-context'])
                if case == 'memberless':
                    login = await client.post('/api/accounts/login', json={
                        'username': 'library-outsider', 'password': 'fixture-password-123'})
                    assert login.status_code == 200
                    headers['authorization'] = 'Bearer '+login.json()['token']
                    context['actor_id'] = outsider['id']
                else:
                    context['actor_id' if case == 'foreign_actor' else 'workspace_id'] = 'foreign'
                headers['x-vision-context'] = json.dumps(context, separators=(',', ':'))
            params = {'state': 'valid', 'limit': 120}
            if case == 'named_revision': params['revision_id'] = '0'*32
            if case == 'send_failure':
                with pytest.raises(OSError, match='owned source response transport failed'):
                    await client.get('/api/dataset/library/images', headers=headers, params=params)
                return
            response = await client.get('/api/dataset/library/images', headers=headers, params=params)
            expected = 200 if case == 'success' else 404 if case == 'named_revision' else 403 if case in {'foreign_actor', 'foreign_workspace', 'memberless'} else 409
            assert response.status_code == expected
            if expected == 409:
                assert response.json() == {'detail': 'No validated dataset revision is active; validate the source and accept a revision first'}
            elif expected == 404:
                assert response.json() == {'detail': 'Revision not found in this project'}
            elif case == 'success':
                assert len(response.json()['items']) == 1
            elif case == 'memberless':
                assert response.json() == {'detail': 'Project permission required'}
            else:
                assert response.json() == {'detail': 'Explicit context does not match the authenticated account workspace'}

    asyncio.run(sequence())
    assert _durable_graphs(project, recipe, seed) == before
    admission.close()
    drained = admission.drain(.03)
    assert drained['status'] == 'refused' and 'uncovered_request_protocol' in drained['unsupported']
    assert not any(attempt.settled for attempt in admission._UI_ancillary_requests.records if attempt.name == 'library_images')
    assert all(observer.snapshot()[key] is False for key in (
        'whole_writer_coverage', 'can_release_launch_lease', 'process_tree_exit_verified'))


def test_original_no_revision_library_409_keeps_owned_open_managed(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current
    app, admission, observer, _, project, _, seed, recipe, _ = _stack(tmp_path, monkeypatch)
    before = _durable_graphs(project, recipe, seed)

    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            # A genuine earlier typed startup GET warms the original lazy
            # included-router contexts; it never creates a request witness for
            # the distinct library GET that follows.
            catalog = await client.get('/api/flowchart/models/catalog', headers=headers,
                params={'source_dataset_path': project['source_dataset_dir']})
            assert catalog.status_code == 200 and catalog.json()['models'] == []
            # The separately owned existing revision route performs any cold
            # index initialization. This target lane claims only the later
            # warm, original two-read no-active-revision refusal.
            revisions = await client.get('/api/dataset/revisions', headers=headers)
            assert revisions.status_code == 200 and revisions.json() == {'active_revision': None, 'revisions': []}
            baseline = _facts(observer, admission)
            assert baseline['unsupported'] == [] and baseline['failure_tags'] == []
            assert baseline['first_origin']['first'] is None
            response = await client.get('/api/dataset/library/images', headers=headers,
                                        params={'state': 'valid', 'limit': 120})
            assert response.status_code == 409
            assert response.json() == {'detail': 'No validated dataset revision is active; validate the source and accept a revision first'}
            assert len(response.content) == 101
            assert response.headers['x-vision-context'] == headers['x-vision-context']
            return {'status': response.status_code, 'bytes': len(response.content),
                    'sha256': hashlib.sha256(response.content).hexdigest()}

    response = asyncio.run(sequence())
    assert _durable_graphs(project, recipe, seed) == before
    facts = _facts(observer, admission)
    last = facts['records'][-1]
    assert last['ingress'] == {'type': 'http', 'method': 'GET', 'path': '/api/dataset/library/images'}
    assert last['response_status'] == 409 and last['completed'] and last['normal_return'] and last['released']
    assert last['error_type'] is None and last['canceled'] is False
    assert facts['failure_tags'] == [] and facts['uncertain'] is False and facts['pending_requests'] == 0
    admission.close()
    drained = admission.drain(.03)
    print('NO_REVISION_LIBRARY_CAUSAL ' + json.dumps({
        'response': response, 'facts': facts, 'drain': drained,
        'retained_graph_bytes_unchanged': True, 'real_model_or_GPU_execution': False,
        'native_request_80_route_or_equivalence_proved': False,
    }, sort_keys=True))
    # Desired ownership outcome: original expected 409 is fully enclosed.
    # The unchanged source is expected to fail only here, after real outcomes.
    assert drained == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
