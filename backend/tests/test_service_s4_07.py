"""OBB manifest/adapter integration with a fake external runtime, no downloads."""
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import torch
from PIL import Image
from backend.engine import rotated_detection as rotated


def dataset(tmp_path, *, crowded=33, direction=False):
    rows=[]
    for i,split in enumerate(('train','val','test')):
        Image.fromarray(np.full((100,100,3),50+i,dtype=np.uint8)).save(tmp_path/f'{i}.png')
        objects=[] if split=='val' else [{'label':'가 defect','box':{'cx':30,'cy':30,'width':12,'height':8,'angle_deg':-20},
            **({'direction_deg':270} if direction else {})} for _ in range(crowded if split=='train' else 1)]
        rows.append({'image':f'{i}.png','split':split,'objects':objects})
    return rows


def test_manifest_keeps_empty_background_hashes_and_more_than_32_objects(tmp_path):
    manifest=rotated.write_rotated_manifest(tmp_path,dataset(tmp_path))
    assert manifest.provenance['source_image_count'] == 3
    assert manifest.provenance['object_count'] == 34
    assert len(manifest.images) == 3 and manifest.images[1].image == '1.png'
    assert '1.png' in manifest.provenance['source_sha256']
    from backend.api.routes_rotated_detection import _manifest_result
    result=_manifest_result(manifest)
    assert result['samples'][1]['objects'] == []
    with pytest.raises(ValueError,match='fixed.*slot|fixed_slot'):
        rotated.train_rotated_detector(tmp_path,tmp_path/'legacy',epochs=1)


class ExternalYOLO:
    """Only replaces the optional third party boundary; production conversion stays real."""
    def __init__(self,path,task=None):
        self.task='obb';self.path=Path(path);self.trainer=None
    def add_callback(self,event,callback): pass
    def train(self,**options):
        data=json.loads(Path(options['data']).read_text())
        assert data['names'] == {'0':'가 defect'}
        labels=Path(data['path'])/'labels'
        assert (labels/'val/000001.txt').read_text() == ''
        assert len((labels/'train/000000.txt').read_text().splitlines()) == 33
        output=Path(options['project'])/options['name'];(output/'weights').mkdir(parents=True)
        best=output/'weights/best.pt';best.write_bytes(b'external-native-weights')
        self.trainer=SimpleNamespace(best=best,epoch=0)
        return SimpleNamespace(box=SimpleNamespace(map=.25,map50=.5))
    def val(self,**options):
        assert options['split']=='test'
        return SimpleNamespace(box=SimpleNamespace(map=.25,map50=.5))
    def predict(self,source,**options):
        # Third party result format documented by Ultralytics; clockwise pi/2 is axial -90.
        obb=SimpleNamespace(xywhr=torch.tensor([[40.,30.,20.,10.,np.pi/2]]),
                            xyxyxyxy=torch.tensor([[[45.,20.],[45.,40.],[35.,40.],[35.,20.]]]),
                            cls=torch.tensor([0.]),conf=torch.tensor([.9]))
        return [SimpleNamespace(obb=obb,names={0:'가 defect'})]


def test_opt_in_yolo_dispatches_train_eval_and_runtime_with_original_geometry(tmp_path,monkeypatch):
    rotated.write_rotated_manifest(tmp_path,dataset(tmp_path))
    local=tmp_path/'local-obb.pt';local.write_bytes(b'acknowledged-local-native-model')
    import backend.engine.yolo_obb_adapter as adapter
    monkeypatch.setattr(adapter,'_runtime',lambda:ExternalYOLO)
    output=tmp_path/'model'
    receipt=rotated.train_rotated_detector(tmp_path,output,epochs=1,recipe={'adapter':'ultralytics_yolo_obb','model_path':str(local),'trust_native_weights':True})
    assert receipt['adapter']=='ultralytics_yolo_obb'
    assert receipt['license']['distribution_status']=='pending_review'
    result=rotated.predict_rotated_array(output/'best_model.pt',np.zeros((100,100,3),dtype=np.uint8))
    assert result['detections'][0]['box'] == pytest.approx({'cx':40.,'cy':30.,'width':20.,'height':10.,'angle_deg':-90.},abs=1e-5)
    assert result['detections'][0]['axis_aligned_box'] == [35.,20.,45.,40.]
    assert result['angle_convention']=='clockwise_degrees_axial_180'
    assert 'direction_deg' not in result['detections'][0]
    evaluation=rotated.evaluate_rotated_detector(output/'best_model.pt',tmp_path)
    assert evaluation['sample_count']==1 and evaluation['ground_truth_objects']==1
    assert evaluation['runtime_metrics']['mAP_50']==.5


def test_yolo_refuses_implicit_model_download_and_independent_direction(tmp_path):
    rotated.write_rotated_manifest(tmp_path,dataset(tmp_path,direction=True))
    with pytest.raises(ValueError,match='local.*model|regular.*model'):
        rotated.train_rotated_detector(tmp_path,tmp_path/'bad',recipe={'adapter':'ultralytics_yolo_obb','model_path':'yolo11n-obb.pt'})
    local=tmp_path/'local.pt';local.write_bytes(b'acknowledged-local-native-model')
    with pytest.raises(ValueError,match='direction'):
        rotated.train_rotated_detector(tmp_path,tmp_path/'bad',recipe={'adapter':'ultralytics_yolo_obb','model_path':str(local)})


def test_yolo_training_fails_if_selected_model_changes_during_the_run(tmp_path,monkeypatch):
    rotated.write_rotated_manifest(tmp_path,dataset(tmp_path))
    local=tmp_path/'local.pt';local.write_bytes(b'acknowledged-local-native-model')
    class ChangedModel(ExternalYOLO):
        def train(self,**options):
            result=super().train(**options);local.write_text('changed');return result
    import backend.engine.yolo_obb_adapter as adapter
    monkeypatch.setattr(adapter,'_runtime',lambda:ChangedModel)
    with pytest.raises(ValueError,match='selected.*model.*changed'):
        rotated.train_rotated_detector(tmp_path,tmp_path/'model',recipe={'adapter':'ultralytics_yolo_obb','model_path':str(local),'trust_native_weights':True})


def test_yolo_polygon_export_keeps_pixels_and_rejects_out_of_bounds_truth(tmp_path):
    rows=dataset(tmp_path,crowded=1)
    rows[0]['objects'][0]['box']={'cx':50,'cy':50,'width':20,'height':10,'angle_deg':0}
    manifest=rotated.write_rotated_manifest(tmp_path,rows)
    import backend.engine.yolo_obb_adapter as adapter
    adapter.export_dataset(manifest,tmp_path/'derived')
    assert (tmp_path/'derived/labels/train/000000.txt').read_text().strip() == '0 0.400000000 0.550000000 0.400000000 0.450000000 0.600000000 0.450000000 0.600000000 0.550000000'
    rows[0]['objects'][0]['box']['cx']=0
    with pytest.raises(ValueError,match='outside'):rotated.write_rotated_manifest(tmp_path,rows)


def test_rotated_route_allows_empty_and_large_objects_without_changing_default_adapter():
    from backend.api.routes_rotated_detection import RotatedSampleInput, TrainRequest
    assert RotatedSampleInput(image='x.png',split='val',objects=[]).objects == []
    assert len(RotatedSampleInput(image='x.png',split='train',objects=[{}]*33).objects)==33
    assert TrainRequest(dataset_path='/data').recipe.adapter == 'fixed_slot_cnn'


def test_yolo_completed_job_uses_safe_checkpoint_contract_for_flow_and_packages(tmp_path,monkeypatch):
    from backend.tests.test_rotated_detection_api import _client,_await_terminal
    from backend.engine.specialized_models import resolve_specialized_checkpoint
    import backend.engine.yolo_obb_adapter as adapter
    monkeypatch.setattr(adapter,'_runtime',lambda:ExternalYOLO)
    source=tmp_path/'source';source.mkdir();rows=dataset(source)
    client=_client(tmp_path)
    project=client.post('/api/project/create',json={'name':'OBB opt in','task':'detection'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    prepared=client.post('/api/rotated-detection/prepare',json={'source_dataset_path':str(source),'samples':rows})
    assert prepared.status_code==200,prepared.text
    assert prepared.json()['samples'][1]['objects']==[]
    local=tmp_path/'local.pt';local.write_bytes(b'acknowledged-local-native-model')
    submitted=client.post('/api/rotated-detection/train',json={'dataset_path':prepared.json()['dataset_path'],'epochs':1,
        'recipe':{'adapter':'ultralytics_yolo_obb','model_path':str(local),'trust_native_weights':True}})
    assert submitted.status_code==200,submitted.text
    state=_await_terminal(client,submitted.json()['job_id'])
    assert state['status']=='completed',state
    path,meta=resolve_specialized_checkpoint(project['models_dir'],state['job_id'],'rotated_detection',source_dataset_path=str(source))
    payload=torch.load(path,map_location='cpu',weights_only=True)
    assert payload['task']=='rotated_detection' and payload['adapter']=='ultralytics_yolo_obb'
    assert isinstance(payload['native_model_bytes'],bytes)
    prediction=client.post('/api/rotated-detection/predict',json={'job_id':state['job_id'],'image_path':str(source/'2.png')})
    assert prediction.status_code==200,prediction.text
    assert prediction.json()['coordinate_space']=='original_image_pixels'


def test_native_weight_metadata_does_not_authorize_an_untrusted_host(tmp_path,monkeypatch):
    rotated.write_rotated_manifest(tmp_path,dataset(tmp_path))
    local=tmp_path/'local.pt';local.write_bytes(b'acknowledged-local-native-model')
    import backend.engine.yolo_obb_adapter as adapter
    monkeypatch.setattr(adapter,'_runtime',lambda:ExternalYOLO)
    output=tmp_path/'model'
    rotated.train_rotated_detector(tmp_path,output,recipe={'adapter':'ultralytics_yolo_obb','model_path':str(local),'trust_native_weights':True})
    monkeypatch.setattr(adapter,'_TRUSTED_NATIVE_SHA256',set(),raising=False)
    monkeypatch.delenv('MODU_VISION_TRUSTED_YOLO_OBB_SHA256',raising=False)
    with pytest.raises(ValueError,match='native.*trust|trust.*native'):
        rotated.predict_rotated_array(output/'best_model.pt',np.zeros((100,100,3),dtype=np.uint8))
    meta=json.loads((output/'model_meta.json').read_text())
    monkeypatch.setenv('MODU_VISION_TRUSTED_YOLO_OBB_SHA256',meta['native_model_sha256'])
    assert rotated.predict_rotated_array(output/'best_model.pt',np.zeros((100,100,3),dtype=np.uint8))['adapter']=='ultralytics_yolo_obb'
    monkeypatch.setenv('MODU_VISION_TRUSTED_YOLO_OBB_SHA256','*')
    with pytest.raises(ValueError,match='wildcard'):rotated.predict_rotated_array(output/'best_model.pt',np.zeros((100,100,3),dtype=np.uint8))


def test_training_requires_explicit_acknowledgement_before_native_runtime(tmp_path,monkeypatch):
    rotated.write_rotated_manifest(tmp_path,dataset(tmp_path))
    local=tmp_path/'local.pt';local.write_bytes(b'untrusted')
    import backend.engine.yolo_obb_adapter as adapter
    monkeypatch.setattr(adapter,'_runtime',lambda:pytest.fail('Runtime must not load without trust acknowledgement'))
    with pytest.raises(ValueError,match='trust acknowledgement'):
        rotated.train_rotated_detector(tmp_path,tmp_path/'model',recipe={'adapter':'ultralytics_yolo_obb','model_path':str(local)})
