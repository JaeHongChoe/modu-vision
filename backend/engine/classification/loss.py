"""
backend/engine/classification/loss.py

Class-weighted Cross-Entropy loss with label smoothing for severe industrial class imbalance.
"""

from __future__ import annotations

from typing import Dict, List, Union
import torch
import torch.nn as nn


def compute_class_weights(
    class_counts: Union[List[int], Dict[int, int], Dict[str, int]],
    num_classes: int,
    min_weight: float = 0.1,
    max_weight: float = 10.0,
) -> torch.Tensor:
    """
    Computes inverse frequency class weights to counteract manufacturing class imbalance
    (e.g. 100:1 normal to defect ratio). Weights are clamped and normalized to mean 1.0.

    Args:
        class_counts: Sample count per class as list or dict
        num_classes: Total number of classes
        min_weight: Minimum weight floor to prevent zeroing gradients
        max_weight: Maximum weight cap to prevent exploding gradients
    """
    if isinstance(class_counts, dict):
        if all(isinstance(k, int) for k in class_counts.keys()):
            cnt_arr = [class_counts.get(i, 0) for i in range(num_classes)]
        else:
            cnt_arr = list(class_counts.values())
    else:
        cnt_arr = list(class_counts)

    total = sum(cnt_arr)
    weights = []
    for c in cnt_arr:
        if c > 0:
            w = total / (num_classes * c)
        else:
            w = 1.0
        weights.append(min(max_weight, max(min_weight, float(w))))

    w_tensor = torch.tensor(weights, dtype=torch.float32)
    mean_w = w_tensor.mean()
    if mean_w > 0:
        return w_tensor / mean_w
    return w_tensor


def create_classification_loss(
    weights: torch.Tensor | None = None,
    label_smoothing: float = 0.1,
) -> nn.CrossEntropyLoss:
    """
    Factory creating CrossEntropyLoss configured with class weights and label smoothing.
    """
    return nn.CrossEntropyLoss(weight=weights, label_smoothing=label_smoothing)
