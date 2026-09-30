import json
from pathlib import Path
import pytest
from PIL import Image
from fastapi import HTTPException
from backend.api import routes_annotation as ra
from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root
from backend.engine.dataset_metadata import metadata_for_path,update_metadata

@pytest.fixture
def scoped(tmp_path):
    source=tmp_path/'source'; source.mkdir(); Image.new('RGB',(100,100)).save(source/'a.png')
    project=tmp_path/'project'; project.mkdir(); (project/'project.json').write_text(json.dumps({'source_dataset_dir':str(source)}))
    a=set_request_annotation_root(project/'annotations'); p=set_request_project_root(project)
    yield project,source
    reset_request_project_root(p);reset_request_annotation_root(a)

def test_canvas_stale_write_rejected_and_actor_invalidates_approval(scoped):
    project,source=scoped
    first=metadata_for_path(project,source,source/'a.png')
    approved=update_metadata(project,source,first['image_uuid'],first['revision'],'Kim',{'workflow_state':'approved'})
    req=ra.AnnotationSaveRequest(image_id='a',image_path=str(source/'a.png'),image_width=100,image_height=100,
        annotations=[{'type':'polygon','label':'NG','polygon':[[1.25,1.5],[10.75,1.5],[5.2,10.125]]}],expected_revision=approved['revision'],actor='Lee')
    result=ra.save_annotations(req)
    assert result['metadata']['workflow_state']=='needs_review'
    assert result['metadata']['audit'][-1]['actor']=='Lee'
    loaded=ra.get_annotations('a',file_path=str(source/'a.png'))
    assert loaded['annotations'][0]['polygon'][0]==[1.25,1.5]
    assert loaded['metadata']['image_uuid']==first['image_uuid']
    with pytest.raises(HTTPException) as caught: ra.save_annotations(req)
    assert caught.value.status_code==409

def test_ambiguous_same_stem_sources_fail_without_overwriting(scoped):
    project,source=scoped;Image.new('RGB',(100,100),'white').save(source/'a.jpg')
    with pytest.raises(HTTPException) as caught:
        ra.save_annotations(ra.AnnotationSaveRequest(image_id='a',image_path=str(source/'a.png'),annotations=[]))
    assert caught.value.status_code==422

def test_source_coco_and_yolo_load_exact_nested_image_without_manual_reimport(scoped):
    project,source=scoped
    (source/'images'/'train').mkdir(parents=True);Image.new('RGB',(100,100)).save(source/'images'/'train'/'part.png')
    (source/'annotations_train.json').write_text(json.dumps({'images':[{'id':1,'file_name':'part.png','width':100,'height':100}], 'categories':[{'id':2,'name':'Scratch'}], 'annotations':[{'id':1,'image_id':1,'category_id':2,'bbox':[10,20,30,40]}]}))
    loaded=ra.get_annotations('part',file_path=str(source/'images'/'train'/'part.png'))
    assert loaded['annotations'][0]['label']=='Scratch'
    assert loaded['annotations'][0]['bbox']==[10,20,40,60]
    (source/'annotations_train.json').unlink();(source/'labels'/'train').mkdir(parents=True)
    (source/'classes.txt').write_text('Scratch\nCrack')
    (source/'labels'/'train'/'part.txt').write_text('1 0.5 0.5 0.4 0.2')
    loaded=ra.get_annotations('part',file_path=str(source/'images'/'train'/'part.png'))
    assert loaded['annotations'][0]['label']=='Crack'
    assert loaded['annotations'][0]['bbox']==pytest.approx([30,40,70,60])

def test_saved_raster_mask_change_invalidates_review(scoped):
    project,source=scoped
    ra.save_annotations(ra.AnnotationSaveRequest(image_id='a',image_path=str(source/'a.png'),image_width=100,image_height=100,annotations=[{'type':'polygon','label':'NG','polygon':[[1,1],[10,1],[10,10]]}]))
    current=metadata_for_path(project,source,source/'a.png')
    approved=update_metadata(project,source,current['image_uuid'],current['revision'],'Kim',{'workflow_state':'approved'})
    from backend.engine.annotation_storage import dataset_annotation_dir
    mask=dataset_annotation_dir(source,project/'annotations')/'masks'/'a.png'
    Image.new('L',(100,100),255).save(mask)
    after=metadata_for_path(project,source,source/'a.png')
    assert after['workflow_state']=='needs_review'
    assert after['revision']>approved['revision']
