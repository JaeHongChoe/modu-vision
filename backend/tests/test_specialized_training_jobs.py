"""Persisted specialized jobs cancel, reopen, isolate projects and detect restart."""
import json
import threading
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from backend.main import create_app
from backend.api import routes_ocr, routes_defect_gan


@pytest.mark.parametrize('task,route,module,method,labels',[
    ('ocr','ocr',routes_ocr,'train_ocr',[{'image':'train.png','text':'A','split':'train'},{'image':'val.png','text':'A','split':'val'}]),
    ('defect_gan','defect-gan',routes_defect_gan,'train_defect_gan',[{'image':'train.png','bbox':[0,0,32,32],'split':'train'},{'image':'val.png','bbox':[0,0,32,32],'split':'train'}]),
])
def test_background_job_cancel_reopen_and_project_isolation(tmp_path,monkeypatch,task,route,module,method,labels):
    entered=threading.Event()
    def controlled(dataset,output,*,cancel_event,on_progress,**kwargs):
        on_progress({'epoch':1,'batch':1,'batches':2,'loss':.25});entered.set()
        assert cancel_event.wait(5),'Cancellation must reach the running trainer'
        raise InterruptedError('Training cancelled')
    monkeypatch.setattr(module,method,controlled)
    app=create_app(project_dir=str(tmp_path/'projects'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':'Jobs'}).json()
    source=tmp_path/'source';source.mkdir()
    Image.new('RGB',(32,32),'white').save(source/'train.png');Image.new('RGB',(32,32),'black').save(source/'val.png')
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    assert client.post(f'/api/{route}/manifest',json={'dataset_path':str(source),'samples':labels}).status_code==200
    started=client.post(f'/api/{route}/train',json={'dataset_path':str(source),'epochs':2,'background':True})
    assert started.status_code==202,started.text
    identifier=started.json()['job_id'];assert entered.wait(5)
    progress=client.get(f'/api/{route}/jobs/{identifier}').json()
    assert progress['batch']==1 and progress['training_provenance']['dataset_version_id']
    assert client.post(f'/api/{route}/jobs/{identifier}/cancel').status_code==200
    for _ in range(100):
        record=client.get(f'/api/{route}/jobs/{identifier}').json()
        if record['status']=='stopped':break
        time.sleep(.01)
    assert record['status']=='stopped' and record['events'][-1]['status']=='stopped'
    assert not Path(project['models_dir']).joinpath(task,identifier,'best_model.pt').exists()
    assert client.get(f'/api/{route}/jobs').json()['jobs'][0]['job_id']==identifier
    client.post('/api/project/create',json={'name':'Other'})
    assert client.get(f'/api/{route}/jobs/{identifier}').status_code==404
    assert client.post(f'/api/{route}/train',json={'dataset_path':str(source),'epochs':1,'background':True}).status_code==409
    client.post('/api/project/open',json={'project_dir':project['project_dir']})
    journal=Path(project['models_dir'])/task/identifier/'job.json'
    previous=json.loads(journal.read_text());previous.update(status='running',owner_instance='previous-process')
    journal.write_text(json.dumps(previous))
    assert client.get(f'/api/{route}/jobs/{identifier}').json()['status']=='interrupted'
