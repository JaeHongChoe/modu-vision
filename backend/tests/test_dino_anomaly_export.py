"""Synthetic anomaly deployment keeps binary probabilities and native coverage."""
import importlib.util
import json
import math
from pathlib import Path
import numpy as np
import pytest
import torch
from backend.engine import exporter


class BinaryPatchLogits(torch.nn.Module):
    def forward(self, x):
        value = torch.sigmoid(x.mean(dim=(1,2,3)))
        return torch.stack((1.0-value, value), dim=1)


def test_dino_anomaly_standalone_keeps_patch_geometry_and_threshold(tmp_path, monkeypatch):
    metadata = {'task':'anomaly', 'classes':['good','anomaly'], 'detector_type':'dino_synthetic',
        'patch_size':32, 'stride':16, 'image_size':[32,32], 'anomaly_threshold':.8,
        'map_semantics':'patch_score'}
    checkpoint = tmp_path/'best_model.pt'
    torch.save(metadata, checkpoint)
    monkeypatch.setattr(exporter, 'locate_checkpoint', lambda _: checkpoint)
    monkeypatch.setattr(exporter, 'load_checkpoint_and_reconstruct_model', lambda _: (BinaryPatchLogits(), metadata, object()))
    receipt = exporter.export_runtime_package(job_id='job_1234567890_abcdef', export_format='torchscript', output_base_dir=tmp_path/'out')
    directory = tmp_path/'out'/receipt['package_name']
    config = json.loads((directory/'config.json').read_text())
    monkeypatch.syspath_prepend(str(directory))
    assert config['task'] == 'anomaly'
    assert config['detector_type'] == 'dino_synthetic'
    assert config['normal_class'] == 'good'
    assert config['optimal_threshold'] == .8
    spec = importlib.util.spec_from_file_location('native_anomaly_infer', directory/'infer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.StandaloneInspector(config_path=str(directory/'config.json')).inspect(np.full((80,96,3),255,dtype=np.uint8))
    assert result['confidence_score'] == pytest.approx(1/(1+math.exp(-1)), abs=1e-4)
    assert result['verdict'] == 'OK'
    assert result['task'] == 'anomaly'
    assert result['map_semantics'] == 'patch_score'
    assert result['patches'][-1]['box'] == [64,48,96,80]
    assert len(result['patches']) == 20
    small = np.zeros((16,16,3),dtype=np.uint8)
    small[:,:8] = 255
    padded = module.StandaloneInspector(config_path=str(directory/'config.json')).inspect(small)
    assert padded['confidence_score'] == pytest.approx(1/(1+math.exp(-.25)), abs=1e-5)


def test_binary_export_pools_spatial_tokens_and_normalizes_once():
    from types import SimpleNamespace
    from backend.engine.anomaly.dino_export import DinoSyntheticPatchExport
    class Encoder(torch.nn.Module):
        def forward_features(self, rgb):
            cls = rgb.mean(dim=(1,2,3))
            return torch.stack((cls, torch.full_like(cls,99), torch.full_like(cls,.1), torch.full_like(cls,.3)), dim=1).unsqueeze(-1)
    head = torch.nn.Linear(2,1, bias=False)
    with torch.no_grad(): head.weight.copy_(torch.tensor([[1.,2.]]))
    source = SimpleNamespace(model=SimpleNamespace(encoder=Encoder(), head=head, num_prefix_tokens=2,
        input_mean=torch.full((1,3,1,1),.25), input_std=torch.full((1,3,1,1),.25)))
    exported = DinoSyntheticPatchExport(source)
    logits = exported(torch.full((2,3,32,32),.5))
    expected = torch.sigmoid(torch.tensor(1.4)).item()
    assert logits[:,0].tolist() == pytest.approx([1.-expected,1.-expected])
    assert logits[:,1].tolist() == pytest.approx([expected,expected])
    traced = torch.jit.trace(exported, torch.full((1,3,32,32),.5))
    assert traced(torch.full((3,3,32,32),.5)).shape == (3,2)


@pytest.mark.parametrize('export_format', ['torchscript', 'onnx'])
def test_export_preserves_normal_verdict_at_calibration_equality(tmp_path, monkeypatch, export_format):
    from types import SimpleNamespace
    from backend.engine.anomaly.dino_export import DinoSyntheticPatchExport
    class Encoder(torch.nn.Module):
        def forward_features(self, rgb):
            return rgb.mean(dim=(1,2,3)).reshape(-1,1,1).expand(-1,2,1)
    head = torch.nn.Linear(2,1)
    with torch.no_grad():
        head.weight.zero_()
        head.bias.fill_(-1.23)
    source = SimpleNamespace(model=SimpleNamespace(encoder=Encoder(), head=head, num_prefix_tokens=1,
        input_mean=torch.zeros(1,3,1,1),input_std=torch.ones(1,3,1,1)))
    threshold = torch.sigmoid(torch.tensor(-1.23)).item()
    metadata = {'task':'anomaly','classes':['good','anomaly'],'detector_type':'dino_synthetic',
        'patch_size':32,'stride':16,'image_size':[32,32],'anomaly_threshold':threshold}
    checkpoint = tmp_path/'best_model.pt'
    torch.save(metadata,checkpoint)
    monkeypatch.setattr(exporter,'locate_checkpoint',lambda _:checkpoint)
    monkeypatch.setattr(exporter,'load_checkpoint_and_reconstruct_model',lambda _: (DinoSyntheticPatchExport(source),metadata,source))
    receipt = exporter.export_runtime_package('job_1234567890_abcdef',export_format=export_format,output_base_dir=tmp_path/'out')
    directory = Path(receipt['package_path'])
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location('equality_anomaly_infer',directory/'infer.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    inspector = module.StandaloneInspector(config_path=str(directory/'config.json'))
    image = np.zeros((32,32,3),dtype=np.uint8)
    result = inspector.inspect(image)
    assert result['defect_score'] == pytest.approx(threshold,abs=1e-7)
    if export_format == 'torchscript': assert result['defect_score'] == threshold
    assert result['verdict'] == 'OK'
    assert result['threshold'] == threshold and result['threshold_roundoff_ulps'] == 1
    one_ulp = np.nextafter(np.float32(threshold),np.float32(np.inf))
    two_ulps = np.nextafter(one_ulp,np.float32(np.inf))
    assert result['decision_threshold'] == float(one_ulp)
    if export_format == 'torchscript':
        for score,verdict in [(one_ulp,'OK'),(two_ulps,'NG')]:
            inspector.torch_model = lambda images, value=float(score): torch.tensor([[1.-value,value]],dtype=torch.float32).expand(len(images),-1)
            assert inspector.inspect(image)['verdict'] == verdict


def test_synthetic_export_rejects_input_geometry_mismatch_before_writing(tmp_path, monkeypatch):
    metadata = {'task':'anomaly','classes':['good','anomaly'],'detector_type':'dino_synthetic',
        'patch_size':32,'stride':16,'image_size':[64,32],'anomaly_threshold':.8}
    checkpoint = tmp_path/'best_model.pt'; torch.save(metadata,checkpoint)
    monkeypatch.setattr(exporter,'locate_checkpoint',lambda _:checkpoint)
    monkeypatch.setattr(exporter,'load_checkpoint_and_reconstruct_model',lambda _: (BinaryPatchLogits(),metadata,object()))
    with pytest.raises(ValueError,match='patch_size'):
        exporter.export_runtime_package('job_1234567890_abcdef',export_format='torchscript',output_base_dir=tmp_path/'out')
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('checkpoint_task,sidecar_task',[('anomaly','unknown'),('anomaly','classification'),('unknown',None)])
def test_reconstruction_rejects_unknown_or_conflicting_tasks(tmp_path,checkpoint_task,sidecar_task):
    checkpoint=tmp_path/'best_model.pt'
    torch.save({'task':checkpoint_task,'model_state_dict':{}},checkpoint)
    if sidecar_task:
        (tmp_path/'model_meta.json').write_text(json.dumps({'task':sidecar_task}))
    with pytest.raises(ValueError,match='task'):
        exporter.load_checkpoint_and_reconstruct_model(checkpoint)


def test_legacy_checkpoint_without_task_accepts_sidecar_identity(tmp_path,monkeypatch):
    model=torch.nn.Linear(2,2)
    checkpoint=tmp_path/'best_model.pt';torch.save({'model_state_dict':model.state_dict()},checkpoint)
    (tmp_path/'model_meta.json').write_text(json.dumps({'task':'classification','classes':['good','defect']}))
    monkeypatch.setattr(exporter,'create_classification_model',lambda **kwargs:torch.nn.Linear(2,2))
    restored,metadata,_=exporter.load_checkpoint_and_reconstruct_model(checkpoint)
    assert metadata['task']=='classification'
    assert torch.equal(restored.weight,model.weight)
