"""Selected-profile trial ownership and worker measurement contracts; no SSH/GPU."""
import hashlib
import json
from pathlib import Path
import threading

import pytest
import torch
from PIL import Image


def data(root):
    for split in ('train', 'val'):
        for label, color in [('good', 'white'), ('defect', 'black')]:
            folder = root / split / label; folder.mkdir(parents=True)
            Image.new('RGB', (32, 32), color).save(folder / 'sample.png')


def profile():
    from backend.remote.profiles import ComputeProfile
    return ComputeProfile(id='selected-worker', name='Contract worker', ssh_target='user@contract.invalid', ssh_port=22,
                          remote_root='/owned/modu', runtime_kind='python', runtime_value='python', gpu_selector='0')


def test_selected_remote_profile_dispatches_owned_candidates_and_never_local_fallback(tmp_path, monkeypatch):
    import backend.engine.automated_trials as trials
    import backend.remote.coordinator as coordinator
    from backend.api.routes_training import TrainingJobManager
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.remote.profiles import get_profile_store
    selected = profile(); get_profile_store().save(selected)
    monkeypatch.setattr('backend.remote.ssh_transport.SSHTransport.probe', lambda *args: {'ready': True, 'runtime_ready': True,
        'checks': {'device_type': 'cuda', 'runtime_dependencies': {'timm': True, 'safetensors': True, 'huggingface_hub': True}}})
    manager = TrainingJobManager(); manager._leases = ResourceLeases(tmp_path / 'leases.sqlite')
    monkeypatch.setattr('backend.api.routes_training.training_job_manager', manager)
    spec = trials._RUNNERS['classification']
    monkeypatch.setitem(trials._RUNNERS, 'classification', trials.TaskRunner(lambda _: pytest.fail('Local training runner used for remote search'), spec.architectures))
    observed = []
    def remote_runner(selected_profile, launch):
        def run(record):
            observed.append((selected_profile.id, record.job_id, dict(launch)))
            directory = Path(record.output_dir); directory.mkdir(parents=True, exist_ok=True)
            measurement = {'metrics': {'val_loss': .4}, 'latency_ms': 1.25, 'epochs_completed': 1,
                           'latency_scope': 'heldout_image_model_forward_only'}
            payload = {'task': record.task, 'model_state_dict': {'weight': torch.ones(2, 3)},
                       'training_config': launch['config_overrides'], 'measured_candidate': measurement}
            torch.save(payload, directory / 'best_model.pt')
            (directory / 'model_meta.json').write_text(json.dumps(payload | {'model_state_dict': None}))
            return {'status': 'completed', 'best_metric': .4, 'worker_exit_confirmed': True}
        return run
    monkeypatch.setattr(coordinator, 'make_remote_runner', remote_runner)
    source = tmp_path / 'data'; data(source)
    result = trials.run_automated_training(task='classification', dataset_path=source, models_dir=tmp_path / 'models',
        mode='quick', device='cuda:0', compute_profile_id=selected.id, remote_owner={'project_id': 'P', 'account_id': 'A'},
        base_config={'image_size': 32, 'batch_size': 1}, epochs_per_trial=1,
        budget={'max_trials': 1, 'max_total_epochs': 1, 'max_seconds': 10})
    assert result['status'] == 'completed', result
    assert len(observed) == 1 and observed[0][0] == 'selected-worker'
    launch = observed[0][2]
    assert launch['parent_search_id'] == result['search_id']
    assert launch['project_id'] == 'P' and launch['account_id'] == 'A'
    assert launch['measured_candidate'] is True
    assert result['compute_profile_id'] == 'selected-worker'
    assert result['winner']['compute_profile_id'] == 'selected-worker'
    assert result['winner']['latency_ms'] == 1.25
    receipt = json.loads((Path(result['winner']['checkpoint_path']).parent / 'job_receipt.json').read_text())
    assert receipt['compute_profile_id'] == 'selected-worker'
    assert receipt['search_id'] == result['search_id']


def test_remote_search_rejects_unsupported_device_and_ddp_before_any_job(tmp_path):
    from backend.engine.remote_automated_trials import validate_remote_search
    selected = profile()
    with pytest.raises(ValueError, match='MPS|device'):
        validate_remote_search(selected, 'classification', 'fast', 'mps', {}, {}, {'max_trials': 1})
    distributed = selected.model_copy(update={'distributed_processes': 2, 'gpu_selector': '0,1'})
    with pytest.raises(ValueError, match='distributed|DDP'):
        validate_remote_search(distributed, 'classification', 'fast', 'cuda:0', {}, {}, {'max_trials': 1})
    with pytest.raises(ValueError, match='transfer|OBB|local'):
        validate_remote_search(selected, 'rotated_detection', 'fast', 'cuda:0',
            {'recipe': {'adapter': 'ultralytics_yolo_obb', 'model_path': '/local/weights.pt'}}, {}, {'max_trials': 1})


def test_remote_parent_cancel_reaches_only_its_owned_trial(tmp_path, monkeypatch):
    from backend.engine.remote_automated_trials import run_remote_candidate
    from backend.engine.automated_trials import TrialContext
    from backend.api.routes_training import TrainingJobManager
    from backend.engine.shared_scheduler import ResourceLeases
    import backend.remote.coordinator as coordinator
    manager = TrainingJobManager(); manager._leases = ResourceLeases(tmp_path / 'leases.sqlite')
    monkeypatch.setattr('backend.api.routes_training.training_job_manager', manager)
    started = threading.Event(); acknowledged = threading.Event(); event = threading.Event()
    def remote_runner(selected, launch):
        def run(record):
            started.set()
            assert record.preparation_cancel.wait(3)
            acknowledged.set()
            return {'status': 'aborted', 'worker_exit_confirmed': True}
        return run
    monkeypatch.setattr(coordinator, 'make_remote_runner', remote_runner)
    source = tmp_path / 'source'; data(source)
    context = TrialContext('classification', source, tmp_path / 'models' / 'job_cancel_123', {'epochs': 1}, 'fast', 'cpu', None, event, lambda _: None)
    timer = threading.Thread(target=lambda: (started.wait(3), event.set()), daemon=True); timer.start()
    with pytest.raises(InterruptedError):
        run_remote_candidate(context, profile=profile(), search_id='a' * 32, source_dataset_path=source,
                             training_binding=None, owner={'project_id': 'P'}, remaining_seconds=5)
    assert acknowledged.is_set()
    assert manager.get_job('job_cancel_123').status == 'aborted'


def test_remote_worker_measurement_is_saved_and_hash_bound_in_model_artifacts(tmp_path, monkeypatch):
    from backend.remote.snapshot import build_snapshot
    from backend.remote.worker import run_train
    import backend.engine.trainer as trainer
    import backend.engine.exporter as exporter
    torch.set_num_threads(1)
    def model(**kwargs):
        return torch.nn.Sequential(torch.nn.AdaptiveAvgPool2d(1), torch.nn.Flatten(), torch.nn.Linear(3, 2))
    monkeypatch.setattr(trainer, 'create_classification_model', model)
    def reconstruct(path):
        payload = torch.load(path, weights_only=True); instance = model(); instance.load_state_dict(payload['model_state_dict'])
        return instance.eval(), payload, None
    monkeypatch.setattr(exporter, 'load_checkpoint_and_reconstruct_model', reconstruct)
    source = tmp_path / 'source'; data(source)
    snapshot = build_snapshot(source, tmp_path / 'snapshot', threading.Event())
    run = tmp_path / 'run'; run.mkdir(); (run / 'snapshot.tar.gz').write_bytes(snapshot.archive_path.read_bytes())
    spec = {'protocol_version': 1, 'job_id': 'job_measured_123', 'operation': 'train', 'task': 'classification',
        'preset': 'fast', 'device': 'cpu', 'config_overrides': {'epochs': 1, 'image_size': 32, 'batch_size': 1, 'pretrained': False},
        'snapshot_archive': 'snapshot.tar.gz', 'input_manifest_sha256': snapshot.manifest_sha256, 'measured_candidate': True,
        'automated_training': {'search_id': 'a' * 32, 'trial_id': 'job_measured_123'}}
    path = run / 'spec.json'; path.write_text(json.dumps(spec))
    assert run_train(path)['status'] == 'completed'
    metadata = json.loads((run / 'outputs' / 'model_meta.json').read_text())
    assert metadata['measured_candidate']['latency_ms'] > 0
    assert metadata['measured_candidate']['metrics']['val_loss'] >= 0
    manifest = json.loads((run / 'artifacts.json').read_text())
    for artifact in manifest['artifacts']:
        assert artifact['sha256'] == hashlib.sha256((run / artifact['path']).read_bytes()).hexdigest()
    assert metadata['automated_training']['search_id'] == 'a' * 32
