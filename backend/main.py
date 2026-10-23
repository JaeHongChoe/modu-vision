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

# Owned application authentication precedes SCM and all mutable server imports.
from backend.engine.application_launch_handshake import early_backend_bootstrap, backend_bootstrap_ready, backend_work_admission
early_backend_bootstrap()

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
from backend.api.routes_model_execution import router as model_execution_router
from backend.api.routes_workers import router as workers_router
from backend.api.routes_onboarding import router as onboarding_router
from backend.api.routes_provenance import router as provenance_router
from backend.api.routes_defect_gan import router as defect_gan_router
from backend.api.routes_rotated_detection import router as rotated_detection_router
from backend.api.routes_project import get_current_project, router as project_router
from backend.api.routes_project_preferences import router as project_preferences_router
from backend.api.routes_assistant import router as assistant_router
from backend.api.routes_patch_classification import router as patch_classification_router
from backend.api.routes_rotation import router as rotation_router
from backend.api.routes_automated_training import router as automated_training_router
from backend.api.routes_geometry import router as geometry_router
from backend.api.routes_accounts import router as accounts_router
from backend.api.routes_mask_exchange import router as mask_exchange_router
from backend.api.routes_class_rename import router as class_rename_router
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
        # A write the person confirmed in the desktop assistant carries its run id; the central audit writers record it
        # as `via: assistant` beside the person's own actor (attribution only, the role check above is unchanged).
        from backend.engine import assistant_run
        run_token = assistant_run.bind(assistant_run.run_id_from_headers(Headers(scope=scope)))
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
            assistant_run.reset(run_token)
            current_project_context.reset(token)


class OwnedWorkAdmissionMiddleware:
    """Close HTTP/WS entries before project resolution can perform writes."""
    def __init__(self, app, admission):
        self.app, self.admission = app, admission
        if admission is not None: admission.bind_activity_middleware(self, app)
        if admission is not None and admission._current_project_requests is not None:
            admission._current_project_requests.bind_chain(self, app)
        if admission is not None and admission._desktop_denials is not None:
            admission._desktop_denials.bind_chain(self, app)
        if admission is not None and admission._shared_denials is not None:
            admission._shared_denials.bind_chain(self, app)
        if admission is not None and admission._account_requests is not None:
            admission._account_requests.bind_chain(self, app)
        if admission is not None and admission._flow_requests is not None:
            admission._flow_requests.bind_chain(self, app)
        if admission is not None and admission._flow_execution_requests is not None:
            admission._flow_execution_requests.bind_chain(self, app)
        if admission is not None and admission._UI_ancillary_requests is not None:
            admission._UI_ancillary_requests.bind_chain(self, app)
        if admission is not None and admission._telemetry_socket_requests is not None:
            admission._telemetry_socket_requests.bind_chain(self, app)

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'} or self.admission is None:
            return await self.app(scope, receive, send)
        activity = self.admission.request_activity(self, self.app, scope)
        if activity is not None:
            return await self._observed_call(scope, receive, send, activity)
        if not self.admission.enter(scope):
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1013})
            return await JSONResponse({'detail': 'Owned backend admission is closed for drain'}, status_code=409)(scope, receive, send)
        async def tracked_send(message):
            if message['type'] == 'http.response.start': self.admission.response(message['status'])
            await send(message)
        try: await self.app(scope, receive, tracked_send)
        finally: self.admission.leave()


    async def _observed_call(self, scope, receive, send, activity):
        admitted = self.admission.enter(scope, activity=activity)
        normal = False
        http_capture = None
        if admitted and scope.get('type') == 'http' and scope.get('method') in {'POST', 'GET'}:
            from backend.engine.required_flow_http_capture import begin_original_flow_http_capture
            http_capture = begin_original_flow_http_capture(self.admission, activity)
        async def tracked_send(message):
            if message['type'] == 'http.response.start': self.admission.response(message['status'])
            self.admission.observe_activity('send_attempt', activity, message)
            original_http_frame = http_capture.send_attempt(message) if http_capture is not None else None
            try: await send(message)
            except BaseException as error:
                self.admission.observe_activity('send_error', activity, error)
                raise
            self.admission.observe_activity('send_success', activity, message)
            if http_capture is not None: http_capture.sent(message, original_http_frame)
        async def tracked_receive():
            try: message = await receive()
            except BaseException as error:
                self.admission.observe_activity('receive_error', activity, error)
                raise
            self.admission.observe_activity('receive', activity, message)
            if http_capture is not None: http_capture.received(message)
            return message
        try:
            if admitted:
                from backend.engine.original_current_project_request import current_project_request_scope
                from backend.engine.original_desktop_auth_denial import desktop_denial_scope
                from backend.engine.original_shared_auth_denial import shared_denial_scope
                from backend.engine.original_owned_account_request import original_owned_account_request_scope
                from backend.engine.original_owned_flow_request import original_owned_flow_request_scope
                from backend.engine.original_owned_flow_execution_request import original_owned_flow_execution_request_scope
                from backend.engine.original_owned_UI_ancillary_request import original_owned_UI_ancillary_request_scope
                from backend.engine.original_owned_telemetry_socket_request import original_owned_telemetry_socket_scope
                with current_project_request_scope(self.admission, activity), desktop_denial_scope(self.admission, activity), shared_denial_scope(self.admission, activity), original_owned_account_request_scope(self.admission, activity), original_owned_flow_request_scope(self.admission, activity), original_owned_flow_execution_request_scope(self.admission, activity), original_owned_UI_ancillary_request_scope(self.admission, activity), original_owned_telemetry_socket_scope(self.admission, activity):
                    await self.app(scope, tracked_receive, tracked_send)
            elif scope['type'] == 'websocket':
                await tracked_send({'type': 'websocket.close', 'code': 1013})
            else:
                await JSONResponse({'detail': 'Owned backend admission is closed for drain'}, status_code=409)(scope, tracked_receive, tracked_send)
            normal = True
        except BaseException as error:
            self.admission.observe_activity('downstream_error', activity, error)
            raise
        finally:
            try:
                if admitted: self.admission.leave(activity=activity, normal_return=normal)
                else: self.admission.finish_rejected_activity(activity, normal_return=normal)
            finally:
                if http_capture is not None: http_capture.finish(normal)


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
                from backend.engine.original_desktop_auth_denial import original_desktop_denial_response
                with original_desktop_denial_response(self, scope, receive, send):
                    await JSONResponse({"detail": "Desktop session authorization required"}, status_code=401)(
                        scope, receive, send,
                    )
            return
        await self.app(scope, receive, send)


from backend.engine.original_current_project_request import observe_storage_scope


class ProjectStorageScopeMiddleware:
    """Carry the authenticated project's label and split roots through routes."""

    def __init__(self, app: ASGIApp, project_app: FastAPI):
        self.app = app
        self.project_app = project_app

    @observe_storage_scope
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get('path','').startswith('/api/accounts/') or scope.get("path") in {
            "/health", "/api/project/create", "/api/project/open",
            "/api/project/compatibility/preview", "/api/project/compatibility/apply",
            "/api/project/compatibility/recover", "/api/project/compatibility/global-preview",
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
    from backend.engine.global_store_paths import startup_admission
    with startup_admission():
        async with _admitted_lifespan(app):yield


def _capture_telemetry_startup_join(owner):
    """Bind the original broadcaster and method before its tasks are started."""
    from types import MethodType
    from backend.engine.application_launch_handshake import HandshakeError
    prepare = owner.prepare_shutdown
    if type(prepare) is not MethodType or prepare.__self__ is not owner:
        raise HandshakeError('Original telemetry broadcaster callback binding is unavailable')
    function = prepare.__func__
    def original():
        current = owner.prepare_shutdown
        if (broadcaster is not owner or type(current) is not MethodType
                or current.__self__ is not owner or current.__func__ is not function):
            raise HandshakeError('Original telemetry broadcaster or callback changed')
    def join(absolute_deadline):
        original()
        receipt = prepare(absolute_deadline)
        original()
        if (not isinstance(receipt, dict) or receipt.get('idle') is not True
                or receipt.get('supported') is not True or type(receipt.get('task_count')) is not int
                or receipt['task_count'] < 2 or receipt.get('joined_tasks') != receipt['task_count']
                or type(receipt.get('pending_tasks')) is not int or receipt['pending_tasks'] != 0
                or any(receipt.get(flag) is not False for flag in
                    ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))):
            raise HandshakeError('Original telemetry broadcaster local closure receipt differs')
        return receipt
    return join


@asynccontextmanager
async def _admitted_lifespan(app: FastAPI):
    """Lifespan context manager orchestrating clean startup and shutdown sequences."""
    from backend.api.routes_workers import stop_for_shutdown as stop_worker_preflight
    execution_stop=None;execution_thread=None;sweep_owner=None;startup_observation=None
    telemetry_owner = broadcaster
    telemetry_shutdown = telemetry_owner.shutdown
    try:
        admission = getattr(app.state, 'owned_work_admission', None)
        if admission is not None:
            from backend.engine.application_launch_handshake import begin_fresh_startup_observation
            startup_observation = begin_fresh_startup_observation(app, training_job_manager, telemetry_owner)
        # Startup may partially recover workers before a failure or supervisor
        # EOF. Cleanup must also run when the suspended lifespan is finalized
        # without Uvicorn's normal shutdown notification.
        loop = asyncio.get_running_loop()
        if admission is not None:
            admission.register_startup_join('telemetry_broadcaster',
                _capture_telemetry_startup_join(telemetry_owner))
        telemetry_owner.start(loop)
        from backend.remote.coordinator import recover_remote_jobs
        if startup_observation is None: recover_remote_jobs(training_job_manager)
        else: recover_remote_jobs(training_job_manager, outcome_observer=startup_observation.recovery)
        from backend.engine.local_training_worker import recover_local_jobs
        if startup_observation is None: recover_local_jobs(training_job_manager)
        else: recover_local_jobs(training_job_manager, outcome_observer=startup_observation.recovery)
        from backend.api.routes_dataset_imports import recover_imports_at_startup
        if startup_observation is None: recover_imports_at_startup(app)
        else: recover_imports_at_startup(app, outcome_observer=startup_observation.recovery)
        from backend.engine.worker_preflight import sweep_stale_runs
        # Only stale preflight folders of this installation are swept.
        if admission is None:
            threading.Thread(target=sweep_stale_runs, name="PreflightRunSweep", daemon=True).start()
        else:
            from backend.engine.startup_thread_lifetime import OriginalStartupThread
            if startup_observation is None:
                sweep_owner = OriginalStartupThread('preflight_run_sweep',
                    lambda stop: sweep_stale_runs(stop_event=stop), name='PreflightRunSweep')
            else:
                sweep_owner = OriginalStartupThread('preflight_run_sweep',
                    lambda stop: sweep_stale_runs(startup_observation.sweep.root, stop_event=stop,
                        outcome_observer=startup_observation.sweep), name='PreflightRunSweep')
                startup_observation.bind_sweep(sweep_owner)
            admission.register_startup_join('job_ledger_maintenance',
                training_job_manager.capture_startup_maintenance_join())
            admission.register_startup_join('preflight_run_sweep',
                lambda deadline: sweep_owner.stop_and_join(deadline, expected_thread=sweep_owner.thread))
            sweep_owner.start()
            if startup_observation is not None: startup_observation.recovery_ready()
        logger.info("Vision AI Studio backend daemon initialized (v%s).", VERSION)
        if backend_bootstrap_ready() is not None:
            from backend.engine.application_launch_handshake import backend_execution_service
            execution_stop=threading.Event()
            execution_thread=threading.Thread(target=backend_execution_service,args=(execution_stop,),
                name='OwnedCPUKnownImage',daemon=True)
            execution_thread.start()
        yield
    finally:
        if startup_observation is not None:
            # Normal lifespan exit alone cannot settle the retained startup token.
            startup_observation.lifespan_ended()
        if sweep_owner is not None:sweep_owner.stop_event.set()
        if execution_stop is not None:execution_stop.set()
        if execution_thread is not None:execution_thread.join(timeout=1)
        logger.info("Initiating Vision AI Studio backend shutdown...")
        try:
            # Detach owned jobs for reattachment; do not terminate training.
            training_job_manager.detach_all_for_shutdown()
        finally:
            try:
                # A preflight is stopped through its owned handle, not a PID.
                stop_worker_preflight()
            finally:
                try:
                    await telemetry_shutdown()
                finally:
                    clear_device_cache()
        logger.info("Shutdown cleanup complete.")


def create_app(project_dir: Optional[str] = None, shared_auth_dir: Optional[str] = None) -> FastAPI:
    from backend.engine.global_store_paths import startup_admission,validate_startup_scopes
    with startup_admission():
        validate_startup_scopes(project_dir or ROOT_DIR/'projects',shared_auth_dir)
        return _admitted_create_app(project_dir,shared_auth_dir)


def _admitted_create_app(project_dir: Optional[str] = None, shared_auth_dir: Optional[str] = None) -> FastAPI:
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
    app.state.owned_work_admission = backend_work_admission()
    from backend.engine.shared_accounts import AccountStore
    app.state.accounts=AccountStore(Path(shared_auth_dir)/'accounts.sqlite') if shared_auth_dir else None
    from backend.engine.migration_guard import ProjectMaintenanceMiddleware
    app.add_middleware(ProjectMaintenanceMiddleware, project_app=app)
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

    # A health readback is data only and uses the existing process capability.
    health_admission = app.state.owned_work_admission
    health_observer = health_admission._activity_observer if health_admission is not None else None
    health_token = app.state.api_token
    # Health check endpoint strictly conforming to PROJECT.md line 144
    @app.get("/health")
    def health_check(request: Request):
        dev = get_device()
        info = get_device_info(dev)
        result = {
            "status": "ok",
            "version": VERSION,
            "device": info.device_type,  # "mps" | "cuda" | "cpu"
            "device_name": info.device_name,
            "is_accelerated": info.is_accelerated,
            "torch_version": info.torch_version,
        }
        from backend.engine.original_request_activity import OriginalRequestActivityObserver
        if (type(health_token) is str and health_token
                and app.state.api_token is health_token
                and app.state.owned_work_admission is health_admission
                and health_admission is not None and health_admission._activity_observer is health_observer
                and type(health_observer) is OriginalRequestActivityObserver
                and secrets.compare_digest(request.headers.get('x-vision-token', '').encode('utf-8'), health_token.encode('utf-8'))):
            diagnostic = health_observer.health_diagnostic(request.scope)
            if diagnostic is not None: result['owned_request_diagnostic'] = diagnostic
            if diagnostic is not None:
                try:
                    origin = health_observer.first_unsupported_origin_diagnostic()
                    if (origin is not None and origin['sequence'] == diagnostic['sequence']
                            and origin['request_count'] == diagnostic['request_count']):
                        result['owned_first_unsupported_origin'] = origin
                except Exception:
                    pass  # Only optional data; the original health objects remain unchanged.
            if diagnostic is not None:
                failures = health_observer.health_failure_diagnostic(request.scope)
                if (failures is not None
                        and failures['diagnostic_request_identity'] == diagnostic['diagnostic_request_identity']):
                    result['owned_request_failure_diagnostic'] = failures
                    try:
                        attribution = health_observer.health_ancillary_failure_attribution(request.scope)
                        if (attribution is not None
                                and attribution['diagnostic_request_identity'] == diagnostic['diagnostic_request_identity']
                                and [row['reason_sha256'] for row in attribution['reasons']] == failures['reason_sha256']):
                            result['owned_ancillary_failure_attribution'] = attribution
                    except Exception:
                        pass  # Only the optional sibling is omitted; original health remains unchanged.
        return result

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
    app.include_router(assistant_router)
    app.include_router(accounts_router)
    app.include_router(mask_exchange_router)
    app.include_router(class_rename_router)
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
    app.include_router(model_execution_router)
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

    app.add_middleware(OwnedWorkAdmissionMiddleware, admission=app.state.owned_work_admission)
    from backend.engine.original_current_project_request import bind_current_project_request
    bind_current_project_request(app, app.state.owned_work_admission)
    from backend.engine.original_desktop_auth_denial import bind_original_desktop_denial
    bind_original_desktop_denial(app, app.state.owned_work_admission)
    from backend.engine.original_shared_auth_denial import bind_original_shared_denial
    bind_original_shared_denial(app, app.state.owned_work_admission)
    from backend.engine.original_owned_account_request import bind_original_owned_account_request
    bind_original_owned_account_request(app, app.state.owned_work_admission)
    from backend.engine.original_owned_flow_request import bind_original_owned_flow_request
    bind_original_owned_flow_request(app, app.state.owned_work_admission)
    from backend.engine.original_owned_flow_execution_request import bind_original_owned_flow_execution_request
    bind_original_owned_flow_execution_request(app, app.state.owned_work_admission)
    from backend.engine.original_owned_UI_ancillary_request import bind_original_owned_UI_ancillary_request
    bind_original_owned_UI_ancillary_request(app, app.state.owned_work_admission)
    from backend.engine.original_owned_telemetry_socket_request import bind_original_owned_telemetry_socket_request
    bind_original_owned_telemetry_socket_request(app, app.state.owned_work_admission)
    if app.state.owned_work_admission is not None:
        from backend.engine.required_flow_passive_capture import bind_original_required_flow_selector
        selector = bind_original_required_flow_selector(app,backend_work_admission,SharedAuthorizationMiddleware if app.state.accounts is not None else DesktopApiAuthMiddleware,ROOT_DIR)
        from backend.engine.required_flow_http_capture import bind_original_flow_http_capture
        bind_original_flow_http_capture(selector)
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
        if app.state.owned_work_admission is None:
            threading.Thread(target=_stop_when_stdin_closes, args=(server,), name="StopOnStdinEOF", daemon=True).start()
        else:
            from backend.engine.owned_stdin_lifetime import OwnedStdinLifetime
            stdin_owner = OwnedStdinLifetime(0, lambda fd, observed:
                _stop_when_stdin_closes(server, stdin_fd=fd, on_natural_eof=observed))
            app.state.owned_stdin_lifetime = stdin_owner  # captured before its original Thread starts
            stdin_owner.start()

    try:
        server.run(sockets=[sock])
    finally:
        sock.close()


# The desktop supervisor keeps this process's stdin open and closes it to ask for a graceful stop (when the app itself
# exits, its exit hook stops this process outright instead). On Windows a stop signal from Electron terminates the
# process outright, so this is the stop that runs the shutdown sequence there; it is opt-in because a server started
# from a shell may have no stdin at all.
STOP_ON_STDIN_EOF = "VISION_AI_STUDIO_STOP_ON_STDIN_EOF"


def _stop_when_stdin_closes(server, *, stdin_fd=0, on_natural_eof=None):
    read_error = None; eof_error = None
    try:
        # The raw descriptor, not sys.stdin: a daemon thread blocked in a buffered read holds a lock that interpreter
        # shutdown would then wait for.
        while os.read(stdin_fd, 65536):
            pass
        if on_natural_eof is not None:
            try:on_natural_eof()
            except BaseException as error:eof_error = error
    except OSError as error:
        read_error = error  # a broken pipe requests stop but is not genuine EOF evidence
    logger.info("The supervisor closed stdin; detaching owned training and triggering exit...")
    try:
        training_job_manager.detach_all_for_shutdown()
    finally:  # a failed detach (a locked database) never loses the stop request
        server.should_exit = True
    if on_natural_eof is not None:
        if read_error is not None:
            from backend.engine.owned_stdin_lifetime import OriginalStdinError
            raise OriginalStdinError('Original stdin reader failed before genuine EOF') from read_error
        if eof_error is not None:raise eof_error
        return {'natural_stdin_eof': True, 'detach_completed': True}


def main():
    """Run the original CLI dispatch through the canonical module instance."""
    if len(sys.argv)>1 and sys.argv[1]=='--managed-service-project':
        if len(sys.argv)!=3:raise SystemExit('--managed-service-project requires exactly one project directory')
        from backend.engine.service_bootstrap import main as service_bootstrap_main
        service_bootstrap_main(sys.argv[2])
    elif len(sys.argv)>1 and sys.argv[1]=='--inspection-service':
        del sys.argv[1]
        from backend.engine.inspection_service import main as inspection_service_main
        raise SystemExit(inspection_service_main())
    else:run_server()


if __name__ == "__main__":
    main()
