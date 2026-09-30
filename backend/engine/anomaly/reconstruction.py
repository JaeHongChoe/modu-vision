"""Offline reconstruction of the feature space used by fitted anomaly statistics."""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from typing import Any, Mapping

import torch
from torchvision import models

from backend.engine.anomaly.padim import PaDiMDetector
from backend.engine.anomaly.patchcore import PatchCoreDetector


def _backbone_name(value: Any) -> str:
    name = str(value).lower().strip()
    if name == 'resnet':
        name = 'resnet18'
    if name not in ('resnet18', 'resnet50'):
        raise ValueError(f'Unsupported saved anomaly backbone: {value}')
    return name


def _verified_legacy_backbone(backbone: str, metadata: Mapping[str, Any]):
    """Read verified torchvision bytes directly; this path never asks a loader to download."""
    if metadata.get('pretrained') is False:
        raise ValueError('Legacy anomaly statistics have no saved feature state or verified local pretrained origin')
    enum = models.ResNet18_Weights if backbone == 'resnet18' else models.ResNet50_Weights
    recorded_weights = metadata.get('pretrained_weights')
    if recorded_weights:
        name = str(recorded_weights).rsplit('.', 1)[-1]
        if name not in enum.__members__:
            raise ValueError(f'Unsupported saved torchvision weights: {recorded_weights}')
        weights = enum.__members__[name]
    else:
        # Historical ResNetFeatureExtractor selected this exact torchvision default.
        weights = enum.DEFAULT
    source = metadata.get('pretrained_source')
    if isinstance(source, str) and source.startswith('https://'):
        matching = [item for item in enum if item.url == source]
        if not matching:
            raise ValueError('Saved anomaly pretrained source does not match its backbone')
        if recorded_weights and matching[0] != weights:
            raise ValueError('Saved anomaly pretrained source and weights disagree')
        weights = matching[0]
    filename = Path(weights.url).name
    official_hash = re.search(r'-([0-9a-f]{8,64})\.', filename)
    path = Path(torch.hub.get_dir()) / 'checkpoints' / filename
    if not official_hash or path.is_symlink() or not path.is_file():
        raise ValueError(f'Legacy anomaly statistics require verified local pretrained {backbone} weights: {path}')
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    pinned = metadata.get('pretrained_sha256')
    if not digest.startswith(official_hash.group(1)) or (pinned and pinned != digest):
        raise ValueError(f'Legacy anomaly local pretrained cache SHA-256 mismatch: {path}')
    try:
        state = torch.load(io.BytesIO(data), map_location='cpu', weights_only=True)
    except Exception as exc:
        raise ValueError('Verified local pretrained anomaly weights are not a valid state dictionary') from exc
    return state, {'feature_source': 'verified_local_torchvision_cache',
                   'pretrained_source': weights.url, 'pretrained_sha256': digest}


def reconstruct_anomaly_detector(state: Mapping[str, Any], metadata: Mapping[str, Any] | None = None,
                                 device='cpu'):
    """Restore full saved features, or the verified original cached legacy encoder."""
    if not isinstance(state, Mapping):
        raise ValueError('Saved anomaly state must be a dictionary')
    metadata = metadata or {}
    kind = str(metadata.get('detector_type') or '').lower().strip()
    if kind and kind not in ('padim', 'patchcore'):
        raise ValueError(f'Unsupported saved anomaly detector type: {kind}')
    if not kind:
        preset = str(metadata.get('preset', '')).lower()
        kind = 'patchcore' if 'coreset' in state or 'patchcore' in preset or 'precision' in preset else 'padim'
    saved_name = state.get('backbone_name')
    recorded_name = metadata.get('feature_backbone') or metadata.get('backbone')
    backbone = _backbone_name(saved_name or recorded_name or 'resnet18')
    if recorded_name and _backbone_name(recorded_name) != backbone:
        raise ValueError('Saved anomaly state and metadata backbone disagree')
    features = state.get('feature_extractor_state_dict')
    if features is None:
        legacy_state, receipt = _verified_legacy_backbone(backbone, metadata)
    else:
        if not isinstance(features, Mapping) or not features:
            raise ValueError('Saved anomaly feature extractor state is empty or invalid')
        legacy_state = None
        receipt = {'feature_source': 'saved_checkpoint'}
    detector = (PatchCoreDetector if kind == 'patchcore' else PaDiMDetector)(
        backbone_name=backbone, device=device, pretrained=False)
    if legacy_state is not None:
        detector.feature_extractor.backbone.load_state_dict(legacy_state, strict=True)
    detector.load_state_dict(dict(state))
    detector.eval()
    detector.reconstruction_metadata = receipt
    return detector
