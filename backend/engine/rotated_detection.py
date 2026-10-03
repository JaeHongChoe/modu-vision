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
Version 2 adds explicit per-image objects, classes and a bounded multi-slot detector; v1 models remain readable.
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
    direction_deg: float | None = None


@dataclass(frozen=True)
class RotatedImageRecord:
    image: str
    path: Path
    source_sha256: str
    split: str
    size: tuple[int, int]


@dataclass(frozen=True)
class RotatedBoxManifest:
    root: Path
    class_name: str
    records: tuple[RotatedBoxRecord, ...]
    provenance: dict[str, Any]
    version: int = 1
    images: tuple[RotatedImageRecord, ...] = ()

    @property
    def direction_enabled(self):
        return bool(self.records and self.records[0].direction_deg is not None)

    @property
    def class_names(self):
        return tuple(sorted({record.label for record in self.records}))


def _load_rotated_manifest_path(root: str | Path, manifest_path: Path) -> RotatedBoxManifest:
    """Verify every source byte, image, oriented target and split assignment."""
    root = Path(root).expanduser().resolve()
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Rotated dataset needs a regular rotated_boxes.json")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid rotated_boxes.json: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") not in (1, 2):
        raise ValueError("rotated_boxes.json must use version 1")
    rows = raw.get("samples")
    if not isinstance(rows, list) or not rows:
        raise ValueError("rotated_boxes.json needs one-object image samples")
    records: list[RotatedBoxRecord] = []
    images: list[RotatedImageRecord] = []
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
        if not isinstance(split,str) or split not in _SPLITS:
            raise ValueError(f"Rotated sample {index} split must be train, val or test")
        existing_split = hashes_by_split.setdefault(actual, split)
        if existing_split != split:
            raise ValueError("Byte-identical rotated source images occur in different split partitions")
        objects = row.get("objects") if raw["version"] == 2 else [row]
        if not isinstance(objects,list):
            raise ValueError('Rotated objects must be an explicit list; empty lists represent verified background')
        images.append(RotatedImageRecord(image,path,actual,split,size))
        for obj in objects:
            if not isinstance(obj,dict): raise ValueError('Rotated object must be an object')
            label = obj.get("label")
            if not isinstance(label, str) or not label.strip() or label != label.strip():
                raise ValueError(f"Rotated sample {index} needs a nonempty label")
            labels.add(label)
            box = _valid_box(obj.get("box"), *size)
            direction=obj.get('direction_deg')
            if direction is not None and (isinstance(direction,bool) or not isinstance(direction,(int,float)) or not math.isfinite(direction) or not 0<=direction<360):
                raise ValueError('Object direction must be a finite independent target in [0,360)')
            records.append(RotatedBoxRecord(image, path, actual, split, label, box, size, float(direction) if direction is not None else None))
        counts[split] += 1
    if any(r.direction_deg is not None for r in records) and any(r.direction_deg is None for r in records):
        raise ValueError('Independent direction requires explicit targets for every object; it is never inferred from axial OBB angle')
    if raw["version"] == 1 and len(labels) != 1:
        raise ValueError("Single-object rotated model supports exactly one label class")
    if not counts["train"] or not counts["val"]:
        raise ValueError("Rotated training requires separate nonempty train and val splits")
    if not labels or not any(record.split=='train' for record in records):
        raise ValueError('Rotated training requires at least one explicit train object class')
    manifest_sha = _sha256(manifest_path)
    digest = hashlib.sha256()
    digest.update(b"rotated-detection-single-object-v1\0")
    digest.update(manifest_sha.encode("ascii"))
    # Preserve v1/v2 dataset digests for existing nonempty fixed-slot models.
    object_images={record.image for record in records}
    digest_records=list(records)+[image for image in images if image.image not in object_images]
    for record in sorted(digest_records, key=lambda item: item.image):
        digest.update(b"\0")
        digest.update(record.image.encode("utf-8"))
        digest.update(b"\0")
        digest.update(record.source_sha256.encode("ascii"))
    provenance = {
        "dataset_sha256": f"sha256:{digest.hexdigest()}",
        "manifest_sha256": manifest_sha,
        "source_sha256": {row.image: row.source_sha256 for row in images},
        "split_counts": counts,
        "source_image_count": len(seen_images), "object_count": len(records),
        "direction_enabled": any(r.direction_deg is not None for r in records),
    }
    from backend.engine.prepared_family_datasets import prepared_source_provenance
    provenance.update(prepared_source_provenance(root, raw, provenance['source_sha256']))
    return RotatedBoxManifest(root, sorted(labels)[0], tuple(records), provenance, raw["version"],tuple(images))


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
                       **({"objects":row["objects"]} if "objects" in row else {"label": row.get("label"), "box": row.get("box"), **({"direction_deg":row["direction_deg"]} if row.get("direction_deg") is not None else {})})})
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                         prefix=".rotated_boxes-", suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            json.dump({"version": 2 if any("objects" in row for row in pinned) else 1, "samples": pinned}, temporary, ensure_ascii=False, indent=2)
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
        if record.direction_deg is not None:
            direction=math.radians(record.direction_deg)
            target=torch.cat([target,torch.tensor([math.sin(direction),math.cos(direction)],dtype=torch.float32)])
        return tensor, target


class RotatedBoxNet(nn.Module):
    """Small CNN regressor returning center, size and doubled-angle logits."""

    def __init__(self, direction_enabled=False):
        super().__init__()
        self.direction_enabled=bool(direction_enabled)
        self.features = nn.Sequential(
            nn.Conv2d(3, 16, 3, stride=2, padding=1), nn.ReLU(),
            nn.Conv2d(16, 32, 3, stride=2, padding=1), nn.ReLU(),
            nn.Conv2d(32, 64, 3, stride=2, padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool2d(1), nn.Flatten(),
        )
        self.head = nn.Sequential(nn.Linear(64, 64), nn.ReLU(), nn.Linear(64, 8 if self.direction_enabled else 6))

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
    loss=coordinates + 0.1 * orientation
    if targets.shape[-1]==8:
        loss=loss+0.1*nn.functional.mse_loss(nn.functional.normalize(raw[:,6:8],dim=1),targets[:,6:8])
    return loss


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
    if meta.get("version") == 2:
        if (payload.get("version") != 2 or payload.get("class_names") != meta.get("class_names")
                or payload.get("max_objects") != meta.get("max_objects")
                or not isinstance(meta.get("class_names"),list) or not meta["class_names"]
                or type(meta.get("max_objects")) is not int or not 1<=meta["max_objects"]<=32):
            raise ValueError("Rotated multi-object class or capacity metadata differs from checkpoint")
    direction_enabled=meta.get('direction_enabled',False)
    if type(direction_enabled) is not bool or payload.get('direction_enabled',False)!=direction_enabled:
        raise ValueError('Independent direction metadata differs from checkpoint')
    model = RotatedMultiBoxNet(len(meta["class_names"]), meta["max_objects"],direction_enabled) if meta.get("version") == 2 else RotatedBoxNet(direction_enabled)
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
    checkpoint: str | Path, image: str | Path, *, device: str | torch.device = "cpu", threshold: float = 0.5,
) -> dict[str, Any]:
    """Predict one rotated box in the supplied image's original pixel space."""
    from backend.engine.yolo_obb_adapter import adapter_metadata, predict_yolo_array
    yolo_meta=adapter_metadata(checkpoint)
    if yolo_meta is not None:
        source=Path(image).expanduser()
        if source.is_symlink() or not source.is_file(): raise ValueError('Rotated inference needs a regular source image')
        data=source.read_bytes()
        with Image.open(io.BytesIO(data)) as opened: result=predict_yolo_array(checkpoint,np.asarray(opened.convert('RGB')),device=device,threshold=threshold,meta=yolo_meta)
        result.update(source_image=str(source.resolve()),source_sha256=hashlib.sha256(data).hexdigest())
        return result
    model, meta, checksum = _load_checkpoint(checkpoint, device)
    source = Path(image).expanduser()
    if source.is_symlink() or not source.is_file():
        raise ValueError("Rotated inference needs a regular source image")
    source = source.resolve()
    source_bytes = source.read_bytes()
    if meta.get("version") == 2:
        with Image.open(io.BytesIO(source_bytes)) as opened:
            prediction = predict_rotated_array(checkpoint,np.asarray(opened.convert("RGB")),device=device,threshold=threshold)
        prediction.update(source_image=str(source),source_sha256=hashlib.sha256(source_bytes).hexdigest())
        return prediction
    tensor, size = _tensor_for_image(source_bytes, meta["image_size"])
    with torch.no_grad():
        raw=model(tensor.to(device))[0]
        box = _decode(raw, *size)
    polygon = _points(box).astype(float).tolist()
    corners = np.asarray(polygon)
    return {
        "task": "rotated_detection", "label": meta["class_name"],
        "box": box, "polygon": polygon,
        **({"direction_deg":_decode_direction(raw[-2:]),"direction_encoding":"unit_vector_360"} if meta.get("direction_enabled") else {}),
        "axis_aligned_box": [float(corners[:, 0].min()), float(corners[:, 1].min()),
                             float(corners[:, 0].max()), float(corners[:, 1].max())],
        "image_size": [size[0], size[1]],
        "source_image": str(source),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "model_sha256": checksum,
    }


def _decode_direction(raw):
    return math.degrees(math.atan2(float(raw[0]),float(raw[1])))%360


def _direction_error(predicted,target):
    return abs((predicted-target+180)%360-180)


def _angle_error(predicted: float, target: float) -> float:
    return abs((predicted - target + 90) % 180 - 90)


def _evaluate_model(model: RotatedBoxNet, manifest: RotatedBoxManifest, split: str,
                    image_size: int, device: str | torch.device) -> dict[str, Any]:
    from backend.engine.evaluation_evidence import match_objects,object_average_precision
    dataset = RotatedBoxDataset(manifest, split=split, image_size=image_size)
    if not dataset:
        raise ValueError(f"Rotated {split} split has no samples")
    ious: list[float] = []
    angles: list[float] = []
    samples=[];directions=[]
    model.eval()
    with torch.no_grad():
        for index, record in enumerate(dataset.records):
            image, _ = dataset[index]
            raw=model(image.unsqueeze(0).to(device))[0]
            box = _decode(raw, *record.size)
            direction=_decode_direction(raw[-2:]) if getattr(model,'direction_enabled',False) else None
            if direction is not None and record.direction_deg is not None:directions.append(_direction_error(direction,record.direction_deg))
            ious.append(oriented_iou(box, record.box))
            angles.append(_angle_error(box["angle_deg"], record.box["angle_deg"]))
            evidence=match_objects([{'label':record.label,'box':box,'confidence':1.}], [{'label':record.label,'box':record.box}])
            samples.append({'image':record.image,'file_path':str(record.path),'source_sha256':record.source_sha256,
                'ground_truth':record.label,'predicted_class':record.label,'confidence':1.,'is_correct':evidence['counts']['fp']==0 and evidence['counts']['fn']==0,
                'object_evidence':{**evidence,'coordinate_space':'original_image','source_size':list(record.size)},
                'oriented_iou':ious[-1],'angle_error_deg':angles[-1],**({'direction_deg':direction,'ground_truth_direction_deg':record.direction_deg,'direction_error_deg':directions[-1]} if directions and direction is not None and record.direction_deg is not None else {})})
    return {"split": split, "sample_count": len(ious),
            "mean_oriented_iou": float(np.mean(ious)),
            "mean_angle_error_deg": float(np.mean(angles)),
            "dataset_sha256": manifest.provenance["dataset_sha256"],'test_predictions':samples,**({"mean_direction_error_deg":float(np.mean(directions)),"direction_sample_count":len(directions)} if directions else {}),**object_average_precision(samples)}


def evaluate_rotated_detector(
    checkpoint: str | Path, root: str | Path, *, split: str = "test",
    device: str | torch.device = "cpu", allow_dataset_revision: bool = False,
) -> dict[str, Any]:
    """Measure oriented IoU and modulo-180 angle error on a held-out split."""
    if split not in ("val", "test"):
        raise ValueError("Rotated evaluation requires a held-out val or test split")
    from backend.engine.yolo_obb_adapter import adapter_metadata, evaluate_yolo
    if adapter_metadata(checkpoint) is not None:
        return evaluate_yolo(checkpoint,load_rotated_manifest(root),split=split,device=device,allow_dataset_revision=allow_dataset_revision)
    model, meta, checksum = _load_checkpoint(checkpoint, device)
    manifest = load_rotated_manifest(root)
    revised = meta.get("dataset_sha256") != manifest.provenance["dataset_sha256"]
    if revised and not allow_dataset_revision:
        raise ValueError("Rotated evaluation dataset differs from checkpoint source")
    trained_classes = meta.get("class_names", [meta["class_name"]])
    if not set(manifest.class_names).issubset(trained_classes):
        raise ValueError("Rotated evaluation class differs from checkpoint")
    if revised and manifest.provenance["source_sha256"] != meta.get("provenance", {}).get("source_sha256"):
        raise ValueError("Rotated source images differ from checkpoint lineage")
    result = _evaluate_multi(model,manifest,split,meta,device) if meta.get("version")==2 else _evaluate_model(model, manifest, split, meta["image_size"], device)
    result["model_sha256"] = checksum
    result["training_dataset_sha256"] = meta.get("dataset_sha256")
    result["dataset_revision_changed"] = revised
    return result


def train_rotated_detector(
    root: str | Path, output_dir: str | Path, *, epochs: int = 3,
    batch_size: int = 8, image_size: int = 64, learning_rate: float = 1e-3,
    device: str | torch.device = "cpu",
    cancel_event: threading.Event | None = None,
    warm_start=None,
    recipe=None,
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
    from backend.engine.yolo_obb_adapter import OBBRecipe, train_yolo, validate_training_recipe
    recipe=OBBRecipe.from_value(recipe)
    validate_training_recipe(manifest,recipe,warm_start=warm_start)
    if recipe.adapter=='ultralytics_yolo_obb':
        return train_yolo(manifest,output_dir,recipe,epochs=epochs,batch_size=batch_size,image_size=image_size,learning_rate=learning_rate,device=device,cancel_event=cancel_event)
    if manifest.version == 2:
        return _train_multi(manifest,output_dir,epochs,batch_size,image_size,learning_rate,device,cancel_event,warm_start)
    train = RotatedBoxDataset(manifest, split="train", image_size=image_size)
    loader = DataLoader(train, batch_size=batch_size, shuffle=True, num_workers=0)
    model = RotatedBoxNet(manifest.direction_enabled).to(device)
    lineage = {}
    if warm_start is not None:
        from backend.engine.specialized_warm_start import load_family_weights, requested_signature, require_new_candidate
        require_new_candidate(output_dir, warm_start)
        load_family_weights({'model_state_dict': model}, warm_start,
                            requested_signature('rotated_detection', root, {'image_size': image_size}))
        lineage = {'warm_start': warm_start.lineage()}
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
                            "image_size": image_size, "direction_enabled":manifest.direction_enabled,"model_state_dict": model.state_dict(), **lineage}, checkpoint)
        if cancel_event is not None and cancel_event.is_set():
            raise RotatedTrainingCancelled()
    except RotatedTrainingCancelled:
        checkpoint.unlink(missing_ok=True)
        (output / "model_meta.json").unlink(missing_ok=True)
        (output / "job_receipt.json").unlink(missing_ok=True)
        raise
    checksum = _sha256(checkpoint)
    meta = {"task": "rotated_detection", "class_name": manifest.class_name,"direction_enabled":manifest.direction_enabled,"direction_encoding":"unit_vector_360" if manifest.direction_enabled else None,
            "image_size": image_size, "checkpoint_sha256": checksum,
            "dataset_sha256": manifest.provenance["dataset_sha256"],
            "manifest_sha256": manifest.provenance["manifest_sha256"],
            "dataset_path": str(manifest.root), "provenance": manifest.provenance,
            "split_counts": manifest.provenance["split_counts"],
            "validation": best_metrics, **lineage}
    (output / "model_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    receipt = {"status": "completed", "task": "rotated_detection",
               "epochs_completed": epochs, "checkpoint_sha256": checksum,
               "dataset_sha256": manifest.provenance["dataset_sha256"],
               "validation": best_metrics}
    (output / "job_receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


class RotatedMultiBoxNet(nn.Module):
    """Bounded multi-object CNN with objectness, oriented geometry and class logits."""
    def __init__(self,class_count,max_objects,direction_enabled=False):
        super().__init__()
        self.class_count,self.max_objects=class_count,max_objects
        self.direction_enabled=bool(direction_enabled);self.output_size=7+class_count+(2 if self.direction_enabled else 0)
        self.features=RotatedBoxNet().features
        self.head=nn.Sequential(nn.Linear(64,128),nn.ReLU(),nn.Linear(128,max_objects*self.output_size))
    def forward(self,images):
        return self.head(self.features(images)).reshape(-1,self.max_objects,self.output_size)


class RotatedMultiDataset(Dataset):
    def __init__(self,manifest,split,image_size,max_objects):
        self.groups={}
        for record in manifest.records:
            if record.split==split: self.groups.setdefault(record.image,[]).append(record)
        self.groups=list(self.groups.values());self.image_size=image_size;self.max_objects=max_objects
        self.classes=manifest.class_names
    def __len__(self): return len(self.groups)
    def __getitem__(self,index):
        records=sorted(self.groups[index],key=lambda r:(r.box['cx'],r.box['cy'],r.label))
        first=records[0]
        data=first.path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=first.source_sha256: raise ValueError('Rotated source changed after validation')
        image,_=_tensor_for_image(data,self.image_size)
        targets=torch.zeros(self.max_objects,10 if records[0].direction_deg is not None else 8)
        for slot,r in enumerate(records):
            box=r.box; diagonal=math.hypot(*r.size); angle=math.radians(2*box['angle_deg'])
            values=[box['cx']/r.size[0],box['cy']/r.size[1],box['width']/diagonal,box['height']/diagonal,math.sin(angle),math.cos(angle),1,self.classes.index(r.label)]
            if r.direction_deg is not None:values.extend([math.sin(math.radians(r.direction_deg)),math.cos(math.radians(r.direction_deg))])
            targets[slot]=torch.tensor(values)
        return image[0],targets


def _multi_loss(raw,targets):
    present=targets[:,:,6]>0
    objectness=nn.functional.binary_cross_entropy_with_logits(raw[:,:,6],targets[:,:,6])
    if not present.any(): return objectness
    geometry=_loss(raw[present][:,:6],targets[present][:,:6])
    logits=raw[present][:,7:-2] if targets.shape[-1]==10 else raw[present][:,7:]
    classes=nn.functional.cross_entropy(logits,targets[present][:,7].long())
    loss=objectness+geometry+classes
    if targets.shape[-1]==10:loss=loss+0.1*nn.functional.mse_loss(nn.functional.normalize(raw[present][:,-2:],dim=1),targets[present][:,8:10])
    return loss


def _multi_predictions(raw,size,meta,threshold):
    detections=[]
    for slot in raw:
        confidence=float(torch.sigmoid(slot[6]).item())
        if confidence<threshold: continue
        probabilities=torch.softmax(slot[7:-2] if meta.get("direction_enabled") else slot[7:],dim=0)
        index=int(probabilities.argmax().item())
        box=_decode(slot[:6],*size); polygon=_points(box).astype(float).tolist()
        points=np.asarray(polygon)
        candidate={'label':meta['class_names'][index],'confidence':confidence,'class_confidence':float(probabilities[index]),'box':box,'polygon':polygon,'axis_aligned_box':[float(points[:,0].min()),float(points[:,1].min()),float(points[:,0].max()),float(points[:,1].max())]}
        if meta.get('direction_enabled'):candidate.update(direction_deg=_decode_direction(slot[-2:]),direction_encoding='unit_vector_360')
        if any(d['label']==candidate['label'] and oriented_iou(d['box'],box)>0.5 for d in detections): continue
        detections.append(candidate)
    return detections


def predict_rotated_array(checkpoint,image_rgb,*,device='cpu',threshold=0.5):
    if not 0<=threshold<=1: raise ValueError('Rotated threshold must be [0,1]')
    from backend.engine.yolo_obb_adapter import adapter_metadata, predict_yolo_array
    meta=adapter_metadata(checkpoint)
    if meta is not None: return predict_yolo_array(checkpoint,image_rgb,device=device,threshold=threshold,meta=meta)
    model,meta,checksum=_load_checkpoint(checkpoint,device)
    height,width=image_rgb.shape[:2]
    pixels=cv2.resize(image_rgb,(meta['image_size'],meta['image_size']))
    tensor=torch.from_numpy(np.ascontiguousarray(pixels.transpose(2,0,1))).float().unsqueeze(0)/255
    with torch.inference_mode(): raw=model(tensor.to(device))[0]
    if meta.get('version')==2: detections=_multi_predictions(raw,(width,height),meta,threshold)
    else:
        box=_decode(raw,width,height);polygon=_points(box).astype(float).tolist();points=np.asarray(polygon)
        detections=[{'label':meta['class_name'],'confidence':1.0,'box':box,'polygon':polygon,'axis_aligned_box':[float(points[:,0].min()),float(points[:,1].min()),float(points[:,0].max()),float(points[:,1].max())],**({'direction_deg':_decode_direction(raw[-2:])} if meta.get('direction_enabled') else {})}]
    return {'task':'rotated_detection','detections':detections,'image_size':[width,height],'model_sha256':checksum}


def _evaluate_multi(model,manifest,split,meta,device):
    from backend.engine.evaluation_evidence import match_objects,object_average_precision
    data=RotatedMultiDataset(manifest,split,meta['image_size'],meta['max_objects'])
    if not len(data): raise ValueError(f'Rotated {split} split has no samples')
    matched=predicted=truth=0;ious=[];angles=[];samples=[];directions=[]
    with torch.inference_mode():
        for records in data.groups:
            # Evaluation truth may contain newly corrected objects beyond the
            # old model's fixed slot count; all must count toward recall.
            first=records[0];source_bytes=first.path.read_bytes()
            if hashlib.sha256(source_bytes).hexdigest()!=first.source_sha256:
                raise ValueError('Rotated source changed after validation')
            image,_=_tensor_for_image(source_bytes,meta['image_size'])
            predictions=_multi_predictions(model(image.to(device))[0],first.size,meta,0.)
            targets=[{'label':r.label,'box':r.box,**({'direction_deg':r.direction_deg} if r.direction_deg is not None else {})} for r in records]
            evidence=match_objects(predictions,targets)
            if meta.get('direction_enabled'):
                for pair in evidence['matches']:
                    predicted_index=pair.get('prediction_index',pair.get('predicted_index'));truth_index=pair.get('truth_index',pair.get('target_index'))
                    if predicted_index is not None and truth_index is not None and records[truth_index].direction_deg is not None:
                        directions.append(_direction_error(predictions[predicted_index]['direction_deg'],records[truth_index].direction_deg))
            matched+=evidence['counts']['tp'];predicted+=evidence['counts']['tp']+evidence['counts']['fp'];truth+=len(records)
            ious.extend(row['iou'] for row in evidence['matches']);angles.extend(row['angle_error_deg'] for row in evidence['matches'])
            samples.append({'image':first.image,'file_path':str(first.path),'source_sha256':first.source_sha256,
                'ground_truth_classes':sorted({r.label for r in records}),'confidence':max((p['confidence'] for p in predictions),default=0),
                'is_correct':evidence['counts']['fp']==0 and evidence['counts']['fn']==0,
                'object_evidence':{**evidence,'coordinate_space':'original_image','source_size':list(first.size)}})
    return {'split':split,'sample_count':len(data),'ground_truth_objects':truth,'predicted_objects':predicted,'precision':matched/max(1,predicted),'recall':matched/max(1,truth),'mean_oriented_iou':float(np.mean(ious)) if ious else 0.0,'mean_angle_error_deg':float(np.mean(angles)) if angles else 90.0,'dataset_sha256':manifest.provenance['dataset_sha256'],'test_predictions':samples,**({'mean_direction_error_deg':float(np.mean(directions)) if directions else None,'direction_matched_count':len(directions)} if meta.get('direction_enabled') else {}),**object_average_precision(samples)}


def _train_multi(manifest,output_dir,epochs,batch_size,image_size,learning_rate,device,cancel_event,warm_start=None):
    groups={}
    for r in manifest.records: groups[r.image]=groups.get(r.image,0)+1
    max_objects=max(groups.values())
    meta={'task':'rotated_detection','direction_enabled':manifest.direction_enabled,'direction_encoding':'unit_vector_360' if manifest.direction_enabled else None,'version':2,'class_name':manifest.class_name,'class_names':list(manifest.class_names),'max_objects':max_objects,'image_size':image_size,'dataset_sha256':manifest.provenance['dataset_sha256'],'dataset_path':str(manifest.root),'provenance':manifest.provenance}
    data=RotatedMultiDataset(manifest,'train',image_size,max_objects)
    model=RotatedMultiBoxNet(len(manifest.class_names),max_objects,manifest.direction_enabled).to(device)
    if warm_start is not None:
        from backend.engine.specialized_warm_start import load_family_weights, requested_signature, require_new_candidate
        require_new_candidate(output_dir, warm_start)
        load_family_weights({'model_state_dict': model}, warm_start,
                            requested_signature('rotated_detection', manifest.root, {'image_size': image_size}))
        meta['warm_start'] = warm_start.lineage()
    optimizer=torch.optim.Adam(model.parameters(),lr=learning_rate)
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=True);checkpoint=output/'best_model.pt'
    best=-1
    try:
        for epoch in range(epochs):
            model.train()
            for images,targets in DataLoader(data,batch_size=batch_size,shuffle=True):
                if cancel_event is not None and cancel_event.is_set(): raise RotatedTrainingCancelled()
                optimizer.zero_grad();loss=_multi_loss(model(images.to(device)),targets.to(device));loss.backward();optimizer.step()
            model.eval();metrics=_evaluate_multi(model,manifest,'val',meta,device)
            if metrics['mean_oriented_iou']>best:
                best=metrics['mean_oriented_iou'];meta['validation']=metrics
                torch.save({**meta,'model_state_dict':model.state_dict()},checkpoint)
        if cancel_event is not None and cancel_event.is_set(): raise RotatedTrainingCancelled()
    except RotatedTrainingCancelled:
        checkpoint.unlink(missing_ok=True);raise
    meta['checkpoint_sha256']=_sha256(checkpoint)
    (output/'model_meta.json').write_text(json.dumps(meta),encoding='utf-8')
    receipt={'status':'completed','task':'rotated_detection','epochs_completed':epochs,'checkpoint_sha256':meta['checkpoint_sha256'],'dataset_sha256':meta['dataset_sha256'],'validation':meta['validation']}
    (output/'job_receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
    return receipt
