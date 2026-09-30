"""Loopback process lifecycle verifies runtime identity, not model quality."""
from pathlib import Path
import pytest
from backend.tests.test_model_deployments import _fixture,_report,_approve
from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.engine.managed_service import ManagedService


def test_managed_process_apply_readback_restart_and_real_rollback(tmp_path):
    client,project,source,fingerprint,models=_fixture(tmp_path)
    service=ManagedService(project['project_dir'])
    history=[]
    try:
        for number,(baseline,candidate) in enumerate([('job_base','job_candidate'),('job_candidate','job_third')]):
            comparison_id='comparison_'+str(number)*32
            _report(project,source,fingerprint,models,incumbent=baseline,candidate=candidate,comparison_id=comparison_id)
            response=_approve(client,source,comparison_id)
            assert response.status_code==200,response.text
            revision=response.json()['revision']
            pipeline=get_single_segmentation_flowchart(job_id=candidate)
            for node in pipeline.nodes:
                if node.data.node_type=='inspection':node.data.task='classification'
            result=build_flow_package(pipeline=pipeline,checkpoints={candidate:models[candidate]},output_base_dir=tmp_path/'exports',package_name=f'release_{number}',approved_revisions={candidate:{key:revision[key] for key in ('revision_id','job_id','task','checkpoint_sha256')}})
            deployed=service.apply(result['package_path'],'cpu','operator',project)
            assert service.readback()['manifest_sha256']==deployed['release']['manifest_sha256']
            if number==0:
                with service.client() as runtime_client:
                    event=runtime_client.post('/v1/device-events',json={'device_id':'fixture-plc','event_id':'event-1','image_path':str(source/'test'/'OK'/'ok_00.png')})
                    assert event.status_code==202,event.text
            history.append(deployed)
        process_before=service.config['pid']
        assert service.stop()['status']=='stopped'
        recovered=ManagedService(project['project_dir'])
        assert recovered.start()['manifest_sha256']==history[1]['release']['manifest_sha256']
        assert recovered.config['pid']!=process_before
        restored=recovered.rollback(history[0]['deployment_id'],'operator')
        assert restored['restored_from']==history[0]['deployment_id']
        assert recovered.readback()['manifest_sha256']==history[0]['release']['manifest_sha256']
        recovered.stop()
    finally:
        ManagedService(project['project_dir']).stop()


def test_adapter_configuration_does_not_contact_network(tmp_path,monkeypatch):
    import socket
    import httpx
    service=ManagedService(tmp_path)
    monkeypatch.setattr(socket,'create_connection',lambda *args,**kwargs:pytest.fail('configuration must not contact PLC'))
    monkeypatch.setattr(httpx,'post',lambda *args,**kwargs:pytest.fail('configuration must not contact MES'))
    assert service.configure_adapters({'enabled':False,'modbus':{'host':'127.0.0.1','result_register':10,'ack_register':11},'mes':None})['saved']
    assert service.read_adapter_config()['enabled'] is False


@pytest.mark.parametrize('name', ['service.json','state','adapters.json','install','runtime_deployments.sqlite3'])
def test_managed_service_rejects_linked_project_state(tmp_path,name):
    project=tmp_path/'project';project.mkdir();root=project/'runtime_service';root.mkdir()
    external=tmp_path/'external'
    if name in ('state','install'):external.mkdir()
    else:external.write_text('{}')
    (root/name).symlink_to(external)
    with pytest.raises(ValueError,match='linked'):ManagedService(project)


def test_native_control_rejects_label_from_another_project(tmp_path,monkeypatch):
    import json,subprocess
    service=ManagedService(tmp_path)
    service.config['native_label']='local.moduvision.inspection.other-project'
    service.save(service.config)
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs:pytest.fail('Cross-project native service must not be controlled'))
    with pytest.raises(ValueError,match='identity'):service.uninstall_native()


def test_redacted_mes_token_survives_routine_config_edit_and_can_be_explicitly_cleared(tmp_path):
    import json
    service=ManagedService(tmp_path)
    service.configure_adapters({'enabled':False,'mes':{'url':'http://127.0.0.1:9000/inspection','token':'private-fixture-token'}})
    redacted=service.read_adapter_config()
    assert redacted['mes']['token'] is None
    redacted['mes']['timeout']=10
    service.configure_adapters(redacted)
    stored=json.loads((service.root/'adapters.json').read_text())
    assert stored['mes']['token']=='private-fixture-token'
    assert stored['mes']['timeout']==10
    service.configure_adapters({**redacted,'clear_mes_token':True})
    assert json.loads((service.root/'adapters.json').read_text())['mes']['token'] is None
