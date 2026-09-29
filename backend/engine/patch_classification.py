"""Explicit, source-linked patch classification data and reusable inference.

``patches.json`` lives at the selected dataset root::

    {"version": 1, "classes": ["OK", "NG"], "normal_class": "OK",
     "patch_size": 256, "stride": 128,
     "patches": [{"image": "images/part.png", "box": [0, 0, 256, 256],
                  "label": "NG", "split": "train", "source_sha256": "..."}]}

Boxes use original-image, half-open pixel coordinates. The optional
``source_sha256`` in each row pins a source before training; source hashes are
always calculated and saved with the checkpoint even when the field is absent.
All crops of a source (including byte-identical copies) must use one split.
Stage 4 image-level comparison also requires ``test_image_verdicts`` in
``patches.json`` with one independently reviewed ``OK`` or ``NG`` verdict for
every distinct test source path (for example ``"images/test/part.png": "NG"``).
Patch labels alone do not establish that a complete source image is OK.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import cv2
import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset

from backend.engine.classification import create_classification_model
from backend.engine.device import get_device


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class PatchRecord:
    image: str
    image_path: Path
    box: tuple[int, int, int, int]
    label: str
    label_index: int
    split: str
    source_sha256: str


@dataclass(frozen=True)
class PatchManifest:
    root: Path
    classes: list[str]
    normal_class: str
    patch_size: int
    stride: int
    patches: list[PatchRecord]
    provenance: dict[str, Any]


def _check_box(box: Any, width: int, height: int) -> tuple[int, int, int, int]:
    if not isinstance(box, (list, tuple)) or len(box) != 4 or any(type(v) is not int for v in box):
        raise ValueError("Patch box must contain four integer pixel coordinates")
    x1, y1, x2, y2 = box
    if x1 < 0 or y1 < 0 or x1 >= x2 or y1 >= y2 or x2 > width or y2 > height:
        raise ValueError(f"Patch box is outside source image bounds: {box} vs {width}x{height}")
    return x1, y1, x2, y2


def load_patch_manifest(root: str | Path) -> PatchManifest:
    """Validate labels, pixels, split isolation and source SHA-256 values."""
    root = Path(root).expanduser().resolve()
    manifest_path = root / "patches.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError(f"Patch dataset needs a regular patches.json: {manifest_path}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid patches.json: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("patches.json must use version 1")
    classes = raw.get("classes")
    if (not isinstance(classes, list) or len(classes) < 2
            or any(not isinstance(c, str) or not c.strip() or c != c.strip() for c in classes)
            or len(set(classes)) != len(classes)):
        raise ValueError("Patch classes must contain at least two distinct nonempty names")
    normal_class = raw.get("normal_class")
    if normal_class not in classes:
        raise ValueError("normal_class must name one of the classes")
    patch_size, stride = raw.get("patch_size"), raw.get("stride")
    if type(patch_size) is not int or patch_size < 1 or type(stride) is not int or not 1 <= stride <= patch_size:
        raise ValueError("patch_size and stride must be positive integers with stride <= patch_size")
    entries = raw.get("patches")
    if not isinstance(entries, list) or not entries:
        raise ValueError("patches.json must contain labeled patch rows")

    source_hashes: dict[str, str] = {}
    source_sizes: dict[str, tuple[int, int]] = {}
    source_splits: dict[str, str] = {}
    hash_splits: dict[str, str] = {}
    patches: list[PatchRecord] = []
    split_counts = {"train": 0, "val": 0, "test": 0}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Patch row {index} must be an object")
        image = entry.get("image")
        if not isinstance(image, str) or not image or Path(image).is_absolute() or ".." in Path(image).parts:
            raise ValueError(f"Patch row {index} image must stay inside dataset root")
        image_path = (root / image).resolve()
        if not image_path.is_relative_to(root) or not image_path.is_file() or image_path.is_symlink():
            raise ValueError(f"Patch row {index} image must be a file inside dataset root")
        relative = image_path.relative_to(root).as_posix()
        if relative not in source_hashes:
            try:
                with Image.open(image_path) as opened:
                    source_sizes[relative] = opened.size
                    opened.verify()
                source_hashes[relative] = _sha256(image_path)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Patch source image is unreadable: {relative}: {exc}") from exc
        claimed = entry.get("source_sha256")
        if claimed is not None and claimed != source_hashes[relative]:
            raise ValueError(f"Source SHA-256 mismatch for {relative}")
        split = entry.get("split")
        if split not in split_counts:
            raise ValueError(f"Patch row {index} split must be train, val or test")
        old_split = source_splits.setdefault(relative, split)
        if old_split != split:
            raise ValueError(f"One source image occurs in different split partitions: {relative}")
        old_hash_split = hash_splits.setdefault(source_hashes[relative], split)
        if old_hash_split != split:
            raise ValueError(f"Byte-identical source images occur in different split partitions: {relative}")
        label = entry.get("label")
        if label not in classes:
            raise ValueError(f"Patch row {index} label is not in classes: {label}")
        width, height = source_sizes[relative]
        box = _check_box(entry.get("box"), width, height)
        patches.append(PatchRecord(relative, image_path, box, label, classes.index(label), split, source_hashes[relative]))
        split_counts[split] += 1
    if not split_counts["train"] or not split_counts["val"]:
        raise ValueError("Patch training requires separate nonempty train and val splits")

    manifest_sha = _sha256(manifest_path)
    digest = hashlib.sha256()
    digest.update(b"patch-classification-dataset-v1\0")
    digest.update(manifest_sha.encode("ascii"))
    for image, source_hash in sorted(source_hashes.items()):
        digest.update(b"\0")
        digest.update(image.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source_hash.encode("ascii"))
    provenance = {
        "dataset_sha256": f"sha256:{digest.hexdigest()}",
        "manifest_sha256": manifest_sha,
        "source_sha256": dict(sorted(source_hashes.items())),
        "split_counts": split_counts,
        "source_image_count": len(source_hashes),
        "patch_count": len(patches),
    }
    return PatchManifest(root, list(classes), normal_class, patch_size, stride, patches, provenance)


class PatchClassificationDataset(Dataset):
    """Read annotated source crops while retaining each row's source identity."""

    def __init__(
        self,
        root: str | Path,
        *,
        split: str,
        image_size: tuple[int, int] = (256, 256),
        transform: Optional[Callable] = None,
        manifest: Optional[PatchManifest] = None,
    ):
        if split not in {"train", "val", "test"}:
            raise ValueError("Patch split must be train, val or test")
        self.manifest = manifest or load_patch_manifest(root)
        self.classes = self.manifest.classes
        self.normal_class = self.manifest.normal_class
        self.samples = [record for record in self.manifest.patches if record.split == split]
        self.class_counts = [sum(record.label_index == index for record in self.samples) for index in range(len(self.classes))]
        self.image_size = image_size
        self.transform = transform

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int]:
        record = self.samples[index]
        source_bytes = record.image_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != record.source_sha256:
            raise ValueError(f"Source image changed after patch manifest validation: {record.image}")
        with Image.open(io.BytesIO(source_bytes)) as source:
            rgb = np.asarray(source.convert("RGB").crop(record.box), dtype=np.uint8)
        if self.image_size:
            rgb = cv2.resize(rgb, self.image_size, interpolation=cv2.INTER_LINEAR)
        image = torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1))).float() / 255.0
        if self.transform:
            image = self.transform(image)
        return image, record.label_index


def _grid_positions(length: int, patch_size: int, stride: int) -> list[int]:
    if length <= patch_size:
        return [0]
    positions = list(range(0, length - patch_size + 1, stride))
    last = length - patch_size
    if positions[-1] != last:
        positions.append(last)
    return positions


def predict_patch_classification(
    checkpoint: str | Path,
    image: str | Path | Image.Image | np.ndarray,
    *,
    boxes: Optional[Sequence[Sequence[int]]] = None,
    threshold: float = 0.5,
    device: Optional[str | torch.device] = None,
    source_id: Optional[str] = None,
    max_patches: int = 256,
) -> dict[str, Any]:
    """Run a trained patch model on explicit ROIs or a deterministic grid.

    Returns original-image boxes, class probabilities, defect score and source
    and checkpoint hashes. A crop supplied by the caller uses crop coordinates.
    """
    checkpoint = Path(checkpoint).expanduser().resolve()
    meta_path = checkpoint.parent / "model_meta.json"
    if not checkpoint.is_file() or not meta_path.is_file():
        raise ValueError("Patch checkpoint needs best_model.pt and model_meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("task") != "patch_classification":
        raise ValueError("Checkpoint task must be patch_classification")
    classes = meta.get("classes")
    normal_class = meta.get("normal_class")
    if not isinstance(classes, list) or len(classes) < 2 or normal_class not in classes:
        raise ValueError("Patch checkpoint has invalid classes or normal_class")
    patch_size, stride = meta.get("patch_size"), meta.get("stride")
    if type(patch_size) is not int or patch_size < 1 or type(stride) is not int or not 1 <= stride <= patch_size:
        raise ValueError("Patch checkpoint has invalid geometry")
    size = meta.get("image_size")
    if not isinstance(size, list) or len(size) != 2 or any(type(value) is not int or value < 1 for value in size):
        raise ValueError("Patch checkpoint has invalid image_size")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("Patch threshold must be between 0 and 1")

    if isinstance(image, (str, Path)):
        image_path = Path(image).expanduser().resolve()
        with Image.open(image_path) as opened:
            rgb = np.asarray(opened.convert("RGB"), dtype=np.uint8)
        source_name = str(image_path)
        source_hash = _sha256(image_path)
    elif isinstance(image, Image.Image):
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        source_name = source_id or "<memory>"
        source_hash = hashlib.sha256(rgb.tobytes()).hexdigest()
    elif isinstance(image, np.ndarray):
        if image.ndim == 2:
            rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        elif image.ndim == 3 and image.shape[2] == 4:
            rgb = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
        elif image.ndim == 3 and image.shape[2] == 3:
            rgb = image
        else:
            raise ValueError("Patch image array must be HxW, HxWx3 or HxWx4")
        rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
        source_name = source_id or "<memory>"
        source_hash = hashlib.sha256(rgb.tobytes()).hexdigest()
    else:
        raise ValueError(f"Unsupported patch image type: {type(image)}")
    height, width = rgb.shape[:2]
    if boxes is None:
        xs = _grid_positions(width, patch_size, stride)
        ys = _grid_positions(height, patch_size, stride)
        patch_boxes = [
            (x, y, min(x + patch_size, width), min(y + patch_size, height))
            for y in ys for x in xs
        ]
    else:
        patch_boxes = [_check_box(box, width, height) for box in boxes]
        if not patch_boxes:
            raise ValueError("At least one patch box is required")
    if len(patch_boxes) > max_patches:
        raise ValueError(f"Patch inspection needs {len(patch_boxes)} crops; limit is {max_patches}")

    dev = get_device(device)
    checkpoint_data = torch.load(checkpoint, map_location=dev, weights_only=True)
    state_dict = checkpoint_data.get("model_state_dict", checkpoint_data)
    model = create_classification_model(
        backbone=meta.get("backbone", "resnet18"), num_classes=len(classes), pretrained=False,
    ).to(dev)
    model.load_state_dict(state_dict)
    model.eval()
    normal_index = classes.index(normal_class)
    results: list[dict[str, Any]] = []
    with torch.inference_mode():
        for offset in range(0, len(patch_boxes), 32):
            group = patch_boxes[offset:offset + 32]
            tensors = []
            for x1, y1, x2, y2 in group:
                crop = cv2.resize(rgb[y1:y2, x1:x2], tuple(size), interpolation=cv2.INTER_LINEAR)
                tensors.append(torch.from_numpy(np.ascontiguousarray(crop.transpose(2, 0, 1))).float() / 255.0)
            probs = torch.softmax(model(torch.stack(tensors).to(dev)), dim=1).cpu().numpy()
            for box, scores in zip(group, probs):
                pred_index = int(np.argmax(scores))
                defect_score = float(1.0 - scores[normal_index])
                results.append({
                    "box": list(box),
                    "source_image": source_name,
                    "source_sha256": source_hash,
                    "predicted_class": classes[pred_index],
                    "confidence": float(scores[pred_index]),
                    "class_scores": {name: float(scores[index]) for index, name in enumerate(classes)},
                    "defect_score": defect_score,
                    "decision": "FAIL" if defect_score >= threshold else "PASS",
                })
    maximum = max(record["defect_score"] for record in results)
    return {
        "task": "patch_classification",
        "decision": "FAIL" if maximum >= threshold else "PASS",
        "confidence_score": maximum,
        "max_defect_score": maximum,
        "threshold": threshold,
        "patches": results,
        "source_image": source_name,
        "source_sha256": source_hash,
        "model_sha256": _sha256(checkpoint),
        "model_path": str(checkpoint),
        "patch_size": patch_size,
        "stride": stride,
        "dataset_provenance": meta.get("patch_provenance"),
    }
