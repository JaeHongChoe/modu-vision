"""Root-only original FastAPI/SQLite/admission coupling; no model execution.

Use original conftest. Only the existing flow route's execution app is an
explicit non-model ASGI body. Original auth, project scope, middleware, real
carrier/consume and sync ContextVar transfer execute. The already-validated
bootstrap cache/home are controlled data, not native/lease evidence.
"""
import base64,hashlib,json,os,stat
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from backend import main
from backend.engine import application_launch_handshake as handshake
from backend.engine import required_flow_passive_capture as capture
from backend.tests import test_required_flow_shared_authorized_continuation as original

def owned_http(tmp_path,monkeypatch):
    root=(tmp_path/'owned-bootstrap-control').resolve();root.mkdir(mode=0o700)
    nonce='2'*32
    (root/'.application-runtime-homes').mkdir(mode=0o700)
    home=root/'.application-runtime-homes'/nonce;home.mkdir(mode=0o700)
    admission=handshake._new_original_work_admission()
    monkeypatch.setattr(handshake,'_CACHE',{'root':root,'proof':{'nonce':nonce},'context':handshake._context(),'admission':admission,'ready':True})
    monkeypatch.setattr(original,'_new_original_work_admission',lambda:admission)
    app,selector,session,project=original.owned(root,monkeypatch)
    owner=getattr(selector,'http_capture',None)
    assert owner is not None
    output=home/'required-flow-http'
    prepared=json.loads((output/'prepared.json').read_bytes())
    assert prepared['process_pid']==os.getpid() and prepared['capture_nonce']==selector.nonce
    assert prepared['permission_authority']is False and prepared['lease_authority']is False
    return app,selector,session,project,output

def original_route_control(app,selector,monkeypatch,seen):
    routes=[route for route in app.routes if getattr(route,'path',None)=='/api/flowchart/run']
    assert len(routes)==1
    async def nonmodel_execution_body(scope,receive,send):
        request=Request(scope,receive);body=await request.json();seen['calls']=seen.get('calls',0)+1
        def consume_original():
            # Real shared authorization continuation; no token/header override.
            seen['witness']=selector.shared_owner.current(request)
            carrier=capture.offer_original_required_flow_capture(selector,request,original.Profile(),pipeline=body['pipeline'],image_path=Path(body['image_path']),device=body['device'],stop_node_id=None)
            assert carrier is not None
            grant=capture.serialize_required_flow_grant(carrier,profile_id=original.Profile.id,execution_profile_sha256=original.FACTS['normalized_execution_profile_defaultASCII_sha256'],pipeline=body['pipeline'],image_sha256=capture.RETAINED_IMAGE_SHA,models=[{'job_id':j,'task':t,'checkpoint_sha256':s}for j,t,s in capture.RETAINED_MODELS])
            assert grant['nonce']==selector.nonce and grant['runtime_authority']is False
            seen['carrier']=carrier
        await run_in_threadpool(consume_original)
        await JSONResponse({'data_only_nonmodel_execution_body':True,'GPU_forward':False})(scope,receive,send)
    # Preserve the original outer middleware object and actual observer binding.
    # No production model/remote callback is monkeypatched or invoked.
    monkeypatch.setattr(routes[0],'app',nonmodel_execution_body)

def body(project):
    return {**original.body(),'image_id':'owned-control-image','project_id':project['id']}

def test_real_factory_shared_auth_original_ASGI_and_carrier_write_private_exact_terminal(tmp_path,monkeypatch):
    app,selector,session,project,output=owned_http(tmp_path,monkeypatch);seen={}
    observer=app.state.owned_work_admission._activity_observer
    original_middleware=observer._middleware;original_downstream=observer._downstream
    original_route_control(app,selector,monkeypatch,seen)
    client=TestClient(app,headers={'Authorization':'Bearer '+session['token']})
    try:response=client.post('/api/flowchart/run',json=body(project))
    finally:client.close()
    assert response.status_code==200,response.text
    terminal=json.loads((output/'terminal.json').read_bytes())
    assert terminal['complete']is True and terminal['refusals']==[]
    request=base64.b64decode(terminal['request_base64'],validate=True)
    assert json.loads(request)==body(project)
    assert base64.b64decode(terminal['response_base64'],validate=True)==response.content
    assert terminal['response_sha256']==hashlib.sha256(response.content).hexdigest()
    assert type(terminal['original_request_identity'])is int and terminal['original_request_identity']>0
    assert type(terminal['original_entered_sequence'])is int and terminal['original_released_sequence']>terminal['original_entered_sequence']
    assert observer._middleware is original_middleware and observer._downstream is original_downstream
    assert seen['calls']==1 and selector.used and seen['carrier'].used
    assert seen['witness'].active is False
    assert app.state.owned_work_admission._active==0
    assert 'uncovered_request_protocol'in app.state.owned_work_admission._unsupported
    for path in (output/'prepared.json',output/'terminal.json'):
        info=path.lstat();assert stat.S_ISREG(info.st_mode)and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1
        raw=path.read_bytes();assert session['token'].encode()not in raw and app.state.api_token.encode()not in raw
    assert terminal['headers_query_or_tokens_serialized']is False
    assert terminal['server_ASGI_send_receipt']is True and terminal['client_receive_proven']is False
    assert terminal['lease_authority']is False and terminal['parent_credit']is False

def test_real_original_shared_401_never_promotes_HTTP_body(tmp_path,monkeypatch):
    app,selector,session,project,output=owned_http(tmp_path,monkeypatch);seen={}
    original_route_control(app,selector,monkeypatch,seen)
    client=TestClient(app)
    try:response=client.post('/api/flowchart/run',json=body(project))
    finally:client.close()
    assert response.status_code==401 and seen=={} and not selector.used
    terminal=json.loads((output/'terminal.json').read_bytes())
    assert terminal['complete']is False and terminal['status']==401
    assert 'request_base64'not in terminal and 'response_base64'not in terminal
    assert 'original_carrier_not_consumed'in terminal['refusals']

def test_real_unowned_default_factory_has_no_private_recorder(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'backend_work_admission',lambda:None)
    app=main.create_app(project_dir=str(tmp_path/'projects'),shared_auth_dir=str(tmp_path/'accounts'))
    assert app.state.accounts is not None and getattr(app.state,'required_retained_flow_capture',None)is None
    assert not list(tmp_path.glob('**/required-flow-http'))
