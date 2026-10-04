"""Strict continuation for completed, project-owned specialist candidates."""
from __future__ import annotations

import json
import re
from pathlib import Path

import torch

from backend.engine.warm_start import WarmStartParent, _sha256, _strict_state, verify_parent_status
from backend.engine.specialized_models import require_completed_checkpoint, resolve_specialized_checkpoint

FAMILIES = ('ocr', 'rotated_detection', 'defect_gan', 'enhancement', 'rotation')


def _load_parent_checkpoint(path):
    """Normalize unreadable model bytes so selectors can exclude invalid candidates."""
    try:
        payload = torch.load(path, map_location='cpu', weights_only=True)
    except Exception as exc:
        raise ValueError('Specialist parent checkpoint is unreadable') from exc
    if not isinstance(payload, dict):
        raise ValueError('Specialist parent checkpoint must contain a state dictionary')
    return payload


def family_signature(task, metadata):
    """Include output identities and all shape/geometry controls in the signature."""
    if task == 'rotation':
        if metadata.get('architecture') != 'small_cnn_angle_v1' or metadata.get('width') not in (8,16,32) or type(metadata.get('image_size')) is not int:
            raise ValueError('Rotation parent architecture or geometry is invalid')
        return f"rotation:small_cnn_angle_v1:{metadata['width']}:{metadata['image_size']}:circle360", ('upright_correction',)
    if task == 'ocr':
        alphabet = metadata.get('alphabet')
        size = metadata.get('image_size')
        if metadata.get('architecture') != 'small_cnn_bigru_ctc' or not isinstance(alphabet, str) or not alphabet or not isinstance(size, (tuple, list)) or len(size) != 2:
            raise ValueError('OCR parent architecture or alphabet is invalid')
        return f'ocr:small_cnn_bigru_ctc:{size[0]}x{size[1]}', tuple(alphabet)
    if task == 'rotated_detection':
        version = metadata.get('version', 1)
        classes = metadata.get('class_names') if version == 2 else [metadata.get('class_name')]
        capacity = metadata.get('max_objects') if version == 2 else 1
        if not isinstance(classes, (list, tuple)) or not classes or any(not isinstance(c, str) or not c for c in classes) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError('Rotated parent classes or capacity is invalid')
        return f'rotated_detection:v{version}:{metadata.get("image_size")}:{capacity}', tuple(classes)
    if task == 'defect_gan':
        if metadata.get('model_kind') != 'dcgan_defect_crop' or metadata.get('image_size') != 64 or metadata.get('latent_size') != 64 or not isinstance(metadata.get('base_channels'), int):
            raise ValueError('GAN parent architecture is invalid')
        return f'defect_gan:dcgan_defect_crop:{metadata["base_channels"]}:64:64', ('explicit_defect_crop',)
    if task == 'enhancement' and metadata.get('architecture') == 'rgb_residual_cnn':
        return 'enhancement:rgb_residual_cnn:v1', ('RGB',)
    raise ValueError('Specialist parent task or architecture is invalid')


def requested_signature(task, dataset, options):
    if task == 'rotation':
        from backend.engine.rotation import load_rotation_manifest
        load_rotation_manifest(dataset)
        return family_signature(task, {'architecture':'small_cnn_angle_v1','width':options.get('width',16),'image_size':options.get('image_size',64)})
    if task == 'ocr':
        from backend.engine.ocr import load_ocr_manifest
        alphabet = load_ocr_manifest(dataset).alphabet
        size = options.get('image_size', (options.get('image_height', 32), options.get('image_width', 128)))
        return family_signature(task, {'architecture': 'small_cnn_bigru_ctc', 'alphabet': alphabet, 'image_size': size})
    if task == 'rotated_detection':
        from backend.engine.rotated_detection import load_rotated_manifest
        manifest = load_rotated_manifest(dataset)
        groups = {}
        for row in manifest.records:
            groups[row.image] = groups.get(row.image, 0) + 1
        return family_signature(task, {'version': manifest.version, 'class_name': manifest.class_name,
            'class_names': list(manifest.class_names), 'max_objects': max(groups.values()), 'image_size': options.get('image_size', 64)})
    if task == 'defect_gan':
        from backend.engine.defect_gan import load_defect_gan_manifest
        load_defect_gan_manifest(dataset)
        return family_signature(task, {'model_kind': 'dcgan_defect_crop', 'image_size': 64, 'latent_size': 64, 'base_channels': options.get('base_channels', 16)})
    if task == 'enhancement':
        from backend.engine.enhancement import load_enhancement_manifest
        load_enhancement_manifest(dataset)
        return family_signature(task, {'architecture': 'rgb_residual_cnn'})
    raise ValueError('Unsupported specialist parent family')


def resolve_family_parent(models_dir, job_id, task, source, dataset, options=None):
    root = Path(models_dir)
    if task not in FAMILIES or not isinstance(job_id, str) or re.fullmatch('[0-9a-f]{32}', job_id) is None:
        raise ValueError('Invalid specialist parent identity')
    directory = root / task / job_id
    checkpoint = directory / 'best_model.pt'
    metadata_path = directory / 'model_meta.json'
    receipt_path = directory / 'job_receipt.json'
    if (any(path.is_symlink() for path in (root, root / task, directory, checkpoint, metadata_path, receipt_path))
            or not all(path.is_file() for path in (checkpoint, metadata_path, receipt_path))):
        raise ValueError('Specialist parent must be a completed candidate in the active project')
    require_completed_checkpoint(checkpoint)
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt.get('job_id') != job_id or receipt.get('task') != task or receipt.get('status') != 'completed':
        raise ValueError('Specialist parent completed receipt identity differs')
    if not receipt.get('source_dataset_path') or Path(receipt['source_dataset_path']).resolve() != Path(source).resolve():
        raise ValueError('Specialist parent source differs from the active project')
    digest = _sha256(checkpoint)
    if receipt.get('checkpoint_sha256') != digest or (metadata.get('checkpoint_sha256') and metadata['checkpoint_sha256'] != digest):
        raise ValueError('Specialist parent checkpoint SHA-256 hash differs from completed receipt')
    fingerprint = receipt.get('dataset_fingerprint') or receipt.get('training_provenance', {}).get('dataset_fingerprint')
    if not isinstance(fingerprint, str) or not fingerprint.startswith('v1:'):
        raise ValueError('Specialist parent dataset fingerprint is missing')
    payload = _load_parent_checkpoint(checkpoint)
    signature = family_signature(task, metadata)
    if not isinstance(payload, dict) or payload.get('task') != task or signature != family_signature(task, payload):
        raise ValueError('Specialist checkpoint classes or architecture conflict with metadata')
    if signature != requested_signature(task, dataset, options or {}):
        raise ValueError('Specialist parent classes or architecture differ from current training')
    if task != 'defect_gan':
        resolve_specialized_checkpoint(root, job_id, task, source)
    else:
        from backend.engine.specialized_models import _verify_historical_source
        from backend.engine.defect_gan import MANIFEST_NAME
        if payload.get('training_provenance'):
            _verify_historical_source(root.resolve(), source, payload, metadata)
        else:
            from backend.engine.defect_gan import load_defect_gan_manifest
            prepared=load_defect_gan_manifest(dataset)
            declared=prepared.get('source_dataset_path',str(dataset))
            if Path(declared).resolve()!=Path(source).resolve():
                raise ValueError('GAN parent prepared source differs from the active project')
            if payload.get('source_manifest_sha256') != _sha256(Path(dataset) / MANIFEST_NAME):
                raise ValueError('GAN parent source manifest differs without immutable lineage')
    return WarmStartParent(job_id, checkpoint.resolve(), digest, task, signature[0], signature[1], fingerprint)


def require_new_candidate(output, parent):
    output = Path(output)
    if output.is_symlink() or output.resolve() == parent.checkpoint_path.parent.resolve() or (output / 'best_model.pt').exists():
        raise ValueError('Parent training needs a new candidate directory')


def load_family_weights(modules, parent, signature):
    """Check the complete GAN pair before either network mutates."""
    if signature != (parent.architecture, parent.classes):
        raise ValueError('Specialist parent classes or architecture differ from current training')
    path = parent.checkpoint_path
    verify_parent_status(parent)
    require_completed_checkpoint(path)
    if path.is_symlink() or not path.is_file() or _sha256(path) != parent.checkpoint_sha256:
        raise ValueError('Specialist parent checkpoint SHA-256 changed before training')
    payload = _load_parent_checkpoint(path)
    if payload.get('task') != parent.task or family_signature(parent.task, payload) != signature:
        raise ValueError('Specialist parent checkpoint signature changed')
    for key, model in modules.items():
        _strict_state(model, payload.get(key))
    for key, model in modules.items():
        model.load_state_dict(payload[key], strict=True)


def list_family_parents(models_dir, task, source, dataset, options=None):
    from backend.engine.parent_summary import parent_summary
    root = Path(models_dir) / task
    parents = []
    for directory in sorted(root.iterdir()) if root.is_dir() else []:
        try:
            parent = resolve_family_parent(models_dir, directory.name, task, source, dataset, options)
        except (ValueError, OSError, RuntimeError, KeyError, TypeError):
            continue
        parents.append({'job_id': parent.job_id, 'architecture': parent.architecture,
            'classes': list(parent.classes), 'checkpoint_sha256': parent.checkpoint_sha256,
            'dataset_fingerprint': parent.dataset_fingerprint, 'semantics': parent.semantics,
            'summary': parent_summary(parent.checkpoint_path.parent)})
    return {'parents': parents, 'total': len(parents)}
