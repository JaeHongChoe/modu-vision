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
import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image
from backend.engine.dicom_input import open_source_image
from pydantic import BaseModel, ConfigDict, Field

from backend.engine.dataset_loaders import (
    AnomalyDataset,
    DatasetSummary,
    SUPPORTED_IMAGE_EXTENSIONS,
    inspect_dataset,
    scoped_split_root,
    split_dataset,
    decode_image_file,
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
    labels: List[str] = Field(default_factory=list)
    thumbnail_url: str


def _split_manifest_file(folder: Path) -> Path:
    key = hashlib.sha256(str(folder.resolve()).encode("utf-8")).hexdigest()
    return scoped_split_root(SPLIT_MANIFEST_DIR) / f"{key}.json"


def migrate_legacy_split_manifest(project: Dict[str, Any]) -> Optional[Path]:
    """Copy a global split once; later absence in the project is intentional."""
    source = project.get("source_dataset_dir")
    if not source:
        return None
    source_path = Path(source).expanduser().resolve()
    key = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()
    legacy = SPLIT_MANIFEST_DIR / f"{key}.json"
    target = Path(project["dataset_dir"]) / "splits" / f"{key}.json"
    marker = target.parent / f".legacy-split-migration-{key}.json"
    if marker.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    copied = False
    if not target.exists() and legacy.is_file() and not legacy.is_symlink():
        try:
            payload = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict) and payload.get("folder_path") == str(source_path):
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".legacy-split-", delete=False) as handle:
                temporary = Path(handle.name)
                with legacy.open("rb") as reader:
                    shutil.copyfileobj(reader, handle)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                if not target.exists():
                    os.replace(temporary, target)
                    copied = True
            finally:
                temporary.unlink(missing_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                     prefix=".legacy-split-marker-", delete=False) as handle:
        json.dump({"source_dataset_dir": str(source_path), "copied": copied}, handle)
        temporary_marker = Path(handle.name)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary_marker, marker)
    finally:
        temporary_marker.unlink(missing_ok=True)
    return target if copied else None


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
                or (item.get("type") == "tag" and isinstance(item.get("label"), str)
                    and bool(item["label"].strip()))
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


def _flat_labelme_class_counts(folder: Path, paired_images: set[Path], *, count_images: bool = False) -> Dict[str, int]:
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
        image_labels = set()
        for shape in shapes:
            if not isinstance(shape, dict) or shape.get("is_normal") or shape.get("label") == "OK":
                continue
            if not (len(shape.get("polygon") or shape.get("points") or []) >= 2
                    or len(shape.get("bbox") or []) == 4):
                continue
            label = str(shape.get("label") or "defect").strip() or "defect"
            if not count_images or label not in image_labels:
                classes[label] = classes.get(label, 0) + 1
            image_labels.add(label)
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


def _folder_ids(path: str) -> set:
    """(device, inode) of a folder and of every folder above it: identity, not spelling (case-insensitive volumes)."""
    ids, current = set(), os.path.realpath(path)
    while True:
        try:
            stat = os.stat(current)
            ids.add((stat.st_dev, stat.st_ino))
        except OSError:
            pass
        parent = os.path.dirname(current)
        if parent == current:
            return ids
        current = parent


def _folder_id(path: str) -> tuple:
    try:
        stat = os.stat(path)
        return stat.st_dev, stat.st_ino
    except OSError:
        return None, None


def _contains(parent: str, child: str) -> bool:
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:  # different drives on Windows
        return False


_QUICK_VALIDATION_LIMIT = 201  # this quick inspection samples beyond it; the dataset index validates every image
_QUICK_VALIDATION_FOLDERS = 2000


def _validate_folder_images(folder: Path):
    """Decode every supported image file under the folder in a stable order, following folder links (each real folder
    once) and hidden folders, so the decoded set covers every image a folder-scanned inventory can contain.

    A link to a folder that contains the selection is not followed (it would read the selection's siblings); it makes
    the result partial instead. The walk stops after _QUICK_VALIDATION_LIMIT images or _QUICK_VALIDATION_FOLDERS folders.
    Returns (corrupted, checked, sampled, gaps): gaps name what the walk could not cover.
    """
    corrupted, checked, gaps, seen, folders = [], 0, [], set(), 0
    selection = os.path.realpath(folder)
    above = _folder_ids(selection) - {_folder_id(selection)}  # the selection's parents, by identity
    for root, dirs, files in os.walk(folder, followlinks=True, onerror=lambda error: gaps.append("unreadable folder")):
        real = os.path.realpath(root)
        if real in seen:  # a link back to a folder already walked
            dirs[:] = []
            continue
        if real != selection and (_contains(real, selection) or _folder_id(real) in above):
            gaps.append("a folder link points to a folder that contains the selection")
            dirs[:] = []
            continue
        seen.add(real)
        folders += 1
        if folders > _QUICK_VALIDATION_FOLDERS:
            return corrupted, checked, True, gaps
        dirs.sort()
        for name in sorted(files):
            path = Path(root) / name
            if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                continue
            if checked >= _QUICK_VALIDATION_LIMIT:
                return corrupted, checked, True, gaps
            result = decode_image_file(path)  # every pixel: header checks alone pass truncated JPEG/BMP/TIFF data
            if not result.valid:
                corrupted.append({"file_path": str(path), "error_code": result.error_code, "details": result.details})
            checked += 1
    return corrupted, checked, False, gaps


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
    # An inventory listed by annotation files may name images that are missing or outside the folder; a folder-scanned
    # one is a subset of the image files under the folder, which the validation below decodes.
    listed_inventory = False
    if req.task == "detection":
        named_annotations = any(
            (effective_folder / parent / f"annotations_{partition}.json").is_file()
            for parent in ("", "annotations") for partition in ("train", "val")
        )
        if named_annotations and not _detection_train_val_ready(effective_folder):
            raise HTTPException(status_code=422, detail=DETECTION_SPLIT_LAYOUT_MESSAGE)
        # Every detection inventory except paired LabelMe files is read from a COCO annotation file, which may list
        # images that are missing or outside the folder.
        listed_inventory = not flat_labelme

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
                    listed_inventory = True
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
            summary.classes = _flat_labelme_class_counts(folder, paired_images, count_images=True)
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

    # Saved group assignments select the actual source paths for every task.
    grouped_assignments = _read_split_manifest(folder)
    if grouped_assignments:
        from backend.engine.grouped_dataset_views import source_image_paths
        selected_paths = {str(path) for path in source_image_paths(folder, req.task)}
        if flat_labelme:
            selected_paths &= {str(path) for path in paired_images}
        if selected_paths and selected_paths.issubset(grouped_assignments):
            split_counts = {part:sum(grouped_assignments[path] == part for path in selected_paths) for part in ('train','val','test')}
            train_count, val_count = split_counts['train'], split_counts['val']
            summary.total_images = len(selected_paths)

    # Optional image validation scanning
    corrupted_images, count_checked, sampled, gaps = (
        _validate_folder_images(folder) if req.validate_images else ([], 0, False, []))
    if not req.validate_images:
        scope = "not requested"
    elif sampled:
        scope = (f"sampled: first {_QUICK_VALIDATION_LIMIT} images in folder order" if count_checked >= _QUICK_VALIDATION_LIMIT
                 else f"sampled: stopped after {_QUICK_VALIDATION_FOLDERS} folders ({count_checked} image files decoded)")
    elif gaps:
        scope = f"partial: {count_checked} image files decoded; {'; '.join(sorted(set(gaps)))}"
    elif listed_inventory:
        scope = (f"partial: {count_checked} image files under the folder decoded; the inventory is listed by "
                 "annotation files, which the dataset index validates")
    elif count_checked < summary.total_images:
        scope = f"partial: {count_checked} image files decoded for {summary.total_images} counted images"
    else:
        scope = "all images: every image file under the folder was decoded"

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
        # An empty corrupted list after a sampled check is not a statement about the unchecked images.
        "validation": {"requested": req.validate_images, "checked_images": count_checked,
                       "complete": scope.startswith("all images"), "scope": scope},
        "split_supported": split_supported,
        "split_unavailable_reason": split_unavailable_reason,
    }


@router.post("/import-industrial")
def import_industrial_dataset_endpoint(req: IndustrialDatasetImportRequest):
    """
    Ingests real manufacturing inspection datasets using industrial adapters.
    Handles hierarchical classification (OK/**, NG/**), LabelMe annotations,
    polygon rasterization, and flexible anomaly datasets without a strict anomaly layout.
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


def _class_split_counts(images: List[ImageMeta]) -> dict[str, dict[str, int]]:
    """Count saved image partitions across the complete, unfiltered gallery."""
    counts: dict[str, dict[str, int]] = {}
    for item in images:
        if item.split not in ("train", "val", "test"):
            continue
        for label in dict.fromkeys(item.labels or ([item.label] if item.label else [])):
            partitions = counts.setdefault(label, {"train": 0, "val": 0, "test": 0})
            partitions[item.split] += 1
    return counts


@router.get("/images")
def list_dataset_images(
    folder_path: Optional[str] = Query(None, description="Dataset folder path"),
    task: Optional[Literal["classification", "detection", "segmentation", "anomaly"]] = Query(None, description="Dataset task"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    split: Optional[str] = Query(None, description="Filter by train, val, or test"),
    class_name: Optional[str] = Query(None, description="Filter by category"),
    label_status: Optional[Literal["labeled", "unlabeled"]] = Query(None, description="Filter by active project annotation status"),
):
    """Return image metadata and unfiltered class partition counts before pagination."""
    # Internal task adapters call this function directly, so FastAPI Query
    # defaults must not become active filters outside HTTP dependency parsing.
    split=split if isinstance(split,str) and split!='all' else None
    class_name=class_name if isinstance(class_name,str) and class_name else None
    label_status=label_status if isinstance(label_status,str) and label_status in {'labeled','unlabeled'} else None
    target_dir = Path(folder_path).resolve() if folder_path else Path("./datasets/synthetic").resolve()
    if not target_dir.exists():
        return {"total": 0, "limit": limit, "offset": offset, "items": []}

    requested_task = task if isinstance(task, str) else None
    include_class_splits = requested_task in ("classification", "anomaly") and not (split or class_name or label_status)
    from backend.engine.annotation_storage import request_project_root,scoped_annotation_root
    selected_project=request_project_root()
    if selected_project and (selected_project/'project.json').is_file():
        project=json.loads((selected_project/'project.json').read_text())
        if project.get('source_dataset_dir') and Path(project['source_dataset_dir']).resolve()==target_dir and requested_task:
            from backend.engine.dataset_summary import dataset_summary
            from backend.engine.dataset_metadata import list_metadata
            metadata={r['file_path']:r for r in list_metadata(selected_project,target_dir,scoped_annotation_root(STUDIO_ANNOTATIONS_DIR))}
            summary=dataset_summary(target_dir,requested_task,assignments=_read_split_manifest(target_dir),metadata=metadata)
            all_rows=summary['items']
            filtered=[r for r in all_rows if (not split or r['split']==split)
                      and (not class_name or class_name in r['labels'])
                      and (not label_status or r['label_status']==label_status)]
            paged=[]
            for row in filtered[offset:offset+limit]:
                item=ImageMeta(**row)
                with open_source_image(item.file_path) as opened:item.width,item.height=opened.size
                paged.append(item.model_dump())
            return {'total':len(filtered),'limit':limit,'offset':offset,'items':paged,
                    'class_split_counts':_class_split_counts([ImageMeta(**r) for r in all_rows]) if include_class_splits else None}
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
    paired_images: set[Path] = set()
    has_labelme = False
    assignments = _read_split_manifest(target_dir)
    if assignments and requested_task in {'detection','segmentation','anomaly'}:
        from backend.engine.grouped_dataset_views import source_image_paths, _annotations, is_anomaly_normal
        for image in source_image_paths(target_dir, requested_task):
            partition = assignments.get(str(image))
            if partition is None or (split and split != partition):
                continue
            if requested_task == 'anomaly':
                label = 'good' if is_anomaly_normal(image, target_dir) else image.parent.name
                labels = [label]
            else:
                try:
                    annotations, _ = _annotations(target_dir, image)
                except (ValueError, KeyError, OSError) as exc:
                    raise HTTPException(422, detail=f'Cannot read source labels: {exc}') from exc
                labels = list(dict.fromkeys(a['label'] for a in annotations or [] if a.get('label')))
                label = labels[0] if labels else None
            if class_name and class_name not in labels:
                continue
            if label_status in ('labeled','unlabeled') and bool(label) != (label_status == 'labeled'):
                continue
            with open_source_image(image) as pil:
                width,height=pil.size
            all_images.append(ImageMeta(image_id=image.stem,file_name=image.name,file_path=str(image),width=width,height=height,
                split=partition,label=label,labels=labels,thumbnail_url=f'/api/dataset/thumbnail/{image.name}?file_path={image}'))
        total=len(all_images)
        return {'total':total,'limit':limit,'offset':offset,
                'class_split_counts': _class_split_counts(all_images) if include_class_splits else None,
                'items':[item.model_dump() for item in all_images[offset:offset+limit]]}
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
        studio_dir = dataset_annotation_dir(target_dir, STUDIO_ANNOTATIONS_DIR)
        for f in sorted(effective_dir.rglob("*")):
            if f.is_file() and not f.name.startswith("._") and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                assigned_split = assignments.get(str(f))
                if split and (assigned_split != split or (has_labelme and f.resolve() not in paired_images)):
                    continue
                c_label = None if has_labelme else f.parent.name
                c_labels = [] if c_label is None else [c_label]
                img_w = None
                img_h = None
                studio_json = studio_dir / f"{f.stem}.json"
                json_candidate = studio_json if studio_json.is_file() else f.with_suffix(".json")
                if json_candidate.exists():
                    try:
                        with open(json_candidate, "r", encoding="utf-8") as jf:
                            jd = json.load(jf)
                            img_w = jd.get("image_width", jd.get("imageWidth"))
                            img_h = jd.get("image_height", jd.get("imageHeight"))
                            shapes = jd.get("annotations", []) if json_candidate == studio_json else jd.get("shapes", [])
                            c_labels = list(dict.fromkeys(shape['label'] for shape in shapes
                                if isinstance(shape,dict) and isinstance(shape.get('label'),str) and shape['label']))
                            c_label = c_labels[0] if c_labels else None
                    except Exception:
                        pass
                if has_labelme and f.resolve() not in paired_images:
                    # A project edit can intentionally clear source LabelMe.
                    c_label = None
                    c_labels = []
                if class_name and class_name not in c_labels:
                    continue
                all_images.append(
                    ImageMeta(
                        image_id=f.stem,
                        file_name=f.name,
                        file_path=str(f),
                        width=img_w,
                        height=img_h,
                        split=("unlabeled" if has_labelme and f.resolve() not in paired_images else assigned_split or "all"),
                        label=c_label,
                        labels=c_labels,
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

    if label_status in ("labeled", "unlabeled"):
        all_images = [
            item for item in all_images
            if (
                Path(item.file_path).resolve() in paired_images if has_labelme
                else item.label is not None
            ) == (label_status == "labeled")
        ]

    total = len(all_images)
    paged = all_images[offset : offset + limit]
    for item in paged:
        if item.width is None or item.height is None:
            try:
                with open_source_image(item.file_path) as source_image:
                    item.width, item.height = source_image.size
            except (OSError, ValueError):
                pass
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "class_split_counts": _class_split_counts(all_images) if include_class_splits else None,
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

    from backend.engine.dicom_input import is_dicom, normalized_view
    if is_dicom(candidate_path):
        from backend.engine.annotation_storage import request_project_root
        project_root=request_project_root()
        if project_root is None:raise HTTPException(422,detail='DICOM display requires an active project')
        try:receipt=normalized_view(candidate_path,project_root/'dicom_views')
        except (ValueError,OSError) as exc:raise HTTPException(422,detail=str(exc)) from exc
        return FileResponse(receipt['view_path'],media_type='image/png',headers={'X-Source-SHA256':receipt['source_sha256']})
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


_THUMBNAIL_VERSION = "v3"
_DIGEST_LOCK = threading.Lock()
_DIGESTS: "OrderedDict[tuple, tuple[str, float]]" = OrderedDict()
# An unchanged (device, inode, ctime, mtime, size) only lets a cached digest be reused for a while: POSIX ctime moves on
# every write, but FAT/exFAT/SMB and Windows (where ctime is the creation time) give weaker signals, so the bytes are
# hashed again after the bound. The memo lives in memory: after a restart the first request reads the file once.
_DIGEST_TTL = 60.0 if os.name == "nt" else 600.0
# Source bytes are never held whole by this route: the hash is streamed, and a render of a source up to _COPY_LIMIT
# keeps one private copy (in memory up to _SPOOL_IN_MEMORY, then in an anonymous temporary file) that decides both the
# key and the pixels. A larger source (a TIFF stack, say) is decoded from the file itself and hashed again afterwards;
# a change in between refuses the thumbnail. Decoded pixels are bounded by Pillow's decompression-bomb limit, checked
# from the header; Pillow's WebP reader loads the whole file to read its header, so WebP costs its file size twice.
_COPY_LIMIT = 768 * 1024 * 1024
_WEBP_LIMIT = 256 * 1024 * 1024
_SPOOL_IN_MEMORY = 16 * 1024 * 1024
_CHUNK = 1024 * 1024
_FAILURES: "OrderedDict[tuple, tuple[int, Any, float]]" = OrderedDict()


def _memo_key(path: Path, stat: os.stat_result) -> tuple:
    return (str(path), stat.st_dev, stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns, stat.st_size)


def _memo_get(path: Path, stat: os.stat_result) -> Optional[str]:
    key, now = _memo_key(path, stat), time.monotonic()
    with _DIGEST_LOCK:
        hit = _DIGESTS.pop(key, None)
        if hit is not None and now - hit[1] < _DIGEST_TTL:
            _DIGESTS[key] = hit  # most recently used
            return hit[0]
    return None


def _memo_put(path: Path, before: os.stat_result, digest: str) -> None:
    try:
        after = path.stat()
    except OSError:
        return
    if _memo_key(path, after) != _memo_key(path, before):
        return  # changed while it was read: the digest may describe bytes that are no longer there
    with _DIGEST_LOCK:
        _DIGESTS[_memo_key(path, before)] = (digest, time.monotonic())
        while len(_DIGESTS) > 4096:
            _DIGESTS.popitem(last=False)


def _failure_get(path: Path, stat: os.stat_result) -> Optional[tuple[int, Any]]:
    """A decode failure of these exact bytes (same identity, within the trust bound) is answered without a read."""
    key, now = _memo_key(path, stat), time.monotonic()
    with _DIGEST_LOCK:
        hit = _FAILURES.get(key)
        if hit is not None and now - hit[2] < _DIGEST_TTL:
            return hit[0], hit[1]
        _FAILURES.pop(key, None)
    return None


def _failure_put(path: Path, before: os.stat_result, error: HTTPException) -> None:
    try:
        after = path.stat()
    except OSError:
        return
    if _memo_key(path, after) != _memo_key(path, before):
        return
    with _DIGEST_LOCK:
        _FAILURES[_memo_key(path, before)] = (error.status_code, error.detail, time.monotonic())
        while len(_FAILURES) > 4096:
            _FAILURES.popitem(last=False)


def _decoder_tag(dicom: bool) -> str:
    """Which decoder draws the pixels: identical bytes read as DICOM and as TIFF are different thumbnails."""
    if not dicom:
        return f"pillow-{Image.__version__}"
    try:
        import pydicom
        return f"dicom-{pydicom.__version__}"
    except ImportError:
        return "dicom-unavailable"


def _thumbnail_key(digest: str, size: int, decoder: str) -> str:
    """Content digest + size + thumbnail format version + decoder and its version."""
    return hashlib.sha256(f"{digest}:{size}:{_THUMBNAIL_VERSION}:{decoder}".encode("utf-8")).hexdigest()


def _etag_matches(header: Optional[str], etag: str) -> bool:
    if not header:
        return False
    tags = [tag.strip() for tag in header.split(",")]
    return "*" in tags or any((tag[2:] if tag.startswith("W/") else tag) == etag for tag in tags)


class _ReadFailure(HTTPException):
    """The source could not be read (a share hiccup, a sharing violation): never remembered as a broken image."""


def _source_error(path: Path, exc: BaseException) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail="Image file not found")
    return _ReadFailure(status_code=400, detail=format_error_response(
        "ERR_CORRUPT_IMAGE", details=f"Cannot read image {path}: {exc}"))


def _changed_while_read(path: Path) -> HTTPException:
    return HTTPException(status_code=409, detail=f"{path} changed while its thumbnail was drawn; request it again")


def _hash_source(path: Path, copy=None) -> str:
    """sha256 of the source read in chunks; with ``copy``, the same chunks are kept there for decoding."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
                if copy is None:
                    continue
                try:
                    copy.write(chunk)
                except OSError as exc:  # the backend's own temporary storage, not the source
                    raise HTTPException(status_code=507, detail=(
                        "The backend's temporary storage is full; free space in its temporary folder and request "
                        f"the thumbnail again ({exc})")) from None
                if copy.tell() > _COPY_LIMIT:  # grew past the copy budget while being read
                    raise _changed_while_read(path)
    except OSError as exc:
        raise _source_error(path, exc) from None
    return digest.hexdigest()


def _probe_header(path: Path) -> None:
    """Pillow reads only the header: a file it cannot identify, or a decompression bomb, is refused before any read."""
    try:
        with Image.open(path) as probe:
            probe.size
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image file not found") from None
    except Exception as exc:
        raise HTTPException(status_code=400, detail=format_error_response(
            "ERR_CORRUPT_IMAGE", details=f"Cannot decode image {path}: {exc}")) from None


def _render_jpeg(image: Image.Image, size: int) -> bytes:
    with image:
        image.draft("RGB", (size, size))  # JPEG sources decode at a reduced scale that still covers the size
        rendered = image.convert("RGB")
        rendered.thumbnail((size, size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        rendered.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()


@router.get("/thumbnail/{image_path:path}")
def get_thumbnail(
    image_path: str,
    request: Request,
    file_path: Optional[str] = Query(None),
    size: int = Query(128, ge=32, le=4096),
):
    """A downsampled thumbnail keyed by the content and decoder it was drawn from; the browser revalidates by ETag."""
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
    try:
        stat = candidate_path.stat()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image file not found")
    if stat.st_size == 0:
        raise HTTPException(
            status_code=400,
            detail=format_error_response("ERR_CORRUPT_IMAGE", details=f"Image file is empty (0 bytes): {candidate_path}"),
        )

    def respond(key: str, rendered: Optional[bytes] = None):
        cached = THUMBNAIL_CACHE_DIR / f"{key}.jpg"
        headers = {"Cache-Control": "private, no-cache", "ETag": f'"{key}"'}
        if _etag_matches(request.headers.get("if-none-match"), headers["ETag"]) and (rendered is not None or cached.exists()):
            return Response(status_code=304, headers=headers)
        if rendered is not None:
            return Response(content=rendered, media_type="image/jpeg", headers=headers)
        if not cached.exists():
            return None
        return FileResponse(cached, media_type="image/jpeg", headers=headers)

    from backend.engine.dicom_input import is_dicom, read_dicom
    dicom = is_dicom(candidate_path)
    decoder = _decoder_tag(dicom)
    known = _memo_get(candidate_path, stat)
    if known is not None:
        hit = respond(_thumbnail_key(known, size, decoder))
        if hit is not None:
            return hit  # a warm hit reads no source bytes
    failed = _failure_get(candidate_path, stat)
    if failed is not None:
        raise HTTPException(status_code=failed[0], detail=failed[1])  # the same bytes failed to decode a moment ago

    try:
        return _render_thumbnail(candidate_path, stat, size, dicom, decoder, read_dicom, respond)
    except HTTPException as error:
        if error.status_code == 400 and not isinstance(error, _ReadFailure):
            _failure_put(candidate_path, stat, error)
        raise


def _render_thumbnail(candidate_path: Path, stat: os.stat_result, size: int, dicom: bool, decoder: str, read_dicom,
                      respond):
    if candidate_path.suffix.lower() == ".webp" and stat.st_size > _WEBP_LIMIT:
        # Pillow's WebP reader loads the whole file even to read its header: no memory bound applies to it.
        raise HTTPException(status_code=400, detail=format_error_response(
            "ERR_CORRUPT_IMAGE", details=f"{candidate_path} is a WebP file over {_WEBP_LIMIT} bytes; no thumbnail is drawn"))
    if not dicom:
        _probe_header(candidate_path)  # refused from the header: nothing more is read, so a failure stays cheap
    digest = _hash_source(candidate_path)  # streamed; nothing is kept
    hit = respond(_thumbnail_key(digest, size, decoder))
    if hit is not None:
        _memo_put(candidate_path, stat, digest)
        return hit

    # A render decides the key and the pixels from one read, so a rewrite in between cannot attach new pixels to an old key.
    try:
        if dicom:
            image, metadata = read_dicom(candidate_path)
            digest = metadata["source_sha256"]
            rendered = _render_jpeg(image, size)
        elif stat.st_size > _COPY_LIMIT:
            # Too large to copy: decode from the file, then confirm the bytes did not change while it was read.
            before = digest
            rendered = _render_jpeg(Image.open(candidate_path), size)
            if _hash_source(candidate_path) != before:
                raise _changed_while_read(candidate_path)
        else:
            with tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY) as copy:
                digest = _hash_source(candidate_path, copy)
                copy.seek(0)
                rendered = _render_jpeg(Image.open(copy), size)
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image file not found") from None
    except Exception as e:
        logger.warning("Failed to render thumbnail for %s: %s", candidate_path, e)
        raise HTTPException(
            status_code=400,
            detail=format_error_response("ERR_CORRUPT_IMAGE", details=f"Cannot decode image {candidate_path}: {e}"),
        ) from None
    _memo_put(candidate_path, stat, digest)
    key = _thumbnail_key(digest, size, decoder)
    cached = THUMBNAIL_CACHE_DIR / f"{key}.jpg"
    if not cached.exists():
        staging = THUMBNAIL_CACHE_DIR / f".{key}.{uuid.uuid4().hex}.jpg"
        try:
            staging.write_bytes(rendered)
            try:
                os.replace(staging, cached)  # a reader never sees a half-written thumbnail
            except OSError:
                if not cached.exists():  # Windows refuses to replace a file being served; the same bytes are there
                    raise
        except OSError as e:
            logger.warning("Could not cache the thumbnail of %s: %s", candidate_path, e)  # served from memory below
        finally:
            try:
                staging.unlink(missing_ok=True)
            except OSError as e:  # Windows may refuse while a scanner holds it; the thumbnail is still served
                logger.warning("Could not remove the thumbnail staging file %s: %s", staging, e)
    served = respond(key)
    return served if served is not None else respond(key, rendered)  # the cache file can vanish before it is served


class DatasetArtifactIngestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_name: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.post('/artifacts/ingest')
def ingest_source_artifact(body: DatasetArtifactIngestRequest, request: Request):
    """Copy one registered source file into managed storage; no source/label edits."""
    from backend.api.routes_artifacts import authorize_artifact, get_artifact_store, receipt, storage_errors
    from backend.api.routes_project import get_current_project
    from backend.engine.dataset_fingerprint import source_artifact_identity
    context = authorize_artifact(request, write=True)
    project = get_current_project(request)
    if not project.get('source_dataset_dir'):
        raise HTTPException(409, 'Select a project source dataset before ingesting artifacts')
    with storage_errors():
        try:
            with source_artifact_identity(Path(project['source_dataset_dir']), body.source_name) as (source, digest, size):
                if digest != body.sha256:
                    raise HTTPException(409, 'Source content hash does not match')
                ref = get_artifact_store(request).put_verified(context, source, digest, size, kind='source',
                                                              authorize=lambda: authorize_artifact(request, write=True))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return receipt(context, artifact_ref=ref.model_dump(), state='referenced',
                       source_identity={'version': 'sha256-v1', 'sha256': digest, 'size_bytes': size})
