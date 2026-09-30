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
    if preset not in PRESET_CONFIGS:
        raise ValueError("Unsupported training preset for warm start")
    config = PRESET_CONFIGS[preset]
    if task == "classification":
        return f"classification:{config.backbone_classification}"
    if task == "patch_classification":
        backbone = str((overrides or {}).get("backbone", config.backbone_classification))
        return f"patch_classification:{backbone}"
    if task == "segmentation":
        return f"segmentation:{preset}"
    raise ValueError(f"Warm start is unsupported for task {task}")


def _metadata_architecture(task: str, metadata: Mapping[str, Any]) -> str | None:
    if task in ("classification", "patch_classification"):
        backbone = metadata.get("backbone")
        return f"{task}:{backbone}" if isinstance(backbone, str) and backbone else None
    if task == "segmentation":
        preset = metadata.get("preset")
        return f"segmentation:{preset}" if preset in ("fast", "precision") else None
    return None


@dataclass(frozen=True)
class WarmStartParent:
    job_id: str
    checkpoint_path: Path
    checkpoint_sha256: str
    task: str
    architecture: str
    classes: tuple[str, ...]
    dataset_fingerprint: str

    def lineage(self) -> dict[str, str]:
        return {
            "parent_job_id": self.job_id,
            "parent_checkpoint_sha256": self.checkpoint_sha256,
            "parent_dataset_fingerprint": self.dataset_fingerprint,
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
    if (not isinstance(payload, dict) or payload.get("task") != task
            or payload.get("classes") != classes
            or _metadata_architecture(task, payload) != architecture
            or not isinstance(payload.get("model_state_dict"), dict)
            or not payload["model_state_dict"]):
        raise ValueError("Warm-start parent checkpoint conflicts with its model metadata")
    return WarmStartParent(
        job_id=job_id, checkpoint_path=checkpoint.resolve(), checkpoint_sha256=_sha256(checkpoint),
        task=task, architecture=architecture, classes=tuple(classes), dataset_fingerprint=fingerprint,
    )


def load_parent_weights(model: nn.Module, parent: WarmStartParent, classes: Sequence[str]) -> None:
    """Recheck content and signature before loading all weights, with no partial load."""
    if tuple(classes) != parent.classes:
        raise ValueError("Warm-start parent classes differ from current training classes")
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
    current = model.state_dict()
    if (not isinstance(weights, dict) or set(weights) != set(current)
            or any(not isinstance(value, torch.Tensor) or value.shape != current[key].shape
                   or value.dtype != current[key].dtype for key, value in weights.items())):
        raise ValueError("Warm-start parent weights are incompatible with the selected model")
    model.load_state_dict(weights, strict=True)


def portable_parent(parent: WarmStartParent, directory: Path) -> dict[str, Any]:
    """Copy a verified completed parent to a job-owned portable transfer slot."""
    import shutil
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    if parent.checkpoint_path.is_symlink() or _sha256(parent.checkpoint_path) != parent.checkpoint_sha256:
        raise ValueError('Warm-start parent hash changed before transfer')
    target=directory/'parent.pt'
    shutil.copyfile(parent.checkpoint_path,target)
    if _sha256(target)!=parent.checkpoint_sha256: raise ValueError('Warm-start transfer hash mismatch')
    return {'checkpoint':'parent.pt','job_id':parent.job_id,'checkpoint_sha256':parent.checkpoint_sha256,
            'task':parent.task,'architecture':parent.architecture,'classes':list(parent.classes),'dataset_fingerprint':parent.dataset_fingerprint}


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
    return WarmStartParent(envelope['job_id'],checkpoint,envelope['checkpoint_sha256'],task,envelope['architecture'],tuple(classes),envelope['dataset_fingerprint'])
