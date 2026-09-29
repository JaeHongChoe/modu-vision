"""
backend/engine/segmentation/__init__.py

Public API exports for the Task 3: Semantic Segmentation Engine.
"""

from backend.engine.segmentation.model import (
    DoubleConv,
    UNet,
    DeepLabV3Wrapper,
    build_segmentation_model,
    create_segmentation_model,
)
from backend.engine.segmentation.loss import (
    SoftDiceLoss,
    FocalLoss,
    ComboLoss,
)
from backend.engine.segmentation.contours import (
    extract_polygons,
    polygons_to_mask,
)
from backend.engine.segmentation.metrics import (
    compute_segmentation_metrics,
)

__all__ = [
    "DoubleConv",
    "UNet",
    "DeepLabV3Wrapper",
    "build_segmentation_model",
    "create_segmentation_model",
    "SoftDiceLoss",
    "FocalLoss",
    "ComboLoss",
    "extract_polygons",
    "polygons_to_mask",
    "compute_segmentation_metrics",
]
