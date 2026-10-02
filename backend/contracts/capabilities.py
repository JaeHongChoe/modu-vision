"""Worker capabilities and support decisions (S1-05).

A worker (this computer, an SSH host, a container, later a pull worker) advertises what it actually has: operating
system, architecture, devices, which model families and stages its installed runtime can run, its runtime digest and
weight inventory. A combination is one of four states, never guessed:

- ``verified``: a preflight of this task, stage and device passed on this worker;
- ``unverified``: the runtime has what it needs, but no preflight has run yet;
- ``not_installed``: a required package or weight is missing on this worker;
- ``unsupported``: the worker cannot run it at all (no such device, a stage the family does not have, MPS off macOS).

A request is decided for the device it names. There is no fallback: a CUDA request on a worker without CUDA is
refused with its reason, never run on the CPU or on another worker.
"""
from __future__ import annotations

import platform as _platform
import time
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1
SupportState = Literal['verified', 'unverified', 'not_installed', 'unsupported']
DeviceKind = Literal['cpu', 'cuda', 'mps']
STAGES = ('train', 'evaluate', 'infer', 'search', 'export')
_FAMILY_STAGE = {'train': 'train', 'evaluate': 'evaluate', 'infer': 'flow', 'export': 'export'}


class DeviceInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: DeviceKind
    name: str
    index: Optional[int] = None
    memory_mb: Optional[int] = None


class WorkerCapabilities(BaseModel):
    model_config = ConfigDict(extra='forbid')
    protocol_version: int = PROTOCOL_VERSION
    worker_id: str = Field(min_length=1, max_length=128)
    transport: Literal['local', 'ssh', 'docker', 'pull']
    os: Literal['windows', 'linux', 'darwin']
    arch: str
    devices: list[DeviceInfo]
    tasks: dict[str, dict[str, SupportState]]
    missing: dict[str, list[str]] = Field(default_factory=dict)
    runtime_digest: Optional[str] = None
    weight_inventory: dict[str, str] = Field(default_factory=dict)
    verified: dict[str, float] = Field(default_factory=dict)  # "task:stage:device" -> time a preflight passed
    probed_at: float


@dataclass(frozen=True)
class SupportDecision:
    state: SupportState
    reason: str


def _os_name(system: str) -> str:
    value = system.lower()
    return 'windows' if value.startswith('win') else 'darwin' if value == 'darwin' else 'linux'


def probe_devices(os_name: str, torch_module: Any = None) -> list[DeviceInfo]:
    """The devices this runtime can use; CUDA as the installed torch reports it, MPS only on macOS when available."""
    devices = [DeviceInfo(kind='cpu', name=_platform.processor() or _platform.machine() or 'cpu')]
    torch = torch_module
    if torch is None:
        if find_spec('torch') is None:
            return devices
        import torch  # noqa: PLC0415 - only when the runtime has it
    try:
        if torch.cuda.is_available():
            for index in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(index)
                devices.append(DeviceInfo(kind='cuda', name=str(props.name), index=index,
                                          memory_mb=int(props.total_memory // (1024 * 1024))))
    except Exception:  # a broken driver reports no CUDA device rather than a guessed one
        pass
    if os_name == 'darwin':
        mps = getattr(getattr(torch, 'backends', None), 'mps', None)
        try:
            if mps is not None and mps.is_built() and mps.is_available():
                devices.append(DeviceInfo(kind='mps', name='Apple GPU (MPS)'))
        except Exception:
            pass
    return devices


def probe_local(worker_id: str = 'local', *, families: Optional[list[dict]] = None, system: Optional[str] = None,
                torch_module: Any = None, has_torch: Optional[bool] = None, runtime_digest: Optional[str] = None,
                verified: Optional[dict[str, float]] = None, clock: Callable[[], float] = time.time) -> WorkerCapabilities:
    """This computer's capabilities from its installed runtime; nothing is downloaded or trained."""
    if families is None:
        from backend.engine.model_catalog import model_family_catalog
        families = model_family_catalog()['families']
    os_name = _os_name(system or _platform.system())
    torch_present = (find_spec('torch') is not None) if has_torch is None else has_torch
    tasks, missing = {}, {}
    for family in families:
        needs = list(family.get('missing_dependencies') or []) + ([] if torch_present else ['torch'])
        stages = {}
        for stage in STAGES:
            available = (family.get('automated_training') if stage == 'search'
                         else _FAMILY_STAGE[stage] in (family.get('stages') or []))
            stages[stage] = 'unsupported' if not available else 'not_installed' if needs else 'unverified'
        tasks[family['task']] = stages
        if needs:
            missing[family['task']] = needs
    devices = probe_devices(os_name, torch_module) if torch_present else [DeviceInfo(kind='cpu', name=_platform.machine() or 'cpu')]
    return WorkerCapabilities(worker_id=worker_id, transport='local', os=os_name, arch=_platform.machine() or 'unknown',
                              devices=devices, tasks=tasks, missing=missing, runtime_digest=runtime_digest,
                              verified=dict(verified or {}), probed_at=clock())


def decide(capabilities: WorkerCapabilities, task: str, stage: str, device: str) -> SupportDecision:
    """The support state of exactly this request on this worker; never a fallback to another device or worker."""
    if stage not in STAGES:
        return SupportDecision('unsupported', f'알 수 없는 단계입니다: {stage}')
    if device == 'mps' and capabilities.os != 'darwin':
        return SupportDecision('unsupported', 'MPS는 macOS에서만 제공됩니다.')
    if device not in {item.kind for item in capabilities.devices}:
        return SupportDecision('unsupported', f'이 worker({capabilities.worker_id})에는 {device.upper()} 장치가 없습니다. '
                                              '다른 장치로 바꿔 실행하지 않습니다.')
    stages = capabilities.tasks.get(task)
    if stages is None:
        return SupportDecision('unsupported', f'이 worker는 {task} 모델군을 실행할 수 없습니다.')
    state = stages.get(stage, 'unsupported')
    if state == 'unsupported':
        return SupportDecision('unsupported', f'{task} 모델군에는 {stage} 단계가 없습니다.')
    if state == 'not_installed':
        needs = ', '.join(capabilities.missing.get(task) or ['필요한 구성요소'])
        return SupportDecision('not_installed', f'이 worker에 {needs}이(가) 설치되어 있지 않습니다.')
    if f'{task}:{stage}:{device}' in capabilities.verified:
        return SupportDecision('verified', '이 worker에서 사전 점검을 통과했습니다.')
    return SupportDecision('unverified', '실행 구성요소는 있지만 이 조합의 사전 점검을 아직 하지 않았습니다.')


def local_device_kinds(system: Optional[str] = None, torch_module: Any = None) -> list[str]:
    """Device kinds the catalog may offer on this computer (MPS never off macOS)."""
    os_name = _os_name(system or _platform.system())
    if torch_module is None and find_spec('torch') is None:
        return ['cpu']
    return sorted({device.kind for device in probe_devices(os_name, torch_module)}, key=('cpu', 'cuda', 'mps').index)


__all__ = ['DeviceInfo', 'PROTOCOL_VERSION', 'STAGES', 'SupportDecision', 'WorkerCapabilities', 'decide',
           'local_device_kinds', 'probe_devices', 'probe_local']
