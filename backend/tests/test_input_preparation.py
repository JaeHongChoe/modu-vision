"""CPU-only deterministic training recipe contracts; no weights or model calls."""
import json
import numpy as np
import pytest
from PIL import Image


def recipe(ops):
    return {'version': 1, 'operations': ops}


def test_ordered_recipe_has_stable_hash_and_rejects_unknown_operations():
    from backend.engine.input_preparation import canonical_recipe, recipe_sha256
    a = recipe([{'op': 'rotate90', 'turns': 1}, {'op': 'resize', 'width': 32, 'height': 48}])
    assert canonical_recipe(a, 'classification') == a
    assert recipe_sha256(a) == recipe_sha256(json.loads(json.dumps(a, sort_keys=True)))
    assert recipe_sha256(a) != recipe_sha256(recipe(list(reversed(a['operations']))))
    with pytest.raises(ValueError, match='Unsupported'):
        canonical_recipe(recipe([{'op': 'learned_rotation'}]), 'classification')
    with pytest.raises(ValueError, match='classification.*segmentation'):
        canonical_recipe(a, 'detection')
    with pytest.raises(ValueError):
        canonical_recipe(recipe([{'op':'brightness', 'factor': float('nan')}]), 'classification')


def test_crop_rotate_resize_keeps_image_mask_and_inverse_geometry_synchronized():
    from backend.engine.input_preparation import prepare_input, restore_mask
    mask = np.zeros((8, 12), dtype=np.uint8)
    mask[2:6, 3:9] = 2
    rgb = np.repeat((mask * 100)[..., None], 3, axis=2)
    rules = recipe([{'op':'crop', 'roi':[.25,.25,.75,.75]}, {'op':'rotate90','turns':1}, {'op':'resize','width':32,'height':48}])
    image, target, geometry = prepare_input(rgb, rules, task='segmentation', target=mask)
    assert image.shape == (48,32,3) and target.shape == (48,32)
    assert set(np.unique(target)) == {2}
    restored = restore_mask(target, geometry)
    np.testing.assert_array_equal(restored, mask)
    assert geometry['source_size'] == [12,8]
    # No original array or labels are changed.
    assert mask.sum() == 48


def test_classification_train_and_val_use_same_fixed_input_before_train_only_augmentation(tmp_path):
    from backend.engine.dataset_loaders import ClassificationDataset
    rules = recipe([{'op':'crop','roi':[0,0,.5,1]}, {'op':'resize','width':32,'height':32}])
    for split in ('train','val'):
        directory=tmp_path/split/'ok';directory.mkdir(parents=True)
        pixels=np.zeros((32,64,3),dtype=np.uint8);pixels[:,:32]=255
        Image.fromarray(pixels).save(directory/'sample.png')
    train=ClassificationDataset(tmp_path,split='train',image_size=(32,32),input_recipe=rules)
    val=ClassificationDataset(tmp_path,split='val',image_size=(32,32),input_recipe=rules)
    image,_=train[0]; validation,_=val[0]
    assert image.shape == (3,32,32)
    assert image.equal(validation) and float(image.min()) == 1


def test_model_metadata_cannot_change_checkpoint_bound_recipe():
    from backend.engine.input_preparation import bind_recipe, saved_recipe
    rules=recipe([{'op':'brightness','factor':1.5}])
    binding=bind_recipe(rules,'classification')
    assert saved_recipe(binding,binding,'classification') == rules
    changed=bind_recipe(recipe([{'op':'brightness','factor':2}]),'classification')
    with pytest.raises(ValueError,match='differ'):
        saved_recipe(changed,binding,'classification')
    assert saved_recipe({}, {}, 'classification') is None


def test_exported_preprocessor_reuses_recipe_without_double_normalization(monkeypatch):
    import sys
    from backend.engine import input_preparation
    from backend.engine.exporter import generate_standalone_infer_py
    monkeypatch.setitem(sys.modules,'input_preparation',input_preparation)
    scope={'__name__':'generated_recipe_runtime'}
    exec(generate_standalone_infer_py(),scope)
    rules=recipe([{'op':'crop','roi':[0,0,.5,1]}, {'op':'rotate90','turns':1}])
    rgb=np.zeros((32,64,3),dtype=np.uint8);rgb[:,:32,0]=255
    expected=input_preparation.prepare_input(rgb,rules,image_size=(32,32))[0].transpose(2,0,1)/255
    batch,width,height=scope['preprocess_image'](rgb[...,::-1].copy(),target_size=(32,32),mean=[0,0,0],std=[1,1,1],input_recipe=rules)
    np.testing.assert_allclose(batch[0],expected)
    assert (width,height)==(64,32)


def test_preview_is_read_only_scoped_to_active_source_and_shows_transformed_target(tmp_path,monkeypatch):
    from backend.engine.input_preparation_preview import preview_input
    from backend.api import routes_project
    source=tmp_path/'source';source.mkdir();image=source/'a.png'
    Image.new('RGB',(64,32),'white').save(image)
    monkeypatch.setattr(routes_project,'get_current_project',lambda request:{'source_dataset_dir':str(source)})
    value=preview_input(None, task='classification', image_path=str(image), input_recipe=recipe([{'op':'rotate90','turns':1}]))
    assert value['source_size']==[64,32] and value['prepared_size']==[32,64]
    assert value['prepared_image'].startswith('data:image/png;base64,')
    from hashlib import sha256
    assert value['source_sha256']==sha256(image.read_bytes()).hexdigest()
    assert list(source.iterdir())==[image]
    with pytest.raises(ValueError,match='active source'):
        preview_input(None,task='classification',image_path=str(tmp_path/'outside.png'),input_recipe=recipe([]))


@pytest.mark.parametrize('task',['classification','segmentation'])
def test_inspection_node_reuses_saved_recipe_and_restores_mask(task,monkeypatch):
    import torch
    from backend.engine.flowchart_engine import FlowchartEngine,FlowNode,FlowNodeData
    from backend.engine.input_preparation import prepare_input
    rules=recipe([{'op':'crop','roi':[0,0,.5,1]},{'op':'rotate90','turns':1}])
    rgb=np.zeros((32,64,3),dtype=np.uint8);rgb[4:12,8:20,0]=255
    observed=[]
    class ToyModel(torch.nn.Module):
        def forward(self,image):
            observed.append(image.detach().cpu())
            if task=='classification':return torch.tensor([[0.,10.]])
            signal=image[:,0:1]*20-10
            return torch.cat([-signal,signal],dim=1)
    engine=FlowchartEngine(device='cpu',checkpoint_resolver=lambda *_:None)
    key=(task,'recipe-fixture','fast')
    engine._model_input_sizes[key]=(32,32)
    engine._model_classes[key]=['background','crack'] if task=='segmentation' else ['OK','Defect']
    engine._model_input_recipes={key:rules}
    monkeypatch.setattr(engine,'_get_inspection_model',lambda **_: (ToyModel(),True))
    node=FlowNode(id='input-recipe-fixture',position={'x':0,'y':0},data=FlowNodeData(label='recipe fixture',node_type='inspection',task=task,model_job_id='recipe-fixture',threshold=.5,crop_padding=0,params={'min_defect_area_px':1}))
    results,_,_=engine._inspect_crops(rgb,[{'id':'full_image','label':'sample','bbox':[0,0,64,32]}],node)
    expected=prepare_input(rgb,rules,task=task,image_size=(32,32))[0]
    np.testing.assert_allclose(observed[0][0].numpy(),expected.transpose(2,0,1)/255)
    assert len(observed)==1 and observed[0].shape[0]==1, 'Recipe-bound full images run once, never raw-image tiling'
    if task=='segmentation':
        assert results[0].defect_area_px==96
        np.testing.assert_array_equal(results[0]._defect_mask,rgb[...,0]>0)


def test_preview_includes_final_model_size_after_ordered_operations(tmp_path,monkeypatch):
    from backend.engine.input_preparation_preview import preview_input
    from backend.api import routes_project
    source=tmp_path/'source';source.mkdir();image=source/'a.png';Image.new('RGB',(64,32),'white').save(image)
    monkeypatch.setattr(routes_project,'get_current_project',lambda request:{'source_dataset_dir':str(source)})
    value=preview_input(None,task='classification',image_path=str(image),input_recipe=recipe([{'op':'rotate90','turns':1}]),image_size=[32,48])
    assert value['prepared_size']==[32,48]
    assert value['geometry']['steps'][-1]=={'op':'resize','before':[32,64]}


def test_exported_segmentation_exposes_inverse_mask_and_source_area(monkeypatch):
    import sys,base64,io
    from types import SimpleNamespace
    from backend.engine import input_preparation
    from backend.engine.exporter import generate_standalone_infer_py
    monkeypatch.setitem(sys.modules,'input_preparation',input_preparation)
    scope={'__name__':'generated_recipe_runtime'};exec(generate_standalone_infer_py(),scope)
    inspector=scope['StandaloneInspector'].__new__(scope['StandaloneInspector'])
    inspector.task='segmentation';inspector.classes=['background','crack'];inspector.threshold=.5
    inspector.model_format='onnx';inspector.resolution=(32,32);inspector.mean=[0,0,0];inspector.std=[1,1,1]
    inspector.segmentation_mode='resize_single';inspector.min_defect_area_px=1;inspector.input_name='input'
    inspector.input_recipe=recipe([{'op':'crop','roi':[0,0,.5,1]},{'op':'rotate90','turns':1}])
    rgb=np.zeros((32,64,3),dtype=np.uint8);rgb[4:12,8:20,0]=255
    prepared=input_preparation.prepare_input(rgb,inspector.input_recipe,task='segmentation',image_size=(32,32))[0]
    signal=prepared[...,0].astype(np.float32)/255*20-10
    inspector.session=SimpleNamespace(run=lambda *_:[np.stack([-signal,signal])[None]])
    result=inspector.inspect(rgb[...,::-1].copy())
    assert result['defect_area_px']==96
    mask=np.asarray(Image.open(io.BytesIO(base64.b64decode(result['segmentation_mask'].split(',')[1]))))
    np.testing.assert_array_equal(mask>0,rgb[...,0]>0)


def test_checkpoint_writes_recipe_binding_into_both_artifacts_without_training(tmp_path):
    import torch
    from backend.engine.trainer import UnifiedAutoMLTrainer
    rules=recipe([{'op':'rotate90','turns':1}])
    trainer=UnifiedAutoMLTrainer('classification',tmp_path/'source',tmp_path/'model',device='cpu',config_overrides={'input_recipe':rules,'backbone':'resnet18','pretrained':False})
    trainer._save_checkpoint(0,torch.nn.Linear(1,2),.1,['OK','Defect'],(32,32),0.)
    payload=torch.load(tmp_path/'model/best_model.pt',map_location='cpu',weights_only=True)
    metadata=json.loads((tmp_path/'model/model_meta.json').read_text())
    for key in ('input_recipe','input_recipe_sha256','input_preparation_contract'):
        assert payload[key]==metadata[key]
    assert metadata['training_config']['input_recipe']==rules


@pytest.mark.parametrize('task',['classification','segmentation'])
def test_cpu_evaluation_and_prediction_receive_same_prepared_pixels(task,tmp_path,monkeypatch):
    import torch
    from backend.engine import trainer
    from backend.api import routes_evaluation as evaluation
    from backend.engine.input_preparation import bind_recipe,prepare_input
    rules=recipe([{'op':'crop','roi':[0,0,.5,1]},{'op':'rotate90','turns':1},{'op':'brightness','factor':.8}])
    rgb=np.zeros((32,64,3),dtype=np.uint8);rgb[4:12,8:20,0]=255
    source=tmp_path/'source';observed=[]
    if task=='classification':
        image=source/'test/Defect/a.png';image.parent.mkdir(parents=True)
        classes=['OK','Defect']
    else:
        image=source/'images/test/a.png';image.parent.mkdir(parents=True)
        masks=source/'masks/test';masks.mkdir(parents=True)
        Image.fromarray((rgb[...,0]>0).astype(np.uint8)).save(masks/'a.png')
        classes=['background','crack']
    Image.fromarray(rgb).save(image)
    metadata={'task':task,'image_size':[32,32],'classes':classes,**bind_recipe(rules,task)}
    checkpoint=tmp_path/'model/best_model.pt';checkpoint.parent.mkdir()
    torch.save({**metadata,'model_state_dict':{}},checkpoint)
    checkpoint.with_name('model_meta.json').write_text(json.dumps(metadata))
    class ToyModel(torch.nn.Module):
        def forward(self,pixels):
            observed.append(pixels.detach().cpu())
            if task=='classification':return torch.tensor([[0.,10.]])
            signal=pixels[:,0:1]*20-10
            return torch.cat([-signal,signal],dim=1)
    factory='create_classification_model' if task=='classification' else 'build_segmentation_model'
    monkeypatch.setattr(trainer,factory,lambda **_:ToyModel())
    monkeypatch.setattr(evaluation,factory,lambda **_:ToyModel())
    function=evaluation._evaluate_classification if task=='classification' else evaluation._evaluate_segmentation
    result=function(checkpoint,metadata,source,torch.device('cpu'))
    prediction=trainer.infer(task,checkpoint,rgb,device='cpu')
    expected=prepare_input(rgb,rules,task=task,image_size=(32,32))[0].transpose(2,0,1)/255
    assert len(observed)>=2
    for pixels in observed:np.testing.assert_allclose(pixels[0].numpy(),expected,atol=1e-7)
    assert result['test_predictions'] and prediction.task==task
    if task=='segmentation':
        mapping=result['test_predictions'][0]['pixel_evidence']['mapping']
        assert mapping['kind']=='fixed_input_recipe' and mapping['source_size']==[64,32]
        import base64,io
        mask=np.asarray(Image.open(io.BytesIO(base64.b64decode(prediction.predictions['mask_candidates'][0]['mask_rle'].split(',')[1]))))
        np.testing.assert_array_equal(mask[...,3]>0,rgb[...,0]>0)


@pytest.mark.parametrize('task',['detection','anomaly','patch_classification','rotation','ocr','rotated_detection','enhancement','defect_gan'])
def test_unsupported_families_refuse_nonempty_shared_recipe(task):
    from backend.engine.model_backbones import validate_training_controls
    with pytest.raises(ValueError,match='classification and segmentation'):
        validate_training_controls(task,'fast',{'input_recipe':recipe([{'op':'rotate90','turns':1}])})


def test_saved_recipe_does_not_accept_partial_hash_or_implementation_binding():
    from backend.engine.input_preparation import bind_recipe,saved_recipe
    binding=bind_recipe(recipe([{'op':'rotate90','turns':1}]),'classification')
    for key in ('input_recipe','input_recipe_sha256','input_preparation_contract'):
        partial={k:v for k,v in binding.items() if k!=key}
        with pytest.raises(ValueError):saved_recipe(partial,binding,'classification')


def test_flow_model_loading_validates_and_caches_checkpoint_bound_recipe(tmp_path,monkeypatch):
    import torch
    from backend.engine import flowchart_engine,model_runtime
    from backend.engine.input_preparation import bind_recipe
    rules=recipe([{'op':'rotate90','turns':1}])
    metadata={'task':'classification','classes':['OK','Defect'],'image_size':[32,32],**bind_recipe(rules,'classification')}
    checkpoint=tmp_path/'best_model.pt';torch.save({**metadata,'model_state_dict':{}},checkpoint)
    checkpoint.with_name('model_meta.json').write_text(json.dumps(metadata))
    class ToyModel(torch.nn.Module):
        def forward(self,image):return torch.zeros((image.shape[0],2))
    monkeypatch.setattr(flowchart_engine,'create_classification_model',lambda **_:ToyModel())
    monkeypatch.setattr(model_runtime,'runtime_model',lambda model,*_:model)
    engine=flowchart_engine.FlowchartEngine(device='cpu',checkpoint_resolver=lambda *_:checkpoint)
    _,trained=engine._get_inspection_model(task='classification',job_id='recipe-fixture')
    key=engine._cache_key('classification','recipe-fixture','fast',checkpoint)
    assert trained and engine._model_input_recipes[key]==rules
    metadata['input_recipe_sha256']='0'*64;checkpoint.with_name('model_meta.json').write_text(json.dumps(metadata))
    with pytest.raises(RuntimeError,match='input recipe'):
        engine._get_inspection_model(task='classification',job_id='recipe-fixture')
