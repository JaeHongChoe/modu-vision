#!/usr/bin/env python3
"""Owned synthetic Modu cookie/CSRF → installed Geti CPU lifecycle acceptance.

No real account/image/GPU/provider access. The durable receipt supports resuming
an interrupted run without creating another training operation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--descriptor', type=Path, required=True)
    args = parser.parse_args()
    owned = args.root.expanduser().resolve()
    owned.mkdir(parents=True, exist_ok=True)
    receipt_path = owned / 'receipt.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {'schema': 1, 'kind': 'owned synthetic integrated platform acceptance', 'started_at': time.time()}
    if receipt.get('status') == 'FAIL' and receipt.get('error') and not receipt.get('failures'):
        receipt['failures'] = [{'message': receipt['error'], 'at': receipt.get('failed_at')}]
    attempts = owned / 'attempts'
    for index, failure in enumerate(receipt.get('failures', []), 1):
        attempts.mkdir(exist_ok=True)
        attempt = attempts / f'failure-{index}.json'
        if not attempt.exists():
            attempt.write_text(json.dumps({'kind':'preserved failed acceptance attempt event','status':'FAIL',**failure},indent=2))
    def save(**values):
        if values.get('status') == 'FAIL': receipt.setdefault('failures', []).append({'message': values.get('error'), 'at': values.get('failed_at')})
        if values.get('status') == 'PASS':
            receipt.pop('error', None)
            receipt.pop('failed_at', None)
        receipt.update(values)
        if values.get('status') == 'FAIL':
            attempts.mkdir(exist_ok=True)
            (attempts / f"failure-{len(receipt['failures'])}.json").write_text(json.dumps(receipt,indent=2,ensure_ascii=False))
        temporary = receipt_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(receipt, indent=2, ensure_ascii=False))
        temporary.replace(receipt_path)
    def event(name, **values):
        print(json.dumps({'event': name, **values}, ensure_ascii=False), flush=True)
    descriptor = args.descriptor.resolve(strict=True)
    os.environ['VISION_AI_STUDIO_USER_DATA_DIR'] = str(owned / 'userdata')
    os.environ['MODU_GETI_RUNTIME_DESCRIPTOR'] = str(descriptor)
    os.environ['MODU_BROWSER_ORIGINS'] = '["https://owned-fixture.test"]'
    for key in ('MODU_GETI_PYTHON', 'MODU_GETI_SOURCE_ROOT', 'MODU_GETI_ENTRYPOINT'):
        os.environ.pop(key, None)
    from PIL import Image, ImageDraw
    from fastapi.testclient import TestClient
    from backend.main import create_app
    source = owned / 'source'
    if not source.exists():
        for subset_index, (subset, count) in enumerate([('train', 8), ('val', 2), ('test', 2)]):
            for label, color in [('red', (220, 35, 35)), ('blue', (35, 35, 220))]:
                folder = source / subset / label
                folder.mkdir(parents=True, exist_ok=True)
                for index in range(count):
                    image = Image.new('RGB', (96, 80), color)
                    draw = ImageDraw.Draw(image)
                    draw.rectangle((8 + index, 10, 50 + index, 55), outline=(240, 240, 240), width=3)
                    draw.line((index, 0, 95, 79 - index), fill=(index * 10, 180, 40), width=2)
                    draw.rectangle((65, 5, 72 + subset_index, 11 + index), fill=(30 + subset_index * 80, 10 + index * 20, 80))
                    image.save(folder / f'{subset}-{label}-{index}.png')
    original_hashes = receipt.get('original_hashes') or {str(path.relative_to(source)): sha(path) for path in source.rglob('*.png') if 'inspection-new' not in path.name}
    save(descriptor_sha256=sha(descriptor), original_hashes=original_hashes)
    app = create_app(project_dir=str(owned / 'projects'), shared_auth_dir=str(owned / 'auth'))
    origin = 'https://owned-fixture.test'
    browser = TestClient(app, base_url=origin, headers={'Origin': origin})
    capability = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    password = 'owned synthetic password 123'
    username = 'owned-platform-admin'
    def call(client, method, path, *, json_body=None, expected=200):
        response = client.request(method, path, json=json_body) if json_body is not None else client.request(method, path)
        if response.status_code != expected:
            raise AssertionError(f'{method} {path}: {response.status_code}: {response.text[:2000]}')
        return response
    def login(client, name):
        result = call(client, 'POST', '/api/accounts/login', json_body={'username': name, 'password': password, 'transport': 'cookie'}).json()
        assert 'token' not in result and client.cookies.get('vision_session')
        client.headers['X-Vision-CSRF'] = result['csrf_token']
        return result
    try:
        bootstrap = capability.post('/api/accounts/bootstrap', json={'username': username, 'password': password})
        assert bootstrap.status_code in {200, 409}, bootstrap.text
        login(browser, username)
        if not receipt.get('project_id'):
            project = call(browser, 'POST', '/api/project/create', json_body={'name': 'Owned integrated classification', 'task': 'classification'}).json()
            save(project_id=project['id'])
        call(browser, 'POST', '/api/accounts/select-project', json_body={'project_id': receipt['project_id']})
        project = call(browser, 'GET', '/api/project/current').json()
        if project.get('source_dataset_dir') != str(source):
            call(browser, 'PUT', '/api/project/update', json_body={'source_dataset_dir': str(source)})
        initial = call(browser, 'GET', '/api/platform/geti/status').json()
        assert initial['project']['task'] == 'classification' and initial['available'], initial
        if not receipt.get('job'):
            assert initial['runtime']['state'] == 'stopped', initial
        event('initial', runtime=initial['runtime'])
        # An authentic cookie alone cannot mutate the child. No CSRF → no start.
        csrf = browser.headers.pop('X-Vision-CSRF')
        call(browser, 'POST', '/api/platform/geti/start', json_body={}, expected=403)
        browser.headers['X-Vision-CSRF'] = csrf
        started = call(browser, 'POST', '/api/platform/geti/start', json_body={}).json()
        assert started['runtime']['state'] == 'running', started
        architecture = next(row for row in started['architectures'] if row['id'] == 'image-classification-efficientnet-b0')
        assert architecture['supported'] and architecture['weights_ready'], architecture
        save(runtime_started=True, weights=architecture)
        if not receipt.get('dataset'):
            prepared = call(browser, 'POST', '/api/platform/geti/prepare', json_body={'operation_id': 'owned-prepare-1', 'input_recipe': {'version': 1, 'operations': [{'op': 'resize', 'width': 80, 'height': 64}]}}).json()
            assert prepared['counts'] == {'training': 16, 'validation': 4, 'testing': 4, 'total': 24}, prepared['counts']
            assert prepared['recipe_sha256'] and len(prepared['source_mapping']) == 24
            assert all('copied_path' not in row for row in prepared['source_mapping'])
            repeated = call(browser, 'POST', '/api/platform/geti/prepare', json_body={'operation_id': 'owned-prepare-1', 'input_recipe': {'version': 1, 'operations': [{'op': 'resize', 'width': 80, 'height': 64}]}}).json()
            assert repeated['id'] == prepared['id']
            save(dataset=prepared)
        event('prepared', counts=receipt['dataset']['counts'], recipe_sha256=receipt['dataset']['recipe_sha256'])
        # A separate owned member sees this project but cannot start/prepare/train.
        viewer_name = 'owned-platform-viewer'
        created = browser.post('/api/accounts/users', json={'username': viewer_name, 'password': password, 'administrator': False})
        if created.status_code == 200: viewer_id = created.json()['id']
        else:
            users = call(browser, 'GET', '/api/accounts/users').json()['users']
            viewer_id = next(row['id'] for row in users if row['username'] == viewer_name)
        call(browser, 'PUT', '/api/accounts/projects/' + receipt['project_id'] + '/members', json_body={'user_id': viewer_id, 'role': 'viewer'})
        viewer = TestClient(app, base_url=origin, headers={'Origin': origin})
        login(viewer, viewer_name)
        call(viewer, 'POST', '/api/accounts/select-project', json_body={'project_id': receipt['project_id']})
        view_status = call(viewer, 'GET', '/api/platform/geti/status').json()
        assert view_status['permissions']['can_train'] is False
        call(viewer, 'POST', '/api/platform/geti/start', json_body={}, expected=403)
        call(viewer, 'POST', '/api/platform/geti/prepare', json_body={'operation_id': 'viewer-must-not-publish'}, expected=403)
        if not receipt.get('foreign_project_id'):
            other = call(browser, 'POST', '/api/project/create', json_body={'name': 'Owned isolated second project', 'task': 'classification'}).json()
            save(foreign_project_id=other['id'])
            call(browser, 'POST', '/api/accounts/select-project', json_body={'project_id': receipt['project_id']})
        call(viewer, 'POST', '/api/accounts/select-project', json_body={'project_id': receipt['foreign_project_id']}, expected=403)
        save(authority={'cookie': True, 'csrf_required': True, 'viewer_mutations_denied': True, 'foreign_project_denied': True})
        train_body = {'operation_id': 'owned-train-1', 'architecture_id': 'image-classification-efficientnet-b0', 'max_epochs': 1}
        if not receipt.get('job'):
            job = call(browser, 'POST', '/api/platform/geti/train', json_body=train_body).json()
            save(job=job)
        job = receipt['job']
        if not receipt.get('inspection_added'):
            repeated = call(browser, 'POST', '/api/platform/geti/train', json_body=train_body).json()
            assert repeated['id'] == job['id']
        event('submitted', id=job['id'], status=job['status'])
        deadline = time.monotonic() + 900
        last = None
        while time.monotonic() < deadline:
            job = call(browser, 'GET', '/api/platform/geti/jobs/' + job['id']).json()
            state = (job['status'], job.get('progress'), job.get('message'), job.get('worker_exited'))
            if state != last:
                event('job', status=state[0], progress=state[1], message=state[2], worker_exited=state[3])
                save(job=job)
                last = state
            if job['status'] in {'failed', 'cancelled', 'rejected', 'submission_unconfirmed'}:
                raise AssertionError('Training did not complete: ' + json.dumps(job, ensure_ascii=False))
            if job['status'] == 'done' and job.get('worker_exited'): break
            time.sleep(1)
        else: raise AssertionError('Owned training/worker exit timeout')
        models = call(browser, 'GET', '/api/platform/geti/models').json()
        model = next(row for row in models if row['id'] == job['model_id'])
        assert model['binding_state'] == 'verified', model
        assert model['recipe_sha256'] == receipt['dataset']['recipe_sha256']
        assert len(model['variants']) == 3 and all(row['weights_size'] > 0 and row['evaluations'] for row in model['variants'])
        formats = {row['format'] for row in model['variants']}
        assert formats == {'pytorch', 'openvino', 'onnx'}, formats
        save(models=models)
        event('evaluated_models', formats=sorted(formats), dataset_revision_id=model['dataset_revision_id'])
        exports = []
        for variant in model['variants']:
            response = call(browser, 'GET', f"/api/platform/geti/models/{model['id']}/export/{variant['id']}")
            path = owned / f"export-{variant['format']}.zip"
            path.write_bytes(response.content)
            assert sha(path) == response.headers['X-Artifact-SHA256']
            exports.append({'format': variant['format'], 'bytes': path.stat().st_size, 'sha256': sha(path)})
        save(exports=exports)
        variant = next(row for row in model['variants'] if row['format'] == 'openvino')
        original = source / next(iter(original_hashes))
        variant_predictions = {}
        for candidate in model['variants']:
            if candidate['format'] not in {'openvino','onnx'}: continue
            result = call(browser, 'POST', '/api/platform/geti/predict', json_body={'model_id': model['id'], 'variant_id': candidate['id'], 'image_path': str(original)}).json()
            assert result['format'] == candidate['format'] and result['coordinate_space'] == 'original' and result['predictions']
            for item in result['predictions']:
                if 'confidence' in item:
                    import math
                    assert math.isfinite(item['confidence']) and 0 <= item['confidence'] <= 1
            variant_predictions[candidate['format']] = result
        assert set(variant_predictions) == {'openvino','onnx'}
        save(variant_predictions=variant_predictions)
        prediction = call(browser, 'POST', '/api/platform/geti/predict', json_body={'model_id': model['id'], 'variant_id': variant['id'], 'image_path': str(original)}).json()
        assert prediction['predictions'] and prediction['model_id'] == model['id'], prediction
        save(prediction=prediction)
        context = call(browser, 'GET', '/api/project/current').headers['X-Vision-Context']
        from backend.contracts.context import ProjectContext
        key = app.state.context_registry.project_key(ProjectContext.model_validate_json(context))
        app.state.geti_runtime.stop(key)
        stopped = call(browser, 'GET', '/api/platform/geti/status').json()
        assert stopped['runtime']['state'] == 'stopped' and any(row['id'] == job['id'] for row in stopped['jobs'])
        assert any(row['id'] == model['id'] for row in stopped['models'])
        call(browser, 'POST', '/api/platform/geti/start', json_body={})
        restarted = call(browser, 'GET', '/api/platform/geti/models').json()
        assert any(row['id'] == model['id'] for row in restarted)
        if not receipt.get('inspection_added'):
            repeated = call(browser, 'POST', '/api/platform/geti/train', json_body=train_body).json()
            assert repeated['id'] == job['id'] and repeated['status'] == 'done'
        save(restarted_without_duplicate=True)
        fresh = source / 'test/red/inspection-new.png'
        if not fresh.exists(): Image.new('RGB', (96, 80), (220, 35, 35)).save(fresh)
        save(inspection_added=True)
        new_prediction = call(browser, 'POST', '/api/platform/geti/predict', json_body={'model_id': model['id'], 'variant_id': variant['id'], 'image_path': str(fresh)}).json()
        assert new_prediction['predictions'], new_prediction
        # Use the very same trained artifact through the existing inspection
        # catalog, saved pipeline and durable run APIs. This never trains again.
        from urllib.parse import urlencode
        catalog = call(browser, 'GET', '/api/flowchart/models/catalog?' + urlencode({'source_dataset_path': str(source)})).json()
        selected = next(row for row in catalog['models'] if row.get('platform') == 'geti' and row['format'] == 'openvino' and row['platform_model']['model_id'] == model['id'])
        call(browser, 'POST', '/api/flowchart/models/verify', json_body={'source_dataset_path': str(source), 'models': [{'job_id': selected['job_id'], 'task': 'classification', 'platform_model': selected['platform_model']}]})
        from backend.engine.flowchart_engine import get_single_segmentation_flowchart
        pipeline = get_single_segmentation_flowchart(selected['job_id']).model_dump()
        pipeline['nodes'][1]['data'].update(task='classification', params={})
        call(browser, 'POST', '/api/flowchart/pipeline', json_body=pipeline)
        flow = call(browser, 'POST', '/api/flowchart/run', json_body={'pipeline': pipeline, 'image_path': str(fresh), 'image_id': fresh.stem, 'execution_target': 'local', 'device': 'cpu', 'project_id': project['id']}).json()
        artifacts = [artifact for step in flow['execution_steps'] for artifact in step['artifacts'] if artifact.get('kind') == 'geti_model_prediction']
        assert artifacts and artifacts[0]['model_id'] == model['id'] and len(artifacts[0]['export_sha256']) == 64
        listed = call(browser, 'GET', '/api/dataset/images?' + urlencode({'folder_path': str(source), 'task': 'classification', 'limit': 500, 'offset': 0})).json()
        image_row = next(row for row in listed['items'] if row['file_path'] == str(fresh))
        run = call(browser, 'POST', '/api/inspections/runs', json_body={'source_folder': str(source), 'task': 'classification', 'scope': 'all', 'pipeline': pipeline, 'images': [image_row], 'execution_target': 'local', 'device': 'cpu', 'project_id': project['id']}).json()
        completed = call(browser, 'POST', '/api/inspections/runs/' + run['run_id'] + '/execute', json_body={'image_path': str(fresh)}).json()
        assert completed['final_verdict'] in {'OK', 'NG', 'REVIEW'} and completed['crops'][0]['predicted_class']
        save(native_flow={'job_id': selected['job_id'], 'run_id': run['run_id'], 'verdict': flow['final_verdict'], 'durable_verdict': completed['final_verdict'], 'artifact': artifacts[0]})
        # New inspection images cannot silently enter the old frozen training set.
        call(browser, 'POST', '/api/platform/geti/train', json_body={**train_body, 'operation_id': 'stale-cohort-must-not-train'}, expected=409)
        assert len(call(browser, 'GET', '/api/platform/geti/status').json()['jobs']) == 1
        save(new_source_prediction=new_prediction, frozen_cohort_preserved=True)
        for name, expected in original_hashes.items(): assert sha(source / name) == expected
        stale_cookie = browser.cookies.get('vision_session')
        call(browser, 'POST', '/api/accounts/logout', json_body={})
        browser.cookies.set('vision_session', stale_cookie)
        call(browser, 'GET', '/api/platform/geti/status', expected=401)
        viewer.close()
        save(status='PASS', finished_at=time.time(), source_originals_unchanged=True, logout_revoked=True)
        event('PASS', receipt=str(receipt_path))
    except BaseException as exc:
        save(status='FAIL', error=str(exc), failed_at=time.time())
        event('FAIL', error=str(exc), receipt=str(receipt_path))
        raise
    finally:
        app.state.geti_runtime.shutdown()
        browser.close()
        capability.close()


if __name__ == '__main__': main()
