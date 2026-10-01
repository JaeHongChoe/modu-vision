"""Product training contracts, with no trainer submission or external downloads."""
import pytest
from pydantic import ValidationError
from backend.api.routes_automated_training import StartRequest
from backend.api.routes_training import TrainingConfigOverrides


def test_budget_relationship_is_rejected_before_a_job_is_created():
    with pytest.raises(ValidationError, match='budget'):
        StartRequest(dataset_path='/fixture/source',epochs_per_trial=9,budget={'max_trials':4,'max_total_epochs':8,'max_seconds':600})


def test_anomaly_purpose_survives_validated_training_options():
    assert TrainingConfigOverrides(anomaly_mode='segmentation').model_dump(exclude_none=True)['anomaly_mode']=='segmentation'
    with pytest.raises(ValidationError):
        TrainingConfigOverrides(anomaly_mode='invented')


def test_local_readiness_does_not_download_and_missing_weights_stay_unready(tmp_path,monkeypatch):
    import importlib.util
    assert importlib.util.find_spec('backend.engine.training_workspace') is not None, 'Readiness workspace is absent'
    from backend.engine import training_workspace
    monkeypatch.setattr(training_workspace,'_package_available',lambda name:True)
    monkeypatch.setattr(training_workspace,'_cached_weights',lambda model:None)
    result=training_workspace.model_readiness('classification','dinov3_vits16','cpu')
    assert result['ready'] is False
    assert result['weights']['state']=='missing'
    result=training_workspace.model_readiness('classification','dinov3_vits16','cpu',str(tmp_path/'absent.pt'))
    assert result['weights']['state']=='missing'


def test_task_inventory_reopens_disk_job_and_filters_source_and_labelset(tmp_path):
    import importlib.util,json
    assert importlib.util.find_spec('backend.engine.training_workspace') is not None, 'Task workspace is absent'
    from backend.engine.training_workspace import persisted_task_rows
    root=tmp_path/'models'
    for number,source,labelset in [(1,'/source','chosen'),(2,'/other','chosen'),(3,'/source','other')]:
        identifier=f'{number:032x}';folder=root/'ocr'/identifier;folder.mkdir(parents=True)
        (folder/'job.json').write_text(json.dumps({'job_id':identifier,'task':'ocr','status':'completed','created_at':1,'source_dataset_path':source,'training_provenance':{'labelset_id':labelset}}))
    rows=persisted_task_rows(root,'/source','chosen')
    assert [row['job_id'] for row in rows]==['00000000000000000000000000000001']
    assert rows[0]['kind']=='ocr'


def test_weight_import_preserves_source_binds_hash_and_reopens_without_download(tmp_path):
    import hashlib
    from backend.engine import training_workspace
    assert hasattr(training_workspace,'import_pretrained_weight'),'No project weight import action'
    project={'project_dir':str(tmp_path/'project'),'models_dir':str(tmp_path/'project'/'models')}
    source=tmp_path/'official.safetensors';source.write_bytes(b'fixture official bytes')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    saved=training_workspace.import_pretrained_weight(project,'classification','dinov3_vits16',str(source),digest)
    assert source.read_bytes()==b'fixture official bytes'
    assert saved['sha256']==digest
    assert training_workspace.imported_weight(project,'classification','dinov3_vits16')==saved['pretrained_checkpoint']
    from pathlib import Path
    Path(saved['pretrained_checkpoint']).write_bytes(b'tampered')
    assert training_workspace.imported_weight(project,'classification','dinov3_vits16') is None


def test_memory_budget_stops_before_candidate_and_does_not_claim_a_winner(tmp_path):
    from backend.engine.automated_trials import run_automated_training
    from PIL import Image
    source=tmp_path/'source'
    for split in ('train','val'):
        for label in ('normal','defect'):
            folder=source/split/label;folder.mkdir(parents=True);Image.new('RGB',(8,8),'white').save(folder/'a.png')
    try:
        result=run_automated_training(task='classification',dataset_path=source,models_dir=tmp_path/'models',device='cpu',
            epochs_per_trial=1,budget={'max_trials':1,'max_total_epochs':1,'max_seconds':60,'max_memory_mb':1})
    except ValueError:result={'stop_reason':'memory_budget_unsupported'}
    assert result['stop_reason']=='memory_budget'
    assert result['winner'] is None and result['trials']==[]
    assert result['memory_scope']=='backend_process_rss'


def test_workspace_same_job_cancel_ack_and_backend_reopen(tmp_path,monkeypatch):
    import threading,time,hashlib
    from fastapi.testclient import TestClient
    from PIL import Image
    from backend.main import create_app
    from backend.api import routes_ocr
    entered=threading.Event();release=threading.Event()
    def controlled(dataset,output,*,cancel_event,on_progress,**options):
        entered.set();on_progress({'epoch':1,'batch':1,'batches':2,'loss':.5})
        assert cancel_event.wait(5)
        assert release.wait(5)
        raise InterruptedError('cancel acknowledged')
    monkeypatch.setattr(routes_ocr,'train_ocr',controlled)
    projects=tmp_path/'projects';app=create_app(project_dir=str(projects));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':'Task fixture'}).json()
    source=tmp_path/'source';source.mkdir()
    for name,color in [('train.png','white'),('val.png','black')]:Image.new('RGB',(32,32),color).save(source/name)
    before={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    preparation=client.post('/api/ocr/prepare',json={'source_dataset_path':str(source),'samples':[{'image':'train.png','text':'검사','split':'train'},{'image':'val.png','text':'검사','split':'val'}]})
    assert preparation.status_code==200,preparation.text
    prepared=preparation.json()
    started=client.post('/api/ocr/train',json={'dataset_path':prepared['dataset_path'],'background':True,'epochs':2})
    assert started.status_code==202,started.text
    identifier=started.json()['job_id'];assert entered.wait(3)
    try:
        assert client.post(f'/api/ocr/jobs/{identifier}/cancel').json()['status']=='stopping'
        rows=client.get('/api/training-workspace/tasks');assert rows.status_code==200,rows.text
        job=next(row for row in rows.json()['tasks'] if row['job_id']==identifier)
        assert job['status']=='stopping' and job['source_dataset_path']==str(source)
    finally:release.set()
    for _ in range(100):
        if client.get(f'/api/ocr/jobs/{identifier}').json()['status']=='stopped':break
        time.sleep(.01)
    other=create_app(project_dir=str(projects));reopened=TestClient(other,headers={'X-Vision-Token':other.state.api_token})
    rows=reopened.get('/api/training-workspace/tasks').json()['tasks']
    assert next(row for row in rows if row['job_id']==identifier)['status']=='stopped'
    reopened.post('/api/project/create',json={'name':'Other fixture'})
    assert all(row.get('job_id')!=identifier for row in reopened.get('/api/training-workspace/tasks').json()['tasks'])
    assert before=={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}


def test_regular_anomaly_start_rejects_invalid_purpose_before_preparing_data(tmp_path):
    from backend.api.routes_training import TrainingStartRequest,start_training
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        start_training(TrainingStartRequest(task='anomaly',dataset_path=str(tmp_path/'absent'),config_overrides={'anomaly_mode':'invented'}))
    assert failure.value.status_code==422
    assert 'purpose' in str(failure.value.detail)


def test_search_progress_has_live_elapsed_memory_and_budget_before_completion(tmp_path,monkeypatch):
    from backend.engine import automated_trials as trials
    source=tmp_path/'source';source.mkdir();(source/'input.txt').write_text('immutable input')
    records=[]
    def runner(context):
        context.on_progress({'epoch':1,'batch':1})
        raise InterruptedError('controlled stop')
    monkeypatch.setitem(trials._RUNNERS,'progress_fixture',trials.TaskRunner(runner,('fixture',),search_defaults={'architectures':['fixture']}))
    trials.run_automated_training(task='progress_fixture',dataset_path=source,models_dir=tmp_path/'models',mode='quick',epochs_per_trial=1,on_progress=records.append)
    active=next(row for row in records if row['status']=='running' and row['trials'] and row['trials'][0].get('progress'))
    assert active['duration_seconds']>=0
    assert active['memory_used_mb']>0
    assert active['epochs_consumed']==0 and active['budget']['max_total_epochs']==8
