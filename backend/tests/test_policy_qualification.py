"""Measured fixture records qualify by policy without inventing a human reviewer."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from backend.tests.test_model_deployments import _fixture, _report
from backend.tests.test_whole_flow_approval import setup_review, POLICY
from backend.tests.test_whole_flow_evaluation import workspace
from backend.engine import model_operations
from backend.engine.evaluation_history import EvaluationHistory


def model_evidence(tmp_path):
    client, project, source, fingerprint, models = _fixture(tmp_path)
    comparison = _report(project, source, fingerprint, models)
    policy = model_operations.configure_program(project, {'task':'classification','parent_job_id':'job_base',
        'reviewer':'Configuration author','auto_approve':True,'approval_policy_authorized':True,
        'minimum_metrics':{'accuracy':.95},'minimum_sample_count':16})
    evaluation = EvaluationHistory(Path(project['reports_dir'])/'evaluations').append(
        {'job_id':'job_candidate','task':'classification','metrics':{'accuracy':1.,'evaluated_split':'test','selection_overlap':False},
         'test_predictions':[{'file_path':row['file_path'],'ground_truth':row['ground_truth_verdict']} for row in comparison['images']]},
        {'source_dataset_path':str(source),'dataset_fingerprint':fingerprint,
         'checkpoint_sha256':hashlib.sha256(models['job_candidate'].read_bytes()).hexdigest(),
         'labelset_id':'default','threshold_settings':{}})
    return client,project,source,models,comparison,policy,evaluation


def test_model_policy_qualification_is_separate_and_exact_export_binding(tmp_path):
    from backend.api import routes_model_deployments as deployments, routes_export
    _,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    assert hasattr(deployments,'qualify_candidate_policy'), 'policy qualification must not call a human approval route'
    qualified=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'],comparison_id=comparison['comparison_id'])
    record=qualified['qualification'];assert qualified['status']=='qualified'
    assert record['decision_source']=='policy' and record['policy_revision']==policy['revision']
    assert not {'reviewer','holdout_reviewed','holdout_review_verified'} & record.keys()
    assert record['metrics']=={'accuracy':1.} and record['checks']['independent_holdout'] is True
    assert record['package_qualified'] is False and record['device_accepted'] is False
    with deployments._store(p) as db:
        assert db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]==0
    assert deployments.verified_approval_revision(p,record['revision_id'])['decision_source']=='policy'
    selected=routes_export._selected_release(p,source,'classification','job_candidate',models['job_candidate'],record['revision_id'])
    assert selected['checkpoint_sha256']==record['checkpoint_sha256']
    assert routes_export._selected_release(p,source,'classification','job_third',models['job_third'],record['revision_id']) is None
    models['job_candidate'].write_bytes(b'changed')
    assert deployments.verified_approval_revision(p,record['revision_id']) is None


def test_model_policy_qualifier_uses_saved_metrics_and_rejects_forged_or_failed_evidence(tmp_path):
    from backend.api import routes_model_deployments as deployments
    _,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    assert hasattr(deployments,'qualify_candidate_policy')
    original=copy.deepcopy(evaluation);evaluation['result']['metrics']['accuracy']=0.
    record=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'],comparison_id=comparison['comparison_id'])
    assert record['qualification']['metrics']['accuracy']==1., 'caller metrics cannot replace immutable evidence'
    failed=EvaluationHistory(Path(p['reports_dir'])/'evaluations').append(
        {**original['result'],'metrics':{**original['result']['metrics'],'accuracy':.2}},original['binding'])
    assert deployments.qualify_candidate_policy(p,policy,failed['evaluation_id'],comparison_id=comparison['comparison_id'])['status']=='not_qualified'
    path=Path(p['reports_dir'])/'evaluations'/(original['evaluation_id']+'.json')
    document=json.loads(path.read_text());document['result']['metrics']['accuracy']=.5;path.write_text(json.dumps(document))
    assert deployments.verified_approval_revision(p,record['qualification']['revision_id']) is None


def test_flow_policy_qualification_preserves_human_history_and_rechecks_truth(workspace,monkeypatch):
    module,p,evaluation=setup_review(workspace,monkeypatch)
    assert hasattr(module,'qualify_flow_policy'), 'automated qualification needs distinct evidence'
    model_operations.OperationsStore(p['project_dir']).save_policy({'revision':1,'project_id':p['id'],
        'source_dataset_path':p['source_dataset_dir'],'labelset_id':p.get('active_labelset_id','default'),
        'approval_policy_authorized':True,'auto_approve':True,'flow_policy':POLICY})
    from backend.tests.test_whole_flow_approval import approve
    human=approve(module,p,evaluation);human_bytes=Path(human['record_path']).read_bytes()
    qualified=module.qualify_flow_policy(p,evaluation_id=evaluation['evaluation_id'],expected_revision=human['revision_id'])
    assert qualified['status']=='qualified';record=qualified['qualification']
    assert record['contract']=='whole_flow_policy_qualification_v1' and record['decision_source']=='policy'
    assert not {'reviewer','holdout_reviewed','holdout_review_verified'} & record.keys()
    assert record['metrics']==evaluation['metrics'] and record['checks']['explicit_truth'] is True
    assert module.verified_approval(p,record['revision_id'])['decision_source']=='policy'
    assert record['package_qualified'] is False and record['device_accepted'] is False
    assert Path(human['record_path']).read_bytes()==human_bytes
    from backend.tests.test_whole_flow_evaluation import declare
    from backend.engine import image_truth
    declare(image_truth,p,'normal','NG',classes=['crack'])
    with pytest.raises(ValueError):module.verified_approval(p,record['revision_id'])


def authorize_evaluation(client,project,evaluation):
    task=client.post('/api/assistant-tasks',json={'goal':'Evaluate fixture','binding':'fixture-binding',
        'steps':[{'action':'evaluate_model','args':{'job_id':'job_candidate'},'label':'Evaluate saved candidate'}]}).json()['task']
    task=client.post('/api/assistant-tasks/'+task['id']+'/events',json={'type':'authorize','authorization_source':'oneclick',
        'binding':task['binding'],'expected_revision':task['revision']}).json()['task']
    step=task['steps'][0]
    task=client.post('/api/assistant-tasks/'+task['id']+'/events',json={'type':'begin_step','step_id':step['id'],'attempt_id':'fixture-attempt',
        'binding':task['binding'],'expected_revision':task['revision']}).json()['task']
    receipt={'project_id':project['id'],'source_dataset_path':project['source_dataset_dir'],'labelset_id':'default',
        'job_id':'job_candidate','evaluation_id':evaluation['evaluation_id'],
        'quality_assessment':{'status':'pending','evaluation_id':evaluation['evaluation_id'],'decision_source':'policy'}}
    task=client.post('/api/assistant-tasks/'+task['id']+'/events',json={'type':'complete_step','step_id':step['id'],'attempt_id':'fixture-attempt',
        'receipt':receipt,'binding':task['binding'],'expected_revision':task['revision']}).json()['task']
    return task,step


def test_qualification_endpoint_requires_exact_authorized_step_and_is_idempotent(tmp_path):
    client,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    task,step=authorize_evaluation(client,p,evaluation)
    body={'task_id':task['id'],'step_id':step['id'],'evaluation_id':evaluation['evaluation_id'],'comparison_id':comparison['comparison_id']}
    response=client.post('/api/model-operations/qualify-model',json=body)
    assert response.status_code==200,response.text
    assert response.json()['status']=='qualified'
    assert 'quality_assessment' not in response.json()['qualification']['authorization']['receipt']
    again=client.post('/api/model-operations/qualify-model',json=body)
    assert again.json()['qualification']['revision_id']==response.json()['qualification']['revision_id']
    assert again.json()['assessment_id']==response.json()['assessment_id']
    readback=client.get('/api/model-operations/assessments/'+response.json()['assessment_id'])
    assert readback.status_code==200 and readback.json()['status']=='qualified'
    rows=client.get('/api/model-operations/assessments',params={'task_id':task['id']}).json()['assessments']
    assert len(rows)==1 and rows[0]['assessment_id']==response.json()['assessment_id']
    current=client.get('/api/assistant-tasks/'+task['id']).json()['task']
    projected=client.post('/api/assistant-tasks/'+task['id']+'/quality',json={
        'binding':current['binding'],'expected_revision':current['revision'],'step_id':step['id'],
        'attempt_id':'fixture-attempt','assessment_id':response.json()['assessment_id']})
    assert projected.status_code==200,projected.text
    observed=projected.json()['task']['steps'][0]['receipt']['quality_assessment']
    assert observed['status']=='qualified' and observed['assessment_id']==response.json()['assessment_id']
    after=client.post('/api/model-operations/qualify-model',json=body)
    assert after.json()['qualification']['revision_id']==response.json()['qualification']['revision_id']
    assert after.json()['assessment_id']==response.json()['assessment_id']
    forged=client.post('/api/model-operations/qualify-model',json={**body,'evaluation_id':'evaluation_'+'f'*32})
    assert forged.status_code in (403,409)


def test_mismatched_task_evaluation_binding_does_not_write_a_qualification(tmp_path):
    from backend.api import routes_model_deployments as deployments
    _,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    result=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'],comparison_id=comparison['comparison_id'],
        authorization={'kind':'assistant_task','receipt':{'job_id':'job_third'}})
    assert result['status']=='not_qualified'
    with deployments._store(p) as db:assert db.execute('SELECT COUNT(*) FROM policy_qualifications').fetchone()[0]==0


def test_flow_receipt_hashes_cannot_select_a_different_saved_graph(workspace,monkeypatch):
    module,p,evaluation=setup_review(workspace,monkeypatch)
    model_operations.OperationsStore(p['project_dir']).save_policy({'revision':1,'project_id':p['id'],
        'source_dataset_path':p['source_dataset_dir'],'labelset_id':p.get('active_labelset_id','default'),
        'approval_policy_authorized':True,'auto_approve':True,'flow_policy':POLICY})
    result=module.qualify_flow_policy(p,evaluation_id=evaluation['evaluation_id'],
        authorization={'kind':'assistant_task','receipt':{'evaluation_id':evaluation['evaluation_id'],'graph_sha256':'f'*64}})
    assert result['status']=='not_qualified'
    assert module.current_approval(p) is None


def test_whole_flow_policy_qualification_never_qualifies_unknown_truth(workspace,monkeypatch):
    module,p,evaluation=setup_review(workspace,monkeypatch,unknown=True)
    model_operations.OperationsStore(p['project_dir']).save_policy({'revision':1,'project_id':p['id'],
        'source_dataset_path':p['source_dataset_dir'],'labelset_id':p.get('active_labelset_id','default'),
        'approval_policy_authorized':True,'auto_approve':True,'flow_policy':POLICY})
    result=module.qualify_flow_policy(p,evaluation_id=evaluation['evaluation_id'])
    assert result['status']=='not_qualified' and any('truth' in reason for reason in result['reasons'])
    assert module.current_approval(p) is None


def test_missing_configured_policy_or_comparison_is_explicit_input_needed(tmp_path):
    from backend.api import routes_model_deployments as deployments
    _,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    (Path(p['reports_dir'])/'model_comparisons'/(comparison['comparison_id']+'.json')).unlink()
    result=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'])
    assert result['status']=='input_needed' and 'comparison' in result['reasons'][0]
    model_operations.OperationsStore(p['project_dir']).save_policy({**policy,'approval_policy_authorized':False})
    result=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'],comparison_id=comparison['comparison_id'])
    assert result['status']=='input_needed'


def test_automatic_activation_uses_a_policy_selection_without_a_human_revision(tmp_path):
    from backend.api import routes_model_deployments as deployments
    _,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    saved=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'],comparison_id=comparison['comparison_id'],activate=True)
    assert saved['status']=='qualified'
    with deployments._store(p) as db:
        assert db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM active_revisions').fetchone()[0]==0
        assert deployments._active(db,source,'classification')['revision_id']==saved['qualification']['revision_id']


from backend.tests.test_whole_flow_runtime_review import converted_control


def test_automated_converted_qualification_requires_actual_runtime_measurement(converted_control):
    module,p,evaluation,human,package,measured,evidence=converted_control
    model_operations.OperationsStore(p['project_dir']).save_policy({'revision':1,'project_id':p['id'],
        'source_dataset_path':p['source_dataset_dir'],'labelset_id':p.get('active_labelset_id','default'),
        'approval_policy_authorized':True,'auto_approve':True,'flow_policy':POLICY})
    qualified=module.qualify_flow_policy(p,evaluation_id=evaluation['evaluation_id'],expected_revision=human['revision_id'])['qualification']
    package_result=module.qualify_package(p,package,qualified['revision_id'],device='openvino:CPU')
    assert package_result['contract']=='whole_flow_runtime_policy_qualification_v1'
    assert package_result['decision_source']=='policy' and package_result['device_accepted'] is False
    runtime_file=Path(p['project_dir'])/'flow_approvals'/'runtime_reviews'/(package_result['runtime_review_revision_id']+'.json')
    saved=json.loads(runtime_file.read_text())
    assert not {'reviewer','holdout_reviewed','holdout_review_verified'} & saved.keys()
    assert saved['subject']['runtime_acceptance_sha256']==evidence['receipt_sha256']
    assert saved['subject']['metrics']['escape_rate']==0.
    measured['images'][0]['sha256']='f'*64
    with pytest.raises(ValueError,match='cohort'):module.qualify_package(p,package,qualified['revision_id'],device='openvino:CPU')


def test_threshold_metadata_changes_invalidate_policy_evaluation(tmp_path):
    from backend.api import routes_model_deployments as deployments
    _,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    metadata=models['job_candidate'].with_name('model_meta.json')
    metadata.write_text(json.dumps({'task':'classification','threshold':.9}))
    result=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'],comparison_id=comparison['comparison_id'])
    assert result['status']=='not_qualified' and any('threshold' in reason for reason in result['reasons'])


def test_retry_of_activated_qualification_reuses_original_baseline(tmp_path):
    from backend.api import routes_model_deployments as deployments
    _,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    first=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'],comparison_id=comparison['comparison_id'],activate=True)
    second=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'],comparison_id=comparison['comparison_id'],activate=True)
    assert second['status']=='qualified'
    assert first['qualification']['revision_id']==second['qualification']['revision_id']


def test_archive_rebind_preserves_policy_receipt_and_selected_invalid_history(tmp_path):
    import sqlite3
    from backend.api import routes_model_deployments as deployments
    from backend.engine.project_archive import _rebind_deployments
    _,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    saved=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'],comparison_id=comparison['comparison_id'],activate=True)
    staging=tmp_path/'archive-staging';staging.mkdir();database=staging/'model_deployments.sqlite3'
    with deployments._store(p) as current,sqlite3.connect(database) as copy_db:current.backup(copy_db)
    with sqlite3.connect(database) as db:original=db.execute('SELECT payload FROM policy_qualifications').fetchone()[0]
    moved_source=tmp_path/'restored-source';moved_source.mkdir()
    _rebind_deployments(staging,source,moved_source)
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT source_dataset_path FROM policy_active_revisions').fetchone()[0]==str(moved_source)
        assert db.execute('SELECT payload FROM policy_qualifications').fetchone()[0]==original


def test_validation_split_metrics_cannot_claim_independent_policy_qualification(tmp_path):
    from backend.api import routes_model_deployments as deployments
    _,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    overlapping=EvaluationHistory(Path(p['reports_dir'])/'evaluations').append(
        {**evaluation['result'],'metrics':{**evaluation['result']['metrics'],'evaluated_split':'val','selection_overlap':True}},evaluation['binding'])
    result=deployments.qualify_candidate_policy(p,policy,overlapping['evaluation_id'],comparison_id=comparison['comparison_id'])
    assert result['status']=='not_qualified' and any('independent' in reason for reason in result['reasons'])


def test_task_hook_can_discover_only_an_exact_persisted_comparison(tmp_path):
    client,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    task,step=authorize_evaluation(client,p,evaluation)
    result=client.post('/api/model-operations/qualify-model',json={'task_id':task['id'],'step_id':step['id'],'evaluation_id':evaluation['evaluation_id']})
    assert result.status_code==200,result.text
    assert result.json()['status']=='qualified'
    assert result.json()['qualification']['comparison_id']==comparison['comparison_id']


def test_auto_flow_selection_uses_current_policy_without_an_extra_review(workspace,monkeypatch):
    from backend.tests.test_whole_flow_approval import approve
    module,p,evaluation=setup_review(workspace,monkeypatch)
    human=approve(module,p,evaluation);before=Path(human['record_path']).read_bytes()
    model_operations.OperationsStore(p['project_dir']).save_policy({'revision':1,'project_id':p['id'],
        'source_dataset_path':p['source_dataset_dir'],'labelset_id':p.get('active_labelset_id','default'),
        'approval_policy_authorized':True,'auto_approve':True,'flow_policy':POLICY})
    result=module.qualify_flow_policy(p,evaluation_id=evaluation['evaluation_id'])
    assert result['status']=='qualified'
    assert Path(human['record_path']).read_bytes()==before


def test_failed_and_missing_policy_assessments_are_durable_without_human_fields(tmp_path):
    from backend.api import routes_model_deployments as deployments
    _,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    failed=EvaluationHistory(Path(p['reports_dir'])/'evaluations').append(
        {**evaluation['result'],'metrics':{**evaluation['result']['metrics'],'accuracy':.2}},evaluation['binding'])
    result=deployments.qualify_candidate_policy(p,policy,failed['evaluation_id'],comparison_id=comparison['comparison_id'])
    assert result['status']=='not_qualified' and result.get('assessment_id')
    assessment=model_operations.OperationsStore(p['project_dir']).assessment(result['assessment_id'])
    assert assessment['status']=='not_qualified' and assessment['measured']['metrics']['accuracy']==.2
    assert not {'reviewer','holdout_reviewed','holdout_review_verified'} & assessment.keys()
    model_operations.OperationsStore(p['project_dir']).save_policy({**policy,'approval_policy_authorized':False})
    missing=deployments.qualify_candidate_policy(p,policy,evaluation['evaluation_id'],comparison_id=comparison['comparison_id'])
    assert missing['status']=='input_needed' and missing.get('assessment_id')
    assert model_operations.OperationsStore(p['project_dir']).assessment(missing['assessment_id'])['status']=='input_needed'


def test_input_needed_endpoint_outcome_survives_task_reload(tmp_path):
    client,p,source,models,comparison,policy,evaluation=model_evidence(tmp_path)
    task,step=authorize_evaluation(client,p,evaluation)
    model_operations.OperationsStore(p['project_dir']).save_policy({**policy,'approval_policy_authorized':False})
    result=client.post('/api/model-operations/qualify-model',json={'task_id':task['id'],'step_id':step['id'],'evaluation_id':evaluation['evaluation_id']})
    assert result.status_code==200 and result.json()['status']=='input_needed'
    reloaded=client.get('/api/model-operations/assessments',params={'task_id':task['id']}).json()['assessments']
    assert len(reloaded)==1 and reloaded[0]['status']=='input_needed'
    assert reloaded[0]['measured']['evidence_available'] is True and reloaded[0]['measured']['metrics']['accuracy']==1.
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task']['status']=='completed'
