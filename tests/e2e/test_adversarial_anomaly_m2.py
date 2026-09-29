"""
tests/e2e/test_adversarial_anomaly_m2.py

Milestone M2 Adversarial Test Suite (Empirical Challenger 2).
Focus:
  1. Strict Normal-Only Invariant:
     - Feeds datasets with defect contamination in train split ('defect', 'scratch', 'crack', 'ng', 'void', etc.)
     - Confirms AnomalyDataset and detectors rigorously reject them via ValueError.
     - Confirms valid normal folder aliases ('good', 'ok', 'normal', 'pass') load cleanly with label 0.
  2. Anomaly Separation Power:
     - Generates synthetic normal vs defect samples with textured background.
     - Fits PaDiM and PatchCore on normal samples; evaluates on mixed test sets.
     - Measures AUROC (>= 0.95), anomaly score margin (ratio >= 2.0x), and threshold calibration.
  3. Covariance Regularization & Inversion Stability:
     - Stress tests PaDiM covariance inversion with identical/duplicate normal images (zero variance patches).
     - Confirms epsilon regularization prevents LinAlgError crashes on both CPU and Apple Silicon MPS.
     - Verifies condition of cov_inv (no NaNs, no Infs) and boundary N=1 sample rejection.
  4. Vector Polygon Extraction & Rasterization Roundtrip:
     - Evaluates complex defect contours: serpentine scratches, multi-island pits, and donut shapes.
     - Measures Intersection-over-Union (IoU) roundtrip fidelity between extract_polygons and polygons_to_mask.
     - Identifies and reproduces topological hole limitation in cv2.RETR_EXTERNAL.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
import pytest
import torch
from PIL import Image
from torch.utils.data import DataLoader, TensorDataset

from backend.engine.anomaly import (
    PaDiMDetector,
    PatchCoreDetector,
    compute_anomaly_metrics,
)
from backend.engine.dataset_loaders import AnomalyDataset
from backend.engine.segmentation.contours import extract_polygons, polygons_to_mask


# ============================================================================
# 1. Strict Normal-Only Invariant Tests
# ============================================================================

class TestStrictNormalOnlyInvariant:
    """
    Adversarially feeds contaminated datasets to AnomalyDataset to verify
    strict 100% normal-only training invariant enforcement.
    """

    @pytest.mark.parametrize(
        "defect_folder",
        [
            "defect",
            "scratch",
            "crack",
            "foreign_particle",
            "void",
            "contamination",
            "NG",
            "solder_bridge",
            "불량",
        ],
    )
    def test_train_split_rejects_defect_folder_contamination(self, defect_folder: str):
        """Verify any defect folder in train/ raises ValueError with descriptive message."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir)
            (p / "train" / "good").mkdir(parents=True)
            (p / "train" / defect_folder).mkdir(parents=True)

            Image.new("RGB", (32, 32), color=(50, 100, 150)).save(p / "train" / "good" / "normal_01.png")
            Image.new("RGB", (32, 32), color=(200, 20, 20)).save(p / "train" / defect_folder / "defect_01.png")

            with pytest.raises(ValueError, match="must contain exclusively normal"):
                _ = AnomalyDataset(root_dir=p, split="train")

    def test_missing_train_directory_raises_file_not_found(self):
        """Verify missing train directory raises FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir)
            with pytest.raises(FileNotFoundError, match="Anomaly train directory does not exist"):
                _ = AnomalyDataset(root_dir=p, split="train")

    @pytest.mark.parametrize("alias", ["good", "ok", "normal", "pass", "GOOD", "OK"])
    def test_train_split_accepts_valid_normal_aliases(self, alias: str):
        """Verify standard normal aliases are accepted and produce label 0 with zero masks."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir)
            dir_alias = p / "train" / alias
            dir_alias.mkdir(parents=True)

            for i in range(4):
                Image.new("RGB", (32, 32), color=(40, 80 + i * 10, 120)).save(dir_alias / f"sample_{i}.png")

            ds = AnomalyDataset(root_dir=p, split="train")
            assert len(ds) == 4
            for idx in range(len(ds)):
                img_t, label, mask_t = ds[idx]
                assert label == 0
                assert (mask_t == 0).all()


# ============================================================================
# 2. Covariance Regularization & Inversion Stability Tests
# ============================================================================

class TestCovarianceRegularizationAndStability:
    """
    Stress tests PaDiM covariance regularization (Sigma + eps * I) and inversion stability
    under zero-variance conditions (identical/duplicate images).
    """

    def test_padim_zero_variance_identical_images_inversion_cpu(self):
        """
        Adversarial test: Train PaDiM on 5 completely identical images.
        Sample covariance Sigma is identically zero across all patches and dimensions.
        Confirms eps * I regularization prevents LinAlgError singular matrix crash on CPU.
        """
        torch.manual_seed(42)
        identical_imgs = torch.ones(5, 3, 128, 128) * 0.5
        loader = DataLoader(TensorDataset(identical_imgs), batch_size=2)

        detector = PaDiMDetector(
            backbone_name="resnet18",
            target_dim=32,
            regularizer=0.01,
            pretrained=False,
            device="cpu",
        )
        fit_stats = detector.fit(loader)

        assert fit_stats["total_samples"] == 5
        assert detector.cov_inv is not None
        assert not torch.isnan(detector.cov_inv).any(), "cov_inv contains NaN values"
        assert not torch.isinf(detector.cov_inv).any(), "cov_inv contains Inf values"

        # Predict anomaly on identical normal image: Mahalanobis distance should be near 0
        hmap_norm, score_norm = detector.predict_anomaly_map(identical_imgs[0])
        assert not math.isnan(score_norm)
        assert score_norm < 1e-3, f"Expected near-zero normal score, got {score_norm}"

        # Predict anomaly on contrasting defect image: Mahalanobis distance should be large & finite
        defect_img = identical_imgs[0].clone()
        defect_img[:, 30:70, 30:70] = 0.05
        hmap_def, score_def = detector.predict_anomaly_map(defect_img)
        assert not math.isnan(score_def)
        assert score_def > 1.0, f"Expected defect score > 1.0, got {score_def}"
        assert score_def > score_norm * 1000.0, "Defect score must be overwhelmingly higher"

    def test_padim_zero_variance_identical_images_inversion_mps(self):
        """
        Adversarial test: Verify flattened view(-1, d, d) inversion executes safely on
        Apple Silicon MPS without kernel crashes or deadlocks.
        """
        if not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
            pytest.skip("Apple Silicon MPS is not available on this environment")

        torch.manual_seed(42)
        identical_imgs = torch.ones(5, 3, 128, 128) * 0.5
        loader = DataLoader(TensorDataset(identical_imgs), batch_size=2)

        detector = PaDiMDetector(
            backbone_name="resnet18",
            target_dim=32,
            regularizer=0.01,
            pretrained=False,
            device="mps",
        )
        fit_stats = detector.fit(loader)

        assert fit_stats["total_samples"] == 5
        assert detector.cov_inv is not None
        assert not torch.isnan(detector.cov_inv).any()
        assert not torch.isinf(detector.cov_inv).any()

        hmap_norm, score_norm = detector.predict_anomaly_map(identical_imgs[0])
        assert not math.isnan(score_norm)

    def test_padim_minimum_sample_size_boundary(self):
        """Verify PaDiM rejects N=1 sample (requires >= 2 for sample covariance)."""
        single_img = torch.randn(1, 3, 128, 128)
        loader = DataLoader(TensorDataset(single_img), batch_size=1)

        detector = PaDiMDetector(backbone_name="resnet18", pretrained=False)
        with pytest.raises(ValueError, match="at least 2 normal images"):
            detector.fit(loader)

    def test_padim_two_sample_boundary(self):
        """Verify PaDiM succeeds with exact boundary condition N=2 normal images."""
        two_imgs = torch.randn(2, 3, 128, 128)
        loader = DataLoader(TensorDataset(two_imgs), batch_size=1)

        detector = PaDiMDetector(backbone_name="resnet18", target_dim=16, pretrained=False)
        fit_stats = detector.fit(loader)
        assert fit_stats["total_samples"] == 2
        assert detector.cov_inv is not None


# ============================================================================
# 3. Anomaly Separation Power & Calibration Tests
# ============================================================================

def _generate_synthetic_anomaly_dataset(
    num_train: int = 10,
    num_test_normal: int = 8,
    num_test_defect: int = 8,
    img_size: int = 128,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Generates synthetic textured normal samples and contrasting localized defect samples."""
    torch.manual_seed(42)
    np.random.seed(42)

    def make_normal(n: int) -> torch.Tensor:
        base = torch.zeros(n, 3, img_size, img_size)
        for i in range(img_size):
            base[:, :, i, :] = 0.5 + 0.12 * math.sin(i / 4.0)
        base += 0.02 * torch.randn_like(base)
        return torch.clamp(base, 0.0, 1.0)

    train_norm = make_normal(num_train)
    test_norm = make_normal(num_test_normal)

    # Defect samples: same base texture + high-contrast localized flaw (dark burn pit)
    test_defect = make_normal(num_test_defect)
    for i in range(num_test_defect):
        x = np.random.randint(35, 75)
        y = np.random.randint(35, 75)
        test_defect[i, :, y : y + 25, x : x + 25] = 0.02

    return train_norm, test_norm, test_defect


class TestAnomalySeparationPower:
    """
    Adversarial verification of detection power: AUROC, margin separation ratio,
    and automatic decision threshold calibration.
    """

    def test_padim_separation_power_and_threshold_calibration(self):
        """
        Verifies PaDiM achieves AUROC >= 0.95, defect score margin >= 2.0x,
        and threshold cleanly separates normal from defect distributions.
        """
        train_norm, test_norm, test_def = _generate_synthetic_anomaly_dataset()
        train_loader = DataLoader(TensorDataset(train_norm), batch_size=4)

        padim = PaDiMDetector(backbone_name="resnet18", target_dim=50, regularizer=0.01, pretrained=False)
        padim.fit(train_loader)

        norm_scores = [padim.predict_anomaly_map(img)[1] for img in test_norm]
        def_scores = [padim.predict_anomaly_map(img)[1] for img in test_def]

        mean_n = float(np.mean(norm_scores))
        mean_d = float(np.mean(def_scores))
        margin = mean_d / max(1e-8, mean_n)

        scores = norm_scores + def_scores
        labels = [0] * len(norm_scores) + [1] * len(def_scores)
        metrics = compute_anomaly_metrics(scores, labels, fixed_threshold=padim.threshold)

        # 1. AUROC check
        assert metrics["image_auroc"] >= 0.95, f"PaDiM AUROC {metrics['image_auroc']} < 0.95"

        # 2. Defect margin check (defect score / normal score >= 2.0x)
        assert margin >= 2.0, f"PaDiM score margin {margin:.2f}x < 2.0x"

        # 3. Decision threshold calibration check
        # All defect samples must score strictly above the calibrated decision threshold
        assert all(s >= padim.threshold for s in def_scores), (
            f"Some defect scores were below threshold {padim.threshold}: {def_scores}"
        )

    def test_patchcore_separation_power_and_threshold_calibration(self):
        """
        Verifies PatchCore achieves AUROC >= 0.95, defect score margin >= 2.0x,
        and threshold cleanly separates normal from defect distributions.
        """
        train_norm, test_norm, test_def = _generate_synthetic_anomaly_dataset()
        train_loader = DataLoader(TensorDataset(train_norm), batch_size=4)

        patchcore = PatchCoreDetector(
            backbone_name="resnet18",
            coreset_sampling_ratio=0.1,
            max_coreset_size=200,
            pretrained=False,
        )
        patchcore.fit(train_loader)

        norm_scores = [patchcore.predict_anomaly_map(img)[1] for img in test_norm]
        def_scores = [patchcore.predict_anomaly_map(img)[1] for img in test_def]

        mean_n = float(np.mean(norm_scores))
        mean_d = float(np.mean(def_scores))
        margin = mean_d / max(1e-8, mean_n)

        scores = norm_scores + def_scores
        labels = [0] * len(norm_scores) + [1] * len(def_scores)
        metrics = compute_anomaly_metrics(scores, labels, fixed_threshold=patchcore.threshold)

        # 1. AUROC check
        assert metrics["image_auroc"] >= 0.95, f"PatchCore AUROC {metrics['image_auroc']} < 0.95"

        # 2. Defect margin check (defect score / normal score >= 2.0x)
        assert margin >= 2.0, f"PatchCore score margin {margin:.2f}x < 2.0x"

        # 3. Decision threshold calibration check
        assert all(s >= patchcore.threshold for s in def_scores), (
            f"Some defect scores were below threshold {patchcore.threshold}: {def_scores}"
        )


# ============================================================================
# 4. Vector Polygon Extraction & Rasterization Roundtrip Tests
# ============================================================================

class TestVectorPolygonRoundtripFidelity:
    """
    Adversarially challenges vector polygon extraction and rasterization roundtrip
    across complex industrial defect topologies:
      - Multi-island pits
      - Serpentine winding scratches
      - Donut shapes (slit C-ring and closed topological annulus)
    """

    def test_polygon_roundtrip_multi_island_pits(self):
        """
        Verifies vector polygon extraction correctly isolates multiple disjoint
        small defect islands and achieves roundtrip IoU >= 0.98.
        """
        h, w = 256, 256
        mask = np.zeros((h, w), dtype=np.uint8)
        centers = [(40, 50), (100, 80), (180, 60), (70, 180), (150, 200), (210, 160)]
        for cx, cy in centers:
            cv2.circle(mask, (cx, cy), 8, 1, -1)

        polys = extract_polygons(mask, approx_epsilon=0.001)
        assert len(polys) == len(centers), f"Expected {len(centers)} polygons, extracted {len(polys)}"

        recon = polygons_to_mask(polys, h, w)
        intersection = np.logical_and(mask > 0, recon > 0).sum()
        union = np.logical_or(mask > 0, recon > 0).sum()
        iou = float(intersection) / float(union)

        assert iou >= 0.98, f"Multi-island pits IoU {iou:.4f} < 0.98"

    def test_polygon_roundtrip_serpentine_scratch(self):
        """
        Verifies vector polygon extraction handles curved, winding scratches.
        Confirms IoU >= 0.98 with fine approximation tolerance (approx_epsilon <= 0.0005).
        """
        h, w = 256, 256
        mask = np.zeros((h, w), dtype=np.uint8)
        pts_scratch = []
        for x in range(30, 220):
            y = int(128 + 40 * math.sin(x / 15.0))
            pts_scratch.append((x, y))
        for i in range(len(pts_scratch) - 1):
            cv2.line(mask, pts_scratch[i], pts_scratch[i + 1], 1, thickness=5)

        # With fine approximation tolerance
        polys_fine = extract_polygons(mask, approx_epsilon=0.0005)
        recon_fine = polygons_to_mask(polys_fine, h, w)

        inter = np.logical_and(mask > 0, recon_fine > 0).sum()
        union = np.logical_or(mask > 0, recon_fine > 0).sum()
        iou_fine = float(inter) / float(union)

        assert iou_fine >= 0.98, f"Serpentine scratch fine IoU {iou_fine:.4f} < 0.98"

    def test_polygon_roundtrip_slit_c_ring_donut(self):
        """
        Verifies vector polygon extraction handles open annular defect geometries
        (C-ring / slit donut) and achieves roundtrip IoU >= 0.98.
        """
        h, w = 256, 256
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(mask, (128, 128), 50, 1, -1)
        cv2.circle(mask, (128, 128), 25, 0, -1)
        # 4-pixel slit makes it topologically simply connected
        mask[126:130, 128:185] = 0

        polys = extract_polygons(mask, approx_epsilon=0.0005)
        recon = polygons_to_mask(polys, h, w)

        inter = np.logical_and(mask > 0, recon > 0).sum()
        union = np.logical_or(mask > 0, recon > 0).sum()
        iou = float(inter) / float(union)

        assert iou >= 0.98, f"Slit C-ring donut IoU {iou:.4f} < 0.98"

    def test_polygon_roundtrip_closed_donut_hole_empirical_finding(self):
        """
        EMPIRICAL CHALLENGE FINDING:
        Tests a closed donut shape (outer circle radius 50, inner circular hole radius 20).
        Because extract_polygons() uses cv2.RETR_EXTERNAL, only the outer contour is
        extracted and the inner hole is dropped.
        Consequently, polygons_to_mask() fills the entire disk, yielding an IoU of ~0.84 (< 0.98).
        This test documents the empirical finding and verifies the hole punching mechanism.
        """
        h, w = 256, 256
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(mask, (128, 128), 50, 1, -1)
        cv2.circle(mask, (128, 128), 20, 0, -1)

        # 1. Current extract_polygons behavior (cv2.RETR_EXTERNAL)
        polys_current = extract_polygons(mask, approx_epsilon=0.0)
        recon_current = polygons_to_mask(polys_current, h, w)

        inter = np.logical_and(mask > 0, recon_current > 0).sum()
        union = np.logical_or(mask > 0, recon_current > 0).sum()
        iou_current = float(inter) / float(union)

        # Document exact empirical finding: IoU is ~0.8398 because the hole is filled
        assert iou_current < 0.98, "Expected current RETR_EXTERNAL to drop internal hole"
        assert math.isclose(iou_current, 0.8398, abs_tol=0.01)

        # 2. Verify hole-punching remediation:
        # If inner hole is extracted and tagged with class_id=0, polygons_to_mask punches hole cleanly
        outer_cnt, _ = cv2.findContours(
            (mask > 0).astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        hole_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(hole_mask, outer_cnt, 1)
        hole_mask = np.logical_and(hole_mask == 1, mask == 0).astype(np.uint8) * 255
        inner_cnt, _ = cv2.findContours(hole_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        polys_remediated = [
            {"class_id": 1, "points": [[int(pt[0][0]), int(pt[0][1])] for pt in outer_cnt[0]]},
            {"class_id": 0, "points": [[int(pt[0][0]), int(pt[0][1])] for pt in inner_cnt[0]]},
        ]
        recon_remediated = polygons_to_mask(polys_remediated, h, w)
        inter_rem = np.logical_and(mask > 0, recon_remediated > 0).sum()
        union_rem = np.logical_or(mask > 0, recon_remediated > 0).sum()
        iou_remediated = float(inter_rem) / float(union_rem)

        assert iou_remediated >= 0.98, f"Remediated hole IoU {iou_remediated:.4f} < 0.98"
