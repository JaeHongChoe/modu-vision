"""
backend/engine/segmentation/postprocess.py
Alias for contours.py
"""
from backend.engine.segmentation.contours import extract_polygons, polygons_to_mask

__all__ = ["extract_polygons", "polygons_to_mask"]
