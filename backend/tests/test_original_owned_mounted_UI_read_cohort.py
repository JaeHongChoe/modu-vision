"""Root-only tests-first draft for the mounted UI's finite uncovered reads.

No implementation/route/DB/worker/resource witness is substituted. Every case
uses the existing real shared-auth ASGI _stack in a fresh owned fixture. Small
image/template/comparison/index records are disclosed original data setup,
outside the observed requests. No training, inference, SSH or service is used.
The final drain assertion is deliberately after the original HTTP/body/source
and normal ASGI terminal assertions. A 200 alone supplies no lifetime credit.
"""
import asyncio
import hashlib
from io import BytesIO
import json
from pathlib import Path

import httpx
import pytest


CASES = (
    'compute-reservations', 'compute-targets', 'compute-jobs',
    'compute-inputs-classification', 'compute-inputs-segmentation',
    'workspace-templates', 'workspace-comparisons', 'flow-active-version',
    'dataset-current-summary', 'dataset-revisions', 'dataset-active-source-status',
    'project-preferences', 'dataset-statistics', 'saved-data-diagnostics',
    'onboarding-team', 'project-list', 'team-readiness', 'provenance-impact',
    'job-events-initial', 'dataset-thumbnail-cold-and-warm',
)


@pytest.mark.parametrize('case', CASES)
def test_original_mounted_UI_read_preserves_source_and_drains(case, tmp_path, monkeypatch):
    from PIL import Image
    from backend.api import routes_dataset, routes_flowchart as flows
    from backend.engine.dataset_index import DatasetIndex, index_path
    from backend.engine.flow_workspace import save_template, atomic_json
    from backend.tests.test_original_owned_flow_lifetime import (
        _stack, _login_current, _no_whole_credit,
    )

    monkeypatch.chdir(tmp_path)
    # Only original storage locations are isolated. No handler/resource method
    # is replaced; all IO/SQLite/lock/worker operations remain the originals.
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'runtime-user-data'))
    monkeypatch.setenv('MODU_FLOW_TEMPLATE_DIR', str(tmp_path/'templates'))
    (tmp_path/'thumbnails').mkdir(mode=0o700)
    monkeypatch.setattr(routes_dataset, 'THUMBNAIL_CACHE_DIR', tmp_path/'thumbnails')
    app, admission, observer, actor, project, graph, seed, recipe, audit = _stack(tmp_path, monkeypatch)
    root, source = Path(project['project_dir']), Path(project['source_dataset_dir'])
    image = source/'mounted-UI-original.png'
    Image.new('RGB', (16, 16), (30, 70, 90)).save(image)
    annotation = image.with_suffix('.json')
    annotation.write_text(json.dumps({
        'imagePath':image.name, 'imageWidth':16, 'imageHeight':16,
        'shapes':[{'label':'defect', 'shape_type':'polygon',
                   'points':[[1, 1], [9, 1], [9, 8]]}],
    }), encoding='utf-8')
    protected = [image, annotation, recipe, root/'project.json',
                 flows._active_flow_file(root), flows._version_dir(root)/(seed+'.json')]
    template = comparison = revision = None
    if case == 'workspace-templates':
        library = tmp_path/'templates'/'accounts'/hashlib.sha256(actor['id'].encode()).hexdigest()
        template = save_template(library, graph, name='Original mounted UI fixture', project_id=project['id'])
        protected.append(library/(template['template_id']+'.json'))
    elif case == 'workspace-comparisons':
        # Original schema-2 record format; no comparison/model execution or
        # model-quality result is claimed. Empty history rows are valid data.
        comparison = {
            'schema_version':2, 'comparison_id':'1'*32, 'name':'Original history fixture',
            'created_at':'2026-10-16T00:00:00+00:00', 'project_id':project['id'],
            'source_dataset_path':str(source),
            'labelset_id':project.get('active_labelset_id', 'default'), 'rows':[],
        }
        comparison['record_sha256'] = hashlib.sha256(json.dumps(
            comparison, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
            allow_nan=False).encode()).hexdigest()
        record = root/'flowcharts'/'comparisons'/(comparison['comparison_id']+'.json')
        atomic_json(record, comparison)
        protected.append(record)
    elif case == 'dataset-active-source-status':
        # Exact original index API before HTTP observation. It decodes only the
        # temporary PNG and publishes a real revision; no background job starts.
        context = app.state.context_registry.context(project, actor)
        project_key = app.state.context_registry.project_key(context)
        index = DatasetIndex(index_path(app.state.context_registry.root))
        revision = index.build_revision(project_key, root, source, 'segmentation', 'exclude')
        assert revision.valid_count == revision.image_count == 1
        assert index.activate(project_key, revision.revision_id, None) == revision.revision_id
    basis = {file:file.read_bytes() for file in protected}
    assert observer.snapshot()['records'] == []

    async def original_sequence():
        requests = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            async def get(path, **kwargs):
                response = await client.get(path, headers=headers, **kwargs)
                requests.append((response.request.method, response.request.url.path))
                assert response.status_code == 200, (case, response.status_code, response.text[:300])
                return response
            if case == 'compute-reservations':
                body = (await get('/api/compute/reservations')).json()
                assert body == {'reservations':[]}
            elif case == 'compute-targets':
                body = (await get('/api/compute/targets')).json()
                assert body == {'targets':[]}
            elif case == 'compute-jobs':
                body = (await get('/api/compute/jobs')).json()
                assert body == {'jobs':[]}
            elif case in {'compute-inputs-classification', 'compute-inputs-segmentation'}:
                task = case.removeprefix('compute-inputs-')
                body = (await get('/api/compute/inputs', params={'task':task})).json()
                assert body == {'inputs':[{'dataset_path':str(source), 'name':'프로젝트 원본'}]}
            elif case == 'workspace-templates':
                body = (await get('/api/flow-workspace/templates')).json()
                assert body == {'templates':[template]}
            elif case == 'workspace-comparisons':
                body = (await get('/api/flow-workspace/comparisons')).json()
                assert body == {'comparisons':[{**comparison, 'integrity':'verified'}], 'invalid':[]}
            elif case == 'flow-active-version':
                assert (await get('/api/flowchart/pipeline/active-version')).json() == {'version_id':seed}
            elif case == 'dataset-current-summary':
                body = (await get('/api/dataset/current-summary',
                    params={'folder_path':str(source), 'task':'segmentation'})).json()
                assert body['total_images'] == body['source_images'] == 1
                assert body['classes'] == {'defect':1} and body['corrupted_images'] == []
                assert body['validation']['checked_images'] == 1
            elif case == 'dataset-revisions':
                assert (await get('/api/dataset/revisions')).json() == {'active_revision':None, 'revisions':[]}
            elif case == 'dataset-active-source-status':
                body = (await get('/api/dataset/revisions/'+revision.revision_id+'/source-status')).json()
                assert body['revision_id'] == revision.revision_id and body['matches'] is True
                assert body['images'] == body['revision_images'] == 1
                assert (body['added'], body['removed'], body['changed'], body['gaps_changed']) == (0, 0, 0, False)
            elif case == 'project-preferences':
                assert (await get('/api/project/preferences')).json() == {
                    'schema_version':1, 'revision':0, 'tag_colors':{},
                    'model_flags':{}, 'labelset_flags':{}, 'audit':[]}
            elif case == 'dataset-statistics':
                body = (await get('/api/dataset/metadata/statistics')).json()
                assert body['total'] == 1 and body['labelset_id'] == project.get('active_labelset_id', 'default')
                assert body['classes']['defect']['count'] == body['labeling']['labeled']['count'] == 1
            elif case == 'saved-data-diagnostics':
                assert (await get('/api/data-workbench/diagnostics')).json() == {'report':None}
            elif case == 'onboarding-team':
                body = (await get('/api/onboarding')).json()
                assert body['version'] == 1 and body['team'] is True
                assert body['dismissed'] is True and body['example_projects'] == []
            elif case == 'project-list':
                body = (await get('/api/project/list')).json()
                assert len(body['projects']) == 1 and body['projects'][0]['id'] == project['id']
            elif case == 'team-readiness':
                body = (await get('/api/team-data/readiness')).json()
                assert type(body['ready']) is bool and body['counts']['total'] == 1
                assert body['counts']['eligible'] in (0, 1)
                assert len(body['policy_sha256']) == 64 and len(body['eligibility_sha256']) == 64
            elif case == 'provenance-impact':
                body = (await get('/api/provenance/impact')).json()
                assert body['project_id'] == project['id'] and body['source_dataset_path'] == str(source)
                assert body['labelset_id'] == project.get('active_labelset_id', 'default')
                assert body['models'] == []
                assert len(body['flows']) == 1 and body['flows'][0]['version_id'] == seed
            elif case == 'job-events-initial':
                body = (await get('/api/job-events', params={'limit':200})).json()
                assert body['reset'] is True and body['reason'] == 'initial'
                assert body['events'] == [] and body['more'] is False and body['cursor'].startswith('v1.')
            elif case == 'dataset-thumbnail-cold-and-warm':
                path = '/api/dataset/thumbnail/'+image.name
                params = {'file_path':str(image), 'size':128}
                cold = await get(path, params=params)
                assert cold.headers['content-type'] == 'image/jpeg' and cold.headers['etag']
                # Decode returned real JPEG, after the real source/cache handler
                # returned; this decoder is test inspection, not a witness.
                with Image.open(BytesIO(cold.content)) as decoded:
                    decoded.load()
                    assert decoded.size == (16, 16)
                warm = await get(path, params=params)
                assert warm.content == cold.content and warm.headers['etag'] == cold.headers['etag']
                cache = tuple((tmp_path/'thumbnails').glob('*.jpg'))
                assert len(cache) == 1 and cache[0].read_bytes() == cold.content
            else:
                raise AssertionError('Unknown literal mounted UI case')
        return requests

    requests = asyncio.run(original_sequence())
    assert all(file.read_bytes() == raw for file, raw in basis.items())
    assert audit.verify() == {'intact':True, 'rows':0}
    assert {file.name for file in flows._version_dir(root).iterdir()} == {seed+'.json'}
    assert app.state.accounts.project_for(actor['id'])['id'] == project['id']
    rows = observer.snapshot()['records']
    assert len(rows) == 2 + len(requests)
    assert [row['ingress'] for row in rows[2:]] == [
        {'type':'http', 'method':method, 'path':path} for method, path in requests]
    assert all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in rows)
    admission.close()
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}
    assert observer.snapshot()['all_settled'] and not observer.snapshot()['uncertain']
    assert all(row['completed'] for row in observer.snapshot()['records'])
    _no_whole_credit(observer)


@pytest.mark.parametrize('mutation', ('singleton', 'mutex', 'lease-context-delegate'))
def test_original_compute_resource_binding_remains_exact_through_ASGI_finish(mutation, tmp_path, monkeypatch):
    """Real bodies/locked snapshots/SQLite closes precede the late binding fault.

    The lease variant is an ongoing instance/source-delegate control. Both real
    original SQLite connections are already closed; it is deliberately NOT
    described as an unclosed-connection fault or evidence of its close check.
    """
    import sqlite3
    import threading
    from types import MethodType
    from backend.api import routes_training
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'runtime-user-data'))
    app, admission, observer, _, project, _, _, _, audit = _stack(tmp_path, monkeypatch)
    original_manager = routes_training.training_job_manager
    # Original data/resource construction before every observed request; no job
    # or thread is started. This distinct same-class singleton is never adopted.
    foreign_manager = routes_training.TrainingJobManager() if mutation == 'singleton' else None
    path = '/api/compute/reservations' if mutation == 'lease-context-delegate' else '/api/compute/jobs'
    altered = []
    async def actual_transport(scope, receive, send):
        async def original_send(message):
            if scope.get('path') == path and message['type'] == 'http.response.start':
                assert message['status'] == 200
                attempts = [attempt for attempt in admission._UI_ancillary_requests.pending.values()
                            if attempt.scope is scope]
                assert len(attempts) == 1
                attempt = attempts[0]
                assert attempt.handler_done and not attempt.worker_live and attempt.result is not None
                if mutation in {'singleton', 'mutex'}:
                    assert len(attempt.job_lists) == len(attempt.job_snapshots) == 1
                    assert attempt.job_lists[0] is attempt.job_snapshots[0] and attempt.job_lists[0] == []
                    assert not original_manager._lock.locked()
                    if mutation == 'singleton':
                        assert foreign_manager is not original_manager and type(foreign_manager) is type(original_manager)
                        monkeypatch.setattr(routes_training, 'training_job_manager', foreign_manager)
                    else:
                        retained_lock = original_manager._lock
                        monkeypatch.setattr(original_manager, '_lock', threading.Lock())
                        assert original_manager._lock is not retained_lock
                else:
                    assert len(attempt.lease_connections) == 2
                    store = attempt.lease_connections[0][0]
                    assert store is attempt.lease_connections[1][0]
                    for _, connection, _, _ in attempt.lease_connections:
                        with pytest.raises(sqlite3.ProgrammingError):
                            connection.in_transaction
                    original_connect = store.connect
                    def delegate(current, *args, **kwargs):
                        assert current is store
                        return original_connect(*args, **kwargs)
                    monkeypatch.setattr(store, 'connect', MethodType(delegate, store))
                    assert store.connect.__self__ is store and store.connect.__func__ is delegate
                altered.append(attempt)
            await send(message)
        await app(scope, receive, original_send)
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=actual_transport),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get(path, headers=headers)
            assert response.status_code == 200
            assert response.json() == ({'reservations':[]} if mutation == 'lease-context-delegate' else {'jobs':[]})
    asyncio.run(sequence())
    assert len(altered) == 1 and audit.verify() == {'intact':True, 'rows':0}
    rows = observer.snapshot()['records']
    assert len(rows) == 3 and all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in rows)
    _refused(admission)
    _no_whole_credit(observer)


def test_original_compute_unsupported_input_branch_cannot_borrow_source_only_scope(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'runtime-user-data'))
    app, admission, observer, _, project, _, _, _, audit = _stack(tmp_path, monkeypatch)
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/compute/inputs', params={'task':'rotation'}, headers=headers)
            assert response.status_code == 200 and response.json() == {'inputs':[]}
    asyncio.run(sequence())
    assert audit.verify() == {'intact':True, 'rows':0}
    rows = observer.snapshot()['records']
    assert len(rows) == 3 and all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in rows)
    _refused(admission)
    _no_whole_credit(observer)


@pytest.mark.parametrize('mutation', ('late-map', 'equal-member-alias'))
def test_original_workspace_iterator_and_member_cannot_be_substituted(mutation, tmp_path, monkeypatch):
    """Real file/HTTP success cannot stand in for the exact captured producer.

    late-map changes only the actual already-exhausted listing record at the
    real response.start boundary. equal-member-alias delegates the actual
    reader through a same-value distinct Path and checks the yielded-member
    identity guard, rather than claiming an unchanged supplier alias.
    """
    from backend.engine import flow_workspace
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'runtime-user-data'))
    monkeypatch.setenv('MODU_FLOW_TEMPLATE_DIR', str(tmp_path/'templates'))
    app, admission, observer, actor, project, graph, _, _, audit = _stack(tmp_path, monkeypatch)
    library = tmp_path/'templates'/'accounts'/hashlib.sha256(actor['id'].encode()).hexdigest()
    template = flow_workspace.save_template(library, graph, name='Actual original member', project_id=project['id'])
    file = library/(template['template_id']+'.json')
    before = file.read_bytes()
    altered = []
    if mutation == 'equal-member-alias':
        reader = flow_workspace.original_UI_ancillary_read_text
        def original_reader_with_distinct_path(path, *args, **kwargs):
            assert kwargs.get('role') == 'workspace_template'
            different = Path(str(path))
            assert different is not path and different == path
            altered.append((path, different))
            return reader(different, *args, **kwargs)
        monkeypatch.setattr(flow_workspace, 'original_UI_ancillary_read_text', original_reader_with_distinct_path)
    async def actual_transport(scope, receive, send):
        async def original_send(message):
            if (mutation == 'late-map' and scope.get('path') == '/api/flow-workspace/templates'
                    and message['type'] == 'http.response.start'):
                assert message['status'] == 200
                attempts = [attempt for attempt in admission._UI_ancillary_requests.pending.values() if attempt.scope is scope]
                assert len(attempts) == 1 and attempts[0].handler_done and not attempts[0].worker_live
                assert len(attempts[0].workspace_listings) == 1
                row = attempts[0].workspace_listings[0]
                assert row['kind'] is map and type(row['iterator']) is map
                assert row['complete'] and not row['live'] and len(row['members']) == 1
                retained = row['iterator']
                foreign = map(lambda value:value, tuple(row['members']))
                assert foreign is not retained and type(foreign) is type(retained)
                row['iterator'] = foreign
                altered.append((retained, foreign))
            await send(message)
        await app(scope, receive, original_send)
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=actual_transport), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/flow-workspace/templates', headers=headers)
            assert response.status_code == 200 and response.json() == {'templates':[template]}
    asyncio.run(sequence())
    assert len(altered) == 1 and file.read_bytes() == before and audit.verify() == {'intact':True, 'rows':0}
    rows = observer.snapshot()['records']
    assert len(rows) == 3 and all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in rows)
    _refused(admission)
    _no_whole_credit(observer)



def test_original_dataset_index_connect_binding_survives_until_ASGI_finish(tmp_path, monkeypatch):
    """Change only the actual produced/closed index instance after real reads."""
    from types import MethodType
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'runtime-user-data'))
    app, admission, observer, _, project, _, _, _, audit = _stack(tmp_path, monkeypatch)
    altered = []
    async def actual_transport(scope, receive, send):
        async def original_send(message):
            if scope.get('path') == '/api/dataset/revisions' and message['type'] == 'http.response.start':
                assert message['status'] == 200
                attempts = [attempt for attempt in admission._UI_ancillary_requests.pending.values() if attempt.scope is scope]
                assert len(attempts) == 1
                attempt = attempts[0]
                assert attempt.handler_done and not attempt.worker_live and not attempt.dataset_live
                assert any(kind == 'index_active' for kind, _ in attempt.dataset_operations)
                assert any(kind == 'index_revisions' for kind, _ in attempt.dataset_operations)
                assert len(attempt.dataset_connections) >= 2 and all(closed for _, _, closed in attempt.dataset_connections)
                stores = [store for kind, store, _ in attempt.dataset_connections if kind == 'index']
                assert stores and all(store is stores[0] for store in stores)
                store = stores[0]
                assert store is app.state.dataset_imports.index and type(store) is attempt.owner.dataset_index_kind
                original_connect = store._connect
                assert type(original_connect) is MethodType and original_connect.__self__ is store
                def delegate(current, *args, **kwargs):
                    assert current is store
                    return original_connect(*args, **kwargs)
                monkeypatch.setattr(store, '_connect', MethodType(delegate, store))
                assert store._connect.__self__ is store and store._connect.__func__ is delegate
                altered.append(store)
            await send(message)
        await app(scope, receive, original_send)
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=actual_transport), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/dataset/revisions', headers=headers)
            assert response.status_code == 200 and response.json() == {'active_revision':None, 'revisions':[]}
    asyncio.run(sequence())
    assert len(altered) == 1 and audit.verify() == {'intact':True, 'rows':0}
    rows = observer.snapshot()['records']
    assert len(rows) == 3 and all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in rows)
    _refused(admission)
    _no_whole_credit(observer)
