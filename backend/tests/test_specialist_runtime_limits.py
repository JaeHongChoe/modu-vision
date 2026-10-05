"""Native family budgets cancel only their owned worker and never publish partial models."""
import hashlib
import json
from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from backend.main import create_app
from backend.api import routes_rotation, routes_ocr, routes_defect_gan, routes_enhancement, routes_rotated_detection
from backend.engine.rotated_detection import RotatedTrainingCancelled

FAMILIES = [
    ('rotation', routes_rotation, 'train_rotation'),
    ('ocr', routes_ocr, 'train_ocr'),
    ('defect-gan', routes_defect_gan, 'train_defect_gan'),
    ('enhancement', routes_enhancement, 'train_enhancement'),
    ('rotated-detection', routes_rotated_detection, 'train_rotated_detector'),
]


@pytest.fixture(autouse=True)
def private_user_data(tmp_path,monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))


def prepared_client(tmp_path, family):
    app = create_app(project_dir=str(tmp_path/'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name':'Budget family'}).json()
    source = tmp_path/'source';source.mkdir()
    rows=[]
    for i, split in enumerate(('train','train','val','test')):
        name=f'{i}.png';Image.new('RGB',(32,32),(i*50+20,20,100)).save(source/name)
        row={'image':name,'split':split}
        if family=='rotation': row['correction_deg']=i*30
        elif family=='ocr': row['text']='A'
        elif family=='defect-gan': row['bbox']=[0,0,32,32]
        elif family=='rotated-detection':row.update(label='part',box={'cx':16,'cy':16,'width':12,'height':8,'angle_deg':0})
        rows.append(row)
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    data={'source_dataset_path':str(source)}
    if family!='enhancement':data['samples']=rows
    response=client.post(f'/api/{family}/prepare',json=data)
    assert response.status_code==200,response.text
    return client, project, source, response.json()['dataset_path']


def await_terminal(client,family,identifier):
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        response=client.get(f'/api/{family}/jobs/{identifier}');assert response.status_code==200,response.text
        record=response.json()
        if record['status'] not in {'queued','running','stopping'}:return record
        time.sleep(.01)
    raise AssertionError('Owned family did not acknowledge its budget cancellation')


@pytest.mark.parametrize('family,module,method',FAMILIES)
def test_family_budget_is_saved_before_runner_and_cancels_without_progress(tmp_path,monkeypatch,family,module,method):
    client,project,source,dataset=prepared_client(tmp_path,family)
    originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.png')}
    entered=threading.Event();observed=[]
    def controlled(dataset_path,output,*,cancel_event,**kwargs):
        output=Path(output)
        journal=output/('job_state.json' if family=='rotated-detection' else 'job.json')
        row=json.loads(journal.read_text());observed.append(row)
        assert row['budget']['max_runtime_s']==.12
        assert row['runtime_started_at']>0
        entered.set()
        assert cancel_event.wait(3),'Runtime budget must reach the owned trainer without a progress callback'
        output.mkdir(parents=True,exist_ok=True)
        (output/'best_model.pt').write_bytes(b'incomplete owned synthetic checkpoint')
        if family=='rotated-detection':raise RotatedTrainingCancelled()
        raise InterruptedError('Controlled trainer acknowledged cancellation')
    monkeypatch.setattr(module,method,controlled)
    body={'dataset_path':dataset,'epochs':20,'max_runtime_s':.12}
    if family!='rotated-detection':body['background']=True
    response=client.post(f'/api/{family}/train',json=body)
    assert response.status_code==(200 if family=='rotated-detection' else 202),response.text
    identifier=response.json()['job_id'];assert entered.wait(3)
    terminal=await_terminal(client,family,identifier)
    assert terminal['status']==('aborted' if family=='rotated-detection' else 'stopped'),terminal
    assert terminal['stop_reason']=='time_limit' and 'runtime limit' in terminal['error'].lower()
    root=Path(project['models_dir'])/family.replace('-','_')/identifier
    assert not (root/'best_model.pt').exists() and not (root/'job_receipt.json').exists()
    assert client.get(f'/api/{family}/models').json()['models']==[]
    client.post('/api/project/open',json={'project_dir':project['project_dir']})
    reopened=client.get(f'/api/{family}/jobs/{identifier}').json()
    assert reopened['budget']=={'max_runtime_s':.12} and reopened['stop_reason']=='time_limit'
    assert originals=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.png')}
    assert len(observed)==1


@pytest.mark.parametrize('family,module,method',FAMILIES)
@pytest.mark.parametrize('value',[0,-1,604801,'NaN',True,'1'])
def test_invalid_family_budget_refuses_before_runner(tmp_path,monkeypatch,family,module,method,value):
    client,_,_,dataset=prepared_client(tmp_path,family);called=[]
    monkeypatch.setattr(module,method,lambda *a,**k:called.append(True))
    body={'dataset_path':dataset,'epochs':1,'max_runtime_s':value}
    if family!='rotated-detection':body['background']=True
    response=client.post(f'/api/{family}/train',json=body)
    assert response.status_code==422,response.text
    assert not called


@pytest.mark.parametrize('value',[True,'1'])
def test_all_training_budgets_require_json_numbers_before_admission(value):
    from pydantic import ValidationError
    from backend.api.routes_training import TrainingStartRequest
    from backend.api.routes_patch_classification import TrainRequest as PatchRequest
    models=[TrainingStartRequest,PatchRequest,routes_rotation.TrainRequest,routes_ocr.OCRTrainRequest,
            routes_defect_gan.GANTrainRequest,routes_enhancement.Train,routes_rotated_detection.TrainRequest]
    for model in models:
        with pytest.raises(ValidationError,match='max_runtime_s'):
            model(dataset_path='private-owned-fixture',max_runtime_s=value)
        for valid in (None,1,.3):
            assert model(dataset_path='private-owned-fixture',max_runtime_s=valid).max_runtime_s==valid


def test_actual_cpu_rotation_budget_runs_optimizer_and_leaves_no_reservation(tmp_path):
    client,project,source,dataset=prepared_client(tmp_path,'rotation')
    response=client.post('/api/rotation/train',json={'dataset_path':dataset,'epochs':500,'batch_size':1,'image_size':32,'width':8,'background':True,'device':'cpu','max_runtime_s':.3})
    assert response.status_code==202,response.text
    row=await_terminal(client,'rotation',response.json()['job_id'])
    assert row['status']=='stopped' and row['stop_reason']=='time_limit',row
    assert row['batch']>0,'The real optimizer must run before the limit expires'
    from backend.engine.shared_scheduler import shared_leases
    with shared_leases().connect() as db:
        assert not db.execute('SELECT * FROM leases WHERE job_id=?',(row['job_id'],)).fetchall()
    assert not Path(project['models_dir']).joinpath('rotation',row['job_id'],'best_model.pt').exists()


def test_budget_starts_only_after_admission_and_closes_watchdog(tmp_path):
    from backend.engine.runtime_budget import RuntimeBudget
    from backend.engine.shared_scheduler import ResourceLeases,compute_lease_scope
    event=threading.Event();starts=[];reasons=[]
    leases=ResourceLeases(tmp_path/'user'/'leases.sqlite')
    budget=RuntimeBudget(.1,event,starts.append,lambda:reasons.append('time_limit'))
    time.sleep(.15) # constructed/admission wait is excluded
    assert not event.is_set() and not starts
    with compute_lease_scope('owned-control','cuda:0',leases=leases),budget:
        assert len(starts)==1 and len(leases.list())==1
        assert event.wait(2)
        with pytest.raises(InterruptedError,match='runtime limit'):budget.seal()
    assert reasons==['time_limit'] and leases.list()==[]
    assert not budget._thread.is_alive()


def test_completed_budget_does_not_cancel_later_or_turn_manual_stop_into_timeout():
    from backend.engine.runtime_budget import RuntimeBudget
    event=threading.Event();reasons=[]
    with RuntimeBudget(.08,event,lambda _:None,lambda:reasons.append('timeout')) as budget:
        budget.seal()
    assert not event.wait(.12) and reasons==[]
    with RuntimeBudget(1,event,lambda _:None,lambda:reasons.append('timeout')) as manual:
        event.set()
        with pytest.raises(InterruptedError,match='^Training cancelled$'):manual.check()
    assert not manual.spent and reasons==[] and not manual._thread.is_alive()


def test_budget_journal_failure_before_runner_and_expiry_fail_closed():
    from backend.engine.runtime_budget import RuntimeBudget
    event=threading.Event()
    def broken(*args):raise OSError('Controlled budget journal outage')
    budget=RuntimeBudget(.1,event,broken,lambda:None)
    with pytest.raises(OSError,match='Controlled budget journal'):budget.__enter__()
    assert budget._thread is None
    expiry=RuntimeBudget(.04,event,lambda _:None,broken)
    with expiry:
        assert event.wait(2)
        expiry._thread.join(1)
        with pytest.raises(OSError,match='cancellation journal'):expiry.check()
    assert expiry.spent and not expiry._thread.is_alive()
