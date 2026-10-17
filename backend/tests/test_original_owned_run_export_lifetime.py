"""Tests-first draft: real CPU Run and no-parity Export, never native/GPU proof.

Root alone may copy this into a NEW R749 successor and execute it. Model/source
files are fresh temporary fixtures. No auth/ASGI/owner/handler/DB/compute/package
function is replaced; the existing owned component fixture supplies admission.
There is no training, SSH, GPU, parity, approval or whole-process-tree claim.
"""
import asyncio
import hashlib
import json
from pathlib import Path


def _model_stack(tmp_path, monkeypatch):
    from PIL import Image
    import torch
    from backend.api import routes_flowchart as flows
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.engine.ocr import SmallCTCOCR, load_ocr_manifest, write_ocr_manifest
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects

    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    _, (project,) = _owned_projects(app, observer, 'Owned CPU Run Export')
    root = Path(project['project_dir'])
    source = Path(project['dataset_dir']).resolve()
    project.update(task='segmentation', source_dataset_dir=str(source))
    flows._write_json(root/'project.json', project)
    rows = []
    for number, split in enumerate(('train', 'val', 'test')):
        name = f'fixture-{split}.png'
        Image.new('RGB', (64, 32), (35+number*70, 20, 10)).save(source/name)
        rows.append({'image':name, 'text':'A', 'split':split})
    write_ocr_manifest(source, rows)
    provenance = load_ocr_manifest(source).provenance
    job = 'f6'*16
    directory = Path(project['models_dir'])/'ocr'/job
    directory.mkdir(parents=True)
    checkpoint = directory/'best_model.pt'
    # Exact existing SmallCTCOCR fixture adapter; no optimizer or training step.
    with torch.device('cpu'), torch.no_grad():
        model = SmallCTCOCR(1)
        for parameter in model.parameters():
            parameter.zero_()
        model.head.bias[0] = 20
    torch.save({'task':'ocr', 'version':1, 'alphabet':'A', 'image_size':[32,64],
        'model_state_dict':model.state_dict(), 'dataset_provenance':provenance}, checkpoint)
    assert 0 < checkpoint.stat().st_size < 1024*1024
    metadata = directory/'model_meta.json'
    metadata.write_text(json.dumps({'task':'ocr', 'version':1, 'alphabet':'A',
        'image_size':[32,64], 'dataset_path':str(source), 'dataset_provenance':provenance,
        'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest()}))
    graph = get_single_segmentation_flowchart(job)
    graph.execution_config.max_workers = 1
    graph.execution_config.device_slots = 1
    inspection = next(node for node in graph.nodes if node.data.node_type == 'inspection')
    inspection.data.task = 'ocr'
    inspection.data.params = {'regex':'^$'}
    seed = flows._save_version(graph, 'ocr', str(source), root)
    # This legacy fixture has no recorded training journal. Do not fabricate a
    # completed training job to pass the original checkpoint validator.
    protected = [checkpoint, metadata, source/'ocr.json',
                 *(source/row['image'] for row in rows)]
    before = {path:path.read_bytes() for path in protected}
    assert observer.snapshot()['records'] == []
    return app, admission, observer, project, graph, seed, checkpoint, before


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


def _unchanged(before):
    assert all(path.read_bytes() == body for path, body in before.items())


def _drains(admission, observer):
    snapshot = observer.snapshot()
    assert len(snapshot['records']) == 3
    assert all(row['completed'] and row['normal_return'] and row['released']
               for row in snapshot['records'])
    admission.close()
    # R749 causal RED must occur here, after the real original HTTP/data work.
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}
    final = observer.snapshot()
    assert final['all_settled'] and not final['uncertain']
    assert all(final[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


def test_original_owned_local_CPU_run_real_small_checkpoint_drains(tmp_path, monkeypatch):
    import httpx
    app, admission, observer, project, graph, _, _, before = _model_stack(tmp_path, monkeypatch)

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            return await client.post('/api/flowchart/run', headers=headers, json={
                'pipeline':graph.model_dump(),
                'image_path':str(Path(project['dataset_dir'])/'fixture-test.png'),
                'image_id':None, 'project_id':project['id'],
                'execution_target':'local', 'device':'cpu'})

    response = asyncio.run(original_sequence())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['execution_target'] == 'local' and result['execution_device'] == 'cpu'
    assert result['compute_profile_id'] is None
    assert result['final_verdict'] == 'OK' and result['roi_count'] == 1
    assert result['crops'][0]['recognized_text'] == ''
    from backend.api import routes_flowchart as flows
    assert flows._CPU_ENGINE.execution_resources()['active_executions'] == 0
    _unchanged(before)
    _drains(admission, observer)


def test_original_owned_export_real_package_and_library_drains(tmp_path, monkeypatch):
    import httpx
    from backend.engine.flow_package_runtime import verify_flow_package
    app, admission, observer, project, graph, seed, checkpoint, before = _model_stack(tmp_path, monkeypatch)

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            return await client.post('/api/export/flow', headers=headers, json={
                'source_dataset_path':project['source_dataset_dir'], 'recipe_task':'ocr',
                'version_id':seed, 'package_name':'owned_small_CPU_fixture'})

    response = asyncio.run(original_sequence())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'success' and result['parity'] == {'status':'not_run'}
    package = Path(result['package_path'])
    assert package.is_relative_to(Path(project['project_dir'])/'exports'/'flows')
    packaged, models = verify_flow_package(package)
    assert packaged.model_dump() == graph.model_dump()
    assert set(models) == {checkpoint.parent.name}
    assert models[checkpoint.parent.name].read_bytes() == before[checkpoint]
    journal = json.loads((Path(project['project_dir'])/'delivery'/'library.json').read_bytes())
    assert len(journal['packages']) == 1
    row = next(iter(journal['packages'].values()))
    assert row['version_id'] == seed and row['recipe_task'] == 'ocr'
    assert row['project_id'] == project['id'] and row['source_dataset_path'] == project['source_dataset_dir']
    assert row['parity'] == {'status':'not_run'}
    assert row['manifest_sha256'] == hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest()
    _unchanged(before)
    _drains(admission, observer)



def _refuses_execution(admission, observer, pending):
    admission.close()
    assert admission.drain(.03) == {
        'status':'refused', 'active_scopes':pending,
        'unsupported':['uncovered_request_protocol']}
    final = observer.snapshot()
    assert all(final[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


def test_original_two_worker_CPU_business_result_cannot_claim_linear_lifetime(tmp_path, monkeypatch):
    import httpx
    app, admission, observer, project, graph, _, _, before = _model_stack(tmp_path, monkeypatch)
    graph.execution_config.max_workers = 2
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            return await client.post('/api/flowchart/run', headers=headers, json={
                'pipeline':graph.model_dump(), 'image_path':str(Path(project['dataset_dir'])/'fixture-test.png'),
                'image_id':None, 'project_id':project['id'], 'execution_target':'local', 'device':'cpu'})
    response = asyncio.run(sequence())
    assert response.status_code == 200, response.text
    assert response.json()['final_verdict'] == 'OK' and response.json()['roi_count'] == 1
    _unchanged(before)
    _refuses_execution(admission, observer, 1)


def test_original_CPU_instance_delegate_is_ordinary_unowned_work(tmp_path, monkeypatch):
    import httpx
    from types import MethodType
    from backend.api import routes_flowchart as flows
    app, admission, observer, project, graph, _, _, before = _model_stack(tmp_path, monkeypatch)
    engine = flows._CPU_ENGINE
    original = engine.execute
    def delegate(self, *args, **kwargs):
        assert self is engine
        return original(*args, **kwargs)
    monkeypatch.setattr(engine, 'execute', MethodType(delegate, engine))
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            return await client.post('/api/flowchart/run', headers=headers, json={
                'pipeline':graph.model_dump(), 'image_path':str(Path(project['dataset_dir'])/'fixture-test.png'),
                'image_id':None, 'project_id':project['id'], 'execution_target':'local', 'device':'cpu'})
    response = asyncio.run(sequence())
    assert response.status_code == 200, response.text
    assert response.json()['final_verdict'] == 'OK' and response.json()['roi_count'] == 1
    _unchanged(before)
    _refuses_execution(admission, observer, 0)


def test_original_export_library_error_releases_real_lock_but_refuses_drain(tmp_path, monkeypatch):
    import httpx
    from backend.engine.runtime_process_control import runtime_state_lock
    app, admission, observer, project, graph, seed, _, before = _model_stack(tmp_path, monkeypatch)
    delivery = Path(project['project_dir'])/'delivery'
    delivery.mkdir(exist_ok=True)
    # A real unusable data path makes the original journal read fail after the
    # original package was built and the original library fd/lock was acquired.
    (delivery/'library.json').mkdir()
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            return await client.post('/api/export/flow', headers=headers, json={
                'source_dataset_path':project['source_dataset_dir'], 'recipe_task':'ocr',
                'version_id':seed, 'package_name':'owned_library_error_fixture'})
    response = asyncio.run(sequence())
    assert response.status_code == 500
    package = Path(project['project_dir'])/'exports'/'flows'/'owned_library_error_fixture'
    assert package.is_dir() and (package/'manifest.json').is_file()
    assert (delivery/'library.json').is_dir()
    # Reacquire the real original RLock/OFD after the failing request; this is
    # neither a lease-release claim nor a replacement observer authority.
    with runtime_state_lock(delivery):
        pass
    _unchanged(before)
    _refuses_execution(admission, observer, 1)



def test_original_export_changed_saved_graph_lock_is_unowned_after_real_publication(tmp_path, monkeypatch):
    import httpx
    import threading
    from backend.api import routes_export as exports
    from backend.engine.flow_package_runtime import verify_flow_package
    app, admission, observer, project, graph, seed, checkpoint, before = _model_stack(tmp_path, monkeypatch)
    # A real replacement RLock preserves ordinary original Export behavior.
    # Its source-module alias is replaced only after the owner captured source.
    replacement = threading.RLock()
    assert replacement is not exports._FLOW_SAVE_LOCK
    monkeypatch.setattr(exports, '_FLOW_SAVE_LOCK', replacement)
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            return await client.post('/api/export/flow', headers=headers, json={
                'source_dataset_path':project['source_dataset_dir'], 'recipe_task':'ocr',
                'version_id':seed, 'package_name':'owned_changed_graph_lock_fixture'})
    response = asyncio.run(sequence())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status']=='success' and result['parity']=={'status':'not_run'}
    package = Path(result['package_path'])
    packaged, models = verify_flow_package(package)
    assert packaged.model_dump()==graph.model_dump()
    assert models[checkpoint.parent.name].read_bytes()==before[checkpoint]
    journal = json.loads((Path(project['project_dir'])/'delivery'/'library.json').read_bytes())
    assert len(journal['packages'])==1
    row = next(iter(journal['packages'].values()))
    assert row['version_id']==seed and row['project_id']==project['id'] and row['parity']=={'status':'not_run'}
    _unchanged(before)
    _refuses_execution(admission, observer, 0)
