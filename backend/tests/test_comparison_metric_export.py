"""Common binary metrics never silently remove a model's difficult outcomes."""
import hashlib
import json
from pathlib import Path

from backend.api import routes_model_comparisons as routes
from backend.tests.test_comparison_compute_target import comparison


def row(truth, incumbent, candidate, **extra):
    return {'ground_truth_verdict': truth,
            'incumbent': {'verdict': incumbent, 'error': None},
            'candidate': {'verdict': candidate, 'error': None}, **extra}


def test_both_models_share_the_identical_metric_denominator():
    rows = [row('NG', 'NG', 'OK'), row('OK', 'OK', 'NG'),
            row('NG', 'REVIEW', 'NG'), row('OK', None, 'OK'), row(None, 'OK', 'OK')]
    metrics = routes._binary_metrics(rows)
    assert metrics['evaluated_images'] == 2
    assert metrics['excluded'] == {'unknown_truth': 1, 'review': 1, 'error': 1}
    assert metrics['incumbent']['counts'] == {'tp': 1, 'tn': 1, 'fp': 0, 'fn': 0}
    assert metrics['candidate']['counts'] == {'tp': 0, 'tn': 0, 'fp': 1, 'fn': 1}
    assert metrics['incumbent']['accuracy'] == 1
    assert metrics['candidate']['miss_rate'] == metrics['candidate']['overkill_rate'] == 1


def test_missing_classes_and_empty_comparison_have_unavailable_rates():
    metrics = routes._binary_metrics([row('NG', 'NG', 'NG')])
    assert metrics['candidate']['overkill_rate'] is None
    assert metrics['candidate']['recall_ng'] == 1
    metrics = routes._binary_metrics([row(None, 'OK', 'NG'), row('NG', 'REVIEW', 'REVIEW')])
    assert metrics['evaluated_images'] == 0
    assert metrics['candidate']['accuracy'] is None
    assert metrics['candidate']['precision_ng'] is None


def test_error_flag_prevents_a_valid_verdict_being_counted():
    damaged = row('NG', 'NG', 'OK')
    damaged['candidate']['error'] = 'inference incomplete'
    metrics = routes._binary_metrics([damaged])
    assert metrics['evaluated_images'] == 0 and metrics['excluded']['error'] == 1


def test_saved_export_binds_exact_bytes_parameters_and_prediction_evidence(comparison, monkeypatch):
    client, project, source, payload, _ = comparison
    from backend.remote import operations
    monkeypatch.setattr(operations, 'run_verified_flowchart_on_compute', lambda *args, **kwargs:
        {'final_verdict': 'NG', 'crops': [], 'remote_operation_id': 'op_'+'a'*32,
         'remote_result_sha256': 'b'*64, 'execution_target': 'selected_compute', 'execution_device': 'cuda'})
    created = client.post('/api/evaluation/model-comparisons', json=payload)
    assert created.status_code == 200, created.text
    report = created.json()
    path = Path(project['reports_dir'])/'model_comparisons'/f"{report['comparison_id']}.json"
    before = path.read_bytes()
    exported = client.get(f"/api/evaluation/model-comparisons/{report['comparison_id']}/export",
                          params={'source_dataset_path': str(source), 'task': 'classification'})
    assert exported.status_code == 200, exported.text
    envelope = exported.json()
    assert envelope['saved_report_sha256'] == hashlib.sha256(before).hexdigest()
    assert envelope['report'] == json.loads(before)
    assert envelope['binary_metrics']['candidate']['counts']['fp'] == 1
    assert envelope['task_specific_metrics']['status'] == 'unavailable'
    assert envelope['report']['execution']['device'] == 'cuda'
    assert envelope['report']['images'][0]['candidate']['execution']['remote_result_sha256'] == 'b'*64
    assert path.read_bytes() == before
    foreign = client.get(f"/api/evaluation/model-comparisons/{report['comparison_id']}/export",
                         params={'source_dataset_path': str(source.parent/'foreign'), 'task': 'classification'})
    assert foreign.status_code == 409
