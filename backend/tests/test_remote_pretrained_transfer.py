"""New default weights reach a run as verified bytes, never a host-local path."""
import hashlib
import json
from pathlib import Path

import pytest

from backend.remote import ssh_transport
from backend.remote.coordinator import run_remote_training
from backend.remote.worker import run_train
from backend.tests.test_remote_coordinator import FakeRemote, _setup
from backend.tests.test_remote_worker import StubTrainer, _spec


@pytest.mark.parametrize('task,overrides,missing', [
    ('classification', {}, 'timm'), ('segmentation', {}, 'safetensors'),
    ('detection', {}, 'ultralytics'), ('classification', {'backbone': 'dinov3_vitb16'}, 'timm')])
def test_model_specific_remote_readiness_rejects_missing_new_dependencies(task, overrides, missing):
    readiness = {'ready': True, 'runtime_ready': True, 'checks': {'runtime_dependencies': {
        'timm': True, 'safetensors': True, 'huggingface_hub': True, 'ultralytics': True}}}
    readiness['checks']['runtime_dependencies'][missing] = False
    with pytest.raises(ValueError, match=missing):
        ssh_transport.require_training_runtime(readiness, task, 'fast', overrides)


def test_remote_coordinator_transfers_and_pins_explicit_pretrained_weights(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    _, output, profile, record = _setup(tmp_path)
    weights = tmp_path / 'host only.safetensors'; weights.write_bytes(b'owned pretrained fixture bytes')
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    class WeightRemote(FakeRemote):
        def launch(self, profile, argv, run_id):
            run = self.root / 'runs' / run_id
            spec = json.loads((run / 'spec.json').read_text())
            assert spec['config_overrides']['pretrained_checkpoint'] == 'pretrained.safetensors'
            assert spec['config_overrides']['pretrained_sha256'] == digest
            assert spec['pretrained_weights']['sha256'] == digest
            assert (run / 'pretrained.safetensors').read_bytes() == weights.read_bytes()
            assert str(weights) not in (run / 'spec.json').read_text()
            return super().launch(profile, argv, run_id)
    result = run_remote_training(record, profile, config_overrides={
        'model_name': 'dinov3_vits16', 'pretrained_checkpoint': str(weights), 'pretrained_sha256': digest},
        transport=WeightRemote(Path(profile.remote_root)))
    assert result['status'] == 'completed'


def test_invalid_local_pretrained_hash_never_launches_remote_worker(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    _, _, profile, record = _setup(tmp_path)
    weights = tmp_path / 'weights.pt'; weights.write_bytes(b'fixture')
    remote = FakeRemote(Path(profile.remote_root))
    result = run_remote_training(record, profile, config_overrides={'pretrained_checkpoint': str(weights),
        'pretrained_sha256': '0' * 64}, transport=remote)
    assert result['status'] == 'failed'
    assert 'hash' in result['error'].lower() or 'sha256' in result['error'].lower()
    assert remote.launches == 0


@pytest.mark.parametrize('tamper', [False, True])
def test_worker_verifies_and_rebases_pretrained_input_before_constructing_trainer(tmp_path, tamper):
    run, spec, _ = _spec(tmp_path)
    weights = run / 'pretrained.safetensors'; weights.write_bytes(b'fixture pretrained weights')
    payload = json.loads(spec.read_text())
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    payload['pretrained_weights'] = {'checkpoint': 'pretrained.safetensors', 'sha256': digest,
                                    'source': 'local:fixture.safetensors', 'model': 'dinov3_vits16'}
    payload['config_overrides'].update(backbone='dinov3_vits16', pretrained_checkpoint='pretrained.safetensors', pretrained_sha256=digest)
    spec.write_text(json.dumps(payload))
    if tamper: weights.write_bytes(b'changed')
    observed = []
    class WeightTrainer(StubTrainer):
        def __init__(self, **kw):
            observed.append(kw['config_overrides'])
            super().__init__(**kw)
    result = run_train(spec, trainer_factory=WeightTrainer)
    if tamper:
        assert result['status'] == 'failed'
        assert observed == []
    else:
        assert result['status'] == 'completed'
        assert observed[0]['pretrained_checkpoint'] == str(weights)
        assert observed[0]['pretrained_origin'] == 'local:fixture.safetensors'


def test_worker_rejects_unbound_absolute_pretrained_path(tmp_path):
    run, spec, _ = _spec(tmp_path)
    outside = tmp_path / 'outside.pt'; outside.write_bytes(b'fixture')
    payload = json.loads(spec.read_text()); payload['config_overrides']['pretrained_checkpoint'] = str(outside)
    spec.write_text(json.dumps(payload))
    result = run_train(spec, trainer_factory=StubTrainer)
    assert result['status'] == 'failed'
    assert 'pretrained' in result['error'].lower()


@pytest.mark.parametrize('missing', ['timm', 'safetensors', 'huggingface_hub', 'ultralytics'])
def test_general_runtime_can_be_ready_while_selected_model_dependency_remains_blocked(tmp_path, monkeypatch, missing):
    import subprocess
    from backend.tests.test_remote_profiles import profile_data
    from backend.remote.profiles import ComputeProfile
    checks = {'protocol_version': 1, 'remote_root_exists': True, 'free_bytes': 2_000_000_000,
              'device_type': 'cpu', 'device_name': 'CPU', 'model_dependencies': {'dinov3_vits16': True, 'yolo26n': True},
              'pretrained_weights': {name: {'ok': True} for name in ssh_transport._REQUIRED_WEIGHTS},
              'runtime_dependencies': {name: True for name in ['torch', 'torchvision', 'cv2', 'numpy', 'PIL', 'sklearn',
                                      'psutil', 'fastapi', 'pydantic', 'timm', 'safetensors', 'huggingface_hub', 'ultralytics']}}
    checks['runtime_dependencies'][missing] = False
    monkeypatch.setattr(ssh_transport.SSHTransport, 'exec', lambda *a, **kw: subprocess.CompletedProcess([], 0, json.dumps(checks), ''))
    result = ssh_transport.SSHTransport().probe(ComputeProfile(**profile_data()))
    assert result['ready'] is True
    assert result['runtime_ready'] is True
    assert result['checks']['runtime_dependencies'][missing] is False
    task = 'detection' if missing == 'ultralytics' else 'classification'
    with pytest.raises(ValueError, match=missing):
        ssh_transport.require_training_runtime(result, task, 'fast')


def test_default_dino_and_yolo_are_available_without_unrelated_legacy_weight_cache(tmp_path, monkeypatch):
    import subprocess
    from backend.tests.test_remote_profiles import profile_data
    from backend.remote.profiles import ComputeProfile
    checks = {'protocol_version': 1, 'remote_root_exists': True, 'free_bytes': 2_000_000_000,
              'device_type': 'cpu', 'device_name': 'CPU', 'model_dependencies': {'dinov3_vits16': True, 'yolo26n': True},
              'pretrained_weights': {},
              'runtime_dependencies': {name: True for name in ['torch', 'torchvision', 'cv2', 'numpy', 'PIL', 'sklearn',
                                      'psutil', 'fastapi', 'pydantic', 'timm', 'safetensors', 'huggingface_hub', 'ultralytics']}}
    monkeypatch.setattr(ssh_transport.SSHTransport, 'exec', lambda *a, **kw: subprocess.CompletedProcess([], 0, json.dumps(checks), ''))
    result = ssh_transport.SSHTransport().probe(ComputeProfile(**profile_data()))
    assert result['ready'] is True
    for task in ('classification', 'segmentation', 'detection'):
        ssh_transport.require_training_runtime(result, task, 'fast')


def test_worker_rejects_pretrained_model_identity_conflicting_with_selected_backbone(tmp_path):
    run, spec, _ = _spec(tmp_path)
    weights = run / 'pretrained.pt'; weights.write_bytes(b'fixture')
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    payload = json.loads(spec.read_text())
    payload['pretrained_weights'] = {'checkpoint': 'pretrained.pt', 'sha256': digest, 'source': 'local:fixture.pt', 'model': 'yolo26n'}
    payload['config_overrides'].update(backbone='dinov3_vits16', pretrained_checkpoint='pretrained.pt', pretrained_sha256=digest)
    spec.write_text(json.dumps(payload))
    result = run_train(spec, trainer_factory=StubTrainer)
    assert result['status'] == 'failed'
    assert 'architecture' in result['error'].lower()
