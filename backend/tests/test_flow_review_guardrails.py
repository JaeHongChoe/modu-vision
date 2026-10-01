"""Scoped debug inputs and known target classes must be verified before execution/import."""
from types import SimpleNamespace
from pathlib import Path
import pytest
from PIL import Image
from fastapi import HTTPException
from backend.api import routes_flowchart as flows
from backend.engine.flowchart_engine import FlowchartRunRequest, get_fixed_roi_flowchart
from backend.engine.flow_workspace import save_template, map_template


def _debug(path, project_id='project'):
    graph=get_fixed_roi_flowchart(inspection_task='segmentation')
    return FlowchartRunRequest(project_id=project_id,pipeline=graph,image_path=str(path),execution_target='local',device='cpu',
        stop_node_id=next(n.id for n in graph.nodes if n.data.node_type=='input'))


@pytest.mark.parametrize('linked',[False,True])
def test_shared_handler_rejects_private_image_before_model_free_debug(tmp_path,monkeypatch,linked):
    from backend.engine.annotation_storage import set_request_shared_scope,reset_request_shared_scope
    own=tmp_path/'project';own.mkdir();source=tmp_path/'registered-source';source.mkdir()
    private=tmp_path/'other-project'/'private.png';private.parent.mkdir();Image.new('RGB',(32,32),'red').save(private)
    supplied=own/'linked.png' if linked else private
    if linked:supplied.symlink_to(private)
    project={'id':'project','project_dir':str(own),'dataset_dir':str(own/'dataset'),'source_dataset_dir':str(source),'models_dir':str(own/'models')}
    monkeypatch.setattr(flows,'get_current_project',lambda _:project)
    def forbidden_engine(*args):raise AssertionError('Outside image reached execution')
    monkeypatch.setattr(flows,'_local_execution_engine',forbidden_engine)
    shared=set_request_shared_scope(True)
    try:
        with pytest.raises(HTTPException) as error:
            flows.run_flowchart(_debug(supplied),request=SimpleNamespace(state=SimpleNamespace(account_user={'id':'user'})))
        assert error.value.status_code==422
        assert 'project' in str(error.value.detail).lower()
    finally:reset_request_shared_scope(shared)


def test_desktop_http_debug_rejects_foreign_image_and_accepts_project_or_registered_source(tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    app=create_app(project_dir=str(tmp_path/'projects'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':'Scoped debug'}).json();identifier=project['id']
    source=tmp_path/'source';source.mkdir();outside=tmp_path/'foreign.png';Image.new('RGB',(32,32),'red').save(outside)
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    rejected=client.post('/api/flowchart/run',json=_debug(outside,identifier).model_dump())
    assert rejected.status_code==422,rejected.text
    for root in [Path(project['project_dir']),Path(project['dataset_dir']),source]:
        root.mkdir(parents=True,exist_ok=True);image=root/'owned.png';Image.new('RGB',(32,32),'blue').save(image)
        response=client.post('/api/flowchart/run',json=_debug(image,identifier).model_dump())
        assert response.status_code==200,response.text
        assert response.json()['status']=='partial'
        assert response.json()['annotated_image']


def test_standalone_debug_keeps_explicit_arbitrary_image_support(tmp_path):
    image=tmp_path/'standalone.png';Image.new('RGB',(32,32)).save(image)
    req=_debug(image);req.project_id=None
    assert flows.run_flowchart(req,request=None)['status']=='partial'


def _template(tmp_path):
    graph=get_fixed_roi_flowchart(inspection_task='segmentation',job_id='job_old')
    node=next(n for n in graph.nodes if n.data.node_type=='inspection')
    node.data.params.update(class_names=['background','old'],class_ids=[1])
    return save_template(tmp_path,graph,name='Class mapping',project_id='old'),node.id


@pytest.mark.parametrize('mapped_name,mapped_id',[('invented',1),('scratch',99)])
def test_template_rejects_unknown_target_class_name_or_id(tmp_path,mapped_name,mapped_id):
    record,node_id=_template(tmp_path)
    mapping={f'{node_id}:name:background':'background',f'{node_id}:name:old':mapped_name,f'{node_id}:id:1':mapped_id}
    catalog=[{'job_id':'job_new','task':'segmentation','class_names':['background','scratch'],'class_ids':[0,1]}]
    with pytest.raises(ValueError,match='class'):
        map_template(record,{node_id:'job_new'},mapping,catalog)


def test_template_accepts_known_target_classes_and_keeps_metadata_free_legacy_mapping(tmp_path):
    record,node_id=_template(tmp_path)
    mapping={f'{node_id}:name:background':'background',f'{node_id}:name:old':'scratch',f'{node_id}:id:1':1}
    for catalog in [[{'job_id':'job_new','task':'segmentation','classes':['background','scratch']}],[{'job_id':'job_new','task':'segmentation'}]]:
        mapped=map_template(record,{node_id:'job_new'},mapping,catalog)
        node=next(n for n in mapped.nodes if n.id==node_id)
        assert node.data.params['class_names']==['background','scratch'] and node.data.params['class_ids']==[1]


def test_catalog_exposes_known_names_and_runtime_class_ids_without_inventing_legacy_classes():
    assert flows._catalog_model_settings({'task':'segmentation','classes':['background','scratch']})['class_names']==['background','scratch']
    assert flows._catalog_model_settings({'task':'segmentation','classes':['background','scratch']})['class_ids']==[0,1]
    assert flows._catalog_model_settings({'task':'detection','classes':['background','scratch']})['class_names']==['scratch']
    assert flows._catalog_model_settings({'task':'detection','classes':['background','scratch']})['class_ids']==[1]
    assert 'class_names' not in flows._catalog_model_settings({'task':'classification'})


def test_downstream_blob_rule_mapping_uses_recorded_upstream_model_class_ids(tmp_path):
    from backend.engine.flowchart_engine import FlowNode,FlowNodeData,FlowEdge
    graph=get_fixed_roi_flowchart(inspection_task='segmentation',job_id='job_old')
    model=next(n for n in graph.nodes if n.data.node_type=='inspection')
    decision=next(n for n in graph.nodes if n.data.node_type=='decision')
    blob=FlowNode(id='measure',position={'x':0,'y':0},data=FlowNodeData(label='Measure',node_type='blob_measure',params={'class_ids':[1]}))
    graph.nodes.insert(3,blob)
    next(e for e in graph.edges if e.target==decision.id).target=blob.id
    graph.edges.append(FlowEdge(id='measure-decision',source=blob.id,target=decision.id,payload_type='result'))
    record=save_template(tmp_path,graph,name='Measured target class',project_id='old')
    catalog=[{'job_id':'job_new','task':'segmentation','metadata':{'classes':['background','scratch']}}]
    with pytest.raises(ValueError,match='Target class'):
        map_template(record,{model.id:'job_new'},{'measure:id:1':99},catalog)
    mapped=map_template(record,{model.id:'job_new'},{'measure:id:1':1},catalog)
    assert next(n for n in mapped.nodes if n.id=='measure').data.params['class_ids']==[1]


def test_explicit_class_id_metadata_is_enforced_even_when_names_are_not_recorded(tmp_path):
    record,node_id=_template(tmp_path)
    mapping={f'{node_id}:name:background':'background',f'{node_id}:name:old':'scratch',f'{node_id}:id:1':99}
    with pytest.raises(ValueError,match='Target class'):
        map_template(record,{node_id:'job_new'},mapping,[{'job_id':'job_new','task':'segmentation','class_ids':[0,1]}])
    assert flows._catalog_model_settings({'task':'segmentation','class_ids':[0,1]})['class_ids']==[0,1]


@pytest.mark.parametrize('target_names',[
    ['background','dent','scratch'],
    ['background','dent','scratch','new_target_class'],
])
def test_template_uses_target_checkpoint_class_order_for_real_inspection(tmp_path,monkeypatch,target_names):
    import numpy as np
    import torch
    from backend.engine.flowchart_engine import FlowchartEngine
    graph=get_fixed_roi_flowchart(inspection_task='segmentation',job_id='old')
    node=next(n for n in graph.nodes if n.data.node_type=='inspection')
    next(n for n in graph.nodes if n.data.node_type=='fixed_roi').data.params['roi_bbox']=[0,0,32,32]
    node.data.params={'class_names':['background','scratch','dent'],'class_ids':[1]}
    record=save_template(tmp_path,graph,name='Class order mapping',project_id='old')
    names={f'{node.id}:name:{name}':name for name in ['background','scratch','dent']}
    mapped=map_template(record,{node.id:'new'},{**names,f'{node.id}:id:1':2},[
        {'job_id':'new','task':'segmentation','class_names':target_names,'class_ids':list(range(len(target_names)))}
    ])
    engine=FlowchartEngine(device='cpu')
    class FixtureModel(torch.nn.Module):
        def forward(self,images):
            logits=torch.zeros(images.shape[0],len(target_names),*images.shape[-2:])
            logits[:,2]=8
            return logits
    monkeypatch.setattr(engine,'_get_inspection_model',lambda **kwargs:(FixtureModel(),True))
    monkeypatch.setattr(engine,'_resolve_checkpoint',lambda *args:None)
    key=engine._cache_key('segmentation','new','fast',None)
    engine._model_classes[key]=target_names
    engine._model_input_sizes[key]=(32,32)
    result=engine.execute(pipeline=mapped,image=np.zeros((32,32,3),dtype=np.uint8),stop_node_id=node.id)
    assert result['status']=='partial'
    assert result['crops'],result['execution_steps']
    selected=[row for row in result['crops'][0]['segmentation_classes'] if row['selected']]
    assert [row['class_name'] for row in selected]==['scratch']
    assert [row['class_id'] for row in selected]==[2]
