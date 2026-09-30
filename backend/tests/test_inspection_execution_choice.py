import json
import sqlite3
from pathlib import Path

import pytest
from backend.api import routes_flowchart
from backend.api.routes_project import get_current_project
from backend.engine.annotation_storage import scoped_annotation_root
from backend.tests.test_inspection_history import configured_flow


def selected_run(monkeypatch,tmp_path):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    client,project,source,image,checkpoint,first,second,payload=configured_flow(monkeypatch,tmp_path)
    profile={'id':'selected-qa','name':'Selected QA','ssh_target':'qa@server','ssh_port':22,
             'remote_root':'/qa/runtime','runtime_kind':'docker','runtime_value':'worker-image','gpu_selector':'2'}
    assert client.post('/api/compute/profiles',json=profile).status_code==201
    payload.update(execution_target='selected_compute',device='cuda',compute_profile_id=profile['id'],project_id=project['id'])
    return client,project,source,image,payload,profile


def result(image,**extra):
    return {'status':'success','final_verdict':'OK','is_ok':True,'rejection_reason':'','roi_count':1,
            'defective_roi_count':0,'crops':[],'execution_steps':[{'node_id':'inspection','name':'Inspection','status':'passed','latency_ms':1}],
            'total_latency_ms':1,'image_path':str(image),'image_id':image.stem,
            'execution_target':'selected_compute','execution_device':'cuda','compute_profile_id':'selected-qa',**extra}


def test_batch_freezes_explicit_execution_and_keeps_original_project_after_switch(monkeypatch,tmp_path):
    client,project,source,image,payload,profile=selected_run(monkeypatch,tmp_path)
    created=client.post('/api/inspections/runs',json=payload)
    assert created.status_code==200,created.text
    run_id=created.json()['run_id']
    saved=client.get(f'/api/inspections/runs/{run_id}').json()
    assert saved['execution_config']['execution_target']=='selected_compute'
    assert saved['execution_config']['device']=='cuda'
    assert saved['execution_config']['compute_profile_id']==profile['id']
    assert saved['execution_config']['project_id']==project['id']
    other=client.post('/api/project/create',json={'name':'Other scope','task':'segmentation'}).json()
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    calls=[]
    def execute(req,request):
        assert get_current_project(request)['id']==project['id']!=other['id']
        assert scoped_annotation_root(Path('unused'))==Path(project['annotations_dir'])
        assert req.execution_target=='selected_compute' and req.device=='cuda' and req.compute_profile_id==profile['id']
        assert request.state.frozen_execution_profile.model_dump()==profile
        calls.append(req)
        return result(image)
    monkeypatch.setattr(routes_flowchart,'run_flowchart',execute)
    response=client.post(f'/api/inspections/runs/{run_id}/execute',json={'image_path':str(image)})
    assert response.status_code==200,response.text
    assert response.json()['execution_target']=='selected_compute' and response.json()['execution_device']=='cuda'
    assert len(calls)==1 and client.get('/api/project/current').json()['id']==other['id']
    assert client.get(f'/api/inspections/runs/{run_id}').json()['rows'][0]['result']['compute_profile_id']==profile['id']


def test_explicit_batch_create_rejects_stale_or_absent_project_identity(monkeypatch,tmp_path):
    client,project,source,image,payload,profile=selected_run(monkeypatch,tmp_path)
    assert client.post('/api/inspections/runs',json={**payload,'project_id':'other-project'}).status_code==409
    payload.pop('project_id')
    assert client.post('/api/inspections/runs',json=payload).status_code==422


@pytest.mark.parametrize('damage',['profile','execution_config','device_readback'])
def test_batch_refuses_changed_frozen_execution_or_wrong_device(monkeypatch,tmp_path,damage):
    client,project,source,image,payload,profile=selected_run(monkeypatch,tmp_path)
    created=client.post('/api/inspections/runs',json=payload);assert created.status_code==200,created.text
    run_id=created.json()['run_id'];calls=[]
    if damage=='profile':
        assert client.post('/api/compute/profiles',json={**profile,'gpu_selector':'3'}).status_code==201
    elif damage=='execution_config':
        with sqlite3.connect(Path(project['project_dir'])/'inspection_history.sqlite3') as conn:
            config=json.loads(conn.execute('SELECT execution_config_json FROM runs WHERE run_id=?',(run_id,)).fetchone()[0])
            config['device']='cpu'
            conn.execute('UPDATE runs SET execution_config_json=? WHERE run_id=?',(json.dumps(config),run_id))
    def execute(req,request):
        calls.append(req)
        return result(image,execution_device='cpu')
    monkeypatch.setattr(routes_flowchart,'run_flowchart',execute)
    response=client.post(f'/api/inspections/runs/{run_id}/execute',json={'image_path':str(image)})
    assert response.status_code==(502 if damage=='device_readback' else 409),response.text
    assert len(calls)==(1 if damage=='device_readback' else 0)
    assert client.get(f'/api/inspections/runs/{run_id}').json()['rows'][0]['state']=='pending'
