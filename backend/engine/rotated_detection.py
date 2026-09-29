"""Hash-verified single-object rotated-box regression for image crops.

The v1 ``rotated_boxes.json`` manifest uses one oriented box per image::

    {"version": 1, "samples": [
      {"image": "images/train/part.png", "source_sha256": "<64 hex>",
       "split": "train", "label": "defect",
       "box": {"cx": 42, "cy": 30, "width": 18, "height": 9,
               "angle_deg": 25}}
    ]}

Geometry is in original image pixels. Positive ``angle_deg`` follows image
coordinates (clockwise on screen); angles are equivalent modulo 180 degrees.
This deliberately does not perform multi-object detection or class prediction.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset


_SPLITS = ("train", "val", "test")
_BOX_KEYS = ("cx", "cy", "width", "height", "angle_deg")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class RotatedTrainingCancelled(Exception):
    """The user requested cancellation before a usable candidate was saved."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _points(box: Mapping[str, float]) -> np.ndarray:
    return cv2.boxPoints((
        (float(box["cx"]), float(box["cy"])),
        (float(box["width"]), float(box["height"])),
        float(box["angle_deg"]),
    ))


def _valid_box(box: Any, width: int, height: int) -> dict[str, float]:
    if not isinstance(box, dict) or set(box) != set(_BOX_KEYS):
        raise ValueError("Rotated box needs cx, cy, width, height and angle_deg")
    if any(isinstance(box[key], bool) or not isinstance(box[key], (int, float))
           or not math.isfinite(float(box[key])) for key in _BOX_KEYS):
        raise ValueError("Rotated box coordinates must be finite numbers")
    result = {key: float(box[key]) for key in _BOX_KEYS}
    if result["width"] <= 0 or result["height"] <= 0 or not -90 <= result["angle_deg"] < 90:
        raise ValueError("Rotated box size must be positive and angle in [-90, 90)")
    corners = _points(result)
    if (np.any(corners[:, 0] < -1e-4) or np.any(corners[:, 0] > width + 1e-4)
            or np.any(corners[:, 1] < -1e-4) or np.any(corners[:, 1] > height + 1e-4)):
        raise ValueError("Rotated box extends outside source image bounds")
    return result


def box_from_polygon(points: Sequence[Sequence[float]]) -> dict[str, float]:
    """Fit a minimum-area oriented rectangle to LabelMe-style polygon points."""
    coordinates = np.asarray(points, dtype=np.float32)
    if (coordinates.ndim != 2 or coordinates.shape[1] != 2 or len(coordinates) < 3
            or not np.isfinite(coordinates).all()):
        raise ValueError("Polygon needs at least three finite 2D points")
    (cx, cy), (width, height), angle = cv2.minAreaRect(coordinates)
    if width <= 0 or height <= 0:
        raise ValueError("Polygon has no positive area")
    if width < height:
        width, height = height, width
        angle += 90
    angle = (angle + 90) % 180 - 90
    return {"cx": float(cx), "cy": float(cy), "width": float(width),
            "height": float(height), "angle_deg": float(angle)}


def oriented_iou(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    """Intersection over union of two pixel-coordinate oriented rectangles."""
    first = ((float(left["cx"]), float(left["cy"])),
             (float(left["width"]), float(left["height"])), float(left["angle_deg"]))
    second = ((float(right["cx"]), float(right["cy"])),
              (float(right["width"]), float(right["height"])), float(right["angle_deg"]))
    _, intersection = cv2.rotatedRectangleIntersection(first, second)
    intersection_area = float(abs(cv2.contourArea(intersection))) if intersection is not None else 0.0
    union_area = first[1][0] * first[1][1] + second[1][0] * second[1][1] - intersection_area
    return float(np.clip(intersection_area / union_area, 0.0, 1.0)) if union_area > 0 else 0.0


@dataclass(frozen=True)
class RotatedBoxRecord:
    image: str
    path: Path
    source_sha256: str
    split: str
    label: str
    box: dict[str, float]
    size: tuple[int, int]


@dataclass(frozen=True)
class RotatedBoxManifest:
    root: Path
    class_name: str
    records: tuple[RotatedBoxRecord, ...]
    provenance: dict[str, Any]


def _load_rotated_manifest_path(root: str | Path, manifest_path: Path) -> RotatedBoxManifest:
    """Verify every source byte, image, oriented target and split assignment."""
    root = Path(root).expanduser().resolve()
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Rotated dataset needs a regular rotated_boxes.json")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid rotated_boxes.json: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("rotated_boxes.json must use version 1")
    rows = raw.get("samples")
    if not isinstance(rows, list) or not rows:
        raise ValueError("rotated_boxes.json needs one-object image samples")
    records: list[RotatedBoxRecord] = []
    hashes_by_split: dict[str, str] = {}
    seen_images: set[str] = set()
    counts = dict.fromkeys(_SPLITS, 0)
    labels: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Rotated sample {index} must be an object")
        image = row.get("image")
        if (not isinstance(image, str) or not image or Path(image).is_absolute()
                or ".." in Path(image).parts):
            raise ValueError(f"Rotated sample {index} image must stay inside dataset root")
        source_path = root / image
        if source_path.is_symlink():
            raise ValueError(f"Rotated sample {index} image must be a regular file inside dataset root")
        path = source_path.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Rotated sample {index} image must be a regular file inside dataset root")
        image = path.relative_to(root).as_posix()
        if image in seen_images:
            raise ValueError(f"One-object crop image occurs more than once: {image}")
        seen_images.add(image)
        claimed = row.get("source_sha256")
        if not isinstance(claimed, str) or not _SHA256.fullmatch(claimed):
            raise ValueError(f"Rotated sample {index} requires a source SHA-256")
        actual = _sha256(path)
        if actual != claimed:
            raise ValueError(f"Source SHA-256 mismatch for {image}")
        try:
            with Image.open(path) as opened:
                size = opened.size
                opened.verify()
        except (OSError, ValueError) as exc:
            raise ValueError(f"Rotated source image is unreadable: {image}") from exc
        split = row.get("split")
        if split not in _SPLITS:
            raise ValueError(f"Rotated sample {index} split must be train, val or test")
        existing_split = hashes_by_split.setdefault(actual, split)
        if existing_split != split:
            raise ValueError("Byte-identical rotated source images occur in different split partitions")
        label = row.get("label")
        if not isinstance(label, str) or not label.strip() or label != label.strip():
            raise ValueError(f"Rotated sample {index} needs a nonempty label")
        labels.add(label)
        box = _valid_box(row.get("box"), *size)
        records.append(RotatedBoxRecord(image, path, actual, split, label, box, size))
        counts[split] += 1
    if len(labels) != 1:
        raise ValueError("Single-object rotated model supports exactly one label class")
    if not counts["train"] or not counts["val"]:
        raise ValueError("Rotated training requires separate nonempty train and val splits")
    manifest_sha = _sha256(manifest_path)
    digest = hashlib.sha256()
    digest.update(b"rotated-detection-single-object-v1\0")
    digest.update(manifest_sha.encode("ascii"))
    for record in sorted(records, key=lambda item: item.image):
        digest.update(b"\0")
        digest.update(record.image.encode("utf-8"))
        digest.update(b"\0")
        digest.update(record.source_sha256.encode("ascii"))
    provenance = {
        "dataset_sha256": f"sha256:{digest.hexdigest()}",
        "manifest_sha256": manifest_sha,
        "source_sha256": {row.image: row.source_sha256 for row in records},
        "split_counts": counts,
        "source_image_count": len(records),
    }
    return RotatedBoxManifest(root, next(iter(labels)), tuple(records), provenance)


def load_rotated_manifest(root: str | Path) -> RotatedBoxManifest:
    root = Path(root).expanduser().resolve()
    return _load_rotated_manifest_path(root, root / "rotated_boxes.json")


def write_rotated_manifest(root: str | Path, samples: Sequence[Mapping[str, Any]]) -> RotatedBoxManifest:
    """Hash original images and atomically save only a valid explicit-label manifest."""
    root = Path(root).expanduser().resolve()
    path = root / "rotated_boxes.json"
    if not root.is_dir() or path.is_symlink():
        raise ValueError("Rotated dataset root or manifest path is invalid")
    if not samples:
        raise ValueError("Rotated manifest needs labeled samples")
    pinned: list[dict[str, Any]] = []
    for index, row in enumerate(samples):
        if not isinstance(row, Mapping):
            raise ValueError(f"Rotated sample {index} must be an object")
        image = row.get("image")
        if (not isinstance(image, str) or not image or Path(image).is_absolute()
                or ".." in Path(image).parts):
            raise ValueError(f"Rotated sample {index} image must stay inside dataset root")
        candidate = root / image
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root) or not candidate.is_file():
            raise ValueError(f"Rotated sample {index} image must be a regular file inside dataset root")
        source_sha = _sha256(candidate)
        claimed = row.get("source_sha256")
        if claimed is not None and claimed != source_sha:
            raise ValueError(f"Source SHA-256 mismatch for {image}")
        pinned.append({"image": image, "source_sha256": source_sha, "split": row.get("split"),
                       "label": row.get("label"), "box": row.get("box")})
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                         prefix=".rotated_boxes-", suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            json.dump({"version": 1, "samples": pinned}, temporary, ensure_ascii=False, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        manifest = _load_rotated_manifest_path(root, temporary_path)
        os.replace(temporary_path, path)
        return manifest
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class RotatedBoxDataset(Dataset):
    """Resize each verified crop and encode its original-pixel box target."""

    def __init__(self, manifest: RotatedBoxManifest, *, split: str, image_size: int = 64):
        if split not in _SPLITS:
            raise ValueError("Rotated split must be train, val or test")
        if type(image_size) is not int or not 16 <= image_size <= 512:
            raise ValueError("Rotated model image_size must be an integer from 16 to 512")
        self.records = [record for record in manifest.records if record.split == split]
        self.image_size = image_size

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        record = self.records[index]
        source_bytes = record.path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != record.source_sha256:
            raise ValueError(f"Rotated source image changed after manifest validation: {record.image}")
        with Image.open(io.BytesIO(source_bytes)) as opened:
            resized = opened.convert("RGB").resize((self.image_size, self.image_size), Image.Resampling.BILINEAR)
            pixels = np.asarray(resized, dtype=np.uint8)
        tensor = torch.from_numpy(np.ascontiguousarray(pixels.transpose(2, 0, 1))).float() / 255.0
        box = record.box
        diagonal = math.hypot(*record.size)
        radians = math.radians(2 * box["angle_deg"])
        target = torch.tensor([
            box["cx"] / record.size[0], box["cy"] / record.size[1],
            box["width"] / diagonal, box["height"] / diagonal,
            math.sin(radians), math.cos(radians),
        ], dtype=torch.float32)
        return tensor, target


class RotatedBoxNet(nn.Module):
    """Small CNN regressor returning center, size and doubled-angle logits."""

    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 16, 3, stride=2, padding=1), nn.ReLU(),
            nn.Conv2d(16, 32, 3, stride=2, padding=1), nn.ReLU(),
            nn.Conv2d(32, 64, 3, stride=2, padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool2d(1), nn.Flatten(),
        )
        self.head = nn.Sequential(nn.Linear(64, 64), nn.ReLU(), nn.Linear(64, 6))

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        return self.head(self.features(images))


def _decode(raw: torch.Tensor, width: int, height: int) -> dict[str, float]:
    values = torch.sigmoid(raw[:4]).detach().cpu().tolist()
    angle = math.degrees(math.atan2(float(raw[4]), float(raw[5]))) / 2
    diagonal = math.hypot(width, height)
    return {"cx": values[0] * width, "cy": values[1] * height,
            "width": values[2] * diagonal, "height": values[3] * diagonal,
            "angle_deg": angle}


def _loss(raw: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    coordinates = nn.functional.smooth_l1_loss(torch.sigmoid(raw[:, :4]), targets[:, :4])
    angles = nn.functional.normalize(raw[:, 4:6], dim=1)
    orientation = nn.functional.mse_loss(angles, targets[:, 4:6])
    return coordinates + 0.1 * orientation


def _tensor_for_image(source_bytes: bytes, image_size: int) -> tuple[torch.Tensor, tuple[int, int]]:
    try:
        with Image.open(io.BytesIO(source_bytes)) as opened:
            size = opened.size
            resized = opened.convert("RGB").resize((image_size, image_size), Image.Resampling.BILINEAR)
            pixels = np.asarray(resized, dtype=np.uint8)
    except (OSError, ValueError) as exc:
        raise ValueError("Rotated inference source image is unreadable") from exc
    tensor = torch.from_numpy(np.ascontiguousarray(pixels.transpose(2, 0, 1))).float() / 255.0
    return tensor.unsqueeze(0), size


def _load_checkpoint(checkpoint: str | Path, device: str | torch.device) -> tuple[RotatedBoxNet, dict, str]:
    checkpoint = Path(checkpoint).expanduser()
    if checkpoint.is_symlink():
        raise ValueError("Rotated checkpoint needs a regular best_model.pt and model_meta.json")
    checkpoint = checkpoint.resolve()
    meta_path = checkpoint.parent / "model_meta.json"
    if checkpoint.is_symlink() or not checkpoint.is_file() or meta_path.is_symlink() or not meta_path.is_file():
        raise ValueError("Rotated checkpoint needs best_model.pt and model_meta.json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Rotated checkpoint metadata is invalid") from exc
    if (not isinstance(meta, dict) or meta.get("task") != "rotated_detection"
            or not isinstance(meta.get("class_name"), str)
            or type(meta.get("image_size")) is not int or not 16 <= meta["image_size"] <= 512):
        raise ValueError("Checkpoint task or rotated model metadata is invalid")
    checksum = _sha256(checkpoint)
    if meta.get("checkpoint_sha256") != checksum:
        raise ValueError("Rotated checkpoint SHA-256 differs from metadata")
    try:
        payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    except Exception as exc:
        raise ValueError("Rotated checkpoint cannot be loaded safely") from exc
    if (not isinstance(payload, dict) or payload.get("task") != "rotated_detection"
            or payload.get("class_name") != meta["class_name"]
            or payload.get("image_size") != meta["image_size"]):
        raise ValueError("Rotated checkpoint signature differs from metadata")
    model = RotatedBoxNet()
    state = payload.get("model_state_dict")
    expected = model.state_dict()
    if (not isinstance(state, dict) or set(state) != set(expected)
            or any(not isinstance(value, torch.Tensor) or value.shape != expected[name].shape
                   or value.dtype != expected[name].dtype for name, value in state.items())):
        raise ValueError("Rotated checkpoint model weights are incompatible")
    model.load_state_dict(state, strict=True)
    model.to(device).eval()
    return model, meta, checksum


def predict_rotated_box(
    checkpoint: str | Path, image: str | Path, *, device: str | torch.device = "cpu",
) -> dict[str, Any]:
    """Predict one rotated box in the supplied image's original pixel space."""
    model, meta, checksum = _load_checkpoint(checkpoint, device)
    source = Path(image).expanduser()
    if source.is_symlink() or not source.is_file():
        raise ValueError("Rotated inference needs a regular source image")
    source = source.resolve()
    source_bytes = source.read_bytes()
    tensor, size = _tensor_for_image(source_bytes, meta["image_size"])
    with torch.no_grad():
        box = _decode(model(tensor.to(device))[0], *size)
    polygon = _points(box).astype(float).tolist()
    corners = np.asarray(polygon)
    return {
        "task": "rotated_detection", "label": meta["class_name"],
        "box": box, "polygon": polygon,
        "axis_aligned_box": [float(corners[:, 0].min()), float(corners[:, 1].min()),
                             float(corners[:, 0].max()), float(corners[:, 1].max())],
        "image_size": [size[0], size[1]],
        "source_image": str(source),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "model_sha256": checksum,
    }


def _angle_error(predicted: float, target: float) -> float:
    return abs((predicted - target + 90) % 180 - 90)


def _evaluate_model(model: RotatedBoxNet, manifest: RotatedBoxManifest, split: str,
                    image_size: int, device: str | torch.device) -> dict[str, Any]:
    dataset = RotatedBoxDataset(manifest, split=split, image_size=image_size)
    if not dataset:
        raise ValueError(f"Rotated {split} split has no samples")
    ious: list[float] = []
    angles: list[float] = []
    model.eval()
    with torch.no_grad():
        for index, record in enumerate(dataset.records):
            image, _ = dataset[index]
            box = _decode(model(image.unsqueeze(0).to(device))[0], *record.size)
            ious.append(oriented_iou(box, record.box))
            angles.append(_angle_error(box["angle_deg"], record.box["angle_deg"]))
    return {"split": split, "sample_count": len(ious),
            "mean_oriented_iou": float(np.mean(ious)),
            "mean_angle_error_deg": float(np.mean(angles)),
            "dataset_sha256": manifest.provenance["dataset_sha256"]}


def evaluate_rotated_detector(
    checkpoint: str | Path, root: str | Path, *, split: str = "test",
    device: str | torch.device = "cpu",
) -> dict[str, Any]:
    """Measure oriented IoU and modulo-180 angle error on a held-out split."""
    if split not in ("val", "test"):
        raise ValueError("Rotated evaluation requires a held-out val or test split")
    model, meta, checksum = _load_checkpoint(checkpoint, device)
    manifest = load_rotated_manifest(root)
    if (meta.get("dataset_sha256") != manifest.provenance["dataset_sha256"]
            or meta["class_name"] != manifest.class_name):
        raise ValueError("Rotated evaluation dataset differs from checkpoint source")
    result = _evaluate_model(model, manifest, split, meta["image_size"], device)
    result["model_sha256"] = checksum
    return result


def train_rotated_detector(
    root: str | Path, output_dir: str | Path, *, epochs: int = 3,
    batch_size: int = 8, image_size: int = 64, learning_rate: float = 1e-3,
    device: str | torch.device = "cpu",
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Train a small single-object regressor; select the best validation IoU."""
    if type(epochs) is not int or epochs < 1 or type(batch_size) is not int or batch_size < 1:
        raise ValueError("Rotated epochs and batch_size must be positive integers")
    if type(image_size) is not int or not 16 <= image_size <= 512:
        raise ValueError("Rotated image_size must be an integer from 16 to 512")
    if not isinstance(learning_rate, (int, float)) or not math.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("Rotated learning_rate must be positive")
    if cancel_event is not None and cancel_event.is_set():
        raise RotatedTrainingCancelled()
    manifest = load_rotated_manifest(root)
    train = RotatedBoxDataset(manifest, split="train", image_size=image_size)
    loader = DataLoader(train, batch_size=batch_size, shuffle=True, num_workers=0)
    model = RotatedBoxNet().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / "best_model.pt"
    best_score = -1.0
    best_metrics: dict[str, Any] = {}
    try:
        for _ in range(epochs):
            if cancel_event is not None and cancel_event.is_set():
                raise RotatedTrainingCancelled()
            model.train()
            for images, targets in loader:
                if cancel_event is not None and cancel_event.is_set():
                    raise RotatedTrainingCancelled()
                optimizer.zero_grad(set_to_none=True)
                loss = _loss(model(images.to(device)), targets.to(device))
                loss.backward()
                optimizer.step()
            if cancel_event is not None and cancel_event.is_set():
                raise RotatedTrainingCancelled()
            metrics = _evaluate_model(model, manifest, "val", image_size, device)
            if cancel_event is not None and cancel_event.is_set():
                raise RotatedTrainingCancelled()
            if metrics["mean_oriented_iou"] > best_score:
                best_score = metrics["mean_oriented_iou"]
                best_metrics = metrics
                torch.save({"task": "rotated_detection", "class_name": manifest.class_name,
                            "image_size": image_size, "model_state_dict": model.state_dict()}, checkpoint)
        if cancel_event is not None and cancel_event.is_set():
            raise RotatedTrainingCancelled()
    except RotatedTrainingCancelled:
        checkpoint.unlink(missing_ok=True)
        (output / "model_meta.json").unlink(missing_ok=True)
        (output / "job_receipt.json").unlink(missing_ok=True)
        raise
    checksum = _sha256(checkpoint)
    meta = {"task": "rotated_detection", "class_name": manifest.class_name,
            "image_size": image_size, "checkpoint_sha256": checksum,
            "dataset_sha256": manifest.provenance["dataset_sha256"],
            "manifest_sha256": manifest.provenance["manifest_sha256"],
            "split_counts": manifest.provenance["split_counts"],
            "validation": best_metrics}
    (output / "model_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    receipt = {"status": "completed", "task": "rotated_detection",
               "epochs_completed": epochs, "checkpoint_sha256": checksum,
               "dataset_sha256": manifest.provenance["dataset_sha256"],
               "validation": best_metrics}
    (output / "job_receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt
