import json
from pathlib import Path
import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import routes_project,routes_dataset_metadata as rdm,routes_dataset_versions as versions
from backend.engine.annotation_storage import dataset_annotation_dir

@pytest.fixture
def client_workspace(tmp_path, monkeypatch):
    import httpx, inspect
    if "app" not in inspect.signature(httpx.Client.__init__).parameters:
        original=httpx.Client.__init__
        def compatible(self,*args,app=None,**kwargs): return original(self,*args,**kwargs)
        monkeypatch.setattr(httpx.Client,"__init__",compatible)
    source=tmp_path/'source';source.mkdir(); Image.new('RGB',(100,80),'white').save(source/'a.png'); Image.new('RGB',(100,80),'black').save(source/'b.png')
    app=FastAPI();app.state.project_dir=tmp_path/'projects';app.include_router(routes_project.router);app.include_router(rdm.router);app.include_router(rdm.format_router);app.include_router(versions.router)
    client=TestClient(app)
    project=client.post('/api/project/create',json={'name':'metadata','task':'detection'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    return client,project,source

def test_review_filter_conflict_and_version_retains_metadata(client_workspace):
    client,project,source=client_workspace
    row=client.get('/api/dataset/metadata').json()['items'][0]
    result=client.patch('/api/dataset/metadata/'+row['image_uuid'],json={'expected_revision':row['revision'],'actor':'Kim','changes':{'workflow_state':'approved','product':'P','lot':'L'}})
    assert result.status_code==200,result.text
    assert client.get('/api/dataset/metadata?state=approved&product=P').json()['total']==1
    stale=client.patch('/api/dataset/metadata/'+row['image_uuid'],json={'expected_revision':row['revision'],'actor':'Lee','changes':{'product':'P2'}})
    assert stale.status_code==409
    # versions need same annotation scope as app middleware
    from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root
    token=set_request_annotation_root(Path(project['annotations_dir']))
    try: version=client.post('/api/dataset/versions',json={'name':'reviewed'}).json()
    finally: reset_request_annotation_root(token)
    manifest=json.loads((Path(project['project_dir'])/'versions'/version['id']/'manifest.json').read_text())
    assert any(r['relative_path']=='metadata/workflow.json' and r['snapshot_path'] for r in manifest['files'])

def test_import_preview_revision_export_and_source_preservation(client_workspace):
    client,project,source=client_workspace; original=(source/'a.png').read_bytes()
    payload={'imagePath':'a.png','imageWidth':100,'imageHeight':80,'shapes':[{'label':'scratch','shape_type':'rectangle','points':[[1.25,2.5],[40.75,50.5]]}]}
    preview=client.post('/api/dataset/formats/import',json={'format':'labelme','payload':payload}).json()['preview']
    body={'format':'labelme','payload':payload,'mode':'apply','actor':'Kim','expected_revisions':{r['image_uuid']:r['revision'] for r in preview}}
    response=client.post('/api/dataset/formats/import',json=body); assert response.status_code==200,response.text
    assert client.post('/api/dataset/formats/import',json=body).status_code==409
    exported=client.post('/api/dataset/formats/export',json={'format':'coco'})
    assert exported.status_code==200,exported.text
    assert exported.json()['payload']['annotations'][0]['bbox']==[1.25,2.5,39.5,48.0]
    assert client.get(exported.json()['download_url']).status_code==200
    assert (source/'a.png').read_bytes()==original and not (source/'a.json').exists()

def test_nested_format_import_snapshot_restore_retains_audit(client_workspace):
    client,project,source=client_workspace
    (source/'parts').mkdir(); Image.new('RGB',(100,80),'red').save(source/'parts'/'nested.png')
    payload={'imagePath':'parts/nested.png','imageWidth':100,'imageHeight':80,'shapes':[{'label':'NG','shape_type':'rectangle','points':[[1,2],[20,30]]}]}
    preview=client.post('/api/dataset/formats/import',json={'format':'labelme','payload':payload}).json()['preview']
    imported=client.post('/api/dataset/formats/import',json={'format':'labelme','payload':payload,'mode':'apply','actor':'Kim','expected_revisions':{r['image_uuid']:r['revision'] for r in preview}})
    assert imported.status_code==200,imported.text
    from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root
    token=set_request_annotation_root(Path(project['annotations_dir']))
    try:
        baseline=client.post('/api/dataset/versions',json={'name':'Nested baseline'}).json()
        manifest=client.get('/api/dataset/versions/'+baseline['id']).json()
        assert any(r['origin']=='studio_scoped' and r['relative_path'].endswith('/nested.json') for r in manifest['files'])
        meta=client.get('/api/dataset/metadata/image',params={'image_path':str(source/'parts'/'nested.png')}).json()
        approved=client.patch('/api/dataset/metadata/'+meta['image_uuid'],json={'expected_revision':meta['revision'],'actor':'Reviewer Lee','changes':{'workflow_state':'approved'}}).json()
        restored=client.post('/api/dataset/versions/'+baseline['id']+'/restore')
        assert restored.status_code==200,restored.text
        after=client.get('/api/dataset/metadata/image',params={'image_path':str(source/'parts'/'nested.png')}).json()
        assert after['revision']>approved['revision']
        assert any(a['actor']=='Reviewer Lee' for a in after['audit'])
        assert after['workflow_state']=='needs_review'
    finally:reset_request_annotation_root(token)

def test_import_transaction_rolls_back_all_images_on_late_save_failure(client_workspace,monkeypatch):
    client,project,source=client_workspace
    payload={'documents':[{'imagePath':name,'imageWidth':100,'imageHeight':80,'shapes':[{'label':'NG','shape_type':'rectangle','points':[[1,2],[20,30]]}]} for name in ['a.png','b.png']]}
    preview=client.post('/api/dataset/formats/import',json={'format':'labelme','payload':payload}).json()['preview']
    original=rdm.routes_annotation.save_annotations;calls=[]
    def fail_later(req):
        calls.append(req.image_id)
        if len(calls)==2: raise rdm.HTTPException(500,detail='injected write failure')
        return original(req)
    monkeypatch.setattr(rdm.routes_annotation,'save_annotations',fail_later)
    result=client.post('/api/dataset/formats/import',json={'format':'labelme','payload':payload,'mode':'apply','expected_revisions':{r['image_uuid']:r['revision'] for r in preview}})
    assert result.status_code==500
    studio=dataset_annotation_dir(source,Path(project['annotations_dir']),use_scope=False)
    assert not (studio/'a.json').exists() and not (studio/'b.json').exists()
    rows=client.get('/api/dataset/metadata').json()['items']
    assert all(r['workflow_state']=='unworked' for r in rows)
    assert all(not any(a['action']=='annotation_changed' for a in r['audit']) for r in rows)

def test_bulk_metadata_is_atomic_and_never_bulk_approves(client_workspace):
    client,project,source=client_workspace
    rows=client.get('/api/dataset/metadata').json()['items']
    response=client.post('/api/dataset/metadata/bulk',json={'actor':'Kim','items':[{'image_uuid':r['image_uuid'],'expected_revision':r['revision']} for r in rows],'changes':{'lot':'L88','group':'G1'}})
    assert response.status_code==200,response.text
    assert all(r['lot']=='L88' and r['workflow_state']=='unworked' for r in response.json()['items'])
    stale=client.post('/api/dataset/metadata/bulk',json={'actor':'Lee','items':[{'image_uuid':r['image_uuid'],'expected_revision':r['revision']} for r in rows],'changes':{'product':'wrong'}})
    assert stale.status_code==409
    assert all(not r['product'] for r in client.get('/api/dataset/metadata').json()['items'])

def test_structured_coco_group_apply_gallery_and_train_view_agree(client_workspace):
    client,project,source=client_workspace
    from backend.api import routes_dataset
    client.app.include_router(routes_dataset.router)
    data={'images':[{'id':i,'file_name':name,'width':100,'height':80} for i,name in enumerate(['a.png','b.png'],1)],'categories':[{'id':1,'name':'NG'}], 'annotations':[{'id':i,'image_id':i,'category_id':1,'bbox':[1,2,20,30]} for i in [1,2]]}
    (source/'annotations.json').write_text(json.dumps(data))
    rows=client.get('/api/dataset/metadata').json()['items']
    for i,row in enumerate(rows):client.patch('/api/dataset/metadata/'+row['image_uuid'],json={'actor':'Kim','expected_revision':row['revision'],'changes':{'lot':f'L{i}'}})
    result=client.post('/api/dataset/metadata/split',json={'group_by':['lot'],'train_ratio':.5,'val_ratio':.5,'test_ratio':0,'apply':True,'actor':'Kim'})
    assert result.status_code==200,result.text
    assert result.json()['applied'] and result.json()['split']=={'train':1,'val':1,'test':0}
    from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
    from backend.engine.grouped_dataset_views import load_manifest_dataset
    token=set_request_split_root(Path(project['dataset_dir'])/'splits')
    try:
        imported=client.post('/api/dataset/import',json={'folder_path':str(source),'task':'detection','validate_images':False})
        assert imported.status_code==200,imported.text
        assert imported.json()['split']=={'train':1,'val':1,'test':0}
        gallery=client.get('/api/dataset/images',params={'folder_path':str(source),'task':'detection','split':'train'})
        assert gallery.status_code==200,gallery.text
        train=load_manifest_dataset('detection',source,'train')
        expected_path=str(source/train.images[train.image_ids[0]]['file_name'])
        assert gallery.json()['total']==1 and gallery.json()['items'][0]['file_path']==expected_path
    finally:reset_request_split_root(token)
