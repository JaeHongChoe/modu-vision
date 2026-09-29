"""
backend/api/__init__.py

FastAPI Routers and WebSocket Services for Vision AI Studio.
"""

from backend.api.routes_annotation import router as annotation_router
from backend.api.routes_dataset import router as dataset_router
from backend.api.routes_evaluation import router as evaluation_router
from backend.api.routes_project import router as project_router
from backend.api.routes_report import router as report_router
from backend.api.routes_training import router as training_router, training_job_manager
from backend.api.websocket_telemetry import (
    WebSocketTelemetryCallback,
    WebSocketTrainingCallback,
    broadcaster,
    router as telemetry_router,
)

__all__ = [
    "annotation_router",
    "dataset_router",
    "evaluation_router",
    "project_router",
    "report_router",
    "training_router",
    "training_job_manager",
    "telemetry_router",
    "broadcaster",
    "WebSocketTelemetryCallback",
    "WebSocketTrainingCallback",
]
