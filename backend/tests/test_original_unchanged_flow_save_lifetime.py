"""One original ASGI unchanged-graph sequence; no installed-app equivalence.

Uses the existing real owned-store/account/admission supplier. The original
preview, save, version and list handlers, SQLite stores, lock/publication and
ownership decisions are not replaced. The unbound graph loads no model/GPU.
"""
import asyncio
import json

import httpx


def test_original_preview_unchanged_save_readbacks_drain(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_flow_lifetime import (
        _stack, _login_current, _save_params, _no_whole_credit,
    )

    app, admission, observer, _, project, graph, seed, recipe, audit = _stack(
        tmp_path, monkeypatch)
    graph_body = graph.model_dump()
    controlled_reason = 'Root23 installed QA: operation-local transfer capability'
    phases = []

    def record(phase):
        observed = observer.snapshot()
        phases.append({
            'phase': phase, 'request_count': observed['request_count'],
            'activity_sequence': observed['sequence'],
            'unsupported': list(observed['unsupported']),
            'uncertain': observed['uncertain'],
            'failure_tags': list(observed['failures']),
            'pending_requests': observed['pending_requests'],
            'records': [
                {key: row[key] for key in (
                    'identity', 'ingress', 'completed', 'normal_return',
                    'released', 'error_type', 'canceled')}
                for row in observed['records']
            ],
        })

    async def sequence():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url='http://owned.invalid',
        ) as client:
            headers = await _login_current(client, app, project)
            record('login-and-current')
            preview = await client.post('/api/flowchart/pipeline/diff',
                headers=headers, params={'expected_version_id': seed},
                json=graph_body)
            assert preview.status_code == 200
            assert preview.json()['parent_revision'] == seed
            assert preview.json()['stale'] is False
            assert preview.json()['semantic_delta']['changes'] == []
            record('unchanged-preview')
            params = _save_params(project, seed)
            params['change_reason'] = controlled_reason
            saved = await client.post('/api/flowchart/pipeline',
                headers=headers, params=params, json=graph_body)
            assert saved.status_code == 200
            body = saved.json()
            assert body['status'] == 'saved'
            assert body['version_id'] != seed
            assert body['change_id']
            record('unchanged-save')
            version = await client.get(
                '/api/flowchart/pipelines/' + body['version_id'], headers=headers)
            assert version.status_code == 200
            assert version.json() == graph_body
            record('saved-version-readback')
            listed = await client.get('/api/flowchart/pipelines', headers=headers,
                params={'source_dataset_path': project['source_dataset_dir']})
            assert listed.status_code == 200
            assert listed.json()['total'] == 2
            assert [row['version_id'] for row in listed.json()['pipelines']
                if row['is_active']] == [body['version_id']]
            record('saved-list-readback')
            return body

    saved = asyncio.run(sequence())
    # Original durable outcomes are required before evaluating lifetime credit.
    assert json.loads(recipe.read_bytes()) == graph_body
    assert json.loads(flows._active_flow_file(recipe.parent.parent).read_bytes())[
        'version_id'] == saved['version_id']
    assert audit.verify() == {'intact': True, 'rows': 1}
    row, = audit.list()
    assert row['action'] == 'save'
    assert row['parent_revision'] == seed
    assert row['next_revision'] == saved['version_id']
    assert row['reason'] == controlled_reason
    assert row['semantic_delta']['changes'] == []
    admission.close()
    drained = admission.drain(.03)
    record('closed')
    print('UNCHANGED_SAVE_ORIGINAL_SEQUENCE ' + json.dumps({
        'schema': 'modu-vision.original-unchanged-flow-save-control/v1',
        'phases': phases, 'drain': drained,
        'new_saved_version': saved['version_id'] != seed,
        'exact_controlled_reason_committed': True,
        'empty_semantic_changes_committed': True,
        'real_models_or_GPU_loaded': False,
        'installed_native_or_Root23_equivalence': False,
    }, sort_keys=True))
    assert drained == {
        'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    assert all(not p['uncertain'] and not p['unsupported'] and not p['failure_tags']
        and p['pending_requests'] == 0 for p in phases)
    assert observer.snapshot()['all_settled']
    _no_whole_credit(observer)
