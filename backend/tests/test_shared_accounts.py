from pathlib import Path
import pytest


def test_request_scope_never_resolves_global_checkpoint(tmp_path, monkeypatch):
    from backend.engine.annotation_storage import set_request_project_root,reset_request_project_root,set_request_shared_scope,reset_request_shared_scope
    from backend.engine.checkpoint_paths import trusted_checkpoint
    monkeypatch.chdir(tmp_path)
    legacy=tmp_path/'models'/'job_foreign';legacy.mkdir(parents=True)
    (legacy/'best_model.pt').write_bytes(b'foreign')
    token=set_request_project_root(tmp_path/'own')
    shared=set_request_shared_scope(True)
    try:assert trusted_checkpoint('job_foreign') is None
    finally:reset_request_shared_scope(shared);reset_request_project_root(token)


def test_shared_telemetry_hides_foreign_jobs_and_revoked_sessions(tmp_path):
    from backend.engine.shared_accounts import AccountStore
    from backend.api.websocket_telemetry import TelemetryBroadcaster
    from types import SimpleNamespace
    store=AccountStore(tmp_path/'accounts.sqlite');admin=store.bootstrap('admin','long password 123')
    user=store.create_user('viewer','long password 456',administrator=False)
    root=tmp_path/'project';root.mkdir();store.register_project('own',str(root),admin['id'])
    store.set_membership('own',user['id'],'viewer',admin['id']);session=store.login('viewer','long password 456')
    ws=SimpleNamespace(scope={'state':{'account_user':user,'account_session_token':session['token'],'scoped_project':{'id':'own','models_dir':str(root/'models')}},'app':SimpleNamespace(state=SimpleNamespace(accounts=store))})
    bc=TelemetryBroadcaster()
    assert bc._can_receive(ws,{'event':'hardware_stats','data':{}})
    assert not bc._can_receive(ws,{'event':'training_started','data':{'job_id':'job_foreign','output_dir':str(tmp_path/'other')}})
    from unittest.mock import patch
    with patch('backend.api.routes_training.training_job_manager.get_job',return_value=SimpleNamespace(output_dir=str(root/'models'/'job_own'))):
        assert bc._can_receive(ws,{'event':'epoch_progress','data':{'job_id':'job_own'}})
        store.logout(session['token'])
        assert not bc._can_receive(ws,{'event':'epoch_progress','data':{'job_id':'job_own'}})


def test_account_password_sessions_and_project_roles_are_persisted(tmp_path):
    from backend.engine.shared_accounts import AccountStore
    store=AccountStore(tmp_path/'accounts.sqlite')
    admin=store.bootstrap('admin','long password 123')
    with pytest.raises(ValueError):store.bootstrap('second','long password 123')
    reviewer=store.create_user('reviewer','long password 456',administrator=False)
    session=store.login('reviewer','long password 456')
    assert AccountStore(tmp_path/'accounts.sqlite').authenticate(session['token'])['id']==reviewer['id']
    with pytest.raises(ValueError):store.login('reviewer','wrong password')
    store.register_project('project1',str(tmp_path/'project'),admin['id'])
    store.set_membership('project1',reviewer['id'],'reviewer',admin['id'])
    assert store.project_role(reviewer['id'],'project1')=='reviewer'
    assert store.project_role(reviewer['id'],'foreign') is None
    store.logout(session['token'])
    with pytest.raises(ValueError):store.authenticate(session['token'])
    assert b'long password' not in (tmp_path/'accounts.sqlite').read_bytes()


def test_shared_api_requires_accounts_enforces_roles_and_scopes_projects(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app=create_app(project_dir=str(tmp_path/'projects'),shared_auth_dir=str(tmp_path/'auth'))
    desktop=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    result=desktop.post('/api/accounts/bootstrap',json={'username':'admin','password':'long password 123'})
    assert result.status_code==200,result.text
    admin=TestClient(app)
    login=admin.post('/api/accounts/login',json={'username':'admin','password':'long password 123'}).json()
    admin.headers['Authorization']='Bearer '+login['token']
    first=admin.post('/api/project/create',json={'name':'Shared A'}).json()
    created=admin.post('/api/accounts/users',json={'username':'viewer','password':'long password 456','administrator':False})
    assert created.status_code==200,created.text
    grant=admin.put('/api/accounts/projects/'+first['id']+'/members',json={'user_id':created.json()['id'],'role':'viewer'})
    assert grant.status_code==200,grant.text
    viewer=TestClient(app)
    viewer_login=viewer.post('/api/accounts/login',json={'username':'viewer','password':'long password 456'}).json()
    viewer.headers['Authorization']='Bearer '+viewer_login['token']
    assert viewer.post('/api/accounts/select-project',json={'project_id':first['id']}).status_code==200
    assert viewer.get('/api/project/current').json()['id']==first['id']
    assert viewer.put('/api/project/update',json={'name':'forbidden'}).status_code==403
    second=admin.post('/api/project/create',json={'name':'Shared B'}).json()
    assert viewer.get('/api/project/current').json()['id']==first['id']
    assert viewer.post('/api/accounts/select-project',json={'project_id':second['id']}).status_code==403
    assert TestClient(app).get('/api/project/current').status_code==401
    admin.put('/api/accounts/projects/'+first['id']+'/members',json={'user_id':created.json()['id'],'role':'labeler'})
    import json
    from PIL import Image
    Image.new('RGB',(8,8),'white').save(tmp_path/'foreign.png')
    payload={'image_path':str(tmp_path/'foreign.png'),'seed_x':1,'seed_y':1}
    response=viewer.post('/api/annotations/auto-select',content=json.dumps(payload))
    assert response.status_code==403,response.text
    admin.put('/api/accounts/projects/'+first['id']+'/members',json={'user_id':created.json()['id'],'role':'owner'})
    assert viewer.put('/api/project/update',json={'source_dataset_dir':str(tmp_path)}).status_code==403
    response=viewer.post('/api/annotations/auto-select',content=json.dumps(payload),headers={'Content-Type':'application/vision+json'})
    assert response.status_code==403,response.text
