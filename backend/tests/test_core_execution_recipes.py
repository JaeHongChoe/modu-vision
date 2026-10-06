"""Real CPU control models prove portable core trials, not model quality."""
import hashlib,json
from pathlib import Path
import pytest,torch
from PIL import Image
from backend.tests.test_model_comparisons import _client,_checkpoint
from backend.engine.dataset_fingerprint import fingerprint_dataset

@pytest.mark.parametrize('task',['classification','segmentation','detection','anomaly'])
def test_core_prediction_and_benchmark_use_explicit_cpu_and_saved_model(tmp_path,monkeypatch,task):
    from backend.tests.test_remote_operations import FakeAdditionalRemote
    from backend.remote.recipe import run_recipe_worker
    from backend.remote import operations
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'));torch.set_num_threads(1)
    source=tmp_path/'source';source.mkdir();image=source/'sample.png';Image.new('RGB',(40,32),(170,80,25)).save(image);original=hashlib.sha256(image.read_bytes()).hexdigest()
    client=_client(tmp_path);project=client.post('/api/project/create',json={'name':'core CPU control','task':task}).json();assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    directory=Path(project['models_dir'])/'job_core';_checkpoint(directory,source,fingerprint_dataset(source),1)
    if task=='segmentation':
        from backend.engine.segmentation import build_segmentation_model
        model=build_segmentation_model('unet',num_classes=2,pretrained=False)
        torch.save({'task':task,'model_name':'unet','classes':['background','defect'],'image_size':[32,32],'model_state_dict':model.state_dict()},directory/'best_model.pt')
        (directory/'model_meta.json').write_text(json.dumps({'task':task}))
        rec=json.loads((directory/'job_receipt.json').read_text());rec['task']=task;(directory/'job_receipt.json').write_text(json.dumps(rec))
    elif task in ('detection','anomaly'):
        if task=='detection':
            from backend.engine.detection import create_detection_model
            model=create_detection_model(backbone='yolo26n',num_classes=2,pretrained=False)
            metadata={'task':task,'backbone':'yolo26n','classes':['defect'],'image_size':[32,32],'pretrained':False}
        else:
            from backend.engine.anomaly import PaDiMDetector
            model=PaDiMDetector(pretrained=False,target_dim=4)
            model.fit(torch.utils.data.DataLoader(torch.rand(2,3,32,32),batch_size=2))
            metadata={'task':task,'detector_type':'padim','feature_backbone':'resnet18','classes':['good','anomaly'],'image_size':[32,32],'pretrained':False}
        torch.save({**metadata,'model_state_dict':model.state_dict()},directory/'best_model.pt')
        (directory/'model_meta.json').write_text(json.dumps(metadata))
        rec=json.loads((directory/'job_receipt.json').read_text());rec['task']=task;(directory/'job_receipt.json').write_text(json.dumps(rec))
    profile={'id':'owned-cpu','name':'Owned CPU loopback','ssh_target':'fixture-only','ssh_port':22,'remote_root':str(tmp_path/'remote'),'runtime_kind':'python','runtime_value':'python3'};assert client.post('/api/compute/profiles',json=profile).status_code==201
    class RealCPU(FakeAdditionalRemote):
        def launch(self,profile,argv,run_id):
            self.launches+=1;state=run_recipe_worker(self.root/'runs'/run_id/'spec.json');assert state['status']=='completed',state
            return 'owned-in-process-cpu-worker'
    worker=RealCPU(tmp_path/'remote');monkeypatch.setattr(operations,'SSHTransport',lambda:worker)
    results=[]
    for stage in ['predict','benchmark']:
        params={'job_id':'job_core',**({'image_path':str(image)} if stage=='predict' else {'iterations':5,'resolution':32})}
        pair=[]
        for target in ['local','selected_compute']:
            body={'task':task,'stage':stage,'params':params,'device':'cpu','execution_target':target,**({'compute_profile_id':'owned-cpu'} if target=='selected_compute' else {})}
            response=client.post('/api/model-execution/recipes',json=body);assert response.status_code==200,response.text
            result=response.json();assert result['execution']['runtime']['device']=='cpu' and result['execution']['runtime']['process_id']>0
            assert result['execution']['checkpoint_sha256']==hashlib.sha256((directory/'best_model.pt').read_bytes()).hexdigest()
            reopened=client.get('/api/model-execution/recipes/'+result['execution']['receipt_id']);assert reopened.status_code==200
            value=json.loads((Path(project['reports_dir'])/'execution_recipes'/(result['execution']['receipt_id']+'.json')).read_text());digest=value.pop('evidence_sha256');assert hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()==digest
            pair.append(result);results.append(result)
        assert pair[0]['execution']['input_binding_sha256']==pair[1]['execution']['input_binding_sha256']
        if stage=='predict':assert pair[0]['predictions']==pair[1]['predictions'] and pair[0]['overlay_base64']==pair[1]['overlay_base64'] and pair[0]['source_sha256']==original
        else:
            scope='feature_extractor_forward_only' if task=='anomaly' else 'model_forward_only'
            for result in pair:assert result['iterations']==5 and result['measurement_scope']==scope and result['input_kind']=='synthetic_random_tensor' and result['mean_latency_ms']>0
    assert worker.launches==2 and hashlib.sha256(image.read_bytes()).hexdigest()==original
    assert len(client.get('/api/model-execution/recipes',params={'task':task,'job_id':'job_core'}).json()['receipts'])==4
    from backend.api import routes_model_execution
    from backend.remote.ssh_transport import SSHTransportError
    def disconnected(*args):raise SSHTransportError('owned core target unavailable')
    monkeypatch.setattr(routes_model_execution,'execute_remote',disconnected);monkeypatch.setattr(routes_model_execution,'run_recipe',lambda *args:pytest.fail('local fallback'))
    failed=client.post('/api/model-execution/recipes',json=body);assert failed.status_code==503 and 'no local fallback' in failed.text


def test_core_benchmark_refuses_requested_missing_accelerator_before_model_load(tmp_path,monkeypatch):
    from backend.engine.execution_recipe import run_recipe
    monkeypatch.setattr(torch.cuda,'is_available',lambda:False)
    with pytest.raises(ValueError,match='CUDA.*unavailable'):run_recipe('classification','benchmark',tmp_path/'absent.pt',{}, {'job_id':'job_missing','iterations':5,'resolution':32},tmp_path/'outputs','cuda:0')
