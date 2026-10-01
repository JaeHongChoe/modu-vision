import importlib.util
from pathlib import Path
import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import routes_project
from backend.engine.evaluation_history import EvaluationHistory

@pytest.fixture
def client_workspace(tmp_path,monkeypatch):
    import httpx,inspect
    if 'app' not in inspect.signature(httpx.Client.__init__).parameters:
        original=httpx.Client.__init__
        def compatible(self,*args,app=None,**kwargs):return original(self,*args,**kwargs)
        monkeypatch.setattr(httpx.Client,'__init__',compatible)
    source=tmp_path/'source';source.mkdir();Image.new('RGB',(64,48),'gray').save(source/'part.png')
    app=FastAPI();app.state.project_dir=tmp_path/'projects';app.include_router(routes_project.router)
    if importlib.util.find_spec('backend.api.routes_data_workbench') is not None:
        from backend.api import routes_data_workbench
        app.include_router(routes_data_workbench.router)
    client=TestClient(app);project=client.post('/api/project/create',json={'name':'data-upgrade','task':'detection'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    return client,project,source

def test_diagnostics_derived_reopen_and_stale_annotation_guard(client_workspace):
    client,project,source=client_workspace
    result=client.post('/api/data-workbench/diagnostics',json={});assert result.status_code==200,result.text
    data=result.json();assert data['summary']['total']==1 and data['items'][0]['issues']==['blur']
    assert client.get('/api/data-workbench/diagnostics').json()['source_sha256']==data['source_sha256']
    row=data['items'][0]
    request={'image_path':str(source/'part.png'),'expected_revision':row['revision'],'expected_sha256':row['source_sha256'],'operation':{'kind':'flip','axis':'horizontal'},'actor':'Kim'}
    edited=client.post('/api/data-workbench/derived',json=request);assert edited.status_code==200,edited.text
    version=edited.json();assert client.get(version['image_url']).status_code==200
    assert client.get('/api/data-workbench/derived',params={'image_path':str(source/'part.png')}).json()['versions'][0]['id']==version['id']
    bad={**request,'expected_revision':0};assert client.post('/api/data-workbench/derived',json=bad).status_code==422
    outside=source.parent/'outside.png';Image.new('RGB',(64,48)).save(outside)
    assert client.post('/api/data-workbench/derived',json={**request,'image_path':str(outside)}).status_code==422


def test_queue_uses_saved_evaluation_and_scope_and_persists(client_workspace):
    client,project,source=client_workspace
    binding={'source_dataset_path':str(source),'task':'detection','labelset_id':'default'}
    record=EvaluationHistory(Path(project['project_dir'])/'reports'/'evaluations').append({'job_id':'trained','test_predictions':[{'file_path':str(source/'part.png'),'is_correct':False,'confidence':.9}]},binding)
    body={'evaluation_id':record['evaluation_id'],'threshold':.5,'margin':.05}
    created=client.post('/api/data-workbench/review-queues',json=body);assert created.status_code==200,created.text
    queue=created.json();assert queue['origin']['evidence_sha256']==record['evidence_sha256']
    assert client.get('/api/data-workbench/review-queues').json()['queues'][0]['id']==queue['id']
    advanced=client.post('/api/data-workbench/review-queues/'+queue['id']+'/advance',json={'expected_revision':queue['revision'],'relative_path':'part.png','state':'reviewed','actor':'Kim'})
    assert advanced.status_code==200 and advanced.json()['cursor']==1
    client.put('/api/project/update',json={'task':'segmentation'})
    assert client.get('/api/data-workbench/review-queues/'+queue['id']).status_code==409


def test_derived_versions_are_isolated_between_labelsets(client_workspace):
    client,project,source=client_workspace
    row=client.post('/api/data-workbench/diagnostics',json={}).json()['items'][0]
    version=client.post('/api/data-workbench/derived',json={'image_path':str(source/'part.png'),'expected_revision':row['revision'],'expected_sha256':row['source_sha256'],'operation':{'kind':'flip','axis':'horizontal'},'actor':'Kim'}).json()
    labelset=client.post('/api/project/labelsets',json={'name':'Second labels'}).json()
    client.put('/api/project/labelsets/'+labelset['id']+'/activate')
    assert client.get('/api/data-workbench/derived',params={'image_path':str(source/'part.png')}).json()['versions']==[]
    assert client.get('/api/data-workbench/derived/'+version['id']).status_code==409


def test_saved_comparison_disagreement_creates_queue_and_origin_digest_blocks_changed_report(client_workspace):
    import hashlib,json
    client,project,source=client_workspace
    comparison_id='comparison_'+'a'*32;directory=Path(project['reports_dir'])/'model_comparisons';directory.mkdir(parents=True)
    report={'comparison_id':comparison_id,'project_id':project['id'],'source_dataset_path':str(source),'task':'detection','labelset_id':'default','created_at':'2026-10-01T00:00:00Z','candidate_job_id':'candidate','images':[{'file_path':str(source/'part.png'),'image_sha256':hashlib.sha256((source/'part.png').read_bytes()).hexdigest(),'ground_truth_verdict':None,'candidate':{'verdict':'NG','max_defect_score':.9},'incumbent':{'verdict':'OK'},'disagrees':True}]}
    path=directory/(comparison_id+'.json');path.write_text(json.dumps(report))
    created=client.post('/api/data-workbench/review-queues',json={'comparison_id':comparison_id});assert created.status_code==200,created.text
    queue=created.json();assert queue['origin']['comparison_id']==comparison_id and queue['items'][0]['reasons']==['disagreement']
    report['images'][0]['candidate']['verdict']='OK';path.write_text(json.dumps(report))
    assert client.get('/api/data-workbench/review-queues/'+queue['id']).status_code==409


def test_diagnostics_use_project_saved_splits_and_invalidate_after_split_changes(client_workspace):
    import hashlib,json
    client,project,source=client_workspace
    directory=Path(project['dataset_dir'])/'splits';directory.mkdir(parents=True,exist_ok=True)
    path=directory/(hashlib.sha256(str(source.resolve()).encode()).hexdigest()+'.json')
    saved={'folder_path':str(source.resolve()),'seed':42,'assignments':{'part.png':'val'}}
    path.write_text(json.dumps(saved))
    result=client.post('/api/data-workbench/diagnostics',json={});assert result.status_code==200,result.text
    assert result.json()['items'][0]['split']=='val'
    assert result.json()['summary']['assignments']['val']['count']==1
    saved['assignments']['part.png']='train';path.write_text(json.dumps(saved))
    reopened=client.get('/api/data-workbench/diagnostics').json()
    assert reopened['stale'] is True
    assert reopened['items'][0]['current_split']=='train'
    assert reopened['items'][0]['split']=='val'
