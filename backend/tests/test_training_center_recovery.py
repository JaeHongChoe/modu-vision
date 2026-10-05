"""The common inventory must retain scoped ledger-only jobs after a restart."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.engine.job_store import JobStore
from backend.api import routes_training


@pytest.mark.parametrize('task',['classification','segmentation','detection','anomaly','patch_classification'])
def test_common_inventory_reopens_ledger_only_failure_without_new_execution(tmp_path,monkeypatch,task):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userData'))
    app=create_app(project_dir=str(tmp_path/'projects'))
    client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':'Retained job','task':'classification'}).json()
    source=tmp_path/'source';source.mkdir()
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    context=app.state.context_registry.context(project,None)
    store=JobStore(tmp_path/'owned-jobs.sqlite3')
    monkeypatch.setattr(routes_training,'job_ledger',lambda:store)
    monkeypatch.setattr(routes_training,'training_job_manager',routes_training.TrainingJobManager())
    ref=store.submit(context,app.state.context_registry.project_key(context),'training',
                     {'task':task,'dataset_path':str(source),'labelset_id':'default'},
                     job_id='job_retained',output_dir=str(Path(project['models_dir'])/'job_retained'))
    store.transition(ref.id,ref.revision,'fail',{'reason':'Controlled preparation failure'})
    before=store.record(ref.id)
    inventory=client.get('/api/training-workspace/tasks');assert inventory.status_code==200,inventory.text
    own=[row for row in inventory.json()['tasks'] if row['job_id']==ref.id]
    assert len(own)==1
    assert own[0]['task']==task and own[0]['status']=='failed'
    assert own[0]['execution_job_id']==ref.id and own[0]['model_id']==ref.id
    assert 'Controlled preparation failure' in own[0]['error']['message']
    assert store.record(ref.id)==before and not Path(before['output_dir']).exists()
    other=client.post('/api/project/create',json={'name':'Other project','task':'classification'}).json()
    assert other['id']!=project['id']
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    assert not any(row['job_id']==ref.id for row in client.get('/api/training-workspace/tasks').json()['tasks'])


def test_ledger_readback_preserves_declared_labelset_without_rebinding_it():
    row={'id':'job_saved','spec_json':json.dumps({'task':'classification','dataset_path':'/source','labelset_id':'reviewed-v2'}),'output_dir':'/owned/models/job_saved'}
    record=routes_training._ledger_job_record(row,'failed','Preserved failure')
    assert record.dataset_binding=={'labelset_id':'reviewed-v2'}
