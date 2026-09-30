"""
backend/engine/segmentation/model.py

Modular Semantic Segmentation Architectures for Industrial Defect Inspection.
Supports:
  1. Pure PyTorch Modular UNet (Fast Prototype & High Precision configurations)
  2. TorchVision DeepLabV3 Adapter (ResNet50 / MobileNetV3 backbones)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models.segmentation as tv_seg


class DoubleConv(nn.Module):
    """(Conv2d -> BatchNorm2d -> ReLU) * 2 block with padding=1."""

    def __init__(self, in_channels: int, out_channels: int, mid_channels: Optional[int] = None):
        super().__init__()
        if mid_channels is None:
            mid_channels = out_channels
        self.double_conv = nn.Sequential(
            nn.Conv2d(in_channels, mid_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(mid_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(mid_channels, out_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.double_conv(x)


class UNet(nn.Module):
    """
    Modular pure PyTorch UNet with configurable depth, channel progression, and upsampling.
    
    Args:
        in_channels: Number of input image channels (default: 3 for RGB).
        num_classes: Number of target segmentation classes (default: 2 for bg/defect).
        features: Channel progression across encoder stages.
                  Default [32, 64, 128, 256] for Fast Prototype,
                  [64, 128, 256, 512] for High Precision.
        bilinear: If True, uses bilinear interpolation upsampling;
                  if False, uses ConvTranspose2d.
    """

    def __init__(
        self,
        in_channels: int = 3,
        num_classes: int = 2,
        features: Optional[List[int]] = None,
        bilinear: bool = True,
    ):
        super().__init__()
        if features is None:
            features = [32, 64, 128, 256]

        self.in_channels = in_channels
        self.num_classes = num_classes
        self.features = features
        self.bilinear = bilinear

        self.downs = nn.ModuleList()
        self.ups = nn.ModuleList()
        self.pool = nn.MaxPool2d(kernel_size=2, stride=2)

        # Encoder stages
        curr_in = in_channels
        for f in features:
            self.downs.append(DoubleConv(curr_in, f))
            curr_in = f

        # Bottleneck stage
        self.bottleneck = DoubleConv(features[-1], features[-1] * 2)

        # Decoder stages
        prev_ch = features[-1] * 2
        for f in reversed(features):
            if bilinear:
                self.ups.append(nn.Upsample(scale_factor=2, mode="bilinear", align_corners=True))
                self.ups.append(DoubleConv(prev_ch + f, f))
            else:
                self.ups.append(nn.ConvTranspose2d(prev_ch, f, kernel_size=2, stride=2))
                self.ups.append(DoubleConv(f + f, f))
            prev_ch = f

        # Output projection head (1x1 conv)
        self.head = nn.Conv2d(features[0], num_classes, kernel_size=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        skips = []
        for down in self.downs:
            x = down(x)
            skips.append(x)
            x = self.pool(x)

        x = self.bottleneck(x)
        skips = skips[::-1]

        for i in range(0, len(self.ups), 2):
            up = self.ups[i]
            conv = self.ups[i + 1]
            x = up(x)
            skip = skips[i // 2]
            if x.shape[2:] != skip.shape[2:]:
                x = F.interpolate(x, size=skip.shape[2:], mode="bilinear", align_corners=True)
            x = torch.cat([skip, x], dim=1)
            x = conv(x)

        return self.head(x)


class DeepLabV3Wrapper(nn.Module):
    """Adapter for TorchVision DeepLabV3 with customized segmentation head."""

    def __init__(
        self,
        num_classes: int = 2,
        backbone: str = "resnet50",
        pretrained: bool = True,
    ):
        super().__init__()
        self.num_classes = num_classes

        if backbone == "resnet50":
            weights = tv_seg.DeepLabV3_ResNet50_Weights.DEFAULT if pretrained else None
            self.model = tv_seg.deeplabv3_resnet50(
                weights=weights, **({"weights_backbone": None} if not pretrained else {}),
            )
            in_ch = self.model.classifier[4].in_channels
            self.model.classifier[4] = nn.Conv2d(in_ch, num_classes, kernel_size=1)
            if self.model.aux_classifier is not None:
                aux_in = self.model.aux_classifier[4].in_channels
                self.model.aux_classifier[4] = nn.Conv2d(aux_in, num_classes, kernel_size=1)
        elif backbone in ("mobilenet_v3", "mobilenet"):
            weights = tv_seg.DeepLabV3_MobileNet_V3_Large_Weights.DEFAULT if pretrained else None
            self.model = tv_seg.deeplabv3_mobilenet_v3_large(
                weights=weights, **({"weights_backbone": None} if not pretrained else {}),
            )
            in_ch = self.model.classifier[4].in_channels
            self.model.classifier[4] = nn.Conv2d(in_ch, num_classes, kernel_size=1)
            if self.model.aux_classifier is not None:
                aux_in = self.model.aux_classifier[4].in_channels
                self.model.aux_classifier[4] = nn.Conv2d(aux_in, num_classes, kernel_size=1)
        else:
            raise ValueError(f"Unsupported DeepLabV3 backbone: {backbone}")

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.model(x)
        return out["out"]


def build_segmentation_model(
    model_name: str = "unet",
    num_classes: int = 2,
    in_channels: int = 3,
    preset: str = "fast",
    pretrained: bool = True,
    pretrained_checkpoint: str | None = None,
    pretrained_sha256: str | None = None,
) -> nn.Module:
    """
    Factory constructing segmentation models based on model_name and preset.
    """
    name = model_name.lower().strip()
    p = preset.lower().strip()

    from backend.engine.model_backbones import DinoTaskModel, is_dino_backbone
    if is_dino_backbone(name):
        if in_channels != 3:
            raise ValueError("DINOv3 segmentation requires RGB input channels")
        return DinoTaskModel("segmentation", name, num_classes, pretrained,
                             pretrained_checkpoint, pretrained_sha256)

    if "deeplab" in name:
        bb = "resnet50" if ("resnet" in name or p == "precision") else "mobilenet_v3"
        return DeepLabV3Wrapper(num_classes=num_classes, backbone=bb, pretrained=pretrained)

    if name not in {"unet", "unet_lightweight", "unet_full"}:
        raise ValueError(f"Unsupported segmentation architecture: {model_name}")

    # UNet family
    if p == "precision" or "full" in name:
        features = [64, 128, 256, 512]
    else:
        features = [32, 64, 128, 256]

    return UNet(
        in_channels=in_channels,
        num_classes=num_classes,
        features=features,
        bilinear=True,
    )


# Alias
create_segmentation_model = build_segmentation_model
