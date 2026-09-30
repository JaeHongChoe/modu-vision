"""Format round trips preserve source coordinates or reject loss explicitly."""
import pytest
from backend.engine.annotation_formats import export_annotations, import_annotations

IMAGES=[{'file_name':'parts/a.png','width':200,'height':100,'annotations':[
    {'type':'bbox','label':'scratch','bbox':[10.5,12.2,70.8,80.1]},
    {'type':'polygon','label':'crack','polygon':[[1.25,3.5],[40.75,5.0],[30.0,70.125]]}]}]

@pytest.mark.parametrize('format',['labelme','coco','yolo'])
def test_roundtrip_geometry(format):
    exported=export_annotations(IMAGES,format)
    restored=import_annotations(exported,format)
    assert restored[0]['file_name']=='parts/a.png'
    assert (restored[0]['width'],restored[0]['height'])==(200,100)
    for left,right in zip(IMAGES[0]['annotations'],restored[0]['annotations']):
        assert left['label']==right['label'] and left['type']==right['type']
        key='bbox' if left['type']=='bbox' else 'polygon'
        assert right[key]==pytest.approx(left[key]) if key=='bbox' else all(b==pytest.approx(a) for a,b in zip(left[key],right[key]))

@pytest.mark.parametrize('format',['labelme','coco','yolo'])
def test_unsafe_paths_and_lossy_shapes_rejected(format):
    with pytest.raises(ValueError): export_annotations([{**IMAGES[0],'file_name':'../escape.png'}],format)
    with pytest.raises(ValueError): export_annotations([{**IMAGES[0],'annotations':[{'type':'brush_mask','label':'NG','mask_rle':'data'}]}],format)

def test_coco_rle_and_unknown_category_rejected():
    payload={'images':[{'id':1,'file_name':'a.png','width':20,'height':20}], 'categories':[{'id':1,'name':'NG'}],
             'annotations':[{'image_id':1,'category_id':1,'segmentation':{'counts':'abc','size':[20,20]}}]}
    with pytest.raises(ValueError): import_annotations(payload,'coco')

def test_labelme_rotated_box_preserves_angle_extension():
    rows=[{**IMAGES[0],'annotations':[{'type':'rotated_bbox','label':'NG','rotated_bbox':[40.2,50.5,20.1,10.2,32.5]}]}]
    restored=import_annotations(export_annotations(rows,'labelme'),'labelme')
    assert restored[0]['annotations'][0]['rotated_bbox']==pytest.approx(rows[0]['annotations'][0]['rotated_bbox'])
    with pytest.raises(ValueError): export_annotations(rows,'yolo')
