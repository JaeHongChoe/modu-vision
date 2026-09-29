"""
backend/engine/classification/model.py

Classification model factory supporting ResNet18, ConvNeXt-Tiny, and EfficientNet-B0 backbones
with custom classification heads and target Grad-CAM layer registration.
"""

from __future__ import annotations

import torch.nn as nn
import torchvision.models as models


def create_classification_model(
    backbone: str = "resnet18",
    num_classes: int = 2,
    pretrained: bool = True,
) -> nn.Module:
    """
    Constructs transfer learning classification model with custom classification head.
    Attaches `target_gradcam_layer` attribute to the model pointing to the last convolutional layer.

    Args:
        backbone: One of 'resnet18', 'convnext_tiny', 'efficientnet_b0'
        num_classes: Number of target output classes (e.g. 2 for OK vs NG)
        pretrained: If True, initializes weights from default ImageNet pretraining
    """
    backbone_clean = backbone.lower().strip()

    if backbone_clean in ("resnet18", "resnet"):
        weights = models.ResNet18_Weights.DEFAULT if pretrained else None
        model = models.resnet18(weights=weights)
        in_features = model.fc.in_features
        model.fc = nn.Linear(in_features, num_classes)
        model.target_gradcam_layer = model.layer4

    elif backbone_clean in ("convnext_tiny", "convnext"):
        weights = models.ConvNeXt_Tiny_Weights.DEFAULT if pretrained else None
        model = models.convnext_tiny(weights=weights)
        in_features = model.classifier[2].in_features
        model.classifier[2] = nn.Linear(in_features, num_classes)
        model.target_gradcam_layer = model.features[-1]

    elif backbone_clean in ("efficientnet_b0", "efficientnet"):
        weights = models.EfficientNet_B0_Weights.DEFAULT if pretrained else None
        model = models.efficientnet_b0(weights=weights)
        in_features = model.classifier[1].in_features
        model.classifier[1] = nn.Linear(in_features, num_classes)
        model.target_gradcam_layer = model.features[-1]

    else:
        raise ValueError(
            f"Unsupported classification backbone: '{backbone}'. "
            f"Supported backbones: 'resnet18', 'convnext_tiny', 'efficientnet_b0'"
        )

    return model


# Alias for trainer compatibility
build_classification_model = create_classification_model
