"""Durable deployment recovery exercises real SQLite state and exact receipts."""
import pytest
import json
from pathlib import Path
import subprocess
import sys
from backend.engine.runtime_deployment import DeploymentLedger


def release(letter):
    return {'manifest_sha256': letter * 64, 'device': 'cpu', 'package_path': '/release/' + letter}


def receipt(value):
    return {'status': 'ready', **value}


def test_process_interruption_after_candidate_apply_keeps_recovery_record(tmp_path):
    ledger = DeploymentLedger(tmp_path)
    prior = ledger.apply(release('a'), receipt, reviewer='operator')
    runtime = dict(release('a'))

    def interrupted(value):
        runtime.update(value)
        raise KeyboardInterrupt('power interrupted before database commit')

    with pytest.raises(KeyboardInterrupt):
        ledger.apply(release('b'), interrupted, reviewer='operator')
    assert ledger.active()['deployment_id'] == prior['deployment_id']
    assert ledger.diagnostics()['pending']['release']['manifest_sha256'] == 'b' * 64

    def restore(value):
        runtime.update(value)
        return receipt(value)

    recovered = DeploymentLedger(tmp_path).recover(restore)
    assert recovered['status'] == 'rolled_back'
    assert runtime['manifest_sha256'] == 'a' * 64
    assert recovered['ack']['manifest_sha256'] == 'a' * 64
    assert ledger.diagnostics()['pending'] is None


def test_failed_rollback_remains_pending_until_exact_previous_receipt(tmp_path):
    ledger = DeploymentLedger(tmp_path)
    ledger.apply(release('a'), receipt, reviewer='operator')

    def wrong_runtime(value):
        return receipt(release('b'))

    with pytest.raises(ValueError):
        ledger.apply(release('c'), wrong_runtime, reviewer='operator')
    pending = ledger.diagnostics()['pending']
    assert pending['status'] == 'needs_review'
    assert ledger.active()['release']['manifest_sha256'] == 'a' * 64
    assert ledger.recover(receipt)['ack']['manifest_sha256'] == 'a' * 64


def test_interrupted_first_deployment_never_promotes_unaccepted_candidate(tmp_path):
    ledger = DeploymentLedger(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        ledger.apply(release('a'), lambda value: (_ for _ in ()).throw(KeyboardInterrupt()), reviewer='operator')
    recovered = ledger.recover(lambda value: pytest.fail('No accepted prior release to recover'))
    assert recovered['status'] == 'interrupted_without_previous'
    assert ledger.active() is None


def test_committed_update_has_diagnostic_identity_matching_active_runtime(tmp_path):
    ledger = DeploymentLedger(tmp_path)
    applied = ledger.apply(release('a'), receipt, reviewer='operator')
    diagnostics = ledger.diagnostics()
    assert diagnostics['active']['deployment_id'] == applied['deployment_id']
    assert diagnostics['last_operation']['status'] == 'committed'
    assert diagnostics['last_operation']['ack']['manifest_sha256'] == 'a' * 64


def test_abrupt_process_exit_leaves_a_durable_recovery_intent(tmp_path):
    root=Path(__file__).resolve().parents[2]
    script='''
import json,os,sys
from pathlib import Path
from backend.engine.runtime_deployment import DeploymentLedger
root=Path(sys.argv[1]);ledger=DeploymentLedger(root)
def ack(value): return {'status':'ready',**value}
ledger.apply({'manifest_sha256':'a'*64,'device':'cpu'},ack,reviewer='operator')
def switch(value):
    (root/'observed.json').write_text(json.dumps(value))
    os._exit(17)
ledger.apply({'manifest_sha256':'b'*64,'device':'cpu'},switch,reviewer='operator')
'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path)],cwd=root)
    assert result.returncode==17
    assert json.loads((tmp_path/'observed.json').read_text())['manifest_sha256']=='b'*64
    ledger=DeploymentLedger(tmp_path)
    assert ledger.active()['release']['manifest_sha256']=='a'*64
    assert ledger.diagnostics()['pending']['release']['manifest_sha256']=='b'*64
    assert ledger.recover(receipt)['status']=='rolled_back'


def test_managed_restart_restores_prior_live_runtime_after_interrupted_apply(tmp_path,monkeypatch):
    from backend.tests.test_model_deployments import _fixture,_report,_approve
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.engine.managed_service import ManagedService
    client,project,source,fingerprint,models=_fixture(tmp_path)
    from backend.tests.runtime_release_fixture import real_classification_checkpoints,cohort_receipt
    real_classification_checkpoints(models)
    service=ManagedService(project['project_dir']);releases=[]
    try:
        for number,(baseline,candidate) in enumerate((('job_base','job_candidate'),('job_candidate','job_third'))):
            comparison='comparison_'+str(number)*32
            _report(project,source,fingerprint,models,incumbent=baseline,candidate=candidate,comparison_id=comparison)
            approved=_approve(client,source,comparison)
            assert approved.status_code==200,approved.text
            revision=approved.json()['revision']
            graph=get_single_segmentation_flowchart(job_id=candidate)
            for node in graph.nodes:
                if node.data.node_type=='inspection':node.data.task='classification'
            exported=build_flow_package(pipeline=graph,checkpoints={candidate:models[candidate]},output_base_dir=tmp_path/'exports',package_name=f'recovery_{number}',approved_revisions={candidate:{key:revision[key] for key in ('revision_id','job_id','task','checkpoint_sha256')}})
            cohort_receipt(exported['package_path'],graph,{candidate:models[candidate]},[source/'test'/'OK'/'ok_00.png',source/'test'/'NG'/'ng_00.png'])
            releases.append({**service.stage(exported['package_path'],project),'device':'cpu'})
        accepted=service.ledger.apply(releases[0],service.apply_runtime,reviewer='operator')
        pid=service.config['pid']
        def interrupted(value):
            service.apply_runtime(value)
            raise KeyboardInterrupt('manager interrupted after service acknowledgment')
        with pytest.raises(KeyboardInterrupt):service.ledger.apply(releases[1],interrupted,reviewer='operator')
        assert service.readback()['manifest_sha256']==releases[1]['manifest_sha256']
        recovered=ManagedService(project['project_dir'])
        assert recovered.start()['manifest_sha256']==releases[0]['manifest_sha256']
        assert recovered.config['pid']==pid
        assert recovered.ledger.active()['deployment_id']==accepted['deployment_id']
        assert recovered.state()['recovery']['last_operation']['status']=='rolled_back'
        from backend.engine.native_autostart import NativeAutostart
        def rejected_install(controller):raise RuntimeError('target registration rejected')
        monkeypatch.setattr(NativeAutostart,'install',rejected_install)
        with pytest.raises(RuntimeError,match='rejected'):recovered.activate_install()
        assert recovered.readback()['status']=='ready'
        assert recovered.readback()['manifest_sha256']==releases[0]['manifest_sha256']
    finally:ManagedService(project['project_dir']).stop()


def test_interrupted_native_install_readback_is_owned_and_never_registers_again(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from backend.engine.managed_service import ManagedService
    from backend.engine import native_autostart as native
    item=ManagedService(tmp_path);calls=[];registered=False
    item.ledger.apply(release('a'),receipt,reviewer='operator')
    def run(arguments,**kwargs):
        nonlocal registered
        calls.append(arguments)
        if arguments[1]=='print':return SimpleNamespace(returncode=0 if registered else 113,stdout='state = running\npid = 12',stderr='')
        if arguments[1]=='bootstrap':registered=True
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(native.subprocess,'run',run)
    controller=native.NativeAutostart(item,system='Darwin',home=tmp_path/'home')
    original_save=item.save
    def crash(config):raise KeyboardInterrupt('power interruption after OS registration')
    monkeypatch.setattr(item,'save',crash)
    with pytest.raises(KeyboardInterrupt):controller.install()
    monkeypatch.setattr(item,'save',original_save)
    state=controller.query()
    assert state['registered']
    assert state['install_recovery']['status']=='interrupted_registration'
    assert state['install_recovery']['manifest_sha256']=='a'*64
    result=controller.install()
    assert result['install_recovery']['status']=='registration_checked'
    assert len([call for call in calls if call[1]=='bootstrap'])==1
    assert item.config['native_label']==item.native_identity()
