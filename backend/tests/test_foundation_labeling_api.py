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
    assert reviewed.status_code==409 and reviewed.json()['detail']==routes._PROVENANCE_CHANGED_AT_REVIEW


def test_setup_readiness_names_the_blocker_and_the_files_the_loader_expects(workspace,monkeypatch,tmp_path):
    # Stage 2 (SAM2 preparation card): readiness keeps ready/error as the gate and adds the first blocker,
    # the loader's file list with measured sizes, and the weight file's SHA256 as identification only.
    # Nothing is downloaded and no size or licence is asserted by the backend.
    import hashlib,importlib.util
    from pathlib import Path
    client,project,source=workspace
    installed={'transformers':True};real_find_spec=importlib.util.find_spec
    def find_spec(name,*args,**kwargs):
        if name=='transformers':return object() if installed['transformers'] else None
        return real_find_spec(name,*args,**kwargs)
    monkeypatch.setattr(importlib.util,'find_spec',find_spec)
    names=['config.json','preprocessor_config.json','model.safetensors']
    def foundation():
        response=client.get('/api/label-candidates/setup');assert response.status_code==200,response.text
        return response.json()['providers']['foundation']
    readiness=foundation()
    assert readiness['ready'] is False and 'SAM2' in readiness['error'] and readiness['state']=='not_configured'
    assert readiness['expected_files']==[{'name':name,'present':False,'bytes':None} for name in names]
    assert readiness['missing_files']==names and readiness['model_sha256'] is None and readiness['model_type'] is None
    assert readiness['official_reference']['url']=='https://huggingface.co/facebook/sam2.1-hiera-tiny'
    assert not {'size','bytes','license','sha256'}&{key.lower() for key in readiness['official_reference']}
    folder=tmp_path/'sam2';folder.mkdir()
    saved=client.put('/api/label-candidates/setup',json={'mask_model_dir':str(folder)})
    assert saved.status_code==200,saved.text
    readiness=saved.json()['providers']['foundation']
    assert readiness['ready'] is False and 'SAM2' in readiness['error'] and readiness['state']=='files_missing'
    config=json.dumps({'model_type':'sam2'});weights=b'not real weights, identification only'
    (folder/'config.json').write_text(config,encoding='utf-8');(folder/'model.safetensors').write_bytes(weights)
    readiness=foundation()
    # The gate is unchanged: config.json + model.safetensors make it ready; the processor file is only listed as missing.
    assert readiness['ready'] is True and readiness['error'] is None and readiness['state']=='ready'
    assert readiness['missing_files']==['preprocessor_config.json'] and readiness['model_type']=='sam2'
    assert [(row['name'],row['present'],row['bytes']) for row in readiness['expected_files']]==[
        ('config.json',True,len(config.encode('utf-8'))),('preprocessor_config.json',False,None),('model.safetensors',True,len(weights))]
    assert readiness['model_sha256']==hashlib.sha256(weights).hexdigest()
    # A rewritten weight file gets a new identification (the cache keys on the file's size and modification time).
    (folder/'model.safetensors').write_bytes(weights+b' rewritten')
    assert foundation()['model_sha256']==hashlib.sha256(weights+b' rewritten').hexdigest()
    (folder/'config.json').write_text(json.dumps({'model_type':'grounding-dino'}),encoding='utf-8')
    readiness=foundation()
    assert readiness['ready'] is False and 'SAM2' in readiness['error']
    assert readiness['state']=='wrong_model_type' and readiness['model_type']=='grounding-dino'
    (folder/'config.json').write_text('{not json',encoding='utf-8')
    readiness=foundation()
    assert readiness['ready'] is False and 'SAM2' in readiness['error'] and readiness['state']=='invalid_config'
    partial=tmp_path/'partial';partial.mkdir();(partial/'model.safetensors').write_bytes(weights)
    assert client.put('/api/label-candidates/setup',json={'mask_model_dir':str(partial)}).status_code==200
    readiness=foundation()
    assert readiness['ready'] is False and 'SAM2' in readiness['error'] and readiness['state']=='files_incomplete'
    assert readiness['missing_files']==['config.json','preprocessor_config.json']
    # A missing dependency is the first blocker, while the folder's files are still reported.
    installed['transformers']=False
    readiness=foundation()
    assert readiness['ready'] is False and 'SAM2' in readiness['error'] and readiness['dependency_available'] is False
    assert readiness['state']=='dependency_missing' and readiness['expected_files'][2]['present'] is True
    installed['transformers']=True
    # A configured folder that no longer exists is a path problem, not a file problem.
    (Path(project['project_dir'])/'semantic_labeling.json').write_text(json.dumps({'mask_model_dir':str(tmp_path/'gone')}),encoding='utf-8')
    readiness=foundation()
    assert readiness['ready'] is False and 'SAM2' in readiness['error'] and readiness['state']=='path_invalid'
    assert readiness['expected_files']==[{'name':name,'present':False,'bytes':None} for name in names]
    refused=client.post('/api/label-candidates/generate',json={'backend':'foundation','image_path':str(source/'target.png'),'points':[{'x':8,'y':4,'label':1}]})
    assert refused.status_code==422 and 'SAM2' in refused.json()['detail']


def _weight_files(folder):
    """Weight files a user can end up with: a genuine one, and the damaged copies a download or clone leaves behind."""
    from safetensors.numpy import save_file
    genuine=folder/'genuine.safetensors'
    save_file({'image_encoder.weight':np.arange(1000,dtype=np.float32),'mask_decoder.bias':np.zeros((3,5),np.int64)},str(genuine),metadata={'format':'pt'})
    data=genuine.read_bytes();header=8+int.from_bytes(data[:8],'little')
    damaged={
        # What `git clone` of the official repository leaves without git-lfs.
        'lfs_pointer':b'version https://git-lfs.github.com/spec/v1\noid sha256:'+b'0'*64+b'\nsize 155906050\n',
        'empty':b'',
        'truncated':data[:-100],
        'header_only':data[:header],
        'extra_bytes':data+b'\0'*16,
        'short':b'\x01\x02',
        'zero_length_header':(0).to_bytes(8,'little')+b'{}',
        'array_header':(2).to_bytes(8,'little')+b'[]',
        'no_offsets':(len(b'{"a":{"dtype":"F32"}}')).to_bytes(8,'little')+b'{"a":{"dtype":"F32"}}',
    }
    paths={}
    for name,payload in damaged.items():
        paths[name]=folder/f'{name}.safetensors';paths[name].write_bytes(payload)
    return genuine,paths


def test_safetensors_header_check_agrees_with_the_loader_without_reading_past_the_header(tmp_path,monkeypatch):
    # Stage 2: readiness names a weight file the loader would refuse (a git-lfs pointer, an interrupted copy, an
    # empty file) from its header alone. The real safetensors loader is the oracle for every verdict.
    from safetensors import safe_open
    from backend.engine import foundation_labeling as foundation
    genuine,damaged=_weight_files(tmp_path)
    def loads(path):
        try:
            with safe_open(str(path),framework='numpy') as stream:
                return sorted(stream.keys())
        except Exception:
            return None
    assert foundation.safetensors_header_check(genuine)=='ok'
    assert loads(genuine)==['image_encoder.weight','mask_decoder.bias']
    expected={'lfs_pointer':'not_safetensors','empty':'empty','truncated':'truncated','header_only':'truncated',
              'extra_bytes':'truncated','short':'not_safetensors','zero_length_header':'not_safetensors',
              'array_header':'not_safetensors','no_offsets':'not_safetensors'}
    assert {name:foundation.safetensors_header_check(path) for name,path in damaged.items()}==expected
    assert all(loads(path) is None for path in damaged.values()),'every file named damaged is one the loader refuses'
    # Only the 8-byte length and the header are read: a header length beyond the loader's limit is refused unread.
    monkeypatch.setattr(foundation,'_SAFETENSORS_MAX_HEADER',16)
    assert foundation.safetensors_header_check(genuine)=='not_safetensors'
    reads=[]
    class Recorder(io.BytesIO):
        def read(self,size=-1):
            reads.append(size);return super().read(size)
    monkeypatch.setattr(foundation,'_SAFETENSORS_MAX_HEADER',100_000_000)
    data=genuine.read_bytes();original_open=type(genuine).open
    monkeypatch.setattr(type(genuine),'open',lambda self,*args,**kwargs:Recorder(data) if self==genuine else original_open(self,*args,**kwargs))
    assert foundation.safetensors_header_check(genuine)=='ok'
    assert reads==[8,int.from_bytes(data[:8],'little')],reads


def test_setup_readiness_flags_damaged_weights_and_a_non_object_config_without_changing_the_gate(workspace,monkeypatch,tmp_path):
    # ready/error stay the gate every caller reads; weights_check is the card's evidence that model.safetensors is a
    # complete safetensors file. A config.json that is valid JSON but not an object is invalid_config, not a 500.
    import importlib.util,shutil
    client,project,source=workspace
    real_find_spec=importlib.util.find_spec
    monkeypatch.setattr(importlib.util,'find_spec',lambda name,*args,**kwargs:object() if name=='transformers' else real_find_spec(name,*args,**kwargs))
    weights=tmp_path/'weights';weights.mkdir();genuine,damaged=_weight_files(weights)
    folder=tmp_path/'sam2';folder.mkdir()
    (folder/'config.json').write_text(json.dumps({'model_type':'sam2'}),encoding='utf-8')
    (folder/'preprocessor_config.json').write_text('{}',encoding='utf-8')
    assert client.put('/api/label-candidates/setup',json={'mask_model_dir':str(folder)}).status_code==200
    def foundation():
        response=client.get('/api/label-candidates/setup');assert response.status_code==200,response.text
        return response.json()['providers']['foundation']
    for name,expected in [('lfs_pointer','not_safetensors'),('truncated','truncated'),('empty','empty')]:
        shutil.copyfile(damaged[name],folder/'model.safetensors')
        readiness=foundation()
        assert (readiness['ready'],readiness['error'],readiness['state'])==(True,None,'ready'),name
        assert readiness['weights_check']==expected,name
        assert readiness['expected_files'][2]['bytes']==damaged[name].stat().st_size,name
    shutil.copyfile(genuine,folder/'model.safetensors')
    readiness=foundation()
    assert (readiness['ready'],readiness['state'],readiness['weights_check'])==(True,'ready','ok')
    for config in ['null','[]','"sam2"']:
        (folder/'config.json').write_text(config,encoding='utf-8')
        readiness=foundation()
        assert (readiness['ready'],readiness['state'])==(False,'invalid_config'),config
        assert readiness['error']=='SAM2 model configuration is missing or invalid.',config
    (folder/'model.safetensors').unlink()
    assert foundation()['weights_check'] is None,'no weight file, no verdict'
