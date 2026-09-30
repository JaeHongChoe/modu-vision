from pathlib import Path
import json
import numpy as np
import torch
from PIL import Image
from backend.engine.rotated_detection import write_rotated_manifest, train_rotated_detector, predict_rotated_box, evaluate_rotated_detector


def test_multi_object_class_labels_train_evaluate_and_predict(tmp_path: Path):
    rows=[]
    for index,split in enumerate(('train','train','val','test')):
        image=f'{index}.png'
        Image.fromarray(np.full((64,64,3),40+index*20,dtype=np.uint8)).save(tmp_path/image)
        objects=[{'label':'scratch','box':{'cx':20,'cy':20,'width':12,'height':8,'angle_deg':10}}, {'label':'dent','box':{'cx':44,'cy':44,'width':10,'height':6,'angle_deg':-20}}]
        rows.append({'image':image,'split':split,'objects':objects})
    manifest=write_rotated_manifest(tmp_path,rows)
    assert manifest.class_names==('dent','scratch')
    assert manifest.provenance['source_image_count']==4
    torch.set_num_threads(2)
    output=tmp_path/'model'
    receipt=train_rotated_detector(tmp_path,output,epochs=1,batch_size=2)
    result=predict_rotated_box(output/'best_model.pt',tmp_path/'3.png',threshold=0)
    assert 1 <= len(result['detections']) <= 2
    assert json.loads((output/'model_meta.json').read_text())['max_objects']==2
    assert all(item['label'] in manifest.class_names for item in result['detections'])
    metrics=evaluate_rotated_detector(output/'best_model.pt',tmp_path,split='test')
    assert metrics['ground_truth_objects']==2
    assert 'precision' in metrics and 'recall' in metrics
    rows[-1]['objects'].append({'label':'dent','box':{'cx':30,'cy':45,'width':8,'height':5,'angle_deg':5}})
    write_rotated_manifest(tmp_path,rows)
    revised=evaluate_rotated_detector(output/'best_model.pt',tmp_path,split='test',allow_dataset_revision=True)
    assert revised['dataset_revision_changed'] is True
    assert revised['ground_truth_objects']==3
    assert revised['training_dataset_sha256']==receipt['dataset_sha256']
