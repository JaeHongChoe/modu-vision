"""
backend/engine/anomaly/metrics.py

Evaluation Metrics for Unsupervised Anomaly Detection:
Image-level AUROC, Pixel-level AUROC, F1-Score, and Optimal Decision Threshold search.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
from sklearn.metrics import confusion_matrix, f1_score, precision_recall_curve, roc_auc_score


def compute_anomaly_metrics(
    image_scores: List[float],
    image_labels: List[int],
    pixel_heatmaps: Optional[List[np.ndarray]] = None,
    pixel_masks: Optional[List[np.ndarray]] = None,
    fixed_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Computes image-level and pixel-level anomaly detection metrics.

    Args:
        image_scores: Continuous anomaly scores per test image
        image_labels: Binary ground truth (0=good, 1=defect)
        pixel_heatmaps: Optional list of 2D continuous anomaly heatmaps
        pixel_masks: Optional list of 2D binary defect ground truth masks
        fixed_threshold: Optional manual threshold override
    """
    scores_arr = np.asarray(image_scores, dtype=np.float64)
    labels_arr = np.asarray(image_labels, dtype=np.int64)

    # 1. Image-level AUROC
    if len(np.unique(labels_arr)) > 1:
        image_auroc = float(roc_auc_score(labels_arr, scores_arr))
        # Search optimal threshold via Precision-Recall curve max-F1
        prec, rec, ths = precision_recall_curve(labels_arr, scores_arr)
        f1s = (2.0 * prec * rec) / (prec + rec + 1e-8)
        best_idx = int(np.argmax(f1s))
        optimal_th = float(ths[best_idx]) if (len(ths) > 0 and best_idx < len(ths)) else (float(ths[-1]) if len(ths) > 0 else 0.5)
    else:
        image_auroc = 1.0
        optimal_th = float(np.mean(scores_arr) + 3.0 * np.std(scores_arr)) if len(scores_arr) > 0 else 0.5

    chosen_th = fixed_threshold if fixed_threshold is not None else optimal_th
    preds = (scores_arr >= chosen_th).astype(np.int64)

    cm = confusion_matrix(labels_arr, preds, labels=[0, 1]).tolist() if len(labels_arr) > 0 else [[0, 0], [0, 0]]
    f1 = float(f1_score(labels_arr, preds, zero_division=0)) if len(labels_arr) > 0 else 0.0

    # 2. Pixel-level AUROC
    pixel_auroc = None
    if pixel_heatmaps is not None and pixel_masks is not None and len(pixel_heatmaps) > 0:
        flat_maps = np.concatenate([m.reshape(-1) for m in pixel_heatmaps])
        flat_masks = np.concatenate([m.reshape(-1) for m in pixel_masks])
        bin_masks = (flat_masks > 0).astype(np.int64)
        if len(np.unique(bin_masks)) > 1:
            pixel_auroc = float(roc_auc_score(bin_masks, flat_maps))

    return {
        "image_auroc": round(image_auroc, 4),
        "pixel_auroc": round(pixel_auroc, 4) if pixel_auroc is not None else None,
        "optimal_threshold": round(optimal_th, 4),
        "active_threshold": round(chosen_th, 4),
        "f1_score": round(f1, 4),
        "confusion_matrix": cm,
    }
