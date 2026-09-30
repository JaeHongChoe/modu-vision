import json
import torch
from PIL import Image
from backend.engine.segmentation.model import build_segmentation_model
from backend.engine.trainer import infer
from backend.api.routes_label_suggestions import _candidate_annotations


def test_segmentation_inference_retains_second_class_probability_and_lossless_mask(tmp_path):
    model=build_segmentation_model(num_classes=3,preset='fast',model_name='unet',pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters():parameter.zero_()
        head=[m for m in model.modules() if isinstance(m,torch.nn.Conv2d) and m.out_channels==3][-1]
        head.bias[2]=10
    checkpoint=tmp_path/'model.pt'
    torch.save({'model_state_dict':model.state_dict(),'task':'segmentation','classes':['background','scratch','spot'],'image_size':[32,32],'model_name':'unet','preset':'fast'},checkpoint)
    (tmp_path/'model_meta.json').write_text(json.dumps({'task':'segmentation','model_name':'unet','preset':'fast','classes':['background','scratch','spot'],'image_size':[32,32]}))
    image=tmp_path/'image.png';Image.new('RGB',(48,40),'white').save(image)
    result=infer('segmentation',checkpoint,image,device='cpu')
    assert result.confidence_score>.99
    candidates=_candidate_annotations('segmentation',result.predictions,result.confidence_score,'suggestion_test')
    assert len(candidates)==1 and candidates[0]['annotation']['label']=='spot'
    assert candidates[0]['annotation']['type']=='brush_mask' and candidates[0]['annotation']['category_id']==2
    assert candidates[0]['annotation']['mask_rle'].startswith('data:image/png;base64,')


def test_actual_segmentation_evaluation_reports_second_class_false_pixels(tmp_path):
    import numpy as np
    from backend.api.routes_evaluation import _evaluate_segmentation
    model=build_segmentation_model(num_classes=3,preset='fast',model_name='unet',pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters():parameter.zero_()
        [m for m in model.modules() if isinstance(m,torch.nn.Conv2d) and m.out_channels==3][-1].bias[2]=10
    checkpoint=tmp_path/'model.pt';torch.save({'model_state_dict':model.state_dict()},checkpoint)
    dataset=tmp_path/'data';images=dataset/'images'/'test';masks=dataset/'masks'/'test';images.mkdir(parents=True);masks.mkdir(parents=True)
    Image.new('RGB',(32,32),'white').save(images/'part.png');mask=np.zeros((32,32),np.uint8);mask[:8,:8]=1;mask[8:16,8:16]=2;Image.fromarray(mask).save(masks/'part.png')
    result=_evaluate_segmentation(checkpoint,{'classes':['background','scratch','spot'],'image_size':[32,32],'model_name':'unet','preset':'fast'},dataset,torch.device('cpu'))
    evidence=result['test_predictions'][0]['pixel_evidence']
    assert evidence['per_class']['scratch']['fn']==64
    assert evidence['per_class']['spot']['tp']==64 and evidence['per_class']['spot']['fp']==960
    assert evidence['prediction_mask'].startswith('data:image/png;base64,')
    import pytest
    assert result['metrics']['per_class_iou']['spot']==pytest.approx(64/1024)


def test_saved_multiclass_labelme_holdout_reevaluation_keeps_native_pixels(tmp_path):
    import numpy as np
    from pathlib import Path
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.engine.model_operations import project_scope
    from backend.api.routes_dataset import _write_split_manifest
    app=create_app(project_dir=str(tmp_path/'registry'))
    client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    source=tmp_path/'source';source.mkdir()
    assignments={}
    for split in ('train','val','test'):
        image=source/f'{split}.png';Image.new('RGB',(32,32),'white').save(image)
        image.with_suffix('.json').write_text(json.dumps({'imagePath':image.name,'imageWidth':32,'imageHeight':32,'shapes':[
            {'label':'scratch','shape_type':'polygon','points':[[0,0],[7,0],[7,7],[0,7]]},
            {'label':'spot','shape_type':'polygon','points':[[8,8],[15,8],[15,15],[8,15]]}]}))
        assignments[image.name]=split
    project=client.post('/api/project/create',json={'name':'Multiclass','task':'segmentation'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    project['source_dataset_dir']=str(source)
    with project_scope(project):_write_split_manifest(source,assignments,0)
    model=build_segmentation_model(num_classes=3,preset='fast',model_name='unet',pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters():parameter.zero_()
        [m for m in model.modules() if isinstance(m,torch.nn.Conv2d) and m.out_channels==3][-1].bias[2]=10
    directory=Path(project['models_dir'])/'job_multiclass';directory.mkdir()
    meta={'task':'segmentation','classes':['background','scratch','spot'],'image_size':[32,32],'model_name':'unet','preset':'fast','source_dataset_path':str(source)}
    torch.save({**meta,'model_state_dict':model.state_dict()},directory/'best_model.pt')
    (directory/'model_meta.json').write_text(json.dumps(meta))
    (directory/'job_receipt.json').write_text(json.dumps({'task':'segmentation','status':'completed','source_dataset_path':str(source),'dataset_path':str(source),'dataset_fingerprint':'v1:fixture'}))
    response=client.post('/api/evaluation/reevaluate',json={'task':'segmentation','job_id':'job_multiclass','source_dataset_path':str(source)})
    assert response.status_code==200,response.text
    result=response.json();row=result['test_predictions'][0]
    assert Path(row['file_path'])==source/'test.png'
    assert row['pixel_evidence']['per_class']['scratch']['fn']==64
    assert row['pixel_evidence']['per_class']['spot']['tp']==64
    assert result['metrics']['per_class_iou']['spot']>0
