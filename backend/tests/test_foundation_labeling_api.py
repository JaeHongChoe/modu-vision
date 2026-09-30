import base64
import io
import json
import time

import numpy as np
from PIL import Image

from backend.tests.test_label_candidate_api import workspace
from backend.tests.test_label_suggestions import suggestion_workspace
from backend.api import routes_label_candidates as routes
from backend.engine.annotation_storage import dataset_annotation_dir


def test_feature_training_jobs_reopen_with_interrupted_receipt(workspace):
    from backend.engine import labeling_tasks
    client,project,source=workspace
    labeling_tasks.write(project,{'id':'featurejob_'+'a'*24,'kind':'feature_train','status':'running',
        'project_id':project['id'],'source_dataset_dir':str(source),'labelset_id':'default','created_at':'2026-09-30T00:00:00Z'})
    response=client.get('/api/label-suggestions/feature-jobs')
    assert response.status_code==200,response.text
    assert response.json()['jobs'][0]['status']=='interrupted'


def test_foundation_ui_class_mapping_keeps_trainable_native_mask_ids(workspace,monkeypatch):
    from backend.engine.foundation_labeling import mask_candidate
    client,project,source=workspace
    mask=np.zeros((60,80),bool);mask[20:30,30:40]=1
    monkeypatch.setattr(routes,'foundation_candidates',lambda *a,**k:[mask_candidate(mask,.95,'Scratch','mask',{'provider':'sam2'})])
    generated=client.post('/api/label-candidates/generate',json={'backend':'foundation','image_path':str(source/'target.png'),'output_geometry':'mask','class_ids':{'Scratch':7}})
    assert generated.status_code==200,generated.text
    proposal=generated.json();assert proposal['candidates'][0]['annotation']['category_id']==7
    accepted=client.post('/api/label-suggestions/'+proposal['id']+'/review',json={'decision':'accept','candidate_ids':[proposal['candidates'][0]['id']]})
    assert accepted.status_code==200,accepted.text
    path=dataset_annotation_dir(source,__import__('pathlib').Path(project['annotations_dir']),use_scope=False)/'masks/target.png'
    assert np.max(np.asarray(Image.open(path)))==7


def test_setup_reports_foundation_prerequisites_and_active_labelset(workspace):
    client, project, source = workspace
    setup = client.get('/api/label-candidates/setup').json()
    assert setup['providers']['foundation']['ready'] is False
    assert 'SAM2' in setup['providers']['foundation']['error']
    assert setup['labelset_id'] == 'default'
    assert setup['labelset_version']
    result = client.post('/api/label-candidates/generate', json={
        'backend': 'foundation', 'image_path': str(source/'target.png'),
        'points': [{'x': 8, 'y': 4, 'label': 1}]})
    assert result.status_code == 422
    assert 'SAM2' in result.json()['detail']


def test_foundation_proposal_acceptance_keeps_mask_hole_and_rejects_stale_labelset(workspace, monkeypatch):
    from backend.engine.foundation_labeling import mask_candidate
    client, project, source = workspace
    mask = np.zeros((60, 80), bool); mask[20:32, 30:45] = 1; mask[23:26, 34:38] = 0
    monkeypatch.setattr(routes, 'foundation_candidates', lambda *args, **kwargs:
        [mask_candidate(mask, .95, 'Scratch', 'mask', {'provider': 'sam2', 'device': 'cpu'})])
    setup = client.get('/api/label-candidates/setup').json()
    payload = {'backend': 'foundation', 'image_path': str(source/'target.png'),
               'labelset_id': 'default', 'labelset_version': setup['labelset_version'],
               'boxes': [[25, 15, 50, 40]], 'output_geometry': 'mask'}
    stale = client.post('/api/label-candidates/generate', json={**payload, 'labelset_version': 'stale'})
    assert stale.status_code == 409
    generated = client.post('/api/label-candidates/generate', json=payload)
    assert generated.status_code == 200, generated.text
    proposal = generated.json()
    assert proposal['labelset_id'] == 'default'
    assert proposal['candidates'][0]['area'] == 168
    reviewed = client.post('/api/label-suggestions/'+proposal['id']+'/review', json={
        'decision': 'accept', 'candidate_ids': [proposal['candidates'][0]['id']]})
    assert reviewed.status_code == 200, reviewed.text
    target = dataset_annotation_dir(source, __import__('pathlib').Path(project['annotations_dir']), use_scope=False)/'masks'/'target.png'
    pixels = np.asarray(Image.open(target))
    assert np.all(pixels[23:26, 34:38] == 0)
    assert np.count_nonzero(pixels) == 168


def test_keyword_provider_batch_cancels_and_persists_partial_work(workspace, monkeypatch):
    import threading
    from backend.engine.foundation_labeling import LabelingCancelled
    client, project, source = workspace
    started = threading.Event()
    def provider(*args, **kwargs):
        started.set()
        while not kwargs['cancel'].wait(.01):
            pass
        raise LabelingCancelled('cancelled')
    monkeypatch.setattr(routes, 'foundation_candidates', provider)
    result = client.post('/api/label-candidates/batches', json={'backend': 'foundation',
        'image_paths': [str(source/'target.png'), str(source/'exemplar.png')], 'prompt': 'scratch'})
    assert result.status_code == 200, result.text
    batch = result.json(); assert started.wait(2)
    cancelled = client.post('/api/label-candidates/batches/'+batch['id']+'/cancel')
    assert cancelled.status_code == 200
    for _ in range(100):
        current = client.get('/api/label-candidates/batches/'+batch['id']).json()
        if current['status'] == 'stopped': break
        time.sleep(.01)
    assert current['status'] == 'stopped'
    assert current['generated'] == 0
    assert not list((__import__('pathlib').Path(project['project_dir'])/'label_suggestions').glob('suggestion_*.json'))


def test_small_manual_labels_train_and_refine_new_immutable_feature_model(workspace, monkeypatch):
    import torch
    from backend.engine import foundation_labeling as foundation
    client, project, source = workspace
    (source/'target.json').write_text(json.dumps({'imageWidth':80,'imageHeight':60,'shapes':[
        {'label':'Scratch','shape_type':'rectangle','points':[[30,20],[45,32]]},
        {'label':'background','shape_type':'rectangle','points':[[0,0],[15,12]]}]}))
    class Encoder:
        model_metadata = {'pretrained': True, 'pretrained_sha256': 'test-feature-boundary', 'backbone':'dinov3_vits16'}
    monkeypatch.setattr(foundation, 'feature_encoder', lambda *args: Encoder())
    monkeypatch.setattr(foundation, 'extract_features', lambda images,*args:
        torch.tensor([[1.,0.],[0.,1.]])[:len(images)])
    def wait(job):
        for _ in range(200):
            current = client.get('/api/label-suggestions/feature-train/'+job['id']).json()
            if current.get('status') in ['completed','failed','stopped']: return current
            time.sleep(.01)
        return current
    started = client.post('/api/label-suggestions/feature-train',json={'epochs':30,'learning_rate':.1,'device':'cpu'})
    assert started.status_code == 200, started.text
    first = wait(started.json()); assert first['status']=='completed',first
    models = client.get('/api/label-suggestions/feature-models').json()['models']
    assert len(models)==1
    model = models[0]; assert model['classes']==['__background__','Scratch']
    path = __import__('pathlib').Path(model['checkpoint_path']); original = path.read_bytes()
    refined = client.post('/api/label-suggestions/feature-train',json={
        'epochs':20,'device':'cpu','parent_model_id':model['id']})
    assert refined.status_code == 200,refined.text
    second = wait(refined.json()); assert second['status']=='completed',second
    assert second['model_id'] != first['model_id']
    assert path.read_bytes()==original
    assert len(client.get('/api/label-suggestions/feature-models').json()['models'])==2
    # A completed artifact from a job whose current receipt is stopped must
    # never become a selectable parent after cancellation / recovery.
    job_file=__import__('pathlib').Path(project['project_dir'])/'labeling_jobs'/(first['id']+'.json')
    receipt=json.loads(job_file.read_text());receipt['status']='stopped';job_file.write_text(json.dumps(receipt))
    assert len(client.get('/api/label-suggestions/feature-models').json()['models'])==1


def test_foundation_click_and_box_endpoints_route_actual_masks(workspace, monkeypatch):
    from backend.api import routes_annotation
    client, project, source = workspace
    client.app.include_router(routes_annotation.router)
    seen=[]
    def model(path,setup,**kwargs):
        seen.append(kwargs)
        return [{'polygon':[[30.,20.],[44.,20.],[44.,31.],[30.,31.]],
                 'annotation':{'bbox':[30.,20.,45.,32.]},'confidence':.9,'area':180,'source':'sam2','provenance':{}}]
    monkeypatch.setattr(routes_annotation,'foundation_candidates',model)
    clicked=client.post('/api/annotations/auto-select',json={
        'image_path':str(source/'target.png'),'seed_x':35,'seed_y':25,'backend':'foundation','device':'cpu'})
    assert clicked.status_code==200,clicked.text
    assert seen[-1]['points']==[{'x':35.,'y':25.,'label':1}]
    boxed=client.post('/api/annotations/shape-converter',json={
        'image_path':str(source/'target.png'),'bbox':[25,15,50,40],'backend':'foundation'})
    assert boxed.status_code==200,boxed.text
    assert seen[-1]['boxes']==[[25.,15.,50.,40.]]
    assert boxed.json()['result']['source']=='sam2'


def test_completed_model_proposal_honors_device_and_size_bounds(suggestion_workspace,monkeypatch):
    from backend.api import routes_label_suggestions
    from types import SimpleNamespace
    client, project, source, image, *rest = suggestion_workspace
    def inference(**kwargs):
        assert kwargs['device']=='cpu'
        return SimpleNamespace(predictions=[{'bbox':[10,10,15,15],'score':.95,'label':'part'},
            {'bbox':[20,10,40,30],'score':.95,'label':'part'}],confidence_score=.95,latency_ms=1.)
    monkeypatch.setattr(routes_label_suggestions,'infer',inference)
    generated=client.post('/api/label-suggestions/generate',json={
        'job_id':'job_12345_test','image_path':str(image),'device':'cpu','min_area':100})
    assert generated.status_code==200,generated.text
    assert len(generated.json()['candidates'])==1
    assert generated.json()['candidates'][0]['annotation']['bbox']==[20.,10.,40.,30.]
    invalid=client.post('/api/label-suggestions/generate',json={
        'job_id':'job_12345_test','image_path':str(image),'device':'cuda:999999'})
    assert invalid.status_code==422


def test_mutated_foundation_feature_weights_block_review(workspace,monkeypatch,tmp_path):
    from backend.engine.foundation_labeling import mask_candidate,file_sha256
    client,project,source=workspace
    weights=tmp_path/'feature.safetensors';weights.write_bytes(b'pinned feature bytes')
    mask=np.zeros((60,80),bool);mask[20:32,30:45]=1
    monkeypatch.setattr(routes,'foundation_candidates',lambda *args,**kwargs:[mask_candidate(mask,.95,'part','polygon',{
        'provider':'sam2','feature_metadata':{'feature_checkpoint':str(weights),'pretrained_sha256':file_sha256(weights)}})])
    proposal=client.post('/api/label-candidates/generate',json={
        'backend':'foundation','image_path':str(source/'target.png'),'boxes':[[25,15,50,40]]}).json()
    weights.write_bytes(b'changed feature bytes')
    reviewed=client.post('/api/label-suggestions/'+proposal['id']+'/review',json={
        'decision':'accept','candidate_ids':[proposal['candidates'][0]['id']]})
    assert reviewed.status_code==409
