"""Real ResNet CPU controls qualify patch execution identity, never training quality."""
from pathlib import Path
import hashlib,json
import pytest,torch
from backend.tests.test_model_comparisons import _client,_checkpoint
from backend.tests.test_patch_classification import _manifest
from backend.engine.patch_classification import load_patch_manifest
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.api import routes_dataset


def test_patch_predict_and_heldout_evaluation_bind_target_pixels_and_reopen(tmp_path,monkeypatch):
    from backend.tests.test_remote_operations import FakeAdditionalRemote
    from backend.remote import operations
    from backend.remote.recipe import run_recipe_worker
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'));torch.set_num_threads(1)
    client=_client(tmp_path);project=client.get('/api/project/current').json();dataset=Path(project['dataset_dir'])/'patch'/'control';dataset.mkdir(parents=True)
    value=_manifest(dataset);source=tmp_path/'source';source.mkdir();mapping={}
    for row in value['patches']:
        original=source/row['image'];original.parent.mkdir(parents=True,exist_ok=True);original.write_bytes((dataset/row['image']).read_bytes())
        mapping[row['image']]={'source_relative_path':row['image'],'source_sha256':row['source_sha256']}
    value.update(source_dataset_path=str(source),source_map=mapping);(dataset/'patches.json').write_text(json.dumps(value))
    # The real preparation route also writes this audited provenance sidecar.
    (dataset/'source_manifest.json').write_text(json.dumps([
        {'image':str(dataset/name),'source_image':str(source/row['source_relative_path']),'source_sha256':row['source_sha256']}
        for name,row in mapping.items()]))
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    directory=Path(project['models_dir'])/'job_patch_recipe';_checkpoint(directory,source,fingerprint_dataset(source,studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,split_manifest=routes_dataset._split_manifest_file(source)),1)
    payload=torch.load(directory/'best_model.pt',weights_only=True);payload['task']='patch_classification';torch.save(payload,directory/'best_model.pt')
    metadata={'task':'patch_classification','backbone':'resnet18','classes':['OK','NG'],'normal_class':'OK','image_size':[32,32],'patch_size':16,'stride':16,'patch_provenance':load_patch_manifest(dataset).provenance}
    (directory/'model_meta.json').write_text(json.dumps(metadata));receipt=json.loads((directory/'job_receipt.json').read_text());receipt.update(task='patch_classification',dataset_path=str(dataset));(directory/'job_receipt.json').write_text(json.dumps(receipt))
    profile={'id':'owned-patch-recipe','name':'Owned patch CPU','ssh_target':'fixture-only','ssh_port':22,'remote_root':str(tmp_path/'remote'),'runtime_kind':'python','runtime_value':'python3'};assert client.post('/api/compute/profiles',json=profile).status_code==201
    class RealCPU(FakeAdditionalRemote):
        def launch(self,profile,argv,run_id):
            self.launches+=1;state=run_recipe_worker(self.root/'runs'/run_id/'spec.json');assert state['status']=='completed',state
            return 'owned-patch-in-process-cpu'
    worker=RealCPU(tmp_path/'remote');monkeypatch.setattr(operations,'SSHTransport',lambda:worker)
    before=hashlib.sha256((directory/'best_model.pt').read_bytes()).hexdigest();results=[]
    for stage,extra in [('predict',{'image_path':str(source/'images/test_ng.png')}),('evaluate',{'dataset_path':str(dataset),'split':'test'}),('evaluate',{'dataset_path':str(dataset),'split':'val'})]:
        pair=[]
        for target in ('local','selected_compute'):
            body={'task':'patch_classification','stage':stage,'device':'cpu','execution_target':target,'params':{'job_id':directory.name,**extra},**({'compute_profile_id':profile['id']} if target=='selected_compute' else {})}
            response=client.post('/api/model-execution/recipes',json=body);assert response.status_code==200,response.text
            result=response.json();assert result['execution']['runtime']['device']=='cpu' and result['execution']['checkpoint_sha256']==before
            assert client.get('/api/model-execution/recipes/'+result['execution']['receipt_id']).json()['evidence_sha256']==result['execution']['evidence_sha256']
            if stage=='evaluate':
                assert result['metrics']['evaluated_split']==extra['split'] and len(result['test_predictions'])==2
                assert result['binding']['execution_target']==target and result['binding']['device']=='cpu' and result['binding']['checkpoint_sha256']==before
                assert all(Path(row['file_path']).is_relative_to(source) and hashlib.sha256(Path(row['file_path']).read_bytes()).hexdigest()==row['source_sha256'] for row in result['test_predictions'])
                assert result['evaluation_id'] and not result.get('quality_approved',False)
            pair.append(result);results.append(result)
        assert pair[0]['execution']['input_binding_sha256']==pair[1]['execution']['input_binding_sha256']
        if stage=='predict':assert pair[0]['patches']==pair[1]['patches'] and pair[0]['score_map_base64']==pair[1]['score_map_base64']
        else:assert pair[0]['metrics']==pair[1]['metrics'] and pair[0]['test_predictions']==pair[1]['test_predictions']
    assert worker.launches==3 and len(client.get('/api/model-execution/recipes',params={'task':'patch_classification','job_id':directory.name}).json()['receipts'])==6
    sidecar=dataset/'source_manifest.json';saved_sidecar=sidecar.read_bytes()
    altered_sidecar=json.loads(saved_sidecar);altered_sidecar[0]['source_image']='/unowned/private.png';sidecar.write_text(json.dumps(altered_sidecar))
    refused=client.post('/api/model-execution/recipes',json=body);assert refused.status_code==422 and 'sidecar differs' in refused.text
    sidecar.write_bytes(saved_sidecar);unrelated=dataset/'unrelated.json';unrelated.write_text('{"unowned":"not transferred"}')
    refused=client.post('/api/model-execution/recipes',json=body);assert refused.status_code==422 and 'only its task manifest' in refused.text
    unrelated.unlink();assert worker.launches==3
    from backend.engine.patch_evaluation_evidence import archive_patch_evaluation
    project['source_dataset_dir']=str(source)
    for change in ('box','missing','duplicate','source','model','cohort','quality','split'):
        altered=json.loads(json.dumps(results[-1]))
        if change=='box':altered['test_predictions'][0]['box']=[0,0,8,8]
        if change=='missing':altered['test_predictions'].pop()
        if change=='duplicate':altered['test_predictions'].append(altered['test_predictions'][0])
        if change=='source':altered['test_predictions'][0]['source_sha256']='a'*64
        if change=='model':altered['model_sha256']='b'*64
        if change=='cohort':altered['dataset_sha256']='c'*64
        if change=='quality':altered['quality_approved']=True
        if change=='split':altered['metrics']['evaluated_split']='test'
        with pytest.raises(ValueError):archive_patch_evaluation(project,directory/'best_model.pt',dataset,altered,execution={})
    from backend.api import routes_model_execution
    from backend.remote.ssh_transport import SSHTransportError
    def disconnected(*args):raise SSHTransportError('owned patch unavailable')
    monkeypatch.setattr(routes_model_execution,'execute_remote',disconnected);monkeypatch.setattr(routes_model_execution,'run_recipe',lambda *args:pytest.fail('local fallback'))
    refused=client.post('/api/model-execution/recipes',json=body);assert refused.status_code==503 and 'no local fallback' in refused.text
    assert hashlib.sha256((directory/'best_model.pt').read_bytes()).hexdigest()==before
    for name,row in mapping.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==row['source_sha256']


def test_patch_evaluation_never_accepts_training_partition():
    from backend.engine.execution_recipe import request_model
    model=request_model('patch_classification','evaluate')
    assert model.model_validate({'job_id':'job_patch','dataset_path':'/owned','split':'test'}).split=='test'
    with pytest.raises(ValueError):model.model_validate({'job_id':'job_patch','dataset_path':'/owned','split':'train'})
