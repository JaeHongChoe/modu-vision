import base64
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from backend.tests.test_dataset_metadata_api import client_workspace


def bundle(tmp_path, source):
    root = tmp_path / 'external'; root.mkdir()
    mask = np.zeros((80,100),np.uint8)
    mask[5:30,10:40] = 3; mask[12:20,20:30] = 0
    mask[40:55,50:70] = 7
    Image.fromarray(mask).save(root/'a-mask.png')
    manifest = {'schema_version':1,'classes':[{'id':0,'name':'background','color':'#000000'},
        {'id':3,'name':'Scratch','color':'#ef4444'},{'id':7,'name':'Particle','color':'#22d3ee'}],
        'images':[{'file_name':'a.png','mask_file':'a-mask.png','width':100,'height':80}]}
    (root/'mask_manifest.json').write_text(json.dumps(manifest))
    return root,mask


def test_mask_bundle_keeps_sparse_ids_holes_and_source_names(tmp_path):
    from backend.engine.mask_exchange import load_mask_bundle, build_mask_bundle
    source=tmp_path/'source';source.mkdir();Image.new('RGB',(100,80)).save(source/'a.png')
    root,pixels=bundle(tmp_path,source)
    imported=load_mask_bundle(root)
    assert imported[0]['file_name']=='a.png'
    assert [a['category_id'] for a in imported[0]['annotations']]==[3,7]
    rgba=np.asarray(Image.open(io.BytesIO(base64.b64decode(imported[0]['annotations'][0]['mask_rle'].split(',')[1]))))
    assert np.array_equal(rgba[:,:,3]>0,pixels==3)
    files=build_mask_bundle(imported,source)
    manifest=json.loads(files['mask_manifest.json'])
    assert manifest['images'][0]['source_sha256']
    assert {c['id']:c['name'] for c in manifest['classes']}=={0:'background',3:'Scratch',7:'Particle'}
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(files[manifest['images'][0]['mask_file']]))),pixels)
    assert files['originals/a.png']==(source/'a.png').read_bytes()


def test_mask_bundle_rejects_unmapped_ids_and_traversal(tmp_path):
    from backend.engine.mask_exchange import load_mask_bundle
    source=tmp_path/'source';source.mkdir();root,_=bundle(tmp_path,source)
    doc=json.loads((root/'mask_manifest.json').read_text());doc['classes']=doc['classes'][:2]
    (root/'mask_manifest.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='class'):load_mask_bundle(root)
    doc['images'][0]['mask_file']='../outside.png';(root/'mask_manifest.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='path'):load_mask_bundle(root)


def test_mask_export_same_stem_sources_have_distinct_masks(tmp_path):
    from backend.engine.mask_exchange import build_mask_bundle
    source=tmp_path/'source';source.mkdir()
    for name in ['a.png','a.jpg']:Image.new('RGB',(20,20)).save(source/name)
    rows=[{'file_name':name,'width':20,'height':20,'annotations':[]} for name in ['a.png','a.jpg']]
    files=build_mask_bundle(rows,source)
    images=json.loads(files['mask_manifest.json'])['images']
    assert len({row['mask_file'] for row in images})==2


def test_mask_import_preview_conflict_revision_export_and_trainable_pixels(client_workspace,tmp_path):
    from backend.api import routes_mask_exchange
    from backend.engine.annotation_storage import dataset_annotation_dir
    from backend.engine.grouped_dataset_views import ManifestSegmentationDataset
    client,project,source=client_workspace;client.app.include_router(routes_mask_exchange.router)
    original=(source/'a.png').read_bytes();root,pixels=bundle(tmp_path,source)
    body={'import_dir':str(root),'actor':'Reviewer'}
    preview=client.post('/api/dataset/masks/import',json=body)
    assert preview.status_code==200,preview.text
    rows=preview.json()['preview']
    apply={**body,'mode':'apply','expected_revisions':{r['image_uuid']:r['revision'] for r in rows}}
    result=client.post('/api/dataset/masks/import',json=apply)
    assert result.status_code==200,result.text
    assert result.json()['backup_version_id']
    assert client.post('/api/dataset/masks/import',json=apply).status_code==409
    studio=dataset_annotation_dir(source,Path(project['annotations_dir']),use_scope=False)
    assert np.array_equal(np.asarray(Image.open(studio/'masks/a.png')),pixels)
    exported=client.post('/api/dataset/masks/export',json={})
    assert exported.status_code==200,exported.text
    archive=zipfile.ZipFile(io.BytesIO(client.get(exported.json()['download_url']).content))
    manifest=json.loads(archive.read('mask_manifest.json'))
    assert archive.read('originals/a.png')==original
    assert {c['id']:c['name'] for c in manifest['classes']}[7]=='Particle'
    from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root
    token=set_request_annotation_root(Path(project['annotations_dir']))
    try:
        dataset=ManifestSegmentationDataset(source,[source/'a.png'])
        assert dataset.classes[3]=='Scratch' and dataset.classes[7]=='Particle'
        assert np.array_equal(dataset[0][1].numpy(),pixels)
    finally:reset_request_annotation_root(token)
    assert (source/'a.png').read_bytes()==original and not (source/'a.json').exists()


def test_mask_manifest_retains_unused_classes_and_id255_in_grouped_training(client_workspace,tmp_path):
    from backend.api import routes_mask_exchange,routes_dataset
    from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root
    from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
    from backend.engine.grouped_dataset_views import load_manifest_dataset
    client,project,source=client_workspace;client.app.include_router(routes_mask_exchange.router)
    root,_=bundle(tmp_path,source);pixels=np.zeros((80,100),np.uint8);pixels[20:40,20:40]=255
    Image.fromarray(pixels).save(root/'a-mask.png');doc=json.loads((root/'mask_manifest.json').read_text())
    doc['classes']=[{'id':0,'name':'background','color':'#000000'},{'id':255,'name':'edge','color':'#ff0000'},{'id':7,'name':'absent','color':'#00ff00'}]
    (root/'mask_manifest.json').write_text(json.dumps(doc));preview=client.post('/api/dataset/masks/import',json={'import_dir':str(root)}).json()['preview']
    response=client.post('/api/dataset/masks/import',json={'import_dir':str(root),'mode':'apply','expected_revisions':{r['image_uuid']:r['revision'] for r in preview}})
    assert response.status_code==200,response.text
    a=set_request_annotation_root(Path(project['annotations_dir']));s=set_request_split_root(Path(project['dataset_dir'])/'splits')
    try:
        routes_dataset._write_split_manifest(source,{'a.png':'train'},42)
        dataset=load_manifest_dataset('segmentation',source,'train')
        assert dataset.classes[255]=='edge' and dataset.classes[7]=='absent'
        assert np.array_equal(dataset[0][1].numpy(),pixels)
        exported=client.post('/api/dataset/masks/export',json={}).json()
        archive=zipfile.ZipFile(io.BytesIO(client.get(exported['download_url']).content))
        classes=json.loads(archive.read('mask_manifest.json'))['classes']
        assert {c['id']:c['name'] for c in classes}[7]=='absent'
    finally:reset_request_annotation_root(a);reset_request_split_root(s)


def test_mask_merge_keeps_existing_box_as_trainable_class_pixels(client_workspace,tmp_path):
    from backend.api import routes_mask_exchange,routes_annotation
    from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root
    client,project,source=client_workspace;client.app.include_router(routes_mask_exchange.router)
    a=set_request_annotation_root(Path(project['annotations_dir']));p=set_request_project_root(Path(project['project_dir']))
    try:
        routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(image_id='a',image_path=str(source/'a.png'),image_width=100,image_height=80,annotations=[{'id':'box','type':'bbox','label':'Box','category_id':2,'bbox':[1,1,4,4],'color':'#ff00ff'}]))
        root,_=bundle(tmp_path,source);preview=client.post('/api/dataset/masks/import',json={'import_dir':str(root)}).json()['preview']
        result=client.post('/api/dataset/masks/import',json={'import_dir':str(root),'mode':'apply','conflict_policy':'merge','expected_revisions':{r['image_uuid']:r['revision'] for r in preview}})
        assert result.status_code==200,result.text
        studio=__import__('backend.engine.annotation_storage',fromlist=['dataset_annotation_dir']).dataset_annotation_dir(source)
        assert np.asarray(Image.open(studio/'masks/a.png'))[2,2]==2
    finally:reset_request_annotation_root(a);reset_request_project_root(p)
