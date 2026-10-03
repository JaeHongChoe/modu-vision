"""
backend/engine/dataset_loaders.py

Universal Industrial Vision Dataset Loaders and Format Parsers.
Supports:
  1. Multi-Class Classification (Folder-based OK/NG with automatic stratified splitting)
  2. Object Detection (COCO instances JSON & Pascal VOC XML with box sanitization)
  3. Semantic Segmentation (Paired RGB images & single-channel 8-bit PNG masks with NEAREST interpolation)
  4. Unsupervised Anomaly Detection (standard anomaly layout: strict normal-only train split)
  5. Image Health Validation (0-byte, corrupt headers, truncated streams)
  6. Robust Stratified Split Engine with single-sample fallback
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import math
import os
import random
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
from PIL import Image
from backend.engine.dicom_input import open_source_image, is_dicom, DICOM_EXTENSIONS
import torch
from torch.utils.data import DataLoader, Dataset
import torchvision.transforms.functional as TF

logger = logging.getLogger("vision_ai_studio.dataset_loaders")

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"} | DICOM_EXTENSIONS
# MODU_SPLIT_MANIFEST_DIR relocates the saved splits (the tests point it at their own folder).
SPLIT_MANIFEST_DIR = Path(os.environ.get("MODU_SPLIT_MANIFEST_DIR") or Path.home() / ".modu_vision" / "splits")
_REQUEST_SPLIT_ROOT: ContextVar[Optional[Path]] = ContextVar("project_split_root", default=None)


def set_request_split_root(path: Path) -> Token:
    return _REQUEST_SPLIT_ROOT.set(Path(path).resolve())


def reset_request_split_root(token: Token) -> None:
    _REQUEST_SPLIT_ROOT.reset(token)


def scoped_split_root(default: Path) -> Path:
    return _REQUEST_SPLIT_ROOT.get() or Path(default)


@contextmanager
def split_root_scope(path: Optional[str | Path]):
    token = set_request_split_root(Path(path)) if path is not None else None
    try:
        yield
    finally:
        if token is not None:
            reset_request_split_root(token)


def _classification_split_assignments(root: Path) -> Optional[Dict[str, str]]:
    """Read the split selected in Step 1, if one was saved for this folder."""
    root = root.resolve()
    # Generated datasets place task data in selected/classification while the
    # source folder selected in Step 1 owns the saved split manifest.
    candidate_roots = (root, root.parent) if root.name in {"classification", "detection", "segmentation", "anomaly"} else (root,)
    for selected_root in candidate_roots:
        key = hashlib.sha256(str(selected_root).encode("utf-8")).hexdigest()
        manifest = scoped_split_root(SPLIT_MANIFEST_DIR) / f"{key}.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("folder_path") != str(selected_root) or not isinstance(data.get("assignments"), dict):
                raise ValueError("folder path or assignments are invalid")
            assignments = {}
            for relative, partition in data["assignments"].items():
                path = Path(relative)
                if path.is_absolute() or ".." in path.parts or partition not in {"train", "val", "test"}:
                    raise ValueError("an image path or partition is invalid")
                assignments[str(selected_root / path)] = partition
            from backend.engine.dataset_usage import unused_image_paths
            unused=unused_image_paths(selected_root)
            return {path:partition for path,partition in assignments.items() if path not in unused}
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"Saved split is invalid for {selected_root}; apply the split again: {exc}") from exc
    return None


# ============================================================================
# Coordinate Data Models & Bounding Box Utilities
# ============================================================================

@dataclass
class BoundingBox:
    """Bounding box with dual pixel and normalized coordinate representations."""
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    category_id: int
    category_name: str

    def sanitize(self, img_width: int, img_height: int) -> Optional["BoundingBox"]:
        """
        Clamps coordinates to image bounds, swaps inverted coordinates,
        and rejects degenerate boxes with zero/negative area.
        """
        x1 = max(0.0, min(float(self.xmin), float(img_width)))
        y1 = max(0.0, min(float(self.ymin), float(img_height)))
        x2 = max(0.0, min(float(self.xmax), float(img_width)))
        y2 = max(0.0, min(float(self.ymax), float(img_height)))

        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1

        # Reject degenerate boxes with width or height < 1 pixel
        if (x2 - x1) < 1.0 or (y2 - y1) < 1.0:
            return None

        return BoundingBox(
            xmin=x1, ymin=y1, xmax=x2, ymax=y2,
            category_id=self.category_id,
            category_name=self.category_name,
        )

    def to_voc(self) -> List[float]:
        """Return [xmin, ymin, xmax, ymax] pixel coordinates."""
        return [self.xmin, self.ymin, self.xmax, self.ymax]

    def to_coco(self) -> List[float]:
        """Return [x, y, width, height] pixel coordinates."""
        return [self.xmin, self.ymin, self.xmax - self.xmin, self.ymax - self.ymin]

    def to_normalized(self, img_width: int, img_height: int) -> List[float]:
        """Return [xmin_norm, ymin_norm, xmax_norm, ymax_norm] in [0.0, 1.0]."""
        w = max(1.0, float(img_width))
        h = max(1.0, float(img_height))
        return [
            max(0.0, min(1.0, self.xmin / w)),
            max(0.0, min(1.0, self.ymin / h)),
            max(0.0, min(1.0, self.xmax / w)),
            max(0.0, min(1.0, self.ymax / h)),
        ]

    @classmethod
    def from_coco(
        cls,
        bbox: Sequence[float],
        category_id: int,
        category_name: str,
    ) -> "BoundingBox":
        """Construct from COCO [x, y, width, height]."""
        x, y, w, h = bbox[:4]
        return cls(
            xmin=float(x),
            ymin=float(y),
            xmax=float(x + w),
            ymax=float(y + h),
            category_id=category_id,
            category_name=category_name,
        )


@dataclass
class DatasetSummary:
    """Statistical summary matching REST API /api/dataset/import contract."""
    task: str
    total_images: int
    classes: Dict[str, int]
    split_counts: Dict[str, int]
    sample_size: Optional[Tuple[int, int]] = None


# ============================================================================
# Image Health & File Validation
# ============================================================================

@dataclass
class ValidationResult:
    """File health status for industrial image validation."""
    valid: bool
    error_code: str
    dimensions: Optional[Tuple[int, int]] = None
    details: str = ""


def validate_image_file(file_path: Union[str, Path]) -> ValidationResult:
    """
    Zero-cost file integrity, magic bytes, and decodability validator.
    Detects 0-byte files, corrupt headers, and truncated bitstreams.
    """
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return ValidationResult(valid=False, error_code="FILE_NOT_FOUND", details="File does not exist")

    size = p.stat().st_size
    if size == 0:
        return ValidationResult(valid=False, error_code="ZERO_BYTE", details="File size is 0 bytes")

    if is_dicom(p):
        try:
            with open_source_image(p) as image: dimensions = image.size
            return ValidationResult(valid=True, error_code="OK", dimensions=dimensions, details="Valid DICOM native frame")
        except (ValueError, OSError) as exc:
            return ValidationResult(valid=False, error_code="DICOM_DECODE_ERROR", details=str(exc))

    # Magic byte checks
    try:
        with open(p, "rb") as f:
            header = f.read(16)
    except Exception as e:
        return ValidationResult(valid=False, error_code="READ_ERROR", details=str(e))

    is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpg = header.startswith(b"\xff\xd8\xff")
    is_bmp = header.startswith(b"BM")
    is_tiff = header.startswith(b"II*\x00") or header.startswith(b"MM\x00*")
    is_webp = header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WEBP"

    if not (is_png or is_jpg or is_bmp or is_tiff or is_webp):
        return ValidationResult(valid=False, error_code="CORRUPT_HEADER", details="Header magic bytes do not match standard formats")

    # Decodability check with PIL
    try:
        with Image.open(p) as im:
            im.verify()
        with Image.open(p) as im:
            w, h = im.size
    except Exception as e:
        return ValidationResult(valid=False, error_code="CORRUPT_HEADER", details=str(e))

    if w < 16 or h < 16:
        return ValidationResult(valid=False, error_code="DIMENSION_TOO_SMALL", dimensions=(w, h), details="Image dimensions smaller than 16x16")

    return ValidationResult(valid=True, error_code="OK", dimensions=(w, h), details="Valid image")


def decode_image_file(file_path: Union[str, Path]) -> ValidationResult:
    """validate_image_file, then a full pixel decode.

    Pillow's verify() checks PNG checksums but decodes no JPEG, BMP or TIFF pixel data, so a truncated or corrupt
    bitstream passes the header checks; only decoding every pixel shows it (DECODE_ERROR). DICOM pixel data is already
    decoded by the header check. Decoded pixels stay within Pillow's decompression-bomb limit.
    """
    result = validate_image_file(file_path)
    if not result.valid or is_dicom(Path(file_path)):
        return result
    try:
        with Image.open(file_path) as image:
            image.load()
    except Exception as exc:
        return ValidationResult(valid=False, error_code="DECODE_ERROR", dimensions=result.dimensions, details=str(exc))
    return result


# ============================================================================
# Stratified Split Engine
# ============================================================================

def split_dataset(
    items: List[Dict[str, Any]],
    train_ratio: float = 0.8,
    val_ratio: float = 0.2,
    seed: int = 42,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Partitions items with stratified defect class distribution.
    CRITICAL CORNER CASE: If any rare class has < 2 samples, gracefully
    falls back to unstratified shuffle to prevent scikit-learn/split collapse.
    """
    if not items:
        return {"train": [], "val": []}

    rng = np.random.default_rng(seed)

    class_groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        lbl = str(it.get("label", "default"))
        class_groups.setdefault(lbl, []).append(it)

    has_single_item = any(len(grp) < 2 for grp in class_groups.values())

    if has_single_item:
        shuffled = list(items)
        rng.shuffle(shuffled)
        n_train = max(1, int(round(len(shuffled) * (train_ratio / (train_ratio + val_ratio)))))
        return {
            "train": shuffled[:n_train],
            "val": shuffled[n_train:],
        }

    train_set: List[Dict[str, Any]] = []
    val_set: List[Dict[str, Any]] = []

    for lbl, grp in class_groups.items():
        shuffled = list(grp)
        rng.shuffle(shuffled)
        n_train = max(1, int(round(len(shuffled) * (train_ratio / (train_ratio + val_ratio)))))
        train_set.extend(shuffled[:n_train])
        val_set.extend(shuffled[n_train:])

    return {"train": train_set, "val": val_set}


# ============================================================================
# Helper: Double Extension Sanitation & Image Reading
# ============================================================================

def sanitize_file_stem(path_or_name: Union[str, Path]) -> str:
    """
    Normalizes filenames containing double extensions (e.g. `diag_00.jpg.jpg`)
    returning the sanitized stem without duplicate suffix artifacts.
    """
    name = Path(path_or_name).name
    for ext in SUPPORTED_IMAGE_EXTENSIONS:
        double_ext = f"{ext}{ext}"
        if name.lower().endswith(double_ext):
            name = name[:-len(ext)]
            break

    stem = Path(name).stem
    if any(stem.lower().endswith(ext) for ext in SUPPORTED_IMAGE_EXTENSIONS):
        stem = Path(stem).stem
    return stem


def _read_image_rgb(path: Union[str, Path], bg_color: str = "white") -> np.ndarray:
    """
    Reads image safely even when paths contain Unicode, spaces, or Korean characters.
    Industrial Preprocessing Safeguards:
      1. Converts 16-bit Grayscale/TIFF (I;16, I, L, float) to 8-bit dynamic range without clipping.
      2. Converts RGBA/LA images with clean background compositing (white by default, or black).
      3. Handles single-channel images by replicating into 3-channel RGB.
      4. Always returns uint8 RGB ndarray of shape (H, W, 3).
    """
    p_str = str(path)
    with open_source_image(p_str) as im:
        # Check for 16-bit / 32-bit single-channel modes (e.g. industrial TIFF/AOI)
        if im.mode in ("I;16", "I;16L", "I;16B", "I", "F"):
            arr = np.array(im)
            if arr.dtype in (np.uint16, np.int32, np.int64, np.float32, np.float64):
                min_v = float(np.min(arr))
                max_v = float(np.max(arr))
                if max_v > min_v:
                    norm = ((arr.astype(np.float32) - min_v) / (max_v - min_v) * 255.0).clip(0, 255).astype(np.uint8)
                else:
                    norm = np.clip(arr, 0, 255).astype(np.uint8)
            else:
                norm = arr.astype(np.uint8)
            return np.stack([norm, norm, norm], axis=-1)

        # Single-channel 8-bit grayscale
        if im.mode == "L":
            arr = np.array(im, dtype=np.uint8)
            return np.stack([arr, arr, arr], axis=-1)

        # RGBA / LA images with alpha transparency (e.g. isolated wafer chucks)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im_rgba = im.convert("RGBA")
            bg_rgb = (0, 0, 0) if bg_color == "black" else (255, 255, 255)
            background = Image.new("RGB", im_rgba.size, bg_rgb)
            background.paste(im_rgba, mask=im_rgba.split()[3])
            return np.array(background, dtype=np.uint8)

        # Standard conversion for RGB, CMYK, P, etc.
        rgb = im.convert("RGB")
        return np.array(rgb, dtype=np.uint8)


# ============================================================================
# Task 1: Multi-Class Classification Dataset
# ============================================================================

class ClassificationDataset(Dataset):
    """
    Folder-based industrial defect classification dataset.
    Structure:
      root_dir/{class_name}/*.png
      or root_dir/{train,val}/{class_name}/*.png
    Yields:
      (image_tensor: Tensor[3, H, W] float32, label_idx: int)
    """

    def __init__(
        self,
        root_dir: Union[str, Path],
        split: Optional[str] = None,
        val_split: float = 0.2,
        seed: int = 42,
        transform: Optional[Callable] = None,
        image_size: Optional[Tuple[int, int]] = None,
        max_dim: int = 1600,
        ignore_saved_split: bool = False,
    ):
        self.root_dir = Path(root_dir)
        self.split = split
        self.val_split = val_split
        self.seed = seed
        self.transform = transform
        self.image_size = image_size
        self.max_dim = max_dim

        train_dir = self.root_dir / "train"
        val_dir = self.root_dir / "val"
        test_dir = self.root_dir / "test"

        assignments = None if ignore_saved_split else _classification_split_assignments(self.root_dir)
        if assignments is not None:
            self.split_basis = "saved_manifest"
            source_dirs = [d for d in (train_dir, val_dir, test_dir) if d.is_dir()] or [self.root_dir]
            folder_samples = [self._load_from_folder(source_dir) for source_dir in source_dirs]
            class_names = {name for _, names, _ in folder_samples for name in names}
            self.classes = sorted(
                class_names,
                key=lambda name: (0 if name.lower() in ("ok", "good", "normal", "pass", "정상_ok", "정상") else 1,
                                  name.lower()),
            )
            self.class_to_idx = {name: idx for idx, name in enumerate(self.classes)}
            all_samples = [
                (path, self.class_to_idx[names[label_idx]])
                for samples, names, _ in folder_samples for path, label_idx in samples
            ]
            image_paths = {str(path) for path, _ in all_samples}
            if image_paths != set(assignments):
                raise ValueError("Saved split no longer matches classification images; apply the split again")
            self.samples = [
                sample for sample in all_samples
                if split is None or assignments[str(sample[0])] == split
            ]
            return

        if split and (train_dir.is_dir() or val_dir.is_dir() or test_dir.is_dir()):
            self.split_basis = "folders"
            available = [directory for directory in (train_dir, val_dir, test_dir) if directory.is_dir()]
            names = {name for directory in available for name in self._load_from_folder(directory)[1]}
            self.classes = sorted(
                names,
                key=lambda name: (0 if name.lower() in ("ok", "good", "normal", "pass", "정상_ok", "정상") else 1,
                                  name.lower()),
            )
            self.class_to_idx = {name: idx for idx, name in enumerate(self.classes)}
            target_dir = train_dir if split == "train" else (val_dir if split == "val" else test_dir)
            if target_dir.is_dir():
                local_samples, local_classes, _ = self._load_from_folder(target_dir)
                self.samples = [(path, self.class_to_idx[local_classes[label_idx]]) for path, label_idx in local_samples]
            else:
                self.samples = []
        else:
            self.split_basis = "automatic"
            self.samples, self.classes, self.class_to_idx = self._load_from_folder(self.root_dir)
            if split and split in ["train", "val"]:
                self.samples = self._split_samples(self.samples, split, val_split, seed)
            elif split == "test":
                # Automatic train/validation splitting does not declare a test set.
                self.samples = []

    @staticmethod
    def _load_from_folder(folder: Path) -> Tuple[List[Tuple[Path, int]], List[str], Dict[str, int]]:
        from backend.engine.dataset_usage import unused_image_paths
        unused=unused_image_paths(folder)
        class_names = [d.name for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")]

        # Deterministic sort: 'OK', 'good', 'normal', 'pass' sorted first (index 0)
        def sort_key(name: str) -> Tuple[int, str]:
            low = name.lower()
            if low in ("ok", "good", "normal", "pass", "정상_ok", "정상"):
                return (0, low)
            return (1, low)

        class_names.sort(key=sort_key)
        class_to_idx = {name: idx for idx, name in enumerate(class_names)}

        samples: List[Tuple[Path, int]] = []
        for cname in class_names:
            cidx = class_to_idx[cname]
            cdir = folder / cname
            for p in sorted(cdir.glob("*")):
                if p.is_file() and str(p.resolve()) not in unused and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    samples.append((p, cidx))

        return samples, class_names, class_to_idx

    @staticmethod
    def _split_samples(
        samples: List[Tuple[Path, int]],
        split: str,
        val_split: float,
        seed: int,
    ) -> List[Tuple[Path, int]]:
        by_class: Dict[int, List[Tuple[Path, int]]] = {}
        for s in samples:
            by_class.setdefault(s[1], []).append(s)

        train_samples: List[Tuple[Path, int]] = []
        val_samples: List[Tuple[Path, int]] = []

        rng = random.Random(seed)
        for cidx, items in by_class.items():
            shuffled = list(items)
            rng.shuffle(shuffled)
            val_count = max(1, int(len(shuffled) * val_split)) if len(shuffled) > 1 else 0
            val_samples.extend(shuffled[:val_count])
            train_samples.extend(shuffled[val_count:])

        return train_samples if split == "train" else val_samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, int]:
        img_path, label_idx = self.samples[idx]
        rgb = _read_image_rgb(img_path)
        h, w = rgb.shape[:2]

        if self.image_size:
            rgb = cv2.resize(rgb, self.image_size, interpolation=cv2.INTER_LINEAR)
        elif self.max_dim and max(h, w) > self.max_dim:
            scale = self.max_dim / float(max(h, w))
            new_w = max(16, int(round(w * scale)))
            new_h = max(16, int(round(h * scale)))
            rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        tensor = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0
        if self.transform:
            tensor = self.transform(tensor)

        return tensor, label_idx


# ============================================================================
# Task 2: Object Detection Dataset
# ============================================================================

def detection_collate_fn(batch: List[Tuple[torch.Tensor, Dict[str, torch.Tensor], Any]]):
    """Custom collate function handling variable-length bounding boxes."""
    return tuple(zip(*batch))


class DetectionDataset(Dataset):
    """
    Industrial defect bounding-box detection dataset supporting COCO JSON annotations.
    Sanitizes degenerate and inverted bounding boxes.
    Yields:
      (image_tensor: Tensor[3, H, W] float32, target: Dict[str, Tensor])
    """

    def __init__(
        self,
        images_dir: Optional[Union[str, Path]] = None,
        annotation_file: Optional[Union[str, Path]] = None,
        root_dir: Optional[Union[str, Path]] = None,
        transform: Optional[Callable] = None,
        image_size: Optional[Tuple[int, int]] = None,
        max_dim: int = 1600,
        class_names: Optional[Sequence[str]] = None,
        annotation_data: Optional[Dict[str, Any]] = None,
    ):
        self.transform = transform
        self.image_size = image_size
        self.max_dim = max_dim

        if root_dir is not None:
            r = Path(root_dir)
            if images_dir is None:
                images_dir = r / "images" if (r / "images").exists() else r
            if annotation_file is None:
                annos = [
                    f for f in (list(r.glob("*.json")) + list(r.glob("annotations/*.json")))
                    if f.name.lower() not in {
                        "all_inspection_progress.json",
                        "audit_progress.json",
                        "fail_records.json",
                        "week_audit_report.json",
                    }
                ]
                annotation_file = annos[0] if annos else None


        if images_dir is None or (annotation_file is None and annotation_data is None):
            raise ValueError("images_dir and annotation_file must be specified for DetectionDataset")

        self.images_dir = Path(images_dir)
        self.annotation_file = Path(annotation_file) if annotation_file is not None else None

        if annotation_data is None:
            with open(self.annotation_file, "r", encoding="utf-8") as f:
                coco_data = json.load(f)
        else:
            coco_data = annotation_data

        raw_categories = coco_data.get("categories", [])
        if not isinstance(raw_categories, list) or not raw_categories:
            raise ValueError("COCO detection annotations require at least one category")
        original_categories = {int(c["id"]): str(c["name"]) for c in raw_categories}
        if len(original_categories) != len(raw_categories):
            raise ValueError("COCO detection category IDs must be unique")
        # Torchvision reserves model class 0 for background. COCO IDs need
        # not be contiguous or start at 1. Reuse the training class order for
        # validation and evaluation, even if their COCO files omit a class.
        names = list(class_names) if class_names is not None else [
            original_categories[original_id] for original_id in sorted(original_categories)
        ]
        if len(set(names)) != len(names):
            raise ValueError("COCO detection category names must be unique")
        name_to_dense = {name: index for index, name in enumerate(names, start=1)}
        unknown_names = set(original_categories.values()) - name_to_dense.keys()
        if unknown_names:
            raise ValueError(f"COCO detection has categories absent from the training class mapping: {sorted(unknown_names)}")
        self.original_to_dense = {
            original_id: name_to_dense[name]
            for original_id, name in original_categories.items()
        }
        self.categories = {
            dense_id: name for name, dense_id in name_to_dense.items()
        }
        from backend.engine.dataset_usage import unused_image_paths
        unused=unused_image_paths(self.images_dir)
        self.images = {img["id"]: img for img in coco_data.get("images", [])
                       if str((self.images_dir/img['file_name']).resolve()) not in unused}

        self.img_to_annos: Dict[int, List[Dict[str, Any]]] = {img_id: [] for img_id in self.images}
        for anno in coco_data.get("annotations", []):
            iid = anno["image_id"]
            if iid in self.img_to_annos:
                self.img_to_annos[iid].append(anno)

        self.image_ids = sorted(list(self.images.keys()))

    def __len__(self) -> int:
        return len(self.image_ids)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, Dict[str, torch.Tensor]]:
        img_id = self.image_ids[idx]
        img_meta = self.images[img_id]
        img_path = self.images_dir / img_meta["file_name"]

        rgb = _read_image_rgb(img_path)
        h, w = rgb.shape[:2]

        target_w, target_h = w, h
        if self.image_size:
            target_w, target_h = self.image_size
            scale_x = target_w / float(w)
            scale_y = target_h / float(h)
            rgb = cv2.resize(rgb, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        elif self.max_dim and max(h, w) > self.max_dim:
            scale = self.max_dim / float(max(h, w))
            target_w = max(16, int(round(w * scale)))
            target_h = max(16, int(round(h * scale)))
            scale_x = target_w / float(w)
            scale_y = target_h / float(h)
            rgb = cv2.resize(rgb, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        else:
            scale_x = 1.0
            scale_y = 1.0

        tensor = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0

        raw_annos = self.img_to_annos.get(img_id, [])
        valid_boxes: List[List[float]] = []
        valid_labels: List[int] = []
        valid_areas: List[float] = []
        valid_norms: List[List[float]] = []
        valid_rotated: List[List[float]] = []
        rotated_present: List[bool] = []
        rotated_authority: List[bool] = []
        valid_rotated_polygons: List[List[List[float]]] = []
        valid_directions: List[float] = []
        direction_present: List[bool] = []

        for a in raw_annos:
            x, y, bw, bh = a["bbox"]
            # Bounding box sanitization: reject negative width/height
            if bw <= 0 or bh <= 0:
                continue

            x1 = min(float(target_w), max(0.0, float(x) * scale_x))
            y1 = min(float(target_h), max(0.0, float(y) * scale_y))
            x2 = min(float(target_w), max(0.0, float(x + bw) * scale_x))
            y2 = min(float(target_h), max(0.0, float(y + bh) * scale_y))
            if x2 <= x1 or y2 <= y1:
                continue

            # Inverted coordinate auto-swap
            if x2 < x1:
                x1, x2 = x2, x1
            if y2 < y1:
                y1, y2 = y2, y1

            # Microscopic flaw support: ensure minimum 1px dimension
            if (x2 - x1) < 1.0:
                x2 = min(float(target_w), x1 + 1.0)
            if (y2 - y1) < 1.0:
                y2 = min(float(target_h), y1 + 1.0)

            valid_boxes.append([x1, y1, x2, y2])
            original_category = int(a["category_id"])
            if original_category not in self.original_to_dense:
                raise ValueError(f"COCO annotation references unknown category ID: {original_category}")
            valid_labels.append(self.original_to_dense[original_category])
            valid_areas.append(float((x2 - x1) * (y2 - y1)))
            valid_norms.append([x1 / target_w, y1 / target_h, x2 / target_w, y2 / target_h])
            rotated = a.get("rotated_bbox")
            if rotated is not None:
                if len(rotated) != 5 or not all(math.isfinite(float(value)) for value in rotated) or rotated[2] <= 0 or rotated[3] <= 0:
                    raise ValueError("Invalid rotated box target")
                cx, cy, box_w, box_h, angle = rotated
                radians = math.radians(angle)
                corners = [[(cx + dx * math.cos(radians) - dy * math.sin(radians)) * scale_x,
                            (cy + dx * math.sin(radians) + dy * math.cos(radians)) * scale_y]
                           for dx, dy in [(-box_w/2, -box_h/2), (box_w/2, -box_h/2),
                                          (box_w/2, box_h/2), (-box_w/2, box_h/2)]]
                valid_rotated_polygons.append(corners)
                # Anisotropic resizing preserves exact quadrilateral vertices.
                # Only uniform scaling or axis-aligned source edges preserve a
                # rectangular OBB. Even max_dim rounding is accounted for.
                rectangular = scale_x == scale_y or angle % 90 == 0
                if rectangular:
                    width_scale, height_scale = ((scale_y, scale_x) if angle % 180 == 90 else (scale_x, scale_y))
                    valid_rotated.append([cx * scale_x, cy * scale_y, box_w * width_scale, box_h * height_scale, angle])
                else:
                    valid_rotated.append([0, 0, 0, 0, 0])
                rotated_authority.append(rectangular)
                xs, ys = zip(*corners)
                x1, y1 = max(0, min(xs)), max(0, min(ys))
                x2, y2 = min(target_w, max(xs)), min(target_h, max(ys))
                valid_boxes[-1] = [x1, y1, x2, y2]
                valid_areas[-1] = (x2 - x1) * (y2 - y1)
                valid_norms[-1] = [x1 / target_w, y1 / target_h, x2 / target_w, y2 / target_h]
            else:
                valid_rotated.append([0, 0, 0, 0, 0])
                valid_rotated_polygons.append([[0, 0]] * 4)
                rotated_authority.append(False)
            rotated_present.append(rotated is not None)
            direction = a.get("direction_deg")
            if direction is not None and (not math.isfinite(float(direction)) or not 0 <= float(direction) < 360):
                raise ValueError("Direction target must be finite degrees in [0,360)")
            if direction is not None:
                radians = math.radians(float(direction))
                direction = math.degrees(math.atan2(scale_y * math.sin(radians), scale_x * math.cos(radians))) % 360
            valid_directions.append(float(direction) if direction is not None else 0)
            direction_present.append(direction is not None)

        if len(valid_boxes) > 0:
            boxes_t = torch.tensor(valid_boxes, dtype=torch.float32)
            labels_t = torch.tensor(valid_labels, dtype=torch.int64)
            areas_t = torch.tensor(valid_areas, dtype=torch.float32)
            iscrowd_t = torch.zeros(len(valid_boxes), dtype=torch.int64)
            norm_t = torch.tensor(valid_norms, dtype=torch.float32)
        else:
            boxes_t = torch.zeros((0, 4), dtype=torch.float32)
            labels_t = torch.zeros((0,), dtype=torch.int64)
            areas_t = torch.zeros((0,), dtype=torch.float32)
            iscrowd_t = torch.zeros((0,), dtype=torch.int64)
            norm_t = torch.zeros((0, 4), dtype=torch.float32)

        target = {
            "boxes": boxes_t,
            "labels": labels_t,
            "image_id": torch.tensor([img_id], dtype=torch.int64),
            "area": areas_t,
            "iscrowd": iscrowd_t,
            "boxes_normalized": norm_t,
        }
        # Optional native metadata is retained beside the AABB envelope. Validity
        # masks distinguish missing targets in mixed legacy annotation sets.
        if any(rotated_present):
            target["rotated_boxes"] = torch.tensor(valid_rotated, dtype=torch.float32)
            target["rotated_boxes_valid"] = torch.tensor(rotated_authority, dtype=torch.bool)
            target["rotated_polygons"] = torch.tensor(valid_rotated_polygons, dtype=torch.float32)
            target["rotated_polygons_valid"] = torch.tensor(rotated_present, dtype=torch.bool)
        if any(direction_present):
            target["direction_deg"] = torch.tensor(valid_directions, dtype=torch.float32)
            target["direction_valid"] = torch.tensor(direction_present, dtype=torch.bool)

        if self.transform:
            from backend.engine.augmentations import apply_sample_transform
            sample = apply_sample_transform(self.transform, tensor, target, task="detection")
            tensor, target = sample.image, sample.targets

        return tensor, target


# ============================================================================
# Task 3: Semantic Segmentation Dataset
# ============================================================================

class SegmentationDataset(Dataset):
    """
    Industrial semantic segmentation dataset loading RGB images and single-channel PNG masks.
    Enforces NEAREST neighbor interpolation on masks to strictly preserve discrete class IDs.
    Yields:
      (image_tensor: Tensor[3, H, W] float32, mask_tensor: Tensor[H, W] int64)
    """

    def __init__(
        self,
        images_dir: Optional[Union[str, Path]] = None,
        masks_dir: Optional[Union[str, Path]] = None,
        root_dir: Optional[Union[str, Path]] = None,
        transform: Optional[Callable] = None,
        image_size: Optional[Tuple[int, int]] = None,
        max_dim: int = 1600,
    ):
        self.transform = transform
        self.image_size = image_size
        self.max_dim = max_dim

        if root_dir is not None:
            r = Path(root_dir)
            if images_dir is None:
                images_dir = r / "images" if (r / "images").exists() else r
            if masks_dir is None:
                masks_dir = r / "masks" if (r / "masks").exists() else r

        if images_dir is None or masks_dir is None:
            raise ValueError("images_dir and masks_dir must be specified for SegmentationDataset")

        self.images_dir = Path(images_dir)
        self.masks_dir = Path(masks_dir)

        self.samples: List[Tuple[Path, Path]] = []
        from backend.engine.dataset_usage import unused_image_paths
        unused=unused_image_paths(self.images_dir)
        for img_p in sorted(self.images_dir.glob("*")):
            if str(img_p.resolve()) in unused:continue
            if img_p.is_file() and (img_p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS or img_p.name.lower().endswith(".jpg.jpg")):
                clean_stem = sanitize_file_stem(img_p)
                raw_stem = img_p.stem
                cand_stems = [clean_stem] if clean_stem == raw_stem else [clean_stem, raw_stem]

                found = False
                for s in cand_stems:
                    for cand_name in [f"{s}.png", f"{s}_mask.png", f"{s}.mask.png"]:
                        cand_mask = self.masks_dir / cand_name
                        if cand_mask.exists():
                            self.samples.append((img_p, cand_mask))
                            found = True
                            break
                    if found:
                        break

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        img_p, mask_p = self.samples[idx]
        rgb = _read_image_rgb(img_p)
        h, w = rgb.shape[:2]

        with Image.open(str(mask_p)) as m_im:
            mask_l = m_im.convert("L")
            mask_np = np.array(mask_l)

        # Automatic mask dimension alignment
        if mask_np.shape != (h, w):
            mask_np = cv2.resize(mask_np, (w, h), interpolation=cv2.INTER_NEAREST)

        if self.image_size:
            rgb = cv2.resize(rgb, self.image_size, interpolation=cv2.INTER_LINEAR)
            mask_np = cv2.resize(mask_np, self.image_size, interpolation=cv2.INTER_NEAREST)
        elif self.max_dim and max(h, w) > self.max_dim:
            scale = self.max_dim / float(max(h, w))
            new_w = max(16, int(round(w * scale)))
            new_h = max(16, int(round(h * scale)))
            rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            mask_np = cv2.resize(mask_np, (new_w, new_h), interpolation=cv2.INTER_NEAREST)

        # Automatic binary remapping: if mask only contains {0, 255}, remap 255 -> 1
        u_vals = np.unique(mask_np)
        if set(u_vals).issubset({0, 255}):
            mask_np = np.where(mask_np == 255, 1, 0).astype(np.int64)
        else:
            mask_np = mask_np.astype(np.int64)

        img_t = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0
        mask_t = torch.from_numpy(mask_np).long()
        if self.transform:
            from backend.engine.augmentations import apply_sample_transform
            sample = apply_sample_transform(self.transform, img_t, mask_t, task="segmentation")
            img_t, mask_t = sample.image, sample.targets
        return img_t, mask_t


# ============================================================================
# Task 4: Unsupervised Anomaly Detection Dataset
# ============================================================================

class AnomalyDataset(Dataset):
    """
    Unsupervised industrial anomaly detection dataset (PaDiM / PatchCore style).
    CRITICAL SECURITY INVARIANT:
      - split='train': Strictly 100% normal/good images (0 defects). Any defect folder raises ValueError.
      - split='test' / 'val': Contains both normal (OK) and defective (NG) images with GT masks.
    Yields:
      Train: (img_tensor, label=0, mask_zeros)
      Test: (img_tensor, label, mask_tensor)
    """

    def __init__(
        self,
        root_dir: Optional[Union[str, Path]] = None,
        split: str = "train",
        transform: Optional[Callable] = None,
        image_size: Optional[Tuple[int, int]] = None,
        max_dim: int = 1600,
        normal_dir: Optional[Union[str, Path]] = None,
        anomaly_dir: Optional[Union[str, Path]] = None,
    ):
        self.root_dir = Path(root_dir) if root_dir is not None else None
        self.split = split.lower().strip()
        self.transform = transform
        self.image_size = image_size
        self.max_dim = max_dim
        self.normal_dir = Path(normal_dir) if normal_dir is not None else None
        self.anomaly_dir = Path(anomaly_dir) if anomaly_dir is not None else None
        self.samples: List[Tuple[Path, int, Optional[Path]]] = []

        from backend.engine.anomaly_split import partition_evaluation_images, partition_normal_images

        def image_paths(directory: Path, recursive: bool = True) -> List[Path]:
            from backend.engine.dataset_usage import unused_image_paths
            unused=unused_image_paths(directory)
            paths = directory.rglob("*") if recursive else directory.glob("*")
            return [
                p for p in paths
                if p.is_file() and str(p.resolve()) not in unused and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                and not (directory.name == "test_crop_output" and p.name.startswith("mask_"))
            ]

        def train_normal_paths(directory: Path) -> List[Path]:
            """Use every normal alias as one source for disjoint train/val/test partitions."""
            aliases = {"good", "ok", "normal", "pass"}
            folders = [p for p in directory.iterdir() if p.is_dir() and not p.name.startswith(".")]
            for folder in folders:
                if folder.name.lower() not in aliases:
                    raise ValueError(
                        f"Anomaly training split must contain exclusively normal (good) images, found: {folder.name}"
                    )
            if folders:
                return sorted(p for folder in folders for p in image_paths(folder, recursive=False))
            return sorted(image_paths(directory, recursive=False))

        if self.split == "train":
            if self.normal_dir and self.normal_dir.is_dir():
                self.samples.extend(
                    (p, 0, None) for p in partition_normal_images(image_paths(self.normal_dir))["train"]
                )
            elif self.root_dir is not None:
                train_dir = self.root_dir / "train"
                if train_dir.exists():
                    self.samples.extend((p, 0, None) for p in train_normal_paths(train_dir))
                    if not (self.root_dir / "test").is_dir() and not (self.root_dir / "val").is_dir():
                        self.samples = [
                            (p, 0, None)
                            for p in partition_normal_images(p for p, _, _ in self.samples)["train"]
                        ]
                elif (self.root_dir / "OK").is_dir():
                    self.samples.extend(
                        (p, 0, None) for p in partition_normal_images(image_paths(self.root_dir / "OK"))["train"]
                    )
                elif (self.root_dir / "test_crop_output").is_dir():
                    self.samples.extend(
                        (p, 0, None) for p in partition_normal_images(
                            image_paths(self.root_dir / "test_crop_output", recursive=False)
                        )["train"]
                    )
                else:
                    raise FileNotFoundError(f"Anomaly train directory does not exist at {train_dir}")
            else:
                raise FileNotFoundError("Neither root_dir nor normal_dir was provided for AnomalyDataset")

        elif self.split in ["test", "val"]:
            if self.normal_dir or self.anomaly_dir:
                if self.normal_dir and self.normal_dir.is_dir():
                    self.samples.extend(
                        (p, 0, None) for p in partition_normal_images(image_paths(self.normal_dir))[self.split]
                    )
                if self.anomaly_dir and self.anomaly_dir.is_dir():
                    self.samples.extend(
                        (p, 1, None) for p in partition_evaluation_images(image_paths(self.anomaly_dir))[self.split]
                    )
            elif self.root_dir is not None:
                test_dir = self.root_dir / "test"
                val_dir = self.root_dir / "val"
                gt_dir = self.root_dir / "ground_truth"

                if test_dir.is_dir() or val_dir.is_dir():
                    has_named_splits = test_dir.is_dir() and val_dir.is_dir()
                    source_dir = (val_dir if self.split == "val" else test_dir) if has_named_splits else (
                        test_dir if test_dir.is_dir() else val_dir
                    )
                    normal_samples: List[Tuple[Path, int, Optional[Path]]] = []
                    defect_samples: List[Tuple[Path, int, Optional[Path]]] = []
                    for sub in sorted(source_dir.iterdir()):
                        if not sub.is_dir() or sub.name.startswith("."):
                            continue
                        is_good = sub.name.lower() in ("good", "ok", "normal", "pass")
                        for p in sorted(image_paths(sub, recursive=False)):
                            mask_p = None
                            if not is_good and gt_dir.exists():
                                candidate = gt_dir / sub.name / f"{p.stem}_mask.png"
                                if not candidate.exists():
                                    candidate = gt_dir / sub.name / p.name
                                if candidate.exists():
                                    mask_p = candidate
                            sample = (p, 0 if is_good else 1, mask_p)
                            (normal_samples if is_good else defect_samples).append(sample)

                    if has_named_splits:
                        self.samples.extend(normal_samples + defect_samples)
                    else:
                        selected = set(partition_evaluation_images(
                            p for p, _, _ in normal_samples
                        )[self.split]) | set(partition_evaluation_images(
                            p for p, _, _ in defect_samples
                        )[self.split])
                        self.samples.extend(
                            sample for sample in normal_samples + defect_samples if sample[0] in selected
                        )
                else:
                    norm_dir = next((directory for directory in (
                        self.root_dir / "test_crop_output",
                        self.root_dir / "OK",
                        self.root_dir / "train",
                    ) if directory.is_dir()), self.root_dir / "OK")
                    anom_dir = (self.root_dir / "fail") if (self.root_dir / "fail").is_dir() else (self.root_dir / "NG")
                    if norm_dir.is_dir():
                        normal_paths = (
                            train_normal_paths(norm_dir)
                            if norm_dir == self.root_dir / "train" else image_paths(norm_dir)
                        )
                        self.samples.extend(
                            (p, 0, None) for p in partition_normal_images(normal_paths)[self.split]
                        )
                    if anom_dir.is_dir():
                        self.samples.extend(
                            (p, 1, None) for p in partition_evaluation_images(image_paths(anom_dir))[self.split]
                        )
        else:
            raise ValueError(f"Unknown anomaly split: {split}")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, int, torch.Tensor]:
        img_p, label, mask_p = self.samples[idx]
        rgb = _read_image_rgb(img_p)
        h, w = rgb.shape[:2]

        if self.image_size:
            target_w, target_h = self.image_size
            rgb = cv2.resize(rgb, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        elif self.max_dim and max(h, w) > self.max_dim:
            scale = self.max_dim / float(max(h, w))
            target_w = max(16, int(round(w * scale)))
            target_h = max(16, int(round(h * scale)))
            rgb = cv2.resize(rgb, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        else:
            target_w, target_h = w, h

        img_t = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0

        if mask_p is not None and mask_p.exists():
            with Image.open(str(mask_p)) as m_im:
                mask_l = m_im.convert("L")
                mask_np = np.array(mask_l)
            if mask_np.shape != (target_h, target_w):
                mask_np = cv2.resize(mask_np, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
            # Binary mask: values > 0 are anomalies
            mask_t = torch.from_numpy(np.where(mask_np > 0, 1, 0)).long()
        else:
            mask_t = torch.zeros((target_h, target_w), dtype=torch.int64)

        if self.transform:
            from backend.engine.augmentations import apply_sample_transform
            sample = apply_sample_transform(self.transform, img_t, mask_t, task="anomaly")
            img_t, mask_t = sample.image, sample.targets
        return img_t, label, mask_t


# ============================================================================
# Universal Dataset Factory & Inspection
# ============================================================================

# ============================================================================
# File Parsers (Pure Python: No C++ pycocotools dependencies)
# ============================================================================

class PascalVocParser:
    """Parser for Pascal VOC XML annotation files."""

    @staticmethod
    def parse_file(xml_path: Union[str, Path]) -> Dict[str, Any]:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()

        filename = root.findtext("filename") or ""
        size_elem = root.find("size")
        width = int(size_elem.findtext("width", "0")) if size_elem is not None else 0
        height = int(size_elem.findtext("height", "0")) if size_elem is not None else 0

        boxes: List[Dict[str, Any]] = []
        for obj in root.findall("object"):
            name = (obj.findtext("name") or "defect").strip()
            bndbox = obj.find("bndbox")
            if bndbox is None:
                continue

            try:
                xmin = float(bndbox.findtext("xmin", "0"))
                ymin = float(bndbox.findtext("ymin", "0"))
                xmax = float(bndbox.findtext("xmax", "0"))
                ymax = float(bndbox.findtext("ymax", "0"))
                boxes.append({
                    "name": name,
                    "bbox": [xmin, ymin, xmax, ymax]
                })
            except ValueError:
                continue

        return {
            "filename": filename,
            "width": width,
            "height": height,
            "objects": boxes,
        }


class CocoJsonParser:
    """Pure-Python COCO format parser."""

    @staticmethod
    def parse_file(json_path: Union[str, Path]) -> Dict[str, Any]:
        with open(str(json_path), "r", encoding="utf-8") as f:
            data = json.load(f)

        cat_id_to_name: Dict[int, str] = {}
        for cat in data.get("categories", []):
            cat_id_to_name[cat["id"]] = cat.get("name", f"cat_{cat['id']}")

        annotations_by_image: Dict[int, List[Dict[str, Any]]] = {}
        for ann in data.get("annotations", []):
            img_id = ann["image_id"]
            annotations_by_image.setdefault(img_id, []).append(ann)

        return {
            "images": data.get("images", []),
            "categories": cat_id_to_name,
            "annotations_by_image": annotations_by_image,
        }


# ============================================================================
# Universal Dataset Factory & Inspection
# ============================================================================

def inspect_dataset(root_dir: Union[str, Path], task: str, ignore_saved_split: bool = False) -> DatasetSummary:
    """
    Fast, metadata-only dataset scanner for Electron GUI project import.
    Returns counts matching REST API contract without loading full images.
    """
    root = Path(root_dir)
    task_clean = task.lower().strip()

    if task_clean == "classification":
        if not ignore_saved_split and _classification_split_assignments(root) is not None:
            all_dataset = ClassificationDataset(root_dir=root)
            counts: Dict[str, int] = {}
            for _, label_idx in all_dataset.samples:
                name = all_dataset.classes[label_idx]
                counts[name] = counts.get(name, 0) + 1
            split_counts = {
                partition: len(ClassificationDataset(root_dir=root, split=partition))
                for partition in ("train", "val", "test")
            }
            return DatasetSummary(
                task="classification", total_images=len(all_dataset),
                classes=counts, split_counts=split_counts,
            )
        if (root / "train").is_dir():
            split_counts: Dict[str, int] = {}
            counts: Dict[str, int] = {}
            for split_name in ["train", "val", "test"]:
                s_dir = root / split_name
                if s_dir.is_dir():
                    split_total = 0
                    for cdir in sorted(s_dir.iterdir()):
                        if cdir.is_dir() and not cdir.name.startswith("."):
                            cname = cdir.name
                            c_count = sum(
                                1 for p in cdir.glob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                            )
                            counts[cname] = counts.get(cname, 0) + c_count
                            split_total += c_count
                    split_counts[split_name] = split_total
            total = sum(split_counts.values())
            if "train" not in split_counts:
                split_counts["train"] = total
            if "val" not in split_counts:
                split_counts["val"] = 0
            return DatasetSummary(
                task="classification",
                total_images=total,
                classes=counts,
                split_counts=split_counts,
            )
        else:
            ds = ClassificationDataset(root_dir=root, ignore_saved_split=ignore_saved_split)
            counts = {}
            for _, lbl in ds.samples:
                cname = ds.classes[lbl]
                counts[cname] = counts.get(cname, 0) + 1
            total = len(ds)
            train_count = int(total * 0.8)
            return DatasetSummary(
                task="classification",
                total_images=total,
                classes=counts,
                split_counts={"train": train_count, "val": total - train_count},
            )

    elif task_clean == "detection":
        train_json = root / "annotations_train.json" if (root / "annotations_train.json").exists() else (
            (root / "annotations" / "annotations_train.json") if (root / "annotations" / "annotations_train.json").exists() else None
        )
        val_json = root / "annotations_val.json" if (root / "annotations_val.json").exists() else (
            (root / "annotations" / "annotations_val.json") if (root / "annotations" / "annotations_val.json").exists() else None
        )

        if train_json or val_json:
            counts = {}
            train_count = 0
            val_count = 0

            if train_json and train_json.exists():
                with open(train_json, "r", encoding="utf-8") as f:
                    data_tr = json.load(f)
                cats = {c["id"]: c["name"] for c in data_tr.get("categories", [])}
                for cname in cats.values():
                    if cname != "__background__" and cname not in counts:
                        counts[cname] = 0
                train_count = len(data_tr.get("images", []))
                for ann in data_tr.get("annotations", []):
                    cname = cats.get(ann.get("category_id"), "defect")
                    if cname != "__background__":
                        counts[cname] = counts.get(cname, 0) + 1

            if val_json and val_json.exists():
                with open(val_json, "r", encoding="utf-8") as f:
                    data_val = json.load(f)
                cats = {c["id"]: c["name"] for c in data_val.get("categories", [])}
                for cname in cats.values():
                    if cname != "__background__" and cname not in counts:
                        counts[cname] = 0
                val_count = len(data_val.get("images", []))
                for ann in data_val.get("annotations", []):
                    cname = cats.get(ann.get("category_id"), "defect")
                    if cname != "__background__":
                        counts[cname] = counts.get(cname, 0) + 1

            total = train_count + val_count
            return DatasetSummary(
                task="detection",
                total_images=total,
                classes=counts,
                split_counts={"train": train_count, "val": val_count},
            )
        else:
            ds = DetectionDataset(root_dir=root)
            counts = {c: 0 for c in ds.categories.values() if c != "__background__"}
            for item_annos in ds.img_to_annos.values():
                for ann in item_annos:
                    dense_id = ds.original_to_dense.get(int(ann["category_id"]))
                    if dense_id is None:
                        raise ValueError(f"COCO annotation references unknown category ID: {ann['category_id']}")
                    cname = ds.categories[dense_id]
                    counts[cname] = counts.get(cname, 0) + 1
            total = len(ds)
            return DatasetSummary(
                task="detection",
                total_images=total,
                classes=counts,
                split_counts={"train": total, "val": 0},
            )

    elif task_clean == "segmentation":
        split_image_dirs = None
        if (root / "images" / "train").is_dir():
            split_image_dirs = {name: root / "images" / name for name in ("train", "val", "test")}
        elif (root / "train" / "images").is_dir():
            split_image_dirs = {name: root / name / "images" for name in ("train", "val", "test")}

        if split_image_dirs is not None:
            split_counts = {
                name: sum(
                    1 for p in image_dir.glob("*")
                    if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                ) if image_dir.is_dir() else 0
                for name, image_dir in split_image_dirs.items()
            }
            total = sum(split_counts.values())
            return DatasetSummary(
                task="segmentation",
                total_images=total,
                classes={"defect_mask": total},
                split_counts=split_counts,
            )
        else:
            ds = SegmentationDataset(root_dir=root)
            total = len(ds)
            train_count = int(total * 0.8)
            return DatasetSummary(
                task="segmentation",
                total_images=total,
                classes={"defect_mask": total},
                split_counts={"train": train_count, "val": total - train_count},
            )

    elif task_clean in ("anomaly", "anomaly_detection"):
        ds_train = AnomalyDataset(root_dir=root, split="train")
        ds_val = AnomalyDataset(root_dir=root, split="val")
        ds_test = AnomalyDataset(root_dir=root, split="test")
        counts = {"good": len(ds_train)}
        for _, label, _ in ds_val.samples + ds_test.samples:
            name = "defect" if label == 1 else "good"
            counts[name] = counts.get(name, 0) + 1
        total = len(ds_train) + len(ds_val) + len(ds_test)
        return DatasetSummary(
            task="anomaly",
            total_images=total,
            classes=counts,
            split_counts={"train": len(ds_train), "val": len(ds_val), "test": len(ds_test)},
        )

    else:
        raise ValueError(f"Unknown task type: {task}")


def create_dataloader(
    dataset: Dataset,
    batch_size: int = 16,
    shuffle: bool = True,
    num_workers: int = 0,
    task: str = "classification",
) -> DataLoader:
    """
    Construct a PyTorch DataLoader with appropriate collation.
    num_workers defaults to 0 to eliminate macOS MPS multiprocessing fork hazards.
    """
    collate = detection_collate_fn if task.lower().strip() == "detection" else None
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=num_workers,
        collate_fn=collate,
        pin_memory=False,
    )


# ============================================================================
# Industrial Adapters Re-exports
# ============================================================================

from backend.engine.industrial_adapters import (
    DEFECT_KEYWORD_MAP,
    FlexibleAnomalyDataset,
    HierarchicalClassificationAdapter,
    HierarchicalClassificationDataset,
    IGNORED_JSON_NAMES,
    LabelMeDetectionDataset,
    LabelMeParser,
    LabelMeRasterizer,
    LabelMeSegmentationDataset,
    find_labelme_folder,
    find_matching_image,
    inspect_industrial_dataset,
    is_valid_labelme_file,
    normalize_defect_category,
    read_image_safely_rgb,
)
