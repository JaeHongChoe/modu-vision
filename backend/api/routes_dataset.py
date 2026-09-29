"""
backend/api/routes_dataset.py

Dataset Management, Synthetic Generation, Inspection, Pagination & Thumbnail Serving.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import random
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from backend.engine.dataset_loaders import (
    AnomalyDataset,
    DatasetSummary,
    SUPPORTED_IMAGE_EXTENSIONS,
    inspect_dataset,
    split_dataset,
    validate_image_file,
)
from backend.engine.industrial_adapters import inspect_industrial_dataset, find_matching_image, is_valid_labelme_file
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.synthetic_generator import generate_synthetic_dataset
from backend.utils.error_catalog import format_error_response

logger = logging.getLogger("vision_ai_studio.routes_dataset")

router = APIRouter(prefix="/api/dataset", tags=["dataset"])

THUMBNAIL_CACHE_DIR = Path.home() / ".vision_ai_studio_thumbnails"
THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
SPLIT_MANIFEST_DIR = Path.home() / ".modu_vision" / "splits"
STUDIO_ANNOTATIONS_DIR = Path("./annotations")


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
    task: Optional[Literal["classification", "detection", "segmentation", "anomaly"]] = None
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


def _paired_labelme_images(folder: Path) -> set[Path]:
    """Find images trainable with their source or dataset-scoped Studio label.

    A Studio save takes precedence over source LabelMe, including an empty save
    that intentionally removes the original annotation.
    """
    images = {
        path.resolve() for path in folder.iterdir()
        if path.is_file() and not path.name.startswith("._")
        and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    }
    paired = {
        match.resolve() for annotation in folder.glob("*.json")
        if is_valid_labelme_file(annotation, require_image=True)
        for match in [find_matching_image(annotation)] if match is not None
    } & images
    studio_dir = dataset_annotation_dir(folder, STUDIO_ANNOTATIONS_DIR)
    for image in images:
        studio_json = studio_dir / f"{image.stem}.json"
        if not studio_json.is_file():
            continue
        try:
            saved = json.loads(studio_json.read_text(encoding="utf-8"))
            annotations = saved["annotations"]
            if not isinstance(annotations, list):
                raise ValueError("annotations must be a list")
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid Studio annotation {studio_json}: {exc}") from exc
        has_region_or_ok = any(
            isinstance(item, dict) and (
                len(item.get("polygon") or item.get("points") or []) >= 3
                or len(item.get("bbox") or []) == 4
                or item.get("is_normal") or item.get("label") == "OK"
            )
            for item in annotations
        )
        has_mask = bool(saved.get("mask_file")) and (studio_dir / "masks" / f"{image.stem}.png").is_file()
        if has_region_or_ok or has_mask:
            paired.add(image)
        else:
            paired.discard(image)
    return paired


def _has_flat_labelme_annotations(folder: Path) -> bool:
    if any(is_valid_labelme_file(path, require_image=True) for path in folder.glob("*.json")):
        return True
    studio_dir = dataset_annotation_dir(folder, STUDIO_ANNOTATIONS_DIR)
    return studio_dir.is_dir() and any(path.is_file() for path in studio_dir.glob("*.json"))


def _flat_labelme_class_counts(folder: Path, paired_images: set[Path]) -> Dict[str, int]:
    """Count the active source or Studio regions without parsing LabelMe as COCO."""
    source_annotations = {
        match.resolve(): path for path in folder.glob("*.json")
        if is_valid_labelme_file(path, require_image=True)
        for match in [find_matching_image(path)] if match is not None
    }
    studio_dir = dataset_annotation_dir(folder, STUDIO_ANNOTATIONS_DIR)
    classes: Dict[str, int] = {}
    for image in paired_images:
        studio_json = studio_dir / f"{image.stem}.json"
        annotation = studio_json if studio_json.is_file() else source_annotations.get(image)
        if annotation is None:
            continue
        data = json.loads(annotation.read_text(encoding="utf-8"))
        shapes = data.get("annotations", []) if annotation == studio_json else data.get("shapes", [])
        found = False
        for shape in shapes:
            if not isinstance(shape, dict) or shape.get("is_normal") or shape.get("label") == "OK":
                continue
            if not (len(shape.get("polygon") or shape.get("points") or []) >= 2
                    or len(shape.get("bbox") or []) == 4):
                continue
            label = str(shape.get("label") or "defect").strip() or "defect"
            classes[label] = classes.get(label, 0) + 1
            found = True
        if not found and annotation == studio_json and data.get("mask_file"):
            classes["defect"] = classes.get("defect", 0) + 1
    return classes


def _resolve_task_folder(folder: Path, task: Optional[str]) -> Path:
    """Use the same task subfolder for import, split, and training."""
    if task:
        task_folder = folder / task.lower().strip()
        if task_folder.is_dir() and any(path.is_dir() for path in task_folder.iterdir()):
            return task_folder
    return folder


DETECTION_SPLIT_LAYOUT_MESSAGE = (
    "검출 학습에는 images/train, images/val과 데이터 폴더 바로 아래의 "
    "annotations_train.json, annotations_val.json이 필요합니다. / "
    "Detection training requires separate images/train, images/val and root-level "
    "annotations_train.json, annotations_val.json."
)


def _split_capability(task: str, flat_labelme: bool) -> tuple[bool, Optional[str]]:
    """Describe whether the split endpoint changes what this task's loader reads."""
    if task == "classification" or (task in ("segmentation", "detection") and flat_labelme):
        return True, None
    if task == "segmentation":
        return False, (
            "일반 이미지·마스크 세그멘테이션의 재분할은 지원되지 않습니다. "
            "원본 train/val 폴더를 사용하세요. / Re-splitting paired image/mask "
            "segmentation is not supported; use source train/val folders."
        )
    return False, (
        f"{task} 분할은 현재 학습 데이터에 적용되지 않습니다. "
        f"원본 데이터의 train/val/test 구성을 사용하세요. / "
        f"{task} split is not applied by the training loader; use source train/val/test folders."
    )


def _detection_train_val_ready(folder: Path) -> bool:
    train_images = folder / "images" / "train"
    val_images = folder / "images" / "val"
    train_annotations = folder / "annotations_train.json"
    val_annotations = folder / "annotations_val.json"
    return (
        train_images.is_dir() and val_images.is_dir()
        and train_annotations.is_file() and val_annotations.is_file()
        and train_images.resolve() != val_images.resolve()
        and train_annotations.resolve() != val_annotations.resolve()
    )


def _split_labelme_source_groups(
    items: List[Dict[str, Any]], manifest_path: Path,
    train_ratio: float, val_ratio: float, test_ratio: float, seed: int,
) -> Dict[str, List[Dict[str, Any]]]:
    """Keep each original parent directory in one partition when flatten metadata exists."""
    source_group_by_name: Dict[str, str] = {}
    try:
        with manifest_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not {"renamed_filename", "relative_source_path"}.issubset(reader.fieldnames or []):
                raise ValueError("required renamed_filename and relative_source_path columns are missing")
            for row in reader:
                name = (row.get("renamed_filename") or "").strip()
                source = (row.get("relative_source_path") or "").strip().replace("\\", "/")
                relative = PurePosixPath(source)
                if not name or Path(name).name != name or relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2:
                    raise ValueError("each row needs a filename and a relative source parent directory")
                if name in source_group_by_name:
                    raise ValueError(f"duplicate renamed_filename: {name}")
                source_group_by_name[name] = str(relative.parent)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid flatten_manifest.csv: {exc}") from exc

    missing = sorted({Path(item["image_path"]).name for item in items} - source_group_by_name.keys())
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"flatten_manifest.csv has no source group for {len(missing)} labeled image(s), including {missing[0]}",
        )

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in sorted(items, key=lambda item: item["image_path"]):
        groups.setdefault(source_group_by_name[Path(item["image_path"]).name], []).append(item)
    group_names = sorted(groups)
    rng = random.Random(seed)
    rng.shuffle(group_names)
    ratios = (train_ratio, val_ratio, test_ratio)
    active = tuple(index for index, ratio in enumerate(ratios) if ratio > 0)
    if len(group_names) < len(active):
        raise HTTPException(
            status_code=422,
            detail=f"flatten_manifest.csv has {len(group_names)} source groups; at least {len(active)} are needed for the requested split.",
        )

    # A state retains one reproducible assignment for each possible (train, val)
    # image count. This finds the closest achievable ratios with indivisible groups.
    states = {(0, 0): ()}
    for group_name in group_names:
        size = len(groups[group_name])
        next_states = {}
        for (train_count, val_count), assignment in states.items():
            for partition in active:
                key = (train_count + (size if partition == 0 else 0),
                       val_count + (size if partition == 1 else 0))
                next_states.setdefault(key, assignment + (partition,))
        states = next_states

    total = len(items)
    targets = tuple(total * ratio for ratio in ratios)
    feasible = []
    for (train_count, val_count), assignment in states.items():
        counts = (train_count, val_count, total - train_count - val_count)
        if all(counts[index] > 0 for index in active):
            distance = tuple(abs(counts[index] - targets[index]) for index in range(3))
            feasible.append((sum(value * value for value in distance), max(distance), sum(distance),
                             counts, assignment))
    if not feasible:
        raise HTTPException(status_code=422, detail="No nonempty group-preserving split is possible.")
    _, _, _, _, selected = min(feasible)
    result: Dict[str, List[Dict[str, Any]]] = {"train": [], "val": [], "test": []}
    for group_name, partition in zip(group_names, selected):
        result[("train", "val", "test")[partition]].extend(groups[group_name])
    return result


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

    image_files = {p.resolve() for p in folder.iterdir() if p.is_file() and not p.name.startswith("._")
                   and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS}
    paired_images = _paired_labelme_images(folder)
    flat_labelme = _has_flat_labelme_annotations(folder)
    if flat_labelme and req.task not in ("segmentation", "detection"):
        raise HTTPException(
            status_code=422,
            detail="This flat LabelMe NG dataset supports segmentation and detection training. Classification and anomaly training also require task-specific OK data.",
        )

    effective_folder = _resolve_task_folder(folder, req.task)
    if req.task == "detection":
        named_annotations = any(
            (effective_folder / parent / f"annotations_{partition}.json").is_file()
            for parent in ("", "annotations") for partition in ("train", "val")
        )
        if named_annotations and not _detection_train_val_ready(effective_folder):
            raise HTTPException(status_code=422, detail=DETECTION_SPLIT_LAYOUT_MESSAGE)

    try:
        if flat_labelme and req.task == "detection":
            summary = DatasetSummary(task="detection", total_images=len(paired_images),
                                     classes=_flat_labelme_class_counts(folder, paired_images),
                                     split_counts={"train": 0, "val": 0, "test": 0})
        else:
            try:
                summary = inspect_dataset(effective_folder, req.task)
            except ValueError as exc:
                if req.task != "classification" or not str(exc).startswith("Saved split"):
                    raise
                # Preserve the actual inventory so the user can reapply the split.
                # Training still rejects the stale manifest until that succeeds.
                summary = inspect_dataset(effective_folder, req.task, ignore_saved_split=True)
                summary.split_counts = {"train": 0, "val": 0, "test": 0}
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
    unlabeled_images = len(image_files - paired_images) if flat_labelme else 0

    if flat_labelme:
        summary.total_images = len(paired_images)
        if req.task == "segmentation":
            summary.classes = {"defect_mask": len(paired_images)}
        assignments = _read_split_manifest(folder)
        # Paired LabelMe discovery resolves image symlinks to their source,
        # while the saved manifest names files inside the selected folder.
        resolved_assignments = {str(Path(path).resolve()): partition for path, partition in assignments.items()}
        image_keys = {str(p) for p in paired_images}
        valid_partitions = {"train", "val", "test"}
        if image_keys.issubset(resolved_assignments) and all(resolved_assignments[key] in valid_partitions for key in image_keys):
            split_counts = {part: sum(resolved_assignments[key] == part for key in image_keys) for part in valid_partitions}
        else:
            split_counts = {"train": 0, "val": 0, "test": 0}
        train_count = split_counts["train"]
        val_count = split_counts["val"]

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

    split_supported, split_unavailable_reason = _split_capability(req.task, flat_labelme)

    return {
        "status": "success",
        "total_images": summary.total_images,
        "source_images": len(image_files) if flat_labelme else summary.total_images,
        "unlabeled_images": unlabeled_images,
        "classes": summary.classes,
        "split": {
            "train": train_count,
            "val": val_count,
            "test": split_counts.get("test", 0),
        },
        "corrupted_images": corrupted_images,
        "split_supported": split_supported,
        "split_unavailable_reason": split_unavailable_reason,
    }


@router.post("/import-industrial")
def import_industrial_dataset_endpoint(req: IndustrialDatasetImportRequest):
    """
    Ingests real manufacturing inspection datasets using industrial adapters.
    Handles hierarchical classification (OK/**, NG/**), LabelMe annotations,
    polygon rasterization, and flexible anomaly datasets without strict MVTec layout.
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
    flat_labelme = _has_flat_labelme_annotations(folder)
    if req.task == "anomaly" or (req.task == "detection" and not flat_labelme):
        raise HTTPException(
            status_code=422,
            detail=(f"{req.task} 분할은 현재 학습 데이터에 적용되지 않습니다. "
                    f"원본 데이터의 train/val/test 구성을 사용하세요. / "
                    f"{req.task} split is not applied by the training loader; use source train/val/test folders."),
        )
    if req.task == "segmentation" and not flat_labelme:
        raise HTTPException(
            status_code=422,
            detail=("일반 이미지·마스크 세그멘테이션의 재분할은 지원되지 않습니다. "
                    "LabelMe 이미지와 라벨 폴더에서 분할하거나 원본 train/val 폴더를 사용하세요. / "
                    "Re-splitting paired image/mask segmentation is not supported; use flat LabelMe or source train/val folders."),
        )
    if req.task == "classification" and flat_labelme:
        raise HTTPException(status_code=422, detail="이 LabelMe 폴더는 세그멘테이션 분할만 지원합니다. / This LabelMe folder supports segmentation split only.")
    if req.task is None and not flat_labelme and (
        (folder / "images").is_dir() or (folder / "masks").is_dir()
        or any(folder.glob("annotations*.json"))
    ):
        raise HTTPException(
            status_code=422,
            detail="데이터 작업 유형을 지정해야 안전하게 분할할 수 있습니다. / Specify the dataset task before splitting this layout.",
        )
    val_ratio = req.val_ratio if req.val_ratio is not None else 1.0 - req.train_ratio - req.test_ratio
    if val_ratio < 0 or abs(req.train_ratio + val_ratio + req.test_ratio - 1.0) > 1e-6:
        raise HTTPException(status_code=422, detail="Train, validation and test ratios must sum to 1.")
    effective_folder = _resolve_task_folder(folder, req.task)

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

    paired_images = _paired_labelme_images(effective_folder)
    if _has_flat_labelme_annotations(effective_folder):
        items = [item for item in items if Path(item["image_path"]).resolve() in paired_images]

    if not items:
        raise HTTPException(status_code=422, detail="No valid images found to split.")

    seed = req.seed if req.seed is not None else 42
    flatten_manifest = folder / "flatten_manifest.csv"
    if paired_images and flatten_manifest.is_file():
        grouped = _split_labelme_source_groups(
            items, flatten_manifest, req.train_ratio, val_ratio, req.test_ratio, seed,
        )
        train_items, val_items, test_items = grouped["train"], grouped["val"], grouped["test"]
    else:
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
    task: Optional[Literal["classification", "detection", "segmentation", "anomaly"]] = Query(None, description="Dataset task"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    split: Optional[str] = Query(None, description="Filter by train, val, or test"),
    class_name: Optional[str] = Query(None, description="Filter by category"),
):
    """Returns paginated image metadata with thumbnail URLs."""
    target_dir = Path(folder_path).resolve() if folder_path else Path("./datasets/synthetic").resolve()
    if not target_dir.exists():
        return {"total": 0, "limit": limit, "offset": offset, "items": []}

    requested_task = task if isinstance(task, str) else None
    effective_dir = _resolve_task_folder(target_dir, requested_task) if requested_task else target_dir
    if not requested_task and not (target_dir / "train").is_dir():
        for sub in target_dir.iterdir():
            if sub.is_dir() and (sub / "train").is_dir():
                effective_dir = sub
                break

    # Detection and paired segmentation store source images separately from
    # masks/annotations. The gallery must follow the same image root as import.
    image_root = effective_dir / "images"
    task_image_mode = requested_task in ("detection", "segmentation") and image_root.is_dir()
    alternate_segmentation_layout = (
        requested_task == "segmentation" and not task_image_mode
        and (effective_dir / "train" / "images").is_dir()
    )
    if alternate_segmentation_layout:
        task_image_mode = True
    anomaly_mode = requested_task == "anomaly"

    all_images: List[ImageMeta] = []
    assignments = _read_split_manifest(target_dir)
    # Search structured splits
    split_names = ["train", "val", "test"] if not split else [split]
    if anomaly_mode:
        # The anomaly loader derives val/test from test/ when no val/ exists.
        # Use its metadata partition so the gallery shows the actual images
        # counted by import and read during training/evaluation.
        for s_name in split_names:
            if s_name not in ("train", "val", "test"):
                continue
            try:
                samples = AnomalyDataset(root_dir=effective_dir, split=s_name).samples
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=f"Cannot list anomaly {s_name} images: {exc}") from exc
            for image_path, label, _mask_path in samples:
                label_name = "defect" if label else "good"
                if class_name and class_name != label_name:
                    continue
                all_images.append(ImageMeta(
                    image_id=image_path.stem,
                    file_name=image_path.name,
                    file_path=str(image_path),
                    split=s_name,
                    label=label_name,
                    thumbnail_url=f"/api/dataset/thumbnail/{image_path.name}?file_path={image_path}",
                ))
        split_dirs = []
    elif task_image_mode:
        split_dirs = [
            (name, effective_dir / name / "images" if alternate_segmentation_layout else image_root / name)
            for name in split_names
        ]
    else:
        split_dirs = [(name, effective_dir / name) for name in ([] if assignments else split_names)]
    has_physical_splits = any(path.is_dir() for _, path in split_dirs)
    for s_name, s_dir in split_dirs:
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
    if not anomaly_mode and not task_image_mode and not all_images and (assignments or not any((effective_dir / name).is_dir() for name in ("train", "val", "test"))):
        paired_images = _paired_labelme_images(target_dir)
        has_labelme = _has_flat_labelme_annotations(target_dir)
        for f in sorted(effective_dir.rglob("*")):
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
                        split=assigned_split or ("unlabeled" if has_labelme and f.resolve() not in paired_images else "all"),
                        label=c_label,
                        thumbnail_url=f"/api/dataset/thumbnail/{f.name}?file_path={f}",
                    )
                )

    if task_image_mode and not has_physical_splits and not split and not alternate_segmentation_layout:
        # Single-COCO-file layouts have an unsplit images/ directory. Keep
        # those visible while accurately reporting no train/val partition.
        for f in sorted(image_root.iterdir()):
            if f.is_file() and not f.name.startswith("._") and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                all_images.append(ImageMeta(
                    image_id=f.stem,
                    file_name=f.name,
                    file_path=str(f),
                    split="all",
                    label=None,
                    thumbnail_url=f"/api/dataset/thumbnail/{f.name}?file_path={f}",
                ))

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
