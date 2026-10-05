"""Actual legacy dense decoders reconstruct offline; no pretrained quality claim."""
import pytest
import torch
from backend.engine.segmentation import build_segmentation_model
from backend.engine.exporter import load_checkpoint_and_reconstruct_model

@pytest.mark.parametrize('name',['unet','deeplab_mobilenet'])
def test_existing_dense_decoder_checkpoint_keeps_logits_without_download(tmp_path,monkeypatch,name):
    def refused(*args,**kwargs):raise AssertionError('Offline checkpoint must not download')
    monkeypatch.setattr(torch.hub,'download_url_to_file',refused)
    torch.set_num_threads(1);torch.manual_seed(112)
    model=build_segmentation_model(name,3,pretrained=False).eval();pixels=torch.rand(1,3,64,64)
    with torch.no_grad():expected=model(pixels)
    checkpoint=tmp_path/'best_model.pt';torch.save({'task':'segmentation','model_name':name,'preset':'fast','classes':['background','scratch','stain'],'image_size':[64,64],'model_state_dict':model.state_dict()},checkpoint)
    rebuilt,metadata,anomaly=load_checkpoint_and_reconstruct_model(checkpoint)
    with torch.no_grad():actual=rebuilt(pixels)
    torch.testing.assert_close(actual,expected,rtol=0,atol=0)
    assert metadata['classes']==['background','scratch','stain'] and anomaly is None
