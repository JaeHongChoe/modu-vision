"""Captured input priority is grounded in actual persisted run evidence."""
from pathlib import Path

from PIL import Image
import pytest

from backend.engine import capture_intake as ci
from backend.tests.test_capture_intake import project


def complete_job(store, color, result):
    path = store.state_dir / 'uploads' / (color + '.png')
    Image.new('RGB', (24, 24), color).save(path)
    identifier = store.enqueue(path, 'http')
    assert store.claim()['job_id'] == identifier
    store.finish(identifier, result=result)
    return identifier


def test_review_priority_persists_error_disagreement_threshold_and_unknown_truth(project):
    p, store, baseline = project
    assert callable(getattr(ci, 'review_queue', None)), 'Captured-input review priority queue is missing'
    disagreement = complete_job(store, 'green', {'status': 'success', 'final_verdict': 'REVIEW', 'models_disagree': True,
        'execution_steps': [{'node_id': 'model_a'}, {'node_id': 'model_b'}]})
    near = complete_job(store, 'blue', {'status': 'success', 'final_verdict': 'NG', 'max_defect_score': .52, 'score_unit': 'fraction'})
    failed = store.enqueue_device_event('camera', 'failed_1', store.state_dir / 'uploads' / 'missing.png')
    registered = ci.register_service_jobs(p, job_ids=[baseline, disagreement, near, failed])['candidates']
    queue = ci.review_queue(p)
    assert [row['origin']['job_id'] for row in queue['candidates']] == [failed, disagreement, near, baseline]
    assert queue['candidates'][0]['review_reasons'] == ['error', 'unknown_truth']
    assert 'disagreement' in queue['candidates'][1]['review_reasons']
    assert 'threshold' in queue['candidates'][2]['review_reasons']
    assert all(row['truth_verdict'] == 'UNKNOWN' for row in queue['candidates'])
    assert queue['candidates'][1]['origin']['node_evidence'] == [{'node_id': 'model_a'}, {'node_id': 'model_b'}]
    baseline_row = next(row for row in registered if row['origin']['job_id'] == baseline)
    ci.review_candidate(p, baseline_row['candidate_id'], expected_revision=1, actor='Reviewer', decision='reject')
    reopened = ci.review_queue(p)
    assert reopened['candidates'][-1]['review_state'] == 'reviewed'
    assert reopened['pending'] == 3 and reopened['total'] == 4


def test_review_priority_does_not_compare_untyped_percent_scores_with_fraction_band(project):
    p, store, baseline = project
    assert callable(getattr(ci, 'review_queue', None)), 'Captured-input review priority queue is missing'
    percent = complete_job(store, 'purple', {'status': 'success', 'final_verdict': 'NG', 'max_defect_score': .5, 'score_unit': 'percent'})
    untyped = complete_job(store, 'yellow', {'status': 'success', 'final_verdict': 'NG', 'max_defect_score': .5})
    ci.register_service_jobs(p, job_ids=[percent, untyped])
    assert all('threshold' not in row['review_reasons'] for row in ci.review_queue(p)['candidates'])
    with pytest.raises(ValueError, match='threshold|margin'):
        ci.review_queue(p, threshold=float('nan'))
