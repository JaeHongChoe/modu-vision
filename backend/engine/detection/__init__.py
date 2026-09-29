"""
backend/engine/detection/__init__.py

Public API exports for the Task 2: Object Detection Engine.
"""

from backend.engine.detection.model import (
    create_detection_model,
    build_detection_model,
    checkpoint_detection_num_classes,
    foreground_class_names,
)
from backend.engine.detection.metrics import (
    PALETTE,
    safe_nms,
    compute_ap_coco,
    evaluate_detections_map,
    draw_detection_overlays,
)

__all__ = [
    "create_detection_model",
    "build_detection_model",
    "checkpoint_detection_num_classes",
    "foreground_class_names",
    "PALETTE",
    "safe_nms",
    "compute_ap_coco",
    "evaluate_detections_map",
    "draw_detection_overlays",
]
