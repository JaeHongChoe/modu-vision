import hashlib
import json
import threading
from pathlib import Path
from unittest import mock
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
    drafts=result['result']['label_drafts']
    assert result['status']=='needs_review' and drafts['drafted']==1 and drafts['batch_id'] and drafts['review_fingerprint']
    assert [row['status'] for row in drafts['images']]==['drafted'] and drafts['images'][0]['candidate_count']==1
    assert hashlib.sha256(image.read_bytes()).hexdigest()==before and not image.with_suffix('.json').exists()
    # A draft is not a label: the image has none until a person adopts the draft in step 2.
    assert not client.get('/api/annotations/new',params={'file_path':str(image)}).json()['annotations']
    listed=client.get('/api/label-suggestions').json()['suggestions']
    assert len(listed)==1 and listed[0]['id']==drafts['images'][0]['proposal_id']
    draft=listed[0]
    assert draft['status']=='pending' and draft['image_path']==str(image) and draft['batch_id']==drafts['batch_id'] and draft['backend']=='trained_model' and draft['job_id']=='job_parent'
    assert draft['image_sha256']==before and draft['checkpoint_sha256']==policy['parent_checkpoint_sha256'] and draft['dataset_fingerprint']==drafts['review_fingerprint']
    assert [c['annotation']['label'] for c in draft['candidates']]==['NG']
    assert all(c['id'].startswith(draft['id']+'_c') and c['annotation']['id']==c['id'] for c in draft['candidates'])
    batch=client.get(f"/api/label-suggestions/batches/{drafts['batch_id']}").json()
    assert batch['status']=='completed' and batch['review_fingerprint']==drafts['review_fingerprint'] and batch['generated']==1
    assert batch['entries']==[{'image_path':str(image),'image_id':'new','status':'generated','candidate_count':1,'proposal_id':draft['id']}]
    store=OperationsStore(project['project_dir']);assert store.get(result['cycle_id'])['result']==result['result']
    assert store.policy()['holdout']==policy['holdout']
    assert run_cycle(project,threading.Event())['status']=='idle'
    assert len(client.get('/api/label-suggestions').json()['suggestions'])==1
    reviewed=client.post(f"/api/label-suggestions/{draft['id']}/review",json={'decision':'accept','candidate_ids':[draft['candidates'][0]['id']],'actor':'tester'})
    assert reviewed.status_code==200,reviewed.text
    assert reviewed.json()['status']=='accepted' and reviewed.json()['backup_version_id']
    adopted=client.get('/api/annotations/new',params={'file_path':str(image)}).json()['annotations']
    assert adopted[0]['label']=='NG' and adopted[0]['id']==draft['candidates'][0]['id']
    assert adopted[0]['origin']['kind']=='model_draft' and adopted[0]['origin']['proposal_id']==draft['id'] and adopted[0]['origin']['reviewer']=='tester'
    assert hashlib.sha256(image.read_bytes()).hexdigest()==before and not image.with_suffix('.json').exists()
    image2=source/'test'/'OK'/'test.png';Image.new('RGB',(32,32),'red').save(image2)
    assert run_cycle(project,threading.Event())['status']=='blocked'


def test_a_pending_draft_is_not_drafted_again_by_a_later_cycle(tmp_path):
    client,project,source=setup_project(tmp_path)
    configure_program(project,{'parent_job_id':'job_parent','task':'classification','auto_label':True,'confidence_threshold':.9,'reviewer':'qa'})
    image=source/'new.png';Image.new('RGB',(32,32),'yellow').save(image)
    first=run_cycle(project,threading.Event())['result']['label_drafts']
    assert first['drafted']==1
    # Reprocessing existing images makes the same image new again; its pending draft is reused, not duplicated.
    configure_program(project,{'parent_job_id':'job_parent','task':'classification','auto_label':True,'confidence_threshold':.9,'reviewer':'qa','process_existing':True})
    second=run_cycle(project,threading.Event())
    drafts=second['result']['label_drafts']
    assert second['status']=='needs_review' and drafts['duplicate_pending']==1
    mine=[row for row in drafts['images'] if row['image_path']==str(image)]
    assert len(mine)==1 and mine[0]['status']=='duplicate_pending' and mine[0]['proposal_id']==first['images'][0]['proposal_id']
    pending=[s for s in client.get('/api/label-suggestions',params={'image_path':str(image)}).json()['suggestions'] if s['status']=='pending']
    assert len(pending)==1 and pending[0]['id']==first['images'][0]['proposal_id']


def test_a_cycle_failing_after_one_draft_keeps_its_batch_in_the_journal(tmp_path):
    client,project,source=setup_project(tmp_path)
    configure_program(project,{'parent_job_id':'job_parent','task':'classification','auto_label':True,'confidence_threshold':.9,'reviewer':'qa'})
    for name in ('a.png','b.png'):Image.new('RGB',(32,32),'yellow').save(source/name)
    from backend.engine import trainer
    real=trainer.infer;calls=[]
    def flaky(task,checkpoint,path,**kwargs):
        calls.append(str(path))
        if len(calls)==2:raise RuntimeError('controlled inference failure')
        return real(task,checkpoint,path,**kwargs)
    with mock.patch.object(trainer,'infer',flaky):result=run_cycle(project,threading.Event())
    assert result['status']=='failed' and 'controlled inference failure' in result['error']
    # The first draft reached step 2 before the failure, so the journal keeps its batch, fingerprint and count.
    drafts=result['result']['label_drafts']
    assert drafts['drafted']==1 and drafts['batch_id'] and drafts['review_fingerprint']
    assert [(row['image_path'],row['status']) for row in drafts['images']]==[(str(source/'a.png'),'drafted')]
    assert OperationsStore(project['project_dir']).get(result['cycle_id'])['result']['label_drafts']['batch_id']==drafts['batch_id']
    batch=client.get(f"/api/label-suggestions/batches/{drafts['batch_id']}").json()
    assert batch['status']=='failed' and batch['review_fingerprint']==drafts['review_fingerprint'] and [e['status'] for e in batch['entries']]==['generated','failed']
    pending=client.get('/api/label-suggestions').json()['suggestions']
    assert [(s['status'],s['batch_id'],s['image_path']) for s in pending]==[('pending',drafts['batch_id'],str(source/'a.png'))]
    # The next cycle reuses that pending draft and drafts only the image whose inference failed.
    second=run_cycle(project,threading.Event())
    assert second['status']=='needs_review' and second['result']['label_drafts']['duplicate_pending']==1 and second['result']['label_drafts']['drafted']==1
    assert len([s for s in client.get('/api/label-suggestions').json()['suggestions'] if s['status']=='pending'])==2


def test_auto_label_refuses_a_parent_that_step2_review_cannot_resolve(tmp_path):
    client,project,source=setup_project(tmp_path)
    parent=Path(project['models_dir'])/'job_parent'
    for name,task,recorded_source in (('job_foreign','classification',str(tmp_path/'elsewhere')),('job_detector','detection',str(source))):
        directory=Path(project['models_dir'])/name;directory.mkdir()
        (directory/'best_model.pt').write_bytes((parent/'best_model.pt').read_bytes())
        (directory/'model_meta.json').write_text(json.dumps({'task':task,'backbone':'resnet18','classes':['OK','NG'],'image_size':[32,32]}))
        (directory/'job_receipt.json').write_text(json.dumps({'task':task,'status':'completed','source_dataset_path':recorded_source,'dataset_fingerprint':'v1:fixture'}))
    # A parent trained on another source, or for another task than the project's, has no step 2 catalog row to adopt from.
    for parent_job_id,task in (('job_foreign','classification'),('job_detector','detection')):
        refused=client.put('/api/model-operations/policy',json={'task':task,'parent_job_id':parent_job_id,'auto_label':True,'reviewer':'qa'})
        assert refused.status_code==422 and '초안 검토' in refused.json()['detail'],refused.text
    assert client.put('/api/model-operations/policy',json={'task':'classification','parent_job_id':'job_parent','auto_label':True,'reviewer':'qa'}).status_code==200
    assert not list((Path(project['project_dir'])/'label_suggestions').glob('suggestion_*.json'))


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
    assert not list((Path(project['project_dir'])/'label_suggestions').glob('suggestion_*.json'))


def test_unexpected_adapter_error_has_a_terminal_reopenable_journal(tmp_path):
    client,project,source=setup_project(tmp_path)
    configure_program(project,{'parent_job_id':'job_parent','task':'classification','auto_retrain':True,'require_label_review':False,'reviewer':'qa'})
    Image.new('RGB',(32,32),'red').save(source/'new.png')
    def failed_adapter(*args):raise AttributeError('Controlled adapter contract failure')
    result=run_cycle(project,threading.Event(),training_fn=failed_adapter)
    assert result['status']=='failed' and 'contract failure' in result['error']
    assert OperationsStore(project['project_dir']).get(result['cycle_id'])['status']=='failed'


def _draft_files(project):
    root=Path(project['project_dir'])/'label_suggestions'
    return sorted(root.glob('suggestion_*.json')),sorted((root/'batches').glob('batch_*.json'))


def test_a_rejected_draft_is_not_drafted_again_while_the_cycle_waits_for_review(tmp_path):
    """Under auto_retrain the cycle stops at needs_review before its images count as seen; the drafts it made must
    not be made again on every later cycle, and a draft a person rejected is not offered again for the same bytes."""
    client,project,source=setup_project(tmp_path)
    configure_program(project,{'parent_job_id':'job_parent','task':'classification','auto_label':True,'auto_retrain':True,'confidence_threshold':.9,'reviewer':'qa'})
    image=source/'new.png';Image.new('RGB',(32,32),'yellow').save(image)
    first=run_cycle(project,threading.Event())
    assert first['status']=='needs_review' and first['result']['label_drafts']['drafted']==1
    draft_id=first['result']['label_drafts']['images'][0]['proposal_id']
    again=run_cycle(project,threading.Event())['result']['label_drafts']
    assert again['drafted']==0 and again['duplicate_pending']==1 and again['batch_id'] is None,again
    rejected=client.post(f'/api/label-suggestions/{draft_id}/review',json={'decision':'reject','actor':'tester'})
    assert rejected.status_code==200,rejected.text
    for _ in range(2):
        cycle=run_cycle(project,threading.Event())
        drafts=cycle['result']['label_drafts']
        assert cycle['status']=='needs_review' and drafts['drafted']==0 and drafts['rejected_before']==1 and drafts['batch_id'] is None,drafts
    suggestions,batches=_draft_files(project)
    assert len(suggestions)==1 and len(batches)==1
    # Changed image bytes are new work: the next cycle drafts them again.
    Image.new('RGB',(32,32),'orange').save(image)
    fresh=run_cycle(project,threading.Event())['result']['label_drafts']
    assert fresh['drafted']==1 and fresh['rejected_before']==0
    assert len(_draft_files(project)[0])==2


def test_a_zero_candidate_image_is_run_once_not_once_per_cycle(tmp_path):
    client,project,source=setup_project(tmp_path)
    configure_program(project,{'parent_job_id':'job_parent','task':'classification','auto_label':True,'auto_retrain':True,'confidence_threshold':.9,'reviewer':'qa'})
    image=source/'new.png';Image.new('RGB',(32,32),'yellow').save(image)
    from types import SimpleNamespace
    from backend.engine import trainer
    unsure=lambda task,checkpoint,path,**kwargs:SimpleNamespace(predictions={'predicted_class':'NG','class_index':1,'confidence':0.1},confidence_score=0.1,latency_ms=1.0)
    with mock.patch.object(trainer,'infer',unsure):
        first=run_cycle(project,threading.Event())['result']['label_drafts']
        assert first['zero_candidates']==1 and first['batch_id']
        later=[run_cycle(project,threading.Event())['result']['label_drafts'] for _ in range(2)]
    assert all(row['zero_candidates']==0 and row['drafted_before']==1 and row['batch_id'] is None for row in later),later
    assert len(_draft_files(project)[1])==1
    assert OperationsStore(project['project_dir']).policy()['drafted'][str(image)]['status']=='zero_candidates'
