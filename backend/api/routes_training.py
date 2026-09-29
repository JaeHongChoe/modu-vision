"""
backend/api/routes_training.py

Thread-safe AutoML Training Job Manager & REST API Endpoints.
Coordinates background worker threads, hooks WebSocket telemetry,
and handles clean aborts with GPU/MPS memory clearing.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.api.websocket_telemetry import WebSocketTelemetryCallback, broadcaster
from backend.engine.device import clear_device_cache, get_device
from backend.engine.trainer import UnifiedAutoMLTrainer
from backend.engine.industrial_adapters import is_valid_labelme_file
from backend.engine.labelme_preparation import prepare_labelme_segmentation
from backend.utils.error_catalog import classify_exception, format_error_response

logger = logging.getLogger("vision_ai_studio.routes_training")

router = APIRouter(prefix="/api/training", tags=["training"])


@dataclass
class JobRecord:
    job_id: str
    task: str
    preset: str
    dataset_path: str
    output_dir: str
    status: str  # "running" | "completed" | "aborted" | "failed"
    thread: Optional[threading.Thread] = None
    trainer: Optional[UnifiedAutoMLTrainer] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    start_time: float = field(default_factory=time.time)
    current_epoch: int = 0
    total_epochs: int = 0
    current_step: int = 0
    total_steps: int = 0
    train_loss: Optional[float] = None
    val_loss: Optional[float] = None
    best_metric: Optional[float] = None
    metrics: Dict[str, float] = field(default_factory=dict)


class TrainingJobManager:
    """Singleton coordinator for background PyTorch training jobs."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, JobRecord] = {}
        self._active_job_id: Optional[str] = None

    @property
    def is_training(self) -> bool:
        with self._lock:
            if self._active_job_id is None:
                return False
            rec = self._jobs.get(self._active_job_id)
            return rec is not None and rec.status == "running"

    def get_active_job(self) -> Optional[JobRecord]:
        with self._lock:
            if self._active_job_id:
                return self._jobs.get(self._active_job_id)
            return None

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def start_job(
        self,
        job_id: str,
        task: str,
        dataset_path: str,
        output_dir: str,
        preset: str = "fast",
        device: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> JobRecord:
        with self._lock:
            active_record = self._jobs.get(self._active_job_id) if self._active_job_id else None
            if active_record is not None and active_record.status == "running":
                active = self._active_job_id
                raise HTTPException(
                    status_code=409,
                    detail=f"Another training job ({active}) is currently in progress.",
                )

            # Build telemetry callback
            cb = WebSocketTelemetryCallback(job_id=job_id, max_hz=30.0)

            # Store callback updates into JobRecord for HTTP polling fallbacks
            orig_step_end = cb.on_step_end
            orig_epoch_end = cb.on_epoch_end

            def _step_end_wrapper(step, total_steps, current_loss, epoch):
                orig_step_end(step, total_steps, current_loss, epoch)
                with self._lock:
                    rec = self._jobs.get(job_id)
                    if rec:
                        rec.current_step = step + 1
                        rec.total_steps = total_steps
                        rec.train_loss = float(current_loss)
                        rec.current_epoch = epoch + 1

            def _epoch_end_wrapper(epoch, total_epochs, train_loss, val_loss, lr, metrics):
                orig_epoch_end(epoch, total_epochs, train_loss, val_loss, lr, metrics)
                with self._lock:
                    rec = self._jobs.get(job_id)
                    if rec:
                        rec.current_epoch = epoch + 1
                        rec.total_epochs = total_epochs
                        rec.train_loss = float(train_loss)
                        rec.val_loss = float(val_loss)
                        rec.metrics = metrics

            cb.on_step_end = _step_end_wrapper
            cb.on_epoch_end = _epoch_end_wrapper

            try:
                trainer = UnifiedAutoMLTrainer(
                    task=task,
                    dataset_path=dataset_path,
                    output_dir=output_dir,
                    preset=preset,
                    device=device,
                    callback=cb,
                    config_overrides=config_overrides,
                )
            except Exception as e:
                err_card = classify_exception(e, details=str(e))
                raise HTTPException(
                    status_code=422,
                    detail=format_error_response(err_card.code, details=str(e)),
                )

            record = JobRecord(
                job_id=job_id,
                task=task,
                preset=preset,
                dataset_path=dataset_path,
                output_dir=output_dir,
                status="running",
                trainer=trainer,
            )
            self._jobs[job_id] = record
            self._active_job_id = job_id

            def _worker():
                try:
                    logger.info("Background training thread started for job %s", job_id)
                    res = trainer.train(job_id=job_id)
                    with self._lock:
                        record.status = res.get("status", "completed")
                        record.result = res
                        record.best_metric = res.get("best_metric")
                except Exception as ex:
                    logger.exception("Training job %s failed: %s", job_id, ex)
                    err_card = classify_exception(ex, details=str(ex))
                    with self._lock:
                        record.status = "failed"
                        record.error = err_card.to_ws_payload()
                    # Also notify via WebSocket
                    cb.on_error(ex, stage="training_loop")
                finally:
                    clear_device_cache()
                    with self._lock:
                        if self._active_job_id == job_id:
                            self._active_job_id = None
                    logger.info("Background training thread finished for job %s", job_id)

            t = threading.Thread(target=_worker, name=f"Trainer-{job_id}", daemon=True)
            record.thread = t
            t.start()
            return record

    def abort_job(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.trainer is None:
                return False
            trainer = record.trainer
            thread = record.thread

        logger.info("Aborting job %s...", job_id)
        trainer.abort()
        if thread and thread.is_alive():
            thread.join(timeout=4.0)

        clear_device_cache()

        with self._lock:
            record.status = "aborted"
            if self._active_job_id == job_id:
                self._active_job_id = None
        return True

    def abort_all(self) -> None:
        with self._lock:
            active_id = self._active_job_id
        if active_id:
            self.abort_job(active_id)


# Global singleton instance
training_job_manager = TrainingJobManager()


class TrainingConfigOverrides(BaseModel):
    model_config = ConfigDict(extra="ignore")
    epochs: Optional[int] = Field(None, ge=1, le=500)
    batch_size: Optional[int] = Field(None, ge=1, le=128)
    learning_rate: Optional[float] = Field(None, gt=0.0, le=1.0)
    image_size: Optional[int] = Field(None, ge=64, le=1024)
    patience: Optional[int] = Field(None, ge=1, le=50)
    device: Optional[str] = None


class TrainingStartRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    task: Literal["classification", "detection", "segmentation", "anomaly"] = "classification"
    preset: Literal["fast", "precision"] = "fast"
    dataset_path: str = Field(..., min_length=1)
    output_dir: Optional[str] = "./models"
    config_overrides: Optional[Dict[str, Any]] = None
    device: Optional[str] = None


class TrainingStopRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: Optional[str] = None


@router.post("/start")
def start_training(req: TrainingStartRequest):
    """Initiates an asynchronous background AutoML training job."""
    # Verify dataset path exists
    d_path = Path(req.dataset_path).resolve()
    if not d_path.exists():
        raise HTTPException(
            status_code=400,
            detail=format_error_response("ERR_NO_DATA", details=f"Dataset path not found: {req.dataset_path}"),
        )

    local_labelme = any(is_valid_labelme_file(path, require_image=True) for path in d_path.glob("*.json"))
    if local_labelme and req.task != "segmentation":
        raise HTTPException(
            status_code=422,
            detail="Flat LabelMe folders currently support segmentation training only; other tasks need task-specific OK/NG data.",
        )

    out_dir = Path(req.output_dir or "./models").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    job_id = f"job_{int(time.time())}_{str(uuid.uuid4())[:6]}"
    job_dir = out_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    dataset_for_training = d_path

    if local_labelme:
        from backend.api.routes_dataset import _read_split_manifest

        dataset_for_training = job_dir / "dataset"
        try:
            prepare_labelme_segmentation(
                d_path, dataset_for_training,
                image_size=int((req.config_overrides or {}).get("image_size", 256)),
                assignments=_read_split_manifest(d_path),
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Could not prepare LabelMe training data: {exc}") from exc

    record = training_job_manager.start_job(
        job_id=job_id,
        task=req.task,
        dataset_path=str(dataset_for_training),
        output_dir=str(job_dir),
        preset=req.preset,
        device=req.device,
        config_overrides=req.config_overrides,
    )

    return {
        "job_id": job_id,
        "status": "started",
        "preset": req.preset,
        "task": req.task,
        "output_dir": str(job_dir),
    }


@router.post("/stop")
def stop_training(req: TrainingStopRequest):
    """Aborts the specified or currently active training job."""
    job_id = req.job_id or (
        training_job_manager.get_active_job().job_id
        if training_job_manager.get_active_job()
        else None
    )

    if not job_id:
        return {"status": "not_running", "job_id": None}

    success = training_job_manager.abort_job(job_id)
    return {
        "status": "stopping" if success else "not_running",
        "job_id": job_id,
    }


@router.get("/status")
def get_training_status(job_id: Optional[str] = Query(None)):
    """Retrieves current training status and progress for polling fallbacks."""
    if job_id:
        record = training_job_manager.get_job(job_id)
    else:
        record = training_job_manager.get_active_job()

    if not record:
        return {
            "job_id": None,
            "status": "idle",
            "is_training": False,
        }

    duration = time.time() - record.start_time if record.status == "running" else 0.0

    return {
        "job_id": record.job_id,
        "status": record.status,
        "is_training": record.status == "running",
        "task": record.task,
        "preset": record.preset,
        "current_epoch": record.current_epoch,
        "total_epochs": record.total_epochs,
        "current_step": record.current_step,
        "total_steps": record.total_steps,
        "current_train_loss": record.train_loss,
        "current_val_loss": record.val_loss,
        "best_metric": record.best_metric,
        "metrics": record.metrics,
        "duration_seconds": round(duration, 2),
        "result": record.result,
        "error": record.error,
    }
