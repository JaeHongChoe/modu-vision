"""
backend/engine/labeling_ai.py

Industrial AI-Assisted Auto-Labeling Engines (inspired by Neurocle Neuro-T):
1. Auto-Selector (Smart Magic Wand / Click-to-Segment):
   Extracts high-precision object/defect contour polygons from a single seed click.
2. Shape Converter (BBox to Polygon):
   Automatically adapts a coarse bounding box into a fine-grained boundary polygon.
3. Rotated BBox (OBB) Geometry Math & Polygon conversions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

logger = logging.getLogger("vision_ai_studio.labeling_ai")


def auto_select_contour(
    image_input: Union[str, Path, np.ndarray],
    seed_x: float,
    seed_y: float,
    tolerance: int = 25,
    min_area: float = 16.0,
    epsilon_ratio: float = 0.015,
) -> Dict[str, Any]:
    """
    AI Auto-Selector (Smart Magic Wand):
    Given a seed point (seed_x, seed_y) on an image, automatically extracts the
    underlying flaw or component boundary as a simplified polygon contour.
    """
    if isinstance(image_input, (str, Path)):
        img = cv2.imread(str(image_input))
        if img is None:
            raise FileNotFoundError(f"Image not found at path: {image_input}")
    else:
        img = image_input

    h, w = img.shape[:2]
    ix = int(np.clip(round(seed_x), 0, w - 1))
    iy = int(np.clip(round(seed_y), 0, h - 1))

    # Convert to grayscale & smoothed representation
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Strategy 1: Local FloodFill mask from seed
    flood_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    tol = max(5, int(tolerance))
    cv2.floodFill(
        blurred.copy(),
        flood_mask,
        (ix, iy),
        newVal=255,
        loDiff=tol,
        upDiff=tol,
        flags=4 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY,
    )
    mask = flood_mask[1 : h + 1, 1 : w + 1]

    # If flood fill produced trivial or entire-image mask, fallback to local adaptive threshold
    mask_pixel_count = cv2.countNonZero(mask)
    if mask_pixel_count < min_area or mask_pixel_count > (h * w * 0.95):
        # Extract a local window (64x64 or 128x128) around seed
        radius = min(64, max(16, min(w, h) // 4))
        x0 = max(0, ix - radius)
        y0 = max(0, iy - radius)
        x1 = min(w, ix + radius)
        y1 = min(h, iy + radius)

        roi = gray[y0:y1, x0:x1]
        _, roi_otsu = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[y0:y1, x0:x1] = roi_otsu

    # Morphological cleanup
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    # Find external contours
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    selected_contour = None
    if contours:
        # Prefer contour containing the seed point
        for cnt in contours:
            if cv2.contourArea(cnt) >= min_area:
                if cv2.pointPolygonTest(cnt, (float(ix), float(iy)), False) >= 0:
                    selected_contour = cnt
                    break
        # If no contour strictly contains seed, pick the closest large contour
        if selected_contour is None:
            valid_contours = [c for c in contours if cv2.contourArea(c) >= min_area]
            if valid_contours:
                selected_contour = max(valid_contours, key=cv2.contourArea)

    # Fallback if still None: synthetic box around seed
    if selected_contour is None:
        r = 12
        fallback_pts = np.array(
            [[ix - r, iy - r], [ix + r, iy - r], [ix + r, iy + r], [ix - r, iy + r]],
            dtype=np.int32,
        )
        selected_contour = fallback_pts

    # Simplify contour with approxPolyDP for clean UI polygon
    perimeter = cv2.arcLength(selected_contour, True)
    epsilon = max(1.5, perimeter * epsilon_ratio)
    approx = cv2.approxPolyDP(selected_contour, epsilon, True)
    if len(approx) < 3:
        approx = cv2.convexHull(selected_contour)

    polygon = [[float(pt[0][0]), float(pt[0][1])] for pt in approx]
    bx, by, bw, bh = cv2.boundingRect(selected_contour)
    area = float(cv2.contourArea(selected_contour))

    return {
        "polygon": polygon,
        "bbox": [float(bx), float(by), float(bx + bw), float(by + bh)],
        "area": area,
        "seed_point": [float(seed_x), float(seed_y)],
    }


def shape_converter_bbox_to_polygon(
    image_input: Union[str, Path, np.ndarray],
    bbox: List[float],
    sensitivity: float = 0.5,
    min_area: float = 8.0,
) -> Dict[str, Any]:
    """
    Shape Converter (Neuro-T BBox-to-Polygon):
    Takes a coarse bounding box [xmin, ymin, xmax, ymax] and snaps it to the
    precise defect/object boundary contour inside that region.
    """
    if isinstance(image_input, (str, Path)):
        img = cv2.imread(str(image_input))
        if img is None:
            raise FileNotFoundError(f"Image not found at path: {image_input}")
    else:
        img = image_input

    h, w = img.shape[:2]
    x1, y1, x2, y2 = bbox
    x1 = int(np.clip(min(x1, x2), 0, w - 1))
    y1 = int(np.clip(min(y1, y2), 0, h - 1))
    x2 = int(np.clip(max(x1, x2), x1 + 2, w))
    y2 = int(np.clip(max(y1, y2), y1 + 2, h))

    crop = img[y1:y2, x1:x2]
    if crop.size == 0 or crop.shape[0] < 2 or crop.shape[1] < 2:
        # Fallback to rectangle
        return {
            "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            "area": float((x2 - x1) * (y2 - y1)),
        }

    gray_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    blurred = cv2.GaussianBlur(gray_crop, (3, 3), 0)

    # Use Otsu or Canny edge + morphological close
    canny_thresh2 = max(20, int(150 * (1.0 - sensitivity * 0.5)))
    canny_thresh1 = canny_thresh2 // 2
    edges = cv2.Canny(blurred, canny_thresh1, canny_thresh2)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    closed_edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

    # Also threshold for contrast
    _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    combined = cv2.bitwise_or(closed_edges, otsu)

    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        valid_contours = [c for c in contours if cv2.contourArea(c) >= min_area]
        best_contour = max(valid_contours, key=cv2.contourArea) if valid_contours else max(contours, key=cv2.contourArea)
    else:
        best_contour = None

    if best_contour is not None and len(best_contour) >= 3:
        peri = cv2.arcLength(best_contour, True)
        eps = max(1.5, peri * 0.02)
        approx = cv2.approxPolyDP(best_contour, eps, True)
        if len(approx) < 3:
            approx = cv2.convexHull(best_contour)

        # Offset coordinates back to global image space
        polygon = [[float(pt[0][0] + x1), float(pt[0][1] + y1)] for pt in approx]
        area = float(cv2.contourArea(best_contour))
    else:
        # Fallback to bbox corners
        polygon = [
            [float(x1), float(y1)],
            [float(x2), float(y1)],
            [float(x2), float(y2)],
            [float(x1), float(y2)],
        ]
        area = float((x2 - x1) * (y2 - y1))

    return {
        "polygon": polygon,
        "area": area,
        "source_bbox": [float(x1), float(y1), float(x2), float(y2)],
    }


def rotated_bbox_to_corners(
    cx: float, cy: float, width: float, height: float, angle_degrees: float
) -> List[List[float]]:
    """
    Converts rotated bounding box (center_x, center_y, width, height, angle_deg)
    to 4 clockwise corner coordinates [[x1, y1], [x2, y2], [x3, y3], [x4, y4]].
    """
    rect = ((float(cx), float(cy)), (float(width), float(height)), float(angle_degrees))
    box_pts = cv2.boxPoints(rect)  # 4x2 float32 array
    return [[float(pt[0]), float(pt[1])] for pt in box_pts]


def bbox_to_polygon(bbox: List[float]) -> List[List[float]]:
    """
    Converts bounding box [xmin, ymin, xmax, ymax] into a 4-point polygon contour:
    [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]].
    """
    if len(bbox) != 4:
        raise ValueError(f"bbox must contain 4 coordinates, got {len(bbox)}")
    x1, y1, x2, y2 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    return [
        [x1, y1],
        [x2, y1],
        [x2, y2],
        [x1, y2],
    ]


def polygon_to_bbox(points: List[List[float]]) -> List[float]:
    """
    Computes axis-aligned bounding box [xmin, ymin, xmax, ymax] enclosing polygon points.
    """
    if not points:
        return [0.0, 0.0, 0.0, 0.0]
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def mask_to_polygon(
    mask: Union[np.ndarray, List[List[int]]],
    approx_epsilon: float = 1.5,
) -> List[List[float]]:
    """
    Extracts outer contour from a binary mask via cv2.findContours and simplifies it.
    """
    m = np.asarray(mask, dtype=np.uint8)
    if m.ndim > 2:
        m = m[:, :, 0]
    m_bin = (m > 0).astype(np.uint8) * 255

    if cv2.countNonZero(m_bin) == 0:
        return []

    contours, _ = cv2.findContours(m_bin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    largest_cnt = max(contours, key=cv2.contourArea)
    if approx_epsilon > 0.0:
        approx = cv2.approxPolyDP(largest_cnt, float(approx_epsilon), True)
        if len(approx) < 3:
            approx = cv2.convexHull(largest_cnt)
    else:
        approx = largest_cnt

    return [[float(pt[0][0]), float(pt[0][1])] for pt in approx]


def polygon_to_mask(
    points: List[List[float]],
    shape: Union[Tuple[int, int], List[int]],
) -> np.ndarray:
    """
    Rasterizes discrete polygon contour into binary mask (h, w) via cv2.fillPoly.
    Values are 255 for inside, 0 for background.
    """
    h, w = int(shape[0]), int(shape[1])
    mask = np.zeros((h, w), dtype=np.uint8)
    if not points or len(points) < 3:
        return mask

    pts = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
    cv2.fillPoly(mask, [pts], 255)
    return mask


def mask_to_bbox(mask: Union[np.ndarray, List[List[int]]]) -> List[float]:
    """
    Returns axis-aligned bounding box [xmin, ymin, xmax, ymax] of non-zero pixels in mask.
    """
    m = np.asarray(mask, dtype=np.uint8)
    if m.ndim > 2:
        m = m[:, :, 0]
    m_bin = (m > 0).astype(np.uint8)

    if cv2.countNonZero(m_bin) == 0:
        return [0.0, 0.0, 0.0, 0.0]

    bx, by, bw, bh = cv2.boundingRect(m_bin)
    return [float(bx), float(by), float(bx + bw), float(by + bh)]


def bbox_to_mask(
    bbox: List[float],
    shape: Union[Tuple[int, int], List[int]],
) -> np.ndarray:
    """
    Creates a rectangular binary mask of given shape (h, w) from bounding box [xmin, ymin, xmax, ymax].
    """
    h, w = int(shape[0]), int(shape[1])
    mask = np.zeros((h, w), dtype=np.uint8)
    if len(bbox) != 4:
        return mask

    x1 = int(np.clip(round(bbox[0]), 0, w))
    y1 = int(np.clip(round(bbox[1]), 0, h))
    x2 = int(np.clip(round(bbox[2]), 0, w))
    y2 = int(np.clip(round(bbox[3]), 0, h))

    xmin, xmax = min(x1, x2), max(x1, x2)
    ymin, ymax = min(y1, y2), max(y1, y2)

    mask[ymin:ymax, xmin:xmax] = 255
    return mask


def polygon_to_rotated_bbox(points: List[List[float]]) -> Dict[str, Any]:
    """
    Computes the minimum area rotated rectangle enclosing polygon points via cv2.minAreaRect.
    Returns {center: [cx, cy], size: [w, h], angle: deg}.
    """
    if not points:
        return {"center": [0.0, 0.0], "size": [0.0, 0.0], "angle": 0.0}

    pts = np.array(points, dtype=np.float32).reshape((-1, 1, 2))
    if len(points) < 3:
        # Fallback for fewer than 3 points
        if len(points) == 1:
            return {"center": [float(points[0][0]), float(points[0][1])], "size": [1.0, 1.0], "angle": 0.0}
        p0, p1 = points[0], points[1]
        cx = (p0[0] + p1[0]) / 2.0
        cy = (p0[1] + p1[1]) / 2.0
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        dist = float(np.hypot(dx, dy))
        angle = float(np.degrees(np.arctan2(dy, dx)))
        return {"center": [float(cx), float(cy)], "size": [dist, 1.0], "angle": angle}

    (cx, cy), (w, h), angle = cv2.minAreaRect(pts)
    return {
        "center": [float(cx), float(cy)],
        "size": [float(w), float(h)],
        "angle": float(angle),
    }


def mask_to_rotated_bbox(mask: Union[np.ndarray, List[List[int]]]) -> Dict[str, Any]:
    """
    Computes minimum area rotated rectangle on mask contours via cv2.minAreaRect.
    """
    m = np.asarray(mask, dtype=np.uint8)
    if m.ndim > 2:
        m = m[:, :, 0]
    m_bin = (m > 0).astype(np.uint8)

    if cv2.countNonZero(m_bin) == 0:
        return {"center": [0.0, 0.0], "size": [0.0, 0.0], "angle": 0.0}

    contours, _ = cv2.findContours(m_bin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {"center": [0.0, 0.0], "size": [0.0, 0.0], "angle": 0.0}

    all_pts = np.vstack(contours)
    (cx, cy), (w, h), angle = cv2.minAreaRect(all_pts)
    return {
        "center": [float(cx), float(cy)],
        "size": [float(w), float(h)],
        "angle": float(angle),
    }


def bbox_to_rotated_bbox(bbox: List[float]) -> Dict[str, Any]:
    """
    Converts axis-aligned bounding box [xmin, ymin, xmax, ymax] to angle=0 rotated bbox:
    {center: [cx, cy], size: [w, h], angle: 0.0}.
    """
    if len(bbox) != 4:
        raise ValueError(f"bbox must contain 4 coordinates, got {len(bbox)}")
    x1, y1, x2, y2 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    xmin, xmax = min(x1, x2), max(x1, x2)
    ymin, ymax = min(y1, y2), max(y1, y2)
    cx = (xmin + xmax) / 2.0
    cy = (ymin + ymax) / 2.0
    w = xmax - xmin
    h = ymax - ymin
    return {
        "center": [float(cx), float(cy)],
        "size": [float(w), float(h)],
        "angle": 0.0,
    }


def rotated_bbox_to_polygon(
    center: List[float],
    size: List[float],
    angle: float,
) -> List[List[float]]:
    """
    Computes 4 rotated corner coordinates [[x1, y1], [x2, y2], [x3, y3], [x4, y4]].
    """
    return rotated_bbox_to_corners(center[0], center[1], size[0], size[1], angle)


def rotated_bbox_to_bbox(
    center: List[float],
    size: List[float],
    angle: float,
) -> List[float]:
    """
    Computes axis-aligned bounding box [xmin, ymin, xmax, ymax] enclosing the rotated rectangle.
    """
    corners = rotated_bbox_to_polygon(center, size, angle)
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]

