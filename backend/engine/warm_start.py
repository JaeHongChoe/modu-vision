"""Verified local checkpoint lineage and strict warm-start weight loading."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch
from torch import nn

from backend.engine.checkpoint_paths import completed_job_receipt, is_job_id


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def architecture_for(task: str, preset: str, overrides: Mapping[str, Any] | None = None) -> str:
    """Describe the model structure selected for a supervised training run."""
    from backend.engine.trainer import PRESET_CONFIGS
    from backend.engine.model_backbones import canonical_dino_name
    if preset not in PRESET_CONFIGS:
        raise ValueError("Unsupported training preset for warm start")
    config = PRESET_CONFIGS[preset]
    overrides = overrides or {}
    if task == "classification":
        return f"classification:{canonical_dino_name(str(overrides.get('backbone', config.backbone_classification)))}"
    if task == "patch_classification":
        backbone = canonical_dino_name(str(overrides.get("backbone", config.backbone_classification)))
        return f"patch_classification:{backbone}"
    if task == "segmentation":
        model_name = canonical_dino_name(str(overrides.get('model_name', config.backbone_segmentation)))
        if 'deeplab' in model_name:
            backbone = 'resnet50' if 'resnet' in model_name or preset == 'precision' else 'mobilenet_v3'
            return f'segmentation:deeplab:{backbone}'
        if model_name in ('unet', 'unet_full', 'unet_lightweight'):
            return f"segmentation:{'precision' if preset == 'precision' or 'full' in model_name else 'fast'}"
        return f"segmentation:{model_name}"
    if task == "detection":
        backbone = str(overrides.get('backbone', config.backbone_detection)).lower().strip().removesuffix('.pt').removesuffix('.yaml')
        legacy = {'fast': 'fasterrcnn_mobilenet_v3_large_fpn', 'mobilenet': 'fasterrcnn_mobilenet_v3_large_fpn',
                  'precision': 'fasterrcnn_resnet50_fpn_v2', 'resnet50': 'fasterrcnn_resnet50_fpn_v2'}
        backbone = legacy.get(preset) if backbone == 'fasterrcnn' else legacy.get(backbone, backbone)
        return f"detection:{backbone}"
    if task in ("anomaly", "anomaly_detection"):
        kind = overrides.get('anomaly_method', 'patchcore' if 'patchcore' in config.backbone_anomaly else 'padim')
        if kind not in ('padim', 'patchcore'):
            raise ValueError('Unsupported anomaly method')
        return f"{task}:{kind}:resnet18"
    raise ValueError(f"Warm start is unsupported for task {task}")


def _metadata_architecture(task: str, metadata: Mapping[str, Any]) -> str | None:
    if task in ("classification", "patch_classification"):
        from backend.engine.model_backbones import canonical_dino_name
        backbone = metadata.get("backbone")
        return f"{task}:{canonical_dino_name(backbone)}" if isinstance(backbone, str) and backbone else None
    if task == "segmentation":
        model_name = metadata.get('model_name')
        preset = metadata.get("preset")
        if model_name and preset in ('fast', 'precision'):
            return architecture_for(task, preset, {'model_name': model_name})
        return f"segmentation:{preset}" if preset in ("fast", "precision") else None
    if task == "detection":
        backbone = metadata.get('backbone')
        if not backbone:
            preset = metadata.get('detector_preset', metadata.get('preset'))
            legacy = {'fast': 'fasterrcnn_mobilenet_v3_large_fpn', 'precision': 'fasterrcnn_resnet50_fpn_v2'}
            backbone = legacy.get(preset)
        if backbone:
            return architecture_for('detection', metadata.get('preset', metadata.get('detector_preset', 'fast')), {'backbone': backbone})
        return None
    if task in ('anomaly', 'anomaly_detection'):
        kind = metadata.get('detector_type')
        backbone = metadata.get('feature_backbone', metadata.get('model_state_dict', {}).get('backbone_name'))
        return f"{task}:{kind}:{backbone}" if kind and backbone else None
    return None


def training_classes(task: str, source: str | Path) -> tuple[str, ...]:
    """Read the class ordering selected by the training datasets without writing a candidate."""
    from backend.api.routes_dataset import _resolve_task_folder, _paired_labelme_images
    from backend.engine.dataset_loaders import ClassificationDataset
    from backend.engine.grouped_dataset_views import load_manifest_dataset
    source = Path(source).resolve()
    effective = _resolve_task_folder(source, task)
    if task == 'classification':
        return tuple(ClassificationDataset(effective, split='train').classes)
    if task == 'patch_classification':
        from backend.engine.patch_classification import load_patch_manifest
        return tuple(load_patch_manifest(effective).classes)
    if task in ('anomaly', 'anomaly_detection'):
        return ('good', 'anomaly')
    paired = _paired_labelme_images(source)
    if paired:
        if task == 'segmentation':
            return ('background', 'defect')
        from backend.engine.grouped_dataset_views import _annotations
        labels = set()
        for image in paired:
            annotations, mask = _annotations(source, image)
            for row in annotations or []:
                if row.get('is_normal') or row.get('label') == 'OK':
                    continue
                if row.get('type') in ('bbox', 'polygon', 'rotated_bbox'):
                    labels.add(str(row.get('label') or 'defect').strip() or 'defect')
                elif row.get('type') == 'brush_mask':
                    labels.add('defect')
            if not annotations and mask:
                labels.add('defect')
        return tuple(sorted(labels))
    grouped = load_manifest_dataset(task, effective, 'train')
    if grouped is not None:
        return tuple(grouped.categories.values()) if task == 'detection' else tuple(grouped.classes)
    if task == 'detection':
        from backend.engine.trainer import _build_detection_datasets
        train, _ = _build_detection_datasets(effective, None, (64, 64))
        return tuple(train.categories.values())
    if task == 'segmentation':
        return ('background', 'defect')
    raise ValueError('Unsupported parent task')


@dataclass(frozen=True)
class WarmStartParent:
    job_id: str
    checkpoint_path: Path
    checkpoint_sha256: str
    task: str
    architecture: str
    classes: tuple[str, ...]
    dataset_fingerprint: str
    semantics: str = "weight_initialization"

    def lineage(self) -> dict[str, str]:
        return {
            "parent_job_id": self.job_id,
            "parent_checkpoint_sha256": self.checkpoint_sha256,
            "parent_dataset_fingerprint": self.dataset_fingerprint,
            "semantics": self.semantics,
        }


def resolve_warm_start_parent(
    job_id: str, models_dir: str | Path, source_dataset_path: str | Path,
    task: str, architecture: str,
) -> WarmStartParent:
    """Accept only a completed model in the current project's model store."""
    root = Path(models_dir)
    if not is_job_id(job_id) or root.is_symlink() or not root.is_dir():
        raise ValueError("Warm-start parent job is outside the current project")
    job_dir = root / job_id
    checkpoint = job_dir / "best_model.pt"
    meta_path = job_dir / "model_meta.json"
    receipt_path = job_dir / "job_receipt.json"
    if job_dir.is_symlink() or not job_dir.is_dir() or checkpoint.is_symlink() or not checkpoint.is_file():
        raise ValueError("Warm-start parent checkpoint is missing from the current project")
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError("Warm-start parent needs a completed job receipt")
    receipt = completed_job_receipt(job_dir)
    if not isinstance(receipt, dict) or receipt.get("job_id") != job_id:
        raise ValueError("Warm-start parent needs a valid completed job receipt")
    if receipt.get("task") != task:
        raise ValueError("Warm-start parent task differs from the requested task")
    recorded_source = receipt.get("source_dataset_path")
    if (not isinstance(recorded_source, str) or not recorded_source
            or Path(recorded_source).expanduser().resolve() != Path(source_dataset_path).expanduser().resolve()):
        raise ValueError("Warm-start parent source differs from this project's source")
    fingerprint = receipt.get("dataset_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint.startswith("v1:"):
        raise ValueError("Warm-start parent source fingerprint is missing")
    if meta_path.is_symlink() or not meta_path.is_file():
        raise ValueError("Warm-start parent metadata is missing")
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Warm-start parent metadata is invalid") from exc
    if not isinstance(metadata, dict) or metadata.get("task") != task:
        raise ValueError("Warm-start parent metadata task differs from the requested task")
    classes = metadata.get("classes")
    if not isinstance(classes, list) or not classes or any(not isinstance(name, str) for name in classes):
        raise ValueError("Warm-start parent classes are missing")
    actual_architecture = _metadata_architecture(task, metadata)
    if actual_architecture != architecture:
        raise ValueError("Warm-start parent architecture differs from the requested model")
    try:
        payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    except Exception as exc:
        raise ValueError("Warm-start parent checkpoint is unreadable") from exc
    digest = _sha256(checkpoint)
    for record in (receipt, metadata):
        if record.get('checkpoint_sha256') and record['checkpoint_sha256'] != digest:
            raise ValueError('Warm-start parent checkpoint SHA-256 differs from completed metadata')
    if (not isinstance(payload, dict) or payload.get("task") != task
            or payload.get("classes") != classes
            or _metadata_architecture(task, payload) != architecture
            or not isinstance(payload.get("model_state_dict"), dict)
            or not payload["model_state_dict"]):
        raise ValueError("Warm-start parent checkpoint conflicts with its model metadata")
    if task in ('anomaly', 'anomaly_detection') and not payload['model_state_dict'].get('feature_extractor_state_dict'):
        raise ValueError('Statistical refit needs a parent with saved feature extractor weights; train a fresh candidate first')
    if payload.get('training_provenance'):
        from backend.engine.specialized_models import _verify_historical_source
        _verify_historical_source(root.resolve(), source_dataset_path, payload, metadata)
    return WarmStartParent(
        job_id=job_id, checkpoint_path=checkpoint.resolve(), checkpoint_sha256=digest,
        task=task, architecture=architecture, classes=tuple(classes), dataset_fingerprint=fingerprint,
        semantics='statistical_refit' if task in ('anomaly', 'anomaly_detection') else 'weight_initialization',
    )


def load_parent_weights(model: nn.Module, parent: WarmStartParent, classes: Sequence[str]) -> None:
    """Recheck content and signature before loading all weights, with no partial load."""
    if tuple(classes) != parent.classes:
        raise ValueError("Warm-start parent classes differ from current training classes")
    verify_parent_status(parent)
    checkpoint = parent.checkpoint_path
    if checkpoint.is_symlink() or not checkpoint.is_file() or _sha256(checkpoint) != parent.checkpoint_sha256:
        raise ValueError("Warm-start parent checkpoint SHA-256 changed before training")
    try:
        payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    except Exception as exc:
        raise ValueError("Warm-start parent checkpoint could not be read safely") from exc
    if (not isinstance(payload, dict) or payload.get("task") != parent.task
            or payload.get("classes") != list(parent.classes)
            or _metadata_architecture(parent.task, payload) != parent.architecture):
        raise ValueError("Warm-start parent checkpoint task, classes, or architecture do not match metadata")
    weights = payload.get("model_state_dict")
    if parent.semantics == 'statistical_refit':
        feature_weights = weights.get('feature_extractor_state_dict') if isinstance(weights, dict) else None
        _strict_state(model.feature_extractor, feature_weights)
        if weights.get('backbone_name') != model.backbone_name:
            raise ValueError('Statistical refit feature extractor architecture differs')
        if hasattr(model, 'sub_dims'):
            dims = weights.get('sub_dims')
            if (not isinstance(dims, torch.Tensor) or dims.shape != model.sub_dims.shape
                    or dims.dtype != model.sub_dims.dtype):
                raise ValueError('Statistical refit feature dimension signature differs')
        model.feature_extractor.load_state_dict(feature_weights, strict=True)
        if hasattr(model, 'sub_dims'):
            model.sub_dims = dims.to(model.device)
        return
    _strict_state(model, weights)
    model.load_state_dict(weights, strict=True)
    if isinstance(getattr(model, 'model_metadata', None), dict):
        for key in ('pretrained', 'pretrained_source', 'pretrained_sha256', 'encoder_architecture',
                    'encoder_frozen', 'adapter_version', 'input_normalization'):
            if key in payload:
                model.model_metadata[key] = payload[key]


def verify_parent_status(parent: WarmStartParent) -> None:
    """A queued local parent must still be completed; transfers carry a verified envelope."""
    if parent.checkpoint_path.name == 'parent.pt':
        return
    from backend.engine.specialized_models import require_completed_checkpoint
    directory = parent.checkpoint_path.parent
    receipt_path = directory / 'job_receipt.json'
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError('Warm-start parent needs its completed receipt before training')
    receipt = json.loads(receipt_path.read_text())
    if (not isinstance(receipt, dict) or receipt.get('status') != 'completed'
            or receipt.get('job_id') != parent.job_id or receipt.get('task') != parent.task):
        raise ValueError('Warm-start parent must still be a completed job before training')
    if receipt.get('checkpoint_sha256') and receipt['checkpoint_sha256'] != parent.checkpoint_sha256:
        raise ValueError('Warm-start parent completed receipt SHA-256 changed before training')
    require_completed_checkpoint(parent.checkpoint_path)


def _strict_state(model: nn.Module, weights) -> None:
    """Validate every tensor before allowing a module to mutate its weights."""
    current = model.state_dict()
    if (not isinstance(weights, dict) or set(weights) != set(current)
            or any(not isinstance(value, torch.Tensor) or value.shape != current[key].shape
                   or value.dtype != current[key].dtype for key, value in weights.items())):
        raise ValueError("Warm-start parent weights are incompatible with the selected model")


def portable_parent(parent: WarmStartParent, directory: Path) -> dict[str, Any]:
    """Copy a verified completed parent to a job-owned portable transfer slot."""
    import shutil
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    if parent.checkpoint_path.is_symlink() or _sha256(parent.checkpoint_path) != parent.checkpoint_sha256:
        raise ValueError('Warm-start parent hash changed before transfer')
    target=directory/'parent.pt'
    verify_parent_status(parent)
    shutil.copyfile(parent.checkpoint_path,target)
    if _sha256(target)!=parent.checkpoint_sha256: raise ValueError('Warm-start transfer hash mismatch')
    return {'checkpoint':'parent.pt','job_id':parent.job_id,'checkpoint_sha256':parent.checkpoint_sha256,
            'task':parent.task,'architecture':parent.architecture,'classes':list(parent.classes),'dataset_fingerprint':parent.dataset_fingerprint,'semantics':parent.semantics}


def restore_portable_parent(directory: Path, envelope: dict[str, Any], task: str) -> WarmStartParent:
    if envelope.get('checkpoint')!='parent.pt' or envelope.get('task')!=task or not is_job_id(envelope.get('job_id')):
        raise ValueError('Invalid portable warm-start parent identity')
    checkpoint=Path(directory)/'parent.pt'
    if checkpoint.is_symlink() or not checkpoint.is_file() or _sha256(checkpoint)!=envelope.get('checkpoint_sha256'):
        raise ValueError('Portable warm-start parent hash mismatch')
    payload=torch.load(checkpoint,map_location='cpu',weights_only=True)
    classes=envelope.get('classes')
    if not isinstance(payload,dict) or payload.get('task')!=task or not isinstance(classes,list) or payload.get('classes')!=classes or _metadata_architecture(task,payload)!=envelope.get('architecture') or not isinstance(payload.get('model_state_dict'),dict):
        raise ValueError('Portable parent model signature mismatch')
    semantics = 'statistical_refit' if task in ('anomaly', 'anomaly_detection') else 'weight_initialization'
    if envelope.get('semantics', semantics) != semantics:
        raise ValueError('Portable parent training semantics mismatch')
    if semantics == 'statistical_refit' and not payload['model_state_dict'].get('feature_extractor_state_dict'):
        raise ValueError('Portable statistical refit parent has no saved feature extractor')
    return WarmStartParent(envelope['job_id'],checkpoint,envelope['checkpoint_sha256'],task,envelope['architecture'],tuple(classes),envelope['dataset_fingerprint'],semantics)
