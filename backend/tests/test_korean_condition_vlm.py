"""A real local HTTP receiver exercises VLM image transport and review adoption."""
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pytest
from backend.tests.test_label_candidate_api import workspace
from backend.engine.annotation_storage import dataset_annotation_dir

@pytest.fixture
def vlm_receiver():
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append({'body':body,'authorization':self.headers.get('Authorization')})
            payload={'choices':[{'message':{'content':json.dumps({'candidates':[{'label':'스크래치','confidence':.91,'bbox':[30,20,45,32],'reason':'길고 가는 표면 손상'}]},ensure_ascii=False)}}]}
            raw=json.dumps(payload).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}/v1/chat/completions',received
    finally:server.shutdown();server.server_close();thread.join(2)


def test_korean_conditions_and_examples_use_configured_http_provider_and_require_review(workspace,vlm_receiver,monkeypatch):
    client,project,source=workspace;endpoint,received=vlm_receiver;monkeypatch.setenv('MODU_TEST_VLM_KEY','secret-provider-token')
    setup={'vlm':{'enabled':True,'endpoint':endpoint,'model':'vision-local','api_key_env':'MODU_TEST_VLM_KEY'}}
    configured=client.put('/api/label-candidates/setup',json=setup)
    assert configured.status_code==200,configured.text
    assert configured.json()['providers']['vlm']['ready']
    created=client.post('/api/label-candidates/generate',json={'backend':'vlm','image_path':str(source/'target.png'),'prompt':'길고 가는 표면 손상만 찾고 인쇄 무늬는 제외하세요.','label':'스크래치','output_geometry':'bbox',
        'positive_examples':[{'image_path':str(source/'target.png'),'roi':[30,20,45,32]}], 'negative_examples':[{'image_path':str(source/'target.png'),'roi':[0,0,15,12]}]})
    assert created.status_code==200,created.text
    proposal=created.json();assert proposal['status']=='pending' and proposal['candidates'][0]['annotation']['bbox']==[30.,20.,45.,32.]
    body=received[0]['body'];content=body['messages'][1]['content'];text=' '.join(p.get('text','') for p in content)
    assert '길고 가는 표면 손상만' in text and '긍정' in text and '부정' in text
    assert len([p for p in content if p['type']=='image_url'])==3
    assert received[0]['authorization']=='Bearer secret-provider-token'
    path=dataset_annotation_dir(source,Path(project['annotations_dir']),use_scope=False)/'target.json'
    assert not path.exists() and 'secret-provider-token' not in json.dumps(proposal)
    assert 'secret-provider-token' not in (Path(project['project_dir'])/'semantic_labeling.json').read_text()
    accepted=client.post('/api/label-suggestions/'+proposal['id']+'/review',json={'decision':'accept','candidate_ids':[proposal['candidates'][0]['id']],'actor':'Kim'})
    assert accepted.status_code==200,accepted.text
    assert json.loads(path.read_text())['annotations'][0]['label']=='스크래치'


def test_vlm_is_opt_in_and_configuration_rejects_inline_secrets(workspace):
    client,project,source=workspace
    assert client.put('/api/label-candidates/setup',json={'api_key':'secret'}).status_code==422
    response=client.post('/api/label-candidates/generate',json={'backend':'vlm','image_path':str(source/'target.png'),'prompt':'결함 찾기','output_geometry':'bbox'})
    assert response.status_code==422 and 'configured' in response.text.lower()
    assert client.put('/api/label-candidates/setup',json={'vlm':{'enabled':True,'endpoint':'http://remote.example/v1/chat/completions','model':'vision'}}).status_code==422


@pytest.mark.parametrize('role',['labeler','trainer','reviewer','owner'])
def test_shared_provider_configuration_is_admin_only_and_members_use_saved_provider(workspace,monkeypatch,role):
    import asyncio,io,httpx
    from backend.api.shared_authorization import SharedAuthorizationMiddleware
    client,project,source=workspace;app=client.app;calls=[]
    monkeypatch.setenv('MODU_TEST_VLM_KEY','approved-provider-key')
    monkeypatch.setenv('MODU_SERVER_SENTINEL_SECRET','must-not-leave-server')
    class Accounts:
        def authenticate(self,token):return {'id':token,'username':token,'administrator':token=='admin'}
        def project_for(self,*args):return {'path':project['project_dir']}
        def project_role(self,user,*args):return 'owner' if user=='admin' else role
    app.state.accounts=Accounts()
    wrapped=SharedAuthorizationMiddleware(app,app)
    class Opener:
        def open(self,request,timeout):
            calls.append({'url':request.full_url,'authorization':request.get_header('Authorization')})
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps({'candidates':[{'label':'스크래치','confidence':.9,'bbox':[1,1,10,10]}]})}}]}).encode())
    monkeypatch.setattr('urllib.request.build_opener',lambda *args:Opener())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped),base_url='http://local') as member:
            saved=await member.put('/api/label-candidates/setup',headers={'Authorization':'Bearer admin'},json={'vlm':{'enabled':True,'endpoint':'https://approved.example/v1/chat/completions','model':'vision','api_key_env':'MODU_TEST_VLM_KEY'}})
            assert saved.status_code==200,saved.text
            forbidden=await member.put('/api/label-candidates/setup',headers={'Authorization':'Bearer member'},json={'vlm':{'enabled':True,'endpoint':'https://attacker.example/v1/chat/completions','model':'vision','api_key_env':'MODU_SERVER_SENTINEL_SECRET'}})
            assert forbidden.status_code==403,forbidden.text
            readback=await member.get('/api/label-candidates/setup',headers={'Authorization':'Bearer member'})
            assert readback.json()['can_configure_vlm'] is False
            assert readback.json()['configuration']['vlm']['endpoint']=='https://approved.example/v1/chat/completions'
            generated=await member.post('/api/label-candidates/generate',headers={'Authorization':'Bearer member'},json={'backend':'vlm','image_path':str(source/'target.png'),'prompt':'표면 손상','label':'스크래치','output_geometry':'bbox'})
            assert generated.status_code==200,generated.text
            assert generated.json()['status']=='pending' and 'must-not-leave-server' not in generated.text
    asyncio.run(run())
    assert calls==[{'url':'https://approved.example/v1/chat/completions','authorization':'Bearer approved-provider-key'}]
    assert 'MODU_SERVER_SENTINEL_SECRET' not in (Path(project['project_dir'])/'semantic_labeling.json').read_text()
