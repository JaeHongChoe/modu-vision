"""Durable queue pagination and project/role-scoped operator actions."""
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from backend.tests.test_product_delivery import ApiClient, project
from backend.tests.test_service_s5_02 import client_for, image
from backend.tests.test_inspection_service import package  # noqa: F401
from backend.engine.inspection_service import InspectionStore


def test_queue_pages_reopen_without_losing_history_or_filter_totals(tmp_path):
    store = InspectionStore(tmp_path/'state', max_outstanding=1000)
    source = image(tmp_path)
    ids = [store.enqueue(source, 'file') for _ in range(65)]
    row = store.claim(); store.finish(row['job_id'], error='controlled failure')
    first = store.page(limit=30, offset=0)
    second = InspectionStore(tmp_path/'state', max_outstanding=1000).page(limit=30, offset=30)
    tail = store.page(limit=30, offset=60)
    assert first['total'] == second['total'] == tail['total'] == 65
    assert first['outstanding'] == 64 and first['capacity'] == 1000
    assert [r['job_id'] for p in (first, second, tail) for r in p['jobs']] == ids[::-1]
    failed = store.page(limit=30, offset=0, state='error')
    assert failed['total'] == 1 and failed['jobs'][0]['job_id'] == row['job_id']
    assert failed['jobs'][0]['image_sha256'] == store.get(row['job_id'])['image_sha256']


def test_runtime_queue_is_authenticated_and_validates_paging(package, tmp_path):
    _, client = client_for(package, tmp_path)
    with client:
        assert client.get('/v1/queue', headers={'X-Vision-Token':'wrong'}).status_code == 401
        for query in ('limit=0', 'limit=501', 'offset=-1', 'state=anything'):
            assert client.get('/v1/queue?'+query).status_code == 422
        assert client.get('/v1/queue?state=error').json()['total'] == 0
        for _ in range(3): client.post('/v1/jobs/file',json={'image_path':str(image(tmp_path))})
        exported = client.get('/v1/results/export?format=json&limit=2').json()
        assert (exported['count'], exported['total'], exported['truncated']) == (2, 3, True)
        csv = client.get('/v1/results/export?format=csv&limit=2')
        assert (csv.headers['X-Result-Count'], csv.headers['X-Total-Results']) == ('2','3')


def scoped_client(monkeypatch, tmp_path, *, account_role='owner', response_status=202):
    from backend.api import routes_product_delivery as routes
    p = project(tmp_path); calls = []
    class Service:
        def __init__(self, directory): assert directory == p['project_dir']
        def state(self):return {'active':{'release':{'manifest_sha256':'a'*64}},'runtime':{'status':'ready','manifest_sha256':'a'*64}}
        @contextmanager
        def client(self):
            def respond(request):
                calls.append((request.method,str(request.url),request.content))
                return httpx.Response(response_status,json={'job_id':'owned','state':'queued','detail':'Inspection inbox is full'},headers={'Retry-After':'1'})
            with httpx.Client(base_url='http://owned',transport=httpx.MockTransport(respond)) as client:yield client
    monkeypatch.setattr(routes,'ManagedService',Service)
    app=FastAPI();app.state.accounts=SimpleNamespace(project_role=lambda *_:account_role)
    @app.middleware('http')
    async def scope(request,call_next):
        request.state.scoped_project=p;request.state.account_user={'id':'a','username':'Authenticated reviewer'}
        return await call_next(request)
    app.include_router(routes.router)
    return ApiClient(app),calls


def test_queue_mutations_refuse_viewer_and_invalid_job_path_before_service_access(monkeypatch,tmp_path):
    client,calls=scoped_client(monkeypatch,tmp_path,account_role='viewer')
    for action in ('retry','replay'):
        response=client.post('/api/product-delivery/operator/queue/job/'+action,json={'reason':'Explicit retest','operator':'impersonated'})
        assert response.status_code==403
    assert calls==[]
    client,calls=scoped_client(monkeypatch,tmp_path,account_role='owner')
    assert client.post('/api/product-delivery/operator/queue/bad.name/retry').status_code==422
    assert calls==[]


def test_operator_replay_uses_authenticated_actor_and_preserves_backpressure(monkeypatch,tmp_path):
    import json
    client,calls=scoped_client(monkeypatch,tmp_path,response_status=429)
    response=client.post('/api/product-delivery/operator/queue/owned/replay',json={'reason':'Explicit retest','operator':'impersonated'})
    assert response.status_code==429 and response.headers['Retry-After']=='1'
    assert json.loads(calls[0][2])['operator']=='Authenticated reviewer'
    assert calls[0][1]=='http://owned/v1/jobs/owned/replay'


def test_operator_mutation_refuses_unready_or_different_active_release(monkeypatch,tmp_path):
    from backend.api import routes_product_delivery as routes
    client,calls=scoped_client(monkeypatch,tmp_path)
    monkeypatch.setattr(routes.ManagedService,'state',lambda _: {'active':{'release':{'manifest_sha256':'b'*64}},'runtime':{'status':'ready','manifest_sha256':'a'*64}})
    assert client.post('/api/product-delivery/operator/queue/owned/retry').status_code==409
    assert calls==[]
