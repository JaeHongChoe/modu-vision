import json
from pathlib import Path
import pytest
from PIL import Image
from backend.engine.grouped_dataset_views import load_manifest_dataset
from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
from backend.api.routes_dataset import _write_split_manifest

@pytest.fixture
def grouped(tmp_path):
    root=tmp_path/'source';(root/'images'/'train').mkdir(parents=True);(root/'images'/'val').mkdir(parents=True)
    Image.new('RGB',(64,48),'white').save(root/'images'/'train'/'a.png');Image.new('RGB',(64,48),'black').save(root/'images'/'val'/'b.png')
    token=set_request_split_root(tmp_path/'splits');_write_split_manifest(root,{'images/train/a.png':'val','images/val/b.png':'train'},42)
    yield root
    reset_request_split_root(token)

def test_coco_manifest_moves_original_paths_and_keeps_class_indices(grouped):
    root=grouped
    for split,name in [('train','a.png'),('val','b.png')]:
        (root/f'annotations_{split}.json').write_text(json.dumps({'images':[{'id':1,'file_name':name,'width':64,'height':48}], 'categories':[{'id':3,'name':'Scratch'}], 'annotations':[{'id':1,'image_id':1,'category_id':3,'bbox':[2,3,10,12]}]}))
    train=load_manifest_dataset('detection',root,'train',image_size=(64,48)); val=load_manifest_dataset('detection',root,'val',image_size=(64,48),class_names=list(train.categories.values()))
    assert train.images[train.image_ids[0]]['file_name']=='images/val/b.png'
    assert val.images[val.image_ids[0]]['file_name']=='images/train/a.png'
    assert train[0][1]['labels'].tolist()==[1]
    assert train[0][1]['boxes'][0].tolist()==[2,3,12,15]

def test_yolo_and_paired_mask_manifest_views_are_actual_training_inputs(grouped):
    root=grouped;(root/'classes.txt').write_text('Scratch')
    for split,name in [('train','a'),('val','b')]:
        (root/'labels'/split).mkdir(parents=True);(root/'labels'/split/f'{name}.txt').write_text('0 .5 .5 .5 .5')
        (root/'masks'/split).mkdir(parents=True);Image.new('L',(64,48),255).save(root/'masks'/split/f'{name}.png')
    detection=load_manifest_dataset('detection',root,'val',image_size=(64,48));assert detection[0][1]['boxes'][0].tolist()==[16,12,48,36]
    segmentation=load_manifest_dataset('segmentation',root,'train',image_size=(64,48));assert str(segmentation.samples[0][0]).endswith('/images/val/b.png')
    assert segmentation[0][1].unique().tolist()==[1]

def test_anomaly_manifest_never_trains_on_defects(tmp_path):
    root=tmp_path/'anomaly';(root/'train'/'good').mkdir(parents=True);(root/'test'/'crack').mkdir(parents=True)
    Image.new('RGB',(32,32)).save(root/'train'/'good'/'normal.png');Image.new('RGB',(32,32),'white').save(root/'test'/'crack'/'ng.png')
    token=set_request_split_root(tmp_path/'splits')
    try:
        _write_split_manifest(root,{'train/good/normal.png':'train','test/crack/ng.png':'val'},42)
        ds=load_manifest_dataset('anomaly',root,'train');assert ds.samples[0][1]==0
        _write_split_manifest(root,{'train/good/normal.png':'val','test/crack/ng.png':'train'},42)
        with pytest.raises(ValueError,match='normal'):load_manifest_dataset('anomaly',root,'train')
    finally:reset_request_split_root(token)
