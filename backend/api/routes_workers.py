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
_WORKER: dict[str, Any] = {'thread': None}  # the running preflight's work thread, joined at shutdown
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
        # The same rule as acquire: an expired reservation of a crashed app is not busy.
        return None if shared_leases().available_to_acquire(LOCAL_COMPUTE, 'all') else '학습 또는 다른 사전 점검이 이 컴퓨터의 계산 자원을 사용 중입니다.'
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
    from backend.engine.application_launch_handshake import create_preflight_writer_ticket
    ticket = create_preflight_writer_ticket(device=req.device)
    transferred = False
    try:
        result = _start_preflight_admitted(req, request, ticket)
        transferred = True
        return result
    finally:
        # Successful dispatch owns the whole lifetime. Pre-dispatch failures
        # are cleaned by the admitted helper before this original final close.
        if not transferred:
            # The helper settles failed starts; ordinary validation refusal
            # still owns an undispatched ticket on this request thread.
            if ticket._phase == 'created': ticket.finish()


def _start_preflight_admitted(req, request, ticket):
    from backend.api.routes_training import training_job_manager
    from backend.engine.shared_scheduler import shared_leases
    from backend.engine.worker_preflight import PreflightRefused, plan, preflight_architecture, run_preflight
    _require_owner(request)
    try:
        stages = plan(req.task, req.device, req.stages)
    except PreflightRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    if training_job_manager.local_queue_waiting():
        # A training already waiting for this computer goes first; a preflight never overtakes it.
        raise HTTPException(409, '이 컴퓨터를 기다리는 학습이 있습니다. 학습이 시작·종료된 뒤 사전 점검을 실행하세요.')
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
    heartbeat_failed = threading.Event()

    def release_reserved():
        if leases.release(lease_id) is not True:
            raise RuntimeError('Original preflight reservation release is unconfirmed')

    def keep_reserved():
        while not stop.wait(max(0.5, leases.lease_seconds / 3)):
            try:
                renewed = leases.heartbeat(lease_id)
            except Exception:  # a busy or unreadable reservation table: try again at the next beat
                heartbeat_failed.set()
                logger.warning('The local compute reservation of preflight %s could not be renewed now', lease_id, exc_info=True)
                continue
            if not renewed:
                heartbeat_failed.set()
                logger.warning('The local compute reservation of preflight %s is no longer held', lease_id)
                return

    def work():
        ticket.claim()
        heartbeat = None
        outcome = {**run, 'results': {}, 'error': '사전 점검 실행 결과를 확인하지 못했습니다.'}
        try:
            heartbeat = threading.Thread(target=keep_reserved, name=f'{lease_id}-heartbeat', daemon=True)
            heartbeat.start()
            outcome = {**run, **run_preflight(req.task, req.device, stages, _writer_ticket=ticket), 'error': None}
        except Exception as exc:  # recording failed; the stages are not verified and the reason is kept
            ticket.unconfirmed()
            logger.exception('Worker preflight of %s on %s could not be recorded', req.task, req.device)
            outcome = {**run, 'results': {}, 'error': f'{type(exc).__name__}: {exc}'}
        finally:
            stop.set()
            try:
                # Keep original custody until every in-flight heartbeat write
                # has returned. Shutdown's existing join budget stays unchanged.
                if heartbeat is not None:
                    heartbeat.join()
                if heartbeat_failed.is_set(): ticket.unconfirmed()
                def publish_finished():
                    release_reserved()
                    from backend.engine.application_preflight_child_relay import finish_ticket_child
                    finish_ticket_child(ticket)
                    with _LOCK:
                        _STATE['running'], _STATE['last'] = None, {**outcome, 'finished_at': time.time()}
                ticket.finish(before_leave=publish_finished)
            except BaseException:
                ticket.retain_unconfirmed()
                logger.exception('The original cleanup lifetime of preflight %s is unresolved', lease_id)
    try:
        thread = threading.Thread(target=work, name=f'worker-preflight-{req.task}', daemon=True)
    except BaseException:
        ticket.unconfirmed()
        try:
            def clear_unstarted():
                release_reserved()
                with _LOCK:
                    if _STATE['running'] is run: _STATE['running'] = None
            ticket.finish(before_leave=clear_unstarted)
        except BaseException:
            ticket.retain_unconfirmed()
        raise
    try:
        ticket.dispatch_to(thread)  # Sticky background refusal before Thread.start.
        _WORKER['thread'] = thread
        thread.start()
    except BaseException:
        if ticket.cancel_dispatch():
            # No claim can start later. Cleanup remains inside retained custody.
            try:
                stop.set()
                def clear_cancelled():
                    release_reserved()
                    with _LOCK:
                        if _STATE['running'] is run: _STATE['running'] = None
                        if _WORKER.get('thread') is thread: _WORKER['thread'] = None
                ticket.finish(before_leave=clear_cancelled)
            except BaseException:
                ticket.retain_unconfirmed()
                raise
        # A start that raised after claim never closes the worker's capability.
        raise
    return {'status': 'started', **run}


def stop_for_shutdown(wait: float = 3.0) -> None:
    """App shutdown: stop the running preflight child through its handle and let its work thread record the outcome
    and release the local compute reservation (a quit never leaves the reservation held until it expires). The wait
    stays under the desktop supervisor's stop window, so a locked reservation table cannot turn a quit into a kill."""
    from backend.engine.worker_preflight import stop_running_preflights
    stop_running_preflights()
    thread = _WORKER.get('thread')
    if thread is not None and thread.is_alive():
        thread.join(wait)
