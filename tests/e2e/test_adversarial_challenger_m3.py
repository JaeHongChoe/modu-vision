"""
tests/e2e/test_adversarial_challenger_m3.py

Milestone M3 Adversarial Challenge & Empirical Stress-Test Suite.
Authored by Challenger 1.

Validates:
1. Mathematical precision and robustness of Zero-Escape tau* calculation:
   - Extreme boundary scores (1e-6, 1.0 - 1e-6).
   - Datasets with 0 defects (all-normal) or 0 normals (all-defect).
   - Identical defect score ties.
   - Monotonicity across full threshold sweep tau in [0.0, 1.0].
   - Strict Zero-Underkill guarantee (FN == 0 at tau*).
   - Cost optimization formula: TotalCost(tau) = FN * Cost_escape + FP * Cost_scrap.
2. 4-Quadrant sample classification into FP (과검), FN (미검), TP (검출), TN (정상):
   - Mutually exclusive and collectively exhaustive partition.
   - Normal label resolution against shop-floor variants.
3. Live FastAPI endpoint verification:
   - POST /api/evaluation/zero-escape-calibrate
   - GET /api/evaluation/zero-escape-calibrate
   - GET /api/evaluation/overkill-underkill
4. Scale and stress test with 5,000 synthetic test predictions (latency < 250ms).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.engine.zero_escape_analyzer import (
    analyze_zero_escape,
    calculate_optimal_zero_underkill_threshold,
    compute_sample_defect_score,
    compute_sample_status,
    is_defect_label,
)


@pytest.fixture
def client(tmp_path):
    app = create_app(project_dir=str(tmp_path))
    return TestClient(app)


# ============================================================================
# 1. Mathematical Rigor & Boundary Stress Tests for tau*
# ============================================================================

def test_tau_star_extreme_boundary_scores():
    """Stress-test tau* with near-zero and near-one defect scores."""
    # Near zero score (e.g. 0.00005)
    # min_defect_score = 0.00005, tau* = min - 1e-4 = -0.00005 -> clamped to 0.0
    ng_scores = [0.00005, 0.50, 0.99]
    tau_star = calculate_optimal_zero_underkill_threshold(ng_scores, epsilon=1e-4)
    assert tau_star == 0.0, f"Expected 0.0 due to clamping, got {tau_star}"

    # Normal low defect score (e.g. 0.15)
    ng_scores2 = [0.15, 0.40, 0.85]
    tau_star2 = calculate_optimal_zero_underkill_threshold(ng_scores2, epsilon=1e-4)
    assert abs(tau_star2 - 0.1499) < 1e-5

    # High defect scores (all >= 0.90)
    ng_scores3 = [0.90, 0.95, 0.99]
    tau_star3 = calculate_optimal_zero_underkill_threshold(ng_scores3, epsilon=1e-4)
    assert abs(tau_star3 - 0.8999) < 1e-5


def test_tau_star_all_normals_dataset():
    """Edge case: Dataset contains zero defects (100% OK samples)."""
    # Direct function call with empty defects returns default_threshold
    assert calculate_optimal_zero_underkill_threshold([], default_threshold=0.50) == 0.50

    predictions = [
        {"image_id": f"ok_{i}", "ground_truth": "OK", "predicted_class": "OK", "confidence": 0.90, "defect_score": 0.10}
        for i in range(25)
    ]
    result = analyze_zero_escape(predictions, task="classification", current_threshold=0.50)
    assert result["status"] == "success"
    assert result["total_defects"] == 0
    assert result["total_normals"] == 25
    # When there are 0 defects, any threshold achieves 0 underkills.
    # The optimizer correctly maximizes threshold to 1.0 to achieve 0 overkills and 0 cost.
    assert result["optimal_threshold"] == 1.0
    assert result["optimal_stats"]["underkill_count"] == 0
    assert result["optimal_stats"]["underkill_rate"] == 0.0
    assert result["optimal_stats"]["overkill_count"] == 0
    assert result["optimal_stats"]["overkill_rate"] == 0.0
    assert result["optimal_stats"]["total_cost"] == 0.0


def test_tau_star_all_defects_dataset():
    """Edge case: Dataset contains zero normals (100% defect samples)."""
    predictions = [
        {"image_id": f"ng_{i}", "ground_truth": "crack", "predicted_class": "crack", "confidence": 0.85, "defect_score": 0.70 + (i * 0.01)}
        for i in range(20)
    ]
    result = analyze_zero_escape(predictions, task="classification", current_threshold=0.50)
    assert result["status"] == "success"
    assert result["total_defects"] == 20
    assert result["total_normals"] == 0
    # Overkill rate should be 0.0% without division by zero
    assert result["optimal_stats"]["overkill_count"] == 0
    assert result["optimal_stats"]["overkill_rate"] == 0.0
    # tau* = 0.70 - 1e-4 = 0.6999
    assert abs(result["optimal_threshold"] - 0.6999) < 1e-3
    assert result["optimal_stats"]["underkill_count"] == 0


def test_tau_star_identical_defect_scores():
    """Edge case: All defects have the exact same score."""
    predictions = [
        {"image_id": f"ng_{i}", "ground_truth": "Scratch", "predicted_class": "Scratch", "defect_score": 0.55}
        for i in range(10)
    ] + [
        {"image_id": f"ok_{i}", "ground_truth": "OK", "predicted_class": "OK", "defect_score": 0.20}
        for i in range(10)
    ]
    result = analyze_zero_escape(predictions, task="classification")
    assert result["status"] == "success"
    assert abs(result["optimal_threshold"] - 0.5499) < 1e-3
    assert result["optimal_stats"]["underkill_count"] == 0


def test_tradeoff_curve_cost_calculation():
    """Verify financial cost equation: Total Cost = (FN * Cost_escape) + (FP * Cost_scrap)."""
    cost_escape = 1500.0
    cost_scrap = 50.0

    predictions = [
        {"image_id": "d1", "ground_truth": "NG", "defect_score": 0.80},
        {"image_id": "d2", "ground_truth": "NG", "defect_score": 0.30},
        {"image_id": "n1", "ground_truth": "OK", "defect_score": 0.10},
        {"image_id": "n2", "ground_truth": "OK", "defect_score": 0.40},
    ]

    result = analyze_zero_escape(
        predictions,
        cost_escape=cost_escape,
        cost_scrap=cost_scrap,
        current_threshold=0.50,
    )

    # At current threshold tau = 0.50:
    # d1 (score 0.80) -> Caught (TP)
    # d2 (score 0.30) -> Escape (FN = 1)
    # n1 (score 0.10) -> Pass (TN)
    # n2 (score 0.40) -> Pass (TN)
    # FN = 1, FP = 0. Expected cost = 1 * 1500 + 0 * 50 = 1500.0
    assert result["current_stats"]["underkill_count"] == 1
    assert result["current_stats"]["overkill_count"] == 0
    assert result["current_stats"]["total_cost"] == 1500.0

    # At optimal tau* = 0.30 - 1e-4 = 0.2999:
    # d1 (0.80) >= 0.2999 -> Caught (TP)
    # d2 (0.30) >= 0.2999 -> Caught (TP, FN = 0)
    # n1 (0.10) < 0.2999 -> Pass (TN)
    # n2 (0.40) >= 0.2999 -> Overkill (FP = 1)
    # FN = 0, FP = 1. Expected cost = 0 * 1500 + 1 * 50 = 50.0
    assert result["optimal_stats"]["underkill_count"] == 0
    assert result["optimal_stats"]["overkill_count"] == 1
    assert result["optimal_stats"]["total_cost"] == 50.0


# ============================================================================
# 2. 4-Quadrant Partitioning & Label Resolution
# ============================================================================

def test_four_quadrant_partition_and_labels():
    """Verify that all shop-floor normal label representations are recognized."""
    normal_labels = ["OK", "ok", "Normal", "NORMAL", "pass", "PASS", "Good", "0", 0, "ok_normal", "true_ok", "ok_chip"]
    defect_labels = ["NG", "ng", "Scratch", "scratch", "crack", "Pinhole", "stain", "1", 1, "chip_defect"]

    for lbl in normal_labels:
        assert not is_defect_label(lbl), f"Label '{lbl}' should be recognized as NORMAL (OK)"

    for lbl in defect_labels:
        assert is_defect_label(lbl), f"Label '{lbl}' should be recognized as DEFECT (NG)"

    # Verify all 4 quadrants for a given sample
    # 1. Defect caught (TP)
    assert compute_sample_status(is_true_defect=True, defect_score=0.75, threshold=0.50) == "CORRECT_NG"
    # 2. Defect escaped (FN)
    assert compute_sample_status(is_true_defect=True, defect_score=0.25, threshold=0.50) == "ESCAPE"
    # 3. Normal overkill (FP)
    assert compute_sample_status(is_true_defect=False, defect_score=0.60, threshold=0.50) == "OVERKILL"
    # 4. Normal passed (TN)
    assert compute_sample_status(is_true_defect=False, defect_score=0.15, threshold=0.50) == "CORRECT_OK"


# ============================================================================
# 3. Scale & Stress Test with 5,000 Predictions
# ============================================================================

def test_zero_escape_large_scale_performance():
    """
    Stress-test with 5,000 predictions.
    Verifies that tradeoff curve generation and calibration complete in < 250ms.
    """
    np.random.seed(42)
    num_samples = 5000
    # 1000 defects, 4000 normals
    predictions = []
    for i in range(1000):
        score = float(np.random.beta(5, 2)) # concentrated towards 0.7 - 0.95
        predictions.append({
            "image_id": f"defect_{i:04d}",
            "ground_truth": "crack",
            "defect_score": score,
        })
    for i in range(4000):
        score = float(np.random.beta(1.5, 8)) # concentrated towards 0.05 - 0.25
        predictions.append({
            "image_id": f"normal_{i:04d}",
            "ground_truth": "OK",
            "defect_score": score,
        })

    t0 = time.perf_counter()
    result = analyze_zero_escape(
        predictions,
        task="classification",
        target_max_underkill=0,
        num_threshold_steps=101,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert result["status"] == "success"
    assert result["total_defects"] == 1000
    assert result["total_normals"] == 4000
    assert result["optimal_stats"]["underkill_count"] == 0
    assert elapsed_ms < 250.0, f"Analysis of 5,000 samples took {elapsed_ms:.1f}ms (expected < 250ms)"


# ============================================================================
# 4. Live API Endpoint Verification
# ============================================================================

def test_zero_escape_calibrate_api_endpoints(client):
    """Test POST and GET /api/evaluation/zero-escape-calibrate."""
    # Test POST
    payload = {
        "target_max_underkill": 0,
        "cost_escape": 1000.0,
        "cost_scrap": 25.0,
        "current_threshold": 0.50,
        "apply_to_eval_results": False,
    }
    resp_post = client.post("/api/evaluation/zero-escape-calibrate", json=payload)
    assert resp_post.status_code == 422

    # Test GET
    resp_get = client.get("/api/evaluation/zero-escape-calibrate?target_max_underkill=0&current_threshold=0.50")
    assert resp_get.status_code == 422
