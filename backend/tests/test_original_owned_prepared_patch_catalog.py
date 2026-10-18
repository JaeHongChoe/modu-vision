"""Actual source ASGI catalog, generated PATCH data; no model execution."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil

import httpx
import pytest


def _basis(paths):
    result = {}
    for path in paths:
        before = path.stat()
        body = path.read_bytes()
        after = path.stat()
        raw = lambda s: [s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_uid,
                         s.st_gid, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
        assert raw(before) == raw(after)
        result[str(path)] = {'sha256': hashlib.sha256(body).hexdigest(), 'raw9': raw(after)}
    return result


def _patch_stack(tmp_path, monkeypatch):
    import torch
    from PIL import Image
    from backend.api import routes_dataset, routes_flowchart as flows
    from backend.engine.annotation_storage import set_request_project_root, reset_request_project_root
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.patch_preparation import prepare_patch_dataset
    from backend.tests.test_original_owned_flow_lifetime import _stack

    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, _, _, _, audit = _stack(tmp_path, monkeypatch)
    source = tmp_path / 'original-patch-source'
    source.mkdir()
    for index in range(3):
        image = source / f'part-{index}.png'
        Image.new('RGB', (32, 32), (index * 20, 60, 90)).save(image)
        image.with_suffix('.json').write_text(json.dumps({
            'imagePath': image.name, 'imageWidth': 32, 'imageHeight': 32,
            'shapes': [{'label': 'chip', 'shape_type': 'rectangle', 'points': [[0, 0], [8, 8]]}],
        }))
    project['source_dataset_dir'] = str(source)
    flows._write_json(Path(project['project_dir']) / 'project.json', project)
    scope = set_request_project_root(Path(project['project_dir']))
    try:
        prepared = prepare_patch_dataset(source, Path(project['dataset_dir']) / 'patch' / 'owned-derived',
                                         patch_size=16, stride=16)
        fingerprint = fingerprint_dataset(source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
                                          split_manifest=routes_dataset._split_manifest_file(source))
    finally:
        reset_request_project_root(scope)
    dataset = Path(prepared['dataset_path'])
    assert prepared['patch_count'] == 12 and dataset != source
    job = 'job_owned_prepared_patch_catalog'
    directory = Path(project['models_dir']) / job
    directory.mkdir()
    # This serialized dictionary is deliberately not a learned/runnable model.
    checkpoint = directory / 'best_model.pt'
    torch.save({'task': 'patch_classification', 'model_state_dict': {}}, checkpoint)
    checkpoint_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    (directory / 'model_meta.json').write_text(json.dumps({
        'task': 'patch_classification', 'classes': ['OK', 'chip'],
        'checkpoint_sha256': checkpoint_sha,
    }))
    (directory / 'job_receipt.json').write_text(json.dumps({
        'job_id': job, 'status': 'completed', 'task': 'patch_classification',
        'source_dataset_path': str(source), 'dataset_path': str(dataset),
        'dataset_fingerprint': fingerprint, 'checkpoint_sha256': checkpoint_sha,
    }))
    paths = [*source.iterdir(), *(p for p in dataset.rglob('*') if p.is_file()),
             *directory.iterdir(), Path(project['project_dir']) / 'project.json']
    assert observer.snapshot()['records'] == []
    return app, admission, observer, project, job, source, dataset, paths, audit


def test_original_catalog_accepts_completed_owned_prepared_patch_lifetime(tmp_path, monkeypatch):
    from backend.api import routes_evaluation
    from backend.engine.annotation_storage import (
        set_request_project_root, reset_request_project_root,
        set_request_shared_scope, reset_request_shared_scope,
    )
    from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit

    app, admission, observer, project, job, source, dataset, paths, audit = _patch_stack(tmp_path, monkeypatch)
    before = _basis(paths)
    # Read the original resolver under the same original project scope before
    # the observed ASGI sequence. No resolver/handler/witness is replaced.
    root_token = set_request_project_root(Path(project['project_dir']))
    shared_token = set_request_shared_scope(True)
    try:
        resolved = routes_evaluation._resolve_job_artifacts(job, source_dataset_path=str(source))
    finally:
        reset_request_shared_scope(shared_token)
        reset_request_project_root(root_token)
    directory, checkpoint, metadata, task, resolved_job, resolved_dataset = resolved
    assert directory == Path(project['models_dir']) / job
    assert checkpoint == directory / 'best_model.pt'
    assert type(metadata) is dict and metadata['task'] == task == 'patch_classification'
    assert resolved_job == job and resolved_dataset == dataset and resolved_dataset != source
    assert observer.snapshot()['records'] == [] and list(observer._failures) == []
    result = {'schema': 'modu-vision.prepared-patch-catalog-lifetime-source/v1',
              'original_resolver_tuple': {'directory': str(directory), 'checkpoint': str(checkpoint),
                  'meta_task': metadata['task'], 'resolved_task': task, 'resolved_job': resolved_job,
                  'resolved_dataset': str(resolved_dataset), 'selected_original_source': str(source)},
              'only_original_model_return_shape_predicate_false': 'dataset == Path(source).resolve()',
              'no_profile_or_trace': True, 'no_method_or_observer_replacement': True,
              'native_exact_emitter_identity_claim': False, 'model_execution': False}

    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            assert list(observer._failures) == [] and not observer.snapshot()['uncertain']
            response = await client.get('/api/flowchart/models/catalog', headers=headers,
                                        params={'source_dataset_path': str(source)})
            assert response.status_code == 200
            body = response.json()
            assert body['total'] == len(body['models']) == 1
            assert body['models'][0]['job_id'] == job
            assert body['models'][0]['task'] == 'patch_classification'
            assert body['models'][0]['source_dataset_path'] == str(source)
            result['catalog'] = body

    try:
        asyncio.run(sequence())
        assert _basis(paths) == before and audit.verify() == {'intact': True, 'rows': 0}
        observed = observer.snapshot()
        result['original_failures'] = list(observer._failures)
        result['envelopes'] = observed['records']
        assert len(observed['records']) == 3
        assert all(row['completed'] and row['normal_return'] and row['released'] for row in observed['records'])
        _no_whole_credit(observer)
        admission.close()
        result['drain'] = admission.drain(.03)
        assert result['drain'] == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
        assert observer.snapshot()['all_settled'] and not observer.snapshot()['uncertain']
    finally:
        result['protected_before_after_equal'] = _basis(paths) == before
        result['original_failures'] = list(observer._failures)
        if os.environ.get('MV_PATCH_CATALOG_READBACK'):
            path = Path(os.environ['MV_PATCH_CATALOG_READBACK'])
            with path.open('x') as stream:
                path.chmod(0o600)
                json.dump(result, stream, indent=2, sort_keys=True)
                stream.write('\n')


@pytest.mark.parametrize('changed', ['foreign_prepared', 'foreign_source', 'manifest_source',
                                   'checkpoint_sha', 'resolver_alias', 'validator_alias'])
def test_prepared_patch_catalog_retains_foreign_and_replaced_provider_refusal(tmp_path, monkeypatch, changed):
    from backend.api import routes_flowchart as flows, routes_patch_classification as patches
    from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit
    from backend.tests.test_original_owned_account_request_lifetime import _refused

    app, admission, observer, project, job, source, dataset, paths, audit = _patch_stack(tmp_path, monkeypatch)
    receipt_path = Path(project['models_dir']) / job / 'job_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    if changed == 'foreign_prepared':
        foreign = tmp_path / 'another-project' / 'dataset' / 'patch' / 'prepared'
        shutil.copytree(dataset, foreign)
        receipt['dataset_path'] = str(foreign)
        receipt_path.write_text(json.dumps(receipt))
        paths.extend(p for p in foreign.rglob('*') if p.is_file())
    elif changed in {'foreign_source', 'manifest_source'}:
        foreign = tmp_path / 'another-original-source'
        shutil.copytree(source, foreign)
        paths.extend(foreign.iterdir())
        if changed == 'foreign_source':
            receipt['source_dataset_path'] = str(foreign)
            receipt_path.write_text(json.dumps(receipt))
        else:
            manifest_path = dataset / 'patches.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['source_dataset_path'] = str(foreign)
            manifest_path.write_text(json.dumps(manifest))
    elif changed == 'checkpoint_sha':
        receipt['checkpoint_sha256'] = '0' * 64
        receipt_path.write_text(json.dumps(receipt))
    before = _basis(paths)

    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            assert list(observer._failures) == []
            if changed in {'resolver_alias', 'validator_alias'}:
                module, name = (flows, '_resolve_job_artifacts') if changed == 'resolver_alias' else (patches, '_owned')
                original = getattr(module, name)
                def delegated(*args, **kwargs):
                    return original(*args, **kwargs)
                monkeypatch.setattr(module, name, delegated)
            response = await client.get('/api/flowchart/models/catalog', headers=headers,
                                        params={'source_dataset_path': str(source)})
            assert response.status_code == 200
            if changed == 'foreign_source':
                assert response.json() == {'models': [], 'total': 0}
            else:
                assert response.json()['models'][0]['job_id'] == job

    asyncio.run(sequence())
    assert _basis(paths) == before and audit.verify() == {'intact': True, 'rows': 0}
    records = observer.snapshot()['records']
    assert len(records) == 3 and all(row['completed'] and row['normal_return'] and row['released'] for row in records)
    _refused(admission)
    _no_whole_credit(observer)
