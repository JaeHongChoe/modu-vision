"""
backend/api/routes_annotation.py

Interactive Canvas Annotation Persistence, BBox Sanitization & Polygon-to-Mask Rasterization.
"""

from __future__ import annotations

import json
import logging
import base64
import binascii
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.engine.dataset_loaders import BoundingBox
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.labeling_ai import (
    auto_select_contour,
    bbox_to_mask,
    bbox_to_polygon,
    bbox_to_rotated_bbox,
    mask_to_bbox,
    mask_to_polygon,
    mask_to_rotated_bbox,
    polygon_to_bbox,
    polygon_to_mask,
    polygon_to_rotated_bbox,
    rotated_bbox_to_bbox,
    rotated_bbox_to_corners,
    rotated_bbox_to_polygon,
    shape_converter_bbox_to_polygon,
)
from backend.engine.segmentation.contours import polygons_to_mask
from backend.utils.error_catalog import format_error_response

logger = logging.getLogger("vision_ai_studio.routes_annotation")

router = APIRouter(prefix="/api/annotations", tags=["annotations"])

ANNOTATIONS_DIR = Path("./annotations")
ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
MASKS_DIR = Path("./annotations/masks")
MASKS_DIR.mkdir(parents=True, exist_ok=True)


def _validate_image_id(image_id: str) -> None:
    if (not image_id.strip() or image_id in {".", ".."}
            or Path(image_id).name != image_id or "/" in image_id or "\\" in image_id
            or any(ord(character) < 32 for character in image_id)):
        raise HTTPException(status_code=422, detail="image_id must be a single image filename stem")


def _trusted_annotation_directory(requested: str) -> Path:
    """Allow explicit output folders only below the app root or configured roots."""
    target = Path(requested).expanduser().resolve()
    roots = [ANNOTATIONS_DIR.resolve()]
    roots.extend(
        Path(value).expanduser().resolve()
        for value in os.environ.get("VISION_AI_STUDIO_ANNOTATION_ROOTS", "").split(os.pathsep)
        if value.strip()
    )
    if not any(target == root or root in target.parents for root in roots):
        raise HTTPException(status_code=403, detail="Annotation folder is outside approved roots")
    return target


class AnnotationItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    type: Literal["tag", "bbox", "rotated_bbox", "polygon", "brush_mask"] = "bbox"
    label: str = Field(..., min_length=1)
    category_id: Optional[int] = 1
    bbox: Optional[List[float]] = None  # [xmin, ymin, xmax, ymax]
    rotated_bbox: Optional[List[float]] = None  # [cx, cy, width, height, angle_degrees]
    polygon: Optional[List[List[float]]] = None  # [[x, y], ...]
    points: Optional[List[List[float]]] = None  # Alias for polygon
    mask_rle: Optional[str] = None
    is_normal: Optional[bool] = None
    color: Optional[str] = None

    @field_validator("bbox")
    @classmethod
    def validate_and_sanitize_bbox(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is not None:
            if len(v) != 4:
                raise ValueError("BBox must contain exactly 4 coordinates [xmin, ymin, xmax, ymax]")
            x1, y1, x2, y2 = v
            # Auto-swap inverted coordinates
            if x2 < x1:
                x1, x2 = x2, x1
            if y2 < y1:
                y1, y2 = y2, y1
            if (x2 - x1) <= 0.0 or (y2 - y1) <= 0.0:
                raise ValueError("BBox width and height must be strictly greater than 0")
            return [x1, y1, x2, y2]
        return v


class AnnotationSaveRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    image_id: str = Field(..., min_length=1)
    image_path: Optional[str] = None
    annotations: List[AnnotationItem] = Field(default_factory=list)
    image_width: Optional[int] = 256
    image_height: Optional[int] = 256
    output_dir: Optional[str] = None


class AnnotationBatchSaveRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: List[AnnotationSaveRequest]


@router.post("/save")
def save_annotations(req: AnnotationSaveRequest):
    """
    Saves canvas annotations for an image.
    Automatically:
      1. Sanitizes bounding box coordinates with coordinate swapping and boundary clamping.
      2. Rasterizes vector polygon contours to binary/multiclass mask PNG for segmentation.
    """
    _validate_image_id(req.image_id)
    if req.image_path and Path(req.image_path).stem != req.image_id:
        raise HTTPException(status_code=422, detail="image_id must match image_path filename")
    target_dir = (_trusted_annotation_directory(req.output_dir) if req.output_dir else
                  dataset_annotation_dir(Path(req.image_path).parent, ANNOTATIONS_DIR)
                  if req.image_path else ANNOTATIONS_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    masks_dir = target_dir / "masks"
    masks_dir.mkdir(parents=True, exist_ok=True)

    img_w = req.image_width or 256
    img_h = req.image_height or 256

    sanitized_items = []
    polygons_for_mask = []
    brush_masks = []

    for item in req.annotations:
        item_dict = item.model_dump()
        # Sanitize bbox if present
        if item.bbox is not None:
            try:
                bb = BoundingBox(
                    xmin=item.bbox[0],
                    ymin=item.bbox[1],
                    xmax=item.bbox[2],
                    ymax=item.bbox[3],
                    category_id=item.category_id or 1,
                    category_name=item.label,
                )
                san = bb.sanitize(img_w, img_h)
                if san is None:
                    # Degenerate bbox rejected
                    continue
                item_dict["bbox"] = [san.xmin, san.ymin, san.xmax, san.ymax]
            except Exception as e:
                logger.debug("BBox sanitization failed: %s", e)

        # Sanitize rotated_bbox if present
        if item.rotated_bbox is not None and len(item.rotated_bbox) == 5:
            cx, cy, bw, bh, angle = item.rotated_bbox
            cx = float(np.clip(cx, 0, img_w))
            cy = float(np.clip(cy, 0, img_h))
            bw = float(max(2.0, min(bw, img_w * 2)))
            bh = float(max(2.0, min(bh, img_h * 2)))
            item_dict["rotated_bbox"] = [cx, cy, bw, bh, float(angle)]
            corners = rotated_bbox_to_corners(cx, cy, bw, bh, angle)
            polygons_for_mask.append({
                "class_id": item.category_id or 1,
                "class_name": item.label,
                "is_hole": False,
                "points": [[int(pt[0]), int(pt[1])] for pt in corners],
            })

        # Collect polygon points for raster mask generation
        poly_points = item.polygon or item.points
        if poly_points and len(poly_points) >= 3:
            pts_int = [[int(pt[0]), int(pt[1])] for pt in poly_points]
            polygons_for_mask.append({
                "class_id": item.category_id or 1,
                "class_name": item.label,
                "is_hole": False,
                "points": pts_int,
            })
            item_dict["polygon"] = pts_int
            item_dict["points"] = pts_int

        if item.type == "brush_mask" and item.mask_rle:
            try:
                prefix, encoded = item.mask_rle.split(",", 1)
                if prefix != "data:image/png;base64" or len(encoded) > 32_000_000:
                    raise ValueError("Brush mask must be a PNG data URL under 24 MB")
                decoded = base64.b64decode(encoded, validate=True)
                rgba = cv2.imdecode(np.frombuffer(decoded, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
                if rgba is None or rgba.shape[:2] != (img_h, img_w) or rgba.ndim != 3 or rgba.shape[2] != 4:
                    raise ValueError("Brush mask dimensions or alpha channel are invalid")
                brush_masks.append((rgba[:, :, 3] > 0, item.category_id or 1))
            except (ValueError, binascii.Error, cv2.error) as exc:
                raise HTTPException(status_code=422, detail=f"Invalid brush mask: {exc}") from exc

        sanitized_items.append(item_dict)

    # If polygon contours provided, auto-rasterize mask PNG
    mask_file_path = None
    if polygons_for_mask or brush_masks:
        try:
            mask_arr = polygons_to_mask(polygons_for_mask, height=img_h, width=img_w) if polygons_for_mask else np.zeros((img_h, img_w), dtype=np.uint8)
            for brush_pixels, category_id in brush_masks:
                mask_arr[brush_pixels] = category_id
            mask_file = masks_dir / f"{req.image_id}.png"
            if not cv2.imwrite(str(mask_file), mask_arr):
                raise OSError(f"Could not write annotation mask: {mask_file}")
            mask_file_path = str(mask_file)
        except Exception as e:
            logger.exception("Failed to rasterize polygon mask: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to save annotation mask: {e}") from e

    # Persist JSON file
    json_path = target_dir / f"{req.image_id}.json"
    data = {
        "image_id": req.image_id,
        "annotations": sanitized_items,
        "image_width": img_w,
        "image_height": img_h,
        "mask_file": mask_file_path,
    }

    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=format_error_response("ERR_UNKNOWN", details=f"Failed to write annotations: {e}"),
        )

    return {
        "status": "saved",
        "image_id": req.image_id,
        "count": len(sanitized_items),
        "mask_generated": mask_file_path is not None,
    }


@router.post("/batch_save")
def batch_save_annotations(req: AnnotationBatchSaveRequest):
    """Batch persists annotations across multiple images."""
    results = []
    for item in req.items:
        res = save_annotations(item)
        results.append(res)
    return {
        "status": "saved",
        "saved_images_count": len(results),
        "results": results,
    }


@router.get("/{image_id}")
def get_annotations(
    image_id: str,
    dir_path: Optional[str] = Query(None),
    file_path: Optional[str] = Query(None),
):
    """Retrieves stored annotations for the requested image_id, with seamless LabelMe format conversion."""
    _validate_image_id(image_id)
    target_dir = (_trusted_annotation_directory(str(dir_path)) if dir_path and isinstance(dir_path, (str, Path)) else
                  dataset_annotation_dir(Path(file_path).parent, ANNOTATIONS_DIR)
                  if file_path else ANNOTATIONS_DIR)
    json_path = target_dir / f"{image_id}.json"

    # 1. If Studio-saved annotation JSON exists, return it directly
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read annotation file: {e}")

    # 2. Check for adjacent LabelMe JSON file next to image
    candidate_json = None
    if file_path and isinstance(file_path, (str, Path)):
        cand = Path(file_path).with_suffix(".json")
        if cand.exists():
            candidate_json = cand
    if not candidate_json and not file_path:
        # Check in project / dataset directories
        for root in [Path("./datasets"), Path.cwd()]:
            if root.exists():
                matches = list(root.rglob(f"{image_id}.json"))
                if matches:
                    candidate_json = matches[0]
                    break

    if candidate_json and candidate_json.exists():
        try:
            with open(candidate_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("shapes"), list):
                raise ValueError("LabelMe JSON must contain a shapes list")
            # If standard LabelMe schema with "shapes"
            if "shapes" in data:
                img_w = data.get("imageWidth", 8192)
                img_h = data.get("imageHeight", 5464)
                converted_annotations = []
                # Palette for defect classes
                class_colors = {
                    "Bow": "#ef4444",
                    "Scratch": "#f59e0b",
                    "Pollution": "#06b6d4",
                    "Spot": "#3b82f6",
                    "White Spot": "#8b5cf6",
                    "Particle": "#ec4899",
                    "Discolor": "#14b8a6",
                    "Mount Guide 볼트 1EA 체결상태 불량": "#f97316",
                }
                for idx, shape in enumerate(data.get("shapes", [])):
                    if not isinstance(shape, dict):
                        raise ValueError(f"Shape {idx + 1} must be an object")
                    lbl = shape.get("label", "Defect")
                    stype = shape.get("shape_type", "polygon")
                    pts = shape.get("points", [])
                    if not isinstance(pts, list):
                        raise ValueError(f"Shape {idx + 1} points must be a list")
                    color = class_colors.get(lbl, "#3b82f6")

                    if stype == "polygon" and len(pts) >= 3:
                        poly = [[round(p[0], 2), round(p[1], 2)] for p in pts]
                        xs = [p[0] for p in poly]
                        ys = [p[1] for p in poly]
                        bbox = [min(xs), min(ys), max(xs), max(ys)]
                        converted_annotations.append({
                            "id": f"labelme_{idx}_{image_id[:12]}",
                            "type": "polygon",
                            "label": lbl,
                            "category_id": idx + 1,
                            "polygon": poly,
                            "points": poly,
                            "bbox": bbox,
                            "color": color,
                        })
                    elif stype in ("rectangle", "bbox") and len(pts) >= 2:
                        p1, p2 = pts[0], pts[1]
                        xmin, xmax = min(p1[0], p2[0]), max(p1[0], p2[0])
                        ymin, ymax = min(p1[1], p2[1]), max(p1[1], p2[1])
                        converted_annotations.append({
                            "id": f"labelme_{idx}_{image_id[:12]}",
                            "type": "bbox",
                            "label": lbl,
                            "category_id": idx + 1,
                            "bbox": [xmin, ymin, xmax, ymax],
                            "color": color,
                        })
                    else:
                        raise ValueError(f"Shape {idx + 1} has unsupported type or invalid points: {stype}")
                return {
                    "image_id": image_id,
                    "annotations": converted_annotations,
                    "image_width": img_w,
                    "image_height": img_h,
                    "mask_file": None,
                }
        except (OSError, UnicodeError) as e:
            logger.warning("Failed to read LabelMe JSON %s: %s", candidate_json, e)
            raise HTTPException(status_code=500, detail=f"Cannot read LabelMe annotations: {candidate_json.name}: {e}") from e
        except Exception as e:
            logger.warning("Failed to parse LabelMe JSON %s: %s", candidate_json, e)
            raise HTTPException(status_code=422, detail=f"Invalid LabelMe annotations: {candidate_json.name}: {e}") from e

    # Return empty list for unannotated images
    return {
        "image_id": image_id,
        "annotations": [],
        "image_width": 512,
        "image_height": 512,
        "mask_file": None,
    }


@router.delete("/{image_id}")
def delete_annotations(image_id: str, dir_path: Optional[str] = Query(None)):
    """Deletes stored annotations for the requested image_id."""
    _validate_image_id(image_id)
    target_dir = (_trusted_annotation_directory(str(dir_path))
                  if dir_path and isinstance(dir_path, (str, Path)) else ANNOTATIONS_DIR)
    json_path = target_dir / f"{image_id}.json"
    mask_path = target_dir / "masks" / f"{image_id}.png"

    deleted = False
    if json_path.exists():
        json_path.unlink()
        deleted = True
    if mask_path.exists():
        mask_path.unlink()

    return {"status": "deleted" if deleted else "not_found", "image_id": image_id}


def _resolve_image_path(image_path: Optional[str], image_id: Optional[str]) -> Path:
    if image_path:
        p = Path(image_path).resolve()
        if p.exists():
            return p
    if image_id:
        for root in [Path("./datasets"), Path("./projects"), Path("./tmp_out")]:
            if root.exists():
                for ext in [".png", ".jpg", ".jpeg", ".bmp"]:
                    matches = list(root.rglob(f"{image_id}{ext}"))
                    if matches:
                        return matches[0]
                    for file in root.rglob(f"*{ext}"):
                        if file.stem == image_id:
                            return file
    raise HTTPException(
        status_code=404,
        detail=format_error_response("ERR_003", details=f"Image not found for path: {image_path}, id: {image_id}"),
    )


class AutoSelectRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    image_path: Optional[str] = None
    image_id: Optional[str] = None
    seed_x: float
    seed_y: float
    tolerance: Optional[int] = 25


class ShapeConverterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source_type: Optional[Literal["bbox", "polygon", "mask", "rotated_bbox"]] = None
    target_type: Optional[Literal["bbox", "polygon", "mask", "rotated_bbox"]] = None
    data: Optional[Any] = None
    image_dimensions: Optional[Dict[str, int]] = None

    # Legacy fields
    image_path: Optional[str] = None
    image_id: Optional[str] = None
    bbox: Optional[List[float]] = None
    sensitivity: Optional[float] = 0.5


def _extract_bbox_from_data(data: Any) -> List[float]:
    if isinstance(data, list):
        if len(data) != 4:
            raise ValueError(f"bbox must contain 4 coordinates, got {len(data)}")
        return [float(x) for x in data]
    if isinstance(data, dict):
        if "bbox" in data and isinstance(data["bbox"], list):
            return [float(x) for x in data["bbox"]]
        if all(k in data for k in ("xmin", "ymin", "xmax", "ymax")):
            return [float(data["xmin"]), float(data["ymin"]), float(data["xmax"]), float(data["ymax"])]
    raise ValueError(f"Invalid bbox data: {data}")


def _extract_polygon_from_data(data: Any) -> List[List[float]]:
    if isinstance(data, list):
        return [[float(p[0]), float(p[1])] for p in data]
    if isinstance(data, dict):
        pts = data.get("polygon") or data.get("points")
        if pts and isinstance(pts, list):
            return [[float(p[0]), float(p[1])] for p in pts]
    raise ValueError(f"Invalid polygon data: {data}")


def _extract_rotated_bbox_from_data(data: Any) -> Tuple[List[float], List[float], float]:
    if isinstance(data, dict):
        if "center" in data and "size" in data:
            c = [float(x) for x in data["center"]]
            s = [float(x) for x in data["size"]]
            a = float(data.get("angle", 0.0))
            return c, s, a
        if "rotated_bbox" in data and isinstance(data["rotated_bbox"], list):
            arr = data["rotated_bbox"]
            return [float(arr[0]), float(arr[1])], [float(arr[2]), float(arr[3])], float(arr[4])
        if "cx" in data and "cy" in data:
            c = [float(data["cx"]), float(data["cy"])]
            w = float(data.get("width", data.get("w", 0.0)))
            h = float(data.get("height", data.get("h", 0.0)))
            a = float(data.get("angle", 0.0))
            return c, [w, h], a
    elif isinstance(data, list) and len(data) == 5:
        return [float(data[0]), float(data[1])], [float(data[2]), float(data[3])], float(data[4])
    raise ValueError(f"Invalid rotated_bbox data: {data}")


def _extract_mask_from_data(data: Any) -> np.ndarray:
    if isinstance(data, dict) and "mask" in data:
        data = data["mask"]
    m = np.asarray(data, dtype=np.uint8)
    if m.ndim > 2:
        m = m[:, :, 0]
    return (m > 0).astype(np.uint8) * 255


@router.post("/auto-select")
def api_auto_select(req: AutoSelectRequest):
    """
    AI Auto-Selector (Smart Magic Wand / Click-to-Segment):
    Extracts precise defect contour from a seed click point.
    """
    resolved_path = _resolve_image_path(req.image_path, req.image_id)
    try:
        res = auto_select_contour(
            resolved_path,
            seed_x=req.seed_x,
            seed_y=req.seed_y,
            tolerance=req.tolerance or 25,
        )
        return {"status": "success", "result": res}
    except Exception as e:
        logger.exception("Auto-selector failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail=format_error_response("ERR_UNKNOWN", details=f"Auto-selector failed: {e}"),
        )


@router.post("/shape-converter")
def api_shape_converter(req: ShapeConverterRequest):
    """
    Unified Bidirectional Shape Converter & Snapping API:
    Supports converting between bbox, polygon, mask, and rotated_bbox.
    """
    # 1. Legacy fallback if source_type is omitted but bbox is passed
    if req.source_type is None and req.bbox is not None:
        if len(req.bbox) != 4:
            raise HTTPException(status_code=400, detail="BBox must contain 4 elements [xmin, ymin, xmax, ymax]")
        resolved_path = _resolve_image_path(req.image_path, req.image_id)
        try:
            res = shape_converter_bbox_to_polygon(
                resolved_path,
                bbox=req.bbox,
                sensitivity=req.sensitivity if req.sensitivity is not None else 0.5,
            )
            return {
                "status": "success",
                "target_type": "polygon",
                "converted_data": res,
                "result": res,
            }
        except Exception as e:
            logger.exception("Legacy shape converter failed: %s", e)
            raise HTTPException(
                status_code=500,
                detail=format_error_response("ERR_UNKNOWN", details=f"Shape converter failed: {e}"),
            )

    if not req.source_type or not req.target_type:
        raise HTTPException(
            status_code=400,
            detail="source_type and target_type must be specified ('bbox', 'polygon', 'mask', 'rotated_bbox')",
        )

    img_w = 512
    img_h = 512
    if req.image_dimensions:
        img_w = int(req.image_dimensions.get("width", 512))
        img_h = int(req.image_dimensions.get("height", 512))

    try:
        converted: Dict[str, Any] = {}
        src = req.source_type
        tgt = req.target_type
        data = req.data if req.data is not None else (req.bbox if src == "bbox" else None)
        if data is None:
            raise ValueError("Input data is required for shape conversion")

        if src == "bbox":
            bbox = _extract_bbox_from_data(data)
            if tgt == "bbox":
                converted = {"bbox": bbox}
            elif tgt == "polygon":
                if req.image_path or req.image_id:
                    try:
                        resolved_path = _resolve_image_path(req.image_path, req.image_id)
                        res = shape_converter_bbox_to_polygon(
                            resolved_path,
                            bbox=bbox,
                            sensitivity=req.sensitivity if req.sensitivity is not None else 0.5,
                        )
                        converted = res
                    except Exception:
                        poly = bbox_to_polygon(bbox)
                        converted = {"polygon": poly}
                else:
                    poly = bbox_to_polygon(bbox)
                    converted = {"polygon": poly}
            elif tgt == "mask":
                m = bbox_to_mask(bbox, (img_h, img_w))
                converted = {"mask": m.tolist(), "shape": [img_h, img_w]}
            elif tgt == "rotated_bbox":
                rbox = bbox_to_rotated_bbox(bbox)
                converted = {
                    "rotated_bbox": [rbox["center"][0], rbox["center"][1], rbox["size"][0], rbox["size"][1], rbox["angle"]],
                    **rbox,
                }

        elif src == "polygon":
            poly = _extract_polygon_from_data(data)
            if tgt == "polygon":
                converted = {"polygon": poly}
            elif tgt == "bbox":
                bb = polygon_to_bbox(poly)
                converted = {"bbox": bb}
            elif tgt == "mask":
                m = polygon_to_mask(poly, (img_h, img_w))
                converted = {"mask": m.tolist(), "shape": [img_h, img_w]}
            elif tgt == "rotated_bbox":
                rbox = polygon_to_rotated_bbox(poly)
                converted = {
                    "rotated_bbox": [rbox["center"][0], rbox["center"][1], rbox["size"][0], rbox["size"][1], rbox["angle"]],
                    **rbox,
                }

        elif src == "mask":
            m = _extract_mask_from_data(data)
            if tgt == "mask":
                converted = {"mask": m.tolist(), "shape": list(m.shape)}
            elif tgt == "bbox":
                bb = mask_to_bbox(m)
                converted = {"bbox": bb}
            elif tgt == "polygon":
                poly = mask_to_polygon(m)
                converted = {"polygon": poly}
            elif tgt == "rotated_bbox":
                rbox = mask_to_rotated_bbox(m)
                converted = {
                    "rotated_bbox": [rbox["center"][0], rbox["center"][1], rbox["size"][0], rbox["size"][1], rbox["angle"]],
                    **rbox,
                }

        elif src == "rotated_bbox":
            center, size, angle = _extract_rotated_bbox_from_data(data)
            if tgt == "rotated_bbox":
                converted = {
                    "rotated_bbox": [center[0], center[1], size[0], size[1], angle],
                    "center": center,
                    "size": size,
                    "angle": angle,
                }
            elif tgt == "polygon":
                poly = rotated_bbox_to_polygon(center, size, angle)
                converted = {"polygon": poly}
            elif tgt == "bbox":
                bb = rotated_bbox_to_bbox(center, size, angle)
                converted = {"bbox": bb}
            elif tgt == "mask":
                poly = rotated_bbox_to_polygon(center, size, angle)
                m = polygon_to_mask(poly, (img_h, img_w))
                converted = {"mask": m.tolist(), "shape": [img_h, img_w]}

        return {
            "status": "success",
            "target_type": tgt,
            "converted_data": converted,
            "result": converted,
        }
    except Exception as e:
        logger.exception("Shape conversion failed: %s", e)
        raise HTTPException(
            status_code=400,
            detail=format_error_response("ERR_UNKNOWN", details=f"Shape conversion error: {e}"),
        )
