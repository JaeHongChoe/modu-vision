"""Cancellation must stop anomaly fitting before its long inner loops finish."""

from __future__ import annotations

import threading

import pytest
import torch

from backend.engine.anomaly.padim import PaDiMDetector
from backend.engine.anomaly.patchcore import PatchCoreDetector


class _FeatureExtractor(torch.nn.Module):
    def __init__(self, on_forward=None):
        super().__init__()
        self.calls = 0
        self.on_forward = on_forward

    def forward(self, images):
        self.calls += 1
        if self.on_forward is not None:
            self.on_forward()
        return torch.arange(12, dtype=torch.float32).reshape(1, 3, 2, 2).repeat(len(images), 1, 1, 1)


def _padim(extractor):
    detector = object.__new__(PaDiMDetector)
    detector.device = torch.device("cpu")
    detector.feature_extractor = extractor
    detector.sub_dims = torch.tensor([0, 1])
    detector.regularizer = 0.01
    detector.mean = None
    detector.cov_inv = None
    detector.threshold = 0.0
    return detector


def _patchcore(extractor):
    detector = object.__new__(PatchCoreDetector)
    detector.device = torch.device("cpu")
    detector.feature_extractor = extractor
    detector.coreset_sampling_ratio = 1.0
    detector.max_coreset_size = 32
    detector.seed = 42
    detector.coreset = None
    detector.threshold = 0.0
    return detector


def _batches(count=2):
    return [torch.ones((1, 3, 2, 2)) for _ in range(count)]


def test_padim_stops_between_embedding_batches():
    from backend.engine.anomaly.cancellation import AnomalyFitCancelled

    cancelled = threading.Event()
    extractor = _FeatureExtractor(on_forward=cancelled.set)
    detector = _padim(extractor)

    with pytest.raises(AnomalyFitCancelled):
        detector.fit(_batches(), cancellation_requested=cancelled.is_set)

    assert extractor.calls == 1
    assert detector.mean is None
    assert detector.cov_inv is None


def test_patchcore_stops_during_greedy_coreset_loop(monkeypatch):
    from backend.engine.anomaly.cancellation import AnomalyFitCancelled

    cancelled = threading.Event()
    detector = _patchcore(_FeatureExtractor())
    real_minimum = torch.minimum
    coreset_iterations = 0

    def minimum_then_cancel(left, right):
        nonlocal coreset_iterations
        coreset_iterations += 1
        cancelled.set()
        return real_minimum(left, right)

    monkeypatch.setattr(torch, "minimum", minimum_then_cancel)

    with pytest.raises(AnomalyFitCancelled):
        detector.fit(_batches(), cancellation_requested=cancelled.is_set)

    assert coreset_iterations == 1
    assert detector.coreset is None


@pytest.mark.parametrize("detector_factory", [_padim, _patchcore])
def test_fit_stops_during_normal_score_calibration(detector_factory):
    from backend.engine.anomaly.cancellation import AnomalyFitCancelled

    cancelled = threading.Event()
    detector = detector_factory(_FeatureExtractor())
    scores_seen = []

    def score_one_image(image):
        scores_seen.append(len(scores_seen))
        cancelled.set()
        return None, 0.1

    detector.predict_anomaly_map = score_one_image

    with pytest.raises(AnomalyFitCancelled):
        detector.fit(_batches(), cancellation_requested=cancelled.is_set)

    assert scores_seen == [0]


def test_trainer_reports_fit_cancellation_as_aborted(tmp_path, monkeypatch):
    from backend.engine.anomaly.cancellation import AnomalyFitCancelled
    import backend.engine.trainer as training_module

    events = []

    class Callback:
        def on_training_start(self, config):
            events.append("started")

        def on_training_aborted(self, epoch, reason):
            events.append("aborted")

        def on_error(self, error, context):
            events.append("error")

        def on_training_completed(self, job_id, duration_seconds, best_metric, model_path):
            events.append("completed")

    class FakeDataset:
        def __init__(self, root_dir, split, **kwargs):
            self.split = split
            self.samples = []

    trainer = training_module.UnifiedAutoMLTrainer(
        "anomaly", tmp_path, tmp_path / "models", device="cpu", callback=Callback(),
    )

    class CancellingModel:
        def __init__(self, **kwargs):
            pass

        def fit(self, dataloader, cancellation_requested=None, calibration_dataset=None):
            assert cancellation_requested is not None
            trainer.abort()
            assert cancellation_requested()
            raise AnomalyFitCancelled("Anomaly fit cancelled")

    monkeypatch.setattr(training_module, "AnomalyDataset", FakeDataset)
    monkeypatch.setattr(training_module, "PaDiMDetector", CancellingModel)
    monkeypatch.setattr(training_module, "PatchCoreDetector", CancellingModel)
    monkeypatch.setattr(training_module, "create_industrial_transforms", lambda **kwargs: None)
    monkeypatch.setattr(training_module, "create_dataloader", lambda dataset, **kwargs: _batches())

    assert trainer.train("cancel-test") == {"status": "aborted", "epoch": 0}
    assert events == ["started", "aborted"]
    assert not (tmp_path / "models" / "best_model.pt").exists()
