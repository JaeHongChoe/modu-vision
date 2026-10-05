"""
backend/api/websocket_telemetry.py

High-Speed Asynchronous WebSocket Telemetry Streaming Layer for Vision AI Studio.
Broadcasts live training loss, step progress, epoch ETA, overall training ETA,
and hardware metrics (GPU/MPS/CPU/RAM) at 10-50Hz with thread-safe queue bridging
and client connection management.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None
        self._dispatch_task: Optional[asyncio.Task] = None
        self._hw_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._device = None

    @property
    def active_connections(self) -> Set[WebSocket]:
        return self._active_connections

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Sets the running asyncio event loop and initializes queues if needed."""
        self.start(loop)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Initializes queues and background dispatch loops within the running event loop."""
        if self._running and self._loop == loop:
            return
        self._loop = loop
        self._queue = asyncio.Queue()
        self._running = True
        try:
            self._device = get_device()
        except Exception:
            self._device = None

        # Cancel existing tasks if restarting
        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
        if self._hw_task and not self._hw_task.done():
            self._hw_task.cancel()

        self._dispatch_task = self._loop.create_task(self._event_dispatch_loop())
        self._hw_task = self._loop.create_task(self._hardware_telemetry_loop())
        logger.info("TelemetryBroadcaster started.")

    async def shutdown(self) -> None:
        """Gracefully shuts down all active WebSocket connections and background tasks."""
        self._running = False
        if self._hw_task:
            self._hw_task.cancel()
            try:
                await self._hw_task
            except (asyncio.CancelledError, Exception):
                pass
            self._hw_task = None

        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except (asyncio.CancelledError, Exception):
                pass
            self._dispatch_task = None

        # Close all active websockets with code 1001 (Going Away)
        for ws in list(self._active_connections):
            await self._safe_close(ws)
        self._active_connections.clear()
        logger.info("TelemetryBroadcaster shut down cleanly.")

    async def _safe_close(self, ws: WebSocket) -> None:
        try:
            await ws.close(code=1001, reason="Server shutting down")
        except Exception:
            pass

    async def connect(self, websocket: WebSocket) -> None:
        """Alias for register to support different naming conventions."""
        await self.register(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Alias for unregister."""
        self.unregister(websocket)

    async def register(self, websocket: WebSocket) -> None:
        """Accepts and registers a new WebSocket client, sending initial state snapshot."""
        await websocket.accept()
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
            if not store.project_role(user['id'],project['id']):return False
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
        broadcaster.unregister(websocket)


@router.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    """Primary telemetry WebSocket endpoint specified in user request."""
    await _handle_websocket_connection(websocket)


@router.websocket("/ws/training")
async def websocket_training_endpoint(websocket: WebSocket):
    """Canonical telemetry WebSocket endpoint specified in PROJECT.md and TEST_INFRA.md."""
    await _handle_websocket_connection(websocket)
