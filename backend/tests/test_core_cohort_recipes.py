"""Actual untrained CPU classifier evaluates a frozen original heldout cohort."""
import copy,hashlib,json,shutil
from pathlib import Path
import pytest,torch
from PIL import Image
from backend.tests.test_model_comparisons import _client

@pytest.mark.parametrize('task',['classification','segmentation','detection','anomaly'])
def test_local_completed_model_evaluates_same_cohort_on_local_and_other_target(tmp_path,monkeypatch,task):
    from backend.engine.classification import create_classification_model
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.tests.test_remote_operations import FakeAdditionalRemote
    from backend.remote.recipe import run_recipe_worker
    from backend.remote import operations
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'));torch.set_num_threads(1)
    source=tmp_path/'source'
    for part,offset in [('train',10),('val',40),('test',90)]:
        for label,color in [('OK',30),('NG',160)]:
            image=(source/'images'/part/(label+'.png') if task in ('segmentation','detection') else source/part/('good' if label=='OK' else 'defect')/'sample.png' if task=='anomaly' else source/part/label/'sample.png');image.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(32,32),(color,offset,20)).save(image)
            if task=='detection':image.with_suffix('.json').write_text(json.dumps({'imagePath':image.name,'imageWidth':32,'imageHeight':32,'shapes':[] if label=='OK' else [{'label':'defect','shape_type':'rectangle','points':[[3,3],[15,15]]}]}))
            if task=='segmentation' or task=='anomaly' and label=='NG':
                mask=(source/'masks'/part/image.name if task=='segmentation' else source/'ground_truth'/'defect'/'sample_mask.png')
                mask.parent.mkdir(parents=True,exist_ok=True);Image.new('L',(32,32),0 if label=='OK' else 1).save(mask)
    client=_client(tmp_path);project=client.post('/api/project/create',json={'name':'CPU heldout control','task':task}).json()
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    version=client.post('/api/dataset/versions',json={'name':'Original independent test'}).json()
    from backend.api.routes_dataset_versions import _read_manifest
    manifest_sha=_read_manifest(project,version['id'])[1]['content_digest']
    directory=Path(project['models_dir'])/'job_cpu_eval';directory.mkdir()
    if task=='classification':
        model=create_classification_model('efficientnet_b0',2,pretrained=False)
        meta={'task':task,'backbone':'efficientnet_b0','classes':['OK','NG']}
    elif task=='segmentation':
        from backend.engine.segmentation import build_segmentation_model
        model=build_segmentation_model('unet',2,pretrained=False);meta={'task':task,'model_name':'unet','preset':'fast','classes':['background','defect']}
    elif task=='detection':
        from backend.engine.detection import create_detection_model
        model=create_detection_model(backbone='yolo26n',num_classes=2,pretrained=False);meta={'task':task,'backbone':'yolo26n','classes':['defect'],'pretrained':False}
    else:
        from backend.engine.anomaly import PaDiMDetector
        model=PaDiMDetector(pretrained=False,target_dim=4);model.fit(torch.utils.data.DataLoader(torch.rand(2,3,32,32),batch_size=2))
        meta={'task':task,'detector_type':'padim','feature_backbone':'resnet18','classes':['good','anomaly'],'pretrained':False,'anomaly_mode':'segmentation'}
    if task=='classification':
        with torch.no_grad():
            for parameter in model.parameters():parameter.zero_()
    meta.update(image_size=[32,32],training_provenance={'manifest_sha256':manifest_sha,'dataset_version_id':version['id'],'labelset_id':'default'})
    torch.save({**meta,'model_state_dict':model.state_dict()},directory/'best_model.pt')
    (directory/'model_meta.json').write_text(json.dumps(meta))
    (directory/'job_receipt.json').write_text(json.dumps({'status':'completed','task':task,'source_dataset_path':str(source),
        'dataset_fingerprint':fingerprint_dataset(source),'dataset_path':str(source),'training_provenance':meta['training_provenance']}))
    profile={'id':'alternate-target','name':'Different owned CPU','ssh_target':'fixture-only','ssh_port':22,'remote_root':str(tmp_path/'remote'),'runtime_kind':'python','runtime_value':'python3'}
    assert client.post('/api/compute/profiles',json=profile).status_code==201
    class RealCPU(FakeAdditionalRemote):
        def launch(self,profile,argv,run_id):
            self.launches+=1;status=run_recipe_worker(self.root/'runs'/run_id/'spec.json');assert status['status']=='completed',status
            return 'actual-in-process-cpu-evaluator'
    worker=RealCPU(tmp_path/'remote');monkeypatch.setattr(operations,'SSHTransport',lambda:worker)
    params={'job_id':'job_cpu_eval','dataset_path':str(source),'evaluation_dataset_version_id':version['id']}
    outcomes=[]
    for target in ('local','selected_compute'):
        body={'task':task,'stage':'evaluate','params':params,'device':'cpu','execution_target':target,
              **({'compute_profile_id':profile['id']} if target=='selected_compute' else {})}
        response=client.post('/api/model-execution/recipes',json=body);assert response.status_code==200,response.text
        value=response.json();outcomes.append(value)
        assert value['execution_target']==target and value['compute_profile_id']==(profile['id'] if target=='selected_compute' else None)
        assert value['common_cohort']['image_count']==2 and value['metrics']['evaluated_split']=='test' and not value['metrics']['selection_overlap']
        if task=='classification':assert value['metrics']['accuracy']==.5 and value['confusion_matrix']['matrix']==[[1,0],[1,0]]
        assert not value['quality_approved'] and value['execution']['runtime']['device']=='cpu'
        assert value['execution']['checkpoint_sha256']==hashlib.sha256((directory/'best_model.pt').read_bytes()).hexdigest()
        assert client.get('/api/model-execution/recipes/'+value['execution']['receipt_id']).status_code==200
        test=source/'images/test' if task in ('segmentation','detection') else source/'test'
        assert len(value['test_predictions'])==2 and all(Path(row['file_path']).is_relative_to(test) for row in value['test_predictions'])
    assert worker.launches==1
    assert outcomes[0]['common_cohort']==outcomes[1]['common_cohort'] and outcomes[0]['metrics']==outcomes[1]['metrics']
    if task=='classification' and torch.backends.mps.is_available():
        metal=client.post('/api/model-execution/recipes',json={'task':task,'stage':'evaluate','params':params,'device':'mps','execution_target':'local'})
        assert metal.status_code==200,metal.text
        assert metal.json()['execution']['runtime']['device']=='mps' and metal.json()['metrics']==outcomes[0]['metrics']
    from backend.api import routes_model_execution
    from backend.remote.ssh_transport import SSHTransportError
    def disconnected(*args):raise SSHTransportError('alternate target unavailable')
    monkeypatch.setattr(routes_model_execution,'execute_remote',disconnected)
    monkeypatch.setattr(routes_model_execution,'run_recipe',lambda *args:pytest.fail('Host fallback'))
    refused=client.post('/api/model-execution/recipes',json=body);assert refused.status_code==503 and 'no local fallback' in refused.text
