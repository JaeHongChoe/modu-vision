"""One versioned class-role meaning for flow decisions, runtime packages, analysis and the renderer."""
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from backend.engine import class_semantics as semantics
from backend.engine import exporter
from backend.engine.evaluation_history import binary_verdict
from backend.engine.flowchart_engine import FlowchartEngine, _normal_class_indices
from backend.engine.zero_escape_analyzer import is_defect_label
from backend.tests.test_export_calibration_integrity import FixedProbabilities, _export, _inspector

CASES = json.loads((Path(__file__).resolve().parents[2] / "src/renderer/utils/classSemanticsCases.json")
                   .read_text(encoding="utf-8"))["cases"]
IDS = [case["name"] or "<empty>" for case in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_alias_resolution_matches_the_shared_cases(case):
    assert semantics.class_role(case["name"]) == case["role"]
    assert semantics.is_defect_class(case["name"]) is (case["role"] != semantics.ROLE_NORMAL)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_flow_runtime_and_analysis_use_the_same_meaning(case):
    name, normal = case["name"], case["role"] == semantics.ROLE_NORMAL
    assert _normal_class_indices([name]) == ([0] if normal else [])
    assert exporter.runtime_is_defect_class(name) is (not normal)
    assert is_defect_label(name) is (not normal)
    if name.strip():
        assert binary_verdict(name) == {"normal": "OK", "defect": "NG", "unknown": None}[case["role"]]


def test_unknown_and_conflicting_names_are_never_normal():
    for name in ("scratch", "class_0", "review", "ok1", "비정상", "ok_ng", "NG_normal", "fail_OK"):
        assert semantics.class_role(name) != semantics.ROLE_NORMAL
    assert semantics.resolve_class_role("ok_ng") == (semantics.ROLE_DEFECT, "conflict")
    assert semantics.resolve_class_role("ok_ng", {"ok_ng": "unknown"}) == (semantics.ROLE_UNKNOWN, "explicit")
    assert semantics.is_defect_class("ok_ng", {"ok_ng": "unknown"}) is True
    assert semantics.resolve_class_role("scratch") == (semantics.ROLE_DEFECT, "default")
    assert semantics.resolve_class_role("합격") == (semantics.ROLE_NORMAL, "alias")


def test_explicit_roles_override_aliases():
    roles = {"scratch": "normal", "OK": "defect"}
    assert semantics.resolve_class_role("scratch", roles) == (semantics.ROLE_NORMAL, "explicit")
    assert semantics.resolve_class_role("OK", roles) == (semantics.ROLE_DEFECT, "explicit")
    assert semantics.normal_class_indices(["OK", "scratch", "합격"], roles) == [1, 2]
    assert _normal_class_indices(["OK", "scratch"], roles) == [1]
    assert exporter.runtime_is_defect_class("OK", roles) is True


@pytest.mark.parametrize("roles", [{"OK": "maybe"}, {"missing": "normal"}, {"OK": "unknown"}, ["OK"], {1: "normal"}])
def test_requested_roles_are_validated_against_the_model_classes(roles):
    with pytest.raises(ValueError):
        semantics.validate_class_roles(["OK", "NG"], roles)


def test_record_freezes_roles_with_version_and_basis():
    record = semantics.class_semantics_record(["OK", "scratch", "ok_ng", "alpha"], {"alpha": "normal"})
    assert record["version"] == semantics.CLASS_SEMANTICS_VERSION == 1
    assert record["roles"] == {"OK": "normal", "scratch": "defect", "ok_ng": "defect", "alpha": "normal"}
    assert record["basis"] == {"OK": "alias", "scratch": "default", "ok_ng": "conflict", "alpha": "explicit"}
    assert record["normal_classes"] == ["OK", "alpha"]
    assert record["defect_classes"] == ["scratch", "ok_ng"] and record["unknown_classes"] == []
    assert semantics.class_semantics_record(["x"], {"x": "unknown"})["unknown_classes"] == ["x"]
    json.dumps(record, ensure_ascii=False, allow_nan=False)
    assert semantics.recorded_roles({"class_semantics": record}) == record["roles"]
    assert semantics.recorded_roles({"classes": ["OK"]}) is None


@pytest.mark.parametrize("record", [
    {"version": 2, "roles": {"OK": "normal"}},
    {"version": True, "roles": {"OK": "normal"}},
    {"version": 1, "roles": {"OK": "fine"}},
    {"version": 1, "roles": ["OK"]},
    "v1",
])
def test_unsupported_or_malformed_records_are_refused(record):
    with pytest.raises(ValueError):
        semantics.recorded_roles({"class_semantics": record})


def test_training_metadata_records_requested_roles():
    record = semantics.training_class_semantics(["alpha", "beta"], {"alpha": "normal"})
    assert record["roles"] == {"alpha": "normal", "beta": "defect"}
    assert record["basis"]["alpha"] == "explicit"
    with pytest.raises(ValueError):
        semantics.training_class_semantics(["alpha", "beta"], {"gamma": "normal"})


def test_flow_engine_keeps_the_checkpoint_record_for_decisions():
    engine = FlowchartEngine.__new__(FlowchartEngine)
    engine._model_classes, engine._model_class_roles = {}, {}
    record = semantics.class_semantics_record(["alpha", "beta"], {"alpha": "normal"})
    engine._remember_model_classes(("k",), ["alpha", "beta"], {"class_semantics": record})
    assert engine._model_classes[("k",)] == ["alpha", "beta"]
    assert _normal_class_indices(engine._model_classes[("k",)], engine._model_class_roles[("k",)]) == [0]
    engine._remember_model_classes(("old",), ["no_defect", "scratch"], {"classes": ["no_defect", "scratch"]})
    assert engine._model_class_roles[("old",)] is None
    assert _normal_class_indices(["no_defect", "scratch"], None) == [0]


def test_new_package_records_and_applies_the_model_roles(tmp_path, monkeypatch):
    record = semantics.class_semantics_record(["alpha", "beta"], {"alpha": "normal"})
    meta = {"task": "classification", "classes": ["alpha", "beta"], "image_size": [32, 32],
            "optimal_threshold": 0.5, "class_semantics": record}
    _, directory, config, _ = _export(tmp_path, monkeypatch, FixedProbabilities([0.9, 0.1]), meta)
    assert config["class_semantics"]["version"] == 1
    assert config["class_semantics"]["roles"] == {"alpha": "normal", "beta": "defect"}
    result = _inspector(directory, monkeypatch).inspect(np.zeros((32, 32, 3), dtype=np.uint8))
    assert result["verdict"] == "OK"
    assert result["defect_score"] == pytest.approx(0.1, abs=1e-4)


def test_new_package_freezes_alias_roles_for_models_without_a_record(tmp_path, monkeypatch):
    meta = {"task": "classification", "classes": ["no_defect", "scratch"], "image_size": [32, 32],
            "optimal_threshold": 0.5}
    _, directory, config, _ = _export(tmp_path, monkeypatch, FixedProbabilities([0.2, 0.8]), meta)
    assert config["class_semantics"]["roles"] == {"no_defect": "normal", "scratch": "defect"}
    assert config["class_semantics"]["basis"] == {"no_defect": "alias", "scratch": "default"}
    result = _inspector(directory, monkeypatch).inspect(np.zeros((32, 32, 3), dtype=np.uint8))
    assert result["verdict"] == "NG"
    assert result["defect_score"] == pytest.approx(0.8, abs=1e-4)


def test_runtime_without_a_recorded_meaning_uses_its_own_embedded_rule(tmp_path, monkeypatch):
    meta = {"task": "classification", "classes": ["alpha", "beta"], "image_size": [32, 32], "optimal_threshold": 0.5,
            "class_semantics": semantics.class_semantics_record(["alpha", "beta"], {"alpha": "normal"})}
    _, directory, config, _ = _export(tmp_path, monkeypatch, FixedProbabilities([0.9, 0.1]), meta)
    config.pop("class_semantics")
    (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
    result = _inspector(directory, monkeypatch).inspect(np.zeros((32, 32, 3), dtype=np.uint8))
    assert result["verdict"] == "REVIEW"  # neither alpha nor beta is a normal alias


def test_packaged_runtime_contains_the_shared_resolver_source(tmp_path, monkeypatch):
    _, directory, _, _ = _export(tmp_path, monkeypatch, FixedProbabilities([0.7, 0.3]),
                                 {"task": "classification", "classes": ["OK", "NG"], "image_size": [32, 32],
                                  "optimal_threshold": 0.5})
    source = (directory / "infer.py").read_text(encoding="utf-8")
    assert semantics.runtime_source() in source
    assert f"CLASS_SEMANTICS_VERSION = {semantics.CLASS_SEMANTICS_VERSION}" in source
    torch.manual_seed(0)


def test_segmentation_roles_follow_mask_channels_for_legacy_and_alias_names():
    record = semantics.training_class_semantics(["background", "ok_area", "scratch"], None, task="segmentation")
    assert record["roles"] == {"background": "normal", "ok_area": "defect", "scratch": "defect"}
    assert record["basis"] == {"background": "segmentation_structure", "ok_area": "segmentation_structure",
                               "scratch": "segmentation_structure"}
    explicit = semantics.training_class_semantics(["background", "scratch"], {"scratch": "defect"}, task="segmentation")
    assert explicit["basis"] == {"background": "segmentation_structure", "scratch": "explicit"}


@pytest.mark.parametrize("roles", [{"background": "defect"}, {"scratch": "normal"}])
def test_segmentation_rejects_requested_roles_that_contradict_mask_channels(roles):
    with pytest.raises(ValueError, match="channel 0"):
        semantics.training_class_semantics(["background", "scratch"], roles, task="segmentation")


def test_classification_roles_stay_free():
    record = semantics.training_class_semantics(["background", "scratch"], {"background": "defect", "scratch": "normal"},
                                                task="classification")
    assert record["roles"] == {"background": "defect", "scratch": "normal"}


@pytest.mark.parametrize("roles", [
    {"background": "normal", "scratch": "normal"},
    {"background": "defect", "scratch": "defect"},
    {"background": "normal", "scratch": "unknown"},
    {"background": "normal"},
])
def test_contradicting_segmentation_records_are_refused_at_load(roles):
    metadata = {"classes": ["background", "scratch"], "class_semantics": {"version": 1, "roles": roles}}
    with pytest.raises(ValueError, match="channel 0"):
        semantics.recorded_roles(metadata, task="segmentation")
    engine = FlowchartEngine.__new__(FlowchartEngine)
    engine._model_classes, engine._model_class_roles = {}, {}
    with pytest.raises(ValueError, match="channel 0"):
        engine._remember_model_classes(("k",), ["background", "scratch"], metadata, task="segmentation")
    assert semantics.recorded_roles(metadata, task="classification") == roles


def test_legacy_segmentation_without_a_record_keeps_background_and_foreground():
    engine = FlowchartEngine.__new__(FlowchartEngine)
    engine._model_classes, engine._model_class_roles = {}, {}
    engine._remember_model_classes(("old",), ["background", "scratch"], {"classes": ["background", "scratch"]}, task="segmentation")
    assert engine._model_class_roles[("old",)] is None
    from backend.api import routes_evaluation
    derived = routes_evaluation._evaluation_class_semantics({"classes": ["background", "ok_area"]}, "segmentation")
    assert derived["roles"] == {"background": "normal", "ok_area": "defect"}
    trained = semantics.training_class_semantics(["background", "scratch"], None, task="segmentation")
    assert semantics.recorded_roles({"classes": ["background", "scratch"], "class_semantics": trained},
                                    task="segmentation") == {"background": "normal", "scratch": "defect"}
