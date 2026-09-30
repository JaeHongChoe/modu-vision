import hashlib
import json
import threading
from pathlib import Path
import torch
from fastapi.testclient import TestClient
from PIL import Image
from backend.engine.classification.model import create_classification_model
from backend.engine.model_operations import OperationsStore, configure_program, run_cycle
from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root
from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
from backend.main import create_app


def setup_project(tmp_path):
    app=create_app(project_dir=str(tmp_path/'registry'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    source=tmp_path/'source'
    for split,color in [('train','white'),('val','gray'),('test','black')]:
        folder=source/split/'OK';folder.mkdir(parents=True);Image.new('RGB',(32,32),color).save(folder/f'{split}.png')
    project=client.post('/api/project/create',json={'name':'Operations','task':'classification'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    project['source_dataset_dir']=str(source)
    model=create_classification_model('resnet18',2,pretrained=False)
    with torch.no_grad():
        for p in model.parameters():p.zero_()
        model.fc.bias[1]=10
    directory=Path(project['models_dir'])/'job_parent';directory.mkdir()
    torch.save({'task':'classification','backbone':'resnet18','classes':['OK','NG'],'image_size':[32,32],'model_state_dict':model.state_dict()},directory/'best_model.pt')
    (directory/'model_meta.json').write_text(json.dumps({'task':'classification','backbone':'resnet18','classes':['OK','NG'],'image_size':[32,32]}))
    (directory/'job_receipt.json').write_text(json.dumps({'task':'classification','status':'completed','source_dataset_path':str(source),'dataset_fingerprint':'v1:fixture'}))
    return client,project,source


def test_new_data_auto_labels_are_reviewable_originals_untouched_and_journal_reopens(tmp_path):
    client,project,source=setup_project(tmp_path)
    policy=configure_program(project,{'parent_job_id':'job_parent','task':'classification','auto_label':True,'confidence_threshold':.9,'auto_retrain':False,'reviewer':'qa','process_existing':False})
    image=source/'new.png';Image.new('RGB',(32,32),'yellow').save(image);before=hashlib.sha256(image.read_bytes()).hexdigest()
    result=run_cycle(project,threading.Event())
    assert result['status']=='needs_review' and result['result']['auto_labels'][0]['accepted_count']==1
    assert hashlib.sha256(image.read_bytes()).hexdigest()==before and not image.with_suffix('.json').exists()
    proposal=client.get('/api/annotations/new',params={'file_path':str(image)}).json()
    assert proposal['annotations'][0]['label']=='NG'
    store=OperationsStore(project['project_dir']);assert store.get(result['cycle_id'])['result']==result['result']
    assert store.policy()['holdout']==policy['holdout']
    assert run_cycle(project,threading.Event())['status']=='idle'
    image2=source/'test'/'OK'/'test.png';Image.new('RGB',(32,32),'red').save(image2)
    assert run_cycle(project,threading.Event())['status']=='blocked'


def test_configured_automatic_activation_requires_explicit_policy_authorization(tmp_path):
    client,project,source=setup_project(tmp_path)
    response=client.put('/api/model-operations/policy',json={'task':'classification','parent_job_id':'job_parent','auto_approve':True,'reviewer':'qa'})
    assert response.status_code==422
    configured=client.put('/api/model-operations/policy',json={'task':'classification','parent_job_id':'job_parent','reviewer':'qa','auto_label':False})
    assert configured.status_code==200,configured.text
    reopened=client.get('/api/model-operations').json();assert reopened['policy']['parent_job_id']=='job_parent'
    response=client.post('/api/model-operations/run',json={'background':False})
    assert response.status_code==200 and response.json()['status']=='idle'


def test_cancel_and_unrelated_owner_recovery_are_truthful(tmp_path):
    import os,psutil
    client,project,source=setup_project(tmp_path)
    configure_program(project,{'parent_job_id':'job_parent','task':'classification','reviewer':'qa'})
    event=threading.Event();event.set();result=run_cycle(project,event)
    assert result['status']=='cancelled'
    store=OperationsStore(project['project_dir']);stale={**result,'cycle_id':'cycle_stale','status':'running','owner_pid':os.getppid(),'owner_created_at':0};store.save(stale)
    read=client.get('/api/model-operations').json()
    assert next(row for row in read['cycles'] if row['cycle_id']=='cycle_stale')['status']=='interrupted'


def test_holdout_duplicates_are_blocked_before_automatic_labels(tmp_path):
    client,project,source=setup_project(tmp_path)
    configure_program(project,{'parent_job_id':'job_parent','task':'classification','auto_label':True,'reviewer':'qa'})
    duplicate=source/'duplicate.png';duplicate.write_bytes((source/'test'/'OK'/'test.png').read_bytes())
    result=run_cycle(project,threading.Event())
    assert result['status']=='blocked' and 'duplicates' in result['error']
    assert not client.get('/api/annotations/duplicate',params={'file_path':str(duplicate)}).json()['annotations']


def test_unexpected_adapter_error_has_a_terminal_reopenable_journal(tmp_path):
    client,project,source=setup_project(tmp_path)
    configure_program(project,{'parent_job_id':'job_parent','task':'classification','auto_retrain':True,'require_label_review':False,'reviewer':'qa'})
    Image.new('RGB',(32,32),'red').save(source/'new.png')
    def failed_adapter(*args):raise AttributeError('Controlled adapter contract failure')
    result=run_cycle(project,threading.Event(),training_fn=failed_adapter)
    assert result['status']=='failed' and 'contract failure' in result['error']
    assert OperationsStore(project['project_dir']).get(result['cycle_id'])['status']=='failed'
