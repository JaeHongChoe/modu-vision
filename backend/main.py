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
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path for unified backend imports
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# SCM must connect its dispatcher before importing Studio/GPU application code.
if __name__ == '__main__' and sys.argv[1:2] == ['--windows-inspection-service']:
    from backend.engine.windows_inspection_service import main as service_main
    raise SystemExit(service_main(sys.argv[2:]))

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Query, Path as ApiPath
from fastapi.middleware.cors import CORSMiddleware
import httpx
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

# Compatibility patch for Starlette TestClient with httpx >= 0.28.0
_orig_httpx_init = httpx.Client.__init__
def _compat_httpx_init(self, *args, app=None, **kwargs):
    return _orig_httpx_init(self, *args, **kwargs)
httpx.Client.__init__ = _compat_httpx_init

from backend.api.routes_annotation import router as annotation_router
from backend.api.routes_compute import router as compute_router
from backend.api.routes_dataset import router as dataset_router
from backend.api.routes_dataset_imports import router as dataset_imports_router
from backend.api.routes_image_library import router as image_library_router
from backend.api.routes_job_events import router as job_events_router
from backend.api.routes_dataset_versions import router as dataset_versions_router
from backend.api.routes_dataset_metadata import router as dataset_metadata_router, format_router as dataset_format_router
from backend.api.routes_evaluation import router as evaluation_router
from backend.api.routes_evaluation_history import router as evaluation_history_router
from backend.api.routes_export import router as export_router
from backend.api.routes_data_workbench import router as data_workbench_router
from backend.api.routes_capture_intake import router as capture_intake_router
from backend.api.routes_team_data import router as team_data_router
from backend.api.routes_flow_workspace import router as flow_workspace_router
from backend.api.routes_flow_evaluation import router as flow_evaluation_router
from backend.api.routes_image_truth import router as image_truth_router
from backend.api.routes_training_workspace import router as training_workspace_router
from backend.api.routes_product_delivery import router as product_delivery_router
from backend.api.routes_flowchart import router as flowchart_router
from backend.api.routes_inspections import router as inspections_router
from backend.api.routes_label_suggestions import router as label_suggestions_router
from backend.api.routes_label_candidates import router as label_candidates_router
from backend.api.routes_runtime_services import router as runtime_services_router
from backend.api.routes_model_operations import router as model_operations_router
from backend.api.routes_fleet import router as fleet_router
from backend.api.routes_training_engine import router as training_engine_router
from backend.api.routes_model_deployments import router as model_deployments_router
from backend.api.routes_ocr import router as ocr_router
from backend.api.routes_enhancement import router as enhancement_router
from backend.api.routes_model_catalog import router as model_catalog_router
from backend.api.routes_workers import router as workers_router
from backend.api.routes_onboarding import router as onboarding_router
from backend.api.routes_provenance import router as provenance_router
from backend.api.routes_defect_gan import router as defect_gan_router
from backend.api.routes_rotated_detection import router as rotated_detection_router
from backend.api.routes_project import get_current_project, router as project_router
from backend.api.routes_project_preferences import router as project_preferences_router
from backend.api.routes_patch_classification import router as patch_classification_router
from backend.api.routes_rotation import router as rotation_router
from backend.api.routes_automated_training import router as automated_training_router
from backend.api.routes_geometry import router as geometry_router
from backend.api.routes_accounts import router as accounts_router
from backend.api.routes_mask_exchange import router as mask_exchange_router
from backend.api.routes_artifacts import router as artifacts_router
from backend.api.routes_dicom import router as dicom_router
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

from backend.contracts.context import (
    ArtifactRef, ArtifactRegistration, ContextRegistry, current_project_context,
    declared_context, get_project_context,
)


class ProjectContextMiddleware:
    """Freeze project/actor identity before the existing storage scopes run."""
    def __init__(self, app, project_app):
        self.app, self.project_app = app, project_app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] not in {'http', 'websocket'} or path == '/health' or path.startswith('/api/accounts/') or scope.get('method') == 'OPTIONS':
            return await self.app(scope, receive, send)
        scope.setdefault('app', self.project_app)
        state = scope.setdefault('state', {})
        registry = self.project_app.state.context_registry
        account = state.get('account_user')
        selection = path in {'/api/project/create', '/api/project/open', '/api/project/restore'}
        async def reject(exc):
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            else:
                await JSONResponse({'detail': exc.detail}, status_code=exc.status_code)(scope, receive, send)
        try:
            headers = Headers(scope=scope)
            explicit = declared_context(headers, query_string=scope.get('query_string') if scope['type']=='websocket' else None)
            if explicit and ((account and explicit.workspace_id != registry.workspace_id)
                             or explicit.actor_id != (account['id'] if account else registry.local_actor_id)
                             or explicit.mode != ('team' if account else 'local')):
                raise HTTPException(403, 'Explicit context does not match the authenticated workspace actor')
            project = state.get('scoped_project')
            if not selection:
                if project is None:
                    identifier = explicit.project_id if explicit else headers.get('x-vision-project')
                    project = registry.local_project(identifier, HTTPConnection(scope),workspace_id=explicit.workspace_id if explicit else None) if identifier else get_current_project(HTTPConnection(scope))
                if explicit and explicit.project_id != project['id']:
                    raise HTTPException(403, 'Explicit context project is not authorized')
                registry.register_project(project)
                state['scoped_project'] = project
                state['project_context'] = registry.context(project, account)
                if explicit and explicit.workspace_id != state['project_context'].workspace_id:
                    raise HTTPException(403, 'Explicit workspace context does not identify this project location')
        except HTTPException as exc:
            return await reject(exc)
        token = current_project_context.set(state.get('project_context'))
        held_start = None
        selected_body = bytearray()
        async def emit_start(message):
            context = state.get('project_context')
            if context:
                import json
                message = {**message, 'headers': [*message.get('headers', []),
                    (b'x-vision-context', json.dumps(context.model_dump(), separators=(',', ':')).encode('ascii'))]}
            await send(message)
        async def contextual_send(message):
            nonlocal held_start
            if selection and message['type'] == 'http.response.start':
                held_start = message
                return
            if selection and message['type'] == 'http.response.body':
                selected_body.extend(message.get('body', b''))
                if message.get('more_body'):
                    return
                if held_start and held_start['status'] < 300:
                    import json
                    selected = json.loads(selected_body)
                    try:
                        registry.register_project(selected)
                    except HTTPException as exc:
                        return await reject(exc)
                    state['project_context'] = registry.context(selected, account)
                if held_start:
                    await emit_start(held_start)
                return await send({**message, 'body': bytes(selected_body)})
            if message['type'] == 'http.response.start':
                return await emit_start(message)
            await send(message)
        try:
            await self.app(scope, receive, contextual_send)
        finally:
            current_project_context.reset(token)


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
        if scope["type"] != "http" or scope.get('path','').startswith('/api/accounts/') or scope.get("path") in {
            "/health", "/api/project/create", "/api/project/open",
            "/api/project/compatibility/preview", "/api/project/compatibility/apply",
        }:
            await self.app(scope, receive, send)
            return
        scope.setdefault("app", self.project_app)
        project = get_current_project(Request(scope))
        project_token = set_request_project_root(Path(project["project_dir"]))
        from backend.engine.annotation_storage import set_request_shared_scope,reset_request_shared_scope
        shared_token=set_request_shared_scope(bool(scope.get('state',{}).get('account_user')))
        annotation_token = set_request_annotation_root(Path(project["annotations_dir"]))
        split_token = set_request_split_root(Path(project["dataset_dir"]) / "splits")
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_split_root(split_token)
            reset_request_annotation_root(annotation_token)
            reset_request_project_root(project_token)
            reset_request_shared_scope(shared_token)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager orchestrating clean startup and shutdown sequences."""
    # Startup Sequence
    loop = asyncio.get_running_loop()
    broadcaster.start(loop)
    from backend.remote.coordinator import recover_remote_jobs
    recover_remote_jobs(training_job_manager)
    from backend.engine.local_training_worker import recover_local_jobs
    recover_local_jobs(training_job_manager)
    from backend.api.routes_dataset_imports import recover_imports_at_startup
    recover_imports_at_startup(app)
    from backend.api.routes_workers import stop_for_shutdown as stop_worker_preflight
    from backend.engine.worker_preflight import sweep_stale_runs
    # Run folders of preflights an earlier app stopped mid-run (its child exited with it) are removed off the event loop.
    threading.Thread(target=sweep_stale_runs, name="PreflightRunSweep", daemon=True).start()
    logger.info("Vision AI Studio backend daemon initialized (v%s).", VERSION)
    yield
    # Graceful Shutdown Sequence
    logger.info("Initiating Vision AI Studio backend shutdown...")
    # A normal quit keeps job ownership: owned workers are detached with a recorded intent and reattached on restart.
    training_job_manager.detach_all_for_shutdown()
    # A running worker preflight is not a job to reattach: its child is stopped through its handle and its
    # reservation released.
    stop_worker_preflight()
    await broadcaster.shutdown()
    clear_device_cache()
    logger.info("Shutdown cleanup complete.")


def create_app(project_dir: Optional[str] = None, shared_auth_dir: Optional[str] = None) -> FastAPI:
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
    app.state.context_registry = ContextRegistry(p_dir)

    # The Electron supervisor provides a fresh capability for each daemon process.
    # Standalone invocations generate one too, so a missing environment variable
    # never silently disables API authorization.
    app.state.api_token = os.environ.get("VISION_AI_STUDIO_API_TOKEN") or secrets.token_urlsafe(32)
    from backend.engine.shared_accounts import AccountStore
    app.state.accounts=AccountStore(Path(shared_auth_dir)/'accounts.sqlite') if shared_auth_dir else None
    app.add_middleware(ProjectStorageScopeMiddleware, project_app=app)
    app.add_middleware(ProjectContextMiddleware, project_app=app)
    if app.state.accounts is not None:
        from backend.api.shared_authorization import SharedAuthorizationMiddleware
        app.add_middleware(SharedAuthorizationMiddleware,project_app=app)
    else:
        app.add_middleware(DesktopApiAuthMiddleware, api_token=app.state.api_token)

    # CORS is outermost so permitted renderers can read authentication errors.
    # "null" is needed by packaged file://, but the process token still gates it.
    cors_options = {
        "allow_origins": ["http://127.0.0.1:5173", "http://localhost:5173", "null"],
        "allow_credentials": False,
        "allow_methods": ["*"],
        "allow_headers": ["Content-Type", "X-Vision-Token", "Authorization", "X-Vision-Project", "X-Vision-Context"],
        "expose_headers": ["X-Vision-Context"],
    }
    configured_origins = os.environ.get("MODU_BROWSER_ORIGINS", "")
    if configured_origins:
        import json
        from backend.contracts.authentication import configure_browser_origins
        try:
            origins = json.loads(configured_origins)
            if not isinstance(origins, list) or any(
                not isinstance(origin, str) or "*" in origin for origin in origins
            ):
                raise ValueError("Expected an array of exact origins")
            from urllib.parse import urlsplit
            for origin in origins:
                authority = urlsplit(origin)
                # Accessing port validates its syntax and range.
                _ = authority.port
                if authority.netloc.endswith(":"):
                    raise ValueError("Origin port must not be empty")
            if origins:
                cors_options = configure_browser_origins(app, origins)
        except (ValueError, TypeError) as exc:
            raise ValueError("MODU_BROWSER_ORIGINS must be a JSON array of exact HTTPS origins") from exc
    app.add_middleware(CORSMiddleware, **cors_options)

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
    @app.get('/api/context')
    def request_context(request: Request):
        return {'version': 1, 'project_context': get_project_context(request).model_dump()}

    @app.post('/api/context/artifacts')
    def register_artifact(body: ArtifactRegistration, request: Request):
        context = get_project_context(request)
        ref = app.state.context_registry.register_artifact(get_current_project(request), body)
        return {'project_context': context.model_dump(), 'artifact_ref': ref.model_dump()}

    @app.get('/api/context/artifacts/{artifact_id}')
    def artifact_reference(request: Request, artifact_id: str = ApiPath(pattern=r'^[A-Za-z0-9_.:-]{1,128}$'), revision: int = Query(gt=0), sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        ref = ArtifactRef(id=artifact_id, revision=revision, sha256=sha256)
        app.state.context_registry.resolve_artifact(get_current_project(request), ref)
        return {'project_context': get_project_context(request).model_dump(), 'artifact_ref': ref.model_dump()}

    @app.get('/api/context/artifacts/{artifact_id}/content')
    def artifact_content(request: Request, artifact_id: str = ApiPath(pattern=r'^[A-Za-z0-9_.:-]{1,128}$'), revision: int = Query(gt=0), sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        import hashlib
        ref = ArtifactRef(id=artifact_id, revision=revision, sha256=sha256)
        path = app.state.context_registry.resolve_artifact(get_current_project(request), ref)
        # Return the verified bytes, rather than reopening a mutable path after
        # verification. Large artifact streaming belongs to S1-06's store.
        with path.open('rb') as handle:
            content = handle.read(64 * 1024 * 1024 + 1)
        if len(content) > 64 * 1024 * 1024:
            raise HTTPException(413, 'Artifact exceeds the context content limit (64 MiB)')
        if hashlib.sha256(content).hexdigest() != ref.sha256:
            raise HTTPException(409, 'Artifact content changed during read')
        return Response(content, media_type='application/octet-stream', headers={'ETag': '"' + ref.sha256 + '"'})

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
    app.include_router(artifacts_router)
    app.include_router(project_router)
    app.include_router(project_preferences_router)
    app.include_router(accounts_router)
    app.include_router(mask_exchange_router)
    app.include_router(dicom_router)
    app.include_router(compute_router)
    app.include_router(dataset_router)
    app.include_router(dataset_imports_router)
    app.include_router(image_library_router)
    app.include_router(job_events_router)
    app.include_router(dataset_versions_router)
    app.include_router(dataset_metadata_router)
    app.include_router(dataset_format_router)
    app.include_router(annotation_router)
    app.include_router(label_suggestions_router)
    app.include_router(label_candidates_router)
    app.include_router(runtime_services_router)
    app.include_router(model_operations_router)
    app.include_router(fleet_router)
    app.include_router(training_engine_router)
    app.include_router(training_router)
    app.include_router(evaluation_router)
    app.include_router(evaluation_history_router)
    app.include_router(model_deployments_router)
    app.include_router(ocr_router)
    app.include_router(patch_classification_router)
    app.include_router(rotation_router)
    app.include_router(automated_training_router)
    app.include_router(geometry_router)
    app.include_router(enhancement_router)
    app.include_router(model_catalog_router)
    app.include_router(workers_router)
    app.include_router(onboarding_router)
    app.include_router(provenance_router)
    app.include_router(defect_gan_router)
    app.include_router(rotated_detection_router)
    app.include_router(data_workbench_router)
    app.include_router(capture_intake_router)
    app.include_router(team_data_router)
    app.include_router(flow_workspace_router)
    app.include_router(flow_evaluation_router)
    app.include_router(image_truth_router)
    app.include_router(training_workspace_router)
    app.include_router(product_delivery_router)
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
    parser.add_argument('--shared-auth-dir',type=str,default=None,help='Enable authenticated shared-project server using owned account storage')
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

    app = create_app(project_dir=args.project_dir,shared_auth_dir=args.shared_auth_dir)

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
        logger.info("Received signal %d, detaching owned training and triggering exit...", sig)
        try:
            training_job_manager.detach_all_for_shutdown()
        finally:
            server.should_exit = True

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _sig_handler)
        except (ValueError, AttributeError):
            pass

    if os.environ.get(STOP_ON_STDIN_EOF) == "1":
        threading.Thread(target=_stop_when_stdin_closes, args=(server,), name="StopOnStdinEOF", daemon=True).start()

    server.run(sockets=[sock])


# The desktop supervisor keeps this process's stdin open and closes it to ask for a graceful stop (when the app itself
# exits, its exit hook stops this process outright instead). On Windows a stop signal from Electron terminates the
# process outright, so this is the stop that runs the shutdown sequence there; it is opt-in because a server started
# from a shell may have no stdin at all.
STOP_ON_STDIN_EOF = "VISION_AI_STUDIO_STOP_ON_STDIN_EOF"


def _stop_when_stdin_closes(server) -> None:
    try:
        # The raw descriptor, not sys.stdin: a daemon thread blocked in a buffered read holds a lock that interpreter
        # shutdown would then wait for.
        while os.read(0, 65536):
            pass
    except OSError:
        pass  # a broken pipe also means the supervisor is gone
    logger.info("The supervisor closed stdin; detaching owned training and triggering exit...")
    try:
        training_job_manager.detach_all_for_shutdown()
    finally:  # a failed detach (a locked database) never loses the stop request
        server.should_exit = True


if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1]=='--managed-service-project':
        if len(sys.argv)!=3:raise SystemExit('--managed-service-project requires exactly one project directory')
        from backend.engine.service_bootstrap import main as service_bootstrap_main
        service_bootstrap_main(sys.argv[2])
    elif len(sys.argv)>1 and sys.argv[1]=='--inspection-service':
        del sys.argv[1]
        from backend.engine.inspection_service import main as inspection_service_main
        raise SystemExit(inspection_service_main())
    else:run_server()
