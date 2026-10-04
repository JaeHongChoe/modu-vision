import numpy as np
import pytest
from backend.engine.flowchart_engine import FlowchartEngine, CropInspectionResult, get_fixed_roi_flowchart
from backend.tests.test_service_e02 import fixture_image, moved, warp, corners


def pipeline(reference_ref='fixture-ref:'+'a'*64, revision=1):
    pipe = get_fixed_roi_flowchart(inspection_task='classification', job_id='job_fixture_qa')
    roi = next(node for node in pipe.nodes if node.data.node_type == 'fixed_roi')
    roi.data.params = {'roi_bbox': [60, 50, 180, 140], 'fixture': {
        'reference_ref': reference_ref, 'reference_revision': revision, 'scope': 'rigid', 'min_support': .8}}
    return pipe


def inspect_spy(engine, monkeypatch):
    calls = []
    def inspect(image, rois, node):
        calls.extend(rois)
        crops = [CropInspectionResult(roi_id=roi['id'], label='QA', bbox=roi['bbox'], defect_score=0,
                    verdict='OK', crop_thumbnail='', flaw_type='synthetic spy', source_transform=roi.get('source_transform'),
                    polygon=roi.get('polygon')) for roi in rois]
        return crops, 0, 'passed'
    monkeypatch.setattr(engine, '_inspect_crops', inspect)
    return calls


def test_missing_fixture_reference_routes_review_without_downstream_inspection(monkeypatch):
    engine = FlowchartEngine(device='cpu')
    calls = inspect_spy(engine, monkeypatch)
    result = engine.execute(pipeline=pipeline(), image=np.full((240, 320, 3), 70, np.uint8))
    assert result['final_verdict'] == 'REVIEW'
    assert calls == [], 'a missing reference must never be treated as an ordinary fixed pixel crop'
    step = next(row for row in result['execution_steps'] if row['node_id'] == 'node_fixed_roi')
    assert step['status'] == 'review_required' and step['artifacts'][0]['fixture_pose']['status'] == 'review'
    artifact=step['artifacts'][0]
    assert artifact['roi_id'] and len(artifact['bbox'])==4 and artifact['image'].startswith('data:image/')
    assert artifact['evidence']['fixture_pose']==artifact['fixture_pose']


@pytest.fixture
def reference_store(tmp_path):
    import hashlib
    from backend.engine.fixture_flow import FixtureReferenceStore
    from backend.engine.fixture_pose import FixtureReference
    store = FixtureReferenceStore(tmp_path / 'project' / 'fixture_references')
    pixels = fixture_image()
    artifact = store.save(FixtureReference(pixels, (0, 0, 320, 240)), source_sha256=hashlib.sha256(pixels.tobytes()).hexdigest(), name='Seeded fixture')
    return store, artifact


@pytest.mark.parametrize('angle,shift,scale,scope', [(0,(90,70),1,'rigid'), (7,(110,60),1,'rigid'), (5,(70,60),1.12,'similarity')])
def test_production_fixture_roi_moves_and_retains_source_transform(reference_store, monkeypatch, angle, shift, scale, scope):
    from backend.engine.fixture_flow import fixture_scope
    store, artifact = reference_store
    pipe = pipeline(artifact.ref)
    pipe.nodes[1].data.params['fixture']['scope'] = scope
    truth = moved(angle, np.array(shift, float), scale=scale)
    observed = warp(artifact.reference.grey, truth)
    engine = FlowchartEngine(device='cpu'); calls = inspect_spy(engine, monkeypatch)
    with fixture_scope(store.load): result = engine.execute(pipeline=pipe, image=observed)
    assert result['final_verdict'] == 'OK', result['rejection_reason']
    roi = calls[0]; transform = np.array(roi['source_transform'])
    local = np.array([[0,0,1],[120,0,1],[120,90,1],[0,90,1]])
    expected = corners((60,50,180,140)) @ truth[:,:2].T + truth[:,2]
    assert np.abs((transform @ local.T)[:2].T - expected).max() < 1.5
    assert roi['image'].shape[:2] == (90,120)
    step = next(row for row in result['execution_steps'] if row['node_id'] == 'node_fixed_roi')
    assert step['artifacts'][0]['fixture_pose']['reference_artifact_ref'] == artifact.ref


@pytest.mark.parametrize('failure', ['blank','two_copies','rigid_scale','support'])
def test_fixture_failure_never_calls_downstream_model(reference_store, monkeypatch, failure):
    from backend.engine.fixture_flow import fixture_scope
    store, artifact = reference_store; pipe = pipeline(artifact.ref)
    observed = warp(artifact.reference.grey, moved(0,np.array((90,70),float)))
    if failure == 'blank': observed[:] = 70
    if failure == 'two_copies':
        observed = np.full((300,760),40,np.uint8)
        observed[30:270,30:350]=artifact.reference.grey;observed[30:270,410:730]=artifact.reference.grey
    if failure == 'rigid_scale': observed = warp(artifact.reference.grey,moved(5,np.array((70,60),float),scale=1.12))
    if failure == 'support': pipe.nodes[1].data.params['roi_bbox']=[0,0,320,240];pipe.nodes[1].data.params['fixture']['min_support']=1
    engine = FlowchartEngine(device='cpu');calls = inspect_spy(engine,monkeypatch)
    with fixture_scope(store.load):result = engine.execute(pipeline=pipe,image=observed)
    assert result['final_verdict']=='REVIEW', result
    assert calls==[]


def test_reference_revision_and_file_mutation_invalidate_graph(reference_store, monkeypatch):
    from backend.engine.fixture_flow import fixture_scope
    from backend.engine.fixture_pose import FixtureReference
    store, artifact = reference_store
    newer = store.save(FixtureReference(artifact.reference.grey, (0,0,320,240),revision=2), source_sha256=artifact.body['source_sha256'],
        name='Seeded fixture revision2',fixture_id=artifact.body['fixture_id'],expected_ref=artifact.ref)
    assert store.load(artifact.ref) is None and store.load(newer.ref)
    engine = FlowchartEngine(device='cpu');calls = inspect_spy(engine,monkeypatch)
    with fixture_scope(store.load):result=engine.execute(pipeline=pipeline(artifact.ref),image=artifact.reference.grey)
    assert result['final_verdict']=='REVIEW' and calls==[]
    image=store.root/f'{newer.ref.split(":")[-1]}.png';image.write_bytes(b'changed')
    assert store.load(newer.ref) is None


def test_project_fixture_creation_api_owns_reference_and_rejects_stale_revision(tmp_path, monkeypatch):
    import hashlib
    from PIL import Image
    from types import SimpleNamespace
    from backend.api import routes_geometry, routes_project
    project={'project_dir':str(tmp_path/'project')}
    monkeypatch.setattr(routes_project,'get_current_project',lambda request:project)
    source=tmp_path/'source.png';Image.fromarray(fixture_image()).save(source)
    request=SimpleNamespace(state=SimpleNamespace(account_user=None))
    request_model=routes_geometry.FixtureReferenceRequest(image_path=str(source),name='Fixture',valid_region=[0,0,320,240])
    receipt=routes_geometry.create_fixture_reference(request_model,request)
    assert receipt['source_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert routes_geometry.list_fixture_references(request)['fixtures'][0]['ref']==receipt['ref']
    assert 'image_path' not in receipt


def test_fixture_package_copies_exact_reference_and_runtime_geometry(reference_store,tmp_path):
    import torch
    from PIL import Image
    from backend.engine.classification.model import create_classification_model
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flow_package_runtime import verify_flow_package, run_flow_package
    from backend.engine.fixture_flow import package_fixtures, fixture_scope
    store,artifact=reference_store
    checkpoint=tmp_path/'models'/'job_fixture_qa'/'best_model.pt';checkpoint.parent.mkdir(parents=True)
    torch.manual_seed(13)
    model=create_classification_model(backbone='resnet18',num_classes=2,pretrained=False)
    torch.save({'task':'classification','backbone':'resnet18','classes':['OK','NG'],'image_size':[64,64],'model_state_dict':model.state_dict()},checkpoint)
    pipe=pipeline(artifact.ref)
    with fixture_scope(store.load):
        built=build_flow_package(pipeline=pipe,checkpoints={'job_fixture_qa':checkpoint},output_base_dir=tmp_path/'out',package_name='fixture')
    package=__import__('pathlib').Path(built['package_path'])
    manifest=verify_flow_package(package)
    copied=package_fixtures(package).load(artifact.ref)
    assert copied.to_json()==artifact.to_json() and copied.png==artifact.png
    observed=warp(artifact.reference.grey,moved(7,np.array((110,60),float)))
    image=tmp_path/'observed.png';Image.fromarray(observed).save(image)
    result=run_flow_package(package,image)
    assert result['final_verdict'] in ('OK','NG'),result
    fixed=next(row for row in result['execution_steps'] if row['node_id']=='node_fixed_roi')
    assert fixed['artifacts'][0]['fixture_pose']['reference_artifact_ref']==artifact.ref
    assert result['crops'][0]['source_transform'] is not None
    assert result['crops'][0]['fixture_pose']['reference_artifact_ref']==artifact.ref
    from backend.engine.fixture_flow import fixture_scope
    with fixture_scope(store.load):
        project_result=FlowchartEngine(device='cpu',checkpoint_resolver=lambda job,task:checkpoint).execute(pipeline=pipe,image_path=image)
    assert result['crops'][0]['bbox']==project_result['crops'][0]['bbox']
    assert result['crops'][0]['fixture_pose']==project_result['crops'][0]['fixture_pose']
    assert result['crops'][0]['defect_score']==project_result['crops'][0]['defect_score']
    (package/'fixture_references'/f'{artifact.ref.split(":")[-1]}.png').write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'):verify_flow_package(package)


def test_anchored_segmentation_runs_real_remap_and_source_pixel_mask(reference_store, monkeypatch):
    import torch, cv2, base64
    from backend.engine.fixture_flow import fixture_scope
    store,artifact=reference_store;pipe=pipeline(artifact.ref)
    inspection=next(node for node in pipe.nodes if node.data.node_type=='inspection')
    inspection.data.task='segmentation';inspection.data.params={'class_names':['background','defect'],'min_defect_area_px':8}
    engine=FlowchartEngine(device='cpu')
    calls=[]
    class Segmentation(torch.nn.Module):
        def forward(self,image):
            calls.append(tuple(image.shape));logits=torch.zeros((1,2,*image.shape[2:]),device=image.device)
            logits[:,0]=5;logits[:,1,:,image.shape[3]//2:]=10
            return logits
    monkeypatch.setattr(engine,'_get_inspection_model',lambda **kwargs:(Segmentation(),True))
    key=engine._cache_key('segmentation','job_fixture_qa','fast',engine._resolve_checkpoint('job_fixture_qa','segmentation'))
    engine._model_input_sizes[key]=(120,90);engine._model_classes[key]=['background','defect']
    truth=moved(7,np.array((110,60),float));observed=warp(artifact.reference.grey,truth)
    with fixture_scope(store.load):result=engine.execute(pipeline=pipe,image=observed)
    crop=result['crops'][0];assert calls and crop['fixture_pose']['reference_artifact_ref']==artifact.ref
    mask=cv2.imdecode(np.frombuffer(base64.b64decode(crop['mask'].split(',')[1]),np.uint8),cv2.IMREAD_GRAYSCALE)
    assert mask.shape==(crop['bbox'][3]-crop['bbox'][1],crop['bbox'][2]-crop['bbox'][0])
    assert crop['defect_area_px']==int((mask>0).sum())
    ys,xs=np.nonzero(mask);center=np.array([xs.mean()+crop['bbox'][0],ys.mean()+crop['bbox'][1]])
    expected=np.array([150,95]) @ truth[:,:2].T+truth[:,2]
    assert np.linalg.norm(center-expected)<4, (center,expected)


def test_cross_project_reference_and_symlink_fail_closed(reference_store,tmp_path,monkeypatch):
    from backend.engine.fixture_flow import FixtureReferenceStore,fixture_scope
    store,artifact=reference_store
    other=FixtureReferenceStore(tmp_path/'other'/'fixture_references')
    engine=FlowchartEngine(device='cpu');calls=inspect_spy(engine,monkeypatch)
    with fixture_scope(other.load):result=engine.execute(pipeline=pipeline(artifact.ref),image=artifact.reference.grey)
    assert result['final_verdict']=='REVIEW' and not calls
    png=store.root/f'{artifact.ref.split(":")[-1]}.png';saved=tmp_path/'outside.png';png.rename(saved);png.symlink_to(saved)
    with fixture_scope(store.load):result=engine.execute(pipeline=pipeline(artifact.ref),image=artifact.reference.grey)
    assert result['final_verdict']=='REVIEW' and not calls


def test_stale_reference_export_refused_before_publication(reference_store,tmp_path):
    from backend.engine.flow_package import build_flow_package
    from backend.engine.fixture_pose import FixtureReference
    store,artifact=reference_store
    store.save(FixtureReference(artifact.reference.grey,(0,0,320,240),revision=2),source_sha256=artifact.body['source_sha256'],name='Updated',fixture_id=artifact.body['fixture_id'],expected_ref=artifact.ref)
    with pytest.raises(ValueError,match='stale fixture'):
        build_flow_package(pipeline=pipeline(artifact.ref),checkpoints={},output_base_dir=tmp_path/'output',package_name='stale',fixtures=store.load)
    assert not (tmp_path/'output').exists()


def test_fixture_graph_draft_reopen_preserves_exact_hash_and_execution(reference_store,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from backend.api import routes_flowchart as routes
    from backend.engine.flowchart_engine import FlowchartPipeline
    store,artifact=reference_store;source=tmp_path/'source';source.mkdir()
    project={'id':'fixture-project','project_dir':str(store.root.parent),'source_dataset_dir':str(source),'active_labelset_id':'default'}
    monkeypatch.setattr(routes,'get_current_project',lambda request:project)
    context={'project_id':project['id'],'source_dataset_path':str(source.resolve()),'labelset_id':'default'}
    pipe=pipeline(artifact.ref);request=SimpleNamespace()
    saved=routes.save_flow_draft(routes.FlowDraftSaveRequest(pipeline=pipe,context=context),request)
    reopened=routes.get_flow_draft(request)
    restored=FlowchartPipeline.model_validate(reopened['pipeline'])
    assert saved['draft_sha256']==routes.pipeline_sha256(restored)==routes.pipeline_sha256(pipe)
    assert restored.nodes[1].data.params==pipe.nodes[1].data.params
    engine=FlowchartEngine(device='cpu');calls=inspect_spy(engine,monkeypatch)
    with routes._project_calibrations(project):result=engine.execute(pipeline=restored,image=warp(artifact.reference.grey,moved(0,np.array((90,70),float))))
    assert result['final_verdict']=='OK' and calls[0]['fixture_pose']['reference_artifact_ref']==artifact.ref


def test_shared_fixture_source_cannot_escape_active_source(tmp_path,monkeypatch):
    from PIL import Image
    from types import SimpleNamespace
    from fastapi import HTTPException
    from backend.api import routes_geometry,routes_project
    source=tmp_path/'source';source.mkdir();outside=tmp_path/'outside.png';Image.fromarray(fixture_image()).save(outside)
    project={'project_dir':str(tmp_path/'project'),'source_dataset_dir':str(source)}
    monkeypatch.setattr(routes_project,'get_current_project',lambda request:project)
    monkeypatch.setattr(routes_geometry,'request_shared_scope',lambda:True)
    req=routes_geometry.FixtureReferenceRequest(image_path=str(outside),name='Unsafe',valid_region=[0,0,320,240])
    with pytest.raises(HTTPException) as error:routes_geometry.create_fixture_reference(req,SimpleNamespace())
    assert error.value.status_code==422 and not (tmp_path/'project').exists()


def test_changed_observation_size_with_reference_search_bounds_routes_review(reference_store,monkeypatch):
    from backend.engine.fixture_flow import fixture_scope
    store,artifact=reference_store;pipe=pipeline(artifact.ref)
    pipe.nodes[1].data.params['fixture']['search_region']=[0,0,320,240]
    engine=FlowchartEngine(device='cpu');calls=inspect_spy(engine,monkeypatch)
    with fixture_scope(store.load):result=engine.execute(pipeline=pipe,image=np.full((120,160,3),70,np.uint8))
    assert result['final_verdict']=='REVIEW' and not calls
    step=next(row for row in result['execution_steps'] if row['node_id']=='node_fixed_roi')
    assert step['artifacts'][0]['fixture_pose']['status']=='review'
    assert 'search_region' in step['artifacts'][0]['fixture_pose']['reason']
