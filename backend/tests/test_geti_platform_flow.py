"""Select platform variants through the real flow catalog and execute saved inspections."""
import copy
import json
from pathlib import Path
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from tests.test_geti_platform_dataset_transfer import transfer_workspace
from tests.test_geti_platform_model_runtime import model_fixture


@pytest.fixture
def platform_flow(transfer_workspace):
    from backend.api import routes_flowchart, routes_inspections
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    from backend.platforms.geti.bindings import PlatformStore
    from backend.contracts.context import ContextRegistry
    project,source,upstream=transfer_workspace
    project.update(schema_version=1,created_at='2026-11-17T00:00:00Z',updated_at='2026-11-17T00:00:00Z',models_dir=str(Path(project['project_dir'])/'models'),reports_dir=str(Path(project['project_dir'])/'reports'))
    Path(project['models_dir']).mkdir()
    (Path(project['project_dir'])/'project.json').write_text(json.dumps(project))
    app=FastAPI(); app.state.current_project=project
    app.state.context_registry=ContextRegistry(Path(project['project_dir'])/'context')
    context=app.state.context_registry.context(project,None)
    key=app.state.context_registry.project_key(context)
    app.state.geti_store=PlatformStore(Path(project['project_dir'])/'platform-ledger')
    receipt=prepare_dataset(project,{'actor':'Reviewer'},upstream,transfer_root=app.state.geti_store.root/'transfers'/key)
    model=model_fixture(upstream,receipt)
    app.state.geti_store.save_dataset(key,receipt)
    class Runtime:
        starts=0
        def status(self,key): return {'state':'running'}
        def client(self,key): self.starts+=1; return upstream
    app.state.geti_runtime=Runtime()
    @app.middleware('http')
    async def select(request,next):
        request.state.scoped_project=project; request.state.project_context=context
        return await next(request)
    app.include_router(routes_flowchart.router); app.include_router(routes_inspections.router)
    return project,source,upstream,receipt,model,app,TestClient(app)


def test_platform_variants_select_verify_save_execute_and_durable_run(platform_flow):
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    project,source,upstream,receipt,model,app,client=platform_flow
    response=client.get('/api/flowchart/models/catalog',params={'source_dataset_path':str(source)})
    assert response.status_code==200,response.text
    entries=[row for row in response.json()['models'] if row.get('platform')=='geti']
    assert {row['format'] for row in entries}=={'openvino','onnx'}
    selected=next(row for row in entries if row['format']=='openvino')
    assert selected['platform_model']['model_id']==model['id']
    assert selected['platform_model']['transfer_id']==receipt['id']
    verified=client.post('/api/flowchart/models/verify',json={'source_dataset_path':str(source),
        'models':[{'job_id':selected['job_id'],'task':'classification','platform_model':selected['platform_model']}]})
    assert verified.status_code==200,verified.text
    pipeline=get_single_segmentation_flowchart(selected['job_id']).model_dump()
    pipeline['nodes'][1]['data'].update(task='classification',params={})
    saved=client.post('/api/flowchart/pipeline',json=pipeline)
    assert saved.status_code==200,saved.text
    image=source/'incoming'/'unseen.png'; image.parent.mkdir(); Image.new('RGB',(32,24),(10,20,30)).save(image)
    ng=next(c['geti_label_id'] for c in receipt['class_mapping'] if c['name']=='NG')
    upstream.prediction={'predictions':[{'media':{'id':str(uuid.uuid4()),'frame_index':None},
        'prediction':[{'labels':[{'id':ng}],'shape':{'type':'full_image'},'confidences':[0.9]}]}]}
    executed=client.post('/api/flowchart/run',json={'image_path':str(image),'image_id':image.stem,
        'pipeline':pipeline,'execution_target':'local','device':'cpu','project_id':project['id']})
    assert executed.status_code==200,executed.text
    result=executed.json()
    assert result['final_verdict']=='NG' and result['crops'][0]['predicted_class']=='NG'
    artifacts=[a for step in result['execution_steps'] for a in step['artifacts'] if a.get('kind')=='geti_model_prediction']
    assert artifacts[0]['model_id']==model['id'] and len(artifacts[0]['export_sha256'])==64
    assert len(upstream.projects[receipt['geti_project_id']]['media'])==4
    # Existing durable inspection API records and revalidates the exported artifact identity.
    from backend.api.routes_dataset import list_dataset_images
    listed=list_dataset_images(folder_path=str(source),task='classification',limit=50000,offset=0)
    row=next(item for item in listed['items'] if item['file_path']==str(image))
    created=client.post('/api/inspections/runs',json={'source_folder':str(source),'task':'classification',
        'scope':'all','pipeline':pipeline,'images':[row],'execution_target':'local','device':'cpu','project_id':project['id']})
    assert created.status_code==200,created.text
    run_id=created.json()['run_id']
    completed=client.post(f'/api/inspections/runs/{run_id}/execute',json={'image_path':str(image)})
    assert completed.status_code==200,completed.text
    assert completed.json()['final_verdict']=='NG'


def test_flow_rechecks_current_authority_during_model_readback(platform_flow):
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    project,source,upstream,receipt,model,app,client=platform_flow
    from backend.platforms.geti.inspection import GetiFlowModelReference
    selected=GetiFlowModelReference(transfer_id=receipt['id'],model_id=model['id'],variant_id=model['variants'][2]['id']).token()
    pipeline=get_single_segmentation_flowchart(selected).model_dump()
    pipeline['nodes'][1]['data'].update(task='classification',params={})
    original=upstream.request
    reads=0; predictions=[]
    def revoked_during_readback(method,path,**kwargs):
        nonlocal reads
        if method=='POST' and path.endswith('/predict'): predictions.append(path)
        response=original(method,path,**kwargs)
        if method=='GET' and path.endswith('/models'):
            reads+=1
            if reads==3: app.state.context_registry.local_actor_id='revoked-local-authority'
        return response
    upstream.request=revoked_during_readback
    image=source/'train/NG/image-1.png'
    response=client.post('/api/flowchart/run',json={'image_path':str(image),'image_id':image.stem,
        'pipeline':pipeline,'execution_target':'local','device':'cpu','project_id':project['id']})
    assert response.status_code==403,response.text
    assert predictions==[]


def test_forged_platform_variant_cannot_enter_native_checkpoint_loader(platform_flow):
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    project,source,_,receipt,model,app,client=platform_flow
    forged='geti:'+receipt['id']+':'+model['id']+':'+str(uuid.uuid4())
    response=client.post('/api/flowchart/models/verify',json={'source_dataset_path':str(source),
        'models':[{'job_id':forged,'task':'classification'}]})
    assert response.status_code in (409,422)
    assert not any(path.endswith('/dataset/media:predict') for _,path,_ in app.state.geti_runtime.client('unused').calls)


def test_cropped_or_transformed_input_is_explicit_review(platform_flow):
    from backend.engine.flowchart_engine import get_fixed_roi_flowchart
    project,source,upstream,_,_,_,client=platform_flow
    response=client.get('/api/flowchart/models/catalog',params={'source_dataset_path':str(source)})
    selected=next(row for row in response.json()['models'] if row.get('platform')=='geti')
    pipeline=get_fixed_roi_flowchart('classification',selected['job_id']).model_dump()
    pipeline['nodes'][1]['data']['params']={'roi_bbox':[0,0,16,16]}
    image=source/'test/NG/image-3.png'
    result=client.post('/api/flowchart/run',json={'image_path':str(image),'pipeline':pipeline,
        'execution_target':'local','device':'cpu','project_id':project['id']})
    assert result.status_code==200,result.text
    assert result.json()['final_verdict']=='REVIEW'
    assert not any(path.startswith('/modu/projects/') and path.endswith('/predict') for _,path,_ in upstream.calls)


def test_explicit_instance_model_uses_native_segmentation_flow_with_original_polygon_evidence(platform_flow):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.engine.segmentation_evidence import decoded_array
    project,source,upstream,_,_,app,client=platform_flow
    project['task']='segmentation'; (Path(project['project_dir'])/'project.json').write_text(json.dumps(project))
    for image in source.rglob('*.png'):
        image.with_suffix('.json').write_text(json.dumps({'imagePath':image.name,'imageWidth':32,'imageHeight':24,
            'shapes':[{'label':'scratch','shape_type':'polygon','points':[[2,3],[12,3],[10,13]]}]}))
    receipt=prepare_dataset(project,{'actor':'Reviewer','target_task':'instance_segmentation'},upstream)
    key=app.state.context_registry.project_key(app.state.context_registry.context(project,None))
    app.state.geti_store.save_dataset(key,receipt); model=model_fixture(upstream,receipt)
    response=client.get('/api/flowchart/models/catalog',params={'source_dataset_path':str(source)})
    entries=[row for row in response.json()['models'] if row.get('platform')=='geti']
    assert entries and entries[0]['task']=='segmentation' and entries[0]['platform_task']=='instance_segmentation'
    pipeline=get_single_segmentation_flowchart(entries[0]['job_id']).model_dump()
    label=receipt['class_mapping'][0]['geti_label_id']
    points=[{'x':2.0,'y':3.0},{'x':12.0,'y':3.0},{'x':10.0,'y':13.0}]
    upstream.prediction={'predictions':[{'media':{'id':str(uuid.uuid4()),'frame_index':None},
        'prediction':[{'labels':[{'id':label}],'shape':{'type':'polygon','points':points},'confidences':[0.9]}]}]}
    image=source/'test/NG/image-3.png'
    response=client.post('/api/flowchart/run',json={'pipeline':pipeline,'image_path':str(image),
        'project_id':project['id'],'execution_target':'local','device':'cpu'})
    assert response.status_code==200,response.text
    result=response.json(); crop=result['crops'][0]
    assert result['final_verdict']=='NG' and crop['map_semantics']=='instance_polygon_union'
    evidence=next(row for row in crop['segmentation_classes'] if row['class_name']=='scratch')
    mask=decoded_array(evidence['mask'])
    assert mask.shape==(24,32) and mask[5,5]==1 and mask[0,0]==0
    artifact=next(a for step in result['execution_steps'] for a in step['artifacts'] if a.get('kind')=='geti_model_prediction')
    assert artifact['instance_predictions'][0]['shape']['points']==points


def test_cli_contract_exposes_typed_platform_model_and_variant_ids(platform_flow):
    from backend.automation.catalog import operation_catalog
    *_,app,client=platform_flow
    catalog=operation_catalog(app,operation='POST /api/flowchart/models/verify')
    schema=catalog['components']['schemas']['GetiFlowModelReference']
    assert schema['properties']['model_id']['format']=='uuid'
    assert schema['properties']['variant_id']['format']=='uuid'
    assert set(schema['required'])=={'transfer_id','model_id','variant_id'}


def test_new_project_without_source_keeps_native_catalog_empty_and_does_not_start_platform(platform_flow):
    project,source,_,_,_,app,client=platform_flow
    project['source_dataset_dir']=None
    starts=app.state.geti_runtime.starts
    response=client.get('/api/flowchart/models/catalog',params={'source_dataset_path':str(source)})
    assert response.status_code==200,response.text
    assert response.json()['models']==[] and app.state.geti_runtime.starts==starts


def test_new_prepare_preserves_saved_reference_to_prior_target_project(platform_flow):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    project,source,upstream,receipt,model,app,client=platform_flow
    entries=client.get('/api/flowchart/models/catalog',params={'source_dataset_path':str(source)}).json()['models']
    selected=next(row for row in entries if row.get('platform')=='geti')
    second=prepare_dataset(project,{'actor':'Reviewer'},upstream)
    key=app.state.context_registry.project_key(app.state.context_registry.context(project,None))
    app.state.geti_store.save_dataset(key,second)
    response=client.post('/api/flowchart/models/verify',json={'source_dataset_path':str(source),
        'models':[{'job_id':selected['job_id'],'task':'classification'}]})
    assert response.status_code==200,response.text
    assert receipt['geti_project_id']!=second['geti_project_id'] and len(upstream.projects)==2


def test_platform_model_compute_reports_actual_cpu_and_preserves_threshold_boundary(platform_flow,monkeypatch):
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.api import routes_flowchart
    from types import SimpleNamespace
    project,source,upstream,receipt,_,_,client=platform_flow
    selected=next(row for row in client.get('/api/flowchart/models/catalog',params={'source_dataset_path':str(source)}).json()['models'] if row.get('platform')=='geti')
    pipeline=get_single_segmentation_flowchart(selected['job_id']).model_dump()
    pipeline['nodes'][1]['data'].update(task='classification',params={})
    ng=next(c['geti_label_id'] for c in receipt['class_mapping'] if c['name']=='NG')
    upstream.prediction={'predictions':[{'media':{'id':str(uuid.uuid4())},'prediction':[
        {'labels':[{'id':ng}],'shape':{'type':'full_image'},'confidences':[0.5]}]}]}
    monkeypatch.setattr(routes_flowchart,'_ENGINE',SimpleNamespace(device='mps',execute=lambda **kwargs: (_ for _ in ()).throw(AssertionError('native default engine used'))))
    response=client.post('/api/flowchart/run',json={'pipeline':pipeline,'image_path':str(source/'test/NG/image-3.png'),
        'project_id':project['id'],'execution_target':'model_compute','device':'cpu'})
    assert response.status_code==200,response.text
    assert response.json()['execution_device']=='cpu' and response.json()['final_verdict']=='NG'
