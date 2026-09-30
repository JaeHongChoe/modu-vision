import io
from pathlib import Path
import zipfile
import pytest
from backend.engine.fleet_agent import extract_package_archive
from backend.engine.fleet import validate_target_url,FleetRegistry


def test_fleet_archive_rejects_traversal_links_and_unbounded_expansion(tmp_path):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr('../escape','unsafe')
    archive=tmp_path/'archive.zip';archive.write_bytes(stream.getvalue())
    with pytest.raises(ValueError):extract_package_archive(archive,tmp_path/'target')
    assert not (tmp_path/'escape').exists()


def test_fleet_targets_reopen_without_exposing_tokens_and_reject_insecure_remote(tmp_path):
    with pytest.raises(ValueError):validate_target_url('http://192.168.1.42:8080')
    assert validate_target_url('http://127.0.0.1:8080')=='http://127.0.0.1:8080'
    store=FleetRegistry(tmp_path);target=store.save_target(name='Cell 1',url='https://cell1.example.com',token='private-secret-value')
    reopened=FleetRegistry(tmp_path).targets()
    assert reopened[0]['target_id']==target['target_id'] and reopened[0]['token_set']
    assert 'private-secret-value' not in str(reopened)
    assert store.secret(target['target_id'])=='private-secret-value'


def test_real_field_agent_release_inference_reopen_and_rollback(tmp_path,monkeypatch):
    import hashlib,time
    from fastapi.testclient import TestClient
    from backend.tests.test_model_deployments import _fixture,_report,_approve
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.engine.fleet_agent import create_agent_app
    client,project,source,fingerprint,models=_fixture(tmp_path)
    import torch,json
    from backend.engine.classification.model import create_classification_model
    for checkpoint in models.values():
        model=create_classification_model('resnet18',2,pretrained=False)
        with torch.no_grad():
            for parameter in model.parameters():parameter.zero_()
            model.fc.bias[0]=10
        meta={'task':'classification','backbone':'resnet18','classes':['OK','NG'],'image_size':[32,32]}
        torch.save({**meta,'model_state_dict':model.state_dict()},checkpoint)
        checkpoint.with_name('model_meta.json').write_text(json.dumps(meta))
    agent_app=create_agent_app(tmp_path/'field' ,'agent-test-secret-12345678')
    registry=FleetRegistry(project['project_dir']);target=registry.save_target(name='Loopback Field',url='http://127.0.0.1:8514',token='agent-test-secret-12345678')
    monkeypatch.setattr(registry,'client',lambda identifier:TestClient(agent_app,headers={'Authorization':'Bearer agent-test-secret-12345678'}))
    deployments=[]
    try:
        for number,(baseline,candidate) in enumerate([('job_base','job_candidate'),('job_candidate','job_third')]):
            comparison='comparison_'+str(number)*32
            _report(project,source,fingerprint,models,incumbent=baseline,candidate=candidate,comparison_id=comparison)
            approved=_approve(client,source,comparison);assert approved.status_code==200,approved.text
            revision=approved.json()['revision'];pipeline=get_single_segmentation_flowchart(job_id=candidate)
            next(n for n in pipeline.nodes if n.data.node_type=='inspection').data.task='classification'
            built=build_flow_package(pipeline=pipeline,checkpoints={candidate:models[candidate]},output_base_dir=tmp_path/'packages',package_name='release'+str(number),approved_revisions={candidate:{key:revision[key] for key in ('revision_id','job_id','task','checkpoint_sha256')}})
            package=Path(built['package_path']);digest=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest()
            deployed=registry.apply(target['target_id'],{'package_path':str(package),'manifest_sha256':digest,'device':'cpu'},reviewer='qa')
            assert deployed['ack']['manifest_sha256']==digest and registry.readback(target['target_id'])['matches_active']
            deployments.append(deployed)
            if number==0:
                with TestClient(agent_app,headers={'Authorization':'Bearer agent-test-secret-12345678'}) as remote:
                    job=remote.post('/agent/v1/jobs/upload',content=(source/'test'/'OK'/'ok_00.png').read_bytes()).json()
                    until=time.monotonic()+25
                    while time.monotonic()<until:
                        result=remote.get('/agent/v1/jobs/'+job['job_id']).json()
                        if result['state'] in ('completed','error'):break
                        time.sleep(.1)
                    assert result['state']=='completed' and result['verdict'] in ('OK','NG'),result
        recovered=FleetRegistry(project['project_dir']);assert recovered.ledger(target['target_id']).active()['deployment_id']==deployments[1]['deployment_id']
        rolled=registry.rollback(target['target_id'],deployments[0]['deployment_id'],reviewer='qa')
        assert rolled['restored_from']==deployments[0]['deployment_id']
        assert agent_app.state.agent.runtime()['manifest_sha256']==deployments[0]['release']['manifest_sha256']
    finally:agent_app.state.agent.stop()
