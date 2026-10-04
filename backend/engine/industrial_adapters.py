"""
backend/engine/industrial_adapters.py

Production-Grade Industrial Dataset Ingestion Adapters & Preprocessing Safeguards.
Designed for real semiconductor manufacturing inspection datasets with
hierarchical classes, LabelMe annotations, and high-resolution source images.

Features:
  1. Standardized defect taxonomy mapping (Korean/English manufacturing categories).
  2. Double extension sanitation (e.g. .jpg.jpg -> .jpg).
  3. Hierarchical Classification Adapter (OK/**, NG/** with binary & multiclass regex).
  4. Native LabelMe-to-COCO Object Detection Adapter (supports microscopic flaws down to 1px).
  5. LabelMe Polygon Rasterizer Segmentation Adapter (cv2.fillPoly discrete 8-bit masks).
  6. Flexible Industrial Anomaly Dataset Loader (arbitrary normal folders without a rigid anomaly layout).
  7. High-resolution memory safeguard (adaptive dimension capping preventing 45MP OOM).
"""

from __future__ import annotations

import functools
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
from PIL import Image
from backend.engine.dicom_input import open_source_image
import torch
from torch.utils.data import Dataset

from backend.engine.anomaly_split import partition_evaluation_images, partition_normal_images

logger = logging.getLogger("vision_ai_studio.industrial_adapters")

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}

# Standardized defect mapping for semiconductor manufacturing
DEFECT_KEYWORD_MAP: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"scratch|긁힘|스크래치|s/c", re.IGNORECASE), "Scratch"),
    (re.compile(r"white\s*spot|화이트\s*스팟|흰\s*점|흰점", re.IGNORECASE), "White Spot"),
    (re.compile(r"black\s*spot|블랙\s*스팟|검은\s*점|검은점", re.IGNORECASE), "Black Spot"),
    (re.compile(r"discolor|변색|꽃구름", re.IGNORECASE), "Discolor"),
    (re.compile(r"pollution|오염|stain|얼룩", re.IGNORECASE), "Pollution"),
    (re.compile(r"bolt|볼트|체결\s*상태|체결\s*불량|체결불량", re.IGNORECASE), "Bolt Defect"),
    (re.compile(r"particle|파티클|이물질|이물|머리카락", re.IGNORECASE), "Particle"),
    (re.compile(r"machining|가공\s*이상|가공이상|치수", re.IGNORECASE), "Machining Error"),
    (re.compile(r"chipping|칩핑|깨짐|c/p|e/c", re.IGNORECASE), "Chipping"),
    (re.compile(r"crack|크랙|균열", re.IGNORECASE), "Crack"),
    (re.compile(r"pitting|피팅|기공|p/t", re.IGNORECASE), "Pitting"),
    (re.compile(r"peeling|박리", re.IGNORECASE), "Peeling"),
    (re.compile(r"spot|스팟|점", re.IGNORECASE), "Spot"),
    (re.compile(r"상면\s*외곽\s*림\s*잘림|rim\s*clipping|림\s*잘림", re.IGNORECASE), "Rim Clipping"),
]


def sanitize_file_stem(path_or_name: Union[str, Path]) -> str:
    """
    Normalizes filenames containing double extensions (e.g. `diag_00.jpg.jpg`)
    returning the sanitized stem without duplicate suffix artifacts.
    """
    name = Path(path_or_name).name
    # Strip double extensions (.jpg.jpg, .png.png, etc.)
    for ext in SUPPORTED_IMAGE_EXTENSIONS:
        double_ext = f"{ext}{ext}"
        if name.lower().endswith(double_ext):
            name = name[:-len(ext)]
            break

    stem = Path(name).stem
    # If stem still ends with a known image extension (e.g. foo.jpg.png)
    if any(stem.lower().endswith(ext) for ext in SUPPORTED_IMAGE_EXTENSIONS):
        stem = Path(stem).stem
    return stem


def normalize_defect_category(raw_name: str) -> str:
    """
    Maps Korean and English industrial defect terms to standardized categories.
    Defaults to cleaned input string if no rule matches.
    """
    cleaned = raw_name.strip()
    if not cleaned:
        return "Unknown"

    low = cleaned.lower()
    if low in ("ok", "good", "normal", "pass", "정상", "양품"):
        return "OK"

    for pattern, standardized in DEFECT_KEYWORD_MAP:
        if pattern.search(cleaned):
            return standardized

    return cleaned


IGNORED_JSON_NAMES = {
    "all_inspection_progress.json",
    "audit_progress.json",
    "fail_records.json",
    "week_audit_report.json",
}

def find_matching_image(json_path: Union[str, Path]) -> Optional[Path]:
    """
    Finds corresponding image file on disk for a given JSON annotation file.
    Probes JSON's internal 'imagePath' and matching stems across supported extensions.
    """
    p = Path(json_path)
    # 1. Probe internal imagePath if present in JSON
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cand_name = data.get("imagePath")
            if cand_name:
                cand_p = p.parent / cand_name
                if cand_p.is_file():
                    return cand_p
    except Exception:
        pass

    # 2. Probe matching stem with supported image extensions
    stem_clean = sanitize_file_stem(p.stem)
    for ext in SUPPORTED_IMAGE_EXTENSIONS:
        cand = p.parent / f"{stem_clean}{ext}"
        if cand.is_file():
            return cand
        cand_raw = p.parent / f"{p.stem}{ext}"
        if cand_raw.is_file():
            return cand_raw

    return None


def is_valid_labelme_file(json_path: Union[str, Path], require_image: bool = True) -> bool:
    """
    Validates whether a JSON file is a genuine LabelMe annotation file:
      1. Rejects known audit/telemetry/report JSON files.
      2. Requires valid JSON object with 'shapes' non-empty list of shape dicts containing 'points'.
      3. If require_image is True and parent folder contains images, requires matching image on disk.
    """
    p = Path(json_path)
    if p.name.lower() in IGNORED_JSON_NAMES:
        return False
    if not p.is_file():
        return False

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False

    if not isinstance(data, dict):
        return False

    shapes = data.get("shapes")
    if not isinstance(shapes, list) or len(shapes) == 0:
        return False

    has_valid_shape = False
    for s in shapes:
        if isinstance(s, dict) and "points" in s:
            pts = s.get("points")
            if isinstance(pts, (list, tuple)) and len(pts) >= 2:
                has_valid_shape = True
                break
    if not has_valid_shape:
        return False

    if require_image:
        matched = find_matching_image(p)
        if matched is not None:
            return True
        # If the parent folder contains image files, an image was expected on disk
        has_any_images = any(
            f.is_file() and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            for f in p.parent.iterdir()
        )
        if not has_any_images:
            # Synthetic fixtures without image files on disk
            return True
        return False

    return True


def find_labelme_folder(folder: Path) -> Optional[Path]:
    """
    Finds a folder containing valid LabelMe annotations, checking:
      1. folder itself
      2. folder / "NG_labelme"
      3. folder.parent / "NG_labelme"
    Returns None if no valid LabelMe annotations are found.
    """
    candidates = [
        folder,
        folder / "NG_labelme",
        folder.parent / "NG_labelme",
    ]

    for cand in candidates:
        if cand.is_dir():
            valid_jsons = [f for f in cand.glob("*.json") if is_valid_labelme_file(f, require_image=True)]
            if valid_jsons:
                return cand
    return None



def read_image_safely_rgb(
    path: Union[str, Path],
    bg_color: str = "white",
    max_dim: Optional[int] = None,
) -> np.ndarray:
    """
    Robust image reader handling:
      - 16-bit Grayscale and TIFF normalization to 8-bit RGB without clipping
      - RGBA transparent cutout compositing onto white or black background
      - Single-channel grayscale replication to 3-channel RGB
      - Adaptive resolution downscaling if max_dim is specified
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
            rgb = np.stack([norm, norm, norm], axis=-1)

        # Single-channel 8-bit grayscale
        elif im.mode == "L":
            arr = np.array(im, dtype=np.uint8)
            rgb = np.stack([arr, arr, arr], axis=-1)

        # RGBA / LA images with alpha transparency (e.g. isolated wafer chucks)
        elif im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im_rgba = im.convert("RGBA")
            bg_rgb = (0, 0, 0) if bg_color == "black" else (255, 255, 255)
            background = Image.new("RGB", im_rgba.size, bg_rgb)
            background.paste(im_rgba, mask=im_rgba.split()[3])
            rgb = np.array(background, dtype=np.uint8)

        # Standard photographic formats (RGB, CMYK, etc.)
        else:
            rgb_im = im.convert("RGB")
            rgb = np.array(rgb_im, dtype=np.uint8)

    # Adaptive resolution capping safeguard
    if max_dim is not None and max_dim > 0:
        h, w = rgb.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / float(max(h, w))
            new_w = max(16, int(round(w * scale)))
            new_h = max(16, int(round(h * scale)))
            rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    return rgb


# ============================================================================
# 1. Hierarchical Classification Adapter
# ============================================================================

class HierarchicalClassificationAdapter:
    """
    Recursively scans multi-level manufacturing directories (e.g. OK/YYYYMMDD/... and NG/Model/Lot(Defect)/*).
    Supports:
      - Binary Mode: Maps all OK/** paths to 'OK' and NG/** (or fail/**) to 'NG'.
      - Multi-class Mode: Extracts defect names from folder parentheses, e.g. G3834(Mount Guide Scratch) -> 'Scratch'.
    """

    @staticmethod
    def parse_directory(
        root_dir: Union[str, Path],
        mode: Literal["binary", "multiclass"] = "binary",
    ) -> Dict[str, Any]:
        root = Path(root_dir).resolve()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Directory does not exist: {root}")

        samples: List[Tuple[Path, str]] = []
        classes_count: Dict[str, int] = {}

        # Look for operational fail records if present
        fail_records_map: Dict[str, str] = {}
        fail_json = root / "visual_inspection" / "fail_records.json"
        if fail_json.exists():
            try:
                with open(fail_json, "r", encoding="utf-8") as f:
                    f_data = json.load(f)
                    for fname, finfo in f_data.items():
                        fail_records_map[fname] = finfo.get("reason", "Defect")
            except Exception as e:
                logger.warning("Could not read fail_records.json: %s", e)

        for p in root.rglob("*"):
            if not p.is_file():
                continue
            suf = p.suffix.lower()
            if suf not in SUPPORTED_IMAGE_EXTENSIONS and not p.name.lower().endswith(".jpg.jpg"):
                continue

            rel_parts = [part.lower() for part in p.relative_to(root).parts]
            raw_folder_parts = list(p.relative_to(root).parts)

            is_ok = any(part in ("ok", "good", "normal", "pass", "정상", "test_crop_output") for part in rel_parts)
            is_ng = (
                any(part in ("ng", "fail", "defect", "scan_anomalies", "불량") for part in rel_parts)
                or p.name in fail_records_map
                or p.name.lower().startswith("ng_")
            )

            if mode == "binary":
                if is_ng:
                    label = "NG"
                elif is_ok:
                    label = "OK"
                else:
                    label = "NG" if "fail" in p.name.lower() or "anomaly" in p.name.lower() else "OK"
            else:
                # Multi-class extraction
                if is_ok and not is_ng:
                    label = "OK"
                else:
                    # Search folder names for parentheses: e.g. G3834(Mount Guide Scratch)
                    extracted = None
                    for part in reversed(raw_folder_parts[:-1]):
                        match = re.search(r"\(([^)]+)\)", part)
                        if match:
                            extracted = match.group(1).strip()
                            break

                    if not extracted and p.name in fail_records_map:
                        extracted = fail_records_map[p.name]

                    # Filename fallback for imported images with an NG-prefixed label.
                    if not extracted:
                        fn_match = re.search(r"ng_\d+__([^_]+)___", p.name)
                        if fn_match:
                            extracted = fn_match.group(1).strip()

                    if not extracted:
                        for part in reversed(raw_folder_parts[:-1]):
                            if part.lower() not in ("ng", "ok", "fail", "test", "train", "val"):
                                extracted = part
                                break

                    label = normalize_defect_category(extracted or "Defect")

            samples.append((p, label))
            classes_count[label] = classes_count.get(label, 0) + 1

        total_images = len(samples)
        train_count = int(round(total_images * 0.8))
        val_count = max(0, total_images - train_count)

        return {
            "samples": samples,
            "classes": classes_count,
            "total_images": total_images,
            "split": {"train": train_count, "val": val_count},
        }


class HierarchicalClassificationDataset(Dataset):
    """PyTorch Dataset loading from hierarchical manufacturing directory."""

    def __init__(
        self,
        root_dir: Union[str, Path],
        mode: Literal["binary", "multiclass"] = "binary",
        split: Optional[str] = None,
        val_split: float = 0.2,
        seed: int = 42,
        transform: Optional[Callable] = None,
        image_size: Optional[Tuple[int, int]] = None,
        max_dim: int = 1600,
    ):
        self.root_dir = Path(root_dir)
        self.mode = mode
        self.split = split
        self.val_split = val_split
        self.seed = seed
        self.transform = transform
        self.image_size = image_size
        self.max_dim = max_dim

        parsed = HierarchicalClassificationAdapter.parse_directory(root_dir, mode=mode)
        raw_samples = parsed["samples"]

        # Deterministic class ordering: OK/good first
        unique_classes = sorted(list(parsed["classes"].keys()), key=lambda c: (0 if c.lower() in ("ok", "good", "normal") else 1, c))
        self.classes = unique_classes
        self.class_to_idx = {name: idx for idx, name in enumerate(unique_classes)}

        samples_with_idx = [(p, self.class_to_idx[lbl]) for p, lbl in raw_samples]

        if split in ("train", "val") and len(samples_with_idx) > 1:
            by_class: Dict[int, List[Tuple[Path, int]]] = {}
            for s in samples_with_idx:
                by_class.setdefault(s[1], []).append(s)

            rng = np.random.default_rng(seed)
            train_list: List[Tuple[Path, int]] = []
            val_list: List[Tuple[Path, int]] = []

            for c_idx, items in by_class.items():
                items_shuffled = list(items)
                rng.shuffle(items_shuffled)
                n_val = max(1, int(round(len(items_shuffled) * val_split))) if len(items_shuffled) > 1 else 0
                val_list.extend(items_shuffled[:n_val])
                train_list.extend(items_shuffled[n_val:])

            self.samples = train_list if split == "train" else val_list
        else:
            self.samples = samples_with_idx

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, int]:
        img_p, label_idx = self.samples[idx]
        rgb = read_image_safely_rgb(img_p, max_dim=self.max_dim)

        if self.image_size:
            rgb = cv2.resize(rgb, self.image_size, interpolation=cv2.INTER_LINEAR)
        elif self.max_dim:
            h, w = rgb.shape[:2]
            target_dim = self.max_dim
            pad_top = (target_dim - h) // 2
            pad_bottom = target_dim - h - pad_top
            pad_left = (target_dim - w) // 2
            pad_right = target_dim - w - pad_left
            if pad_top > 0 or pad_bottom > 0 or pad_left > 0 or pad_right > 0:
                rgb = cv2.copyMakeBorder(
                    rgb, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                )

        tensor = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0
        if self.transform:
            tensor = self.transform(tensor)

        return tensor, label_idx



# ============================================================================
# 2. LabelMe-to-COCO Object Detection Adapter
# ============================================================================

class LabelMeParser:
    """
    Parses LabelMe JSON format files (e.g. ng_*.json from NG_labelme),
    converts polygon contours into bounding boxes, clamps coordinates,
    and supports microscopic flaw retention (down to 1px).
    """

    @staticmethod
    def parse_file(json_path: Union[str, Path]) -> Dict[str, Any]:
        p = Path(json_path)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        img_w = float(data.get("imageWidth") or 0)
        img_h = float(data.get("imageHeight") or 0)
        matched_img = find_matching_image(p)
        if matched_img is not None:
            img_name = matched_img.name
        else:
            img_name = data.get("imagePath") or f"{p.stem}.jpg"

        # If width or height missing, probe associated image file
        if img_w <= 0 or img_h <= 0:
            cand_img = p.parent / img_name
            if cand_img.exists():
                with open_source_image(cand_img) as im:
                    img_w, img_h = float(im.size[0]), float(im.size[1])
            else:
                img_w, img_h = 8192.0, 5464.0

        boxes: List[Dict[str, Any]] = []

        for shape in data.get("shapes", []):
            points = shape.get("points", [])
            if not points or len(points) < 2:
                continue

            pts = np.array(points, dtype=np.float32)
            xmin = float(np.min(pts[:, 0]))
            ymin = float(np.min(pts[:, 1]))
            xmax = float(np.max(pts[:, 0]))
            ymax = float(np.max(pts[:, 1]))

            # Clamp to image boundaries
            xmin = max(0.0, min(img_w, xmin))
            ymin = max(0.0, min(img_h, ymin))
            xmax = max(0.0, min(img_w, xmax))
            ymax = max(0.0, min(img_h, ymax))

            if xmax < xmin:
                xmin, xmax = xmax, xmin
            if ymax < ymin:
                ymin, ymax = ymax, ymin

            # Microscopic flaw support: ensure minimum 1px dimension
            if (xmax - xmin) < 1.0:
                xmax = min(img_w, xmin + 1.0)
            if (ymax - ymin) < 1.0:
                ymax = min(img_h, ymin + 1.0)

            raw_label = shape.get("label", "defect")
            category_name = str(raw_label).strip() or "defect"

            boxes.append({
                "category_name": category_name,
                "bbox": [xmin, ymin, xmax - xmin, ymax - ymin],  # COCO format [x, y, w, h]
                "bbox_voc": [xmin, ymin, xmax, ymax],           # VOC format [x1, y1, x2, y2]
                "points": points,
            })

        return {
            "file_name": img_name,
            "width": int(img_w),
            "height": int(img_h),
            "boxes": boxes,
        }

    @staticmethod
    def to_coco(folder_path: Union[str, Path]) -> Dict[str, Any]:
        """Converts an entire folder containing LabelMe JSON files into COCO JSON structure."""
        folder = Path(folder_path).resolve()
        json_files = [f for f in sorted(folder.glob("*.json")) if is_valid_labelme_file(f, require_image=True)]

        images = []
        annotations = []
        categories_map: Dict[str, int] = {}
        anno_id = 1

        for img_id, jf in enumerate(json_files, start=1):
            parsed = LabelMeParser.parse_file(jf)
            img_entry = {
                "id": img_id,
                "file_name": parsed["file_name"],
                "width": parsed["width"],
                "height": parsed["height"],
            }
            images.append(img_entry)

            for box in parsed["boxes"]:
                cat_name = box["category_name"]
                if cat_name not in categories_map:
                    categories_map[cat_name] = len(categories_map) + 1
                cat_id = categories_map[cat_name]

                bw, bh = box["bbox"][2], box["bbox"][3]
                annotations.append({
                    "id": anno_id,
                    "image_id": img_id,
                    "category_id": cat_id,
                    "category_name": cat_name,
                    "bbox": box["bbox"],
                    "area": float(bw * bh),
                    "iscrowd": 0,
                    "segmentation": [list(np.array(box["points"]).flatten())],
                })
                anno_id += 1

        categories = [{"id": cid, "name": cname} for cname, cid in categories_map.items()]
        return {"images": images, "annotations": annotations, "categories": categories}


class LabelMeDetectionDataset(Dataset):
    """
    Detection Dataset loading directly from a folder of LabelMe JSON files and associated images.
    Applies adaptive max dimension capping without VRAM OOM.
    """

    def __init__(
        self,
        folder_path: Union[str, Path],
        transform: Optional[Callable] = None,
        image_size: Optional[Tuple[int, int]] = None,
        max_dim: int = 1600,
    ):
        target = Path(folder_path).resolve()
        resolved_folder = find_labelme_folder(target)
        self.folder = resolved_folder if resolved_folder is not None else target
        self.transform = transform
        self.image_size = image_size
        self.max_dim = max_dim

        coco_dict = LabelMeParser.to_coco(self.folder)
        self.categories = {c["id"]: c["name"] for c in coco_dict["categories"]}
        self.images = {img["id"]: img for img in coco_dict["images"]}
        self.img_to_annos: Dict[int, List[Dict[str, Any]]] = {img["id"]: [] for img in coco_dict["images"]}
        for anno in coco_dict["annotations"]:
            self.img_to_annos[anno["image_id"]].append(anno)

        self.image_ids = sorted(list(self.images.keys()))

    def __len__(self) -> int:
        return len(self.image_ids)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, Dict[str, torch.Tensor]]:
        img_id = self.image_ids[idx]
        meta = self.images[img_id]
        img_p = self.folder / meta["file_name"]
        if not img_p.exists():
            matched = find_matching_image(self.folder / meta["file_name"])
            if matched and matched.exists():
                img_p = matched
            else:
                for ext in SUPPORTED_IMAGE_EXTENSIONS:
                    cand = self.folder / f"{Path(meta['file_name']).stem}{ext}"
                    if cand.exists():
                        img_p = cand
                        break
        if not img_p.exists():
            raise FileNotFoundError(f"Image not found for annotation: {meta['file_name']} in {self.folder}")

        rgb = read_image_safely_rgb(img_p, max_dim=None)
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

        for a in raw_annos:
            x, y, bw, bh = a["bbox"]
            x1 = max(0.0, float(x) * scale_x)
            y1 = max(0.0, float(y) * scale_y)
            x2 = min(float(target_w), float(x + bw) * scale_x)
            y2 = min(float(target_h), float(y + bh) * scale_y)

            if x2 < x1:
                x1, x2 = x2, x1
            if y2 < y1:
                y1, y2 = y2, y1

            # Microscopic flaw precision down to 1px
            if (x2 - x1) < 1.0:
                x2 = min(float(target_w), x1 + 1.0)
            if (y2 - y1) < 1.0:
                y2 = min(float(target_h), y1 + 1.0)

            valid_boxes.append([x1, y1, x2, y2])
            valid_labels.append(int(a["category_id"]))
            valid_areas.append(float((x2 - x1) * (y2 - y1)))
            valid_norms.append([x1 / target_w, y1 / target_h, x2 / target_w, y2 / target_h])

        if valid_boxes:
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

        if self.transform:
            from backend.engine.augmentations import apply_sample_transform
            sample = apply_sample_transform(self.transform, tensor, target, task="detection")
            tensor, target = sample.image, sample.targets

        return tensor, target


# ============================================================================
# 3. LabelMe Polygon Rasterizer Segmentation Adapter
# ============================================================================

class LabelMeRasterizer:
    """
    Converts LabelMe polygon vertices into discrete single-channel 8-bit masks
    using cv2.fillPoly, and supports extracting masks from RGBA alpha channels.
    """

    @staticmethod
    def rasterize_polygon(
        points: Sequence[Sequence[float]],
        height: int,
        width: int,
        fill_value: int = 1,
    ) -> np.ndarray:
        mask = np.zeros((height, width), dtype=np.uint8)
        if len(points) < 3:
            return mask
        pts = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
        cv2.fillPoly(mask, [pts], color=int(fill_value))
        return mask

    @staticmethod
    def rasterize_labelme_file(
        json_path: Union[str, Path],
        target_size: Optional[Tuple[int, int]] = None,
        binary: bool = True,
    ) -> np.ndarray:
        p = Path(json_path)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        orig_w = int(data.get("imageWidth") or 8192)
        orig_h = int(data.get("imageHeight") or 5464)

        mask = np.zeros((orig_h, orig_w), dtype=np.uint8)

        for idx, shape in enumerate(data.get("shapes", []), start=1):
            points = shape.get("points", [])
            if len(points) >= 3:
                fill_val = 1 if binary else idx
                pts = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
                cv2.fillPoly(mask, [pts], color=fill_val)

        if target_size and (target_size[0] != orig_w or target_size[1] != orig_h):
            mask = cv2.resize(mask, target_size, interpolation=cv2.INTER_NEAREST)

        return mask

    @staticmethod
    def extract_alpha_mask(image_path: Union[str, Path]) -> Optional[np.ndarray]:
        """Extracts discrete binary mask from RGBA image alpha channel (alpha > 0 -> 1)."""
        with open_source_image(str(image_path)) as im:
            if im.mode not in ("RGBA", "LA"):
                return None
            alpha = np.array(im.split()[-1], dtype=np.uint8)
            return np.where(alpha > 0, 1, 0).astype(np.uint8)


class LabelMeSegmentationDataset(Dataset):
    """
    Segmentation dataset loading images and rasterizing LabelMe JSON polygons on the fly.
    """

    def __init__(
        self,
        folder_path: Union[str, Path],
        transform: Optional[Callable] = None,
        image_size: Optional[Tuple[int, int]] = None,
        max_dim: int = 1600,
    ):
        target = Path(folder_path).resolve()
        resolved_folder = find_labelme_folder(target)
        self.folder = resolved_folder if resolved_folder is not None else target
        self.transform = transform
        self.image_size = image_size
        self.max_dim = max_dim

        self.samples: List[Tuple[Path, Path]] = []
        for jf in sorted(self.folder.glob("*.json")):
            if not is_valid_labelme_file(jf, require_image=True):
                continue
            matching_img = find_matching_image(jf)
            if matching_img is not None:
                self.samples.append((matching_img, jf))


    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        img_p, jf_p = self.samples[idx]
        rgb = read_image_safely_rgb(img_p, max_dim=None)
        h, w = rgb.shape[:2]

        mask = LabelMeRasterizer.rasterize_labelme_file(jf_p, target_size=(w, h), binary=True)

        if self.image_size:
            rgb = cv2.resize(rgb, self.image_size, interpolation=cv2.INTER_LINEAR)
            mask = cv2.resize(mask, self.image_size, interpolation=cv2.INTER_NEAREST)
        elif self.max_dim and max(h, w) > self.max_dim:
            scale = self.max_dim / float(max(h, w))
            new_w = max(16, int(round(w * scale)))
            new_h = max(16, int(round(h * scale)))
            rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            mask = cv2.resize(mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)

        img_t = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0
        mask_t = torch.from_numpy(mask).long()
        if self.transform:
            from backend.engine.augmentations import apply_sample_transform
            sample = apply_sample_transform(self.transform, img_t, mask_t, task="segmentation")
            img_t, mask_t = sample.image, sample.targets
        return img_t, mask_t


# ============================================================================
# 4. Flexible Industrial Anomaly Dataset Loader
# ============================================================================

def _shared_listings(init):
    """The constructor's fixed-name lookups share one listing of each folder (dataset_loaders.folder_listings)."""
    @functools.wraps(init)
    def wrapped(self, *args, **kwargs):
        from backend.engine.dataset_loaders import folder_listings
        with folder_listings():
            return init(self, *args, **kwargs)
    return wrapped


class FlexibleAnomalyDataset(Dataset):
    """
    Industrial anomaly dataset loader that accepts normal-image folders without requiring root/train/good.
    Allows designating any directory of normal images as the training baseline,
    with an optional anomaly directory for evaluation.
    """

    @_shared_listings
    def __init__(
        self,
        root_dir: Optional[Union[str, Path]] = None,
        split: str = "train",
        normal_dir: Optional[Union[str, Path]] = None,
        anomaly_dir: Optional[Union[str, Path]] = None,
        transform: Optional[Callable] = None,
        image_size: Optional[Tuple[int, int]] = None,
        max_dim: int = 1600,
    ):
        self.root_dir = Path(root_dir).resolve() if root_dir is not None else None
        self.split = split.lower().strip()
        self.transform = transform
        self.image_size = image_size
        self.max_dim = max_dim
        self.normal_dir = Path(normal_dir).resolve() if normal_dir is not None else None
        self.anomaly_dir = Path(anomaly_dir).resolve() if anomaly_dir is not None else None

        self.samples: List[Tuple[Path, int, Optional[Path]]] = []

        # Determine effective normal and anomaly directories
        norm_dir = self.normal_dir
        anom_dir = self.anomaly_dir

        from backend.engine.dataset_loaders import named_child_dir  # fixed folder names, whatever their letter case
        named = (lambda *names: named_child_dir(named(*names[:-1]), names[-1]) if len(names) > 1
                 else named_child_dir(self.root_dir, names[0]))
        if norm_dir is None and self.root_dir is not None:
            # Check standard anomaly layout first
            if named("train", "good").is_dir():
                norm_dir = named("train", "good")
            elif named("OK").is_dir():
                norm_dir = named("OK")
            elif named("test_crop_output").is_dir():
                norm_dir = named("test_crop_output")
            elif any(self.root_dir.glob("*.png")) or any(self.root_dir.glob("*.jpg")):
                norm_dir = self.root_dir

        if self.split not in ("train", "val", "test"):
            raise ValueError(f"Unknown anomaly split: {split}")

        # The standard anomaly layout supplies a separate train/good directory. Reuse its explicit
        # layout handling so held-out test images and masks stay associated.
        if (self.root_dir is not None and self.normal_dir is None and self.anomaly_dir is None
                and named("train", "good").is_dir()
                and (named("test").is_dir() or named("val").is_dir())):
            from backend.engine.dataset_loaders import AnomalyDataset
            self.samples = AnomalyDataset(root_dir=self.root_dir, split=self.split).samples
            return

        # Only val and test read defects, and only after the hand-off above (which reads its own layout).
        if anom_dir is None and self.root_dir is not None and self.split in ("val", "test"):
            if named("fail").is_dir():
                anom_dir = named("fail")
            elif named("NG").is_dir():
                anom_dir = named("NG")
            elif named("scan_anomalies").is_dir():
                anom_dir = named("scan_anomalies")

        if norm_dir and norm_dir.is_dir():
            all_normals = [
                p for p in norm_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            ]
        elif self.root_dir:
            all_normals = [
                p for p in self.root_dir.glob("*")
                if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            ]
        else:
            all_normals = []
        self.samples.extend((p, 0, None) for p in partition_normal_images(all_normals)[self.split])

        if self.split in ("val", "test") and anom_dir and anom_dir.is_dir():
            all_anomalies = [
                p for p in anom_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            ]
            self.samples.extend((p, 1, None) for p in partition_evaluation_images(all_anomalies)[self.split])

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, int, torch.Tensor]:
        img_p, label, mask_p = self.samples[idx]
        rgb = read_image_safely_rgb(img_p, max_dim=self.max_dim)
        h, w = rgb.shape[:2]

        target_w, target_h = w, h
        pad_top, pad_bottom, pad_left, pad_right = 0, 0, 0, 0
        if self.image_size:
            target_w, target_h = self.image_size
            rgb = cv2.resize(rgb, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        elif self.max_dim:
            target_dim = self.max_dim
            pad_top = (target_dim - h) // 2
            pad_bottom = target_dim - h - pad_top
            pad_left = (target_dim - w) // 2
            pad_right = target_dim - w - pad_left
            if pad_top > 0 or pad_bottom > 0 or pad_left > 0 or pad_right > 0:
                rgb = cv2.copyMakeBorder(
                    rgb, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                )
            target_w, target_h = target_dim, target_dim

        img_t = torch.from_numpy(rgb.transpose(2, 0, 1)).float() / 255.0
        if self.transform:
            img_t = self.transform(img_t)

        if mask_p is not None and mask_p.exists():
            with Image.open(str(mask_p)) as m_im:
                mask_np = np.array(m_im.convert("L"))
            if self.image_size:
                if mask_np.shape != (target_h, target_w):
                    mask_np = cv2.resize(mask_np, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
            elif self.max_dim:
                if mask_np.shape != (h, w):
                    mask_np = cv2.resize(mask_np, (w, h), interpolation=cv2.INTER_NEAREST)
                if pad_top > 0 or pad_bottom > 0 or pad_left > 0 or pad_right > 0:
                    mask_np = cv2.copyMakeBorder(
                        mask_np, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_CONSTANT, value=0
                    )
            mask_t = torch.from_numpy(np.where(mask_np > 0, 1, 0)).long()
        else:
            mask_t = torch.zeros((target_h, target_w), dtype=torch.int64)

        return img_t, label, mask_t



# ============================================================================
# 5. Master Industrial Dataset Inspector
# ============================================================================

def inspect_industrial_dataset(
    folder_path: Union[str, Path],
    task: str = "classification",
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Main entry point for /api/dataset/import-industrial endpoint.
    Scans the given manufacturing directory, applies appropriate adapter,
    and returns metadata conforming to SCOPE.md interface contract.
    """
    folder = Path(folder_path).resolve()
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Industrial dataset folder not found: {folder_path}")

    opts = options or {}
    task_clean = task.lower().strip()
    mode = opts.get("mode", "binary")

    adapter_used = "HierarchicalClassificationAdapter"
    classes: Dict[str, int] = {}
    total_images = 0
    split = {"train": 0, "val": 0}
    sample_thumbnails: List[str] = []

    if task_clean == "classification":
        adapter_used = f"HierarchicalClassificationAdapter({mode})"
        res = HierarchicalClassificationAdapter.parse_directory(folder, mode=mode)
        classes = res["classes"]
        total_images = res["total_images"]
        split = res["split"]
        sample_thumbnails = [f"/api/dataset/thumbnail/{p.name}?file_path={p.resolve()}" for p, _ in res["samples"][:8]]

    elif task_clean == "detection":
        labelme_dir = find_labelme_folder(folder)
        if labelme_dir is not None:
            adapter_used = "LabelMeToCOCOAdapter"
            target_folder = labelme_dir
            coco_dict = LabelMeParser.to_coco(target_folder)
            total_images = len(coco_dict["images"])
            cat_id_to_name = {c["id"]: c["name"] for c in coco_dict["categories"]}
            for anno in coco_dict["annotations"]:
                cname = cat_id_to_name.get(anno["category_id"], "defect")
                classes[cname] = classes.get(cname, 0) + 1
            train_count = int(round(total_images * 0.8))
            split = {"train": train_count, "val": total_images - train_count}
            sample_thumbnails = [f"/api/dataset/thumbnail/{img['file_name']}?file_path={(target_folder / img['file_name']).resolve()}" for img in coco_dict["images"][:8]]
        else:
            adapter_used = "StandardDetectionFallback"
            # Fallback to standard inspect
            from backend.engine.dataset_loaders import inspect_dataset
            std_summary = inspect_dataset(folder, task="detection")
            classes = std_summary.classes
            total_images = std_summary.total_images
            split = {"train": std_summary.split_counts.get("train", 0), "val": std_summary.split_counts.get("val", 0)}

    elif task_clean == "segmentation":
        # Only route to LabelMe if folder itself contains valid paired LabelMe JSONs
        local_labelme_files = [
            f for f in folder.glob("*.json")
            if is_valid_labelme_file(f, require_image=True)
        ]
        if local_labelme_files:
            adapter_used = "LabelMePolygonRasterizerAdapter"
            ds = LabelMeSegmentationDataset(folder)
            total_images = len(ds)
            classes = {"defect_mask": total_images}
            train_count = int(round(total_images * 0.8))
            split = {"train": train_count, "val": total_images - train_count}
            sample_thumbnails = [f"/api/dataset/thumbnail/{p.name}?file_path={p.resolve()}" for p, _ in ds.samples[:8]]
        else:
            adapter_used = "AlphaMaskAndPairedSegmentationAdapter"
            # Look for masks or RGBA images in test_crop_output
            crop_imgs = list(folder.glob("*.png")) + list((folder / "test_crop_output").glob("*.png"))
            seen_paths = set()
            unique_crops = []
            for p in crop_imgs:
                if p.resolve() not in seen_paths and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    seen_paths.add(p.resolve())
                    unique_crops.append(p)
            total_images = len(unique_crops)
            classes = {"defect_mask": total_images}
            train_count = int(round(total_images * 0.8))
            split = {"train": train_count, "val": total_images - train_count}
            sample_thumbnails = [f"/api/dataset/thumbnail/{p.name}?file_path={p.resolve()}" for p in unique_crops[:8]]


    elif task_clean in ("anomaly", "anomaly_detection"):
        adapter_used = "FlexibleIndustrialAnomalyAdapter"
        norm_dir = opts.get("normal_dir")
        anom_dir = opts.get("anomaly_dir")
        flat_ng_images = any(
            p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS and p.name.lower().startswith("ng_")
            for p in folder.iterdir()
        )
        paired_labelme = any(is_valid_labelme_file(p, require_image=True) for p in folder.glob("*.json"))
        root_is_normal = norm_dir is None or Path(norm_dir).resolve() == folder
        if root_is_normal and (flat_ng_images or paired_labelme):
            raise ValueError("NG-only images cannot be used as normal (OK) anomaly training data. Select a separate normal image folder.")
        ds_train = FlexibleAnomalyDataset(root_dir=folder, split="train", normal_dir=norm_dir, anomaly_dir=anom_dir)
        ds_val = FlexibleAnomalyDataset(root_dir=folder, split="val", normal_dir=norm_dir, anomaly_dir=anom_dir)
        ds_test = FlexibleAnomalyDataset(root_dir=folder, split="test", normal_dir=norm_dir, anomaly_dir=anom_dir)

        classes = {"good": len(ds_train)}
        for _, lbl, _ in ds_val.samples + ds_test.samples:
            name = "defect" if lbl == 1 else "good"
            classes[name] = classes.get(name, 0) + 1

        total_images = len(ds_train) + len(ds_val) + len(ds_test)
        split = {"train": len(ds_train), "val": len(ds_val), "test": len(ds_test)}
        sample_thumbnails = [
            f"/api/dataset/thumbnail/{p.name}?file_path={p.resolve()}"
            for p, _, _ in (ds_train.samples[:4] + ds_val.samples[:2] + ds_test.samples[:2])
        ]

    return {
        "status": "success",
        "total_images": total_images,
        "classes": classes,
        "split": split,
        "sample_thumbnails": sample_thumbnails,
        "adapter_used": adapter_used,
    }
