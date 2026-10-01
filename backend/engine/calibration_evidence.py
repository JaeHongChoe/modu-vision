"""Versioned threshold-calibration evidence for saved evaluation results.

These helpers check what an evaluation payload states about itself: its
contract version, the held-out split it used and its scored predictions. They
do not verify source files or checkpoints, and they never certify that a split
is independent of training or of threshold calibration.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import numbers
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from backend.engine.evaluation_history import binary_verdict
from backend.engine.zero_escape_analyzer import is_defect_label

CALIBRATION_EVIDENCE_VERSION = 1
CALIBRATION_ROLE = "threshold_calibration"
FINGERPRINT_BASIS = "scored_truth_v1"
HELD_OUT_SPLITS = ("test", "val")

# Identity and the inputs zero-escape scoring reads. Review metadata such as
# workflow_state or revision is excluded so approval alone keeps the fingerprint.
_IDENTITY_FIELDS = ("image_id", "file_path", "evaluation_file_path", "file_name",
                    "image_uuid", "content_hash", "source_sha256", "box")
_SCORED_FIELDS = ("ground_truth", "predicted_class", "confidence", "defect_score", "anomaly_score",
                  "mask_coverage", "mask_coverage_percent", "detections", "predictions")
_SCORE_FIELDS = ("confidence", "defect_score", "anomaly_score")
_DUPLICATE_KEY_FIELDS = ("image_id", "file_path", "evaluation_file_path", "box")


def _is_real(value: Any) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(float(value))


def _json_safe(value: Any) -> Any:
    """Plain JSON values with finite numbers; ints and floats keep their kind."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        if not math.isfinite(float(value)):
            raise ValueError("Prediction evidence contains a non-finite number")
        return float(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise ValueError(f"Prediction evidence contains an unsupported value: {type(value).__name__}")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _projection(row: Mapping[str, Any]) -> Dict[str, Any]:
    projected = {key: row[key] for key in (*_IDENTITY_FIELDS, *_SCORED_FIELDS) if row.get(key) is not None}
    for key in _SCORE_FIELDS:
        if key in projected:
            projected[key] = float(projected[key])
    return _json_safe(projected)


def prediction_fingerprint(predictions: Iterable[Mapping[str, Any]]) -> str:
    """Order-independent SHA256 of prediction identity, truth and score inputs."""
    encoded = sorted(_canonical(_projection(row)) for row in predictions)
    return hashlib.sha256(b"[" + b",".join(encoded) + b"]").hexdigest()


def _truth(label: Any, roles: Optional[Dict[str, str]] = None) -> str:
    """Binary truth as zero-escape analysis counts it, refused where that would be wrong."""
    known = binary_verdict(label, roles)
    if known is None:
        raise ValueError(f"Prediction ground truth is unknown ({label!r}); calibration needs reviewed OK/NG truth")
    counted = "ng" if is_defect_label(label, roles) else "ok"
    if counted != known.lower():
        raise ValueError(f"Ground truth {label!r} is {known} but zero-escape analysis would count it as "
                         f"{counted.upper()}; calibration would miscount it")
    return counted


def _validated_rows(predictions: Any, roles: Optional[Dict[str, str]] = None) -> Dict[str, int]:
    if not isinstance(predictions, list) or not predictions:
        raise ValueError("Calibration evidence needs a nonempty test_predictions list")
    counts = {"ng": 0, "ok": 0}
    seen = set()
    for index, row in enumerate(predictions):
        if not isinstance(row, Mapping):
            raise ValueError(f"Prediction {index} is not an object")
        if not any(isinstance(row.get(key), str) and row[key].strip() for key in ("image_id", "file_path")):
            raise ValueError(f"Prediction {index} has no image_id or file_path")
        for key in _SCORE_FIELDS:
            if row.get(key) is not None and not _is_real(row[key]):
                raise ValueError(f"Prediction {index} has an invalid {key}")
        if not any(_is_real(row.get(key)) for key in _SCORE_FIELDS):
            raise ValueError(f"Prediction {index} has no finite score")
        if "ground_truth" not in row:
            raise ValueError(f"Prediction {index} has no ground truth")
        counts[_truth(row["ground_truth"], roles)] += 1
        identity = _canonical(_json_safe({key: row.get(key) for key in _DUPLICATE_KEY_FIELDS}))
        if identity in seen:
            raise ValueError(f"Prediction {index} is a duplicate of an earlier prediction")
        seen.add(identity)
    return counts


def validate_calibration_evidence(payload: Mapping[str, Any], expected_contract_version: int = 2) -> Dict[str, Any]:
    """Check a saved evaluation can serve as threshold-calibration input.

    Raises ValueError for an older or newer contract, a split other than test or
    val, contradictory selection overlap, or predictions without identity,
    finite score or known OK/NG truth. The returned evidence is JSON-safe and
    states that independence was not certified here.
    """
    if type(expected_contract_version) is not int:
        raise ValueError("Expected evaluation contract version must be an integer")
    if not isinstance(payload, Mapping):
        raise ValueError("Evaluation payload must be an object")
    version = payload.get("evaluation_contract_version")
    if type(version) is not int or version != expected_contract_version:
        raise ValueError(f"Evaluation contract version {version!r} is not {expected_contract_version}; re-run the evaluation")
    metrics = payload.get("metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("Evaluation payload has no metrics object")
    split = metrics.get("evaluated_split")
    if not isinstance(split, str) or split not in HELD_OUT_SPLITS:
        raise ValueError(f"Evaluated split {split!r} is not a saved test or val partition")
    selection_overlap = split == "val"
    if "selection_overlap" in metrics and metrics["selection_overlap"] is not selection_overlap:
        raise ValueError(f"selection_overlap {metrics['selection_overlap']!r} contradicts the {split} split")
    if "test_predictions" not in payload:
        raise ValueError("Evaluation payload has no test_predictions")
    predictions = payload["test_predictions"]
    from backend.engine.class_semantics import recorded_roles
    counts = _validated_rows(predictions, recorded_roles(payload))
    binding = payload.get("binding")
    return {
        "evaluation_contract_version": version,
        "task": payload.get("task") if isinstance(payload.get("task"), str) else None,
        "evaluated_split": split,
        "selection_overlap": selection_overlap,
        "prediction_count": len(predictions),
        "truth_counts": counts,
        "prediction_fingerprint": prediction_fingerprint(predictions),
        "fingerprint_basis": FINGERPRINT_BASIS,
        "evaluation_binding_sha256": hashlib.sha256(_canonical(_json_safe(binding))).hexdigest()
        if isinstance(binding, Mapping) else None,
        "independence_certified": False,
    }


_UNVERIFIED_TASK_REASONS = {
    "segmentation": "segmentation_area_rule",
    "patch_classification": "patch_score_rule_unverified",
    "anomaly": "anomaly_uses_saved_model_threshold",
    "anomaly_detection": "anomaly_uses_saved_model_threshold",
}


def _class_is_defect(name: Any, runtime_is_defect: Callable[[Any], bool]) -> bool | None:
    """Defect status when evaluation truth, zero-escape counting and the runtime agree."""
    known = binary_verdict(name)
    if known is None:
        return None
    defect = known == "NG"
    if bool(is_defect_label(name)) != defect or bool(runtime_is_defect(name)) != defect:
        return None
    return defect


def calibration_transfer_scope(task: Any, classes: Any, runtime_is_defect: Callable[[Any], bool]) -> Dict[str, Any]:
    """Whether a threshold fitted on evaluation defect scores means the same at runtime.

    Only two cases are accepted: binary classification with one normal and one
    defect class, where both sides score 1 - P(normal); and detection whose
    foreground classes are all defects, where both sides use the strongest box
    score. Every class must have the same OK/NG meaning in evaluation truth,
    zero-escape counting and the runtime rule supplied by the caller.
    """
    task_clean = str(task).strip().lower()

    def scope(reason=None, normal=(), defect=()):
        return {"transferable": reason is None, "reason": reason, "task": task_clean,
                "normal_classes": list(normal), "defect_classes": list(defect)}

    if task_clean in _UNVERIFIED_TASK_REASONS:
        return scope(_UNVERIFIED_TASK_REASONS[task_clean])
    if task_clean not in ("classification", "detection"):
        return scope("task_not_verified")
    if not isinstance(classes, (list, tuple)) or not classes or not all(isinstance(name, str) for name in classes):
        return scope("classes_unavailable")
    names = list(classes)
    if task_clean == "detection" and names[0].strip().lower() in ("background", "__background__"):
        names = names[1:]
    statuses = [_class_is_defect(name, runtime_is_defect) for name in names]
    if any(status is None for status in statuses):
        return scope("class_semantics_mismatch")
    normal = [name for name, defect in zip(names, statuses) if not defect]
    defect = [name for name, defect in zip(names, statuses) if defect]
    if task_clean == "classification" and (len(normal) != 1 or len(defect) != 1):
        return scope("classification_requires_one_normal_one_defect", normal, defect)
    if task_clean == "detection" and not defect:
        return scope("detection_requires_defect_classes", normal, defect)
    if task_clean == "detection" and normal:
        return scope("detection_non_defect_foreground", normal, defect)
    return scope(None, normal, defect)


def _calibration_time(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("calibrated_at must be an ISO-8601 timestamp with a timezone")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text)
    except ValueError as exc:
        raise ValueError(f"calibrated_at is not an ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("calibrated_at must include a timezone")
    return value


def mark_calibration_evidence(payload: Mapping[str, Any], optimal_threshold: Any, calibrated_at: Any,
                              expected_contract_version: int = 2) -> Dict[str, Any]:
    """Return a copy of an evaluation marked as the set its threshold was fitted on.

    The input is not modified. The copy keeps the top-level optimal_threshold,
    zero_underkill_calibrated and calibrated_at fields existing readers use, and
    records that these predictions are calibration evidence; reporting
    independent performance needs a separate held-out evaluation.
    """
    evidence = validate_calibration_evidence(payload, expected_contract_version)
    if not evidence["truth_counts"]["ng"] or not evidence["truth_counts"]["ok"]:
        raise ValueError("Threshold calibration requires both NG and OK truth in the evaluated predictions")
    if not _is_real(optimal_threshold) or not 0.0 <= float(optimal_threshold) <= 1.0:
        raise ValueError(f"Calibrated threshold must be a finite defect score in [0, 1], got {optimal_threshold!r}")
    threshold = float(optimal_threshold)
    timestamp = _calibration_time(calibrated_at)

    marked = copy.deepcopy(dict(payload))
    marked["optimal_threshold"] = threshold
    marked["zero_underkill_calibrated"] = True
    marked["calibrated_at"] = timestamp
    marked["metrics"] = {
        **marked["metrics"],
        "selection_overlap": evidence["selection_overlap"],
        "threshold_calibration_applied": True,
        "threshold_calibration_split": evidence["evaluated_split"],
        "calibration_role": CALIBRATION_ROLE,
    }
    marked["calibration_evidence"] = {
        "version": CALIBRATION_EVIDENCE_VERSION,
        "role": CALIBRATION_ROLE,
        **{key: evidence[key] for key in ("evaluation_contract_version", "task", "evaluated_split",
                                          "selection_overlap", "prediction_count", "truth_counts",
                                          "prediction_fingerprint", "fingerprint_basis",
                                          "evaluation_binding_sha256")},
        "threshold": threshold,
        "threshold_space": "defect_score",
        "calibrated_at": timestamp,
        "independent_test_required": True,
        "independence_certified": False,
    }
    return marked
