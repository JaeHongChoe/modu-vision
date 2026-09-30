import json
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
