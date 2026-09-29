"""
backend/engine/anomaly/__init__.py

Public API exports for the Task 4: Unsupervised Anomaly Detection Engine.
"""

from backend.engine.anomaly.feature_extractor import ResNetFeatureExtractor
from backend.engine.anomaly.padim import PaDiMDetector
from backend.engine.anomaly.patchcore import PatchCoreDetector
from backend.engine.anomaly.metrics import compute_anomaly_metrics

__all__ = [
    "ResNetFeatureExtractor",
    "PaDiMDetector",
    "PatchCoreDetector",
    "compute_anomaly_metrics",
]
