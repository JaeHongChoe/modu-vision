"""New workspace actions must retain the existing role boundaries."""
import asyncio
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from backend.api.shared_authorization import SharedAuthorizationMiddleware


@pytest.mark.parametrize('role,path,allowed',[
 ('trainer','/api/product-delivery/packages/id/verify',True),
 ('viewer','/api/product-delivery/packages/id/select',True),
 ('viewer','/api/product-delivery/diagnostics',True),
 ('labeler','/api/product-delivery/operator/inspect',True),
 ('trainer','/api/product-delivery/operator/results/id/review',False),
 ('labeler','/api/product-delivery/protocol-test',False),
 ('trainer','/api/training-workspace/readiness',True),
 ('viewer','/api/flow-workspace/templates',False),
 ('labeler','/api/data-workbench/derived',True),
])
def test_workspace_role_policy(tmp_path,monkeypatch,role,path,allowed):
    from backend.api import routes_project
    project={'id':'p','project_dir':str(tmp_path)}
    monkeypatch.setattr(routes_project,'_load_project',lambda _:project)
    class Accounts:
        def authenticate(self,token):return {'id':'user','administrator':False}
        def project_for(self,*args):return {'path':str(tmp_path)}
        def project_role(self,*args):return role
    app=FastAPI();app.state.accounts=Accounts()
    @app.post(path)
    def endpoint():return {'action':'performed'}
    app.add_middleware(SharedAuthorizationMiddleware,project_app=app)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer fixture'}) as client:
            return await client.post(path,json={})
    response=asyncio.run(run())
    assert response.status_code==(200 if allowed else 403)
    if allowed:assert response.json()['action']=='performed'
