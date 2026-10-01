"""Independent clients, session actors, scoped members and immutable training receipts."""
import importlib
import json
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from backend.main import create_app


def connect_router(app):
    assert importlib.util.find_spec('backend.api.routes_team_data') is not None, 'Team-data API is not implemented'
    route = importlib.import_module('backend.api.routes_team_data').router
    if not any(getattr(entry, 'path', '') == '/api/team-data' for entry in app.routes): app.include_router(route)


@pytest.fixture
def client_workspace(tmp_path):
    app = create_app(str(tmp_path / 'registry')); connect_router(app)
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name':'Team data', 'task':'classification'}).json()
    source = tmp_path / 'source'; source.mkdir()
    Image.new('RGB', (40,30), 'white').save(source / 'a.png')
    Image.new('RGB', (40,30), 'black').save(source / 'b.png')
    updated=client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    assert updated.status_code == 200
    project=updated.json()
    return client, project, source


def test_api_lease_conflict_actor_and_token_redaction(client_workspace):
    client, project, source = client_workspace
    assert client.get('/api/team-data').json()['actor'] is None
    assert client.put('/api/team-data/settings', json={'expected_revision':1, 'actor':'owner','changes':{'editing_enabled':True}}).status_code == 200
    image = client.get('/api/team-data/queue').json()['items'][0]
    path = '/api/team-data/images/' + image['image_uuid'] + '/lease/'
    lease = client.post(path + 'acquire', json={'expected_revision':image['revision'],'actor':'Lee'}).json()
    second = TestClient(client.app, headers={'X-Vision-Token':client.app.state.api_token})
    conflict = second.post(path + 'acquire',json={'expected_revision':lease['image']['revision'],'actor':'Kim'})
    assert conflict.status_code == 409
    assert 'token_hash' not in conflict.text
    loaded = client.get('/api/annotations/a',params={'file_path':str(source/'a.png')})
    assert loaded.status_code == 200 and loaded.json()['metadata']['team']['edit_lease']['owner'] == 'Lee'
    assert 'token_hash' not in loaded.text and lease['lease_token'] not in loaded.text
    missing = client.post('/api/annotations/save',json={'image_id':'a','image_path':str(source/'a.png'),'actor':'Lee','annotations':[],'expected_revision':lease['image']['revision']})
    assert missing.status_code == 409
    saved = client.post('/api/annotations/save',json={'image_id':'a','image_path':str(source/'a.png'),'actor':'Lee','annotations':[],
        'expected_revision':lease['image']['revision'],'lease_token':lease['lease_token']})
    assert saved.status_code == 200,saved.text
    assert saved.json()['metadata']['team']['annotation_actor'] == 'Lee' and 'token_hash' not in saved.text
    missing_revision=client.post('/api/annotations/save',json={'image_id':'a','image_path':str(source/'a.png'),'actor':'Lee','annotations':[], 'lease_token':lease['lease_token']})
    assert missing_revision.status_code==409
    stale_metadata=client.patch('/api/dataset/metadata/'+image['image_uuid'],json={'expected_revision':image['revision'],'actor':'Lee','changes':{'lot':'old'}})
    assert stale_metadata.status_code==409 and 'token_hash' not in stale_metadata.text


def test_shared_members_actor_cannot_be_forged_and_roles_cannot_bypass(client_workspace, tmp_path):
    from backend.engine.shared_accounts import AccountStore
    app = create_app(str(tmp_path/'shared-registry'), shared_auth_dir=str(tmp_path/'accounts')); connect_router(app)
    store = app.state.accounts; admin = store.bootstrap('owner','long password 123')
    users = {name: store.create_user(name,'long password 456') for name in ('labeler','reviewer','viewer','stranger')}
    def user_client(name, password='long password 456'):
        login = store.login(name,password)
        return TestClient(app,headers={'Authorization':'Bearer '+login['token']})
    owner = user_client('owner','long password 123')
    project = owner.post('/api/project/create',json={'name':'Shared team'}).json()
    source = tmp_path/'shared-source'; source.mkdir(); Image.new('RGB',(40,30)).save(source/'a.png')
    assert owner.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code == 200
    for name in ('labeler','reviewer','viewer'):
        store.set_membership(project['id'],users[name]['id'],name,admin['id'])
        store.select_project(users[name]['id'],project['id'])
    labeler=user_client('labeler'); reviewer=user_client('reviewer'); viewer=user_client('viewer')
    workspace = labeler.get('/api/team-data').json()
    assert workspace['actor']['name'] == 'labeler' and workspace['actor']['role'] == 'labeler'
    assert {member['name'] for member in workspace['members']} == {'owner','labeler','reviewer','viewer'}
    assert {member['name'] for member in labeler.get('/api/accounts/projects/'+project['id']+'/members').json()['members']} == {'owner','labeler','reviewer','viewer'}
    settings={'expected_revision':1,'actor':'owner','changes':{'editing_enabled':True,'review_enabled':True}}
    assert labeler.put('/api/team-data/settings',json=settings).status_code == 403
    assert reviewer.put('/api/team-data/settings',json=settings).status_code == 200
    row=labeler.get('/api/team-data/queue').json()['items'][0]
    unknown=owner.post('/api/team-data/images/'+row['image_uuid']+'/assign',json={'expected_revision':row['revision'],'actor':'owner','assignee':'stranger'})
    assert unknown.status_code == 422,unknown.text
    assert viewer.post('/api/team-data/images/'+row['image_uuid']+'/lease/acquire',json={'expected_revision':row['revision'],'actor':'owner'}).status_code == 403
    acquired=labeler.post('/api/team-data/images/'+row['image_uuid']+'/lease/acquire',json={'expected_revision':row['revision'],'actor':'owner'})
    assert acquired.status_code==200,acquired.text
    assert acquired.json()['image']['team']['edit_lease']['owner']=='labeler'
    assert labeler.post('/api/team-data/images/'+row['image_uuid']+'/review',json={'expected_revision':acquired.json()['image']['revision'],'actor':'reviewer','decision':'approve'}).status_code==403
    example=reviewer.post('/api/team-data/books',json={'expected_version':0,'actor':'owner','title':'Shared book',
        'categories':[{'id':0,'name':'OK','color':'#10b981','examples':[{'relative_path':'a.png','caption':'Reference'}]}]})
    assert example.status_code==200,example.text
    latest=labeler.get('/api/team-data/queue').json()['items'][0]
    batch=labeler.post('/api/annotations/batch_save',json={'items':[{'image_id':'a','image_path':str(source/'a.png'),
        'actor':'reviewer','lease_token':acquired.json()['lease_token'],'expected_revision':latest['revision'],
        'annotations':[{'type':'tag','label':'OK','category_id':0,'is_normal':True}]}]})
    assert batch.status_code==200,batch.text
    assert batch.json()['results'][0]['metadata']['team']['annotation_actor']=='labeler'


@pytest.mark.parametrize('mode',['single','batch','import'])
def test_shared_omitted_actor_is_session_author_and_cannot_self_approve(tmp_path,mode):
    app=create_app(str(tmp_path/'registry'),shared_auth_dir=str(tmp_path/'accounts'))
    app.state.accounts.bootstrap('owner','long password 123')
    login=app.state.accounts.login('owner','long password 123')
    owner=TestClient(app,headers={'Authorization':'Bearer '+login['token']})
    owner.post('/api/project/create',json={'name':'Shared review','task':'classification'})
    source=tmp_path/'source';source.mkdir();Image.new('RGB',(40,30)).save(source/'a.png')
    owner.put('/api/project/update',json={'source_dataset_dir':str(source)})
    owner.put('/api/team-data/settings',json={'expected_revision':1,'actor':'owner','changes':{'review_enabled':True,'prevent_self_review':True}})
    payload={'image_id':'a','image_path':str(source/'a.png'),'image_width':40,'image_height':30,'annotations':[{'type':'tag','label':'OK','category_id':0,'is_normal':True}]}
    if mode=='single':saved=owner.post('/api/annotations/save',json=payload)
    elif mode=='batch':saved=owner.post('/api/annotations/batch_save',json={'items':[payload]})
    else:
        exchange={'format':'labelme','payload':[{'imagePath':'a.png','imageWidth':40,'imageHeight':30,'shapes':[{'label':'OK','shape_type':'rectangle','points':[[1,1],[10,10]]}]}]}
        preview=owner.post('/api/dataset/formats/import',json=exchange)
        assert preview.status_code==200,preview.text
        first=preview.json()['preview'][0]
        saved=owner.post('/api/dataset/formats/import',json={**exchange,'mode':'apply','expected_revisions':{first['image_uuid']:first['revision']}})
    assert saved.status_code==200,saved.text
    row=owner.get('/api/team-data/queue').json()['items'][0]
    assert row['team']['annotation_actor']=='owner'
    vote=owner.post('/api/team-data/images/'+row['image_uuid']+'/review',json={'expected_revision':row['revision'],'decision':'approve'})
    assert vote.status_code in (409,422),vote.text
    assert '자신' in vote.text


def test_version_restore_clears_votes_and_lease_without_losing_book_history(client_workspace):
    client,project,source=client_workspace
    settings=client.put('/api/team-data/settings',json={'expected_revision':1,'actor':'owner','changes':{'review_enabled':True}})
    assert settings.status_code==200
    saved=client.post('/api/annotations/save',json={'image_id':'a','image_path':str(source/'a.png'),'actor':'Lee','annotations':[{'type':'tag','label':'OK','is_normal':True,'category_id':0}]}).json()['metadata']
    version=client.post('/api/dataset/versions',json={'name':'Before review'}).json()
    reviewed=client.post('/api/team-data/images/'+saved['image_uuid']+'/review',json={'expected_revision':saved['revision'],'actor':'Kim','decision':'approve'}).json()['image']
    leased=client.post('/api/team-data/images/'+saved['image_uuid']+'/lease/acquire',json={'expected_revision':reviewed['revision'],'actor':'Lee'}).json()
    book=client.post('/api/team-data/books',json={'expected_version':0,'actor':'owner','title':'Guidance','categories':[{'id':0,'name':'OK','color':'#10b981','definition':'Normal','examples':[]}]}).json()
    restored=client.post('/api/dataset/versions/'+version['id']+'/restore')
    assert restored.status_code==200,restored.text
    row=client.get('/api/dataset/metadata/image',params={'image_path':str(source/'a.png')}).json()
    assert row['team']['reviews']==[] and row['team']['edit_lease'] is None
    assert row['workflow_state']=='needs_review'
    assert client.get('/api/team-data').json()['book']['sha256']==book['sha256']
    assert any(entry['action']=='invalidated' for entry in row['team']['history'])


def test_training_provenance_pins_book_policy_and_rejects_mutated_receipt(client_workspace):
    from backend.engine.training_provenance import bind_training_version,validate_training_binding
    client,project,source=client_workspace
    client.post('/api/team-data/books',json={'expected_version':0,'actor':'owner','title':'Guidance','categories':[{'id':1,'name':'Scratch','color':'#f59e0b','examples':[]}]})
    from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root
    a=set_request_annotation_root(Path(project['annotations_dir']));p=set_request_project_root(Path(project['project_dir']))
    try:
        binding=bind_training_version(project,source)
        assert binding['team_data']['book_version']==1
        receipt=Path(binding['version_dir'])/'team-data.json'
        assert json.loads(receipt.read_text())==binding['team_data']
        validate_training_binding(binding)
        value=json.loads(receipt.read_text());value['book_version']=999;receipt.write_text(json.dumps(value))
        with pytest.raises(ValueError,match='Team-data'):validate_training_binding(binding)
    finally:reset_request_project_root(p);reset_request_annotation_root(a)


def test_training_rejects_guidance_or_eligibility_changed_after_binding(client_workspace):
    from backend.engine.training_provenance import bind_training_version,validate_training_binding
    client,project,source=client_workspace
    binding=bind_training_version(project,source)
    client.post('/api/team-data/books',json={'expected_version':0,'actor':'owner','title':'Changed guidance','categories':[{'id':1,'name':'Scratch','color':'#f59e0b','examples':[]}]})
    with pytest.raises(ValueError,match='Team-data'):validate_training_binding(binding)


def test_desktop_training_still_rejects_a_changed_active_labelset(client_workspace):
    from backend.engine.training_provenance import bind_training_version,validate_training_binding
    client,project,source=client_workspace
    binding=bind_training_version(project,source)
    path=Path(project['project_dir'])/'project.json'
    configuration=json.loads(path.read_text());configuration['active_labelset_id']='ls_other'
    path.write_text(json.dumps(configuration))
    with pytest.raises(ValueError,match='active labelset'):validate_training_binding(binding)


def test_preflight_does_not_allow_only_approved_validation_images_to_look_trainable(client_workspace,monkeypatch):
    from backend.api import routes_training_workspace as workspace_route
    from backend.api.routes_dataset import _write_split_manifest
    from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
    client,project,source=client_workspace
    for name in ('a','b'):
        client.post('/api/annotations/save',json={'image_id':name,'image_path':str(source/(name+'.png')),'actor':'Lee','annotations':[{'type':'tag','label':'OK','is_normal':True,'category_id':0}]})
    split_token=set_request_split_root(Path(project['dataset_dir'])/'splits')
    try:_write_split_manifest(source,{'a.png':'train','b.png':'val'},0)
    finally:reset_request_split_root(split_token)
    client.put('/api/team-data/settings',json={'expected_revision':1,'actor':'owner','changes':{'approved_only_training':True}})
    row=next(row for row in client.get('/api/team-data/queue').json()['items'] if row['relative_path']=='b.png')
    client.patch('/api/dataset/metadata/'+row['image_uuid'],json={'expected_revision':row['revision'],'actor':'reviewer','changes':{'workflow_state':'approved'}})
    monkeypatch.setattr(workspace_route,'model_readiness',lambda *args:{'ready':True,'runtime':{'available':True},'dependencies':{'missing':[]},'weights':{'state':'file_available'},'next_actions':[]})
    checked=client.post('/api/training-workspace/readiness',json={'task':'classification','model':'resnet18','device':'cpu'})
    assert checked.status_code==200,checked.text
    assert not checked.json()['ready'] and checked.json()['input']['split_counts']['train']==0
    assert checked.json()['input']['split_counts']['val']==1
    assert any('학습 이미지' in reason for reason in checked.json()['next_actions'])
    assert not list(Path(project['models_dir']).glob('job_*'))


def test_specialist_explicit_manifest_cannot_bypass_approved_source_policy(client_workspace):
    from backend.engine.training_provenance import bind_family_training
    from backend.engine.ocr import write_ocr_manifest
    client,project,source=client_workspace
    write_ocr_manifest(source,[{'image':'a.png','text':'A','split':'train'},{'image':'b.png','text':'A','split':'val'}])
    enabled=client.put('/api/team-data/settings',json={'expected_revision':1,'actor':'owner','changes':{'approved_only_training':True}})
    assert enabled.status_code==200
    with pytest.raises(ValueError,match='approved'):bind_family_training(project,source,'ocr')


def test_archive_restore_retains_guidance_but_invalidates_live_team_review_and_leases(client_workspace,tmp_path):
    client,project,source=client_workspace
    client.put('/api/team-data/settings',json={'expected_revision':1,'actor':'owner','changes':{'review_enabled':True}})
    book=client.post('/api/team-data/books',json={'expected_version':0,'actor':'owner','title':'Guidance','categories':[{'id':0,'name':'OK','color':'#10b981','examples':[{'relative_path':'a.png','caption':'Reference'}]}]}).json()
    saved=client.post('/api/annotations/save',json={'image_id':'a','image_path':str(source/'a.png'),'actor':'Lee','annotations':[{'type':'tag','label':'OK','is_normal':True,'category_id':0}]}).json()['metadata']
    approved=client.post('/api/team-data/images/'+saved['image_uuid']+'/review',json={'expected_revision':saved['revision'],'actor':'Kim','decision':'approve'}).json()['image']
    client.post('/api/team-data/images/'+saved['image_uuid']+'/lease/acquire',json={'expected_revision':approved['revision'],'actor':'Lee'})
    backup=client.post('/api/project/backup',json={'destination_dir':str(tmp_path/'backup')}).json()
    restored=client.post('/api/project/restore',json={'archive_path':backup['archive_path'],'target_dir':str(tmp_path/'restored')})
    assert restored.status_code==200,restored.text
    workspace=client.get('/api/team-data').json()
    assert workspace['book']['sha256']==book['sha256']
    row=next(row for row in client.get('/api/team-data/queue').json()['items'] if row['relative_path']=='a.png')
    assert row['workflow_state']=='needs_review' and row['team']['reviews']==[] and row['team']['edit_lease'] is None
    assert any(event['action']=='invalidated' for event in row['team']['history'])


def test_book_missing_saved_class_is_a_readiness_blocker_without_losing_labels(client_workspace):
    client,project,source=client_workspace
    saved=client.post('/api/annotations/save',json={'image_id':'a','image_path':str(source/'a.png'),'actor':'Lee','annotations':[{'type':'bbox','label':'Scratch','category_id':1,'bbox':[2,3,10,12]}]}).json()
    assert saved['status']=='saved'
    book=client.post('/api/team-data/books',json={'expected_version':0,'actor':'owner','title':'Guidance','categories':[{'id':0,'name':'OK','color':'#10b981','examples':[]}]})
    assert book.status_code==200
    readiness=client.get('/api/team-data/readiness').json()
    assert not readiness['ready'] and any('Scratch' in reason for reason in readiness['blockers'])
    assert client.get('/api/annotations/a',params={'file_path':str(source/'a.png')}).json()['annotations'][0]['label']=='Scratch'
    from backend.engine.training_provenance import bind_training_version
    with pytest.raises(ValueError,match='라벨북'):bind_training_version(project,source)
