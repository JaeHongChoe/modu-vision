"""
Reference implementation of backend/engine/dataset_loaders.py for test harness.
Provides PyTorch Dataset classes for all 4 tasks (Classification, Detection, Segmentation, Anomaly),
image health validation, and robust stratified splitting with single-sample fallback.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset


class ClassificationDataset(Dataset):
    """
    Folder-based classification dataset.
    Structure: root_dir/{class_name}/*.{png,jpg,jpeg,bmp}
    Yields: (image_tensor: Tensor[3, H, W] float32, label_idx: int)
    """

    def __init__(self, root_dir: str, transform=None):
        self.root_dir = Path(root_dir)
        self.transform = transform
        self.classes = sorted([d.name for d in self.root_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])
        self.class_to_idx = {cls_name: i for i, cls_name in enumerate(self.classes)}

        self.samples: List[Tuple[Path, int]] = []
        valid_exts = {".png", ".jpg", ".jpeg", ".bmp"}
        for cls_name in self.classes:
            cls_dir = self.root_dir / cls_name
            for p in sorted(cls_dir.glob("*")):
                if p.suffix.lower() in valid_exts and p.is_file():
                    self.samples.append((p, self.class_to_idx[cls_name]))

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, int]:
        img_path, label_idx = self.samples[idx]
        bgr = cv2.imread(str(img_path))
        if bgr is None:
            raise RuntimeError(f"Failed to read image at {img_path}")
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

        tensor = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0
        if self.transform is not None:
            tensor = self.transform(tensor)
        return tensor, label_idx


class DetectionDataset(Dataset):
    """
    Object detection dataset supporting COCO JSON annotations.
    Sanitizes invalid and inverted bounding boxes.
    Yields: (image_tensor: Tensor[3, H, W] float32, target: Dict[str, Tensor])
    """

    def __init__(self, images_dir: str, annotation_file: str, transform=None):
        self.images_dir = Path(images_dir)
        self.annotation_file = Path(annotation_file)
        self.transform = transform

        with open(self.annotation_file, "r") as f:
            coco_data = json.load(f)

        self.categories = {c["id"]: c["name"] for c in coco_data.get("categories", [])}
        self.images = {img["id"]: img for img in coco_data.get("images", [])}

        # Group annotations by image_id
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

        bgr = cv2.imread(str(img_path))
        if bgr is None:
            raise RuntimeError(f"Failed to load image at {img_path}")
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0

        raw_annos = self.img_to_annos.get(img_id, [])
        valid_boxes = []
        valid_labels = []
        valid_areas = []

        for a in raw_annos:
            x, y, bw, bh = a["bbox"]
            # Sanitization invariant: drop invalid or inverted boxes
            if bw <= 0 or bh <= 0:
                continue

            x1 = max(0.0, float(x))
            y1 = max(0.0, float(y))
            x2 = min(float(w), float(x + bw))
            y2 = min(float(h), float(y + bh))

            if x2 <= x1 or y2 <= y1:
                continue

            valid_boxes.append([x1, y1, x2, y2])
            valid_labels.append(int(a["category_id"]))
            valid_areas.append(float((x2 - x1) * (y2 - y1)))

        if len(valid_boxes) > 0:
            boxes_t = torch.tensor(valid_boxes, dtype=torch.float32)
            labels_t = torch.tensor(valid_labels, dtype=torch.int64)
            areas_t = torch.tensor(valid_areas, dtype=torch.float32)
            iscrowd_t = torch.zeros(len(valid_boxes), dtype=torch.int64)
        else:
            boxes_t = torch.zeros((0, 4), dtype=torch.float32)
            labels_t = torch.zeros((0,), dtype=torch.int64)
            areas_t = torch.zeros((0,), dtype=torch.float32)
            iscrowd_t = torch.zeros((0,), dtype=torch.int64)

        target = {
            "boxes": boxes_t,
            "labels": labels_t,
            "image_id": torch.tensor([img_id], dtype=torch.int64),
            "area": areas_t,
            "iscrowd": iscrowd_t,
        }

        return tensor, target


class SegmentationDataset(Dataset):
    """
    Semantic segmentation dataset.
    Images: images_dir/*.{png,jpg}
    Masks: masks_dir/*.png (single channel uint8)
    Yields: (image_tensor: Tensor[3, H, W] float32, mask_tensor: Tensor[H, W] int64)
    """

    def __init__(self, images_dir: str, masks_dir: str, transform=None):
        self.images_dir = Path(images_dir)
        self.masks_dir = Path(masks_dir)
        self.transform = transform

        valid_exts = {".png", ".jpg", ".jpeg", ".bmp"}
        self.samples = []
        for img_p in sorted(self.images_dir.glob("*")):
            if img_p.suffix.lower() in valid_exts and img_p.is_file():
                mask_p = self.masks_dir / f"{img_p.stem}.png"
                if mask_p.exists():
                    self.samples.append((img_p, mask_p))

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        img_p, mask_p = self.samples[idx]
        bgr = cv2.imread(str(img_p))
        if bgr is None:
            raise RuntimeError(f"Failed to read image at {img_p}")
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img_t = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0

        mask_np = cv2.imread(str(mask_p), cv2.IMREAD_GRAYSCALE)
        if mask_np is None:
            raise RuntimeError(f"Failed to read mask at {mask_p}")

        # Edge Case: Automatic mask dimension resizing
        if mask_np.shape != (h, w):
            mask_np = cv2.resize(mask_np, (w, h), interpolation=cv2.INTER_NEAREST)

        mask_t = torch.from_numpy(mask_np).long()
        return img_t, mask_t


class AnomalyDataset(Dataset):
    """
    MVTec AD style unsupervised anomaly dataset.
    Train split: root_dir/train/good/*.png (100% normal)
    Test split: root_dir/test/good/*.png, root_dir/test/{defect}/*.png
    Yields:
      Train: (img_tensor, label=0, mask=zeros)
      Test Good: (img_tensor, label=0, mask=zeros)
      Test Defect: (img_tensor, label=1, mask_tensor)
    """

    def __init__(self, root_dir: str, split: str = "train", transform=None):
        self.root_dir = Path(root_dir)
        self.split = split.lower().strip()
        self.transform = transform
        self.samples: List[Tuple[Path, int, Optional[Path]]] = []

        valid_exts = {".png", ".jpg", ".jpeg", ".bmp"}

        if self.split == "train":
            train_dir = self.root_dir / "train"
            # Strict validation: verify no defect subfolders exist in train other than 'good' or 'ok'
            for sub in train_dir.iterdir():
                if sub.is_dir() and not sub.name.startswith("."):
                    if sub.name.lower() not in ["good", "ok", "normal"]:
                        raise ValueError(
                            f"Anomaly training split must contain exclusively normal (good) images, found: {sub.name}"
                        )

            good_dir = train_dir / "good"
            if not good_dir.exists():
                good_dir = train_dir / "ok"

            if good_dir.exists():
                for p in sorted(good_dir.glob("*")):
                    if p.suffix.lower() in valid_exts and p.is_file():
                        self.samples.append((p, 0, None))

        elif self.split in ["test", "val"]:
            test_dir = self.root_dir / "test"
            gt_dir = self.root_dir / "ground_truth"

            for sub in sorted(test_dir.iterdir()):
                if sub.is_dir() and not sub.name.startswith("."):
                    is_good = (sub.name.lower() in ["good", "ok", "normal"])
                    label = 0 if is_good else 1
                    for p in sorted(sub.glob("*")):
                        if p.suffix.lower() in valid_exts and p.is_file():
                            mask_p = None
                            if not is_good and gt_dir.exists():
                                candidate = gt_dir / sub.name / f"{p.stem}_mask.png"
                                if candidate.exists():
                                    mask_p = candidate
                            self.samples.append((p, label, mask_p))
        else:
            raise ValueError(f"Unknown split: {split}")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, int, torch.Tensor]:
        img_p, label, mask_p = self.samples[idx]
        bgr = cv2.imread(str(img_p))
        if bgr is None:
            raise RuntimeError(f"Failed to read image at {img_p}")
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img_t = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0

        if mask_p is not None and mask_p.exists():
            mask_np = cv2.imread(str(mask_p), cv2.IMREAD_GRAYSCALE)
            if mask_np.shape != (h, w):
                mask_np = cv2.resize(mask_np, (w, h), interpolation=cv2.INTER_NEAREST)
            mask_t = torch.from_numpy(mask_np).long()
        else:
            mask_t = torch.zeros((h, w), dtype=torch.int64)

        return img_t, label, mask_t


@dataclass
class ValidationResult:
    valid: bool
    error_code: str
    dimensions: Optional[Tuple[int, int]] = None
    details: str = ""


def validate_image_file(file_path: Union[str, Path]) -> ValidationResult:
    """Zero-cost image file integrity, header magic bytes, and decodability validator."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return ValidationResult(valid=False, error_code="FILE_NOT_FOUND", details="File does not exist")

    size = p.stat().st_size
    if size == 0:
        return ValidationResult(valid=False, error_code="ZERO_BYTE", details="File size is 0 bytes")

    # Magic byte check
    try:
        with open(p, "rb") as f:
            header = f.read(16)
    except Exception as e:
        return ValidationResult(valid=False, error_code="READ_ERROR", details=str(e))

    # Known magic headers
    is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpg = header.startswith(b"\xff\xd8\xff")
    is_bmp = header.startswith(b"BM")
    is_tiff = header.startswith(b"II*\x00") or header.startswith(b"MM\x00*")

    if not (is_png or is_jpg or is_bmp or is_tiff):
        return ValidationResult(valid=False, error_code="CORRUPT_HEADER", details="Header magic bytes do not match standard formats")

    # Decodability check
    try:
        with Image.open(p) as im:
            im.verify()
        # Re-open to read dimensions
        with Image.open(p) as im:
            w, h = im.size
    except Exception as e:
        return ValidationResult(valid=False, error_code="VERIFY_FAILED", details=str(e))

    if w < 16 or h < 16:
        return ValidationResult(valid=False, error_code="DIMENSION_TOO_SMALL", dimensions=(w, h), details="Image smaller than 16x16")

    return ValidationResult(valid=True, error_code="OK", dimensions=(w, h), details="Valid image")


def split_dataset(
    items: List[Dict[str, Any]],
    train_ratio: float = 0.8,
    val_ratio: float = 0.2,
    seed: int = 42,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Partitions items with stratified defect sampling.
    If rare classes have < 2 samples, gracefully falls back to unstratified split.
    """
    if not items:
        return {"train": [], "val": []}

    rng = np.random.default_rng(seed)

    # Group by label
    class_groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        lbl = str(it.get("label", "default"))
        class_groups.setdefault(lbl, []).append(it)

    train_set = []
    val_set = []

    # Check if any class has only 1 item
    has_single_item = any(len(grp) < 2 for grp in class_groups.values())

    if has_single_item:
        # Graceful fallback: shuffle all items globally
        shuffled = list(items)
        rng.shuffle(shuffled)
        n_train = max(1, int(round(len(shuffled) * (train_ratio / (train_ratio + val_ratio)))))
        train_set = shuffled[:n_train]
        val_set = shuffled[n_train:]
    else:
        for lbl, grp in class_groups.items():
            shuffled = list(grp)
            rng.shuffle(shuffled)
            n_train = max(1, int(round(len(shuffled) * (train_ratio / (train_ratio + val_ratio)))))
            train_set.extend(shuffled[:n_train])
            val_set.extend(shuffled[n_train:])

    return {"train": train_set, "val": val_set}
