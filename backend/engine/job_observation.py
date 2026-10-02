"""What happened to a job, from evidence only, and the one next action the user can take (S1-04).

Cancellation is a chain of separately evidenced steps: the intent is recorded, the worker acknowledges it, signals
are sent (graceful, terminate, kill), the owned process is confirmed to have exited, and its device reservation is
released. A lost connection leaves the chain where it was: it is never reported as a finished cancellation.

Failure causes are classified from exit codes, operating-system error numbers and error text: out of memory, disk
full, lost network, a process number now used by another program, a backend restart, a runtime limit. A kill this
backend sent while cancelling is a cancellation, never an out-of-memory kill.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import errno as _errno
import re
from typing import Any, Optional

# Windows: ERROR_NOT_ENOUGH_MEMORY, ERROR_OUTOFMEMORY; ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL.
_WIN_MEMORY, _WIN_DISK = {8, 14}, {39, 112}
_STATUS_NO_MEMORY = 0xC0000017  # a Windows process ended by an allocation failure
_SIGKILL_EXITS = {137, -9}
_MEMORY_TEXT = re.compile(r'out of memory|MemoryError|cannot allocate memory|can\'t allocate memory|not enough memory'
                          r'|CUDA error: out of memory|DefaultCPUAllocator', re.IGNORECASE)
_DISK_TEXT = re.compile(r'No space left on device|not enough space on the disk|disk is full|ENOSPC|Errno 28', re.IGNORECASE)

NEXT_ACTIONS = {
    'completed': None,
    'cancelled': None,
    'cancel_pending': '중지 요청이 저장됐습니다. 작업자가 확인하고 종료할 때까지 기다리세요. 장비 예약은 종료가 확인된 뒤 풀립니다.',
    'cancel_unconfirmed': '중지 요청은 저장됐지만 작업자에게 전달되거나 종료된 것이 확인되지 않았습니다. 연결을 복구하면 같은 작업을 다시 확인합니다. 장비 예약은 유지됩니다.',
    'out_of_memory': '메모리가 부족했습니다. 배치 크기나 이미지 크기를 줄이거나 메모리가 더 큰 장비를 골라 다시 실행하세요.',
    'killed': '작업이 외부에서 강제 종료됐습니다(메모리 부족일 수 있습니다). 장비의 메모리 사용량을 확인한 뒤 다시 실행하세요.',
    'disk_full': '저장 공간이 부족했습니다. 출력 폴더가 있는 디스크의 여유 공간을 확보한 뒤 다시 실행하세요. 이미 저장된 결과는 유지됩니다.',
    'network_lost': '작업 장비와 연결이 끊겼습니다. 연결을 복구하면 같은 작업을 다시 관찰합니다. 결과가 확인될 때까지 장비 예약은 유지됩니다.',
    'process_identity_changed': '기록된 작업 프로세스 번호를 다른 프로그램이 쓰고 있어 아무 신호도 보내지 않았습니다. 학습이 끝났는지 장비에서 확인한 뒤 예약 해제를 확인하세요.',
    'worker_state_unknown': '작업 프로세스의 상태를 확인하지 못했습니다. 장비 예약은 유지됩니다. 학습이 끝났는지 장비에서 확인한 뒤 다시 확인하세요.',
    'app_restarted': '앱이 다시 시작될 때 이 작업의 프로세스나 결과를 찾지 못했습니다. 같은 설정으로 다시 실행하세요.',
    'time_limit': '정한 실행 시간을 넘어 중지했습니다. 시간 제한을 늘리거나 학습량을 줄여 다시 실행하세요.',
    'worker_exited': '작업 프로세스가 결과를 남기기 전에 끝났습니다. 작업 로그를 확인한 뒤 다시 실행하세요.',
    'worker_failed': '작업이 실패했습니다. 오류 내용을 확인하고 설정을 고친 뒤 다시 실행하세요.',
}
RETRYABLE = {'out_of_memory', 'killed', 'disk_full', 'app_restarted', 'time_limit', 'worker_exited', 'worker_failed'}


@dataclass(frozen=True)
class CancelEvidence:
    """The cancellation steps that have evidence; ``complete`` needs exit confirmation and a released reservation."""
    requested_at: Optional[float] = None
    acknowledged_at: Optional[float] = None
    signals: tuple = ()
    exit_confirmed: bool = False
    exit_code: Optional[int] = None
    reservation_released: Optional[bool] = None  # None: not observable (no reservation DB row information)
    stage: str = 'none'
    complete: bool = False


@dataclass(frozen=True)
class ObservedState:
    state: str
    cause: Optional[str]
    next_action: Optional[str]
    retryable: bool
    evidence: dict = field(default_factory=dict)


def cancellation_evidence(journal: dict, intent: Optional[dict] = None,
                          reservation_present: Optional[bool] = None) -> CancelEvidence:
    """The cancellation chain recorded by the ledger intent, the run journal and the reservation table."""
    requested = journal.get('cancel_requested_at')
    if requested is None and intent is not None:
        requested = intent['requested_ns'] / 1e9  # the ledger's durable intent (JobStore.cancel_intents)
    signals = tuple(name for name, key in (('cooperative', 'cancel_signal_sent_at'), ('terminate', 'cancel_terminate_sent_at'),
                                           ('kill', 'cancel_kill_sent_at')) if journal.get(key))
    exited = journal.get('worker_exit_confirmed') is True
    released = None if reservation_present is None else not reservation_present
    stage = 'none'
    if requested:
        stage = 'requested'
        if journal.get('cancel_acknowledged_at'):
            stage = 'acknowledged'
        if signals:
            stage = 'signalled'
        if exited:
            stage = 'exited'
        if exited and released:
            stage = 'released'
    return CancelEvidence(requested_at=requested,
                          acknowledged_at=journal.get('cancel_acknowledged_at'), signals=signals, exit_confirmed=exited,
                          exit_code=journal.get('worker_exit_code'), reservation_released=released, stage=stage,
                          complete=bool(requested) and exited and released is True)


def _code(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


_MEMORY_CODES = {'ERR_001', 'ERR_OOM', 'ERR_GPU_OOM'}  # the error catalog's out-of-memory codes


def classify_observation(status: str, *, exit_code: Any = None, error: Optional[str] = None, os_errno: Any = None,
                         winerror: Any = None, error_code: Optional[str] = None, connection_lost: bool = False,
                         remote: bool = True, identity_mismatch: bool = False, app_restarted: bool = False,
                         cancel: Optional[CancelEvidence] = None, cancel_reason: Optional[str] = None) -> ObservedState:
    """One observed state with its cause and next action; the evidence used is returned with it."""
    code, os_errno, winerror = _code(exit_code), _code(os_errno), _code(winerror)
    text = error or ''
    evidence = {key: value for key, value in (('status', status), ('exit_code', code), ('errno', os_errno),
                                               ('winerror', winerror), ('error', error or None),
                                               ('cancel_stage', cancel.stage if cancel else None)) if value is not None}

    def observed(state: str, cause: Optional[str]) -> ObservedState:
        return ObservedState(state, cause, NEXT_ACTIONS.get(cause or state), (cause in RETRYABLE), evidence)

    if identity_mismatch:
        return observed('uncertain', 'process_identity_changed')
    if status == 'completed':
        return observed('completed', None)
    cancel_requested = bool(cancel and cancel.stage != 'none')
    if cancel_requested and cancel_reason == 'runtime budget exceeded' and status in ('aborted', 'stopping'):
        return observed(status, 'time_limit')
    if status in ('aborted',) and cancel_requested:
        return observed('aborted', 'cancelled')
    if connection_lost or status == 'disconnected':
        # Only a remote worker is reached over a network; a local worker in this state could not be observed.
        return observed('disconnected', 'cancel_unconfirmed' if cancel_requested else 'network_lost' if remote else 'worker_state_unknown')
    if status in ('running', 'stopping', 'preparing', 'queued', 'accepted', 'detached'):
        return observed(status, 'cancel_pending' if cancel_requested and status in ('running', 'stopping') else None)
    if os_errno == _errno.ENOSPC or winerror in _WIN_DISK or _DISK_TEXT.search(text):
        return observed(status, 'disk_full')
    if (os_errno == _errno.ENOMEM or winerror in _WIN_MEMORY or code == _STATUS_NO_MEMORY or code == _STATUS_NO_MEMORY - (1 << 32)
            or error_code in _MEMORY_CODES or _MEMORY_TEXT.search(text)):
        return observed(status, 'out_of_memory')
    if code in _SIGKILL_EXITS and not (cancel and 'kill' in cancel.signals):
        return observed(status, 'killed')
    if re.search(r'uncertain|unprovable|unconfirmed', text, re.IGNORECASE):
        return observed(status, 'worker_state_unknown')  # the observer could not prove what the process did
    if status == 'interrupted':
        return observed('interrupted', 'app_restarted' if app_restarted else 'worker_exited')
    if status == 'failed':
        return observed('failed', 'worker_failed')
    return observed(status, None)
