"""Real local accounts/registry/controller/broker; only remote HTTP and native provider boundary substituted."""
import asyncio
import copy
import json
import httpx
import pytest
from backend.tests.test_local_host_remote import fixture,login

REMOTE_CONTEXT={'workspace_id':'remote-w','project_id':'remote-p','actor_id':'remote-user','mode':'team'}
RUN='run_'+'a'*24


def remote_fixture(tmp_path,old_local=False):
    from backend.contracts.context import ContextRegistry
    from backend.local_host.projects import LocalProjectRegistry
    app,c,ctl,session,tab,calls=fixture(tmp_path)
    app.state.context_registry=ContextRegistry(tmp_path/'projects')
    registry=app.state.local_projects=LocalProjectRegistry(tmp_path/'registry')
    h=tab();local=app.state.accounts.cookie_session(session['token'])
    if old_local:
        folder=tmp_path/'old-local';folder.mkdir()
        project={'id':'local-p','name':'old local','task':'classification','project_dir':str(folder),'active_labelset_id':'default','source_dataset_dir':None}
        project.update({key:str(folder/name) for key,name in [('dataset_dir','dataset'),('models_dir','models'),('reports_dir','reports'),('annotations_dir','annotations')]})
        project.update(created_at='2026-10-27T00:00:00Z',updated_at='2026-10-27T00:00:00Z')
        from backend.engine.project_labelsets import load_labelsets
        load_labelsets(folder)
        (folder/'project.json').write_text(json.dumps(project))
        registered=registry.register(app,local,project)
        h={**h,'X-Vision-Context':json.dumps(registered['context'])}
    login_response=login(c,h);assert login_response.status_code==200,login_response.text
    rid=login_response.json()['remote_id']
    selected=c.post(f'/api/local-host/remote/{rid}/context',headers=h,json={'project_id':'remote-p','context':REMOTE_CONTEXT}).json()
    rid=selected['remote_id'];ref={'remote_id':rid,'authority_epoch':selected['authority_epoch']}
    h={**h,'X-Vision-Remote-Authority':json.dumps(ref)}
    policy={'role':'owner','status':200,'labelset':'labels-1'};native=[]
    async def upstream(request):
        calls.append(request)
        if request.url.path=='/api/project/current':return httpx.Response(policy['status'],json={'id':'remote-p','name':'Remote','task':'classification','project_dir':'/remote/private','active_labelset_id':policy['labelset']},headers={'X-Vision-Context':json.dumps(REMOTE_CONTEXT)})
        if request.url.path=='/api/accounts/me':return httpx.Response(policy['status'],json={'user':{'id':'remote-user','username':'remote','administrator':False},'projects':([] if policy['role'] is None else [{'id':'remote-p','path':'/remote/private','role':policy['role']}])})
        return httpx.Response(200,json={'ok':True})
    ctl.remote.transport=httpx.MockTransport(upstream)
    original=ctl.client.call
    async def provider(authority,method,args,**kwargs):
        native.append((copy.deepcopy(authority),method,copy.deepcopy(args)))
        if method=='assistant.beginRun':return True
        if method=='assistant.imageIntent':return {'ok':True,'grantId':'img_'+'b'*24,'expiresAt':9999999999999}
        if method=='assistant.send':return {'ok':True,'text':'controlled answer','toolCalls':[],'stop':'end_turn'}
        if method.startswith('assistant.'):return True
        return await original(authority,method,args,**kwargs)
    ctl.client.call=provider
    return app,c,ctl,session,tab,h,rid,policy,native,calls,local


@pytest.mark.parametrize('old_local',[False,True])
def test_remote_assistant_uses_remote_membership_but_keeps_local_vault_identity(tmp_path,old_local):
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path,old_local)
    response=c.post('/api/local-host/assistant/beginRun',headers=h,json={'args':[RUN,'save_split']})
    assert response.status_code==200 and response.json() is True,response.text
    authority=native[-1][0]
    assert authority['actor_id']==local['id'] and authority['project'] is None
    assert authority['project_id'] is None and authority['workspace_id'] is None
    assert authority['remote']['context']==REMOTE_CONTEXT and authority['remote']['role']=='owner'
    assert 'project_dir' not in json.dumps(authority['remote'])
    assert [request.url.path for request in calls].count('/api/accounts/me')==2
    response=c.post(f'/api/local-host/remote/{rid}/api/dataset/split',headers={**h,'X-Modu-Assistant-Run':RUN,'X-Vision-Remote-Context':json.dumps(REMOTE_CONTEXT)},json={'train':70,'val':20,'test':10})
    assert response.status_code==200,response.text
    assert calls[-1].headers['x-modu-assistant-run']==RUN
    assert c.post(f'/api/local-host/remote/{rid}/api/dataset/split',headers={**h,'X-Modu-Assistant-Run':RUN},json={}).status_code==403


@pytest.mark.parametrize('status,role',[(403,'owner'),(200,None),(200,'viewer')])
def test_refused_remote_membership_never_reaches_helper(tmp_path,status,role):
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path)
    policy.update(status=status,role=role)
    assert c.post('/api/local-host/assistant/beginRun',headers=h,json={'args':[RUN,'save_split']}).status_code==403
    assert native==[]


def test_unknown_unselected_foreign_and_missing_remote_envelopes_do_not_use_old_local_project(tmp_path):
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path,True)
    bad=[{**h,'X-Vision-Remote-Authority':json.dumps({'remote_id':'unknown','authority_epoch':1})},
         {**h,'X-Vision-Remote-Authority':json.dumps({'remote_id':rid,'authority_epoch':0})},
         {key:value for key,value in h.items() if key!='X-Vision-Remote-Authority'},
         {**h,'X-Vision-Local-Tab':tab()['X-Vision-Local-Tab']}]
    native.clear()
    for headers in bad:
        assert c.post('/api/local-host/assistant/beginRun',headers=headers,json={'args':[RUN,'save_split']}).status_code in {403,409}
    ctl.remote.connections[rid].context=None
    assert c.post('/api/local-host/assistant/beginRun',headers=h,json={'args':[RUN,'save_split']}).status_code==409
    assert native==[]


@pytest.mark.parametrize('change',['epoch','membership','labelset','local_logout'])
def test_late_remote_assistant_response_and_run_are_discarded(tmp_path,change):
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path)
    original=ctl.client.call
    async def late(authority,method,args,**kwargs):
        result=await original(authority,method,args,**kwargs)
        if change=='epoch':ctl.remote.advance(ctl.remote.connections[rid].owner)
        elif change=='membership':policy['status']=403
        elif change=='labelset':policy['labelset']='labels-2'
        else:app.state.accounts.logout(session['token'])
        return result
    ctl.client.call=late
    response=c.post('/api/local-host/assistant/beginRun',headers=h,json={'args':[RUN,'save_split']})
    assert response.status_code in {401,403,409},response.text
    assert not ctl.runs


def test_remote_native_path_calls_are_refused_even_without_remote_reference(tmp_path):
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path,True)
    local_headers={key:value for key,value in h.items() if key!='X-Vision-Remote-Authority'}
    for path,body in [('/dialog/file',{'purpose':'pretrained-weights'}),('/dialog/folder',{'purpose':'project-open'}),('/open',{'path':str(tmp_path/'old-local')})]:
        assert c.post('/api/local-host'+path,headers=local_headers,json=body).status_code==403
    assert native==[]
    # Another tab's chooser still has its own local authority.
    other=tab();response=c.post('/api/local-host/dialog/folder',headers=other,json={'purpose':'project-open'})
    assert response.status_code==200,response.text


def test_two_tabs_have_separate_remote_runs_and_unknown_scope_cannot_consume_one(tmp_path):
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path)
    other=tab();row=ctl.remote.connections[rid]
    # A second account-authorized broker connection uses the actual dedicated login response.
    original=ctl.remote.transport
    async def upstream(request):
        if request.url.path=='/api/accounts/login':return httpx.Response(200,json={'token':'other-token','expires_at':row.expires,'user':row.user})
        return await original.handle_async_request(request)
    ctl.remote.transport=httpx.MockTransport(upstream)
    second=login(c,other).json()['remote_id']
    selected=c.post(f'/api/local-host/remote/{second}/context',headers=other,json={'project_id':'remote-p','context':REMOTE_CONTEXT}).json();second=selected['remote_id']
    other={**other,'X-Vision-Remote-Authority':json.dumps({'remote_id':second,'authority_epoch':selected['authority_epoch']})}
    for headers in (h,other):assert c.post('/api/local-host/assistant/beginRun',headers=headers,json={'args':[RUN,'save_split']}).json() is True
    assert c.post(f'/api/local-host/remote/{second}/api/dataset/split',headers={**h,'X-Modu-Assistant-Run':RUN},json={}).status_code==403
    assert c.post(f'/api/local-host/remote/{rid}/api/training/start',headers={**h,'X-Modu-Assistant-Run':RUN},json={}).status_code==403
    foreign_context={**REMOTE_CONTEXT,'workspace_id':'other-remote-workspace'}
    assert c.post(f'/api/local-host/remote/{rid}/api/dataset/split',headers={**h,'X-Modu-Assistant-Run':RUN,'X-Vision-Remote-Context':json.dumps(foreign_context)},json={}).status_code==403
    for connection,headers in [(rid,h),(second,other)]:
        assert c.post(f'/api/local-host/remote/{connection}/api/dataset/split',headers={**headers,'X-Modu-Assistant-Run':RUN},json={}).status_code==200
    assert not ctl.runs


def test_remote_image_confirmation_is_bound_to_permission_snapshot_and_labelset(tmp_path):
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path)
    grant=c.post('/api/local-host/assistant/imageIntent',headers=h,json={'args':[{'fileName':'one.png','imageIdentity':'b'*64}]}).json()
    assert grant['ok'] is True
    policy['labelset']='labels-2'
    value={'requestId':'req_'+'c'*16,'context':{},'messages':[{'role':'user','text':'what is shown'}],'tools':[],
           'image':{'grantId':grant['grantId'],'imageIdentity':'b'*64,'mediaType':'image/png','base64':'AA=='}}
    before=len(native)
    assert c.post('/api/local-host/assistant/send',headers=h,json={'args':[value]}).status_code==403
    assert len(native)==before
    grant=c.post('/api/local-host/assistant/imageIntent',headers=h,json={'args':[{'fileName':'one.png','imageIdentity':'b'*64}]}).json()
    value['image']['grantId']=grant['grantId']
    assert c.post('/api/local-host/assistant/send',headers=h,json={'args':[value]}).status_code==200
    assert c.post('/api/local-host/assistant/send',headers=h,json={'args':[value]}).status_code==403


def test_remote_switch_during_native_chooser_issues_no_grant_or_snapshot(tmp_path):
    from backend.tests.test_local_host_routes import setup
    from backend.local_host.remote import install_remote
    app,c,ctl,helper,session,headers=setup(tmp_path);app.include_router(install_remote(ctl))
    tab=c.post('/api/local-host/session',headers=headers).json()['tab_id'];h={**headers,'X-Vision-Local-Tab':tab}
    c.post('/api/local-host/approve',headers=h,json={})
    async def upstream(request):return httpx.Response(200,json={'token':'remote-token','expires_at':9999999999,'user':{'id':'remote-user','username':'remote','administrator':False}})
    ctl.remote.transport=httpx.MockTransport(upstream)
    original=helper.call
    async def chooser(authority,method,args,**kwargs):
        if method=='dialog.folder':
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://localhost:8443',headers={**h,'Origin':'https://localhost:8443','Cookie':'vision_session='+session['token']}) as client:
                switched=await client.post('/api/local-host/remote/login',json={'server_url':'https://remote.test','username':'remote','password':'test-password-123'})
                assert switched.status_code==200
            return str(helper.path)
        return await original(authority,method,args,**kwargs)
    helper.call=chooser
    response=c.post('/api/local-host/dialog/folder',headers=h,json={'purpose':'project-open'})
    assert response.status_code==403,response.text
    assert not ctl.grants.rows


def test_local_assistant_return_and_labelset_change_use_newer_native_scope_epochs(tmp_path):
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path,True)
    assert c.post('/api/local-host/assistant/beginRun',headers=h,json={'args':[RUN,'save_split']}).status_code==200
    remote_epoch=native[-1][2]['projectAuthority']['epoch']
    bare={key:value for key,value in h.items() if key!='X-Vision-Remote-Authority'}
    assert c.post(f'/api/local-host/remote/{rid}/logout',headers=bare,json={}).status_code==200
    assert c.post('/api/local-host/assistant/beginRun',headers=bare,json={'args':[RUN,'save_split']}).status_code==200
    first=native[-1][2]['projectAuthority'];assert first['scope'].startswith('local:') and first['epoch']>remote_epoch
    from backend.engine.project_labelsets import create_labelset,activate_labelset
    created=create_labelset(tmp_path/'old-local','changed')
    activate_labelset(tmp_path/'old-local',created['id'])
    assert c.post('/api/local-host/assistant/beginRun',headers=bare,json={'args':[RUN,'save_split']}).status_code==200
    second=native[-1][2]['projectAuthority'];assert second['scope']!=first['scope'] and second['epoch']>first['epoch']
    assert any(method=='assistant.retireProject' for _,method,_ in native)
    assert c.post('/api/local-host/assistant/beginRun',headers=bare,json={'args':[RUN,'save_split']}).status_code==200
    assert native[-1][2]['projectAuthority']==second,'unchanged project actions must not recursively advance their own authority epoch'


@pytest.mark.parametrize('remote_selected',[True,False])
def test_actor_only_settings_ignore_revoked_local_project_but_keep_cookie_tab_approval(tmp_path,remote_selected):
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path,True)
    h={key:value for key,value in h.items() if key!='X-Vision-Remote-Authority'}
    if not remote_selected:assert c.post(f'/api/local-host/remote/{rid}/logout',headers=h,json={}).status_code==200
    accounts=app.state.accounts
    successor=accounts.create_user('successor','isolated-password-123')
    accounts.set_membership('local-p',successor['id'],'owner',local['id'],session_token=session['token'])
    accounts.remove_membership('local-p',local['id'],local['id'],session_token=session['token'])
    assert accounts.project_role_exact(local['id'],'local-p') is None
    native.clear()
    actions=[('/approve',{}),('/assistant/getState',{'args':[]}),
             ('/assistant/saveProvider',{'args':[{'id':'fake','apiKey':'test-only'}]}),
             ('/assistant/chatgpt.begin',{'args':[{}]}),
             ('/assistant/chatgpt.selectModel',{'args':['fake-record','fake-model']}),
             ('/updates/status',{'args':[]})]
    for path,body in actions:
        response=c.post('/api/local-host'+path,headers=h,json=body)
        assert response.status_code==200,response.text
        authority=native[-1][0]
        assert authority['actor_id']==local['id'] and authority['project'] is None and authority['workspace_id'] is None
    assert c.post('/api/local-host/dialog/file',headers=h,json={'purpose':'pretrained-weights'}).status_code==403
    ctl.tabs[h['X-Vision-Local-Tab']]['approved']=False
    assert c.post('/api/local-host/assistant/getState',headers=h,json={'args':[]}).status_code==403
    assert c.post('/api/local-host/approve',headers={**h,'X-Vision-CSRF':'wrong'},json={}).status_code==403
    c.cookies.clear()
    assert c.post('/api/local-host/approve',headers=h,json={}).status_code==401


def test_remote_context_switch_dispatches_native_cancellation_before_late_reply(tmp_path):
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path)
    other=tab();native.clear()
    async def scenario():
        started=asyncio.Event();cancelled=asyncio.Event();retirements=[]
        original=ctl.client.call
        async def boundary(authority,method,args,**kwargs):
            if method=='assistant.send':
                started.set()
                await asyncio.wait_for(cancelled.wait(),2)
                return {'ok':True,'text':'late cancelled provider reply'}
            if method=='assistant.retireProject':
                retirements.append((authority,args))
                cancelled.set()
            return await original(authority,method,args,**kwargs)
        ctl.client.call=boundary
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ctl.origin,
                headers={**h,'Origin':ctl.origin,'Cookie':'vision_session='+session['token']}) as client:
            pending=asyncio.create_task(client.post('/api/local-host/assistant/send',json={'args':[{'requestId':'req_'+'c'*16,'messages':[],'tools':[],'context':{}}]}))
            await asyncio.wait_for(started.wait(),2)
            changed=await client.post(f'/api/local-host/remote/{rid}/context',json={'project_id':'remote-p','context':REMOTE_CONTEXT})
            assert changed.status_code==200,changed.text
            assert (await pending).status_code==409
            assert len(retirements)==1
            authority,arguments=retirements[0]
            assert authority['actor_id']==local['id'] and authority['tab_id']==h['X-Vision-Local-Tab']
            assert arguments['epoch']==changed.json()['authority_epoch']-1 and arguments['epoch']%2==1
            assert authority['tab_id']!=other['X-Vision-Local-Tab']
    asyncio.run(scenario())
    assert c.post('/api/local-host/assistant/getState',headers=other,json={'args':[]}).status_code==200


def test_expired_remote_cleanup_keeps_local_principal_retires_resources_and_restores_local_paths(tmp_path):
    import time
    app,c,ctl,session,tab,h,rid,policy,native,calls,local=remote_fixture(tmp_path,True)
    assert c.post('/api/local-host/assistant/beginRun',headers=h,json={'args':[RUN,'save_split']}).status_code==200
    row=ctl.remote.connections[rid];row.expires=time.time()-1
    closed=[]
    class Socket:
        async def close(self,code):closed.append(code)
    row.streams.add(Socket())
    bare={key:value for key,value in h.items() if key!='X-Vision-Remote-Authority'}
    assert c.get(f'/api/local-host/remote/{rid}/api/project/current',headers=bare).status_code==401
    assert c.post(f'/api/local-host/remote/{rid}/logout',headers=tab(),json={}).status_code==403
    assert c.post(f'/api/local-host/remote/{rid}/logout',headers={**bare,'X-Vision-CSRF':'wrong'},json={}).status_code==403
    assert c.post(f'/api/local-host/remote/{rid}/logout',headers=bare,json={}).status_code==200
    assert rid not in ctl.remote.connections and not ctl.runs and closed==[1008]
    assert app.state.accounts.cookie_session(session['token'])['id']==local['id']
    assert any(method=='assistant.retireProject' for _,method,_ in native)
    assert c.post('/api/local-host/dialog/folder',headers=bare,json={'purpose':'project-open'}).status_code==200
    # The local account can reuse the broker for another server afterward.
    original=ctl.remote.transport
    async def upstream(request):
        if request.url.path=='/api/accounts/login':return httpx.Response(200,json={'token':'new-remote-token','expires_at':time.time()+30,'user':row.user})
        return await original.handle_async_request(request)
    ctl.remote.transport=httpx.MockTransport(upstream)
    connected=login(c,bare,'https://next.test');assert connected.status_code==200
    rid=connected.json()['remote_id'];ctl.remote.connections[rid].expires=time.time()-1
    assert ctl.remote.connections[rid].origin=='https://next.test'
    c.cookies.set('vision_session','wrong-cookie')
    assert c.post(f'/api/local-host/remote/{rid}/logout',headers=bare,json={}).status_code==401
    c.cookies.set('vision_session',session['token'])
    app.state.accounts.logout(session['token'])
    assert c.post(f'/api/local-host/remote/{rid}/logout',headers=bare,json={}).status_code==401
    assert rid in ctl.remote.connections
