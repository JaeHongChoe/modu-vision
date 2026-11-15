"""AP geometry stays the independently frozen original-image float32 basis after resizing."""
from pathlib import Path

import pytest
import torch
from PIL import Image

from backend.engine.detection.metrics import DETECTION_AP_LEGACY, DETECTION_AP_V2


class OwnedTensorDetector:
    """Known tensor output only; no architecture, weights, training or provider."""
    def to(self, device): return self
    def load_state_dict(self, value): pass
    def eval(self): return self
    def __call__(self, image):
        detected = float(image.mean()) > .3
        return [{'boxes':torch.tensor([[.1,.1,7.1,10.1]] if detected else [], dtype=torch.float32).reshape(-1,4),
                 'labels':torch.tensor([1] if detected else [], dtype=torch.int64),
                 'scores':torch.tensor([.9] if detected else [], dtype=torch.float32)}]


@pytest.mark.parametrize('contract', [DETECTION_AP_V2, DETECTION_AP_LEGACY])
def test_route_metric_and_retained_original_geometry_agree_at_resized_iou_boundary(tmp_path, monkeypatch, contract):
    from backend.api import routes_evaluation as routes
    from backend.engine.dataset_loaders import DetectionDataset
    image = tmp_path/'owned.png';Image.new('RGB',(1000,1000),(150,45,60)).save(image)
    data = {'images':[{'id':1,'file_name':image.name,'width':1000,'height':1000}],
            'categories':[{'id':1,'name':'defect'}],
            'annotations':[{'image_id':1,'category_id':1,'bbox':[.390625,.390625,39.0625,39.0625]}]}
    dataset = DetectionDataset(images_dir=tmp_path, annotation_data=data, image_size=(256,256), class_names=['defect'])
    dataset.evaluation_split = 'test'
    monkeypatch.setattr(routes, '_manifest_evaluation_dataset', lambda *args: dataset)
    monkeypatch.setattr(routes.torch, 'load', lambda *args, **kwargs: {'model_state_dict':{},'classes':['defect']})
    monkeypatch.setattr(routes, 'checkpoint_detection_num_classes', lambda *args: 2)
    monkeypatch.setattr(routes, 'create_detection_model', lambda **kwargs: OwnedTensorDetector())
    observed = routes._evaluate_detection(tmp_path/'unused.pt', {'classes':['defect'],'image_size':[256,256]}, tmp_path, torch.device('cpu'), metric_contract=contract)
    if contract == DETECTION_AP_LEGACY:
        assert observed['metrics']['mAP_50_95'] == .4
    else:
        assert observed['metrics']['mAP_50_95'] == .5
        assert observed['test_predictions'][0]['object_evidence']['truth'][0]['box'] == [.390625,.390625,39.453125,39.453125]


def test_original_geometry_all_no_ground_truth_remains_explicitly_unavailable(tmp_path, monkeypatch):
    from backend.api import routes_evaluation as routes
    from backend.engine.dataset_loaders import DetectionDataset
    image = tmp_path/'normal.png';Image.new('RGB',(1000,1000),(30,45,60)).save(image)
    data = {'images':[{'id':1,'file_name':image.name,'width':1000,'height':1000}],
            'categories':[{'id':1,'name':'defect'}],'annotations':[]}
    dataset = DetectionDataset(images_dir=tmp_path, annotation_data=data, image_size=(256,256), class_names=['defect']);dataset.evaluation_split='test'
    monkeypatch.setattr(routes, '_manifest_evaluation_dataset', lambda *args: dataset)
    monkeypatch.setattr(routes.torch, 'load', lambda *args, **kwargs: {'model_state_dict':{},'classes':['defect']})
    monkeypatch.setattr(routes, 'checkpoint_detection_num_classes', lambda *args: 2)
    monkeypatch.setattr(routes, 'create_detection_model', lambda **kwargs: OwnedTensorDetector())
    observed = routes._evaluate_detection(tmp_path/'unused.pt', {'classes':['defect'],'image_size':[256,256]}, tmp_path, torch.device('cpu'))
    assert observed['metrics']['mAP_50'] is None and observed['metrics']['mAP_50_95'] is None
    assert observed['metrics']['detection_ap_available'] is False


from backend.tests.test_remote_evaluation_cohort import common_workspace, request_common, write_json


@pytest.mark.parametrize('common_workspace',['detection'],indirect=True)
@pytest.mark.parametrize('empty_truth', [False, True])
def test_actual_common_validator_recomputes_exact_original_geometry_and_null_availability(common_workspace, monkeypatch, empty_truth):
    from backend.api import routes_evaluation as routes
    client, _, source, _, _, _, _ = common_workspace
    for index, name in enumerate(('normal', 'defect')):
        image=source/'images/test'/f'{name}.png'
        Image.new('RGB',(1000,1000),(30+index*120,45,60)).save(image)
        shapes=[] if empty_truth or index==0 else [{'label':'defect','shape_type':'rectangle','points':[[3.125,3.125],[315.625,315.625]]}]
        write_json(image.with_suffix('.json'), {'imagePath':image.name,'imageWidth':1000,'imageHeight':1000,'shapes':shapes})
    version=client.post('/api/dataset/versions',json={'name':'Owned AP canonical geometry'}).json()
    monkeypatch.setattr(routes,'create_detection_model',lambda **kwargs: OwnedTensorDetector())
    response=request_common(common_workspace,evaluation_dataset_version_id=version['id'])
    assert response.status_code == 200, response.text
    observed=response.json()
    assert observed['metrics']['detection_metric_contract'] == DETECTION_AP_V2
    assert observed['metrics']['mAP_50_95'] == (None if empty_truth else .5)
    assert observed['metrics']['detection_ap_available'] is (not empty_truth)


@pytest.mark.parametrize('common_workspace',['detection'],indirect=True)
def test_v2_independent_truth_rejects_even_tiny_worker_evidence_drift(common_workspace, monkeypatch):
    import json
    from backend.tests.test_remote_evaluation_cohort import digest
    from backend.api import routes_evaluation as routes
    _, _, _, _, _, remote, _ = common_workspace
    monkeypatch.setattr(routes,'create_detection_model',lambda **kwargs: OwnedTensorDetector())
    original=remote.launch
    def tampered(selected, argv, run_id):
        handle=original(selected,argv,run_id)
        path=remote.root/'runs'/run_id/'outputs/eval_results.json'
        payload=json.loads(path.read_text())
        row=next(r for r in payload['test_predictions'] if r['object_evidence']['truth'])
        row['object_evidence']['truth'][0]['box'][0]+=1e-6
        write_json(path,payload)
        manifest_path=path.parent.parent/'artifacts.json';manifest=json.loads(manifest_path.read_text())
        manifest['artifacts'][0].update(size=path.stat().st_size,sha256=digest(path));write_json(manifest_path,manifest)
        return handle
    monkeypatch.setattr(remote,'launch',tampered)
    response=request_common(common_workspace)
    assert response.status_code == 502, response.text
