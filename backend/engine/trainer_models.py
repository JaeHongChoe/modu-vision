"""
backend/engine/trainer_models.py

Convenience factory adapters for trainer model initialization across all 4 tasks.
"""

from __future__ import annotations
import torch.nn as nn

from backend.engine.classification import create_classification_model
from backend.engine.detection import create_detection_model
from backend.engine.segmentation import build_segmentation_model
from backend.engine.anomaly import PaDiMDetector, PatchCoreDetector


def build_classification_model(num_classes: int = 2, backbone: str = "resnet18", pretrained: bool = True,
                               pretrained_checkpoint: str | None = None,
                               pretrained_sha256: str | None = None) -> nn.Module:
    return create_classification_model(backbone=backbone, num_classes=num_classes, pretrained=pretrained,
                                       pretrained_checkpoint=pretrained_checkpoint, pretrained_sha256=pretrained_sha256)


def build_detection_model(num_classes: int = 4, preset: str = "fast", pretrained: bool = True,
                          backbone: str | None = None, pretrained_checkpoint: str | None = None,
                          pretrained_sha256: str | None = None) -> nn.Module:
    return create_detection_model(preset=preset, num_classes=num_classes, pretrained=pretrained,
                                  backbone=backbone, pretrained_checkpoint=pretrained_checkpoint,
                                  pretrained_sha256=pretrained_sha256)


def build_anomaly_model(backbone: str = "resnet18", preset: str = "fast", pretrained: bool = True):
    if preset.lower().strip() == "precision" or "patchcore" in backbone.lower():
        return PatchCoreDetector(backbone_name=backbone, pretrained=pretrained)
    return PaDiMDetector(backbone_name=backbone, pretrained=pretrained)
