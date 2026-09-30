import numpy as np
import pytest
from backend.engine.flowchart_engine import FlowEdge, FlowNode, FlowNodeData, FlowchartPipeline, FlowchartEngine, CropInspectionResult, ordered_linear_nodes, _branch_matches


def graph(operator=None, predicate=None, task='classification'):
    nodes = [FlowNode(id='input',position={},data=FlowNodeData(label='입력',node_type='input'))]
    edges=[]
    upstream='input'
    if operator:
        nodes.append(FlowNode(id='operator',position={},data=FlowNodeData(label='변환',node_type=operator[0],params=operator[1])))
        edges.append(FlowEdge(id='input-op',source='input',target='operator'))
        upstream='operator'
    nodes.extend([FlowNode(id='model',position={},data=FlowNodeData(label='검사',node_type='inspection',task=task,model_job_id='job_test',params={'expected_text':'ABC'} if task=='ocr' else {})),FlowNode(id='decision',position={},data=FlowNodeData(label='판정',node_type='decision')),FlowNode(id='output',position={},data=FlowNodeData(label='결과',node_type='output'))])
    edges.extend([FlowEdge(id='to-model',source=upstream,target='model'),FlowEdge(id='to-decision',source='model',target='decision',predicate=predicate),FlowEdge(id='to-output',source='decision',target='output')])
    return FlowchartPipeline(nodes=nodes,edges=edges)


def test_class_predicates_use_observed_labels_and_confidence():
    edge=FlowEdge(id='class',source='a',target='b',predicate={'kind':'class','operator':'present','class_name':'scratch','min_confidence':0.8})
    assert not _branch_matches(edge,'NG',[{'label':'scratch','confidence':0.5}])
    assert _branch_matches(edge,'OK',[{'label':'scratch','confidence':0.9}])
    absent=edge.model_copy(update={'predicate': {'kind':'class','operator':'absent','class_name':'scratch','min_confidence':0.8}})
    assert _branch_matches(absent,'OK',[])
    assert _branch_matches(FlowEdge(id='legacy',source='a',target='b',isBranch='fail'),'NG')


def test_invalid_predicate_rejected():
    with pytest.raises(ValueError,match='class'):
        ordered_linear_nodes(graph(predicate={'kind':'class','operator':'present','class_name':''}))


def test_patch_split_covers_source_and_persists_node_images(monkeypatch):
    pipeline=graph(('patch_split',{'patch_width':32,'patch_height':32,'overlap':0}))
    seen=[]
    def inspect(self,image,rois,node):
        seen.extend(rois)
        return [CropInspectionResult(roi_id=r['id'],label='OK',bbox=r['bbox'],defect_score=0,verdict='OK',crop_thumbnail='',flaw_type='') for r in rois],0,'passed'
    monkeypatch.setattr(FlowchartEngine,'_inspect_crops',inspect)
    result=FlowchartEngine(device='cpu').execute(pipeline=pipeline,image=np.zeros((48,64,3),dtype=np.uint8))
    assert len(seen)==4
    assert sorted(r['bbox'] for r in seen)==[[0,0,32,32],[0,16,32,48],[32,0,64,32],[32,16,64,48]]
    step=next(s for s in result['execution_steps'] if s['node_id']=='operator')
    assert len(step['artifacts'])==4
    assert step['artifacts'][0]['image'].startswith('data:image/png;base64,')
    assert step['artifacts'][0]['source_transform']==[[1,0,0],[0,1,0],[0,0,1]]


def test_rotation_maps_pixels_to_original_coordinates():
    from backend.engine.flow_operators import apply_operator
    image=np.zeros((24,32,3),dtype=np.uint8); image[3,4]=255
    regions=apply_operator(image,[{'id':'full','label':'full','bbox':[0,0,32,24]}],'preprocess',{'operation':'rotate','angle_deg':90},'rotate')
    region=regions[0]
    ys,xs=np.where(region['image'][:,:,0]>200)
    point=np.array([xs[0],ys[0],1])
    source=np.array(region['source_transform'])@point
    assert np.allclose(source[:2],[4,3],atol=1)


def test_ocr_task_requires_text_rule_and_compiles_regex():
    pipeline=graph(task='ocr'); ordered_linear_nodes(pipeline)
    pipeline.nodes[1].data.params={'regex':'['}
    with pytest.raises(ValueError,match='regex'):
        ordered_linear_nodes(pipeline)
    pipeline.nodes[1].data.params={}
    with pytest.raises(ValueError,match='expected_text|regex'):
        ordered_linear_nodes(pipeline)


def test_rotation_keeps_entire_rectangular_source():
    from backend.engine.flow_operators import apply_operator
    image=np.full((24,48,3),255,np.uint8)
    result=apply_operator(image,[{'id':'full','label':'full','bbox':[0,0,48,24]}],'preprocess',{'operation':'rotate','angle_deg':90},'rotate')[0]
    assert result['image'].shape[:2]==(48,24)
    assert np.count_nonzero(result['image'][:,:,0]>200)==48*24


def test_absent_class_routes_original_image_after_completed_empty_detector(monkeypatch):
    pipeline=graph()
    model=pipeline.nodes[1]
    model.data.node_type='detection_crop';model.data.task='detection'
    refined=FlowNode(id='refined',position={},data=FlowNodeData(label='정밀 검사',node_type='inspection',task='classification',model_job_id='job_refined'))
    pipeline.nodes.insert(2,refined)
    pipeline.edges=[pipeline.edges[0],FlowEdge(id='absence',source='model',target='refined',payload_type='image',predicate={'kind':'class','operator':'absent','class_name':'scratch'}),FlowEdge(id='refined-result',source='refined',target='decision'),pipeline.edges[-1]]
    engine=FlowchartEngine(device='cpu')
    monkeypatch.setattr(engine,'_detect_in_regions',lambda *args:([],0,'passed'))
    def inspect(image,rois,node):
        return [CropInspectionResult(roi_id='real-inspection',label='OK',bbox=[0,0,32,32],defect_score=0,verdict='OK',crop_thumbnail='',flaw_type='')],0,'passed'
    monkeypatch.setattr(engine,'_inspect_crops',inspect)
    result=engine.execute(pipeline=pipeline,image=np.zeros((32,32,3),np.uint8))
    assert result['final_verdict']=='OK'
    assert next(s for s in result['execution_steps'] if s['node_id']=='refined')['status']=='passed'


def test_alignment_without_observed_orientation_explains_review(monkeypatch):
    pipeline=graph(('preprocess',{'operation':'align','target_angle_deg':0}))
    result=FlowchartEngine(device='cpu').execute(pipeline=pipeline,image=np.zeros((32,32,3),np.uint8))
    assert result['final_verdict']=='REVIEW'
    step=next(s for s in result['execution_steps'] if s['node_id']=='operator')
    assert 'orientation' in step['skip_reason']


@pytest.mark.parametrize('params',[{'expected_text':'A','regex':17},{'regex':17},{'regex':'a','expected_text':'A'}])
def test_ocr_malformed_or_ambiguous_rule_is_explicit_configuration_error(params):
    pipeline=graph(task='ocr');pipeline.nodes[1].data.params=params
    with pytest.raises(ValueError,match='OCR'):
        ordered_linear_nodes(pipeline)
