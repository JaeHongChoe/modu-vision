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
