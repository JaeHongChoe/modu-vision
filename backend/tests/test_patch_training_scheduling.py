"""Actual owned patch preparation and durable scheduling; worker launch observed, not executed."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from PIL import Image

@pytest.fixture
def patch_launch(tmp_path,monkeypatch):
 from backend.main import create_app
 from backend.api import routes_training
 monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
 app=create_app(project_dir=str(tmp_path/'projects'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
 project=client.post('/api/project/create',json={'name':'Patch scheduling'}).json();source=tmp_path/'source';source.mkdir()
 for i in range(3):
  p=source/f'{i}.png';Image.new('RGB',(32,32),(i,0,0)).save(p);p.with_suffix('.json').write_text(json.dumps({'imagePath':p.name,'imageWidth':32,'imageHeight':32,'shapes':[{'label':'chip','shape_type':'rectangle','points':[[0,0],[8,8]]}]}))
 assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
 prepared=client.post('/api/patch-classification/prepare',json={'patch_size':16,'stride':16});assert prepared.status_code==200,prepared.text
 calls=[]
 def capture(job_id,task,dataset_path,output_dir,**kwargs):
  ledger=kwargs.get('ledger');calls.append({'job_id':job_id,'task':task,'dataset_path':dataset_path,'output_dir':output_dir,'budget_at_launch':json.loads(ledger.store.record(job_id)['budget_json'] or '{}') if ledger else None,**kwargs});return SimpleNamespace(status='queued')
 monkeypatch.setattr(routes_training.training_job_manager,'start_job',capture)
 return client,project,source,calls,{'dataset_path':prepared.json()['dataset_path'],'epochs':1,'device':'cpu'}

def test_patch_scheduling_is_saved_before_launch_and_preserves_prepared_source_binding(patch_launch):
 client,project,source,calls,body=patch_launch
 response=client.post('/api/patch-classification/train',json={**body,'max_runtime_s':90,'queue':False,'priority':0});assert response.status_code==200,response.text
 assert len(calls)==1;call=calls[0];assert call['budget_at_launch']=={'max_runtime_s':90,'max_attempts':1};assert call['queue_when_busy'] is False and call['priority']==0
 assert call['dataset_path']==body['dataset_path'] and call['source_dataset_path']==str(source)
 assert call['dataset_binding']['family_provenance']['source_dataset_path']==str(source)
 assert call['dataset_binding']['family_dataset_path']==body['dataset_path']
 assert Path(call['output_dir']).parent==Path(project['models_dir'])
 assert call['ledger'].store.record(call['job_id'])['project_dir']==project['project_dir']

def test_patch_budget_failure_refuses_launch_and_ends_the_accepted_record(patch_launch,monkeypatch):
 from backend.engine.job_store import JobStore
 from backend.api.routes_training import job_ledger
 client,project,_,calls,body=patch_launch
 recorded=[]
 def refuse(store,job_id,budget):
  recorded.append(job_id);raise OSError('Controlled patch budget outage')
 monkeypatch.setattr(JobStore,'set_budget',refuse)
 response=client.post('/api/patch-classification/train',json={**body,'max_runtime_s':60});assert response.status_code==503,response.text;assert calls==[]
 assert not list(Path(project['models_dir']).glob('job_*'))
 assert len(recorded)==1 and job_ledger().get(recorded[0]).state=='failed'

def test_patch_same_request_replays_original_job_without_another_launch(patch_launch):
 client,_,_,calls,body=patch_launch;headers={'Idempotency-Key':'patch-same-request'}
 first=client.post('/api/patch-classification/train',json={**body,'max_runtime_s':60},headers=headers);assert first.status_code==200,first.text
 second=client.post('/api/patch-classification/train',json={**body,'max_runtime_s':60},headers=headers);assert second.status_code==200,second.text
 assert second.json()['idempotent_replay'] is True and second.json()['job_id']==first.json()['job_id'];assert len(calls)==1
 changed=client.post('/api/patch-classification/train',json={**body,'max_runtime_s':61},headers=headers);assert changed.status_code==409,changed.text;assert len(calls)==1

def test_patch_actual_shared_queue_retains_budget_priority_and_prepared_binding(patch_launch,monkeypatch):
 from backend.api import routes_training
 client,project,source,_,body=patch_launch;manager=routes_training.TrainingJobManager()
 manager._local_watcher=SimpleNamespace(is_alive=lambda:True)
 monkeypatch.setattr(routes_training,'training_job_manager',manager)
 assert manager._leases.acquire('owned-queue-control','local-compute','all')
 try:
  response=client.post('/api/patch-classification/train',json={**body,'max_runtime_s':60,'priority':3});assert response.status_code==200,response.text
  job=response.json()['job_id'];assert response.json()['status']=='queued'
  rec=manager.get_job(job);assert rec.dataset_path==body['dataset_path'] and rec.source_dataset_path==str(source)
  row=rec.ledger.store.record(job);assert row['state']=='queued' and row['priority']==3 and row['wait_reason']=='device_reserved'
  assert json.loads(row['budget_json'])=={'max_runtime_s':60,'max_attempts':1}
  assert rec.ledger.store.attempts(job)==[]
  refused=client.post('/api/patch-classification/train',json={**body,'queue':False});assert refused.status_code==409,refused.text
  assert len(manager._local_waiting)==1
 finally:
  manager._shutdown.set();manager._leases.release('owned-queue-control',terminal=True)

@pytest.mark.parametrize('options',[{'max_runtime_s':0},{'max_runtime_s':604801},{'priority':11}])
def test_patch_invalid_scheduling_never_launches(patch_launch,options):
 client,_,_,calls,body=patch_launch;response=client.post('/api/patch-classification/train',json={**body,**options});assert response.status_code==422,response.text;assert calls==[]
