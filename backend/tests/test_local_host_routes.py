import asyncio
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient

ORIGIN = 'https://localhost:8443'


class Helper:
    ready = {'event':'ready','protocol':1,'platform':'darwin'}
    closed = False
    def __init__(self, path): self.path, self.calls, self.on_pick = path, [], None
    async def call(self, authority, method, args, **kwargs):
        self.calls.append(method)
        if method == 'host.approve': return {'approved':True}
        if method == 'dialog.folder':
            if self.on_pick: self.on_pick()
            return str(self.path)
        return {}


def setup(tmp_path):
    from backend.engine.shared_accounts import AccountStore
    from backend.local_host.controller import LocalHostController
    from backend.local_host.routes import local_host_router
    app = FastAPI(); app.state.accounts = AccountStore(tmp_path/'auth/accounts.sqlite')
    account = app.state.accounts.bootstrap('owner', 'isolated-test-password')
    session = app.state.accounts.login('owner', 'isolated-test-password', transport='cookie')
    app.state.context_registry = SimpleNamespace(workspace_id='workspace')
    selected = tmp_path/'selected'; selected.mkdir()
    helper = Helper(selected)
    controller = LocalHostController(app, helper, origin=ORIGIN, state_dir=tmp_path/'state')
    app.state.local_host = controller; app.include_router(local_host_router(controller))
    client = TestClient(app, base_url=ORIGIN, headers={'Origin':ORIGIN})
    client.cookies.set('vision_session', session['token'])
    headers = {'X-Vision-CSRF':session['csrf_token']}
    return app, client, controller, helper, session, headers


def test_cookie_csrf_and_owner_approval_are_real_boundaries(tmp_path):
    app, client, controller, helper, session, headers = setup(tmp_path)
    assert client.post('/api/local-host/session').status_code == 403
    tab = client.post('/api/local-host/session', headers=headers).json()['tab_id']
    h = {**headers, 'X-Vision-Local-Tab':tab}
    assert client.post('/api/local-host/dialog/folder',headers=h,json={'purpose':'project-open'}).status_code == 403
    assert helper.calls == []
    assert client.post('/api/local-host/approve',headers=h,json={}).json() == {'approved':True}
    result = client.post('/api/local-host/dialog/folder',headers=h,json={'purpose':'project-open'})
    assert result.status_code == 200 and result.json()['path'] == str(helper.path)
    assert result.json()['grant_id']
    client.cookies.clear()
    assert client.get('/api/local-host/capabilities').status_code == 401


def test_foreign_origin_tab_and_revoked_session_are_refused(tmp_path):
    app, client, controller, helper, session, headers = setup(tmp_path)
    assert client.post('/api/local-host/session',headers={**headers,'Origin':'https://evil.test'}).status_code == 403
    tab = client.post('/api/local-host/session',headers=headers).json()['tab_id']
    h = {**headers,'X-Vision-Local-Tab':tab}
    assert client.post('/api/local-host/approve',headers={**h,'X-Vision-Local-Tab':'unknown'},json={}).status_code == 403
    client.post('/api/local-host/approve',headers=h,json={})
    helper.on_pick = lambda: app.state.accounts.logout(session['token'])
    assert client.post('/api/local-host/dialog/folder',headers=h,json={'purpose':'project-open'}).status_code == 401
    assert not controller.grants.rows


def test_unknown_helper_actions_and_helper_crash_do_not_reach_native_api(tmp_path):
    app, client, controller, helper, session, headers = setup(tmp_path)
    tab=client.post('/api/local-host/session',headers=headers).json()['tab_id'];h={**headers,'X-Vision-Local-Tab':tab}
    client.post('/api/local-host/approve',headers=h,json={});before=list(helper.calls)
    assert client.post('/api/local-host/assistant/arbitraryInvoke',headers=h,json={'args':[]}).status_code in {404,422}
    assert helper.calls == before
    helper.closed = True
    assert client.post('/api/local-host/dialog/folder',headers=h,json={'purpose':'project-open'}).status_code == 503


def test_role_changed_during_chooser_cannot_issue_a_path_grant(tmp_path):
    app,client,controller,helper,session,headers=setup(tmp_path)
    role=['owner']
    class Registry:
        def resolve_request(self,request,account):
            return {'project':{'id':'project','project_dir':str(tmp_path/'project')},'role':role[0],
                    'context':{'workspace_id':'workspace','project_id':'project'}}
    app.state.local_projects=Registry()
    tab=client.post('/api/local-host/session',headers=headers).json()['tab_id']
    h={**headers,'X-Vision-Local-Tab':tab,'X-Vision-Project':'project'}
    client.post('/api/local-host/approve',headers=h,json={})
    helper.on_pick=lambda:role.__setitem__(0,'viewer')
    assert client.post('/api/local-host/dialog/folder',headers=h,json={'purpose':'project-open'}).status_code==409
    assert not controller.grants.rows


def test_process_status_distinguishes_live_server_native_child_and_dead_helper(tmp_path):
    import os
    app,client,controller,helper,session,headers=setup(tmp_path)
    helper.ready={**helper.ready,'pid':22600}
    helper.process=SimpleNamespace(pid=22601,returncode=None)
    status=client.get('/api/local-host/status').json()
    assert status['server']=={'pid':os.getpid(),'state':'serving'}
    assert status['helper']['pid']==22600
    assert status['helper']['supervisor_pid']==22601
    assert status['helper']['state']=='ready'
    assert status['restart']=='manual'
    assert helper.calls==[], 'diagnostics cannot create a vault or open an approval dialog'
    helper.closed=True
    dead=client.get('/api/local-host/status').json()
    assert dead['helper']['state']=='unavailable' and dead['helper']['pid'] is None
    assert client.get('/api/local-host/status',headers={'Origin':'https://evil.test'}).status_code==403
    client.cookies.clear()
    assert client.get('/api/local-host/status').status_code==401


def test_labelset_changed_during_native_selection_cannot_target_the_new_labelset(tmp_path):
    app,client,controller,helper,session,headers=setup(tmp_path)
    labelset=['original'];workspace=tmp_path/'project';workspace.mkdir()
    class Registry:
        def resolve_request(self,request,account):
            return {'project':{'id':'project','project_dir':str(workspace),'active_labelset_id':labelset[0]},
                    'role':'owner','context':{'workspace_id':'workspace','project_id':'project'}}
    app.state.local_projects=Registry()
    tab=client.post('/api/local-host/session',headers=headers).json()['tab_id']
    h={**headers,'X-Vision-Local-Tab':tab,'X-Vision-Project':'project'}
    client.post('/api/local-host/approve',headers=h,json={})
    helper.on_pick=lambda:labelset.__setitem__(0,'different')
    assert client.post('/api/local-host/dialog/folder',headers=h,json={'purpose':'label-import'}).status_code==409
    assert not controller.grants.rows
    assert not (workspace/'.local-host-imports').exists()
