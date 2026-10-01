"""Holdout identity and unavailable anomaly metrics are explicit evidence."""
from pathlib import Path
import json

import pytest
import torch
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_evaluation as evaluation
from backend.engine.anomaly.metrics import compute_anomaly_metrics
from backend.engine.dataset_loaders import ClassificationDataset


def _classes(root: Path, count: int):
    for name in ("ok", "ng"):
        directory = root / name
        directory.mkdir(parents=True)
        for index in range(count):
            Image.new("RGB", (16, 16), "white").save(directory / f"{name}_{index}.png")


def test_flat_class_test_does_not_reuse_train_or_validation(tmp_path):
    _classes(tmp_path, 10)
    train = {p for p, _ in ClassificationDataset(tmp_path, split="train").samples}
    val = {p for p, _ in ClassificationDataset(tmp_path, split="val").samples}
    test = {p for p, _ in ClassificationDataset(tmp_path, split="test").samples}
    assert len(train) == 16 and len(val) == 4
    assert train.isdisjoint(val)
    assert test == set(), "No independent test was declared in this flat folder"


def test_classification_without_holdout_rejects_before_loading_model(tmp_path, monkeypatch):
    _classes(tmp_path, 1)
    monkeypatch.setattr(evaluation.torch, "load", lambda *a, **k: pytest.fail("Training images must not be evaluated"))
    with pytest.raises(HTTPException) as caught:
        evaluation._evaluate_classification(tmp_path / "model.pt", {}, tmp_path, torch.device("cpu"))
    assert caught.value.status_code == 422


def test_flat_class_validation_requires_a_saved_partition(tmp_path, monkeypatch):
    _classes(tmp_path, 10)
    monkeypatch.setattr(evaluation.torch, "load", lambda *a, **k: pytest.fail("A mutable automatic split is not saved evaluation evidence"))
    with pytest.raises(HTTPException) as caught:
        evaluation._evaluate_classification(tmp_path / "model.pt", {}, tmp_path, torch.device("cpu"))
    assert caught.value.status_code == 422


def test_validation_evaluation_records_selection_overlap(tmp_path, monkeypatch):
    _classes(tmp_path / "train", 3)
    _classes(tmp_path / "val", 2)
    checkpoint = tmp_path / "model.pt"
    torch.save({"model_state_dict": {}, "classes": ["ok", "ng"]}, checkpoint)

    class Detector(torch.nn.Module):
        def forward(self, images):
            return torch.tensor([[1., 0.]]).repeat(len(images), 1)

    monkeypatch.setattr(evaluation, "create_classification_model", lambda **kwargs: Detector())
    result = evaluation._evaluate_classification(checkpoint, {"image_size": [16, 16]}, tmp_path, torch.device("cpu"))
    assert len(result["test_predictions"]) == 4
    assert all(Path(row["file_path"]).parent.parent.name == "val" for row in result["test_predictions"])
    assert result["metrics"]["evaluated_split"] == "val"
    assert result["metrics"]["selection_overlap"] is True


@pytest.mark.parametrize("task", ["detection", "segmentation"])
def test_unpartitioned_paired_images_cannot_silently_evaluate_training(tmp_path, task):
    (tmp_path / "images").mkdir()
    Image.new("RGB", (16, 16)).save(tmp_path / "images" / "a.png")
    with pytest.raises(HTTPException) as caught:
        evaluation._paired_evaluation_paths(tmp_path, task)
    assert caught.value.status_code == 422


@pytest.mark.parametrize("labels", [[1, 1, 1], [0, 0, 0], []])
def test_single_class_anomaly_has_no_auroc_or_searched_threshold(labels):
    scores = [.7, .8, .9] if labels else []
    result = compute_anomaly_metrics(scores, labels, fixed_threshold=.75)
    assert result["image_auroc"] is None
    assert result["optimal_threshold"] is None
    assert result["active_threshold"] == .75
    assert result["threshold_search_available"] is False
    assert result["threshold_basis"] == "model_threshold_single_class"


def test_ng_only_api_evaluation_uses_model_threshold_and_reports_missing_normals(tmp_path, monkeypatch):
    paths = []
    for index in range(3):
        path = tmp_path / f"ng_{index}.png"
        Image.new("RGB", (16, 16)).save(path)
        paths.append(path)

    class Dataset:
        split = "test"
        samples = [(p, 1, None) for p in paths]
        def __len__(self):
            return len(self.samples)

    class Detector:
        threshold = .5
        def __call__(self, batch):
            return torch.full((1, 1, 2, 2), .8), torch.tensor([.8])

    monkeypatch.setattr(evaluation, "AnomalyDataset", lambda **kwargs: Dataset())
    monkeypatch.setattr(evaluation, "reconstruct_anomaly_detector", lambda *a, **k: Detector())
    checkpoint = tmp_path / "model.pt"
    torch.save({"model_state_dict": {}}, checkpoint)
    result = evaluation._evaluate_anomaly(checkpoint, {"image_size": [16, 16]}, tmp_path, torch.device("cpu"))
    metrics = result["metrics"]
    assert metrics["image_auroc"] is None
    assert metrics["optimal_threshold"] is None
    assert metrics["active_threshold"] == .5
    assert metrics["threshold_basis"] == "model_threshold_single_class"
    assert metrics["threshold_search_available"] is False
    assert metrics["evaluated_split"] == "test"
    assert result["confusion_matrix"]["matrix"] == [[0, 0], [0, 3]]


def test_pre_integrity_cached_evaluation_is_recomputed(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _classes(source, 1)
    image = source / "ok" / "ok_0.png"
    output = tmp_path / "models" / "job_123_integrity"
    output.mkdir(parents=True)
    checkpoint = output / "best_model.pt"
    checkpoint.write_bytes(b"checkpoint boundary fixture")
    prediction = {"file_path": str(image), "ground_truth": "ok", "predicted_class": "ok", "confidence": .9}
    cached = {"metrics": {"accuracy": 1.}, "confusion_matrix": {"cell_samples": {"ok:ok": [str(image)]}},
              "test_predictions": [prediction], "task": "classification"}
    (output / "eval_results.json").write_text(json.dumps(cached))
    monkeypatch.setattr(evaluation, "_resolve_job_artifacts", lambda **kwargs: (output, checkpoint, {}, "classification", output.name, source))
    calls = []
    def recompute(*args):
        calls.append(True)
        return {**cached, "metrics": {"accuracy": .5, "evaluated_split": "test", "selection_overlap": False}}
    monkeypatch.setattr(evaluation, "_evaluate_classification", recompute)
    result = evaluation.run_or_load_evaluation(job_id=output.name)
    assert calls == [True], "Old results may contain training images or fabricated single-class AUROC"
    assert result["metrics"]["accuracy"] == .5
    assert result["evaluation_contract_version"] == 2


def _calibration_payload(tmp_path):
    _classes(tmp_path / "test", 1)
    return {"evaluation_contract_version": 2, "task": "classification",
            "metrics": {"evaluated_split": "test", "selection_overlap": False},
            "test_predictions": [
                {"file_path": str(tmp_path / "test" / "ng" / "ng_0.png"), "ground_truth": "ng", "predicted_class": "ng", "confidence": .88, "defect_score": .88},
                {"file_path": str(tmp_path / "test" / "ok" / "ok_0.png"), "ground_truth": "ok", "predicted_class": "ok", "confidence": .88, "defect_score": .12}]}


def test_calibration_analysis_does_not_bypass_old_evaluation_cache(tmp_path, monkeypatch):
    payload = _calibration_payload(tmp_path)
    cached = {**payload, "evaluation_contract_version": 1}
    (tmp_path / "eval_results.json").write_text(json.dumps(cached))
    calls = []
    def recompute(**kwargs):
        calls.append(kwargs)
        return payload
    monkeypatch.setattr(evaluation, "run_or_load_evaluation", recompute)
    result = evaluation.get_overkill_underkill_analysis(job_id=str(tmp_path), target_max_underkill=0,
        cost_escape=500., cost_scrap=25., current_threshold=.5)
    assert calls, "Calibration cannot reuse pre-integrity predictions"
    assert result["evaluated_split"] == "test"
    assert result["calibration_evidence"]["prediction_count"] == 2


def test_calibration_records_same_cohort_role_without_relabeling_quality(tmp_path):
    payload = _calibration_payload(tmp_path)
    file = tmp_path / "eval_results.json"
    file.write_text(json.dumps(payload))
    result = evaluation.run_zero_escape_calibration(job_id=str(tmp_path))
    updated = json.loads(file.read_text())
    assert result["calibrated"] is True
    assert updated["metrics"]["threshold_calibration_applied"] is True
    assert updated["metrics"]["threshold_calibration_split"] == "test"
    assert updated["metrics"]["calibration_role"] == "threshold_calibration"
    assert updated["calibration_evidence"]["prediction_count"] == 2
    assert updated["metrics"]["selection_overlap"] is False


def test_calibration_persistence_failure_cannot_report_success(tmp_path, monkeypatch):
    payload = _calibration_payload(tmp_path)
    file = tmp_path / "eval_results.json"
    file.write_text(json.dumps(payload))
    original = file.read_bytes()
    monkeypatch.setattr(evaluation, "_atomic_write_json", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(HTTPException) as caught:
        evaluation.run_zero_escape_calibration(job_id=str(tmp_path))
    assert caught.value.status_code == 500
    assert file.read_bytes() == original


def test_calibration_rejects_predictions_changed_after_analysis(tmp_path, monkeypatch):
    payload = _calibration_payload(tmp_path)
    file = tmp_path / "eval_results.json"
    file.write_text(json.dumps(payload))
    original = evaluation.get_overkill_underkill_analysis
    def change_after_analysis(**kwargs):
        result = original(**kwargs)
        payload['test_predictions'][0]['defect_score'] = .22
        file.write_text(json.dumps(payload))
        return result
    monkeypatch.setattr(evaluation, 'get_overkill_underkill_analysis', change_after_analysis)
    with pytest.raises(HTTPException) as caught:
        evaluation.run_zero_escape_calibration(job_id=str(tmp_path))
    assert caught.value.status_code == 409
    assert 'zero_underkill_calibrated' not in json.loads(file.read_text())


def test_migration_label_comparison_ignores_private_publication_files(tmp_path):
    original, migrated = tmp_path / 'legacy', tmp_path / 'project'
    for root in (original, migrated):
        (root / 'masks').mkdir(parents=True)
        (root / 'a.json').write_text('{"annotations": []}')
        Image.new('L', (16, 16)).save(root / 'masks' / 'a.png')
    (original / '.annotation-save-a.lock').write_bytes(b'lock')
    (migrated / 'masks' / '.annotation-atomic-backup').write_bytes(b'private recovery bytes')
    assert evaluation._same_label_tree(original, migrated) is True
    (migrated / 'a.json').write_text('{"annotations": [{"label": "NG"}]}')
    assert evaluation._same_label_tree(original, migrated) is False
