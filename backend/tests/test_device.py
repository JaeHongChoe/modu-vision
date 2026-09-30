"""
Tier 1 & Tier 2 Automated Test Suite for Feature F02: Hardware Abstraction & Acceleration.
Covers hardware discovery (Metal MPS / CUDA / CPU), memory telemetry,
MPS float64 safe downcasting, nested device transfer, AMP context, and numerical invariants.
"""

import os
import platform
import pytest
import torch
import torch.nn as nn


# ============================================================================
# Tier 1: Primary Feature Coverage Tests (>=5 tests)
# ============================================================================

def test_get_device_default(device_module):
    """Tier 1: Verifies default hardware discovery returns a valid torch.device."""
    dev = device_module.get_device()
    assert isinstance(dev, torch.device)
    assert dev.type in ["cuda", "mps", "cpu"]

    # On arm64 macOS, verify MPS is prioritized if available
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            assert dev.type == "mps"


def test_get_device_explicit_cpu(device_module):
    """Tier 1: Verifies transparent CPU mode can be explicitly forced."""
    dev = device_module.get_device("cpu")
    assert isinstance(dev, torch.device)
    assert dev.type == "cpu"


def test_device_info_schema(device_module):
    """Tier 1: Verifies DeviceInfo data contract and property types."""
    info = device_module.get_device_info()
    assert hasattr(info, "device_type")
    assert hasattr(info, "device_name")
    assert hasattr(info, "is_accelerated")
    assert hasattr(info, "mps_available")
    assert hasattr(info, "cuda_available")
    assert hasattr(info, "cuda_device_count")
    assert hasattr(info, "torch_version")

    assert info.device_type in ["cuda", "mps", "cpu"]
    assert isinstance(info.is_accelerated, bool)
    assert isinstance(info.mps_available, bool)
    assert isinstance(info.cuda_available, bool)
    assert isinstance(info.cuda_device_count, int)
    assert len(info.device_name) > 0
    assert len(info.torch_version) > 0


def test_memory_stats_telemetry(device_module):
    """Tier 1: Verifies host RAM and device VRAM telemetry query."""
    stats = device_module.get_memory_stats()
    assert hasattr(stats, "total_ram_gb")
    assert hasattr(stats, "used_ram_gb")
    assert hasattr(stats, "free_ram_gb")
    assert hasattr(stats, "ram_percent")
    assert hasattr(stats, "vram_allocated_mb")
    assert hasattr(stats, "vram_reserved_mb")

    assert stats.total_ram_gb > 0.0
    assert stats.used_ram_gb > 0.0
    assert 0.0 <= stats.ram_percent <= 100.0
    assert stats.vram_allocated_mb >= 0.0
    assert stats.vram_reserved_mb >= 0.0


def test_dynamic_amp_context(device_module):
    """Tier 1: Verifies DynamicAMPContext executes mixed precision forward pass."""
    dev = device_module.get_device()
    amp = device_module.DynamicAMPContext(dev, enabled=True)

    with amp.autocast():
        a = torch.randn(8, 8, device=dev, dtype=torch.float32)
        b = torch.randn(8, 8, device=dev, dtype=torch.float32)
        c = torch.matmul(a, b)
        assert c.shape == (8, 8)
        assert not torch.isnan(c).any()


# ============================================================================
# Tier 2: Boundary & Corner Cases Tests (>=5 tests)
# ============================================================================

def test_get_device_invalid_string_fallback(device_module):
    """Tier 2: Requesting an unknown device string gracefully falls back to default/CPU."""
    dev = device_module.get_device("unknown_accelerator_x99")
    assert isinstance(dev, torch.device)
    assert dev.type in ["cuda", "mps", "cpu"]


def test_mps_float64_auto_downcast(device_module):
    """
    Tier 2: CRITICAL MPS INVARIANT.
    Metal MPS does not natively support float64.
    to_device() must automatically downcast float64 to float32 when device is mps.
    """
    mps_available = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
    target_dev = torch.device("mps") if mps_available else torch.device("cpu")

    t_f64 = torch.tensor([1.25, 2.50, 3.75, 5.00], dtype=torch.float64)
    moved = device_module.to_device(t_f64, target_dev)

    assert moved.device.type == target_dev.type
    if target_dev.type == "mps":
        assert moved.dtype == torch.float32
        assert torch.allclose(moved.cpu(), t_f64.to(torch.float32))
    else:
        assert moved.dtype == torch.float64


def test_to_device_nested_structures(device_module):
    """Tier 2: Verifies recursive device placement of dicts, lists, and tuples."""
    dev = device_module.get_device()
    nested_payload = {
        "img": torch.ones(3, 16, 16),
        "boxes": [torch.tensor([0, 0, 10, 10]), torch.tensor([5, 5, 12, 12])],
        "meta": ("item_1", torch.tensor([42])),
        "label_str": "scratch",
    }

    result = device_module.to_device(nested_payload, dev)

    assert result["img"].device.type == dev.type
    assert result["boxes"][0].device.type == dev.type
    assert result["boxes"][1].device.type == dev.type
    assert result["meta"][0] == "item_1"
    assert result["meta"][1].device.type == dev.type
    assert result["label_str"] == "scratch"


def test_clear_device_cache(device_module):
    """Tier 2: Verifies cache clearing executes cleanly without crashing."""
    dev = device_module.get_device()
    device_module.clear_device_cache(dev)
    # Also verify on CPU explicitly
    device_module.clear_device_cache(torch.device("cpu"))


def test_mps_fallback_env_flag(device_module):
    """Tier 2: Verifies that PYTORCH_ENABLE_MPS_FALLBACK is set to '1'."""
    assert os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1"


def test_tensor_computation_and_backward(device_module):
    """Tier 2: Verifies Conv2d, ReLU, and backward gradient pass on device."""
    dev = device_module.get_device()
    model = nn.Sequential(
        nn.Conv2d(3, 8, kernel_size=3, padding=1),
        nn.ReLU(),
        nn.AdaptiveAvgPool2d((1, 1)),
        nn.Flatten(),
        nn.Linear(8, 2),
    )
    model = device_module.to_device(model, dev)

    inputs = torch.randn(2, 3, 32, 32, device=dev, dtype=torch.float32)
    targets = torch.tensor([0, 1], device=dev, dtype=torch.int64)

    outputs = model(inputs)
    criterion = nn.CrossEntropyLoss()
    loss = criterion(outputs, targets)

    assert not torch.isnan(loss)
    assert not torch.isinf(loss)

    loss.backward()
    for param in model.parameters():
        assert param.grad is not None
        assert not torch.isnan(param.grad).any()


def test_tensor_transfer_roundtrip_precision(device_module):
    """Tier 2: Verifies bit-exact numeric preservation across CPU -> Device -> CPU roundtrip."""
    dev = device_module.get_device()

    t_f32 = torch.linspace(0.0, 1.0, 100, dtype=torch.float32)
    t_i64 = torch.arange(100, dtype=torch.int64)
    t_u8 = torch.randint(0, 256, (100,), dtype=torch.uint8)

    # Move to device and back
    r_f32 = device_module.to_device(t_f32, dev).cpu()
    r_i64 = device_module.to_device(t_i64, dev).cpu()
    r_u8 = device_module.to_device(t_u8, dev).cpu()

    assert torch.equal(t_f32, r_f32)
    assert torch.equal(t_i64, r_i64)
    assert torch.equal(t_u8, r_u8)


def test_detect_device_auto(device_module):
    """Tier 1: Verifies detect_device alias returns valid torch.device."""
    dev = device_module.detect_device()
    assert isinstance(dev, torch.device)
    assert dev.type in ("mps", "cuda", "cpu")


def test_memory_info_telemetry(device_module):
    """Tier 1: Verifies get_memory_info returns DeviceMemoryInfo dataclass."""
    mem = device_module.get_memory_info()
    assert mem.allocated_mb >= 0.0
    assert mem.reserved_mb >= 0.0
    assert mem.total_mb > 0.0
    assert 0.0 <= mem.percent_used <= 100.0


def test_host_telemetry_schema(device_module):
    """Tier 1: Verifies get_host_telemetry matches WebSocket schema."""
    telem = device_module.get_host_telemetry()
    assert hasattr(telem, "cpu_percent")
    assert hasattr(telem, "memory_percent")
    assert hasattr(telem, "gpu_name")
    assert hasattr(telem, "gpu_memory_used_mb")
    assert hasattr(telem, "device_type")
    assert 0.0 <= telem.cpu_percent <= 100.0
    assert 0.0 <= telem.memory_percent <= 100.0


def test_autocast_context_forward_pass(device_module):
    """Tier 1: Verifies autocast_context context manager execution."""
    dev = device_module.get_device()
    layer = nn.Linear(32, 16)
    layer = device_module.to_device(layer, dev)
    x = torch.randn(8, 32, device=dev)

    with device_module.autocast_context(dev, enabled=True):
        out = layer(x)
        assert out.shape == (8, 16)
        assert not torch.isnan(out).any()

