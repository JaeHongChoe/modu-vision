"""Durable fleet rollout orchestration against a simulated authenticated transport."""
from contextlib import nullcontext
import json
from pathlib import Path
import httpx
import pytest
from backend.engine.fleet import FleetRegistry


@pytest.fixture
def fleet(tmp_path,monkeypatch):
    store=FleetRegistry(tmp_path);project={'project_dir':str(tmp_path)}
    ids=[store.save_target(name=f'Cell {i}',url=f'https://cell{i}.invalid',token='fixture-secret-123456789')['target_id'] for i in range(4)]
    package=tmp_path/'package';package.mkdir();digest='a'*64
    policy=tmp_path/'policy.json';policy.write_text(json.dumps({'manifest_sha256':digest}))
    release={'package_path':str(package),'manifest_sha256':digest,'device':'cpu','release_policy':str(policy)}
    runtime={identifier:{'status':'idle'} for identifier in ids};offline=set();writes=[];gates=[]
    def handler(request):
        identifier=ids[int(request.url.host[4])]
        if identifier in offline:raise httpx.ConnectError('simulated offline',request=request)
        if request.method=='POST':
            writes.append((identifier,request.url.path))
            if request.url.path=='/agent/v1/releases':return httpx.Response(200,json={'status':'staged','manifest_sha256':request.headers['X-Manifest-SHA256']})
            if request.url.path=='/agent/v1/apply':runtime[identifier]={'status':'ready',**json.loads(request.content)}
        return httpx.Response(200,json=runtime[identifier])
    monkeypatch.setattr(FleetRegistry,'client',lambda self,identifier:httpx.Client(base_url=self.target(identifier)['url'],transport=httpx.MockTransport(handler)))
    import backend.engine.release_eligibility as eligibility
    monkeypatch.setattr(eligibility,'release_authority',lambda project:nullcontext())
    monkeypatch.setattr(eligibility,'authorize_release_action',lambda package,project,action:gates.append(action))
    # These are simulated transport/ledger controls. Actual graph/truth/model
    # qualification is exercised in test_whole_flow_service and real native
    # fleet cases, rather than granted by this synthetic mock package.
    from backend.engine.managed_service import ManagedService
    monkeypatch.setattr(ManagedService,'check_live_release',
        lambda self,release,project,**kwargs:gates.append('whole_flow:'+kwargs['action']))
    monkeypatch.setattr('backend.engine.flow_package_runtime.verify_flow_package',lambda package:(None,{}))
    monkeypatch.setattr('backend.engine.inspection_service._verify_release_policy',lambda *args,**kwargs:None)
    monkeypatch.setattr('backend.engine.fleet.package_archive',lambda package:b'fixture-package-bytes')
    return store,project,ids,release,runtime,offline,writes,gates


def create(fleet):
    store,project,ids,release,*_=fleet
    assert hasattr(store,'create_rollout'),'Durable fleet rollout contract is missing'
    return store.create_rollout(release,target_ids=ids,canary_target_ids=[ids[0]],batch_size=2,reviewer='fixture QA',project=project)


@pytest.mark.parametrize('step,bad',[
    ('stage',{}),('stage',{'status':'ready','manifest_sha256':'a'*64}),
    ('stage',{'status':'staged','manifest_sha256':'b'*64}),
    ('apply',{}),('apply',{'status':'ready','manifest_sha256':'b'*64,'device':'cpu'}),
    ('apply',{'status':'ready','manifest_sha256':'a'*64,'device':'cuda:0'}),
])
def test_release_post_ack_must_match_even_when_final_readback_is_correct(fleet,monkeypatch,step,bad):
    store,project,ids,release,runtime,offline,writes,gates=fleet
    def handler(request):
        writes.append((ids[0],request.url.path))
        if request.url.path=='/agent/v1/releases':
            return httpx.Response(200,json=bad if step=='stage' else {'status':'staged','manifest_sha256':release['manifest_sha256']})
        if request.url.path=='/agent/v1/apply':
            return httpx.Response(200,json=bad if step=='apply' else {'status':'ready','manifest_sha256':release['manifest_sha256'],'device':'cpu'})
        return httpx.Response(200,json={'status':'ready','manifest_sha256':release['manifest_sha256'],'device':'cpu'})
    monkeypatch.setattr(store,'client',lambda identifier:httpx.Client(base_url=store.target(identifier)['url'],transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError,match='acknowledgment'):
        store.apply(ids[0],release,reviewer='QA',project=project)
    assert store.ledger(ids[0]).active() is None
    if step=='stage':assert not any(path=='/agent/v1/apply' for _,path in writes)


def test_canary_confirmation_precedes_batches_and_plan_reopens(fleet):
    store,project,ids,release,runtime,offline,writes,gates=fleet
    plan=create(fleet)
    assert not writes and plan['status']=='planned'
    plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='fixture QA',project=project)
    assert plan['status']=='waiting_canary_confirmation'
    assert len([entry for entry in writes if entry[1]=='/agent/v1/apply'])==1
    reopened=FleetRegistry(store.root.parent)
    before=len(writes)
    waiting=reopened.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='fixture QA',project=project)
    assert waiting['status']=='waiting_canary_confirmation' and len(writes)==before
    plan=reopened.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='fixture QA',project=project,confirm_canary=True)
    assert plan['status']=='running' and sum(row['status']=='applied' for row in plan['targets'])==3
    plan=reopened.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='fixture QA',project=project)
    assert plan['status']=='completed' and all(row['status']=='applied' for row in plan['targets'])
    assert all(store.ledger(identifier).active()['release']['manifest_sha256']==release['manifest_sha256'] for identifier in ids)
    assert len(gates)>=8


def test_offline_target_pauses_batch_and_resume_requires_fresh_canary_readback(fleet):
    store,project,ids,release,runtime,offline,writes,gates=fleet
    plan=create(fleet)
    plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    offline.add(ids[1])
    plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project,confirm_canary=True)
    assert plan['status']=='paused' and plan['targets'][1]['status']=='offline'
    assert store.ledger(ids[2]).active() is None
    offline.remove(ids[1]);runtime[ids[0]]['manifest_sha256']='b'*64
    plan=FleetRegistry(store.root.parent).resume_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert plan['status']=='paused' and 'readback' in plan['pause_reason']
    runtime[ids[0]]['manifest_sha256']=release['manifest_sha256']
    plan=store.resume_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert plan['status']=='running'
    plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert plan['targets'][1]['status']=='applied'


def test_current_eligibility_rejection_sends_no_release_and_stops_later_targets(fleet,monkeypatch):
    store,project,ids,release,runtime,offline,writes,gates=fleet
    plan=create(fleet)
    plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    before=len(writes)
    def revoked(*args,**kwargs):raise ValueError('Approval revoked since canary')
    monkeypatch.setattr('backend.engine.release_eligibility.authorize_release_action',revoked)
    plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project,confirm_canary=True)
    assert plan['status']=='paused' and 'revoked' in plan['pause_reason']
    assert len(writes)==before
    assert store.ledger(ids[1]).active() is None and store.ledger(ids[2]).active() is None


def test_explicit_pause_and_revision_conflict_never_deploy(fleet):
    store,project,ids,release,runtime,offline,writes,gates=fleet
    plan=create(fleet)
    paused=store.pause_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',reason='Review next shift')
    assert paused['status']=='paused' and not writes
    with pytest.raises(ValueError,match='revision'):store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert not writes
    with pytest.raises(ValueError,match='resume'):store.advance_rollout(plan['plan_id'],expected_revision=paused['revision'],reviewer='QA',project=project)


def test_rollout_rollback_reuses_target_history_and_current_eligibility(fleet):
    store,project,ids,release,runtime,offline,writes,gates=fleet
    old={**release,'manifest_sha256':'c'*64}
    Path(old['release_policy']).write_text(json.dumps({'manifest_sha256':old['manifest_sha256']}))
    first=store.apply(ids[0],old,reviewer='QA',project=project)
    Path(release['release_policy']).write_text(json.dumps({'manifest_sha256':release['manifest_sha256']}))
    plan=create(fleet)
    plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    # The old target policy remains separately hash-bound when selected for rollback.
    Path(old['release_policy']).write_text(json.dumps({'manifest_sha256':old['manifest_sha256']}))
    rolled=store.rollback_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert rolled['status']=='rolled_back'
    active=store.ledger(ids[0]).active()
    assert active['restored_from']==first['deployment_id'] and runtime[ids[0]]['manifest_sha256']=='c'*64
    assert 'rollback' in gates


def test_http_rollout_creation_exposes_durable_plan_and_forbids_recipe_actor_fields(fleet,monkeypatch):
    from backend.api import routes_fleet as routes
    from backend.main import create_app
    from fastapi.testclient import TestClient
    store,project,ids,release,*_=fleet
    app=create_app(project_dir=str(store.root.parent/'projects'))
    client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    monkeypatch.setattr(routes,'scope',lambda request:(store,project))
    monkeypatch.setattr(routes.ManagedService,'stage',lambda *args,**kwargs:release)
    response=client.post('/api/fleet/rollouts',json={'target_ids':ids,'canary_target_ids':[ids[0]],'batch_size':2,'package_path':release['package_path'],'reviewer':'QA'})
    assert response.status_code==200,response.text
    plan=response.json();assert plan['status']=='planned'
    read=client.get('/api/fleet/rollouts/'+plan['plan_id'])
    assert read.status_code==200 and read.json()['release']['manifest_sha256']==release['manifest_sha256']
    assert 'fixture-secret' not in read.text
    malformed=client.post('/api/fleet/rollouts/'+plan['plan_id']+'/advance',json={'expected_revision':plan['revision'],'reviewer':'QA','actor_role':'owner'})
    assert malformed.status_code==422


def test_partial_rollback_resume_keeps_restored_targets_and_cannot_advance_deployment(fleet):
    store,project,ids,release,runtime,offline,writes,gates=fleet
    for index,identifier in enumerate(ids[:3]):
        policy=store.root.parent/f'old-policy-{index}.json';policy.write_text(json.dumps({'manifest_sha256':'c'*64}))
        store.apply(identifier,{**release,'manifest_sha256':'c'*64,'release_policy':str(policy)},reviewer='QA',project=project)
    plan=store.create_rollout(release,target_ids=ids[:3],canary_target_ids=[ids[0]],batch_size=2,reviewer='QA',project=project)
    plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project,confirm_canary=True)
    offline.add(ids[1])
    plan=store.rollback_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert plan['status']=='paused' and plan['targets'][2]['status']=='rolled_back'
    offline.remove(ids[1]);before=len(writes)
    plan=FleetRegistry(store.root.parent).resume_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert plan['status']=='rolling_back' and plan['targets'][2]['status']=='rolled_back'
    assert len(writes)==before
    with pytest.raises(ValueError,match='rollback|rolling back'):
        store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    plan=store.rollback_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert plan['status']=='rolled_back' and all(row['status']=='rolled_back' for row in plan['targets'])
    assert len(writes)==before+4  # two releases plus two apply requests; restored target is skipped


def test_committed_rollback_after_interruption_is_adopted_only_with_live_readback(fleet,monkeypatch):
    store,project,ids,release,runtime,offline,writes,gates=fleet
    policy=store.root.parent/'old-policy.json';policy.write_text(json.dumps({'manifest_sha256':'c'*64}))
    original=store.apply(ids[0],{**release,'manifest_sha256':'c'*64,'release_policy':str(policy)},reviewer='QA',project=project)
    plan=create(fleet);plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    real_save=store._save_rollout
    class Crash(BaseException):pass
    def interrupt(plan,event,reviewer):
        if event=='target_rollback_verified':raise Crash()
        return real_save(plan,event,reviewer)
    monkeypatch.setattr(store,'_save_rollout',interrupt)
    with pytest.raises(Crash):store.rollback_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    saved=store.rollout(plan['plan_id']);before=len(writes)
    assert store.ledger(ids[0]).active()['restored_from']==original['deployment_id']
    reopened=FleetRegistry(store.root.parent)
    resumed=reopened.resume_rollout(saved['plan_id'],expected_revision=saved['revision'],reviewer='QA',project=project)
    assert resumed['status']=='rolled_back' and resumed['targets'][0]['status']=='rolled_back'
    assert len(writes)==before and resumed['targets'][0]['readback']['matches_active']


def test_canary_cohort_cannot_exceed_reviewed_batch_bound(fleet):
    store,project,ids,release,runtime,offline,writes,gates=fleet
    with pytest.raises(ValueError,match='canary|Canary'):
        store.create_rollout(release,target_ids=ids,canary_target_ids=ids[:2],batch_size=1,reviewer='QA',project=project)
    assert not writes and not store.rollouts()


def test_unpublished_forward_receipt_is_adopted_on_resume_before_bounded_rollback(fleet,monkeypatch):
    store,project,ids,release,runtime,offline,writes,gates=fleet
    for index,identifier in enumerate(ids[:2]):
        policy=store.root.parent/f'old-policy-{index}.json';policy.write_text(json.dumps({'manifest_sha256':'c'*64}))
        store.apply(identifier,{**release,'manifest_sha256':'c'*64,'release_policy':str(policy)},reviewer='QA',project=project)
    plan=store.create_rollout(release,target_ids=ids[:2],canary_target_ids=[ids[0]],batch_size=1,reviewer='QA',project=project)
    plan=store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    real_apply=store.apply
    class Crash(BaseException):pass
    def unpublished(identifier,*args,**kwargs):
        receipt=real_apply(identifier,*args,**kwargs)
        if identifier==ids[1]:raise Crash()
        return receipt
    monkeypatch.setattr(store,'apply',unpublished)
    with pytest.raises(Crash):store.advance_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project,confirm_canary=True)
    reopened=FleetRegistry(store.root.parent);plan=reopened.rollout(plan['plan_id']);before=len(writes)
    with pytest.raises(ValueError,match='resume'):reopened.rollback_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert len(writes)==before
    plan=reopened.resume_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert plan['targets'][1]['deployment_id']==reopened.ledger(ids[1]).active()['deployment_id']
    assert plan['targets'][1]['readback']['matches_active'] and len(writes)==before
    plan=reopened.rollback_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert plan['status']=='rolling_back' and runtime[ids[1]]['manifest_sha256']=='c'*64
    plan=reopened.rollback_rollout(plan['plan_id'],expected_revision=plan['revision'],reviewer='QA',project=project)
    assert plan['status']=='rolled_back' and all(runtime[identifier]['manifest_sha256']=='c'*64 for identifier in ids[:2])
