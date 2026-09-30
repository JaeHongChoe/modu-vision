"""
backend/engine/device.py (Proposed Remediation)

Universal Hardware Abstraction and Acceleration Layer.
Supports Metal MPS (Metal Performance Shaders), CUDA,
and transparent CPU fallback with unified telemetry, mixed precision management,
and Metal MPS float64 safe downcasting.
"""

from __future__ import annotations

import contextlib
import functools
import gc
import logging
import os
import platform
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

# Ensure MPS CPU fallback is active before any PyTorch operations
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

import psutil
import torch
import torch.nn as nn

logger = logging.getLogger("vision_ai_studio.device")


# ============================================================================
# Data Contracts & Telemetry Schemas
# ============================================================================

@dataclass(frozen=True)
class DeviceInfo:
    """Comprehensive hardware device status and capabilities."""
    device_type: str            # "mps", "cuda", or "cpu"
    device_name: str            # e.g., "Metal GPU", "CUDA GPU", "x86_64 CPU"
    is_accelerated: bool        # True if mps or cuda, False if cpu
    mps_available: bool
    cuda_available: bool
    cuda_device_count: int
    torch_version: str
    device_index: int = 0
    total_memory_mb: float = 0.0
    mps_fallback_enabled: bool = True


@dataclass(frozen=True)
class DeviceMemoryInfo:
    """Real-time device memory allocation statistics."""
    device_type: str
    allocated_mb: float
    reserved_mb: float
    total_mb: float
    free_mb: float
    percent_used: float


@dataclass(frozen=True)
class MemoryStats:
    """Telemetry payload matching spec_report / test harness contracts."""
    total_ram_gb: float
    used_ram_gb: float
    free_ram_gb: float
    ram_percent: float
    vram_allocated_mb: float    # 0.0 on CPU
    vram_reserved_mb: float     # 0.0 on CPU


@dataclass(frozen=True)
class HostTelemetry:
    """Telemetry payload matching WebSocket protocol in PROJECT.md."""
    cpu_percent: float
    memory_percent: float
    gpu_name: str
    gpu_memory_used_mb: float
    device_type: str


# ============================================================================
# Internal Helper Utilities
# ============================================================================

@functools.lru_cache(maxsize=1)
def _get_cpu_brand() -> str:
    """
    Retrieve human-readable CPU brand string cross-platform.
    Memoized with lru_cache(maxsize=1) to eliminate subprocess overhead
    during high-frequency WebSocket telemetry polling (10-50Hz).
    """
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                stderr=subprocess.DEVNULL
            )
            brand = out.decode().strip()
            if brand:
                return brand
        elif system == "Linux":
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
        elif system == "Windows":
            proc = platform.processor()
            if proc:
                return proc
    except Exception:
        pass
    return f"{platform.processor() or platform.machine()} CPU"


def _ensure_device(device: Optional[Union[torch.device, str]] = None) -> torch.device:
    """
    Normalize device parameter to a valid torch.device instance.
    Accepts None (resolves via get_device()), str (e.g. 'mps', 'cpu', 'cuda', 'cuda:0'),
    or torch.device.
    """
    if device is None:
        return get_device()
    if isinstance(device, str):
        return torch.device(device)
    return device


# ============================================================================
# Device Discovery & Selection
# ============================================================================

def get_device(requested: Optional[Union[str, torch.device]] = None) -> torch.device:
    """
    Returns the optimal torch.device following priority: CUDA > MPS > CPU.
    Accepts optional override: 'cuda', 'mps', 'cpu', 'cuda:0', torch.device, etc.
    Gracefully falls back to best available hardware if requested accelerator is unavailable.
    """
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

    if requested is not None:
        if isinstance(requested, torch.device):
            req_str = str(requested)
        elif isinstance(requested, str):
            req_str = requested.strip()
        else:
            req_str = str(requested).strip()

        if req_str and req_str.lower() != "auto":
            req = req_str.lower()
            if req == "cpu":
                return torch.device("cpu")
            if req.startswith("cuda"):
                if torch.cuda.is_available():
                    return torch.device(req)
                logger.warning(
                    "Requested CUDA device '%s' but CUDA is not available on this host. Falling back.",
                    requested,
                )
            elif req.startswith("mps"):
                if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    return torch.device("mps")
                logger.warning(
                    "Requested MPS device '%s' but MPS is not available on this host. Falling back.",
                    requested,
                )
            else:
                logger.warning("Unrecognized device preference '%s'. Falling back to auto-detection.", requested)

    # 1. CUDA
    if torch.cuda.is_available():
        idx = torch.cuda.current_device() if torch.cuda.device_count() > 0 else 0
        return torch.device(f"cuda:{idx}")

    # 2. Metal MPS
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")

    # 3. CPU Fallback
    return torch.device("cpu")


def detect_device(preferred: Optional[Union[str, torch.device]] = None) -> torch.device:
    """Alias for get_device() providing API compatibility with explorer and spec models."""
    return get_device(preferred)


# ============================================================================
# Device Information & Diagnostics
# ============================================================================

def get_device_info(device: Optional[Union[torch.device, str]] = None) -> DeviceInfo:
    """
    Query metadata and capabilities for the target compute device.
    Accepts either torch.device or str (e.g. 'mps', 'cpu', 'cuda').
    """
    device = _ensure_device(device)

    mps_avail = bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
    cuda_avail = bool(torch.cuda.is_available())
    cuda_count = torch.cuda.device_count() if cuda_avail else 0
    dev_index = device.index if device.index is not None else 0
    mps_fallback = os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK", "0") == "1"

    if device.type == "cuda" and cuda_avail:
        dev_name = torch.cuda.get_device_name(dev_index)
        props = torch.cuda.get_device_properties(dev_index)
        total_mem = props.total_memory / (1024.0 * 1024.0)
        is_accel = True
    elif device.type == "mps" and mps_avail:
        arch = platform.machine()
        chip_name = _get_cpu_brand()
        dev_name = f"Metal GPU ({chip_name} / {arch})"
        total_mem = psutil.virtual_memory().total / (1024.0 * 1024.0)
        if hasattr(torch.mps, "recommended_max_memory"):
            try:
                rec_max = torch.mps.recommended_max_memory() / (1024.0 * 1024.0)
                if rec_max > 0:
                    total_mem = rec_max
            except Exception:
                pass
        is_accel = True
    else:
        cpu_name = _get_cpu_brand()
        dev_name = f"Host CPU ({cpu_name})"
        total_mem = psutil.virtual_memory().total / (1024.0 * 1024.0)
        is_accel = False

    return DeviceInfo(
        device_type=device.type,
        device_name=dev_name,
        is_accelerated=is_accel,
        mps_available=mps_avail,
        cuda_available=cuda_avail,
        cuda_device_count=cuda_count,
        torch_version=torch.__version__,
        device_index=dev_index,
        total_memory_mb=round(total_mem, 2),
        mps_fallback_enabled=mps_fallback,
    )


# ============================================================================
# Memory Telemetry & Monitoring
# ============================================================================

def get_memory_stats(device: Optional[Union[torch.device, str]] = None) -> MemoryStats:
    """
    Returns real-time host RAM and device VRAM utilization in GB / MB.
    Accepts either torch.device or str (e.g. 'mps', 'cpu', 'cuda').
    """
    device = _ensure_device(device)

    vm = psutil.virtual_memory()
    total_ram = vm.total / (1024.0 ** 3)
    used_ram = vm.used / (1024.0 ** 3)
    free_ram = vm.available / (1024.0 ** 3)
    ram_pct = vm.percent

    vram_alloc = 0.0
    vram_res = 0.0

    if device.type == "cuda" and torch.cuda.is_available():
        vram_alloc = torch.cuda.memory_allocated(device) / (1024.0 ** 2)
        vram_res = torch.cuda.memory_reserved(device) / (1024.0 ** 2)
    elif device.type == "mps" and hasattr(torch, "mps"):
        try:
            if hasattr(torch.mps, "current_allocated_memory"):
                vram_alloc = torch.mps.current_allocated_memory() / (1024.0 ** 2)
            if hasattr(torch.mps, "driver_allocated_memory"):
                vram_res = torch.mps.driver_allocated_memory() / (1024.0 ** 2)
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


def get_memory_info(device: Optional[Union[torch.device, str]] = None) -> DeviceMemoryInfo:
    """
    Retrieve real-time memory usage (allocated, reserved, free) in megabytes.
    Accepts either torch.device or str (e.g. 'mps', 'cpu', 'cuda').
    """
    device = _ensure_device(device)

    dev_type = device.type

    if dev_type == "cuda" and torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated(device) / (1024.0 * 1024.0)
        reserved = torch.cuda.memory_reserved(device) / (1024.0 * 1024.0)
        total = torch.cuda.get_device_properties(device).total_memory / (1024.0 * 1024.0)
        free = max(0.0, total - allocated)
        pct = (allocated / total * 100.0) if total > 0 else 0.0
        return DeviceMemoryInfo(
            device_type="cuda",
            allocated_mb=round(allocated, 2),
            reserved_mb=round(reserved, 2),
            total_mb=round(total, 2),
            free_mb=round(free, 2),
            percent_used=round(pct, 2),
        )

    elif dev_type == "mps":
        allocated = 0.0
        driver_alloc = 0.0
        try:
            if hasattr(torch.mps, "current_allocated_memory"):
                allocated = torch.mps.current_allocated_memory() / (1024.0 * 1024.0)
            if hasattr(torch.mps, "driver_allocated_memory"):
                driver_alloc = torch.mps.driver_allocated_memory() / (1024.0 * 1024.0)
        except Exception:
            pass

        vm = psutil.virtual_memory()
        total_mb = vm.total / (1024.0 * 1024.0)
        free_mb = vm.available / (1024.0 * 1024.0)
        pct = (allocated / total_mb * 100.0) if total_mb > 0 else 0.0

        return DeviceMemoryInfo(
            device_type="mps",
            allocated_mb=round(allocated, 2),
            reserved_mb=round(driver_alloc, 2),
            total_mb=round(total_mb, 2),
            free_mb=round(free_mb, 2),
            percent_used=round(pct, 2),
        )

    else:
        vm = psutil.virtual_memory()
        total_mb = vm.total / (1024.0 * 1024.0)
        used_mb = vm.used / (1024.0 * 1024.0)
        free_mb = vm.available / (1024.0 * 1024.0)
        return DeviceMemoryInfo(
            device_type="cpu",
            allocated_mb=round(used_mb, 2),
            reserved_mb=round(used_mb, 2),
            total_mb=round(total_mb, 2),
            free_mb=round(free_mb, 2),
            percent_used=round(vm.percent, 2),
        )


def get_host_telemetry(device: Optional[Union[torch.device, str]] = None) -> HostTelemetry:
    """
    Produce a lightweight snapshot matching the WebSocket protocol in PROJECT.md:
    { cpu_percent, memory_percent, gpu_name, gpu_memory_used_mb, device_type }
    Accepts either torch.device or str (e.g. 'mps', 'cpu', 'cuda').
    """
    device = _ensure_device(device)

    mem_stats = get_memory_stats(device)
    dev_info = get_device_info(device)

    gpu_mem_used = mem_stats.vram_allocated_mb if dev_info.is_accelerated else 0.0
    gpu_name = dev_info.device_name if dev_info.is_accelerated else "None (CPU Mode)"

    return HostTelemetry(
        cpu_percent=round(psutil.cpu_percent(interval=None), 1),
        memory_percent=round(mem_stats.ram_percent, 1),
        gpu_name=gpu_name,
        gpu_memory_used_mb=round(gpu_mem_used, 1),
        device_type=device.type,
    )


# ============================================================================
# Device Cache Management
# ============================================================================

def clear_device_cache(device: Optional[Union[torch.device, str]] = None) -> None:
    """
    Flush accelerator caching allocators and trigger garbage collection.
    Invokes gc.collect() prior to emptying accelerator cache so unreferenced
    tensor allocations are recycled before the driver memory flush.
    Accepts either torch.device or str (e.g. 'mps', 'cpu', 'cuda').
    """
    device = _ensure_device(device)

    # 1. Collect unreferenced Python objects to release PyTorch tensor handles
    gc.collect()

    # 2. Release caching allocator pools back to the OS/driver
    if device.type == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()
    elif device.type == "mps" and hasattr(torch, "mps"):
        if hasattr(torch.mps, "empty_cache"):
            try:
                torch.mps.empty_cache()
            except Exception:
                pass


# ============================================================================
# Metal MPS Compatibility & Recursive Device Transfer
# ============================================================================

def to_device(
    obj: Union[torch.Tensor, nn.Module, Dict[str, Any], List[Any], Tuple[Any, ...], Any],
    device: Optional[Union[torch.device, str]] = None,
) -> Any:
    """
    Recursively moves tensors, neural network modules, or nested collection structures to target device.
    CRITICAL MPS INVARIANT: If target device is Metal MPS ('mps'), automatically
    downcasts float64 (double) tensors to float32 to prevent MPS TypeError runtime crashes.
    Accepts either torch.device or str (e.g. 'mps', 'cpu', 'cuda').
    """
    device = _ensure_device(device)

    if isinstance(obj, torch.Tensor):
        if device.type == "mps" and obj.dtype == torch.float64:
            obj = obj.to(dtype=torch.float32)
        return obj.to(device)

    if isinstance(obj, nn.Module):
        if device.type == "mps":
            # Sanitize float64 parameters across all submodules
            for param in obj.parameters():
                if param.dtype == torch.float64:
                    param.data = param.data.to(dtype=torch.float32)
            # Sanitize float64 buffers across all submodules
            for m in obj.modules():
                for buf_name, buf in m._buffers.items():
                    if buf is not None and buf.dtype == torch.float64:
                        m._buffers[buf_name] = buf.to(dtype=torch.float32)
        return obj.to(device)

    if isinstance(obj, dict):
        return {k: to_device(v, device) for k, v in obj.items()}

    if isinstance(obj, list):
        return [to_device(v, device) for v in obj]

    if isinstance(obj, tuple):
        return tuple(to_device(v, device) for v in obj)

    return obj


# ============================================================================
# Mixed Precision Autocast Context
# ============================================================================

@contextmanager
def autocast_context(
    device: Optional[Union[torch.device, str]] = None,
    enabled: bool = True,
) -> Generator[None, None, None]:
    """
    Unified mixed precision context manager for MPS, CUDA, and CPU.
    - CUDA: torch.autocast(device_type="cuda", dtype=torch.float16)
    - MPS: torch.autocast(device_type="mps", dtype=torch.float16)
    - CPU: torch.autocast(device_type="cpu", dtype=torch.bfloat16)
    Accepts either torch.device or str (e.g. 'mps', 'cpu', 'cuda').
    """
    device = _ensure_device(device)
    dev_type = device.type

    if not enabled:
        with contextlib.nullcontext():
            yield
    elif dev_type == "cuda" and torch.cuda.is_available():
        with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=True):
            yield
    elif dev_type == "mps" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        with torch.autocast(device_type="mps", dtype=torch.float16, enabled=True):
            yield
    elif dev_type == "cpu":
        with torch.autocast(device_type="cpu", dtype=torch.bfloat16, enabled=True):
            yield
    else:
        with contextlib.nullcontext():
            yield


class DynamicAMPContext:
    """
    Object-oriented unified AMP context manager for training loops.
    Supports both `amp = DynamicAMPContext(dev); with amp.autocast(): ...`
    and `with DynamicAMPContext(dev): ...` usage patterns.
    Accepts either torch.device or str (e.g. 'mps', 'cpu', 'cuda').
    """

    def __init__(self, device: Optional[Union[torch.device, str]] = None, enabled: bool = True):
        self.device = _ensure_device(device)
        self.enabled = enabled

        if not self.enabled:
            self._ctx = contextlib.nullcontext()
        elif self.device.type == "cuda" and torch.cuda.is_available():
            self._ctx = torch.autocast(device_type="cuda", dtype=torch.float16, enabled=True)
        elif self.device.type == "mps" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            self._ctx = torch.autocast(device_type="mps", dtype=torch.float16, enabled=True)
        elif self.device.type == "cpu":
            self._ctx = torch.autocast(device_type="cpu", dtype=torch.bfloat16, enabled=True)
        else:
            self._ctx = contextlib.nullcontext()

    def autocast(self):
        """Returns the hardware-specific autocast context manager."""
        return self._ctx

    def __enter__(self):
        return self._ctx.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._ctx.__exit__(exc_type, exc_val, exc_tb)
