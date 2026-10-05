"""OCR recipe rules and geometry survive flow execution and ROI remapping."""
import numpy as np
from backend.engine.flowchart_engine import FlowchartEngine, FlowNode, FlowNodeData


def prediction():
    return {'text':'ABC','confidence':.9,'regions':[{'box':[1,2,4,6],'polygon':[[1,2],[4,2],[4,6],[1,6]],'line_index':0,'region_index':0,'text':'ABC','confidence':.9}],
            'recipe':{'mode':'detect_recognize'},'text_rule_result':{'passed':False,'failed_rules':['allowed_charset']}}


def inspect(monkeypatch, roi):
    from backend.engine import ocr
    engine=FlowchartEngine(device='cpu')
    monkeypatch.setattr(engine,'_resolve_checkpoint',lambda *args:'local.pt')
    monkeypatch.setattr(ocr,'predict_ocr_array',lambda *args,**kwargs:prediction())
    node=FlowNode(id='ocr',position={},data=FlowNodeData(label='OCR',node_type='inspection',task='ocr',model_job_id='job',params={'expected_text':'ABC'}))
    rows,_,_=engine._inspect_crops(np.zeros((40,50,3),np.uint8),[roi],node)
    return rows[0].model_dump()


def test_saved_recipe_rule_failure_overrides_matching_node_text(monkeypatch):
    row=inspect(monkeypatch,{'id':'r','label':'text','bbox':[10,20,30,35]})
    assert row['verdict']=='NG'
    assert row['ocr_regions'][0]['box']==[11,22,14,26]
    assert row['ocr_regions'][0]['polygon']==[[11,22],[14,22],[14,26],[11,26]]
    assert row['ocr_recipe']['mode']=='detect_recognize'
    assert row['rule_violations'][-1]['rule']=='allowed_charset'


def test_transformed_text_regions_map_to_original_source(monkeypatch):
    row=inspect(monkeypatch,{'id':'r','label':'text','bbox':[10,20,30,35],
        'image':np.zeros((10,15,3),np.uint8),'source_transform':[[2,0,10],[0,2,15],[0,0,1]]})
    assert row['ocr_regions'][0]['box']==[11,18,18,27]
    assert row['ocr_regions'][0]['polygon']==[[11.5,18.5],[17.5,18.5],[17.5,26.5],[11.5,26.5]]
