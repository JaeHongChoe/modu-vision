"""
backend/engine/detection/evaluator.py
Evaluation module alias for mAP calculation.
"""
from backend.engine.detection.metrics import compute_ap_coco, evaluate_detections_map

__all__ = ["compute_ap_coco", "evaluate_detections_map"]
