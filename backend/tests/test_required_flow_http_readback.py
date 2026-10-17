"""Root-only real GET/auth/SQLite readback coupling. No model/GPU execution.

The real POST fixture substitutes only its disclosed non-model execution body.
GETs use the actual original saved-version/project handlers and auth stack.
"""
import base64,copy,hashlib,json,time
from dataclasses import replace
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.api import routes_flowchart
from backend.engine import required_flow_http_capture as observer
from backend.tests import test_required_flow_http_framework as original

SEED='3'*32;SAVED='4'*32

def ready(tmp_path,monkeypatch):
    app,selector,session,project,output=original.owned_http(tmp_path,monkeypatch)
    root=Path(project['project_dir'])
    project=json.loads((root/'project.json').read_bytes())  # Original update is persisted; create response predates it.
    for version in(SEED,SAVED):
        routes_flowchart._write_json(root/'flowcharts/versions'/f'{version}.json',{
            'version_id':version,'saved_at':'2026-10-15T00:00:00Z','saved_at_ns':1,
            'recipe_task':project['task'],'source_dataset_path':project['source_dataset_dir'],
            'pipeline':original.original.GRAPH})
    routes_flowchart._write_json(root/'flowcharts/active.json',{'version_id':SAVED,'project_id':project['id'],
        'labelset_id':project.get('active_labelset_id','default'),'recipe_task':project['task'],
        'source_dataset_path':project['source_dataset_dir']})
    seen={};original.original_route_control(app,selector,monkeypatch,seen)
    return app,selector,session,project,output,seen

def client(app,session):return TestClient(app,headers={'Authorization':'Bearer '+session['token']})
def rows(output):return [json.loads(path.read_bytes())for path in sorted(output.glob('readback-???.json'))]
def post(c,project):
    result=c.post('/api/flowchart/run',json=original.body(project));assert result.status_code==200,result.text
    return result

def test_initial_real_authenticated_GETs_stay_memory_only_then_promote_and_reopen_is_distinct(tmp_path,monkeypatch):
    app,selector,session,project,output,seen=ready(tmp_path,monkeypatch);c=client(app,session)
    try:
        p=c.get('/api/project/current');assert p.status_code==200,p.text
        g=c.get('/api/flowchart/pipelines/'+SEED);assert g.status_code==200,g.text
        assert rows(output)==[]  # Before POST no provisional body is persisted.
        post(c,project)
        reopened=c.get('/api/flowchart/pipelines/'+SAVED);assert reopened.status_code==200,reopened.text
    finally:c.close()
    records=rows(output);assert len(records)==3
    expected={('/api/project/current','before_post'):p.content,
        ('/api/flowchart/pipelines/'+SEED,'before_post'):g.content,
        ('/api/flowchart/pipelines/'+SAVED,'after_post'):reopened.content}
    assert len({row['original_request_identity']for row in records})==3
    for row in records:
        assert row['complete']is True and row['actual_original_shared_authorization']is True
        raw=base64.b64decode(row['response_base64'],validate=True)
        assert raw==expected[(row['path'],row['phase'])]
        assert row['response_sha256']==hashlib.sha256(raw).hexdigest()
        assert row['project_id']==project['id'] and row['actor_id']==session['user']['id']
        assert row['original_released_sequence']>row['original_entered_sequence']
        assert row['elapsed_monotonic_seconds']<10
        assert row['request_body_declared_empty']is True
        assert row['headers_query_or_tokens_serialized']is False
        assert row['server_ASGI_send_receipt']is True and row['client_receive_proven']is False
        assert row['lease_authority']is False and row['parent_credit']is False
    for path in output.glob('readback-???.json'):
        assert path.stat().st_mode&0o777==0o600
        assert session['token'].encode()not in path.read_bytes() and app.state.api_token.encode()not in path.read_bytes()
    assert seen['calls']==1 and app.state.owned_work_admission._active==0
    assert 'uncovered_request_protocol'in app.state.owned_work_admission._unsupported

def test_real_unauthenticated_initial_GET_never_promotes(tmp_path,monkeypatch):
    app,selector,session,project,output,_=ready(tmp_path,monkeypatch)
    c=TestClient(app)
    try:r=c.get('/api/project/current')
    finally:c.close()
    assert r.status_code==401
    c=client(app,session)
    try:post(c,project)
    finally:c.close()
    assert rows(output)==[] and json.loads((output/'terminal.json').read_bytes())['complete']is True

def test_real_authorized_foreign_actor_GET_is_not_post_lineage(tmp_path,monkeypatch):
    app,selector,session,project,output,_=ready(tmp_path,monkeypatch)
    other=app.state.accounts.create_user('other-reader',original.original.PASSWORD)
    app.state.accounts.set_membership(project['id'],other['id'],'trainer',session['user']['id'],session_token=session['token'])
    account=app.state.accounts.login('other-reader',original.original.PASSWORD)
    app.state.accounts.select_project(other['id'],project['id'])
    c=client(app,account)
    try:r=c.get('/api/project/current');assert r.status_code==200,r.text
    finally:c.close()
    c=client(app,session)
    try:post(c,project)
    finally:c.close()
    assert rows(output)==[]

def test_permission_decision_drift_refuses_data_without_changing_original_GET_response(tmp_path,monkeypatch):
    app,selector,session,project,output,_=ready(tmp_path,monkeypatch)
    route=next(route for route in app.routes if getattr(route,'path',None)=='/api/project/current')
    real=route.app
    async def drift(scope,receive,send):
        async def original_send(message):
            if message.get('type')=='http.response.start':
                state=scope['state'];state['permission_decision']=replace(state['permission_decision'],allowed=False)
            await send(message)
        await real(scope,receive,original_send)
    with monkeypatch.context()as patch:
        patch.setattr(route,'app',drift);c=client(app,session)
        try:r=c.get('/api/project/current');assert r.status_code==200,r.text
        finally:c.close()
    c=client(app,session)
    try:post(c,project)
    finally:c.close()
    assert not any(row['complete']for row in rows(output))

def test_original_GET_clock_and_wrong_version_data_are_not_promoted(tmp_path,monkeypatch):
    app,selector,session,project,output,_=ready(tmp_path,monkeypatch)
    route=next(route for route in app.routes if getattr(route,'path',None)=='/api/project/current');real=route.app
    async def late(scope,receive,send):
        attempt=selector.http_capture.readbacks.for_scope(scope);assert attempt is not None
        attempt.started_monotonic-=11
        await real(scope,receive,send)
    with monkeypatch.context()as patch:
        patch.setattr(route,'app',late);c=client(app,session)
        try:r=c.get('/api/project/current');assert r.status_code==200,r.text
        finally:c.close()
    c=client(app,session)
    try:
        missing=c.get('/api/flowchart/pipelines/'+'9'*32);assert missing.status_code==404
        post(c,project)
    finally:c.close()
    assert not any(row['complete']for row in rows(output))

def test_GET_witness_is_not_mintable_or_copyable_and_replay_does_not_replace_original(tmp_path,monkeypatch):
    app,selector,session,project,output,_=ready(tmp_path,monkeypatch);shared=selector.shared_owner
    assert observer.begin_original_get_readback(shared,shared.middleware,{'type':'http','method':'GET','path':'/api/project/current'})is None
    route=next(route for route in app.routes if getattr(route,'path',None)=='/api/project/current');real=route.app;seen=[]
    async def checked(scope,receive,send):
        attempt=selector.http_capture.readbacks.for_scope(scope);assert attempt is not None
        witness=attempt.witness;assert witness is not None
        with pytest.raises(ValueError):copy.copy(witness)
        seen.append(witness)
        await real(scope,receive,send)
    with monkeypatch.context()as patch:
        patch.setattr(route,'app',checked);c=client(app,session)
        try:r=c.get('/api/project/current');assert r.status_code==200,r.text
        finally:c.close()
    c=client(app,session)
    try:post(c,project)
    finally:c.close()
    before={p.name:p.read_bytes()for p in output.glob('readback-???.json')}
    assert len(before)==1 and seen[0].active is False
    observer.end_original_get_readback(seen[0],True)  # Not from original hook: no effect/authority.
    selector.http_capture.readbacks.promote()
    assert before=={p.name:p.read_bytes()for p in output.glob('readback-???.json')}
