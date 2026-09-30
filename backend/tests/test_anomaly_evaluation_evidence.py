from pathlib import Path
import hashlib
import numpy as np
import pytest
import torch
from PIL import Image
from fastapi import HTTPException
from backend.api import routes_evaluation


def _evaluate(tmp_path,monkeypatch,mode,with_mask):
    good=tmp_path/'good.png';defect=tmp_path/'defect.png';mask=tmp_path/'mask.png'
    Image.new('RGB',(8,8),'white').save(good);Image.new('RGB',(8,8),'black').save(defect)
    pixels=np.zeros((8,8),dtype=np.uint8);pixels[:4,:4]=255;Image.fromarray(pixels).save(mask)
    class Dataset:
        def __init__(self,**kwargs):self.samples=[(good,0,None),(defect,1,mask if with_mask else None)]
        def __len__(self):return len(self.samples)
    class Detector:
        def __init__(self,**kwargs):self.calls=0
        def load_state_dict(self,state):pass
        def eval(self):pass
        def __call__(self,images):
            self.calls+=1
            return (torch.tensor([[[[.1,.1],[.1,.1]]]]) if self.calls==1 else torch.tensor([[[[.9,.2],[.2,.2]]]]),torch.tensor([.1 if self.calls==1 else .9]))
    monkeypatch.setattr(routes_evaluation,'AnomalyDataset',Dataset)
    monkeypatch.setattr(routes_evaluation,'PaDiMDetector',Detector)
    checkpoint=tmp_path/'model.pt';torch.save({'model_state_dict':{}},checkpoint)
    return routes_evaluation._evaluate_anomaly(checkpoint,{'anomaly_mode':mode,'image_size':[8,8]},tmp_path,torch.device('cpu'))


def test_anomaly_segmentation_evaluation_retains_real_masks_and_score_maps(tmp_path,monkeypatch):
    result=_evaluate(tmp_path,monkeypatch,'segmentation',True)
    assert result['metrics']['pixel_auroc']==1.0
    assert result['metrics']['pixel_evaluated_images']==2
    evidence=result['test_predictions'][1]['pixel_evidence']
    path=Path(evidence['file_path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest()==evidence['sha256']
    with np.load(path,allow_pickle=False) as archive:
        assert archive[evidence['heatmap_key']][0,0]==pytest.approx(.9)
        assert archive[evidence['mask_key']].tolist()==[[1,0],[0,0]]


def test_missing_defect_mask_cannot_be_claimed_as_segmentation_evidence(tmp_path,monkeypatch):
    with pytest.raises(HTTPException,match='masks'):_evaluate(tmp_path,monkeypatch,'segmentation',False)
    result=_evaluate(tmp_path,monkeypatch,'classification',False)
    assert result['metrics']['pixel_missing_masks']==1
    assert result['test_predictions'][1]['pixel_evidence']['mask_key'] is None
