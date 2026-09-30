"""Parity compares decoded probabilities while keeping inspection evidence exact."""
import copy
from pathlib import Path
import shutil

import numpy as np
import pytest

from backend.engine.flow_package_runtime import compare_flow_results
from backend.engine.segmentation_evidence import encoded_array


def _result(probability):
    classes = [{"class_id": 1, "probability": encoded_array(probability, "float32"),
                "mask": encoded_array(np.ones(probability.shape), "uint8"), "area_px": 4}]
    return {
        "final_verdict": "NG", "roi_count": 1, "defective_roi_count": 1,
        "execution_steps": [{"node_id": "inspect", "status": "completed", "artifacts": classes}],
        "crops": [{"roi_id": "roi", "verdict": "NG", "defect_score": .8,
                   "defect_area_px": 4, "segmentation_classes": classes}],
    }


def test_decoded_probability_parity_accepts_cpu_rounding_in_both_evidence_locations():
    probability = np.array([[.2, .3], [.7, .8]], dtype=np.float32)
    reference = _result(probability)
    # Actual DINOv3 CPU threads=5 vs threads=1 had maximum gap 1.7881393e-7.
    packaged = _result(probability + np.float32(1.8e-7))
    assert reference["crops"][0]["segmentation_classes"][0]["probability"]["data"] != packaged["crops"][0]["segmentation_classes"][0]["probability"]["data"]
    assert compare_flow_results(reference, packaged)["status"] == "passed"


@pytest.mark.parametrize("change", ["probability", "nonfinite", "shape", "mask", "area", "verdict", "branch"])
def test_probability_tolerance_rejects_changed_inspection_evidence(change):
    reference = _result(np.full((2, 2), .8, dtype=np.float32))
    packaged = copy.deepcopy(reference)
    evidence = packaged["crops"][0]["segmentation_classes"][0]
    if change == "probability":
        evidence["probability"] = encoded_array(np.full((2, 2), .800002), "float32")
    elif change == "nonfinite":
        evidence["probability"] = encoded_array(np.full((2, 2), np.nan), "float32")
    elif change == "shape":
        evidence["probability"]["shape"] = [1, 4]
    elif change == "mask":
        evidence["mask"] = encoded_array(np.zeros((2, 2)), "uint8")
    elif change == "area":
        packaged["crops"][0]["defect_area_px"] = 3
    elif change == "verdict":
        packaged["crops"][0]["verdict"] = "OK"
    else:
        packaged["execution_steps"][0]["selected_edge_ids"] = ["other_branch"]
    assert compare_flow_results(reference, packaged)["status"] == "mismatch"


def test_identical_nonfinite_probability_is_not_a_verified_parity_result():
    result = _result(np.full((2, 2), np.nan, dtype=np.float32))
    assert compare_flow_results(result, copy.deepcopy(result))["status"] == "mismatch"


@pytest.mark.parametrize("incomplete", [False, True])
def test_flow_export_refuses_missing_native_sdk_sources(tmp_path, monkeypatch, incomplete):
    from backend.engine import flow_package
    from backend.engine.flowchart_engine import get_single_detection_flowchart
    checkpoint = tmp_path / "model" / "best_model.pt"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"checkpoint fixture")
    _desktop_without_sdk(tmp_path, flow_package.__file__)
    if incomplete:
        native = tmp_path / "desktop/native_runtime"
        native.mkdir()
        (native / "README.md").write_text("An incomplete desktop resource bundle")
    monkeypatch.setattr(flow_package, "__file__", str(tmp_path / "desktop/backend/engine/flow_package.py"))
    with pytest.raises(ValueError, match="Native SDK source.*missing"):
        flow_package.build_flow_package(pipeline=get_single_detection_flowchart("job_detector"),
            checkpoints={"job_detector": checkpoint}, output_base_dir=tmp_path / "exports", package_name="missing_sdk")
    assert not (tmp_path / "exports/missing_sdk").exists()


@pytest.mark.parametrize("incomplete", [False, True])
def test_gan_export_refuses_missing_native_sdk_sources(tmp_path, monkeypatch, incomplete):
    import hashlib
    import json
    from backend.engine import gan_package_runtime
    checkpoint = tmp_path / "model/best_model.pt"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"GAN checkpoint fixture")
    checkpoint.with_name("model_meta.json").write_text(json.dumps({
        "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
    }))
    _desktop_without_sdk(tmp_path, gan_package_runtime.__file__)
    if incomplete:
        native = tmp_path / "desktop/native_runtime"
        native.mkdir()
        (native / "README.md").write_text("An incomplete desktop resource bundle")
    monkeypatch.setattr(gan_package_runtime, "__file__", str(tmp_path / "desktop/backend/engine/gan_package_runtime.py"))
    with pytest.raises(ValueError, match="Native SDK source.*missing"):
        gan_package_runtime.build_generator_package(checkpoint, tmp_path / "generator")
    assert not (tmp_path / "generator").exists()


def _desktop_without_sdk(tmp_path, module_path):
    engine = Path(module_path).parent
    shutil.copytree(engine, tmp_path / "desktop/backend/engine", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(engine.parents[1] / "examples", tmp_path / "desktop/examples")
