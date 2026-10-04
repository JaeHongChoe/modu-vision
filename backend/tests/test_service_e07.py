"""E07: a deployment preflight lists every dependency of a saved flow on its target, node by node with a remedy; a
missing optional runtime blocks only the path through its node; the report is kept, reopens, and goes stale when the
target, the environment (a pack or a version) or the release changes; an exported package checks itself from its CLI.
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import torch
from fastapi.testclient import TestClient

from backend.engine import flow_preflight as preflight
from backend.engine.flowchart_engine import FlowchartPipeline, get_single_detection_flowchart

CAL = 'spatial-cal:sha256:' + 'c' * 64


def branched_flow() -> FlowchartPipeline:
    """input -> YOLO crop -> decision, and input -> DINOv3 segmentation -> mm measurement -> decision -> output."""
    graph = get_single_detection_flowchart(job_id='job_yolo').model_dump()
    crop = next(node for node in graph['nodes'] if node['id'] == 'node_crop')
    seg = copy.deepcopy(crop)
    seg.update(id='node_seg')
    seg['data'].update(node_type='inspection', task='segmentation', model_job_id='job_dino', label='Segmentation')
    measure = copy.deepcopy(crop)
    measure.update(id='node_measure')
    measure['data'].update(node_type='measurement', task=None, model_job_id=None, label='Length',
                           params={'calibration_ref': CAL, 'threshold_unit': 'mm'})
    graph['nodes'] += [seg, measure]
    edge = graph['edges'][0]
    graph['edges'] += [{**edge, 'id': 'e_seg', 'source': 'node_input', 'target': 'node_seg'},
                       {**edge, 'id': 'e_measure', 'source': 'node_seg', 'target': 'node_measure'},
                       {**edge, 'id': 'e_measure_decision', 'source': 'node_measure', 'target': 'node_decision'}]
    return FlowchartPipeline.model_validate(graph)


def checkpoints(tmp_path):
    made = {}
    for job, backbone in (('job_yolo', 'yolo26n'), ('job_dino', 'dinov3_vits16')):
        folder = tmp_path / job
        folder.mkdir()
        (folder / 'best_model.pt').write_bytes(f'{job} weights'.encode())
        (folder / 'model_meta.json').write_text(json.dumps({'task': 'x', 'backbone': backbone}))
        made[job] = folder / 'best_model.pt'
    return made


def versions(**overrides):
    table = {'numpy': '2.1.0', 'Pillow': '11.0.0', 'opencv-python-headless': '4.11.0', 'pydantic': '2.9.0', 'torch': '2.6.0',
             'torchvision': '0.21.0', 'timm': '1.0.24', 'ultralytics': None, **overrides}
    return lambda name: table.get(name)


def resolver(made):
    return lambda node, task, job_id: {'state': 'ready', 'checkpoint': made[job_id], 'evidence_ref': f'sha256:{job_id}'}


def test_each_dependency_maps_to_its_node_and_a_missing_optional_runtime_blocks_only_its_path(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, 'installed_version', versions())  # ultralytics is not installed
    monkeypatch.setattr(preflight, 'local_devices', lambda: ['cpu'])
    flow = branched_flow()
    requirements = preflight.collect_requirements(flow, target={'kind': 'this_computer', 'device': 'cpu'},
                                                  resolve_model=resolver(checkpoints(tmp_path)), resolve_calibration=lambda ref: None, here=True)
    by = {(r.node_id, r.kind, r.artifact_ref): r for r in requirements}
    yolo = by[('node_crop', 'runtime', 'ultralytics')]
    assert (yolo.state, yolo.version_range) == ('unavailable', '>=8.4.41') and 'install ultralytics>=8.4.41' in yolo.remedy
    assert by[('node_seg', 'runtime', 'timm')].state == 'ready' and by[('node_seg', 'runtime', 'timm')].evidence_ref == 'timm==1.0.24'
    cal = by[('node_measure', 'calibration', CAL)]
    assert cal.state == 'missing' and 'recreate it' in cal.remedy, 'a missing calibration names its node and what to do'
    assert by[('node_crop', 'model', 'detection:job_yolo')].license_ref == 'model-license-matrix:yolo_derived'
    assert by[('node_seg', 'model', 'segmentation:job_dino')].license_ref == 'model-license-matrix:dinov3_derived'
    report = preflight.build_report(flow, requirements, release={'kind': 'saved_flow', 'version_id': '1' * 32},
                                    target={'kind': 'this_computer', 'device': 'cpu'}, environment_value=preflight.environment())
    assert report['status'] == 'blocked' and report['decision_blocked'] is True
    assert report['blocked_nodes']['node_crop'] == ['runtime:ultralytics@node_crop']
    assert report['blocked_nodes']['node_measure'] == [f'calibration:{CAL}@node_measure']
    assert set(report['blocked_nodes']['node_decision']) == {'runtime:ultralytics@node_crop', f'calibration:{CAL}@node_measure'}
    assert 'node_seg' not in report['blocked_nodes'] and 'node_input' not in report['blocked_nodes'], 'other paths stay usable'
    assert report['counts']['unavailable'] == 1 and report['counts']['missing'] == 1


def test_a_model_node_without_a_completed_model_is_missing_and_never_runs_untrained(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, 'installed_version', versions(ultralytics='8.4.41'))
    flow = get_single_detection_flowchart(job_id=None)
    requirements = preflight.collect_requirements(flow, target={'kind': 'this_computer', 'device': 'cpu'},
                                                  resolve_model=lambda node, task, job: {'state': 'missing'},
                                                  resolve_calibration=lambda ref: None, here=True)
    [model] = [r for r in requirements if r.kind == 'model']
    assert (model.node_id, model.state) == ('node_crop', 'missing') and 'train a detection model in step 3' in model.remedy
    blocked = preflight.blocked_nodes(flow, requirements)
    assert set(blocked) == {'node_crop', 'node_decision', 'node_output'}


def test_another_target_leaves_runtime_and_device_unverified_and_a_device_is_never_swapped(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, 'installed_version', versions(ultralytics='8.4.41'))
    monkeypatch.setattr(preflight, 'local_devices', lambda: ['cpu'])
    flow = branched_flow()
    edge = preflight.normalize_target({'kind': 'edge', 'profile': 'edge_cpu', 'os': 'windows', 'architecture': 'x86_64', 'device': 'cpu'})
    requirements = preflight.collect_requirements(flow, target=edge, resolve_model=resolver(checkpoints(tmp_path)),
                                                  resolve_calibration=lambda ref: {'ref': ref}, here=False)
    assert {r.state for r in requirements if r.kind in ('runtime', 'device')} == {'unverified'}
    assert all('run_flow.py --preflight' in r.remedy or 'preflight on the target' in r.remedy for r in requirements if r.state == 'unverified')
    report = preflight.build_report(flow, requirements, release={'kind': 'saved_flow'}, target=edge, environment_value=None)
    assert report['status'] == 'unverified' and report['blocked_nodes'] == {} and report['environment_hash'] is None
    (tmp_path / 'b').mkdir()
    cuda = preflight.collect_requirements(flow, target={'kind': 'this_computer', 'device': 'cuda:0'}, resolve_model=resolver(checkpoints(tmp_path / 'b')),
                                          resolve_calibration=lambda ref: {'ref': ref}, here=True)
    devices = [r for r in cuda if r.kind == 'device']
    assert {r.state for r in devices} == {'unavailable'} and all('no other device is used instead' in r.remedy for r in devices)
    for bad in ({'kind': 'edge', 'profile': 'edge_cpu', 'os': 'windows', 'architecture': 'arm64'}, {'kind': 'edge', 'profile': 'edge_cpu',
                'os': 'linux', 'architecture': 'x86_64', 'device': 'cuda'}, {'kind': 'server'}, {'kind': 'this_computer', 'device': 'tpu'}):
        with pytest.raises(ValueError):
            preflight.normalize_target(bad)


def test_a_kept_report_reopens_and_goes_stale_with_the_target_the_environment_or_the_release(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, 'installed_version', versions(ultralytics='8.4.41'))
    monkeypatch.setattr(preflight, 'local_devices', lambda: ['cpu'])
    flow = branched_flow()
    target = {'kind': 'this_computer', 'device': 'cpu'}
    release = {'kind': 'saved_flow', 'version_id': '1' * 32, 'pipeline_sha256': 'a' * 64}
    requirements = preflight.collect_requirements(flow, target=target, resolve_model=resolver(checkpoints(tmp_path)),
                                                  resolve_calibration=lambda ref: {'ref': ref}, here=True)
    report = preflight.build_report(flow, requirements, release=release, target=target, environment_value=preflight.environment())
    assert report['status'] == 'ready' and report['decision_blocked'] is False
    store = preflight.PreflightStore(tmp_path / 'reports')
    store.save(report)
    reopened = store.load(report['report_id'])
    assert reopened == report and [row['report_id'] for row in store.list()] == [report['report_id']]
    assert preflight.staleness(reopened, release=release, target=target, environment_value=preflight.environment()) == []
    assert preflight.staleness(reopened, release=release, target={'kind': 'this_computer', 'device': 'mps'},
                               environment_value=preflight.environment()) == ['target_changed']
    monkeypatch.setattr(preflight, 'installed_version', versions(ultralytics='8.5.0'))  # a pack updated
    assert preflight.staleness(reopened, release=release, target=target, environment_value=preflight.environment()) == ['environment_changed']
    assert preflight.staleness(reopened, release={**release, 'pipeline_sha256': 'b' * 64}, target=None, environment_value=None) == ['release_changed']
    with pytest.raises(ValueError):
        store.save(report)
    path = tmp_path / 'reports' / f"{report['report_id']}.json"
    changed = json.loads(path.read_text(encoding='utf-8'))
    changed['status'] = 'ready' if changed['status'] != 'ready' else 'blocked'
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='changed after'):
        store.load(report['report_id'])


def _package(tmp_path):
    from backend.engine.flow_package import build_flow_package
    folder = tmp_path / 'job_detector'
    folder.mkdir()
    torch.save({'task': 'detection', 'model_state_dict': {}}, folder / 'best_model.pt')
    (folder / 'model_meta.json').write_text(json.dumps({'task': 'detection'}))
    result = build_flow_package(pipeline=get_single_detection_flowchart(job_id='job_detector'), checkpoints={'job_detector': folder / 'best_model.pt'},
                                output_base_dir=tmp_path / 'exports', package_name='line_a')
    return Path(result['package_path'])


def _cli(package, *args):
    run = subprocess.run([sys.executable, 'run_flow.py', *args], cwd=package, capture_output=True, text=True, timeout=600)
    return run.returncode, json.loads(run.stdout) if run.stdout.strip().startswith('{') else run.stderr


def test_an_exported_package_checks_itself_from_its_cli_and_keeps_the_report(tmp_path):
    package = _package(tmp_path)
    code, report = _cli(package, '--preflight')
    assert code == 0, report
    assert report['recipe_release'] == {'kind': 'package', 'manifest_sha256': __import__('hashlib').sha256((package / 'manifest.json').read_bytes()).hexdigest()}
    assert {(r['node_id'], r['kind'], r['state']) for r in report['requirements'] if r['node_id'] == 'node_crop'} >= {
        ('node_crop', 'model', 'ready'), ('node_crop', 'device', 'ready'), ('node_crop', 'runtime', 'ready')}
    assert (package / 'preflight' / f"{report['report_id']}.json").is_file()
    code, shown = _cli(package, '--show-preflight')
    assert code == 0 and shown['report_id'] == report['report_id'] and shown['stale'] is False
    # A device this computer does not have blocks the model path, with the node named; nothing runs on another device.
    code, blocked = _cli(package, '--preflight', '--device', 'cuda:7')
    assert code == 4 and blocked['status'] == 'blocked' and set(blocked['blocked_nodes']) == {'node_crop', 'node_decision', 'node_output'}
    code, shown = _cli(package, '--show-preflight', '--device', 'cpu')
    assert code == 4 and shown['stale'] is True and shown['stale_reasons'] == ['target_changed']
    assert _cli(package, '--verify-only')[0] == 0, 'the kept reports do not change the verified package'
    # A changed checkpoint is a mismatch at its node.
    (package / 'models' / 'job_detector' / 'best_model.pt').write_bytes(b'changed')
    code, changed = _cli(package, '--preflight')
    assert code == 4 and any(r['node_id'] == 'node_crop' and r['kind'] == 'model' and r['state'] == 'mismatch' for r in changed['requirements'])


def test_the_preflight_api_checks_a_saved_version_and_keeps_history_untouched(tmp_path, monkeypatch):
    from backend.api import routes_dataset, routes_flowchart
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.main import create_app
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    monkeypatch.setattr(routes_flowchart.training_job_manager, '_jobs', {})
    app = create_app(project_dir=str(tmp_path / 'workspace_registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.get('/api/project/current').json()
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'image.jpg').write_bytes(b'source image fixture')
    job_dir = Path(project['models_dir']) / 'job_saved_detector'
    (job_dir / 'dataset').mkdir(parents=True)
    torch.save({'task': 'detection', 'model_state_dict': {}}, job_dir / 'best_model.pt')
    (job_dir / 'model_meta.json').write_text(json.dumps({'task': 'detection'}))
    fingerprint = fingerprint_dataset(source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR, split_manifest=routes_dataset._split_manifest_file(source))
    (job_dir / 'job_receipt.json').write_text(json.dumps({'status': 'completed', 'task': 'detection', 'source_dataset_path': str(source),
                                                          'dataset_fingerprint': fingerprint, 'dataset_path': str(job_dir / 'dataset')}))
    saved = client.post('/api/flowchart/pipeline', params={'recipe_task': 'detection', 'source_dataset_path': str(source)},
                        json=get_single_detection_flowchart(job_id='job_saved_detector').model_dump())
    assert saved.status_code == 200, saved.text
    version = saved.json()['version_id']
    before = sorted(path.relative_to(project['project_dir']).as_posix() for path in Path(project['project_dir']).rglob('*') if path.is_file())
    body = {'source_dataset_path': str(source), 'recipe_task': 'detection', 'version_id': version, 'target': {'kind': 'this_computer', 'device': 'cpu'}}
    made = client.post('/api/export/flow/preflight', json=body)
    assert made.status_code == 200, made.text
    report = made.json()
    model = next(r for r in report['requirements'] if r['kind'] == 'model')
    assert (model['node_id'], model['state']) == ('node_crop', 'ready') and model['evidence_ref'].startswith('sha256:')
    assert report['recipe_release']['version_id'] == version and report['environment_hash']
    after = sorted(path.relative_to(project['project_dir']).as_posix() for path in Path(project['project_dir']).rglob('*') if path.is_file())
    assert [row for row in after if row not in before] == [f"deployment_preflight/{report['report_id']}.json"], 'nothing else is written or hidden'
    edge = client.post('/api/export/flow/preflight', json={**body, 'target': {'kind': 'edge', 'profile': 'edge_cpu', 'os': 'windows',
                                                                              'architecture': 'x86_64', 'device': 'cpu'}}).json()
    assert edge['status'] == 'unverified' and edge['environment_hash'] is None
    listed = client.get('/api/export/flow/preflights', params={'version_id': version}).json()['reports']
    assert {row['report_id'] for row in listed} == {edge['report_id'], report['report_id']}
    reopened = client.get(f"/api/export/flow/preflights/{report['report_id']}", params={'source_dataset_path': str(source),
                          'target': json.dumps({'kind': 'this_computer', 'device': 'cpu'})}).json()
    assert reopened['stale'] is False and reopened['report_id'] == report['report_id']
    moved = client.get(f"/api/export/flow/preflights/{report['report_id']}", params={'source_dataset_path': str(source),
                       'target': json.dumps({'kind': 'edge', 'profile': 'edge_cpu', 'os': 'linux', 'architecture': 'x86_64', 'device': 'cpu'})}).json()
    assert moved['stale'] is True and moved['stale_reasons'] == ['target_changed']
    (job_dir / 'best_model.pt').write_bytes(b'corrupt checkpoint')
    broken = client.post('/api/export/flow/preflight', json=body).json()
    model = next(r for r in broken['requirements'] if r['kind'] == 'model')
    assert model['state'] == 'mismatch' and model['node_id'] == 'node_crop' and 'retrain it' in model['remedy']
    assert broken['status'] == 'blocked' and set(broken['blocked_nodes']) == {'node_crop', 'node_decision', 'node_output'}
    assert client.post('/api/export/flow/preflight', json={**body, 'target': {'kind': 'server'}}).status_code == 422
    assert client.get('/api/export/flow/preflights/' + '0' * 32, params={'source_dataset_path': str(source)}).status_code == 404


@pytest.mark.parametrize('change', ['tamper', 'replacement'])
def test_a_saved_package_report_is_stale_when_checkpoint_bytes_change(tmp_path, change):
    package = _package(tmp_path)
    report = preflight.package_preflight(package, device='cpu')
    assert report['status'] == 'ready'
    stored = package / 'preflight' / f"{report['report_id']}.json"
    original = stored.read_bytes()
    checkpoint = package / 'models' / 'job_detector' / 'best_model.pt'
    if change == 'tamper':
        checkpoint.write_bytes(b'changed after the ready report')
    else:
        torch.save({'task': 'detection', 'model_state_dict': {}, 'replacement': True}, checkpoint)
    shown = preflight.latest_package_preflight(package, device='cpu')
    assert shown['report_id'] == report['report_id'] and shown['status'] == 'ready'
    assert shown['stale'] is True and shown['stale_reasons'] == ['artifacts_changed']
    assert stored.read_bytes() == original and len(list(stored.parent.glob('*.json'))) == 1
    assert preflight.package_preflight(package, device='cpu')['status'] == 'blocked'


def test_an_unchanged_blocked_package_report_stays_current_without_another_report(tmp_path):
    package = _package(tmp_path)
    (package / 'models' / 'job_detector' / 'best_model.pt').write_bytes(b'already changed')
    report = preflight.package_preflight(package, device='cpu')
    assert report['status'] == 'blocked'
    shown = preflight.latest_package_preflight(package, device='cpu')
    assert shown['report_id'] == report['report_id'] and shown['status'] == 'blocked'
    assert shown['stale'] is False and shown['stale_reasons'] == []
    assert len(list((package / 'preflight').glob('*.json'))) == 1


@pytest.mark.parametrize('change', ['checkpoint_tamper', 'checkpoint_replacement', 'calibration_replacement'])
def test_a_saved_flow_report_rechecks_its_model_and_calibration_evidence(tmp_path, monkeypatch, change):
    from backend.api import routes_dataset, routes_flowchart
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.flowchart_engine import FlowEdge, FlowNode, FlowNodeData
    from backend.engine.spatial_calibration import manual_calibration, project_calibration_store
    from backend.main import create_app
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    monkeypatch.setattr(routes_flowchart.training_job_manager, '_jobs', {})
    app = create_app(project_dir=str(tmp_path / 'workspace_registry'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        project = client.get('/api/project/current').json()
        source = tmp_path / 'source'
        source.mkdir()
        (source / 'image.jpg').write_bytes(b'owned synthetic source')
        job = 'job_saved_detector'
        folder = Path(project['models_dir']) / job
        (folder / 'dataset').mkdir(parents=True)
        checkpoint = folder / 'best_model.pt'
        torch.save({'task': 'detection', 'model_state_dict': {}}, checkpoint)
        (folder / 'model_meta.json').write_text(json.dumps({'task': 'detection'}))
        fingerprint = fingerprint_dataset(source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
                                          split_manifest=routes_dataset._split_manifest_file(source))
        (folder / 'job_receipt.json').write_text(json.dumps({'status': 'completed', 'task': 'detection',
            'source_dataset_path': str(source), 'dataset_fingerprint': fingerprint, 'dataset_path': str(folder / 'dataset')}))
        calibration = manual_calibration(.1, .1, camera_id='fixture-camera', acquisition_config={'resolution': [32, 32]},
                                         source_size=[32, 32], approved_by='fixture')
        store = project_calibration_store(project)
        store.save(calibration)
        pipeline = get_single_detection_flowchart(job_id=job)
        pipeline.nodes.append(FlowNode(id='node_measure', position={}, data=FlowNodeData(label='Width', node_type='measurement',
            params={'calibration_ref': calibration.ref, 'threshold_unit': 'mm', 'paths': []})))
        next(edge for edge in pipeline.edges if edge.source == 'node_crop').target = 'node_measure'
        pipeline.edges.append(FlowEdge(id='measure-decision', source='node_measure', target='node_decision'))
        saved = client.post('/api/flowchart/pipeline', params={'recipe_task': 'detection', 'source_dataset_path': str(source)},
                            json=pipeline.model_dump())
        assert saved.status_code == 200, saved.text
        body = {'source_dataset_path': str(source), 'recipe_task': 'detection', 'version_id': saved.json()['version_id'],
                'target': {'kind': 'this_computer', 'device': 'cpu'}}
        made = client.post('/api/export/flow/preflight', json=body)
        assert made.status_code == 200, made.text
        report = made.json()
        assert report['status'] == 'ready'
        params = {'source_dataset_path': str(source), 'target': json.dumps(body['target'])}
        url = f"/api/export/flow/preflights/{report['report_id']}"
        assert client.get(url, params=params).json()['stale'] is False
        stored = Path(project['project_dir']) / 'deployment_preflight' / f"{report['report_id']}.json"
        original = stored.read_bytes()
        if change == 'checkpoint_tamper':
            checkpoint.write_bytes(b'changed checkpoint')
        elif change == 'checkpoint_replacement':
            torch.save({'task': 'detection', 'model_state_dict': {}, 'replacement': True}, checkpoint)
        else:
            replacement = manual_calibration(.2, .2, camera_id='fixture-camera', acquisition_config={'resolution': [32, 32]},
                                             source_size=[32, 32], approved_by='fixture')
            (store.root / f"{calibration.ref.split(':')[-1]}.json").write_text(json.dumps(replacement.to_json()))
        shown = client.get(url, params=params).json()
        assert shown['report_id'] == report['report_id'] and shown['status'] == 'ready'
        assert shown['stale'] is True and shown['stale_reasons'] == ['artifacts_changed']
        assert stored.read_bytes() == original and len(list(stored.parent.glob('*.json'))) == 1
        fresh = client.post('/api/export/flow/preflight', json=body).json()
        if change == 'checkpoint_replacement':
            assert fresh['status'] == 'ready'
        else:
            assert fresh['status'] == 'blocked'
        reopened = client.get(f"/api/export/flow/preflights/{fresh['report_id']}", params=params).json()
        assert reopened['stale'] is False and reopened['status'] == fresh['status']
