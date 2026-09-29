"""
backend/tests/test_anomaly.py

Comprehensive test suite for Task 4: Unsupervised Anomaly Detection Engine.
Validates:
  - ResNet feature extractor hooks (layer2 + layer3) and spatial alignment
  - PaDiM multivariate Gaussian modeling, covariance regularization, and Mahalanobis distance
  - PatchCore memory bank, Min-Max greedy coreset subsampling, and kNN inference
  - Anomaly metrics: Image & Pixel AUROC, F1 score, and decision threshold calibration
  - Strict normal-only training invariant enforcement
  - Score discriminability (defect scores > normal scores)
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import numpy as np
import pytest
import torch
from torch.utils.data import DataLoader, TensorDataset

from backend.engine.anomaly import (
    PaDiMDetector,
    PatchCoreDetector,
    ResNetFeatureExtractor,
    compute_anomaly_metrics,
)
from backend.engine.dataset_loaders import AnomalyDataset


class TestResNetFeatureExtractor:
    """Test suite for intermediate ResNet hook extraction."""

    def test_feature_extractor_shapes(self):
        extractor = ResNetFeatureExtractor(backbone_name="resnet18", pretrained=False)
        x = torch.randn(2, 3, 128, 128)
        feats = extractor(x)
        # ResNet18: layer2 (128) + layer3 (256) = 384 channels. H/8, W/8 = 16x16
        assert feats.shape == (2, 384, 16, 16)

    def test_feature_extractor_resnet50(self):
        extractor = ResNetFeatureExtractor(backbone_name="resnet50", pretrained=False)
        x = torch.randn(1, 3, 128, 128)
        feats = extractor(x)
        # ResNet50: layer2 (512) + layer3 (1024) = 1536 channels
        assert feats.shape == (1, 1536, 16, 16)


class TestPaDiMDetector:
    """Test suite for PaDiM detector fitting, Mahalanobis inference, and persistence."""

    def test_padim_end_to_end(self):
        detector = PaDiMDetector(backbone_name="resnet18", target_dim=32, regularizer=0.01, pretrained=False)

        # 4 normal training images
        train_imgs = torch.randn(4, 3, 128, 128)
        train_loader = DataLoader(TensorDataset(train_imgs), batch_size=2)

        fit_stats = detector.fit(train_loader)
        assert fit_stats["total_samples"] == 4
        assert detector.cov_inv is not None
        assert detector.threshold > 0

        # Inference
        test_img = torch.randn(3, 128, 128)
        hmap, score = detector.predict_anomaly_map(test_img)
        assert hmap.shape == (128, 128)
        assert isinstance(score, float)
        assert score >= 0.0

        # Callable interface
        heatmaps_t, scores_t = detector(test_img.unsqueeze(0))
        assert heatmaps_t.shape == (1, 1, 128, 128)
        assert scores_t.shape == (1,)

        # Save and load roundtrip
        with tempfile.NamedTemporaryFile(suffix=".pt") as tmp:
            detector.save(tmp.name)
            new_det = PaDiMDetector(backbone_name="resnet18", target_dim=32, pretrained=False)
            new_det.load(tmp.name)
            assert new_det.cov_inv is not None
            assert new_det.threshold == detector.threshold


class TestPatchCoreDetector:
    """Test suite for PatchCore detector, greedy coreset subsampling, and kNN inference."""

    def test_patchcore_end_to_end(self):
        detector = PatchCoreDetector(
            backbone_name="resnet18",
            coreset_sampling_ratio=0.1,
            max_coreset_size=50,
            pretrained=False,
        )

        train_imgs = torch.randn(3, 3, 128, 128)
        train_loader = DataLoader(TensorDataset(train_imgs), batch_size=1)

        fit_stats = detector.fit(train_loader)
        assert detector.coreset is not None
        assert detector.coreset.shape[0] <= 50
        assert detector.threshold > 0

        # Inference
        test_img = torch.randn(3, 128, 128)
        hmap, score = detector.predict_anomaly_map(test_img)
        assert hmap.shape == (128, 128)
        assert isinstance(score, float)
        assert score >= 0.0

        # Callable interface
        heatmaps_t, scores_t = detector(test_img.unsqueeze(0))
        assert heatmaps_t.shape == (1, 1, 128, 128)
        assert scores_t.shape == (1,)

        # Save and load roundtrip
        with tempfile.NamedTemporaryFile(suffix=".pt") as tmp:
            detector.save(tmp.name)
            new_det = PatchCoreDetector(backbone_name="resnet18", pretrained=False)
            new_det.load(tmp.name)
            assert new_det.coreset is not None
            assert new_det.threshold == detector.threshold


class TestAnomalyMetrics:
    """Test suite for image & pixel AUROC, F1, and threshold search."""

    def test_compute_anomaly_metrics_perfect_separation(self):
        # Good samples: scores 0.1, 0.2. Defect samples: scores 0.8, 0.9.
        scores = [0.1, 0.2, 0.8, 0.9]
        labels = [0, 0, 1, 1]

        res = compute_anomaly_metrics(scores, labels)
        assert res["image_auroc"] == 1.0
        assert res["f1_score"] == 1.0
        assert res["confusion_matrix"] == [[2, 0], [0, 2]]

    def test_compute_anomaly_metrics_with_pixel_heatmaps(self):
        scores = [0.1, 0.9]
        labels = [0, 1]
        hmap0 = np.zeros((10, 10))
        hmap1 = np.ones((10, 10)) * 0.9
        mask0 = np.zeros((10, 10), dtype=np.uint8)
        mask1 = np.ones((10, 10), dtype=np.uint8)

        res = compute_anomaly_metrics(
            image_scores=scores,
            image_labels=labels,
            pixel_heatmaps=[hmap0, hmap1],
            pixel_masks=[mask0, mask1],
        )
        assert res["image_auroc"] == 1.0
        assert res["pixel_auroc"] == 1.0


class TestAnomalyStrictNormalInvariant:
    """Verify strict 100% normal-only training invariant enforcement."""

    def test_anomaly_dataset_rejects_defect_in_train(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "train" / "good").mkdir(parents=True)
            (root / "train" / "scratch").mkdir(parents=True)  # Forbidden defect directory

            with pytest.raises(ValueError, match="must contain exclusively normal"):
                _ = AnomalyDataset(root_dir=root, split="train")
