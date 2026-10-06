"""Hash-bound epoch continuation inputs; no host paths reach a worker.

Only supervised single-process states are portable to the same runtime/device.
The trainer validates full recipe, RNG topology and numeric flags before restore.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from backend.engine.training_resume import read_training_state

MAX_STATE_BYTES = 2 * 1024**3
STATE_NAME = 'training-state.pt'
STATE_ARTIFACT = 'outputs/latest_training_state.pt'


def identity_sha256(identity):
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _regular(path):
    path = Path(path)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('Exact resume input must be regular and unlinked')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_STATE_BYTES:
        raise ValueError('Exact resume input exceeds the regular training-state size limit')
    return info


def validate_state(path, *, task, preset):
    _regular(path)
    state = read_training_state(path)
    identity = state['identity']
    if task not in ('classification', 'segmentation', 'detection', 'patch_classification'):
        raise ValueError('Exact remote resume supports supervised epoch states only')
    if identity.get('task') != task or identity.get('preset') != preset or identity.get('device') == 'mps':
        raise ValueError('Exact resume task, preset or supported device differs')
    if state['next_epoch'] >= identity['recipe']['epochs'] or state['early_stopping'].get('early_stop'):
        raise ValueError('Exact resume has no remaining epochs')
    return state


def stage_training_state(output, options, *, task, preset):
    options = dict(options or {})
    if not options.get('resume_checkpoint'):
        return options, None, None
    source = Path(options['resume_checkpoint']).expanduser()
    before = _regular(source)
    root = Path(output) / 'training_state_transfer'
    root.mkdir(parents=True, exist_ok=False)
    target = root / STATE_NAME
    digest = hashlib.sha256()
    with source.open('rb') as reader, target.open('xb') as writer:
        opened = os.fstat(reader.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise ValueError('Exact resume input changed before copying')
        remaining = opened.st_size
        while remaining:
            chunk = reader.read(min(1024 * 1024, remaining))
            if not chunk: raise ValueError('Exact resume input shrank while copying')
            digest.update(chunk); writer.write(chunk); remaining -= len(chunk)
        if reader.read(1): raise ValueError('Exact resume input grew while copying')
        writer.flush(); os.fsync(writer.fileno())
        after = _regular(source)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise ValueError('Exact resume input changed while copying')
    state = validate_state(target, task=task, preset=preset)
    if state['_checkpoint_sha256'] != digest.hexdigest():
        raise ValueError('Exact resume staged hash differs')
    envelope = {'schema_version': 1, 'semantics': 'exact_resume', 'boundary': 'epoch',
                'checkpoint': STATE_NAME, 'sha256': digest.hexdigest(), 'size': opened.st_size,
                'identity_sha256': identity_sha256(state['identity']),
                'next_epoch': state['next_epoch'], 'global_step': state['global_step']}
    options['resume_checkpoint'] = STATE_NAME
    options.pop('pretrained_checkpoint', None); options.pop('pretrained_origin', None)
    return options, envelope, (target, STATE_NAME)


def restore_transferred_state(run_dir, spec):
    options = spec['config_overrides']
    envelope = spec.get('training_state')
    if envelope is None:
        if options.get('resume_checkpoint'):
            raise ValueError('Exact resume checkpoint requires a hash-bound run input')
        return
    if spec.get('warm_start') or spec.get('distributed') or spec.get('measured_candidate'):
        raise ValueError('Exact resume is separate from warm-start, DDP and measured search candidates')
    if not isinstance(envelope, dict) or set(envelope) != {'schema_version', 'semantics', 'boundary', 'checkpoint', 'sha256', 'size', 'identity_sha256', 'next_epoch', 'global_step'}:
        raise ValueError('Invalid exact resume transfer envelope')
    if envelope['schema_version'] != 1 or envelope['semantics'] != 'exact_resume' or envelope['boundary'] != 'epoch' or envelope['checkpoint'] != STATE_NAME or options.get('resume_checkpoint') != STATE_NAME:
        raise ValueError('Invalid exact resume run input identity')
    path = Path(run_dir) / STATE_NAME
    info = _regular(path)
    state = validate_state(path, task=spec['task'], preset=spec.get('preset', 'fast'))
    if (type(envelope['size']) is not int or envelope['size'] != info.st_size
            or state['_checkpoint_sha256'] != envelope['sha256']
            or identity_sha256(state['identity']) != envelope['identity_sha256']
            or any(type(envelope[key]) is not int or envelope[key] != state[key] for key in ('next_epoch', 'global_step'))):
        raise ValueError('Exact resume transferred state hash or identity differs')
    options['resume_checkpoint'] = str(path)
