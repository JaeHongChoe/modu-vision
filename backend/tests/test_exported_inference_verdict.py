"""Exercise the shipped infer.py decision logic, not just the exporter wrapper."""

from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import exporter
from backend.engine.exporter import generate_standalone_infer_py


def _inspector(task: str, classes: list[str], outputs: list[np.ndarray]):
    namespace = {"__name__": "generated_infer"}
    exec(generate_standalone_infer_py(), namespace)
    inspector = namespace["StandaloneInspector"].__new__(namespace["StandaloneInspector"])
    inspector.task = task
    inspector.classes = classes
    inspector.threshold = 0.5
    inspector.model_format = "onnx"
    inspector.resolution = (32, 32)
    inspector.mean = [0.485, 0.456, 0.406]
    inspector.std = [0.229, 0.224, 0.225]
    inspector.input_name = "input"
    inspector.session = SimpleNamespace(run=lambda *_: outputs)
    return inspector


def test_exported_detection_counts_foreground_label_one():
    outputs = [np.array([[1, 1, 20, 20]], dtype=np.float32),
               np.array([0.9], dtype=np.float32), np.array([1], dtype=np.int64)]
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    for classes in (["Bow"], ["background", "Bow"]):
        result = _inspector("detection", classes, outputs).inspect(image)
        assert result["verdict"] == "NG"
        assert result["defect_score"] == 0.9
        assert result["predicted_class"] == "Bow"
        assert result["detections"] == [{"bbox": [1.0, 1.0, 20.0, 20.0],
                                         "score": 0.9, "label": "Bow"}]


def test_exported_detection_returns_class_name_and_original_image_box():
    outputs = [np.array([[1, 2, 20, 22]], dtype=np.float32),
               np.array([0.9], dtype=np.float32), np.array([2], dtype=np.int64)]
    result = _inspector("detection", ["Scratch", "Bow"], outputs).inspect(
        np.zeros((64, 64, 3), dtype=np.uint8))
    assert result["predicted_class"] == "Bow"
    assert result["detections"] == [{"bbox": [2.0, 4.0, 40.0, 44.0],
                                     "score": 0.9, "label": "Bow"}]


def test_exported_multiclass_classification_sums_all_defect_probabilities():
    probs = np.array([[0.35, 0.40, 0.25]], dtype=np.float32)
    result = _inspector("classification", ["OK", "Scratch", "Bow"],
                        [np.log(probs)]).inspect(np.zeros((32, 32, 3), dtype=np.uint8))
    assert result["verdict"] == "NG"
    assert result["defect_score"] == 0.65


def test_exported_classification_without_normal_class_requires_review():
    result = _inspector("classification", ["Scratch", "Bow"],
                        [np.array([[0.0, 1.0]], dtype=np.float32)]).inspect(
                            np.zeros((32, 32, 3), dtype=np.uint8))
    assert result["verdict"] == "REVIEW"
    assert "normal" in result["review_reason"]


@pytest.mark.parametrize("normal_name", ["정상", "양품"])
def test_exported_classification_recognizes_korean_normal_class(normal_name):
    probs = np.array([[0.9, 0.1]], dtype=np.float32)
    result = _inspector("classification", [normal_name, "불량"],
                        [np.log(probs)]).inspect(np.zeros((32, 32, 3), dtype=np.uint8))
    assert result["verdict"] == "OK"
    assert result["defect_score"] == 0.1


def test_anomaly_export_rejects_unverified_feature_only_runtime(tmp_path, monkeypatch):
    checkpoint = tmp_path / "best_model.pt"
    checkpoint.touch()
    monkeypatch.setattr(exporter, "locate_checkpoint", lambda _: checkpoint)
    monkeypatch.setattr(exporter, "load_checkpoint_and_reconstruct_model",
                        lambda _: (object(), {"task": "anomaly"}, object()))
    output = tmp_path / "packages"
    with pytest.raises(ValueError, match="PaDiM/PatchCore statistics"):
        exporter.export_runtime_package(job_id="job_example", output_base_dir=output)
    assert not output.exists()
