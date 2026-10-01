"""Debug execution must not run downstream models or issue a production verdict."""
import numpy as np
import pytest
from backend.engine.flowchart_engine import FlowchartEngine, get_fixed_roi_flowchart


def test_stop_at_roi_runs_real_crop_without_loading_downstream_model():
    graph = get_fixed_roi_flowchart(inspection_task='segmentation')
    roi = next(n for n in graph.nodes if n.data.node_type == 'fixed_roi')
    roi.data.params['roi_bbox'] = [8, 12, 40, 44]
    model = next(n for n in graph.nodes if n.data.node_type == 'inspection')
    model.data.model_job_id = None
    result = FlowchartEngine(device='cpu').execute(pipeline=graph, image=np.zeros((64, 64, 3), dtype=np.uint8), stop_node_id=roi.id)
    assert result['status'] == 'partial'
    assert result['final_verdict'] == 'REVIEW'
    assert result['is_ok'] is False
    assert result['routed_output_node_id'] is None
    steps = {step['node_id']: step for step in result['execution_steps']}
    assert steps[roi.id]['artifacts'][0]['bbox'] == [8, 12, 40, 44]
    assert steps[model.id]['skip_reason'] == 'outside_debug_scope'
    assert result['stop_node_id'] == roi.id
    assert result['graph_sha256']


def test_stop_at_input_leaves_every_model_unexecuted():
    graph = get_fixed_roi_flowchart(inspection_task='segmentation')
    input_node = next(n for n in graph.nodes if n.data.node_type == 'input')
    result = FlowchartEngine(device='cpu').execute(pipeline=graph, image=np.zeros((64, 64, 3), dtype=np.uint8), stop_node_id=input_node.id)
    assert len([s for s in result['execution_steps'] if s['status'] != 'skipped']) == 1
    assert all(s['latency_ms'] == 0 for s in result['execution_steps'] if s['status'] == 'skipped')


def test_debug_rejects_unknown_stop_node_before_image_or_model_loading():
    with pytest.raises(ValueError, match='stop node'):
        FlowchartEngine(device='cpu').execute(pipeline=get_fixed_roi_flowchart(), image=np.zeros((64, 64, 3), dtype=np.uint8), stop_node_id='missing')

from backend.engine import flow_workspace


def test_template_roundtrip_requires_explicit_compatible_model_mapping(tmp_path):
    graph = get_fixed_roi_flowchart(inspection_task='classification', job_id='job_old')
    record = flow_workspace.save_template(tmp_path, graph, name='Part inspection', project_id='project-a')
    reopened = flow_workspace.load_templates(tmp_path)
    assert reopened[0]['template_id'] == record['template_id']
    model = next(n for n in graph.nodes if n.data.node_type == 'inspection')
    with pytest.raises(ValueError, match='mapping'):
        flow_workspace.map_template(record, {}, {}, [{'job_id':'job_new','task':'classification'}])
    mapped = flow_workspace.map_template(record, {model.id:'job_new'}, {}, [{'job_id':'job_new','task':'classification'}])
    assert next(n for n in mapped.nodes if n.data.node_type == 'inspection').data.model_job_id == 'job_new'
    with pytest.raises(ValueError, match='compatible'):
        flow_workspace.map_template(record, {model.id:'job_det'}, {}, [{'job_id':'job_det','task':'detection'}])


def test_template_subgraph_preserves_internal_edges_and_exposes_boundary_ports(tmp_path):
    graph = get_fixed_roi_flowchart(inspection_task='classification', job_id='job_old')
    ids = [n.id for n in graph.nodes if n.data.node_type in ('fixed_roi','inspection')]
    record = flow_workspace.save_template(tmp_path, graph, name='ROI module', project_id='project-a', node_ids=ids)
    assert record['kind'] == 'subgraph'
    assert len(record['pipeline']['nodes']) == 2
    assert len(record['pipeline']['edges']) == 1
    assert record['ports']['inputs'][0]['payload_type'] == 'image'
    assert record['ports']['outputs'][0]['payload_type'] == 'result'


def test_comparison_reports_changed_verdict_roi_and_node_execution():
    a={'final_verdict':'OK','rejection_reason':'within limit','crops':[{'roi_id':'a','bbox':[0,0,16,16],'verdict':'OK'}], 'execution_steps':[{'node_id':'inspect','branch_verdict':'OK','skip_reason':None}]}
    b={'final_verdict':'NG','rejection_reason':'area exceeded','crops':[{'roi_id':'a','bbox':[8,0,24,16],'verdict':'NG'}], 'execution_steps':[{'node_id':'inspect','branch_verdict':'NG','skip_reason':None}]}
    diff=flow_workspace.compare_results(a,b)
    assert diff['verdict_changed'] is True
    assert diff['roi_changed'] is True
    assert diff['changed_nodes'] == ['inspect']
    assert diff['reason_a'] == 'within limit'
    assert diff['reason_b'] == 'area exceeded'

from types import SimpleNamespace
from pathlib import Path
from PIL import Image
from fastapi import HTTPException
from backend.api import routes_flow_workspace as workspace_routes
from backend.api import routes_flowchart as flow_routes


@pytest.mark.parametrize('device,target,profile', [('cpu','local',None),('mps','local',None),('cuda','selected_compute','owned-server')])
def test_fixed_input_comparison_persists_and_reopens_without_activating_version(tmp_path,monkeypatch,device,target,profile):
    source=tmp_path/'source';source.mkdir();image=source/'one.png';Image.new('RGB',(64,64),'red').save(image)
    project={'id':'project-a','project_dir':str(tmp_path/'project'),'dataset_dir':str(tmp_path/'project'/'dataset'),'source_dataset_dir':str(source)}
    request=SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(workspace_routes,'get_current_project',lambda _:project)
    monkeypatch.setattr(flow_routes,'get_current_project',lambda _:project)
    ga=get_fixed_roi_flowchart(inspection_task='classification',job_id='job_a')
    gb=ga.model_copy(deep=True);gb.name='Second';next(n for n in gb.nodes if n.data.node_type=='inspection').data.threshold=0.9
    va=flow_routes._save_version(ga,'classification',str(source),Path(project['project_dir']))
    vb=flow_routes._save_version(gb,'classification',str(source),Path(project['project_dir']))
    calls=[]
    def inference(req,request):
        calls.append((req.image_path,req.pipeline.name,req.device,req.execution_target,req.compute_profile_id))
        return {'final_verdict':'OK' if req.pipeline.name=='Second' else 'NG','rejection_reason':req.pipeline.name,'crops':[],'execution_steps':[]}
    monkeypatch.setattr(flow_routes,'run_flowchart',inference)
    result=workspace_routes.compare_versions(workspace_routes.FlowCompare(project_id=project['id'],version_a=va,version_b=vb,image_paths=[str(image)],device=device,execution_target=target,compute_profile_id=profile),request)
    assert calls[0][0]==calls[1][0] and calls[0][0]!=str(image)
    assert not Path(calls[0][0]).exists()
    assert calls[0][2:]==calls[1][2:]==(device,target,profile)
    assert result['device']==device
    assert result['execution_target']==target
    assert result['compute_profile_id']==profile
    assert result['rows'][0]['difference']['verdict_changed'] is True
    assert workspace_routes.comparisons(request)['comparisons'][0]['comparison_id']==result['comparison_id']
    assert not (Path(project['project_dir'])/'flowcharts'/'active.json').exists()
    with pytest.raises(HTTPException) as error:
        workspace_routes.compare_versions(workspace_routes.FlowCompare(project_id='other',version_a=va,version_b=vb,image_paths=[str(image)]),request)
    assert error.value.status_code==409
    project['source_dataset_dir']=str(tmp_path/'other')
    assert workspace_routes.comparisons(request)['comparisons']==[]


def test_comparison_rejects_modified_input_without_publishing_receipt(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();image=source/'one.png';Image.new('RGB',(64,64)).save(image)
    project={'id':'a','project_dir':str(tmp_path/'project'),'source_dataset_dir':str(source)}
    request=SimpleNamespace()
    monkeypatch.setattr(workspace_routes,'get_current_project',lambda _:project)
    monkeypatch.setattr(flow_routes,'get_current_project',lambda _:project)
    graph=get_fixed_roi_flowchart(inspection_task='classification',job_id='job_a')
    va=flow_routes._save_version(graph,'classification',str(source),Path(project['project_dir']))
    vb=flow_routes._save_version(graph,'classification',str(source),Path(project['project_dir']))
    def modified_input(*args,**kwargs):
        image.write_bytes(b'changed after input selection')
        return {'final_verdict':'OK','crops':[],'execution_steps':[]}
    monkeypatch.setattr(flow_routes,'run_flowchart',modified_input)
    with pytest.raises(HTTPException) as error:
        workspace_routes.compare_versions(workspace_routes.FlowCompare(project_id='a',version_a=va,version_b=vb,image_paths=[str(image)]),request)
    assert error.value.status_code==409
    assert not list((Path(project['project_dir'])/'flowcharts'/'comparisons').glob('*.json'))


def test_template_numeric_class_names_and_ids_have_independent_mappings(tmp_path):
    graph=get_fixed_roi_flowchart(inspection_task='segmentation',job_id='job_old')
    model=next(n for n in graph.nodes if n.data.node_type=='inspection')
    model.data.params['class_names']=['background','1']
    graph.edges[2].predicate={'kind':'class','operator':'present','class_name':'1','min_confidence':0}
    record=flow_workspace.save_template(tmp_path,graph,name='Numeric name',project_id='a')
    mapped=flow_workspace.map_template(record,{model.id:'job_new'},{'name:background':'background','name:1':'scratch'},[{'job_id':'job_new','task':'segmentation'}])
    assert next(n for n in mapped.nodes if n.data.node_type=='inspection').data.params['class_names']==['background','scratch']
    assert mapped.edges[2].predicate['class_name']=='scratch'


def test_template_class_mapping_is_per_model_node(tmp_path):
    from backend.engine.flowchart_engine import FlowEdge
    graph=get_fixed_roi_flowchart(inspection_task='segmentation',job_id='job_old')
    first=next(n for n in graph.nodes if n.data.node_type=='inspection')
    first.data.params['class_names']=['background','old']
    second=first.model_copy(deep=True);second.id='second';second.data.model_job_id='job_old2'
    graph.nodes.insert(3,second)
    graph.edges[2].target='second'
    graph.edges[2].payload_type='roi'
    decision=next(n for n in graph.nodes if n.data.node_type=='decision')
    graph.edges.append(FlowEdge(id='second-decision',source='second',target=decision.id,payload_type='result'))
    record=flow_workspace.save_template(tmp_path,graph,name='Two class scopes',project_id='a')
    mapped=flow_workspace.map_template(record,{first.id:'job_new','second':'job_new2'},{f'{first.id}:name:background':'background',f'{first.id}:name:old':'scratch','second:name:background':'background','second:name:old':'dust'},[{'job_id':'job_new','task':'segmentation'},{'job_id':'job_new2','task':'segmentation'}])
    assert next(n for n in mapped.nodes if n.id==first.id).data.params['class_names']==['background','scratch']
    assert next(n for n in mapped.nodes if n.id=='second').data.params['class_names']==['background','dust']


def test_debug_stop_at_decision_does_not_execute_output_or_select_route(monkeypatch):
    from backend.engine.flowchart_engine import CropInspectionResult
    graph=get_fixed_roi_flowchart(inspection_task='classification',job_id='job_a')
    decision=next(n for n in graph.nodes if n.data.node_type=='decision')
    engine=FlowchartEngine(device='cpu')
    monkeypatch.setattr(engine,'_inspect_crops',lambda img,rois,node:([CropInspectionResult(roi_id='one',label='OK',bbox=[0,0,32,32],defect_score=0.1,verdict='OK',crop_thumbnail='',flaw_type='test')],1,'passed'))
    result=engine.execute(pipeline=graph,image=np.zeros((64,64,3),dtype=np.uint8),stop_node_id=decision.id)
    by_id={s['node_id']:s for s in result['execution_steps']}
    assert by_id[decision.id]['selected_edge_ids']==[]
    assert all(by_id[n.id]['skip_reason']=='outside_debug_scope' for n in graph.nodes if n.data.node_type=='output')
    assert result['routed_output_node_id'] is None
    assert result['graph_sha256']


def test_comparison_freezes_input_bytes_even_when_source_changes_and_is_restored(tmp_path,monkeypatch):
    import hashlib
    source=tmp_path/'source';source.mkdir();image=source/'one.png';Image.new('RGB',(64,64),'red').save(image);original=image.read_bytes()
    project={'id':'a','project_dir':str(tmp_path/'project'),'source_dataset_dir':str(source)}
    request=SimpleNamespace()
    monkeypatch.setattr(workspace_routes,'get_current_project',lambda _:project)
    monkeypatch.setattr(flow_routes,'get_current_project',lambda _:project)
    graph=get_fixed_roi_flowchart(inspection_task='classification',job_id='job_a')
    va=flow_routes._save_version(graph,'classification',str(source),Path(project['project_dir']))
    vb=flow_routes._save_version(graph,'classification',str(source),Path(project['project_dir']))
    seen=[]
    def changed_between(req,request):
        seen.append(hashlib.sha256(Path(req.image_path).read_bytes()).hexdigest())
        image.write_bytes(b'different' if len(seen)==1 else original)
        return {'final_verdict':'OK','crops':[],'execution_steps':[]}
    monkeypatch.setattr(flow_routes,'run_flowchart',changed_between)
    result=workspace_routes.compare_versions(workspace_routes.FlowCompare(project_id='a',version_a=va,version_b=vb,image_paths=[str(image)]),request)
    assert seen==[hashlib.sha256(original).hexdigest()]*2
    assert result['rows'][0]['image_path']==str(image)
