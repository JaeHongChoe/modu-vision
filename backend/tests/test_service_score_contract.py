"""Regression boundaries for distance score handoff into executable flows."""
from pathlib import Path
import numpy as np
import pytest

from backend.engine.anomaly.padim import PaDiMDetector
from backend.engine.anomaly.patchcore import PatchCoreDetector
from backend.engine.flowchart_engine import (
    FlowchartEngine, FlowchartPipeline, FlowNode, FlowNodeData,
    FlowchartInspectionConfigurationError, ordered_linear_nodes,
)


def node(**data):
    return FlowNode(id="inspection", position={"x": 0, "y": 0},
                    data=FlowNodeData(label="inspection", node_type="inspection", **data))


def graph(model):
    return FlowchartPipeline(nodes=[
        {"id": "input", "position": {"x": 0, "y": 0}, "data": {"label": "input", "node_type": "input"}},
        model,
        {"id": "decision", "position": {"x": 0, "y": 0}, "data": {"label": "decision", "node_type": "decision"}},
        {"id": "output", "position": {"x": 0, "y": 0}, "data": {"label": "output", "node_type": "output"}},
    ], edges=[
        {"id": "e1", "source": "input", "target": "inspection"},
        {"id": "e2", "source": "inspection", "target": "decision"},
        {"id": "e3", "source": "decision", "target": "output"},
    ])


def distance_spec(unit="mahalanobis_distance", calibration_id="calibration-a", threshold=8):
    return {"domain": "distance", "unit": unit, "direction": "higher_is_defect",
            "calibration_id": calibration_id, "threshold": threshold}


@pytest.mark.parametrize("detector_class,unit", [
    (PaDiMDetector, "mahalanobis_distance"), (PatchCoreDetector, "euclidean_distance"),
])
def test_legacy_unitless_flow_uses_actual_distance_calibration(monkeypatch, detector_class, unit):
    # Keep the detector identity while bounding inference; no weights downloaded.
    detector = object.__new__(detector_class)
    detector.threshold = 8.0
    detector.score_spec = distance_spec(unit)
    detector.predict_anomaly_map = lambda pixels: (np.full((16, 16), 4.0), 4.0)
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **kwargs: (detector, True))
    monkeypatch.setattr(engine, "_resolve_checkpoint", lambda *args: None)
    result, _, status = engine._inspect_crops(np.zeros((32, 32, 3), np.uint8),
        [{"id": "roi", "label": "part", "bbox": [0, 0, 32, 32]}],
        node(task="anomaly", threshold=0.5, crop_padding=0))
    assert status == "passed"
    assert result[0].verdict == "OK"
    assert result[0].score_spec["threshold"] == 8
    assert result[0].score_spec["unit"] == unit


def test_distance_threshold_survives_graph_roundtrip_without_relaxing_probabilities():
    pipeline = graph(node(task="anomaly", threshold=8, score_spec=distance_spec()))
    ordered_linear_nodes(pipeline)
    reopened = FlowchartPipeline.model_validate_json(pipeline.model_dump_json())
    assert reopened.nodes[1].data.score_spec["threshold"] == 8
    with pytest.raises(ValueError, match="threshold"):
        ordered_linear_nodes(graph(node(task="classification", threshold=1.5)))


def test_incompatible_calibration_is_rejected_before_anomaly_prediction(monkeypatch):
    detector = object.__new__(PaDiMDetector)
    detector.threshold = 8.0
    detector.score_spec = distance_spec()
    calls = []
    detector.predict_anomaly_map = lambda pixels: calls.append(pixels)
    engine = FlowchartEngine(device="cpu")
    monkeypatch.setattr(engine, "_get_inspection_model", lambda **kwargs: (detector, True))
    monkeypatch.setattr(engine, "_resolve_checkpoint", lambda *args: None)
    with pytest.raises(FlowchartInspectionConfigurationError, match="calibration"):
        engine._inspect_crops(np.zeros((32, 32, 3), np.uint8),
            [{"id": "roi", "label": "part", "bbox": [0, 0, 32, 32]}],
            node(task="anomaly", threshold=8, score_spec=distance_spec(calibration_id="wrong")))
    assert calls == []


def fitted_detector(monkeypatch, detector_class):
    import torch
    from backend.engine.anomaly import padim, patchcore
    class TinyFeatures(torch.nn.Module):
        embed_dim = 2
        def __init__(self, **kwargs):
            super().__init__()
            self.register_buffer('identity', torch.tensor([1.0]))
        def forward(self, pixels):
            return pixels[:, :2].mean(dim=(2,3), keepdim=True) * self.identity
    monkeypatch.setattr(padim, 'ResNetFeatureExtractor', TinyFeatures)
    monkeypatch.setattr(patchcore, 'ResNetFeatureExtractor', TinyFeatures)
    detector = detector_class(pretrained=False, device='cpu')
    if detector_class is PaDiMDetector:
        detector.mean = torch.zeros(1,1,1,2)
        detector.cov_inv = torch.eye(2).reshape(1,1,2,2)
    else:
        detector.coreset = torch.zeros(2,2)
    detector.threshold = 8.0
    return detector


@pytest.mark.parametrize('detector_class', [PaDiMDetector, PatchCoreDetector])
def test_distance_checkpoint_persists_transfer_stable_calibration(tmp_path, monkeypatch, detector_class):
    import torch
    detector = fitted_detector(monkeypatch, detector_class)
    state = detector.state_dict()
    assert state['score_spec']['threshold'] == 8
    other = fitted_detector(monkeypatch, detector_class)
    other.load_state_dict(state)
    assert other.score_spec == state['score_spec']
    checkpoint = tmp_path/'model.pt'
    torch.save({'task':'anomaly','model_state_dict':state}, checkpoint)
    from backend.engine.score_contract import checkpoint_score_spec
    assert checkpoint_score_spec(checkpoint) == other.score_spec
    moved = tmp_path/'other.pt'; moved.write_bytes(checkpoint.read_bytes())
    assert checkpoint_score_spec(moved) == other.score_spec
    changed = {**state, 'threshold':9}
    with pytest.raises(ValueError, match='calibration'):
        other.load_state_dict(changed)


def test_global_score_rule_rejects_incompatible_units_and_calibrations():
    from backend.engine.flowchart_engine import CropInspectionResult
    def crop(spec):
        return CropInspectionResult(roi_id='roi',label='part',bbox=[0,0,16,16],defect_score=4,
            verdict='OK',crop_thumbnail='',flaw_type='',score_spec=spec)
    decision = FlowNode(id='decision',position={'x':0,'y':0},data=FlowNodeData(label='decision',node_type='decision',
        rule='score_gt_threshold',threshold=8,score_spec=distance_spec()))
    engine = FlowchartEngine(device='cpu')
    for incompatible in [None, distance_spec(unit='euclidean_distance'), distance_spec(calibration_id='other')]:
        with pytest.raises(FlowchartInspectionConfigurationError,match='score|calibration'):
            engine._evaluate_decision_rules([crop(distance_spec()),crop(incompatible)], decision)


@pytest.mark.parametrize('detector_class', [PaDiMDetector, PatchCoreDetector])
def test_catalog_and_portable_package_keep_distance_default(tmp_path, monkeypatch, detector_class):
    import torch
    from PIL import Image
    from backend.api.routes_flowchart import _catalog_model_settings
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flow_package_runtime import run_flow_package
    detector = fitted_detector(monkeypatch, detector_class)
    checkpoint = tmp_path/'best_model.pt'
    state = detector.state_dict()
    torch.save({'task':'anomaly', 'model_state_dict':state, 'image_size':[32,32]}, checkpoint)
    settings = _catalog_model_settings({'task':'anomaly'}, checkpoint)
    assert settings['threshold_settings']['threshold'] == 8
    assert settings['score_spec'] == state['score_spec']
    pipeline = graph(node(task='anomaly',model_job_id='job_distance',threshold=8,score_spec=settings['score_spec']))
    package = build_flow_package(pipeline=pipeline,checkpoints={'job_distance':checkpoint},
        output_base_dir=tmp_path/'packages',package_name='distance')['package_path']
    source = tmp_path/'image.png'; Image.new('RGB',(32,32),'white').save(source)
    result = run_flow_package(Path(package),source,device='cpu',_owned_worker=True)
    assert result['final_verdict'] == 'OK'
    assert result['crops'][0]['score_spec'] == settings['score_spec']
    assert result['crops'][0]['defect_score'] < 8


def test_saved_distance_threshold_agrees_in_direct_inference_and_heatmap_api(tmp_path, monkeypatch):
    import json, torch
    from PIL import Image
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import routes_evaluation
    from backend.engine.trainer import infer
    detector=fitted_detector(monkeypatch, PaDiMDetector)
    state=detector.state_dict(); checkpoint=tmp_path/'best_model.pt'
    torch.save({'task':'anomaly','model_state_dict':state,'image_size':[32,32]},checkpoint)
    checkpoint.with_name('model_meta.json').write_text(json.dumps({'task':'anomaly','image_size':[32,32]}))
    source=tmp_path/'image.png';Image.new('RGB',(32,32),'white').save(source)
    direct=infer('anomaly',checkpoint,source,device='cpu')
    assert direct.predictions['is_anomaly'] is False
    assert direct.predictions['threshold']==8
    monkeypatch.setattr(routes_evaluation,'_find_image_file',lambda *a,**k:source)
    monkeypatch.setattr(routes_evaluation,'_find_model_file',lambda *a,**k:checkpoint)
    monkeypatch.setattr(routes_evaluation,'get_device',lambda:torch.device('cpu'))
    app=FastAPI();app.include_router(routes_evaluation.router);client=TestClient(app)
    result=client.get('/api/evaluation/heatmap/image',params={'job_id':'job_distance'})
    assert result.status_code==200,result.text
    assert result.json()['threshold']==8
    bound=client.get('/api/evaluation/heatmap/image',params={'job_id':'job_distance','threshold':9,
        'score_spec':json.dumps({**state['score_spec'],'threshold':9})})
    assert bound.status_code==200,bound.text
    assert bound.json()['predictions']['threshold']==9
    assert client.get('/api/evaluation/heatmap/image',params={'job_id':'job_distance','threshold':9}).status_code==422
    wrong={**state['score_spec'],'threshold':9,'calibration_id':'other'}
    assert client.get('/api/evaluation/heatmap/image',params={'job_id':'job_distance','threshold':9,'score_spec':json.dumps(wrong)}).status_code==422


def test_test_cohort_does_not_retune_saved_anomaly_threshold(tmp_path,monkeypatch):
    import torch
    from PIL import Image
    from backend.api import routes_evaluation
    detector=fitted_detector(monkeypatch, PaDiMDetector)
    state=detector.state_dict(); checkpoint=tmp_path/'model.pt'
    torch.save({'task':'anomaly','model_state_dict':state,'image_size':[32,32]},checkpoint)
    for label in ['good','anomaly']:
        directory=tmp_path/'test'/label;directory.mkdir(parents=True)
        Image.new('RGB',(32,32),'white').save(directory/'image.png')
    result=routes_evaluation._evaluate_anomaly(checkpoint,{'task':'anomaly','image_size':[32,32]},tmp_path,torch.device('cpu'))
    assert result['metrics']['active_threshold']==8
    assert result['metrics']['score_spec']==state['score_spec']
    assert all(row['predicted_class']=='good' for row in result['test_predictions'])
    assert all(row['defect_score']<8 for row in result['test_predictions'])


def test_evaluation_annotation_preserves_raw_distance_score():
    from backend.api.routes_evaluation import _annotate_predictions
    rows=[{'ground_truth':'good','predicted_class':'good','confidence':4.,'defect_score':4.,'score_spec':distance_spec()}]
    _annotate_predictions(rows,'anomaly',{'good':'normal','anomaly':'defect'})
    assert rows[0]['defect_score']==4.


def test_non_anomaly_inference_rejects_distance_threshold_before_prediction(tmp_path):
    from backend.engine.trainer import infer
    with pytest.raises(ValueError, match='Probability threshold'):
        infer('classification',tmp_path/'unused.pt',np.zeros((32,32,3),np.uint8),threshold=8,device='cpu')


def test_remote_worker_accepts_only_bound_distance_override(tmp_path,monkeypatch):
    import json,torch
    from backend.remote import worker
    from backend.tests.test_remote_worker import _infer_spec
    detector=fitted_detector(monkeypatch,PaDiMDetector);state=detector.state_dict()
    _,operation,spec,_=_infer_spec(tmp_path)
    checkpoint=tmp_path/'distance.pt';torch.save({'task':'anomaly','model_state_dict':state,'image_size':[32,32]},checkpoint)
    metadata={'task':'anomaly'}
    monkeypatch.setattr(worker,'_source_training_run',lambda *a:(None,None,checkpoint,metadata))
    payload=json.loads(spec.read_text());payload.update(task='anomaly',threshold=9,score_spec={**state['score_spec'],'threshold':9})
    spec.write_text(json.dumps(payload))
    result=worker.run_infer(spec)
    assert result['status']=='completed',result
    output=json.loads((operation/'outputs/result.json').read_text())
    assert output['predictions']['is_anomaly'] is False
    assert output['predictions']['score_spec']==payload['score_spec']


def test_comparison_api_carries_bound_distance_override(tmp_path,monkeypatch):
    import json,torch
    from backend.tests.test_model_deployments import _fixture
    monkeypatch.chdir(tmp_path)
    client,project,source,fp,models=_fixture(tmp_path)
    assert client.put('/api/project/update',json={'task':'anomaly'}).status_code==200
    detector=fitted_detector(monkeypatch,PaDiMDetector);state=detector.state_dict()
    for checkpoint in models.values():
        torch.save({'task':'anomaly','classes':['OK','NG'],'model_state_dict':state,'image_size':[32,32]},checkpoint)
        checkpoint.with_name('model_meta.json').write_text(json.dumps({'task':'anomaly','classes':['OK','NG']}))
        receipt=checkpoint.with_name('job_receipt.json');value=json.loads(receipt.read_text());value['task']='anomaly';receipt.write_text(json.dumps(value))
    spec={**state['score_spec'],'threshold':9}
    body={'source_dataset_path':str(source),'task':'anomaly','incumbent_job_id':'job_base','candidate_job_id':'job_candidate',
        'full_test':True,'incumbent_params':{'threshold':9,'score_spec':spec},'candidate_params':{'threshold':9,'score_spec':spec}}
    result=client.post('/api/evaluation/model-comparisons',json=body)
    assert result.status_code==200,result.text
    assert all(row['candidate']['verdict']=='OK' for row in result.json()['images'])
    body['candidate_params']['score_spec']={**spec,'calibration_id':'stale'}
    assert client.post('/api/evaluation/model-comparisons',json=body).status_code==422


def test_probability_optimizer_rejects_distance_without_normalizing(tmp_path):
    import json
    from fastapi import HTTPException
    from backend.api.routes_evaluation import get_overkill_underkill_analysis
    payload={'evaluation_contract_version':2,'task':'anomaly',
        'metrics':{'evaluated_split':'val','selection_overlap':True,'score_spec':distance_spec()},
        'test_predictions':[{'image_id':'good','ground_truth':'good','predicted_class':'good','confidence':4},
            {'image_id':'ng','ground_truth':'anomaly','predicted_class':'anomaly','confidence':9}]}
    (tmp_path/'eval_results.json').write_text(json.dumps(payload))
    with pytest.raises(HTTPException,match='train/validation') as caught:
        get_overkill_underkill_analysis(str(tmp_path),0,500,25,8)
    assert caught.value.status_code==422


def test_remote_orchestrator_transmits_and_verifies_score_binding(tmp_path,monkeypatch):
    import json
    from backend.remote import operations
    from backend.tests.test_remote_operations import _completed_remote,_PNG
    context,_=_completed_remote(tmp_path,monkeypatch)
    image=context.dataset_path/'training_image.png';captured=[];spec=distance_spec()
    def operation(context,kind,params,**kwargs):
        captured.append(params)
        result=tmp_path/'result.json';overlay=tmp_path/'overlay.png';overlay.write_bytes(_PNG)
        result.write_text(json.dumps({'image_path':params['image_path'],'image_sha256':params['image_sha256'],
            'overlay_path':'outputs/overlay.png','predictions':{'score_spec':params.get('score_spec')},'threshold':params['threshold']}))
        return {'outputs/result.json':result,'outputs/overlay.png':overlay}
    monkeypatch.setattr(operations,'run_remote_operation_artifacts',operation)
    output,_=operations.run_remote_inference(context,image,8,'selected',score_spec=spec)
    assert captured[0]['score_spec']==spec
    assert output['predictions']['score_spec']==spec


def test_distance_score_boundary_is_lossless_through_evaluation_and_flow(monkeypatch):
    from backend.api.routes_evaluation import _annotate_predictions
    raw = 7.99996
    rows = [{'ground_truth':'good', 'predicted_class':'good', 'confidence':round(raw,4),
             'defect_score':raw, 'score_spec':distance_spec()}]
    _annotate_predictions(rows, 'anomaly', {'good':'normal', 'anomaly':'defect'})
    assert rows[0]['defect_score'] == raw


def test_distance_score_boundary_is_lossless_through_global_rule(monkeypatch):
    raw = 7.99996
    detector = object.__new__(PaDiMDetector)
    detector.threshold = 8.0
    detector.score_spec = distance_spec()
    detector.predict_anomaly_map = lambda pixels: (np.full((16,16),raw),raw)
    engine = FlowchartEngine(device='cpu')
    monkeypatch.setattr(engine, '_get_inspection_model', lambda **kwargs:(detector,True))
    monkeypatch.setattr(engine, '_resolve_checkpoint', lambda *args:None)
    crops, _, _ = engine._inspect_crops(np.zeros((32,32,3),np.uint8),
        [{'id':'roi','label':'part','bbox':[0,0,32,32]}], node(task='anomaly',threshold=8,score_spec=distance_spec()))
    assert crops[0].verdict == 'OK'
    decision = FlowNode(id='decision',position={'x':0,'y':0},data=FlowNodeData(label='decision',
        node_type='decision',rule='score_gt_threshold',threshold=8,score_spec=distance_spec()))
    assert engine._evaluate_decision_rules(crops,decision)[0] == 'OK'
    assert crops[0].defect_score == raw


def test_inference_revalidates_binding_against_loaded_checkpoint(tmp_path, monkeypatch):
    import torch
    from backend.engine import score_contract, trainer
    detector = fitted_detector(monkeypatch, PaDiMDetector)
    checkpoint = tmp_path/'model.pt'
    torch.save({'model_state_dict':detector.state_dict()},checkpoint)
    original = score_contract.checkpoint_score_spec(checkpoint)
    detector.threshold = .1
    torch.save({'model_state_dict':detector.state_dict()},checkpoint)
    # Force the resolve/read interleaving: cached authorization is stale while
    # the actual inference load already sees a different valid model state.
    monkeypatch.setattr(score_contract, 'resolve_inference_score', lambda *args:(8,original))
    calls=[]
    monkeypatch.setattr(PaDiMDetector,'predict_anomaly_map',lambda *args,**kwargs:calls.append(True))
    with pytest.raises(ValueError,match='calibration'):
        trainer.infer('anomaly',checkpoint,np.zeros((16,16,3),np.uint8),threshold=8,score_spec=original,device='cpu')
    assert calls == []


def test_checkpoint_cache_observes_same_size_same_mtime_replacement(tmp_path, monkeypatch):
    import os, torch
    from backend.engine.score_contract import checkpoint_score_spec
    detector=fitted_detector(monkeypatch,PaDiMDetector)
    checkpoint=tmp_path/'model.pt'
    torch.save({'model_state_dict':detector.state_dict()},checkpoint)
    before=checkpoint.stat(); old=checkpoint_score_spec(checkpoint)
    detector.threshold=.1
    torch.save({'model_state_dict':detector.state_dict()},checkpoint)
    assert checkpoint.stat().st_size == before.st_size
    os.utime(checkpoint,ns=(before.st_atime_ns,before.st_mtime_ns))
    current=checkpoint_score_spec(checkpoint)
    assert current['threshold']==.1
    assert current['calibration_id'] != old['calibration_id']


@pytest.mark.parametrize('detector_class',[PaDiMDetector,PatchCoreDetector])
def test_legacy_checkpoint_can_be_resaved_with_valid_state_binding(monkeypatch, detector_class):
    detector=fitted_detector(monkeypatch,detector_class)
    legacy=detector.state_dict()
    legacy.pop('score_spec')
    legacy.pop('feature_extractor_state_dict')
    restored=fitted_detector(monkeypatch,detector_class)
    restored.load_state_dict(legacy)
    saved=restored.state_dict()
    reloaded=fitted_detector(monkeypatch,detector_class)
    reloaded.load_state_dict(saved)
    assert reloaded.score_spec == saved['score_spec']
