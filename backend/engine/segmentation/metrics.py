"""
backend/engine/segmentation/metrics.py

Comprehensive Industrial Segmentation Metrics:
mIoU, Mean Dice, Pixel Accuracy, Foreground Defect IoU, and Pixel Confusion Matrix.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union
import numpy as np
import torch


def compute_segmentation_metrics(
    predictions: Union[np.ndarray, torch.Tensor],
    targets: Union[np.ndarray, torch.Tensor],
    num_classes: int = 2,
    class_names: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """
    Computes per-class and global segmentation metrics.

    Args:
        predictions: 2D or 3D integer array of class predictions
        targets: 2D or 3D integer array of ground truth labels
        num_classes: Number of classes (including background = 0)
        class_names: Optional mapping from class index to name
    """
    if isinstance(predictions, torch.Tensor):
        preds_np = predictions.detach().cpu().numpy().reshape(-1)
    else:
        preds_np = np.asarray(predictions).reshape(-1)

    if isinstance(targets, torch.Tensor):
        targs_np = targets.detach().cpu().numpy().reshape(-1)
    else:
        targs_np = np.asarray(targets).reshape(-1)

    cm = np.zeros((num_classes, num_classes), dtype=np.int64)
    valid_mask = (targs_np >= 0) & (targs_np < num_classes) & (preds_np >= 0) & (preds_np < num_classes)
    targs_valid = targs_np[valid_mask]
    preds_valid = preds_np[valid_mask]

    for t, p in zip(targs_valid, preds_valid):
        cm[t, p] += 1

    ious: Dict[str, float] = {}
    dices: Dict[str, float] = {}

    total_pixels = cm.sum()
    pixel_acc = float(np.diag(cm).sum() / max(1, total_pixels))

    for c in range(num_classes):
        c_name = class_names.get(c, f"class_{c}") if class_names else f"class_{c}"
        tp = float(cm[c, c])
        fp = float(cm[:, c].sum() - tp)
        fn = float(cm[c, :].sum() - tp)

        denom_iou = tp + fp + fn
        denom_dice = 2.0 * tp + fp + fn

        ious[c_name] = round(tp / denom_iou, 4) if denom_iou > 0 else 1.0
        dices[c_name] = round(2.0 * tp / denom_dice, 4) if denom_dice > 0 else 1.0

    all_ious = list(ious.values())
    all_dices = list(dices.values())
    miou = float(np.mean(all_ious))
    mdice = float(np.mean(all_dices))

    # Foreground defect IoU (excluding background class 0)
    fg_ious = [ious[k] for k in list(ious.keys())[1:]] if num_classes > 1 else all_ious
    fg_iou = float(np.mean(fg_ious)) if fg_ious else miou

    return {
        "pixel_accuracy": round(pixel_acc, 4),
        "miou": round(miou, 4),
        "mdice": round(mdice, 4),
        "foreground_iou": round(fg_iou, 4),
        "per_class_iou": ious,
        "per_class_dice": dices,
        "confusion_matrix": cm.tolist(),
    }
