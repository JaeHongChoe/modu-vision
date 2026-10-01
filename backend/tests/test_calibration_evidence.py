"""Threshold calibration evidence is explicit, versioned and never claims independence."""
import copy
import json
import math

import pytest

from backend.engine.calibration_evidence import (
    mark_calibration_evidence,
    prediction_fingerprint,
    validate_calibration_evidence,
)
from backend.engine.evaluation_history import binary_verdict
from backend.engine.zero_escape_analyzer import analyze_zero_escape, is_defect_label

CALIBRATED_AT = "2026-10-01T09:00:00Z"


def _prediction(name, truth, predicted, confidence, **extra):
    return {"image_id": name, "file_name": f"{name}.png", "file_path": f"/data/test/{name}.png",
            "ground_truth": truth, "predicted_class": predicted, "confidence": confidence, **extra}


def _payload(split="test", version=2, predictions=None, **metrics):
    rows = predictions if predictions is not None else [
        _prediction("ok_0", "ok", "ok", 0.9),
        _prediction("ok_1", "ok", "ng", 0.6),
        _prediction("ng_0", "ng", "ng", 0.8),
        _prediction("ng_1", "ng", "ok", 0.7),
    ]
    payload = {"task": "classification", "metrics": {"evaluated_split": split, "accuracy": 0.5, **metrics},
               "test_predictions": rows, "binding": {"checkpoint_sha256": "a" * 64}}
    if version is not None:
        payload["evaluation_contract_version"] = version
    return payload


@pytest.mark.parametrize("version", [None, 1, 3, "2", 2.0, True])
def test_only_the_exact_contract_version_is_accepted(version):
    with pytest.raises(ValueError, match="contract version"):
        validate_calibration_evidence(_payload(version=version))


@pytest.mark.parametrize("split", ["train", "all", "unknown", "", None, "TEST"])
def test_training_unpartitioned_or_unknown_splits_are_rejected(split):
    with pytest.raises(ValueError, match="split"):
        validate_calibration_evidence(_payload(split=split))


def test_missing_split_is_rejected():
    payload = _payload()
    del payload["metrics"]["evaluated_split"]
    with pytest.raises(ValueError, match="split"):
        validate_calibration_evidence(payload)


@pytest.mark.parametrize("split, overlap", [("test", False), ("val", True)])
def test_selection_overlap_follows_the_evaluated_split(split, overlap):
    evidence = validate_calibration_evidence(_payload(split=split))
    assert evidence["evaluated_split"] == split
    assert evidence["selection_overlap"] is overlap
    assert evidence["prediction_count"] == 4
    assert evidence["truth_counts"] == {"ng": 2, "ok": 2}


def test_contradictory_selection_overlap_is_rejected():
    with pytest.raises(ValueError, match="selection_overlap"):
        validate_calibration_evidence(_payload(split="test", selection_overlap=True))
    with pytest.raises(ValueError, match="selection_overlap"):
        validate_calibration_evidence(_payload(split="val", selection_overlap=False))


def test_helper_never_certifies_independence_or_mutates_input():
    payload = _payload()
    snapshot = copy.deepcopy(payload)
    evidence = validate_calibration_evidence(payload)
    assert payload == snapshot
    assert evidence["independence_certified"] is False
    json.dumps(evidence, allow_nan=False)


@pytest.mark.parametrize("rows", [
    [],
    "not-a-list",
    [None],
    [{"ground_truth": "ok", "confidence": 0.5}],
    [_prediction("a", "ok", "ok", float("nan"))],
    [_prediction("a", "ok", "ok", float("inf"))],
    [_prediction("a", "ok", "ok", True)],
    [{**_prediction("a", "ok", "ok", 0.5), "confidence": None}],
])
def test_predictions_must_be_real_identifiable_and_scored(rows):
    with pytest.raises(ValueError):
        validate_calibration_evidence(_payload(predictions=rows))


def test_missing_predictions_key_is_rejected():
    payload = _payload()
    del payload["test_predictions"]
    with pytest.raises(ValueError, match="test_predictions"):
        validate_calibration_evidence(payload)


@pytest.mark.parametrize("truth", [None, "", "  ", "review", "Unknown"])
def test_unknown_truth_cannot_be_counted_as_ok_or_ng(truth):
    rows = [_prediction("ng_0", "ng", "ng", 0.8), _prediction("x", truth, "ok", 0.9)]
    with pytest.raises(ValueError, match="truth"):
        validate_calibration_evidence(_payload(predictions=rows))


def test_duplicate_prediction_identity_is_rejected():
    rows = [_prediction("ng_0", "ng", "ng", 0.8), _prediction("ng_0", "ng", "ng", 0.8)]
    with pytest.raises(ValueError, match="duplicate"):
        validate_calibration_evidence(_payload(predictions=rows))


def test_patch_boxes_on_one_image_are_distinct_predictions():
    rows = [_prediction("img", "ng", "ng", 0.8, box=[0, 0, 8, 8]),
            _prediction("img", "ok", "ok", 0.9, box=[8, 0, 16, 8])]
    assert validate_calibration_evidence(_payload(predictions=rows))["prediction_count"] == 2


def test_normal_label_is_never_counted_as_a_defect():
    """The zero-escape truth rule is used only where it agrees with known OK/NG truth."""
    rows = [_prediction("ng_0", "ng", "ng", 0.8), _prediction("n_0", "정상", "정상", 0.9)]
    if is_defect_label("정상") and binary_verdict("정상") == "OK":
        with pytest.raises(ValueError, match="truth"):
            validate_calibration_evidence(_payload(predictions=rows))
    else:
        assert validate_calibration_evidence(_payload(predictions=rows))["truth_counts"] == {"ng": 1, "ok": 1}


def test_fingerprint_is_order_independent_and_tracks_scored_truth_only():
    rows = _payload()["test_predictions"]
    base = prediction_fingerprint(rows)
    assert len(base) == 64 and int(base, 16) >= 0
    assert prediction_fingerprint(list(reversed(rows))) == base
    reviewed = [{**row, "workflow_state": "approved", "revision": 7} for row in rows]
    assert prediction_fingerprint(reviewed) == base
    relabeled = [{**rows[0], "ground_truth": "ng"}, *rows[1:]]
    rescored = [{**rows[0], "confidence": 0.91}, *rows[1:]]
    assert prediction_fingerprint(relabeled) != base
    assert prediction_fingerprint(rescored) != base
    assert validate_calibration_evidence(_payload())["prediction_fingerprint"] == base


def test_mark_returns_new_evidence_without_changing_input():
    payload = _payload(split="val")
    snapshot = copy.deepcopy(payload)
    marked = mark_calibration_evidence(payload, 0.4321, CALIBRATED_AT)
    assert payload == snapshot
    assert marked is not payload and marked["metrics"] is not payload["metrics"]
    assert marked["optimal_threshold"] == 0.4321
    assert marked["zero_underkill_calibrated"] is True
    assert marked["calibrated_at"] == CALIBRATED_AT
    metrics = marked["metrics"]
    assert metrics["threshold_calibration_applied"] is True
    assert metrics["threshold_calibration_split"] == "val"
    assert metrics["calibration_role"] == "threshold_calibration"
    assert metrics["selection_overlap"] is True
    assert metrics["accuracy"] == 0.5
    evidence = marked["calibration_evidence"]
    assert evidence["role"] == "threshold_calibration"
    assert evidence["evaluated_split"] == "val"
    assert evidence["prediction_count"] == 4
    assert evidence["prediction_fingerprint"] == prediction_fingerprint(payload["test_predictions"])
    assert evidence["independent_test_required"] is True
    assert evidence["independence_certified"] is False
    assert evidence["threshold"] == 0.4321
    assert evidence["calibrated_at"] == CALIBRATED_AT
    json.dumps(marked, allow_nan=False)


def test_marking_a_test_split_records_that_it_is_no_longer_an_independent_test():
    marked = mark_calibration_evidence(_payload(split="test"), 0.5, CALIBRATED_AT)
    assert marked["metrics"]["selection_overlap"] is False
    assert marked["metrics"]["threshold_calibration_split"] == "test"
    assert marked["calibration_evidence"]["independent_test_required"] is True


@pytest.mark.parametrize("rows", [
    [_prediction("ng_0", "ng", "ng", 0.8), _prediction("ng_1", "ng", "ok", 0.7)],
    [_prediction("ok_0", "ok", "ok", 0.9), _prediction("ok_1", "ok", "ng", 0.6)],
])
def test_single_class_sets_cannot_be_calibrated(rows):
    validate_calibration_evidence(_payload(predictions=rows))
    with pytest.raises(ValueError, match="both"):
        mark_calibration_evidence(_payload(predictions=rows), 0.5, CALIBRATED_AT)


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -0.01, 1.01, True, "0.5", None])
def test_threshold_must_be_a_finite_defect_score(threshold):
    with pytest.raises(ValueError, match="threshold"):
        mark_calibration_evidence(_payload(), threshold, CALIBRATED_AT)


@pytest.mark.parametrize("calibrated_at", [None, "", "yesterday", "2026-10-01T09:00:00", 123])
def test_calibration_time_must_be_a_timezone_aware_iso_timestamp(calibrated_at):
    with pytest.raises(ValueError, match="calibrated_at"):
        mark_calibration_evidence(_payload(), 0.5, calibrated_at)


def test_old_cached_payload_cannot_be_marked():
    with pytest.raises(ValueError, match="contract version"):
        mark_calibration_evidence(_payload(version=None), 0.5, CALIBRATED_AT)


def test_marking_agrees_with_the_zero_escape_analyzer():
    payload = _payload()
    analysis = analyze_zero_escape(payload["test_predictions"], task="classification")
    marked = mark_calibration_evidence(payload, analysis["optimal_threshold"], CALIBRATED_AT)
    counts = marked["calibration_evidence"]["truth_counts"]
    assert counts == {"ng": analysis["total_defects"], "ok": analysis["total_normals"]}
    assert math.isclose(marked["optimal_threshold"], analysis["optimal_threshold"])


def test_recalibration_replaces_previous_evidence():
    first = mark_calibration_evidence(_payload(), 0.3, CALIBRATED_AT)
    second = mark_calibration_evidence(first, 0.6, "2026-10-02T09:00:00+00:00")
    assert second["optimal_threshold"] == 0.6
    assert second["calibration_evidence"]["threshold"] == 0.6
    assert second["calibration_evidence"]["prediction_fingerprint"] == first["calibration_evidence"]["prediction_fingerprint"]
    assert first["optimal_threshold"] == 0.3


@pytest.mark.parametrize("task, classes, transferable, reason", [
    ("classification", ["OK", "NG"], True, None),
    ("classification", ["Normal_OK", "Defect_NG"], True, None),
    ("Classification ", ["ng", "ok"], True, None),
    ("classification", ["ok", "good", "ng"], False, "classification_requires_one_normal_one_defect"),
    ("classification", ["ok", "scratch", "dent"], False, "classification_requires_one_normal_one_defect"),
    ("classification", ["scratch", "dent"], False, "classification_requires_one_normal_one_defect"),
    ("classification", ["nondefect", "NG"], False, "class_semantics_mismatch"),
    ("classification", ["합격", "불량"], True, None),
    ("classification", ["양품", "NG"], True, None),
    ("classification", ["review", "NG"], False, "class_semantics_mismatch"),
    ("detection", ["background", "scratch", "dent"], True, None),
    ("detection", ["scratch"], True, None),
    ("detection", ["background", "scratch", "component_ok"], False, "detection_non_defect_foreground"),
    ("detection", ["background"], False, "detection_requires_defect_classes"),
    ("detection", ["__background__", "nondefect"], False, "class_semantics_mismatch"),
    ("segmentation", ["background", "defect"], False, "segmentation_area_rule"),
    ("patch_classification", ["good", "defect"], False, "patch_score_rule_unverified"),
    ("anomaly", ["good", "anomaly"], False, "anomaly_uses_saved_model_threshold"),
    ("anomaly_detection", ["good", "anomaly"], False, "anomaly_uses_saved_model_threshold"),
    ("ocr", ["text"], False, "task_not_verified"),
    ("classification", "OK,NG", False, "classes_unavailable"),
    ("classification", [], False, "classes_unavailable"),
])
def test_transfer_scope_allows_only_identical_evaluation_and_runtime_scores(task, classes, transferable, reason):
    from backend.engine.calibration_evidence import calibration_transfer_scope
    from backend.engine.exporter import runtime_is_defect_class
    scope = calibration_transfer_scope(task, classes, runtime_is_defect_class)
    assert scope["transferable"] is transferable
    assert scope["reason"] == reason
    json.dumps(scope, allow_nan=False)


def test_transfer_scope_requires_the_runtime_class_rule():
    from backend.engine.calibration_evidence import calibration_transfer_scope
    with pytest.raises(TypeError):
        calibration_transfer_scope("classification", ["OK", "NG"])
