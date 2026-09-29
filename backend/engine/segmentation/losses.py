"""
backend/engine/segmentation/losses.py
Alias for loss.py
"""
from backend.engine.segmentation.loss import SoftDiceLoss, FocalLoss, ComboLoss

__all__ = ["SoftDiceLoss", "FocalLoss", "ComboLoss"]
