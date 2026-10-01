"""Evaluation truth, scores and calibration use the roles recorded with the evaluated model."""
import json

import pytest
from PIL import Image

from backend.api import routes_evaluation as evaluation
from backend.engine import class_semantics as semantics
from backend.engine.calibration_evidence import validate_calibration_evidence
from backend.engine.evaluation_evidence import evaluation_analysis
from backend.engine.evaluation_history import binary_verdict
from backend.engine.zero_escape_analyzer import analyze_zero_escape, compute_sample_defect_score, is_defect_label

ROLES = {"alpha": "normal", "beta": "defect"}
RECORD = semantics.class_semantics_record(["alpha", "beta"], {"alpha": "normal"})


def test_recorded_roles_reach_truth_and_scores():
    assert is_defect_label("alpha") is True
    assert is_defect_label("alpha", ROLES) is False
    assert binary_verdict("alpha") == "NG" and binary_verdict("alpha", ROLES) == "OK"
    assert binary_verdict("x", {"x": "unknown"}) is None
    assert is_defect_label("x", {"x": "unknown"}) is True
    assert binary_verdict("review", ROLES) is None
    assert compute_sample_defect_score({"predicted_class": "alpha", "confidence": .9}) == pytest.approx(.9)
    assert compute_sample_defect_score({"predicted_class": "alpha", "confidence": .9}, roles=ROLES) == pytest.approx(.1)
    detections = {"detections": [{"label": "alpha", "score": .95}, {"label": "beta", "score": .4}]}
    assert compute_sample_defect_score(detections, task="detection", roles=ROLES) == pytest.approx(.4)


def test_zero_escape_analysis_counts_truth_with_recorded_roles():
    rows = [{"image_id": "a", "ground_truth": "alpha", "predicted_class": "alpha", "confidence": .9},
            {"image_id": "b", "ground_truth": "beta", "predicted_class": "beta", "confidence": .8}]
    counted = analyze_zero_escape(rows, task="classification", class_roles=ROLES)
    assert (counted["total_defects"], counted["total_normals"]) == (1, 1)
    default = analyze_zero_escape(rows, task="classification")
    assert (default["total_defects"], default["total_normals"]) == (2, 0)


def test_evaluation_roc_uses_recorded_truth_roles():
    rows = [{"ground_truth": "alpha", "defect_score": .1, "file_path": "/a.png"},
            {"ground_truth": "beta", "defect_score": .9, "file_path": "/b.png"}]
    assert evaluation_analysis(rows, "classification", roles=ROLES)["roc"]["negative_count"] == 1
    assert evaluation_analysis(rows, "classification")["roc"]["negative_count"] == 0


def test_calibration_truth_uses_the_payload_record():
    payload = {"evaluation_contract_version": 2, "task": "classification", "class_semantics": RECORD,
               "metrics": {"evaluated_split": "test", "selection_overlap": False},
               "test_predictions": [
                   {"image_id": "a", "ground_truth": "alpha", "predicted_class": "alpha", "confidence": .9},
                   {"image_id": "b", "ground_truth": "beta", "predicted_class": "beta", "confidence": .8}]}
    assert validate_calibration_evidence(payload)["truth_counts"] == {"ng": 1, "ok": 1}
    payload.pop("class_semantics")
    assert validate_calibration_evidence(payload)["truth_counts"] == {"ng": 2, "ok": 0}


def _job(tmp_path, monkeypatch, meta, predictions):
    source = tmp_path / "source"
    for name in ("alpha", "beta"):
        (source / name).mkdir(parents=True)
        Image.new("RGB", (8, 8), "white").save(source / name / f"{name}.png")
    output = tmp_path / "models" / "job_roles"
    output.mkdir(parents=True)
    checkpoint = output / "best_model.pt"
    checkpoint.write_bytes(b"checkpoint boundary fixture")
    rows = [{**row, "file_path": str(source / row["ground_truth"] / f"{row['ground_truth']}.png")} for row in predictions]
    monkeypatch.setattr(evaluation, "_resolve_job_artifacts",
                        lambda **kwargs: (output, checkpoint, meta, "classification", output.name, source))
    calls = []

    def evaluate(*args):
        calls.append(True)
        return {"metrics": {"accuracy": 1., "evaluated_split": "test", "selection_overlap": False},
                "confusion_matrix": {"cell_samples": {"x": [rows[0]["file_path"]]}},
                "test_predictions": json.loads(json.dumps(rows))}

    monkeypatch.setattr(evaluation, "_evaluate_classification", evaluate)
    return output, calls


ROWS = [{"ground_truth": "alpha", "predicted_class": "alpha", "confidence": .9},
        {"ground_truth": "beta", "predicted_class": "alpha", "confidence": .7}]


def test_fresh_evaluation_records_the_model_roles_and_annotates_with_them(tmp_path, monkeypatch):
    _, calls = _job(tmp_path, monkeypatch, {"classes": ["alpha", "beta"], "class_semantics": RECORD}, ROWS)
    result = evaluation.run_or_load_evaluation(job_id="job_roles")
    assert calls == [True]
    assert result["class_semantics"] == RECORD
    alpha, beta = result["test_predictions"]
    assert alpha["is_defect"] is False and alpha["defect_score"] == pytest.approx(.1)
    assert beta["is_defect"] is True and beta["defect_score"] == pytest.approx(.3)
    again = evaluation.run_or_load_evaluation(job_id="job_roles")
    assert calls == [True] and again["class_semantics"] == RECORD


def test_models_without_a_record_keep_default_alias_meaning(tmp_path, monkeypatch):
    _, _ = _job(tmp_path, monkeypatch, {"classes": ["alpha", "beta"]}, ROWS)
    result = evaluation.run_or_load_evaluation(job_id="job_roles")
    assert result["class_semantics"]["roles"] == {"alpha": "defect", "beta": "defect"}
    assert result["class_semantics"]["basis"] == {"alpha": "default", "beta": "default"}
    assert [row["is_defect"] for row in result["test_predictions"]] == [True, True]
    assert result["test_predictions"][0]["defect_score"] == pytest.approx(.9)


def test_cached_evaluation_with_a_different_meaning_is_recomputed(tmp_path, monkeypatch):
    output, calls = _job(tmp_path, monkeypatch, {"classes": ["alpha", "beta"], "class_semantics": RECORD}, ROWS)
    evaluation.run_or_load_evaluation(job_id="job_roles")
    cached = json.loads((output / "eval_results.json").read_text())
    cached.pop("class_semantics")
    (output / "eval_results.json").write_text(json.dumps(cached))
    result = evaluation.run_or_load_evaluation(job_id="job_roles")
    assert calls == [True, True]
    assert result["class_semantics"] == RECORD


def test_remote_or_worker_annotations_are_recomputed_with_recorded_roles():
    rows = [{"ground_truth": "alpha", "predicted_class": "alpha", "confidence": .9, "is_defect": True, "defect_score": .9},
            {"ground_truth": "beta", "predicted_class": "beta", "confidence": .6, "defect_score": .6}]
    evaluation._annotate_predictions(rows, "classification", ROLES)
    assert rows[0]["is_defect"] is False and rows[0]["defect_score"] == pytest.approx(.1)
    assert rows[1]["is_defect"] is True and rows[1]["defect_score"] == pytest.approx(.6)
    explicit = [{"ground_truth": "beta", "predicted_class": "beta", "confidence": .2, "defect_score": .77}]
    evaluation._annotate_predictions(explicit, "segmentation", ROLES)
    assert explicit[0]["defect_score"] == pytest.approx(.77)


def test_overkill_analysis_counts_truth_with_the_payload_record(tmp_path, monkeypatch):
    payload = {"evaluation_contract_version": evaluation.EVALUATION_CONTRACT_VERSION, "task": "classification",
               "class_semantics": RECORD, "metrics": {"evaluated_split": "test", "selection_overlap": False},
               "test_predictions": [
                   {"image_id": "a", "ground_truth": "alpha", "predicted_class": "alpha", "confidence": .9, "defect_score": .1},
                   {"image_id": "b", "ground_truth": "beta", "predicted_class": "beta", "confidence": .8, "defect_score": .8}]}
    monkeypatch.setattr(evaluation, "run_or_load_evaluation", lambda **kwargs: payload)
    result = evaluation.get_overkill_underkill_analysis(job_id="job_roles", target_max_underkill=0, cost_escape=500.0,
                                                         cost_scrap=25.0, current_threshold=.5)
    assert (result["total_defects"], result["total_normals"]) == (1, 1)


def test_grouped_errors_use_recorded_roles_for_class_labels_but_keep_binary_flow_verdicts():
    from backend.engine.evaluation_history import grouped_errors
    roles = {"normal": "defect", "scratch": "normal"}
    rows = [{"ground_truth": "normal", "predicted_class": "scratch", "is_correct": False, "product": "p"}]
    counted = grouped_errors(rows, "classification", roles)["product"]["p"]
    assert (counted["misses"], counted["overkill"]) == (1, 0)
    default = grouped_errors(rows, "classification")["product"]["p"]
    assert (default["misses"], default["overkill"]) == (0, 1)
    hostile = {"OK": "defect", "NG": "normal", "normal": "defect"}
    flow = [{"ground_truth_verdict": "OK", "ground_truth": "normal", "candidate": {"verdict": "NG"}, "product": "f"}]
    counted = grouped_errors(flow, "classification", hostile)["product"]["f"]
    assert (counted["misses"], counted["overkill"]) == (0, 1)
    final = [{"ground_truth_verdict": "NG", "final_verdict": "OK", "product": "g"}]
    assert grouped_errors(final, "classification", hostile)["product"]["g"]["misses"] == 1


def test_history_append_counts_with_the_result_class_semantics(tmp_path):
    from backend.engine.evaluation_history import EvaluationHistory
    result = {"task": "classification", "class_semantics": {"version": 1, "roles": {"normal": "defect", "scratch": "normal"}},
              "test_predictions": [{"ground_truth": "normal", "predicted_class": "scratch", "is_correct": False, "product": "p"}]}
    record = EvaluationHistory(tmp_path / "history").append(result, {"task": "classification"})
    assert (record["grouped_errors"]["product"]["p"]["misses"], record["grouped_errors"]["product"]["p"]["overkill"]) == (1, 0)
