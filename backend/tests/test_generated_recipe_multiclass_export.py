"""Execute shipped inference template on authored logits, without models or provider calls."""
import base64
import io
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from backend.engine import input_preparation
from backend.engine.exporter import generate_standalone_infer_py
from backend.engine.segmentation_evidence import class_evidence


@pytest.mark.parametrize('channel,small,expected',[(1,False,'NG'),(2,False,'NG'),(0,False,'OK'),(2,True,'OK')])
def test_recipe_multiclass_score_mask_and_area_match_foreground_evidence(monkeypatch,channel,small,expected):
    monkeypatch.setitem(sys.modules,'input_preparation',input_preparation)
    scope={'__name__':'generated_multiclass_recipe_runtime'}
    exec(generate_standalone_infer_py(),scope)
    inspector=scope['StandaloneInspector'].__new__(scope['StandaloneInspector'])
    inspector.task='segmentation';inspector.classes=['background','crack','scratch'];inspector.threshold=.5
    inspector.model_format='onnx';inspector.resolution=(32,32);inspector.mean=[0,0,0];inspector.std=[1,1,1]
    inspector.segmentation_mode='resize_single';inspector.min_defect_area_px=8;inspector.input_name='input'
    inspector.input_recipe={'version':1,'operations':[{'op':'brightness','factor':1}]}
    logits=np.zeros((1,3,32,32),dtype=np.float32)
    if small:
        logits[:,0]=10;logits[:,0,2,3]=0;logits[:,channel,2,3]=10
    else:logits[:,channel]=10
    inspector.session=SimpleNamespace(run=lambda *_:[logits])
    source=np.zeros((32,32,3),dtype=np.uint8)
    result=inspector.inspect(source)
    exp=np.exp(logits[0]-np.max(logits[0],axis=0,keepdims=True));probabilities=exp/exp.sum(axis=0,keepdims=True)
    evidence,_,union=class_evidence(probabilities,source,[0,0,32,32],inspector.classes,.5,{})
    score=max(row['confidence'] for row in evidence if row['selected'])
    mask=np.asarray(Image.open(io.BytesIO(base64.b64decode(result['segmentation_mask'].split(',')[1]))))
    np.testing.assert_array_equal(mask,union)
    assert result['defect_area_px']==int(union.sum())
    assert result['defect_score']==round(score,4)
    assert result['verdict']==expected
    assert result['predicted_class']==('Defect' if expected=='NG' else 'OK')
