"""Durable redacted project observations and explicit local notifications."""
import importlib.util
import json
import sqlite3
from pathlib import Path
import pytest
from backend.tests.test_product_delivery import project
from backend.engine.inspection_service import InspectionStore
from PIL import Image

def store_for(p):
    assert importlib.util.find_spec('backend.engine.observability'),'Durable operations observations are missing'
    from backend.engine.observability import ObservabilityStore
    return ObservabilityStore(p)

def inspection(p):
    store=InspectionStore(Path(p['project_dir'])/'runtime_service'/'state')
    image=Path(p['source_dataset_dir'])/'sample.png';Image.new('RGB',(8,8)).save(image)
    return store,image

READY={'runtime':{'status':'ready','manifest_sha256':'a'*64},'active':{'release':{'manifest_sha256':'a'*64}}}
METRICS={'disk':{'free_bytes':10000000000,'total_bytes':20000000000,'available':True},'gpu':{'available':False,'devices':[],'reason':'no_local_nvidia'}}

def test_inspection_logs_persist_paged_filtered_trace_and_exact_totals(tmp_path):
    p=project(tmp_path);input_store,image=inspection(p)
    first=input_store.enqueue(image,'file');input_store.claim();input_store.finish(first,result={'final_verdict':'NG'})
    second=input_store.enqueue(image,'file');input_store.claim();input_store.finish(second,error='Controlled decoder failure')
    ops=store_for(p);ops.collect(READY,metrics=METRICS)
    page=ops.logs(limit=2);assert page['total']>2 and len(page['items'])==2 and page['has_more']
    exact=ops.logs(job_id=first,limit=50);assert exact['total']==3 and all(r['job_id']==r['trace_id']==first for r in exact['items'])
    failure=ops.logs(job_id=second,state='error');assert failure['total']==1 and failure['items'][0]['message']=='Controlled decoder failure'
    reopened=store_for(p);reopened.collect(READY,metrics=METRICS)
    assert reopened.logs()['total']==ops.logs()['total']
    assert reopened.logs(job_id=first)['items']==exact['items']
    assert ops.policy()['enabled'] is False and ops.notifications()['total']==0
    assert ops.snapshot()['external_telemetry'] is False

def test_secrets_contacts_paths_and_endpoints_are_redacted_before_persistence(tmp_path):
    p=project(tmp_path);input_store,image=inspection(p)
    identifier=input_store.enqueue(image,'file');input_store.claim()
    secret='private-observation-fixture'
    input_store.finish(identifier,error=f'Bearer {secret}; token={secret} https://private.invalid/path /private/person/file.png user@example.invalid')
    ops=store_for(p);ops.collect(READY,metrics=METRICS)
    data=json.dumps(ops.logs(),ensure_ascii=False)
    assert secret not in data and 'private.invalid' not in data and 'user@example.invalid' not in data
    with sqlite3.connect(ops.path) as db:stored=json.dumps(db.execute('SELECT message,payload_json FROM logs').fetchall())
    assert secret not in stored and '/private/person' not in stored and '[REDACTED]' in stored

def test_local_notification_opt_in_records_new_failures_once_and_policy_conflicts(tmp_path):
    p=project(tmp_path);input_store,image=inspection(p);ops=store_for(p)
    old=input_store.enqueue(image,'file');input_store.claim();input_store.finish(old,error='Before opt-in')
    ops.collect(READY,metrics=METRICS);policy=ops.policy()
    saved=ops.configure(True,expected_revision=policy['revision'],actor='reviewer-a',reason='Local errors only')
    assert saved['enabled'] and saved['revision']==policy['revision']+1
    with pytest.raises(ValueError,match='revision'):ops.configure(False,expected_revision=policy['revision'],actor='reviewer-b',reason='Stale')
    assert ops.notifications()['total']==0
    fresh=input_store.enqueue(image,'file');input_store.claim();input_store.finish(fresh,error='New failure')
    for _ in range(3):ops.collect(READY,metrics=METRICS)
    alerts=ops.notifications();assert alerts['total']==1 and alerts['items'][0]['job_id']==fresh
    ops.collect({'runtime':{'status':'unavailable'},'active':READY['active']},metrics=METRICS)
    ops.collect({'runtime':{'status':'unavailable'},'active':READY['active']},metrics=METRICS)
    assert ops.notifications()['total']==2
    assert ops.policy_history()[0]['actor']=='reviewer-a'

def test_linked_source_and_observation_databases_are_refused(tmp_path):
    p=project(tmp_path);external=tmp_path/'external.sqlite3';external.write_text('private')
    root=Path(p['project_dir'])/'delivery';root.mkdir();(root/'observability.sqlite3').symlink_to(external)
    with pytest.raises(ValueError,match='link'):store_for(p)
    (root/'observability.sqlite3').unlink();ops=store_for(p)
    state=Path(p['project_dir'])/'runtime_service'/'state';state.mkdir(parents=True)
    (state/'inspection_service.sqlite3').symlink_to(external)
    with pytest.raises(ValueError,match='link'):ops.collect(READY,metrics=METRICS)
    assert external.read_text()=='private'

def test_training_events_are_scoped_and_stream_replacement_cannot_skip_records(tmp_path):
    from backend.engine.job_store import JobStore
    from backend.contracts.context import ProjectContext
    p=project(tmp_path);ctx=ProjectContext(workspace_id='w',project_id=p['id'],actor_id='a',mode='local')
    jobs=JobStore(tmp_path/'jobs.sqlite3');first=jobs.submit(ctx,'owned-project','training',{'model':'synthetic'})
    jobs.submit(ctx,'foreign-project','training',{'model':'foreign'})
    ops=store_for(p);ops.collect(READY,metrics=METRICS,training=(jobs,ctx,'owned-project'))
    records=ops.logs(job_id=first.id)['items'];assert len(records)==1 and records[0]['kind']=='training'
    assert 'foreign' not in json.dumps(ops.logs())
    replacement=JobStore(tmp_path/'replacement.sqlite3');new=replacement.submit(ctx,'owned-project','training',{'model':'new'})
    ops.collect(READY,metrics=METRICS,training=(replacement,ctx,'owned-project'))
    assert ops.logs(job_id=new.id)['total']==1
    assert ops.logs(job_id=first.id)['items']==records

def test_invalid_filters_and_policy_values_are_refused(tmp_path):
    ops=store_for(project(tmp_path))
    for args in ({'limit':0},{'limit':501},{'offset':-1},{'state':'foreign'},{'job_id':'../other'}):
        with pytest.raises(ValueError):ops.logs(**args)
    for enabled in ('yes',1,None):
        with pytest.raises(ValueError):ops.configure(enabled,expected_revision=1,actor='a',reason='Explicit local policy')

def test_training_callback_retains_redacted_epoch_logs_without_websocket_and_refuses_foreign_identity(tmp_path,monkeypatch):
    from backend.engine.job_store import JobStore
    from backend.contracts.context import ProjectContext,current_project_context
    from backend.api import websocket_telemetry as telemetry
    p=project(tmp_path);ctx=ProjectContext(workspace_id='w',project_id=p['id'],actor_id='a',mode='local')
    jobs=JobStore(tmp_path/'jobs.sqlite3');job=jobs.submit(ctx,'owned','training',{})
    monkeypatch.setattr(telemetry,'observation_ledger',lambda:jobs,raising=False)
    monkeypatch.setattr(telemetry.broadcaster,'broadcast_sync',lambda *args:None)
    token=current_project_context.set(ctx)
    try:callback=telemetry.WebSocketTelemetryCallback(job.id)
    finally:current_project_context.reset(token)
    callback.on_training_start({'epochs':2,'token':'private-callback-fixture'})
    callback.on_epoch_end(0,2,.5,.6,.01,{'accuracy':.5})
    callback.on_step_end(0,1,.5,0);callback.on_hardware_stats({'gpu_utilization':1})
    callback.on_epoch_end(1,2,.3,.4,.01,{'accuracy':.6})
    callback.on_training_completed(job.id,1,.4,'/private/person/checkpoint.pt')
    events=jobs.events(job.id);saved=[r for r in events if r['event']!='submit']
    assert [r['event'] for r in saved]==['training_started','epoch_progress','epoch_progress','training_completed']
    assert 'private-callback-fixture' not in json.dumps(saved) and '/private/person' not in json.dumps(saved)
    assert json.loads(saved[1]['payload_json'])['train_loss']==.5
    foreign=ctx.model_copy(update={'actor_id':'other'})
    token=current_project_context.set(foreign)
    try:imposter=telemetry.WebSocketTelemetryCallback(job.id)
    finally:current_project_context.reset(token)
    imposter.on_epoch_end(0,1,999,None,.01,{})
    assert jobs.events(job.id)==events
    ops=store_for(p);ops.collect(READY,metrics=METRICS,training=(jobs,ctx,'owned'))
    assert ops.logs(job_id=job.id)['total']==5
    assert store_for(p).logs(job_id=job.id)['items']==ops.logs(job_id=job.id)['items']

def test_collection_failure_rolls_back_source_cursor_and_partial_logs(tmp_path,monkeypatch):
    p=project(tmp_path);inputs,image=inspection(p);job=inputs.enqueue(image,'file');inputs.claim();inputs.finish(job,error='Controlled')
    ops=store_for(p);original=ops._training
    monkeypatch.setattr(ops,'_training',lambda *_:(_ for _ in ()).throw(ValueError('Controlled collection fault')))
    with pytest.raises(ValueError,match='fault'):ops.collect(READY,metrics=METRICS)
    assert ops.logs()['total']==0
    monkeypatch.setattr(ops,'_training',original);ops.collect(READY,metrics=METRICS)
    assert ops.logs(job_id=job)['total']==3

def test_api_notification_authority_actor_cas_and_no_external_sink(tmp_path):
    from fastapi import FastAPI
    from types import SimpleNamespace
    from backend.api.routes_product_delivery import router
    from backend.tests.test_product_delivery import ApiClient
    p=project(tmp_path);app=FastAPI();app.include_router(router);current={'role':'viewer'}
    app.state.accounts=SimpleNamespace(project_role=lambda *_:current['role'])
    @app.middleware('http')
    async def scope(request,call_next):
        request.state.scoped_project=p;request.state.account_user={'id':'authenticated-reviewer'}
        return await call_next(request)
    client=ApiClient(app);route='/api/product-delivery/operations/notification-policy'
    body={'enabled':True,'expected_revision':1,'actor':'forged-other','reason':'Controlled local failure history'}
    assert client.request('PUT',route,json=body).status_code==403
    current['role']='reviewer';reply=client.request('PUT',route,json=body);assert reply.status_code==200,reply.text
    assert reply.json()['external_telemetry'] is False
    assert store_for(p).policy_history()[0]['actor']=='authenticated-reviewer'
    assert client.request('PUT',route,json=body).status_code==409
    assert client.request('PUT',route,json={**body,'expected_revision':2,'external_url':'https://example.invalid'}).status_code==422
    current['role']='viewer';reply=client.get('/api/product-delivery/operations/observability')
    assert reply.status_code==200,reply.text
    assert reply.json()['can_configure'] is False
    assert client.get('/api/product-delivery/operations/observability?job_id=../foreign').status_code==409
