"""Versioned exact continuation at completed epoch boundaries.

Model-only checkpoints remain portable warm starts. Exact continuation pins the
dataset and recipe and restores all stochastic and optimization state. The
Studio loader has zero workers; mid-epoch and DDP continuation are not claimed.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import random

import numpy as np
import torch


def backend_numeric_flags():
    return {'cudnn_deterministic':torch.backends.cudnn.deterministic,
            'cudnn_benchmark':torch.backends.cudnn.benchmark,
            'cudnn_allow_tf32':torch.backends.cudnn.allow_tf32,
            'cuda_matmul_allow_tf32':torch.backends.cuda.matmul.allow_tf32}


def build_identity(*, task, preset, recipe, dataset_path, classes, model, device):
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.warm_start import architecture_for
    recipe = {key: value for key, value in recipe.items() if key not in {
        'resume_checkpoint', 'pretrained_checkpoint', 'pretrained_sha256', 'pretrained_origin', 'pretrained'}}
    return json.loads(json.dumps({
        'task': task, 'preset': preset, 'recipe': recipe,
        'architecture': architecture_for(task, preset, recipe), 'classes': list(classes),
        'dataset_fingerprint': fingerprint_dataset(Path(dataset_path)), 'device': str(device),
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
    state = torch.load(io.BytesIO(payload), map_location='cpu', weights_only=True)
    required = {'identity', 'model_state_dict', 'optimizer_state_dict', 'scheduler_state_dict',
                'scaler_state_dict', 'scaler_enabled', 'rng_state', 'early_stopping', 'next_epoch', 'global_step'}
    if (not isinstance(state, dict) or state.get('schema_version') != 1 or state.get('semantics') != 'exact_resume'
            or state.get('boundary') != 'epoch' or not required.issubset(state)):
        raise ValueError('Checkpoint has no complete exact training state; use warm-start for model-only checkpoints')
    if any(type(state[key]) is not int or state[key] < 0 for key in ('next_epoch', 'global_step')):
        raise ValueError('Exact resume epoch or step is invalid')
    # The source job may atomically advance latest_training_state.pt while a
    # new job resumes. Record the exact byte snapshot deserialized above.
    state['_checkpoint_sha256'] = hashlib.sha256(payload).hexdigest()
    return state


def restore_training_state(path, model, optimizer, scheduler, scaler, *, identity, early_stopping):
    state = read_training_state(path)
    if state['identity'] != identity: raise ValueError('Exact resume identity differs: dataset, recipe, classes, model, or device changed')
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
