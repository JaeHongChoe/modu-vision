"""This computer as a worker (S1-05): its capabilities, the support state of every family, stage and device, and real
preflights that move a state to ``verified`` for the runtime they ran on."""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix='/api/workers', tags=['workers'])
logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {'running': None, 'last': None}
LOCAL_COMPUTE = 'local-compute'


class PreflightRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task: str = Field(min_length=1, max_length=64)
    device: Literal['cpu', 'cuda', 'mps'] = 'cpu'
    stages: Optional[list[Literal['train', 'evaluate', 'infer', 'export']]] = Field(default=None, min_length=1, max_length=4)


def _store():
    from backend.engine.worker_preflight import PreflightStore
    return PreflightStore()


def local_worker() -> dict:
    """This computer's capability record with each family/stage/device decision and its preflight results."""
    from backend.contracts.capabilities import STAGES, decide, probe_local
    from backend.engine.worker_preflight import PREFLIGHT_TASKS, preflight_architecture, runtime_digest
    store, digest = _store(), runtime_digest()
    try:
        results = store.results('local', digest)
        unreadable = None
    except (OSError, ValueError) as exc:  # an unreadable record verifies nothing; it is reported, not hidden
        results, unreadable = {}, str(exc)
    verified = {key: row['at'] for key, row in results.items() if row.get('passed') is True}
    capabilities = probe_local(runtime_digest=digest, verified=verified)
    support = {task: {stage: {device.kind: dataclasses.asdict(decide(capabilities, task, stage, device.kind))
                              for device in capabilities.devices} for stage in STAGES}
               for task in capabilities.tasks}
    return {**capabilities.model_dump(), 'support': support, 'preflight': results, 'preflight_tasks': sorted(PREFLIGHT_TASKS),
            'preflight_architectures': {task: preflight_architecture(task) for task in sorted(PREFLIGHT_TASKS)},
            'preflight_record_error': unreadable, 'local_compute_busy': _local_compute_busy()}


def _local_compute_busy() -> Optional[str]:
    """Why this computer's compute is reserved now (training or another preflight, in any app process), or None."""
    from backend.engine.shared_scheduler import shared_leases
    try:
        return None if shared_leases().available(LOCAL_COMPUTE, 'all') else '학습 또는 다른 사전 점검이 이 컴퓨터의 계산 자원을 사용 중입니다.'
    except Exception as exc:  # an unreadable reservation table is a reason not to start, and it is shown
        return f'계산 자원 예약 상태를 확인하지 못했습니다: {type(exc).__name__}'


@router.get('')
def workers():
    with _LOCK:
        running, last = _STATE['running'], _STATE['last']
    return {'workers': [local_worker()], 'running_preflight': running, 'last_preflight': last}


def _require_owner(request: Request) -> None:
    """With shared accounts, a preflight uses this computer's compute and is run by a project owner only."""
    account = getattr(request.state, 'account_user', None)
    if account is None:
        return
    from backend.api.routes_project import get_current_project
    project = get_current_project(request)
    if request.app.state.accounts.project_role(account['id'], project['id']) != 'owner':
        raise HTTPException(403, '이 컴퓨터의 사전 점검은 프로젝트 소유자만 실행할 수 있습니다.')


@router.post('/local/preflight', status_code=202)
def start_preflight(req: PreflightRequest, request: Request):
    from backend.engine.shared_scheduler import shared_leases
    from backend.engine.worker_preflight import PreflightRefused, plan, preflight_architecture, run_preflight
    _require_owner(request)
    try:
        stages = plan(req.task, req.device, req.stages)
    except PreflightRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    run = {'task': req.task, 'device': req.device, 'stages': list(stages), 'started_at': time.time(),
           'architecture': preflight_architecture(req.task)}
    with _LOCK:
        if _STATE['running'] is not None:
            raise HTTPException(409, '다른 사전 점검이 실행 중입니다.')
        # The same app-wide reservation every local training holds, for the whole preflight: a training started
        # meanwhile (in this or another app process) waits or is refused, and a preflight never starts beside one.
        leases, lease_id = shared_leases(), f'worker-preflight-{uuid.uuid4().hex[:12]}'
        if not leases.acquire(lease_id, LOCAL_COMPUTE, 'all', task=req.task):
            raise HTTPException(409, '학습 또는 다른 사전 점검이 이 컴퓨터의 계산 자원을 사용 중입니다. 끝난 뒤 실행하세요.')
        _STATE['running'] = run
    stop = threading.Event()

    def keep_reserved():
        while not stop.wait(max(0.5, leases.lease_seconds / 3)):
            if not leases.heartbeat(lease_id):
                logger.warning('The local compute reservation of preflight %s could not be renewed', lease_id)
                return

    def work():
        threading.Thread(target=keep_reserved, name=f'{lease_id}-heartbeat', daemon=True).start()
        try:
            outcome = {**run, **run_preflight(req.task, req.device, stages), 'error': None}
        except Exception as exc:  # recording failed; the stages are not verified and the reason is kept
            logger.exception('Worker preflight of %s on %s could not be recorded', req.task, req.device)
            outcome = {**run, 'results': {}, 'error': f'{type(exc).__name__}: {exc}'}
        finally:
            stop.set()
            try:
                leases.release(lease_id)
            except Exception:  # an unreleased row expires after its lease time; the outcome is still published
                logger.exception('The local compute reservation of preflight %s could not be released', lease_id)
        with _LOCK:
            _STATE['running'], _STATE['last'] = None, {**outcome, 'finished_at': time.time()}
    threading.Thread(target=work, name=f'worker-preflight-{req.task}', daemon=True).start()
    return {'status': 'started', **run}
