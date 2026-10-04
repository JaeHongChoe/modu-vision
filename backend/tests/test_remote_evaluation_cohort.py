"""Common-cohort evaluation crosses only the controlled transport boundary."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from threading import Event

import httpx
import pytest
import torch
from PIL import Image


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, payload):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload), encoding='utf-8')


class Client:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, **kwargs):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test',
                                         headers={'X-Vision-Token': self.app.state.api_token}) as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(send())

    def get(self, path, **kwargs): return self.request('GET', path, **kwargs)
    def post(self, path, **kwargs): return self.request('POST', path, **kwargs)
    def put(self, path, **kwargs): return self.request('PUT', path, **kwargs)


@pytest.fixture
def common_workspace(tmp_path, monkeypatch, request):
    task=getattr(request,"param","classification")
    from backend.main import create_app
    from backend.remote.profiles import ComputeProfile, get_profile_store
    from backend.remote.snapshot import build_snapshot, extract_snapshot
    from backend.engine.classification import create_classification_model
    from backend.tests.test_remote_coordinator import FakeRemote
    from backend.remote import operations, worker

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    monkeypatch.setattr(operations, 'OP_POLL_INTERVAL_SECONDS', .001)
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    client = Client(create_app(project_dir=str(tmp_path / 'registry')))
    project = client.post('/api/project/create', json={'name': 'Common cohort', 'task': task}).json()
    source = tmp_path / 'common'
    import numpy as np
    if task=='classification':
        paths=[source/'test/OK/OK.png',source/'test/NG/NG.png']
    elif task in ('detection','segmentation'):
        paths=[source/'images/test/normal.png',source/'images/test/defect.png']
    else:
        paths=[source/'test/good/normal.png',source/'test/defect/defect.png']
    for index,image in enumerate(paths):
        image.parent.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',(32,32),(30+index*120,45,60)).save(image)
        if task=='detection':
            write_json(image.with_suffix('.json'),{'imagePath':image.name,'imageWidth':32,'imageHeight':32,
                      'shapes':[] if index==0 else [{'label':'defect','shape_type':'rectangle','points':[[3,3],[15,15]]}]})
        if task in ('segmentation','anomaly'):
            mask=source/'masks/test'/image.name if task=='segmentation' else source/'ground_truth/defect/defect_mask.png'
            if task=='segmentation' or index==1:
                mask.parent.mkdir(parents=True,exist_ok=True)
                Image.fromarray(np.full((32,32),index,dtype=np.uint8)).save(mask)
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    project['source_dataset_dir'] = str(source)
    version = client.post('/api/dataset/versions', json={'name': 'Common held-out test'}).json()
    profile = ComputeProfile(id='original-target', name='Original verified server', ssh_target='owned-host',
                             ssh_port=22, remote_root=str(tmp_path / 'remote'), runtime_kind='python', runtime_value='python3')
    get_profile_store().save(profile)
    if task=='classification':
        model=create_classification_model('resnet18',2,pretrained=False)
        meta={'task':task,'backbone':'resnet18','classes':['OK','NG'],'image_size':[32,32]}
    elif task=='detection':
        from backend.engine.detection import create_detection_model
        model=create_detection_model('fast',2,pretrained=False)
        meta={'task':task,'preset':'fast','classes':['defect'],'image_size':[32,32]}
    elif task=='segmentation':
        from backend.engine.segmentation import build_segmentation_model
        model=build_segmentation_model('unet',2,pretrained=False)
        meta={'task':task,'model_name':'unet','preset':'fast','classes':['background','defect'],'image_size':[32,32]}
    else:
        from backend.engine.anomaly import PaDiMDetector
        model=PaDiMDetector(pretrained=False,target_dim=4)
        model.fit(torch.utils.data.DataLoader(torch.rand(2,3,32,32),batch_size=2))
        meta={'task':task,'detector_type':'padim','feature_backbone':'resnet18','classes':['good','anomaly'],
              'image_size':[32,32],'anomaly_mode':'segmentation'}
    if task!='anomaly':
        with torch.no_grad():
            for parameter in model.parameters():parameter.zero_()
    jobs = []
    for index in range(2):
        job_id = 'job_common_' + str(index)
        output = Path(project['models_dir']) / job_id
        original = tmp_path / ('training_' + str(index))
        image = original / 'train' / 'OK' / 'old.png'
        image.parent.mkdir(parents=True)
        Image.new('RGB', (32, 32), (index + 1, 2, 3)).save(image)
        snapshot = build_snapshot(original, output / 'remote_snapshot', Event())
        if task=='classification':
            with torch.no_grad():model.fc.bias[index]=10
        torch.save({**meta,'model_state_dict':model.state_dict()},output/'best_model.pt')
        write_json(output/'model_meta.json',{**meta,'checkpoint_sha256':digest(output/'best_model.pt')})
        remote_run = Path(profile.remote_root) / 'runs' / job_id
        extract_snapshot(snapshot.archive_path, remote_run / 'input', snapshot.manifest_sha256)
        artifacts = []
        for name in ('best_model.pt', 'model_meta.json'):
            target = remote_run / 'outputs' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output / name, target)
            artifacts.append({'path': 'outputs/' + name, 'size': target.stat().st_size, 'sha256': digest(target)})
        manifest = {'protocol_version': 1, 'job_id': job_id, 'operation': 'train',
                    'input_manifest_sha256': snapshot.manifest_sha256, 'artifacts': artifacts}
        write_json(remote_run / 'artifacts.json', manifest)
        write_json(remote_run / 'status.json', {'protocol_version': 1, 'job_id': job_id, 'operation': 'train', 'status': 'completed'})
        write_json(output / 'remote_artifacts.json', manifest)
        write_json(output / 'job_receipt.json', {'job_id': job_id, 'status': 'completed', 'task': task,
                   'compute_profile_id': profile.id, 'dataset_path': str(snapshot.data_path),
                   'source_dataset_path': str(original), 'dataset_fingerprint': 'v1:fixture_' + str(index)})
        write_json(output / 'remote_job.json', {'job_id': job_id, 'state': 'completed', 'task': task,
                   'profile': profile.model_dump(), 'dataset_path': str(snapshot.data_path), 'dataset_fingerprint': 'v1:fixture_' + str(index),
                   'source_dataset_path': str(original), 'input_manifest_sha256': snapshot.manifest_sha256})
        jobs.append(output)

    class EvaluationWorkerRemote(FakeRemote):
        def __init__(self, root):
            super().__init__(root)
            self.uploaded = []
            self.worker_results = []
        def upload(self, selected, local, relative, **kwargs):
            self.uploaded.append(relative)
            return super().upload(selected, local, relative, **kwargs)
        def launch(self, selected, argv, run_id):
            self.launches += 1
            result = worker.run_evaluate(self.root / 'runs' / run_id / 'spec.json')
            self.worker_results.append(result)
            return 'owned-evaluation-worker'

    remote = EvaluationWorkerRemote(Path(profile.remote_root))
    monkeypatch.setattr(operations, 'SSHTransport', lambda: remote)
    # A common request must enter the actual remote worker evaluator, never the route's local fallback.
    from backend.api import routes_evaluation
    monkeypatch.setattr(routes_evaluation, 'get_device', lambda: pytest.fail('API host evaluation fallback'))
    yield client, project, source, version, profile, remote, jobs
    torch.set_num_threads(previous_threads)


def request_common(workspace, *, job=0, **overrides):
    client, _, source, version, profile, _, jobs = workspace
    params = {'job_id': jobs[job].name, 'dataset_path': str(source),
              'evaluation_dataset_version_id': version['id'], 'compute_profile_id': profile.id, 'device': 'cpu'}
    params.update(overrides)
    return client.get('/api/evaluation/results', params=params)


def test_shared_actor_cannot_discover_or_cancel_another_common_operation(common_workspace,tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    _,project,source,version,profile,remote,jobs=common_workspace
    app=create_app(project_dir=str(tmp_path/'registry'),shared_auth_dir=str(tmp_path/'shared-auth'))
    bootstrap=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    assert bootstrap.post('/api/accounts/bootstrap',json={'username':'fixture-admin','password':'fixture-password123'}).status_code==200
    admin=TestClient(app)
    admin.headers['Authorization']='Bearer '+admin.post('/api/accounts/login',json={'username':'fixture-admin','password':'fixture-password123'}).json()['token']
    owner=app.state.accounts.authenticate(admin.headers['Authorization'].removeprefix('Bearer '))
    app.state.accounts.register_project(project['id'],project['project_dir'],owner['id'])
    clients=[]
    for name in ('first-trainer','second-trainer'):
        user=admin.post('/api/accounts/users',json={'username':name,'password':'fixture-password456','administrator':False}).json()
        assert admin.put('/api/accounts/projects/'+project['id']+'/members',json={'user_id':user['id'],'role':'trainer'}).status_code==200
        client=TestClient(app);client.headers['Authorization']='Bearer '+client.post('/api/accounts/login',json={'username':name,'password':'fixture-password456'}).json()['token']
        assert client.post('/api/accounts/select-project',json={'project_id':project['id']}).status_code==200
        clients.append(client)
    params={'job_id':jobs[0].name,'dataset_path':str(source),'evaluation_dataset_version_id':version['id'],'compute_profile_id':profile.id,'device':'cpu'}
    result=clients[0].get('/api/evaluation/results',params=params)
    assert result.status_code==200,result.text
    listed=clients[0].get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name})
    assert listed.status_code==200 and len(listed.json()['operations'])==1,listed.text
    operation=listed.json()['operations'][0]
    journal=next((jobs[0]/'remote_operations').glob('evaluate_*.json'))
    state=json.loads(journal.read_text(encoding='utf-8'));state.update(state='uploading',worker_exit_confirmed=False)
    write_json(journal,state);before=journal.read_bytes()
    hidden=clients[1].get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name})
    assert hidden.status_code==200 and hidden.json()['operations']==[],hidden.text
    body={key:operation[key] for key in ('job_id','cohort_sha256','evaluation_binding_sha256')}
    refused=clients[1].post('/api/evaluation/remote-operations/'+operation['op_id']+'/cancel',json=body)
    assert refused.status_code==404,refused.text
    assert journal.read_bytes()==before
    assert not list((jobs[0]/'remote_operations').glob('*/cancel-intent.json'))
    accepted=clients[0].post('/api/evaluation/remote-operations/'+operation['op_id']+'/cancel',json=body)
    assert accepted.status_code==200 and accepted.json()['state']=='cancel_requested',accepted.text


def test_remote_models_with_distinct_training_snapshots_evaluate_one_frozen_common_test(common_workspace):
    client, project, source, _, profile, remote, jobs = common_workspace
    responses = [request_common(common_workspace, job=index) for index in (0, 1)]
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    rows = [response.json() for response in responses]
    assert remote.launches == 2 and all(row['status'] == 'completed' for row in remote.worker_results)
    assert rows[0]['common_cohort'] == rows[1]['common_cohort']
    assert rows[0]['binding']['checkpoint_sha256'] != rows[1]['binding']['checkpoint_sha256']
    expected = {str(path.resolve()) for path in source.rglob('*.png')}
    for row in rows:
        assert {item['file_path'] for item in row['test_predictions']} == expected
        assert row['metrics']['evaluated_split'] == 'test' and row['metrics']['selection_overlap'] is False
        assert row['execution_target'] == 'model_compute' and row['compute_profile_id'] == profile.id
        assert row['device'] == row['resolved_device'] == 'cpu'
        assert row['runtime_device_identity']['device'] == 'cpu'
    assert len(list((Path(project['reports_dir']) / 'evaluations').glob('evaluation_*.json'))) == 2
    assert all((job / 'eval_results.json').is_file() for job in jobs)
    from backend.engine.shared_scheduler import shared_leases
    assert shared_leases().list() == []


def test_different_target_is_refused_before_upload(common_workspace):
    _, _, _, _, _, remote, _ = common_workspace
    response = request_common(common_workspace, compute_profile_id='different')
    assert response.status_code in (404, 409), response.text
    assert remote.uploaded == [] and remote.launches == 0


def test_common_request_does_not_weaken_legacy_dataset_override(common_workspace):
    client, _, source, _, _, remote, jobs = common_workspace
    response = client.get('/api/evaluation/results', params={'job_id': jobs[0].name, 'dataset_path': str(source)})
    assert response.status_code == 422 and 'snapshot' in response.text
    assert remote.uploaded == [] and remote.launches == 0


def test_cancel_during_transfer_is_durable_bound_and_prevents_worker_launch(common_workspace,monkeypatch):
    client, _, _, version, profile, remote, jobs=common_workspace
    original_upload=remote.upload
    observed=[]
    def upload(selected,local,relative,**kwargs):
        if not observed:
            rows=client.get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name})
            assert rows.status_code==200,rows.text
            row=rows.json()['operations'][0]
            assert row['dataset_version_id']==version['id'] and row['compute_profile_id']==profile.id
            assert row['task']=='classification' and row['labelset_id']=='default' and row['device']=='cpu'
            body={k:row[k] for k in ('job_id','cohort_sha256','evaluation_binding_sha256')}
            wrong=client.post('/api/evaluation/remote-operations/'+row['op_id']+'/cancel',json={**body,'cohort_sha256':'0'*64})
            assert wrong.status_code==409
            cancelled=client.post('/api/evaluation/remote-operations/'+row['op_id']+'/cancel',json=body)
            assert cancelled.status_code==200,cancelled.text
            observed.append(cancelled.json())
        return original_upload(selected,local,relative,**kwargs)
    monkeypatch.setattr(remote,'upload',upload)
    response=request_common(common_workspace)
    assert response.status_code==409,response.text
    assert remote.launches==0 and observed[0]['cancel_requested_at']
    rows=client.get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name}).json()['operations']
    assert rows[0]['state']=='aborted' and rows[0]['worker_exit_confirmed'] is True
    assert rows[0]['cancel_requested_at'] and rows[0]['cancel_acknowledged_at']
    assert not (jobs[0]/'eval_results.json').exists()
    from backend.engine.shared_scheduler import shared_leases
    assert shared_leases().list()==[]


def test_version_summary_keeps_manifest_task(common_workspace):
    client, _, _, version, _, _, _=common_workspace
    response=client.get('/api/dataset/versions')
    assert response.status_code==200
    assert next(row for row in response.json()['versions'] if row['id']==version['id'])['task']=='classification'


@pytest.mark.parametrize('common_workspace',['detection','segmentation','anomaly'],indirect=True)
def test_common_cohort_uses_actual_task_evaluator_and_bound_geometry(common_workspace):
    _,_,source,_,_,remote,_=common_workspace
    response=request_common(common_workspace)
    assert response.status_code==200,response.text
    row=response.json()
    assert remote.launches==1 and remote.worker_results[0]['status']=='completed'
    assert row['metrics']['evaluated_split']=='test' and row['metrics']['selection_overlap'] is False
    assert len(row['test_predictions'])==2
    if row['task']=='detection':assert all('object_evidence' in r for r in row['test_predictions'])
    else:assert all('pixel_evidence' in r for r in row['test_predictions'])
    assert all(Path(r['file_path']).is_relative_to(source) for r in row['test_predictions'])


@pytest.mark.parametrize('case',['missing_version','changed_version','changed_source','different_labelset','different_task',
 'unknown_class','validation_only','automatic','overlap','changed_profile','unsupported_device','foreign_job','missing_job'])
def test_unsafe_common_inputs_fail_before_any_upload(common_workspace,case):
    client,project,source,version,profile,remote,jobs=common_workspace
    params={}
    if case=='missing_version':params['evaluation_dataset_version_id']='v_missing'
    elif case=='changed_version':
        path=Path(project['project_dir'])/'dataset/versions'/version['id']/'manifest.json'
        # Locate the actual owned version directory; no original dataset is changed.
        path=next(Path(project['project_dir']).rglob(version['id']+'/manifest.json'))
        path.write_text('{}',encoding='utf-8')
    elif case=='changed_source':Image.new('RGB',(32,32),(99,1,2)).save(source/'test/OK/OK.png')
    elif case=='different_labelset':
        labelset=client.post('/api/project/labelsets',json={'name':'other'}).json()
        assert client.put('/api/project/labelsets/'+labelset['id']+'/activate').status_code==200
    elif case=='different_task':params['source_task']='segmentation'
    elif case=='unknown_class':
        (source/'test/NG').rename(source/'test/foreign')
        params['evaluation_dataset_version_id']=client.post('/api/dataset/versions',json={'name':'unknown class'}).json()['id']
    elif case=='validation_only':
        (source/'test').rename(source/'val')
        params['evaluation_dataset_version_id']=client.post('/api/dataset/versions',json={'name':'validation only'}).json()['id']
    elif case=='automatic':
        for name in ('OK','NG'):(source/'test'/name).rename(source/name)
        (source/'test').rmdir()
        params['evaluation_dataset_version_id']=client.post('/api/dataset/versions',json={'name':'no saved split'}).json()['id']
    elif case=='overlap':
        shutil.copyfile(jobs[0]/'remote_snapshot/data/train/OK/old.png',source/'test/OK/OK.png')
        params['evaluation_dataset_version_id']=client.post('/api/dataset/versions',json={'name':'overlap'}).json()['id']
    elif case=='changed_profile':
        from backend.remote.profiles import get_profile_store
        get_profile_store().save(profile.model_copy(update={'runtime_value':'changed-python'}))
    elif case=='unsupported_device':params['device']='mps'
    elif case=='foreign_job':params['job_id']='job_foreign'
    elif case=='missing_job':params['job_id']=''
    response=request_common(common_workspace,**params)
    assert 400<=response.status_code<600,response.text
    assert remote.uploaded==[] and remote.launches==0
    assert not (jobs[0]/'eval_results.json').exists()


@pytest.mark.parametrize('case',['archive','checkpoint','status'])
def test_worker_refuses_tampered_actual_transferred_inputs(common_workspace,monkeypatch,case):
    _,_,_,_,profile,remote,jobs=common_workspace
    original_launch=remote.launch
    def launch(selected,argv,run_id):
        if case=='archive':
            path=remote.root/'runs'/run_id/'inputs/cohort.tar.gz';path.write_bytes(path.read_bytes()+b'changed')
        elif case=='checkpoint':(remote.root/'runs'/jobs[0].name/'outputs/best_model.pt').write_bytes(b'changed')
        else:
            path=remote.root/'runs'/jobs[0].name/'status.json';body=json.loads(path.read_text(encoding='utf-8'));body['job_id']='job_foreign';write_json(path,body)
        return original_launch(selected,argv,run_id)
    monkeypatch.setattr(remote,'launch',launch)
    response=request_common(common_workspace)
    assert response.status_code==409,response.text
    assert remote.launches==1 and remote.worker_results[0]['status']=='failed'
    assert not (jobs[0]/'eval_results.json').exists()
    from backend.engine.shared_scheduler import shared_leases
    assert shared_leases().list()==[]


@pytest.mark.parametrize('case',['duplicate','truth','matrix','runtime','profile','binding','archive_receipt','version','task','metrics','confidence'])
def test_hash_valid_but_unbound_worker_result_is_never_adopted(common_workspace,monkeypatch,case):
    _,project,_,_,_,remote,jobs=common_workspace
    original_launch=remote.launch
    def launch(selected,argv,run_id):
        handle=original_launch(selected,argv,run_id)
        result_path=remote.root/'runs'/run_id/'outputs/eval_results.json'
        body=json.loads(result_path.read_text(encoding='utf-8'))
        if case=='duplicate':body['test_predictions'][1]=dict(body['test_predictions'][0])
        elif case=='truth':body['test_predictions'][0]['ground_truth']='OK' if body['test_predictions'][0]['ground_truth']=='NG' else 'NG'
        elif case=='matrix':body['confusion_matrix']['matrix']=[[0,0],[0,0]]
        elif case=='runtime':body['runtime_device_identity']['process_id']=0
        elif case=='profile':body['compute_profile_id']='foreign'
        elif case=='binding':body['evaluation_binding_sha256']='0'*64
        elif case=='archive_receipt':body['input_receipt']['archive_sha256']='0'*64
        elif case=='version':body['common_cohort']['dataset_version_id']='v_foreign'
        elif case=='metrics':body['metrics']['accuracy']=123
        elif case=='confidence':body['test_predictions'][0]['confidence']=float('nan')
        else:body['task']='segmentation'
        write_json(result_path,body)
        manifest_path=remote.root/'runs'/run_id/'artifacts.json';manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        row=manifest['artifacts'][0];row.update(size=result_path.stat().st_size,sha256=digest(result_path));write_json(manifest_path,manifest)
        return handle
    monkeypatch.setattr(remote,'launch',launch)
    (jobs[0]/'eval_results.json').write_bytes(b'previous verified cache')
    response=request_common(common_workspace)
    assert response.status_code==502,response.text
    assert (jobs[0]/'eval_results.json').read_bytes()==b'previous verified cache'
    assert list((Path(project['reports_dir'])/'evaluations').glob('evaluation_*.json'))==[]
    client=common_workspace[0]
    operation=client.get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name}).json()['operations'][0]
    assert operation['state']=='failed' and operation['receipt_verified'] is False


def test_cancel_between_actual_samples_acknowledges_and_preserves_previous_cache(common_workspace,monkeypatch):
    client,_,_,_,_,remote,jobs=common_workspace
    from backend.api import routes_evaluation
    actual=routes_evaluation._evaluate_classification
    checks=[]
    def evaluate(checkpoint,meta,data,device,cancel=None):
        class CancelAfterFirst:
            def is_set(self):
                checks.append(True)
                if len(checks)==2:
                    row=client.get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name}).json()['operations'][0]
                    response=client.post('/api/evaluation/remote-operations/'+row['op_id']+'/cancel',json={k:row[k] for k in ('job_id','cohort_sha256','evaluation_binding_sha256')})
                    assert response.status_code==200
                    # Controlled remote delivery, exactly the existing owned sentinel.
                    (remote.root/'runs'/row['op_id']/'cancel').touch()
                return cancel.is_set()
        return actual(checkpoint,meta,data,device,cancel=CancelAfterFirst())
    monkeypatch.setattr(routes_evaluation,'_evaluate_classification',evaluate)
    (jobs[0]/'eval_results.json').write_bytes(b'previous verified cache')
    response=request_common(common_workspace)
    assert response.status_code==409,response.text
    assert len(checks)==2 and remote.worker_results[0]['status']=='aborted'
    row=client.get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name}).json()['operations'][0]
    assert row['cancel_requested_at'] and row['cancel_acknowledged_at'] and row['worker_exit_confirmed']
    assert (jobs[0]/'eval_results.json').read_bytes()==b'previous verified cache'
    assert not (remote.root/'runs'/row['op_id']/'artifacts.json').exists()


def test_same_binding_reconnect_reuses_owned_worker_and_uncertain_lease(common_workspace,monkeypatch):
    _,_,_,_,_,remote,jobs=common_workspace
    original_launch=remote.launch
    def launch(*args):
        handle=original_launch(*args);remote.disconnect=True;return handle
    monkeypatch.setattr(remote,'launch',launch)
    first=request_common(common_workspace)
    assert first.status_code==503 and remote.launches==1
    from backend.engine.shared_scheduler import shared_leases
    assert len(shared_leases().list())==1 and shared_leases().list()[0]['uncertain']
    remote.disconnect=False
    second=request_common(common_workspace)
    assert second.status_code==200,second.text
    assert remote.launches==1 and shared_leases().list()==[]


def test_cancelled_transfer_replay_does_not_create_new_worker(common_workspace,monkeypatch):
    client,_,_,_,_,remote,jobs=common_workspace
    actual_upload=remote.upload;done=[]
    def upload(*args,**kwargs):
        if not done:
            row=client.get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name}).json()['operations'][0]
            done.append(row)
            assert client.post('/api/evaluation/remote-operations/'+row['op_id']+'/cancel',json={k:row[k] for k in ('job_id','cohort_sha256','evaluation_binding_sha256')}).status_code==200
        return actual_upload(*args,**kwargs)
    monkeypatch.setattr(remote,'upload',upload)
    assert request_common(common_workspace).status_code==409
    assert request_common(common_workspace).status_code==409
    assert remote.launches==0


def test_selected_gpu_two_is_bound_before_strict_unavailable_cuda_failure(common_workspace,monkeypatch):
    _,_,_,_,profile,remote,jobs=common_workspace
    from backend.remote.profiles import get_profile_store
    selected=profile.model_copy(update={'gpu_selector':'2','runtime_kind':'docker','runtime_value':'owned-image'})
    get_profile_store().save(selected)
    for output in jobs:
        path=output/'remote_job.json';body=json.loads(path.read_text(encoding='utf-8'));body['profile']=selected.model_dump();write_json(path,body)
    monkeypatch.setattr(remote,'probe',lambda p:{'ready':True,'checks':{'device_inventory':{'devices':[{'selector':'2','uuid':'GPU-owned-two'}]},
        'visible_device_inventory':{'devices':[{'selector':'0','uuid':'GPU-owned-two'}]}}},raising=False)
    monkeypatch.setattr(torch.cuda,'is_available',lambda:False)
    response=request_common(common_workspace,device='cuda:0')
    assert response.status_code==409,response.text
    assert remote.launches==1 and remote.worker_results[0]['status']=='failed'
    spec=json.loads(next((remote.root/'runs').glob('op_*/spec.json')).read_text(encoding='utf-8'))
    assert spec['expected_runtime_gpu_uuid']=='GPU-owned-two' and spec['device']=='cuda:0'
    assert 'unavailable' in response.text


@pytest.mark.parametrize('identity',[{}, {'device':'cuda:0','process_id':1,'gpu_uuid':'GPU-stale'}])
def test_absent_or_wrong_actual_gpu_identity_is_refused(monkeypatch,identity):
    from backend.remote.evaluation_cohort import target_identity
    from backend.engine import runtime_device,runtime_device_identity
    monkeypatch.setattr(runtime_device,'resolve_runtime_device',lambda _:torch.device('cuda:0'))
    monkeypatch.setattr(runtime_device_identity,'runtime_device_identity',lambda _:identity)
    with pytest.raises(ValueError,match='identity|GPU'):
        target_identity({'device':'cuda:0','expected_runtime_gpu_uuid':'GPU-owned-two'})


def _rewrite_metadata(workspace,change):
    _,_,_,_,_,remote,jobs=workspace
    local=jobs[0]/'model_meta.json';meta=json.loads(local.read_text(encoding='utf-8'));change(meta);write_json(local,meta)
    remote_meta=remote.root/'runs'/jobs[0].name/'outputs/model_meta.json';shutil.copyfile(local,remote_meta)
    for manifest_path in (jobs[0]/'remote_artifacts.json',remote.root/'runs'/jobs[0].name/'artifacts.json'):
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        for row in manifest['artifacts']:
            if row['path']=='outputs/model_meta.json':row.update(size=local.stat().st_size,sha256=digest(local))
        write_json(manifest_path,manifest)


def test_checkpoint_and_metadata_class_order_cannot_disagree(common_workspace):
    _rewrite_metadata(common_workspace,lambda meta:meta.update(classes=['NG','OK']))
    response=request_common(common_workspace)
    assert response.status_code==409,response.text
    assert 'class' in response.text.lower()


def test_usage_policy_change_during_worker_execution_prevents_adoption(common_workspace,monkeypatch):
    _,project,source,_,_,remote,jobs=common_workspace
    original_launch=remote.launch
    def launch(*args):
        result=original_launch(*args)
        from backend.engine.annotation_storage import dataset_annotation_dir
        path=dataset_annotation_dir(source,Path(project['annotations_dir']),use_scope=False)/'metadata/workflow.json'
        write_json(path,{'images':{'test/NG/NG.png':{'usage_state':'not_used'}}})
        return result
    monkeypatch.setattr(remote,'launch',launch)
    response=request_common(common_workspace)
    assert response.status_code in (409,502),response.text
    assert not (jobs[0]/'eval_results.json').exists()


@pytest.mark.parametrize('common_workspace',['segmentation'],indirect=True)
def test_saved_mask_class_names_must_match_completed_model(common_workspace):
    client,project,source,_,_,remote,jobs=common_workspace
    from backend.engine.annotation_storage import dataset_annotation_dir
    path=dataset_annotation_dir(source/'images/test',Path(project['annotations_dir']),use_scope=False)/'defect.json'
    write_json(path,{'annotations':[],'mask_classes':[{'id':1,'name':'foreign'}]})
    version=client.post('/api/dataset/versions',json={'name':'foreign mask class'}).json()
    response=request_common(common_workspace,evaluation_dataset_version_id=version['id'])
    assert response.status_code==422,response.text
    assert remote.uploaded==[] and remote.launches==0


def test_upload_transport_cancellation_exception_is_recorded_aborted(common_workspace,monkeypatch):
    client,_,_,_,_,remote,jobs=common_workspace
    from backend.remote.ssh_transport import SSHTransferCancelled
    def upload(selected,local,relative,*,cancel=None):
        row=client.get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name}).json()['operations'][0]
        assert client.post('/api/evaluation/remote-operations/'+row['op_id']+'/cancel',json={k:row[k] for k in ('job_id','cohort_sha256','evaluation_binding_sha256')}).status_code==200
        assert cancel.is_set()
        raise SSHTransferCancelled('upload cancelled')
    monkeypatch.setattr(remote,'upload',upload)
    response=request_common(common_workspace)
    assert response.status_code==409,response.text
    row=client.get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name}).json()['operations'][0]
    assert row['state']=='aborted' and row['cancel_acknowledged_at'] and row['worker_exit_confirmed']
    assert remote.launches==0


@pytest.mark.parametrize('common_workspace',['detection'],indirect=True)
def test_hash_valid_detection_result_with_same_class_wrong_truth_box_is_refused(common_workspace,monkeypatch):
    _,_,_,_,_,remote,jobs=common_workspace
    original_launch=remote.launch
    def launch(selected,argv,run_id):
        handle=original_launch(selected,argv,run_id)
        path=remote.root/'runs'/run_id/'outputs/eval_results.json';result=json.loads(path.read_text(encoding='utf-8'))
        row=next(r for r in result['test_predictions'] if r['object_evidence']['truth'])
        row['object_evidence']['truth'][0]['box']=[0,0,1,1]
        write_json(path,result)
        manifest_path=path.parent.parent/'artifacts.json';manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['artifacts'][0].update(size=path.stat().st_size,sha256=digest(path));write_json(manifest_path,manifest)
        return handle
    monkeypatch.setattr(remote,'launch',launch)
    response=request_common(common_workspace)
    assert response.status_code==502,response.text
    assert not (jobs[0]/'eval_results.json').exists()


@pytest.mark.parametrize('common_workspace',['segmentation','anomaly'],indirect=True)
def test_missing_frozen_truth_mask_is_refused_before_upload(common_workspace):
    client,_,source,_,_,remote,_=common_workspace
    for path in list((source/'masks').rglob('*.png'))+list((source/'ground_truth').rglob('*.png')):path.unlink()
    version=client.post('/api/dataset/versions',json={'name':'missing truth'}).json()
    response=request_common(common_workspace,evaluation_dataset_version_id=version['id'])
    assert response.status_code==422,response.text
    assert remote.uploaded==[] and remote.launches==0


@pytest.mark.parametrize('case',['symlink','count_bound','byte_bound','sharing','distributed','busy'])
def test_common_safety_bounds_and_reservation_refuse_before_upload(common_workspace,monkeypatch,case):
    _,_,source,_,profile,remote,jobs=common_workspace
    if case=='symlink':
        path=source/'test/OK/OK.png';target=source/'outside.png';path.rename(target);path.symlink_to(target)
    elif case=='count_bound':
        from backend.remote import evaluation_cohort
        monkeypatch.setattr(evaluation_cohort,'MAX_IMAGES',1)
    elif case=='byte_bound':
        from backend.remote import evaluation_cohort
        # The production copy helper's default is bound at definition time; pass
        # a monkeypatch to the actual size policy without changing a mock image.
        monkeypatch.setattr(evaluation_cohort,'MAX_EXPANDED_BYTES',1)
    elif case in ('sharing','distributed'):
        from backend.remote.profiles import get_profile_store
        selected=profile.model_copy(update={'allow_sharing':True,'memory_budget_mb':256,'gpu_selector':'2'} if case=='sharing' else {'distributed_processes':2,'gpu_selector':'2,3'})
        get_profile_store().save(selected)
        for output in jobs:
            path=output/'remote_job.json';body=json.loads(path.read_text(encoding='utf-8'));body['profile']=selected.model_dump();write_json(path,body)
    else:
        from backend.engine.shared_scheduler import shared_leases
        assert shared_leases().acquire('job_other','ssh:owned-host:22','all',remote=True)
    response=request_common(common_workspace)
    assert response.status_code in (409,422),response.text
    assert remote.uploaded==[] and remote.launches==0


@pytest.mark.parametrize('case',['link','traversal','expanded'])
def test_cohort_archive_preflight_refuses_unsafe_members_before_extraction(tmp_path,case):
    import tarfile
    from backend.remote.evaluation_cohort import extract_cohort,digest as value_digest,MAX_EXPANDED_BYTES
    run=tmp_path/'op';archive=run/'inputs/cohort.tar.gz';archive.parent.mkdir(parents=True)
    with tarfile.open(archive,'w:gz') as writer:
        member=tarfile.TarInfo('../escaped' if case=='traversal' else 'data/image.png')
        if case=='link':member.type=tarfile.SYMTYPE;member.linkname='/outside'
        elif case=='expanded':member.size=MAX_EXPANDED_BYTES+1
        if case!='expanded':writer.addfile(member)
    if case=='expanded':
        import gzip
        archive.write_bytes(gzip.compress(member.tobuf()+b'\0'*1024))
    cohort={'task':'classification','split':'test','image_count':1,'archive_path':'inputs/cohort.tar.gz',
            'archive_size':archive.stat().st_size,'archive_sha256':digest(archive),'expanded_bytes':1}
    spec={'common_cohort_contract':1,'evaluation_cohort':cohort,'task':'classification','device':'cpu'}
    spec['evaluation_binding_sha256']=value_digest(spec)
    with pytest.raises(ValueError):extract_cohort(run,spec)
    assert not (run/'evaluation_input').exists() and not (tmp_path/'escaped').exists()


def test_terminal_and_foreign_operation_cancellation_cannot_touch_worker(common_workspace,monkeypatch):
    client,_,_,_,_,remote,jobs=common_workspace
    assert request_common(common_workspace).status_code==200
    row=client.get('/api/evaluation/remote-operations',params={'job_id':jobs[0].name}).json()['operations'][0]
    body={k:row[k] for k in ('job_id','cohort_sha256','evaluation_binding_sha256')}
    assert client.post('/api/evaluation/remote-operations/'+row['op_id']+'/cancel',json=body).status_code==409
    assert client.post('/api/evaluation/remote-operations/'+row['op_id']+'/cancel',json={**body,'job_id':jobs[1].name}).status_code==404
    assert not (remote.root/'runs'/row['op_id']/'cancel').exists()



def test_completed_status_must_match_uploaded_common_spec(common_workspace,monkeypatch):
    _,_,_,_,_,remote,jobs=common_workspace
    actual=remote.launch
    def launch(selected,argv,run_id):
        handle=actual(selected,argv,run_id)
        path=remote.root/'runs'/run_id/'status.json';row=json.loads(path.read_text(encoding='utf-8'));row['spec_sha256']='0'*64;write_json(path,row)
        return handle
    monkeypatch.setattr(remote,'launch',launch)
    response=request_common(common_workspace)
    assert response.status_code==502,response.text
    assert not (jobs[0]/'eval_results.json').exists()

@pytest.mark.parametrize('runtime_uuid',['680335ce-4e6c-c68f-e3ee-7135586d4a04','GPU-680335ce-4e6c-c68f-e3ee-7135586d4a04'])
def test_actual_pytorch_and_nvml_uuid_forms_bind_same_physical_gpu(monkeypatch,runtime_uuid):
    from backend.remote.evaluation_cohort import target_identity
    from backend.engine import runtime_device,runtime_device_identity
    monkeypatch.setattr(runtime_device,'resolve_runtime_device',lambda _:torch.device('cuda:0'))
    identity={'device':'cuda:0','process_id':1,'gpu_uuid':runtime_uuid}
    monkeypatch.setattr(runtime_device_identity,'runtime_device_identity',lambda _:identity)
    assert target_identity({'device':'cuda:0','expected_runtime_gpu_uuid':'GPU-680335ce-4e6c-c68f-e3ee-7135586d4a04'})[1]==identity
    with pytest.raises(ValueError,match='GPU'):
        target_identity({'device':'cuda:0','expected_runtime_gpu_uuid':'GPU-680335ce-4e6c-c68f-e3ee-7135586d4a05'})
    with pytest.raises(ValueError,match='GPU'):
        target_identity({'device':'cuda:0','expected_runtime_gpu_uuid':'MIG-680335ce-4e6c-c68f-e3ee-7135586d4a04'})

@pytest.mark.parametrize('actual,expected',[(1,2),(['GPU'],['GPU']),({'uuid':'GPU'},{'uuid':'GPU'}),('', ''),(None,None)])
def test_malformed_gpu_identity_never_matches(actual,expected):
    from backend.remote.evaluation_cohort import _gpu_uuid_matches
    assert not _gpu_uuid_matches(actual,expected)
