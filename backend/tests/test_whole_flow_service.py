"""Central service gates with real package bytes and explicit contract reviews.

The model comparison and graph-review metadata below are synthetic controls;
they establish rejection/order/persistence, never manufacturing approval.
"""
import hashlib
import json
from pathlib import Path

import pytest
from backend.tests.test_service_release_eligibility import bound_context, _package, _change_truth
from backend.tests.test_model_deployments import _approve
from backend.engine.managed_service import ManagedService
from backend.engine.flow_package_runtime import verify_flow_package
from backend.engine.flow_provenance import pipeline_sha256
from backend.engine.inspection_service import _verify_release_policy


@pytest.fixture
def staged_review(bound_context,tmp_path,monkeypatch):
    client,project,source,models,report=bound_context
    response=_approve(client,source,report['comparison_id'])
    assert response.status_code==200,response.text
    package=_package(tmp_path,source,models,response.json()['revision'])
    graph,checkpoints=verify_flow_package(package)
    manifest=json.loads((package/'manifest.json').read_text())
    review={
        'contract':'whole_flow_review_v1','revision_id':'flowapproval_'+'a'*32,
        **{name:'a'*64 for name in ('approval_sha256','evaluation_sha256','policy_sha256','cohort_sha256')},
        'graph_sha256':pipeline_sha256(graph),'manifest_sha256':hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest(),
        'parity_receipt_sha256':hashlib.sha256((package/'parity_receipt.json').read_bytes()).hexdigest(),
        'device':'cpu','model_approval_revisions':manifest['release']['approval_revisions'],
        'runtime_cohort_qualified':True,'device_accepted':False,
    }
    service=ManagedService(project['project_dir'])
    monkeypatch.setattr(service,'whole_flow_review',lambda *args,**kwargs:dict(review))
    release=service.stage(package,project,'cpu',whole_flow_revision_id=review['revision_id'])
    return service,project,source,release,review


def test_stage_seals_review_and_reopen_binds_real_graph_and_runtime(staged_review):
    service,project,source,release,review=staged_review
    path=Path(release['release_policy'])
    assert json.loads(path.read_text())['whole_flow_review']==review
    assert release['whole_flow_review']==review
    _verify_release_policy(Path(release['package_path']),verify_flow_package(Path(release['package_path']))[1],path,device='cpu')
    service.check_live_release(release,project)
    _change_truth(project,source)
    with pytest.raises(ValueError,match='stale|truth|revoked'):service.check_live_release(release,project)
    # No new central action is authorized; the existing offline seal is unchanged.
    assert json.loads(path.read_text())['whole_flow_review']==review
    _verify_release_policy(Path(release['package_path']),verify_flow_package(Path(release['package_path']))[1],path,device='cpu')


def test_sealed_review_tamper_refused_before_runtime_state(staged_review):
    service,_,_,release,_=staged_review
    path=Path(release['release_policy']);original=path.read_text()
    for field in ('graph_sha256','manifest_sha256','parity_receipt_sha256','device',
                  'runtime_cohort_qualified','device_accepted','model_approval_revisions','approval_sha256'):
        policy=json.loads(original);value=policy['whole_flow_review'][field]
        policy['whole_flow_review'][field]=not value if type(value) is bool else [] if isinstance(value,list) else 'changed'
        path.write_text(json.dumps(policy))
        with pytest.raises(ValueError,match='whole-flow'):
            _verify_release_policy(Path(release['package_path']),verify_flow_package(Path(release['package_path']))[1],path,device='cpu')
    path.write_text(original)
    assert not (service.root/'state').exists()


def test_review_cannot_be_omitted_to_bypass_current_project_gate(tmp_path,monkeypatch):
    from backend.engine import whole_flow_approval as module
    calls=[]
    monkeypatch.setattr(module,'current_approval',lambda *a,**k:{'revision_id':'flowapproval_'+'b'*32})
    def refuse(*args,**kwargs):calls.append(args[2]);raise ValueError('Truth changed; review is stale')
    monkeypatch.setattr(module,'qualify_package',refuse)
    with pytest.raises(ValueError,match='stale'):ManagedService.whole_flow_review(tmp_path,{},'cpu')
    assert calls==['flowapproval_'+'b'*32]


def test_new_central_release_requires_review_even_without_current_selection(tmp_path,monkeypatch):
    from backend.engine import whole_flow_approval as module
    monkeypatch.setattr(module,'current_approval',lambda *a,**k:None)
    monkeypatch.setattr(module,'qualify_package',lambda *a,**k:pytest.fail('No graph was reviewed'))
    with pytest.raises(ValueError,match='Whole-flow.*review.*required'):
        ManagedService.whole_flow_review(tmp_path,{},'cpu')


def test_non_http_evidence_uses_project_split_and_restores_outer_context(tmp_path):
    from backend.tests.test_model_deployments import _fixture
    from backend.tests.runtime_release_fixture import synthetic_service_truth
    from backend.engine.release_eligibility import evidence_context
    from backend.api.routes_model_comparisons import _fingerprint
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root,scoped_split_root
    _,project,source,_,_=_fixture(tmp_path)
    synthetic_service_truth(project,[source/'test/OK/ok_00.png',source/'test/NG/ng_00.png'])
    split=Path(project['dataset_dir'])/'splits'
    expected=fingerprint_dataset(source,studio_root=Path(project['annotations_dir']),
        split_manifest=split/(hashlib.sha256(str(source).encode()).hexdigest()+'.json'))
    outer=tmp_path/'other-project-splits';token=set_request_split_root(outer)
    try:
        with evidence_context(project):assert _fingerprint(source)==expected
        assert scoped_split_root(tmp_path/'unused')==outer
    finally:reset_request_split_root(token)


def test_fleet_refuses_unbound_graph_before_network_or_ledger(staged_review,monkeypatch):
    from backend.engine.fleet import FleetRegistry
    service,project,_,release,_=staged_review
    unbound={key:value for key,value in release.items() if key!='whole_flow_review'}
    registry=FleetRegistry(project['project_dir'])
    target=registry.save_target(name='No network',url='https://example.invalid',token='fixture-token-12345678')
    monkeypatch.setattr(FleetRegistry,'client',lambda *a,**k:pytest.fail('Unbound release sent traffic'))
    with pytest.raises(ValueError,match='Whole-flow.*review.*missing|required'):
        registry.apply(target['target_id'],unbound,reviewer='fixture',project=project)
    assert registry.ledger(target['target_id']).history()==[]


def test_legacy_offline_seal_remains_recoverable_but_new_command_refuses(staged_review,monkeypatch):
    service,project,_,release,_=staged_review
    policy_path=Path(release['release_policy']);policy=json.loads(policy_path.read_text())
    del policy['whole_flow_review'];policy_path.write_text(json.dumps(policy))
    legacy={key:value for key,value in release.items() if key!='whole_flow_review'}
    # Compatibility applies only to already sealed offline recovery, not a
    # fresh central stage/apply/rollback command.
    _verify_release_policy(Path(legacy['package_path']),verify_flow_package(Path(legacy['package_path']))[1],policy_path,device='cpu')
    monkeypatch.setattr(service,'whole_flow_review',lambda *a,**k:None)
    with pytest.raises(ValueError,match='Whole-flow.*review.*missing|required'):
        service.check_live_release(legacy,project)


def test_selected_graph_review_must_equal_seal_sent_to_runtime(staged_review):
    service,project,_,release,_=staged_review
    path=Path(release['release_policy']);policy=json.loads(path.read_text())
    policy['whole_flow_review']['approval_sha256']='b'*64
    path.write_text(json.dumps(policy))
    with pytest.raises(ValueError,match='Whole-flow.*seal|policy'):
        service.check_live_release(release,project)


def test_fleet_rechecks_command_actor_after_actual_transport_ack(staged_review,monkeypatch):
    from backend.engine.fleet import FleetRegistry
    from backend.tests.test_service_release_eligibility import _Transport
    _,project,_,release,review=staged_review
    monkeypatch.setattr(ManagedService,'whole_flow_review',staticmethod(lambda *a,**k:dict(review)))
    class Accounts:
        role='reviewer'
        def project_role(self,user,identifier):return self.role
    accounts=Accounts();registry=FleetRegistry(project['project_dir'],accounts=accounts,authority_user_id='fixture-user')
    target=registry.save_target(name='Controlled ACK',url='https://example.invalid',token='fixture-token-12345678')
    class Transport(_Transport):
        def get(self,url):accounts.role='viewer';return super().get(url)
    transport=Transport();monkeypatch.setattr(registry,'client',lambda _:transport)
    with pytest.raises(ValueError,match='command authority'):
        registry.apply(target['target_id'],release,reviewer='fixture',project=project)
    assert ('POST','/agent/v1/apply') in transport.calls
    assert registry.ledger(target['target_id']).active() is None


def test_live_authority_rechecked_after_ack_and_offline_recovery_is_distinct(tmp_path,monkeypatch):
    service=ManagedService(tmp_path);candidate={'package_path':'candidate','device':'cpu'}
    calls=[]
    def check(*args,**kwargs):
        calls.append('live')
        if calls.count('live')==2:raise ValueError('reviewer revoked during runtime switch')
    monkeypatch.setattr(service,'check_live_release',check)
    monkeypatch.setattr(service,'apply_runtime',lambda release:calls.append(release['package_path']) or {'status':'ready'})
    callback=service.checked_apply({},None,candidate)
    with pytest.raises(ValueError,match='revoked'):callback(candidate)
    assert calls==['live','candidate','live']
    assert callback({'package_path':'previous_sealed','device':'cpu'})=={'status':'ready'}
    assert calls[-1]=='previous_sealed' and calls.count('live')==2


def test_bound_review_rollback_requires_live_project_authority(tmp_path,monkeypatch):
    service=ManagedService(tmp_path)
    monkeypatch.setattr(service.ledger,'history',lambda:[{'deployment_id':'history','release':{'whole_flow_review':{'revision_id':'bound'}}}])
    monkeypatch.setattr(service.ledger,'rollback',lambda *a,**k:pytest.fail('Must not mutate runtime without authority'))
    with pytest.raises(ValueError,match='live project'):service.rollback('history','reviewer')


def test_command_actor_revocation_during_ack_refuses_new_commit(tmp_path,monkeypatch):
    service=ManagedService(tmp_path);project={'id':'project'}
    candidate={'package_path':'candidate','device':'cpu'};calls=[]
    class Accounts:
        role='reviewer'
        def project_role(self,user,identifier):
            assert (user,identifier)==('command-user','project')
            return self.role
    accounts=Accounts()
    monkeypatch.setattr(service,'check_live_release',lambda *a,**k:calls.append('subject'))
    def runtime(release):
        calls.append(release['package_path']);accounts.role='viewer';return {'status':'ready'}
    monkeypatch.setattr(service,'apply_runtime',runtime)
    callback=service.checked_apply(project,accounts,candidate,authority_user_id='command-user')
    with pytest.raises(ValueError,match='command authority'):callback(candidate)
    assert calls==['subject','candidate']
    assert callback({'package_path':'previous_sealed','device':'cpu'})=={'status':'ready'}


def test_http_denial_precedes_service_creation_and_passes_session_identity(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import routes_runtime_services as routes
    app=FastAPI();app.include_router(routes.router);project={'id':'project','project_dir':str(tmp_path)}
    class Accounts:
        role='trainer'
        def project_role(self,*_):return self.role
    app.state.accounts=Accounts();calls=[]
    @app.middleware('http')
    async def account(request,call_next):
        request.state.account_user={'id':'command-user','username':'Trusted reviewer'}
        return await call_next(request)
    monkeypatch.setattr(routes,'get_current_project',lambda _:project)
    class Service:
        def __init__(self,*_):calls.append('constructed')
        def apply(self,*args,**kwargs):calls.append((args,kwargs));return {'ok':True}
        def rollback(self,*args,**kwargs):calls.append((args,kwargs));return {'ok':True}
    monkeypatch.setattr(routes,'ManagedService',Service)
    client=TestClient(app)
    body={'package_path':str(tmp_path),'reviewer':'Forged actor'}
    assert client.post('/api/runtime-services/apply',json=body).status_code==403
    assert calls==[]
    app.state.accounts.role='reviewer'
    for endpoint,payload in [('apply',body),('rollback',{'deployment_id':'record','reviewer':'Forged actor'})]:
        response=client.post('/api/runtime-services/'+endpoint,json=payload)
        assert response.status_code==200,response.text
        args,kwargs=calls[-1]
        assert 'Trusted reviewer' in args and 'Forged actor' not in args
        assert kwargs['authority_user_id']=='command-user'
