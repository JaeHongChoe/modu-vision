"""Root-only real original populated provenance read, not model execution.

Fresh temporary data uses the existing genuine SmallCTCOCR checkpoint fixture,
original immutable training binding, original package builder/library publisher,
original evaluation writer and original deployment schema constructor. No model
forward/training, HTTP handler, ownership witness or resource method is mocked.
The read-only GET must preserve all original artifacts before final drain.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil

import httpx


def test_original_populated_provenance_UI_preserves_real_resources_and_drains(tmp_path,monkeypatch):
    from backend.api import routes_dataset,routes_model_deployments
    from backend.engine.training_provenance import bind_training_version
    from backend.engine.evaluation_history import EvaluationHistory
    from backend.engine.flow_package import build_flow_package
    from backend.engine.product_delivery import record_package
    from backend.tests.test_original_owned_run_export_lifetime import _model_stack,_login_current
    from backend.tests.test_original_owned_flow_lifetime import _no_whole_credit
    from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root
    from backend.engine.dataset_loaders import split_root_scope
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'runtime-user-data'))
    app,admission,observer,project,graph,seed,checkpoint,before=_model_stack(tmp_path,monkeypatch)
    root=Path(project['project_dir']);source=Path(project['source_dataset_dir'])
    # Original project-scoped annotation/split contexts during data setup;
    # factory-captured global aliases and all HTTP ownership stay unchanged.
    token=set_request_annotation_root(Path(project['annotations_dir']))
    try:
        with split_root_scope(Path(project['dataset_dir'])/'splits'):
            binding=bind_training_version(project,source)
    finally:reset_request_annotation_root(token)
    assert binding['team_data'] and Path(binding['version_dir']).is_relative_to(root/'versions')
    metadata=checkpoint.parent/'model_meta.json'
    value=json.loads(metadata.read_text());value['training_provenance']=binding
    value['source_dataset_path']=str(source);metadata.write_text(json.dumps(value))
    second=checkpoint.parent.with_name('e7'*16);second.mkdir()
    shutil.copyfile(checkpoint,second/'best_model.pt')
    (second/'model_meta.json').write_text(json.dumps(value))
    job=checkpoint.parent.name
    built=build_flow_package(pipeline=graph,checkpoints={job:checkpoint},
        output_base_dir=root/'exports'/'flows',package_name='mounted_provenance_fixture')
    package=Path(built['package_path'])
    record_package(project,package,version_id=seed,recipe_task='ocr',parity={'status':'not_run'})
    history=EvaluationHistory(root/'reports'/'evaluations')
    records=[history.append({'job_id':path.parent.name,'task':'ocr','test_predictions':[]},
        {'checkpoint_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        for path in (checkpoint,second/'best_model.pt')]
    # This creates the original empty schema, not an approval or fake release.
    with routes_model_deployments._store(project):pass
    protected=set(before)|{metadata,second/'model_meta.json',second/'best_model.pt',
        root/'delivery'/'library.json',root/'model_deployments.sqlite3'}
    protected.update(Path(binding['version_dir']).rglob('*'))
    protected.update((root/'reports'/'evaluations').glob('evaluation_*.json'))
    protected.update(package.rglob('*'))
    basis={path:path.read_bytes() for path in protected if path.is_file()}
    assert observer.snapshot()['records']==[]
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid') as client:
            headers=await _login_current(client,app,project)
            return await client.get('/api/provenance/impact',headers=headers)
    response=asyncio.run(sequence())
    assert response.status_code==200,response.text
    body=response.json()
    assert body['project_id']==project['id'] and body['source_dataset_path']==str(source)
    assert {row['job_id'] for row in body['models']}=={job,second.name}
    assert all(row['checkpoint_state']=='current' and row['data_state']=='current' for row in body['models'])
    assert {row['evaluation_id'] for row in body['model_evaluations']}=={row['evaluation_id'] for row in records}
    assert len(body['packages'])==1 and body['packages'][0]['state']=='unverified'
    assert body['packages'][0]['scope_matches'] and body['packages'][0]['parity']=={'status':'not_run'}
    assert body['approvals']==body['flow_evaluations']==[] and body['quality_approved'] is False
    assert all(path.read_bytes()==raw for path,raw in basis.items())
    terminal=observer.snapshot()['records']
    assert len(terminal)==3 and all(row['released'] and row['completed'] and row['normal_return'] for row in terminal)
    admission.close()
    assert admission.drain(.03)=={'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    assert observer.snapshot()['all_settled'] and not observer.snapshot()['uncertain']
    _no_whole_credit(observer)
