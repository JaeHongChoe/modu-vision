"""Selected package parity crosses only the transport boundary in these tests."""
import json
import hashlib
from pathlib import Path, PurePosixPath

import pytest

from backend.remote.profiles import ComputeProfile, get_profile_store
from backend.tests.test_flow_export_release import _project_flow, _export, _library_parity
from backend.tests.test_remote_coordinator import FakeRemote


class PackageWorkerTransport(FakeRemote):
    """Controlled SSH boundary: run the production worker on a local fake server."""
    def exec(self, profile, argv, **kwargs):
        # The selected Linux server uses POSIX paths even from a Windows client.
        # Translate only fake transport filesystem commands into its local root.
        if argv[0] in {'mkdir', 'cat'}:
            relative = PurePosixPath(argv[-1]).relative_to(profile.remote_root)
            argv = [*argv[:-1], str(self.root / Path(*relative.parts))]
        return super().exec(profile, argv, **kwargs)

    def launch(self, profile, argv, run_id):
        from backend.remote.package_parity import run_package_parity
        self.launches += 1
        result = run_package_parity(self.root / 'runs' / run_id / 'spec.json')
        assert result['status'] in {'completed', 'failed'}, result
        return 'owned-package-worker'


def setup_remote(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    client, project, source, images, _ = _project_flow(tmp_path, monkeypatch, count=2)
    profile = ComputeProfile(id='package-target', name='Selected package server', ssh_target='selected-host',
                             ssh_port=22, remote_root='/controlled/package-target', runtime_kind='python', runtime_value='python3')
    get_profile_store().save(profile)
    remote = PackageWorkerTransport(tmp_path / 'remote')
    from backend.remote import operations
    monkeypatch.setattr(operations, 'SSHTransport', lambda: remote)
    return client, project, source, images, profile, remote


def test_export_cohort_runs_on_explicit_remote_target_and_keeps_target_receipt(tmp_path, monkeypatch):
    client, project, source, images, profile, remote = setup_remote(tmp_path, monkeypatch)
    response = _export(client, source, 'remote_cohort', parity_images=[{'path': str(p)} for p in images],
                       parity_device='cpu', compute_profile_id=profile.id)
    assert response.status_code == 200, response.text
    report = response.json()['parity']
    assert remote.launches == 1
    assert report['status'] == 'passed' and report['completed_count'] == 2
    assert report['execution_target'] == 'selected_compute' and report['compute_profile_id'] == profile.id
    assert report['device'] == report['resolved_device'] == 'cpu'
    assert report['packaged_runtime']['independent_process'] is True
    reference = report['reference_runtime']['runtime_device_identity']
    assert reference['gpu_uuid'] is None and reference['device'] == 'cpu'
    assert all(row['packaged']['runtime_device_identity']['process_id'] != reference['process_id'] for row in report['images'])
    assert all(row['image_path'] == str(images[index]) for index, row in enumerate(report['images']))
    assert _library_parity(project, response.json()['package_path'])['compute_profile_id'] == profile.id
    assert json.loads((Path(response.json()['package_path']) / 'parity_receipt.json').read_text(encoding='utf-8')) == {'schema_version': 1, **report}
    journal = json.loads(next((Path(project['reports_dir']) / 'remote_package_parity').rglob('package_parity_*.json')).read_text(encoding='utf-8'))
    assert journal['state'] == 'completed' and journal['worker_exit_confirmed'] is True
    from backend.engine.shared_scheduler import shared_leases
    assert shared_leases().list() == []


@pytest.mark.parametrize('damage', ['missing_profile', 'mps', 'cuda_without_gpu', 'cpu_budget', 'sharing', 'distributed', 'single'])
def test_unsupported_targets_fail_before_transfer(tmp_path, monkeypatch, damage):
    client, project, source, images, profile, remote = setup_remote(tmp_path, monkeypatch)
    request = {'parity_images': [{'path': str(p)} for p in images], 'parity_device': 'cpu', 'compute_profile_id': profile.id}
    if damage == 'missing_profile':
        request['compute_profile_id'] = 'removed'
    elif damage in ('mps', 'cuda_without_gpu'):
        device = 'mps' if damage == 'mps' else 'cuda:0'
        request.update(parity_device=device, runtime_config={'device': device})
    elif damage == 'cpu_budget':
        get_profile_store().save(profile.model_copy(update={'gpu_selector': '2', 'memory_budget_mb': 8192}))
    elif damage == 'sharing':
        get_profile_store().save(profile.model_copy(update={'gpu_selector': '2', 'memory_budget_mb': 8192, 'allow_sharing': True}))
    elif damage == 'distributed':
        get_profile_store().save(profile.model_copy(update={'gpu_selector': '2,3', 'distributed_processes': 2}))
    else:
        request = {'verification_image_path': str(images[0]), 'compute_profile_id': profile.id}
    response = _export(client, source, 'unsupported', **request)
    assert response.status_code in (404, 422), response.text
    assert remote.launches == 0 and not (remote.root / 'runs').exists()
    assert not (Path(project['project_dir']) / 'exports' / 'flows' / 'unsupported').exists()


@pytest.mark.parametrize('damage', ['archive', 'image'])
def test_uploaded_bytes_changed_are_rejected_by_worker(tmp_path, monkeypatch, damage):
    client, project, source, images, profile, remote = setup_remote(tmp_path, monkeypatch)
    original = remote.upload
    def upload(selected, local, relative, **kwargs):
        result = original(selected, local, relative, **kwargs)
        if relative.endswith('inputs/package.tar.gz') and damage == 'archive' or '/inputs/images/' in relative and damage == 'image':
            (remote.root / relative).write_bytes(b'changed uploaded bytes')
        return result
    remote.upload = upload
    response = _export(client, source, 'changed_upload', parity_images=[{'path': str(p)} for p in images],
                       parity_device='cpu', compute_profile_id=profile.id)
    assert response.status_code == 409, response.text
    report = response.json()['detail']['parity']
    assert report['status'] == 'failed' and ('hash' in report['error'] or 'size' in report['error'])
    assert report['execution_target'] == 'selected_compute'
    assert _library_parity(project, response.json()['detail']['package_path'])['status'] == 'failed'


@pytest.mark.parametrize('damage', ['profile', 'device', 'missing_identity', 'same_pid', 'artifact_hash', 'no_execution'])
def test_received_report_cannot_promote_wrong_target_or_unproven_runtime(tmp_path, monkeypatch, damage):
    client, project, source, images, profile, remote = setup_remote(tmp_path, monkeypatch)
    original = remote.launch
    def launch(*args):
        handle = original(*args)
        run = remote.root / 'runs' / args[2]
        output = run / 'outputs' / 'parity_report.json'
        report = json.loads(output.read_text(encoding='utf-8'))
        if damage == 'profile': report['compute_profile_id'] = 'other-server'
        elif damage == 'device': report['device'] = 'cuda:0'
        elif damage == 'missing_identity': report['images'][0]['packaged'].pop('runtime_device_identity')
        elif damage == 'same_pid': report['images'][0]['packaged']['runtime_device_identity']['process_id'] = report['reference_runtime']['runtime_device_identity']['process_id']
        elif damage == 'no_execution':
            for row in report['images']: row['status'] = 'not_run'
            report['completed_count'] = 0
        else: output.write_text('changed report', encoding='utf-8'); return handle
        output.write_text(json.dumps(report), encoding='utf-8')
        manifest = json.loads((run / 'artifacts.json').read_text(encoding='utf-8'))
        manifest['artifacts'][0].update(size=output.stat().st_size, sha256=hashlib.sha256(output.read_bytes()).hexdigest())
        (run / 'artifacts.json').write_text(json.dumps(manifest), encoding='utf-8')
        return handle
    remote.launch = launch
    response = _export(client, source, 'wrong_receipt', parity_images=[{'path': str(p)} for p in images],
                       parity_device='cpu', compute_profile_id=profile.id)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['parity']['status'] == 'failed'


def test_cuda_identity_requires_observed_uuid_and_preserves_logical_zero_budget(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import torch
    from backend.engine.runtime_device_identity import runtime_device_identity
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '2')
    monkeypatch.setenv('VISION_PACKAGE_PARITY_CUDA_BUDGET_MB', '4096')
    calls = []
    monkeypatch.setattr(torch.cuda, 'get_device_properties', lambda index: SimpleNamespace(uuid='GPU-selected-2', name='Fixture GPU', total_memory=8192*1024*1024))
    monkeypatch.setattr(torch.cuda, 'set_per_process_memory_fraction', lambda fraction, index: calls.append((fraction, index)))
    identity = runtime_device_identity('cuda:0')
    assert identity['logical_cuda_index'] == 0 and identity['cuda_visible_devices'] == '2'
    assert identity['gpu_uuid'] == 'GPU-selected-2' and identity['memory_budget_mb'] == 4096
    assert calls == [(0.5, 0)]
    monkeypatch.setattr(torch.cuda, 'get_device_properties', lambda index: SimpleNamespace(uuid=None, name='GPU', total_memory=8192*1024*1024))
    with pytest.raises(ValueError, match='UUID'): runtime_device_identity('cuda:0')


def test_cuda_same_device_proof_rejects_wrong_or_absent_gpu_uuid():
    from backend.remote.package_parity import _device_evidence_error
    reference = {'device': 'cuda:0', 'gpu_uuid': 'GPU-2', 'process_id': 101, 'memory_budget_mb': 4096}
    report = {'reference_runtime': {'runtime_device_identity': reference}, 'packaged_runtime': {'independent_process': True},
              'images': [{'status': 'passed', 'packaged': {'runtime_device_identity': {**reference, 'process_id': 102}}}]}
    assert _device_evidence_error(report, 'cuda:0') is None
    report['images'][0]['packaged']['runtime_device_identity']['gpu_uuid'] = 'GPU-3'
    assert 'different' in _device_evidence_error(report, 'cuda:0')
    report['reference_runtime']['runtime_device_identity']['gpu_uuid'] = None
    assert 'UUID' in _device_evidence_error(report, 'cuda:0')


@pytest.mark.parametrize('damage', ['source_image', 'source_checkpoint', 'profile_changed'])
def test_original_source_or_profile_changed_during_worker_cannot_pass(tmp_path, monkeypatch, damage):
    client, project, source, images, profile, remote = setup_remote(tmp_path, monkeypatch)
    original = remote.launch
    def launch(*args):
        handle = original(*args)
        if damage == 'source_image': images[0].write_bytes(b'changed original after worker')
        elif damage == 'source_checkpoint':
            checkpoint = next(Path(project['models_dir']).glob('*/best_model.pt'))
            checkpoint.write_bytes(b'changed checkpoint after worker')
        else: get_profile_store().save(profile.model_copy(update={'ssh_target': 'different-host'}))
        return handle
    remote.launch = launch
    response = _export(client, source, 'source_changed', parity_images=[{'path': str(p)} for p in images],
                       parity_device='cpu', compute_profile_id=profile.id)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['parity']['status'] == 'failed'


def test_existing_compute_owner_blocks_package_execution_before_transfer(tmp_path, monkeypatch):
    client, project, source, images, profile, remote = setup_remote(tmp_path, monkeypatch)
    from backend.engine.shared_scheduler import shared_leases
    leases = shared_leases()
    assert leases.acquire('existing-training', 'ssh:selected-host:22', 'all', remote=True)
    response = _export(client, source, 'busy', parity_images=[{'path': str(p)} for p in images],
                       parity_device='cpu', compute_profile_id=profile.id)
    assert response.status_code == 409 and remote.launches == 0
    assert leases.list()[0]['job_id'] == 'existing-training'


def test_ambiguous_launch_reconnects_exact_same_package_worker_without_duplicate(tmp_path, monkeypatch):
    client, project, source, images, profile, remote = setup_remote(tmp_path, monkeypatch)
    from backend.remote.coordinator import _sha256
    from backend.remote.ssh_transport import SSHTransport
    from backend.remote.package_parity import verify_package_on_compute
    from backend.engine.flow_package_runtime import verify_flow_package
    from backend.engine.shared_scheduler import shared_leases
    original = remote.launch
    def launch(*args):
        original(*args)
        run = remote.root / 'runs' / args[2]
        spec = json.loads((run / 'spec.json').read_text(encoding='utf-8'))
        token = 'a' * 32
        (run / 'worker_identity.json').write_text(json.dumps({'protocol_version': 1, 'run_id': run.name,
            'job_id': spec['job_id'], 'operation': 'package_parity', 'spec_sha256': _sha256(run / 'spec.json'),
            'control_kind': 'python', 'control_handle': '101:' + token,
            'pid': 101, 'group': 101, 'session': 101, 'token': token}), encoding='utf-8')
        raise ConnectionError('Lost launch response after owned worker completed')
    remote.launch = launch
    remote.recover_handle = lambda *args, **kwargs: SSHTransport.recover_handle(remote, *args, **kwargs)
    response = _export(client, source, 'reconnect', parity_images=[{'path': str(p)} for p in images],
                       parity_device='cpu', compute_profile_id=profile.id)
    assert response.status_code == 409 and remote.launches == 1
    assert shared_leases().list()[0]['uncertain'] == 1
    package = Path(response.json()['detail']['package_path'])
    pipeline, packaged_models = verify_flow_package(package)
    checkpoints = {job: Path(project['models_dir']) / job / 'best_model.pt' for job in packaged_models}
    result = verify_package_on_compute(profile, project, package_dir=package, pipeline=pipeline, checkpoints=checkpoints,
                                      images=[{'path': str(p)} for p in images], device='cpu', transport=remote)
    assert result['status'] == 'passed' and remote.launches == 1
    assert shared_leases().list() == []
    journal = json.loads(next((Path(project['reports_dir']) / 'remote_package_parity').rglob('package_parity_*.json')).read_text(encoding='utf-8'))
    assert journal['launch_acknowledgment_recovered'] is True and journal['worker_exit_confirmed'] is True
