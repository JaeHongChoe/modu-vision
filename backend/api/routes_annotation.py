"""
backend/api/routes_annotation.py

Interactive Canvas Annotation Persistence, BBox Sanitization & Polygon-to-Mask Rasterization.
"""

from __future__ import annotations

import json
import logging
import base64
import binascii
import hashlib
import os
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.engine.dataset_loaders import BoundingBox, SUPPORTED_IMAGE_EXTENSIONS
from backend.engine.annotation_storage import dataset_annotation_dir, scoped_annotation_root, request_project_root
from backend.engine.annotation_transactions import AnnotationFileTransaction
from backend.engine import dataset_metadata as metadata_engine
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
from backend.engine.label_candidate_providers import foundation_candidates
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
    roots = [scoped_annotation_root(ANNOTATIONS_DIR).resolve()]
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
    direction_deg: Optional[float] = Field(None,ge=0,lt=360,allow_inf_nan=False)

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
    expected_revision: Optional[int] = Field(None, ge=1)
    actor: str = Field("operator", min_length=1, max_length=100)
    lease_token: Optional[str] = Field(None, min_length=1, max_length=200)
    mask_classes: Optional[List[Dict[str, Any]]] = None


class AnnotationBatchSaveRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: List[AnnotationSaveRequest]


def _save_annotations_impl(req: AnnotationSaveRequest, file_transaction=None):
    target_dir = (_trusted_annotation_directory(req.output_dir) if req.output_dir else
                  dataset_annotation_dir(Path(req.image_path).parent, ANNOTATIONS_DIR)
                  if req.image_path else scoped_annotation_root(ANNOTATIONS_DIR))
    if file_transaction is not None:
        return _save_annotations_locked(req, target_dir, file_transaction)
    # Legacy requests have no ledger lock. Hold one image lock from validation
    # and inherited palette reads through encoding, publication and cleanup.
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        lock_name = hashlib.sha256(req.image_id.encode()).hexdigest()
        with metadata_engine._file_lock(target_dir / f".annotation-save-{lock_name}.lock"):
            files = AnnotationFileTransaction()
            result = _save_annotations_locked(req, target_dir, files)
            files.cleanup()
            return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=format_error_response(
            "ERR_UNKNOWN", details=f"Failed to write annotations: {exc}")) from exc


def _save_annotations_locked(req: AnnotationSaveRequest, target_dir: Path, file_transaction):
    """
    Saves canvas annotations for an image.
    Automatically:
      1. Sanitizes bounding box coordinates with coordinate swapping and boundary clamping.
      2. Rasterizes vector polygon contours to binary/multiclass mask PNG for segmentation.
    """
    _validate_image_id(req.image_id)
    if req.image_path and Path(req.image_path).stem != req.image_id:
        raise HTTPException(status_code=422, detail="image_id must match image_path filename")
    if req.image_path:
        image = Path(req.image_path)
        peers = [p for p in image.parent.iterdir() if p.is_file() and p.stem == image.stem and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS]
        if len(peers) > 1:
            raise HTTPException(status_code=422, detail="같은 폴더에 확장자만 다른 동일 이름 이미지가 있어 라벨 저장이 모호합니다. 이미지 이름을 구분한 데이터셋을 사용하세요.")
    masks_dir = target_dir / "masks"

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
            item_dict["polygon"] = [[float(pt[0]), float(pt[1])] for pt in poly_points]
            item_dict["points"] = item_dict["polygon"]

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

    # A mixed mask/vector label set must train all its accepted regions.
    if brush_masks:
        for item in sanitized_items:
            if item.get("type") == "bbox" and item.get("bbox"):
                x1,y1,x2,y2=item["bbox"]
                polygons_for_mask.append({"class_id":item.get("category_id") or 1,"class_name":item["label"],
                    "is_hole":False,"points":[[int(x1),int(y1)],[int(x2),int(y1)],[int(x2),int(y2)],[int(x1),int(y2)]]})

    # If polygon contours provided, auto-rasterize mask PNG
    mask_file_path = None
    mask_file = masks_dir / f"{req.image_id}.png"
    mask_bytes = None
    if polygons_for_mask or brush_masks:
        try:
            mask_arr = polygons_to_mask(polygons_for_mask, height=img_h, width=img_w) if polygons_for_mask else np.zeros((img_h, img_w), dtype=np.uint8)
            for brush_pixels, category_id in brush_masks:
                mask_arr[brush_pixels] = category_id
            encoded, png = cv2.imencode(".png", mask_arr)
            if not encoded:
                raise OSError(f"Could not encode annotation mask: {mask_file}")
            mask_bytes = png.tobytes()
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
    # Preserve explicit external palette entries, including classes with no pixels.
    mapping = req.mask_classes
    if mapping is None and json_path.is_file():
        try: mapping = json.loads(json_path.read_text(encoding="utf-8")).get("mask_classes")
        except (OSError, ValueError): mapping = None
    if mapping is not None:
        from backend.engine.mask_exchange import _classes
        try:
            classes = _classes(mapping)
            for item in sanitized_items:
                if item["type"] != "tag":
                    cid = item.get("category_id") or 1
                    classes[cid] = {"id": cid, "name": item["label"], "color": item.get("color") or "#22d3ee"}
            data["mask_classes"] = list(_classes(list(classes.values())).values())
        except ValueError as exc: raise HTTPException(422, detail=str(exc)) from exc

    # Finish validation and both encodings before touching any live annotation.
    try:
        json_bytes = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid annotation data: {exc}") from exc
    updates = {mask_file: mask_bytes, json_path: json_bytes}
    try:
        file_transaction.publish(updates)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=format_error_response("ERR_UNKNOWN", details=f"Failed to write annotations: {e}"),
        ) from e

    return {
        "status": "saved",
        "image_id": req.image_id,
        "count": len(sanitized_items),
        "mask_generated": mask_file_path is not None,
    }


def _metadata_binding(image_path):
    project = request_project_root()
    if project is None or not image_path:
        return None
    try:
        saved = json.loads((project / "project.json").read_text(encoding="utf-8"))
        if not saved.get("source_dataset_dir"):
            return None  # Legacy canvas can read before importing a project dataset.
        source = Path(saved["source_dataset_dir"]).expanduser().resolve()
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(status_code=422, detail="Active project has no valid dataset source.")
    try:
        metadata_engine._visible_path(source, image_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return project, source


@router.post("/save")
def save_annotations(req: AnnotationSaveRequest, request: Request = None):
    from backend.api.shared_authorization import request_actor
    req=req.model_copy(update={'actor':request_actor(request,req.actor)})
    binding = _metadata_binding(req.image_path)
    if binding is None:
        return _save_annotations_impl(req)
    project, source = binding
    # Keep validation, image write and review invalidation under the same OS lock.
    with metadata_engine.metadata_transaction(project, source) as ledger:
        row = metadata_engine._ensure(ledger, project, source, req.image_path)
        if req.expected_revision is not None and row["revision"] != req.expected_revision:
            from backend.engine.team_data import public_image
            raise HTTPException(status_code=409, detail={"message": "다른 작업자가 수정했습니다. 최신 라벨을 다시 불러오세요.", "current": public_image(row)})
        from backend.engine.team_data import guard_annotation_write, annotation_written, public_image
        try:
            from backend.engine.team_data import _state
            if _state(ledger)['settings']['editing_enabled'] and req.expected_revision is None:
                raise ValueError('팀 편집은 최신 라벨의 수정 번호가 필요합니다. 최신 라벨을 다시 불러오세요.')
            guard_annotation_write(ledger, row, req.actor, req.lease_token, req.annotations)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={'message': str(exc), 'current': public_image(row)}) from exc
        result = _save_annotations_impl(req, metadata_engine.annotation_file_transaction(ledger))
        row["workflow_state"] = "needs_review"
        row["reviewer"] = None
        row["annotation_hash"] = metadata_engine._annotation_hash(project, source, Path(req.image_path))
        row["mask_hash"] = metadata_engine._mask_hash(project, source, Path(req.image_path))
        annotation_written(row, req.actor)
        metadata_engine._event(row, req.actor.strip() or "operator", "annotation_changed", {"workflow_state":"needs_review"})
        result["metadata"] = public_image(row)
        return result


@router.post("/batch_save")
def batch_save_annotations(req: AnnotationBatchSaveRequest, request: Request = None):
    """One project/source commits together; legacy mixed failures disclose saves."""
    bindings = []
    for item in req.items:
        try:
            bindings.append(_metadata_binding(item.image_path))
        except HTTPException:
            # Keep per-item validation and its original error for an unbound or
            # mixed batch, including the saved prefix if a later item fails.
            bindings.append(None)
    atomic = bool(bindings and bindings[0] is not None and all(binding == bindings[0] for binding in bindings))
    transaction = metadata_engine.metadata_transaction(*bindings[0]) if atomic else nullcontext()
    results = []
    with transaction:
        for index, item in enumerate(req.items):
            try:
                results.append(save_annotations(item, request))
            except HTTPException as exc:
                if atomic:
                    raise
                detail = dict(exc.detail) if isinstance(exc.detail, dict) else {"message": exc.detail}
                detail.update({"cause": exc.detail, "saved_images_count": len(results), "results": results,
                               "failed_image_id": item.image_id, "failed_index": index})
                raise HTTPException(status_code=exc.status_code, detail=detail, headers=exc.headers) from exc
            except Exception as exc:
                if atomic:
                    raise
                detail = format_error_response("ERR_UNKNOWN", details=f"Failed to save annotations: {exc}")
                detail.update({"cause": str(exc), "saved_images_count": len(results), "results": results,
                               "failed_image_id": item.image_id, "failed_index": index})
                raise HTTPException(status_code=500, detail=detail) from exc
    return {
        "status": "saved",
        "saved_images_count": len(results),
        "results": results,
    }


def _get_annotations_impl(
    image_id: str,
    dir_path: Optional[str] = Query(None),
    file_path: Optional[str] = Query(None),
):
    """Retrieves stored annotations for the requested image_id, with seamless LabelMe format conversion."""
    _validate_image_id(image_id)
    target_dir = (_trusted_annotation_directory(str(dir_path)) if dir_path and isinstance(dir_path, (str, Path)) else
                  dataset_annotation_dir(Path(file_path).parent, ANNOTATIONS_DIR)
                  if file_path else scoped_annotation_root(ANNOTATIONS_DIR))
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
                    if converted_annotations:
                        flags=shape.get('flags',{})
                        if flags.get('studio_rotated_bbox') is not None:
                            converted_annotations[-1].update(type='rotated_bbox',rotated_bbox=flags['studio_rotated_bbox'])
                        direction=flags.get('studio_direction_deg',shape.get('direction_deg'))
                        if direction is not None:
                            converted_annotations[-1]['direction_deg']=AnnotationItem(label=lbl,direction_deg=direction).direction_deg
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


@router.get("/{image_id}")
def get_annotations(image_id: str, dir_path: Optional[str] = Query(None), file_path: Optional[str] = Query(None)):
    result = _get_annotations_impl(image_id, dir_path, file_path)
    binding = _metadata_binding(file_path) if isinstance(file_path, (str, Path)) else None
    if binding:
        project, source = binding
        studio_json = dataset_annotation_dir(Path(file_path).parent, ANNOTATIONS_DIR) / f"{image_id}.json"
        if not studio_json.exists() and not Path(file_path).with_suffix(".json").exists():
            from backend.engine.annotation_formats import source_annotations_for_image
            try:
                imported = source_annotations_for_image(source, Path(file_path))
            except (ValueError, KeyError, TypeError, OSError) as exc:
                raise HTTPException(status_code=422, detail=f"Source annotation format: {exc}") from exc
            if imported is not None:
                result["annotations"] = imported
        result["metadata"] = metadata_engine.metadata_for_path(*binding, file_path)
        result["image_width"] = result["metadata"]["width"]
        result["image_height"] = result["metadata"]["height"]
    return result


@router.get("/{image_id}/mask")
def get_annotation_mask(
    image_id: str,
    dir_path: Optional[str] = Query(None),
    file_path: Optional[str] = Query(None),
):
    """Serve only this image's saved mask from its dataset-scoped annotation folder."""
    _validate_image_id(image_id)
    if file_path and Path(file_path).stem != image_id:
        raise HTTPException(status_code=422, detail="image_id must match file_path filename")
    target_dir = (_trusted_annotation_directory(dir_path) if dir_path else
                  dataset_annotation_dir(Path(file_path).parent, ANNOTATIONS_DIR)
                  if file_path else scoped_annotation_root(ANNOTATIONS_DIR))
    mask_path = target_dir / "masks" / f"{image_id}.png"
    json_path = target_dir / f"{image_id}.json"
    if not json_path.is_file() or not mask_path.is_file():
        raise HTTPException(status_code=404, detail="Annotation mask not found")
    try:
        saved = json.loads(json_path.read_text(encoding="utf-8"))
        if not saved.get("mask_file"):
            raise HTTPException(status_code=404, detail="Annotation mask not found")
        mask = cv2.imread(str(mask_path), cv2.IMREAD_UNCHANGED)
        if mask is None or mask.ndim != 2:
            raise ValueError("Saved annotation mask is unreadable")
        overlay = np.zeros((*mask.shape, 4), dtype=np.uint8)
        category_colors: dict[int, str] = {}
        for item in saved.get("annotations", []):
            if isinstance(item, dict):
                color = item.get("color")
                if isinstance(color, str) and len(color) == 7 and color.startswith("#"):
                    try:
                        int(color[1:], 16)
                        category_colors[int(item.get("category_id") or 1)] = color
                    except (TypeError, ValueError):
                        continue
        for category_id in np.unique(mask):
            if category_id == 0:
                continue
            color = category_colors.get(int(category_id), "#3b82f6")
            pixels = mask == category_id
            overlay[pixels, 0] = int(color[5:7], 16)
            overlay[pixels, 1] = int(color[3:5], 16)
            overlay[pixels, 2] = int(color[1:3], 16)
            overlay[pixels, 3] = 255
        ok, encoded = cv2.imencode(".png", overlay)
        if not ok:
            raise ValueError("Could not encode annotation mask")
        return Response(content=encoded.tobytes(), media_type="image/png")
    except HTTPException:
        raise
    except (OSError, UnicodeError, ValueError, cv2.error) as exc:
        raise HTTPException(status_code=500, detail=f"Cannot read annotation mask: {exc}") from exc


@router.delete("/{image_id}")
def delete_annotations(image_id: str, dir_path: Optional[str] = Query(None)):
    """Deletes stored annotations for the requested image_id."""
    _validate_image_id(image_id)
    target_dir = (_trusted_annotation_directory(str(dir_path))
                  if dir_path and isinstance(dir_path, (str, Path)) else scoped_annotation_root(ANNOTATIONS_DIR))
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
    backend: Literal['opencv','foundation'] = 'opencv'
    device: str = 'cpu'
    points: List[Dict[str, Any]] = Field(default_factory=list)


class ShapeConverterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source_type: Optional[Literal["bbox", "polygon", "mask", "rotated_bbox"]] = None
    target_type: Optional[Literal["bbox", "polygon", "mask", "rotated_bbox"]] = None
    data: Optional[Any] = None
    image_dimensions: Optional[Dict[str, int]] = None
    mask_color: str = Field(default="#3b82f6", pattern=r"^#[0-9a-fA-F]{6}$")

    # Legacy fields
    image_path: Optional[str] = None
    image_id: Optional[str] = None
    bbox: Optional[List[float]] = None
    sensitivity: Optional[float] = 0.5
    backend: Literal['opencv','foundation'] = 'opencv'
    device: str = 'cpu'


def _foundation_selection_setup():
    root = request_project_root()
    if root is None: return {}
    path = Path(root) / 'semantic_labeling.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def _foundation_selection_result(path, req, points=None, boxes=None):
    candidates = foundation_candidates(path, _foundation_selection_setup(), points=points, boxes=boxes,
                                       device=req.device, threshold=0, output_geometry='polygon', max_candidates=1)
    if not candidates: raise ValueError('The foundation model returned no nonempty object mask; refine the prompt')
    candidate = candidates[0]
    return {**candidate, 'bbox':candidate['annotation']['bbox']}


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
    if isinstance(data, dict):
        data = data.get("mask_rle", data.get("mask", data))
    if isinstance(data, str):
        prefix, separator, encoded = data.partition(",")
        if prefix != "data:image/png;base64" or not separator or len(encoded) > 32_000_000:
            raise ValueError("Mask must be a PNG data URL under 24 MB")
        decoded = base64.b64decode(encoded, validate=True)
        image = cv2.imdecode(np.frombuffer(decoded, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError("Mask PNG could not be decoded")
        if image.ndim == 3:
            data = image[:, :, 3] if image.shape[2] == 4 else np.any(image > 0, axis=2)
        else:
            data = image
    m = np.asarray(data, dtype=np.uint8)
    if m.ndim > 2:
        m = m[:, :, 3] if m.shape[2] == 4 else m[:, :, 0]
    if m.ndim != 2:
        raise ValueError("Mask must have two image dimensions")
    return (m > 0).astype(np.uint8) * 255


def _encode_mask_result(mask: np.ndarray, color: str) -> Dict[str, Any]:
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    bgra = np.empty((*mask.shape, 4), dtype=np.uint8)
    bgra[:, :, 0] = blue
    bgra[:, :, 1] = green
    bgra[:, :, 2] = red
    bgra[:, :, 3] = mask
    encoded_ok, encoded_png = cv2.imencode(".png", bgra, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not encoded_ok:
        raise ValueError("Mask PNG encoding failed")
    return {
        "mask_rle": "data:image/png;base64," + base64.b64encode(encoded_png.tobytes()).decode("ascii"),
        "shape": [int(mask.shape[0]), int(mask.shape[1])],
    }


@router.post("/auto-select")
def api_auto_select(req: AutoSelectRequest):
    """
    Select a model mask or the explicitly named OpenCV flood-fill alternative.
    """
    resolved_path = _resolve_image_path(req.image_path, req.image_id)
    try:
        if req.backend == 'foundation':
            result = _foundation_selection_result(resolved_path,req,
                points=[{'x':req.seed_x,'y':req.seed_y,'label':1}, *req.points])
            return {'status':'success','result':result}
        res = auto_select_contour(
            resolved_path,
            seed_x=req.seed_x,
            seed_y=req.seed_y,
            tolerance=req.tolerance or 25,
        )
        return {"status": "success", "result": {**res,'source':'opencv_flood_fill','is_foundation':False}}
    except (ValueError,ImportError,RuntimeError) as e:
        raise HTTPException(422,detail=str(e)) from e
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
    if req.backend == 'foundation':
        if req.target_type not in (None,'polygon','mask') or req.source_type not in (None,'bbox'):
            raise HTTPException(422,detail='Foundation conversion accepts a source image and box, producing an object mask or polygon')
        try:
            box = req.bbox or _extract_bbox_from_data(req.data)
            resolved_path = _resolve_image_path(req.image_path,req.image_id)
            result = _foundation_selection_result(resolved_path,req,boxes=[box])
            return {'status':'success','target_type':req.target_type or 'polygon','converted_data':result,'result':result}
        except (ValueError,ImportError,RuntimeError) as e:
            raise HTTPException(422,detail=str(e)) from e
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
            res.update(source='opencv_edges',is_foundation=False)
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
                converted = _encode_mask_result(m, req.mask_color)
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
                converted = _encode_mask_result(m, req.mask_color)
            elif tgt == "rotated_bbox":
                rbox = polygon_to_rotated_bbox(poly)
                converted = {
                    "rotated_bbox": [rbox["center"][0], rbox["center"][1], rbox["size"][0], rbox["size"][1], rbox["angle"]],
                    **rbox,
                }

        elif src == "mask":
            m = _extract_mask_from_data(data)
            if tgt == "mask":
                converted = _encode_mask_result(m, req.mask_color)
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
                converted = _encode_mask_result(m, req.mask_color)

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
