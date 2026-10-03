"""Independent real HTTP authorization and audit review of extension work."""
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.main import create_app
from backend.engine import dataset_metadata as dm
from backend.engine.inspection_service import InspectionStore


@pytest.fixture
def shared(tmp_path):
    app=create_app(project_dir=str(tmp_path/'projects'),shared_auth_dir=str(tmp_path/'auth'))
    bootstrap=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    assert bootstrap.post('/api/accounts/bootstrap',json={'username':'admin','password':'long password 123'}).status_code==200
    admin=TestClient(app)
    session=admin.post('/api/accounts/login',json={'username':'admin','password':'long password 123'}).json()
    admin.headers['Authorization']='Bearer '+session['token']
    response=admin.post('/api/project/create',json={'name':'Shared audit','task':'segmentation'})
    assert response.status_code==200,response.text
    project=response.json()
    source=Path(project['project_dir'])/'source'
    source.mkdir()
    for name,color in [('train','black'),('test','white')]:
        Image.new('RGB',(8,8),color).save(source/f'{name}.png')
        (source/f'{name}.json').write_text(json.dumps({'imagePath':f'{name}.png','imageWidth':8,'imageHeight':8,'shapes':[]}))
    updated=admin.put('/api/project/update',json={'source_dataset_dir':str(source),'task':'segmentation'})
    assert updated.status_code==200,updated.text
    project=updated.json()
    split=Path(project['dataset_dir'])/'splits'/(hashlib.sha256(str(source.resolve()).encode()).hexdigest()+'.json')
    split.parent.mkdir(parents=True,exist_ok=True)
    split.write_text(json.dumps({'folder_path':str(source.resolve()),'assignments':{'train.png':'train','test.png':'test'}}))
    clients={}
    for username,role in [('reviewer','reviewer'),('labeler','labeler'),('viewer','viewer')]:
        user=admin.post('/api/accounts/users',json={'username':username,'password':'long password 456','administrator':False}).json()
        assert admin.put(f"/api/accounts/projects/{project['id']}/members",json={'user_id':user['id'],'role':role}).status_code==200
        client=TestClient(app)
        session=client.post('/api/accounts/login',json={'username':username,'password':'long password 456'}).json()
        client.headers['Authorization']='Bearer '+session['token']
        assert client.post('/api/accounts/select-project',json={'project_id':project['id']}).status_code==200
        clients[username]=client
    return project,clients


def test_shared_reviewer_can_create_drift_reference_and_labeler_cannot(shared):
    project,clients=shared
    store=InspectionStore(Path(project['project_dir'])/'runtime_service'/'state')
    path=store.state_dir/'uploads'/'capture.png'
    Image.new('RGB',(8,8),'red').save(path)
    job=store.enqueue(path,'http')
    store.claim()
    store.finish(job,result={'final_verdict':'NG'})
    registered=clients['reviewer'].post('/api/capture-intake/register',json={'job_ids':[job]})
    assert registered.status_code==200,registered.text
    candidate=registered.json()['candidates'][0]['candidate_id']
    payload={'candidate_ids':[candidate],'name':'Reference','actor':'spoofed-admin'}
    assert clients['labeler'].post('/api/capture-intake/drift/references',json=payload).status_code==403
    response=clients['reviewer'].post('/api/capture-intake/drift/references',json=payload)
    assert response.status_code==200,response.text
    assert response.json()['actor']=='reviewer'


def test_brightness_shared_actor_is_authenticated_and_viewer_cannot_derive(shared):
    project,clients=shared
    source=Path(project['source_dataset_dir'])
    image=source/'train.png'
    metadata=dm.metadata_for_path(Path(project['project_dir']),source,image,Path(project['annotations_dir']))
    payload={'image_path':str(image),'expected_revision':metadata['revision'],'expected_sha256':metadata['content_hash'],
             'operation':{'kind':'brightness','factor':2},'actor':'spoofed-admin'}
    assert clients['viewer'].post('/api/data-workbench/derived',json=payload).status_code==403
    response=clients['labeler'].post('/api/data-workbench/derived',json=payload)
    assert response.status_code==200,response.text
    assert response.json()['actor']=='labeler'
