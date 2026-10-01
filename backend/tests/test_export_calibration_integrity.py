"""Exported thresholds use an evaluation calibration only where it still applies at runtime."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

from backend.engine import exporter
from backend.engine.calibration_evidence import mark_calibration_evidence
from backend.engine.zero_escape_analyzer import analyze_zero_escape

CALIBRATED_AT = "2026-10-01T09:00:00Z"
JOB_ID = "job_1234567890_abcdef"


class FixedProbabilities(torch.nn.Module):
    """Classifier whose softmax output is the given probabilities for every image."""

    def __init__(self, probabilities):
        super().__init__()
        self.register_buffer("logits", torch.log(torch.tensor(probabilities, dtype=torch.float32)))

    def forward(self, x):
        return self.logits + 0.0 * x.mean(dim=(1, 2, 3)).unsqueeze(1)


class BrightSpotSegmenter(torch.nn.Module):
    """Foreground probability is near 1 on white pixels and near 0 on black pixels."""

    def __init__(self):
        super().__init__()
        self.conv = torch.nn.Conv2d(3, 2, kernel_size=1)
        with torch.no_grad():
            self.conv.weight.zero_()
            self.conv.bias.zero_()
            self.conv.weight[1].fill_(10.0)
            self.conv.bias[1] = -15.0

    def forward(self, x):
        return self.conv(x)


class BinaryPatchLogits(torch.nn.Module):
    def forward(self, x):
        value = torch.sigmoid(x.mean(dim=(1, 2, 3)))
        return torch.stack((1.0 - value, value), dim=1)


def _row(name, truth, predicted, confidence, **extra):
    return {"image_id": name, "file_name": f"{name}.png", "file_path": f"/data/val/{name}.png",
            "ground_truth": truth, "predicted_class": predicted, "confidence": confidence, **extra}


BINARY = [_row("ok_0", "OK", "OK", 0.9), _row("ok_1", "OK", "NG", 0.6),
          _row("ng_0", "NG", "NG", 0.8), _row("ng_1", "NG", "OK", 0.7)]

# A true NG predicted as one of two normal classes: evaluation scores it 1 - P(ok) = 0.5,
# while the exported runtime scores 1 - (P(ok) + P(good)) = 0.2.
MULTI_NORMAL = [
    _row("ng_a", "ng", "ok", 0.50, class_scores={"ok": 0.50, "good": 0.30, "ng": 0.20}),
    _row("ng_b", "ng", "ng", 0.90, class_scores={"ok": 0.05, "good": 0.05, "ng": 0.90}),
    _row("ok_a", "ok", "ok", 0.95, class_scores={"ok": 0.95, "good": 0.03, "ng": 0.02}),
    _row("ok_b", "good", "good", 0.90, class_scores={"ok": 0.05, "good": 0.90, "ng": 0.05}),
]

SEGMENTATION = [_row("seg_ng", "defect", "defect", 0.97, defect_score=0.97),
                _row("seg_ok", "background", "background", 0.95, defect_score=0.30)]

ANOMALY = [_row("good_0", "good", "good", 0.2), _row("bad_0", "anomaly", "anomaly", 0.9)]

DINO_META = {"task": "anomaly", "classes": ["good", "anomaly"], "detector_type": "dino_synthetic",
             "patch_size": 32, "stride": 16, "image_size": [32, 32], "anomaly_threshold": 0.8,
             "map_semantics": "patch_score"}


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _evaluation(checkpoint, task, predictions, threshold, split="val", checkpoint_sha256=None):
    payload = {"job_id": JOB_ID, "task": task, "evaluation_contract_version": 2,
               "metrics": {"evaluated_split": split, "selection_overlap": split == "val"},
               "test_predictions": predictions,
               "binding": {"source_dataset_path": "/data/source", "dataset_fingerprint": "d" * 64,
                           "checkpoint_sha256": checkpoint_sha256 or _sha256(checkpoint)}}
    return mark_calibration_evidence(payload, threshold, CALIBRATED_AT)


def _export(tmp_path, monkeypatch, model, meta, evaluation=None, anomaly_obj=None,
            export_format="torchscript", resolution=32):
    job = tmp_path / "job"
    job.mkdir()
    checkpoint = job / "best_model.pt"
    torch.save(meta, checkpoint)
    if evaluation is not None:
        content = evaluation(checkpoint) if callable(evaluation) else evaluation
        (job / "eval_results.json").write_text(content if isinstance(content, str) else json.dumps(content),
                                               encoding="utf-8")
    monkeypatch.setattr(exporter, "locate_checkpoint", lambda _: checkpoint)
    monkeypatch.setattr(exporter, "load_checkpoint_and_reconstruct_model", lambda _: (model, meta, anomaly_obj))
    receipt = exporter.export_runtime_package(job_id=JOB_ID, export_format=export_format, resolution=resolution,
                                              output_base_dir=tmp_path / "out")
    directory = Path(receipt["package_path"])
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    readme = (directory / "README_DEPLOY.md").read_text(encoding="utf-8")
    return receipt, directory, config, readme


def _inspector(directory, monkeypatch):
    monkeypatch.syspath_prepend(str(directory))
    name = f"exported_infer_{abs(hash(str(directory)))}"
    spec = importlib.util.spec_from_file_location(name, directory / "infer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules.pop(name, None)
    return module.StandaloneInspector(config_path=str(directory / "config.json"))


def _assert_no_guarantee_claim(readme):
    lowered = readme.lower()
    assert "zero-escape" not in lowered and "zero escape" not in lowered and "guarantee" not in lowered


@pytest.mark.parametrize("export_format", ["torchscript", "onnx"])
def test_binary_classification_exports_the_verified_calibrated_threshold(tmp_path, monkeypatch, export_format):
    meta = {"task": "classification", "classes": ["OK", "NG"], "image_size": [32, 32], "optimal_threshold": 0.65}
    receipt, directory, config, readme = _export(
        tmp_path, monkeypatch, FixedProbabilities([0.7, 0.3]), meta,
        lambda checkpoint: _evaluation(checkpoint, "classification", BINARY, 0.25), export_format=export_format)

    assert config["optimal_threshold"] == 0.25
    assert config["zero_underkill_calibrated"] is True
    assert config["threshold_source"] == "evaluation_calibration"
    assert config["calibration_not_transferable"] is None
    calibration = config["threshold_calibration"]
    assert calibration["evaluated_split"] == "val" and calibration["selection_overlap"] is True
    assert calibration["prediction_count"] == 4
    assert calibration["independent_test_required"] is True
    assert calibration["independence_certified"] is False
    assert receipt["optimal_threshold"] == 0.25
    assert receipt["zero_underkill_calibrated"] is True

    result = _inspector(directory, monkeypatch).inspect(np.zeros((32, 32, 3), dtype=np.uint8))
    assert result["optimal_threshold"] == 0.25
    assert result["defect_score"] == pytest.approx(0.3, abs=1e-4)
    assert result["verdict"] == "NG"
    assert "not an independent test" in readme
    _assert_no_guarantee_claim(readme)


def test_multi_normal_classification_keeps_the_saved_threshold(tmp_path, monkeypatch):
    analysis = analyze_zero_escape(MULTI_NORMAL, task="classification")
    tau = analysis["optimal_threshold"]
    assert analysis["optimal_stats"]["underkill_count"] == 0
    meta = {"task": "classification", "classes": ["ok", "good", "ng"], "image_size": [32, 32], "optimal_threshold": 0.65}
    receipt, directory, config, readme = _export(
        tmp_path, monkeypatch, FixedProbabilities([0.5, 0.3, 0.2]), meta,
        lambda checkpoint: _evaluation(checkpoint, "classification", MULTI_NORMAL, tau))

    assert config["optimal_threshold"] == 0.65
    assert config["zero_underkill_calibrated"] is False
    assert config["threshold_source"] == "saved_model_threshold"
    assert config["calibration_not_transferable"] == "classification_requires_one_normal_one_defect"
    assert "threshold_calibration" not in config
    assert receipt["optimal_threshold"] == 0.65
    # The exported runtime scores the evaluated NG sample below the evaluation-space threshold.
    runtime = _inspector(directory, monkeypatch).inspect(np.zeros((32, 32, 3), dtype=np.uint8))
    assert runtime["defect_score"] == pytest.approx(0.2, abs=1e-4)
    assert runtime["defect_score"] < tau
    assert "classification_requires_one_normal_one_defect" in readme
    _assert_no_guarantee_claim(readme)


def test_segmentation_area_rule_keeps_the_saved_threshold(tmp_path, monkeypatch):
    analysis = analyze_zero_escape(SEGMENTATION, task="segmentation")
    tau = analysis["optimal_threshold"]
    assert analysis["optimal_stats"]["underkill_count"] == 0
    meta = {"task": "segmentation", "classes": ["background", "defect"], "image_size": [32, 32], "optimal_threshold": 0.5}
    _, directory, config, readme = _export(
        tmp_path, monkeypatch, BrightSpotSegmenter().eval(), meta,
        lambda checkpoint: _evaluation(checkpoint, "segmentation", SEGMENTATION, tau))

    assert config["optimal_threshold"] == 0.5
    assert config["zero_underkill_calibrated"] is False
    assert config["calibration_not_transferable"] == "segmentation_area_rule"
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[10:12, 10:12] = 255
    runtime = _inspector(directory, monkeypatch).inspect(image)
    # A 4-pixel defect scores above the evaluation threshold but stays below the 8-pixel area rule.
    assert runtime["defect_score"] > tau
    assert runtime["defect_area_px"] == 4
    assert runtime["verdict"] == "OK"
    assert "segmentation_area_rule" in readme
    _assert_no_guarantee_claim(readme)


def test_dino_anomaly_records_its_saved_model_threshold_not_a_calibration(tmp_path, monkeypatch):
    _, _, config, readme = _export(
        tmp_path, monkeypatch, BinaryPatchLogits(), dict(DINO_META),
        lambda checkpoint: _evaluation(checkpoint, "anomaly", ANOMALY, 0.3), anomaly_obj=object())

    assert config["optimal_threshold"] == 0.8
    assert config["zero_underkill_calibrated"] is False
    assert config["threshold_source"] == "saved_model_threshold"
    assert config["calibration_not_transferable"] == "anomaly_uses_saved_model_threshold"
    assert "threshold_calibration" not in config
    _assert_no_guarantee_claim(readme)


BINARY_META = {"task": "classification", "classes": ["OK", "NG"], "image_size": [32, 32], "optimal_threshold": 0.65}


def _tampered(change):
    def build(checkpoint):
        payload = _evaluation(checkpoint, "classification", BINARY, 0.25)
        change(payload)
        return payload
    return build


def _old_flags_only(checkpoint):
    return {"job_id": JOB_ID, "task": "classification", "zero_underkill_calibrated": True,
            "optimal_threshold": 0.25, "test_predictions": BINARY}


@pytest.mark.parametrize("evaluation, reason", [
    (_old_flags_only, "calibration_evidence_missing"),
    (_tampered(lambda p: p.pop("calibration_evidence")), "calibration_evidence_missing"),
    (_tampered(lambda p: p["calibration_evidence"].update(version=2)), "calibration_evidence_missing"),
    (_tampered(lambda p: p["calibration_evidence"].update(version=True)), "calibration_evidence_missing"),
    (_tampered(lambda p: p["calibration_evidence"].update(version=1.0)), "calibration_evidence_missing"),
    (_tampered(lambda p: p["calibration_evidence"].update(task="detection")), "calibration_evidence_mismatch"),
    (_tampered(lambda p: p.update(task="detection")), "calibration_evidence_mismatch"),
    (_tampered(lambda p: (p.update(task="detection"), p["calibration_evidence"].update(task="detection"))),
     "calibration_evidence_mismatch"),
    (_tampered(lambda p: p["calibration_evidence"].update(threshold_space="raw_score")), "calibration_evidence_mismatch"),
    (_tampered(lambda p: p.update(evaluation_contract_version=1)), "evaluation_evidence_invalid"),
    (_tampered(lambda p: p["test_predictions"][0].update(confidence=0.91)), "calibration_evidence_mismatch"),
    (_tampered(lambda p: p["test_predictions"].pop()), "calibration_evidence_mismatch"),
    (_tampered(lambda p: p["binding"].update(dataset_fingerprint="e" * 64)), "calibration_evidence_mismatch"),
    (_tampered(lambda p: p.pop("binding")), "calibration_evidence_mismatch"),
    (_tampered(lambda p: p.update(optimal_threshold=0.2)), "calibrated_threshold_mismatch"),
    (lambda checkpoint: _evaluation(checkpoint, "classification", BINARY, 0.25, checkpoint_sha256="0" * 64),
     "checkpoint_binding_mismatch"),
    ("{not json", "evaluation_results_unreadable"),
])
def test_stale_or_tampered_calibration_is_not_exported(tmp_path, monkeypatch, evaluation, reason):
    receipt, _, config, readme = _export(tmp_path, monkeypatch, FixedProbabilities([0.7, 0.3]), dict(BINARY_META), evaluation)
    assert config["optimal_threshold"] == 0.65
    assert config["zero_underkill_calibrated"] is False
    assert config["threshold_source"] == "saved_model_threshold"
    assert config["calibration_not_transferable"] == reason
    assert "threshold_calibration" not in config
    assert receipt["calibration_not_transferable"] == reason
    assert reason in readme
    _assert_no_guarantee_claim(readme)


@pytest.mark.parametrize("evaluation", [None, lambda checkpoint: {
    **{key: value for key, value in _evaluation(checkpoint, "classification", BINARY, 0.25).items()
       if key not in ("zero_underkill_calibrated", "calibration_evidence", "optimal_threshold", "calibrated_at")}}])
def test_uncalibrated_export_uses_the_saved_threshold_without_a_rejection_reason(tmp_path, monkeypatch, evaluation):
    _, _, config, readme = _export(tmp_path, monkeypatch, FixedProbabilities([0.7, 0.3]), dict(BINARY_META), evaluation)
    assert config["optimal_threshold"] == 0.65
    assert config["zero_underkill_calibrated"] is False
    assert config["threshold_source"] == "saved_model_threshold"
    assert config["calibration_not_transferable"] is None
    assert "saved model threshold" in readme
    _assert_no_guarantee_claim(readme)


def test_export_time_class_rule_is_the_packaged_runtime_rule(tmp_path, monkeypatch):
    _, directory, _, _ = _export(tmp_path, monkeypatch, FixedProbabilities([0.7, 0.3]), dict(BINARY_META))
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("packaged_rule_infer", directory / "infer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = ["OK", "NG", "good", "Normal_OK", "Defect_NG", "background", "bg", "nondefect", "정상", "양품", "합격",
             "scratch", "component_ok", "review", "", "0"]
    assert [exporter.runtime_is_defect_class(name) for name in names] == [bool(module.is_defect_class(name)) for name in names]


@pytest.mark.parametrize("probabilities, verdict", [([0.9, 0.1], "OK"), ([0.2, 0.8], "NG")])
def test_korean_pass_class_is_normal_in_the_saved_runtime(tmp_path, monkeypatch, probabilities, verdict):
    meta = {"task": "classification", "classes": ["합격", "불량"], "image_size": [32, 32], "optimal_threshold": 0.5}
    _, directory, config, _ = _export(tmp_path, monkeypatch, FixedProbabilities(probabilities), meta)
    result = _inspector(directory, monkeypatch).inspect(np.zeros((32, 32, 3), dtype=np.uint8))
    assert config["classes"] == ["합격", "불량"]
    assert result["defect_score"] == pytest.approx(probabilities[1], abs=1e-4)
    assert result["verdict"] == verdict
