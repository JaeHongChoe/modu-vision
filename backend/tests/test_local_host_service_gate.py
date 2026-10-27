"""Native ownership supplements existing owner/admin service checks, without OS registration."""
from types import SimpleNamespace
from backend.tests.test_local_host_routes import setup


def test_local_service_registration_requires_native_approval_before_side_effect(tmp_path,monkeypatch):
    from backend.api import routes_runtime_services as routes
    app,client,controller,helper,session,headers=setup(tmp_path)
    project={'id':'project','project_dir':str(tmp_path/'project')}
    class Registry:
        def resolve_request(self,request,account):
            return {'project':project,'role':'owner','context':{'workspace_id':'workspace','project_id':'project'}}
    app.state.local_projects=Registry()
    effects=[]
    service=SimpleNamespace(activate_install=lambda:effects.append('activate') or {'registered':True})
    def manager(request,allowed=None):
        assert allowed=={'owner'}
        return service,project
    monkeypatch.setattr(routes,'manager',manager)
    app.include_router(routes.router)
    tab=client.post('/api/local-host/session',headers=headers).json()['tab_id']
    h={**headers,'X-Vision-Local-Tab':tab,'X-Vision-Project':'project'}
    assert client.post('/api/runtime-services/install/activate',headers=h).status_code==403
    assert effects==[]
    assert client.post('/api/local-host/approve',headers=h,json={}).json()=={'approved':True}
    assert client.post('/api/runtime-services/install/activate',headers=h).json()=={'registered':True}
    assert effects==['activate']
    controller.tabs.pop(tab)
    assert client.post('/api/runtime-services/install/activate',headers=h).status_code==403
    assert effects==['activate']
