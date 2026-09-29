"""
backend/main.py

FastAPI Entry Point & Desktop Daemon Server for Vision AI Studio.
Handles:
  - Ephemeral port socket allocation with machine-parseable stdout logging (VISION_AI_STUDIO_PORT=<port>)
  - Health check endpoint (GET /health) strictly conforming to PROJECT.md line 144
  - WebSocket telemetry route registration (/ws/telemetry, /ws/training)
  - All REST API routes (/api/project, /api/dataset, /api/annotations, /api/training, /api/evaluation, /api/report, /api/errors)
  - CORS configuration for Electron renderer origins
  - Graceful shutdown signal interceptors (SIGTERM, SIGINT) and lifespan resource cleanup
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import signal
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path for unified backend imports
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import httpx
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Compatibility patch for Starlette TestClient with httpx >= 0.28.0
_orig_httpx_init = httpx.Client.__init__
def _compat_httpx_init(self, *args, app=None, **kwargs):
    return _orig_httpx_init(self, *args, **kwargs)
httpx.Client.__init__ = _compat_httpx_init

from backend.api.routes_annotation import router as annotation_router
from backend.api.routes_compute import router as compute_router
from backend.api.routes_dataset import router as dataset_router
from backend.api.routes_dataset_versions import router as dataset_versions_router
from backend.api.routes_evaluation import router as evaluation_router
from backend.api.routes_export import router as export_router
from backend.api.routes_flowchart import router as flowchart_router
from backend.api.routes_inspections import router as inspections_router
from backend.api.routes_label_suggestions import router as label_suggestions_router
from backend.api.routes_model_deployments import router as model_deployments_router
from backend.api.routes_ocr import router as ocr_router
from backend.api.routes_defect_gan import router as defect_gan_router
from backend.api.routes_rotated_detection import router as rotated_detection_router
from backend.api.routes_project import get_current_project, router as project_router
from backend.api.routes_report import router as report_router
from backend.api.routes_training import router as training_router, training_job_manager
from backend.api.websocket_telemetry import broadcaster, router as telemetry_router
from backend.engine.device import clear_device_cache, get_device, get_device_info
from backend.engine.annotation_storage import (
    reset_request_annotation_root, reset_request_project_root,
    set_request_annotation_root, set_request_project_root,
)
from backend.engine.dataset_loaders import reset_request_split_root, set_request_split_root
from backend.utils.error_catalog import (
    CATALOG,
    ErrorCatalogItem,
    format_error_response,
    get_all_errors,
    get_error,
)

logger = logging.getLogger("vision_ai_studio.daemon")

VERSION = "0.1.0"


class DesktopApiAuthMiddleware:
    """Require the Electron process capability for HTTP and WebSocket API access."""

    def __init__(self, app: ASGIApp, api_token: str):
        self.app = app
        self.api_token = api_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if scope_type == "http" and (scope.get("method") == "OPTIONS" or scope.get("path") == "/health"):
            await self.app(scope, receive, send)
            return
        provided_token = Headers(scope=scope).get("x-vision-token", "")
        if not secrets.compare_digest(provided_token, self.api_token):
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": "Desktop session authorization required"}, status_code=401)(
                    scope, receive, send,
                )
            return
        await self.app(scope, receive, send)


class ProjectStorageScopeMiddleware:
    """Carry the authenticated project's label and split roots through routes."""

    def __init__(self, app: ASGIApp, project_app: FastAPI):
        self.app = app
        self.project_app = project_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in {
            "/health", "/api/project/create", "/api/project/open",
        }:
            await self.app(scope, receive, send)
            return
        scope.setdefault("app", self.project_app)
        project = get_current_project(Request(scope))
        project_token = set_request_project_root(Path(project["project_dir"]))
        annotation_token = set_request_annotation_root(Path(project["annotations_dir"]))
        split_token = set_request_split_root(Path(project["dataset_dir"]) / "splits")
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_split_root(split_token)
            reset_request_annotation_root(annotation_token)
            reset_request_project_root(project_token)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager orchestrating clean startup and shutdown sequences."""
    # Startup Sequence
    loop = asyncio.get_running_loop()
    broadcaster.start(loop)
    from backend.remote.coordinator import recover_remote_jobs
    recover_remote_jobs(training_job_manager)
    logger.info("Vision AI Studio backend daemon initialized (v%s).", VERSION)
    yield
    # Graceful Shutdown Sequence
    logger.info("Initiating Vision AI Studio backend shutdown...")
    training_job_manager.abort_all()
    await broadcaster.shutdown()
    clear_device_cache()
    logger.info("Shutdown cleanup complete.")


def create_app(project_dir: Optional[str] = None) -> FastAPI:
    """Factory creating and configuring the FastAPI application."""
    app = FastAPI(
        title="Vision AI Studio Backend",
        version=VERSION,
        lifespan=lifespan,
    )

    # Store project base directory in app state
    p_dir = Path(project_dir) if project_dir else (ROOT_DIR / "projects")
    p_dir.mkdir(parents=True, exist_ok=True)
    app.state.project_dir = p_dir

    # The Electron supervisor provides a fresh capability for each daemon process.
    # Standalone invocations generate one too, so a missing environment variable
    # never silently disables API authorization.
    app.state.api_token = os.environ.get("VISION_AI_STUDIO_API_TOKEN") or secrets.token_urlsafe(32)
    app.add_middleware(ProjectStorageScopeMiddleware, project_app=app)
    app.add_middleware(DesktopApiAuthMiddleware, api_token=app.state.api_token)

    # CORS is outermost so permitted renderers can read authentication errors.
    # "null" is needed by packaged file://, but the process token still gates it.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "null"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Content-Type", "X-Vision-Token"],
    )

    # Health check endpoint strictly conforming to PROJECT.md line 144
    @app.get("/health")
    def health_check():
        dev = get_device()
        info = get_device_info(dev)
        return {
            "status": "ok",
            "version": VERSION,
            "device": info.device_type,  # "mps" | "cuda" | "cpu"
            "device_name": info.device_name,
            "is_accelerated": info.is_accelerated,
            "torch_version": info.torch_version,
        }

    # Error catalog endpoints
    @app.get("/api/errors")
    def list_error_catalog():
        """Lists all registered industrial error catalog definitions."""
        return {"errors": list(get_all_errors().values())}

    @app.get("/api/errors/{code}")
    def get_error_by_code(code: str):
        """Retrieves specific error definition by code (e.g. ERR_001)."""
        item = get_error(code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Error code '{code}' not found in catalog")
        return item

    # Register all modular routers
    app.include_router(project_router)
    app.include_router(compute_router)
    app.include_router(dataset_router)
    app.include_router(dataset_versions_router)
    app.include_router(annotation_router)
    app.include_router(label_suggestions_router)
    app.include_router(training_router)
    app.include_router(evaluation_router)
    app.include_router(model_deployments_router)
    app.include_router(ocr_router)
    app.include_router(defect_gan_router)
    app.include_router(rotated_detection_router)
    app.include_router(flowchart_router)
    app.include_router(inspections_router)
    app.include_router(export_router)
    app.include_router(report_router)
    app.include_router(telemetry_router)

    return app


def parse_args():
    parser = argparse.ArgumentParser(description="Vision AI Studio Backend Daemon")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (0 for ephemeral)")
    parser.add_argument(
        "--project-dir",
        type=str,
        default=str(ROOT_DIR / "projects"),
        help="Base directory for project workspaces",
    )
    parser.add_argument(
        "--log-level", type=str, default="info", help="Log level (debug, info, warning, error)"
    )
    parser.add_argument(
        "--device", type=str, default="auto", help="Compute device override (mps, cuda, cpu, auto)"
    )
    return parser.parse_args()


def run_server():
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    app = create_app(project_dir=args.project_dir)

    # Ephemeral or Static Port Socket Allocation
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    actual_port = sock.getsockname()[1]

    # Machine-parseable output for Electron Supervisor
    print(f"VISION_AI_STUDIO_PORT={actual_port}", flush=True)
    logger.info("Bound to http://%s:%d (PID %d)", args.host, actual_port, os.getpid())

    config = uvicorn.Config(
        app=app,
        log_level=args.log_level.lower(),
        access_log=False,
    )
    server = uvicorn.Server(config=config)

    # Signal handlers for direct SIGINT/SIGTERM termination
    def _sig_handler(sig, frame):
        logger.info("Received signal %d, aborting training and triggering exit...", sig)
        training_job_manager.abort_all()
        server.should_exit = True

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _sig_handler)
        except (ValueError, AttributeError):
            pass

    server.run(sockets=[sock])


if __name__ == "__main__":
    run_server()
