"""
backend/api/routes_dataset.py

Dataset Management, Synthetic Generation, Inspection, Pagination & Thumbnail Serving.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from backend.engine.dataset_loaders import (
    SUPPORTED_IMAGE_EXTENSIONS,
    inspect_dataset,
    split_dataset,
    validate_image_file,
)
from backend.engine.industrial_adapters import inspect_industrial_dataset, find_matching_image, is_valid_labelme_file
from backend.engine.synthetic_generator import generate_synthetic_dataset
from backend.utils.error_catalog import format_error_response

logger = logging.getLogger("vision_ai_studio.routes_dataset")

router = APIRouter(prefix="/api/dataset", tags=["dataset"])

THUMBNAIL_CACHE_DIR = Path.home() / ".vision_ai_studio_thumbnails"
THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
SPLIT_MANIFEST_DIR = Path.home() / ".modu_vision" / "splits"


class DatasetGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    task: Literal["classification", "detection", "segmentation", "anomaly", "all"] = "classification"
    num_samples: int = Field(100, ge=10, le=5000)
    output_dir: str = Field("./datasets/synthetic")
    modality: Optional[Literal["pcb", "wafer", "metal", "all"]] = "pcb"
    width: Optional[int] = Field(256, ge=64, le=1024)
    height: Optional[int] = Field(256, ge=64, le=1024)
    split_ratio: float = Field(0.8, gt=0.0, lt=1.0)
    normal_ratio: float = Field(0.5, ge=0.0, le=1.0)
    seed: int = 42


class DatasetImportRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    folder_path: str = Field(..., min_length=1)
    task: Literal["classification", "detection", "segmentation", "anomaly"] = "classification"
    validate_images: bool = True


class IndustrialImportOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: Optional[Literal["binary", "multiclass"]] = "binary"
    normal_dir: Optional[str] = None
    anomaly_dir: Optional[str] = None
    max_dim: Optional[int] = 1600


class IndustrialDatasetImportRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    folder_path: str = Field(..., min_length=1)
    task: Literal["classification", "detection", "segmentation", "anomaly"] = "classification"
    options: Optional[IndustrialImportOptions] = None


class DatasetSplitRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    folder_path: Optional[str] = None
    train_ratio: float = Field(0.8, gt=0.0, lt=1.0)
    val_ratio: Optional[float] = Field(None, ge=0.0, lt=1.0)
    test_ratio: float = Field(0.0, ge=0.0, lt=1.0)
    seed: Optional[int] = 42


class ImageMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")
    image_id: str
    file_name: str
    file_path: str
    width: Optional[int] = None
    height: Optional[int] = None
    split: str = "train"
    label: Optional[str] = None
    thumbnail_url: str


def _split_manifest_file(folder: Path) -> Path:
    key = hashlib.sha256(str(folder.resolve()).encode("utf-8")).hexdigest()
    return SPLIT_MANIFEST_DIR / f"{key}.json"


def _read_split_manifest(folder: Path) -> Dict[str, str]:
    path = _split_manifest_file(folder)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("folder_path") != str(folder.resolve()):
            return {}
        return {str(folder / relative): partition for relative, partition in data["assignments"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("Invalid split manifest at %s", path)
        return {}


def _write_split_manifest(folder: Path, assignments: Dict[str, str], seed: int) -> None:
    path = _split_manifest_file(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"folder_path": str(folder.resolve()), "seed": seed, "assignments": assignments}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    os.replace(temporary, path)


@router.post("/generate")
def generate_dataset(req: DatasetGenerateRequest):
    """Procedurally renders synthetic industrial defect dataset for OK/NG verification."""
    try:
        res = generate_synthetic_dataset(
            output_dir=req.output_dir,
            num_samples=req.num_samples,
            modality=req.modality or "pcb",
            task=req.task,
            width=req.width or 256,
            height=req.height or 256,
            split_ratio=req.split_ratio,
            normal_ratio=req.normal_ratio,
            seed=req.seed,
        )
        out_dir_path = Path(res.get("output_dir", req.output_dir)).resolve()
        classes = res.get("classes", [])
        if not classes:
            manifest_file = out_dir_path / "manifest.json"
            if manifest_file.exists():
                try:
                    with open(manifest_file, "r", encoding="utf-8") as mf:
                        m_data = json.load(mf)
                        cats = m_data.get("categories", [])
                        classes = [c.get("name") if isinstance(c, dict) else str(c) for c in cats]
                except Exception:
                    pass
        if not classes:
            for cand_dir in [out_dir_path / req.task / "train", out_dir_path / "train"]:
                if cand_dir.is_dir():
                    classes = [d.name for d in sorted(cand_dir.iterdir()) if d.is_dir() and not d.name.startswith(".")]
                    if classes:
                        break
        if not classes:
            classes = ["OK", "Defect"]

        return {
            "status": "success",
            "count": res.get("count", req.num_samples),
            "classes": classes,
            "output_dir": str(out_dir_path),
            "train_count": res.get("train_count", 0),
            "val_count": res.get("val_count", 0),
            "duration_seconds": res.get("duration_seconds", 0.0),
        }
    except Exception as e:
        logger.exception("Failed to generate synthetic dataset: %s", e)
        raise HTTPException(
            status_code=400,
            detail=format_error_response("ERR_UNKNOWN", details=str(e)),
        )


@router.post("/import")
def import_dataset(req: DatasetImportRequest):
    """Scans dataset directory and returns total_images, classes, and split."""
    folder = Path(req.folder_path).resolve()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(
            status_code=404,
            detail=format_error_response("ERR_NO_DATA", details=f"Folder not found: {req.folder_path}"),
        )

    effective_folder = folder
    task_clean = req.task.lower().strip()
    if (folder / task_clean / "train").is_dir():
        effective_folder = folder / task_clean
    elif not (folder / "train").is_dir():
        for sub in folder.iterdir():
            if sub.is_dir() and (sub / "train").is_dir():
                effective_folder = sub
                break

    try:
        summary = inspect_dataset(effective_folder, req.task)
        # If standard scanner found 0 images, auto-inspect via industrial adapter (LabelMe, manufacturing layout)
        if summary.total_images == 0:
            ind_res = inspect_industrial_dataset(effective_folder, task=req.task)
            if ind_res and ind_res.get("total_images", 0) > 0:
                summary.total_images = ind_res["total_images"]
                summary.classes = ind_res.get("classes", {})
                ind_split = ind_res.get("split", {})
                summary.split_counts = {
                    "train": ind_split.get("train", 0),
                    "val": ind_split.get("val", 0),
                    "test": ind_split.get("test", 0),
                }
    except Exception as e:
        logger.exception("Failed to inspect dataset: %s", e)
        raise HTTPException(
            status_code=422,
            detail=format_error_response("ERR_NO_DATA", details=str(e)),
        )

    # Convert split_counts to { train: int, val: int }
    split_counts = summary.split_counts
    train_count = split_counts.get("train", 0)
    val_count = split_counts.get("val", 0)
    image_files = {p.resolve() for p in folder.iterdir() if p.is_file() and not p.name.startswith("._")
                   and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS}
    paired_images = {match.resolve() for annotation in folder.glob("*.json")
                     if is_valid_labelme_file(annotation, require_image=True)
                     for match in [find_matching_image(annotation)] if match is not None}
    unlabeled_images = len(image_files - paired_images) if paired_images else 0

    # Optional image validation scanning
    corrupted_images = []
    if req.validate_images:
        count_checked = 0
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                val_res = validate_image_file(p)
                if not val_res.valid:
                    corrupted_images.append({
                        "file_path": str(p),
                        "error_code": val_res.error_code,
                        "details": val_res.details,
                    })
                count_checked += 1
                if count_checked > 200:  # Sample-based check for quick responsiveness
                    break

    return {
        "status": "success",
        "total_images": summary.total_images,
        "source_images": len(image_files) if paired_images else summary.total_images,
        "unlabeled_images": unlabeled_images,
        "classes": summary.classes,
        "split": {
            "train": train_count,
            "val": val_count,
            "test": split_counts.get("test", 0),
        },
        "corrupted_images": corrupted_images,
    }


@router.post("/import-industrial")
def import_industrial_dataset_endpoint(req: IndustrialDatasetImportRequest):
    """
    Ingests real manufacturing inspection datasets using industrial adapters.
    Handles hierarchical classification (OK/**, NG/**), LabelMe annotations,
    polygon rasterization, and flexible anomaly datasets without strict Reference layout.
    """
    folder = Path(req.folder_path).resolve()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(
            status_code=404,
            detail=format_error_response("ERR_NO_DATA", details=f"Folder not found: {req.folder_path}"),
        )

    options_dict = req.options.model_dump() if req.options else {}
    try:
        result = inspect_industrial_dataset(folder, task=req.task, options=options_dict)
        return result
    except Exception as e:
        logger.exception("Failed to import industrial dataset: %s", e)
        raise HTTPException(
            status_code=422,
            detail=format_error_response("ERR_NO_DATA", details=str(e)),
        )


@router.post("/split")
def split_dataset_endpoint(req: DatasetSplitRequest):
    """Performs stratified partitioning of items in the dataset directory."""
    target_dir = req.folder_path or "./datasets/synthetic"
    folder = Path(target_dir).resolve()
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Dataset folder not found: {folder}")
    val_ratio = req.val_ratio if req.val_ratio is not None else 1.0 - req.train_ratio - req.test_ratio
    if val_ratio < 0 or abs(req.train_ratio + val_ratio + req.test_ratio - 1.0) > 1e-6:
        raise HTTPException(status_code=422, detail="Train, validation and test ratios must sum to 1.")
    # Check for nested task directory
    effective_folder = folder
    if not (folder / "train").is_dir():
        for sub in folder.iterdir():
            if sub.is_dir() and (sub / "train").is_dir():
                effective_folder = sub
                break

    # Gather image items
    items = []
    for split_dir in ["train", "val", "test"]:
        s_path = effective_folder / split_dir
        if s_path.is_dir():
            for cdir in s_path.iterdir():
                if cdir.is_dir():
                    cname = cdir.name
                    for f in cdir.glob("*"):
                        if f.is_file() and not f.name.startswith("._") and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                            items.append({"image_path": str(f), "label": cname})

    if not items:
        # Check flat or recursive directories
        for f in effective_folder.rglob("*"):
            if f.is_file() and not f.name.startswith("._") and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                items.append({"image_path": str(f), "label": f.parent.name})

    paired_images = {match.resolve() for annotation in effective_folder.glob("*.json")
                     if is_valid_labelme_file(annotation, require_image=True)
                     for match in [find_matching_image(annotation)] if match is not None}
    if paired_images:
        items = [item for item in items if Path(item["image_path"]).resolve() in paired_images]

    if not items:
        raise HTTPException(status_code=422, detail="No valid images found to split.")

    seed = req.seed if req.seed is not None else 42
    primary = split_dataset(items, train_ratio=req.train_ratio, val_ratio=val_ratio + req.test_ratio, seed=seed)
    train_items = primary["train"]
    remaining = primary["val"]
    if req.test_ratio > 0 and remaining:
        secondary = split_dataset(
            remaining, train_ratio=val_ratio, val_ratio=req.test_ratio, seed=seed + 1,
        )
        val_items, test_items = secondary["train"], secondary["val"]
    else:
        val_items, test_items = remaining, []

    assignments = {}
    for partition, partition_items in (("train", train_items), ("val", val_items), ("test", test_items)):
        for item in partition_items:
            relative = str(Path(item["image_path"]).relative_to(folder))
            assignments[relative] = partition
    _write_split_manifest(folder, assignments, seed)

    return {
        "status": "success",
        "split": {
            "train": len(train_items),
            "val": len(val_items),
            "test": len(test_items),
        },
    }


@router.get("/images")
def list_dataset_images(
    folder_path: Optional[str] = Query(None, description="Dataset folder path"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    split: Optional[str] = Query(None, description="Filter by train, val, or test"),
    class_name: Optional[str] = Query(None, description="Filter by category"),
):
    """Returns paginated image metadata with thumbnail URLs."""
    target_dir = Path(folder_path).resolve() if folder_path else Path("./datasets/synthetic").resolve()
    if not target_dir.exists():
        return {"total": 0, "limit": limit, "offset": offset, "items": []}

    effective_dir = target_dir
    if not (target_dir / "train").is_dir():
        for sub in target_dir.iterdir():
            if sub.is_dir() and (sub / "train").is_dir():
                effective_dir = sub
                break

    all_images: List[ImageMeta] = []
    # Search structured splits
    for s_name in (["train", "val", "test"] if not split else [split]):
        s_dir = effective_dir / s_name
        if s_dir.is_dir():
            for sub in sorted(s_dir.iterdir()):
                if sub.is_dir() and (not class_name or sub.name == class_name):
                    c_label = sub.name
                    for f in sorted(sub.iterdir()):
                        if f.is_file() and not f.name.startswith("._") and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                            all_images.append(
                                ImageMeta(
                                    image_id=f.stem,
                                    file_name=f.name,
                                    file_path=str(f),
                                    split=s_name,
                                    label=c_label,
                                    thumbnail_url=f"/api/dataset/thumbnail/{f.name}?file_path={f}",
                                )
                            )
                elif sub.is_file() and not sub.name.startswith("._") and sub.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    all_images.append(
                        ImageMeta(
                            image_id=sub.stem,
                            file_name=sub.name,
                            file_path=str(sub),
                            split=s_name,
                            label=None,
                            thumbnail_url=f"/api/dataset/thumbnail/{sub.name}?file_path={sub}",
                        )
                    )

    # Fallback to direct recursive glob if no splits
    if not all_images and not any((effective_dir / name).is_dir() for name in ("train", "val", "test")):
        assignments = _read_split_manifest(target_dir)
        has_labelme = any(is_valid_labelme_file(path, require_image=True) for path in target_dir.glob("*.json"))
        for f in sorted(target_dir.rglob("*")):
            if f.is_file() and not f.name.startswith("._") and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                assigned_split = assignments.get(str(f))
                if split and assigned_split != split:
                    continue
                c_label = None if has_labelme else f.parent.name
                img_w = None
                img_h = None
                json_candidate = f.with_suffix(".json")
                if json_candidate.exists():
                    try:
                        with open(json_candidate, "r", encoding="utf-8") as jf:
                            jd = json.load(jf)
                            img_w = jd.get("imageWidth")
                            img_h = jd.get("imageHeight")
                            shapes = jd.get("shapes", [])
                            if shapes and "label" in shapes[0]:
                                c_label = shapes[0]["label"]
                    except Exception:
                        pass
                if class_name and c_label != class_name:
                    continue
                all_images.append(
                    ImageMeta(
                        image_id=f.stem,
                        file_name=f.name,
                        file_path=str(f),
                        width=img_w,
                        height=img_h,
                        split=assigned_split or ("unlabeled" if has_labelme and not json_candidate.exists() else "all"),
                        label=c_label,
                        thumbnail_url=f"/api/dataset/thumbnail/{f.name}?file_path={f}",
                    )
                )

    total = len(all_images)
    paged = all_images[offset : offset + limit]
    for item in paged:
        if item.width is None or item.height is None:
            try:
                with Image.open(item.file_path) as source_image:
                    item.width, item.height = source_image.size
            except (OSError, ValueError):
                pass
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [item.model_dump() for item in paged],
    }


@router.get("/raw/{image_path:path}")
def get_raw_image(
    image_path: str,
    file_path: Optional[str] = Query(None),
):
    """Serves uncompressed raw inspection image directly for pixel-accurate labeling."""
    candidate_path = None
    if file_path and Path(file_path).is_file():
        candidate_path = Path(file_path)
    elif Path(image_path).is_file():
        candidate_path = Path(image_path)
    else:
        for root in [Path("./datasets"), Path.cwd()]:
            matches = list(root.rglob(image_path))
            if matches:
                candidate_path = matches[0]
                break

    if not candidate_path or not candidate_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    stat = candidate_path.stat()
    if stat.st_size == 0:
        raise HTTPException(
            status_code=400,
            detail=format_error_response("ERR_CORRUPT_IMAGE", details=f"Image file is empty (0 bytes): {candidate_path}"),
        )

    ext = candidate_path.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }
    media_type = media_types.get(ext, "application/octet-stream")
    return FileResponse(
        candidate_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/thumbnail/{image_path:path}")
def get_thumbnail(
    image_path: str,
    file_path: Optional[str] = Query(None),
    size: int = Query(128, ge=32, le=4096),
):
    """Generates and serves a downsampled thumbnail image with aggressive caching headers."""
    # Resolve image file location
    candidate_path = None
    if file_path and Path(file_path).is_file():
        candidate_path = Path(file_path)
    elif Path(image_path).is_file():
        candidate_path = Path(image_path)
    else:
        # Search synthetic datasets or cwd
        for root in [Path("./datasets"), Path.cwd()]:
            matches = list(root.rglob(image_path))
            if matches:
                candidate_path = matches[0]
                break

    if not candidate_path or not candidate_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    # Reject 0-byte files with HTTP 400 ERR_CORRUPT_IMAGE
    stat = candidate_path.stat()
    if stat.st_size == 0:
        raise HTTPException(
            status_code=400,
            detail=format_error_response("ERR_CORRUPT_IMAGE", details=f"Image file is empty (0 bytes): {candidate_path}"),
        )

    cache_key = hashlib.md5(f"{candidate_path}:{stat.st_mtime}:{size}".encode("utf-8")).hexdigest()
    cached_thumb = THUMBNAIL_CACHE_DIR / f"{cache_key}.jpg"

    if cached_thumb.exists():
        return FileResponse(
            cached_thumb,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    try:
        with Image.open(candidate_path) as img:
            img = img.convert("RGB")
            img.thumbnail((size, size), Image.Resampling.LANCZOS)
            img.save(cached_thumb, format="JPEG", quality=85)
        return FileResponse(
            cached_thumb,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception as e:
        logger.warning("Failed to render thumbnail for %s: %s", candidate_path, e)
        raise HTTPException(
            status_code=400,
            detail=format_error_response("ERR_CORRUPT_IMAGE", details=f"Cannot decode image {candidate_path}: {e}"),
        )
