"""
backend/tests/test_classification.py

Comprehensive test suite for Task 1: Classification Engine.
Validates:
  - Model architectures (ResNet18, ConvNeXt-Tiny, EfficientNet-B0)
  - Class-weighted Cross-Entropy loss with label smoothing
  - Pure PyTorch native Grad-CAM hooks and normalized heatmaps
  - Multi-class evaluation metrics and confusion matrix calculations
  - Multi-batch training convergence
"""

from __future__ import annotations

import numpy as np
import pytest
import torch
import torch.nn as nn

from backend.engine.classification import (
    build_classification_model,
    compute_classification_metrics,
    compute_class_weights,
    create_classification_loss,
    create_classification_model,
    GradCAM,
)


class TestClassificationModelFactory:
    """Test suite for classification model architecture instantiation and heads."""

    def test_resnet18_architecture_and_head(self):
        model = create_classification_model(backbone="resnet18", num_classes=3, pretrained=False)
        assert isinstance(model.fc, nn.Linear)
        assert model.fc.out_features == 3
        assert hasattr(model, "target_gradcam_layer")
        assert model.target_gradcam_layer is model.layer4

        x = torch.randn(2, 3, 128, 128)
        out = model(x)
        assert out.shape == (2, 3)

    def test_convnext_tiny_architecture_and_head(self):
        model = create_classification_model(backbone="convnext_tiny", num_classes=4, pretrained=False)
        assert isinstance(model.classifier[2], nn.Linear)
        assert model.classifier[2].out_features == 4
        assert hasattr(model, "target_gradcam_layer")
        assert model.target_gradcam_layer is model.features[-1]

        x = torch.randn(2, 3, 128, 128)
        out = model(x)
        assert out.shape == (2, 4)

    def test_efficientnet_b0_architecture_and_head(self):
        model = create_classification_model(backbone="efficientnet_b0", num_classes=2, pretrained=False)
        assert isinstance(model.classifier[1], nn.Linear)
        assert model.classifier[1].out_features == 2
        assert hasattr(model, "target_gradcam_layer")
        assert model.target_gradcam_layer is model.features[-1]

        x = torch.randn(2, 3, 128, 128)
        out = model(x)
        assert out.shape == (2, 2)

    def test_invalid_backbone_raises_error(self):
        with pytest.raises(ValueError, match="Unsupported classification backbone"):
            _ = create_classification_model(backbone="invalid_transformer", num_classes=2)


class TestClassWeightedLoss:
    """Test suite for class weights computation and label smoothed CrossEntropyLoss."""

    def test_compute_class_weights_balanced(self):
        counts = [100, 100, 100]
        weights = compute_class_weights(counts, num_classes=3)
        assert len(weights) == 3
        assert torch.allclose(weights, torch.tensor([1.0, 1.0, 1.0]), atol=1e-5)

    def test_compute_class_weights_imbalanced(self):
        # 100 OK vs 10 scratch vs 5 bridge
        counts = [100, 10, 5]
        weights = compute_class_weights(counts, num_classes=3)
        assert weights[2] > weights[1] > weights[0]
        assert float(weights.mean()) == pytest.approx(1.0, abs=1e-4)

    def test_compute_class_weights_clamping(self):
        # Extreme 1000:1 ratio
        counts = [1000, 1]
        weights = compute_class_weights(counts, num_classes=2, min_weight=0.1, max_weight=10.0)
        assert float(weights.max()) <= 10.0 * 2.0  # Normalized

    def test_compute_class_weights_dict_input(self):
        counts = {"good": 50, "bridge": 10, "scratch": 5}
        weights = compute_class_weights(counts, num_classes=3)
        assert len(weights) == 3

    def test_loss_forward_and_backward(self):
        weights = torch.tensor([0.2, 1.8], dtype=torch.float32)
        criterion = create_classification_loss(weights=weights, label_smoothing=0.1)
        logits = torch.randn(4, 2, requires_grad=True)
        targets = torch.tensor([0, 1, 0, 1], dtype=torch.long)

        loss = criterion(logits, targets)
        assert torch.isfinite(loss)
        assert loss.item() > 0

        loss.backward()
        assert logits.grad is not None
        assert torch.isfinite(logits.grad).all()


class TestGradCAM:
    """Test suite for pure-PyTorch Grad-CAM hook and heatmap normalization."""

    def test_gradcam_generation_and_normalization(self):
        model = create_classification_model(backbone="resnet18", num_classes=2, pretrained=False)
        cam = GradCAM(model=model, target_layer=model.target_gradcam_layer)

        x = torch.randn(2, 3, 128, 128, requires_grad=True)
        heatmap = cam.generate(x, target_class=1)

        assert heatmap.shape == (2, 128, 128)
        assert float(heatmap.min()) >= 0.0 - 1e-6
        assert float(heatmap.max()) <= 1.0 + 1e-6
        cam.remove()
        assert len(cam.hooks) == 0

    def test_gradcam_context_manager_cleanup(self):
        model = create_classification_model(backbone="resnet18", num_classes=2, pretrained=False)
        with GradCAM(model=model, target_layer=model.target_gradcam_layer) as cam:
            x = torch.randn(1, 3, 64, 64)
            hmap = cam.generate(x)
            assert hmap.shape == (1, 64, 64)
        # Verify hooks cleaned up
        assert len(cam.hooks) == 0

    def test_gradcam_automatic_target_class(self):
        model = create_classification_model(backbone="resnet18", num_classes=3, pretrained=False)
        with GradCAM(model=model, target_layer=model.target_gradcam_layer) as cam:
            x = torch.randn(3, 3, 64, 64)
            hmap = cam.generate(x, target_class=None)
            assert hmap.shape == (3, 64, 64)


class TestClassificationMetrics:
    """Test suite for multi-class classification metrics and confusion matrix."""

    def test_perfect_predictions(self):
        preds = [0, 1, 2, 0, 1, 2]
        targets = [0, 1, 2, 0, 1, 2]
        res = compute_classification_metrics(preds, targets, num_classes=3, class_names=["OK", "Bridge", "Void"])

        assert res["accuracy"] == 1.0
        assert res["macro_precision"] == 1.0
        assert res["macro_recall"] == 1.0
        assert res["macro_f1"] == 1.0
        assert res["confusion_matrix"]["matrix"] == [[2, 0, 0], [0, 2, 0], [0, 0, 2]]

    def test_imbalanced_predictions_confusion_matrix(self):
        preds = [0, 0, 0, 1]
        targets = [0, 0, 1, 1]
        res = compute_classification_metrics(preds, targets, num_classes=2, class_names=["OK", "Defect"])

        assert res["accuracy"] == 0.75
        cm = res["confusion_matrix"]["matrix"]
        assert cm[0][0] == 2  # TN
        assert cm[1][0] == 1  # FN
        assert cm[1][1] == 1  # TP
        assert "per_class" in res
        assert res["per_class"]["OK"]["support"] == 2
        assert res["per_class"]["Defect"]["support"] == 2

    def test_tensor_inputs(self):
        preds = torch.tensor([0, 1, 2])
        targets = torch.tensor([0, 2, 2])
        res = compute_classification_metrics(preds, targets, num_classes=3)
        assert isinstance(res["accuracy"], float)
        assert res["accuracy"] == pytest.approx(2.0 / 3.0, abs=1e-3)


class TestClassificationTrainingLoop:
    """Verify that forward + loss + backward + optimizer converges on toy batch."""

    def test_convergence_on_toy_dataset(self):
        torch.manual_seed(42)
        model = create_classification_model(backbone="resnet18", num_classes=2, pretrained=False)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        criterion = nn.CrossEntropyLoss()

        x = torch.randn(4, 3, 64, 64)
        y = torch.tensor([0, 1, 0, 1], dtype=torch.long)

        initial_loss = None
        final_loss = None

        for step in range(5):
            optimizer.zero_grad()
            out = model(x)
            loss = criterion(out, y)
            loss.backward()
            optimizer.step()
            if step == 0:
                initial_loss = loss.item()
            if step == 4:
                final_loss = loss.item()

        assert final_loss < initial_loss
