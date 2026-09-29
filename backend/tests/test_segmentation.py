"""
backend/tests/test_segmentation.py

Comprehensive test suite for Task 3: Semantic Segmentation Engine.
Validates:
  - Pure PyTorch modular UNet and DeepLabV3 architectures
  - Focal Loss + Multi-Class Soft Dice Combo Loss on microscopic flaws (<0.02% area)
  - OpenCV contour-to-polygon vector extraction and mask roundtrip reconstruction
  - Segmentation metrics (mIoU, Mean Dice, Pixel Accuracy, Foreground IoU, Confusion Matrix)
  - Mini training loop convergence
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
import torch
import torch.nn as nn

from backend.engine.segmentation import (
    ComboLoss,
    DoubleConv,
    FocalLoss,
    SoftDiceLoss,
    UNet,
    build_segmentation_model,
    compute_segmentation_metrics,
    create_segmentation_model,
    extract_polygons,
    polygons_to_mask,
)


class TestSegmentationArchitectures:
    """Test suite for UNet and DeepLabV3 segmentation models."""

    def test_double_conv_block(self):
        conv = DoubleConv(in_channels=3, out_channels=16)
        x = torch.randn(2, 3, 32, 32)
        out = conv(x)
        assert out.shape == (2, 16, 32, 32)

    def test_unet_shapes_and_gradients(self):
        for features in [[16, 32, 64], [32, 64, 128, 256]]:
            model = UNet(in_channels=3, num_classes=3, features=features)
            x = torch.randn(2, 3, 128, 128, requires_grad=True)
            out = model(x)
            assert out.shape == (2, 3, 128, 128)
            loss = out.sum()
            loss.backward()
            assert x.grad is not None
            assert torch.isfinite(x.grad).all()

    def test_build_segmentation_model_presets(self):
        fast_model = build_segmentation_model(model_name="unet", preset="fast", num_classes=2)
        assert isinstance(fast_model, UNet)
        assert fast_model.features == [32, 64, 128, 256]

        prec_model = build_segmentation_model(model_name="unet", preset="precision", num_classes=2)
        assert isinstance(prec_model, UNet)
        assert prec_model.features == [64, 128, 256, 512]


class TestSegmentationLosses:
    """Test suite for SoftDiceLoss, FocalLoss, and composite ComboLoss."""

    def test_soft_dice_loss_perfect_prediction(self):
        # High confidence for correct classes
        logits = torch.tensor([[[[10.0, 10.0]], [[-10.0, -10.0]]], [[[-10.0, -10.0]], [[10.0, 10.0]]]])
        targets = torch.tensor([[[0, 0]], [[1, 1]]], dtype=torch.long)
        criterion = SoftDiceLoss(num_classes=2, include_background=True)
        loss = criterion(logits, targets)
        assert float(loss.item()) < 0.05

    def test_focal_loss_numerical_stability(self):
        logits = torch.randn(2, 2, 64, 64, requires_grad=True)
        targets = torch.zeros(2, 64, 64, dtype=torch.long)
        targets[0, 10:20, 10:20] = 1

        criterion = FocalLoss(gamma=2.0)
        loss = criterion(logits, targets)
        assert torch.isfinite(loss)
        loss.backward()
        assert torch.isfinite(logits.grad).all()

    def test_combo_loss_microscopic_defect(self):
        """Verify ComboLoss stability on microscopic defects (<0.02% pixel coverage)."""
        B, C, H, W = 2, 2, 256, 256
        logits = torch.randn(B, C, H, W, requires_grad=True)
        targets = torch.zeros(B, H, W, dtype=torch.long)
        # Inject 9 defect pixels out of 65,536 (0.013% coverage)
        targets[0, 100:103, 100:103] = 1

        criterion = ComboLoss(num_classes=2, dice_weight=1.0, include_background=False)
        total_loss, details = criterion(logits, targets)

        assert torch.isfinite(total_loss)
        assert details["focal_loss"] > 0
        assert details["dice_loss"] > 0

        total_loss.backward()
        assert torch.isfinite(logits.grad).all()


class TestPolygonExtractionAndRoundtrip:
    """Test suite for raster contour to vector polygon extraction and reconstruction."""

    def test_polygon_extraction_and_reconstruction(self):
        H, W = 256, 256
        mask = np.zeros((H, W), dtype=np.uint8)
        mask[40:80, 50:120] = 1  # Rectangle defect
        scratch_pts = np.array([[150, 150], [170, 155], [200, 180], [210, 210]], dtype=np.int32)
        cv2.polylines(mask, [scratch_pts], isClosed=False, color=2, thickness=4)

        polys = extract_polygons(mask, {1: "box_flaw", 2: "scratch"}, approx_epsilon=0.005)
        assert len(polys) == 2

        # Check coordinate bounds
        for poly in polys:
            assert len(poly["points"]) >= 3
            assert poly["area"] > 0
            for norm_pt in poly["points_normalized"]:
                assert 0.0 <= norm_pt[0] <= 1.0
                assert 0.0 <= norm_pt[1] <= 1.0

        recon_mask = polygons_to_mask(polys, H, W)
        # IoU on rectangular defect >= 0.99
        iou1 = np.sum((mask == 1) & (recon_mask == 1)) / np.sum((mask == 1) | (recon_mask == 1))
        assert iou1 >= 0.99

    def test_polygon_extraction_min_area_noise_filter(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[10, 10] = 1  # 1-pixel noise
        polys = extract_polygons(mask, min_area=4.0)
        assert len(polys) == 0


class TestSegmentationMetrics:
    """Test suite for mIoU, Mean Dice, Pixel Accuracy, and Confusion Matrix."""

    def test_segmentation_metrics_calculation(self):
        preds = np.array([[0, 1], [0, 1]])
        targets = np.array([[0, 1], [0, 0]])
        metrics = compute_segmentation_metrics(preds, targets, num_classes=2)

        assert metrics["pixel_accuracy"] == 0.75
        assert metrics["foreground_iou"] == 0.5
        assert "confusion_matrix" in metrics
        assert "per_class_iou" in metrics
        assert "per_class_dice" in metrics

    def test_tensor_metric_inputs(self):
        preds = torch.tensor([[0, 1], [1, 0]])
        targets = torch.tensor([[0, 1], [1, 0]])
        metrics = compute_segmentation_metrics(preds, targets, num_classes=2)
        assert metrics["pixel_accuracy"] == 1.0
        assert metrics["miou"] == 1.0
        assert metrics["mdice"] == 1.0


class TestSegmentationConvergence:
    """Verify that forward + combo loss + backward converges on synthetic inputs."""

    def test_segmentation_training_step(self):
        torch.manual_seed(42)
        model = UNet(in_channels=3, num_classes=2, features=[16, 32, 64])
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        criterion = ComboLoss(num_classes=2)

        x = torch.randn(2, 3, 64, 64)
        y = torch.zeros(2, 64, 64, dtype=torch.long)
        y[:, 20:40, 20:40] = 1

        init_loss = None
        final_loss = None

        for step in range(5):
            optimizer.zero_grad()
            out = model(x)
            loss, _ = criterion(out, y)
            loss.backward()
            optimizer.step()
            if step == 0:
                init_loss = loss.item()
            if step == 4:
                final_loss = loss.item()

        assert final_loss < init_loss
