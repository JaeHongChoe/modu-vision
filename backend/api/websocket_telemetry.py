"""
backend/api/websocket_telemetry.py

High-Speed Asynchronous WebSocket Telemetry Streaming Layer for Vision AI Studio.
Broadcasts live training loss, step progress, epoch ETA, overall training ETA,
and hardware metrics (GPU/MPS/CPU/RAM) at 10-50Hz with thread-safe queue bridging
and client connection management.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import logging
import math
import threading
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.engine.device import get_device, get_host_telemetry
from backend.engine.trainer import TrainingCallback
from backend.utils.error_catalog import classify_exception
from backend.contracts.context import current_project_context, originating_context
from backend.engine.job_store import ledger as observation_ledger
from backend.engine.product_delivery import redact_diagnostics
from backend.engine.original_owned_telemetry_socket_request import (
    observe_original_telemetry_handler, observe_original_telemetry_register,
    observe_original_telemetry_unregister,
    observe_original_telemetry_task_creation, observe_original_telemetry_drain_creation,
)

logger = logging.getLogger("vision_ai_studio.telemetry")

router = APIRouter(tags=["telemetry"])



def _metric_value(value: Any) -> Any:
    """A number rounded for the live progress message; other recorded values (an anomaly model's confusion matrix,
    its threshold basis label, a flag) are sent as recorded, as the status endpoint returns them. A numpy or torch
    value is sent as the plain value it holds (a scalar, or a list for an array), so the message always serialises."""
    if hasattr(value, 'tolist') and not isinstance(value, (list, tuple, str, bytes)):
        value = value.tolist()  # numpy scalars and 0-d tensors give a Python scalar; arrays give (nested) lists
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(float(value), 5)

class RateLimiter:
    """Controls event emission frequency to stay within 10-50Hz."""

    def __init__(self, max_hz: float = 30.0):
        # Clamp frequency between 10Hz and 50Hz (min_interval between 20ms and 100ms)
        clamped_hz = max(10.0, min(50.0, float(max_hz)))
        self.min_interval = 1.0 / clamped_hz
        self.last_emit = 0.0

    def should_emit(self, force: bool = False) -> bool:
        now = time.time()
        if force or (now - self.last_emit) >= self.min_interval:
            self.last_emit = now
            return True
        return False


class TelemetryBroadcaster:
    """
    Central WebSocket Connection Manager and Thread-Safe Event Broadcaster.
    Bridges synchronous worker threads (PyTorch engine) to async WebSocket event loops
    via threadsafe queue dispatching.
    """

    def __init__(self):
        self._active_connections: Set[WebSocket] = set()
        self._connection_sends: Dict[WebSocket, asyncio.Lock] = {}
        self._closing_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None
        self._dispatch_task: Optional[asyncio.Task] = None
        self._hw_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._device = None
        self._task_lifetimes: List[Dict[str, Any]] = []
        self._task_heads: Dict[str, Dict[str, Any]] = {}
        self._lifetime_lock = threading.RLock()
        self._shutdown_started = False
        self._prepared_shutdown = False
        self._shutdown_failure: Optional[str] = None
        self._shutdown_failures: Dict[str, str] = {}
        self._shutdown_observers: List[Dict[str, Any]] = []

    @property
    def active_connections(self) -> Set[WebSocket]:
        return self._active_connections

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Sets the running asyncio event loop and initializes queues if needed."""
        self.start(loop)

    def _retain_shutdown_failure(self, message: str) -> str:
        # One built-in dictionary insertion retains the first failure without
        # waiting for the lifetime lock that may itself have timed out.
        first = self._shutdown_failures.setdefault('first', message)
        self._shutdown_failure = first
        return first

    def _refuse_shutdown(self, message: str) -> None:
        self._retain_shutdown_failure(message)
        raise RuntimeError(message)

    @contextmanager
    def _lifetime_access(self, deadline: Optional[float] = None):
        acquired = (self._lifetime_lock.acquire() if deadline is None else
                    self._lifetime_lock.acquire(timeout=self._remaining(deadline)))
        if not acquired:
            self._refuse_shutdown("Original telemetry lifetime lock unresolved at deadline")
        try:
            if deadline is not None:
                self._remaining(deadline)
            yield
        finally:
            self._lifetime_lock.release()

    def _remaining(self, deadline: float) -> float:
        observed = time.monotonic()
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or deadline <= observed):
            self._refuse_shutdown("Original telemetry shutdown deadline expired or invalid")
        return deadline - observed

    def _validate_original_tasks(self, deadline: Optional[float] = None) -> None:
        with self._lifetime_access(deadline):
            for role, attribute in (("dispatch", "_dispatch_task"), ("hardware", "_hw_task")):
                head = self._task_heads.get(role)
                current = getattr(self, attribute)
                if head is None:
                    if current is not None:
                        self._refuse_shutdown("Original telemetry task ownership is unknown")
                elif current is not head['task'] and not (head['joined'] and current is None):
                    self._refuse_shutdown("Original telemetry task ownership was substituted")
            for record in self._task_lifetimes:
                if (not isinstance(record['task'], asyncio.Task)
                        or record['task'].get_loop() is not record['loop']):
                    self._refuse_shutdown("Original telemetry task loop ownership differs")
            latest = self._task_heads.get('hardware')
            if latest is not None and self._loop is not latest['loop']:
                self._refuse_shutdown("Original telemetry loop ownership was substituted")

    @observe_original_telemetry_task_creation
    def _record_task(self, loop: asyncio.AbstractEventLoop, role: str, coroutine,
                     deadline: Optional[float] = None) -> Dict[str, Any]:
        try:
            with self._lifetime_access(deadline):
                task = loop.create_task(coroutine)
                record = {'task': task, 'loop': loop, 'role': role, 'joined': False}
                self._task_lifetimes.append(record)
                return record
        except BaseException:
            coroutine.close()
            self._refuse_shutdown("Original telemetry task creation was incomplete")

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Retain the actual created tasks, including superseded generations."""
        self._validate_original_tasks()
        with self._lifetime_lock:
            if self._shutdown_failure is not None:
                raise RuntimeError(self._shutdown_failure)
            if self._shutdown_started and any(not r['joined'] for r in self._task_lifetimes):
                self._refuse_shutdown("Original telemetry shutdown tasks remain unresolved")
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is not loop or loop.is_closed() or not loop.is_running():
            self._refuse_shutdown("Original telemetry start requires its running event loop")
        if self._running and self._loop is loop:
            return
        try:
            device = get_device()
        except Exception:
            device = None
        # Admission and exact pair publication are atomic with the preparation
        # snapshot; device probing happens outside this small critical section.
        with self._lifetime_lock:
            self._validate_original_tasks()
            if self._shutdown_failure is not None:
                raise RuntimeError(self._shutdown_failure)
            if self._prepared_shutdown:
                self._refuse_shutdown("Original telemetry startup admission is closed")
            if self._shutdown_started and any(not r['joined'] for r in self._task_lifetimes):
                self._refuse_shutdown("Original telemetry shutdown tasks remain unresolved")
            if self._active_connections and self._loop is not loop:
                self._refuse_shutdown("Original telemetry connection loop ownership differs")
            for record in tuple(self._task_lifetimes):
                if not record['joined'] and not record['task'].done():
                    owner = record['loop']
                    if owner.is_closed() or not owner.is_running():
                        self._refuse_shutdown("Original telemetry loop is stopped")
                    owner.call_soon_threadsafe(record['task'].cancel)
            self._loop = loop
            self._queue = asyncio.Queue()
            self._device = device
            self._running = True
            self._shutdown_started = False
            dispatch = self._record_task(loop, 'dispatch', self._event_dispatch_loop())
            self._task_heads['dispatch'] = dispatch
            self._dispatch_task = dispatch['task']
            hardware = self._record_task(loop, 'hardware', self._hardware_telemetry_loop())
            self._task_heads['hardware'] = hardware
            self._hw_task = hardware['task']
        logger.info("TelemetryBroadcaster started.")

    async def _join_records(self, records: List[Dict[str, Any]], deadline: float) -> None:
        tasks = {record['task'] for record in records}
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self._remaining(deadline))
            if pending:
                self._refuse_shutdown("Original telemetry tasks unresolved at shutdown deadline")
            self._remaining(deadline)
        with self._lifetime_access(deadline):
            failure = None
            for record in records:
                task = record['task']
                try:
                    error = task.exception()
                except asyncio.CancelledError:
                    error = None
                record['joined'] = True
                if error is not None:
                    failure = "Original telemetry original task failed: " + type(error).__name__
                if record['role'] == 'connection_close':
                    if error is None and not task.cancelled() and task.result() is True:
                        self._active_connections.discard(record['connection'])
                    else:
                        failure = "Original telemetry connection close was not confirmed"
            if failure is not None:
                self._refuse_shutdown(failure)

    @observe_original_telemetry_drain_creation
    async def _drain_original_loop(self, loop: asyncio.AbstractEventLoop,
                                   records: List[Dict[str, Any]], deadline: float,
                                   observer: Dict[str, Any]) -> None:
        if asyncio.get_running_loop() is not loop:
            self._refuse_shutdown("Original telemetry observer loop ownership differs")
        original_task = asyncio.current_task()
        with self._lifetime_access(deadline):
            if observer['task'] is not None and observer['task'] is not original_task:
                self._refuse_shutdown('Original telemetry observer task ownership differs')
            observer['task'] = original_task
            observer['original_task'] = original_task
        self._remaining(deadline)
        for record in records:
            if record['task'].get_loop() is not loop:
                self._refuse_shutdown("Original telemetry task loop ownership differs")
            if not record['task'].done():
                record['task'].cancel()
        await self._join_records(records, deadline)
        if loop is self._loop:
            closes = []
            for ws in tuple(self._active_connections):
                record = self._record_task(loop, 'connection_close', self._safe_close(ws), deadline)
                record['connection'] = ws
                closes.append(record)
            await self._join_records(closes, deadline)

    def _begin_shutdown(self, deadline: float, *, cleanup: bool = False) -> Dict[Any, Any]:
        self._remaining(deadline)
        self._validate_original_tasks(deadline)
        with self._lifetime_access(deadline):
            if self._shutdown_failure is not None and not cleanup:
                raise RuntimeError(self._shutdown_failure)
            groups: Dict[Any, Any] = {}
            for record in self._task_lifetimes:
                if not record['joined']:
                    groups.setdefault(record['loop'], []).append(record)
            if self._active_connections and self._loop is not None:
                groups.setdefault(self._loop, [])
            for loop in groups:
                if loop.is_closed() or not loop.is_running():
                    self._refuse_shutdown("Original telemetry loop is stopped")
            self._running = False
            self._shutdown_started = True
            if not cleanup:
                self._prepared_shutdown = True
            return groups

    def _closure_receipt(self, deadline: float) -> Dict[str, Any]:
        self._validate_original_tasks(deadline)
        with self._lifetime_access(deadline):
            if any(not r['joined'] for r in self._task_lifetimes) or self._active_connections:
                self._refuse_shutdown("Original telemetry tasks or connections remain unresolved")
            if self._shutdown_failure is not None:
                raise RuntimeError(self._shutdown_failure)
            for observer in self._shutdown_observers:
                task, future = observer.get('task'), observer.get('future')
                if (not isinstance(task, asyncio.Task) or task is not observer.get('original_task')
                        or task.get_loop() is not observer['loop'] or not task.done()
                        or future is None or not future.done()):
                    self._refuse_shutdown('Original telemetry observer ownership or exit remains unresolved')
                try:
                    error = future.exception()
                except BaseException:
                    self._refuse_shutdown('Original telemetry observer completion was interrupted')
                if error is not None:
                    self._refuse_shutdown('Original telemetry observer completion failed')
            self._remaining(deadline)
            for role, attribute in (("dispatch", "_dispatch_task"), ("hardware", "_hw_task")):
                if self._task_heads.get(role, {}).get('joined'):
                    setattr(self, attribute, None)
            return {'idle': True, 'supported': True, 'task_count': len(self._task_lifetimes),
                    'joined_tasks': len(self._task_lifetimes), 'pending_tasks': 0,
                    'whole_writer_coverage': False, 'process_tree_exit_verified': False,
                    'can_release_launch_lease': False}

    def _observe_shutdown(self, observer: Dict[str, Any], future) -> None:
        """Retain terminal observer failure even after a bounded caller returns."""
        try:
            error = future.exception()
        except BaseException as error:
            observer['exception_type'] = type(error).__name__
            if self._shutdown_failure is None:
                self._retain_shutdown_failure("Original telemetry observer was interrupted")
        else:
            observer['exception_type'] = type(error).__name__ if error is not None else None
            if error is not None and self._shutdown_failure is None:
                self._retain_shutdown_failure("Original telemetry observer failed: " + type(error).__name__)
        observer['terminal_observed'] = True

    def prepare_shutdown(self, deadline: float) -> Dict[str, Any]:
        """Bounded original-loop observation from the owned preparation thread."""
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is not None:
            self._refuse_shutdown("Telemetry sync preparation cannot block the same event loop")
        groups = self._begin_shutdown(deadline)
        observers = []
        for loop, records in groups.items():
            observer = {'loop': loop, 'task': None, 'future': None}
            coroutine = self._drain_original_loop(loop, records, deadline, observer)
            try:
                future = asyncio.run_coroutine_threadsafe(coroutine, loop)
            except BaseException:
                coroutine.close()
                self._refuse_shutdown("Original telemetry observer scheduling failed")
            observer['future'] = future
            self._shutdown_observers.append(observer)
            future.add_done_callback(lambda done, original=observer: self._observe_shutdown(original, done))
            observers.append(future)
        for future in observers:
            try:
                future.result(timeout=self._remaining(deadline))
            except TimeoutError:
                # Retain the observer and originals; late completion cannot
                # clear this admission failure or qualify release afterward.
                self._refuse_shutdown("Original telemetry shutdown deadline expired")
            except BaseException as error:
                if self._shutdown_failure is None:
                    self._refuse_shutdown("Original telemetry observer failed: " + type(error).__name__)
                raise RuntimeError(self._shutdown_failure) from error
        self._remaining(deadline)
        return self._closure_receipt(deadline)

    async def shutdown(self, deadline: Optional[float] = None) -> None:
        """Compatible async interface, bounded on the caller's single deadline."""
        deadline = time.monotonic() + 4.0 if deadline is None else deadline
        groups = self._begin_shutdown(deadline, cleanup=True)
        current = asyncio.get_running_loop()
        observers = []
        for loop, records in groups.items():
            observer = {'loop': loop, 'task': None, 'future': None}
            coroutine = self._drain_original_loop(loop, records, deadline, observer)
            if loop is current:
                future = loop.create_task(coroutine)
            else:
                try:
                    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
                except BaseException:
                    coroutine.close()
                    self._refuse_shutdown("Original telemetry observer scheduling failed")
            observer['future'] = future
            self._shutdown_observers.append(observer)
            future.add_done_callback(lambda done, original=observer: self._observe_shutdown(original, done))
            observers.append(future if isinstance(future, asyncio.Future) else asyncio.wrap_future(future))
        try:
            if observers:
                done, pending = await asyncio.wait(observers, timeout=self._remaining(deadline))
                if pending:
                    self._refuse_shutdown("Original telemetry shutdown tasks unresolved at deadline")
                for future in done:
                    future.result()
        except asyncio.CancelledError:
            if self._shutdown_failure is None:
                self._retain_shutdown_failure("Original telemetry shutdown observer was interrupted")
            raise
        self._remaining(deadline)
        self._closure_receipt(deadline)
        logger.info("TelemetryBroadcaster shut down cleanly.")

    async def _safe_close(self, ws: WebSocket) -> bool:
        try:
            lock = self._connection_sends.get(ws)
            if lock is None:
                return False
            async with lock:
                if ws not in self._active_connections or ws in self._closing_connections:
                    return False
                await ws.close(code=1001, reason="Server shutting down")
                return True
        except Exception:
            return False

    async def connect(self, websocket: WebSocket) -> None:
        """Alias for register to support different naming conventions."""
        await self.register(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Alias for unregister."""
        self.unregister(websocket)

    @observe_original_telemetry_register
    async def register(self, websocket: WebSocket) -> None:
        """Accepts and registers a new WebSocket client, sending initial state snapshot."""
        if self._shutdown_started:
            raise RuntimeError("Telemetry shutdown no longer admits new connections")
        await websocket.accept()
        if self._shutdown_started:
            await websocket.close(code=1001, reason="Server shutting down")
            return
        self._connection_sends[websocket] = asyncio.Lock()
        self._active_connections.add(websocket)
        logger.info(
            "WebSocket client connected. Active connections: %d",
            len(self._active_connections),
        )

        # Send initial hardware snapshot immediately
        try:
            hw = get_host_telemetry(self._device)
            snapshot = {
                "event": "hardware_stats",
                "type": "hardware_stats",
                "timestamp": time.time(),
                "data": {
                    "cpu_percent": hw.cpu_percent,
                    "memory_percent": hw.memory_percent,
                    "gpu_name": hw.gpu_name,
                    "gpu_memory_used_mb": hw.gpu_memory_used_mb,
                    "device_type": hw.device_type,
                },
                "cpu_percent": hw.cpu_percent,
                "memory_percent": hw.memory_percent,
                "gpu_name": hw.gpu_name,
                "gpu_memory_used_mb": hw.gpu_memory_used_mb,
                "device_type": hw.device_type,
            }
            await websocket.send_text(json.dumps(snapshot))
        except Exception as e:
            logger.debug("Failed to send initial hardware snapshot: %s", e)

    @observe_original_telemetry_unregister
    def unregister(self, websocket: WebSocket) -> None:
        """Removes disconnected WebSocket client."""
        self._active_connections.discard(websocket)
        logger.info(
            "WebSocket client disconnected. Active connections: %d",
            len(self._active_connections),
        )

    def _can_receive(self, websocket, payload) -> bool:
        """Recheck session and job ownership before every shared-server frame."""
        scope=getattr(websocket,'scope',{})
        state=scope.get('state',{}) if isinstance(scope,dict) else {}
        origin=payload.get('project_context')
        recipient=state.get('project_context')
        if origin is not None:
            if not isinstance(origin,dict) or recipient is None:return False
            if (origin.get('workspace_id'),origin.get('project_id')) != (recipient.workspace_id,recipient.project_id):return False
        if not state.get('account_user'):return True
        try:
            store=scope['app'].state.accounts
            user=store.authenticate(state['account_session_token'])
            project=state['scoped_project']
            role=(store.project_role_exact(user['id'],state['account_project_key']) if 'account_project_key' in state
                  else store.project_role(user['id'],project['id']))
            if not role:return False
            if payload.get('event',payload.get('type'))=='hardware_stats':return True
            data=payload.get('data',payload)
            job_id=data.get('job_id')
            if not job_id:return False
            from backend.api.routes_training import training_job_manager
            record=training_job_manager.get_job(job_id)
            return bool(record and Path(record.output_dir).resolve().is_relative_to(Path(project['models_dir']).resolve()))
        except (ValueError,KeyError,TypeError,OSError,AttributeError):return False

    def broadcast_sync(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Thread-safe entry point called by PyTorch TrainingCallback from worker thread.
        Bridges to asyncio event loop via loop.call_soon_threadsafe(queue.put_nowait, msg).
        """
        if not self._running or self._loop is None or self._queue is None:
            return

        payload = {
            "event": event_type,
            "type": event_type,
            "timestamp": time.time(),
            "data": data,
            **data,  # Flatten for universal client compatibility
        }
        context = originating_context()
        if context is not None:
            payload['project_context'] = context

        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)
        except Exception as e:
            logger.debug("Failed to schedule broadcast message: %s", e)

    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """Direct async broadcast method."""
        payload = {
            "event": event_type,
            "type": event_type,
            "timestamp": time.time(),
            "data": data,
            **data,
        }
        context = originating_context()
        if context is not None:
            payload['project_context'] = context
        text = json.dumps(payload)
        dead = []
        for ws in list(self._active_connections):
            if not self._can_receive(ws,payload):continue
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)

    async def _event_dispatch_loop(self) -> None:
        """Async worker consuming the queue and broadcasting to all connected sockets."""
        while self._running:
            try:
                if self._queue is None:
                    await asyncio.sleep(0.01)
                    continue
                msg = await self._queue.get()
                if not self._active_connections:
                    self._queue.task_done()
                    continue

                text = json.dumps(msg)
                stale = []
                for ws in list(self._active_connections):
                    lock = self._connection_sends.get(ws)
                    if lock is None:
                        continue
                    async with lock:
                        if ws not in self._active_connections or ws in self._closing_connections:
                            continue
                        if not self._can_receive(ws,msg):continue
                        try:
                            await ws.send_text(text)
                        except Exception:
                            stale.append(ws)

                for ws in stale:
                    self.unregister(ws)

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in telemetry dispatch loop: %s", e)

    async def _hardware_telemetry_loop(self) -> None:
        """Periodic background task broadcasting hardware metrics at 10Hz (every 100ms) when clients are connected."""
        while self._running:
            try:
                await asyncio.sleep(0.1)  # 10Hz
                if self._active_connections:
                    hw = get_host_telemetry(self._device)
                    self.broadcast_sync("hardware_stats", {
                        "cpu_percent": hw.cpu_percent,
                        "memory_percent": hw.memory_percent,
                        "gpu_name": hw.gpu_name,
                        "gpu_memory_used_mb": hw.gpu_memory_used_mb,
                        "device_type": hw.device_type,
                    })
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Error in hardware telemetry polling: %s", e)


# Global singleton instance
broadcaster = TelemetryBroadcaster()


class WebSocketTelemetryCallback(TrainingCallback):
    """
    Adapter bridging UnifiedAutoMLTrainer callbacks to the TelemetryBroadcaster.
    Implements rate limiting (10-50Hz), step progress, epoch ETA, and overall training ETA calculation.
    """

    def __init__(self, job_id: str, max_hz: float = 30.0):
        self.job_id = job_id
        # Legacy training managers launch raw threads. Freeze the authenticated
        # submission identity here instead of depending on worker inheritance.
        self._project_context = current_project_context.get()
        self.rate_limiter = RateLimiter(max_hz=max_hz)
        self.training_start_time = time.time()
        self.epoch_start_time = time.time()
        self.total_epochs = 1
        self.total_steps = 0
        self.epoch_durations: List[float] = []

    def _broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        # Retain lifecycle/epoch evidence even with no connected WebSocket.
        # High-frequency step/hardware samples remain live measurements.
        if event_type in {'training_started', 'epoch_progress', 'training_completed', 'training_aborted', 'training_error'} and self._project_context is not None:
            try:
                store = observation_ledger()
                owner = store.record(self.job_id)
                identity = self._project_context.model_dump()
                if all(owner[field] == value for field, value in identity.items()) and data.get('job_id') == self.job_id:
                    store.record_event(self.job_id, event_type, redact_diagnostics(data))
            except (KeyError, OSError, sqlite3.Error, ValueError):
                # A log failure must not cancel training or print private payloads.
                logger.warning('Persistent training observation unavailable for the originating job')
        token = current_project_context.set(self._project_context)
        try:
            broadcaster.broadcast_sync(event_type, data)
        finally:
            current_project_context.reset(token)

    def on_training_start(self, config: Dict[str, Any]) -> None:
        self.total_epochs = config.get("epochs", 1)
        self.training_start_time = time.time()
        self.epoch_start_time = time.time()
        self._broadcast("training_started", {
            "job_id": self.job_id,
            "total_epochs": self.total_epochs,
            **config,
        })

    def on_step_end(self, step: int, total_steps: int, current_loss: float, epoch: int) -> None:
        self.total_steps = total_steps
        now = time.time()
        elapsed_epoch = max(1e-4, now - self.epoch_start_time)
        steps_per_epoch = max(1, total_steps // max(1, self.total_epochs))
        step_in_epoch = step % steps_per_epoch
        steps_left = max(0, steps_per_epoch - (step_in_epoch + 1))
        rate = (step_in_epoch + 1) / elapsed_epoch
        epoch_eta = steps_left / max(1e-4, rate)

        # Force emission on final step of epoch or training
        is_last_step = (step + 1 >= total_steps) or (step_in_epoch + 1 >= steps_per_epoch)
        if self.rate_limiter.should_emit(force=is_last_step):
            self._broadcast("step_progress", {
                "job_id": self.job_id,
                "step": step + 1,
                "total_steps": total_steps,
                "current_loss": round(float(current_loss), 5),
                "epoch": epoch + 1,
                "total_epochs": self.total_epochs,
                "epoch_eta_seconds": round(epoch_eta, 1),
            })

    def on_epoch_end(
        self,
        epoch: int,
        total_epochs: int,
        train_loss: float,
        val_loss: Optional[float],
        lr: float,
        metrics: Dict[str, float],
    ) -> None:
        now = time.time()
        duration = now - self.epoch_start_time
        self.epoch_durations.append(duration)
        self.epoch_start_time = now  # Reset for subsequent epoch

        remaining_epochs = max(0, total_epochs - (epoch + 1))
        avg_duration = sum(self.epoch_durations) / max(1, len(self.epoch_durations))
        total_eta = remaining_epochs * avg_duration

        self._broadcast("epoch_progress", {
            "job_id": self.job_id,
            "epoch": epoch + 1,
            "total_epochs": total_epochs,
            "train_loss": round(float(train_loss), 5),
            "val_loss": None if val_loss is None else round(float(val_loss), 5),
            "lr": float(lr),
            "metrics": {k: _metric_value(v) for k, v in metrics.items()},
            "eta_seconds": round(total_eta, 1),
        })

    def on_hardware_stats(self, stats: Dict[str, Any]) -> None:
        self._broadcast("hardware_stats", stats)

    def on_training_completed(
        self, job_id: str, duration_seconds: float, best_metric: float, model_path: str
    ) -> None:
        self._broadcast("training_completed", {
            "job_id": job_id,
            "duration_seconds": round(float(duration_seconds), 2),
            "best_metric": round(float(best_metric), 5),
            "model_path": str(model_path),
        })

    def on_training_aborted(self, epoch: int, reason: str) -> None:
        self._broadcast("training_aborted", {
            "job_id": self.job_id,
            "epoch": epoch + 1,
            "reason": reason,
        })

    def on_error(self, error: Exception, stage: str) -> None:
        err_card = classify_exception(error, details=f"Error in {stage}: {str(error)}")
        payload = err_card.to_ws_payload()
        payload["job_id"] = self.job_id
        payload["stage"] = stage
        self._broadcast("training_error", payload)


# Backward compatibility alias
WebSocketTrainingCallback = WebSocketTelemetryCallback


async def _handle_websocket_connection(websocket: WebSocket) -> None:
    await broadcaster.register(websocket)
    try:
        while True:
            # Keep connection alive; accept optional ping/command frames
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.debug("WebSocket connection terminated: %s", e)
    finally:
        # Keep this original socket registered until its existing background
        # send has returned. Only this connection stops admitting new sends.
        lock = broadcaster._connection_sends.get(websocket)
        if lock is None:
            if websocket in broadcaster.active_connections:
                raise RuntimeError("Registered telemetry connection send lock is missing")
            broadcaster.unregister(websocket)
        else:
            broadcaster._closing_connections.add(websocket)
            async with lock:
                broadcaster.unregister(websocket)
            del broadcaster._connection_sends[websocket]
            broadcaster._closing_connections.discard(websocket)


@observe_original_telemetry_handler(router.websocket("/ws/telemetry"))
async def websocket_telemetry_endpoint(websocket: WebSocket):
    """Primary telemetry WebSocket endpoint specified in user request."""
    await _handle_websocket_connection(websocket)


@router.websocket("/ws/training")
async def websocket_training_endpoint(websocket: WebSocket):
    """Canonical telemetry WebSocket endpoint specified in PROJECT.md and TEST_INFRA.md."""
    await _handle_websocket_connection(websocket)
