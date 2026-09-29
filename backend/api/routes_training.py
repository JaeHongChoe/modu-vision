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
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.websocket_telemetry import WebSocketTelemetryCallback, broadcaster
from backend.engine.device import clear_device_cache, get_device
from backend.engine.dataset_loaders import ClassificationDataset, scoped_split_root, split_root_scope
from backend.engine.trainer import UnifiedAutoMLTrainer
from backend.engine.labelme_preparation import LabelMePreparationCancelled, prepare_labelme_segmentation
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.patch_classification import load_patch_manifest
from backend.engine.warm_start import WarmStartParent, architecture_for, resolve_warm_start_parent
from backend.remote.profiles import ComputeProfile
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
    remote_profile_id: Optional[str] = None
    phase: Optional[str] = None
    transferred_bytes: int = 0
    total_bytes: int = 0
    remote_device_name: Optional[str] = None
    remote_runner: Optional[Callable[[JobRecord], Dict[str, Any]]] = field(default=None, repr=False)
    remote_profile: Optional[ComputeProfile] = field(default=None, repr=False)
    launch_spec: Optional[Dict[str, Any]] = field(default=None, repr=False)
    split_manifest_root: Optional[str] = None
    warm_start: Optional[WarmStartParent] = None


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
    if record.remote_profile_id:
        receipt["compute_profile_id"] = record.remote_profile_id
    if record.warm_start is not None:
        receipt["warm_start"] = record.warm_start.lineage()
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
        if record.remote_profile_id:
            from backend.remote.coordinator import mark_remote_journal_terminal

            mark_remote_journal_terminal(output_dir, record.job_id, record.status)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class TrainingJobManager:
    """Singleton coordinator for background PyTorch training jobs."""

    ACTIVE_STATES = ("running", "stopping", "disconnected")

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, JobRecord] = {}
        self._active_job_id: Optional[str] = None
        self._remote_queue: List[str] = []

    @staticmethod
    def _profiles_conflict(first: ComputeProfile, second: ComputeProfile) -> bool:
        """Reserve the physical SSH host, allowing disjoint explicit GPUs."""
        first_host = first.ssh_target.rsplit("@", 1)[-1].lower()
        second_host = second.ssh_target.rsplit("@", 1)[-1].lower()
        if (first_host, first.ssh_port) != (second_host, second.ssh_port):
            return False
        if first.gpu_selector in (None, "all") or second.gpu_selector in (None, "all"):
            return True
        first_devices = first.gpu_selector.split(",")
        second_devices = second.gpu_selector.split(",")
        first_numeric = all(re.fullmatch(r"[0-9]+", device) for device in first_devices)
        second_numeric = all(re.fullmatch(r"[0-9]+", device) for device in second_devices)
        first_uuids = all(re.fullmatch(r"GPU-[0-9a-fA-F-]+", device) for device in first_devices)
        second_uuids = all(re.fullmatch(r"GPU-[0-9a-fA-F-]+", device) for device in second_devices)
        if not ((first_numeric and second_numeric) or (first_uuids and second_uuids)):
            # Ranges, MIG selectors, and mixed UUID/index forms have ambiguous
            # overlap. Reserve the host until the run reaches a terminal state.
            return True
        return bool(set(first_devices) & set(second_devices))

    def _remote_slot_busy(self, profile: ComputeProfile) -> bool:
        for record in self._jobs.values():
            if record.status not in self.ACTIVE_STATES or record.remote_profile_id is None:
                continue
            if record.remote_profile is None or self._profiles_conflict(profile, record.remote_profile):
                return True
        return False

    def _refresh_active_id(self) -> None:
        active = self._jobs.get(self._active_job_id) if self._active_job_id else None
        if active is not None and active.status in self.ACTIVE_STATES:
            return
        self._active_job_id = next(
            (job_id for job_id, record in self._jobs.items() if record.status in self.ACTIVE_STATES),
            None,
        )

    def list_jobs(self) -> List[JobRecord]:
        with self._lock:
            return list(self._jobs.values())

    def restore_terminal_job(self, record: JobRecord) -> None:
        if record.status not in ("completed", "aborted", "failed"):
            raise ValueError("Only terminal jobs can be restored without a worker")
        with self._lock:
            self._jobs.setdefault(record.job_id, record)

    @property
    def is_training(self) -> bool:
        with self._lock:
            return any(record.status in self.ACTIVE_STATES for record in self._jobs.values())

    def get_active_job(self) -> Optional[JobRecord]:
        with self._lock:
            self._refresh_active_id()
            if self._active_job_id:
                return self._jobs.get(self._active_job_id)
            if self._remote_queue:
                return self._jobs.get(self._remote_queue[0])
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
        split_manifest_root: Optional[str] = None,
        warm_start: Optional[WarmStartParent] = None,
    ) -> JobRecord:
        with self._lock:
            active_record = next(
                (record for record in self._jobs.values()
                 if record.remote_profile_id is None and record.status in self.ACTIVE_STATES), None,
            )
            if active_record is not None:
                active = active_record.job_id
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
                    warm_start=warm_start,
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
                warm_start=warm_start,
            )
            self._jobs[job_id] = record
            self._active_job_id = job_id

            def _worker():
                result = None
                error = None
                try:
                    logger.info("Background training thread started for job %s", job_id)
                    with split_root_scope(split_manifest_root):
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
                            self._refresh_active_id()
                        try:
                            _write_job_receipt(record)
                        except OSError:
                            logger.exception("Could not persist terminal receipt for job %s", job_id)
                    logger.info("Background training thread finished for job %s", job_id)

            t = threading.Thread(target=_worker, name=f"Trainer-{job_id}", daemon=True)
            record.thread = t
            t.start()
            return record

    def start_remote_job(
        self,
        job_id: str,
        task: str,
        dataset_path: str,
        output_dir: str,
        remote_profile_id: str,
        remote_runner: Callable[[JobRecord], Dict[str, Any]],
        preset: str = "fast",
        source_dataset_path: Optional[str] = None,
        dataset_fingerprint: Optional[str] = None,
        split_manifest_root: Optional[str] = None,
        profile: Optional[ComputeProfile] = None,
        launch_spec: Optional[Dict[str, Any]] = None,
        recovery_state: Optional[str] = None,
    ) -> JobRecord:
        """Track one detached remote run through the existing training contract.

        The runner returns only after a terminal remote receipt and all local
        artifacts have been verified and copied. A lost connection returns
        ``disconnected`` so the run remains reserved for later reconciliation.
        """
        with self._lock:
            if job_id in self._jobs:
                raise HTTPException(status_code=409, detail=f"Training job {job_id} already exists")
            if profile is None:
                active = next((rec for rec in self._jobs.values() if rec.status in self.ACTIVE_STATES), None)
                if active is not None:
                    raise HTTPException(status_code=409, detail=f"Another training job ({active.job_id}) is currently in progress.")
            else:
                if profile.id != remote_profile_id:
                    raise ValueError("Remote profile identity does not match the training job")
                if launch_spec is None and recovery_state is None:
                    raise ValueError("A durable launch specification is required for queued remote jobs")
            queued = profile is not None and recovery_state not in ("launching", "launched", "artifacts_verified", "completed") and self._remote_slot_busy(profile)
            record = JobRecord(
                job_id=job_id, task=task, preset=preset, dataset_path=dataset_path,
                output_dir=output_dir, status="queued" if queued else "running", remote_profile_id=remote_profile_id,
                phase="queued" if queued else ("reconnecting" if recovery_state else "preparing"), source_dataset_path=source_dataset_path,
                dataset_fingerprint=dataset_fingerprint,
                remote_runner=remote_runner, remote_profile=profile, launch_spec=launch_spec,
                split_manifest_root=split_manifest_root,
            )
            if profile is not None and recovery_state is None:
                from backend.remote.coordinator import persist_queued_remote_job

                persist_queued_remote_job(record, profile, launch_spec)
            self._jobs[job_id] = record
            if queued:
                self._remote_queue.append(job_id)
            else:
                if self._active_job_id is None:
                    self._active_job_id = job_id
                self._launch_remote_worker_locked(record)
            return record

    def _launch_remote_worker_locked(self, record: JobRecord) -> None:
        job_id = record.job_id

        def _worker() -> None:
            try:
                with split_root_scope(record.split_manifest_root):
                    result = record.remote_runner(record)
                state = result.get("status", "failed")
                if state not in ("completed", "aborted", "failed", "disconnected"):
                    raise ValueError(f"Unexpected remote job status: {state}")
                with self._lock:
                    record.status = state
                    record.result = result
                    record.best_metric = result.get("best_metric")
                    if state == "failed" and result.get("error"):
                        record.error = {"message": str(result["error"])}
            except Exception as exc:
                logger.exception("Remote training job %s failed: %s", job_id, exc)
                with self._lock:
                    record.status = "disconnected" if record.phase in ("running", "reconnecting", "disconnected") else "failed"
                    record.error = {"message": str(exc)}
            if record.status != "disconnected":
                try:
                    _write_job_receipt(record)
                except OSError:
                    logger.exception("Could not persist terminal receipt for remote job %s", job_id)
                with self._lock:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                    self._start_waiting_remote_jobs_locked()
                    self._refresh_active_id()

        thread = threading.Thread(target=_worker, name=f"RemoteTrainer-{job_id}", daemon=True)
        record.thread = thread
        thread.start()

    def _start_waiting_remote_jobs_locked(self) -> None:
        for job_id in list(self._remote_queue):
            record = self._jobs.get(job_id)
            if record is None:
                self._remote_queue.remove(job_id)
                continue
            if record.status != "queued":
                self._remote_queue.remove(job_id)
                continue
            if record.remote_profile is not None and self._remote_slot_busy(record.remote_profile):
                continue
            self._remote_queue.remove(job_id)
            record.status = "running"
            record.phase = "preparing"
            if self._active_job_id is None:
                self._active_job_id = job_id
            self._launch_remote_worker_locked(record)

    def reconnect_remote_job(self, job_id: str) -> Optional[JobRecord]:
        """Recheck the same server/run after network loss without launching it again."""
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.remote_profile_id is None or record.remote_runner is None:
                return None
            if record.thread is not None and record.thread.is_alive():
                return record
            if record.status not in ("disconnected", "stopping"):
                return None
            was_stopping = record.preparation_cancel.is_set()
            record.status = "stopping" if was_stopping else "running"
            record.phase = "reconnecting"
            if self._active_job_id is None:
                self._active_job_id = job_id

        def _worker() -> None:
            try:
                result = record.remote_runner(record)
                state = result.get("status", "failed")
                if state not in ("completed", "aborted", "failed", "disconnected"):
                    raise ValueError(f"Unexpected remote job status: {state}")
                with self._lock:
                    record.status = state
                    record.result = result
                    record.best_metric = result.get("best_metric")
                    if state == "failed" and result.get("error"):
                        record.error = {"message": str(result["error"])}
            except Exception as exc:
                logger.exception("Could not reconnect remote job %s", job_id)
                with self._lock:
                    record.status = "disconnected"
                    record.error = {"message": str(exc)}
            if record.status != "disconnected":
                try:
                    _write_job_receipt(record)
                except OSError:
                    logger.exception("Could not persist terminal receipt for remote job %s", job_id)
                with self._lock:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                    self._start_waiting_remote_jobs_locked()
                    self._refresh_active_id()

        thread = threading.Thread(target=_worker, name=f"RemoteReconnect-{job_id}", daemon=True)
        record.thread = thread
        thread.start()
        return record

    def abort_job(self, job_id: str) -> bool:
        queued_record = None
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None and record.status == "queued":
                record.status = "aborted"
                record.phase = "aborted"
                record.result = {"status": "aborted"}
                self._remote_queue.remove(job_id)
                queued_record = record
            elif not record or record.status not in self.ACTIVE_STATES:
                return False
            else:
                record.status = "stopping"
                record.preparation_cancel.set()
                if record.trainer is not None:
                    record.trainer.abort()

        if queued_record is not None:
            _write_job_receipt(queued_record)
            return True

        logger.info("Aborting job %s...", job_id)
        return True

    def abort_all(self) -> None:
        with self._lock:
            local_ids = [record.job_id for record in self._jobs.values()
                         if record.remote_profile_id is None and record.status in self.ACTIVE_STATES]
        for job_id in local_ids:
            self.abort_job(job_id)


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
    task: Literal["classification", "patch_classification", "detection", "segmentation", "anomaly"] = "classification"
    preset: Literal["fast", "precision"] = "fast"
    dataset_path: str = Field(..., min_length=1)
    output_dir: Optional[str] = None
    config_overrides: Optional[Dict[str, Any]] = None
    device: Optional[str] = None
    compute_profile_id: Optional[str] = None
    warm_start_job_id: Optional[str] = None


class TrainingStopRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: Optional[str] = None


def _warm_start_scope(request: Request, dataset_path: Path) -> Path:
    """Pin retraining to the selected project's registered source and model store."""
    from backend.api.routes_project import get_current_project

    project = get_current_project(request)
    registered_source = project.get("source_dataset_dir")
    if (not registered_source or Path(registered_source).expanduser().resolve() != dataset_path):
        raise HTTPException(status_code=409, detail="Warm-start source must match the current project's dataset")
    models = Path(project["models_dir"])
    if models.is_symlink() or not models.is_dir() or models.resolve() != (Path(project["project_dir"]) / "models").resolve():
        raise HTTPException(status_code=422, detail="Current project models directory is invalid")
    return models


@router.get("/warm-start-parents")
def list_warm_start_parents(
    dataset_path: str, task: str, preset: str = "fast", request: Request = None,
    backbone: Optional[str] = None,
):
    if request is None:
        raise HTTPException(status_code=409, detail="Open a project before selecting a warm-start parent")
    source = Path(dataset_path).expanduser().resolve()
    models = _warm_start_scope(request, source)
    try:
        architecture = architecture_for(task, preset, {"backbone": backbone} if backbone else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    parents = []
    for job_dir in sorted(models.iterdir()):
        if job_dir.is_symlink() or not job_dir.is_dir():
            continue
        try:
            parent = resolve_warm_start_parent(job_dir.name, models, source, task, architecture)
        except (OSError, ValueError):
            continue
        parents.append({
            "job_id": parent.job_id,
            "classes": list(parent.classes),
            "architecture": parent.architecture,
            "checkpoint_sha256": parent.checkpoint_sha256,
            "dataset_fingerprint": parent.dataset_fingerprint,
        })
    return {"parents": parents, "total": len(parents)}


@router.post("/start")
def start_training(req: TrainingStartRequest, request: Request = None):
    """Initiates an asynchronous background AutoML training job."""
    # Verify dataset path exists
    d_path = Path(req.dataset_path).resolve()
    if not d_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail=format_error_response("ERR_NO_DATA", details=f"Dataset folder not found: {req.dataset_path}"),
        )

    if req.warm_start_job_id and req.compute_profile_id:
        raise HTTPException(status_code=422, detail="Remote warm start needs portable parent checkpoint transfer; select this computer")

    profile = None
    if req.compute_profile_id:
        from backend.remote.profiles import get_profile_store

        try:
            profile = get_profile_store().get(req.compute_profile_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Unknown compute server: {req.compute_profile_id}") from exc
        if profile is None:
            raise HTTPException(status_code=422, detail=f"Unknown compute server: {req.compute_profile_id}")

    from backend.api.routes_dataset import (
        DETECTION_SPLIT_LAYOUT_MESSAGE, SPLIT_MANIFEST_DIR, STUDIO_ANNOTATIONS_DIR,
        _detection_train_val_ready, _paired_labelme_images, _read_split_manifest,
        _resolve_task_folder, _split_manifest_file,
    )
    from backend.engine.annotation_storage import scoped_annotation_root
    annotation_root = scoped_annotation_root(STUDIO_ANNOTATIONS_DIR)
    split_manifest_root = scoped_split_root(SPLIT_MANIFEST_DIR)
    effective_dataset_path = _resolve_task_folder(d_path, req.task)

    paired_images = [] if req.task == "patch_classification" else _paired_labelme_images(d_path)
    local_labelme = bool(paired_images)

    if req.task == "patch_classification":
        if profile is not None:
            raise HTTPException(status_code=422, detail="Remote patch classification training is not supported yet")
        try:
            load_patch_manifest(effective_dataset_path)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid patch classification dataset: {exc}") from exc

    if req.task == "detection" and not local_labelme and not _detection_train_val_ready(effective_dataset_path):
        raise HTTPException(status_code=422, detail=DETECTION_SPLIT_LAYOUT_MESSAGE)
    if profile is not None and req.task == "detection" and not local_labelme:
        from backend.remote.detection_validation import validate_coco_detection_paths

        try:
            validate_coco_detection_paths(effective_dataset_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Remote detection dataset is not portable: {exc}") from exc

    if local_labelme and req.task not in ("segmentation", "detection"):
        raise HTTPException(
            status_code=422,
            detail="Flat LabelMe folders support segmentation and detection training. Classification or anomaly training needs task-specific OK/NG data.",
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
            d_path, studio_root=annotation_root, split_manifest=_split_manifest_file(d_path),
        )
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Could not fingerprint training data: {exc}") from exc

    if profile is not None:
        from backend.remote.ssh_transport import SSHTransport

        readiness = SSHTransport().probe(profile)
        if not readiness.get("ready"):
            raise HTTPException(
                status_code=422,
                detail=f"Compute server is not ready: {readiness.get('message') or 'connection test failed'}",
            )

    if req.output_dir:
        out_dir = Path(req.output_dir).resolve()
    elif request is not None:
        from backend.api.routes_project import get_current_project

        out_dir = Path(get_current_project(request)["models_dir"])
    else:
        # Direct Python calls used by backend tests retain their historical default.
        out_dir = Path("./models").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    warm_start = None
    if req.warm_start_job_id:
        if request is None:
            raise HTTPException(status_code=409, detail="Open a project before warm-start retraining")
        models = _warm_start_scope(request, d_path)
        if out_dir.resolve() != models.resolve():
            raise HTTPException(status_code=422, detail="Warm-start candidate must be stored in the current project")
        try:
            architecture = architecture_for(req.task, req.preset, req.config_overrides)
            warm_start = resolve_warm_start_parent(
                req.warm_start_job_id, models, d_path, req.task, architecture,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    job_id = f"job_{int(time.time())}_{str(uuid.uuid4())[:6]}"
    job_dir = out_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    dataset_for_training = effective_dataset_path
    prepare_dataset = None

    if local_labelme:
        dataset_for_training = job_dir / "dataset"
        image_size = int((req.config_overrides or {}).get("image_size", 256))

        def prepare_dataset(cancel_event: threading.Event) -> None:
            if req.task == "detection":
                from backend.engine.labelme_detection_preparation import prepare_labelme_detection
                prepare_labelme_detection(
                    d_path, dataset_for_training,
                    image_size=image_size,
                    assignments=assignments,
                    require_complete_assignments=has_split_manifest,
                    cancellation_requested=cancel_event.is_set,
                    annotation_root=annotation_root,
                )
            else:
                prepare_labelme_segmentation(
                    d_path, dataset_for_training,
                    image_size=image_size,
                    assignments=assignments,
                    require_complete_assignments=has_split_manifest,
                    cancellation_requested=cancel_event.is_set,
                    annotation_root=annotation_root,
                )
    elif profile is not None and req.task == "classification":
        dataset_for_training = job_dir / "dataset"

    if profile is not None:
        from backend.remote.coordinator import make_remote_runner

        preparation = "none"
        prepare_source_path = effective_dataset_path
        if local_labelme:
            preparation = "labelme_detection" if req.task == "detection" else "labelme_segmentation"
            prepare_source_path = d_path
        elif req.task == "classification":
            preparation = "remote_classification"
        launch_spec = {
            "preparation": preparation,
            "prepare_source_path": str(prepare_source_path),
            "image_size": int((req.config_overrides or {}).get("image_size", 256)),
            "assignments": assignments,
            "require_complete_assignments": has_split_manifest,
            "annotation_root": str(annotation_root),
            "config_overrides": req.config_overrides or {},
            "device": req.device,
        }

        record = training_job_manager.start_remote_job(
            job_id=job_id,
            task=req.task,
            dataset_path=str(dataset_for_training),
            output_dir=str(job_dir),
            remote_profile_id=profile.id,
            preset=req.preset,
            source_dataset_path=str(d_path),
            dataset_fingerprint=source_fingerprint,
            remote_runner=make_remote_runner(profile, launch_spec),
            split_manifest_root=str(split_manifest_root),
            profile=profile,
            launch_spec=launch_spec,
        )
    else:
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
            split_manifest_root=str(split_manifest_root),
            warm_start=warm_start,
        )

    return {
        "job_id": job_id,
        "status": "queued" if profile is not None and record.status == "queued" else "started",
        "preset": req.preset,
        "task": req.task,
        "output_dir": str(job_dir),
        "compute_profile_id": profile.id if profile is not None else None,
        "phase": record.phase if profile is not None else None,
        "warm_start_parent_job_id": warm_start.job_id if warm_start is not None else None,
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

    previous = training_job_manager.get_job(job_id)
    disconnected = previous is not None and previous.status == "disconnected"
    success = training_job_manager.abort_job(job_id)
    if success and disconnected:
        training_job_manager.reconnect_remote_job(job_id)
    return {
        "status": "stopping" if success else "not_running",
        "job_id": job_id,
    }


@router.post("/reconnect")
def reconnect_training(req: TrainingStopRequest):
    if not req.job_id:
        raise HTTPException(status_code=422, detail="job_id is required to reconnect a remote run")
    record = training_job_manager.reconnect_remote_job(req.job_id)
    if record is None:
        raise HTTPException(status_code=409, detail="This job cannot be reconnected")
    return {"job_id": record.job_id, "status": record.status, "compute_profile_id": record.remote_profile_id}


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

    duration = time.time() - record.start_time if record.status in training_job_manager.ACTIVE_STATES else 0.0

    return {
        "job_id": record.job_id,
        "status": record.status,
        "is_training": record.status in training_job_manager.ACTIVE_STATES,
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
        "compute_profile_id": record.remote_profile_id,
        "phase": record.phase,
        "transferred_bytes": record.transferred_bytes,
        "total_bytes": record.total_bytes,
        "device_name": record.remote_device_name,
    }


@router.get("/jobs")
def list_training_jobs():
    """Expose active and queued jobs so clients can reconnect by original ID."""
    queue_position = 0
    jobs = []
    for record in training_job_manager.list_jobs():
        position = None
        if record.status == "queued":
            queue_position += 1
            position = queue_position
        jobs.append({
            "job_id": record.job_id,
            "status": record.status,
            "phase": record.phase,
            "compute_profile_id": record.remote_profile_id,
            "task": record.task,
            "preset": record.preset,
            "queue_position": position,
            "output_dir": record.output_dir,
        })
    return {"jobs": jobs}
