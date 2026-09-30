from pathlib import Path
import pytest
from fastapi.testclient import TestClient


def clients(tmp_path):
    from backend.main import create_app
    app=create_app(str(tmp_path/'projects'),str(tmp_path/'auth'))
    admin=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    admin.post('/api/accounts/bootstrap',json={'username':'admin','password':'long password 123'})
    admin.headers['Authorization']='Bearer '+admin.post('/api/accounts/login',json={'username':'admin','password':'long password 123'}).json()['token']
    project=admin.post('/api/project/create',json={'name':'A'}).json()
    created=admin.post('/api/accounts/users',json={'username':'viewer','password':'long password 456'}).json()
    admin.put('/api/accounts/projects/'+project['id']+'/members',json={'user_id':created['id'],'role':'viewer'})
    viewer=TestClient(app,headers={'Authorization':'Bearer '+admin.post('/api/accounts/login',json={'username':'viewer','password':'long password 456'}).json()['token'],'X-Vision-Project':project['id']})
    return app,admin,viewer,project


def test_shared_viewer_cannot_inspect_server_ssh_profiles_or_other_project_leases(tmp_path,monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    app,admin,viewer,project=clients(tmp_path)
    from backend.engine.shared_scheduler import shared_leases
    leases=shared_leases();leases.acquire('foreign','host','0',remote=True,project_id='other')
    leases.acquire('own','host','1',remote=True,project_id=project['id'])
    assert viewer.get('/api/compute/profiles').status_code==403
    rows=viewer.get('/api/compute/reservations').json()['reservations']
    assert [row['job_id'] for row in rows]==['own']
    assert all('owner' not in row for row in rows)
    assert admin.get('/api/compute/profiles').status_code==200


def test_compute_api_exposes_real_family_and_distributed_contract(tmp_path,monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    _,admin,viewer,project=clients(tmp_path)
    result=admin.get('/api/compute/capabilities')
    assert result.status_code==200,result.text
    assert set(result.json()['training_tasks'])=={'classification','patch_classification','detection','segmentation','anomaly','rotation','ocr','rotated_detection','enhancement','defect_gan'}
    assert result.json()['labeling_provider']=='foundation'
    assert result.json()['distributed_tasks']==['classification','patch_classification','segmentation']
    assert viewer.post('/api/compute/jobs',json={'task':'classification','dataset_path':'/foreign','compute_profile_id':'missing'}).status_code==403


def test_completed_remote_ocr_reopens_in_native_model_selector_and_prediction(tmp_path,monkeypatch):
    import time
    from PIL import Image
    import torch
    from backend.api import routes_training
    from backend.remote.worker import run_train,run_infer
    from backend.remote.coordinator import run_remote_training
    from backend.tests.test_remote_coordinator import FakeRemote
    from backend.engine.prepared_family_datasets import prepare_family_dataset
    from backend.tests.test_ocr import _labeled_images
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    app,admin,_,project=clients(tmp_path)
    source=tmp_path/'source';rows=_labeled_images(source)
    assert admin.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    owned=prepare_family_dataset('ocr',source,Path(project['dataset_dir'])/'ocr'/'owned',rows).root
    profile={'id':'loopback','name':'Owned CPU','ssh_target':'loopback','ssh_port':22,'remote_root':str(tmp_path/'server'),'runtime_kind':'python','runtime_value':'python3'}
    assert admin.post('/api/compute/profiles',json=profile).status_code==201
    from backend.remote.ssh_transport import SSHTransport
    monkeypatch.setattr(SSHTransport,'probe',lambda self,profile:{'ready':True,'runtime_ready':True,'checks':{'cuda_device_count':0,'device_inventory':{'devices':[]}}})
    class RealRemote(FakeRemote):
        def launch(self,profile,argv,run_id):
            path=self.root/'runs'/run_id/'spec.json'
            result=run_infer(path) if run_id.startswith('op_') else run_train(path);assert result['status']=='completed',result
            return 'loopback-owned-worker'
    from backend.remote import coordinator
    monkeypatch.setattr(coordinator,'make_remote_runner',lambda profile,launch:lambda record:run_remote_training(record,profile,transport=RealRemote(Path(profile.remote_root)),device='cpu',config_overrides=launch['config_overrides']))
    torch.set_num_threads(1)
    response=admin.post('/api/compute/jobs',json={'task':'ocr','dataset_path':str(source),'family_dataset_path':str(owned),'compute_profile_id':'loopback','device':'cpu','config_overrides':{'epochs':1,'image_size':32,'image_width':64,'batch_size':2}})
    assert response.status_code==202,response.text
    submitted=response.json();identifier=submitted['job_id'];native=submitted['model_id']
    assert identifier=='job_'+native and len(native)==32
    for _ in range(300):
        status=admin.get('/api/compute/jobs/'+identifier).json()
        if status['status'] in ('completed','failed','aborted'):break
        time.sleep(.02)
    assert status['status']=='completed',status
    models=admin.get('/api/ocr/models').json()['models']
    assert any(model['job_id']==native for model in models),models
    predicted=admin.post('/api/ocr/predict',json={'job_id':native,'image_path':str(source/rows[0]['image']),'device':'cpu'})
    assert predicted.status_code==200,predicted.text
    assert isinstance(predicted.json()['text'],str)
    assert predicted.json()['model_sha256']==next(model['model_sha256'] for model in models if model['job_id']==native)
    result=admin.get('/api/compute/jobs/'+identifier+'/results')
    assert result.status_code==200 and result.json()['task']=='ocr'
    from backend.remote import operations
    monkeypatch.setattr(operations,'SSHTransport',lambda:RealRemote(Path(profile['remote_root'])))
    remote_prediction=admin.post('/api/compute/jobs/'+identifier+'/predict',json={'image_path':str(source/rows[0]['image'])})
    assert remote_prediction.status_code==200,remote_prediction.text
    assert remote_prediction.json()['model_id']==native
    assert isinstance(remote_prediction.json()['predictions']['text'],str)
    assert remote_prediction.json()['overlay_base64'].startswith('data:image/png;base64,')
