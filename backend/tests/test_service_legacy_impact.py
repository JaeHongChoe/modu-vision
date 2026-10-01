"""Recorded legacy impact is advisory, scoped, and never rewrites evidence."""
import copy
import json
from pathlib import Path
import pytest
from backend.engine import workflow_impact


def model(**updates):
    return {'job_id':'job_old','task':'segmentation','source_dataset_path':'/recorded/source',
            'training_provenance':{'dataset_version_id':'data_old','labelset_id':'labels_old'},
            'training_config':{'augmentation_profile':'industrial'},
            'augmentation_contract':{'version':0,'target_sync':False},**updates}


def assess(**snapshot):
    return workflow_impact.assess_legacy_impact(snapshot)


@pytest.mark.parametrize('profile,status', [('industrial','affected'),('none','unaffected'),('photometric','unaffected')])
def test_geometry_impact_uses_recorded_profile(profile,status):
    item=model(training_config={'augmentation_profile':profile})
    original=copy.deepcopy(item)
    report=assess(models=[item])
    entry=next(row for row in report[status] if row['issue']=='training_geometry')
    assert entry['source_ids']['job_id']=='job_old'
    assert entry['lineage']['dataset_version_id']=='data_old'
    assert entry['lineage']['labelset_id']=='labels_old'
    assert entry['quality_cause']=='not_established'
    assert item==original
    if status=='affected':
        assert {'train_candidate','compare_fixed_cohort'} <= {row['action'] for row in report['next_action']}
        assert all(row['requires_human_review'] for row in report['next_action'])


def test_missing_config_unknown_and_synchronized_new_training_unaffected():
    report=assess(models=[model(job_id='missing',training_config={}),
        model(job_id='current',augmentation_contract={'version':1,'target_sync':True})])
    assert any(row['source_ids']['job_id']=='missing' for row in report['unknown'])
    assert any(row['source_ids']['job_id']=='current' for row in report['unaffected'])
    assert not report['affected']


def test_distance_evidence_and_approval_recovery_preserve_independent_lineage():
    spec={'domain':'distance','unit':'mahalanobis_distance','direction':'higher_is_defect','calibration_id':'cal-old','threshold':8}
    old=model(task='anomaly',model_type='padim',score_spec=spec)
    snapshot={'models':[old], 'evaluations':[{'evaluation_id':'eval-old','binding':{'labelset_id':'eval-labels'},
        'result':{'job_id':'job_old','task':'anomaly','metrics':{}}}],
        'flows':[{'version_id':'flow-old','pipeline':{'nodes':[{'id':'inspect','data':{'model_job_id':'job_old','task':'anomaly','threshold':.5}}]}}],
        'approvals':[{'revision_id':'approval-old','job_id':'job_old','comparison_id':'compare-old'}]}
    before=copy.deepcopy(snapshot);report=assess(**snapshot)
    assert snapshot==before
    assert any(row['source_ids'].get('evaluation_id')=='eval-old' for row in report['unknown'])
    assert any(row['source_ids'].get('version_id')=='flow-old' for row in report['affected'])
    assert any(row['source_ids'].get('revision_id')=='approval-old' for row in report['unknown'])
    actions={row['action'] for row in report['next_action']}
    assert {'evaluate_model','evaluate_flow','review_approval'} <= actions
    assert 'train_candidate' not in actions
    assert report['quality_approved'] is False


def test_compatible_recorded_score_evidence_is_unaffected_but_not_quality_approval():
    spec={'domain':'distance','unit':'euclidean_distance','direction':'higher_is_defect','calibration_id':'cal-a','threshold':8}
    report=assess(models=[model(task='anomaly',score_spec=spec)],evaluations=[{'evaluation_id':'eval-new',
        'result':{'job_id':'job_old','metrics':{'score_spec':spec,'score_basis':'saved_model_calibration','active_threshold':8}}}])
    assert any(row['source_ids'].get('evaluation_id')=='eval-new' for row in report['unaffected'])
    assert report['quality_approved'] is False


def test_legacy_impact_api_reads_artifacts_and_does_not_change_files(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import routes_model_operations
    root=tmp_path/'project';directory=root/'models'/'job_old';directory.mkdir(parents=True)
    (directory/'model_meta.json').write_text(json.dumps(model()))
    (directory/'best_model.pt').write_bytes(b'preserve-model')
    project={'id':'p','project_dir':str(root),'models_dir':str(root/'models'),'source_dataset_dir':'/current/source','active_labelset_id':'current-labels'}
    monkeypatch.setattr(routes_model_operations,'get_current_project',lambda request:project)
    app=FastAPI();app.include_router(routes_model_operations.router);client=TestClient(app)
    before={str(path):path.read_bytes() for path in root.rglob('*') if path.is_file()}
    response=client.get('/api/model-operations/legacy-impact')
    assert response.status_code==200,response.text
    report=response.json();assert report['project_id']=='p'
    entry=report['affected'][0]
    assert entry['lineage']['source_dataset_path']=='/recorded/source'
    assert entry['scope_matches'] is False
    assert report['next_action'][0]['requires_human_review'] is True
    assert before=={str(path):path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_recorded_approval_binding_is_read_from_actual_comparison_storage(tmp_path):
    import hashlib
    from backend.tests.test_workflow_impact import project_fixture
    from backend.api.routes_model_deployments import _store
    project,_=project_fixture(tmp_path);root=Path(project['project_dir'])
    evidence={'truth_binding':{'scope':{'task':'classification','classes':['OK','NG'],
        'class_semantics':{'roles':{'OK':'normal','NG':'defect'},'basis':{'OK':'explicit','NG':'explicit'}}},
        'images':[{'file_path':'/recorded/source/a.png','truth_sha256':'a'*64}], 'metadata_sha256':{'model_meta.json':'b'*64}}}
    directory=root/'reports'/'model_comparisons';directory.mkdir(parents=True)
    path=directory/'comparison_one.json';path.write_text(json.dumps(evidence))
    checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    with _store(project) as conn:
        conn.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('revision_one',project['source_dataset_dir'],'classification','job_one','old-checkpoint',
             'training-fingerprint','eval-fingerprint','comparison_one',checksum,None,None,'approve','reviewer','reason','2026-10-01'))
        conn.commit()
    report=workflow_impact.legacy_impact(project)
    assert any(row['source_ids'].get('revision_id')=='revision_one' for row in report['unaffected'])
    path.write_text(json.dumps({**evidence,'changed':True}))
    report=workflow_impact.legacy_impact(project)
    assert any(row['source_ids'].get('revision_id')=='revision_one' for row in report['unknown'])


def test_malformed_training_configuration_is_unknown():
    report=assess(models=[model(training_config={'augmentation_profile':{}})])
    assert any(row['issue']=='training_geometry' for row in report['unknown'])


def test_unreadable_legacy_evidence_is_reported_unknown_without_hiding_models(tmp_path):
    from backend.tests.test_workflow_impact import project_fixture
    project,checkpoint=project_fixture(tmp_path)
    (checkpoint.parent/'model_meta.json').write_text('{broken')
    (Path(project['project_dir'])/'model_deployments.sqlite3').write_bytes(b'not a database')
    report=workflow_impact.legacy_impact(project)
    assert any(row['source_ids'].get('job_id')=='job_one' for row in report['unknown'])
    assert any(row['issue']=='unreadable_evidence' for row in report['unknown'])
    assert checkpoint.read_bytes()==b'model-v1'


@pytest.mark.parametrize('active_threshold',[.5,None])
def test_evaluation_threshold_receipt_must_match_recorded_score_spec(active_threshold):
    spec={'domain':'distance','unit':'euclidean_distance','direction':'higher_is_defect','calibration_id':'cal-a','threshold':8}
    report=assess(models=[model(task='anomaly',score_spec=spec)],evaluations=[{'evaluation_id':'eval-conflict',
        'result':{'job_id':'job_old','metrics':{'score_spec':spec,'score_basis':'saved_model_calibration','active_threshold':active_threshold}}}])
    assert not any(row['source_ids'].get('evaluation_id')=='eval-conflict' for row in report['unaffected'])
    assert any(row['source_ids'].get('evaluation_id')=='eval-conflict' for row in report['unknown']+report['affected'])


def test_malformed_truth_binding_and_graph_are_unknown():
    report=assess(approvals=[{'revision_id':'bad-approval','evidence_binding':{
        'scope':{'task':'anomaly','classes':[],'class_semantics':{'roles':{},'basis':{}}},
        'images':[{'file_path':'','truth_sha256':'x'}],'metadata_sha256':{'x':'y'}}}],
        flows=[{'version_id':'bad-flow','pipeline':{'nodes':None}}])
    assert any(row['source_ids'].get('revision_id')=='bad-approval' for row in report['unknown'])
    assert any(row['source_ids'].get('version_id')=='bad-flow' for row in report['unknown'])


def test_checkpoint_without_metadata_remains_visible_as_unknown(tmp_path):
    from backend.tests.test_workflow_impact import project_fixture
    project,checkpoint=project_fixture(tmp_path)
    (checkpoint.parent/'model_meta.json').unlink()
    report=workflow_impact.legacy_impact(project)
    assert any(row['source_ids'].get('job_id')=='job_one' for row in report['unknown'])
    assert any(row['action']=='inspect_training_config' for row in report['next_action'])
    assert checkpoint.read_bytes()==b'model-v1'
