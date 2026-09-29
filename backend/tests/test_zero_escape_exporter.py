"""
backend/tests/test_zero_escape_exporter.py

Comprehensive Test Suite for Milestone M9:
  1. test_zero_escape_analyzer_with_real_predictions
     - Verifies tau* calculation, zero-underkill guarantee, and tradeoff curve monotonicity.
  2. test_defect_score_ordering_and_escape_detection
     - Verifies true defect misclassifications produce correct Escape status.
  3. test_no_random_beta_fallback
     - Verifies zero usage of random beta scores and authentic evaluation linkage.
  4. test_authentic_model_export_classification
     - Verifies export loads real checkpoint weights matching PyTorch outputs in ONNX & TorchScript.
  5. test_standalone_infer_script_execution
     - Executes generated infer.py via subprocess on test image, verifying exit code 0 and valid JSON.
  6. test_authentic_model_export_segmentation_and_anomaly
     - Verifies multi-task export for segmentation and anomaly detection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np
import onnxruntime as ort
import pytest
import torch
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.engine.classification import create_classification_model
from backend.engine.detection.model import create_detection_model
from backend.engine.segmentation import build_segmentation_model
from backend.engine.anomaly import PaDiMDetector
from backend.engine.zero_escape_analyzer import (
    analyze_zero_escape,
    calculate_optimal_zero_underkill_threshold,
    compute_sample_defect_score,
    compute_sample_status,
    is_defect_label,
)
from backend.engine.exporter import (
    export_runtime_package,
    load_checkpoint_and_reconstruct_model,
    locate_checkpoint,
)


@pytest.fixture
def client(tmp_path):
    app = create_app(project_dir=str(tmp_path))
    return TestClient(app)



# ============================================================================
# 1. Zero-Escape Analyzer & Tradeoff Curve Tests
# ============================================================================

def test_zero_escape_analyzer_with_real_predictions():
    """
    Verifies tau* calculation, zero underkill guarantee, and
    tradeoff curve monotonicity on authentic prediction inputs.
    """
    # Create realistic test predictions:
    # 10 Defective items with varying defect scores
    # 20 Normal items with low defect scores
    predictions: List[Dict[str, Any]] = []

    # Defect scores for true NGs: min is 0.35
    ng_scores = [0.35, 0.42, 0.60, 0.75, 0.88, 0.91, 0.94, 0.96, 0.98, 0.99]
    for i, s in enumerate(ng_scores):
        predictions.append({
            "image_id": f"defect_{i:03d}",
            "file_name": f"defect_{i:03d}.png",
            "ground_truth": "NG_scratch",
            "predicted_class": "NG_scratch" if s >= 0.5 else "OK",
            "confidence": s if s >= 0.5 else (1.0 - s),
            "defect_score": s,
        })

    # Normal scores: all <= 0.30 except 2 overkills at 0.38 and 0.45
    ok_scores = [0.01, 0.02, 0.04, 0.05, 0.07, 0.09, 0.11, 0.14, 0.16, 0.18,
                 0.20, 0.22, 0.24, 0.25, 0.27, 0.29, 0.30, 0.31, 0.38, 0.45]
    for i, s in enumerate(ok_scores):
        predictions.append({
            "image_id": f"normal_{i:03d}",
            "file_name": f"normal_{i:03d}.png",
            "ground_truth": "OK",
            "predicted_class": "OK" if s < 0.5 else "NG_scratch",
            "confidence": (1.0 - s) if s < 0.5 else s,
            "defect_score": s,
        })

    result = analyze_zero_escape(
        predictions=predictions,
        task="classification",
        target_max_underkill=0,
        cost_escape=1000.0,
        cost_scrap=20.0,
        current_threshold=0.50,
        num_threshold_steps=101,
    )

    assert result["status"] == "success"
    assert result["total_defects"] == 10
    assert result["total_normals"] == 20

    # Minimum defect score among true defects is 0.35
    # tau* = min(defects) - 1e-4 = 0.3499
    expected_tau_star = round(0.35 - 1e-4, 4)
    assert abs(result["optimal_threshold"] - expected_tau_star) < 1e-3

    # ZERO underkill guarantee: at optimal threshold, underkill must be strictly 0
    opt_stats = result["optimal_stats"]
    assert opt_stats["underkill_count"] == 0
    assert opt_stats["underkill_rate"] == 0.0

    # Overkills at tau* = 0.3499: normals with score >= 0.3499 are 0.38 and 0.45 (2 overkills)
    assert opt_stats["overkill_count"] == 2
    assert opt_stats["overkill_rate"] == 10.0  # 2 / 20 * 100%

    # Verify Tradeoff Curve Monotonicity:
    curve = result["tradeoff_curve"]
    assert len(curve) >= 50

    for i in range(len(curve) - 1):
        th_a = curve[i]["threshold"]
        th_b = curve[i + 1]["threshold"]
        assert th_a <= th_b

        # As threshold increases:
        # Underkill count (FN) must be non-decreasing
        assert curve[i]["underkill_count"] <= curve[i + 1]["underkill_count"]
        # Overkill count (FP) must be non-increasing
        assert curve[i]["overkill_count"] >= curve[i + 1]["overkill_count"]

    # Verify Sample Details:
    sample_details = result["sample_details"]
    assert len(sample_details) == 30
    for s in sample_details:
        if s["is_defect"]:
            # With tau*, all defects MUST be caught
            assert s["status"] == "CORRECT_NG"
        else:
            if s["defect_score"] >= result["optimal_threshold"]:
                assert s["status"] == "OVERKILL"
            else:
                assert s["status"] == "CORRECT_OK"


# ============================================================================
# 2. Defect Score Formulation & Escape Detection Tests
# ============================================================================

def test_defect_score_ordering_and_escape_detection():
    """
    Verifies that true defect misclassifications produce low defect scores and correct ESCAPE status.
    """
    # Case 1: True Defect misclassified as OK with 0.95 confidence
    p_false_negative = {
        "ground_truth": "NG_crack",
        "predicted_class": "OK",
        "confidence": 0.95,
    }
    score_fn = compute_sample_defect_score(p_false_negative, task="classification")
    # defect_score = 1.0 - 0.95 = 0.05
    assert abs(score_fn - 0.05) < 1e-4

    # At normal operating threshold tau = 0.50:
    status_at_05 = compute_sample_status(
        is_true_defect=is_defect_label(p_false_negative["ground_truth"]),
        defect_score=score_fn,
        threshold=0.50,
    )
    assert status_at_05 == "ESCAPE", "A misclassified defect must be flagged as ESCAPE at standard threshold!"

    # Case 2: True Defect correctly classified as NG with 0.92 confidence
    p_true_positive = {
        "ground_truth": "NG_crack",
        "predicted_class": "NG_crack",
        "confidence": 0.92,
    }
    score_tp = compute_sample_defect_score(p_true_positive, task="classification")
    assert abs(score_tp - 0.92) < 1e-4
    assert compute_sample_status(True, score_tp, 0.50) == "CORRECT_NG"

    # Case 3: True Normal correctly classified as OK with 0.90 confidence
    p_true_negative = {
        "ground_truth": "OK",
        "predicted_class": "OK",
        "confidence": 0.90,
    }
    score_tn = compute_sample_defect_score(p_true_negative, task="classification")
    assert abs(score_tn - 0.10) < 1e-4
    assert compute_sample_status(False, score_tn, 0.50) == "CORRECT_OK"

    # Case 4: True Normal misclassified as NG with 0.80 confidence (Overkill)
    p_false_positive = {
        "ground_truth": "OK",
        "predicted_class": "NG_crack",
        "confidence": 0.80,
    }
    score_fp = compute_sample_defect_score(p_false_positive, task="classification")
    assert abs(score_fp - 0.80) < 1e-4
    assert compute_sample_status(False, score_fp, 0.50) == "OVERKILL"

    # Case 5: Detection flaw boxes
    p_det = {
        "ground_truth": "NG",
        "detections": [
            {"label": "OK_chip", "score": 0.98},
            {"label": "defect_crack", "score": 0.74},
        ],
    }
    score_det = compute_sample_defect_score(p_det, task="detection")
    assert abs(score_det - 0.74) < 1e-4

    # Case 6: Anomaly normalization
    p_anom = {"ground_truth": "NG", "confidence": 18.5}
    score_anom = compute_sample_defect_score(p_anom, task="anomaly", anomaly_min=2.0, anomaly_max=22.0)
    assert 0.0 <= score_anom <= 1.0
    assert abs(score_anom - (18.5 - 2.0) / 20.0) < 1e-3


# ============================================================================
# 3. Elimination of Synthetic Beta Fallback Tests
# ============================================================================

def test_no_random_beta_fallback(client):
    """
    Verifies zero usage of random beta distributions when evaluating jobs.
    Checks source files and endpoint reproducibility.
    """
    # 1. Source check: ensure np.random.beta does NOT exist in backend
    repo_root = Path(__file__).resolve().parent.parent
    eval_routes_file = repo_root / "api" / "routes_evaluation.py"
    analyzer_file = repo_root / "engine" / "zero_escape_analyzer.py"

    eval_text = eval_routes_file.read_text(encoding="utf-8")
    analyzer_text = analyzer_file.read_text(encoding="utf-8")

    assert "random.beta" not in eval_text, "routes_evaluation.py must not contain np.random.beta"
    assert "random.beta" not in analyzer_text, "zero_escape_analyzer.py must not contain np.random.beta"

    # 2. No job cannot produce an evidence-backed curve.
    resp1 = client.get("/api/evaluation/overkill-underkill")
    assert resp1.status_code == 422

    resp2 = client.get("/api/evaluation/overkill-underkill")
    assert resp2.status_code == 422

    # 3. Linkage with real eval_results.json:
    # Write a test eval_results.json to a temporary job directory and ensure the API loads it
    test_job_id = "test_real_eval_linkage_job"
    models_dir = Path("./models") / test_job_id
    models_dir.mkdir(parents=True, exist_ok=True)
    try:
        custom_preds = [
            {"image_id": "img_001", "ground_truth": "NG", "predicted_class": "NG", "confidence": 0.88},
            {"image_id": "img_002", "ground_truth": "OK", "predicted_class": "OK", "confidence": 0.95},
        ]
        with open(models_dir / "eval_results.json", "w", encoding="utf-8") as f:
            json.dump({
                "job_id": test_job_id,
                "task": "classification",
                "test_predictions": custom_preds,
            }, f)

        resp = client.get(f"/api/evaluation/overkill-underkill?job_id={test_job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sample_count"] == 2
        assert data["total_defects"] == 1
        assert data["total_normals"] == 1
        assert data["optimal_threshold"] == round(0.88 - 1e-4, 4)
    finally:
        import shutil
        if models_dir.exists():
            shutil.rmtree(models_dir)


# ============================================================================
# 4. Authentic Model Export Tests (Weights match checkpoint)
# ============================================================================

def test_authentic_model_export_classification():
    """
    Creates a real checkpoint with deterministic non-random weights,
    exports to ONNX and TorchScript, and verifies that inference outputs
    strictly match the PyTorch checkpoint model outputs.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="export_test_"))
    try:
        # 1. Create a model with specific custom weights
        torch.manual_seed(1337)
        pt_model = create_classification_model("resnet18", num_classes=2, pretrained=False)
        # Modify the head weights with distinct known values
        with torch.no_grad():
            pt_model.fc.weight.fill_(0.1234)
            pt_model.fc.bias.fill_(0.5678)
        pt_model.eval()

        ckpt_path = temp_dir / "best_model.pt"
        payload = {
            "model_state_dict": pt_model.state_dict(),
            "task": "classification",
            "classes": ["Normal_OK", "Defect_NG"],
            "backbone": "resnet18",
            "image_size": [128, 128],
            "optimal_threshold": 0.42,
        }
        torch.save(payload, ckpt_path)

        # 2. Export to ONNX
        pkg_onnx_dir = temp_dir / "pkg_onnx"
        res_onnx = export_runtime_package(
            job_id=str(ckpt_path),
            export_format="onnx",
            resolution=128,
            package_name="test_onnx_pkg",
            output_base_dir=pkg_onnx_dir,
        )
        assert res_onnx["status"] == "success"
        onnx_file = Path(res_onnx["package_path"]) / "model.onnx"
        assert onnx_file.is_file()

        # 3. Export to TorchScript
        pkg_ts_dir = temp_dir / "pkg_ts"
        res_ts = export_runtime_package(
            job_id=str(ckpt_path),
            export_format="torchscript",
            resolution=128,
            package_name="test_ts_pkg",
            output_base_dir=pkg_ts_dir,
        )
        assert res_ts["status"] == "success"
        ts_file = Path(res_ts["package_path"]) / "model.pt"
        assert ts_file.is_file()

        # 4. Compare outputs on the same test input tensor
        test_input = torch.randn(1, 3, 128, 128)
        with torch.no_grad():
            expected_output = pt_model(test_input).numpy()

        # Verify ONNX Runtime produces identical output
        ort_sess = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
        inp_name = ort_sess.get_inputs()[0].name
        onnx_output = ort_sess.run(None, {inp_name: test_input.numpy()})[0]
        np.testing.assert_allclose(expected_output, onnx_output, rtol=1e-4, atol=1e-4)

        # Verify TorchScript produces identical output
        ts_model = torch.jit.load(str(ts_file), map_location="cpu")
        ts_model.eval()
        with torch.no_grad():
            ts_output = ts_model(test_input).numpy()
        np.testing.assert_allclose(expected_output, ts_output, rtol=1e-4, atol=1e-4)

    finally:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


# ============================================================================
# 5. Standalone infer.py Execution Test via Subprocess
# ============================================================================

def test_standalone_infer_script_execution():
    """
    Executes the generated infer.py script via a standalone Python subprocess
    on a test image and verifies exit code 0 and valid JSON output.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="infer_subproc_test_"))
    try:
        # Create a lightweight checkpoint
        pt_model = create_classification_model("resnet18", num_classes=2, pretrained=False)
        pt_model.eval()
        ckpt_path = temp_dir / "best_model.pt"
        torch.save({
            "model_state_dict": pt_model.state_dict(),
            "task": "classification",
            "classes": ["OK", "NG"],
            "backbone": "resnet18",
            "image_size": [128, 128],
            "optimal_threshold": 0.35,
        }, ckpt_path)

        res = export_runtime_package(
            job_id=str(ckpt_path),
            export_format="onnx",
            resolution=128,
            package_name="test_runtime_pkg",
            output_base_dir=temp_dir,
        )
        pkg_dir = Path(res["package_path"])
        infer_script = pkg_dir / "infer.py"
        assert infer_script.is_file()

        # Create a sample test image
        sample_img_path = temp_dir / "sample.png"
        img = np.zeros((128, 128, 3), dtype=np.uint8)
        cv2.circle(img, (64, 64), 30, (200, 200, 200), -1)
        cv2.imwrite(str(sample_img_path), img)

        # 1. Execute infer.py with an image
        proc = subprocess.run(
            [sys.executable, str(infer_script), "--image", str(sample_img_path)],
            cwd=str(pkg_dir),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"infer.py failed with stderr: {proc.stderr}"
        out_json = json.loads(proc.stdout.strip())

        assert out_json["status"] == "success"
        assert out_json["verdict"] in ("OK", "NG")
        assert "defect_score" in out_json
        assert 0.0 <= out_json["defect_score"] <= 1.0
        assert out_json["optimal_threshold"] == 0.35
        assert "latency_ms" in out_json
        assert out_json["latency_ms"] > 0

        # 2. Execute infer.py in self-test mode
        proc_self = subprocess.run(
            [sys.executable, str(infer_script), "--self-test"],
            cwd=str(pkg_dir),
            capture_output=True,
            text=True,
        )
        assert proc_self.returncode == 0
        self_json = json.loads(proc_self.stdout.strip())
        assert self_json["status"] == "success"
        assert self_json["verdict"] in ("OK", "NG")

    finally:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


# ============================================================================
# 6. Multi-Task Export Tests (Segmentation & Anomaly)
# ============================================================================

def test_authentic_model_export_segmentation_and_anomaly():
    """
    Verifies authentic export for Segmentation (UNet) and Anomaly Detection (PaDiM).
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="multitask_export_"))
    try:
        # A. Segmentation
        seg_model = build_segmentation_model(model_name="unet", num_classes=2, preset="fast", pretrained=False)
        seg_model.eval()
        seg_ckpt = temp_dir / "seg_model.pt"
        torch.save({
            "model_state_dict": seg_model.state_dict(),
            "task": "segmentation",
            "classes": ["background", "defect"],
            "image_size": [128, 128],
            "preset": "fast",
        }, seg_ckpt)

        seg_res = export_runtime_package(
            job_id=str(seg_ckpt),
            export_format="onnx",
            resolution=128,
            package_name="seg_pkg",
            output_base_dir=temp_dir,
        )
        assert seg_res["status"] == "success"
        assert (Path(seg_res["package_path"]) / "model.onnx").is_file()

        # B. Anomaly (PaDiM)
        anom_detector = PaDiMDetector(backbone_name="resnet18", device="cpu", pretrained=False)
        # Mock fit distribution
        anom_detector.mean = torch.zeros(16, 16, 1, 100)
        anom_detector.cov_inv = torch.eye(100).unsqueeze(0).unsqueeze(0).repeat(16, 16, 1, 1)
        anom_detector.threshold = 3.5

        anom_ckpt = temp_dir / "anom_model.pt"
        torch.save({
            "model_state_dict": anom_detector.state_dict(),
            "task": "anomaly",
            "detector_type": "padim",
            "classes": ["OK", "NG"],
            "image_size": [128, 128],
        }, anom_ckpt)

        anom_res = export_runtime_package(
            job_id=str(anom_ckpt),
            export_format="onnx",
            resolution=128,
            package_name="anom_pkg",
            output_base_dir=temp_dir,
        )
        assert anom_res["status"] == "success"
        pkg_path = Path(anom_res["package_path"])
        assert (pkg_path / "model.onnx").is_file()
        assert (pkg_path / "anomaly_stats.pt").is_file()

    finally:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


# ============================================================================
# 7. Detection Model Export & Dynamic Batching & CLI Override Tests
# ============================================================================

def test_authentic_model_export_detection():
    """
    Verifies authentic export for Object Detection (Faster R-CNN) to both ONNX and TorchScript.
    Confirms outputs have correct names (boxes, scores, labels) and shapes.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="detection_export_"))
    try:
        det_model = create_detection_model(preset="fast", num_classes=2, pretrained=False)
        det_model.eval()
        det_ckpt = temp_dir / "det_model.pt"
        torch.save({
            "model_state_dict": det_model.state_dict(),
            "task": "detection",
            "classes": ["background", "defect"],
            "detector_preset": "fast",
            "image_size": [128, 128],
            "optimal_threshold": 0.50,
        }, det_ckpt)

        # 1. Export ONNX
        res_onnx = export_runtime_package(
            job_id=str(det_ckpt),
            export_format="onnx",
            resolution=128,
            package_name="det_onnx_pkg",
            output_base_dir=temp_dir,
        )
        assert res_onnx["status"] == "success"
        onnx_file = Path(res_onnx["package_path"]) / "model.onnx"
        assert onnx_file.is_file()

        # Verify ONNX runtime outputs
        sess = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
        out_names = [o.name for o in sess.get_outputs()]
        assert out_names == ["boxes", "scores", "labels"]
        dummy_in = np.random.randn(1, 3, 128, 128).astype(np.float32)
        onnx_out = sess.run(None, {"input": dummy_in})
        assert len(onnx_out) == 3
        # boxes: [N, 4], scores: [N], labels: [N]
        assert onnx_out[0].ndim == 2 and onnx_out[0].shape[1] == 4
        assert onnx_out[1].ndim == 1
        assert onnx_out[2].ndim == 1

        # 2. Export TorchScript
        res_ts = export_runtime_package(
            job_id=str(det_ckpt),
            export_format="torchscript",
            resolution=128,
            package_name="det_ts_pkg",
            output_base_dir=temp_dir,
        )
        assert res_ts["status"] == "success"
        ts_file = Path(res_ts["package_path"]) / "model.pt"
        assert ts_file.is_file()

        ts_model = torch.jit.load(str(ts_file), map_location="cpu")
        ts_model.eval()
        dummy_tensor = torch.randn(1, 3, 128, 128)
        with torch.no_grad():
            ts_out = ts_model(dummy_tensor)
        assert isinstance(ts_out, (tuple, list))
        assert len(ts_out) == 3
        # boxes, scores, labels
        assert ts_out[0].ndim == 2 and ts_out[0].shape[1] == 4
        assert ts_out[1].ndim == 1
        assert ts_out[2].ndim == 1

    finally:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def test_onnx_dynamic_batching_multi_batch():
    """
    Verifies that ONNX export with dynamo=False preserves dynamic batching
    and does not freeze reshape nodes to batch_size=1.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="dynamic_batch_"))
    try:
        model = create_classification_model("resnet18", num_classes=2, pretrained=False)
        model.eval()
        ckpt_path = temp_dir / "cls_model.pt"
        torch.save({
            "model_state_dict": model.state_dict(),
            "task": "classification",
            "classes": ["OK", "NG"],
            "backbone": "resnet18",
            "image_size": [128, 128],
            "optimal_threshold": 0.50,
        }, ckpt_path)

        res = export_runtime_package(
            job_id=str(ckpt_path),
            export_format="onnx",
            resolution=128,
            package_name="cls_dyn_pkg",
            output_base_dir=temp_dir,
        )
        assert res["status"] == "success"
        onnx_file = Path(res["package_path"]) / "model.onnx"
        assert onnx_file.is_file()

        sess = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
        inp_name = sess.get_inputs()[0].name

        # Test batch sizes 1, 2, 4, 8
        for b in [1, 2, 4, 8]:
            dummy = np.random.randn(b, 3, 128, 128).astype(np.float32)
            out = sess.run(None, {inp_name: dummy})[0]
            assert out.shape == (b, 2), f"Expected shape ({b}, 2) but got {out.shape}"

    finally:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def test_standalone_infer_threshold_override_and_detection():
    """
    Verifies that infer.py CLI supports --threshold-override,
    and that detection post-processing accurately extracts defect scores.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="infer_cli_test_"))
    try:
        # 1. Test --threshold-override on classification
        cls_model = create_classification_model("resnet18", num_classes=2, pretrained=False)
        cls_model.eval()
        cls_ckpt = temp_dir / "cls_model.pt"
        torch.save({
            "model_state_dict": cls_model.state_dict(),
            "task": "classification",
            "classes": ["OK", "NG"],
            "image_size": [128, 128],
            "optimal_threshold": 0.40,
        }, cls_ckpt)

        res_cls = export_runtime_package(
            job_id=str(cls_ckpt),
            export_format="onnx",
            resolution=128,
            package_name="cls_infer_pkg",
            output_base_dir=temp_dir,
        )
        pkg_cls = Path(res_cls["package_path"])
        infer_cls = pkg_cls / "infer.py"

        proc = subprocess.run(
            [sys.executable, str(infer_cls), "--self-test", "--threshold-override", "0.85"],
            cwd=str(pkg_cls),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"infer.py failed with stderr: {proc.stderr}"
        data = json.loads(proc.stdout.strip())
        assert data.get("optimal_threshold") == 0.85 or data.get("threshold") == 0.85
        assert data.get("status") == "success"

        # 2. Test Detection infer.py execution and defect score range
        det_model = create_detection_model(preset="fast", num_classes=2, pretrained=False)
        det_model.eval()
        det_ckpt = temp_dir / "det_model.pt"
        torch.save({
            "model_state_dict": det_model.state_dict(),
            "task": "detection",
            "classes": ["background", "defect"],
            "image_size": [128, 128],
            "optimal_threshold": 0.50,
        }, det_ckpt)

        res_det = export_runtime_package(
            job_id=str(det_ckpt),
            export_format="onnx",
            resolution=128,
            package_name="det_infer_pkg",
            output_base_dir=temp_dir,
        )
        pkg_det = Path(res_det["package_path"])
        infer_det = pkg_det / "infer.py"

        proc_det = subprocess.run(
            [sys.executable, str(infer_det), "--self-test"],
            cwd=str(pkg_det),
            capture_output=True,
            text=True,
        )
        assert proc_det.returncode == 0, f"infer.py for detection failed: {proc_det.stderr}"
        data_det = json.loads(proc_det.stdout.strip())
        assert data_det["status"] == "success"
        assert data_det["task"] == "detection"
        # Defect score must be normalized probability in [0, 1], NOT bounding box pixel coordinates (> 10)
        assert 0.0 <= data_det["defect_score"] <= 1.0
        assert data_det["verdict"] in ("OK", "NG")

        # 3. Test TorchScript Detection infer.py execution via subprocess
        res_det_ts = export_runtime_package(
            job_id=str(det_ckpt),
            export_format="torchscript",
            resolution=128,
            package_name="det_ts_infer_pkg",
            output_base_dir=temp_dir,
        )
        pkg_det_ts = Path(res_det_ts["package_path"])
        infer_det_ts = pkg_det_ts / "infer.py"

        proc_det_ts = subprocess.run(
            [sys.executable, str(infer_det_ts), "--self-test"],
            cwd=str(pkg_det_ts),
            capture_output=True,
            text=True,
        )
        assert proc_det_ts.returncode == 0, (
            f"infer.py for TorchScript detection failed: {proc_det_ts.stderr}"
        )
        data_det_ts = json.loads(proc_det_ts.stdout.strip())
        assert data_det_ts["status"] == "success"
        assert data_det_ts["task"] == "detection"
        assert 0.0 <= data_det_ts["defect_score"] <= 1.0
        assert data_det_ts["verdict"] in ("OK", "NG")

    finally:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def test_standalone_infer_torchscript_detection_subprocess():
    """
    Verifies standalone infer.py execution via subprocess on an exported
    TorchScript Detection model package, ensuring returncode 0 and valid
    defect score / verdict without crashing on missing torchvision::nms op.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="det_ts_infer_test_"))
    try:
        det_model = create_detection_model(
            preset="fast", num_classes=2, pretrained=False
        )
        det_model.eval()
        det_ckpt = temp_dir / "det_model.pt"
        torch.save({
            "model_state_dict": det_model.state_dict(),
            "task": "detection",
            "classes": ["background", "scratch"],
            "image_size": [128, 128],
            "optimal_threshold": 0.45,
        }, det_ckpt)

        res = export_runtime_package(
            job_id=str(det_ckpt),
            export_format="torchscript",
            resolution=128,
            package_name="det_ts_standalone_pkg",
            output_base_dir=temp_dir,
        )
        assert res["status"] == "success"
        pkg_dir = Path(res["package_path"])
        infer_py = pkg_dir / "infer.py"
        model_pt = pkg_dir / "model.pt"
        assert infer_py.is_file()
        assert model_pt.is_file()

        # 1. Run --self-test via subprocess in clean environment
        proc_self = subprocess.run(
            [sys.executable, str(infer_py), "--self-test"],
            cwd=str(pkg_dir),
            capture_output=True,
            text=True,
        )
        assert proc_self.returncode == 0, (
            f"infer.py --self-test failed with stderr:\n{proc_self.stderr}"
        )
        data_self = json.loads(proc_self.stdout.strip())
        assert data_self["status"] == "success"
        assert data_self["task"] == "detection"
        assert 0.0 <= data_self["defect_score"] <= 1.0
        assert data_self["verdict"] in ("OK", "NG")

        # 2. Run with an image file
        test_img_path = temp_dir / "test_defect.png"
        sample_img = np.zeros((128, 128, 3), dtype=np.uint8)
        cv2.rectangle(sample_img, (20, 20), (80, 80), (255, 255, 255), -1)
        cv2.imwrite(str(test_img_path), sample_img)

        proc_img = subprocess.run(
            [sys.executable, str(infer_py), "--image", str(test_img_path)],
            cwd=str(pkg_dir),
            capture_output=True,
            text=True,
        )
        assert proc_img.returncode == 0, (
            f"infer.py --image failed with stderr:\n{proc_img.stderr}"
        )
        data_img = json.loads(proc_img.stdout.strip())
        assert data_img["status"] == "success"
        assert data_img["task"] == "detection"
        assert 0.0 <= data_img["defect_score"] <= 1.0
        assert data_img["verdict"] in ("OK", "NG")
        assert data_img["image_dimensions"] == [128, 128]
        assert data_img["latency_ms"] > 0

        # 3. Dynamic threshold override
        proc_override = subprocess.run(
            [
                sys.executable,
                str(infer_py),
                "--image",
                str(test_img_path),
                "--threshold-override",
                "0.9999",
            ],
            cwd=str(pkg_dir),
            capture_output=True,
            text=True,
        )
        assert proc_override.returncode == 0
        data_override = json.loads(proc_override.stdout.strip())
        assert data_override["threshold"] == 0.9999
        assert data_override["verdict"] == "OK"

    finally:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
