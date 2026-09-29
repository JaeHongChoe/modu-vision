"""
backend/engine/segmentation/contours.py

Raster Defect Mask to Vector Polygon Coordinates & Roundtrip Reconstruction.
Formats polygons for the Electron/React 3-layer interactive canvas.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union
import cv2
import numpy as np


def extract_polygons(
    mask: np.ndarray,
    class_names: Optional[Dict[int, str]] = None,
    min_area: float = 4.0,
    approx_epsilon: float = 0.005,
    include_holes: bool = False,
) -> List[Dict[str, Any]]:
    """
    Extracts vector polygon contours from a 2D integer raster segmentation mask.

    Args:
        mask: 2D numpy array (H, W) of integer class labels (0=bg, 1..K=defects)
        class_names: Dict mapping class_id -> class_name
        min_area: Minimum pixel area threshold to filter spurious 1-pixel noise
        approx_epsilon: Douglas-Peucker simplification tolerance factor (fraction of perimeter)
        include_holes: If True, uses cv2.RETR_CCOMP to extract both external boundaries and interior holes

    Returns:
        List of polygon dictionaries formatted for React Canvas:
        [{
            "class_id": int,
            "class_name": str,
            "is_hole": bool,
            "area": float,
            "bbox": [xmin, ymin, xmax, ymax],
            "bbox_normalized": [xmin_n, ymin_n, xmax_n, ymax_n],
            "points": [[x0, y0], [x1, y1], ...],
            "points_normalized": [[x0_n, y0_n], ...],
            "num_points": int
        }]
    """
    if class_names is None:
        class_names = {}

    h, w = mask.shape[:2]
    unique_classes = [int(c) for c in np.unique(mask) if c > 0]
    polygons: List[Dict[str, Any]] = []

    for c in unique_classes:
        bin_mask = (mask == c).astype(np.uint8) * 255
        if include_holes:
            contours, hierarchy = cv2.findContours(bin_mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
            hier = hierarchy[0] if hierarchy is not None and len(hierarchy) > 0 else None
        else:
            contours, _ = cv2.findContours(bin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            hier = None

        for idx, cnt in enumerate(contours):
            area = float(cv2.contourArea(cnt))
            if area < min_area:
                continue

            peri = cv2.arcLength(cnt, closed=True)
            eps = approx_epsilon * peri if approx_epsilon > 0 else 0.0
            approx = cv2.approxPolyDP(cnt, eps, closed=True) if eps > 0 else cnt

            pts_px = [[int(pt[0][0]), int(pt[0][1])] for pt in approx]
            if len(pts_px) < 3:
                continue

            is_hole = bool(hier[idx][3] != -1) if hier is not None else False
            poly_cid = 0 if is_hole else c
            poly_cname = "background" if is_hole else class_names.get(c, f"defect_{c}")

            pts_norm = [[round(p[0] / max(1, w), 5), round(p[1] / max(1, h), 5)] for p in pts_px]
            bx, by, bw, bh = cv2.boundingRect(cnt)

            polygons.append({
                "class_id": poly_cid,
                "class_name": poly_cname,
                "is_hole": is_hole,
                "area": area,
                "bbox": [int(bx), int(by), int(bx + bw), int(by + bh)],
                "bbox_normalized": [
                    round(bx / max(1, w), 5),
                    round(by / max(1, h), 5),
                    round((bx + bw) / max(1, w), 5),
                    round((by + bh) / max(1, h), 5),
                ],
                "points": pts_px,
                "points_normalized": pts_norm,
                "num_points": len(pts_px),
            })

    return polygons


def polygons_to_mask(
    polygons: List[Dict[str, Any]],
    height: int,
    width: int,
) -> np.ndarray:
    """
    Rasterizes vector polygon definitions back into a 2D integer segmentation mask.
    Used when user edits polygon vertices on the React canvas and saves.
    Handles outer boundaries and punched interior holes cleanly.
    """
    mask = np.zeros((height, width), dtype=np.uint8)
    # First rasterize outer boundaries (non-holes)
    for poly in polygons:
        if not poly.get("is_hole", False) and int(poly.get("class_id", 1)) != 0:
            cid = int(poly.get("class_id", 1))
            pts = np.array(poly["points"], dtype=np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(mask, [pts], cid)
    # Next punch out holes with value 0
    for poly in polygons:
        if poly.get("is_hole", False) or int(poly.get("class_id", 1)) == 0:
            pts = np.array(poly["points"], dtype=np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(mask, [pts], 0)
    return mask
