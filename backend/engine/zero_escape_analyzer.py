"""
backend/engine/zero_escape_analyzer.py

Authentic Zero-Escape (Zero-Underkill / 미검 제로화) Analyzer & Threshold Optimizer.
Calibrates production inspection thresholds using genuine model evaluation predictions:
  - Formulates defect_score in [0.0, 1.0] across all 4 vision tasks (Classification, Detection, Segmentation, Anomaly).
  - Guarantees 0% Escape Rate (Zero Underkill):
      tau* = min(defect_scores of true defects) - 1e-4
  - Generates comprehensive trade-off curves (Overkill vs Underkill, F1, Cost).
  - Provides per-sample audit status: CORRECT_OK, CORRECT_NG, OVERKILL, ESCAPE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger("vision_ai_studio.zero_escape_analyzer")

def is_defect_label(label: Any, roles: Optional[Dict[str, str]] = None) -> bool:
    """Returns True unless the label names a normal class, using recorded roles first."""
    if label is None:
        return False
    if isinstance(label, (int, float)):
        return label != 0
    from backend.engine.class_semantics import is_defect_class
    return is_defect_class(label, roles)


def compute_sample_defect_score(
    prediction: Dict[str, Any],
    task: str = "classification",
    anomaly_min: Optional[float] = None,
    anomaly_max: Optional[float] = None,
    roles: Optional[Dict[str, str]] = None,
) -> float:
    """
    Computes genuine defect_score in [0.0, 1.0]:
      - Classification:
          if predicted as NG: conf
          if predicted as OK: 1.0 - conf
          (If a true defect is misclassified as OK with 0.95 conf, score is 0.05 -> ESCAPE)
      - Anomaly: min-max normalized image-level anomaly score
      - Detection: max detection confidence of defect flaws (or 0 if none)
      - Segmentation: max defect mask probability / coverage ratio
    """
    # 1. If explicit defect_score already provided, respect it
    if "defect_score" in prediction and prediction["defect_score"] is not None:
        try:
            return float(np.clip(float(prediction["defect_score"]), 0.0, 1.0))
        except (ValueError, TypeError):
            pass

    task_clean = str(task).lower().strip()

    # 2. Classification
    if task_clean == "classification":
        pred_cls = str(prediction.get("predicted_class", "")).strip()
        conf = float(prediction.get("confidence", 0.5))
        is_pred_defect = is_defect_label(pred_cls, roles)
        if is_pred_defect:
            score = conf
        else:
            score = 1.0 - conf
        return float(np.clip(score, 0.0, 1.0))

    # 3. Anomaly Detection
    elif task_clean in ("anomaly", "anomaly_detection"):
        raw_score = float(prediction.get("confidence", prediction.get("anomaly_score", 0.0)))
        if anomaly_min is not None and anomaly_max is not None and anomaly_max > anomaly_min:
            norm_score = (raw_score - anomaly_min) / (anomaly_max - anomaly_min + 1e-7)
        elif raw_score > 1.0:
            # Heuristic normalization if max wasn't provided but score > 1.0
            norm_score = raw_score / (raw_score + 1.0)
        else:
            norm_score = raw_score
        return float(np.clip(norm_score, 0.0, 1.0))

    # 4. Object Detection
    elif task_clean == "detection":
        detections = prediction.get("detections") or prediction.get("predictions")
        if isinstance(detections, list) and detections:
            defect_scores = [
                float(d.get("score", 0.0))
                for d in detections
                if isinstance(d, dict) and is_defect_label(d.get("label", ""), roles)
            ]
            score = max(defect_scores, default=0.0)
        else:
            pred_cls = str(prediction.get("predicted_class", "")).strip()
            conf = float(prediction.get("confidence", 0.0))
            score = conf if is_defect_label(pred_cls, roles) else 0.0
        return float(np.clip(score, 0.0, 1.0))

    # 5. Semantic Segmentation
    elif task_clean == "segmentation":
        pred_cls = str(prediction.get("predicted_class", "")).strip()
        conf = float(prediction.get("confidence", 0.5))
        coverage = float(prediction.get("mask_coverage", prediction.get("mask_coverage_percent", 0.0)))
        if coverage > 1.0:
            coverage = coverage / 100.0  # normalize percent to [0, 1]

        if is_defect_label(pred_cls, roles) or coverage > 0.001:
            score = max(conf, min(1.0, coverage * 5.0))
        else:
            score = 1.0 - conf
        return float(np.clip(score, 0.0, 1.0))

    # Fallback
    pred_cls = str(prediction.get("predicted_class", "")).strip()
    conf = float(prediction.get("confidence", 0.5))
    score = conf if is_defect_label(pred_cls, roles) else (1.0 - conf)
    return float(np.clip(score, 0.0, 1.0))


def compute_sample_status(is_true_defect: bool, defect_score: float, threshold: float) -> str:
    """
    Evaluates sample verdict against threshold:
      - True Defect (NG):
          defect_score >= threshold -> CORRECT_NG (Caught)
          defect_score < threshold  -> ESCAPE (Underkill / 미검 - Shop-floor Critical Risk)
      - True Normal (OK):
          defect_score >= threshold -> OVERKILL (False Positive / 과검 - Yield Loss)
          defect_score < threshold  -> CORRECT_OK (Passed)
    """
    predicted_ng = (defect_score >= threshold)
    if is_true_defect:
        return "CORRECT_NG" if predicted_ng else "ESCAPE"
    else:
        return "OVERKILL" if predicted_ng else "CORRECT_OK"


def calculate_optimal_zero_underkill_threshold(
    defect_scores_of_true_ngs: List[float],
    epsilon: float = 1e-4,
    default_threshold: float = 0.5,
) -> float:
    """
    Calculates optimal zero-underkill threshold tau*:
      tau* = min(defect_scores of true NGs) - epsilon
    guaranteeing underkill_count == 0.
    """
    if not defect_scores_of_true_ngs:
        return default_threshold

    min_defect_score = float(min(defect_scores_of_true_ngs))
    tau_star = min_defect_score - epsilon
    return float(max(0.0, min(1.0, tau_star)))


def analyze_zero_escape(
    predictions: List[Dict[str, Any]],
    task: str = "classification",
    target_max_underkill: int = 0,
    cost_escape: float = 500.0,
    cost_scrap: float = 25.0,
    current_threshold: float = 0.5,
    num_threshold_steps: int = 101,
    class_roles: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Performs comprehensive zero-escape optimization and tradeoff analysis.
    """
    if not predictions:
        return {
            "status": "empty",
            "message": "No test predictions provided for analysis",
            "sample_count": 0,
            "total_defects": 0,
            "total_normals": 0,
            "target_max_underkill": target_max_underkill,
            "optimal_threshold": current_threshold,
            "optimal_cost_threshold": current_threshold,
            "current_stats": {
                "threshold": current_threshold,
                "underkill_count": 0,
                "underkill_rate": 0.0,
                "overkill_count": 0,
                "overkill_rate": 0.0,
                "total_cost": 0.0,
            },
            "optimal_stats": {
                "threshold": current_threshold,
                "underkill_count": 0,
                "underkill_rate": 0.0,
                "overkill_count": 0,
                "overkill_rate": 0.0,
                "total_cost": 0.0,
            },
            "tradeoff_curve": [],
            "sample_details": [],
        }

    # A missing/review truth is neither OK nor NG. Use the history contract so
    # calibration cannot turn unreviewed samples into threshold evidence.
    from backend.engine.evaluation_history import binary_verdict
    known_truth = []
    for idx, prediction in enumerate(predictions):
        verdict = binary_verdict(prediction.get("ground_truth"), class_roles)
        if verdict is None:
            raise ValueError(f"Prediction {idx} has unknown ground truth; zero-escape analysis needs reviewed OK/NG truth")
        known_truth.append(verdict == "NG")

    # Pre-scan anomaly bounds if anomaly task
    anomaly_min, anomaly_max = None, None
    if str(task).lower().strip() in ("anomaly", "anomaly_detection"):
        raw_scores = [
            float(p.get("confidence", p.get("anomaly_score", 0.0)))
            for p in predictions
            if "confidence" in p or "anomaly_score" in p
        ]
        if raw_scores:
            anomaly_min = min(raw_scores)
            anomaly_max = max(raw_scores)

    # 1. Standardize samples with authentic defect scores
    samples: List[Dict[str, Any]] = []
    defect_scores_of_ngs: List[float] = []

    for idx, p in enumerate(predictions):
        gt = p.get("ground_truth", "")
        is_def = known_truth[idx]
        score = compute_sample_defect_score(
            p, task=task, anomaly_min=anomaly_min, anomaly_max=anomaly_max, roles=class_roles
        )
        img_id = str(p.get("image_id", p.get("file_name", f"sample_{idx:04d}")))
        file_name = str(p.get("file_name", img_id))

        if is_def:
            defect_scores_of_ngs.append(score)

        samples.append({
            "image_id": img_id,
            "file_name": file_name,
            "file_path": p.get("file_path", ""),
            "ground_truth": str(gt),
            "predicted_class": str(p.get("predicted_class", "")),
            "is_defect": is_def,
            "defect_score": score,
            "raw_prediction": p,
        })

    total_defects = len(defect_scores_of_ngs)
    total_normals = len(samples) - total_defects

    # 2. Compute tau* (optimal zero-underkill threshold)
    tau_star = calculate_optimal_zero_underkill_threshold(
        defect_scores_of_ngs,
        epsilon=1e-4,
        default_threshold=current_threshold,
    )

    # 3. Sweep thresholds for tradeoff curve
    raw_thresholds = set(np.linspace(0.0, 1.0, max(50, min(200, num_threshold_steps))))
    raw_thresholds.add(tau_star)
    raw_thresholds.add(current_threshold)
    # Also add boundary test points around minimum defect score if available
    if defect_scores_of_ngs:
        raw_thresholds.add(max(0.0, min(defect_scores_of_ngs)))
        raw_thresholds.add(max(0.0, min(defect_scores_of_ngs) - 1e-4))
        raw_thresholds.add(min(1.0, min(defect_scores_of_ngs) + 1e-4))

    sorted_thresholds = sorted([round(float(t), 4) for t in raw_thresholds])

    curve_points: List[Dict[str, Any]] = []
    min_cost = float("inf")
    optimal_cost_thresh = current_threshold

    for th in sorted_thresholds:
        underkill = sum(1 for s in samples if s["is_defect"] and s["defect_score"] < th)
        overkill = sum(1 for s in samples if (not s["is_defect"]) and s["defect_score"] >= th)
        tp = total_defects - underkill
        tn = total_normals - overkill

        underkill_rate = (underkill / total_defects * 100.0) if total_defects > 0 else 0.0
        overkill_rate = (overkill / total_normals * 100.0) if total_normals > 0 else 0.0

        precision = (tp / (tp + overkill)) if (tp + overkill) > 0 else (1.0 if tp == 0 and overkill == 0 else 0.0)
        recall = (tp / (tp + underkill)) if (tp + underkill) > 0 else 1.0
        f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        total_cost = (underkill * cost_escape) + (overkill * cost_scrap)

        if total_cost < min_cost:
            min_cost = total_cost
            optimal_cost_thresh = th

        curve_points.append({
            "threshold": th,
            "underkill_count": underkill,
            "underkill_rate": round(underkill_rate, 2),
            "overkill_count": overkill,
            "overkill_rate": round(overkill_rate, 2),
            "tp": tp,
            "tn": tn,
            "fp": overkill,
            "fn": underkill,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "total_cost": round(total_cost, 2),
        })

    # 4. Resolve best point for target_max_underkill (Zero Underkill)
    eligible = [p for p in curve_points if p["underkill_count"] <= target_max_underkill]
    if target_max_underkill == 0 and defect_scores_of_ngs:
        optimal_thresh = tau_star
        matching_pts = [p for p in curve_points if abs(p["threshold"] - tau_star) < 1e-4]
        best_point = matching_pts[0] if matching_pts else min(eligible or curve_points, key=lambda x: x["underkill_count"])
    elif eligible:
        # Choose lowest overkill count; break ties with higher threshold
        best_point = min(eligible, key=lambda x: (x["overkill_count"], -x["threshold"]))
        optimal_thresh = best_point["threshold"]
    else:
        best_point = min(curve_points, key=lambda x: x["underkill_count"])
        optimal_thresh = best_point["threshold"]

    # Calculate stats for current_threshold
    cur_underkill = sum(1 for s in samples if s["is_defect"] and s["defect_score"] < current_threshold)
    cur_overkill = sum(1 for s in samples if (not s["is_defect"]) and s["defect_score"] >= current_threshold)
    cur_underkill_rate = (cur_underkill / total_defects * 100.0) if total_defects > 0 else 0.0
    cur_overkill_rate = (cur_overkill / total_normals * 100.0) if total_normals > 0 else 0.0
    cur_cost = (cur_underkill * cost_escape) + (cur_overkill * cost_scrap)

    # 5. Populate sample_details
    sample_details: List[Dict[str, Any]] = []
    for s in samples:
        status_opt = compute_sample_status(s["is_defect"], s["defect_score"], optimal_thresh)
        status_cur = compute_sample_status(s["is_defect"], s["defect_score"], current_threshold)
        sample_details.append({
            "image_id": s["image_id"],
            "file_name": s["file_name"],
            "file_path": s["file_path"],
            "true_class": s["ground_truth"],
            "pred_class": s["predicted_class"],
            "defect_score": round(s["defect_score"], 4),
            "status": status_opt,  # Status at optimal zero-underkill threshold
            "status_current": status_cur,  # Status at current threshold
            "is_defect": s["is_defect"],
        })

    return {
        "status": "success",
        "sample_count": len(samples),
        "total_defects": total_defects,
        "total_normals": total_normals,
        "target_max_underkill": target_max_underkill,
        "optimal_threshold": round(optimal_thresh, 4),
        "optimal_cost_threshold": round(optimal_cost_thresh, 4),
        "current_stats": {
            "threshold": current_threshold,
            "underkill_count": cur_underkill,
            "underkill_rate": round(cur_underkill_rate, 2),
            "overkill_count": cur_overkill,
            "overkill_rate": round(cur_overkill_rate, 2),
            "total_cost": round(cur_cost, 2),
        },
        "optimal_stats": {
            "threshold": best_point["threshold"],
            "underkill_count": best_point["underkill_count"],
            "underkill_rate": best_point["underkill_rate"],
            "overkill_count": best_point["overkill_count"],
            "overkill_rate": best_point["overkill_rate"],
            "total_cost": best_point["total_cost"],
        },
        "tradeoff_curve": curve_points,
        "sample_details": sample_details,
    }
