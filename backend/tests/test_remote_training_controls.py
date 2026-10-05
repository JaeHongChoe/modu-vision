"""Workbench controls cross the compute API, durable queue and owned worker."""
import json
from pathlib import Path
import threading
import time

import pytest


@pytest.mark.parametrize('family', ['rotation','ocr','defect-gan','enhancement','rotated-detection'])
def test_remote_workbench_controls_reach_owned_launch(tmp_path, monkeypatch, family):
    from backend.tests.test_specialist_runtime_limits import prepared_client
    from backend.api import routes_training
    from backend.remote.ssh_transport import SSHTransport
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'user'))
    client, project, source, dataset = prepared_client(tmp_path, family)
    profile = {'id':'controlled','name':'Controlled CPU','ssh_target':'fixture-only','ssh_port':22,
               'remote_root':'/srv/owned-fixture','runtime_kind':'python','runtime_value':'python3'}
    assert client.post('/api/compute/profiles', json=profile).status_code == 201
    monkeypatch.setattr(SSHTransport, 'probe', lambda *a: {'ready':True,'runtime_ready':True,
        'checks':{'cuda_device_count':0,'device_inventory':{'devices':[]}}})
    captured = []
    def capture(**kwargs):
        captured.append(kwargs)
        return routes_training.JobRecord(job_id=kwargs['job_id'],task=kwargs['task'],preset='fast',
            dataset_path=kwargs['dataset_path'],output_dir=kwargs['output_dir'],status='queued',
            remote_profile_id='controlled',launch_spec=kwargs['launch_spec'],dataset_binding=kwargs['dataset_binding'])
    monkeypatch.setattr(routes_training.training_job_manager, 'start_remote_job', capture)
    response = client.post('/api/compute/jobs', json={'task':family.replace('-','_'),
        'dataset_path':str(source),'family_dataset_path':dataset,'compute_profile_id':'controlled',
        'device':'cpu','queue':False,'priority':3,'max_runtime_s':.3,'config_overrides':{'epochs':1}})
    assert response.status_code == 202, response.text
    assert len(captured) == 1
    launch = captured[0]['launch_spec']
    assert captured[0]['queue_when_busy'] is False
    assert launch['priority'] == 3 and launch['max_runtime_s'] == .3
    assert not {'queue','priority','max_runtime_s'} & launch['config_overrides'].keys()
    assert launch['family_dataset_path'] == dataset
    assert Path(captured[0]['output_dir']).is_relative_to(Path(project['models_dir']))
    assert response.json()['budget']=={'max_runtime_s':.3} and response.json()['priority']==3


def test_remote_admission_race_refuses_instead_of_silently_queueing(tmp_path,monkeypatch):
    from backend.tests.test_compute_queue import _profile
    from backend.api.routes_training import TrainingJobManager
    from fastapi import HTTPException
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    manager=TrainingJobManager();profile=_profile(tmp_path,'owned');called=[]
    monkeypatch.setattr(manager,'_remote_slot_busy',lambda _:False)
    monkeypatch.setattr(manager._leases,'acquire',lambda *a,**k:False)
    with pytest.raises(HTTPException) as refused:
        manager.start_remote_job(job_id='job_race',task='rotation',dataset_path=str(tmp_path/'data'),
            output_dir=str(tmp_path/'race'),remote_profile_id=profile.id,profile=profile,
            remote_runner=lambda _:called.append(True),launch_spec={'preparation':'none'},queue_when_busy=False)
    assert refused.value.status_code==409 and not called and not manager._remote_queue
    assert manager.get_job('job_race') is None and manager._active_job_id is None
    assert json.loads((tmp_path/'race'/'remote_job.json').read_text())['state']=='failed'


def test_remote_queued_recovery_restores_priority_before_any_launch(tmp_path,monkeypatch):
    from backend.tests.test_compute_queue import _profile
    from backend.api.routes_training import JobRecord
    from backend.remote import coordinator
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    profile=_profile(tmp_path,'owned')
    for name, priority in [('older_low',-2),('newer_high',4)]:
        record=JobRecord(job_id='job_'+name,task='rotation',preset='fast',dataset_path=str(tmp_path/'data'),
            output_dir=str(tmp_path/name),status='queued',remote_profile_id=profile.id)
        coordinator.persist_queued_remote_job(record,profile,{'preparation':'none','priority':priority,'max_runtime_s':.3})
    captured=[]
    class Manager:
        def get_job(self, identifier): return None
        def start_remote_job(self, **kwargs): captured.append(kwargs)
    coordinator.recover_remote_jobs(Manager())
    assert [row['job_id'] for row in captured]==['job_newer_high','job_older_low']
    assert all(row['launch_spec']['max_runtime_s']==.3 for row in captured)


def test_actual_cpu_worker_budget_survives_coordinator_spec_transfer_and_observation(tmp_path,monkeypatch):
    import hashlib
    import torch
    from backend.tests.test_rotation import rotation_data
    from backend.engine.rotation import write_rotation_manifest
    from backend.tests.test_remote_coordinator import FakeRemote
    from backend.api.routes_training import JobRecord,_job_observation
    from backend.remote.coordinator import run_remote_training
    from backend.remote.profiles import ComputeProfile
    from backend.remote.worker import run_train
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    source=tmp_path/'source';write_rotation_manifest(source,rotation_data(source))
    originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
    profile=ComputeProfile(id='controlled',name='Controlled CPU',ssh_target='fixture-only',ssh_port=22,
        remote_root=str(tmp_path/'server'),runtime_kind='python',runtime_value='python3')
    record=JobRecord(job_id='job_cpu_budget',task='rotation',preset='fast',dataset_path=str(source),
        output_dir=str(tmp_path/'output'),status='running',remote_profile_id=profile.id,
        source_dataset_path=str(source),launch_spec={'priority':3,'max_runtime_s':.2})
    workers=[]
    class ActualCPU(FakeRemote):
        def launch(self, profile, argv, run_id):
            workers.append(run_train(self.root/'runs'/run_id/'spec.json'))
            return 'owned-in-process-cpu-worker'
    torch.set_num_threads(1)
    result=run_remote_training(record,profile,transport=ActualCPU(Path(profile.remote_root)),device='cpu',
        config_overrides={'epochs':500,'image_size':32,'batch_size':2,'width':8})
    assert result['status']=='aborted' and result['stop_reason']=='time_limit' and result['worker_exit_confirmed'], result
    assert len(workers)==1 and workers[0]['current_epoch']>0
    assert workers[0]['budget']=={'max_runtime_s':.2}
    assert not Path(record.output_dir).joinpath('best_model.pt').exists()
    record.status='aborted';record.result=result;record.error={'message':result['error']}
    observation=_job_observation(record,set())
    assert observation['cause']=='time_limit' and observation['cancel']['complete'] is True
    journal=json.loads(Path(record.output_dir).joinpath('remote_job.json').read_text())
    assert journal['stop_reason']=='time_limit' and journal['worker_exit_confirmed']
    assert originals=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}


def test_remote_priority_claims_waiting_jobs_without_preempting_current_owner(tmp_path, monkeypatch):
    from backend.tests.test_compute_queue import _profile
    from backend.api.routes_training import TrainingJobManager
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'user'))
    manager = TrainingJobManager(); release = threading.Event(); started = threading.Event(); order=[]
    profile = _profile(tmp_path,'owned')
    def run(name):
        def runner(record):
            order.append(name)
            if name == 'active': started.set(); assert release.wait(5)
            return {'status':'aborted'}
        return runner
    def start(name, priority=0, **kwargs):
        return manager.start_remote_job(job_id='job_'+name,task='rotation',dataset_path=str(tmp_path/'data'),
            output_dir=str(tmp_path/name),remote_profile_id=profile.id,profile=profile,remote_runner=run(name),
            launch_spec={'preparation':'none','config_overrides':{},'priority':priority,'max_runtime_s':.3},**kwargs)
    active = start('active')
    try:
        assert started.wait(2)
        low = start('low',-2); high = start('high',4)
        for record in (low,high):
            journal=json.loads((Path(record.output_dir)/'remote_job.json').read_text())
            assert journal['launch_spec']['max_runtime_s'] == .3
        release.set()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and (low.thread is None or low.thread.is_alive()): time.sleep(.01)
        assert order == ['active','high','low']
    finally:
        release.set(); manager._shutdown.set()
        for record in manager.list_jobs():
            if record.thread: record.thread.join(5)


def test_remote_immediate_refusal_creates_no_owned_launch_or_queue(tmp_path, monkeypatch):
    from backend.tests.test_compute_queue import _profile
    from backend.api.routes_training import TrainingJobManager
    from fastapi import HTTPException
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'user'))
    manager=TrainingJobManager();profile=_profile(tmp_path,'owned')
    assert manager._leases.acquire('owned-control',manager._lease_host(profile),'0',remote=True)
    called=[]
    try:
        with pytest.raises(HTTPException) as refused:
            manager.start_remote_job(job_id='job_refused',task='rotation',dataset_path=str(tmp_path/'data'),
                output_dir=str(tmp_path/'refused'),remote_profile_id=profile.id,profile=profile,
                remote_runner=lambda _:called.append(True),launch_spec={'preparation':'none'},queue_when_busy=False)
        assert refused.value.status_code == 409
        assert not called and manager.get_job('job_refused') is None and not manager._remote_queue
        assert not (tmp_path/'refused').exists()
    finally: manager._leases.release('owned-control',terminal=True);manager._shutdown.set()


def test_remote_worker_budget_cancels_without_callbacks_and_never_publishes_artifacts(tmp_path):
    from backend.tests.test_remote_worker import _spec, StubTrainer
    from backend.remote.worker import run_train
    run, spec, _ = _spec(tmp_path)
    payload=json.loads(spec.read_text());payload['max_runtime_s']=.06;spec.write_text(json.dumps(payload))
    acknowledged=[]
    class OwnedTrainer(StubTrainer):
        def train(self, job_id):
            acknowledged.append(self.aborted.wait(.4))
            self.output_dir.joinpath('best_model.pt').write_bytes(b'partial')
            self.output_dir.joinpath('model_meta.json').write_text('{"task":"classification"}')
            return {'status':'completed','best_metric':1}
    result=run_train(spec,trainer_factory=OwnedTrainer)
    assert result['status'] == 'aborted' and result['stop_reason'] == 'time_limit'
    assert result['budget']=={'max_runtime_s':.06} and result['runtime_started_at']>0
    assert acknowledged == [True]
    assert result['cancel_acknowledged_at']>=result['runtime_started_at']
    assert not (run/'artifacts.json').exists()
    assert not any(t.name=='FamilyRuntimeBudget' and t.is_alive() for t in threading.enumerate())


def test_remote_runtime_budget_includes_final_artifact_hashing(tmp_path,monkeypatch):
    from backend.tests.test_remote_worker import _spec, StubTrainer
    from backend.remote import worker
    run,spec,_=_spec(tmp_path);payload=json.loads(spec.read_text());payload['max_runtime_s']=.06;spec.write_text(json.dumps(payload))
    original=worker._artifact_manifest
    def slow(*args): time.sleep(.1);return original(*args)
    monkeypatch.setattr(worker,'_artifact_manifest',slow)
    result=worker.run_train(spec,trainer_factory=StubTrainer)
    assert result['status']=='aborted' and result['stop_reason']=='time_limit'
    assert not (run/'artifacts.json').exists()


def test_remote_budget_journal_outage_still_reaches_owned_trainer(tmp_path,monkeypatch):
    from backend.tests.test_remote_worker import _spec,StubTrainer
    from backend.remote import worker
    run,spec,_=_spec(tmp_path);payload=json.loads(spec.read_text());payload['max_runtime_s']=.06;spec.write_text(json.dumps(payload))
    original=worker._StatusWriter.update;observed=[]
    def unavailable(writer,**changes):
        if 'cancel_requested_at' in changes:raise OSError('Controlled cancellation journal outage')
        return original(writer,**changes)
    monkeypatch.setattr(worker._StatusWriter,'update',unavailable)
    class Owned(StubTrainer):
        def train(self,job_id):
            observed.append(self.aborted.wait(.4))
            return {'status':'completed','best_metric':1}
    result=worker.run_train(spec,trainer_factory=Owned)
    assert observed==[True] and result['status']=='failed'
    assert result['error'].startswith('OSError:') and not (run/'artifacts.json').exists()


@pytest.mark.parametrize('value',[True,'1',0,-1,604801])
def test_invalid_remote_budget_is_refused_before_probe_or_worker(tmp_path,monkeypatch,value):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    from backend.remote.ssh_transport import SSHTransport
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    called=[];monkeypatch.setattr(SSHTransport,'probe',lambda *a:called.append(True))
    app=create_app(project_dir=str(tmp_path/'projects'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    response=client.post('/api/compute/jobs',json={'task':'rotation','dataset_path':'owned-fixture',
        'compute_profile_id':'fixture','max_runtime_s':value})
    assert response.status_code==422 and not called
