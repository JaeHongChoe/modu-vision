"""
backend/api/routes_evaluation.py

Evaluation Results, Interactive Clickable Confusion Matrix & Heatmap Overlays.
100% Genuine PyTorch Engine Execution:
  - Dynamically runs evaluation on actual dataset images using trained checkpoints
  - Computes authentic Confusion Matrix & per-class metrics
  - Populates cell_samples with real, existing image file paths on disk
  - Generates real Grad-CAM / BBox / Mask / Anomaly overlays via infer()
  - Zero synthetic math formulas, zero hardcoded metrics, zero fictitious filenames
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import cv2
import numpy as np
import torch
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from PIL import Image

from backend.api.routes_training import training_job_manager
from backend.engine.classification import (
    create_classification_model,
    compute_classification_metrics,
)
from backend.engine.detection import (
    create_detection_model,
    evaluate_detections_map,
    checkpoint_detection_num_classes,
    foreground_class_names,
)
from backend.engine.segmentation import (
    build_segmentation_model,
    compute_segmentation_metrics,
)
from backend.engine.anomaly import (
    PaDiMDetector,
    PatchCoreDetector,
    compute_anomaly_metrics,
    reconstruct_anomaly_detector,
)
from backend.engine.dataset_loaders import (
    SUPPORTED_IMAGE_EXTENSIONS,
    ClassificationDataset,
    DetectionDataset,
    SegmentationDataset,
    AnomalyDataset,
    _read_image_rgb,
)
from backend.engine.device import get_device
from backend.engine.checkpoint_paths import (
    active_project_models_dir,
    completed_job_receipt,
    is_job_id,
    trusted_checkpoint,
)
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.patch_classification import load_patch_manifest
from backend.engine.annotation_storage import dataset_annotation_dir, scoped_annotation_root
from backend.engine.trainer import infer
from backend.utils.error_catalog import format_error_response

logger = logging.getLogger("vision_ai_studio.routes_evaluation")
EVALUATION_CONTRACT_VERSION = 2

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])

_eval_file_lock = threading.Lock()


def _atomic_write_json(file_path: Union[str, Path], data: Any) -> None:
    """Atomically write data to a JSON file via a temporary file + os.replace."""
    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = p.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, p)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _find_image_file(image_id: str, file_path: Optional[str] = None) -> Optional[Path]:
    """Search project and dataset directories for image file matching image_id or filename."""
    if file_path and Path(file_path).is_file():
        return Path(file_path)
    p_id = Path(image_id)
    if p_id.is_file():
        return p_id

    # Fast bounded search directories (OS temp directories explicitly excluded)
    search_dirs = [
        Path("./datasets"),
        Path("./projects"),
        Path("./models"),
        Path.cwd(),
    ]
    for s_dir in search_dirs:
        if not s_dir.exists():
            continue
        # Direct check
        cand = s_dir / image_id
        if cand.is_file():
            return cand
        for ext in SUPPORTED_IMAGE_EXTENSIONS:
            cand = s_dir / f"{image_id}{ext}"
            if cand.is_file():
                return cand
        # Fast bounded rglob
        for ext in ["", *SUPPORTED_IMAGE_EXTENSIONS]:
            try:
                for p in s_dir.rglob(f"{image_id}{ext}"):
                    if p.is_file():
                        return p
            except Exception:
                pass
    return None


def _record_for_selected_checkpoint(job_id: str):
    """A restored project retains job IDs, but has its own receipts and files."""
    record = training_job_manager.get_job(job_id)
    project_models = active_project_models_dir()
    if record is not None and project_models is not None:
        checkpoint = trusted_checkpoint(job_id)
        selected_job = (project_models / job_id).resolve()
        if (checkpoint is not None and checkpoint.parent == selected_job
                and Path(record.output_dir).expanduser().resolve() != selected_job):
            # A live record from the original project must not override the
            # completed checkpoint copied into the selected project.
            return None
    return record


def _find_model_file(job_id: Optional[str] = None) -> Optional[Path]:
    """Locates only the explicitly selected training job's checkpoint."""
    if not job_id:
        return None
    rec = _record_for_selected_checkpoint(job_id)
    if rec and rec.status != "completed":
        return None
    return trusted_checkpoint(job_id, rec.output_dir if rec else None)


def _same_label_tree(first: Path, second: Path) -> bool:
    """Compare migrated labels by path and bytes; copy changes ctime in v1 hashes."""
    def inventory(root: Path) -> Optional[Dict[str, str]]:
        if not root.exists():
            return {}
        if root.is_symlink() or not root.is_dir():
            return None
        files: Dict[str, str] = {}
        for path in root.rglob("*"):
            if path.is_symlink():
                return None
            if path.is_file():
                if path.name.startswith(('.annotation-save-', '.annotation-atomic-')):
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                files[path.relative_to(root).as_posix()] = digest.hexdigest()
        return files

    first_files = inventory(first)
    second_files = inventory(second)
    return first_files is not None and second_files is not None and first_files == second_files


def _same_optional_file(first: Path, second: Path) -> bool:
    if first.is_symlink() or second.is_symlink():
        return False
    if not first.exists() and not second.exists():
        return True
    if not first.is_file() or not second.is_file():
        return False
    return hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()


def _matches_source_dataset(
    output_dir: Path,
    source_dataset_path: Optional[str],
    source_task: Optional[str],
    dataset_hint: Optional[str] = None,
    recorded_source: Optional[str] = None,
    recorded_fingerprint: Optional[str] = None,
) -> bool:
    """Match the original import folder, never the prepared evaluation dataset."""
    if not source_dataset_path and not source_task:
        return True
    receipt = completed_job_receipt(output_dir) or {}
    meta_path = output_dir / "model_meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    except (OSError, ValueError):
        meta = {}
    if source_task and (receipt.get("task") or meta.get("task")) != source_task:
        return False
    if not source_dataset_path:
        return True

    selected = Path(source_dataset_path).expanduser().resolve()
    original = receipt.get("source_dataset_path") or recorded_source
    expected_fingerprint = receipt.get("dataset_fingerprint") or recorded_fingerprint
    # Legacy receipts can identify a folder, but cannot prove it is unchanged.
    if not original or not isinstance(expected_fingerprint, str) or not expected_fingerprint.startswith("v1:"):
        return False
    original_path = Path(original).expanduser().resolve()
    if original_path != selected or original_path.is_relative_to(output_dir.resolve()):
        return False
    from backend.api import routes_dataset
    from backend.engine.annotation_storage import LEGACY_ANNOTATIONS_ROOT

    try:
        current_fingerprint = fingerprint_dataset(
            selected, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
            split_manifest=routes_dataset._split_manifest_file(selected),
        )
    except OSError:
        return False
    if current_fingerprint == expected_fingerprint:
        return True

    # A pre-project v1 receipt records the old overlay's ctime. Copying the
    # unchanged overlay into a project changes ctime, so compare its content
    # with the original only when the old fingerprint still matches exactly.
    project_root = scoped_annotation_root(routes_dataset.STUDIO_ANNOTATIONS_DIR)
    if project_root.resolve() == LEGACY_ANNOTATIONS_ROOT.resolve():
        return False
    legacy_split = routes_dataset.SPLIT_MANIFEST_DIR / (
        hashlib.sha256(str(selected).encode("utf-8")).hexdigest() + ".json"
    )
    try:
        legacy_fingerprint = fingerprint_dataset(
            selected, studio_root=LEGACY_ANNOTATIONS_ROOT,
            split_manifest=legacy_split, use_scope=False,
        )
        if legacy_fingerprint != expected_fingerprint:
            return False
        return _same_label_tree(
            dataset_annotation_dir(selected, LEGACY_ANNOTATIONS_ROOT, use_scope=False),
            dataset_annotation_dir(selected, project_root, use_scope=False),
        ) and _same_optional_file(legacy_split, routes_dataset._split_manifest_file(selected))
    except OSError:
        return False


def _resolve_job_artifacts(
    job_id: Optional[str] = None,
    dataset_path_override: Optional[str] = None,
    source_dataset_path: Optional[str] = None,
    source_task: Optional[str] = None,
) -> Tuple[Path, Path, Dict[str, Any], str, str, Path]:
    """
    Resolves (output_dir, model_pt, meta, task, resolved_job_id, dataset_path).
    Raises HTTPException 400 or 404 if job or artifacts are invalid.
    """
    if job_id and job_id not in ("latest", "current", "default"):
        if not is_job_id(job_id):
            raise HTTPException(status_code=404, detail=f"Evaluation data or model checkpoint not found for job: {job_id}")
        rec = _record_for_selected_checkpoint(job_id)
        if rec:
            if rec.status in ("queued", "running", "stopping", "disconnected"):
                raise HTTPException(status_code=400, detail=f"Training job '{job_id}' is still in progress")
            if rec.status in ("failed", "aborted"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Training job '{job_id}' did not complete successfully (status: {rec.status})",
                )
            model_pt = trusted_checkpoint(job_id, rec.output_dir)
            if model_pt is None:
                raise HTTPException(status_code=404, detail=f"Evaluation model checkpoint not found for job: {job_id}")
            out_dir = model_pt.parent
            if not _matches_source_dataset(
                out_dir, source_dataset_path, source_task, rec.dataset_path,
                getattr(rec, "source_dataset_path", None), getattr(rec, "dataset_fingerprint", None),
            ):
                raise HTTPException(status_code=404, detail=f"No completed model matches the selected dataset for job: {job_id}")
            task_hint = rec.task
            dataset_hint = Path(rec.dataset_path)
            resolved_job_id = rec.job_id
        else:
            model_pt = trusted_checkpoint(job_id)
            if model_pt is None:
                raise HTTPException(status_code=404, detail=f"Evaluation data or model checkpoint not found for job: {job_id}")
            out_dir = model_pt.parent
            if not _matches_source_dataset(out_dir, source_dataset_path, source_task):
                raise HTTPException(status_code=404, detail=f"No completed model matches the selected dataset for job: {job_id}")
            receipt = completed_job_receipt(out_dir) or {}
            task_hint = receipt.get("task")
            dataset_hint = Path(receipt["dataset_path"]) if receipt.get("dataset_path") else None
            resolved_job_id = job_id
    else:
        # Search for latest completed job
        completed_jobs = sorted(
            (r for r in training_job_manager._jobs.values() if r.status == "completed"),
            key=lambda r: getattr(r, "start_time", 0), reverse=True,
        )
        latest_rec = next(
            (r for r in completed_jobs
             if _record_for_selected_checkpoint(r.job_id) is r
             and trusted_checkpoint(r.job_id, r.output_dir) is not None
             and _matches_source_dataset(
                 Path(r.output_dir), source_dataset_path, source_task, r.dataset_path,
                 getattr(r, "source_dataset_path", None), getattr(r, "dataset_fingerprint", None),
             )),
            None,
        )
        if latest_rec:
            out_dir = trusted_checkpoint(latest_rec.job_id, latest_rec.output_dir).parent
            task_hint = latest_rec.task
            dataset_hint = Path(latest_rec.dataset_path)
            resolved_job_id = latest_rec.job_id
        else:
            # Desktop renderer and backend processes can restart independently.
            # Reopen the newest usable job checkpoint when in-memory records
            # are gone. A model metadata file is required to identify its task.
            project_models = active_project_models_dir()
            candidates = [
                p for p in (
                    *Path("./models").glob("job_*/best_model.pt"),
                    *Path("./projects").glob("job_*/models/best_model.pt"),
                    *(project_models.glob("job_*/best_model.pt") if project_models else ()),
                )
                if (p.parent / "model_meta.json").is_file()
                and trusted_checkpoint(
                    p.parent.name if p.parent.parent.name == "models" else p.parent.parent.name
                ) == p.resolve()
                and _matches_source_dataset(p.parent, source_dataset_path, source_task)
            ]
            active = training_job_manager.get_active_job()
            if active and active.status in ("queued", "running", "stopping", "disconnected"):
                active_output = Path(active.output_dir).expanduser().resolve()
                candidates = [p for p in candidates if p.parent.resolve() != active_output]
            if not candidates:
                raise HTTPException(status_code=404, detail="No completed training job has been selected")
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            out_dir = newest.parent.resolve()
            receipt = completed_job_receipt(out_dir) or {}
            task_hint = receipt.get("task")
            dataset_hint = Path(receipt["dataset_path"]) if receipt.get("dataset_path") else None
            resolved_job_id = out_dir.name if out_dir.parent.name == "models" else out_dir.parent.name

    model_pt = out_dir / "best_model.pt"
    if not model_pt.is_file():
        raise HTTPException(status_code=404, detail=f"Model checkpoint best_model.pt not found in {out_dir}")

    meta_p = out_dir / "model_meta.json"
    meta: Dict[str, Any] = {}
    if meta_p.is_file():
        try:
            with open(meta_p, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass

    task = task_hint or meta.get("task", "classification")

    if dataset_path_override and isinstance(dataset_path_override, (str, Path)):
        resolved_dataset = Path(dataset_path_override).resolve()
    elif dataset_hint:
        resolved_dataset = dataset_hint.resolve()
    elif (out_dir / "dataset").is_dir():
        resolved_dataset = (out_dir / "dataset").resolve()
    elif "dataset_path" in meta and Path(meta["dataset_path"]).exists():
        resolved_dataset = Path(meta["dataset_path"]).resolve()
    else:
        cand_data = Path("./datasets/synthetic").resolve()
        if cand_data.exists():
            resolved_dataset = cand_data
        else:
            raise HTTPException(status_code=404, detail="Evaluation dataset path could not be located on disk")

    if not resolved_dataset.exists():
        raise HTTPException(status_code=404, detail=f"Dataset path does not exist on disk: {resolved_dataset}")

    return out_dir, model_pt, meta, task, resolved_job_id, resolved_dataset


def _resolve_dataset_dir(dataset_path: Path, task: str) -> Path:
    """Finds the effective dataset folder, accounting for nested task subdirectories."""
    task_clean = task.lower().strip()
    if (dataset_path / task_clean).is_dir():
        cand = dataset_path / task_clean
        if (cand / "train").is_dir() or (cand / "val").is_dir() or (cand / "images").is_dir() or (cand / "test").is_dir():
            return cand
    if not (dataset_path / "train").is_dir() and not (dataset_path / "images").is_dir() and not (dataset_path / "test").is_dir():
        for sub in dataset_path.iterdir():
            if sub.is_dir() and ((sub / "train").is_dir() or (sub / "images").is_dir() or (sub / "test").is_dir()):
                return sub
    return dataset_path


def _paired_evaluation_paths(dataset_dir: Path, task: str) -> Tuple[Path, Path, str]:
    """Use an independent test split when present and refuse incomplete pairs."""
    if task not in ("detection", "segmentation"):
        raise ValueError(f"Unsupported paired evaluation task: {task}")
    image_root = dataset_dir / "images"
    for split in ("test", "val"):
        images = image_root / split
        if not images.is_dir():
            continue
        labels = (dataset_dir / f"annotations_{split}.json") if task == "detection" else (dataset_dir / "masks" / split)
        if not (labels.is_file() if task == "detection" else labels.is_dir()):
            raise HTTPException(status_code=422, detail=f"{task} {split} images exist without matching labels: {labels}")
        if not any(path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS for path in images.rglob('*')):
            continue
        return images, labels, split
    raise HTTPException(status_code=422, detail="Evaluation requires a saved test or validation partition; unpartitioned images may include training data.")


def _split_evidence(split: str) -> Dict[str, Any]:
    return {"evaluated_split": split, "selection_overlap": split == "val"}


def _evaluate_classification(
    model_pt: Path,
    meta: Dict[str, Any],
    dataset_dir: Path,
    device: torch.device,
    cancel=None,
) -> Dict[str, Any]:
    selected_split = "test"
    val_ds = ClassificationDataset(root_dir=dataset_dir, split=selected_split)
    if len(val_ds.samples) == 0:
        selected_split = "val"
        val_ds = ClassificationDataset(root_dir=dataset_dir, split=selected_split)
    if len(val_ds.samples) == 0:
        raise HTTPException(status_code=422, detail="No held-out classification images. Save a nonempty test or validation partition before evaluation.")
    if getattr(val_ds, "split_basis", None) == "automatic":
        raise HTTPException(status_code=422, detail="Save the classification split before evaluation; an automatic split can change after training.")

    ckpt = torch.load(model_pt, map_location=device, weights_only=False)
    state_dict = ckpt["model_state_dict"] if "model_state_dict" in ckpt else ckpt

    classes = meta.get("classes") or ckpt.get("classes") or val_ds.classes
    class_to_idx = {name: idx for idx, name in enumerate(classes)}
    num_classes = len(classes)

    # Determine model output size from state dict if available to match checkpoint head
    model_num_classes = None
    for k in ["fc.weight", "classifier.2.weight", "classifier.1.weight", "head.weight"]:
        if k in state_dict:
            model_num_classes = state_dict[k].shape[0]
            break
    if model_num_classes is None:
        model_num_classes = max(2, num_classes)

    backbone = meta.get("backbone", ckpt.get("backbone", "resnet18"))
    model = create_classification_model(backbone=backbone, num_classes=model_num_classes, pretrained=False).to(device)
    model.load_state_dict(state_dict)
    model.eval()

    img_size = tuple(meta.get("image_size", [256, 256]))

    preds: List[int] = []
    targets: List[int] = []
    test_predictions: List[Dict[str, Any]] = []
    cell_samples: Dict[str, List[str]] = {
        f"{classes[i]}:{classes[j]}": [] for i in range(num_classes) for j in range(num_classes)
    }

    with torch.no_grad():
        for img_path, _ in val_ds.samples:
            if cancel is not None and cancel.is_set(): raise InterruptedError("Common evaluation cancelled")
            cname = img_path.parent.name
            target_idx = class_to_idx.get(cname, 0)

            rgb = _read_image_rgb(img_path)
            resized = cv2.resize(rgb, img_size, interpolation=cv2.INTER_LINEAR)
            img_t = torch.from_numpy(resized.transpose(2, 0, 1)).float().unsqueeze(0) / 255.0
            img_t = img_t.to(device)

            logits = model(img_t)
            probs = torch.softmax(logits, dim=-1)[0]
            conf_val, pred_idx_t = probs.max(dim=-1)
            pred_idx = int(pred_idx_t.item())
            conf = float(conf_val.item())

            preds.append(pred_idx)
            targets.append(target_idx)

            true_name = classes[target_idx] if target_idx < len(classes) else f"class_{target_idx}"
            pred_name = classes[pred_idx] if pred_idx < len(classes) else f"class_{pred_idx}"

            key = f"{true_name}:{pred_name}"
            if key not in cell_samples:
                cell_samples[key] = []
            cell_samples[key].append(str(img_path.resolve()))

            test_predictions.append({
                "image_id": img_path.stem,
                "file_name": img_path.name,
                "file_path": str(img_path.resolve()),
                "ground_truth": true_name,
                "predicted_class": pred_name,
                "confidence": round(conf, 4),
                "class_scores": {name:float(probs[i].item()) for i,name in enumerate(classes)},
                "is_correct": bool(target_idx == pred_idx),
                "thumbnail_url": f"/api/dataset/thumbnail/{img_path.name}?file_path={img_path.resolve()}",
            })

    metrics = compute_classification_metrics(preds, targets, num_classes=num_classes, class_names=classes)
    cm_data = metrics.get("confusion_matrix", {})
    cm_data["classes"] = classes
    cm_data["class_names"] = classes
    cm_data["cell_samples"] = cell_samples

    return {
        "metrics": {
            **_split_evidence(selected_split),
            "accuracy": metrics["accuracy"],
            "macro_precision": metrics["macro_precision"],
            "macro_recall": metrics["macro_recall"],
            "macro_f1": metrics["macro_f1"],
            "best_metric": meta.get("best_metric"),
            "per_class": metrics.get("per_class", {}),
        },
        "confusion_matrix": cm_data,
        "test_predictions": test_predictions,
    }


def _patch_manifest_for_checkpoint(dataset_dir: Path, meta: Dict[str, Any]):
    """Refuse evaluation when labeled pixels changed after this model was trained."""
    expected = meta.get("patch_provenance")
    if not isinstance(expected, dict) or not expected.get("dataset_sha256"):
        raise HTTPException(status_code=409, detail="Patch model has no dataset provenance")
    try:
        manifest = load_patch_manifest(dataset_dir)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"Patch source or labels changed: {exc}") from exc
    if manifest.provenance["dataset_sha256"] != expected["dataset_sha256"]:
        raise HTTPException(status_code=409, detail="Patch source or labels changed after training")
    return manifest


def _evaluate_patch_classification(
    model_pt: Path,
    meta: Dict[str, Any],
    dataset_dir: Path,
    device: torch.device,
    allow_source_revision: bool = False,
) -> Dict[str, Any]:
    """Evaluate only held-out annotated patches, retaining original pixel boxes."""
    manifest = load_patch_manifest(dataset_dir) if allow_source_revision else _patch_manifest_for_checkpoint(dataset_dir, meta)
    selected_split = "test" if manifest.provenance["split_counts"]["test"] else "val"
    samples = [item for item in manifest.patches if item.split == selected_split]
    classes = manifest.classes
    if meta.get("classes") != classes or meta.get("normal_class") != manifest.normal_class:
        raise HTTPException(status_code=409, detail="Patch class mapping changed after training")
    checkpoint = torch.load(model_pt, map_location=device, weights_only=True)
    state_dict = checkpoint.get("model_state_dict", checkpoint)
    model = create_classification_model(
        backbone=meta.get("backbone", "resnet18"), num_classes=len(classes), pretrained=False,
    ).to(device)
    model.load_state_dict(state_dict)
    model.eval()
    image_size = tuple(meta.get("image_size", [256, 256]))
    normal_index = classes.index(manifest.normal_class)
    model_sha = hashlib.sha256(model_pt.read_bytes()).hexdigest()
    preds: List[int] = []
    targets: List[int] = []
    test_predictions: List[Dict[str, Any]] = []
    cell_samples: Dict[str, List[str]] = {
        f"{actual}:{predicted}": [] for actual in classes for predicted in classes
    }
    with torch.inference_mode():
        for record in samples:
            with Image.open(record.image_path) as opened:
                crop = np.asarray(opened.convert("RGB").crop(record.box), dtype=np.uint8)
            crop = cv2.resize(crop, image_size, interpolation=cv2.INTER_LINEAR)
            tensor = torch.from_numpy(np.ascontiguousarray(crop.transpose(2, 0, 1))).float().unsqueeze(0).to(device) / 255.0
            scores = torch.softmax(model(tensor), dim=1)[0].cpu().tolist()
            pred_index = int(np.argmax(scores))
            confidence = float(scores[pred_index])
            defect_score = float(1.0 - scores[normal_index])
            preds.append(pred_index)
            targets.append(record.label_index)
            source_path = str(record.image_path)
            cell_samples[f"{record.label}:{classes[pred_index]}"].append(source_path)
            x1, y1, x2, y2 = record.box
            test_predictions.append({
                "image_id": f"{record.image_path.stem}_{x1}_{y1}_{x2}_{y2}",
                "file_name": record.image_path.name,
                "file_path": source_path,
                "box": [x1, y1, x2, y2],
                "source_sha256": record.source_sha256,
                "dataset_sha256": manifest.provenance["dataset_sha256"],
                "model_sha256": model_sha,
                "ground_truth": record.label,
                "predicted_class": classes[pred_index],
                "confidence": round(confidence, 6),
                "class_scores": {name: round(float(scores[index]), 6) for index, name in enumerate(classes)},
                "defect_score": round(defect_score, 6),
                "is_correct": record.label_index == pred_index,
                "thumbnail_url": f"/api/dataset/thumbnail/{record.image_path.name}?file_path={record.image_path}",
            })
    metrics = compute_classification_metrics(preds, targets, num_classes=len(classes), class_names=classes)
    confusion = metrics["confusion_matrix"]
    confusion["classes"] = classes
    confusion["class_names"] = classes
    confusion["cell_samples"] = cell_samples
    return {
        "metrics": {
            "accuracy": metrics["accuracy"],
            "macro_precision": metrics["macro_precision"],
            "macro_recall": metrics["macro_recall"],
            "macro_f1": metrics["macro_f1"],
            "best_metric": meta.get("best_metric"),
            "per_class": metrics.get("per_class", {}),
            **_split_evidence(selected_split),
        },
        "confusion_matrix": confusion,
        "test_predictions": test_predictions,
        "dataset_provenance": manifest.provenance,
    }


def _manifest_evaluation_dataset(task, source, image_size, class_names=None):
    from backend.engine.grouped_dataset_views import load_manifest_dataset
    try:
        selected_split = "test"
        dataset = load_manifest_dataset(task, source, selected_split, image_size=image_size, class_names=class_names)
        if dataset is not None and len(dataset) == 0:
            selected_split = "val"
            dataset = load_manifest_dataset(task, source, selected_split, image_size=image_size, class_names=class_names)
        if dataset is not None:
            dataset.evaluation_split = selected_split
        return dataset
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(422, f"Saved evaluation split is invalid: {exc}") from exc


def _evaluate_detection(
    model_pt: Path,
    meta: Dict[str, Any],
    dataset_dir: Path,
    device: torch.device,
    cancel=None,
) -> Dict[str, Any]:
    img_size = tuple(meta.get("image_size", [256, 256]))
    ckpt = torch.load(model_pt, map_location=device, weights_only=False)
    state_dict = ckpt["model_state_dict"] if "model_state_dict" in ckpt else ckpt
    metadata_classes = meta.get("classes")
    checkpoint_classes = ckpt.get("classes")
    metadata_names = foreground_class_names(metadata_classes) if isinstance(metadata_classes, (list, tuple)) and metadata_classes else None
    checkpoint_names = foreground_class_names(checkpoint_classes) if isinstance(checkpoint_classes, (list, tuple)) and checkpoint_classes else None
    if checkpoint_names and metadata_names and checkpoint_names != metadata_names:
        raise HTTPException(status_code=422, detail="Detection checkpoint and model metadata have different class order")
    classes = checkpoint_names or metadata_names
    if classes is None:
        train_anno = dataset_dir / "annotations_train.json"
        if train_anno.is_file():
            train_categories = json.loads(train_anno.read_text(encoding="utf-8")).get("categories", [])
            classes = [str(category["name"]) for category in sorted(train_categories, key=lambda category: int(category["id"]))]
    try:
        val_ds = _manifest_evaluation_dataset("detection", dataset_dir, img_size, classes)
        if val_ds is None:
            val_img, val_anno, selected_split = _paired_evaluation_paths(dataset_dir, "detection")
            val_ds = DetectionDataset(images_dir=val_img, annotation_file=val_anno, image_size=img_size, class_names=classes)
        else:
            selected_split = val_ds.evaluation_split
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Detection evaluation class mapping is incompatible: {exc}") from exc
    if len(val_ds) == 0:
        raise HTTPException(status_code=400, detail="No evaluation images found for detection")

    classes = list(val_ds.categories.values())
    num_classes = checkpoint_detection_num_classes(state_dict, classes)
    if num_classes != len(classes) + 1:
        raise HTTPException(status_code=422, detail="Detection checkpoint class count does not match saved class mapping")
    det_preset = meta.get("detector_preset", meta.get("preset", "fast"))
    model = create_detection_model(preset=det_preset, num_classes=num_classes, pretrained=False,
                                   backbone=meta.get("backbone", ckpt.get("backbone"))).to(device)
    model.load_state_dict(state_dict)
    model.eval()

    all_preds: List[Dict[str, torch.Tensor]] = []
    all_targets: List[Dict[str, torch.Tensor]] = []
    test_predictions: List[Dict[str, Any]] = []

    cm_classes = classes if "background" in classes else ["background"] + classes
    cm_class_to_idx = {c: idx for idx, c in enumerate(cm_classes)}
    num_cm = len(cm_classes)
    matrix = [[0] * num_cm for _ in range(num_cm)]
    cell_samples: Dict[str, List[str]] = {
        f"{cm_classes[i]}:{cm_classes[j]}": [] for i in range(num_cm) for j in range(num_cm)
    }

    with torch.no_grad():
        for idx in range(len(val_ds)):
            if cancel is not None and cancel.is_set(): raise InterruptedError("Common evaluation cancelled")
            img_id = val_ds.image_ids[idx]
            img_meta = val_ds.images[img_id]
            img_path = val_ds.images_dir / img_meta["file_name"]

            img_t, target = val_ds[idx]
            img_dev = img_t.to(device).unsqueeze(0)
            outputs = model(img_dev)

            out_boxes = outputs[0]["boxes"]
            out_scores = outputs[0]["scores"]
            out_labels = outputs[0]["labels"]

            all_preds.append({"boxes": out_boxes, "scores": out_scores, "labels": out_labels})
            all_targets.append({"boxes": target["boxes"], "labels": target["labels"]})

            high_conf = float(out_scores.max().item()) if len(out_scores) > 0 else 0.0
            top_label_idx = int(out_labels[out_scores.argmax()].item()) if len(out_scores) > 0 else 0
            pred_name = cm_classes[top_label_idx] if top_label_idx < num_cm else f"class_{top_label_idx}"

            gt_label_idx = int(target["labels"][0].item()) if len(target["labels"]) > 0 else 0
            gt_name = cm_classes[gt_label_idx] if gt_label_idx < num_cm else f"class_{gt_label_idx}"

            matrix[cm_class_to_idx[gt_name]][cm_class_to_idx[pred_name]] += 1
            cm_key = f"{gt_name}:{pred_name}"
            if cm_key not in cell_samples:
                cell_samples[cm_key] = []
            cell_samples[cm_key].append(str(img_path.resolve()))

            test_predictions.append({
                "image_id": str(img_id),
                "file_name": img_meta["file_name"],
                "file_path": str(img_path.resolve()),
                "ground_truth": gt_name,
                "predicted_class": pred_name,
                "confidence": round(high_conf, 4),
                "defect_score": round(high_conf,4),
                "class_scores": {name: max((float(score) for score,label in zip(out_scores,out_labels) if int(label)==index),default=0.0) for index,name in enumerate(cm_classes) if index>0},
                "is_correct": bool(gt_name == pred_name),
                "thumbnail_url": f"/api/dataset/thumbnail/{img_meta['file_name']}?file_path={img_path.resolve()}",
            })
            from backend.engine.evaluation_evidence import match_objects
            scale_x=float(img_meta.get('width',img_size[0]))/img_size[0]
            scale_y=float(img_meta.get('height',img_size[1]))/img_size[1]
            def original_box(box):return [float(v)*scale for v,scale in zip(box,(scale_x,scale_y,scale_x,scale_y))]
            predictions=[{'label':cm_classes[int(label)],'box':original_box(box.cpu().tolist()),'confidence':float(score)} for box,score,label in zip(out_boxes,out_scores,out_labels) if 0<=int(label)<num_cm]
            truth=[{'label':cm_classes[int(label)],'box':original_box(box.cpu().tolist())} for box,label in zip(target['boxes'],target['labels']) if 0<=int(label)<num_cm]
            test_predictions[-1]['object_evidence']={**match_objects(predictions,truth),'coordinate_space':'original_image','source_size':[img_meta.get('width'),img_meta.get('height')]}
            test_predictions[-1]['ground_truth_classes']=sorted({row['label'] for row in truth})

    norm_matrix = [[0.0] * num_cm for _ in range(num_cm)]
    for i in range(num_cm):
        r_sum = sum(matrix[i]) or 1
        for j in range(num_cm):
            norm_matrix[i][j] = round(matrix[i][j] / r_sum, 4)

    cat_map = {idx: name for idx, name in enumerate(cm_classes)}
    map_res = evaluate_detections_map(all_preds, all_targets, num_classes=num_cm, class_names=cat_map)

    return {
        "metrics": {
            **_split_evidence(selected_split),
            "mAP_50": map_res.get("mAP_50", 0.0),
            "mAP_50_95": map_res.get("mAP_50_95", 0.0),
            "best_metric": meta.get("best_metric"),
            "class_ap50": map_res.get("class_ap50", {}),
        },
        "confusion_matrix": {
            "classes": cm_classes,
            "class_names": cm_classes,
            "matrix": matrix,
            "normalized_matrix": norm_matrix,
            "cell_samples": cell_samples,
        },
        "test_predictions": test_predictions,
    }


def _evaluate_segmentation(
    model_pt: Path,
    meta: Dict[str, Any],
    dataset_dir: Path,
    device: torch.device,
    cancel=None,
) -> Dict[str, Any]:
    img_size = tuple(meta.get("image_size", [256, 256]))
    classes = list(meta.get("classes") or ["background", "defect"])
    if classes[0] != "background": classes = ["background", *classes]
    val_ds = _manifest_evaluation_dataset("segmentation", dataset_dir, img_size, classes)
    if val_ds is None:
        val_img, val_mask, selected_split = _paired_evaluation_paths(dataset_dir, "segmentation")
        val_ds = SegmentationDataset(images_dir=val_img, masks_dir=val_mask, image_size=img_size)
    else:
        selected_split = val_ds.evaluation_split
    if len(val_ds) == 0:
        raise HTTPException(status_code=400, detail="No evaluation images found for segmentation")

    num_classes = len(classes)

    ckpt = torch.load(model_pt, map_location=device, weights_only=False)
    state_dict = ckpt["model_state_dict"] if "model_state_dict" in ckpt else ckpt
    seg_preset = meta.get("preset", "fast")
    model = build_segmentation_model(model_name=meta.get("model_name", ckpt.get("model_name", "unet")),
                                     num_classes=num_classes, preset=seg_preset, pretrained=False).to(device)
    model.load_state_dict(state_dict)
    model.eval()

    all_preds: List[np.ndarray] = []
    all_targets: List[np.ndarray] = []
    test_predictions: List[Dict[str, Any]] = []

    matrix = [[0] * num_classes for _ in range(num_classes)]
    cell_samples: Dict[str, List[str]] = {
        f"{classes[i]}:{classes[j]}": [] for i in range(num_classes) for j in range(num_classes)
    }

    with torch.no_grad():
        for idx in range(len(val_ds)):
            if cancel is not None and cancel.is_set(): raise InterruptedError("Common evaluation cancelled")
            img_p, _ = val_ds.samples[idx]
            from backend.engine.evaluation_sources import _sha256
            image_sha256 = _sha256(img_p)
            source_size = None
            try:
                with Image.open(img_p) as original:
                    if original.getexif().get(274, 1) == 1:
                        source_size = list(original.size)
            except (OSError, ValueError):
                # Non-PIL inputs retain standalone masks without claiming a raw-image mapping.
                pass
            img_t, target_mask = val_ds[idx]

            img_dev = img_t.to(device).unsqueeze(0)
            logits = model(img_dev)
            pred_mask = torch.argmax(logits, dim=1)[0].cpu().numpy().astype(np.uint8)
            gt_mask = target_mask.cpu().numpy().astype(np.uint8)
            if _sha256(img_p) != image_sha256:
                raise HTTPException(409, 'Evaluation source image changed while producing pixel evidence')

            all_preds.append(pred_mask)
            all_targets.append(gt_mask)

            gt_has_defect = bool((gt_mask > 0).any())
            pred_has_defect = bool((pred_mask > 0).any())

            gt_idx = int(np.bincount(gt_mask[gt_mask > 0].ravel(), minlength=num_classes).argmax()) if gt_has_defect else 0
            pred_idx = int(np.bincount(pred_mask[pred_mask > 0].ravel(), minlength=num_classes).argmax()) if pred_has_defect else 0
            gt_label = classes[gt_idx]
            pred_label = classes[pred_idx]
            matrix[gt_idx][pred_idx] += 1

            cell_key = f"{gt_label}:{pred_label}"
            cell_samples[cell_key].append(str(img_p.resolve()))

            probs = torch.softmax(logits, dim=1)[0]
            conf = float(probs[pred_idx].max().item()) if pred_has_defect else float(probs[0].mean().item())
            defect_score = float(probs[1:].max().item())

            test_predictions.append({
                "image_id": img_p.stem,
                "file_name": img_p.name,
                "file_path": str(img_p.resolve()),
                "image_sha256": image_sha256,
                "ground_truth": gt_label,
                "predicted_class": pred_label,
                "confidence": round(conf, 4),
                "defect_score": defect_score,
                "is_correct": bool(gt_label == pred_label),
                "thumbnail_url": f"/api/dataset/thumbnail/{img_p.name}?file_path={img_p.resolve()}",
            })
            from backend.engine.evaluation_evidence import pixel_errors
            test_predictions[-1]['pixel_evidence']={'coordinate_space':'model_input','shape':list(gt_mask.shape),'per_class':pixel_errors(pred_mask,gt_mask,classes)}
            import base64
            def index_mask_png(mask):
                from io import BytesIO
                buffer=BytesIO();Image.fromarray(mask.astype(np.uint8)).save(buffer,format='PNG')
                return 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii')
            test_predictions[-1]['pixel_evidence'].update(prediction_mask=index_mask_png(pred_mask),truth_mask=index_mask_png(gt_mask))
            if source_size and getattr(val_ds, 'transform', None) is None:
                test_predictions[-1]['pixel_evidence']['mapping'] = {'kind': 'full_image_resize', 'source_size': source_size}
            test_predictions[-1]['class_scores']={name:float(probs[i].max().item()) for i,name in enumerate(classes)}
            test_predictions[-1]['ground_truth_classes']=[name for i,name in enumerate(classes) if np.any(gt_mask==i)]

    norm_matrix = [[0.0] * num_classes for _ in range(num_classes)]
    for i in range(num_classes):
        r_sum = sum(matrix[i]) or 1
        for j in range(num_classes):
            norm_matrix[i][j] = round(matrix[i][j] / r_sum, 4)

    seg_metrics = compute_segmentation_metrics(
        np.array(all_preds), np.array(all_targets), num_classes=num_classes, class_names=dict(enumerate(classes))
    )

    return {
        "metrics": {
            **_split_evidence(selected_split),
            "miou": seg_metrics.get("miou", 0.0),
            "mdice": seg_metrics.get("mdice", 0.0),
            "pixel_accuracy": seg_metrics.get("pixel_accuracy", 0.0),
            "foreground_iou": seg_metrics.get("foreground_iou", 0.0),
            "per_class_iou": seg_metrics.get("per_class_iou",{}),
            "per_class_dice": seg_metrics.get("per_class_dice",{}),
            "best_metric": meta.get("best_metric"),
        },
        "confusion_matrix": {
            "classes": classes,
            "class_names": classes,
            "matrix": matrix,
            "normalized_matrix": norm_matrix,
            "cell_samples": cell_samples,
        },
        "test_predictions": test_predictions,
    }


def _evaluate_anomaly(
    model_pt: Path,
    meta: Dict[str, Any],
    dataset_dir: Path,
    device: torch.device,
    cancel=None,
) -> Dict[str, Any]:
    val_ds = _manifest_evaluation_dataset("anomaly", dataset_dir, tuple(meta.get("image_size", [256, 256])))
    if val_ds is None:
        selected_split = "test"
        val_ds = AnomalyDataset(root_dir=dataset_dir, split="test")
        if len(val_ds) == 0:
            selected_split = "val"
            val_ds = AnomalyDataset(root_dir=dataset_dir, split="val")
    else:
        selected_split = val_ds.evaluation_split
    if len(val_ds) == 0:
        raise HTTPException(status_code=400, detail="No evaluation images found for anomaly detection")

    classes = ["good", "anomaly"]
    num_classes = len(classes)

    ckpt = torch.load(model_pt, map_location=device, weights_only=False)
    state_dict = ckpt["model_state_dict"] if "model_state_dict" in ckpt else ckpt
    model = reconstruct_anomaly_detector(state_dict, {**ckpt, **meta}, device)
    map_semantics = getattr(model, 'model_metadata', {}).get('map_semantics', 'pixel_score')
    patch_scores = map_semantics == 'patch_score'

    img_size = tuple(meta.get("image_size", [256, 256]))
    image_scores: List[float] = []
    image_labels: List[int] = []
    maps = []
    pixel_heatmaps = []
    pixel_masks = []
    mask_sources = []
    mode = meta.get("anomaly_mode", "classification")
    if mode not in ("classification", "segmentation"): raise HTTPException(422, "Invalid anomaly mode")
    if patch_scores and mode == 'segmentation':
        raise HTTPException(422, 'Patch score maps do not provide trained segmentation masks')

    with torch.no_grad():
        for idx in range(len(val_ds)):
            if cancel is not None and cancel.is_set(): raise InterruptedError("Common evaluation cancelled")
            img_p, label, mask_path = val_ds.samples[idx]
            rgb = _read_image_rgb(img_p)
            resized = rgb if patch_scores else cv2.resize(rgb, img_size, interpolation=cv2.INTER_LINEAR)
            img_t = torch.from_numpy(resized.transpose(2, 0, 1)).float().unsqueeze(0) / 255.0
            if not patch_scores:
                img_t = img_t.to(device)

            heatmap, score = model(img_t)
            heatmap_np = heatmap.detach().cpu().numpy() if isinstance(heatmap, torch.Tensor) else np.asarray(heatmap)
            heatmap_np = np.squeeze(heatmap_np).astype(np.float32)
            if heatmap_np.ndim != 2 or not np.isfinite(heatmap_np).all(): raise HTTPException(422, "Anomaly model returned an invalid pixel map")
            maps.append(heatmap_np)
            mask = None
            if patch_scores:
                mask_sources.append(None)
            elif mask_path and Path(mask_path).is_file():
                with Image.open(mask_path) as original: mask = np.asarray(original.convert("L"))
                mask = (cv2.resize(mask, (heatmap_np.shape[1], heatmap_np.shape[0]), interpolation=cv2.INTER_NEAREST) > 0).astype(np.uint8)
                mask_sources.append({"path": str(Path(mask_path).resolve()), "sha256": hashlib.sha256(Path(mask_path).read_bytes()).hexdigest()})
            elif label == 0:
                mask = np.zeros_like(heatmap_np, dtype=np.uint8)
                mask_sources.append({"path": None, "basis": "reviewed normal image label"})
            else:
                mask_sources.append(None)
                if mode == "segmentation": raise HTTPException(422, "Anomaly segmentation evaluation requires masks for every defect image")
            if mask is not None:
                pixel_heatmaps.append(heatmap_np); pixel_masks.append(mask)
            score_f = float(score[0].item() if isinstance(score, (torch.Tensor, list)) else score)
            image_scores.append(score_f)
            image_labels.append(label)

    single_class = len(set(image_labels)) < 2
    fixed_threshold = float(getattr(model, 'threshold', meta.get('anomaly_threshold', .5)))
    anom_metrics = compute_anomaly_metrics(image_scores, image_labels, pixel_heatmaps=pixel_heatmaps, pixel_masks=pixel_masks,
        fixed_threshold=fixed_threshold,
        threshold_comparison='gt' if patch_scores else 'ge')
    import uuid
    evidence_dir = model_pt.parent / "evaluation_maps"
    evidence_dir.mkdir(exist_ok=True)
    evidence_file = evidence_dir / ("anomaly_" + uuid.uuid4().hex + ".npz")
    evidence_arrays = {f"heatmap_{index}": value for index, value in enumerate(maps)}
    mask_index = 0
    for index, source in enumerate(mask_sources):
        if source is not None:
            evidence_arrays[f"mask_{index}"] = pixel_masks[mask_index]; mask_index += 1
    np.savez_compressed(evidence_file, **evidence_arrays)
    evidence_hash = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
    optimal_th = fixed_threshold
    from backend.engine.score_contract import state_score_spec
    score_spec = state_score_spec(state_dict) if hasattr(model, 'threshold') else None

    matrix = [[0] * num_classes for _ in range(num_classes)]
    cell_samples: Dict[str, List[str]] = {
        f"{classes[i]}:{classes[j]}": [] for i in range(num_classes) for j in range(num_classes)
    }
    test_predictions: List[Dict[str, Any]] = []

    for idx in range(len(val_ds)):
        img_p, label, _ = val_ds.samples[idx]
        score_f = image_scores[idx]
        pred_idx = int(score_f > optimal_th if patch_scores else score_f >= optimal_th)
        matrix[label][pred_idx] += 1

        gt_name = classes[label]
        pred_name = classes[pred_idx]
        cell_key = f"{gt_name}:{pred_name}"
        cell_samples[cell_key].append(str(img_p.resolve()))

        test_predictions.append({
            "image_id": img_p.stem,
            "file_name": img_p.name,
            "file_path": str(img_p.resolve()),
            "ground_truth": gt_name,
            "predicted_class": pred_name,
            "confidence": round(score_f, 4),
            "is_correct": bool(label == pred_idx),
            "anomaly_mode": mode,
            "defect_score": score_f,
            "score_spec": score_spec,
            "map_semantics": map_semantics,
            "pixel_evidence": {"file_path": str(evidence_file), "sha256": evidence_hash,
                               "heatmap_key": f"heatmap_{idx}", "mask_key": f"mask_{idx}" if mask_sources[idx] is not None else None,
                               "source_mask": mask_sources[idx]},
            "thumbnail_url": f"/api/dataset/thumbnail/{img_p.name}?file_path={img_p.resolve()}",
        })

    norm_matrix = [[0.0] * num_classes for _ in range(num_classes)]
    for i in range(num_classes):
        r_sum = sum(matrix[i]) or 1
        for j in range(num_classes):
            norm_matrix[i][j] = round(matrix[i][j] / r_sum, 4)

    return {
        "metrics": {
            **_split_evidence(selected_split),
            "image_auroc": anom_metrics.get("image_auroc"),
            "pixel_auroc": anom_metrics.get("pixel_auroc"),
            "pixel_evaluated_images": len(pixel_heatmaps),
            "pixel_missing_masks": len(maps) - len(pixel_heatmaps),
            "anomaly_mode": mode,
            "map_semantics": map_semantics,
            "threshold_basis": anom_metrics['threshold_basis'] if single_class else 'heldout_normal_calibration' if patch_scores and getattr(model, 'calibration', {}).get('normal_image_count', 0) else 'model_default' if patch_scores else anom_metrics['threshold_basis'],
            "threshold_search_available": anom_metrics['threshold_search_available'],
            "active_threshold": optimal_th,
            "score_spec": score_spec,
            "score_basis": "saved_model_calibration",
            "f1_score": anom_metrics.get("f1_score"),
            "optimal_threshold": None if single_class else round(optimal_th, 4),
            "best_metric": meta.get("best_metric"),
        },
        "confusion_matrix": {
            "classes": classes,
            "class_names": classes,
            "matrix": matrix,
            "normalized_matrix": norm_matrix,
            "cell_samples": cell_samples,
        },
        "test_predictions": test_predictions,
    }


_DERIVED_SCORE_TASKS = ("classification", "anomaly", "anomaly_detection")


def _evaluation_class_semantics(meta: Any, task: Optional[str] = None) -> Dict[str, Any]:
    """The evaluated model's frozen class roles, or the derived record for older models."""
    from backend.engine.class_semantics import class_semantics_record, recorded_roles
    classes = meta.get("classes") if isinstance(meta, dict) else None
    names = [str(name) for name in classes] if isinstance(classes, list) else []
    try:
        if recorded_roles(meta, task=task, classes=names or None) is not None:
            return dict(meta["class_semantics"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Model class semantics record is invalid: {exc}") from exc
    return class_semantics_record(names, task=task)


def _annotate_predictions(predictions: List[Dict[str, Any]], task: str, roles: Optional[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Set truth flags and defect scores with the evaluated model's class roles.

    Scores that only follow from predicted class and confidence are recomputed so a
    worker or older annotation cannot keep a different class meaning; explicit
    model scores (detection, segmentation, patches) are kept.
    """
    from backend.engine.zero_escape_analyzer import is_defect_label, compute_sample_defect_score
    derived = str(task).lower().strip() in _DERIVED_SCORE_TASKS
    for prediction in predictions:
        prediction["is_defect"] = is_defect_label(prediction.get("ground_truth"), roles)
        if task in ('anomaly', 'anomaly_detection') and prediction.get('score_spec'):
            from backend.engine.score_contract import validate_score_spec
            validate_score_spec(prediction['score_spec'])
            prediction['defect_score'] = float(prediction.get('defect_score', prediction['confidence']))
            continue
        if derived:
            prediction.pop("defect_score", None)
        prediction["defect_score"] = compute_sample_defect_score(prediction, task=task, roles=roles)
    return predictions


def _common_project(request):
    from backend.api.routes_project import get_current_project
    project=get_current_project(request)
    account=getattr(request.state,'account_user',None)
    if account and request.app.state.accounts.project_role(account['id'],project['id']) not in {'owner','reviewer','trainer'}:
        raise HTTPException(403,'This project role cannot control remote evaluation')
    return project


def _common_model(project,job_id):
    if not project or not isinstance(job_id,str) or not is_job_id(job_id):
        raise HTTPException(422,'Common evaluation requires an explicit completed project job')
    models=Path(project['models_dir']).resolve()
    output=models/job_id
    if output.is_symlink() or not output.is_dir() or output.parent!=models:
        raise HTTPException(404,'Completed model is not in the active project')
    from backend.remote.operations import remote_job_context
    from backend.remote.coordinator import ArtifactValidationError
    try:context=remote_job_context(output,job_id)
    except (ArtifactValidationError,ValueError,KeyError,OSError) as exc:
        raise HTTPException(502,f'Completed remote model could not be verified: {exc}') from exc
    if context is None:raise HTTPException(422,'Common cohort requires a completed verified remote model')
    return context,json.loads((output/'model_meta.json').read_text(encoding='utf-8'))


def _run_common_evaluation(project,job_id,dataset_path,version_id,profile_id,device,force_recompute,source_task):
    from backend.remote.evaluation_cohort import freeze_cohort,execute_common
    from backend.remote.profiles import get_profile_store
    from backend.remote.coordinator import ArtifactValidationError,RemoteDisconnected
    from backend.remote.operations import RemoteComputeBusy
    from backend.engine.evaluation_history import EvaluationHistory,evaluation_model_context
    from backend.api.routes_dataset_versions import _read_manifest,_verify
    context,meta=_common_model(project,job_id)
    if not isinstance(version_id,str) or not isinstance(profile_id,str) or device not in ('cpu','cuda:0'):
        raise HTTPException(422,'Common evaluation requires a saved version, original profile, and explicit CPU or logical CUDA 0')
    if profile_id!=context.profile.id or get_profile_store().get(profile_id)!=context.profile:
        raise HTTPException(409,'Common evaluation currently supports the unchanged original model compute profile')
    if source_task is not None and source_task!=context.task:raise HTTPException(409,'Common evaluation source task changed')
    try:
        cohort=freeze_cohort(project,version_id,Path(dataset_path or project['source_dataset_dir']),context.task,meta,context)
        result=execute_common(context,cohort,device,force_recompute=bool(force_recompute))
        directory,manifest=_read_manifest(project,version_id)
        verification=_verify(project,directory,manifest)
        if verification['status']!='verified' or verification['editable_changed_files']:
            raise HTTPException(409,'Common source labels or split changed during execution')
    except RemoteComputeBusy as exc:raise HTTPException(409,str(exc)) from exc
    except RemoteDisconnected as exc:raise HTTPException(503,f'Remote evaluation connection lost; retry the same binding: {exc}') from exc
    except ArtifactValidationError as exc:raise HTTPException(502,str(exc)) from exc
    except (ValueError,KeyError,TypeError,OSError) as exc:raise HTTPException(422,f'Common evaluation refused: {exc}') from exc
    except RuntimeError as exc:raise HTTPException(409,str(exc)) from exc
    from backend.api.routes_model_comparisons import _fingerprint,_sha256
    binding={'source_dataset_path':str(cohort['source']),'dataset_fingerprint':_fingerprint(cohort['source']),
             'checkpoint_sha256':_sha256(context.output_dir/'best_model.pt'),**evaluation_model_context(Path(project['project_dir']),meta),
             'common_cohort':result['common_cohort'],'evaluation_binding_sha256':result['evaluation_binding_sha256'],
             'execution_target':result['execution_target'],'compute_profile_id':result['compute_profile_id'],
             'execution_profile_sha256':result['execution_profile_sha256'],'device':result['device'],
             'runtime_device_identity':result['runtime_device_identity'],'input_receipt':result['input_receipt']}
    from backend.engine.dataset_metadata import metadata_for_path
    from backend.api import routes_dataset
    for row in result['test_predictions']:
        metadata=metadata_for_path(Path(project['project_dir']),cohort['source'],Path(row['file_path']),routes_dataset.STUDIO_ANNOTATIONS_DIR)
        row.update({key:metadata.get(key) for key in ('image_uuid','content_hash','content_version','revision','tags','product','lot','group','workflow_state')})
    from backend.engine.evaluation_evidence import evaluation_analysis
    result['analysis']=evaluation_analysis(result['test_predictions'],context.task,result['class_semantics']['roles'])
    record=EvaluationHistory(Path(project['reports_dir'])/'evaluations').append(result,binding)
    result.update(evaluation_id=record['evaluation_id'],binding=binding,grouped_errors=record['grouped_errors'])
    with _eval_file_lock:_atomic_write_json(context.output_dir/'eval_results.json',result)
    return result


@router.get('/remote-operations')
def common_operation_readback(request:Request,job_id:str=Query(...)):
    from backend.remote.operations import common_operation_rows
    project=_common_project(request);context,_=_common_model(project,job_id)
    return {'operations':common_operation_rows(context)}


class CommonOperationCancel(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:str
    cohort_sha256:str
    evaluation_binding_sha256:str


@router.post('/remote-operations/{op_id}/cancel')
def cancel_common_operation(op_id:str,body:CommonOperationCancel,request:Request):
    from backend.remote.operations import cancel_common_operation as cancel_operation
    project=_common_project(request);context,_=_common_model(project,body.job_id)
    try:return cancel_operation(context,op_id,body.cohort_sha256,body.evaluation_binding_sha256)
    except FileNotFoundError as exc:raise HTTPException(404,str(exc)) from exc
    except (ValueError,RuntimeError) as exc:raise HTTPException(409,str(exc)) from exc


def run_or_load_evaluation(
    job_id: Optional[str] = None,
    dataset_path: Optional[str] = None,
    force_recompute: bool = False,
    source_dataset_path: Optional[str] = None,
    source_task: Optional[str] = None,
    allow_source_revision: bool = False,
    evaluation_dataset_version_id: Optional[str] = None,
    compute_profile_id: Optional[str] = None,
    device: Optional[str] = None,
    project: Optional[dict] = None,
) -> Dict[str, Any]:
    if evaluation_dataset_version_id is not None or compute_profile_id is not None or device is not None:
        return _run_common_evaluation(project, job_id, dataset_path, evaluation_dataset_version_id, compute_profile_id, device, force_recompute, source_task)
    job_id_clean = job_id if isinstance(job_id, str) else None
    ds_path_clean = str(dataset_path) if isinstance(dataset_path, (str, Path)) else None
    force_clean = bool(force_recompute) if isinstance(force_recompute, bool) else False

    out_dir, model_pt, meta, task, resolved_job_id, resolved_dataset = _resolve_job_artifacts(
        job_id=job_id_clean, dataset_path_override=ds_path_clean,
        source_dataset_path=None if allow_source_revision else source_dataset_path, source_task=source_task,
    )

    from backend.remote.operations import remote_job_context, run_remote_evaluation
    from backend.remote.coordinator import ArtifactValidationError, RemoteDisconnected

    try:
        remote_context = remote_job_context(out_dir, resolved_job_id)
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=502, detail=f"Remote evaluation result could not be verified: {exc}") from exc
    if remote_context is not None and ds_path_clean and resolved_dataset.resolve() != remote_context.dataset_path:
        raise HTTPException(
            status_code=422,
            detail="Remote evaluation currently uses its original training snapshot; a different dataset path is unsupported",
        )

    from backend.api import routes_dataset
    from backend.api.routes_model_comparisons import _fingerprint, _sha256
    from backend.engine.evaluation_history import EvaluationHistory
    bound_source = Path(source_dataset_path).resolve() if source_dataset_path else resolved_dataset.resolve()
    evaluation_binding = {"source_dataset_path": str(bound_source),
                          "dataset_fingerprint": _fingerprint(bound_source),
                          "checkpoint_sha256": _sha256(model_pt)}
    from backend.engine.evaluation_history import evaluation_model_context
    evaluation_binding.update(evaluation_model_context(out_dir.parent.parent,meta))
    class_semantics = _evaluation_class_semantics(meta, task)
    from backend.engine.evaluation_sources import remap_prepared_predictions

    def preserve_result(result):
        result["evaluation_contract_version"] = EVALUATION_CONTRACT_VERSION
        if _fingerprint(bound_source) != evaluation_binding["dataset_fingerprint"] or _sha256(model_pt) != evaluation_binding["checkpoint_sha256"]:
            raise HTTPException(409, "Evaluation inputs changed during execution")
        from backend.engine.dataset_metadata import metadata_for_path
        project_root = out_dir.parent.parent
        remap_prepared_predictions(result.get("test_predictions", []), resolved_dataset, bound_source, out_dir)
        for prediction in result.get("test_predictions", []):
            path = prediction.get("file_path")
            if path and Path(path).is_file() and Path(path).resolve().is_relative_to(bound_source):
                metadata = metadata_for_path(project_root, bound_source, Path(path), routes_dataset.STUDIO_ANNOTATIONS_DIR)
                prediction.update({key: metadata.get(key) for key in ("image_uuid", "content_hash", "content_version", "revision", "tags", "product", "lot", "group", "workflow_state")})
        from backend.engine.evaluation_evidence import evaluation_analysis
        result['analysis']=evaluation_analysis(result.get('test_predictions',[]),task,(result.get('class_semantics') or {}).get('roles'))
        record = EvaluationHistory(project_root / "reports" / "evaluations").append(result, evaluation_binding)
        result["evaluation_id"] = record["evaluation_id"]
        result["binding"] = evaluation_binding
        result["grouped_errors"] = record["grouped_errors"]
        return result

    eval_json = out_dir / "eval_results.json"
    if task.lower().strip() == "patch_classification" and not allow_source_revision:
        _patch_manifest_for_checkpoint(_resolve_dataset_dir(resolved_dataset, task), meta)
    if not force_clean and eval_json.is_file():
        try:
            with _eval_file_lock:
                with open(eval_json, "r", encoding="utf-8") as f:
                    cached = json.load(f)
            cm = cached.get("confusion_matrix", {})
            cs = cm.get("cell_samples", {})
            if cs:
                all_exist = True
                for samples in cs.values():
                    for p in samples:
                        if not os.path.exists(p):
                            all_exist = False
                            break
                    if not all_exist:
                        break
                if (all_exist and len(cached.get("test_predictions", [])) > 0
                        and cached.get("evaluation_contract_version") == EVALUATION_CONTRACT_VERSION
                        and cached.get("binding") == evaluation_binding
                        and cached.get("class_semantics") == class_semantics
                        and (task not in ('anomaly', 'anomaly_detection') or isinstance(cached.get('metrics', {}).get('score_spec'), dict))):
                    from backend.engine.zero_escape_analyzer import is_defect_label, compute_sample_defect_score
                    for p in cached.get("test_predictions", []):
                        if "is_defect" not in p:
                            p["is_defect"] = is_defect_label(p.get("ground_truth"), class_semantics["roles"])
                        if "defect_score" not in p:
                            p["defect_score"] = compute_sample_defect_score(p, task=task, roles=class_semantics["roles"])
                    remap_prepared_predictions(cached.get("test_predictions", []), resolved_dataset, bound_source, out_dir)
                    return cached
        except Exception:
            pass

    try:
        if remote_context is not None:
            remote = run_remote_evaluation(remote_context, force_recompute=force_clean)
            remote["class_semantics"] = class_semantics
            _annotate_predictions(remote.get("test_predictions", []), remote.get("task", task), class_semantics["roles"])
            return preserve_result(remote)
    except RemoteDisconnected as exc:
        raise HTTPException(status_code=503, detail=f"Remote evaluation connection lost; retry the same job: {exc}") from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=502, detail=f"Remote evaluation result could not be verified: {exc}") from exc

    dev = get_device()
    effective_data = _resolve_dataset_dir(resolved_dataset, task)

    task_clean = task.lower().strip()
    if task_clean == "classification":
        res = _evaluate_classification(model_pt, meta, effective_data, dev)
    elif task_clean == "patch_classification":
        res = _evaluate_patch_classification(model_pt, meta, effective_data, dev, allow_source_revision=allow_source_revision)
    elif task_clean == "detection":
        res = _evaluate_detection(model_pt, meta, effective_data, dev)
    elif task_clean == "segmentation":
        res = _evaluate_segmentation(model_pt, meta, effective_data, dev)
    elif task_clean in ("anomaly", "anomaly_detection"):
        res = _evaluate_anomaly(model_pt, meta, effective_data, dev)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported vision task: {task}")

    _annotate_predictions(res.get("test_predictions", []), task, class_semantics["roles"])

    payload = {
        "job_id": resolved_job_id,
        "task": task,
        "class_semantics": class_semantics,
        "metrics": res["metrics"],
        "confusion_matrix": res["confusion_matrix"],
        "test_predictions": res["test_predictions"],
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if "dataset_provenance" in res:
        payload["dataset_provenance"] = res["dataset_provenance"]

    payload = preserve_result(payload)
    try:
        with _eval_file_lock:
            _atomic_write_json(eval_json, payload)
    except Exception as e:
        logger.warning("Could not persist eval_results.json to %s: %s", eval_json, e)

    return payload


@router.get("/results")
def get_evaluation_results(
    request: Request,
    job_id: Optional[str] = Query(None),
    dataset_path: Optional[str] = Query(None),
    force_recompute: bool = Query(False),
    source_dataset_path: Optional[str] = Query(None),
    source_task: Optional[str] = Query(None),
    evaluation_dataset_version_id: Optional[str] = Query(None),
    compute_profile_id: Optional[str] = Query(None),
    device: Optional[str] = Query(None),
):
    """
    Returns genuine metrics, clickable Confusion Matrix (with real cell_samples on disk),
    and test predictions evaluated with the active PyTorch model on real images.
    """
    job_id_clean = job_id if isinstance(job_id, str) else None
    ds_path_clean = str(dataset_path) if isinstance(dataset_path, (str, Path)) else None
    force_clean = bool(force_recompute) if isinstance(force_recompute, bool) else False
    return run_or_load_evaluation(
        job_id=job_id_clean, dataset_path=ds_path_clean, force_recompute=force_clean,
        source_dataset_path=source_dataset_path, source_task=source_task,
        evaluation_dataset_version_id=evaluation_dataset_version_id, compute_profile_id=compute_profile_id, device=device,
        project=_common_project(request) if evaluation_dataset_version_id is not None or compute_profile_id is not None or device is not None else None,
    )


@router.get("/heatmap/{image_id:path}")
def get_defect_heatmap(
    image_id: str,
    job_id: Optional[str] = Query(None),
    threshold: Optional[float] = Query(None),
    score_spec: Optional[str] = Query(None),
    format: str = Query("base64", description="'base64' or 'image'"),
    file_path: Optional[str] = Query(None),
):
    """
    Executes model inference on the target image and produces
    the visual overlay heatmap with real-time threshold adjustment.
    """
    img_file = _find_image_file(image_id, file_path=file_path)
    if not img_file or not img_file.is_file():
        raise HTTPException(
            status_code=404,
            detail=format_error_response("ERR_CORRUPT_IMAGE", details=f"Image {image_id} not found"),
        )

    model_file = _find_model_file(job_id)
    if not model_file or not model_file.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Model checkpoint not found for job: {job_id}",
        )

    task = "classification"
    meta_path = model_file.parent / "model_meta.json"
    if meta_path.is_file():
        task = json.loads(meta_path.read_text(encoding='utf-8')).get('task', 'classification')
    from backend.engine.score_contract import resolve_inference_score
    try:
        requested_spec = json.loads(score_spec) if isinstance(score_spec, str) else None
        threshold, resolved_score_spec = resolve_inference_score(model_file, task, threshold, requested_spec)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc

    from backend.remote.operations import remote_job_context, run_remote_inference
    from backend.remote.coordinator import ArtifactValidationError, RemoteDisconnected

    try:
        remote_context = remote_job_context(model_file.parent, model_file.parent.name)
        if remote_context is not None:
            payload, png_bytes = run_remote_inference(remote_context, img_file, threshold, image_id,
                **({'score_spec':resolved_score_spec} if resolved_score_spec is not None else {}))
            if format.lower() in ("image", "png"):
                return Response(content=png_bytes, media_type="image/png")
            return payload
    except RemoteDisconnected as exc:
        raise HTTPException(status_code=503, detail=f"Remote inspection connection lost; retry the same image: {exc}") from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=502, detail=f"Remote inspection result could not be verified: {exc}") from exc

    task = "classification"
    meta_path = model_file.parent / "model_meta.json"
    if meta_path.is_file():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                task = meta.get("task", "classification")
        except Exception:
            pass

    try:
        res = infer(
            task=task,
            model_path=model_file,
            image_input=img_file,
            threshold=threshold,
            device=get_device(),
            **({'score_spec':resolved_score_spec} if resolved_score_spec is not None else {}),
        )
        overlay_rgb = res.visual_overlay
        confidence = float(res.confidence_score)
        predictions = res.predictions
        latency_ms = float(res.latency_ms)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        logger.exception("Inference failed on %s: %s", img_file, e)
        raise HTTPException(status_code=500, detail=f"Inference execution failed: {str(e)}")

    overlay_bgr = cv2.cvtColor(overlay_rgb, cv2.COLOR_RGB2BGR)
    ok, png_buf = cv2.imencode(".png", overlay_bgr)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to encode overlay image")

    png_bytes = png_buf.tobytes()

    if format.lower() in ("image", "png"):
        return Response(content=png_bytes, media_type="image/png")

    b64_str = base64.b64encode(png_bytes).decode("utf-8")
    data_url = f"data:image/png;base64,{b64_str}"

    return {
        "image_id": image_id,
        "threshold": threshold,
        "score_spec": resolved_score_spec,
        "confidence_score": round(confidence, 4),
        "overlay_base64": data_url,
        "predictions": predictions,
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/overkill-underkill")
def get_overkill_underkill_analysis(
    job_id: Optional[str] = Query(None),
    target_max_underkill: int = Query(0, ge=0),
    cost_escape: float = Query(500.0, ge=0.0),
    cost_scrap: float = Query(25.0, ge=0.0),
    current_threshold: float = Query(0.5),
):
    """
    Industrial Overkill (과검) vs Underkill (미검) Optimization & Trade-off Curve.
    Computes exact trade-off across confidence/anomaly thresholds:
      - Underkill (미검, False Negative): Defective item escaped as OK (High Risk).
      - Overkill (과검, False Positive): Pure OK item falsely flagged as Defect (Yield Loss).
      - Recommends optimal threshold guaranteeing target_max_underkill (e.g. Zero Underkill).
    """
    from backend.engine.zero_escape_analyzer import analyze_zero_escape

    target_job = job_id or (training_job_manager.active_job_id if hasattr(training_job_manager, "active_job_id") else None)
    if not target_job:
        raise HTTPException(status_code=422, detail="A completed training job is required for calibration analysis.")
    from backend.engine.calibration_evidence import validate_calibration_evidence

    eval_payload = None
    # Explicit directory inputs retain their import contract. Normal jobs always
    # use the checkpoint/data binding checks shared with the evaluation screen.
    if Path(target_job).is_dir():
        try:
            with _eval_file_lock:
                eval_payload = json.loads((Path(target_job) / "eval_results.json").read_text(encoding="utf-8"))
            evidence = validate_calibration_evidence(eval_payload, EVALUATION_CONTRACT_VERSION)
        except (OSError, ValueError, TypeError):
            eval_payload = None
    if eval_payload is None:
        try:
            eval_payload = run_or_load_evaluation(job_id=target_job)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, detail=f"No usable evaluation predictions for calibration: {exc}") from exc
    try:
        evidence = validate_calibration_evidence(eval_payload, EVALUATION_CONTRACT_VERSION)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    predictions = eval_payload["test_predictions"]
    task = eval_payload.get("task", "classification")
    spec = eval_payload.get('metrics', {}).get('score_spec')
    if (isinstance(spec, dict) and spec.get('domain') == 'distance') or (
            task in ('anomaly', 'anomaly_detection') and not isinstance(spec, dict)):
        raise HTTPException(422, 'Raw distance calibration requires a train/validation calibration run; the probability optimizer cannot rescale distance scores. Use the saved model threshold or a bound manual override.')
    if not math.isfinite(current_threshold) or not 0 <= current_threshold <= 1:
        raise HTTPException(422, 'Probability threshold must be between 0 and 1')
    from backend.engine.class_semantics import recorded_roles
    try:
        class_roles = recorded_roles(eval_payload)
    except ValueError as exc:
        raise HTTPException(422, detail=f"Evaluation class semantics record is invalid: {exc}") from exc

    result = analyze_zero_escape(
        predictions=predictions,
        task=task,
        target_max_underkill=target_max_underkill,
        cost_escape=cost_escape,
        cost_scrap=cost_scrap,
        current_threshold=current_threshold,
        num_threshold_steps=101,
        class_roles=class_roles,
    )
    result["job_id"] = target_job
    result["evaluated_split"] = evidence["evaluated_split"]
    result["selection_overlap"] = evidence["selection_overlap"]
    result["calibration_evidence"] = evidence
    return result


class ZeroEscapeCalibrateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: Optional[str] = None
    target_max_underkill: Optional[int] = 0
    cost_escape: Optional[float] = 500.0
    cost_scrap: Optional[float] = 25.0
    current_threshold: Optional[float] = 0.50
    apply_to_eval_results: Optional[bool] = True


def run_zero_escape_calibration(
    job_id: Optional[str] = None,
    target_max_underkill: int = 0,
    cost_escape: float = 500.0,
    cost_scrap: float = 25.0,
    current_threshold: float = 0.50,
    apply_to_eval_results: bool = True,
) -> Dict[str, Any]:
    """Runs authentic zero-escape calibration and updates eval_results.json."""
    result = get_overkill_underkill_analysis(
        job_id=job_id,
        target_max_underkill=target_max_underkill,
        cost_escape=cost_escape,
        cost_scrap=cost_scrap,
        current_threshold=current_threshold,
    )
    if result.get("total_defects", 0) == 0 or result.get("total_normals", 0) == 0:
        raise HTTPException(status_code=422, detail="Calibration requires both NG and OK validation samples.")

    optimal_th = float(result.get("optimal_threshold", current_threshold))
    target_job = result.get("job_id") or job_id

    if apply_to_eval_results and target_job:
        from backend.engine.calibration_evidence import validate_calibration_evidence, mark_calibration_evidence
        if Path(target_job).is_dir():
            eval_path = Path(target_job) / "eval_results.json"
        else:
            eval_path = _resolve_job_artifacts(job_id=target_job)[0] / "eval_results.json"
        with _eval_file_lock:
            try:
                data = json.loads(eval_path.read_text(encoding="utf-8"))
                current = validate_calibration_evidence(data, EVALUATION_CONTRACT_VERSION)
                if current != result["calibration_evidence"]:
                    raise HTTPException(409, "Evaluation predictions changed during calibration; retry the analysis")
                marked = mark_calibration_evidence(data, optimal_th, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), EVALUATION_CONTRACT_VERSION)
                _atomic_write_json(eval_path, marked)
                result["calibration_evidence"] = marked["calibration_evidence"]
            except ValueError as exc:
                raise HTTPException(409, detail=f"Evaluation evidence changed during calibration: {exc}") from exc
            except OSError as exc:
                logger.warning("Could not persist calibrated threshold to %s: %s", eval_path, exc)
                raise HTTPException(500, detail="Calibrated threshold could not be saved; previous settings are retained") from exc

    result["calibrated"] = True
    underkill_cnt = result.get("optimal_stats", {}).get("underkill_count", 0)
    result["message"] = (
        f"미검 제로화 튜닝 완료: 최적 임계값 τ* = {optimal_th:.4f} (미검 {underkill_cnt}건)"
    )
    result["status"] = "success"
    return result


@router.post("/zero-escape-calibrate")
def post_zero_escape_calibrate(req: ZeroEscapeCalibrateRequest):
    """1-Click Zero-Escape Calibration Endpoint."""
    return run_zero_escape_calibration(
        job_id=req.job_id,
        target_max_underkill=req.target_max_underkill if req.target_max_underkill is not None else 0,
        cost_escape=req.cost_escape if req.cost_escape is not None else 500.0,
        cost_scrap=req.cost_scrap if req.cost_scrap is not None else 25.0,
        current_threshold=req.current_threshold if req.current_threshold is not None else 0.50,
        apply_to_eval_results=bool(req.apply_to_eval_results),
    )


@router.get("/zero-escape-calibrate")
def get_zero_escape_calibrate_endpoint(
    job_id: Optional[str] = Query(None),
    target_max_underkill: int = Query(0, ge=0),
    cost_escape: float = Query(500.0, ge=0.0),
    cost_scrap: float = Query(25.0, ge=0.0),
    current_threshold: float = Query(0.5),
    apply_to_eval_results: bool = Query(True),
):
    return run_zero_escape_calibration(
        job_id=job_id,
        target_max_underkill=target_max_underkill,
        cost_escape=cost_escape,
        cost_scrap=cost_scrap,
        current_threshold=current_threshold,
        apply_to_eval_results=apply_to_eval_results,
    )


class BenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: Optional[str] = None
    iterations: Optional[int] = 25
    resolution: Optional[int] = 256


@router.post("/benchmark")
def run_inference_benchmark(req: BenchmarkRequest):
    """
    Benchmark the already loaded model's forward pass on synthetic input.

    Checkpoint loading, preprocessing, overlays, and I/O are outside the timed
    region, so the reported FPS does not confuse startup cost with inference.
    """
    from backend.engine.exporter import load_checkpoint_and_reconstruct_model

    iters = max(5, min(req.iterations or 25, 100))
    res = req.resolution or 256
    if res < 32 or res > 2048:
        raise HTTPException(status_code=422, detail="Benchmark resolution must be between 32 and 2048 pixels")

    model_file = _find_model_file(req.job_id) if req.job_id else None
    if not model_file or not model_file.is_file():
        raise HTTPException(status_code=404, detail="A trained model checkpoint is required for benchmarking.")
    from backend.remote.operations import remote_job_context, run_remote_benchmark
    from backend.remote.coordinator import ArtifactValidationError, RemoteDisconnected

    try:
        remote_context = remote_job_context(model_file.parent, model_file.parent.name)
        if remote_context is not None:
            return run_remote_benchmark(remote_context, iters, res)
    except RemoteDisconnected as exc:
        raise HTTPException(status_code=503, detail=f"Remote benchmark connection lost; retry the same run: {exc}") from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=502, detail=f"Remote benchmark result could not be verified: {exc}") from exc

    device = get_device()
    dev_name = "Metal MPS" if device.type == "mps" else ("CUDA" if device.type == "cuda" else "CPU")
    try:
        model, meta, _ = load_checkpoint_and_reconstruct_model(model_file)
        model = model.to(device).eval()
        task = str(meta.get("task", "classification"))
        dummy_input = torch.rand((1, 3, res, res), dtype=torch.float32, device=device)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot load model for benchmark: {exc}") from exc

    def synchronize() -> None:
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        elif device.type == "mps":
            torch.mps.synchronize()

    latencies = []
    try:
        with torch.inference_mode():
            for _ in range(3):
                model(dummy_input)
                synchronize()
            for _ in range(iters):
                t0 = time.perf_counter()
                model(dummy_input)
                synchronize()
                latencies.append((time.perf_counter() - t0) * 1000.0)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Model inference failed: {exc}") from exc

    latencies_arr = np.array(latencies)
    mean_ms = float(np.mean(latencies_arr))
    p95_ms = float(np.percentile(latencies_arr, 95))
    min_ms = float(np.min(latencies_arr))
    max_ms = float(np.max(latencies_arr))
    std_ms = float(np.std(latencies_arr))
    fps = round(1000.0 / mean_ms, 1) if mean_ms > 0 else 0.0

    return {
        "status": "success",
        "model_path": str(model_file.resolve()),
        "task": task,
        "input_kind": "synthetic_random_tensor",
        "measurement_scope": "model_forward_only",
        "device": device.type,
        "device_name": dev_name,
        "iterations": iters,
        "mean_latency_ms": round(mean_ms, 2),
        "p95_latency_ms": round(p95_ms, 2),
        "min_latency_ms": round(min_ms, 2),
        "max_latency_ms": round(max_ms, 2),
        "std_latency_ms": round(std_ms, 2),
        "fps": fps,
        "resolution": f"{res}x{res}",
        "batch_size": 1,
    }


# Keep candidate-versus-incumbent evidence under the Stage 4 evaluation API.
from backend.api.routes_model_comparisons import router as model_comparisons_router

router.include_router(model_comparisons_router)
