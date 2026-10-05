"""Merging labels must not silently conflate or split existing class identities."""
import json
from pathlib import Path
import pytest
import numpy as np
from PIL import Image
from backend.tests.test_dataset_metadata_api import client_workspace
from backend.api import routes_annotation, routes_mask_exchange
from backend.engine.annotation_formats import export_annotations
from backend.engine.annotation_storage import set_request_annotation_root, reset_request_annotation_root, set_request_project_root, reset_request_project_root


def seed(project, source):
    a=set_request_annotation_root(Path(project['annotations_dir']));p=set_request_project_root(Path(project['project_dir']))
    try:
        routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(image_id='a',image_path=str(source/'a.png'),image_width=100,image_height=80,
            annotations=[{'type':'polygon','label':'Scratch','category_id':7,'color':'#f59e0b','polygon':[[1,1],[15,1],[10,15]]}],
            mask_classes=[{'id':0,'name':'background','color':'#000000'},{'id':7,'name':'Scratch','color':'#f59e0b'},{'id':31,'name':'Empty','color':'#a855f7'}]))
    finally:reset_request_annotation_root(a);reset_request_project_root(p)


def freeze(project):
    return {f.relative_to(project['project_dir']).as_posix():f.read_bytes() for f in Path(project['project_dir']).rglob('*') if f.is_file()}


@pytest.mark.parametrize('format',['labelme','coco','yolo','mask'])
@pytest.mark.parametrize('cid,label',[(7,'Other'),(8,'Scratch')])
def test_merge_refuses_id_or_name_collision_without_a_backup_or_label_mutation(client_workspace,tmp_path,format,cid,label):
    client,project,source=client_workspace;seed(project,source);original=(source/'a.png').read_bytes()
    if format=='mask':
        client.app.include_router(routes_mask_exchange.router);directory=tmp_path/'mask';directory.mkdir();pixels=np.zeros((80,100),np.uint8);pixels[30:40,30:40]=cid;Image.fromarray(pixels).save(directory/'a.png')
        (directory/'mask_manifest.json').write_text(json.dumps({'schema_version':1,'classes':[{'id':0,'name':'background','color':'#000000'},{'id':cid,'name':label,'color':'#f59e0b'}],
            'images':[{'file_name':'a.png','mask_file':'a.png','width':100,'height':80}]}),encoding='utf-8')
        route='/api/dataset/masks/import';body={'import_dir':str(directory)}
    else:
        route='/api/dataset/formats/import';body={'format':format,'payload':export_annotations([{'file_name':'a.png','width':100,'height':80,'annotations':[{'type':'polygon','label':label,'category_id':cid,'color':'#f59e0b','polygon':[[30,30],[40,30],[35,40]]}]}],format)}
    preview=client.post(route,json=body);assert preview.status_code==200,preview.text
    before=freeze(project)
    response=client.post(route,json={**body,'mode':'apply','conflict_policy':'merge','actor':'merge-owner','expected_revisions':{r['image_uuid']:r['revision'] for r in preview.json()['preview']}})
    assert response.status_code==422,response.text
    assert 'class' in response.text.lower()
    assert freeze(project)==before
    assert (source/'a.png').read_bytes()==original


def test_valid_mask_merge_keeps_unused_existing_palette_class(client_workspace,tmp_path):
    client,project,source=client_workspace;seed(project,source);client.app.include_router(routes_mask_exchange.router)
    root=tmp_path/'mask';root.mkdir();pixels=np.zeros((80,100),np.uint8);pixels[30:40,30:40]=23;Image.fromarray(pixels).save(root/'a.png')
    (root/'mask_manifest.json').write_text(json.dumps({'schema_version':1,'classes':[{'id':0,'name':'background','color':'#000000'},{'id':23,'name':'Crack','color':'#ef4444'}],
        'images':[{'file_name':'a.png','mask_file':'a.png','width':100,'height':80}]}),encoding='utf-8')
    body={'import_dir':str(root)};preview=client.post('/api/dataset/masks/import',json=body).json()['preview']
    response=client.post('/api/dataset/masks/import',json={**body,'mode':'apply','conflict_policy':'merge','expected_revisions':{r['image_uuid']:r['revision'] for r in preview}})
    assert response.status_code==200,response.text
    stored=next(Path(project['annotations_dir']).rglob('a.json'))
    data=json.loads(stored.read_text());assert {c['id']:c['name'] for c in data['mask_classes']}=={0:'background',7:'Scratch',23:'Crack',31:'Empty'}
    with Image.open(stored.parent/'masks/a.png') as im:mask=np.asarray(im)
    assert set(np.unique(mask))=={0,7,23} and mask[35,35]==23 and mask[3,5]==7
