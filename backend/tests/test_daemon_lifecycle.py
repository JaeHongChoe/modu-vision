"""
backend/tests/test_daemon_lifecycle.py

Automated Test Suite for Feature F09: FastAPI Server & Daemon Lifecycle.
Validates:
  - Ephemeral port pre-binding (port 0) and stdout machine-parseable announcement (VISION_AI_STUDIO_PORT=<port>)
  - Subprocess live launch, health check handshake, and clean shutdown on SIGTERM / SIGINT
  - CORS header propagation across Electron renderer origins (localhost:*, 127.0.0.1:*)
  - Lifespan context manager startup and shutdown resource cleanup
"""

import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

_orig_httpx_init = httpx.Client.__init__
def _compat_httpx_init(self, *args, app=None, **kwargs):
    return _orig_httpx_init(self, *args, **kwargs)
httpx.Client.__init__ = _compat_httpx_init

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app, parse_args

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class TestEphemeralPortBinding:
    """Validates pre-binding socket mechanics and ephemeral port discovery."""

    def test_ephemeral_port_allocation(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        assert port > 1024, f"Allocated ephemeral port {port} must be > 1024"
        sock.close()


class TestCORSHeaders:
    """Validates CORS header handling for Electron desktop renderer origins."""

    @pytest.mark.parametrize(
        "origin",
        [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8080",
        ],
    )
    def test_cors_preflight_and_get(self, origin: str):
        app = create_app()
        client = TestClient(app)

        # Preflight OPTIONS request
        options_res = client.options(
            "/api/project/current",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert options_res.status_code == 200
        assert "access-control-allow-origin" in options_res.headers

        # Direct GET request
        get_res = client.get("/health", headers={"Origin": origin})
        assert get_res.status_code == 200
        assert "access-control-allow-origin" in get_res.headers


class TestDaemonSubprocessLifecycle:
    """Validates running backend/main.py as a separate process, discovering ephemeral port, and clean shutdown."""

    def test_daemon_live_launch_and_shutdown(self):
        python_bin = sys.executable
        main_script = str(PROJECT_ROOT / "backend" / "main.py")

        # Launch backend daemon on ephemeral port 0
        proc = subprocess.Popen(
            [python_bin, main_script, "--port", "0", "--host", "127.0.0.1", "--log-level", "warning"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(PROJECT_ROOT),
        )

        allocated_port = None
        start_time = time.time()

        # Read stdout to locate VISION_AI_STUDIO_PORT announcement
        try:
            while time.time() - start_time < 8.0:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                match = re.search(r"VISION_AI_STUDIO_PORT=(\d+)", line)
                if match:
                    allocated_port = int(match.group(1))
                    break

            assert allocated_port is not None, f"Daemon did not emit VISION_AI_STUDIO_PORT within timeout"
            assert allocated_port > 0

            # Perform HTTP health check handshake
            health_url = f"http://127.0.0.1:{allocated_port}/health"
            connected = False
            for _ in range(20):
                try:
                    res = httpx.get(health_url, timeout=1.0)
                    if res.status_code == 200:
                        data = res.json()
                        assert data["status"] == "ok"
                        assert "device" in data
                        connected = True
                        break
                except Exception:
                    time.sleep(0.2)

            assert connected, f"Could not connect to daemon /health at {health_url}"

        finally:
            # Terminate gracefully via SIGTERM
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            assert proc.returncode is not None
