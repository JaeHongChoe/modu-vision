"""
backend/tests/test_websocket_telemetry.py

Automated Test Suite for Feature F11: High-Speed WebSocket Telemetry Streaming.
Validates:
  - RateLimiter frequency throttling (10-50Hz) and forced emission on epoch boundary
  - TelemetryBroadcaster connection lifecycle and thread-safe queue dispatching
  - Hardware telemetry polling and broadcast
  - WebSocketTelemetryCallback step progress, epoch ETA, and error event streaming
  - Live WebSocket communication on both /ws/telemetry and /ws/training routes
"""

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest
import httpx

_orig_httpx_init = httpx.Client.__init__
def _compat_httpx_init(self, *args, app=None, **kwargs):
    return _orig_httpx_init(self, *args, **kwargs)
httpx.Client.__init__ = _compat_httpx_init

from fastapi.testclient import TestClient

from backend.api.websocket_telemetry import (
    RateLimiter,
    TelemetryBroadcaster,
    WebSocketTelemetryCallback,
    broadcaster,
)
from backend.main import create_app


class TestRateLimiter:
    """Validates timestamp-based rate limiter capping frequency at 10-50Hz."""

    def test_rate_limiter_interval_bounds(self):
        # 30Hz should have ~0.033s interval
        rl30 = RateLimiter(max_hz=30.0)
        assert 0.030 <= rl30.min_interval <= 0.036

        # Frequencies outside [10, 50] should be clamped
        rl_low = RateLimiter(max_hz=2.0)
        assert rl_low.min_interval == 1.0 / 10.0  # Clamped to 10Hz

        rl_high = RateLimiter(max_hz=200.0)
        assert rl_high.min_interval == 1.0 / 50.0  # Clamped to 50Hz

    def test_rate_limiter_throttling(self):
        rl = RateLimiter(max_hz=20.0)  # 50ms interval
        assert rl.should_emit(force=False) is True
        # Immediate subsequent call should be throttled
        assert rl.should_emit(force=False) is False

    def test_rate_limiter_forced_emission(self):
        rl = RateLimiter(max_hz=20.0)
        assert rl.should_emit(force=False) is True
        # Immediate call with force=True must bypass rate limiting
        assert rl.should_emit(force=True) is True
        assert rl.should_emit(force=True) is True


class TestTelemetryBroadcasterUnit:
    """Unit tests for TelemetryBroadcaster connection management and queue bridging."""

    def test_broadcaster_start_and_shutdown(self):
        async def _run():
            bc = TelemetryBroadcaster()
            loop = asyncio.get_running_loop()
            bc.start(loop)
            assert bc._running is True
            assert bc._queue is not None
            await bc.shutdown()
            assert bc._running is False
            assert len(bc.active_connections) == 0
        asyncio.run(_run())

    def test_broadcast_sync_enqueues_threadsafe(self):
        async def _run():
            bc = TelemetryBroadcaster()
            loop = asyncio.get_running_loop()
            bc._loop = loop
            bc._queue = asyncio.Queue()
            bc._running = True

            bc.broadcast_sync("test_event", {"metric": 42})
            # Allow loop to process call_soon_threadsafe
            await asyncio.sleep(0.01)
            assert not bc._queue.empty()
            item = await bc._queue.get()
            assert item["type"] == "test_event"
            assert item["data"]["metric"] == 42
            await bc.shutdown()
        asyncio.run(_run())


class TestWebSocketTelemetryCallback:
    """Validates TelemetryCallback calculation of epoch ETA, overall ETA, and error payloads."""

    def test_callback_step_and_epoch_eta(self):
        events = []
        with patch.object(broadcaster, "broadcast_sync", side_effect=lambda evt, data: events.append((evt, data))):
            cb = WebSocketTelemetryCallback(job_id="job_test_01", max_hz=50.0)
            cb.on_training_start({"epochs": 5, "batch_size": 16})
            assert len(events) == 1
            assert events[0][0] == "training_started"
            assert events[0][1]["total_epochs"] == 5

            # Simulate step 0 out of 100
            time.sleep(0.01)
            cb.on_step_end(step=0, total_steps=100, current_loss=0.85, epoch=0)
            step_events = [e for e in events if e[0] == "step_progress"]
            assert len(step_events) >= 1
            assert "epoch_eta_seconds" in step_events[-1][1]
            assert step_events[-1][1]["current_loss"] == 0.85

            # Simulate epoch end
            cb.on_epoch_end(epoch=0, total_epochs=5, train_loss=0.65, val_loss=0.55, lr=0.001, metrics={"val_acc": 0.92})
            epoch_events = [e for e in events if e[0] == "epoch_progress"]
            assert len(epoch_events) == 1
            assert "eta_seconds" in epoch_events[0][1]
            assert epoch_events[0][1]["epoch"] == 1
            assert epoch_events[0][1]["metrics"]["val_acc"] == 0.92

    def test_callback_error_handling(self):
        events = []
        with patch.object(broadcaster, "broadcast_sync", side_effect=lambda evt, data: events.append((evt, data))):
            cb = WebSocketTelemetryCallback(job_id="job_err_01")
            cb.on_error(RuntimeError("CUDA error: out of memory"), stage="train_batch")
            err_events = [e for e in events if e[0] == "training_error"]
            assert len(err_events) == 1
            payload = err_events[0][1]
            assert payload["error_code"] == "ERR_001"
            assert payload["stage"] == "train_batch"
            assert "메모리" in payload["message_ko"]


class TestLiveWebSocketEndpoints:
    """Integration tests connecting to live WebSocket endpoints with TestClient."""

    def test_ws_telemetry_connection_and_ping(self):
        app = create_app()
        with TestClient(app) as client:
            with client.websocket_connect(
                "/ws/telemetry", headers={"X-Vision-Token": app.state.api_token},
            ) as ws:
                # First frame is initial hardware stats snapshot
                initial_frame = ws.receive_json()
                assert initial_frame["type"] == "hardware_stats"
                assert "cpu_percent" in initial_frame

                # Send ping frame
                ws.send_text("ping")
                resp = ws.receive_text()
                assert resp == "pong"

    def test_ws_training_alias_endpoint(self):
        app = create_app()
        with TestClient(app) as client:
            with client.websocket_connect(
                "/ws/training", headers={"X-Vision-Token": app.state.api_token},
            ) as ws:
                initial_frame = ws.receive_json()
                assert initial_frame["type"] == "hardware_stats"
                ws.send_text("ping")
                assert ws.receive_text() == "pong"
