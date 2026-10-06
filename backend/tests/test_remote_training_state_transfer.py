"""Exact epoch state crosses only a verified, single-process run boundary."""
import hashlib
import json
from pathlib import Path
import pytest
import torch

from backend.tests.test_extension_training_api import resume_checkpoint, client
from backend.tests.test_remote_worker import _spec, StubTrainer


def test_staging_uses_fixed_hash_bound_bytes_and_removes_host_path(tmp_path):
    from backend.remote.training_state_transfer import stage_training_state
    api, project, source = client(tmp_path)
    checkpoint = resume_checkpoint(project, source)
    original = checkpoint.read_bytes()
    options, envelope, transfer = stage_training_state(tmp_path / 'output', {'resume_checkpoint': str(checkpoint)}, task='classification', preset='fast')
    assert options == {'resume_checkpoint': 'training-state.pt'}
    assert envelope['sha256'] == hashlib.sha256(original).hexdigest()
    assert envelope['next_epoch'] == 1 and envelope['global_step'] == 2
    assert transfer[1] == 'training-state.pt' and transfer[0].read_bytes() == original
    assert str(checkpoint) not in json.dumps(envelope)


def test_worker_refuses_unbound_resume_before_trainer(tmp_path):
    from backend.remote.worker import run_train
    run, spec, _ = _spec(tmp_path)
    data = json.loads(spec.read_text()); data['config_overrides']['resume_checkpoint'] = '/other-project/state.pt'; spec.write_text(json.dumps(data))
    class Forbidden(StubTrainer):
        def __init__(self, **kwargs): pytest.fail('Unbound host state reached trainer')
    result = run_train(spec, trainer_factory=Forbidden)
    assert result['status'] == 'failed' and 'resume' in result['error'].lower()


@pytest.mark.parametrize('tamper', ['bytes', 'identity', 'warm-start', 'ddp'])
def test_worker_refuses_changed_or_incompatible_state_before_trainer(tmp_path, tamper):
    from backend.remote.training_state_transfer import stage_training_state
    from backend.remote.worker import run_train
    api, project, source = client(tmp_path)
    checkpoint = resume_checkpoint(project, source)
    (tmp_path / 'worker').mkdir()
    run, spec, _ = _spec(tmp_path / 'worker')
    options, envelope, transfer = stage_training_state(tmp_path / 'staging', {'resume_checkpoint': str(checkpoint)}, task='classification', preset='fast')
    (run / transfer[1]).write_bytes(transfer[0].read_bytes())
    data = json.loads(spec.read_text()); data.update(task='classification', training_state=envelope); data['config_overrides'].update(options)
    if tamper == 'bytes': (run / transfer[1]).write_bytes(b'changed')
    if tamper == 'identity': data['training_state']['identity_sha256'] = '0' * 64
    if tamper == 'warm-start': data['warm_start'] = {'checkpoint': 'parent.pt'}
    if tamper == 'ddp': data['distributed'] = {'processes': 2}
    spec.write_text(json.dumps(data))
    class Forbidden(StubTrainer):
        def __init__(self, **kwargs): pytest.fail('Invalid state reached trainer')
    result = run_train(spec, trainer_factory=Forbidden)
    assert result['status'] == 'failed', result


def test_staging_rejects_linked_input_and_keeps_original(tmp_path):
    from backend.remote.training_state_transfer import stage_training_state
    api, project, source = client(tmp_path); checkpoint = resume_checkpoint(project, source)
    linked = tmp_path / 'linked.pt'; linked.symlink_to(checkpoint)
    with pytest.raises(ValueError, match='linked|regular'):
        stage_training_state(tmp_path / 'output', {'resume_checkpoint': str(linked)}, task='classification', preset='fast')
    assert checkpoint.is_file()


def remote_resume_fixture(tmp_path):
    from backend.remote.profiles import ComputeProfile, get_profile_store
    api, project, source = client(tmp_path); checkpoint = resume_checkpoint(project, source)
    payload = torch.load(checkpoint, weights_only=True)
    from backend.engine.training_resume import dataset_content_sha256
    payload['identity']['dataset_content_sha256'] = dataset_content_sha256(source)
    torch.save(payload, checkpoint)
    profile = ComputeProfile(id='original', name='Original worker', ssh_target='contract.invalid', ssh_port=22,
        remote_root='/owned/modu', runtime_kind='python', runtime_value='python3')
    get_profile_store().save(profile)
    journal = {'profile': profile.model_dump(), 'job_id': checkpoint.parent.name, 'state': 'aborted',
        'launch_spec': {'project_id': project['id']},
        'worker_exit_confirmed': True, 'task': 'classification', 'input_manifest_sha256': 'a' * 64}
    receipt = {'protocol_version': 1, 'operation': 'train', 'job_id': checkpoint.parent.name, 'input_manifest_sha256': 'a' * 64,
        'path': 'outputs/latest_training_state.pt', 'size': checkpoint.stat().st_size,
        'sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
    (checkpoint.parent / 'remote_job.json').write_text(json.dumps(journal))
    (checkpoint.parent / 'training_state_receipt.json').write_text(json.dumps(receipt))
    return api, project, source, checkpoint, journal, profile


def test_remote_resume_list_requires_unchanged_profile_receipt_and_terminal_exit(tmp_path):
    api, project, source, checkpoint, journal, profile = remote_resume_fixture(tmp_path)
    params = {'dataset_path': str(source), 'task': 'classification', 'compute_profile_id': profile.id}
    assert api.get('/api/training/resume-states', params=params).json()['states'][0]['compute_profile_id'] == profile.id
    assert api.get('/api/training/resume-states', params={k: v for k, v in params.items() if k != 'compute_profile_id'}).json()['states'] == []
    journal['worker_exit_confirmed'] = False
    (checkpoint.parent / 'remote_job.json').write_text(json.dumps(journal))
    assert api.get('/api/training/resume-states', params=params).json()['states'] == []
    journal['worker_exit_confirmed'] = True; journal['launch_spec']['project_id'] = 'other-project'
    (checkpoint.parent / 'remote_job.json').write_text(json.dumps(journal))
    assert api.get('/api/training/resume-states', params=params).json()['states'] == []
    journal['launch_spec']['project_id'] = project['id']
    journal['worker_exit_confirmed'] = True; journal['profile']['ssh_target'] = 'different.invalid'
    (checkpoint.parent / 'remote_job.json').write_text(json.dumps(journal))
    assert api.get('/api/training/resume-states', params=params).json()['states'] == []


def test_remote_resume_tamper_is_refused_before_compute_probe(tmp_path, monkeypatch):
    api, project, source, checkpoint, journal, profile = remote_resume_fixture(tmp_path)
    checkpoint.write_bytes(checkpoint.read_bytes() + b'changed')
    monkeypatch.setattr('backend.remote.ssh_transport.SSHTransport.probe', lambda *a: pytest.fail('Changed state reached compute probe'))
    response = api.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source),
        'compute_profile_id': profile.id, 'config_overrides': {'resume_checkpoint': str(checkpoint)}})
    assert response.status_code == 422 and 'receipt' in str(response.json()).lower()


def test_terminal_aborted_worker_returns_epoch_state_without_marking_model_completed(tmp_path, monkeypatch):
    from backend.api.routes_training import JobRecord
    from backend.remote.profiles import ComputeProfile
    from backend.tests.test_remote_coordinator import FakeRemote
    from backend.remote.coordinator import run_remote_training
    from backend.remote.worker import run_train
    api, project, source = client(tmp_path); checkpoint = resume_checkpoint(project, source)
    output = tmp_path / 'result' / 'job_epoch_123'
    profile = ComputeProfile(id='epoch-contract', name='Epoch contract', ssh_target='contract.invalid', ssh_port=22,
        remote_root=str(tmp_path / 'server'), runtime_kind='python', runtime_value='python3')
    record = JobRecord(job_id=output.name, task='classification', preset='fast', dataset_path=str(source),
        output_dir=str(output), status='running', remote_profile_id=profile.id, source_dataset_path=str(source))
    class PartialTrainer(StubTrainer):
        def train(self, job_id):
            (self.output_dir / 'latest_training_state.pt').write_bytes(checkpoint.read_bytes())
            return {'status': 'aborted'}
    class Remote(FakeRemote):
        def launch(self, profile, argv, run_id):
            self.launches += 1
            result = run_train(self.root / 'runs' / run_id / 'spec.json', trainer_factory=PartialTrainer)
            assert result['status'] == 'aborted', result
            return 'fake-pid'
    result = run_remote_training(record, profile, transport=Remote(Path(profile.remote_root)), config_overrides={'pretrained': False})
    assert result['status'] == 'aborted' and result['worker_exit_confirmed'] is True
    assert (output / 'latest_training_state.pt').read_bytes() == checkpoint.read_bytes()
    assert (output / 'training_state_receipt.json').is_file()
    assert not (output / 'best_model.pt').exists(), 'Aborted work cannot be a completed model'


def test_relocated_snapshot_restores_only_when_names_bytes_recipe_and_device_match(tmp_path):
    import shutil
    from backend.tests.test_extension_resume_state import components, seed, step
    from backend.engine.training_resume import save_training_state, restore_training_state, dataset_content_sha256
    source = tmp_path / 'old'; source.mkdir(); (source / 'a.png').write_bytes(b'exact source image bytes')
    relocated = tmp_path / 'new'; shutil.copytree(source, relocated)
    identity = {'task': 'classification', 'dataset_fingerprint': 'old-location', 'recipe': {'epochs': 3},
        'dataset_content_sha256': dataset_content_sha256(source), 'device': 'cpu'}
    seed(); original = components(); step(*original)
    checkpoint = tmp_path / 'state.pt'
    save_training_state(checkpoint, *original[:4], identity=identity, next_epoch=1, global_step=1, early_stopping=original[4])
    expected = step(*original)
    destination = {**identity, 'dataset_fingerprint': 'new-location', 'dataset_content_sha256': dataset_content_sha256(relocated)}
    restored = components()
    with pytest.raises(ValueError, match='identity'):
        restore_training_state(checkpoint, *restored[:4], identity=destination, early_stopping=restored[4])
    restore_training_state(checkpoint, *restored[:4], identity=destination, early_stopping=restored[4], allow_snapshot_relocation=True)
    assert step(*restored) == expected
    (relocated / 'a.png').write_bytes(b'changed source image bytes')
    destination['dataset_content_sha256'] = dataset_content_sha256(relocated)
    before = restored[0][0].weight.detach().clone()
    with pytest.raises(ValueError, match='content'):
        restore_training_state(checkpoint, *restored[:4], identity=destination, early_stopping=restored[4], allow_snapshot_relocation=True)
    torch.testing.assert_close(before, restored[0][0].weight, rtol=0, atol=0)


def test_direct_training_persists_remote_project_owner_and_admission_controls(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.remote.profiles import ComputeProfile, get_profile_store
    api, project, source = client(tmp_path)
    profile = ComputeProfile(id='scope', name='Owned scope', ssh_target='contract.invalid', ssh_port=22, remote_root='/owned/control', runtime_kind='python', runtime_value='python3')
    get_profile_store().save(profile)
    monkeypatch.setattr('backend.remote.ssh_transport.SSHTransport.probe', lambda *a: {'ready': True, 'runtime_ready': True,
        'checks': {'device_type': 'cpu', 'runtime_dependencies': {'timm': True, 'safetensors': True, 'huggingface_hub': True}}})
    captured = []
    def capture(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(status='queued', phase='queued')
    monkeypatch.setattr('backend.api.routes_training.training_job_manager.start_remote_job', capture)
    response = api.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source),
        'compute_profile_id': profile.id, 'device': 'cpu', 'queue': True, 'priority': 0, 'max_runtime_s': 30,
        'config_overrides': {'backbone': 'dinov3_vits16', 'pretrained': False, 'epochs': 2, 'image_size': 64}})
    assert response.status_code == 200, response.text
    launch = captured[0]['launch_spec']
    assert launch['project_id'] == project['id']
    assert launch['priority'] == 0 and launch['max_runtime_s'] == 30
    assert captured[0]['queue_when_busy'] is True


def test_terminal_training_state_refuses_missing_input_binding_before_download(tmp_path):
    import json
    import subprocess
    from backend.remote.coordinator import _copy_training_state, ArtifactValidationError
    from backend.remote.profiles import ComputeProfile
    class UnboundReceipt:
        def exec(self, profile, argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, json.dumps({'protocol_version': 1,
                'job_id': 'owned', 'operation': 'train', 'input_manifest_sha256': None}), '')
        def download(self, *args):
            pytest.fail('Unbound terminal state must not be downloaded')
    profile = ComputeProfile(id='bound', name='Bound', ssh_target='contract.invalid', ssh_port=22,
        remote_root='/owned/control', runtime_kind='python', runtime_value='python3')
    with pytest.raises(ArtifactValidationError, match='manifest'):
        _copy_training_state(UnboundReceipt(), profile, {'job_id': 'owned', 'operation': 'train'}, tmp_path)


def test_cancelled_worker_with_corrupt_epoch_state_records_failure(tmp_path):
    from backend.remote.worker import run_train
    run, spec, _ = _spec(tmp_path)
    class CancelledWithBadState(StubTrainer):
        def train(self, job_id):
            self.output_dir.joinpath('latest_training_state.pt').write_bytes(b'corrupt cancelled epoch')
            raise InterruptedError('Owned cancellation')
    result = run_train(spec, trainer_factory=CancelledWithBadState)
    assert result['status'] == 'failed'
    assert 'state' in result['error'].lower()
    assert json.loads((run / 'status.json').read_text())['status'] == 'failed'
    assert not (run / 'training_state_artifact.json').exists()


def test_malformed_epoch_identity_is_not_offered_or_launched(tmp_path):
    api, project, source = client(tmp_path)
    checkpoint = resume_checkpoint(project, source)
    state = torch.load(checkpoint, weights_only=True)
    state['identity'] = []
    torch.save(state, checkpoint)
    listed = api.get('/api/training/resume-states', params={'dataset_path': str(source), 'task': 'classification'})
    assert listed.status_code == 200 and listed.json()['states'] == []
    started = api.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source),
        'config_overrides': {'resume_checkpoint': str(checkpoint)}})
    assert started.status_code == 422 and 'state' in started.json()['detail'].lower()
