"""
backend/engine/classification/metrics.py

Multi-class classification evaluation metrics:
Accuracy, Precision, Recall, Macro-F1, and Confusion Matrix.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
import numpy as np
import torch


def compute_classification_metrics(
    predictions: Union[np.ndarray, torch.Tensor, List[int]],
    targets: Union[np.ndarray, torch.Tensor, List[int]],
    num_classes: Optional[int] = None,
    class_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Computes comprehensive multi-class evaluation metrics matching REST API contract:
      - overall accuracy
      - macro precision, recall, f1
      - per-class precision, recall, f1, and support count
      - raw and normalized confusion matrices

    Args:
        predictions: Predicted class indices
        targets: Ground truth class indices
        num_classes: Optional total number of classes (inferred if None)
        class_names: Optional class label names
    """
    if isinstance(predictions, torch.Tensor):
        preds = predictions.detach().cpu().numpy().reshape(-1)
    else:
        preds = np.asarray(predictions).reshape(-1)

    if isinstance(targets, torch.Tensor):
        targs = targets.detach().cpu().numpy().reshape(-1)
    else:
        targs = np.asarray(targets).reshape(-1)

    if num_classes is None:
        max_idx = max(int(np.max(preds)) if len(preds) > 0 else 0, int(np.max(targs)) if len(targs) > 0 else 0)
        num_classes = max_idx + 1

    if class_names is None:
        class_names = [f"class_{i}" for i in range(num_classes)]

    cm = np.zeros((num_classes, num_classes), dtype=np.int64)
    for p, t in zip(preds, targs):
        if 0 <= t < num_classes and 0 <= p < num_classes:
            cm[t, p] += 1

    total_samples = int(np.sum(cm))
    correct = int(np.trace(cm))
    accuracy = float(correct / total_samples) if total_samples > 0 else 0.0

    per_class: Dict[str, Dict[str, float]] = {}
    precisions: List[float] = []
    recalls: List[float] = []
    f1s: List[float] = []

    for c in range(num_classes):
        c_name = class_names[c] if c < len(class_names) else f"class_{c}"
        tp = float(cm[c, c])
        fp = float(np.sum(cm[:, c]) - tp)
        fn = float(np.sum(cm[c, :]) - tp)
        support = float(np.sum(cm[c, :]))

        p_denom = tp + fp
        prec = float(tp / p_denom) if p_denom > 0 else 0.0

        r_denom = tp + fn
        rec = float(tp / r_denom) if r_denom > 0 else 0.0

        f_denom = prec + rec
        f1 = float(2.0 * prec * rec / f_denom) if f_denom > 0 else 0.0

        per_class[c_name] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "support": int(support),
        }
        precisions.append(prec)
        recalls.append(rec)
        f1s.append(f1)

    macro_precision = float(np.mean(precisions)) if precisions else 0.0
    macro_recall = float(np.mean(recalls)) if recalls else 0.0
    macro_f1 = float(np.mean(f1s)) if f1s else 0.0

    # Normalized confusion matrix (row-normalized by ground truth)
    row_sums = cm.sum(axis=1, keepdims=True)
    norm_cm = np.where(row_sums > 0, cm.astype(float) / np.maximum(1, row_sums), 0.0)

    return {
        "accuracy": round(accuracy, 4),
        "macro_precision": round(macro_precision, 4),
        "macro_recall": round(macro_recall, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion_matrix": {
            "matrix": cm.tolist(),
            "normalized_matrix": [[round(x, 4) for x in row] for row in norm_cm.tolist()],
            "class_names": class_names,
        },
    }
