"""
backend/engine/trainer.py

Unified AutoML Vision Training Controller and Inference Engine.
Orchestrates:
  1. Presets ("⚡ Fast Prototype" vs "🎯 High Precision")
  2. Auto image sizing (32-multiples aspect ratio preservation)
  3. Early stopping and atomic model checkpointing (best_model.pt + model_meta.json)
  4. Real-time WebSocket event streaming callbacks
  5. Clean thread-safe abort / cancellation protocol
  6. Unified multi-task inference API with visual overlays
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image

from backend.engine.device import (
    clear_device_cache,
    get_device,
    get_host_telemetry,
    to_device,
)
from backend.engine.dataset_loaders import (
    ClassificationDataset,
    DetectionDataset,
    SegmentationDataset,
    AnomalyDataset,
    create_dataloader,
)
from backend.engine.augmentations import create_industrial_transforms
from backend.engine.scheduler import create_optimizer_and_scheduler
from backend.engine.classification import (
    create_classification_model,
    create_classification_loss,
    compute_class_weights,
    GradCAM,
)
from backend.engine.detection import (
    create_detection_model,
    draw_detection_overlays,
)
from backend.engine.segmentation import (
    build_segmentation_model,
    ComboLoss,
    extract_polygons,
)
from backend.engine.anomaly import (
    PaDiMDetector,
    PatchCoreDetector,
    compute_anomaly_metrics,
)

logger = logging.getLogger("vision_ai_studio.trainer")


# ============================================================================
# Presets & Configuration Data Models
# ============================================================================

@dataclass(frozen=True)
class AutoMLPresetConfig:
    preset_name: str
    target_epochs: int
    image_size: int
    batch_size: int
    learning_rate: float
    patience: int
    backbone_classification: str
    backbone_detection: str
    backbone_segmentation: str
    backbone_anomaly: str


PRESET_CONFIGS: Dict[str, AutoMLPresetConfig] = {
    "fast": AutoMLPresetConfig(
        preset_name="⚡ Fast Prototype",
        target_epochs=12,
        image_size=256,
        batch_size=16,
        learning_rate=1e-3,
        patience=4,
        backbone_classification="resnet18",
        backbone_detection="fasterrcnn_mobilenet_v3_large_fpn",
        backbone_segmentation="unet_lightweight",
        backbone_anomaly="padim_resnet18",
    ),
    "precision": AutoMLPresetConfig(
        preset_name="🎯 High Precision",
        target_epochs=30,
        image_size=512,
        batch_size=8,
        learning_rate=5e-4,
        patience=8,
        backbone_classification="convnext_tiny",
        backbone_detection="fasterrcnn_resnet50_fpn_v2",
        backbone_segmentation="unet_full",
        backbone_anomaly="patchcore_resnet50",
    ),
}


# ============================================================================
# Aspect-Ratio Sizing to 32-Multiples
# ============================================================================

def calculate_optimal_image_size(
    original_size: Tuple[int, int],  # (width, height)
    target_max: int = 256,
    multiple_of: int = 32,
    min_dim: int = 64,
) -> Tuple[int, int]:
    """Snaps image dimensions to 32-multiples preserving aspect ratio."""
    w, h = original_size
    if w <= 0 or h <= 0:
        return (target_max, target_max)

    aspect_ratio = float(w) / float(h)
    if w >= h:
        new_w = target_max
        new_h = max(min_dim, int(round(target_max / aspect_ratio)))
    else:
        new_h = target_max
        new_w = max(min_dim, int(round(target_max * aspect_ratio)))

    snapped_w = max(min_dim, int(round(new_w / multiple_of)) * multiple_of)
    snapped_h = max(min_dim, int(round(new_h / multiple_of)) * multiple_of)
    return (snapped_w, snapped_h)


# ============================================================================
# Early Stopping Logic
# ============================================================================

class EarlyStopping:
    """Monitors validation metric with configurable patience and min_delta."""

    def __init__(self, patience: int = 5, min_delta: float = 1e-4, mode: str = "min"):
        self.patience = patience
        self.min_delta = min_delta
        self.mode = mode.lower().strip()
        self.counter = 0
        self.best_score = float("inf") if self.mode == "min" else float("-inf")
        self.early_stop = False
        self.best_epoch = 0

    def step(self, current_score: float, epoch: int) -> bool:
        if self.mode == "min":
            improved = current_score < (self.best_score - self.min_delta)
        else:
            improved = current_score > (self.best_score + self.min_delta)

        if improved:
            self.best_score = current_score
            self.best_epoch = epoch
            self.counter = 0
            return True
        else:
            self.counter += 1
            if self.counter >= self.patience:
                self.early_stop = True
            return False


# ============================================================================
# Telemetry Callback Interface (Matching WebSocket Contracts)
# ============================================================================

class TrainingCallback:
    """Base callback interface matching WebSocket telemetry protocol."""

    def on_training_start(self, config: Dict[str, Any]) -> None:
        pass

    def on_step_end(self, step: int, total_steps: int, current_loss: float, epoch: int) -> None:
        pass

    def on_epoch_end(
        self,
        epoch: int,
        total_epochs: int,
        train_loss: float,
        val_loss: float,
        lr: float,
        metrics: Dict[str, float],
    ) -> None:
        pass

    def on_hardware_stats(self, stats: Dict[str, Any]) -> None:
        pass

    def on_training_completed(
        self, job_id: str, duration_seconds: float, best_metric: float, model_path: str
    ) -> None:
        pass

    def on_training_aborted(self, epoch: int, reason: str) -> None:
        pass

    def on_error(self, error: Exception, stage: str) -> None:
        pass


# ============================================================================
# Unified Inference Output Model
# ============================================================================

@dataclass
class InferenceResult:
    """Unified inference result for all 4 vision tasks."""
    task: str
    predictions: Any              # Structured predictions per task
    confidence_score: float       # Top confidence score in [0.0, 1.0]
    visual_overlay: np.ndarray    # RGB uint8 numpy array with overlays
    latency_ms: float             # Execution latency in ms
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Unified AutoML Trainer Controller
# ============================================================================

class UnifiedAutoMLTrainer:
    """
    Unified AutoML Vision Training Controller.
    Supports all 4 industrial vision tasks, preset heuristics, clean aborts,
    and atomic checkpointing.
    """

    def __init__(
        self,
        task: str,
        dataset_path: Union[str, Path],
        output_dir: Union[str, Path],
        preset: str = "fast",
        device: Optional[Union[str, torch.device]] = None,
        callback: Optional[TrainingCallback] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        self.task = task.lower().strip()
        self.dataset_path = Path(dataset_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.device = get_device(device)
        self.callback = callback or TrainingCallback()
        self._abort_flag = threading.Event()

        preset_key = preset.lower().strip()
        self.preset_key = preset_key
        self.config = PRESET_CONFIGS.get(preset_key, PRESET_CONFIGS["fast"])
        self.overrides = config_overrides or {}

    def abort(self) -> None:
        """Signal trainer to immediately halt execution."""
        logger.info("Trainer abort requested.")
        self._abort_flag.set()

    def train(self, job_id: str = "job_default") -> Dict[str, Any]:
        """Executes full AutoML training workflow with telemetry streaming."""
        start_time = time.time()
        epochs = self.overrides.get("epochs", self.config.target_epochs)
        target_size = self.overrides.get("image_size", self.config.image_size)
        optimal_size = calculate_optimal_image_size((target_size, target_size), target_max=target_size)

        self.callback.on_training_start({
            "job_id": job_id,
            "task": self.task,
            "preset": self.config.preset_name,
            "epochs": epochs,
            "image_size": optimal_size,
            "device": str(self.device),
        })

        try:
            # 1. Setup Data Augmentation & Datasets
            aug = create_industrial_transforms(task=self.task, preset="fast", is_training=True)

            if self.task == "classification":
                train_ds = ClassificationDataset(
                    root_dir=self.dataset_path, split="train", transform=aug, image_size=optimal_size
                )
                val_ds = ClassificationDataset(
                    root_dir=self.dataset_path, split="val", image_size=optimal_size
                )
                classes = train_ds.classes
                num_classes = max(2, len(classes))
                backbone = self.config.backbone_classification
                model = create_classification_model(backbone=backbone, num_classes=num_classes).to(self.device)
                if not hasattr(train_ds, "class_counts"):
                    counts = [0] * num_classes
                    for _, cidx in getattr(train_ds, "samples", []):
                        if 0 <= cidx < num_classes:
                            counts[cidx] += 1
                    train_ds.class_counts = counts
                class_weights = compute_class_weights(train_ds.class_counts, num_classes=num_classes).to(self.device)
                criterion = create_classification_loss(weights=class_weights, label_smoothing=0.1)

            elif self.task == "detection":
                train_img = (self.dataset_path / "images" / "train") if (self.dataset_path / "images" / "train").exists() else (self.dataset_path / "images")
                val_img = (self.dataset_path / "images" / "val") if (self.dataset_path / "images" / "val").exists() else train_img
                train_anno = (self.dataset_path / "annotations_train.json") if (self.dataset_path / "annotations_train.json").exists() else (self.dataset_path / "annotations.json")
                val_anno = (self.dataset_path / "annotations_val.json") if (self.dataset_path / "annotations_val.json").exists() else train_anno

                train_ds = DetectionDataset(images_dir=train_img, annotation_file=train_anno, transform=aug, image_size=optimal_size)
                val_ds = DetectionDataset(images_dir=val_img, annotation_file=val_anno, image_size=optimal_size)
                classes = list(train_ds.categories.values())
                num_classes = max(2, len(classes))
                model = create_detection_model(preset=self.preset_key, num_classes=num_classes).to(self.device)
                criterion = None

            elif self.task == "segmentation":
                train_img = (self.dataset_path / "images" / "train") if (self.dataset_path / "images" / "train").exists() else (self.dataset_path / "images")
                val_img = (self.dataset_path / "images" / "val") if (self.dataset_path / "images" / "val").exists() else train_img
                train_mask = (self.dataset_path / "masks" / "train") if (self.dataset_path / "masks" / "train").exists() else (self.dataset_path / "masks")
                val_mask = (self.dataset_path / "masks" / "val") if (self.dataset_path / "masks" / "val").exists() else train_mask

                train_ds = SegmentationDataset(images_dir=train_img, masks_dir=train_mask, transform=aug, image_size=optimal_size)
                val_ds = SegmentationDataset(images_dir=val_img, masks_dir=val_mask, image_size=optimal_size)
                classes = ["background", "defect"]
                model = build_segmentation_model(num_classes=len(classes), preset=self.preset_key).to(self.device)
                criterion = ComboLoss(num_classes=len(classes), dice_weight=1.0)

            elif self.task in ("anomaly", "anomaly_detection"):
                train_ds = AnomalyDataset(root_dir=self.dataset_path, split="train", transform=aug, image_size=optimal_size)
                val_ds = AnomalyDataset(root_dir=self.dataset_path, split="test", image_size=optimal_size)
                classes = ["good", "anomaly"]
                if "patchcore" in self.config.backbone_anomaly:
                    model = PatchCoreDetector(backbone_name="resnet18", device=self.device)
                else:
                    model = PaDiMDetector(backbone_name="resnet18", device=self.device)

            train_loader = create_dataloader(train_ds, batch_size=self.config.batch_size, shuffle=True, task=self.task)
            val_loader = create_dataloader(val_ds, batch_size=self.config.batch_size, shuffle=False, task=self.task)

            # 2. Task 4 Anomaly Workflow (OK-Only Embedding Fit)
            if self.task in ("anomaly", "anomaly_detection"):
                if self._abort_flag.is_set():
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(0, "Training aborted by user request")
                    return {"status": "aborted", "epoch": 0}

                model.fit(train_loader)

                if self._abort_flag.is_set():
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(0, "Training aborted by user request")
                    return {"status": "aborted", "epoch": 0}

                val_scores = []
                val_labels = []
                with torch.no_grad():
                    for imgs, labels, masks in val_loader:
                        if self._abort_flag.is_set():
                            clear_device_cache(self.device)
                            self.callback.on_training_aborted(0, "Training aborted by user request")
                            return {"status": "aborted", "epoch": 0}
                        imgs = imgs.to(self.device)
                        _, scores = model(imgs)
                        val_scores.extend(scores.cpu().tolist())
                        val_labels.extend(labels.cpu().tolist())

                anom_metrics = compute_anomaly_metrics(val_scores, val_labels)
                auroc = float(anom_metrics.get("image_auroc", 1.0))
                val_metric = round(1.0 - auroc, 4)
                self.callback.on_epoch_end(
                    epoch=0,
                    total_epochs=1,
                    train_loss=0.0,
                    val_loss=val_metric,
                    lr=0.0,
                    metrics={"image_auroc": auroc, "f1_score": float(anom_metrics.get("f1_score", 1.0))},
                )
                self._save_checkpoint(0, model, val_metric, classes, optimal_size, time.time() - start_time)
                elapsed = time.time() - start_time
                best_model_path = str(self.output_dir / "best_model.pt")
                self.callback.on_training_completed(job_id, elapsed, val_metric, best_model_path)
                return {"status": "completed", "best_metric": val_metric, "model_path": best_model_path}

            # 3. Supervised Tasks Optimization Loop
            optimizer, scheduler = create_optimizer_and_scheduler(
                model=model,
                lr=self.config.learning_rate,
                total_epochs=epochs,
                warmup_epochs=min(3, max(1, epochs // 4)),
            )
            early_stopping = EarlyStopping(patience=self.config.patience, mode="min")
            total_steps = epochs * len(train_loader)
            global_step = 0

            for epoch in range(epochs):
                if self._abort_flag.is_set():
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(epoch, "Training aborted by user request")
                    return {"status": "aborted", "epoch": epoch}

                model.train()
                train_losses = []

                for step, batch in enumerate(train_loader):
                    if self._abort_flag.is_set():
                        clear_device_cache(self.device)
                        self.callback.on_training_aborted(epoch, "Training aborted by user request")
                        return {"status": "aborted", "epoch": epoch}

                    optimizer.zero_grad()
                    if self.task == "classification":
                        imgs, targets = batch
                        imgs, targets = imgs.to(self.device), targets.to(self.device)
                        outputs = model(imgs)
                        loss = criterion(outputs, targets)
                    elif self.task == "detection":
                        imgs, targets = batch
                        imgs = [img.to(self.device) for img in imgs]
                        targets_dev = [{k: v.to(self.device) for k, v in t.items()} for t in targets]
                        loss_dict = model(imgs, targets_dev)
                        loss = sum(v for v in loss_dict.values())
                    elif self.task == "segmentation":
                        imgs, masks = batch
                        imgs, masks = imgs.to(self.device), masks.to(self.device)
                        outputs = model(imgs)
                        if isinstance(criterion, ComboLoss):
                            loss, _ = criterion(outputs, masks)
                        else:
                            loss = criterion(outputs, masks)

                    loss.backward()
                    optimizer.step()

                    loss_val = float(loss.item())
                    train_losses.append(loss_val)
                    self.callback.on_step_end(global_step, total_steps, loss_val, epoch)
                    global_step += 1

                # Validation Evaluation
                model.eval()
                val_losses = []
                with torch.no_grad():
                    for batch in val_loader:
                        if self.task == "classification":
                            imgs, targets = batch
                            imgs, targets = imgs.to(self.device), targets.to(self.device)
                            loss = criterion(model(imgs), targets)
                        elif self.task == "detection":
                            imgs, targets = batch
                            imgs = [img.to(self.device) for img in imgs]
                            targets_dev = [{k: v.to(self.device) for k, v in t.items()} for t in targets]
                            model.train()
                            loss_dict = model(imgs, targets_dev)
                            loss = sum(v for v in loss_dict.values())
                            model.eval()
                        elif self.task == "segmentation":
                            imgs, masks = batch
                            imgs, masks = imgs.to(self.device), masks.to(self.device)
                            if isinstance(criterion, ComboLoss):
                                loss, _ = criterion(model(imgs), masks)
                            else:
                                loss = criterion(model(imgs), masks)
                        val_losses.append(float(loss.item()))

                mean_train_loss = float(np.mean(train_losses)) if train_losses else 0.0
                mean_val_loss = float(np.mean(val_losses)) if val_losses else 0.0
                curr_lr = float(optimizer.param_groups[0]["lr"])

                improved = early_stopping.step(mean_val_loss, epoch)
                if improved:
                    self._save_checkpoint(epoch, model, mean_val_loss, classes, optimal_size, time.time() - start_time)

                # Broadcast Hardware Telemetry
                telemetry = get_host_telemetry(self.device)
                self.callback.on_hardware_stats({
                    "cpu_percent": telemetry.cpu_percent,
                    "memory_percent": telemetry.memory_percent,
                    "gpu_name": telemetry.gpu_name,
                    "gpu_memory_used_mb": telemetry.gpu_memory_used_mb,
                    "device_type": telemetry.device_type,
                })

                self.callback.on_epoch_end(
                    epoch=epoch,
                    total_epochs=epochs,
                    train_loss=mean_train_loss,
                    val_loss=mean_val_loss,
                    lr=curr_lr,
                    metrics={"val_loss": mean_val_loss},
                )

                scheduler.step()
                if early_stopping.early_stop:
                    logger.info("Early stopping triggered at epoch %d", epoch)
                    break

            elapsed = time.time() - start_time
            best_model_path = str(self.output_dir / "best_model.pt")
            if not (self.output_dir / "best_model.pt").exists():
                fallback_loss = mean_val_loss if (val_losses and not np.isnan(mean_val_loss)) else 0.0
                self._save_checkpoint(epochs - 1, model, fallback_loss, classes, optimal_size, elapsed)
            self.callback.on_training_completed(job_id, elapsed, early_stopping.best_score, best_model_path)
            return {"status": "completed", "best_metric": early_stopping.best_score, "model_path": best_model_path}

        except Exception as e:
            logger.exception("Error during training execution: %s", str(e))
            self.callback.on_error(e, "training_loop")
            raise

    def _save_checkpoint(
        self, epoch: int, model: Any, metric: float, classes: List[str], img_size: Tuple[int, int], elapsed: float
    ) -> None:
        """Atomically saves best_model.pt and generates model_meta.json."""
        tmp_pt = self.output_dir / "best_model.pt.tmp"

        meta = {
            "task": self.task,
            "preset": self.preset_key,
            "preset_name": self.config.preset_name,
            "best_epoch": epoch,
            "best_metric": round(metric, 5),
            "classes": classes,
            "image_size": list(img_size),
            "device": str(self.device),
            "training_duration_seconds": round(elapsed, 2),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        if self.task == "classification":
            meta["backbone"] = self.config.backbone_classification
        elif self.task == "detection":
            meta["detector_preset"] = self.preset_key
        elif self.task == "segmentation":
            meta["preset"] = self.preset_key
            meta["features"] = [64, 128, 256, 512] if self.preset_key == "precision" else [32, 64, 128, 256]
        elif self.task in ("anomaly", "anomaly_detection"):
            meta["detector_type"] = "patchcore" if "patchcore" in self.config.backbone_anomaly else "padim"

        ckpt_payload = {
            "epoch": epoch,
            "model_state_dict": model.state_dict(),
            "best_metric": metric,
            "task": self.task,
            "classes": classes,
            "preset": self.preset_key,
            **{k: v for k, v in meta.items() if k not in ("best_metric", "task", "classes")},
        }
        torch.save(ckpt_payload, tmp_pt)
        # POSIX atomic rename prevents corrupt partial files on sudden abort
        tmp_pt.replace(self.output_dir / "best_model.pt")

        with open(self.output_dir / "model_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)


# ============================================================================
# Unified Industrial Inference API
# ============================================================================

def infer(
    task: str,
    model_path: Union[str, Path],
    image_input: Union[str, Path, np.ndarray, Image.Image],
    threshold: float = 0.5,
    device: Optional[Union[str, torch.device]] = None,
) -> InferenceResult:
    """
    Unified industrial inference engine supporting all 4 vision tasks.
    Returns structured predictions, confidence score, and visual overlays.
    """
    start_time = time.time()
    dev = get_device(device)
    m_path = Path(model_path)
    meta_path = m_path.parent / "model_meta.json"
    meta = {}
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

    # 1. Normalize image input to RGB numpy array
    if isinstance(image_input, (str, Path)):
        pil_img = Image.open(str(image_input)).convert("RGB")
        img_np = np.array(pil_img)
    elif isinstance(image_input, Image.Image):
        img_np = np.array(image_input.convert("RGB"))
    elif isinstance(image_input, np.ndarray):
        img_np = image_input.copy()
        if img_np.ndim == 2:
            img_np = cv2.cvtColor(img_np, cv2.COLOR_GRAY2RGB)
        elif img_np.ndim == 3 and img_np.shape[2] == 4:
            img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2RGB)
    else:
        raise ValueError(f"Unsupported image input type: {type(image_input)}")

    if img_np.ndim == 3 and img_np.shape[2] == 4:
        img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2RGB)

    orig_h, orig_w = img_np.shape[:2]

    ckpt = torch.load(m_path, map_location=dev, weights_only=False)
    state_dict = ckpt["model_state_dict"] if "model_state_dict" in ckpt else ckpt
    ckpt_meta = {k: v for k, v in ckpt.items() if k != "model_state_dict"}
    meta = {**ckpt_meta, **meta}

    img_size = tuple(meta.get("image_size", [256, 256]))
    resized_img = cv2.resize(img_np, img_size, interpolation=cv2.INTER_LINEAR)
    img_t = torch.from_numpy(resized_img.transpose(2, 0, 1)).float().unsqueeze(0) / 255.0
    img_t = img_t.to(dev)

    overlay = img_np.copy()
    classes = meta.get("classes", ckpt.get("classes", ["OK", "Defect"]))
    task_clean = task.lower().strip()

    # 2. Execute Task-Specific Forward Pass & Overlay Rendering
    if task_clean == "classification":
        backbone = meta.get("backbone", "resnet18")
        model = create_classification_model(backbone=backbone, num_classes=max(2, len(classes)), pretrained=False).to(dev)
        model.load_state_dict(state_dict)
        model.eval()

        with torch.no_grad():
            logits = model(img_t)
            probs = torch.softmax(logits, dim=-1)[0]
            conf, pred_idx = probs.max(dim=-1)
            confidence = float(conf.item())
            p_idx = int(pred_idx.item())
            pred_class = classes[p_idx] if p_idx < len(classes) else f"Class_{p_idx}"

        # Generate Grad-CAM activation heatmap overlay
        if hasattr(model, "target_gradcam_layer") and model.target_gradcam_layer is not None:
            try:
                with GradCAM(model, model.target_gradcam_layer) as cam:
                    heatmap = cam.generate(img_t, target_class=pred_idx)[0].detach().cpu().numpy()
                heatmap_u8 = (heatmap * 255).astype(np.uint8)
                heatmap_resized = cv2.resize(heatmap_u8, (orig_w, orig_h))
                colored_cam = cv2.applyColorMap(heatmap_resized, cv2.COLORMAP_JET)
                cv2.addWeighted(colored_cam, 0.4, overlay, 0.6, 0, overlay)
            except Exception as cam_err:
                logger.warning("Failed to generate Grad-CAM overlay: %s", cam_err)

        badge_text = f"{pred_class} ({confidence:.1%})"
        badge_color = (40, 200, 40) if pred_class.lower() in ("ok", "good", "pass") else (220, 40, 40)
        cv2.rectangle(overlay, (10, 10), (min(orig_w - 10, 260), 45), (30, 30, 30), -1)
        cv2.putText(overlay, badge_text, (16, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.65, badge_color, 2)

        latency = (time.time() - start_time) * 1000.0
        return InferenceResult(
            task="classification",
            predictions={"predicted_class": pred_class, "class_index": p_idx, "confidence": confidence},
            confidence_score=confidence,
            visual_overlay=overlay,
            latency_ms=round(latency, 2),
            metadata=meta,
        )

    elif task_clean == "detection":
        det_preset = meta.get("detector_preset", meta.get("preset", "fast"))
        model = create_detection_model(preset=det_preset, num_classes=max(2, len(classes)), pretrained=False).to(dev)
        model.load_state_dict(state_dict)
        model.eval()

        with torch.no_grad():
            outputs = model(img_t)
            boxes = outputs[0]["boxes"].cpu().numpy()
            scores = outputs[0]["scores"].cpu().numpy()
            labels = outputs[0]["labels"].cpu().numpy()

        filtered = []
        for b, s, l in zip(boxes, scores, labels):
            if s >= threshold:
                scale_x = orig_w / img_size[0]
                scale_y = orig_h / img_size[1]
                x1, y1, x2, y2 = b[0] * scale_x, b[1] * scale_y, b[2] * scale_x, b[3] * scale_y
                lbl = classes[l] if l < len(classes) else f"defect_{l}"
                filtered.append({"bbox": [float(x1), float(y1), float(x2), float(y2)], "score": float(s), "label": lbl})
                cv2.rectangle(overlay, (int(x1), int(y1)), (int(x2), int(y2)), (255, 50, 50), 2)
                cv2.putText(overlay, f"{lbl} {s:.2f}", (int(x1), max(15, int(y1) - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 50, 50), 1)

        confidence = max([d["score"] for d in filtered], default=float(scores.max() if len(scores) > 0 else 0.0))
        latency = (time.time() - start_time) * 1000.0
        return InferenceResult(
            task="detection",
            predictions=filtered,
            confidence_score=confidence,
            visual_overlay=overlay,
            latency_ms=round(latency, 2),
            metadata=meta,
        )

    elif task_clean == "segmentation":
        seg_preset = meta.get("preset", "fast")
        model = build_segmentation_model(num_classes=len(classes), preset=seg_preset, pretrained=False).to(dev)
        model.load_state_dict(state_dict)
        model.eval()

        with torch.no_grad():
            logits = model(img_t)
            pred_mask = torch.argmax(logits, dim=1)[0].cpu().numpy().astype(np.uint8)

        mask_orig = cv2.resize(pred_mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
        color_mask = np.zeros_like(overlay)
        color_mask[mask_orig > 0] = [255, 40, 40]
        cv2.addWeighted(color_mask, 0.4, overlay, 0.6, 0, overlay)

        polygons = extract_polygons(mask_orig, class_names={1: "defect"})

        coverage = float(np.count_nonzero(mask_orig)) / float(orig_w * orig_h)

        probs = torch.softmax(logits, dim=1)[0]
        mask_dev = torch.as_tensor(mask_orig, device=dev)
        if mask_dev.shape != probs.shape[1:]:
            probs_spatial = F.interpolate(probs.unsqueeze(0), size=(orig_h, orig_w), mode="bilinear", align_corners=False)[0]
        else:
            probs_spatial = probs

        if (mask_orig > 0).any():
            confidence = float(probs_spatial[1][mask_dev > 0].mean().item())
        else:
            confidence = float(probs_spatial[0].mean().item())
        confidence = round(max(0.0, min(1.0, confidence)), 4)

        latency = (time.time() - start_time) * 1000.0
        return InferenceResult(
            task="segmentation",
            predictions={"mask_coverage_percent": round(coverage * 100.0, 3), "polygon_contours": polygons},
            confidence_score=confidence,
            visual_overlay=overlay,
            latency_ms=round(latency, 2),
            metadata=meta,
        )

    elif task_clean in ("anomaly", "anomaly_detection"):
        det_type = meta.get("detector_type", "")
        preset_str = str(meta.get("preset", "")).lower()
        if det_type == "patchcore" or "patchcore" in preset_str or "precision" in preset_str:
            model = PatchCoreDetector(backbone_name="resnet18", device=dev, pretrained=False)
        else:
            model = PaDiMDetector(backbone_name="resnet18", device=dev, pretrained=False)
        model.load_state_dict(state_dict)
        model.eval()

        with torch.no_grad():
            anom_map, score = model.predict_anomaly_map(img_t[0], out_size=(orig_h, orig_w))

        map_norm = np.clip(anom_map / max(1e-4, float(np.max(anom_map))), 0.0, 1.0)
        heat_u8 = np.uint8(255 * map_norm)
        heat_color = cv2.cvtColor(cv2.applyColorMap(heat_u8, cv2.COLORMAP_JET), cv2.COLOR_BGR2RGB)
        cv2.addWeighted(heat_color, 0.45, overlay, 0.55, 0, overlay)

        effective_threshold = threshold if threshold is not None else getattr(model, "threshold", 0.5)
        is_anomaly = score >= effective_threshold
        badge_text = f"{'NG (Anomaly)' if is_anomaly else 'OK (Pass)'} Score: {score:.2f}"
        badge_color = (230, 40, 40) if is_anomaly else (40, 210, 40)
        cv2.rectangle(overlay, (10, 10), (min(orig_w - 10, 280), 45), (30, 30, 30), -1)
        cv2.putText(overlay, badge_text, (16, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.65, badge_color, 2)

        latency = (time.time() - start_time) * 1000.0
        return InferenceResult(
            task="anomaly",
            predictions={"is_anomaly": is_anomaly, "anomaly_score": score, "threshold": effective_threshold},
            confidence_score=score,
            visual_overlay=overlay,
            latency_ms=round(latency, 2),
            metadata=meta,
        )

    else:
        raise ValueError(f"Unknown task: {task}")
