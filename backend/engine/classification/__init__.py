"""
backend/engine/classification/__init__.py

Public API exports for the Task 1: Classification Engine.
"""

from backend.engine.classification.model import (
    create_classification_model,
    build_classification_model,
)
from backend.engine.classification.loss import (
    compute_class_weights,
    create_classification_loss,
)
from backend.engine.classification.gradcam import GradCAM
from backend.engine.classification.metrics import compute_classification_metrics

__all__ = [
    "create_classification_model",
    "build_classification_model",
    "compute_class_weights",
    "create_classification_loss",
    "GradCAM",
    "compute_classification_metrics",
]
