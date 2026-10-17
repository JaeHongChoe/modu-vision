"""Selected-source calibration uses the original resolver/remapper, with no model execution."""
import copy
import json
from pathlib import Path

import pytest
from fastapi import HTTPException


@pytest.fixture
def owned_job(tmp_path, monkeypatch):
    from backend.api import routes_evaluation as evaluation
    from backend.engine import checkpoint_paths
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.remote import operations
    from PIL import Image

    project = tmp_path / 'project'
    source = tmp_path / 'source'
    source.mkdir()
    job = project / 'models' / 'job_owned_A'
    prepared = job / 'dataset'
    rows = []
    manifest = []
    for kind, score in [('ng', .9), ('ok', .1)]:
        original = source / f'{kind}.png'
        Image.new('RGB', (8, 8), 'white' if kind == 'ok' else 'black').save(original)
        image = prepared / 'images' / 'test' / f'{kind}.png'
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(original.read_bytes())
        manifest.append({'image': str(image), 'source_image': str(original)})
        rows.append({'image_id': kind, 'file_path': str(image), 'ground_truth': kind,
                     'predicted_class': kind, 'confidence': .9, 'defect_score': score})
    (prepared / 'source_manifest.json').write_text(json.dumps(manifest))
    (job / 'best_model.pt').write_bytes(b'owned-test-checkpoint-no-load')
    (job / 'model_meta.json').write_text(json.dumps({'task': 'classification', 'classes': ['ng', 'ok']}))
    (job / 'job_receipt.json').write_text(json.dumps({'job_id': job.name, 'status': 'completed',
        'task': 'classification', 'output_dir': str(job), 'dataset_path': str(prepared),
        'source_dataset_path': str(source), 'dataset_fingerprint': fingerprint_dataset(source)}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(checkpoint_paths, '_ACTIVE_PROJECT_MODELS_DIR', job.parent)
    monkeypatch.setattr(evaluation.training_job_manager, '_jobs', {})
    payload = {'job_id': job.name, 'task': 'classification',
        'metrics': {'evaluated_split': 'test', 'selection_overlap': False},
        'confusion_matrix': {'cell_samples': {'ng:ng': [rows[0]['file_path']], 'ok:ok': [rows[1]['file_path']]}},
        'test_predictions': rows}
    calls = []
    monkeypatch.setattr(operations, 'remote_job_context', lambda *args: object())
    def existing_result(*args, **kwargs):
        calls.append(True)
        return copy.deepcopy(payload)
    monkeypatch.setattr(operations, 'run_remote_evaluation', existing_result)
    # Fixture saves the exact original evaluator's returned payload as an already accepted screen cache.
    # The inference boundary above is controlled; resolver/remap/fingerprints/history/files are original.
    accepted = evaluation.run_or_load_evaluation(job_id=job.name, source_dataset_path=str(source), source_task='classification')
    (job / 'eval_results.json').write_text(json.dumps(accepted))
    return evaluation, project, source, job, prepared, calls


def analyze(evaluation, job, **selection):
    return evaluation.get_overkill_underkill_analysis(job_id=job.name,
        target_max_underkill=0, cost_escape=500., cost_scrap=25., current_threshold=.5, **selection)


def test_normal_job_analysis_preserves_original_source_and_prepared_pixels(owned_job):
    evaluation, project, source, job, prepared, calls = owned_job
    # The screen already accepted and persisted this exact source/checkpoint cohort.
    original = evaluation.run_or_load_evaluation(job_id=job.name, source_dataset_path=str(source), source_task='classification')
    before = {path: path.read_bytes() for path in prepared.rglob('*') if path.is_file()}
    result = analyze(evaluation, job, source_dataset_path=str(source), source_task='classification')
    assert result['total_defects'] == result['total_normals'] == 1
    assert result['calibration_evidence']['prediction_count'] == 2
    assert len(calls) == 1, 'The original valid cache is reused; calibration does not run a new model.'
    persisted = json.loads((job / 'eval_results.json').read_text())
    assert persisted['binding']['source_dataset_path'] == str(source)
    assert {row['file_path'] for row in persisted['test_predictions']} == {str(source / 'ng.png'), str(source / 'ok.png')}
    assert all(row['labeling_supported'] for row in persisted['test_predictions'])
    assert persisted['binding'] == original['binding']
    assert {path: path.read_bytes() for path in before} == before


def test_zero_escape_original_write_retains_selected_source_binding(owned_job):
    evaluation, project, source, job, prepared, calls = owned_job
    result = evaluation.run_zero_escape_calibration(job_id=job.name, source_dataset_path=str(source), source_task='classification')
    persisted = json.loads((job / 'eval_results.json').read_text())
    assert result['calibrated'] is True
    assert persisted['binding']['source_dataset_path'] == str(source)
    assert persisted['metrics']['threshold_calibration_applied'] is True
    assert result['calibration_evidence'] == persisted['calibration_evidence']


@pytest.mark.parametrize('wrong', ['source', 'task', 'fingerprint', 'foreign_project', 'traversal', 'symlink'])
def test_selected_source_refusals_are_not_bypassed(owned_job, tmp_path, monkeypatch, wrong):
    from backend.engine.annotation_storage import set_request_project_root, reset_request_project_root
    evaluation, project, source, job, prepared, calls = owned_job
    selection = {'source_dataset_path': str(source), 'source_task': 'classification'}
    token = None
    if wrong == 'source':
        other = tmp_path / 'other'; other.mkdir(); selection['source_dataset_path'] = str(other)
    if wrong == 'task': selection['source_task'] = 'segmentation'
    if wrong == 'fingerprint': (source / 'ng.png').write_bytes(b'changed')
    if wrong == 'foreign_project':
        foreign = tmp_path / 'foreign'; (foreign / 'models').mkdir(parents=True)
        token = set_request_project_root(foreign)
    if wrong in ('traversal', 'symlink'):
        manifest = json.loads((prepared / 'source_manifest.json').read_text())
        if wrong == 'traversal': manifest[0]['source_image'] = str(source / 'child' / '..' / 'ng.png')
        else:
            alias = source / 'alias.png'; alias.symlink_to(source / 'ng.png')
            manifest[0]['source_image'] = str(alias)
            # Isolate the original remapper's symlink guard after original receipt validation.
            from backend.engine.dataset_fingerprint import fingerprint_dataset
            receipt = json.loads((job / 'job_receipt.json').read_text())
            receipt['dataset_fingerprint'] = fingerprint_dataset(source)
            (job / 'job_receipt.json').write_text(json.dumps(receipt))
        (prepared / 'source_manifest.json').write_text(json.dumps(manifest))
    try:
        with pytest.raises((HTTPException, ValueError)):
            analyze(evaluation, job, **selection)
        assert json.loads((job / 'eval_results.json').read_text())['binding']['source_dataset_path'] == str(source)
    finally:
        if token is not None: reset_request_project_root(token)


def test_http_get_and_post_forward_selection_without_dataset_override(owned_job, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    evaluation, project, source, job, prepared, calls = owned_job
    app = create_app(project_dir=str(project))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    created = client.post('/api/project/create', json={'name':'Owned calibration','project_dir':str(project),'task':'classification'})
    assert created.status_code == 200, created.text
    selection = {'job_id': job.name, 'source_dataset_path': str(source), 'source_task': 'classification'}
    response = client.get('/api/evaluation/overkill-underkill', params=selection)
    assert response.status_code == 200, response.text
    response = client.post('/api/evaluation/zero-escape-calibrate', json=selection)
    assert response.status_code == 200, response.text
    assert response.json()['calibrated'] is True
    response = client.get('/api/evaluation/zero-escape-calibrate', params={**selection, 'apply_to_eval_results':'false'})
    assert response.status_code == 200, response.text
    assert response.json()['calibrated'] is True
    assert len(calls) == 1
    client.close()


def test_omitted_source_direct_call_keeps_legacy_kwargs(owned_job, monkeypatch):
    evaluation, project, source, job, prepared, calls = owned_job
    payload = {'evaluation_contract_version': 2, 'task': 'classification',
        'metrics': {'evaluated_split': 'test', 'selection_overlap': False},
        'test_predictions': [{'image_id': 'ng', 'ground_truth': 'ng', 'confidence': .9, 'defect_score': .9},
                             {'image_id': 'ok', 'ground_truth': 'ok', 'confidence': .9, 'defect_score': .1}]}
    seen = []
    def legacy(job_id):
        seen.append(job_id)
        return payload
    monkeypatch.setattr(evaluation, 'run_or_load_evaluation', legacy)
    assert analyze(evaluation, job)['total_defects'] == 1
    assert seen == [job.name]


def test_explicit_directory_cannot_silently_ignore_a_selected_source(owned_job):
    evaluation, project, source, job, prepared, calls = owned_job
    evaluation.run_or_load_evaluation(job_id=job.name, source_dataset_path=str(source), source_task='classification')
    with pytest.raises(HTTPException) as error:
        evaluation.get_overkill_underkill_analysis(job_id=str(job), target_max_underkill=0,
            cost_escape=500., cost_scrap=25., current_threshold=.5,
            source_dataset_path=str(source), source_task='classification')
    assert error.value.status_code == 422
