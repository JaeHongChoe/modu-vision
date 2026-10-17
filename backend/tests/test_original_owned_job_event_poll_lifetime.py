"""Root-only real-ASGI initial -> cursor poll, matching createJobEventStream.

The optional ledger rows are original data-only JobStore API fixtures. No job
runner, training/inference, fake cursor/result/DB, network, or background task is
created. HTTP/body/immutable-source assertions precede the causal drain check.
"""
import asyncio
from pathlib import Path
from types import MethodType

import httpx
import pytest


def _setup(tmp_path,monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _stack
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'runtime-user-data'))
    result=_stack(tmp_path,monkeypatch)
    app,admission,observer,actor,project,graph,seed,recipe,audit=result
    from backend.api import routes_flowchart as flows
    root=Path(project['project_dir'])
    protected={p:p.read_bytes() for p in (root/'project.json',recipe,flows._active_flow_file(root),flows._version_dir(root)/(seed+'.json'))}
    return result,protected


@pytest.mark.parametrize('recorded_events',(False,True))
def test_original_job_event_initial_then_exact_cursor_polls_commit_close_and_drain(recorded_events,tmp_path,monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current,_no_whole_credit
    from backend.engine import job_store
    (app,admission,observer,actor,project,graph,seed,recipe,audit),protected=_setup(tmp_path,monkeypatch)
    store=job_store.ledger()
    context=app.state.context_registry.context(project,actor)
    key=app.state.context_registry.project_key(context)
    job=store.submit(context,key,'fixture-data-only',{'purpose':'UI event cursor data only'}) if recorded_events else None

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid') as client:
            headers=await _login_current(client,app,project)
            first=await client.get('/api/job-events',headers=headers,params={'limit':200})
            assert first.status_code==200
            initial=first.json()
            assert initial['reset'] is True and initial['reason']=='initial' and initial['events']==[] and initial['more'] is False
            if recorded_events:store.record_event(job.id,'original_UI_fixture_event',{'original':True})
            poll=await client.get('/api/job-events',headers=headers,params={'after':initial['cursor'],'limit':200})
            assert poll.status_code==200
            page=poll.json()
            assert page['reset'] is False and page['reason'] is None and page['more'] is False
            if recorded_events:
                assert len(page['events'])==1
                event=page['events'][0]
                assert event['job_id']==job.id and event['seq']==2 and event['event']=='original_UI_fixture_event'
                assert event['kind']=='fixture-data-only' and event['payload']=={'original':True}
                assert page['cursor']!=initial['cursor']
            else:assert page['events']==[] and page['cursor']==initial['cursor']
            repeated=await client.get('/api/job-events',headers=headers,params={'after':page['cursor'],'limit':200})
            assert repeated.status_code==200
            assert repeated.json()=={**page,'events':[]}
    asyncio.run(original_sequence())
    assert all(p.read_bytes()==raw for p,raw in protected.items())
    assert audit.verify()=={'intact':True,'rows':0}
    rows=observer.snapshot()['records']
    assert len(rows)==5 and all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in rows)
    admission.close()
    assert admission.drain(.03)=={'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    assert observer.snapshot()['all_settled'] and not observer.snapshot()['uncertain']
    _no_whole_credit(observer)


def test_original_cursor_poll_late_event_at_instance_delegate_refuses_after_genuine_page(tmp_path,monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current,_no_whole_credit
    from backend.engine import job_store
    (app,admission,observer,actor,project,graph,seed,recipe,audit),protected=_setup(tmp_path,monkeypatch)
    store=job_store.ledger();context=app.state.context_registry.context(project,actor)
    key=app.state.context_registry.project_key(context)
    job=store.submit(context,key,'fixture-data-only',{'purpose':'original event anchor data only'})
    changed=[]
    async def original_app(scope,receive,send):
        async def original_send(message):
            if scope.get('path')=='/api/job-events' and b'after=' in scope.get('query_string',b'') and message['type']=='http.response.start':
                original=store.event_at
                def delegate(self,position):return original(position)
                monkeypatch.setattr(store,'event_at',MethodType(delegate,store));changed.append(True)
            await send(message)
        await app(scope,receive,original_send)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_app),base_url='http://owned.invalid') as client:
            headers=await _login_current(client,app,project)
            initial=await client.get('/api/job-events',headers=headers,params={'limit':200})
            assert initial.status_code==200 and initial.json()['reason']=='initial'
            store.record_event(job.id,'original_anchor_fixture',{'original':True})
            response=await client.get('/api/job-events',headers=headers,params={'after':initial.json()['cursor'],'limit':200})
            assert response.status_code==200
            page=response.json()
            assert page['reset'] is False and page['reason'] is None and page['more'] is False
            assert len(page['events'])==1 and page['events'][0]['job_id']==job.id and page['events'][0]['event']=='original_anchor_fixture'
    asyncio.run(original_sequence())
    assert changed==[True] and all(p.read_bytes()==raw for p,raw in protected.items())
    admission.close();drain=admission.drain(.03)
    assert drain['status']=='refused' and drain['active_scopes']>=1 and 'uncovered_request_protocol' in drain['unsupported']
    _no_whole_credit(observer)
