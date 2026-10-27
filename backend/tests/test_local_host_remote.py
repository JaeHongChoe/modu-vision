"""Real local AccountStore/controller; only upstream HTTP is a fake network boundary."""
import asyncio
import json
import time
import httpx
import pytest
from backend.tests.test_local_host_routes import setup


def fixture(tmp_path):
    from backend.local_host.remote import install_remote
    app,client,controller,helper,session,headers=setup(tmp_path)
    app.include_router(install_remote(controller))
    calls=[]
    async def upstream(request):
        calls.append(request)
        if request.url.path=='/api/accounts/login':
            return httpx.Response(200,json={'token':'REMOTE_SECRET','expires_at':time.time()+3600,
                'user':{'id':'remote-user','username':'remote','administrator':False}},headers={'Set-Cookie':'remote=secret'})
        context=request.headers.get('x-vision-context') or json.dumps({'project_id':request.headers.get('x-vision-project','remote'),'workspace_id':'w','actor_id':'remote-user','mode':'team'})
        return httpx.Response(200,json={'ok':True},headers={'Set-Cookie':'remote=secret','X-Vision-Context':context})
    controller.remote.transport=httpx.MockTransport(upstream)
    def tab():
        value=client.post('/api/local-host/session',headers=headers).json()['tab_id']
        h={**headers,'X-Vision-Local-Tab':value}
        client.post('/api/local-host/approve',headers=h,json={})
        return h
    return app,client,controller,session,tab,calls


def login(client,h,origin='https://remote.test'):
    return client.post('/api/local-host/remote/login',headers=h,json={'server_url':origin,'username':'remote','password':'test-password-123'})


def test_login_keeps_local_principal_and_never_returns_remote_token_or_cookie(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();r=login(c,h)
    assert r.status_code==200,r.text
    assert 'REMOTE_SECRET' not in r.text and 'token' not in r.json()
    assert 'set-cookie' not in r.headers
    assert r.json()['user']['id']=='remote-user'
    assert app.state.accounts.cookie_session(session['token'])['id'] != 'remote-user'
    assert not calls[0].headers.get('cookie') and not calls[0].headers.get('x-vision-csrf')


def test_proxy_owns_tab_filters_headers_and_refuses_token_paths(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();other=tab();connection=login(c,h).json()['remote_id'];base=f'/api/local-host/remote/{connection}'
    r=c.post(base+'/api/dataset/split',headers={**h,'Authorization':'Bearer malicious','X-Vision-Context':'{"actor_id":"local"}','X-Forwarded-For':'evil'},json={'ratio':.5})
    assert r.status_code==200,r.text
    sent=calls[-1]
    assert sent.headers['authorization']=='Bearer REMOTE_SECRET'
    assert not sent.headers.get('cookie') and not sent.headers.get('x-vision-csrf') and not sent.headers.get('x-forwarded-for')
    assert not sent.headers.get('x-vision-context')
    assert 'set-cookie' not in r.headers and 'x-vision-context' not in r.headers
    assert json.loads(r.headers['x-vision-remote-context'])['project_id']=='remote'
    assert c.get(base+'/api/dataset/info',headers=other).status_code==403
    for path in ['accounts/login','accounts/logout','accounts/oidc/callback','local-host/open']:
        assert c.post(base+'/api/'+path,headers=h,json={}).status_code==403


def test_arbitrary_origins_redirects_and_path_escapes_are_refused(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab()
    for url in ['http://remote.test','https://user:pass@remote.test','https://remote.test/api','https://remote.test?x=1']:
        assert login(c,h,url).status_code==422
    connection=login(c,h).json()['remote_id'];base=f'/api/local-host/remote/{connection}'
    for path in ['api/%252e%252e/secret','api/%255csecret','api/https:%252f%252fevil.test']:
        assert c.get(base+'/'+path,headers=h).status_code==422
    ctl.remote.transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'Location':'https://evil.test'}))
    assert c.get(base+'/api/project/current',headers=h).status_code==502


def test_context_rotation_retires_prior_id_and_other_tab_survives(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();other=tab()
    first=login(c,h).json()['remote_id'];second=login(c,other).json()['remote_id']
    r=c.post(f'/api/local-host/remote/{first}/context',headers=h,json={'project_id':'p2','context':{'project_id':'p2','actor_id':'remote-user','workspace_id':'w','mode':'team'}})
    assert r.status_code==200,r.text
    new=r.json()['remote_id'];assert new!=first
    assert c.get(f'/api/local-host/remote/{first}/api/project/current',headers=h).status_code==409
    assert c.get(f'/api/local-host/remote/{new}/api/project/current',headers=h).status_code==200
    assert json.loads(calls[-1].headers['x-vision-context'])['actor_id']=='remote-user'
    assert c.get(f'/api/local-host/remote/{second}/api/project/current',headers=other).status_code==200
    app.state.accounts.logout(session['token'])
    assert c.get(f'/api/local-host/remote/{new}/api/project/current',headers=h).status_code==401


def test_late_login_and_response_cannot_survive_revocation(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab()
    original=ctl.remote.transport
    async def late(request):
        response=await original.handle_async_request(request)
        app.state.accounts.logout(session['token'])
        return response
    ctl.remote.transport=httpx.MockTransport(late)
    assert login(c,h).status_code==401
    assert not ctl.remote.connections


def test_limits_and_csrf_apply_before_remote_io(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab()
    assert login(c,{'X-Vision-Local-Tab':h['X-Vision-Local-Tab']}).status_code==403
    connection=login(c,h).json()['remote_id'];before=len(calls)
    r=c.post(f'/api/local-host/remote/{connection}/api/upload',headers=h,content=b'x'*(8*1024*1024+1))
    assert r.status_code==413 and len(calls)==before
    ctl.remote.transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*(32*1024*1024+1)))
    assert c.get(f'/api/local-host/remote/{connection}/api/download',headers=h).status_code==502


def test_websocket_uses_owned_connection_and_closes_on_tab_revocation(tmp_path):
    from contextlib import asynccontextmanager
    from starlette.websockets import WebSocketDisconnect
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();other=tab();rid=login(c,h).json()['remote_id']
    opened=[]
    class Socket:
        def __init__(self):self.queue=asyncio.Queue();self.closed=False
        async def send(self,value):await self.queue.put(value)
        async def recv(self):return await self.queue.get()
        async def close(self,code=1000):self.closed=True
    @asynccontextmanager
    async def connect(url,headers):
        sock=Socket();opened.append((url,headers,sock));yield sock
    ctl.remote.connect_socket=connect
    url=f'wss://localhost:8443/api/local-host/remote/{rid}/ws/telemetry?local_tab='+h['X-Vision-Local-Tab']
    with c.websocket_connect(url) as ws:
        ws.send_text('ping');assert ws.receive_text()=='ping'
        assert opened[0][0]=='wss://remote.test/ws/telemetry'
        assert opened[0][1]['Authorization']=='Bearer REMOTE_SECRET'
        assert 'Cookie' not in opened[0][1]
        c.post(f'/api/local-host/remote/{rid}/logout',headers=h)
        with pytest.raises(WebSocketDisconnect):ws.receive_text()
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(url.replace(h['X-Vision-Local-Tab'],other['X-Vision-Local-Tab'])):pass


def test_remote_context_envelope_and_cookie_asset_route_keep_authorities_separate(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();rid=login(c,h).json()['remote_id']
    rid=c.post(f'/api/local-host/remote/{rid}/context',headers=h,json={'project_id':'p','context':None}).json()['remote_id']
    context={'workspace_id':'remote-w','project_id':'p','actor_id':'remote-user','mode':'team'}
    base=f'/api/local-host/remote/{rid}'
    assert c.get(base+'/api/project/current',headers={**h,'X-Vision-Remote-Context':json.dumps(context)}).status_code==200
    assert json.loads(calls[-1].headers['x-vision-context'])==context
    assert c.get(base+'/api/image/one?local_tab='+h['X-Vision-Local-Tab'],headers={'Sec-Fetch-Site':'same-origin'}).status_code==200
    assert 'local_tab' not in str(calls[-1].url)
    assert c.get(base+'/api/project/current',headers={**h,'X-Vision-Remote-Context':json.dumps({**context,'actor_id':'local-user'})}).status_code==422


def test_delayed_response_is_discarded_when_connection_rotates(tmp_path):
    from dataclasses import replace
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();rid=login(c,h).json()['remote_id']
    async def switch(request):
        old=ctl.remote.connections[rid];await ctl.remote.drop(old)
        newer=replace(old,id='new',epoch=old.epoch+1,streams=set(),active=0)
        ctl.remote.connections[newer.id]=newer
        return httpx.Response(200,json={'secret':'stale-result'})
    ctl.remote.transport=httpx.MockTransport(switch)
    response=c.get(f'/api/local-host/remote/{rid}/api/project/current',headers=h)
    assert response.status_code==409 and 'stale-result' not in response.text


def test_context_switch_preserves_old_snapshot_and_propagates_remote_membership_denial(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();rid=login(c,h).json()['remote_id']
    old=ctl.remote.connections[rid]
    next_id=c.post(f'/api/local-host/remote/{rid}/context',headers=h,json={'project_id':'next'}).json()['remote_id']
    assert old.id==rid and old is not ctl.remote.connections[next_id]
    ctl.remote.transport=httpx.MockTransport(lambda r:httpx.Response(403,json={'detail':'Current remote project membership required'}))
    response=c.get(f'/api/local-host/remote/{next_id}/api/project/current',headers=h)
    assert response.status_code==403 and response.json()['detail']=='Current remote project membership required'


def test_global_remote_account_calls_omit_project_context_and_downloads_are_sandboxed(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();rid=login(c,h).json()['remote_id']
    context={'workspace_id':'w','project_id':'p','actor_id':'remote-user','mode':'team'}
    rid=c.post(f'/api/local-host/remote/{rid}/context',headers=h,json={'project_id':'p','context':context}).json()['remote_id']
    response=c.post(f'/api/local-host/remote/{rid}/api/accounts/select-project',headers=h,json={'project_id':'next'})
    assert response.status_code==200
    assert not calls[-1].headers.get('x-vision-project') and not calls[-1].headers.get('x-vision-context')
    assert "sandbox" in response.headers['content-security-policy']


def test_discovered_remote_context_pins_following_cookie_image_reads(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();rid=login(c,h).json()['remote_id']
    rid=c.post(f'/api/local-host/remote/{rid}/context',headers=h,json={'project_id':'p'}).json()['remote_id']
    context={'workspace_id':'remote-w','project_id':'p','actor_id':'remote-user','mode':'team'}
    async def response(request):
        calls.append(request)
        return httpx.Response(200,json={'id':'p'},headers={'X-Vision-Context':json.dumps(context)})
    ctl.remote.transport=httpx.MockTransport(response)
    base=f'/api/local-host/remote/{rid}'
    assert c.get(base+'/api/project/current',headers=h).status_code==200
    assert c.get(base+'/api/image/one?local_tab='+h['X-Vision-Local-Tab']).status_code==200
    assert json.loads(calls[-1].headers['x-vision-context'])==context


def test_request_body_deadline_bounds_a_stalled_upload(tmp_path,monkeypatch):
    from fastapi import HTTPException,Request
    from backend.local_host import remote
    app,c,ctl,session,tab,calls=fixture(tmp_path)
    monkeypatch.setattr(remote,'TIMEOUT',.01)
    async def receive():await asyncio.sleep(1);return {'type':'http.request','body':b'x','more_body':True}
    async def run():
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(ctl.remote.body(Request({'type':'http','method':'POST','headers':[]},receive)),.1)
        assert error.value.status_code==408
    asyncio.run(run())


@pytest.mark.parametrize('route',['health'])
def test_fixed_remote_health_route_requires_owner_and_keeps_transport_guards(tmp_path,route):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();other=tab();rid=login(c,h).json()['remote_id'];url=f'/api/local-host/remote/{rid}/{route}'
    response=c.get(url,headers=h)
    assert response.status_code==200 and calls[-1].url.path=='/health'
    assert not response.headers.get('set-cookie') and not calls[-1].headers.get('cookie')
    assert c.get(url,headers=other).status_code==403
    assert c.get(url+'/arbitrary',headers=h).status_code==404
    assert c.post(url,headers=h).status_code==405
    ctl.remote.transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'https://escape.test'}))
    assert c.get(url,headers=h).status_code==502


@pytest.mark.parametrize('earlier_action',['login','context','logout','revoke_tab','revoke_session'])
def test_slow_old_socket_cleanup_cannot_replace_newer_connection_or_other_tab(tmp_path,earlier_action):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();other=tab();rid=login(c,h).json()['remote_id'];other_id=login(c,other).json()['remote_id'];old=ctl.remote.connections[rid]
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        class SlowSocket:
            async def close(self,code=1008):entered.set();await release.wait()
        old.streams.add(SlowSocket())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://localhost:8443',headers={**h,'Origin':'https://localhost:8443','Cookie':'vision_session='+session['token']}) as client:
            if earlier_action=='login':work=client.post('/api/local-host/remote/login',json={'server_url':'https://first.test','username':'remote','password':'test-password-123'})
            elif earlier_action=='context':work=client.post(f'/api/local-host/remote/{rid}/context',json={'project_id':'old-project'})
            elif earlier_action=='logout':work=client.post(f'/api/local-host/remote/{rid}/logout',json={})
            elif earlier_action=='revoke_tab':work=ctl.remote.revoke_tab(*old.owner[:2])
            else:work=ctl.remote.revoke_session(old.owner[0])
            earlier=asyncio.create_task(work)
            await asyncio.wait_for(entered.wait(),1)
            latest=await client.post('/api/local-host/remote/login',json={'server_url':'https://latest.test','username':'remote','password':'test-password-123'})
            assert latest.status_code==200,latest.text
            latest_id=latest.json()['remote_id'];release.set();result=await earlier
            if earlier_action in {'login','context'}:assert result.status_code==409,result.text
            assert [(row.id,row.origin) for row in ctl.remote.connections.values() if row.owner==old.owner]==[(latest_id,'https://latest.test')]
            assert (await client.get(f'/api/local-host/remote/{latest_id}/api/current')).status_code==200
            other_response=await client.get(f'/api/local-host/remote/{other_id}/api/current',headers=other)
            assert other_response.status_code==(409 if earlier_action=='revoke_session' else 200)
    asyncio.run(run())


def test_stale_residual_row_cannot_authorize_http_or_websocket_after_owner_epoch_changed(tmp_path):
    from starlette.websockets import WebSocketDisconnect
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();rid=login(c,h).json()['remote_id'];row=ctl.remote.connections[rid]
    ctl.remote.epochs[row.owner]+=1
    assert c.get(f'/api/local-host/remote/{rid}/api/current',headers=h).status_code==409
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f'wss://localhost:8443/api/local-host/remote/{rid}/ws/telemetry?local_tab='+h['X-Vision-Local-Tab']):pass


def test_login_with_delayed_body_cannot_advance_past_a_newer_login(tmp_path):
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab()
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        async def body():
            entered.set();await release.wait()
            yield json.dumps({'server_url':'https://earlier.test','username':'remote','password':'test-password-123'}).encode()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://localhost:8443',headers={**h,'Origin':'https://localhost:8443','Cookie':'vision_session='+session['token']}) as client:
            early=asyncio.create_task(client.post('/api/local-host/remote/login',content=body()))
            await asyncio.wait_for(entered.wait(),1)
            newer=await client.post('/api/local-host/remote/login',json={'server_url':'https://newer.test','username':'remote','password':'test-password-123'})
            assert newer.status_code==200
            release.set();old=await early
            assert old.status_code==409
            assert list(ctl.remote.connections)==[newer.json()['remote_id']]
    asyncio.run(run())


def test_websocket_opened_after_connection_switch_is_closed_before_delivery(tmp_path):
    from contextlib import asynccontextmanager
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from starlette.websockets import WebSocketDisconnect
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();rid=login(c,h).json()['remote_id']
    entered,release,closed=Event(),Event(),Event()
    class Socket:
        async def close(self,code=1000):closed.set()
    @asynccontextmanager
    async def connect(url,headers):
        entered.set();await asyncio.to_thread(release.wait)
        try:yield Socket()
        finally:closed.set()
    ctl.remote.connect_socket=connect
    def opening():
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f'wss://localhost:8443/api/local-host/remote/{rid}/ws/telemetry?local_tab='+h['X-Vision-Local-Tab']):pass
    with c,ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(opening)
        assert entered.wait(1)
        newer=login(c,h,'https://newer.test');assert newer.status_code==200
        release.set();pending.result(timeout=2)
    assert closed.is_set() and list(ctl.remote.connections)==[newer.json()['remote_id']]


def test_refused_websockets_preserve_capacity_until_an_admitted_socket_closes(tmp_path):
    from contextlib import ExitStack,asynccontextmanager
    from starlette.websockets import WebSocketDisconnect
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();rid=login(c,h).json()['remote_id'];row=ctl.remote.connections[rid]
    class Socket:
        def __init__(self):self.queue=asyncio.Queue()
        async def send(self,value):await self.queue.put(value)
        async def recv(self):return await self.queue.get()
        async def close(self,code=1008):pass
    @asynccontextmanager
    async def connect(url,headers):yield Socket()
    ctl.remote.connect_socket=connect
    url=f'wss://localhost:8443/api/local-host/remote/{rid}/ws/telemetry?local_tab='+h['X-Vision-Local-Tab']
    with c,ExitStack() as stack:
        contexts=[stack.enter_context(ExitStack()) for _ in range(4)]
        sockets=[context.enter_context(c.websocket_connect(url)) for context in contexts]
        assert row.active==4
        for _ in range(3):
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect(url):pass
            assert row.active==4
            for sock in sockets:sock.send_text('still-open');assert sock.receive_text()=='still-open'
        contexts[0].close()
        assert row.active==3
        replacement=stack.enter_context(c.websocket_connect(url))
        replacement.send_text('replacement');assert replacement.receive_text()=='replacement'
        assert row.active==4
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(url):pass
        assert row.active==4
    assert row.active==0


def test_upstream_websocket_failure_releases_its_own_acquired_slot(tmp_path):
    from contextlib import asynccontextmanager
    from starlette.websockets import WebSocketDisconnect
    app,c,ctl,session,tab,calls=fixture(tmp_path);h=tab();rid=login(c,h).json()['remote_id'];row=ctl.remote.connections[rid]
    @asynccontextmanager
    async def failed(url,headers):
        raise OSError('controlled upstream connection failure')
        yield
    ctl.remote.connect_socket=failed
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f'wss://localhost:8443/api/local-host/remote/{rid}/ws/telemetry?local_tab='+h['X-Vision-Local-Tab']):pass
    assert row.active==0
