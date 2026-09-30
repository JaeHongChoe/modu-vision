import inspect,json
from pathlib import Path
import httpx,numpy as np,pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import routes_project,routes_label_suggestions,routes_label_candidates,routes_dataset_versions
from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root,dataset_annotation_dir
from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root

@pytest.fixture
def workspace(tmp_path,monkeypatch):
    if 'app' not in inspect.signature(httpx.Client.__init__).parameters:
        original=httpx.Client.__init__
        def compatible(self,*args,app=None,**kwargs):return original(self,*args,**kwargs)
        monkeypatch.setattr(httpx.Client,'__init__',compatible)
    source=tmp_path/'source';source.mkdir();rng=np.random.default_rng(20);target=rng.integers(0,256,(60,80,3),dtype=np.uint8);patch=rng.integers(0,256,(12,15,3),dtype=np.uint8);target[20:32,30:45]=patch
    Image.fromarray(target).save(source/'target.png');Image.fromarray(patch).save(source/'exemplar.png')
    app=FastAPI();app.state.project_dir=tmp_path/'projects'
    @app.middleware('http')
    async def scope(request,call_next):
        active=getattr(app.state,'current_project',None)
        if not active:return await call_next(request)
        a=set_request_annotation_root(Path(active['annotations_dir']));p=set_request_project_root(Path(active['project_dir']));s=set_request_split_root(Path(active['dataset_dir'])/'splits')
        try:return await call_next(request)
        finally:reset_request_project_root(p);reset_request_annotation_root(a);reset_request_split_root(s)
    for router in [routes_project.router,routes_label_suggestions.router,routes_label_candidates.router,routes_dataset_versions.router]:app.include_router(router)
    client=TestClient(app);project=client.post('/api/project/create',json={'name':'candidate','task':'detection'}).json();client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    return client,project,source

def test_real_template_proposal_review_saves_exact_geometry_and_backup(workspace):
    client,project,source=workspace;before=(source/'target.png').read_bytes()
    created=client.post('/api/label-candidates/generate',json={'backend':'template_match','image_path':str(source/'target.png'),'exemplar_path':str(source/'exemplar.png'),'label':'Scratch','threshold':.99})
    assert created.status_code==200,created.text
    proposal=created.json();assert proposal['status']=='pending';assert proposal['candidates'][0]['annotation']['bbox']==[30.,20.,45.,32.]
    path=dataset_annotation_dir(source,Path(project['annotations_dir']),use_scope=False)/'target.json'
    assert not path.exists()
    adopted=client.post('/api/label-suggestions/'+proposal['id']+'/review',json={'decision':'accept','candidate_ids':[proposal['candidates'][0]['id']],'actor':'Reviewer Kim'})
    assert adopted.status_code==200,adopted.text
    assert adopted.json()['reviewer']=='Reviewer Kim' and adopted.json()['backup_version_id']
    assert json.loads(path.read_text())['annotations'][0]['bbox']==[30.,20.,45.,32.]
    assert (source/'target.png').read_bytes()==before
    assert client.post('/api/label-suggestions/'+proposal['id']+'/review',json={'decision':'reject'}).status_code==409

def test_missing_semantic_model_is_explicit_and_source_changed_blocks_template_review(workspace):
    client,project,source=workspace
    status=client.get('/api/label-candidates/setup').json();assert status['ready'] is False
    no_model=client.post('/api/label-candidates/generate',json={'backend':'grounding_dino','image_path':str(source/'target.png'),'prompt':'scratch.'})
    assert no_model.status_code==422
    proposal=client.post('/api/label-candidates/generate',json={'backend':'template_match','image_path':str(source/'target.png'),'exemplar_path':str(source/'exemplar.png'),'threshold':.99}).json()
    Image.new('RGB',(80,60),'white').save(source/'target.png')
    assert client.post('/api/label-suggestions/'+proposal['id']+'/review',json={'decision':'accept','candidate_ids':[proposal['candidates'][0]['id']]}).status_code==409
