"""Independent behavioral review regressions; implementation owned by task 2."""
from types import SimpleNamespace
import json
from pathlib import Path
import numpy as np
import pytest
from backend.engine.flowchart_engine import (
    FlowchartEngine, FlowNode, FlowNodeData, FlowEdge, FlowchartPipeline,
    CropInspectionResult, FlowchartInspectionConfigurationError,
)


def _graph(task='classification', predicate=None):
    nodes = [FlowNode(id=name, position={}, data=FlowNodeData(
        label=name, node_type=kind, task=task if name=='model' else None,
        model_job_id='a'*32 if name=='model' else None)) for name,kind in
        [('input','input'),('model','inspection'),('decision','decision'),('output','output')]]
    return FlowchartPipeline(nodes=nodes, edges=[
        FlowEdge(id='input-model',source='input',target='model'),
        FlowEdge(id='model-decision',source='model',target='decision',predicate=predicate),
        FlowEdge(id='decision-output',source='decision',target='output'),
    ])


def test_failed_rotated_inference_does_not_take_absent_class_branch(monkeypatch):
    graph = _graph('rotated_detection', {'kind':'class','operator':'absent','class_name':'scratch'})
    engine = FlowchartEngine(device='cpu')
    def failed(*args): raise FlowchartInspectionConfigurationError('Checkpoint unavailable')
    monkeypatch.setattr(engine, '_inspect_crops', failed)
    result = engine.execute(pipeline=graph, image=np.zeros((32,32,3),np.uint8))
    step = next(s for s in result['execution_steps'] if s['node_id']=='model')
    assert result['final_verdict'] == 'REVIEW'
    assert step['branch_verdict'] == 'REVIEW', step
    assert step['selected_edge_ids'] == [], step


def test_class_predicate_does_not_match_inherited_roi_label(monkeypatch):
    graph = _graph(predicate={'kind':'class','operator':'present','class_name':'Full image'})
    engine = FlowchartEngine(device='cpu')
    def classify(image, rois, node):
        return [CropInspectionResult(roi_id='full_image',label='OK',bbox=[0,0,32,32],
            defect_score=0,verdict='OK',crop_thumbnail='',flaw_type='',predicted_class='OK',confidence=.9)],0,'passed'
    monkeypatch.setattr(engine, '_inspect_crops', classify)
    result = engine.execute(pipeline=graph,image=np.zeros((32,32,3),np.uint8))
    step = next(s for s in result['execution_steps'] if s['node_id']=='model')
    assert step['selected_edge_ids'] == [], step


def test_cancel_during_family_finalization_cannot_publish_completed_model(tmp_path,monkeypatch):
    from backend.engine import specialized_training_jobs as jobs
    from backend.engine import training_provenance as provenance
    source = tmp_path/'source'; source.mkdir()
    output = tmp_path/'models'/'ocr'/('b'*32)
    monkeypatch.setattr(provenance,'bind_family_training',lambda *args:{'dataset_fingerprint':'frozen'})
    monkeypatch.setattr(provenance,'validate_training_binding',lambda *args:None)
    accepted = []
    def cancel_at_binding(folder,binding):
        accepted.append(jobs.cancel_job(output.parent,output.name)['status'])
    monkeypatch.setattr(provenance,'persist_model_binding',cancel_at_binding)
    def runner(event,progress,device):
        output.mkdir(parents=True,exist_ok=True)
        (output/'best_model.pt').write_bytes(b'checkpoint')
        (output/'model_meta.json').write_text(json.dumps({'task':'ocr'}))
        return {'finished':True}
    options = SimpleNamespace(device='cpu',epochs=1,background=False)
    with pytest.raises(InterruptedError):
        jobs.start_job(project={'source_dataset_dir':str(source)},task='ocr',source=source,
            output=output,options=options,runner=runner,family_digest=lambda:'frozen-labels')
    assert accepted == ['stopping']
    assert jobs.read_job(output.parent,output.name)['status']=='stopped'
    assert not (output/'best_model.pt').exists()
