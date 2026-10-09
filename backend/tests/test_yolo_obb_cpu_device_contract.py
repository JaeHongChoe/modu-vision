"""CPU device routing uses the real SDK selector; no native model is loaded.

The model-format provider below is explicitly controlled. Only Ultralytics
get_cfg/select_device execute their genuine device handling; neither is patched.
Thread restoration belongs to test cleanup after assertions, never the adapter.
"""
from contextlib import contextmanager
from types import SimpleNamespace
import math

import numpy as np
import pytest
import torch

from backend.engine import yolo_obb_adapter as adapter


class DeviceText:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def __str__(self):
        self.calls += 1
        return self.text


def _meta():
    return {'checkpoint_sha256': 'a'*64, 'image_size': 32,
            'class_names': ['background', 'part']}


@pytest.mark.parametrize('device_kind', ['text', 'resolved', 'coercible'])
def test_predict_exact_cpu_keeps_real_sdk_threads_and_geometry(monkeypatch, device_kind):
    # Import the unchanged SDK before applying the test-owned numeric bound.
    from ultralytics.cfg import get_cfg
    from ultralytics.utils.torch_utils import select_device

    meta = _meta()
    recorded = {}
    image = np.empty((6, 8, 3), dtype=np.uint8)
    image[:] = [7, 11, 19]
    original_pixels = image.copy()
    device = ('cpu' if device_kind == 'text' else torch.device('cpu')
              if device_kind == 'resolved' else DeviceText('cpu'))

    class FormatProvider:
        def predict(self, **options):
            recorded.update(options)
            # These are genuine provider config and selection functions.
            cfg = get_cfg(overrides={k: v for k, v in options.items() if k != 'source'})
            recorded['cfg_device'] = cfg.device
            recorded['selected_device'] = select_device(cfg.device, verbose=False)
            recorded['threads_after_selection'] = torch.get_num_threads()
            obb = SimpleNamespace(
                xywhr=torch.tensor([[3., 4., 4., 4., math.pi/4]]),
                xyxyxyxy=torch.tensor([[[1., 2.], [5., 2.], [5., 6.], [1., 6.]]]),
                cls=torch.tensor([1.]), conf=torch.tensor([.75]))
            return [SimpleNamespace(obb=obb)]

    @contextmanager
    def format_model(checkpoint, verified=None):
        assert verified == (meta, {})
        yield FormatProvider()

    monkeypatch.setattr(adapter, '_verified_payload', lambda checkpoint: (meta, {}))
    monkeypatch.setattr(adapter, '_model', format_model)
    prior_threads = torch.get_num_threads()
    interop_before = torch.get_num_interop_threads()
    try:
        torch.set_num_threads(1)
        result = adapter.predict_yolo_array('controlled-format-only.pt', image, device=device)
        # The old adapter uses the real string path and raises this value on
        # multi-core hosts; exact device type remains causal on any host.
        assert recorded['threads_after_selection'] == 1
        assert torch.get_num_threads() == 1
        assert torch.get_num_interop_threads() == interop_before
        assert isinstance(recorded['device'], torch.device)
        assert recorded['device'].type == 'cpu'
        assert recorded['cfg_device'] is recorded['device']
        assert recorded['selected_device'] is recorded['device']
        assert recorded['imgsz'] == 32 and recorded['conf'] == .5
        assert recorded['max_det'] == 10000
        assert recorded['verbose'] is False and recorded['save'] is False
        assert recorded['source'].flags.c_contiguous
        assert np.array_equal(recorded['source'], original_pixels[:, :, ::-1])
        assert np.array_equal(image, original_pixels)
        assert result['image_size'] == [8, 6]
        assert result['model_sha256'] == meta['checkpoint_sha256']
        assert result['coordinate_space'] == 'original_image_pixels'
        assert result['direction_supported'] is False
        assert result['detections'][0]['label'] == 'part'
        assert result['detections'][0]['confidence'] == pytest.approx(.75)
        assert result['detections'][0]['box'] == pytest.approx(
            {'cx': 3., 'cy': 4., 'width': 4., 'height': 4., 'angle_deg': 45.}, abs=1e-5)
        assert result['detections'][0]['axis_aligned_box'] == [1., 2., 5., 6.]
        if isinstance(device, DeviceText):
            assert device.calls == 1
    finally:
        # Test-local cleanup after all observations, not runtime repair.
        torch.set_num_threads(prior_threads)


@pytest.mark.parametrize('text', [
    'cuda', 'cuda:0', '0', '0,1', 'mps', 'mps:0', 'CPU', 'cpu:0', ' cpu ', '',
])
def test_noncanonical_or_noncpu_device_keeps_exact_original_string(text):
    value = DeviceText(text)
    actual = adapter._prediction_device(value)
    assert type(actual) is str
    assert actual == text
    assert value.calls == 1


def test_metadata_refusal_precedes_device_coercion(monkeypatch):
    meta = _meta()
    value = DeviceText('cpu')
    monkeypatch.setattr(adapter, '_verified_payload', lambda checkpoint: (meta, {}))
    monkeypatch.setattr(adapter, '_model', lambda *a, **k: pytest.fail('metadata guard must run first'))
    with pytest.raises(ValueError, match='checkpoint changed'):
        adapter.predict_yolo_array('controlled-format-only.pt', np.zeros((2, 2, 3), dtype=np.uint8),
                                   device=value, meta={'checkpoint_sha256': 'b'*64})
    assert value.calls == 0


def test_model_authority_refusal_keeps_primary_object_before_device_coercion(monkeypatch):
    meta = _meta()
    value = DeviceText('cpu')
    primary = ValueError('controlled original model authority refusal')
    monkeypatch.setattr(adapter, '_verified_payload', lambda checkpoint: (meta, {}))

    @contextmanager
    def refused_model(*args, **kwargs):
        raise primary
        yield  # keep the contextmanager entry point; body never continues

    monkeypatch.setattr(adapter, '_model', refused_model)
    with pytest.raises(ValueError) as caught:
        adapter.predict_yolo_array('controlled-format-only.pt', np.zeros((2, 2, 3), dtype=np.uint8),
                                   device=value)
    assert caught.value is primary
    assert value.calls == 0
