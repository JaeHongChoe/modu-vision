"""Original nonempty library GET and scope drain; no model execution."""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest


def _basis(root):
    result = {}
    for path in sorted(root.rglob('*')):
        assert not path.is_symlink()
        if not path.is_file():
            continue
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        token = lambda row: [row.st_dev, row.st_ino, row.st_mode, row.st_nlink,
                             row.st_uid, row.st_gid, row.st_size, row.st_mtime_ns, row.st_ctime_ns]
        assert token(before) == token(after)
        result[str(path.relative_to(root))] = {'raw9': token(after),
                                             'sha256': hashlib.sha256(raw).hexdigest()}
    return result


def test_original_populated_package_library_preserves_artifacts_and_drains(tmp_path, monkeypatch):
    from backend.engine.flow_package import build_flow_package
    from backend.engine.product_delivery import record_package
    from backend.tests.test_original_owned_run_export_lifetime import _model_stack, _login_current

    monkeypatch.chdir(tmp_path)
    app, admission, observer, project, graph, seed, checkpoint, before = _model_stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    built = build_flow_package(pipeline=graph, checkpoints={checkpoint.parent.name: checkpoint},
                              output_base_dir=root/'exports'/'flows', package_name='original_populated_library')
    package = Path(built['package_path'])
    identifier = record_package(project, package, version_id=seed, recipe_task='ocr', parity={'status': 'not_run'})
    assert observer.snapshot()['records'] == [] and observer.snapshot()['failures'] == []
    # Original setup prerequisite, closed before the immutable business baseline.
    from backend.engine.migration_guard import maintenance_guard
    with maintenance_guard(root):
        pass
    assert observer.snapshot()['records'] == [] and observer.snapshot()['failures'] == []
    baseline = _basis(root)

    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            return await client.get('/api/product-delivery/packages', headers=headers)

    response = asyncio.run(sequence())
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body['packages']) == 1 and body['selected_package_id'] is None
    row = body['packages'][0]
    assert row['package_id'] == identifier and row['integrity'] == 'verified' and row['scope_matches']
    assert row['package_path'] == str(package) and row['version_id'] == seed
    assert row['parity'] == {'status': 'not_run'}
    assert body['scope'] == {'project_id': project['id'], 'source_dataset_path': project['source_dataset_dir'],
                             'task': project['task']}
    assert _basis(root) == baseline
    assert all(path.read_bytes() == raw for path, raw in before.items())
    records = observer.snapshot()['records']
    assert len(records) == 3 and all(row['released'] and row['normal_return'] and row['completed'] for row in records)
    admission.close()
    drain = admission.drain(.03)
    snapshot = observer.snapshot()
    proof = {'status': response.status_code, 'body': body, 'baseline': baseline,
             'after': _basis(root), 'failures': snapshot['failures'], 'drain': drain,
             'envelopes': [{key: row[key] for key in ('identity', 'ingress', 'released', 'normal_return', 'completed')}
                           for row in snapshot['records']], 'whole_writer_coverage': False,
             'native_emitter_identity_proven': False, 'model_forward_training_GPU': False}
    assert drain == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    assert snapshot['all_settled'] and snapshot['failures'] == []


@pytest.mark.parametrize('case', ('changed-package-bytes','escaping-manifest-path','foreign-source-binding',
                                  'delegated-verifier','delegated-library','missing-auth'))
def test_populated_library_keeps_original_refusals(case, tmp_path, monkeypatch):
    from backend.engine import product_delivery as delivery
    from backend.engine.flow_package import build_flow_package
    from backend.tests.test_original_owned_run_export_lifetime import _model_stack, _login_current
    monkeypatch.chdir(tmp_path)
    app, admission, observer, project, graph, seed, checkpoint, original = _model_stack(tmp_path, monkeypatch)
    root=Path(project['project_dir'])
    result=build_flow_package(pipeline=graph,checkpoints={checkpoint.parent.name:checkpoint},
        output_base_dir=root/'exports'/'flows',package_name='guarded_original_library')
    package=Path(result['package_path'])
    identifier=delivery.record_package(project,package,version_id=seed,recipe_task='ocr',parity={'status':'not_run'})
    from backend.engine.migration_guard import maintenance_guard
    with maintenance_guard(root):pass
    if case=='changed-package-bytes':
        (package/'models'/checkpoint.parent.name/'best_model.pt').write_bytes(b'changed owned package bytes')
    elif case=='escaping-manifest-path':
        outside=root/'outside-package.txt';outside.write_bytes(b'foreign package input must not be opened')
        manifest=package/'manifest.json';value=json.loads(manifest.read_text())
        value['files'][0]={'path':'../outside-package.txt','size':outside.stat().st_size,
                           'sha256':hashlib.sha256(outside.read_bytes()).hexdigest()}
        manifest.write_text(json.dumps(value))
    elif case=='foreign-source-binding':
        journal=root/'delivery'/'library.json';value=json.loads(journal.read_text())
        value['packages'][identifier]['source_dataset_path']=str(tmp_path/'foreign-source')
        journal.write_text(json.dumps(value))
    elif case=='delegated-verifier':
        held=delivery.verify_flow_package
        def delegate(*args,**kwargs):return held(*args,**kwargs)
        monkeypatch.setattr(delivery,'verify_flow_package',delegate)
    elif case=='delegated-library':
        held=delivery.package_library
        def delegate(*args,**kwargs):return held(*args,**kwargs)
        monkeypatch.setattr(delivery,'package_library',delegate)
    baseline=_basis(root)
    assert observer.snapshot()['records']==[] and observer.snapshot()['failures']==[]
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid') as client:
            if case=='missing-auth':return await client.get('/api/product-delivery/packages')
            headers=await _login_current(client,app,project)
            return await client.get('/api/product-delivery/packages',headers=headers)
    response=asyncio.run(sequence())
    assert _basis(root)==baseline
    assert all(path.read_bytes()==raw for path,raw in original.items())
    if case=='missing-auth':
        assert response.status_code==401
    else:
        assert response.status_code==200,response.text
        rows=response.json()['packages'];assert len(rows)==1
        if case in {'changed-package-bytes','escaping-manifest-path'}:assert rows[0]['integrity']=='failed'
        elif case=='foreign-source-binding':assert rows[0]['integrity']=='verified' and rows[0]['scope_matches'] is False
        else:assert rows[0]['integrity']=='verified' and rows[0]['scope_matches'] is True
    records=observer.snapshot()['records']
    assert len(records)==(1 if case=='missing-auth' else 3)
    assert all(row['released'] and row['normal_return'] and row['completed'] for row in records)
    admission.close();drain=admission.drain(.03)
    if case=='missing-auth':
        assert drain=={'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
        assert observer.snapshot()['failures']==[]
    else:
        assert drain['status']=='refused' and drain['unsupported']==['uncovered_request_protocol']
        assert drain['active_scopes']==(0 if case.startswith('delegated-') else 1)
    final=observer.snapshot()
    assert all(final[name] is False for name in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))
