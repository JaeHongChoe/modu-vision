"""Root-only draft: original catalog/verify ASGI and scoped model data.

The tiny CPU-serialized dictionary and completed receipt are disclosed test
data in the same format as test_retained_preprocessor_binding, not evidence
of training, runnable weights, inference, model quality or native ownership.
No handler, resolver, account/SQLite method or lifetime witness is replaced.

First causal selector: test_owned_model_catalog_and_verification_drains.
R749 should pass its HTTP/source/metadata assertions, then refuse only drain.
No implementation belongs in this draft or the immutable R749 checkout.
"""
import asyncio
import json
from pathlib import Path

import httpx
import pytest


def _model_stack(tmp_path, monkeypatch):
    from PIL import Image
    import torch
    from backend.api import routes_dataset
    from backend.engine.annotation_storage import (
        set_request_project_root, reset_request_project_root,
    )
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.tests.test_original_owned_flow_lifetime import _stack

    monkeypatch.chdir(tmp_path)
    app, admission, observer, actor, project, _, _, _, audit = _stack(tmp_path, monkeypatch)
    source = Path(project['source_dataset_dir'])
    image = source/'owned-catalog-source.png'
    Image.new('RGB', (8, 8), (30, 70, 90)).save(image)
    scope = set_request_project_root(Path(project['project_dir']))
    try:
        fingerprint = fingerprint_dataset(source,
            studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
            split_manifest=routes_dataset._split_manifest_file(source))
    finally:
        reset_request_project_root(scope)
    job = 'job_owned_catalog_verification'
    directory = Path(project['models_dir'])/job
    directory.mkdir(parents=True)
    torch.save({'task':'segmentation', 'model_state_dict':{}}, directory/'best_model.pt')
    (directory/'model_meta.json').write_text(json.dumps({
        'task':'segmentation', 'model_name':'Owned data-only checkpoint',
        'classes':['background','defect'], 'optimal_threshold':.61,
    }), encoding='utf-8')
    (directory/'job_receipt.json').write_text(json.dumps({
        'job_id':job, 'status':'completed', 'task':'segmentation',
        'source_dataset_path':str(source), 'dataset_path':str(source),
        'dataset_fingerprint':fingerprint,
    }), encoding='utf-8')
    basis = {path:path.read_bytes() for path in (
        image, directory/'best_model.pt', directory/'model_meta.json', directory/'job_receipt.json')}
    assert observer.snapshot()['records'] == []
    return app, admission, observer, actor, project, job, image, basis, audit


def _unchanged(basis):
    assert all(path.read_bytes() == raw for path, raw in basis.items())


def test_owned_model_catalog_and_verification_drains(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit
    app, admission, observer, _, project, job, _, basis, audit = _model_stack(tmp_path, monkeypatch)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            catalog = await client.get('/api/flowchart/models/catalog', headers=headers,
                params={'source_dataset_path':project['source_dataset_dir']})
            assert catalog.status_code == 200
            body = catalog.json()
            assert body['total'] == len(body['models']) == 1
            model = body['models'][0]
            assert model['job_id'] == job and model['task'] == 'segmentation'
            assert model['source_dataset_path'] == project['source_dataset_dir']
            assert model['threshold_settings'] == {'optimal_threshold':.61}
            assert model['capabilities'] == {'role':'inspection','task':'segmentation'}
            assert model['label'] == 'Owned data-only checkpoint · '+job and model['created_at']
            verified = await client.post('/api/flowchart/models/verify', headers=headers, json={
                'source_dataset_path':project['source_dataset_dir'],
                'models':[{'job_id':job,'task':'segmentation'}]})
            assert verified.status_code == 200
            assert verified.json() == {'verified_job_ids':[job]}
    asyncio.run(original_sequence())
    _unchanged(basis)
    assert audit.verify() == {'intact':True,'rows':0}
    records = observer.snapshot()['records']
    assert len(records) == 4
    assert all(row['completed'] and row['normal_return'] and row['released'] for row in records)
    _no_whole_credit(observer)
    admission.close()
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    final = observer.snapshot()
    assert final['all_settled'] and not final['uncertain']
    _no_whole_credit(observer)


@pytest.mark.parametrize('changed', ['source', 'task'])
def test_owned_model_verification_preserves_source_task_refusal(tmp_path, monkeypatch, changed):
    from PIL import Image
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit
    app, admission, observer, _, project, job, image, basis, audit = _model_stack(tmp_path, monkeypatch)
    task = 'classification' if changed == 'task' else 'segmentation'
    if changed == 'source':
        Image.new('RGB', (9, 9), (90, 70, 30)).save(image)
        basis[image] = image.read_bytes()
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if changed == 'source':
                catalog = await client.get('/api/flowchart/models/catalog', headers=headers,
                    params={'source_dataset_path':project['source_dataset_dir']})
                assert catalog.status_code == 200 and catalog.json() == {'models':[],'total':0}
            response = await client.post('/api/flowchart/models/verify', headers=headers, json={
                'source_dataset_path':project['source_dataset_dir'], 'models':[{'job_id':job,'task':task}]})
            assert response.status_code == 409
            assert response.json() == {'detail':
                f'Model {job} is not a completed {task} model for the selected dataset.'}
    asyncio.run(original_sequence())
    _unchanged(basis)
    assert audit.verify() == {'intact':True,'rows':0}
    _refused(admission)
    _no_whole_credit(observer)
