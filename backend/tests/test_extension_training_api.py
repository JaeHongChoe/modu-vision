"""Pre-submission validation and live REST contracts for training extensions."""
import json
from pathlib import Path
import threading

from fastapi.testclient import TestClient
from PIL import Image
import torch


def client(tmp_path):
    from backend.main import create_app
    app = create_app(project_dir=str(tmp_path / 'projects'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = api.post('/api/project/create', json={'name': 'training'}).json()
    source = tmp_path / 'source'
    for split in ('train', 'val'):
        for label, color in [('good', 'white'), ('defect', 'black')]:
            folder = source / split / label; folder.mkdir(parents=True)
            Image.new('RGB', (64, 64), color).save(folder / 'a.png')
    assert api.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    return api, project, source


def test_unsupported_remote_target_fails_before_search_submission(tmp_path):
    api, project, source = client(tmp_path)
    result = api.post('/api/automated-training/start', json={'task': 'classification', 'dataset_path': str(source),
        'compute_profile_id': 'missing', 'background': False, 'epochs_per_trial': 1})
    assert result.status_code == 422, result.text
    assert 'profile' in str(result.json()).lower()
    assert not (Path(project['models_dir']) / 'automated_training').exists()


def test_invalid_dino_mode_is_refused_before_allocating_a_training_job(tmp_path):
    api, project, source = client(tmp_path)
    result = api.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source), 'device': 'cpu',
        'config_overrides': {'backbone': 'dinov3_vits16', 'train_mode': 'guess'}})
    assert result.status_code == 422, result.text
    assert 'train_mode' in str(result.json())


def test_api_remote_autodl_roundtrip_preserves_verified_worker_artifacts(tmp_path, monkeypatch):
    from backend.tests.test_remote_coordinator import FakeRemote
    from backend.remote.worker import run_train
    from backend.remote import coordinator
    from backend.remote.operations import verify_downloaded_checkpoint
    import backend.engine.trainer as trainer
    import backend.engine.exporter as exporter
    from backend.api.routes_training import TrainingJobManager
    torch.set_num_threads(1)
    def model(**kwargs): return torch.nn.Sequential(torch.nn.AdaptiveAvgPool2d(1), torch.nn.Flatten(), torch.nn.Linear(3, 2))
    monkeypatch.setattr(trainer, 'create_classification_model', model)
    def reconstruct(path):
        payload = torch.load(path, weights_only=True); instance = model(); instance.load_state_dict(payload['model_state_dict'])
        return instance.eval(), payload, None
    monkeypatch.setattr(exporter, 'load_checkpoint_and_reconstruct_model', reconstruct)
    monkeypatch.setattr('backend.api.routes_training.training_job_manager', TrainingJobManager())
    api, project, source = client(tmp_path)
    selected = {'id': 'loopback', 'name': 'CPU contract', 'ssh_target': 'loopback', 'ssh_port': 22,
        'remote_root': str(tmp_path / 'server'), 'runtime_kind': 'python', 'runtime_value': 'python3'}
    assert api.post('/api/compute/profiles', json=selected).status_code == 201
    monkeypatch.setattr('backend.remote.ssh_transport.SSHTransport.probe', lambda *args: {'ready': True, 'runtime_ready': True,
        'checks': {'device_type': 'cpu', 'runtime_dependencies': {'timm': True, 'safetensors': True, 'huggingface_hub': True}}})
    class CpuRemote(FakeRemote):
        def launch(self, profile, argv, run_id):
            state = run_train(self.root / 'runs' / run_id / 'spec.json')
            assert state['status'] == 'completed', state
            return 'owned-contract-worker'
    monkeypatch.setattr(coordinator, 'make_remote_runner', lambda profile, launch:
        lambda record: coordinator.run_remote_training(record, profile, transport=CpuRemote(Path(profile.remote_root)),
                                                       device=launch['device'], config_overrides=launch['config_overrides']))
    result = api.post('/api/automated-training/start', json={'task': 'classification', 'dataset_path': str(source),
        'mode': 'quick', 'compute_profile_id': 'loopback', 'device': 'cpu', 'epochs_per_trial': 1, 'background': False,
        'base_config': {'image_size': 64, 'batch_size': 1, 'pretrained': False},
        'budget': {'max_trials': 1, 'max_total_epochs': 1, 'max_seconds': 20}})
    assert result.status_code == 200, result.text
    search = result.json(); assert search['status'] == 'completed', search
    assert search['compute_profile_id'] == 'loopback'
    trial = search['winner']; verify_downloaded_checkpoint(Path(trial['checkpoint_path']).parent, trial['trial_id'])
    launch = json.loads((Path(trial['checkpoint_path']).parent / 'remote_job.json').read_text())['launch_spec']
    assert launch['parent_search_id'] == search['search_id'] and launch['project_id'] == project['id']
    assert api.get('/api/automated-training/jobs/' + search['search_id']).json()['winner']['checkpoint_sha256'] == trial['checkpoint_sha256']


def resume_checkpoint(project, source):
    from backend.engine.training_resume import save_training_state, backend_numeric_flags, dataset_content_sha256
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.trainer import EarlyStopping
    model = torch.nn.Linear(3, 2); optimizer = torch.optim.AdamW(model.parameters()); scheduler = torch.optim.lr_scheduler.StepLR(optimizer, 1)
    path = Path(project['models_dir']) / 'stopped' / 'latest_training_state.pt'
    identity = {'task': 'classification', 'preset': 'fast', 'recipe': {'epochs': 3, 'backbone': 'dinov3_vits16', 'train_mode': 'head_only'},
        'dataset_fingerprint': fingerprint_dataset(source), 'dataset_content_sha256': dataset_content_sha256(source), 'device': 'cpu', 'torch_version': str(torch.__version__), 'backend_flags':backend_numeric_flags()}
    save_training_state(path, model, optimizer, scheduler, torch.amp.GradScaler('cpu', enabled=False),
        identity=identity, next_epoch=1, global_step=2, early_stopping=EarlyStopping())
    return path


def test_changed_exact_resume_recipe_is_rejected_before_job_allocation(tmp_path, monkeypatch):
    api, project, source = client(tmp_path); checkpoint = resume_checkpoint(project, source)
    monkeypatch.setattr('backend.api.routes_training.training_job_manager.start_job', lambda **kw: (_ for _ in ()).throw(AssertionError('must reject before allocation')))
    result = api.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source),
        'config_overrides': {'resume_checkpoint': str(checkpoint), 'epochs': 4}})
    assert result.status_code == 422, result.text
    assert 'recipe' in str(result.json()).lower()


def test_exact_resume_candidates_are_scoped_and_show_epoch_boundary(tmp_path):
    api, project, source = client(tmp_path); checkpoint = resume_checkpoint(project, source)
    result = api.get('/api/training/resume-states', params={'dataset_path': str(source), 'task': 'classification'})
    assert result.status_code == 200, result.text
    row = result.json()['states'][0]
    assert row['checkpoint_path'] == str(checkpoint) and row['boundary'] == 'epoch'
    assert row['next_epoch'] == 1 and row['global_step'] == 2
    Image.new('RGB', (64, 64), 'red').save(source / 'train' / 'good' / 'a.png')
    assert api.get('/api/training/resume-states', params={'dataset_path': str(source), 'task': 'classification'}).json()['states'] == []


def test_mps_exact_resume_is_explicitly_unsupported_before_submission(tmp_path, monkeypatch):
    api, project, source = client(tmp_path); checkpoint = resume_checkpoint(project, source)
    monkeypatch.setattr('backend.api.routes_training.training_job_manager.start_job', lambda **kw: (_ for _ in ()).throw(AssertionError('must reject before allocation')))
    payload = torch.load(checkpoint, weights_only=True); payload['identity']['device'] = 'mps'; torch.save(payload, checkpoint)
    result = api.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source),
        'config_overrides': {'resume_checkpoint': str(checkpoint)}})
    assert result.status_code == 422, result.text
    assert 'mps' in str(result.json()).lower()


def test_terminal_early_stopping_state_is_not_offered_or_allocated(tmp_path):
    api, project, source = client(tmp_path); checkpoint = resume_checkpoint(project, source)
    payload = torch.load(checkpoint, weights_only=True); payload['early_stopping']['early_stop'] = True; torch.save(payload, checkpoint)
    assert api.get('/api/training/resume-states', params={'dataset_path': str(source), 'task': 'classification'}).json()['states'] == []
    result = api.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source),
        'config_overrides': {'resume_checkpoint': str(checkpoint)}})
    assert result.status_code == 422 and 'remaining' in str(result.json()).lower()


def test_headless_preflight_keeps_dino_scope_and_seed(tmp_path):
    from backend.engine.training_engine import prepare, create_run
    _, _, source = client(tmp_path)
    output = tmp_path / 'headless'
    prepared = prepare(task='classification', source_dataset_path=str(source), output_dir=str(output))
    row = create_run(output_dir=str(output), prepared_id=prepared['prepared_id'], config={'backbone':'dinov3_vits16',
        'train_mode':'partial', 'partial_blocks':2, 'use_amp':False, 'seed':7})
    assert row['config']['train_mode']=='partial' and row['config']['seed']==7


def test_unsupported_specialist_measurements_fail_before_submission(tmp_path):
    from backend.engine.automated_trials import validate_trial_controls
    import pytest
    with pytest.raises(ValueError, match='multiline'):
        validate_trial_controls('ocr','fast',{'recipe':{'mode':'detect_recognize'}})
    with pytest.raises(ValueError, match='OBB'):
        validate_trial_controls('rotated_detection','fast',{'recipe':{'adapter':'ultralytics_yolo_obb'}})


def test_legacy_unbound_resume_is_not_offered_or_launched(tmp_path, monkeypatch):
    api, project, source = client(tmp_path); checkpoint = resume_checkpoint(project, source)
    payload = torch.load(checkpoint, weights_only=True)
    payload['identity'].pop('dataset_content_sha256', None)
    torch.save(payload, checkpoint)
    monkeypatch.setattr('backend.api.routes_training.training_job_manager.start_job',
        lambda **kw: (_ for _ in ()).throw(AssertionError('legacy input identity must fail before launch')))
    result = api.get('/api/training/resume-states', params={'dataset_path': str(source), 'task': 'classification'})
    assert result.json()['states'] == [], 'a state that cannot match the current strict identity cannot be selectable'
    refused = api.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source),
        'config_overrides': {'resume_checkpoint': str(checkpoint)}})
    assert refused.status_code == 422 and 'snapshot' in str(refused.json()).lower()


def test_invalid_resume_tensor_payload_is_a_client_error_before_launch(tmp_path, monkeypatch):
    api, project, source = client(tmp_path); checkpoint = resume_checkpoint(project, source)
    checkpoint.write_bytes(b'invalid-training-state')
    monkeypatch.setattr('backend.api.routes_training.training_job_manager.start_job',
        lambda **kw: (_ for _ in ()).throw(AssertionError('invalid checkpoint must fail before launch')))
    refused = api.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source),
        'config_overrides': {'resume_checkpoint': str(checkpoint)}})
    assert refused.status_code == 422
