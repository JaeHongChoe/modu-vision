"""
backend/engine/segmentation/models.py
Alias for model.py
"""
from backend.engine.segmentation.model import (
    DoubleConv,
    UNet,
    DeepLabV3Wrapper,
    build_segmentation_model,
    create_segmentation_model,
)

__all__ = [
    "DoubleConv",
    "UNet",
    "DeepLabV3Wrapper",
    "build_segmentation_model",
    "create_segmentation_model",
]
