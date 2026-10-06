"""Explicit graph review controls; synthetic fixtures are not human approval."""
import importlib
import json
from pathlib import Path

import pytest
from backend.tests.test_whole_flow_evaluation import workspace, declare, real_engine, model_provider
from backend.engine import flow_evaluation, image_truth

POLICY = {'policy_id':'fixture-process','revision':1,'minimum_normal':1,'minimum_defect':1,
          'maximum_escape_rate':0.,'maximum_overkill_rate':0.,'maximum_review_rate':0.}


def setup_review(workspace, monkeypatch, unknown=False):
    p, graph, version, checkpoint = workspace
    for name, verdict in [('normal','OK'),('defect','NG')]:
        declare(image_truth,p,name,verdict,classes=['crack'] if verdict=='NG' else [])
    paths = ['normal.png','defect.png'] + (['unknown.png'] if unknown else [])
    cohort=flow_evaluation.freeze_cohort(p,version,relative_paths=paths,model_provider=model_provider(checkpoint))
    result=flow_evaluation.evaluate_flow(p,version,cohort['cohort_id'],engine=real_engine(monkeypatch),model_provider=model_provider(checkpoint))
    module=importlib.import_module('backend.engine.whole_flow_approval')
    # This synthetic contract fixture has no installed-project authority.
    monkeypatch.setattr(module,'verify_project_context',lambda _:None)
    return module,p,result


def approve(module,p,result,**changes):
    args=dict(evaluation_id=result['evaluation_id'],policy=POLICY,reviewer='Fixture reviewer',
              reason='Explicit synthetic contract control only',holdout_reviewed=True,expected_revision=None)
    args.update(changes);return module.approve_flow(p,**args)


def test_review_persists_reopens_and_truth_change_refuses_current_approval(workspace,monkeypatch):
    module,p,result=setup_review(workspace,monkeypatch)
    approved=approve(module,p,result)
    original=Path(approved['record_path']).read_bytes()
    reopened=module.current_approval(p)
    assert reopened['revision_id']==approved['revision_id'] and reopened['validity']['valid']
    assert reopened['graph_sha256']==result['graph_sha256']
    assert reopened['package_qualified'] is False and reopened['device_accepted'] is False
    declare(image_truth,p,'normal','NG',classes=['crack'])
    assert module.current_approval(p)['validity']['valid'] is False
    with pytest.raises(ValueError,match='stale|changed'):module.verified_approval(p,approved['revision_id'])
    assert Path(approved['record_path']).read_bytes()==original


@pytest.mark.parametrize('damage',['graph','checkpoint','unknown'])
def test_changed_full_subject_or_unknown_truth_cannot_authorize(workspace,monkeypatch,damage):
    module,p,result=setup_review(workspace,monkeypatch,unknown=damage=='unknown')
    if damage=='unknown':
        with pytest.raises(ValueError,match='truth|unknown'):approve(module,p,result)
        return
    approved=approve(module,p,result)
    if damage=='checkpoint':Path(result['models'][0]['checkpoint_path']).write_bytes(b'changed')
    else:
        path=Path(p['project_dir'])/'flowcharts/versions'/f"{result['version_id']}.json"
        saved=json.loads(path.read_text());saved['pipeline']['nodes'][1]['data']['params']['threshold']=.9
        path.write_text(json.dumps(saved))
    with pytest.raises(ValueError,match='stale|changed'):module.verified_approval(p,approved['revision_id'])


def test_stale_selection_refuses_and_historical_selection_is_rechecked(workspace,monkeypatch):
    module,p,result=setup_review(workspace,monkeypatch)
    first=approve(module,p,result)
    with pytest.raises(ValueError,match='selection changed'):approve(module,p,result)
    second=approve(module,p,result,expected_revision=first['revision_id'])
    with pytest.raises(ValueError,match='selection changed'):
        module.select_approval(p,first['revision_id'],expected_revision=first['revision_id'],reviewer='Reviewer',reason='Reviewed rollback fixture')
    selected=module.select_approval(p,first['revision_id'],expected_revision=second['revision_id'],reviewer='Reviewer',reason='Reviewed rollback fixture')
    assert selected['revision_id']==first['revision_id']
    declare(image_truth,p,'normal','NG',classes=['crack'])
    with pytest.raises(ValueError):
        module.select_approval(p,second['revision_id'],expected_revision=first['revision_id'],reviewer='Reviewer',reason='Do not accept stale rollback')
    assert module.current_approval(p)['revision_id']==first['revision_id']


@pytest.mark.parametrize('change',[{'holdout_reviewed':False},{'reason':'short'},
    {'policy':{**POLICY,'maximum_escape_rate':float('nan')}},{'policy':{**POLICY,'minimum_normal':True}},
    {'policy':{**POLICY,'revision':0}},{'policy':{**POLICY,'unreviewed_extra':True}}])
def test_explicit_review_and_finite_complete_policy_required(workspace,monkeypatch,change):
    module,p,result=setup_review(workspace,monkeypatch)
    with pytest.raises(ValueError):approve(module,p,result,**change)
    assert module.current_approval(p) is None


def test_reviewer_membership_is_rechecked_after_revocation(workspace,monkeypatch):
    module,p,result=setup_review(workspace,monkeypatch)
    class Accounts:
        role='reviewer'
        def project_role(self,user,project):return self.role
    accounts=Accounts()
    approved=approve(module,p,result,authority_user_id='reviewer-id',accounts=accounts)
    assert module.verified_approval(p,approved['revision_id'],accounts=accounts)
    accounts.role='trainer'
    with pytest.raises(ValueError,match='authority'):module.verified_approval(p,approved['revision_id'],accounts=accounts)
    with pytest.raises(ValueError,match='authority'):module.verified_approval(p,approved['revision_id'])


def test_named_policy_revision_is_immutable(workspace,monkeypatch):
    module,p,result=setup_review(workspace,monkeypatch)
    first=approve(module,p,result)
    changed={**POLICY,'maximum_review_rate':.2}
    with pytest.raises(ValueError,match='new revision'):
        approve(module,p,result,policy=changed,expected_revision=first['revision_id'])
    changed['revision']=2
    assert approve(module,p,result,policy=changed,expected_revision=first['revision_id'])['policy']==changed


def test_http_role_and_trusted_actor_are_used_for_graph_review(workspace,monkeypatch):
    module,p,result=setup_review(workspace,monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import routes_flow_evaluation as routes
    class Accounts:
        role='trainer'
        def project_role(self,*_):return self.role
    app=FastAPI();app.state.accounts=Accounts();app.include_router(routes.router)
    @app.middleware('http')
    async def account(request,call_next):
        request.state.account_user={'id':'trusted-user','username':'Trusted reviewer'}
        return await call_next(request)
    monkeypatch.setattr(routes,'get_current_project',lambda _:p)
    client=TestClient(app)
    body=dict(evaluation_id=result['evaluation_id'],policy=POLICY,reviewer='Forged actor',
              reason='Explicit synthetic API control only',holdout_reviewed=True,expected_revision=None)
    assert client.post('/api/flow-evaluations/approvals',json=body).status_code==403
    app.state.accounts.role='reviewer'
    saved=client.post('/api/flow-evaluations/approvals',json=body)
    assert saved.status_code==200,saved.text
    assert saved.json()['reviewer']=='Trusted reviewer'
    app.state.accounts.role='trainer'
    assert client.get('/api/flow-evaluations/approvals/active').json()['validity']['valid'] is False


@pytest.mark.parametrize('damage',['none','graph','cohort','receipt_changed','models'])
def test_package_subject_and_parity_binding_controls(workspace,monkeypatch,tmp_path,damage):
    module,p,result=setup_review(workspace,monkeypatch)
    approved=approve(module,p,result)
    from backend.engine import flow_package_runtime,release_eligibility,runtime_release_evidence
    import hashlib
    from backend.engine.flowchart_engine import FlowchartPipeline
    package=tmp_path/'qualified-package';package.mkdir()
    graph=FlowchartPipeline.model_validate(result['pipeline'])
    if damage=='graph':graph.nodes[1].data.params['threshold']=.9
    checkpoints={row['job_id']:Path(row['checkpoint_path']) for row in result['models']}
    if damage=='models':checkpoints={}
    monkeypatch.setattr(flow_package_runtime,'verify_flow_package',lambda _: (graph,checkpoints))
    calls=[]
    monkeypatch.setattr(release_eligibility,'authorize_release_action',lambda *args,**kwargs:calls.append(kwargs['action']) or ['model-control'])
    inputs=[row['input_sha256'] for row in result['records']]
    if damage=='cohort':inputs[0]='f'*64
    raw=json.dumps({'images':[{'image_sha256':value} for value in inputs]}).encode()
    (package/'parity_receipt.json').write_bytes(raw)
    evidence={'receipt_kind':'flow_parity','manifest_sha256':'d'*64,'receipt_sha256':hashlib.sha256(raw).hexdigest()}
    def verify(*args,**kwargs):
        if damage=='receipt_changed':(package/'parity_receipt.json').write_bytes(b'changed')
        return evidence
    monkeypatch.setattr(runtime_release_evidence,'verify_release_evidence',verify)
    if damage!='none':
        with pytest.raises(ValueError):module.qualify_package(p,package,approved['revision_id'],device='cpu')
    else:
        qualified=module.qualify_package(p,package,approved['revision_id'],device='cpu')
        assert qualified['runtime_cohort_qualified'] is True and qualified['device_accepted'] is False
        assert calls==['stage','stage']
        assert (package/'parity_receipt.json').read_bytes()==raw
