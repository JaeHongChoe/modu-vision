"""
backend/api/routes_training.py

Thread-safe AutoML Training Job Manager & REST API Endpoints.
Coordinates background worker threads, hooks WebSocket telemetry,
and handles clean aborts with GPU/MPS memory clearing.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.api.websocket_telemetry import WebSocketTelemetryCallback, broadcaster
from backend.engine.device import clear_device_cache, get_device
from backend.engine.dataset_loaders import ClassificationDataset
from backend.engine.trainer import UnifiedAutoMLTrainer
from backend.engine.labelme_preparation import LabelMePreparationCancelled, prepare_labelme_segmentation
from backend.engine.dataset_fingerprint import fingerprint_dataset
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
    status: str  # "running" | "stopping" | "completed" | "aborted" | "failed"
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
    preparation_cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    source_dataset_path: Optional[str] = None
    dataset_fingerprint: Optional[str] = None


def _write_job_receipt(record: JobRecord) -> None:
    """Persist the terminal state so evaluation can resolve jobs after a restart."""
    output_dir = Path(record.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "job_id": record.job_id,
        "status": record.status,
        "task": record.task,
        "dataset_path": record.dataset_path,
        "output_dir": record.output_dir,
    }
    if record.source_dataset_path and record.dataset_fingerprint:
        receipt["source_dataset_path"] = record.source_dataset_path
        receipt["dataset_fingerprint"] = record.dataset_fingerprint
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_dir,
            prefix=".job_receipt-", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(receipt, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output_dir / "job_receipt.json")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


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
            return rec is not None and rec.status in ("running", "stopping")

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
        prepare_dataset: Optional[Callable[[threading.Event], Any]] = None,
        source_dataset_path: Optional[str] = None,
        dataset_fingerprint: Optional[str] = None,
    ) -> JobRecord:
        with self._lock:
            active_record = self._jobs.get(self._active_job_id) if self._active_job_id else None
            if active_record is not None and active_record.status in ("running", "stopping"):
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
                source_dataset_path=source_dataset_path,
                dataset_fingerprint=dataset_fingerprint,
            )
            self._jobs[job_id] = record
            self._active_job_id = job_id

            def _worker():
                result = None
                error = None
                try:
                    logger.info("Background training thread started for job %s", job_id)
                    if record.preparation_cancel.is_set():
                        raise LabelMePreparationCancelled("Training preparation cancelled by user request")
                    if prepare_dataset is not None:
                        prepare_dataset(record.preparation_cancel)
                    if record.preparation_cancel.is_set():
                        raise LabelMePreparationCancelled("Training preparation cancelled by user request")
                    result = trainer.train(job_id=job_id)
                except LabelMePreparationCancelled:
                    result = {"status": "aborted"}
                    cb.on_training_aborted(0, "Training preparation cancelled by user request")
                except Exception as ex:
                    logger.exception("Training job %s failed: %s", job_id, ex)
                    err_card = classify_exception(ex, details=str(ex))
                    error = err_card.to_ws_payload()
                    # Also notify via WebSocket
                    cb.on_error(ex, stage="training_loop")
                finally:
                    try:
                        clear_device_cache()
                    finally:
                        with self._lock:
                            if error is not None:
                                record.status = "failed"
                                record.error = error
                            elif record.status == "stopping":
                                record.status = "aborted"
                                record.result = {"status": "aborted"}
                            elif result is not None:
                                record.status = result.get("status", "completed")
                                record.result = result
                                record.best_metric = result.get("best_metric")
                            if self._active_job_id == job_id:
                                self._active_job_id = None
                        try:
                            _write_job_receipt(record)
                        except OSError:
                            logger.exception("Could not persist terminal receipt for job %s", job_id)
                    logger.info("Background training thread finished for job %s", job_id)

            t = threading.Thread(target=_worker, name=f"Trainer-{job_id}", daemon=True)
            record.thread = t
            t.start()
            return record

    def abort_job(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.trainer is None or record.status not in ("running", "stopping"):
                return False
            record.status = "stopping"
            record.preparation_cancel.set()
            record.trainer.abort()

        logger.info("Aborting job %s...", job_id)
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
    if not d_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail=format_error_response("ERR_NO_DATA", details=f"Dataset folder not found: {req.dataset_path}"),
        )

    from backend.api.routes_dataset import (
        DETECTION_SPLIT_LAYOUT_MESSAGE, STUDIO_ANNOTATIONS_DIR,
        _detection_train_val_ready, _paired_labelme_images, _read_split_manifest,
        _resolve_task_folder, _split_manifest_file,
    )
    effective_dataset_path = _resolve_task_folder(d_path, req.task)

    if req.task == "detection" and not _detection_train_val_ready(effective_dataset_path):
        raise HTTPException(status_code=422, detail=DETECTION_SPLIT_LAYOUT_MESSAGE)

    paired_images = _paired_labelme_images(d_path)
    local_labelme = bool(paired_images)
    if local_labelme and req.task != "segmentation":
        raise HTTPException(
            status_code=422,
            detail="Flat LabelMe folders currently support segmentation training only; other tasks need task-specific OK/NG data.",
        )

    if req.task == "classification":
        try:
            train_count = len(ClassificationDataset(effective_dataset_path, split="train"))
            val_count = len(ClassificationDataset(effective_dataset_path, split="val"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"분류 데이터 분할을 다시 확인하세요. / Check classification split: {exc}") from exc
        if train_count == 0 or val_count == 0:
            raise HTTPException(
                status_code=422,
                detail="분류 학습에는 서로 분리된 train 및 val 이미지가 필요합니다. test는 val 대신 사용하지 않습니다. / Classification training requires separate train and val images; test cannot replace val.",
            )

    has_split_manifest = local_labelme and _split_manifest_file(d_path).is_file()
    assignments = _read_split_manifest(d_path) if has_split_manifest else {}
    if has_split_manifest:
        # LabelMe preparation resolves image symlinks before applying assignments.
        # Keep the gallery's lexical paths, but canonicalize this training copy so
        # a read-only linked subset uses the split chosen in Step 1.
        resolved_assignments = {str(Path(path).resolve()): partition for path, partition in assignments.items()}
        if len(resolved_assignments) != len(assignments):
            raise HTTPException(status_code=422, detail="Saved split contains multiple links to the same image; use unique source images.")
        assignments = resolved_assignments
        missing = sorted(str(image) for image in paired_images if str(image) not in assignments)
        invalid = sorted(str(image) for image in paired_images
                         if str(image) in assignments and assignments[str(image)] not in {"train", "val", "test"})
        if missing or invalid:
            raise HTTPException(
                status_code=422,
                detail=("Saved split is incomplete for the current labels. Apply the train/validation split again "
                        f"before training (missing={len(missing)}, invalid={len(invalid)})."),
            )

    try:
        source_fingerprint = fingerprint_dataset(
            d_path, studio_root=STUDIO_ANNOTATIONS_DIR, split_manifest=_split_manifest_file(d_path),
        )
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Could not fingerprint training data: {exc}") from exc

    out_dir = Path(req.output_dir or "./models").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    job_id = f"job_{int(time.time())}_{str(uuid.uuid4())[:6]}"
    job_dir = out_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    dataset_for_training = effective_dataset_path
    prepare_dataset = None

    if local_labelme:
        dataset_for_training = job_dir / "dataset"
        image_size = int((req.config_overrides or {}).get("image_size", 256))

        def prepare_dataset(cancel_event: threading.Event) -> None:
            prepare_labelme_segmentation(
                d_path, dataset_for_training,
                image_size=image_size,
                assignments=assignments,
                require_complete_assignments=has_split_manifest,
                cancellation_requested=cancel_event.is_set,
            )

    record = training_job_manager.start_job(
        job_id=job_id,
        task=req.task,
        dataset_path=str(dataset_for_training),
        output_dir=str(job_dir),
        preset=req.preset,
        device=req.device,
        config_overrides=req.config_overrides,
        prepare_dataset=prepare_dataset,
        source_dataset_path=str(d_path),
        dataset_fingerprint=source_fingerprint,
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
        "is_training": record.status in ("running", "stopping"),
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
