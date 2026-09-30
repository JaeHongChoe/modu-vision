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
    loss_history: List[Dict[str, Any]] = field(default_factory=list)
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
    dataset_binding: Optional[Dict[str, Any]] = None


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
        "current_epoch": record.current_epoch,
        "total_epochs": record.total_epochs,
        "current_step": record.current_step,
        "total_steps": record.total_steps,
        "current_train_loss": record.train_loss,
        "current_val_loss": record.val_loss,
        "best_metric": record.best_metric,
        "metrics": record.metrics,
        "loss_history": record.loss_history,
        "error": record.error,
    }
    if record.source_dataset_path and record.dataset_fingerprint:
        receipt["source_dataset_path"] = record.source_dataset_path
        receipt["dataset_fingerprint"] = record.dataset_fingerprint
    if record.dataset_binding:
        receipt["training_provenance"] = record.dataset_binding
        from backend.engine.training_provenance import persist_model_binding
        if record.status == "completed" and not record.remote_profile_id:
            persist_model_binding(output_dir, record.dataset_binding)
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
        from backend.engine.shared_scheduler import shared_leases
        self._leases = shared_leases()
        self._queue_watcher = None

    def _lease_host(self, profile):
        return f"ssh:{profile.ssh_target.rsplit('@', 1)[-1].lower()}:{profile.ssh_port}"

    def _watch_queue(self):
        if self._queue_watcher is not None and self._queue_watcher.is_alive(): return
        def watch():
            while True:
                time.sleep(.5)
                with self._lock:
                    if not self._remote_queue: return
                    self._start_waiting_remote_jobs_locked()
        self._queue_watcher = threading.Thread(target=watch,daemon=True,name="SharedComputeQueue")
        self._queue_watcher.start()

    def _heartbeat(self, record):
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(5): self._leases.heartbeat(record.job_id)
        threading.Thread(target=heartbeat,daemon=True,name=f"Lease-{record.job_id}").start()
        return stop

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
        for lease in self._leases.list():
            if lease["host"] == self._lease_host(profile) and self._leases.conflict(lease["selector"], profile.gpu_selector or "all"):
                return True
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
        dataset_binding: Optional[Dict[str, Any]] = None,
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

            if not self._leases.acquire(job_id, "local-compute", "all"):
                raise HTTPException(409, "Another application process holds the local compute lease")
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
                        rec.loss_history.append({"epoch": epoch + 1, "train_loss": float(train_loss),
                                                 "val_loss": float(val_loss), "lr": float(lr)})

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
                self._leases.release(job_id)
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
                dataset_binding=dataset_binding,
            )
            self._jobs[job_id] = record
            self._active_job_id = job_id

            def _worker():
                heartbeat = self._heartbeat(record)
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
                        from backend.engine.training_provenance import validate_training_binding
                        validate_training_binding(record.dataset_binding)
                        result = trainer.train(job_id=job_id)
                        validate_training_binding(record.dataset_binding)
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
                        except Exception as persistence_error:
                            logger.exception("Could not persist terminal receipt for job %s", job_id)
                            record.status = "failed"
                            record.error = {"message": f"Training provenance persistence failed: {persistence_error}"}
                            _write_job_receipt(record)
                    heartbeat.set()
                    self._leases.release(job_id, terminal=True)
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
        warm_start: Optional[WarmStartParent] = None,
        dataset_binding: Optional[Dict[str, Any]] = None,
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
                warm_start=warm_start, dataset_binding=dataset_binding,
            )
            if profile is not None and recovery_state is None:
                from backend.remote.coordinator import persist_queued_remote_job

                persist_queued_remote_job(record, profile, launch_spec)
            self._jobs[job_id] = record
            if queued:
                self._remote_queue.append(job_id)
                self._watch_queue()
            else:
                if self._active_job_id is None:
                    self._active_job_id = job_id
                self._launch_remote_worker_locked(record)
            return record

    def _launch_remote_worker_locked(self, record: JobRecord) -> None:
        job_id = record.job_id
        if record.remote_profile is not None:
            self._leases.adopt(job_id)
            if not self._leases.acquire(job_id, self._lease_host(record.remote_profile), record.remote_profile.gpu_selector or "all", remote=True):
                record.status = "queued"; record.phase = "resource_reserved"
                if job_id not in self._remote_queue: self._remote_queue.append(job_id)
                self._watch_queue()
                return

        def _worker() -> None:
            heartbeat = self._heartbeat(record)
            try:
                with split_root_scope(record.split_manifest_root):
                    result = record.remote_runner(record)
                state = result.get("status", "failed")
                if state not in ("completed", "aborted", "failed", "disconnected"):
                    raise ValueError(f"Unexpected remote job status: {state}")
                with self._lock:
                    record.status = state
                    record.phase = state
                    record.result = result
                    record.best_metric = result.get("best_metric")
                    if state == "failed" and result.get("error"):
                        record.error = {"message": str(result["error"])}
            except Exception as exc:
                logger.exception("Remote training job %s failed: %s", job_id, exc)
                with self._lock:
                    record.status = "disconnected" if record.phase in ("running", "reconnecting", "disconnected") else "failed"
                    record.error = {"message": str(exc)}
            heartbeat.set()
            if record.status == "disconnected": self._leases.mark_uncertain(job_id)
            else: self._leases.release(job_id, terminal=True)
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
            heartbeat = self._heartbeat(record)
            try:
                result = record.remote_runner(record)
                state = result.get("status", "failed")
                if state not in ("completed", "aborted", "failed", "disconnected"):
                    raise ValueError(f"Unexpected remote job status: {state}")
                with self._lock:
                    record.status = state
                    record.phase = state
                    record.result = result
                    record.best_metric = result.get("best_metric")
                    if state == "failed" and result.get("error"):
                        record.error = {"message": str(result["error"])}
            except Exception as exc:
                logger.exception("Could not reconnect remote job %s", job_id)
                with self._lock:
                    record.status = "disconnected"
                    record.error = {"message": str(exc)}
            heartbeat.set()
            if record.status == "disconnected": self._leases.mark_uncertain(job_id)
            else: self._leases.release(job_id, terminal=True)
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
    dataset_version_id: Optional[str] = None


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

    if request is not None:
        from backend.api.routes_project import get_current_project
        configured_source = get_current_project(request).get("source_dataset_dir")
        if configured_source and Path(configured_source).expanduser().resolve() != d_path:
            raise HTTPException(409, "Training source must match the current project's dataset")

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
        saved_assignments = _read_split_manifest(d_path)
        if not {"train", "val"}.issubset(set(saved_assignments.values())):
            raise HTTPException(status_code=422, detail=DETECTION_SPLIT_LAYOUT_MESSAGE)
        from backend.engine.grouped_dataset_views import load_manifest_dataset
        try:
            if any(len(load_manifest_dataset("detection", d_path, split)) == 0 for split in ("train", "val")):
                raise ValueError("Saved detection manifest needs nonempty train and val partitions")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
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
    dataset_binding = None
    if request is not None:
        from backend.api.routes_project import get_current_project, update_project, ProjectUpdateRequest
        from backend.engine.training_provenance import bind_training_version
        project = get_current_project(request)
        if not project.get("source_dataset_dir"):
            project = update_project(ProjectUpdateRequest(source_dataset_dir=str(d_path)), request)
        dataset_binding = bind_training_version(project, d_path, req.dataset_version_id)
        if dataset_binding["dataset_fingerprint"] != source_fingerprint:
            raise HTTPException(409, "Training version fingerprint differs from selected source")
    job_id = f"job_{int(time.time())}_{str(uuid.uuid4())[:6]}"
    job_dir = out_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    if dataset_binding:
        from backend.engine.training_provenance import frozen_annotation_root
        frozen_root = frozen_annotation_root(dataset_binding, d_path, job_dir)
        if frozen_root: annotation_root = frozen_root
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
            "dataset_binding": dataset_binding,
            "warm_start": {"job_id": warm_start.job_id, "checkpoint_path": str(warm_start.checkpoint_path), "checkpoint_sha256": warm_start.checkpoint_sha256, "task": warm_start.task, "architecture": warm_start.architecture, "classes": list(warm_start.classes), "dataset_fingerprint": warm_start.dataset_fingerprint} if warm_start else None,
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
            warm_start=warm_start, dataset_binding=dataset_binding,
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
            warm_start=warm_start, dataset_binding=dataset_binding,
        )

    return {
        "job_id": job_id,
        "status": "queued" if profile is not None and record.status == "queued" else "started",
        "preset": req.preset,
        "task": req.task,
        "output_dir": str(job_dir),
        "compute_profile_id": profile.id if profile is not None else None,
        "phase": record.phase if profile is not None else None,
        "training_provenance": dataset_binding,
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


def _completed_receipt_record(job_id: str, request: Request) -> Optional[JobRecord]:
    """Read verified project completion without creating a worker or manager entry."""
    from backend.api.routes_project import get_current_project
    from backend.engine.checkpoint_paths import trusted_checkpoint, completed_job_receipt, is_job_id
    from backend.api.routes_evaluation import _matches_source_dataset
    from backend.api.routes_dataset_versions import _read_manifest, _require_active_labelset, _file_hash
    from backend.engine.training_provenance import validate_training_binding
    import torch
    try:
        if not is_job_id(job_id):
            return None
        project = get_current_project(request)
        models = Path(project['models_dir'])
        expected = models / job_id
        if models.is_symlink() or expected.is_symlink():
            return None
        checkpoint = trusted_checkpoint(job_id, project_models_dir=models)
        if checkpoint is None or checkpoint.parent.resolve() != expected.resolve():
            return None
        receipt = completed_job_receipt(expected)
        source, task = project.get('source_dataset_dir'), project['task']
        if (not source or not receipt or receipt.get('job_id') != job_id or receipt.get('task') != task
                or Path(receipt.get('output_dir', '')).resolve() != expected.resolve()
                or not _matches_source_dataset(expected, source, task)):
            return None
        metadata_path = expected / 'model_meta.json'
        if metadata_path.is_symlink() or not metadata_path.is_file():
            return None
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if (not isinstance(metadata, dict) or metadata.get('task') != task
                or not isinstance(metadata.get('checkpoint_sha256'), str)
                or metadata['checkpoint_sha256'] != _file_hash(checkpoint)):
            return None
        payload = torch.load(checkpoint, map_location='cpu', weights_only=True)
        if (not isinstance(payload, dict) or payload.get('task') != task
                or not isinstance(payload.get('model_state_dict'), dict) or not payload['model_state_dict']
                or ('classes' in metadata and payload.get('classes') != metadata['classes'])):
            return None
        binding = receipt.get('training_provenance')
        original_binding = payload.get('training_provenance')
        if (not isinstance(binding, dict) or binding != metadata.get('training_provenance')
                or not isinstance(original_binding, dict)):
            return None
        directory, manifest = _read_manifest(project, binding['dataset_version_id'])
        _require_active_labelset(project, manifest)
        labelset = project.get('active_labelset_id', 'default')
        if (directory.resolve() != Path(binding['version_dir']).resolve()
                or Path(manifest['source_dataset_dir']).resolve() != Path(source).resolve()
                or manifest['task'] != task
                or binding.get('labelset_id') != labelset or original_binding.get('labelset_id') != labelset
                or binding.get('dataset_fingerprint') != manifest['dataset_fingerprint']
                or receipt['dataset_fingerprint'] != binding.get('dataset_fingerprint')):
            return None
        if binding != original_binding:
            # Archives rebind metadata and version paths, while preserving the
            # immutable checkpoint bytes. Accept only the existing verified
            # manifest alias contract, including original pixel/backup hashes.
            from backend.engine.specialized_models import _verify_historical_source
            _verify_historical_source(models.resolve(), source, payload, metadata)
        validate_training_binding(binding)
        if receipt.get('compute_profile_id') or (expected / 'remote_job.json').exists():
            from backend.remote.operations import verify_downloaded_checkpoint
            verify_downloaded_checkpoint(expected, job_id)
        return JobRecord(
            job_id=job_id, task=task, preset=metadata.get('preset', 'fast'),
            dataset_path=receipt['dataset_path'], output_dir=str(expected), status='completed', phase='completed',
            source_dataset_path=source, dataset_fingerprint=receipt['dataset_fingerprint'],
            dataset_binding=binding, remote_profile_id=receipt.get('compute_profile_id'),
            current_epoch=receipt.get('current_epoch', 0), total_epochs=receipt.get('total_epochs', 0),
            current_step=receipt.get('current_step', 0), total_steps=receipt.get('total_steps', 0),
            train_loss=receipt.get('current_train_loss'), val_loss=receipt.get('current_val_loss'),
            best_metric=receipt.get('best_metric'), metrics=receipt.get('metrics', {}),
            loss_history=receipt.get('loss_history', []) if isinstance(receipt.get('loss_history', []), list) else [],
        )
    except Exception:
        # Missing, stale or corrupt evidence never becomes completed UI state.
        return None


@router.get("/status")
def get_training_status(job_id: Optional[str] = Query(None), request: Request = None):
    """Retrieves current training status and progress for polling fallbacks."""
    if job_id:
        record = training_job_manager.get_job(job_id)
        if record is None and request is not None:
            record = _completed_receipt_record(job_id, request)
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
        "loss_history": record.loss_history,
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
