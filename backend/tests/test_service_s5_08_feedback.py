"""Controlled service storage drift; no learned or field-quality acceptance."""
import json
from backend.engine import capture_intake as ci,capture_drift as drift
from backend.engine.image_truth import digest
from backend.tests.test_capture_intake import project
from backend.tests.test_service_s3_09_extensions import complete_job


def test_feedback_reference_is_frozen_and_later_review_is_not_truth_or_stale_pixels(project):
 p,store,job=project;base=ci.register_service_jobs(p,job_ids=[job])['candidates'][0]
 ref=drift.create_reference(p,[base['candidate_id']],actor='Reviewer',name='Feedback reference')
 incoming=complete_job(store,'blue',{'status':'success','final_verdict':'NG','runtime_identity':{'manifest_sha256':'d'*64,'model_sha256':{'candidate':'e'*64}}})
 row=ci.register_service_jobs(p,job_ids=[incoming])['candidates'][0];ci.review_candidate(p,row['candidate_id'],expected_revision=row['revision'],actor='Reviewer',decision='adopt',note='Controlled capture candidate review')
 ci.review_candidate(p,base['candidate_id'],expected_revision=base['revision'],actor='Reviewer',decision='reject',note='Feedback changed after baseline freeze')
 report=drift.report(p,ref['reference_id']);feedback=report['human_feedback']
 assert feedback['rates']['adopt']=={'reference':0.0,'observed':1.0,'delta':1.0}
 assert feedback['rates']['pending']=={'reference':1.0,'observed':0.0,'delta':-1.0}
 assert len(feedback['reference_updates'])==1 and feedback['reference_updates'][0]['current_decision']=='reject'
 assert drift.read_reference(p,ref['reference_id'])==ref and report['reference_sha256']==ref['record_sha256']
 assert report['quality_status']=='unverified_without_truth' and report['automatic_action']=='none'
 assert all(r['truth']=='UNKNOWN' for r in report['observed_samples'])
 assert report['model_bindings']['observed'][0]['manifest_sha256']=='d'*64
 assert report['model_bindings']['observed'][0]['model_sha256']=={'candidate':'e'*64}
 assert len(report['model_bindings']['reference'])==1


def test_legacy_reference_feedback_is_unavailable_not_invented_zero(project):
 p,store,job=project;base=ci.register_service_jobs(p,job_ids=[job])['candidates'][0]
 ref=drift.create_reference(p,[base['candidate_id']],actor='Reviewer',name='Legacy reference');ref.pop('feedback_at_freeze');ref['record_sha256']=digest({k:v for k,v in ref.items() if k!='record_sha256'})
 path=ci._root(p)/'drift'/(ref['reference_id']+'.json');path.write_text(json.dumps(ref))
 feedback=drift.report(p,ref['reference_id'])['human_feedback'];assert feedback['reference_available'] is False
 assert all(r['reference'] is None and r['observed'] is None and r['delta'] is None for r in feedback['rates'].values())
 assert feedback['reference_updates']==[]
