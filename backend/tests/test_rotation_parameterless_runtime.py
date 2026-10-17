"""Regression at original rotation + CompiledModel host tensor boundary.

The external OpenVINO compile ABI is a deterministic test double; the original
CompiledModel.forward, rotation preprocessing, tensor conversion and native
geometry all execute. These controls claim no actual IR or GPU qualification.
"""
from pathlib import Path
import hashlib
import os
import runpy
import sys
import threading

SOURCE = Path(os.environ.get("ROTATION_RUNTIME_SOURCE_ROOT") or Path(__file__).resolve().parents[2]).resolve(strict=True)
sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE))
if not Path(__file__).resolve().is_relative_to(SOURCE):
    runpy.run_path(str(SOURCE / "backend/conftest.py"), run_name="rotation_runtime_test_isolation")

import numpy as np
import pytest
import torch
from backend.engine import rotation
from backend.engine.openvino_runtime import CompiledModel


class CompiledDirectionABI:
    outputs = (0,)

    def __init__(self):
        self.inputs = []

    def output(self, index):
        return self.outputs[index]

    def __call__(self, value):
        assert isinstance(value, np.ndarray)
        assert value.dtype == np.float32 and value.shape[1:] == (3, 16, 16)
        self.inputs.append(value.copy())
        return {0: np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(value), 1))}


def original_parameterless_adapter():
    # Skip external compile/file I/O only; use the ORIGINAL forward method and
    # its real CPU NumPy/torch output bridge, not a replacement forward mock.
    model = CompiledModel.__new__(CompiledModel)
    torch.nn.Module.__init__(model)
    model._lock = threading.Lock()
    model.tree = {"type": "tensor", "dtype": "float32"}
    model.compiled = CompiledDirectionABI()
    assert list(model.parameters()) == []
    return model


def loader_fixture(tmp_path, monkeypatch, model):
    checkpoint = tmp_path / "best_model.pt"
    checkpoint.write_bytes(b"controlled loader-boundary fixture; not trained weights")
    calls = []

    def load(path, device):
        calls.append((path, device))
        return model, {"image_size": 16}

    monkeypatch.setattr(rotation, "load_rotation_model", load)
    pixels = np.full((24, 32, 3), 80, dtype=np.uint8)
    return checkpoint, pixels, calls


def test_rotation_original_parameterless_openvino_bridge_preserves_native_prediction(tmp_path, monkeypatch):
    model = original_parameterless_adapter()
    checkpoint, pixels, calls = loader_fixture(tmp_path, monkeypatch, model)
    result = rotation.predict_rotation_array(checkpoint, pixels, device="cpu")
    assert calls == [(checkpoint, "cpu")]
    assert len(model.compiled.inputs) == 1
    assert result["correction_deg"] == pytest.approx(0)
    assert result["checkpoint_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert result["source_size"] == result["output_size"] == [32, 24]
    np.testing.assert_array_equal(result["aligned_image"], pixels)
    np.testing.assert_array_equal(result["transform"], np.eye(3))
    np.testing.assert_array_equal(result["inverse_transform"], np.eye(3))


def test_rotation_parameterized_model_keeps_original_parameter_device_semantics(tmp_path, monkeypatch):
    class TorchDirection(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.direction = torch.nn.Parameter(torch.tensor([1.0, 0.0]))
            self.inputs = []

        def forward(self, value):
            self.inputs.append(value.device)
            return self.direction.unsqueeze(0).expand(len(value), -1)

    model = TorchDirection()
    checkpoint, pixels, calls = loader_fixture(tmp_path, monkeypatch, model)
    # Controlled loader returns a real CPU-parameter model. Existing semantics
    # choose that actual parameter device, without re-resolving this argument.
    result = rotation.predict_rotation_array(checkpoint, pixels, device="cuda:999999")
    assert calls == [(checkpoint, "cuda:999999")]
    assert model.inputs == [torch.device("cpu")]
    assert result["correction_deg"] == pytest.approx(0)


@pytest.mark.parametrize("device", ["cuda:unknown", "openvino:GPU"])
def test_rotation_parameterless_model_rejects_unknown_torch_input_device(tmp_path, monkeypatch, device):
    model = original_parameterless_adapter()
    checkpoint, pixels, calls = loader_fixture(tmp_path, monkeypatch, model)
    with pytest.raises(ValueError, match="Runtime device must"):
        rotation.predict_rotation_array(checkpoint, pixels, device=device)
    assert calls == [(checkpoint, device)]
    assert model.compiled.inputs == []


def test_rotation_parameterless_model_does_not_fall_back_when_requested_cuda_unavailable(tmp_path, monkeypatch):
    model = original_parameterless_adapter()
    checkpoint, pixels, calls = loader_fixture(tmp_path, monkeypatch, model)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(ValueError, match="Requested CUDA runtime device is unavailable"):
        rotation.predict_rotation_array(checkpoint, pixels, device="cuda:0")
    assert calls == [(checkpoint, "cuda:0")]
    assert model.compiled.inputs == []
