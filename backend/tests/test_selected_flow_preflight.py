"""Actual dependency check on an owned CPU worker; no inference/quality claim."""
import hashlib,json
from pathlib import Path
import pytest,torch
from PIL import Image
from backend.tests.test_model_comparisons import _client


def test_saved_flow_selected_cpu_preflight_is_bound_reopens_and_never_falls_back(tmp_path,monkeypatch):
    from backend.api import routes_dataset,routes_export
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.flowchart_engine import get_single_detection_flowchart
    from backend.tests.test_remote_operations import FakeAdditionalRemote
    from backend.remote import operations
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    client=_client(tmp_path);project=client.get('/api/project/current').json();source=tmp_path/'source';source.mkdir();Image.new('RGB',(32,32)).save(source/'input.png')
    directory=Path(project['models_dir'])/'job_preflight';(directory/'dataset').mkdir(parents=True)
    torch.save({'task':'detection','model_state_dict':{}},directory/'best_model.pt');(directory/'model_meta.json').write_text(json.dumps({'task':'detection'}))
    digest=fingerprint_dataset(source,studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,split_manifest=routes_dataset._split_manifest_file(source))
    (directory/'job_receipt.json').write_text(json.dumps({'status':'completed','task':'detection','source_dataset_path':str(source),'dataset_fingerprint':digest,'dataset_path':str(directory/'dataset')}))
    saved=client.post('/api/flowchart/pipeline',params={'recipe_task':'detection','source_dataset_path':str(source)},json=get_single_detection_flowchart(job_id='job_preflight').model_dump());assert saved.status_code==200,saved.text
    profile={'id':'owned-preflight','name':'Owned CPU preflight','ssh_target':'fixture-only','ssh_port':22,'remote_root':str(tmp_path/'remote'),'runtime_kind':'python','runtime_value':'python3'};assert client.post('/api/compute/profiles',json=profile).status_code==201
    class ActualCPU(FakeAdditionalRemote):
        def launch(self,profile,argv,run_id):
            from backend.remote.flow_preflight import run_flow_preflight
            self.launches+=1;result=run_flow_preflight(self.root/'runs'/run_id/'spec.json');assert result['status']=='completed',result
            return 'owned-preflight-worker'
    worker=ActualCPU(tmp_path/'remote');monkeypatch.setattr(operations,'SSHTransport',lambda:worker)
    target={'kind':'selected_compute','compute_profile_id':profile['id'],'device':'cpu'}
    body={'source_dataset_path':str(source),'recipe_task':'detection','version_id':saved.json()['version_id'],'target':target}
    absent=client.post('/api/export/flow/preflight',json={**body,'target':{**target,'compute_profile_id':'missing-profile'}})
    assert absent.status_code==422 and 'profile is unavailable' in absent.text
    before=hashlib.sha256((directory/'best_model.pt').read_bytes()).hexdigest()
    original=routes_export._preflight_requirements;monkeypatch.setattr(routes_export,'_preflight_requirements',lambda *args:pytest.fail('API-host dependency fallback'))
    response=client.post('/api/export/flow/preflight',json=body);assert response.status_code==200,response.text
    report=response.json();assert worker.launches==1 and report['target_identity']['kind']=='selected_compute' and report['target_identity']['compute_profile_id']==profile['id']
    assert report['runtime']['device']=='cpu' and report['runtime']['process_id']>0 and report['environment_hash'] and not report['model_inference_executed']
    assert report['recipe_release']['version_id']==saved.json()['version_id'] and report['package_identity']['checkpoints']['job_preflight']==before
    stored=json.loads((Path(project['project_dir'])/'deployment_preflight'/(report['report_id']+'.json')).read_text())
    from backend.engine.flow_preflight import _report_sha
    assert _report_sha(stored)==stored['report_sha256']
    from backend.remote.flow_preflight import validate_report
    binding=json.loads(next((tmp_path/'remote/runs').glob('*/spec.json')).read_text())['preflight_binding']
    for change in ['target','runtime','release','environment','digest','status','blocking','model','decision']:
        altered=json.loads(json.dumps(stored))
        if change=='target':altered['target_identity']['compute_profile_id']='other'
        if change=='runtime':altered['runtime']['device']='mps'
        if change=='release':altered['recipe_release']['pipeline_sha256']='a'*64
        if change=='environment':altered['environment_hash']='b'*64
        if change=='digest':altered['report_sha256']='c'*64
        if change=='status':altered['status']='blocked' if altered['status']=='ready' else 'ready'
        if change=='blocking':altered['blocked_nodes']={'unrelated':['model:other']}
        if change=='model':next(row for row in altered['requirements'] if row['kind']=='model')['evidence_ref']='sha256:'+'d'*64
        if change=='decision':altered['decision_blocked']=not altered['decision_blocked']
        if change!='digest':altered['report_sha256']=_report_sha(altered)
        with pytest.raises(ValueError):validate_report(altered,binding,get_single_detection_flowchart(job_id='job_preflight'))
    monkeypatch.setattr(routes_export,'_preflight_requirements',original)
    reopened=client.get('/api/export/flow/preflights/'+report['report_id'],params={'source_dataset_path':str(source),'target':json.dumps(target)});assert reopened.status_code==200,reopened.text
    assert reopened.json()['stale'] and reopened.json()['stale_reasons']==['selected_environment_not_rechecked']
    assert client.post('/api/compute/profiles',json={**profile,'runtime_value':'python3-other'}).status_code==201
    changed=client.get('/api/export/flow/preflights/'+report['report_id'],params={'source_dataset_path':str(source),'target':json.dumps(target)}).json();assert 'target_changed' in changed['stale_reasons']
    from backend.remote.ssh_transport import SSHTransportError
    def unavailable(*args,**kwargs):raise SSHTransportError('owned preflight unavailable')
    monkeypatch.setattr(routes_export,'selected_flow_preflight',unavailable);monkeypatch.setattr(routes_export,'_preflight_requirements',lambda *args:pytest.fail('failed selected target local fallback'))
    refused=client.post('/api/export/flow/preflight',json=body);assert refused.status_code==503 and 'no local fallback' in refused.text
    assert hashlib.sha256((directory/'best_model.pt').read_bytes()).hexdigest()==before


@pytest.mark.parametrize('target',[{'kind':'selected_compute','device':'cpu'},{'kind':'selected_compute','compute_profile_id':'x','device':'mps'},{'kind':'selected_compute','compute_profile_id':'x','device':'cuda:1'}])
def test_selected_preflight_requires_one_explicit_supported_target(target):
    from backend.engine.flow_preflight import normalize_target
    with pytest.raises(ValueError):normalize_target(target)
