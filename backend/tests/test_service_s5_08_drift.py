"""Drift compares immutable image references; predictions remain unverified quality."""
import importlib
import json
from pathlib import Path
from PIL import Image
import pytest
from backend.engine import capture_intake as ci
from backend.tests.test_capture_intake import project
from backend.tests.test_service_s3_09_extensions import complete_job


def engine():
    assert importlib.util.find_spec('backend.engine.capture_drift'), 'Capture drift evidence is missing'
    return importlib.import_module('backend.engine.capture_drift')


def test_drift_measures_images_preserves_reference_and_strata(project):
    p, store, baseline = project
    rows = ci.register_service_jobs(p, job_ids=[baseline])['candidates']
    drift = engine()
    reference = drift.create_reference(p, [rows[0]['candidate_id']], actor='Reviewer', name='Red baseline')
    incoming = complete_job(store, 'blue', {'status':'success', 'final_verdict':'NG', 'runtime_identity':{'manifest_sha256':'d'*64}})
    ci.register_service_jobs(p, job_ids=[incoming])
    report = drift.report(p, reference['reference_id'])
    assert report['reference_sha256'] == reference['record_sha256']
    assert report['reference_count'] == 1 and report['observed_count'] == 1
    assert report['image_statistics']['mean_luminance_delta'] == pytest.approx(-47)
    assert report['prediction_rates']['NG']['delta'] == 1
    assert report['quality_status'] == 'unverified_without_truth'
    assert report['automatic_action'] == 'none'
    assert report['strata'][0]['camera'] == 'http'
    assert report['strata'][0]['product_id'] == 'unknown'
    assert report['strata'][0]['model_changed'] is True
    assert drift.read_reference(p, reference['reference_id'])['record_sha256'] == reference['record_sha256']


def test_drift_tampered_reference_or_snapshot_is_not_a_valid_report(project):
    p, store, baseline = project
    row = ci.register_service_jobs(p, job_ids=[baseline])['candidates'][0]
    drift = engine()
    reference = drift.create_reference(p, [row['candidate_id']], actor='Reviewer', name='Baseline')
    path = ci._root(p) / 'drift' / (reference['reference_id'] + '.json')
    saved = json.loads(path.read_text()); saved['name'] = 'Forged'; path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match='hash|changed'):
        drift.report(p, reference['reference_id'])
    path.write_text(json.dumps(reference))
    Image.new('RGB',(24,24),'white').save(ci._root(p) / row['snapshot_path'])
    with pytest.raises(ValueError, match='snapshot|changed'):
        drift.report(p, reference['reference_id'])


def test_drift_missing_incoming_and_duplicate_ids_do_not_inflate_sample(project):
    p, store, baseline = project
    row = ci.register_service_jobs(p, job_ids=[baseline])['candidates'][0]
    drift = engine()
    reference = drift.create_reference(p, [row['candidate_id'], row['candidate_id']], actor='Reviewer', name='Baseline')
    assert len(reference['samples']) == 1
    failed = store.enqueue_device_event('camera','missing',store.state_dir/'uploads'/'absent.png')
    ci.register_service_jobs(p, job_ids=[failed])
    report = drift.report(p, reference['reference_id'])
    assert report['observed_count'] == 0 and report['excluded_count'] == 1
    assert report['image_statistics']['mean_luminance_delta'] is None
    assert report['state'] == 'insufficient_samples'
    with pytest.raises(ValueError, match='sample|candidate'):
        drift.create_reference(p, [], actor='Reviewer', name='Empty')
