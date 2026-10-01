import json
import math
import torch
import pytest
from backend.tests.test_rotated_detection import _write_dataset
from backend.engine import rotated_detection as rd
from backend.api.routes_annotation import AnnotationItem


def direction_rows(tmp_path,multi=False):
    rows=_write_dataset(tmp_path)['samples']
    for index,row in enumerate(rows):
        row.pop('source_sha256');row['direction_deg']=(350+index*20)%360
        if multi:row['objects']=[{k:row[k] for k in ('label','box','direction_deg')}]
    return rows


def test_direction_is_saved_separately_from_axial_box_and_encodes_full_circle(tmp_path):
    manifest=rd.write_rotated_manifest(tmp_path,direction_rows(tmp_path))
    assert getattr(manifest.records[0],'direction_deg',None)==350,'An independent direction target must survive manifest write/reopen'
    assert manifest.records[0].box['angle_deg']==25
    _,target=rd.RotatedBoxDataset(rd.load_rotated_manifest(tmp_path),split='train')[0]
    assert target.shape==(8,) and target[6].item()==pytest.approx(math.sin(math.radians(350)))
    assert target[4].item()==pytest.approx(math.sin(math.radians(50)))


def test_direction_rejects_partial_targets_and_invalid_full_circle(tmp_path):
    rows=direction_rows(tmp_path);rows[1].pop('direction_deg')
    with pytest.raises(ValueError,match='direction'):rd.write_rotated_manifest(tmp_path,rows)
    rows=direction_rows(tmp_path);rows[0]['direction_deg']=360
    with pytest.raises(ValueError,match='direction'):rd.write_rotated_manifest(tmp_path,rows)
    item=AnnotationItem(type='rotated_bbox',label='part',rotated_bbox=[10,10,8,4,20],direction_deg=350)
    assert item.model_dump()['direction_deg']==350
    with pytest.raises(ValueError):AnnotationItem(type='rotated_bbox',label='part',direction_deg=float('nan'))


@pytest.mark.parametrize('multi',[False,True])
def test_direction_head_trains_persists_reopens_and_outputs_independent_metric(tmp_path,multi):
    rd.write_rotated_manifest(tmp_path,direction_rows(tmp_path,multi))
    torch.set_num_threads(2)
    output=tmp_path/'model';rd.train_rotated_detector(tmp_path,output,epochs=1,batch_size=2,image_size=32)
    meta=json.loads((output/'model_meta.json').read_text());assert meta.get('direction_enabled') is True
    result=rd.predict_rotated_box(output/'best_model.pt',tmp_path/'images'/'test.png',threshold=0)
    detection=result['detections'][0] if multi else result
    assert 0<=detection['direction_deg']<360 and -90<=detection['box']['angle_deg']<90
    evaluation=rd.evaluate_rotated_detector(output/'best_model.pt',tmp_path,split='test')
    if evaluation.get('direction_matched_count',evaluation.get('direction_sample_count',0)):
        assert 0<=evaluation['mean_direction_error_deg']<=180
    else:assert evaluation['mean_direction_error_deg'] is None


def test_direction_labelme_roundtrip_preserves_target_and_axial_geometry():
    from backend.engine.annotation_formats import export_annotations,import_annotations
    rows=[{'file_name':'part.png','width':64,'height':48,'annotations':[{'type':'rotated_bbox','label':'part','rotated_bbox':[20,20,12,8,25],'direction_deg':350}]}]
    payload=export_annotations(rows,'labelme')
    restored=import_annotations(payload,'labelme')[0]['annotations'][0]
    assert restored.get('direction_deg')==350 and restored['rotated_bbox'][4]==25
