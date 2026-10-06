"""Versioned exact continuation at completed epoch boundaries.

Model-only checkpoints remain portable warm starts. Exact continuation pins the
dataset and recipe and restores all stochastic and optimization state. The
Studio loader has zero workers; mid-epoch and DDP continuation are not claimed.
"""
from __future__ import annotations

import hashlib
import io
import json
import pickle
from pathlib import Path
import random

import numpy as np
import torch


def backend_numeric_flags():
    return {'cudnn_deterministic':torch.backends.cudnn.deterministic,
            'cudnn_benchmark':torch.backends.cudnn.benchmark,
            'cudnn_allow_tf32':torch.backends.cudnn.allow_tf32,
            'cuda_matmul_allow_tf32':torch.backends.cuda.matmul.allow_tf32}


def dataset_content_sha256(dataset_path):
    """Training-relevant relative names and stable bytes survive snapshot relocation."""
    from backend.engine.dataset_fingerprint import _files_under, source_artifact_identity
    root = Path(dataset_path).expanduser().resolve()
    if not root.is_dir(): raise ValueError('Exact resume dataset is unavailable')
    digest = hashlib.sha256(b'modu-exact-epoch-dataset-v1\0')
    for path in _files_under(root):
        relative = path.relative_to(root).as_posix()
        with source_artifact_identity(root, relative) as (_, content, size):
            digest.update(relative.encode('utf-8')); digest.update(b'\0')
            digest.update(str(size).encode('ascii')); digest.update(b'\0')
            digest.update(content.encode('ascii')); digest.update(b'\0')
    return digest.hexdigest()


def build_identity(*, task, preset, recipe, dataset_path, classes, model, device):
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.warm_start import architecture_for
    recipe = {key: value for key, value in recipe.items() if key not in {
        'resume_checkpoint', 'pretrained_checkpoint', 'pretrained_sha256', 'pretrained_origin', 'pretrained'}}
    return json.loads(json.dumps({
        'task': task, 'preset': preset, 'recipe': recipe,
        'architecture': architecture_for(task, preset, recipe), 'classes': list(classes),
        'dataset_fingerprint': fingerprint_dataset(Path(dataset_path)), 'device': str(device),
        'dataset_content_sha256': dataset_content_sha256(dataset_path),
        'torch_version': str(torch.__version__),
        'parameters': {name: [list(parameter.shape), str(parameter.dtype), parameter.requires_grad]
                       for name, parameter in model.named_parameters()},
        'deterministic_algorithms': torch.are_deterministic_algorithms_enabled(),
        'backend_flags':backend_numeric_flags(),
    }, sort_keys=True))


def _rng_state():
    numpy = np.random.get_state()
    return {'python': random.getstate(), 'numpy': [numpy[0], numpy[1].tolist(), *numpy[2:]],
            'torch': torch.get_rng_state(),
            'cuda': torch.cuda.get_rng_state_all() if torch.cuda.is_initialized() else []}


def _restore_rng(state):
    random.setstate(state['python'])
    numpy = state['numpy']
    np.random.set_state((numpy[0], np.array(numpy[1], dtype=np.uint32), *numpy[2:]))
    torch.set_rng_state(state['torch'])
    if state['cuda']:
        if not torch.cuda.is_available() or len(state['cuda']) != torch.cuda.device_count():
            raise ValueError('Exact resume CUDA RNG topology differs')
        torch.cuda.set_rng_state_all(state['cuda'])


def save_training_state(path, model, optimizer, scheduler, scaler, *, identity, next_epoch,
                        global_step, early_stopping, best_model_path=None):
    path = Path(path)
    if path.is_symlink(): raise ValueError('Training state cannot be linked')
    best = torch.load(best_model_path, map_location='cpu', weights_only=True) if best_model_path and Path(best_model_path).is_file() else None
    state = {'schema_version': 1, 'semantics': 'exact_resume', 'boundary': 'epoch', 'identity': identity,
             'next_epoch': next_epoch, 'global_step': global_step, 'model_state_dict': model.state_dict(),
             'optimizer_state_dict': optimizer.state_dict(), 'scheduler_state_dict': scheduler.state_dict(),
             'scaler_state_dict': scaler.state_dict(), 'scaler_enabled': scaler.is_enabled(),
             'rng_state': _rng_state(), 'early_stopping': vars(early_stopping).copy(), 'best_model_payload': best}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    torch.save(state, temporary); temporary.replace(path)


def read_training_state(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file(): raise ValueError('Exact resume training state is unavailable')
    payload = path.read_bytes()
    try:
        state = torch.load(io.BytesIO(payload), map_location='cpu', weights_only=True)
    except (pickle.UnpicklingError, EOFError, RuntimeError, TypeError) as exc:
        raise ValueError('Exact resume training state payload is invalid') from exc
    required = {'identity', 'model_state_dict', 'optimizer_state_dict', 'scheduler_state_dict',
                'scaler_state_dict', 'scaler_enabled', 'rng_state', 'early_stopping', 'next_epoch', 'global_step'}
    if (not isinstance(state, dict) or state.get('schema_version') != 1 or state.get('semantics') != 'exact_resume'
            or state.get('boundary') != 'epoch' or not required.issubset(state)):
        raise ValueError('Checkpoint has no complete exact training state; use warm-start for model-only checkpoints')
    if (any(not isinstance(state[key], dict) for key in ('identity', 'model_state_dict', 'optimizer_state_dict',
            'scheduler_state_dict', 'scaler_state_dict', 'rng_state', 'early_stopping'))
            or not isinstance(state['identity'].get('recipe'), dict) or type(state['scaler_enabled']) is not bool):
        raise ValueError('Exact resume training state structure is invalid')
    if any(type(state[key]) is not int or state[key] < 0 for key in ('next_epoch', 'global_step')):
        raise ValueError('Exact resume epoch or step is invalid')
    # The source job may atomically advance latest_training_state.pt while a
    # new job resumes. Record the exact byte snapshot deserialized above.
    state['_checkpoint_sha256'] = hashlib.sha256(payload).hexdigest()
    return state


def restore_training_state(path, model, optimizer, scheduler, scaler, *, identity, early_stopping, allow_snapshot_relocation=False):
    state = read_training_state(path)
    expected = identity; recorded = state['identity']
    if allow_snapshot_relocation:
        content = recorded.get('dataset_content_sha256')
        if not content or content != expected.get('dataset_content_sha256'):
            raise ValueError('Exact resume relocated dataset content differs')
        expected = {key:value for key,value in expected.items() if key != 'dataset_fingerprint'}
        recorded = {key:value for key,value in recorded.items() if key != 'dataset_fingerprint'}
    if recorded != expected: raise ValueError('Exact resume identity differs: dataset, recipe, classes, model, or device changed')
    if state['scaler_enabled'] != scaler.is_enabled(): raise ValueError('Exact resume AMP scaler configuration differs')
    if state['rng_state'].get('cuda') and (not torch.cuda.is_available() or len(state['rng_state']['cuda']) != torch.cuda.device_count()):
        raise ValueError('Exact resume CUDA RNG topology differs')
    model.load_state_dict(state['model_state_dict'], strict=True)
    # Scheduler is constructed before optimizer restoration; its LR must not
    # overwrite the checkpoint's parameter-group LR during construction.
    scheduler.load_state_dict(state['scheduler_state_dict'])
    optimizer.load_state_dict(state['optimizer_state_dict'])
    scaler.load_state_dict(state['scaler_state_dict'])
    early_stopping.__dict__.update(state['early_stopping'])
    _restore_rng(state['rng_state'])
    return {key: state.get(key) for key in ('next_epoch', 'global_step', 'best_model_payload', '_checkpoint_sha256')}


def resume_lineage(path, state):
    return {'semantics': 'exact_resume', 'boundary': 'epoch', 'next_epoch': state['next_epoch'],
            'global_step': state['global_step'], 'checkpoint_sha256': state.get('_checkpoint_sha256') or hashlib.sha256(Path(path).read_bytes()).hexdigest()}
