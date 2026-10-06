"""A selected specialist target runs the real worker or fails without local fallback."""
import hashlib
import json
from pathlib import Path
import pytest
import torch


def test_explicit_recipe_rejects_unavailable_cuda_before_any_engine(monkeypatch, tmp_path):
    from backend.engine.execution_recipe import run_recipe
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: False)
    with pytest.raises(ValueError, match='CUDA.*unavailable'):
        run_recipe('rotation', 'predict', tmp_path/'missing.pt', {}, {}, tmp_path/'output', 'cuda:0')
    assert not (tmp_path/'output').exists()


def test_rotation_recipe_matches_local_real_model_and_reopens_verified_receipt(tmp_path, monkeypatch):
    from backend.tests.test_specialist_runtime_limits import prepared_client
    from backend.tests.test_remote_operations import FakeAdditionalRemote
    from backend.remote.recipe import run_recipe_worker
    from backend.remote import operations
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    client,project,source,dataset=prepared_client(tmp_path,'rotation');torch.set_num_threads(1)
    trained=client.post('/api/rotation/train',json={'dataset_path':dataset,'epochs':1,'image_size':32,'width':8,'device':'cpu'})
    assert trained.status_code==200,trained.text
    job=trained.json()['job_id'];image=source/'3.png';original=hashlib.sha256(image.read_bytes()).hexdigest()
    profile={'id':'owned-cpu','name':'Owned CPU loopback','ssh_target':'fixture-only','ssh_port':22,'remote_root':str(tmp_path/'remote'),'runtime_kind':'python','runtime_value':'python3'}
    assert client.post('/api/compute/profiles',json=profile).status_code==201
    class RealCPU(FakeAdditionalRemote):
        def launch(self,profile,argv,run_id):
            self.launches+=1
            result=run_recipe_worker(self.root/'runs'/run_id/'spec.json')
            assert result['status']=='completed',result
            return 'owned-in-process-cpu-worker'
    transport=RealCPU(tmp_path/'remote');monkeypatch.setattr(operations,'SSHTransport',lambda:transport)
    body={'task':'rotation','stage':'predict','device':'cpu','execution_target':'local','params':{'job_id':job,'image_path':str(image),'include_aligned':True}}
    local=client.post('/api/model-execution/recipes',json=body);assert local.status_code==200,local.text
    body.update(execution_target='selected_compute',compute_profile_id='owned-cpu')
    remote=client.post('/api/model-execution/recipes',json=body);assert remote.status_code==200,remote.text
    a,b=local.json(),remote.json()
    assert a['correction_deg']==b['correction_deg'] and a['aligned_image_base64']==b['aligned_image_base64']
    assert b['execution']['compute_profile_id']=='owned-cpu' and b['execution']['device']=='cpu'
    assert b['execution']['runtime']['process_id']>0 and b['execution']['checkpoint_sha256']
    assert transport.launches==1 and hashlib.sha256(image.read_bytes()).hexdigest()==original
    restored=client.get('/api/model-execution/recipes/'+b['execution']['receipt_id']);assert restored.status_code==200
    assert restored.json()['evidence_sha256']==b['execution']['evidence_sha256']
    body.update(stage='evaluate',params={'job_id':job,'dataset_path':dataset,'split':'test'})
    evaluated=client.post('/api/model-execution/recipes',json=body);assert evaluated.status_code==200,evaluated.text
    assert evaluated.json()['sample_count']==1 and evaluated.json()['execution']['compute_profile_id']=='owned-cpu'
    assert transport.launches==2
    history=client.get('/api/model-execution/recipes',params={'task':'rotation','job_id':job})
    assert history.status_code==200 and len(history.json()['receipts'])==3
    receipt_path=Path(project['reports_dir'])/'execution_recipes'/(b['execution']['receipt_id']+'.json')
    original_receipt=receipt_path.read_bytes();altered=json.loads(original_receipt);altered['device']='cuda:0'
    receipt_path.write_text(json.dumps(altered))
    assert client.get('/api/model-execution/recipes/'+altered['receipt_id']).status_code==409
    assert client.get('/api/model-execution/recipes',params={'task':'rotation','job_id':job}).status_code==409
    receipt_path.write_bytes(original_receipt)
    # A disconnect must not call the local engine or publish a successful receipt.
    from backend.api import routes_model_execution
    from backend.remote.coordinator import RemoteDisconnected
    def disconnected(*args):raise RemoteDisconnected('owned target unavailable')
    monkeypatch.setattr(routes_model_execution,'execute_remote',disconnected)
    monkeypatch.setattr(routes_model_execution,'run_recipe',lambda *args:pytest.fail('local fallback'))
    failed=client.post('/api/model-execution/recipes',json=body)
    assert failed.status_code==503 and 'no local fallback' in failed.text
    assert len(list(receipt_path.parent.glob('recipe_*.json')))==3
    from backend.remote.ssh_transport import SSHTransportError
    def transport_failed(*args):raise SSHTransportError('owned SSH target unreachable')
    monkeypatch.setattr(routes_model_execution,'execute_remote',transport_failed)
    failed=client.post('/api/model-execution/recipes',json=body)
    assert failed.status_code==503 and 'no local fallback' in failed.text
    body['params']['unreviewed_option']='unsupported'
    assert client.post('/api/model-execution/recipes',json=body).status_code==422
    body['params'].pop('unreviewed_option');body['params']['split']='train'
    assert client.post('/api/model-execution/recipes',json=body).status_code==422


def test_recipe_support_matrix_has_every_family_and_explicit_gan_boundary():
    from backend.engine.execution_recipe import support_matrix
    matrix=support_matrix()
    assert len(matrix)==10 and all(row['train'] and row['evaluate'] for row in matrix.values())
    assert all(row['predict']==(task!='defect_gan') and row['generate']==(task=='defect_gan') for task,row in matrix.items())
    assert matrix['defect_gan']['flow'] is False and matrix['defect_gan']['quality_approved'] is False
    assert matrix['defect_gan']['native_recipe_stages']==['evaluate','generate']
    assert all(matrix[t]['native_recipe_stages']==['predict','benchmark'] for t in ('classification','detection','segmentation','anomaly'))
    assert 'never pipeline' in matrix['anomaly']['benchmark_scope']
    assert matrix['patch_classification']['adapter']=='native_recipe' and matrix['patch_classification']['native_recipe_stages']==['evaluate','predict']
    from backend.engine.execution_recipe import request_model
    for task,row in matrix.items():
        for stage in row['native_recipe_stages']:assert request_model(task,stage).model_fields['job_id']


@pytest.mark.parametrize('invalid',['traversal','symlink','duplicate','changed_hash'])
def test_worker_rejects_unbound_or_linked_inputs(tmp_path,invalid):
    from backend.remote.recipe import verify_inputs,sha
    folder=tmp_path/'inputs';folder.mkdir();a=folder/'a';b=folder/'b';a.write_bytes(b'a');b.write_bytes(b'b')
    refs=[{'path':'inputs/a','size':1,'sha256':sha(a)},{'path':'inputs/b','size':1,'sha256':sha(b)}]
    if invalid=='traversal':refs[1]['path']='inputs/../outside'
    elif invalid=='symlink':b.unlink();b.symlink_to(a)
    elif invalid=='duplicate':refs[1]=refs[0]
    else:refs[1]['sha256']='0'*64
    with pytest.raises(ValueError):verify_inputs(tmp_path,refs)


@pytest.mark.parametrize('family',['ocr','rotated-detection','enhancement','defect-gan'])
def test_all_native_recipes_execute_actual_completed_models_on_owned_cpu(tmp_path,monkeypatch,family):
    from backend.tests.test_specialist_runtime_limits import prepared_client,await_terminal
    from backend.tests.test_remote_operations import FakeAdditionalRemote
    from backend.remote.recipe import run_recipe_worker
    from backend.remote import operations
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    client,project,source,dataset=prepared_client(tmp_path,family);torch.set_num_threads(1)
    options={'dataset_path':dataset,'epochs':1,'device':'cpu'}
    if family=='ocr':options.update(batch_size=2,image_height=16,image_width=32)
    elif family=='defect-gan':options.update(batch_size=2,base_channels=8)
    elif family=='rotated-detection':options.update(batch_size=2,image_size=32)
    trained=client.post('/api/'+family+'/train',json=options);assert trained.status_code==200,trained.text
    job=trained.json()['job_id']
    if family=='rotated-detection':assert await_terminal(client,family,job)['status']=='completed'
    profile={'id':'recipe-cpu','name':'Owned CPU loopback','ssh_target':'fixture-only','ssh_port':22,'remote_root':str(tmp_path/'remote'),'runtime_kind':'python','runtime_value':'python3'}
    assert client.post('/api/compute/profiles',json=profile).status_code==201
    class RealCPU(FakeAdditionalRemote):
        def launch(self,profile,argv,run_id):
            self.launches+=1;result=run_recipe_worker(self.root/'runs'/run_id/'spec.json')
            assert result['status']=='completed',result
            return 'owned-in-process-cpu-worker'
    remote=RealCPU(tmp_path/'remote');monkeypatch.setattr(operations,'SSHTransport',lambda:remote)
    task=family.replace('-','_');params={'job_id':job,'image_path':str(source/'3.png')};stage='predict'
    if task=='defect_gan':stage='generate';params={'job_id':job,'count':2,'seed':11,'source_image_path':str(source/'3.png'),'source_sha256':hashlib.sha256((source/'3.png').read_bytes()).hexdigest(),'regions':[{'id':'small','bbox':[4,4,24,24]}]}
    body={'task':task,'stage':stage,'params':params,'device':'cpu','execution_target':'selected_compute','compute_profile_id':'recipe-cpu'}
    result=client.post('/api/model-execution/recipes',json=body);assert result.status_code==200,result.text
    assert result.json()['execution']['compute_profile_id']=='recipe-cpu' and remote.launches==1
    if task=='defect_gan':
        from backend.engine.defect_gan import validate_composition_source
        generated=result.json();validate_composition_source(generated['review_dir'],generated)
        assert len(generated['candidates'])==2 and all(Path(r['path']).is_file() for r in generated['candidates'])
    local_body={**body,'execution_target':'local'};local_body.pop('compute_profile_id')
    local=client.post('/api/model-execution/recipes',json=local_body);assert local.status_code==200,local.text
    a,b=local.json(),result.json();assert a['execution']['device']=='cpu' and a['execution']['input_binding_sha256']==b['execution']['input_binding_sha256']
    if task=='defect_gan':assert [v['sha256'] for v in a['candidates']]==[v['sha256'] for v in b['candidates']]
    else:assert a['source_sha256']==b['source_sha256']
    reopened=client.get('/api/model-execution/recipes/'+a['execution']['receipt_id']);assert reopened.status_code==200
    assert reopened.json()['evidence_sha256']==a['execution']['evidence_sha256']
    body.update(stage='evaluate',params={'job_id':job,'dataset_path':dataset,'split':'test'})
    evaluated=client.post('/api/model-execution/recipes',json=body);assert evaluated.status_code==200,evaluated.text
    assert evaluated.json()['execution']['runtime']['torch_version'] and remote.launches==2
    local_body={**body,'execution_target':'local'};local_body.pop('compute_profile_id')
    local_eval=client.post('/api/model-execution/recipes',json=local_body);assert local_eval.status_code==200,local_eval.text
    metric={'ocr':'character_error_rate','rotated_detection':'mAP_50','enhancement':'output_mse','defect_gan':'rgb_statistics_mmd'}[task]
    assert local_eval.json()[metric]==evaluated.json()[metric]
    assert local_eval.json()['execution']['checkpoint_sha256']==evaluated.json()['execution']['checkpoint_sha256']
