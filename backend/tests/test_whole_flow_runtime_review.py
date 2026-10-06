"""Separate converted-flow review contracts; controlled fixtures are not quality approval."""
import hashlib
import json
from pathlib import Path

import pytest
from backend.tests.test_whole_flow_evaluation import workspace
from backend.tests.test_whole_flow_approval import setup_review, approve


@pytest.fixture
def converted_control(workspace, monkeypatch, tmp_path):
    module, project, evaluation = setup_review(workspace, monkeypatch)
    reviewed = approve(module, project, evaluation)
    from backend.engine import flow_package_runtime, release_eligibility, runtime_release_evidence
    from backend.engine.flowchart_engine import FlowchartPipeline
    package = tmp_path/'converted'; package.mkdir()
    graph = FlowchartPipeline.model_validate(evaluation['pipeline'])
    models = {row['job_id']:Path(row['checkpoint_path']) for row in evaluation['models']}
    monkeypatch.setattr(flow_package_runtime, 'verify_flow_package', lambda _: (graph, models))
    monkeypatch.setattr(release_eligibility, 'authorize_release_action', lambda *a,**k: ['model-control'])
    images = [{'relative_path':row['relative_path'], 'sha256':row['input_sha256'], 'split':'test'} for row in evaluation['records']]
    measured = {'images':images,'cohort_sha256':'d'*64,'image_count':len(images),'device':'openvino:CPU'}
    acceptance = {'controlled_fixture':True,'input_receipt':{'source_dataset_path':project['source_dataset_dir']}}
    raw = json.dumps(acceptance).encode(); (package/'runtime_acceptance.json').write_bytes(raw)
    evidence = {**measured, 'receipt_kind':'measured_precision_cohort',
        'receipt_sha256':hashlib.sha256(raw).hexdigest(),'manifest_sha256':'c'*64}
    monkeypatch.setattr(runtime_release_evidence, 'verify_release_evidence', lambda *a,**k: dict(evidence))
    monkeypatch.setattr(runtime_release_evidence, 'verify_measured_precision_evidence', lambda *a,**k: dict(measured))
    for index, row in enumerate(evaluation['records']):
        folder=package/'heldout'; folder.mkdir(exist_ok=True)
        (folder/f'heldout_{index:04d}.json').write_text(json.dumps({'image_sha256':row['input_sha256'],
            'reference':{'final_verdict':row['decision']},'candidate':{'final_verdict':row['decision']}}))
    return module,project,evaluation,reviewed,package,measured,evidence


def review(control, **changes):
    from backend.engine.whole_flow_runtime_review import approve_runtime_flow
    _,project,_,base,package,_,_=control
    options=dict(device='openvino:CPU',reviewer='Fixture reviewer',reason='Explicit converted flow fixture review',
        holdout_reviewed=True,expected_revision=None)
    options.update(changes)
    return approve_runtime_flow(project,package,base['revision_id'],**options)


def test_precision_acceptance_alone_cannot_qualify_whole_flow(converted_control):
    module,project,_,base,package,_,_=converted_control
    with pytest.raises(ValueError,match='separate full-flow review|runtime review'):
        module.qualify_package(project,package,base['revision_id'],device='openvino:CPU')


def test_separate_review_qualifies_and_reopens_without_rewriting_package(converted_control):
    module,project,_,base,package,_,_=converted_control
    before={p.relative_to(package).as_posix():p.read_bytes() for p in package.rglob('*') if p.is_file()}
    saved=review(converted_control)
    qualified=module.qualify_package(project,package,base['revision_id'],device='openvino:CPU')
    assert qualified['contract']=='whole_flow_runtime_review_v1'
    assert qualified['runtime_review_revision_id']==saved['revision_id']
    assert qualified['runtime_acceptance_sha256']==converted_control[-1]['receipt_sha256']
    assert 'parity_receipt_sha256' not in qualified
    assert qualified['runtime_cohort_qualified'] and qualified['device_accepted'] is False
    assert module.qualify_package(project,package,base['revision_id'],device='openvino:CPU')==qualified
    assert before=={p.relative_to(package).as_posix():p.read_bytes() for p in package.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage',['cohort','relative_path','reference','escape','overkill','review','not_reviewed','short_reason'])
def test_converted_quality_and_subject_refusals(converted_control, damage):
    _,_,evaluation,_,package,measured,_=converted_control
    changes={}
    if damage=='cohort':measured['images'][0]['sha256']='f'*64
    elif damage=='relative_path':measured['images'][0]['relative_path']='another.png'
    elif damage in ('reference','escape','overkill','review'):
        index=next(i for i,r in enumerate(evaluation['records']) if r['truth']==('NG' if damage=='escape' else 'OK'))
        path=package/'heldout'/f'heldout_{index:04d}.json';value=json.loads(path.read_text())
        if damage=='reference':value['reference']['final_verdict']='NG'
        else:value['candidate']['final_verdict']={'escape':'OK','overkill':'NG','review':'REVIEW'}[damage]
        path.write_text(json.dumps(value))
    elif damage=='not_reviewed':changes['holdout_reviewed']=False
    else:changes['reason']='short'
    with pytest.raises(ValueError):review(converted_control,**changes)


def test_runtime_review_cas_and_live_authority_are_rechecked(converted_control):
    module,project,_,base,package,_,_=converted_control
    class Accounts:
        role='reviewer'
        def project_role(self,*args):return self.role
    accounts=Accounts()
    first=review(converted_control,authority_user_id='reviewer-id',accounts=accounts)
    with pytest.raises(ValueError,match='selection changed'):review(converted_control)
    second=review(converted_control,expected_revision=first['revision_id'],authority_user_id='reviewer-id',accounts=accounts)
    assert second['revision_id']!=first['revision_id']
    accounts.role='trainer'
    with pytest.raises(ValueError,match='authority'):
        module.qualify_package(project,package,base['revision_id'],device='openvino:CPU',accounts=accounts)


def test_runtime_review_changes_after_truth_or_package_change(converted_control):
    module,project,_,base,package,_,evidence=converted_control
    review(converted_control)
    evidence['manifest_sha256']='b'*64
    with pytest.raises(ValueError,match='runtime review'):
        module.qualify_package(project,package,base['revision_id'],device='openvino:CPU')
    evidence['manifest_sha256']='c'*64
    from backend.engine import image_truth
    from backend.tests.test_whole_flow_evaluation import declare
    declare(image_truth,project,'normal','NG',classes=['crack'])
    with pytest.raises(ValueError,match='stale|changed'):
        module.qualify_package(project,package,base['revision_id'],device='openvino:CPU')


def test_runtime_http_requires_review_role_owned_package_and_trusted_actor(converted_control,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import routes_flow_evaluation as routes
    import shutil
    _,project,_,base,package,_,_=converted_control
    owned=Path(project['project_dir'])/'exports'/'converted';shutil.copytree(package,owned)
    class Accounts:
        role='trainer'
        def project_role(self,*args):return self.role
    app=FastAPI();app.state.accounts=Accounts();app.include_router(routes.router)
    @app.middleware('http')
    async def account(request,call_next):
        request.state.account_user={'id':'runtime-reviewer','username':'Trusted runtime reviewer'}
        return await call_next(request)
    monkeypatch.setattr(routes,'get_current_project',lambda _:project)
    client=TestClient(app);prefix='/api/flow-evaluations/approvals/'+base['revision_id']
    body=dict(package_path=str(owned),device='openvino:CPU',reviewer='Forged actor',
        reason='Explicit API converted fixture review',holdout_reviewed=True,expected_revision=None)
    assert client.post(prefix+'/runtime-review',json=body).status_code==403
    app.state.accounts.role='reviewer'
    assert client.post(prefix+'/runtime-preview',json={k:body[k] for k in ('package_path','device')}).json()['review_valid'] is False
    assert client.post(prefix+'/runtime-review',json={**body,'holdout_reviewed':1}).status_code==422
    assert client.post(prefix+'/runtime-review',json={**body,'package_path':str(package)}).status_code==409
    saved=client.post(prefix+'/runtime-review',json=body)
    assert saved.status_code==200,saved.text
    assert saved.json()['reviewer']=='Trusted runtime reviewer'
    assert saved.json()['authority_user_id']=='runtime-reviewer'
    assert client.post(prefix+'/runtime-review',json=body).status_code==409
    app.state.accounts.role='trainer'
    assert client.post(prefix+'/runtime-preview',json={k:body[k] for k in ('package_path','device')}).status_code==403


def test_actual_openvino_full_flow_review_and_package_reopen(tmp_path,monkeypatch):
    """Actual U-Net/IR execution with synthetic truth and policy; no manufacturing approval."""
    import importlib.util,sys
    if importlib.util.find_spec('openvino') is None:pytest.skip('Explicit OpenVINO validation interpreter is required')
    if sys.platform=='darwin' and sys.modules.get('pyarrow') is not None:
        pytest.fail('Use the isolated OpenVINO validation process with pyarrow disabled before collection')
    pytest.importorskip('openvino')
    import torch
    from PIL import Image
    from backend.engine import flow_evaluation,image_truth,whole_flow_approval,release_eligibility
    from backend.engine.segmentation import build_segmentation_model
    from backend.engine.flowchart_engine import FlowchartEngine,get_single_segmentation_flowchart
    from backend.engine.flow_package import build_flow_package
    from backend.engine.openvino_runtime import optimize_flow_package
    from backend.api.routes_export import _optimization_input_receipt
    from backend.engine.runtime_precision_approval import approve_precision_package
    from backend.engine.whole_flow_runtime_review import approve_runtime_flow
    from backend.engine.flow_package_runtime import verify_flow_package,Predictor
    torch.set_num_threads(1)
    source=tmp_path/'source';source.mkdir();project_dir=tmp_path/'project'
    project=dict(id='actual-converted-control',project_dir=str(project_dir),source_dataset_dir=str(source),
        dataset_dir=str(project_dir/'dataset'),annotations_dir=str(project_dir/'annotations'),
        models_dir=str(project_dir/'models'),task='segmentation',active_labelset_id='default')
    model=build_segmentation_model('unet',num_classes=2,pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters():parameter.zero_()
        model.head.bias[1]=8
    checkpoint=project_dir/'models/job_ir/best_model.pt';checkpoint.parent.mkdir(parents=True)
    torch.save(dict(task='segmentation',model_name='unet',classes=['background','defect'],image_size=[32,32],model_state_dict=model.state_dict()),checkpoint)
    (checkpoint.parent/'model_meta.json').write_text(json.dumps({'task':'segmentation','classes':['background','defect']}))
    graph=get_single_segmentation_flowchart('job_ir');graph.nodes[1].data.crop_padding=0
    graph.nodes[1].data.params={'min_defect_area_px':1}
    version='9'*32;folder=project_dir/'flowcharts/versions';folder.mkdir(parents=True)
    (folder/(version+'.json')).write_text(json.dumps({'version_id':version,'recipe_task':'segmentation','source_dataset_path':str(source),'pipeline':graph.model_dump()}))
    files=[]
    for name,color,truth in [('normal',(20,30,40),'OK'),('defect',(170,40,30),'NG')]:
        file=source/(name+'.png');Image.new('RGB',(32,32),color).save(file);files.append(file)
        current=image_truth.read_truth(project,str(file),task='segmentation',classes=['background','defect'])
        image_truth.declare_truth(project,str(file),task='segmentation',classes=['background','defect'],verdict=truth,
            defect_classes=['defect'] if truth=='NG' else [],reviewer='Synthetic control author',
            expected_revision=current['truth_revision'],expected_image_revision=current['image_revision'])
    split=project_dir/'dataset/splits'/f'{hashlib.sha256(str(source).encode()).hexdigest()}.json';split.parent.mkdir(parents=True)
    split.write_text(json.dumps({'folder_path':str(source),'assignments':{p.name:'test' for p in files}}))
    provider=lambda *args:{('job_ir','segmentation'):{'path':str(checkpoint),'metadata':{'classes':['background','defect']}}}
    cohort=flow_evaluation.freeze_cohort(project,version,model_provider=provider)
    evaluated=flow_evaluation.evaluate_flow(project,version,cohort['cohort_id'],model_provider=provider,
        engine=FlowchartEngine(device='cpu',checkpoint_resolver=lambda *_:checkpoint))
    assert evaluated['status']=='completed' and evaluated['metrics']['overkill_rate']==1
    monkeypatch.setattr(whole_flow_approval,'verify_project_context',lambda _:None)
    # Deliberately permissive synthetic policy exercises integration, not acceptable process quality.
    policy=dict(policy_id='synthetic-control-only',revision=1,minimum_normal=1,minimum_defect=1,
        maximum_escape_rate=0,maximum_overkill_rate=1,maximum_review_rate=0)
    base=whole_flow_approval.approve_flow(project,evaluation_id=evaluated['evaluation_id'],policy=policy,
        reviewer='Synthetic control author',reason='Actual execution integration control; not manufacturing approval',
        holdout_reviewed=True,expected_revision=None)
    exports=project_dir/'exports'
    original=Path(build_flow_package(pipeline=graph,checkpoints={'job_ir':checkpoint},output_base_dir=exports,package_name='original')['package_path'])
    inputs=_optimization_input_receipt(project,source,[],[str(p) for p in files])
    converted=optimize_flow_package(original,output_dir=exports/'converted',validation_images=files,input_receipt=inputs)
    revision={'revision_id':'7'*32,'job_id':'job_ir','task':'segmentation','checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
    released=Path(approve_precision_package(converted['package_path'],exports/'approved',revisions={'job_ir':revision},
        reviewer='Synthetic control author',reason='Actual measured IR integration control',maximum_absolute_drift=.001,holdout_reviewed=True)['package_path'])
    # Controlled model approval authority only; package verification, input/truth binding,
    # PyTorch/IR inference, metrics, persistence and revalidation stay actual.
    monkeypatch.setattr(release_eligibility,'authorize_release_action',lambda *a,**k:[revision])
    before={p.relative_to(released).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in released.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='runtime review'):
        whole_flow_approval.qualify_package(project,released,base['revision_id'],device='openvino:CPU')
    review=approve_runtime_flow(project,released,base['revision_id'],device='openvino:CPU',reviewer='Synthetic control author',
        reason='Actual converted decisions and explicit synthetic truth reviewed for integration control',holdout_reviewed=True,expected_revision=None)
    assert review['subject']['metrics']['overkill_rate']==1
    qualified=whole_flow_approval.qualify_package(project,released,base['revision_id'],device='openvino:CPU')
    assert qualified['runtime_review_revision_id']==review['revision_id'] and qualified['device_accepted'] is False
    from backend.engine.inspection_service import _verify_release_policy
    seal=tmp_path/'sealed-policy.json';seal.write_text(json.dumps({'schema_version':1,
        'manifest_sha256':hashlib.sha256((released/'manifest.json').read_bytes()).hexdigest(),
        'approval_revisions':[revision],'device':'openvino:CPU',
        'runtime_acceptance_sha256':qualified['runtime_acceptance_sha256'],'whole_flow_review':qualified}))
    _verify_release_policy(released,verify_flow_package(released)[1],seal,device='openvino:CPU')
    actual=Predictor(released,device='openvino:CPU',deadline_ms=30000).predict(files[0])
    assert actual['model_runtime']['backend']=='openvino' and actual['final_verdict']=='NG'
    assert before=={p.relative_to(released).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in released.rglob('*') if p.is_file()}
    receipt=dict(actual_pytorch_and_openvino_execution=True,synthetic_truth=True,process_quality_approved=False,
        model_authority_controlled=True,package_bytes_unchanged=True,device_accepted=False,
        checkpoint_sha256=revision['checkpoint_sha256'],runtime_review_sha256=review['record_sha256'],qualified=qualified)
    (tmp_path/'actual-converted-flow-receipt.json').write_text(json.dumps(receipt,indent=2))
