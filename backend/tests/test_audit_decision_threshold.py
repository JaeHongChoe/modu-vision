"""The decision threshold a model uses outside step 4 is one value with a named source (audit F-08 / A4)."""
import json
from pathlib import Path


def _client(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert client.post('/api/project/create', json={'name': 'Threshold'}).status_code == 200
    return client


def test_a_model_without_step_4_calibration_reports_its_saved_threshold(tmp_path):
    client = _client(tmp_path)
    models = Path(client.get('/api/project/current').json()['models_dir'])
    model = models / 'job_threshold'
    model.mkdir(parents=True)
    (model / 'best_model.pt').write_bytes(b'checkpoint')
    (model / 'model_meta.json').write_text(json.dumps({'task': 'classification', 'classes': ['OK', 'NG'], 'optimal_threshold': 0.62}))
    body = client.get('/api/models/decision-threshold', params={'job_id': 'job_threshold'}).json()
    assert body == {'job_id': 'job_threshold', 'threshold': 0.62, 'source': 'saved_model_threshold', 'calibrated': False,
                    'not_transferable_reason': None, 'model_threshold': 0.62}


def test_an_uncalibrated_evaluation_file_keeps_the_saved_threshold_and_says_why(tmp_path):
    client = _client(tmp_path)
    models = Path(client.get('/api/project/current').json()['models_dir'])
    model = models / 'job_stale'
    model.mkdir(parents=True)
    (model / 'best_model.pt').write_bytes(b'checkpoint')
    (model / 'model_meta.json').write_text(json.dumps({'task': 'classification', 'classes': ['OK', 'NG'], 'optimal_threshold': 0.5}))
    # A calibration whose evidence is missing never replaces the saved threshold.
    (model / 'eval_results.json').write_text(json.dumps({'zero_underkill_calibrated': True, 'optimal_threshold': 0.91}))
    body = client.get('/api/models/decision-threshold', params={'job_id': 'job_stale'}).json()
    assert body['threshold'] == 0.5 and body['calibrated'] is False and body['not_transferable_reason']


def test_unknown_or_unsafe_job_ids_are_refused(tmp_path):
    client = _client(tmp_path)
    assert client.get('/api/models/decision-threshold', params={'job_id': 'job_missing'}).status_code == 404
    assert client.get('/api/models/decision-threshold', params={'job_id': '../escape'}).status_code == 422
