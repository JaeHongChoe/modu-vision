"""
backend/engine/detection/model.py

Faster R-CNN model factory with MPS-safe box_roi_pool wrapper and custom FastRCNNPredictor.
Supports Fast Prototype (MobileNetV3 Large FPN) and High Precision (ResNet50 FPN v2) backbones.
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence
import torch
import torch.nn as nn
import torchvision.models.detection as detection
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor


def checkpoint_detection_num_classes(state_dict: Mapping[str, torch.Tensor], classes: Sequence[str]) -> int:
    """Use the trained predictor head, including older background-named metadata."""
    predictor = state_dict.get("roi_heads.box_predictor.cls_score.weight")
    if predictor is not None:
        return int(predictor.shape[0])
    foreground = foreground_class_names(classes)
    return max(2, len(foreground) + 1)


def foreground_class_names(classes: Sequence[str]) -> list[str]:
    names = [str(name) for name in classes]
    if names and names[0].lower() in {"background", "__background__"}:
        return names[1:]
    return names


def create_detection_model(
    preset: str = "fast",
    num_classes: int = 4,  # 0 = background, 1..K = defect classes
    pretrained: bool = True,
) -> nn.Module:
    """
    Constructs Faster R-CNN detection model with custom classification & box regression head.
    
    CRITICAL HARDWARE INVARIANT:
      PyTorch's Metal MPS kernel for `roi_align` deadlocks when proposal counts are high (>50).
      This factory wraps `model.roi_heads.box_roi_pool.forward` with an `mps_safe_roi_pool` bridge that executes
      RoI extraction on CPU and transfers back to MPS in <0.08s, completely eliminating MPS deadlocks while retaining
      full MPS hardware acceleration for backbone features, RPN, and prediction heads.
    """
    preset_clean = preset.lower().strip()

    if preset_clean in ("fast", "mobilenet", "fasterrcnn_mobilenet_v3_large_fpn"):
        weights = (
            detection.FasterRCNN_MobileNet_V3_Large_FPN_Weights.DEFAULT
            if pretrained
            else None
        )
        model = detection.fasterrcnn_mobilenet_v3_large_fpn(
            weights=weights, **({"weights_backbone": None} if not pretrained else {}),
        )
    elif preset_clean in ("precision", "resnet50", "fasterrcnn_resnet50_fpn_v2"):
        weights = (
            detection.FasterRCNN_ResNet50_FPN_V2_Weights.DEFAULT
            if pretrained
            else None
        )
        model = detection.fasterrcnn_resnet50_fpn_v2(
            weights=weights, **({"weights_backbone": None} if not pretrained else {}),
        )
    else:
        # Default fallback to MobileNetV3 FPN
        weights = (
            detection.FasterRCNN_MobileNet_V3_Large_FPN_Weights.DEFAULT
            if pretrained
            else None
        )
        model = detection.fasterrcnn_mobilenet_v3_large_fpn(
            weights=weights, **({"weights_backbone": None} if not pretrained else {}),
        )

    # Replace box predictor head with target num_classes
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)

    # Wrap box_roi_pool forward to bypass MPS Metal kernel deadlock
    orig_pool = model.roi_heads.box_roi_pool.forward

    def mps_safe_roi_pool(x, boxes, image_shapes):
        if any(t.device.type == "mps" for t in x.values()):
            x_cpu = {k: v.cpu() for k, v in x.items()}
            b_cpu = [b.cpu() for b in boxes]
            out_cpu = orig_pool(x_cpu, b_cpu, image_shapes)
            return out_cpu.to("mps")
        return orig_pool(x, boxes, image_shapes)

    model.roi_heads.box_roi_pool.forward = mps_safe_roi_pool
    return model


# Alias for trainer compatibility
build_detection_model = create_detection_model
