"""A fresh manager can display verified local completion without launching work."""
import json
import shutil
from pathlib import Path

import pytest
import torch
from PIL import Image
from fastapi.testclient import TestClient

from backend.api import routes_training
from backend.main import create_app


@pytest.fixture
def completed_local(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    manager = routes_training.TrainingJobManager(local_execution='embedded')
    monkeypatch.setattr(routes_training, 'training_job_manager', manager)
    class Trainer:
        def __init__(self, **kwargs): self.output = Path(kwargs['output_dir'])
        def train(self, job_id):
            torch.save({'task': 'classification', 'classes': ['OK', 'NG'], 'backbone': 'resnet18',
                        'model_state_dict': {'weight': torch.zeros(2, 3)}}, self.output / 'best_model.pt')
            (self.output / 'model_meta.json').write_text(json.dumps(
                {'task': 'classification', 'classes': ['OK', 'NG'], 'backbone': 'resnet18'}))
            return {'status': 'completed'}
    monkeypatch.setattr(routes_training, 'UnifiedAutoMLTrainer', Trainer)
    source = tmp_path / 'source'
    for split in ('train', 'val'):
        for label in ('OK', 'NG'):
            directory = source / split / label
            directory.mkdir(parents=True)
            Image.new('RGB', (16, 16)).save(directory / 'sample.png')
    app = create_app(project_dir=str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name': 'Receipt recovery', 'task': 'classification'}).json()
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    started = client.post('/api/training/start', json={'task': 'classification', 'dataset_path': str(source), 'device': 'cpu'})
    assert started.status_code == 200, started.text
    job = started.json()['job_id']
    record = manager.get_job(job)
    record.thread.join(5)
    assert record.status == 'completed'
    record.current_epoch = 2
    record.total_epochs = 5
    record.train_loss = .4
    record.val_loss = .5
    record.loss_history = [{'epoch': 1, 'train_loss': .6, 'val_loss': .7}, {'epoch': 2, 'train_loss': .4, 'val_loss': .5}]
    routes_training._write_job_receipt(record)
    monkeypatch.setattr(routes_training, 'training_job_manager', routes_training.TrainingJobManager(local_execution='embedded'))
    return client, project, source, job, Path(record.output_dir), record.loss_history


def test_verified_local_completed_receipt_recovers_real_progress_without_evaluation(completed_local):
    client, _, _, job, output, history = completed_local
    assert not (output / 'eval_results.json').exists()
    response = client.get('/api/training/status', params={'job_id': job})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['job_id'] == job and result['status'] == 'completed'
    assert result['current_epoch'] == 2 and result['total_epochs'] == 5
    assert result['loss_history'] == history
    assert result['is_training'] is False and result['compute_profile_id'] is None
    assert routes_training.training_job_manager.get_job(job) is None


@pytest.mark.parametrize('damage', ['aborted', 'failed', 'stopping', 'missing_receipt', 'corrupt_receipt',
                                   'wrong_task', 'changed_checkpoint', 'linked_checkpoint', 'changed_source',
                                   'changed_labelset', 'other_project', 'missing_hash'])
def test_receipt_fallback_never_promotes_unverified_or_unowned_completion(completed_local, damage):
    client, project, source, job, output, _ = completed_local
    receipt = output / 'job_receipt.json'
    if damage in ('aborted', 'failed', 'stopping', 'wrong_task'):
        saved = json.loads(receipt.read_text())
        saved['task' if damage == 'wrong_task' else 'status'] = 'segmentation' if damage == 'wrong_task' else damage
        receipt.write_text(json.dumps(saved))
    elif damage == 'missing_receipt': receipt.unlink()
    elif damage == 'corrupt_receipt': receipt.write_text('{broken')
    elif damage == 'changed_checkpoint': (output / 'best_model.pt').write_bytes(b'changed weights')
    elif damage == 'linked_checkpoint':
        checkpoint = output / 'best_model.pt'
        original = output.parent / 'elsewhere.pt'
        checkpoint.rename(original)
        checkpoint.symlink_to(original)
    elif damage == 'changed_source': Image.new('RGB', (16, 16), 'red').save(source / 'train' / 'OK' / 'sample.png')
    elif damage == 'changed_labelset':
        labelset = client.post('/api/project/labelsets', json={'name': 'Other scope'}).json()
        assert client.put(f'/api/project/labelsets/{labelset["id"]}/activate').status_code == 200
    elif damage == 'other_project':
        client.post('/api/project/create', json={'name': 'Other project', 'task': 'classification'})
        client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    elif damage == 'missing_hash':
        path = output / 'model_meta.json'
        metadata = json.loads(path.read_text()); metadata.pop('checkpoint_sha256')
        path.write_text(json.dumps(metadata))
    response = client.get('/api/training/status', params={'job_id': job})
    assert response.status_code == 200, response.text
    assert response.json()['status'] != 'completed'


def test_receipt_without_epoch_history_does_not_invent_curves(completed_local):
    client, _, _, job, output, _ = completed_local
    receipt = output / 'job_receipt.json'
    saved = json.loads(receipt.read_text()); saved.pop('loss_history')
    receipt.write_text(json.dumps(saved))
    result = client.get('/api/training/status', params={'job_id': job}).json()
    assert result['status'] == 'completed'
    assert result['loss_history'] == []


def test_receipt_cannot_relabel_an_unchanged_model_version_as_another_source(completed_local):
    from backend.api.routes_dataset import _split_manifest_file
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    client, project, source, job, output, _ = completed_local
    alternate = source.parent / 'alternate_source'
    shutil.copytree(source, alternate)
    assert client.put('/api/project/update', json={'source_dataset_dir': str(alternate)}).status_code == 200
    receipt = output / 'job_receipt.json'
    saved = json.loads(receipt.read_text())
    saved['source_dataset_path'] = str(alternate)
    saved['dataset_fingerprint'] = fingerprint_dataset(alternate, studio_root=Path(project['annotations_dir']),
                                                     split_manifest=_split_manifest_file(alternate), use_scope=False)
    receipt.write_text(json.dumps(saved))
    result = client.get('/api/training/status', params={'job_id': job}).json()
    assert result['status'] != 'completed'


def test_owned_archive_restore_recovers_receipt_alias_without_rewriting_checkpoint(completed_local):
    client, project, source, job, output, history = completed_local
    original = (output / 'best_model.pt').read_bytes()
    backup = client.post('/api/project/backup', json={'destination_dir': str(source.parent / 'backups')})
    assert backup.status_code == 200, backup.text
    restored = client.post('/api/project/restore', json={'archive_path': backup.json()['archive_path'],
                                                      'target_dir': str(source.parent / 'restored')})
    assert restored.status_code == 200, restored.text
    assert (Path(restored.json()['models_dir']) / job / 'best_model.pt').read_bytes() == original
    result = client.get('/api/training/status', params={'job_id': job}).json()
    assert result['job_id'] == job and result['status'] == 'completed'
    assert result['loss_history'] == history
