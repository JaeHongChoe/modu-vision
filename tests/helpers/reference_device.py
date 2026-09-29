"""
Reference implementation of backend/engine/device.py for testing harness.
Manages hardware discovery, memory telemetry, and tensor compatibility across
Apple Silicon MPS, NVIDIA CUDA, and fallback CPU.
"""

import contextlib
import os
import platform
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import psutil
import torch

# Invariant 1: Ensure MPS fallback is active before any tensor allocations
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"


@dataclass(frozen=True)
class DeviceInfo:
    device_type: str            # "mps", "cuda", or "cpu"
    device_name: str            # e.g., "Apple M-Series GPU", "NVIDIA GeForce...", "Intel Core..."
    is_accelerated: bool        # True if mps or cuda, False if cpu
    mps_available: bool
    cuda_available: bool
    cuda_device_count: int
    torch_version: str


@dataclass(frozen=True)
class MemoryStats:
    total_ram_gb: float
    used_ram_gb: float
    free_ram_gb: float
    ram_percent: float
    vram_allocated_mb: float    # 0.0 on CPU
    vram_reserved_mb: float     # 0.0 on CPU


def get_device(requested: Optional[str] = None) -> torch.device:
    """
    Returns optimal torch.device following priority: CUDA > MPS > CPU.
    Accepts optional override: 'cuda', 'mps', 'cpu', 'cuda:0', etc.
    Falls back gracefully if requested device is invalid or unavailable.
    """
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

    if requested:
        req = requested.strip().lower()
        if req == "cpu":
            return torch.device("cpu")
        if req.startswith("cuda"):
            if torch.cuda.is_available():
                return torch.device(req)
            # Fall back to default if CUDA requested but unavailable
        elif req.startswith("mps"):
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return torch.device("mps")
            # Fall back to default if MPS requested but unavailable

    # Default automatic priority
    if torch.cuda.is_available():
        return torch.device("cuda:0")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def get_device_info(device: Optional[torch.device] = None) -> DeviceInfo:
    """Returns comprehensive hardware device information."""
    if device is None:
        device = get_device()

    mps_avail = bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
    cuda_avail = bool(torch.cuda.is_available())
    cuda_count = torch.cuda.device_count() if cuda_avail else 0

    if device.type == "cuda" and cuda_avail:
        dev_name = torch.cuda.get_device_name(device)
        is_accel = True
    elif device.type == "mps" and mps_avail:
        arch = platform.machine()
        processor = platform.processor() or "Apple Silicon"
        dev_name = f"Apple Silicon ({processor} / {arch})"
        is_accel = True
    else:
        proc = platform.processor() or platform.machine() or "CPU"
        dev_name = f"Host CPU ({proc})"
        is_accel = False

    return DeviceInfo(
        device_type=device.type,
        device_name=dev_name,
        is_accelerated=is_accel,
        mps_available=mps_avail,
        cuda_available=cuda_avail,
        cuda_device_count=cuda_count,
        torch_version=torch.__version__,
    )


def get_memory_stats(device: Optional[torch.device] = None) -> MemoryStats:
    """Returns real-time host RAM and device VRAM utilization in GB / MB."""
    if device is None:
        device = get_device()

    vm = psutil.virtual_memory()
    total_ram = vm.total / (1024 ** 3)
    used_ram = vm.used / (1024 ** 3)
    free_ram = vm.available / (1024 ** 3)
    ram_pct = vm.percent

    vram_alloc = 0.0
    vram_res = 0.0

    if device.type == "cuda" and torch.cuda.is_available():
        vram_alloc = torch.cuda.memory_allocated(device) / (1024 ** 2)
        vram_res = torch.cuda.memory_reserved(device) / (1024 ** 2)
    elif device.type == "mps" and hasattr(torch.mps, "current_allocated_memory"):
        try:
            vram_alloc = torch.mps.current_allocated_memory() / (1024 ** 2)
            if hasattr(torch.mps, "driver_allocated_memory"):
                vram_res = torch.mps.driver_allocated_memory() / (1024 ** 2)
            else:
                vram_res = vram_alloc
        except Exception:
            vram_alloc = 0.0
            vram_res = 0.0

    return MemoryStats(
        total_ram_gb=round(total_ram, 2),
        used_ram_gb=round(used_ram, 2),
        free_ram_gb=round(free_ram, 2),
        ram_percent=round(ram_pct, 1),
        vram_allocated_mb=round(vram_alloc, 2),
        vram_reserved_mb=round(vram_res, 2),
    )


def to_device(
    obj: Union[torch.Tensor, torch.nn.Module, Dict[str, Any], List[Any], Tuple[Any, ...], Any],
    device: torch.device,
) -> Any:
    """
    Moves tensors, modules, or collections to target device.
    CRITICAL MPS RULE: If device is 'mps' and tensor dtype is float64,
    automatically downcasts to float32 to prevent MPS TypeError crashes.
    """
    if isinstance(obj, torch.Tensor):
        if device.type == "mps" and obj.dtype == torch.float64:
            obj = obj.to(dtype=torch.float32)
        return obj.to(device)

    if isinstance(obj, torch.nn.Module):
        return obj.to(device)

    if isinstance(obj, dict):
        return {k: to_device(v, device) for k, v in obj.items()}

    if isinstance(obj, list):
        return [to_device(v, device) for v in obj]

    if isinstance(obj, tuple):
        return tuple(to_device(v, device) for v in obj)

    return obj


def clear_device_cache(device: torch.device) -> None:
    """Frees cached memory via torch.cuda.empty_cache() or torch.mps.empty_cache()."""
    if device.type == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()
    elif device.type == "mps" and hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
        try:
            torch.mps.empty_cache()
        except Exception:
            pass


class DynamicAMPContext:
    """
    Provides unified torch.autocast context across hardware:
    - CUDA: autocast(device_type='cuda', dtype=torch.float16)
    - MPS: autocast(device_type='mps', dtype=torch.float16)
    - CPU: autocast(device_type='cpu', dtype=torch.bfloat16)
    """

    def __init__(self, device: torch.device, enabled: bool = True):
        self.device = device
        self.enabled = enabled

        if not self.enabled:
            self.ctx = contextlib.nullcontext()
        elif device.type == "cuda" and torch.cuda.is_available():
            self.ctx = torch.autocast(device_type="cuda", dtype=torch.float16)
        elif device.type == "mps" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            self.ctx = torch.autocast(device_type="mps", dtype=torch.float16)
        elif device.type == "cpu":
            self.ctx = torch.autocast(device_type="cpu", dtype=torch.bfloat16)
        else:
            self.ctx = contextlib.nullcontext()

    def autocast(self):
        return self.ctx
