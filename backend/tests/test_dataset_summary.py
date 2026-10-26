import json
import numpy as np
import pytest
from PIL import Image


def test_summary_counts_images_once_per_class_and_keeps_unused_separate(tmp_path):
    from backend.engine.dataset_summary import dataset_summary
    root=tmp_path/'images'
    root.mkdir()
    for i in range(3):
        Image.new('RGB',(20,20),color=(i,0,0)).save(root/f'{i}.png')
    (root/'0.json').write_text(json.dumps({'imagePath':'0.png','imageWidth':20,'imageHeight':20,
      'shapes':[{'label':name,'shape_type':'rectangle','points':[[1,1],[3,3]]}
                for name in ['scratch','chip','chip']]}))
    result=dataset_summary(root,'detection',assignments={str(root/'0.png'):'train'},
        usage={str(root/'1.png'):'not_used'})
    assert result['total']==3
    assert result['labeling']['labeled']['count']==1
    assert result['labeling']['unlabeled']['count']==2
    assert result['assignments']['train']['count']==1
    assert result['assignments']['not_used']['count']==1
    assert result['assignments']['not_split']['count']==1
    assert result['classes']['scratch']['count']==result['classes']['chip']['count']==1
    assert result['labeling']['labeled']['ratio']==1/3


def test_summary_explicit_overlay_clear_does_not_restore_source_labels(tmp_path,monkeypatch):
    from backend.engine.dataset_summary import dataset_summary
    from backend.engine.annotation_storage import dataset_annotation_dir,set_request_annotation_root,reset_request_annotation_root
    root=tmp_path/'images'
    root.mkdir()
    Image.new('RGB',(10,10)).save(root/'background.png')
    annotations=tmp_path/'annotations'
    token=set_request_annotation_root(annotations)
    try:
        path=dataset_annotation_dir(root)
        path.mkdir(parents=True)
        (path/'background.json').write_text(json.dumps({'annotations':[]}))
        result=dataset_summary(root,'detection')
    finally:
        reset_request_annotation_root(token)
    assert result['labeling']['unlabeled']['count']==1
    assert result['classes']=={}
    assert result['items'][0]['labels']==[]


@pytest.fixture
def saved_raster(tmp_path):
    from backend.engine.annotation_storage import dataset_annotation_dir,set_request_annotation_root,reset_request_annotation_root
    root=tmp_path/'images'
    root.mkdir()
    image=root/'part.png'
    Image.new('RGB',(10,10)).save(image)
    token=set_request_annotation_root(tmp_path/'annotations')
    overlay=dataset_annotation_dir(root)
    (overlay/'masks').mkdir(parents=True)
    mask=overlay/'masks'/'part.png'
    document={'annotations':[],'mask_file':str(mask)}
    def save(pixels, **fields):
        Image.fromarray(np.asarray(pixels,dtype=np.uint8)).save(mask)
        (overlay/'part.json').write_text(json.dumps({**document,**fields}))
    try:
        yield root,mask,save
    finally:
        reset_request_annotation_root(token)


@pytest.mark.parametrize('fields,expected',[
    ({'mask_classes':[{'id':255,'name':'Explicit white'}]}, {'Explicit white':{'count':1,'ratio':1}}),
    ({}, {'class_1':{'count':1,'ratio':1}}),
    ({'mask_classes':[{'id':2,'name':'Unused'}]}, {'class_1':{'count':1,'ratio':1}}),
    ({'annotations':[{'type':'tag','label':'OK','category_id':1}]},
     {'OK':{'count':1,'ratio':1},'class_1':{'count':1,'ratio':1}}),
])
def test_summary_raster_mapping_keeps_explicit_255_binary_fallback_and_tags(saved_raster,fields,expected):
    from backend.engine.dataset_summary import dataset_summary
    root,mask,save=saved_raster
    save([[0,255]],**fields)
    assert dataset_summary(root,'segmentation')['classes']==expected


def test_summary_unknown_raster_ids_ignore_unused_palette_and_metadata(saved_raster):
    from backend.engine.dataset_summary import dataset_summary
    root,mask,save=saved_raster
    save([[0,6]],mask_classes=[{'id':4,'name':'Unused','external_note':'retained'}],external_metadata={'anything':True})
    assert dataset_summary(root,'segmentation')['classes']=={'class_6':{'count':1,'ratio':1}}


def test_summary_zero_raster_retains_explicit_tags_without_inventing_background(saved_raster):
    from backend.engine.dataset_summary import dataset_summary
    root,mask,save=saved_raster
    save([[0,0]],annotations=[{'type':'brush_mask','label':'Stale','category_id':2},
                            {'type':'tag','label':'OK','category_id':2}])
    result=dataset_summary(root,'segmentation')
    assert result['classes']=={'OK':{'count':1,'ratio':1}}
    assert result['labeling']['labeled']['count']==1


@pytest.mark.parametrize('broken', ['conflict','palette_schema','rgb'])
def test_summary_raster_schema_tolerance_contains_mapping_and_pixel_errors(saved_raster,broken):
    from backend.engine.dataset_summary import dataset_summary
    root,mask,save=saved_raster
    fields={'mask_classes':[{'id':2,'name':'Palette'}]}
    if broken=='conflict':fields['annotations']=[{'type':'brush_mask','category_id':2,'label':'Other'}]
    elif broken=='palette_schema':fields['mask_classes']=[{'id':2}]
    save([[0,2]],**fields)
    if broken=='rgb':Image.new('RGB',(2,2)).save(mask)
    with pytest.raises((ValueError,KeyError,TypeError)):
        dataset_summary(root,'segmentation')
    result=dataset_summary(root,'segmentation',tolerate_schema_errors=True)
    assert result['classes']=={}
    assert result['labeling']['unlabeled']['count']==1


def test_summary_raster_io_errors_propagate_even_with_schema_tolerance(saved_raster,monkeypatch):
    from backend.engine.dataset_summary import dataset_summary
    from backend.engine import grouped_dataset_views
    root,mask,save=saved_raster
    save([[0,2]])
    original=grouped_dataset_views.Image.open
    def inaccessible(path,*args,**kwargs):
        if str(path)==str(mask):raise PermissionError('fixture raster access denied')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(grouped_dataset_views.Image,'open',inaccessible)
    with pytest.raises(PermissionError,match='fixture raster access denied'):
        dataset_summary(root,'segmentation',tolerate_schema_errors=True)


@pytest.mark.parametrize('canonical,declared',[(True,True),(False,True),(True,False)])
def test_summary_only_reads_declared_canonical_active_raster(saved_raster,tmp_path,canonical,declared):
    from backend.engine.dataset_summary import dataset_summary
    root,mask,save=saved_raster
    foreign=tmp_path/'foreign.png'
    # A raw pointer must never be decoded, even when it names an existing file.
    foreign.write_bytes(b'not a raster')
    save([[0,3]],annotations=[{'type':'brush_mask','label':'Scratch','category_id':2}],
         mask_classes=[{'id':3,'name':'Particle'}],mask_file=str(foreign) if declared else None)
    if not canonical:mask.unlink()
    result=dataset_summary(root,'segmentation')
    expected={'Particle':{'count':1,'ratio':1}} if canonical and declared else {'Scratch':{'count':1,'ratio':1}}
    assert result['classes']==expected
