"""Retain received specialist bytes and prove path-only local relocation."""
import os
from pathlib import Path
import shutil

SPECIALIST_TASKS = {'rotation', 'ocr', 'rotated_detection', 'enhancement', 'defect_gan', 'patch_classification'}


def relocate_paths(value, remote_root, local_root):
    if isinstance(value, dict):
        return {key: relocate_paths(child, remote_root, local_root) for key, child in value.items()}
    if isinstance(value, list):
        return [relocate_paths(child, remote_root, local_root) for child in value]
    if isinstance(value, str) and (value == remote_root or value.startswith(remote_root + '/')):
        return local_root + value[len(remote_root):]
    return value


def preserve_received_pair(output, checksum):
    """Existing retained bytes are immutable, including across download retries."""
    output = Path(output); retained = output / 'remote_received'
    if any(p.is_symlink() for p in (retained, *retained.parents)):
        raise ValueError('Original received artifact storage is linked')
    retained.mkdir(mode=0o700, exist_ok=True)
    for name in ('best_model.pt', 'model_meta.json'):
        source, target = output / name, retained / name
        if target.is_symlink():
            raise ValueError('Original received artifact is linked')
        if target.exists():
            if not target.is_file() or target.stat().st_size != source.stat().st_size or checksum(target) != checksum(source):
                raise ValueError('Original received artifact changed across relocation attempts')
            continue
        try:
            with source.open('rb') as reader, target.open('xb') as writer:
                shutil.copyfileobj(reader, writer); writer.flush(); os.fsync(writer.fileno())
            if target.stat().st_size != source.stat().st_size or checksum(target) != checksum(source):
                raise ValueError('Original received artifact copy differs')
        except Exception:
            target.unlink(missing_ok=True)
            raise


def matches_path_relocation(original, current, remote_root, local_root):
    """Compare restricted payloads, preserving every tensor and nonpath value."""
    import torch
    remaining = [1000000]
    def same(before, after, depth=0, move=True):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 64:
            raise ValueError('Relocated model structure exceeds archive limits')
        if isinstance(before, torch.Tensor):
            return (before.layout == torch.strided and isinstance(after, torch.Tensor) and before.dtype == after.dtype
                and before.layout == after.layout and before.shape == after.shape and torch.equal(before, after))
        if isinstance(before, dict):
            return (isinstance(after, dict) and before.keys() == after.keys()
                and all(same(child, after[key], depth + 1, move) for key, child in before.items()))
        if isinstance(before, list):
            return (isinstance(after, list) and len(before) == len(after)
                and all(same(a, b, depth + 1, move) for a, b in zip(before, after)))
        if isinstance(before, str):
            expected = (local_root + before[len(remote_root):]
                if move and (before == remote_root or before.startswith(remote_root + '/')) else before)
            return type(after) is str and after == expected
        if isinstance(before, tuple):
            # The existing producer does not relocate tuple contents.
            return (type(after) is tuple and len(before) == len(after)
                and all(same(a, b, depth + 1, False) for a, b in zip(before, after)))
        if before is None or type(before) in (int, float, bool, bytes):
            return type(after) is type(before) and before == after
        raise ValueError('Unsupported restricted model archive value')
    return same(original, current)
