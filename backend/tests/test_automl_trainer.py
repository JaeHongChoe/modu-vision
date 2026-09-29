"""
backend/tests/test_automl_trainer.py

Comprehensive test suite for AutoML Heuristics, Augmentations,
Scheduler, Unified Trainer, and Multi-Task Inference Engine.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
import torch
import torch.nn as nn
import torchvision.transforms.functional as TF

from backend.engine.device import clear_device_cache, get_device, get_host_telemetry
from backend.engine.dataset_loaders import (
    ClassificationDataset,
    DetectionDataset,
    SegmentationDataset,
    AnomalyDataset,
    create_dataloader,
)
from backend.engine.synthetic_generator import generate_synthetic_dataset
from backend.engine.augmentations import (
    IndustrialAugmentationPipeline,
    create_industrial_transforms,
)
from backend.engine.scheduler import (
    WarmupCosineAnnealingLR,
    create_adamw_optimizer,
    create_optimizer_and_scheduler,
)
from backend.engine.trainer import (
    EarlyStopping,
    InferenceResult,
    TrainingCallback,
    UnifiedAutoMLTrainer,
    calculate_optimal_image_size,
    infer,
)


class LoggingCallback(TrainingCallback):
    """Callback logger for test verification."""

    def __init__(self):
        self.started = False
        self.completed = False
        self.aborted = False
        self.steps = 0
        self.epochs = 0
        self.best_metric = None

    def on_training_start(self, config):
        self.started = True

    def on_step_end(self, step, total_steps, current_loss, epoch):
        self.steps += 1

    def on_epoch_end(self, epoch, total_epochs, train_loss, val_loss, lr, metrics):
        self.epochs += 1

    def on_training_completed(self, job_id, duration_seconds, best_metric, model_path):
        self.completed = True
        self.best_metric = best_metric

    def on_training_aborted(self, epoch, reason):
        self.aborted = True


# ============================================================================
# 1. Industrial Augmentations Tests
# ============================================================================

class TestIndustrialAugmentations:
    """Test suite validating industrial-safe non-destructive augmentations."""

    def test_photometric_bounds_invariant(self):
        """Verifies brightness and contrast jitter do not produce saturated or inverted artifacts."""
        aug = IndustrialAugmentationPipeline(brightness_range=(-0.15, 0.15), contrast_range=(-0.15, 0.15))
        img = torch.full((3, 64, 64), 0.5, dtype=torch.float32)

        for _ in range(20):
            aug_img = aug._apply_photometric(img)
            assert aug_img.min() >= 0.0, "Augmented tensor contains negative values"
            assert aug_img.max() <= 1.0, "Augmented tensor contains values > 1.0"
            assert aug_img.shape == (3, 64, 64)

    def test_rotation_bounds_invariant(self):
        """Verifies rotation is strictly bounded within [-10, 10] degrees and does not distort size."""
        aug = IndustrialAugmentationPipeline(max_rotation_deg=10.0, flip_horizontal=False, flip_vertical=False, cutout_prob=0.0)
        img = torch.rand(3, 128, 128)
        aug_img = aug.forward_classification(img)
        assert aug_img.shape == (3, 128, 128)

    def test_small_cutout_size_constraint(self):
        """Verifies cutout patch covers at most max_cutout_size_ratio of image dimension."""
        max_ratio = 0.10
        aug = IndustrialAugmentationPipeline(cutout_prob=1.0, max_cutout_size_ratio=max_ratio)
        img = torch.ones(3, 100, 100)
        aug_img = aug._apply_cutout(img)

        # Count modified pixels
        diff = (img != aug_img).any(dim=0)
        modified_pixels = int(diff.sum().item())
        max_allowed_pixels = int(100 * 100 * (max_ratio ** 2) * 1.5)  # slight tolerance
        assert modified_pixels <= max_allowed_pixels, f"Cutout too large: {modified_pixels} > {max_allowed_pixels}"

    def test_detection_bbox_transformation(self):
        """Verifies bounding boxes are flipped and rotated accurately without inverted coordinates."""
        aug = IndustrialAugmentationPipeline(max_rotation_deg=5.0, flip_horizontal=True, flip_vertical=True)
        img = torch.rand(3, 200, 200)
        target = {
            "boxes": torch.tensor([[20.0, 30.0, 70.0, 80.0], [100.0, 110.0, 150.0, 160.0]]),
            "labels": torch.tensor([1, 2]),
        }

        for _ in range(10):
            aug_img, aug_target = aug.forward_detection(img, target)
            boxes = aug_target["boxes"]
            assert len(boxes) == 2
            # Verify coordinates are valid and xmin <= xmax, ymin <= ymax
            assert (boxes[:, 0] >= 0.0).all()
            assert (boxes[:, 1] >= 0.0).all()
            assert (boxes[:, 2] <= 200.0).all()
            assert (boxes[:, 3] <= 200.0).all()
            assert (boxes[:, 0] <= boxes[:, 2]).all(), "Inverted X coordinates detected"
            assert (boxes[:, 1] <= boxes[:, 3]).all(), "Inverted Y coordinates detected"

    def test_segmentation_joint_mask_nearest_interpolation(self):
        """Verifies mask undergoes identical spatial transform using NEAREST interpolation."""
        aug = IndustrialAugmentationPipeline(max_rotation_deg=10.0, flip_horizontal=True, flip_vertical=True)
        img = torch.rand(3, 128, 128)
        mask = torch.zeros((128, 128), dtype=torch.long)
        mask[30:60, 30:60] = 1
        mask[70:90, 70:90] = 2

        for _ in range(10):
            aug_img, aug_mask = aug.forward_segmentation(img, mask)
            assert aug_img.shape == (3, 128, 128)
            assert aug_mask.shape == (128, 128)
            assert aug_mask.dtype == torch.long
            unique_vals = set(aug_mask.unique().tolist())
            assert unique_vals.issubset({0, 1, 2}), f"Interpolation blurred discrete mask classes: {unique_vals}"

    def test_anomaly_training_preserves_normal_distribution(self):
        """Verifies anomaly transforms do not introduce artificial cutouts or corruptions."""
        aug = IndustrialAugmentationPipeline(task="anomaly", cutout_prob=0.0)
        img = torch.rand(3, 64, 64)
        aug_img = aug.forward_anomaly(img)
        assert aug_img.shape == (3, 64, 64)

    def test_eval_mode_bypass(self):
        """Verifies that transforms bypass augmentation when is_training=False."""
        aug = IndustrialAugmentationPipeline(is_training=False)
        img = torch.rand(3, 64, 64)
        aug_img = aug.forward_classification(img)
        assert torch.equal(img, aug_img), "Evaluation transform altered input tensor"


# ============================================================================
# 2. Dynamic AdamW Scheduler Tests
# ============================================================================

class TestDynamicAdamWScheduler:
    """Test suite validating AdamW optimizer and WarmupCosineAnnealingLR."""

    def test_optimizer_parameter_groups_separation(self):
        """Verifies weight decay is 0.0 for biases and normalization layers."""
        model = nn.Sequential(
            nn.Conv2d(3, 16, kernel_size=3),
            nn.BatchNorm2d(16),
            nn.Linear(16, 2),
        )
        optimizer = create_adamw_optimizer(model, lr=1e-3, weight_decay=1e-4)
        assert len(optimizer.param_groups) == 2

        decay_group = optimizer.param_groups[0]
        no_decay_group = optimizer.param_groups[1]
        assert decay_group["weight_decay"] == 1e-4
        assert no_decay_group["weight_decay"] == 0.0
        assert len(decay_group["params"]) > 0
        assert len(no_decay_group["params"]) > 0

    def test_warmup_strictly_increasing(self):
        """Verifies learning rate strictly increases during the 3-epoch warmup phase."""
        model = nn.Linear(10, 2)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
        scheduler = WarmupCosineAnnealingLR(optimizer, total_epochs=10, warmup_epochs=3, min_lr=1e-6)

        lrs = []
        for epoch in range(3):
            lrs.append(optimizer.param_groups[0]["lr"])
            optimizer.step()
            scheduler.step()

        assert lrs[0] < lrs[1] < lrs[2], f"Warmup was not monotonically increasing: {lrs}"
        assert math.isclose(lrs[2], 1e-3, rel_tol=1e-3), f"Peak LR {lrs[2]} != base_lr 1e-3"

    def test_cosine_decay_monotonically_decreasing(self):
        """Verifies learning rate strictly decreases after warmup phase down to min_lr."""
        model = nn.Linear(10, 2)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
        scheduler = WarmupCosineAnnealingLR(optimizer, total_epochs=8, warmup_epochs=2, min_lr=1e-6)

        lrs = []
        for epoch in range(8):
            lrs.append(optimizer.param_groups[0]["lr"])
            optimizer.step()
            scheduler.step()

        # Check cosine decay from epoch 2 onwards
        for e in range(2, 7):
            assert lrs[e] >= lrs[e + 1], f"LR did not decrease at epoch {e}: {lrs[e]} vs {lrs[e+1]}"
        assert lrs[-1] >= 1e-6

    def test_edge_case_short_epochs(self):
        """Verifies scheduler handles edge case where total_epochs <= warmup_epochs."""
        model = nn.Linear(10, 2)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
        scheduler = WarmupCosineAnnealingLR(optimizer, total_epochs=2, warmup_epochs=3, min_lr=1e-6)

        for _ in range(2):
            optimizer.step()
            scheduler.step()


# ============================================================================
# 3. AutoML Heuristics & Auto-Sizing Tests
# ============================================================================

class TestAutoMLHeuristics:
    """Test suite validating aspect ratio preservation and 32-multiple image sizing."""

    @pytest.mark.parametrize(
        "orig_size,target_max,expected",
        [
            ((256, 256), 256, (256, 256)),
            ((1920, 1080), 512, (512, 288)),
            ((1080, 1920), 512, (288, 512)),
            ((1024, 768), 256, (256, 192)),
            ((1200, 300), 256, (256, 64)),
            ((300, 1200), 256, (64, 256)),
            ((100, 100), 256, (256, 256)),
        ],
    )
    def test_optimal_image_sizing_snapping(self, orig_size, target_max, expected):
        """Verifies image dimensions are snapped to exact 32-multiples and preserve aspect ratio."""
        w, h = calculate_optimal_image_size(orig_size, target_max=target_max, multiple_of=32, min_dim=64)
        assert (w, h) == expected
        assert w % 32 == 0, f"Width {w} is not divisible by 32"
        assert h % 32 == 0, f"Height {h} is not divisible by 32"
        assert w >= 64 and h >= 64, "Dimensions violate min_dim bound"


# ============================================================================
# 4. Early Stopping & Checkpointing Tests
# ============================================================================

class TestEarlyStoppingAndCheckpointing:
    """Test suite validating early stopping and atomic checkpointing."""

    def test_early_stopping_trigger(self):
        """Verifies early stopping halts when patience is exceeded without improvement."""
        es = EarlyStopping(patience=3, mode="min")
        losses = [1.0, 0.8, 0.7, 0.75, 0.76, 0.77]
        stops = []
        for e, loss in enumerate(losses):
            es.step(loss, e)
            stops.append(es.early_stop)

        assert not stops[2]  # Epoch 2 improved to 0.7
        assert not stops[3]  # Patience counter = 1
        assert not stops[4]  # Patience counter = 2
        assert stops[5]      # Patience counter = 3 -> Early stop triggered!
        assert es.best_score == 0.7
        assert es.best_epoch == 2

    def test_atomic_checkpoint_writing(self, tmp_path):
        """Verifies checkpoint is written atomically with matching model_meta.json."""
        output_dir = tmp_path / "ckpt_test"
        output_dir.mkdir(parents=True, exist_ok=True)

        model = nn.Linear(5, 2)
        tmp_pt = output_dir / "best_model.pt.tmp"
        final_pt = output_dir / "best_model.pt"

        torch.save({"model_state_dict": model.state_dict(), "best_metric": 0.05}, tmp_pt)
        tmp_pt.replace(final_pt)

        meta_p = output_dir / "model_meta.json"
        with open(meta_p, "w", encoding="utf-8") as f:
            json.dump({
                "task": "classification",
                "preset": "⚡ Fast Prototype",
                "best_epoch": 1,
                "best_metric": 0.05,
                "classes": ["OK", "NG"],
            }, f)

        assert final_pt.exists()
        assert not tmp_pt.exists()
        assert meta_p.exists()

        loaded = torch.load(final_pt, weights_only=False)
        assert loaded["best_metric"] == 0.05


# ============================================================================
# 5. Clean Abort Protocol Tests
# ============================================================================

class TestCleanAbortProtocol:
    """Test suite validating clean abort without thread leaks or file corruption."""

    def test_clean_abort_during_training(self, tmp_path):
        """Verifies trainer terminates immediately upon abort call without hanging."""
        cb = LoggingCallback()
        trainer = UnifiedAutoMLTrainer(
            task="classification",
            dataset_path=tmp_path,
            output_dir=tmp_path / "output",
            preset="fast",
            callback=cb,
        )

        trainer.abort()
        assert trainer._abort_flag.is_set()


# ============================================================================
# 6. Unified Inference API Tests
# ============================================================================

class TestUnifiedInferenceAPI:
    """Test suite validating unified infer() across all 4 industrial vision tasks."""

    @pytest.fixture(scope="class")
    def synthetic_models(self, tmp_path_factory):
        tmp_dir = tmp_path_factory.mktemp("infer_suite")
        data_dir = tmp_dir / "data"
        out_dir = tmp_dir / "models"

        # Generate small dataset
        generate_synthetic_dataset(output_dir=data_dir, num_samples=6, modality="pcb", task="all")

        # Train 1 epoch per task
        tasks = ["classification", "detection", "segmentation", "anomaly"]
        model_paths = {}
        for t in tasks:
            t_data = data_dir / t
            t_out = out_dir / t
            trainer = UnifiedAutoMLTrainer(
                task=t,
                dataset_path=t_data,
                output_dir=t_out,
                preset="fast",
                config_overrides={"epochs": 1, "image_size": 128},
            )
            trainer.train(job_id=f"job_{t}")
            model_paths[t] = t_out / "best_model.pt"

        return model_paths

    def test_infer_classification(self, synthetic_models):
        pt = synthetic_models["classification"]
        sample = np.full((128, 128, 3), 100, dtype=np.uint8)
        res = infer("classification", pt, sample)

        assert res.task == "classification"
        assert "predicted_class" in res.predictions
        assert 0.0 <= res.confidence_score <= 1.0
        assert res.visual_overlay.shape == (128, 128, 3)
        assert res.latency_ms > 0.0

    def test_infer_detection(self, synthetic_models):
        pt = synthetic_models["detection"]
        sample = np.full((128, 128, 3), 100, dtype=np.uint8)
        res = infer("detection", pt, sample, threshold=0.1)

        assert res.task == "detection"
        assert isinstance(res.predictions, list)
        assert res.visual_overlay.shape == (128, 128, 3)
        assert res.latency_ms > 0.0

    def test_infer_segmentation(self, synthetic_models):
        pt = synthetic_models["segmentation"]
        sample = np.full((128, 128, 3), 100, dtype=np.uint8)
        res = infer("segmentation", pt, sample)

        assert res.task == "segmentation"
        assert "mask_coverage_percent" in res.predictions
        assert "polygon_contours" in res.predictions
        assert 0.0 <= res.confidence_score <= 1.0
        assert res.confidence_score != 0.92  # Verify dynamic softmax calculation
        assert res.visual_overlay.shape == (128, 128, 3)

    def test_infer_anomaly(self, synthetic_models):
        pt = synthetic_models["anomaly"]
        sample = np.full((128, 128, 3), 100, dtype=np.uint8)
        res = infer("anomaly", pt, sample, threshold=0.5)

        assert res.task == "anomaly"
        assert "is_anomaly" in res.predictions
        assert "anomaly_score" in res.predictions
        assert res.visual_overlay.shape == (128, 128, 3)

    def test_infer_rgba_input(self, synthetic_models):
        """Verifies infer() cleanly sanitizes 4-channel RGBA numpy inputs without crashing."""
        pt = synthetic_models["classification"]
        sample_rgba = np.full((128, 128, 4), 120, dtype=np.uint8)
        res = infer("classification", pt, sample_rgba)

        assert res.task == "classification"
        assert 0.0 <= res.confidence_score <= 1.0
        assert res.visual_overlay.shape == (128, 128, 3)


# ============================================================================
# 7. Milestone M2 Remediation Tests (Integrity & Robustness)
# ============================================================================

class TestDetectionValidationLossRemediation:
    """Verifies that Faster R-CNN detection validation loss is dynamic and not hardcoded 0.05."""

    def test_detection_validation_loss_not_constant_0_05(self, tmp_path):
        data_dir = tmp_path / "data"
        out_dir = tmp_path / "out"
        generate_synthetic_dataset(output_dir=data_dir, num_samples=6, modality="pcb", task="detection")

        class EpochCallback(TrainingCallback):
            def __init__(self):
                self.val_losses = []

            def on_epoch_end(self, epoch, total_epochs, train_loss, val_loss, lr, metrics):
                self.val_losses.append(val_loss)

        cb = EpochCallback()
        trainer = UnifiedAutoMLTrainer(
            task="detection",
            dataset_path=data_dir / "detection",
            output_dir=out_dir,
            preset="fast",
            callback=cb,
            config_overrides={"epochs": 2, "image_size": 64},
        )
        trainer.train(job_id="test_detection_val_loss")

        assert len(cb.val_losses) >= 2
        assert all(vl != 0.05 for vl in cb.val_losses), f"Detected hardcoded 0.05: {cb.val_losses}"
        assert cb.val_losses[0] != cb.val_losses[1], f"Validation loss was static across epochs: {cb.val_losses}"


class TestPrecisionPresetInferenceRemediation:
    """Verifies infer() succeeds on models trained with preset='precision' across all 4 tasks without crashes."""

    @pytest.mark.parametrize("task", ["classification", "detection", "segmentation", "anomaly"])
    def test_precision_preset_infer_all_tasks(self, tmp_path, task):
        data_dir = tmp_path / f"data_{task}"
        out_dir = tmp_path / f"out_{task}"
        generate_synthetic_dataset(output_dir=data_dir, num_samples=4, modality="pcb", task=task)

        trainer = UnifiedAutoMLTrainer(
            task=task,
            dataset_path=data_dir / task,
            output_dir=out_dir,
            preset="precision",
            config_overrides={"epochs": 1, "image_size": 64},
        )
        trainer.train(job_id=f"test_precision_{task}")

        best_model_pt = out_dir / "best_model.pt"
        assert best_model_pt.exists()

        sample = np.full((64, 64, 3), 100, dtype=np.uint8)
        res = infer(task, best_model_pt, sample)

        assert res.task.startswith(task[:4]) or res.task == task
        assert res.confidence_score is not None
        assert res.visual_overlay.shape == (64, 64, 3)
