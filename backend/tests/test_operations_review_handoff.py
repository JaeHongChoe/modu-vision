"""Manual cycle review bindings; authored controls are not model-quality proof."""
from pathlib import Path
import pytest
from backend.tests.test_model_deployments import _fixture,_report,_approve
from backend.engine.model_operations import OperationsStore


@pytest.fixture
def context(tmp_path):
    client,project,source,fingerprint,models=_fixture(tmp_path)
    report=_report(project,source,fingerprint,models)
    return client,project,source,models,report


def bind(context):
    from backend.engine.operations_review import freeze_subject
    _,project,_,_,report=context
    subject=freeze_subject(project,{'task':'classification','parent_job_id':'job_base',
        'labelset_id':'default'},'job_candidate',{'comparison':report})
    store=OperationsStore(project['project_dir']);row={'cycle_id':'cycle_'+'a'*32,
        'status':'awaiting_approval','created_at':1,'policy_revision':1,'result':{'review_handoff':subject}}
    store.save(row);return row


def test_manual_handoff_reopens_current_subject_then_observes_explicit_approval(context):
    client,project,source,_,report=context;row=bind(context)
    endpoint='/api/model-operations/cycles/'+row['cycle_id']+'/review-handoff'
    first=client.get(endpoint);assert first.status_code==200,first.text
    assert first.json()['state']=='awaiting_human_review'
    assert first.json()['next_step']==4 and first.json()['automatic_action']=='none'
    assert _approve(client,source,report['comparison_id']).status_code==200
    reopened=client.get(endpoint).json()
    assert reopened['state']=='model_approval_current' and reopened['next_step']==6
    assert reopened['service_applied'] is False and reopened['device_accepted'] is False
    assert OperationsStore(project['project_dir']).get(row['cycle_id'])==row


@pytest.mark.parametrize('changed',['source','report','checkpoint','labelset'])
def test_changed_manual_subject_cannot_be_shown_as_ready(context,changed):
    from backend.engine.operations_review import read_handoff
    _,project,source,models,report=context;row=bind(context)
    if changed=='source':project={**project,'source_dataset_dir':str(source/'other')}
    elif changed=='labelset':project={**project,'active_labelset_id':'another'}
    elif changed=='checkpoint':models['job_candidate'].write_bytes(b'changed')
    else:(Path(project['reports_dir'])/'model_comparisons'/f"{report['comparison_id']}.json").write_bytes(b'{}')
    result=read_handoff(project,row['cycle_id'])
    assert result['state']=='revalidation_required' and result['next_step'] is None
    assert result['automatic_action']=='none'


def test_unbound_legacy_cycle_and_specialized_evidence_are_not_inferred(context):
    from backend.engine.operations_review import read_handoff,freeze_subject
    _,project,_,_,_=context;store=OperationsStore(project['project_dir'])
    row={'cycle_id':'cycle_'+'b'*32,'status':'awaiting_approval','created_at':1,'result':{'candidate_job_id':'job_candidate'}}
    store.save(row)
    assert read_handoff(project,row['cycle_id'])['state']=='unbound_history'
    assert freeze_subject(project,{'task':'ocr'},'job_candidate',{}) is None


def test_malformed_subject_and_report_scope_refuse_with_revalidation_state(context):
    from backend.engine.operations_review import read_handoff
    from backend.engine.image_truth import digest
    import json
    client,project,source,_,report=context;row=bind(context);store=OperationsStore(project['project_dir'])
    row['result']['review_handoff']='invalid persisted subject';store.save(row)
    endpoint='/api/model-operations/cycles/'+row['cycle_id']+'/review-handoff'
    response=client.get(endpoint)
    assert response.status_code==200 and response.json()['state']=='revalidation_required'
    row=bind(context);subject=row['result']['review_handoff']
    report['project_id']='different-project'
    file=Path(project['reports_dir'])/'model_comparisons'/(report['comparison_id']+'.json')
    file.write_text(json.dumps(report))
    from backend.api.routes_model_deployments import _sha256
    subject['comparison_sha256']=_sha256(file)
    subject['subject_sha256']=digest({k:v for k,v in subject.items() if k!='subject_sha256'})
    store.save(row)
    checked=read_handoff(project,row['cycle_id'])
    assert checked['state']=='revalidation_required' and checked['next_step'] is None
    assert 'different project' in checked['reasons'][0]


def prepare_context(context):
    """Authored approval controls only; not learned-model quality evidence."""
    from backend.engine.model_operations import configure_program
    from backend.api.routes_flowchart import save_pipeline
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from types import SimpleNamespace
    client,project,source,models,report=context
    row=bind(context)
    assert _approve(client,source,report['comparison_id']).status_code==200
    configure_program(project,{'task':'classification','parent_job_id':'job_base','reviewer':'Authored preparation control'})
    pipeline=get_single_segmentation_flowchart('job_base')
    next(n for n in pipeline.nodes if n.data.node_type=='inspection').data.task='classification'
    request=SimpleNamespace(state=SimpleNamespace(scoped_project=project,account_user=None))
    saved=save_pipeline(pipeline,recipe_task='classification',source_dataset_path=str(source),request=request)
    from backend.engine.flow_provenance import pipeline_sha256
    return row,saved,pipeline_sha256(pipeline),request


def test_manual_candidate_preparation_creates_inactive_graph_and_reopens_exact_subject(context):
    from backend.engine.operations_review import prepare_candidate_flow,read_handoff
    from backend.api.routes_flowchart import get_saved_pipeline_version,get_active_pipeline
    from backend.engine.model_operations import OperationsStore
    client,project,source,models,report=context
    row,saved,graph_hash,request=prepare_context(context)
    active=Path(project['project_dir'])/'flowcharts'/'active.json';before=active.read_bytes()
    before_models={k:v.read_bytes() for k,v in models.items()}
    result=prepare_candidate_flow(project,row['cycle_id'],source_version_id=saved['version_id'],expected_graph_sha256=graph_hash,
        expected_subject_sha256=row['result']['review_handoff']['subject_sha256'],reviewer='Authored control',reason='Prepare an inactive candidate graph for separate whole-flow review')
    assert result['flow_activated'] is False and result['service_applied'] is False
    assert result['source_version_id']==saved['version_id'] and result['candidate_job_id']=='job_candidate'
    candidate=get_saved_pipeline_version(result['version_id'],request=request)
    assert next(n for n in candidate.nodes if n.data.node_type=='inspection').data.model_job_id=='job_candidate'
    assert next(n for n in get_active_pipeline(str(source),request).nodes if n.data.node_type=='inspection').data.model_job_id=='job_base'
    assert active.read_bytes()==before and before_models=={k:v.read_bytes() for k,v in models.items()}
    reopened=read_handoff(project,row['cycle_id'])
    assert reopened['prepared_flow']==result and reopened['state']=='model_approval_current'
    assert OperationsStore(project['project_dir']).get(row['cycle_id'])['status']=='awaiting_flow_review'
    assert prepare_candidate_flow(project,row['cycle_id'],source_version_id=saved['version_id'],expected_graph_sha256=graph_hash,
        expected_subject_sha256=row['result']['review_handoff']['subject_sha256'],reviewer='Authored control',reason='Replay the same independent candidate preparation')==result
    version=Path(project['project_dir'])/'flowcharts'/'versions'/(result['version_id']+'.json')
    version.write_text('{}')
    assert read_handoff(project,row['cycle_id'])['state']=='revalidation_required'


@pytest.mark.parametrize('changed',['policy','subject','source_graph','checkpoint','holdout','no_incumbent'])
def test_stale_manual_candidate_preparation_does_not_change_recipe_or_save_version(context,changed):
    from backend.engine.operations_review import prepare_candidate_flow
    from backend.engine.model_operations import OperationsStore
    import json
    client,project,source,models,report=context
    row,saved,graph_hash,request=prepare_context(context)
    store=OperationsStore(project['project_dir']);subject=row['result']['review_handoff']['subject_sha256']
    if changed=='policy':
        policy=store.policy();policy['revision']+=1;store.save_policy(policy)
    elif changed=='subject':subject='0'*64
    elif changed=='checkpoint':models['job_candidate'].write_bytes(b'changed')
    elif changed=='holdout':next(source.rglob('*.png')).write_bytes(b'changed')
    elif changed=='source_graph':graph_hash='0'*64
    else:
        version=Path(project['project_dir'])/'flowcharts'/'versions'/(saved['version_id']+'.json')
        data=json.loads(version.read_text())
        for node in data['pipeline']['nodes']:
            if node['data']['node_type']=='inspection':node['data']['model_job_id']='job_third'
        version.write_text(json.dumps(data))
        from backend.engine.flowchart_engine import FlowchartPipeline
        from backend.engine.flow_provenance import pipeline_sha256
        graph_hash=pipeline_sha256(FlowchartPipeline.model_validate(data['pipeline']))
    base=Path(project['project_dir'])/'flowcharts';before={p:p.read_bytes() for p in base.rglob('*.json')}
    with pytest.raises(ValueError):
        prepare_candidate_flow(project,row['cycle_id'],source_version_id=saved['version_id'],expected_graph_sha256=graph_hash,
            expected_subject_sha256=subject,reviewer='Authored control',reason='Separate candidate graph preparation must remain inactive')
    assert before=={p:p.read_bytes() for p in base.rglob('*.json')}


def test_candidate_preparation_endpoint_requires_current_approval_and_exact_hashes(context):
    client,project,source,models,report=context
    row,saved,graph_hash,request=prepare_context(context)
    endpoint='/api/model-operations/cycles/'+row['cycle_id']+'/prepare-flow'
    payload={'source_version_id':saved['version_id'],'expected_graph_sha256':graph_hash,
        'expected_subject_sha256':row['result']['review_handoff']['subject_sha256'],
        'reviewer':'Authored HTTP control','reason':'Explicit inactive candidate preparation for separate process review'}
    assert client.post(endpoint,json={**payload,'expected_graph_sha256':'invalid'}).status_code==422
    assert client.post(endpoint,json={**payload,'expected_subject_sha256':'0'*64}).status_code==409
    created=client.post(endpoint,json=payload);assert created.status_code==200,created.text
    reopened=client.get('/api/model-operations/cycles/'+row['cycle_id']+'/review-handoff').json()
    assert reopened['prepared_flow']==created.json() and reopened['next_step']==5
    assert created.json()['flow_activated'] is False and created.json()['service_applied'] is False


def test_candidate_preparation_save_failure_removes_only_its_new_inactive_version(context,monkeypatch):
    from backend.engine.operations_review import prepare_candidate_flow
    from backend.engine.model_operations import OperationsStore
    _,project,_,_,_=context;row,saved,graph_hash,_=prepare_context(context)
    root=Path(project['project_dir'])/'flowcharts';before={p:p.read_bytes() for p in root.rglob('*.json')}
    original=OperationsStore(project['project_dir']).get(row['cycle_id'])
    monkeypatch.setattr(OperationsStore,'save',lambda *args:(_ for _ in ()).throw(OSError('Controlled cycle receipt write failure')))
    with pytest.raises(OSError,match='receipt write failure'):
        prepare_candidate_flow(project,row['cycle_id'],source_version_id=saved['version_id'],expected_graph_sha256=graph_hash,
            expected_subject_sha256=row['result']['review_handoff']['subject_sha256'],reviewer='Authored control',reason='Independent inactive graph write failure control')
    assert before=={p:p.read_bytes() for p in root.rglob('*.json')}
    assert OperationsStore(project['project_dir']).get(row['cycle_id'])==original


def delivery_context(context,monkeypatch):
    from backend.engine.operations_review import prepare_candidate_flow
    from backend.engine import whole_flow_approval
    from backend.engine.managed_service import ManagedService
    _,project,_,_,_=context;row,saved,graph_hash,_=prepare_context(context)
    prepared=prepare_candidate_flow(project,row['cycle_id'],source_version_id=saved['version_id'],expected_graph_sha256=graph_hash,
        expected_subject_sha256=row['result']['review_handoff']['subject_sha256'],reviewer='Authored control',reason='Prepare exact immutable candidate for current delivery readback')
    review={'version_id':prepared['version_id'],'graph_sha256':prepared['graph_sha256'],'revision_id':'flowapproval_'+'4'*32,
        'record_sha256':'5'*64,'validity':{'valid':True,'reasons':[]}}
    release={'whole_flow_review':{'revision_id':review['revision_id'],'graph_sha256':prepared['graph_sha256']},'manifest_sha256':'6'*64,'device':'cpu',
        'package_path':'/owned/qualified/package','release_policy':'/owned/qualified/policy.json'}
    ack={'status':'ready','manifest_sha256':'6'*64,'device':'cpu','package_path':release['package_path'],'release_policy':release['release_policy']}
    active={'deployment_id':'7'*32,'release':release,'ack':ack}
    runtime=dict(ack);calls=[]
    monkeypatch.setattr(whole_flow_approval,'current_approval',lambda project,accounts=None:(calls.append(('authority',accounts)) or review))
    class Ledger:
        def active(self):return active
        def diagnostics(self):return {'pending':None}
    monkeypatch.setattr(ManagedService,'__init__',lambda self,root:setattr(self,'ledger',Ledger()))
    monkeypatch.setattr(ManagedService,'check_live_release',lambda self,release,project,**kw:calls.append(('qualify',kw.get('accounts'))))
    monkeypatch.setattr(ManagedService,'readback',lambda self:runtime)
    root=Path(project['project_dir'])/'runtime_service';root.mkdir(exist_ok=True)
    (root/'service.json').write_text('{}');(root/'runtime_deployments.sqlite3').write_bytes(b'Owned service presence control; ledger is mocked')
    return row,prepared,review,active,runtime,calls


def test_delivery_readback_observes_current_graph_and_service_without_mutating_cycle(context,monkeypatch):
    from backend.engine.operations_review import read_handoff
    from backend.engine.model_operations import OperationsStore
    row,prepared,review,active,runtime,calls=delivery_context(context,monkeypatch)
    _,project,_,models,_=context;before=OperationsStore(project['project_dir']).get(row['cycle_id']);weights={k:v.read_bytes() for k,v in models.items()};accounts=object()
    result=read_handoff(project,row['cycle_id'],accounts=accounts);delivery=result['delivery']
    assert delivery['whole_flow_current'] is True and delivery['whole_flow_revision_id']==review['revision_id']
    assert delivery['service_application_recorded'] is True and delivery['service_runtime_ready'] is True
    assert delivery['service_deployment_id']==active['deployment_id'] and delivery['device_accepted'] is False
    assert result['next_step']==6 and result['automatic_action']=='none' and result['service_applied'] is False
    assert calls==[('authority',accounts),('qualify',accounts)]
    assert before==OperationsStore(project['project_dir']).get(row['cycle_id']) and weights=={k:v.read_bytes() for k,v in models.items()}


@pytest.mark.parametrize('change',['different_version','different_graph','invalid_review','wrong_release_review','wrong_ack','wrong_live_manifest','stopped','pending','release_changed','corrupt_ledger'])
def test_delivery_readback_refuses_inferred_healthy_application(context,monkeypatch,change):
    from backend.engine.operations_review import read_handoff
    from backend.engine.managed_service import ManagedService
    row,prepared,review,active,runtime,calls=delivery_context(context,monkeypatch)
    if change=='different_version':review['version_id']='8'*32
    elif change=='different_graph':review['graph_sha256']='9'*64
    elif change=='invalid_review':review['validity']={'valid':False,'reasons':['Truth or reviewer authority changed']}
    elif change=='wrong_release_review':active['release']['whole_flow_review']['revision_id']='flowapproval_'+'0'*32
    elif change=='wrong_ack':active['ack']['manifest_sha256']='0'*64
    elif change=='wrong_live_manifest':runtime['manifest_sha256']='0'*64
    elif change=='stopped':runtime['status']='stopped'
    elif change=='pending':
        from types import SimpleNamespace
        monkeypatch.setattr(ManagedService,'__init__',lambda self,root:setattr(self,'ledger',SimpleNamespace(active=lambda:active,diagnostics=lambda:{'pending':{'status':'needs_review'}})))
    elif change=='release_changed':monkeypatch.setattr(ManagedService,'check_live_release',lambda *a,**kw:(_ for _ in ()).throw(ValueError('Release bytes or authority changed')))
    else:
        import sqlite3
        monkeypatch.setattr(ManagedService,'__init__',lambda *a:(_ for _ in ()).throw(sqlite3.DatabaseError('Changed service ledger')))
    _,project,_,_,_=context;result=read_handoff(project,row['cycle_id']);delivery=result['delivery']
    assert delivery['service_runtime_ready'] is False and delivery['device_accepted'] is False
    if change in {'different_version','different_graph','invalid_review'}:
        assert delivery['whole_flow_current'] is False and result['next_step']==5 and delivery['service_application_recorded'] is False
    elif change in {'wrong_release_review','wrong_ack','pending','release_changed','corrupt_ledger'}:
        assert delivery['service_application_recorded'] is False
    else:assert delivery['service_application_recorded'] is True
    assert delivery['reasons']


def test_delivery_snapshot_does_not_create_missing_service_and_binds_its_digest(context,monkeypatch):
    from backend.engine.operations_review import read_handoff
    from backend.engine.image_truth import digest
    row,prepared,review,active,runtime,calls=delivery_context(context,monkeypatch)
    _,project,_,_,_=context;root=Path(project['project_dir'])/'runtime_service'
    (root/'service.json').unlink();(root/'runtime_deployments.sqlite3').unlink();root.rmdir()
    value=read_handoff(project,row['cycle_id'])['delivery'];assert value['whole_flow_current'] is True and value['service_application_recorded'] is False
    assert not root.exists() and calls==[('authority',None)]
    assert value['snapshot_sha256']==digest({k:v for k,v in value.items() if k!='snapshot_sha256'})
