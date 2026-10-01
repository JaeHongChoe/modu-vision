"""Trainer anomaly inference preserves native inputs and compact raw score evidence."""
import base64
import json
import zlib

import numpy as np
import torch

from backend.engine import trainer


class NoListMap(np.ndarray):
    def tolist(self):
        raise AssertionError('Native patch scores must not become a JSON float list')


class RecordedDetector:
    threshold = 0.5

    def __init__(self, semantics, values, score=0.5):
        self.model_metadata = {'map_semantics': semantics}
        self.values = values
        self.score = score
        self.input_shape = None
        self.input_device = None
        self.output_size = None

    def predict_anomaly_map(self, image, out_size=None):
        self.input_shape = tuple(image.shape)
        self.input_device = image.device.type
        self.output_size = out_size
        return self.values, self.score


def _checkpoint(tmp_path, threshold=.5):
    path = tmp_path / 'best_model.pt'
    torch.save({'model_state_dict': {'threshold': threshold}, 'image_size': [16, 16]}, path)
    return path


def test_dino_native_map_is_lossless_compressed_float32_without_list_or_mask(tmp_path, monkeypatch):
    height, width = 513, 769
    y, x = np.indices((height, width))
    original = ((y % 8 + x % 8) / 16).astype(np.float32)
    detector = RecordedDetector('patch_score', original.view(NoListMap))
    monkeypatch.setattr(trainer, 'reconstruct_anomaly_detector', lambda *args: detector)
    result = trainer.infer('anomaly', _checkpoint(tmp_path), np.zeros((height, width, 3), dtype=np.uint8), device='cpu')
    assert detector.input_shape == (3, height, width)
    assert detector.input_device == 'cpu'
    assert detector.output_size == (height, width)
    predictions = result.predictions
    assert predictions['map_semantics'] == 'patch_score'
    assert 'anomaly_map' not in predictions and 'mask' not in predictions
    encoded = predictions['anomaly_values']
    assert encoded['dtype'] == 'float32' and encoded['encoding'] == 'zlib_base64'
    assert encoded['shape'] == [height, width]
    restored = np.frombuffer(zlib.decompress(base64.b64decode(encoded['data'])), dtype='<f4').reshape(encoded['shape'])
    assert np.array_equal(restored, original)
    assert len(json.dumps(predictions)) < original.nbytes // 10
    assert predictions['is_anomaly'] is False, 'a calibrated threshold tie stays normal'


def test_legacy_pixel_map_preserves_list_mask_and_inclusive_threshold(tmp_path, monkeypatch):
    values = np.full((40, 48), 0.5, dtype=np.float32)
    detector = RecordedDetector('pixel_score', values)
    monkeypatch.setattr(trainer, 'reconstruct_anomaly_detector', lambda *args: detector)
    result = trainer.infer('anomaly', _checkpoint(tmp_path), np.zeros((40, 48, 3), dtype=np.uint8), device='cpu')
    predictions = result.predictions
    assert detector.input_shape == (3, 16, 16)
    assert predictions['map_semantics'] == 'pixel_score'
    assert predictions['anomaly_map'] == values.tolist()
    assert predictions['mask'] == np.ones_like(values, dtype=np.uint8).tolist()
    assert 'anomaly_values' not in predictions
    assert predictions['is_anomaly'] is True


def test_dino_compressed_payload_keeps_exact_calibrated_threshold(tmp_path, monkeypatch):
    threshold = 0.54321987654321
    detector = RecordedDetector('patch_score', np.full((40, 48), 0.125, dtype=np.float32), score=threshold)
    detector.threshold = threshold
    monkeypatch.setattr(trainer, 'reconstruct_anomaly_detector', lambda *args: detector)
    result = trainer.infer('anomaly_detection', _checkpoint(tmp_path, threshold), np.zeros((40, 48, 3), dtype=np.uint8),
                           threshold=None, device='cpu')
    assert result.predictions['threshold'] == threshold
    assert result.predictions['is_anomaly'] is False
    assert result.predictions['anomaly_score'] == threshold
