"""Legacy three-class checkpoints retain real offline logits and class order."""
import pytest
import torch
from backend.engine.classification import create_classification_model
from backend.engine.exporter import load_checkpoint_and_reconstruct_model

@pytest.mark.parametrize('backbone',['resnet18','convnext_tiny','efficientnet_b0'])
def test_existing_classification_checkpoints_reconstruct_without_network(tmp_path,monkeypatch,backbone):
    def refused(*args,**kwargs):raise AssertionError('Offline checkpoint must not download weights')
    monkeypatch.setattr(torch.hub,'download_url_to_file',refused)
    torch.set_num_threads(1);torch.manual_seed(110)
    model=create_classification_model(backbone,3,pretrained=False).eval()
    pixels=torch.rand(2,3,64,64);expected=model(pixels).detach()
    checkpoint=tmp_path/'best_model.pt'
    torch.save({'task':'classification','backbone':backbone,'classes':['OK','scratch','stain'],'image_size':[64,64],'model_state_dict':model.state_dict()},checkpoint)
    reconstructed,metadata,anomaly=load_checkpoint_and_reconstruct_model(checkpoint)
    torch.testing.assert_close(reconstructed(pixels),expected)
    assert metadata['classes']==['OK','scratch','stain'] and metadata['image_size']==[64,64]
    assert metadata['backbone']==backbone and anomaly is None
