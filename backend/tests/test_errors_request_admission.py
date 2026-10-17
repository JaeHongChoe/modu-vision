"""Real fresh create_app middleware/Storage writes, not a synthetic route."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

def files(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}

@pytest.fixture
def original_app(tmp_path,monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from fastapi.testclient import TestClient
    admission=BackendWorkAdmission()
    monkeypatch.setattr(main,'backend_work_admission',lambda:admission)
    # The health control exercises the real route without querying a GPU.
    monkeypatch.setattr(main,'get_device',lambda:'cpu')
    monkeypatch.setattr(main,'get_device_info',lambda dev:SimpleNamespace(device_type='cpu',device_name='Owned source test CPU',is_accelerated=False,torch_version='controlled'))
    root=tmp_path/'fresh-projects';app=main.create_app(project_dir=str(root))
    client=TestClient(app)
    # Deliberately do not enter lifespan: startup tasks are a separate gate.
    try:yield root,app,client,admission
    finally:client.close()

def test_actual_errors_GET_creates_project_and_requires_sticky_unsupported(original_app):
    root,app,client,state=original_app
    before=files(root)
    assert not (root/'default_project').exists()
    with app.state.context_registry._db() as db:assert db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]==0
    response=client.get('/api/errors',headers={'x-vision-token':app.state.api_token})
    assert response.status_code==200 and response.json()['errors']
    created=files(root);assert created!=before
    assert (root/'default_project/project.json').is_file()
    assert app.state.current_project['project_dir']==str(root/'default_project')
    with app.state.context_registry._db() as db:assert db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]==1
    assert state.snapshot()=={'active_scopes':0,'unsupported':['uncovered_request_protocol']}, {
        'actual_created_files':sorted(set(created)-set(before)),
        'actual_changed_files':sorted(p for p in set(before)&set(created) if before[p]!=created[p]),
        'state':state.snapshot()}
    client.get('/health')
    state.close()
    assert state.drain(.03)=={'status':'refused','active_scopes':0,'unsupported':['uncovered_request_protocol']}

def test_actual_error_catalog_body_is_preserved_and_prior_unsupported_never_clears(original_app):
    from fastapi.encoders import jsonable_encoder
    from backend.utils.error_catalog import get_all_errors,get_error
    root,app,client,state=original_app
    state.uncovered('startup_background_protocols')
    headers={'x-vision-token':app.state.api_token}
    response=client.get('/api/errors',headers=headers)
    assert response.status_code==200 and response.json()==jsonable_encoder({'errors':list(get_all_errors().values())})
    item=client.get('/api/errors/ERR_001',headers=headers)
    assert item.status_code==200 and item.json()==jsonable_encoder(get_error('ERR_001'))
    client.get('/health')
    assert state.snapshot()['unsupported']==['startup_background_protocols','uncovered_request_protocol']

def test_real_health_control_has_no_project_Storage_writes_and_can_drain(original_app):
    root,app,client,state=original_app
    before=files(root)
    response=client.get('/health')
    assert response.status_code==200 and response.json()['status']=='ok'
    assert files(root)==before and not (root/'default_project').exists()
    assert state.snapshot()=={'active_scopes':0,'unsupported':[]}
    state.close()
    assert state.drain(.03)=={'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}

def test_closed_real_errors_chain_rejects_before_ProjectContext_or_Storage(original_app):
    root,app,client,state=original_app
    before=files(root);state.close()
    response=client.get('/api/errors',headers={'x-vision-token':app.state.api_token})
    assert response.status_code==409 and response.json()=={'detail':'Owned backend admission is closed for drain'}
    assert files(root)==before and not (root/'default_project').exists()
    assert state.snapshot()=={'active_scopes':0,'unsupported':[]}

@pytest.mark.parametrize('scope_type,method',[('http','GET'),('http','POST'),('websocket',None)])
def test_errors_path_is_not_an_authority_exemption_for_any_transport(scope_type,method):
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    state=BackendWorkAdmission();assert state.enter({'type':scope_type,'method':method,'path':'/api/errors'})
    state.leave();state.close()
    assert state.drain(.03)=={'status':'refused','active_scopes':0,'unsupported':['uncovered_request_protocol']}
