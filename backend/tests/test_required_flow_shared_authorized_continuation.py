"""Root-only real SQLite/shared-auth component controls, not a model/native pass.

The factory uses the actual owned shared arguments. Component controls retain
original SharedAuthorizationMiddleware/ProjectContextMiddleware and an explicit
data-only ASGI downstream; no original model/remote callback is replaced.
"""
import copy
from dataclasses import replace
import json
from pathlib import Path
from backend.tests.private_qa_fixtures import private_qa_root

# External qualification is explicit; absence is a collection failure, never a skip.
J = private_qa_root()
import pytest
pytestmark = pytest.mark.private_qualification
from types import SimpleNamespace
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from backend import main
from backend.api.shared_authorization import SharedAuthorizationMiddleware
from backend.engine.application_launch_handshake import _new_original_work_admission
from backend.engine import required_flow_passive_capture as capture

GRAPH=json.loads((J/'S7-02-installed-retained-rotation-segmentation-fixture-Source01/original-graph.json').read_bytes())
FACTS=json.loads((J/'S7-02-required-flow-passive-startup-binding-Source02/profile-facts.json').read_bytes())
IMAGE=J/'S7-02-authentic-retained-family-Root-inputs-01/rotation-image.png'
PASSWORD='owned control password 123'

class Profile:
    id=FACTS['normalized_model_dump']['id']
    def model_dump(self):return dict(FACTS['normalized_model_dump'])

def owned(tmp_path,monkeypatch):
    admission=_new_original_work_admission()
    monkeypatch.setattr(main,'backend_work_admission',lambda:admission)
    app=main.create_app(project_dir=str(tmp_path/'projects'),shared_auth_dir=str(tmp_path/'accounts'))
    selector=getattr(app.state,'required_retained_flow_capture',None)
    assert app.state.accounts is not None
    assert selector is not None  # exact owned shared shape causal assertion
    client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    try:
        r=client.post('/api/accounts/bootstrap',json={'username':'admin','password':PASSWORD});assert r.status_code==200,r.text
        r=client.post('/api/accounts/login',json={'username':'admin','password':PASSWORD});assert r.status_code==200,r.text
        session=r.json();client.headers['Authorization']='Bearer '+session['token']
        r=client.post('/api/project/create',json={'name':'Own shared passive control'});assert r.status_code==200,r.text
        project=r.json()
        r=client.put('/api/project/update',json={'source_dataset_dir':str(IMAGE.parent)});assert r.status_code==200,r.text
    finally:client.close()
    return app,selector,session,project

def body():return {'pipeline':GRAPH,'image_path':str(IMAGE),'device':'cuda','execution_target':'selected_compute','compute_profile_id':Profile.id}

def observation_stack(app,selector,seen,mode='capture'):
    async def downstream(scope,receive,send):
        request=Request(scope,receive);payload=await request.json()
        def observe():
            witness=selector.shared_owner.current(request)
            seen.update(witness=witness,request=request)
            assert selector.current(request)is scope
            with pytest.raises(ValueError):copy.copy(witness)
            if mode=='foreign-scope':
                with pytest.raises(ValueError):selector.current(Request(dict(scope)))
            if mode=='changed-decision':
                original=scope['state']['permission_decision'];scope['state']['permission_decision']=replace(original,allowed=False)
                try:
                    with pytest.raises(ValueError):selector.current(request)
                finally:scope['state']['permission_decision']=original
            if mode=='no-grant':
                assert capture.offer_original_required_flow_capture(selector,request,SimpleNamespace(id='other'),pipeline={},image_path=IMAGE,device='cpu',stop_node_id=None)is None
                return
            carrier=capture.offer_original_required_flow_capture(selector,request,Profile(),pipeline=payload['pipeline'],image_path=Path(payload['image_path']),device=payload['device'],stop_node_id=None)
            assert carrier is not None
            args=dict(profile_id=Profile.id,execution_profile_sha256=FACTS['normalized_execution_profile_defaultASCII_sha256'],pipeline=payload['pipeline'],image_sha256=capture.RETAINED_IMAGE_SHA,models=[{'job_id':j,'task':t,'checkpoint_sha256':s}for j,t,s in capture.RETAINED_MODELS])
            seen['grant']=capture.serialize_required_flow_grant(carrier,**args)
            with pytest.raises(ValueError):capture.serialize_required_flow_grant(carrier,**args)
        # Original framework ContextVar transfer into the original sync style.
        await run_in_threadpool(observe)
        await JSONResponse({'data_only_observation':True,'authority':False})(scope,receive,send)
    # Explicit controlled auth component; no production callback is substituted.
    app.middleware_stack=SharedAuthorizationMiddleware(main.ProjectContextMiddleware(downstream,project_app=app),project_app=app)

def test_actual_owned_shared_factory_binds_without_disabling_accounts(tmp_path,monkeypatch):
    app,selector,session,project=owned(tmp_path,monkeypatch)
    assert type(selector)is capture._OriginalRequiredFlowSelector
    assert selector.shared_owner.store is app.state.accounts
    assert selector.shared_owner.admission is app.state.owned_work_admission
    assert selector.shared_owner.auth_type is SharedAuthorizationMiddleware
    assert not selector.used

@pytest.mark.parametrize('mode',['capture','changed-decision','foreign-scope'])
def test_original_bearer_only_continuation_yields_one_private_grant(tmp_path,monkeypatch,mode):
    app,selector,session,project=owned(tmp_path,monkeypatch);seen={};observation_stack(app,selector,seen,mode)
    client=TestClient(app,headers={'Authorization':'Bearer '+session['token']})
    try:r=client.post('/api/flowchart/run',json=body())
    finally:client.close()
    assert r.status_code==200,r.text
    assert all(k.lower()!=b'x-vision-token'for k,v in seen['request'].scope['headers'])
    assert seen['grant']['runtime_authority']is False and seen['grant']['parent_credit']is False
    assert session['token']not in json.dumps(seen['grant'])
    assert selector.used and seen['witness'].active is False
    with pytest.raises(ValueError):selector.current(seen['request'])

@pytest.mark.parametrize('denial',['missing-session','expired-session','viewer-role','cookie-no-CSRF'])
def test_original_session_role_CSRF_denials_never_reach_observation(tmp_path,monkeypatch,denial):
    app,selector,session,project=owned(tmp_path,monkeypatch);seen={};observation_stack(app,selector,seen)
    headers={};kwargs={};url='http://testserver'
    if denial=='expired-session':app.state.accounts.logout(session['token']);headers={'Authorization':'Bearer '+session['token']}
    if denial=='viewer-role':
        user=app.state.accounts.create_user('viewer',PASSWORD)
        app.state.accounts.set_membership(project['id'],user['id'],'viewer',session['user']['id'],session_token=session['token'])
        other=app.state.accounts.login('viewer',PASSWORD);app.state.accounts.select_project(user['id'],project['id']);headers={'Authorization':'Bearer '+other['token']}
    if denial=='cookie-no-CSRF':
        cookie=app.state.accounts.login('admin',PASSWORD,transport='cookie');app.state.browser_origins=frozenset({'https://testserver'})
        headers={'Origin':'https://testserver'};kwargs={'cookies':{'vision_session':cookie['token']}};url='https://testserver'
    client=TestClient(app,headers=headers,base_url=url)
    try:r=client.post('/api/flowchart/run',json=body(),**kwargs)
    finally:client.close()
    assert r.status_code==(401 if denial in {'missing-session','expired-session'}else 403),r.text
    assert seen=={} and not selector.used and selector.issued is None

@pytest.mark.parametrize('mode',['capture','no-grant'])
def test_original_cookie_CSRF_unrelated_profile_keeps_normal_behavior(tmp_path,monkeypatch,mode):
    app,selector,session,project=owned(tmp_path,monkeypatch);seen={};observation_stack(app,selector,seen,mode)
    cookie=app.state.accounts.login('admin',PASSWORD,transport='cookie');app.state.browser_origins=frozenset({'https://testserver'})
    client=TestClient(app,base_url='https://testserver',headers={'Origin':'https://testserver','X-Vision-CSRF':cookie['csrf_token']})
    try:r=client.post('/api/flowchart/run',json=body(),cookies={'vision_session':cookie['token']})
    finally:client.close()
    assert r.status_code==200,r.text
    assert seen['witness'].active is False
    assert ('grant'in seen)==(mode=='capture') and selector.used==(mode=='capture')
    if 'grant'in seen:assert cookie['token']not in json.dumps(seen['grant'])

def test_unowned_shared_factory_keeps_default_none_and_denials(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'backend_work_admission',lambda:None)
    app=main.create_app(project_dir=str(tmp_path/'projects'),shared_auth_dir=str(tmp_path/'accounts'))
    assert app.state.accounts is not None and getattr(app.state,'required_retained_flow_capture',None)is None
    client=TestClient(app)
    try:r=client.post('/api/flowchart/run',json={})
    finally:client.close()
    assert r.status_code==401
