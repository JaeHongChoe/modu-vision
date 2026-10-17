"""Root-only real-ASGI controls for the two original model-read routes.

All checkpoint dictionaries/receipts here are disclosed CPU test data, not
training, executable-model, model-quality or native/whole-flow evidence.
Delegation controls change only a tested source boundary and retain the real
HTTP handler, original SQLite/auth stack, checkpoint resolver and ASGI return.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil

import httpx
import pytest

from backend.tests.test_original_owned_model_verification_lifetime import _model_stack, _unchanged
from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit
from backend.tests.test_original_owned_account_request_lifetime import _refused


async def _original_reads(client, headers, project, models):
    catalog = await client.get('/api/flowchart/models/catalog', headers=headers,
        params={'source_dataset_path':project['source_dataset_dir']})
    assert catalog.status_code == 200
    rows = catalog.json()
    assert rows['total'] == len(rows['models']) == len(models)
    assert {(row['job_id'],row['task']) for row in rows['models']} == {
        (row['job_id'],row['task']) for row in models}
    assert all(row['source_dataset_path'] == project['source_dataset_dir'] for row in rows['models'])
    verified = await client.post('/api/flowchart/models/verify', headers=headers, json={
        'source_dataset_path':project['source_dataset_dir'], 'models':models})
    assert verified.status_code == 200
    assert verified.json() == {'verified_job_ids':[row['job_id'] for row in models]}


@pytest.mark.parametrize('changed', ['resolver_alias','resolver_code','fingerprint_alias','manager_instance'])
def test_original_model_read_source_delegation_refuses(tmp_path, monkeypatch, changed):
    from backend.api import routes_flowchart as flows, routes_evaluation
    app, admission, observer, _, project, job, _, basis, audit = _model_stack(tmp_path, monkeypatch)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if changed == 'resolver_code':
                function = routes_evaluation._resolve_job_artifacts
                monkeypatch.setattr(function, '__code__', function.__code__.replace())
            else:
                owner, name = ((flows, '_resolve_job_artifacts') if changed == 'resolver_alias' else
                    (routes_evaluation, 'fingerprint_dataset') if changed == 'fingerprint_alias' else
                    (flows.training_job_manager, 'get_job'))
                original = getattr(owner, name)
                def delegated(*args, **kwargs):
                    return original(*args, **kwargs)
                monkeypatch.setattr(owner, name, delegated)
            await _original_reads(client, headers, project, [{'job_id':job,'task':'segmentation'}])
    asyncio.run(original_sequence())
    _unchanged(basis)
    assert audit.verify() == {'intact':True,'rows':0}
    assert len(observer.snapshot()['records']) == 4
    _refused(admission)
    _no_whole_credit(observer)


@pytest.mark.parametrize('changed', ['http_request','body'])
def test_original_verify_request_and_body_aliases_cannot_replace_originals(tmp_path, monkeypatch, changed):
    from starlette.requests import Request
    app, admission, observer, _, project, job, _, basis, audit = _model_stack(tmp_path, monkeypatch)
    altered = []
    async def original_transport(scope, receive, send):
        async def original_send(message):
            if scope.get('path') == '/api/flowchart/models/verify' and message['type'] == 'http.response.start':
                attempts = [attempt for attempt in admission._flow_requests.pending.values() if attempt.scope is scope]
                assert len(attempts) == 1
                attempt = attempts[0]
                if changed == 'http_request':
                    attempt.values['http_request'] = Request(scope)
                else:
                    attempt.values['request'] = attempt.values['request'].model_copy(deep=True)
                altered.append(changed)
            await send(message)
        await app(scope, receive, original_send)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_transport),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.post('/api/flowchart/models/verify', headers=headers, json={
                'source_dataset_path':project['source_dataset_dir'],
                'models':[{'job_id':job,'task':'segmentation'}]})
            assert response.status_code == 200 and response.json() == {'verified_job_ids':[job]}
    asyncio.run(original_sequence())
    assert altered == [changed]
    _unchanged(basis)
    assert audit.verify() == {'intact':True,'rows':0}
    assert len(observer.snapshot()['records']) == 3
    _refused(admission)
    _no_whole_credit(observer)


def _retained_model_stack(tmp_path, monkeypatch):
    from PIL import Image
    import torch
    from backend.api import routes_flowchart as flows
    from backend.engine.rotation import write_rotation_manifest, load_rotation_manifest
    from backend.engine.training_provenance import bind_family_training
    from backend.engine.retained_preprocessors import bind_retained_rotation, read_registered_project
    from backend.tests.test_original_owned_account_request_lifetime import _owned_projects
    app, admission, observer, _, target, segmentation_job, _, basis, audit = _model_stack(tmp_path, monkeypatch)
    _, (original_project,) = _owned_projects(app, observer, 'Owned original rotation source')
    source = tmp_path/'original-rotation-source'
    source.mkdir()
    prepared = Path(original_project['dataset_dir'])/'rotation'/'owned-prepared'
    prepared.mkdir(parents=True)
    rows = []
    for number, split in enumerate(('train','val','test')):
        name = split+'.png'
        Image.new('RGB', (8, 8), (20+number*60,35,80)).save(source/name)
        shutil.copyfile(source/name, prepared/name)
        rows.append({'image':name,'correction_deg':number*10,'split':split})
    write_rotation_manifest(prepared, rows, source_dataset_path=source)
    original_project.update(task='classification',source_dataset_dir=str(source))
    flows._write_json(Path(original_project['project_dir'])/'project.json', original_project)
    training = bind_family_training(original_project, prepared, 'rotation')
    job = 'e'*32
    directory = Path(original_project['models_dir'])/'rotation'/job
    directory.mkdir(parents=True)
    checkpoint = directory/'best_model.pt'
    provenance = load_rotation_manifest(prepared).provenance
    torch.save({'task':'rotation','version':1,'model_state_dict':{},
        'dataset_provenance':provenance,'training_provenance':training}, checkpoint)
    checkpoint_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    metadata = {'task':'rotation','dataset_path':str(prepared),'source_dataset_path':str(source),
        'checkpoint_sha256':checkpoint_sha,'dataset_provenance':provenance,'training_provenance':training}
    (directory/'model_meta.json').write_text(json.dumps(metadata,sort_keys=True), encoding='utf-8')
    receipt = {'job_id':job,'task':'rotation','status':'completed','source_dataset_path':str(source),
        'dataset_path':str(prepared),'checkpoint_sha256':checkpoint_sha,
        'dataset_fingerprint':training['dataset_fingerprint'],'training_provenance':training}
    (directory/'job_receipt.json').write_text(json.dumps(receipt,sort_keys=True), encoding='utf-8')
    source_manifest = Path(original_project['project_dir'])/'project.json'
    version_manifest = Path(training['version_dir'])/'manifest.json'
    bound = bind_retained_rotation(target,
        read_registered_project(original_project['project_dir'], original_project['id']), job_id=job,
        source_project_manifest_sha256=hashlib.sha256(source_manifest.read_bytes()).hexdigest(),
        checkpoint_sha256=checkpoint_sha,
        version_manifest_sha256=hashlib.sha256(version_manifest.read_bytes()).hexdigest())
    assert bound['job_id'] == job
    sidecar = Path(target['project_dir'])/'.retained-preprocessors'/(job+'.json')
    assert not (Path(target['models_dir'])/'rotation'/job).exists()
    paths = [*source.glob('*.png'),*prepared.glob('*.png'),prepared/'rotation.json',source_manifest,
        checkpoint,directory/'model_meta.json',directory/'job_receipt.json',version_manifest,sidecar]
    basis.update({path:path.read_bytes() for path in paths})
    assert observer.snapshot()['records'] == []
    models = [{'job_id':job,'task':'rotation'},{'job_id':segmentation_job,'task':'segmentation'}]
    return app, admission, observer, target, models, basis, audit


def test_original_retained_rotation_and_segmentation_catalog_verify_drains(tmp_path, monkeypatch):
    app, admission, observer, target, models, basis, audit = _retained_model_stack(tmp_path, monkeypatch)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, target)
            await _original_reads(client, headers, target, models)
    asyncio.run(original_sequence())
    _unchanged(basis)
    assert audit.verify() == {'intact':True,'rows':0}
    records = observer.snapshot()['records']
    assert len(records) == 4
    assert all(row['completed'] and row['normal_return'] and row['released'] for row in records)
    admission.close()
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    final = observer.snapshot()
    assert final['all_settled'] and not final['uncertain']
    _no_whole_credit(observer)
