"""Independent checksum checks before optional native model deserialization."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from backend.engine import yolo_obb_adapter as adapter


def test_obb_envelope_changed_after_verification_never_loads_unverified_native_bytes(tmp_path,monkeypatch):
    checkpoint=tmp_path/'best_model.pt'
    native=b'checked native model bytes'
    monkeypatch.setenv('MODU_VISION_TRUSTED_YOLO_OBB_SHA256',hashlib.sha256(native).hexdigest())
    payload={'task':'rotated_detection','version':3,'adapter':'ultralytics_yolo_obb','model_state_dict':{},
             'class_names':['defect'],'image_size':32,'dataset_sha256':'sha256:dataset',
             'recipe':adapter.OBBRecipe(adapter='ultralytics_yolo_obb',model_path='/explicit/local.pt').to_dict(),
             'native_model_bytes':native,'native_model_sha256':hashlib.sha256(native).hexdigest()}
    torch.save(payload,checkpoint)
    metadata={key:value for key,value in payload.items() if key not in {'native_model_bytes','model_state_dict'}}
    metadata['checkpoint_sha256']=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    (tmp_path/'model_meta.json').write_text(json.dumps(metadata))
    real_load=torch.load
    calls=[]
    def replace_after_verified_read(*args,**kwargs):
        value=real_load(*args,**kwargs)
        if not calls:
            changed=dict(value,native_model_bytes=b'changed unverified bytes')
            torch.save(changed,checkpoint)
        calls.append(True)
        return value
    monkeypatch.setattr(adapter.torch,'load',replace_after_verified_read)
    native_loads=[]
    def native_runtime(path,**kwargs):
        native_loads.append(Path(path).read_bytes())
        return SimpleNamespace(task='obb')
    monkeypatch.setattr(adapter,'_runtime',lambda:native_runtime)
    try:
        with adapter._model(checkpoint):pass
    except ValueError as error:
        assert any(word in str(error).lower() for word in ('checksum','hash','changed','signature'))
    assert not native_loads or native_loads == [native]


def test_fabricated_obb_envelope_cannot_authorize_pickle_native_weights(tmp_path,monkeypatch):
    checkpoint=tmp_path/'best_model.pt'
    native=b'unacknowledged native model review test 20261004'
    monkeypatch.delenv('MODU_VISION_TRUSTED_YOLO_OBB_SHA256',raising=False)
    payload={'task':'rotated_detection','version':3,'adapter':'ultralytics_yolo_obb','model_state_dict':{},
             'class_names':['defect'],'image_size':32,'dataset_sha256':'sha256:dataset',
             'recipe':adapter.OBBRecipe(adapter='ultralytics_yolo_obb',model_path='/claimed/local.pt').to_dict(),
             'native_model_bytes':native,'native_model_sha256':hashlib.sha256(native).hexdigest()}
    torch.save(payload,checkpoint)
    metadata={key:value for key,value in payload.items() if key not in {'native_model_bytes','model_state_dict'}}
    metadata['checkpoint_sha256']=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    (tmp_path/'model_meta.json').write_text(json.dumps(metadata))
    monkeypatch.setattr(adapter,'_runtime',lambda:lambda *_args,**_kwargs:pytest.fail('Unacknowledged pickle-backed weights reached native loader'))
    with pytest.raises(ValueError,match='trust|acknowledg|allowlist|opt.in'):
        with adapter._model(checkpoint):pass


def test_local_yaml_that_can_select_pretrained_weights_is_rejected_before_runtime(tmp_path,monkeypatch):
    local=tmp_path/'download-capable.yaml'
    local.write_text('nc: 1\nbackbone:\n  - [-1, 1, TorchVision, [768, convnext_tiny, DEFAULT, True, 2, True]]\n')
    recipe=adapter.OBBRecipe(adapter='ultralytics_yolo_obb',model_path=str(local),trust_native_weights=True)
    monkeypatch.setattr(adapter,'_runtime',lambda:lambda *_args,**_kwargs:pytest.fail('Local YAML reached constructor that can download weights'))
    with pytest.raises(ValueError,match=r'\.pt|YAML|yaml|weights'):
        adapter.train_yolo(None,tmp_path/'output',recipe,epochs=1,batch_size=1,image_size=32,learning_rate=.01,device='cpu')
