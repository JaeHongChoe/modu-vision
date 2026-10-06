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
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union

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
    checkpoint_detection_num_classes,
    foreground_class_names,
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
    reconstruct_anomaly_detector,
)
from backend.engine.anomaly.cancellation import AnomalyFitCancelled
from backend.engine.patch_classification import (
    PatchClassificationDataset,
    load_patch_manifest,
    predict_patch_classification,
)

logger = logging.getLogger("vision_ai_studio.trainer")

if TYPE_CHECKING:
    from backend.engine.warm_start import WarmStartParent


def _detection_validation_loss(model: nn.Module, images, targets) -> torch.Tensor:
    """Enable detector loss heads while keeping held-out inputs out of running statistics."""
    training_states = [(module, module.training) for module in model.modules()]
    try:
        model.train()
        for module in model.modules():
            if isinstance(module, (nn.modules.batchnorm._BatchNorm, nn.modules.dropout._DropoutNd)):
                module.eval()
        with torch.no_grad():
            return sum(model(images, targets).values())
    finally:
        for module, training in training_states:
            module.training = training


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
        backbone_classification="dinov3_vits16",
        backbone_detection="yolo26n",
        backbone_segmentation="dinov3_vits16",
        backbone_anomaly="padim_resnet18",
    ),
    "precision": AutoMLPresetConfig(
        preset_name="🎯 High Precision",
        target_epochs=30,
        image_size=512,
        batch_size=8,
        learning_rate=5e-4,
        patience=8,
        backbone_classification="dinov3_vits16",
        backbone_detection="yolo26n",
        backbone_segmentation="dinov3_vits16",
        backbone_anomaly="patchcore_resnet18",
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

def _build_detection_datasets(
    dataset_path: Path, train_transform: Optional[Callable], image_size: Tuple[int, int],
) -> Tuple[DetectionDataset, DetectionDataset]:
    """Build both COCO partitions with one foreground class index."""
    from backend.engine.grouped_dataset_views import load_manifest_dataset
    grouped_train = load_manifest_dataset("detection", dataset_path, "train", train_transform, image_size)
    if grouped_train is not None:
        grouped_val = load_manifest_dataset("detection", dataset_path, "val", image_size=image_size, class_names=list(grouped_train.categories.values()))
        return grouped_train, grouped_val
    train_img = (dataset_path / "images" / "train") if (dataset_path / "images" / "train").exists() else (dataset_path / "images")
    val_img = (dataset_path / "images" / "val") if (dataset_path / "images" / "val").exists() else train_img
    train_anno = (dataset_path / "annotations_train.json") if (dataset_path / "annotations_train.json").exists() else (dataset_path / "annotations.json")
    val_anno = (dataset_path / "annotations_val.json") if (dataset_path / "annotations_val.json").exists() else train_anno

    train_ds = DetectionDataset(images_dir=train_img, annotation_file=train_anno,
                                transform=train_transform, image_size=image_size)
    val_ds = DetectionDataset(images_dir=val_img, annotation_file=val_anno,
                              image_size=image_size, class_names=list(train_ds.categories.values()))
    return train_ds, val_ds


def _training_class_semantics(classes, task=None):
    from backend.engine.class_semantics import training_class_semantics
    return training_class_semantics(classes, task=task)


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
        warm_start: Optional[WarmStartParent] = None,
    ):
        self.task = task.lower().strip()
        self.dataset_path = Path(dataset_path)
        self.output_dir = Path(output_dir)
        if warm_start is not None:
            from backend.engine.specialized_warm_start import require_new_candidate
            require_new_candidate(self.output_dir, warm_start)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.device = get_device(device)
        self.callback = callback or TrainingCallback()
        from backend.engine.distributed_training import current_distributed_context,is_primary
        context=current_distributed_context()
        if context.world_size>1:
            if self.task not in ('classification','patch_classification','segmentation'):raise ValueError('Distributed training supports classification, patch classification and segmentation')
            self.device=torch.device(context.device)
            if not is_primary():self.callback=TrainingCallback()
        self._abort_flag = threading.Event()

        preset_key = preset.lower().strip()
        self.preset_key = preset_key
        self.config = PRESET_CONFIGS.get(preset_key, PRESET_CONFIGS["fast"])
        self.overrides = config_overrides or {}
        self.warm_start = warm_start
        from backend.engine.model_backbones import validate_training_controls
        validate_training_controls(self.task, self.preset_key, self.overrides)
        if self.overrides.get('resume_checkpoint'):
            if str(self.device)=='mps':raise ValueError('MPS exact resume is unsupported; use a model-only warm-start')
            if warm_start is not None:
                raise ValueError('Exact resume and warm-start cannot be selected together')
            if self.task in ('anomaly', 'anomaly_detection') or context.world_size > 1:
                raise ValueError('Exact resume currently supports supervised single-process epoch boundaries')

    def abort(self) -> None:
        """Signal trainer to immediately halt execution."""
        logger.info("Trainer abort requested.")
        self._abort_flag.set()

    def _cancel_requested(self)->bool:
        from backend.engine.distributed_training import synchronize_cancel
        return synchronize_cancel(self._abort_flag)

    def train(self, job_id: str = "job_default") -> Dict[str, Any]:
        """Executes full AutoML training workflow with telemetry streaming."""
        start_time = time.time()
        if 'seed' in self.overrides:
            import random
            seed=self.overrides['seed']
            if type(seed)is not int or not 0<=seed<=2147483647:raise ValueError('Training seed must be a bounded nonnegative integer')
            random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
        epochs = self.overrides.get("epochs", self.config.target_epochs)
        target_size = self.overrides.get("image_size", self.config.image_size)
        batch_size = int(self.overrides.get("batch_size", self.config.batch_size))
        learning_rate = float(self.overrides.get("learning_rate", self.config.learning_rate))
        patience = int(self.overrides.get("patience", self.config.patience))
        if batch_size < 1 or learning_rate <= 0 or patience < 1:
            raise ValueError("Training batch size, learning rate, and patience must be positive")
        if 'image_size' in self.overrides:
            # Dataset loaders use OpenCV/PIL (width, height) geometry. Explicit
            # controls must stay exact, including rectangular parent settings.
            dimensions=tuple(target_size) if isinstance(target_size,(list,tuple)) else (target_size,target_size)
            if len(dimensions)!=2 or any(type(value)is not int or not 32<=value<=4096 or value%16 for value in dimensions):
                raise ValueError('Training image_size must be a scalar or [width,height] of bounded multiples of 16')
            optimal_size=dimensions
        else:
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
            aug = create_industrial_transforms(task=self.task, preset=self.preset_key, is_training=True,
                                               profile=self.overrides.get('augmentation_profile', 'industrial'))

            if self.task == "classification":
                train_ds = ClassificationDataset(
                    root_dir=self.dataset_path, split="train", transform=aug, image_size=optimal_size
                )
                val_ds = ClassificationDataset(
                    root_dir=self.dataset_path, split="val", image_size=optimal_size
                )
                classes = train_ds.classes
                num_classes = max(2, len(classes))
                backbone = str(self.overrides.get('backbone', self.config.backbone_classification))
                model = create_classification_model(
                    backbone=backbone, num_classes=num_classes,
                    pretrained=self.warm_start is None and not self.overrides.get('resume_checkpoint') and bool(self.overrides.get('pretrained', True)),
                    pretrained_checkpoint=self.overrides.get('pretrained_checkpoint'),
                    pretrained_sha256=self.overrides.get('pretrained_sha256'),
                    train_mode=self.overrides.get('train_mode', 'head_only'),
                    partial_blocks=self.overrides.get('partial_blocks', 2),
                ).to(self.device)
                if not hasattr(train_ds, "class_counts"):
                    counts = [0] * num_classes
                    for _, cidx in getattr(train_ds, "samples", []):
                        if 0 <= cidx < num_classes:
                            counts[cidx] += 1
                    train_ds.class_counts = counts
                class_weights = compute_class_weights(train_ds.class_counts, num_classes=num_classes).to(self.device)
                criterion = create_classification_loss(weights=class_weights, label_smoothing=0.1)

            elif self.task == "patch_classification":
                patch_manifest = load_patch_manifest(self.dataset_path)
                self._patch_manifest = patch_manifest
                train_ds = PatchClassificationDataset(
                    self.dataset_path, split="train", image_size=optimal_size,
                    transform=aug, manifest=patch_manifest,
                )
                val_ds = PatchClassificationDataset(
                    self.dataset_path, split="val", image_size=optimal_size,
                    manifest=patch_manifest,
                )
                classes = patch_manifest.classes
                backbone = str(self.overrides.get("backbone", self.config.backbone_classification))
                self._patch_backbone = backbone
                model = create_classification_model(
                    backbone=backbone, num_classes=len(classes),
                    pretrained=self.warm_start is None and not self.overrides.get('resume_checkpoint') and bool(self.overrides.get("pretrained", True)),
                    pretrained_checkpoint=self.overrides.get('pretrained_checkpoint'),
                    pretrained_sha256=self.overrides.get('pretrained_sha256'),
                    train_mode=self.overrides.get('train_mode', 'head_only'),
                    partial_blocks=self.overrides.get('partial_blocks', 2),
                ).to(self.device)
                class_weights = compute_class_weights(train_ds.class_counts, num_classes=len(classes)).to(self.device)
                criterion = create_classification_loss(weights=class_weights, label_smoothing=0.1)

            elif self.task == "detection":
                train_ds, val_ds = _build_detection_datasets(self.dataset_path, aug, optimal_size)
                classes = list(train_ds.categories.values())
                num_classes = max(2, len(classes) + 1)
                model = create_detection_model(preset=self.preset_key, num_classes=num_classes,
                    backbone=str(self.overrides.get('backbone', self.config.backbone_detection)),
                    pretrained=self.warm_start is None and not self.overrides.get('resume_checkpoint') and bool(self.overrides.get('pretrained', True)),
                    pretrained_checkpoint=self.overrides.get('pretrained_checkpoint'),
                    pretrained_sha256=self.overrides.get('pretrained_sha256')).to(self.device)
                criterion = None

            elif self.task == "segmentation":
                from backend.engine.dataset_loaders import mask_folder_classes, mask_folder_layout
                train_img, train_mask, val_img, val_mask = mask_folder_layout(self.dataset_path)

                from backend.engine.grouped_dataset_views import load_manifest_dataset
                train_ds = load_manifest_dataset("segmentation", self.dataset_path, "train", aug, optimal_size)
                if train_ds is not None:
                    classes = train_ds.classes
                    val_ds = load_manifest_dataset("segmentation", self.dataset_path, "val", image_size=optimal_size, class_names=classes)
                else:
                    train_ds = SegmentationDataset(images_dir=train_img, masks_dir=train_mask, transform=aug, image_size=optimal_size)
                    val_ds = SegmentationDataset(images_dir=val_img, masks_dir=val_mask, image_size=optimal_size)
                    # The dataset's own class map or mask values, not a fixed binary pair: a multi-class mask folder
                    # (e.g. the synthetic generator's) otherwise fails at its first class id above 1. Warm start
                    # checks a parent against the same list (dataset_loaders.segmentation_folder_classes).
                    classes = mask_folder_classes(self.dataset_path, [mask for _, mask in train_ds.samples + val_ds.samples])
                model = build_segmentation_model(num_classes=len(classes), preset=self.preset_key,
                    model_name=str(self.overrides.get('model_name', self.config.backbone_segmentation)),
                    pretrained=self.warm_start is None and not self.overrides.get('resume_checkpoint') and bool(self.overrides.get('pretrained', True)),
                    pretrained_checkpoint=self.overrides.get('pretrained_checkpoint'),
                    pretrained_sha256=self.overrides.get('pretrained_sha256'),
                    train_mode=self.overrides.get('train_mode', 'head_only'),
                    partial_blocks=self.overrides.get('partial_blocks', 2)).to(self.device)
                criterion = ComboLoss(num_classes=len(classes), dice_weight=1.0)

            elif self.task in ("anomaly", "anomaly_detection"):
                from backend.engine.grouped_dataset_views import load_manifest_dataset
                train_ds = load_manifest_dataset("anomaly", self.dataset_path, "train", aug, optimal_size)
                val_ds = load_manifest_dataset("anomaly", self.dataset_path, "val", image_size=optimal_size)
                if train_ds is None:
                    train_ds = AnomalyDataset(root_dir=self.dataset_path, split="train", transform=aug, image_size=optimal_size)
                    val_ds = AnomalyDataset(root_dir=self.dataset_path, split="val", image_size=optimal_size)
                classes = ["good", "anomaly"]
                method = self.overrides.get('anomaly_method', 'patchcore' if 'patchcore' in self.config.backbone_anomaly else 'padim')
                if method not in ('padim', 'patchcore', 'dino_synthetic'):
                    raise ValueError('Unsupported anomaly method')
                self._anomaly_method = method
                if method == 'dino_synthetic':
                    from backend.engine.anomaly.dino_synthetic import DinoSyntheticDetector
                    model = DinoSyntheticDetector(
                        backbone_name=self.overrides.get('anomaly_backbone', 'dinov3_vits16'),
                        device=self.device, pretrained=self.warm_start is None and bool(self.overrides.get('pretrained', True)),
                        pretrained_checkpoint=self.overrides.get('pretrained_checkpoint'),
                        pretrained_sha256=self.overrides.get('pretrained_sha256'),
                        patch_size=int(self.overrides.get('patch_size', 256)),
                        stride=int(self.overrides.get('stride', 128)), epochs=int(epochs),
                        batch_size=batch_size, learning_rate=learning_rate,
                        patches_per_image=int(self.overrides.get('patches_per_image', 8)),
                        inference_batch_size=int(self.overrides.get('inference_batch_size', 32)),
                    )
                    # The fitter reads native sources; real validation must also retain
                    # their scale rather than applying the generic anomaly resize.
                    train_ds.transform = None
                    train_ds.image_size = None
                    train_ds.max_dim = 0
                    val_ds.image_size = None
                    val_ds.max_dim = 0
                elif method == 'patchcore':
                    model = PatchCoreDetector(backbone_name="resnet18", device=self.device,
                        pretrained=self.warm_start is None and bool(self.overrides.get('pretrained', True)),
                        pretrained_checkpoint=self.overrides.get('pretrained_checkpoint'),
                        pretrained_sha256=self.overrides.get('pretrained_sha256'), seed=self.overrides.get('seed', 42))
                else:
                    model = PaDiMDetector(backbone_name="resnet18", device=self.device,
                        pretrained=self.warm_start is None and bool(self.overrides.get('pretrained', True)),
                        pretrained_checkpoint=self.overrides.get('pretrained_checkpoint'),
                        pretrained_sha256=self.overrides.get('pretrained_sha256'), seed=self.overrides.get('seed', 42))

            if self.warm_start is not None:
                from backend.engine.warm_start import architecture_for, load_parent_weights

                if self.task != self.warm_start.task:
                    raise ValueError("Warm-start parent task differs from current training task")
                architecture = architecture_for(self.task, self.preset_key, self.overrides)
                if architecture != self.warm_start.architecture:
                    raise ValueError("Warm-start parent architecture differs from current training model")
                load_parent_weights(model, self.warm_start, classes)

            # Requested class roles are checked before any epoch runs and frozen with the model.
            from backend.engine.class_semantics import training_class_semantics
            self._class_semantics = training_class_semantics(classes, self.overrides.get('class_roles'), task=self.task)
            if self.task not in ('anomaly', 'anomaly_detection') and (len(train_ds) == 0 or len(val_ds) == 0):
                raise ValueError('Supervised training requires nonempty train and validation splits')
            from backend.engine.distributed_training import current_distributed_context,distributed_loader,wrap_model,set_sampler_epoch,distributed_mean
            distributed=current_distributed_context().world_size>1
            if distributed:
                train_loader=distributed_loader(train_ds,batch_size=batch_size,shuffle=True,num_workers=0)
                val_loader=distributed_loader(val_ds,batch_size=batch_size,shuffle=False,partition=False,num_workers=0)
                model=wrap_model(model)
            else:
                train_loader = create_dataloader(train_ds, batch_size=batch_size, shuffle=True, task=self.task)
                val_loader = create_dataloader(val_ds, batch_size=batch_size, shuffle=False, task=self.task)

            # 2. Task 4 Anomaly Workflow (OK-Only Embedding Fit)
            if self.task in ("anomaly", "anomaly_detection"):
                if self._abort_flag.is_set():
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(0, "Training aborted by user request")
                    return {"status": "aborted", "epoch": 0}

                try:
                    import copy
                    calibration_ds = copy.copy(val_ds)
                    calibration_ds.samples = [sample for sample in val_ds.samples if int(sample[1]) == 0]
                    if self._anomaly_method == 'dino_synthetic':
                        from backend.engine.anomaly.normal_calibration import prepare_normal_calibration, verify_normal_snapshot
                        calibration_snapshot = prepare_normal_calibration(train_ds, calibration_ds, self._abort_flag.is_set)
                        self._last_anomaly_epoch = 0
                        def anomaly_progress(epoch, total_epochs, train_loss, val_loss, lr, metrics):
                            self._last_anomaly_epoch = epoch
                            self.callback.on_epoch_end(epoch - 1, total_epochs, train_loss, val_loss, lr, metrics)
                        model.fit(train_loader, cancellation_requested=self._abort_flag.is_set,
                            progress_callback=anomaly_progress, calibration_dataset=calibration_ds)
                        verify_normal_snapshot(calibration_snapshot, self._abort_flag.is_set)
                        model.calibration.update(calibration_snapshot['public'])
                        model.calibration.update({'quality_approved': False, 'population_fpr_verified': False})
                        model.training_summary['calibration'] = dict(model.calibration)
                    else:
                        model.fit(train_loader, cancellation_requested=self._abort_flag.is_set, calibration_dataset=calibration_ds)
                except AnomalyFitCancelled:
                    clear_device_cache(self.device)
                    stopped_epoch = getattr(self, '_last_anomaly_epoch', 0)
                    self.callback.on_training_aborted(stopped_epoch, "Training aborted by user request")
                    return {"status": "aborted", "epoch": stopped_epoch}

                if self._abort_flag.is_set():
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(0, "Training aborted by user request")
                    return {"status": "aborted", "epoch": 0}

                from backend.engine.anomaly.evaluation import evaluate_anomaly_dataset
                try:
                    anom_metrics = evaluate_anomaly_dataset(model, val_ds, device=self.device, cancellation_requested=self._abort_flag.is_set)
                except AnomalyFitCancelled:
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(0, "Training aborted by user request")
                    return {"status": "aborted", "epoch": 0}
                self._anomaly_validation_metrics = anom_metrics
                auroc = anom_metrics.get("image_auroc")
                val_metric = round(1.0 - float(auroc), 4) if auroc is not None else 0.0
                if self._anomaly_method == 'dino_synthetic' and auroc is None:
                    val_metric = float(model.training_summary['epoch_history'][-1]['train_loss'])
                final_epoch = getattr(self, '_last_anomaly_epoch', 0)
                if self._anomaly_method != 'dino_synthetic':
                    self.callback.on_epoch_end(
                        epoch=0,
                        total_epochs=1,
                        train_loss=0.0,
                        val_loss=val_metric,
                        lr=0.0,
                        metrics=anom_metrics,
                    )
                if self._abort_flag.is_set():
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(0, "Training aborted by user request")
                    return {"status": "aborted", "epoch": 0}
                saved_size = (model.patch_size, model.patch_size) if self._anomaly_method == 'dino_synthetic' else optimal_size
                self._save_checkpoint(final_epoch, model, val_metric, classes, saved_size, time.time() - start_time)
                if self._abort_flag.is_set():
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(0, "Training aborted by user request")
                    return {"status": "aborted", "epoch": 0}
                elapsed = time.time() - start_time
                best_model_path = str(self.output_dir / "best_model.pt")
                self.callback.on_training_completed(job_id, elapsed, val_metric, best_model_path)
                return {"status": "completed", "best_metric": val_metric, "model_path": best_model_path}

            # 3. Supervised Tasks Optimization Loop
            optimizer, scheduler = create_optimizer_and_scheduler(
                model=model,
                lr=learning_rate,
                weight_decay=float(self.overrides.get('weight_decay', 1e-4)),
                total_epochs=epochs,
                warmup_epochs=min(3, max(1, epochs // 4)),
            )
            early_stopping = EarlyStopping(patience=patience, mode="min")
            amp_enabled = bool(self.overrides.get('use_amp', False))
            if amp_enabled and self.device.type != 'cuda':
                raise ValueError('AMP training requires a CUDA device')
            scaler = torch.amp.GradScaler('cuda', enabled=amp_enabled)
            from backend.engine.training_resume import build_identity, restore_training_state, save_training_state, resume_lineage
            resume_identity = build_identity(task=self.task, preset=self.preset_key,
                recipe=self._training_config(), dataset_path=self.dataset_path, classes=classes,
                model=model, device=self.device)
            total_steps = epochs * len(train_loader)
            global_step = 0; first_epoch = 0
            if self.overrides.get('resume_checkpoint'):
                state = restore_training_state(self.overrides['resume_checkpoint'], model, optimizer, scheduler, scaler,
                    identity=resume_identity, early_stopping=early_stopping)
                first_epoch, global_step = state['next_epoch'], state['global_step']
                if first_epoch >= epochs or early_stopping.early_stop:
                    raise ValueError('Exact resume has no remaining epochs in the original recipe')
                self._resume_lineage = resume_lineage(self.overrides['resume_checkpoint'], state)
                if state['best_model_payload'] is not None:
                    best = state['best_model_payload']; best['resume'] = self._resume_lineage
                    torch.save(best, self.output_dir / 'best_model.pt')
                    metadata = {key: value for key, value in best.items() if key != 'model_state_dict'}
                    (self.output_dir / 'model_meta.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')

            for epoch in range(first_epoch, epochs):
                set_sampler_epoch(train_loader,epoch)
                if self._cancel_requested():
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(epoch, "Training aborted by user request")
                    return {"status": "aborted", "epoch": epoch}

                model.train()
                train_losses = []

                for step, batch in enumerate(train_loader):
                    if self._cancel_requested():
                        clear_device_cache(self.device)
                        self.callback.on_training_aborted(epoch, "Training aborted by user request")
                        return {"status": "aborted", "epoch": epoch}

                    optimizer.zero_grad()
                    with torch.autocast(device_type=self.device.type, enabled=amp_enabled):
                        if self.task in ("classification", "patch_classification"):
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

                    scaler.scale(loss).backward()
                    scaler.step(optimizer)
                    scaler.update()

                    loss_val = float(loss.item())
                    train_losses.append(loss_val)
                    self.callback.on_step_end(global_step, total_steps, loss_val, epoch)
                    global_step += 1

                if self._cancel_requested():
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(epoch, "Training aborted by user request")
                    return {"status": "aborted", "epoch": epoch}

                # Validation Evaluation
                model.eval()
                val_losses = []
                with torch.no_grad():
                    for batch in val_loader:
                        if self._cancel_requested():
                            clear_device_cache(self.device)
                            self.callback.on_training_aborted(epoch, "Training aborted by user request")
                            return {"status": "aborted", "epoch": epoch}
                        if self.task in ("classification", "patch_classification"):
                            imgs, targets = batch
                            imgs, targets = imgs.to(self.device), targets.to(self.device)
                            loss = criterion(model(imgs), targets)
                        elif self.task == "detection":
                            imgs, targets = batch
                            imgs = [img.to(self.device) for img in imgs]
                            targets_dev = [{k: v.to(self.device) for k, v in t.items()} for t in targets]
                            loss = _detection_validation_loss(model, imgs, targets_dev)
                        elif self.task == "segmentation":
                            imgs, masks = batch
                            imgs, masks = imgs.to(self.device), masks.to(self.device)
                            if isinstance(criterion, ComboLoss):
                                loss, _ = criterion(model(imgs), masks)
                            else:
                                loss = criterion(model(imgs), masks)
                        val_losses.append(float(loss.item()))

                if self._cancel_requested():
                    clear_device_cache(self.device)
                    self.callback.on_training_aborted(epoch, "Training aborted by user request")
                    return {"status": "aborted", "epoch": epoch}

                mean_train_loss = float(np.mean(train_losses)) if train_losses else 0.0
                mean_val_loss = float(np.mean(val_losses)) if val_losses else 0.0
                mean_train_loss=distributed_mean(mean_train_loss,weight=len(train_losses))
                mean_val_loss=distributed_mean(mean_val_loss,weight=len(val_losses))
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
                if not distributed:
                    save_training_state(self.output_dir / 'latest_training_state.pt', model, optimizer, scheduler, scaler,
                        identity=resume_identity, next_epoch=epoch + 1, global_step=global_step,
                        early_stopping=early_stopping, best_model_path=self.output_dir / 'best_model.pt')
                if early_stopping.early_stop:
                    logger.info("Early stopping triggered at epoch %d", epoch)
                    break

            elapsed = time.time() - start_time
            if self._cancel_requested():
                clear_device_cache(self.device)
                self.callback.on_training_aborted(epoch, "Training aborted by user request")
                return {"status": "aborted", "epoch": epoch}
            best_model_path = str(self.output_dir / "best_model.pt")
            if not (self.output_dir / "best_model.pt").exists():
                fallback_loss = mean_val_loss if (val_losses and not np.isnan(mean_val_loss)) else 0.0
                self._save_checkpoint(epochs - 1, model, fallback_loss, classes, optimal_size, elapsed)
            if self._cancel_requested():
                clear_device_cache(self.device)
                self.callback.on_training_aborted(epoch, "Training aborted by user request")
                return {"status": "aborted", "epoch": epoch}
            self.callback.on_training_completed(job_id, elapsed, early_stopping.best_score, best_model_path)
            return {"status": "completed", "best_metric": early_stopping.best_score, "model_path": best_model_path}

        except Exception as e:
            logger.exception("Error during training execution: %s", str(e))
            self.callback.on_error(e, "training_loop")
            raise

    def _training_config(self):
        return {'epochs': self.overrides.get('epochs', self.config.target_epochs),
                'batch_size': self.overrides.get('batch_size', self.config.batch_size),
                'learning_rate': self.overrides.get('learning_rate', self.config.learning_rate),
                'weight_decay': self.overrides.get('weight_decay', 1e-4),
                'image_size': self.overrides.get('image_size', self.config.image_size),
                'patience': self.overrides.get('patience', self.config.patience),
                'augmentation_profile': self.overrides.get('augmentation_profile', 'industrial'),
                'train_mode': self.overrides.get('train_mode', 'head_only'),
                'partial_blocks': self.overrides.get('partial_blocks', 2),
                'use_amp': self.overrides.get('use_amp', False),
                **{key:value for key,value in self.overrides.items() if key!='resume_checkpoint'}}

    def _save_checkpoint(
        self, epoch: int, model: Any, metric: float, classes: List[str], img_size: Tuple[int, int], elapsed: float
    ) -> None:
        """Atomically saves best_model.pt and generates model_meta.json."""
        from backend.engine.distributed_training import is_primary,unwrap_model,current_distributed_context
        if not is_primary():return
        model=unwrap_model(model)
        tmp_pt = self.output_dir / "best_model.pt.tmp"

        meta = {
            "task": self.task,
            "preset": self.preset_key,
            "preset_name": self.config.preset_name,
            "best_epoch": epoch,
            "best_metric": round(metric, 5),
            "classes": classes,
            "class_semantics": getattr(self, "_class_semantics", None) or _training_class_semantics(classes, self.task),
            "image_size": list(img_size),
            "device": str(self.device),
            "training_duration_seconds": round(elapsed, 2),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "distributed_world_size":current_distributed_context().world_size,
            "training_config": self._training_config(),
        }
        if self.task in {"detection", "segmentation"}:
            # New candidates identify the loader contract, allowing historical
            # impact checks to distinguish image-only augmentation results.
            meta["augmentation_contract"] = {"version": 1, "target_sync": True,
                                             "crop": None, "mask_interpolation": "nearest"}

        if self.task == "classification":
            meta["backbone"] = str(self.overrides.get('backbone', self.config.backbone_classification))
        elif self.task == "patch_classification":
            patch_manifest = self._patch_manifest
            meta.update({
                "backbone": self._patch_backbone,
                "normal_class": patch_manifest.normal_class,
                "patch_size": patch_manifest.patch_size,
                "stride": patch_manifest.stride,
                "patch_provenance": patch_manifest.provenance,
            })
        elif self.task == "detection":
            meta["detector_preset"] = self.preset_key
            # Detection early stopping selects minimum validation loss. Heldout
            # mAP is produced later by evaluation and is not this selection key.
            meta["checkpoint_selection"] = {
                "metric": "val_loss", "direction": "min", "split": "val", "epoch": epoch,
            }
            selected = str(self.overrides.get('backbone', self.config.backbone_detection))
            if selected != 'fasterrcnn':
                meta['backbone'] = selected
        elif self.task == "segmentation":
            meta["preset"] = self.preset_key
            meta["features"] = [64, 128, 256, 512] if self.preset_key == "precision" else [32, 64, 128, 256]
            meta['model_name'] = str(self.overrides.get('model_name', self.config.backbone_segmentation))
        elif self.task in ("anomaly", "anomaly_detection"):
            meta["detector_type"] = getattr(self, '_anomaly_method', 'patchcore' if 'patchcore' in self.config.backbone_anomaly else 'padim')
            meta['feature_backbone'] = model.backbone_name
            meta['anomaly_mode'] = self.overrides.get('anomaly_mode', 'classification')
            meta['evaluation_profile'] = 'region_mask_metrics' if meta['anomaly_mode'] == 'segmentation' else 'image_score_metrics'
            meta["validation"] = getattr(self,"_anomaly_validation_metrics",{})
            from backend.engine.score_contract import resolve_model_score
            meta['score_spec'], _ = resolve_model_score(model, None, model.threshold)
            meta['anomaly_threshold'] = model.threshold
            meta['threshold_settings'] = {'threshold': model.threshold}
            meta['calibration'] = getattr(model, 'calibration', None)
            meta['map_semantics'] = 'pixel_score'
            meta['quality_approved'] = False
            if meta['detector_type'] == 'dino_synthetic':
                meta.update({
                    'anomaly_backbone': model.backbone_name,
                    'patch_size': model.patch_size, 'stride': model.stride,
                    'head_version': 1, 'map_semantics': 'patch_score',
                    'inference_batch_size': model.inference_batch_size,
                    'anomaly_threshold': model.threshold,
                    'calibration': model.calibration,
                    'training_summary': model.training_summary,
                    'best_metric_basis': 'real_validation_auroc_loss' if meta['validation'].get('image_auroc') is not None else 'synthetic_training_loss',
                    'quality_approved': False,
                })

        if self.warm_start is not None:
            meta["warm_start"] = self.warm_start.lineage()
        if hasattr(self, '_resume_lineage'):
            meta['resume'] = self._resume_lineage

        from backend.engine.model_backbones import model_metadata
        meta.update(model_metadata(model))
        if self.warm_start is None and meta.get('pretrained') and self.overrides.get('pretrained_origin'):
            meta['pretrained_source'] = self.overrides['pretrained_origin']
        from backend.engine.warm_start import architecture_for
        meta['architecture'] = architecture_for(self.task, self.preset_key, self.overrides)

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
    threshold: Optional[float] = None,
    device: Optional[Union[str, torch.device]] = None,
    score_spec: Optional[Dict[str, Any]] = None,
) -> InferenceResult:
    """
    Unified industrial inference engine supporting all 4 vision tasks.
    Returns structured predictions, confidence score, and visual overlays.
    """
    start_time = time.time()
    dev = get_device(device)
    m_path = Path(model_path)
    from backend.engine.score_contract import resolve_inference_score
    threshold, resolved_score_spec = resolve_inference_score(m_path, task.lower().strip(), threshold, score_spec)
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

    if task.lower().strip() == "patch_classification":
        patch_result = predict_patch_classification(
            m_path, image_input, threshold=threshold, device=dev,
        )
        overlay = img_np.copy()
        for patch in patch_result["patches"]:
            x1, y1, x2, y2 = patch["box"]
            color = (220, 40, 40) if patch["decision"] == "FAIL" else (40, 200, 40)
            cv2.rectangle(overlay, (x1, y1), (x2 - 1, y2 - 1), color, 2)
        return InferenceResult(
            task="patch_classification", predictions=patch_result,
            confidence_score=patch_result["max_defect_score"],
            visual_overlay=overlay,
            latency_ms=round((time.time() - start_time) * 1000.0, 2),
            metadata=meta,
        )

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
        model = create_detection_model(preset=det_preset,
                                       num_classes=checkpoint_detection_num_classes(state_dict, classes),
                                       backbone=meta.get('backbone'),
                                       pretrained=False).to(dev)
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
                defect_classes = foreground_class_names(classes)
                lbl = defect_classes[l - 1] if 1 <= l <= len(defect_classes) else f"defect_{l}"
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
        model = build_segmentation_model(num_classes=len(classes), preset=seg_preset,
                                        model_name=meta.get('model_name', 'unet'), pretrained=False).to(dev)
        model.load_state_dict(state_dict)
        model.eval()

        with torch.no_grad():
            logits = model(img_t)
            pred_mask = torch.argmax(logits, dim=1)[0].cpu().numpy().astype(np.uint8)

        mask_orig = cv2.resize(pred_mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
        color_mask = np.zeros_like(overlay)
        color_mask[mask_orig > 0] = [255, 40, 40]
        cv2.addWeighted(color_mask, 0.4, overlay, 0.6, 0, overlay)

        polygons = extract_polygons(mask_orig, class_names={index:name for index,name in enumerate(classes)})

        coverage = float(np.count_nonzero(mask_orig)) / float(orig_w * orig_h)

        probs = torch.softmax(logits, dim=1)[0]
        mask_dev = torch.as_tensor(mask_orig, device=dev)
        if mask_dev.shape != probs.shape[1:]:
            probs_spatial = F.interpolate(probs.unsqueeze(0), size=(orig_h, orig_w), mode="bilinear", align_corners=False)[0]
        else:
            probs_spatial = probs

        predicted_probabilities = probs_spatial.gather(0, mask_dev.long().unsqueeze(0))[0]
        confidence = float(predicted_probabilities.mean().item())
        import base64, io
        mask_candidates = []
        for class_id in np.unique(mask_orig):
            if class_id == 0:
                continue
            selected = mask_orig == class_id
            rgba = np.zeros((orig_h, orig_w, 4), dtype=np.uint8)
            rgba[..., :3] = [34, 211, 238]
            rgba[..., 3] = selected.astype(np.uint8) * 255
            stream = io.BytesIO()
            Image.fromarray(rgba).save(stream, format='PNG')
            class_confidence = float(probs_spatial[int(class_id)][mask_dev == int(class_id)].mean().item())
            mask_candidates.append({'class_id':int(class_id), 'class_name':classes[int(class_id)],
                                    'confidence':class_confidence,
                                    'mask_rle':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode('ascii')})

        confidence = round(max(0.0, min(1.0, confidence)), 4)

        latency = (time.time() - start_time) * 1000.0
        return InferenceResult(
            task="segmentation",
            predictions={"mask_coverage_percent": round(coverage * 100.0, 3), "polygon_contours": polygons, "mask_candidates": mask_candidates},
            confidence_score=confidence,
            visual_overlay=overlay,
            latency_ms=round(latency, 2),
            metadata=meta,
        )

    elif task_clean in ("anomaly", "anomaly_detection"):
        # The file may have changed after threshold resolution. Authorize the
        # exact tensors loaded for this prediction, never cached path metadata.
        from backend.engine.score_contract import state_score_spec, compatible_scores
        actual_score_spec = state_score_spec(state_dict)
        if not compatible_scores(actual_score_spec, resolved_score_spec):
            raise ValueError('Score calibration differs from the loaded model state')
        model = reconstruct_anomaly_detector(state_dict, meta, dev)
        patch_scores = getattr(model, 'model_metadata', {}).get('map_semantics') == 'patch_score'
        if patch_scores:
            img_t = torch.from_numpy(np.ascontiguousarray(img_np.transpose(2, 0, 1))).float().unsqueeze(0) / 255.0

        with torch.no_grad():
            anom_map, score = model.predict_anomaly_map(img_t[0], out_size=(orig_h, orig_w))

        map_norm = np.clip(anom_map / max(1e-4, float(np.max(anom_map))), 0.0, 1.0)
        heat_u8 = np.uint8(255 * map_norm)
        heat_color = cv2.cvtColor(cv2.applyColorMap(heat_u8, cv2.COLORMAP_JET), cv2.COLOR_BGR2RGB)
        cv2.addWeighted(heat_color, 0.45, overlay, 0.55, 0, overlay)

        effective_threshold = threshold if threshold is not None else getattr(model, "threshold", 0.5)
        is_anomaly = score > effective_threshold if patch_scores else score >= effective_threshold
        badge_text = f"{'NG (Anomaly)' if is_anomaly else 'OK (Pass)'} Score: {score:.2f}"
        badge_color = (230, 40, 40) if is_anomaly else (40, 210, 40)
        cv2.rectangle(overlay, (10, 10), (min(orig_w - 10, 280), 45), (30, 30, 30), -1)
        cv2.putText(overlay, badge_text, (16, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.65, badge_color, 2)

        latency = (time.time() - start_time) * 1000.0
        predictions = {"is_anomaly": is_anomaly, "anomaly_score": score, "threshold": effective_threshold,
                       "score_spec": resolved_score_spec, "score_basis": "saved_model_calibration" if score_spec is None else "explicit_score_spec",
                       "map_semantics": 'patch_score' if patch_scores else 'pixel_score'}
        if patch_scores:
            import base64
            import zlib
            values = np.asarray(anom_map, dtype='<f4', order='C')
            predictions['anomaly_values'] = {
                'dtype': 'float32', 'encoding': 'zlib_base64', 'shape': list(values.shape),
                'data': base64.b64encode(zlib.compress(values.tobytes())).decode('ascii'),
            }
        else:
            predictions.update({"anomaly_map": anom_map.tolist(),
                                "mask": (anom_map >= effective_threshold).astype(np.uint8).tolist()})
        return InferenceResult(
            task="anomaly",
            predictions=predictions,
            confidence_score=score,
            visual_overlay=overlay,
            latency_ms=round(latency, 2),
            metadata=meta,
        )

    else:
        raise ValueError(f"Unknown task: {task}")
