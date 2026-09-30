import json

import pytest
from PIL import Image

from backend.api import routes_dataset


@pytest.mark.parametrize('assigned', [False, True])
def test_gallery_matches_every_annotation_class(tmp_path, monkeypatch, assigned):
    root = tmp_path/'images'
    root.mkdir()
    Image.new('RGB', (20, 20)).save(root/'multi.png')
    (root/'multi.json').write_text(json.dumps({
        'imagePath':'multi.png', 'imageWidth':20, 'imageHeight':20,
        'shapes':[
            {'label':'scratch','shape_type':'rectangle','points':[[1,1],[5,5]]},
            {'label':'chip','shape_type':'rectangle','points':[[10,10],[15,15]]},
            {'label':'chip','shape_type':'rectangle','points':[[16,16],[19,19]]},
        ],
    }))
    monkeypatch.setattr(routes_dataset, 'SPLIT_MANIFEST_DIR', tmp_path/'splits')
    if assigned:
        routes_dataset._write_split_manifest(root, {'multi.png':'train'}, 42)
    for name in ('scratch','chip'):
        result = routes_dataset.list_dataset_images(folder_path=str(root),task='detection',
            limit=50,offset=0,split=None,class_name=name,label_status=None)
        assert result['total'] == 1
        assert result['items'][0]['labels'] == ['scratch','chip']


def test_secondary_class_disappears_after_explicit_overlay_clear(tmp_path, monkeypatch):
    root=tmp_path/'images'
    root.mkdir()
    Image.new('RGB',(10,10)).save(root/'empty.png')
    (root/'empty.json').write_text(json.dumps({'imagePath':'empty.png','imageWidth':10,'imageHeight':10,
        'shapes':[{'label':'chip','shape_type':'rectangle','points':[[1,1],[3,3]]}]}))
    annotations=tmp_path/'overlays'
    monkeypatch.setattr(routes_dataset,'STUDIO_ANNOTATIONS_DIR',annotations)
    from backend.engine.annotation_storage import dataset_annotation_dir
    target=dataset_annotation_dir(root,annotations)
    target.mkdir(parents=True)
    (target/'empty.json').write_text(json.dumps({'image_id':'empty','annotations':[]}))
    result=routes_dataset.list_dataset_images(folder_path=str(root),task='detection',limit=50,offset=0,
        split=None,class_name='chip',label_status=None)
    assert result['total'] == 0
