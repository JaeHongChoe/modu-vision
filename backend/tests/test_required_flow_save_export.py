"""Root-only actual auth/ASGI coupling; only save/export/run bodies are nonmodel.

No model/checkpoint/remote execution. Original final shared auth, SQLite,
observer, request framing/send/normalleave and real run carrier are retained.
The controlled save body performs only its declared own active-version write.
"""
import base64,hashlib,json
from pathlib import Path
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse
from backend.api import routes_flowchart
from backend.tests import test_required_flow_http_framework as original

SEED='3'*32;SAVED='4'*32

def setup(tmp_path,monkeypatch):
    app,selector,session,project,output=original.owned_http(tmp_path,monkeypatch)
    root=Path(project['project_dir']);project=json.loads((root/'project.json').read_bytes());project['task']='segmentation'
    routes_flowchart._write_json(root/'project.json',project)
    active={'version_id':SEED,'project_id':project['id'],'labelset_id':project['active_labelset_id'],
        'recipe_task':project['task'],'source_dataset_path':project['source_dataset_dir']}
    for version in(SEED,SAVED):
        routes_flowchart._write_json(root/'flowcharts/versions'/f'{version}.json',{
            'version_id':version,'saved_at':'2026-10-15T00:00:00Z','saved_at_ns':1,'recipe_task':project['task'],
            'source_dataset_path':project['source_dataset_dir'],'pipeline':original.original.GRAPH})
    routes_flowchart._write_json(root/'flowcharts/active.json',active)
    seen={};original.original_route_control(app,selector,monkeypatch,seen)
    async def save(scope,receive,send):
        request=Request(scope,receive);seen['save_body']=await request.json();seen['save_calls']=seen.get('save_calls',0)+1
        routes_flowchart._write_json(root/'flowcharts/active.json',{**active,'version_id':SAVED})
        await JSONResponse({'status':'saved','version_id':SAVED,'pipeline_id':original.original.GRAPH['id'],
            'node_count':len(original.original.GRAPH['nodes']),'recipe_task':'segmentation'})(scope,receive,send)
    async def export(scope,receive,send):
        request=Request(scope,receive);seen['export_body']=await request.json();seen['export_calls']=seen.get('export_calls',0)+1
        await JSONResponse({'package_path':str(root/'exports/flows/nonmodel-control'),
            'parity':{'status':'not_run'},'model_job_ids':sorted(row[0]for row in original.capture.RETAINED_MODELS),
            'controlled_nonmodel_body':True})(scope,receive,send)
    for path,body in(('/api/flowchart/pipeline',save),('/api/export/flow',export)):
        route=next(route for route in app.routes if getattr(route,'path',None)==path and 'POST' in route.methods)
        monkeypatch.setattr(route,'app',body)
    return app,selector,session,project,output,seen

def client(app,session):return TestClient(app,headers={'Authorization':'Bearer '+session['token']})
def save(c,project):return c.post('/api/flowchart/pipeline',params={'recipe_task':'segmentation','source_dataset_path':project['source_dataset_dir'],
    'change_reason':'Required retained no-model transport control','expected_version_id':SEED},json=original.original.GRAPH)
def run(c,project):
    response=c.post('/api/flowchart/run',json=original.body(project));assert response.status_code==200,response.text
    return response
def export_body(project):return {'source_dataset_path':project['source_dataset_dir'],'recipe_task':'segmentation','version_id':SAVED}
def receipts(output):return [json.loads(p.read_bytes())for p in sorted(output.glob('exchange-???.json'))]
def counts(output):return json.loads(sorted(output.glob('post-counts-???.json'))[-1].read_bytes())

def test_real_shared_save_run_export_preserve_exact_own_bodies_and_normalleave(tmp_path,monkeypatch):
    app,selector,session,project,output,seen=setup(tmp_path,monkeypatch);c=client(app,session)
    try:
        saved=save(c,project);assert saved.status_code==200,saved.text
        assert receipts(output)==[]  # Initial save bytes still memory only.
        run(c,project)
        exported=c.post('/api/export/flow',json=export_body(project));assert exported.status_code==200,exported.text
    finally:c.close()
    rows=receipts(output);assert len(rows)==2
    expected={'/api/flowchart/pipeline':(original.original.GRAPH,saved.content,10),'/api/export/flow':(export_body(project),exported.content,115)}
    for row in rows:
        body,response,budget=expected[row['path']]
        assert row['complete']is True and row['actual_original_shared_authorization']is True
        assert json.loads(base64.b64decode(row['request_base64'],validate=True))==body
        raw=base64.b64decode(row['response_base64'],validate=True);assert raw==response
        assert row['response_sha256']==hashlib.sha256(raw).hexdigest() and row['elapsed_monotonic_seconds']<budget
        assert row['actor_id']==session['user']['id']and row['project_id']==project['id']
        assert row['original_released_sequence']>row['original_entered_sequence']
        assert row['server_ASGI_send_receipt']is True and row['client_receive_proven']is False
        assert row['headers_query_or_tokens_serialized']is False and row['parent_credit']is False
    assert seen['calls']==seen['save_calls']==seen['export_calls']==1
    summary=counts(output);assert summary['counts']['RUN']==summary['counts']['SAVE']==summary['counts']['EXPORT']==1
    assert summary['counts']['FORBIDDEN_TRAIN']==0
    for path in list(output.glob('exchange-???.json'))+list(output.glob('post-counts-???.json')):
        assert path.stat().st_mode&0o777==0o600
        assert session['token'].encode()not in path.read_bytes()and app.state.api_token.encode()not in path.read_bytes()
    assert app.state.owned_work_admission._active==0
    assert 'uncovered_request_protocol'in app.state.owned_work_admission._unsupported

def test_unauthenticated_export_never_gets_original_auth_data(tmp_path,monkeypatch):
    app,selector,session,project,output,_=setup(tmp_path,monkeypatch);c=client(app,session)
    try:save(c,project);run(c,project)
    finally:c.close()
    c=TestClient(app)
    try:response=c.post('/api/export/flow',json=export_body(project))
    finally:c.close()
    assert response.status_code==401
    assert all(row['path']!='/api/export/flow'for row in receipts(output))

def test_export_parity_field_refuses_data_not_the_original_nonmodel_response(tmp_path,monkeypatch):
    app,selector,session,project,output,_=setup(tmp_path,monkeypatch);c=client(app,session)
    try:
        save(c,project);run(c,project)
        response=c.post('/api/export/flow',json={**export_body(project),'compute_profile_id':'server42-l40s-2'})
    finally:c.close()
    assert response.status_code==200
    assert all(row['path']!='/api/export/flow'for row in receipts(output))

def test_save_or_export_replay_count_cannot_overwrite_first_original_record(tmp_path,monkeypatch):
    app,selector,session,project,output,_=setup(tmp_path,monkeypatch);c=client(app,session)
    try:
        save(c,project);run(c,project);first=c.post('/api/export/flow',json=export_body(project));assert first.status_code==200
        before={p.name:p.read_bytes()for p in output.glob('exchange-???.json')}
        again=c.post('/api/export/flow',json=export_body(project));assert again.status_code==200
    finally:c.close()
    assert before=={p.name:p.read_bytes()for p in output.glob('exchange-???.json')}
    assert counts(output)['counts']['EXPORT']==2

def test_wrong_owned_save_source_query_does_not_promote_original_nonmodel_body(tmp_path,monkeypatch):
    app,selector,session,project,output,_=setup(tmp_path,monkeypatch);c=client(app,session)
    try:
        response=c.post('/api/flowchart/pipeline',params={'recipe_task':'segmentation','source_dataset_path':project['project_dir'],
            'change_reason':'Bounded source-drift control','expected_version_id':SEED},json=original.original.GRAPH)
        assert response.status_code==200,response.text
        run(c,project)
    finally:c.close()
    assert all(row['path']!='/api/flowchart/pipeline'for row in receipts(output))
