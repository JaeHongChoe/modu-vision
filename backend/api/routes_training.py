"""
backend/api/routes_training.py

Thread-safe AutoML Training Job Manager & REST API Endpoints.
Coordinates background worker threads, hooks WebSocket telemetry,
and handles clean aborts with GPU/MPS memory clearing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.api.websocket_telemetry import WebSocketTelemetryCallback, broadcaster
from backend.engine.device import clear_device_cache, get_device
from backend.engine.dataset_loaders import ClassificationDataset, scoped_split_root, split_root_scope
from backend.engine.trainer import UnifiedAutoMLTrainer
from backend.engine.labelme_preparation import LabelMePreparationCancelled, prepare_labelme_segmentation
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.patch_classification import load_patch_manifest
from backend.engine.warm_start import WarmStartParent, architecture_for, resolve_warm_start_parent
from backend.engine.job_scheduler import JobScheduler
from backend.engine.job_state import IllegalTransition
from backend.engine.job_store import JobConflict, JobStore, PublicationFailed, StaleFencingToken, StaleRevision, ledger as _shared_ledger
from backend.remote.profiles import ComputeProfile
from backend.utils.error_catalog import classify_exception, format_error_response

logger = logging.getLogger("vision_ai_studio.routes_training")

router = APIRouter(prefix="/api/training", tags=["training"])

def job_ledger() -> JobStore:
    """The persistent job ledger in the configured application data folder, resolved when first used."""
    return _shared_ledger()


_END_EVENTS = {"completed": "complete", "failed": "fail", "aborted": "abort", "interrupted": "interrupt",
               "disconnected": "disconnect"}
# Ledger bookkeeping never stops a job's own bookkeeping, recovery or shutdown; only a launch
# without a recorded attempt is refused.
_LEDGER_ERRORS = (KeyError, StaleRevision, StaleFencingToken, IllegalTransition, OSError, sqlite3.Error)


def _existing_ledger_link(job_id: str) -> Optional["TrainingLedgerLink"]:
    """The link of a job the ledger already records, carrying its latest attempt's fencing token."""
    try:
        store = job_ledger()
        attempts = store.attempts(job_id)
    except _LEDGER_ERRORS:
        return None
    link = TrainingLedgerLink(store, job_id)
    link.fencing_token = attempts[-1]["fencing_token"] if attempts else None
    return link


class StoredScopeUnavailable(Exception):
    """The project scope verified at submission can no longer be resolved; the failure is kept, never redirected."""


def _register_receipt_in_stored_scope(store: JobStore, job_id: str):
    """Register a job's receipt in the project scope the server verified at submission, never the active selection."""
    row = store.record(job_id)
    receipt = Path(row["output_dir"] or "") / "job_receipt.json"
    if row["source"] != "api" or not row["output_dir"] or not receipt.is_file():
        return None
    if not row["registry_root"]:
        raise StoredScopeUnavailable("the submission did not record its project registry")
    from backend.contracts.context import ArtifactRegistration, ContextRegistry, ProjectContext
    root = Path(row["registry_root"])
    if not (root / ".context.sqlite3").is_file():
        raise StoredScopeUnavailable(f"the project registry recorded at submission is missing: {root}")
    registry = ContextRegistry(root)
    context = ProjectContext(workspace_id=row["workspace_id"], project_id=row["project_id"],
                             actor_id=row["actor_id"], mode=row["mode"])
    try:
        if registry.project_key(context) != row["project_key"]:
            raise StoredScopeUnavailable("the project namespace recorded at submission changed")
        project = registry.local_project(row["project_id"], None, workspace_id=row["workspace_id"])
    except HTTPException as exc:
        raise StoredScopeUnavailable(str(exc.detail)) from exc
    if row["project_dir"] and Path(project["project_dir"]).resolve() != Path(row["project_dir"]).resolve():
        raise StoredScopeUnavailable("the project folder recorded at submission moved")
    models = Path(project["models_dir"]).resolve()
    if not receipt.resolve().is_relative_to(models):
        raise StoredScopeUnavailable("the receipt is outside the project models folder recorded at submission")
    digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    try:
        return registry.register_artifact(project, ArtifactRegistration(
            kind="model", relative_path=receipt.resolve().relative_to(models).as_posix(), sha256=digest))
    except HTTPException as exc:
        raise StoredScopeUnavailable(str(exc.detail)) from exc


class TrainingLedgerLink:
    """Ledger side of one training job: an attempt before its worker starts and the state it ends in."""

    def __init__(self, store: JobStore, job_id: str):
        self.store, self.job_id, self.fencing_token = store, job_id, None
        self._shutdown_noted = False
        self._reattached = False
        self._claim = None
        self._lock = threading.Lock()

    def _record(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        try:
            for attempt in range(3):
                ref = self.store.get(self.job_id)
                try:
                    self.store.transition(self.job_id, ref.revision, event, payload, fencing_token=self.fencing_token)
                    return
                except StaleRevision:
                    # An evidence event (step) recorded between the read and the transition moved the revision; the
                    # transition is checked again against the job's current state.
                    if attempt == 2:
                        raise
        except _LEDGER_ERRORS as exc:
            logger.warning("Job ledger could not record %s for %s: %s", event, self.job_id, exc)

    def step(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """One piece of evidence that does not change the job's state (a cancel acknowledged, a signal sent, the
        worker's exit confirmed, the reservation released; S1-04). A ledger failure never stops the step itself."""
        try:
            self.store.record_event(self.job_id, event, payload)
        except _LEDGER_ERRORS as exc:
            logger.warning("Job ledger could not record %s for %s: %s", event, self.job_id, exc)

    def queued(self) -> None:
        self._record("queue")

    def waiting_for_device(self, resources: Dict[str, Any], priority: int = 0, budget: Optional[Dict[str, Any]] = None) -> None:
        """The job waits in the ledger queue for the local device; the scheduler claims it when it frees."""
        try:
            ref = self.store.get(self.job_id)
            self.store.enqueue(self.job_id, ref.revision, priority, resources, budget)
            self.store.set_wait_reasons({self.job_id: "device_reserved"})
        except _LEDGER_ERRORS as exc:
            logger.warning("Job ledger could not queue %s: %s", self.job_id, exc)

    def claimed(self, lease: Any) -> None:
        """The scheduler already recorded this job's attempt and fence; the launch records no second attempt."""
        self._claim = lease
        self.fencing_token = lease.fence

    def runtime_budget_spent(self) -> bool:
        """True once, when the job's runtime budget runs out: the cancel intent is recorded here."""
        try:
            budget = json.loads(self.store.record(self.job_id).get("budget_json") or "{}")
            limit = budget.get("max_runtime_s")
            attempts = self.store.attempts(self.job_id)
            if not limit or not attempts or self.store.cancel_intent(self.job_id):
                return False
            if time.time_ns() - attempts[-1]["started_ns"] <= float(limit) * 1e9:
                return False
            self.store.request_cancel(self.job_id, "scheduler", "runtime budget exceeded")
            return True
        except _LEDGER_ERRORS as exc:
            logger.warning("Job ledger could not check the budget of %s: %s", self.job_id, exc)
            return False

    def heartbeat(self, lease_seconds: float) -> None:
        """Extend the attempt lease while this backend owns the attempt (claimed, launched or reattached)."""
        if self.fencing_token is None:
            return
        try:
            self.store.heartbeat(self.job_id, self.fencing_token, lease_seconds)
        except _LEDGER_ERRORS as exc:
            logger.warning("Job ledger could not extend the attempt lease of %s: %s", self.job_id, exc)

    def launched(self, executor: str) -> None:
        """Commit the attempt before launching; a failure here stops the launch.

        A run recovered after a restart that already has an attempt continues under its latest
        fencing token (reattach); it is never recorded, or launched, a second time.
        """
        if self._claim is not None:
            return
        from backend.engine.local_training_worker import _boot_id
        ref = self.store.get(self.job_id)
        attempts = self.store.attempts(self.job_id)
        if attempts and ref.state in ("running", "detached", "disconnected"):
            self.fencing_token = attempts[-1]["fencing_token"]
            self.reattached()
            return
        attempt = self.store.begin_attempt(self.job_id, ref.revision, executor, _boot_id(), os.getpid())
        self.fencing_token = attempt.fencing_token
        ref = self.store.get(self.job_id)
        self.store.transition(self.job_id, ref.revision, "reattach" if ref.state in ("detached", "disconnected") else "start",
                              {"attempt": attempt.number}, fencing_token=attempt.fencing_token)

    def reattached(self) -> None:
        """This backend takes the job's attempt over under a new fence, once: the previous owner (an earlier
        process that may still be alive) can no longer heartbeat, end the job or publish its receipt."""
        from backend.engine.shared_scheduler import shared_leases
        with self._lock:
            try:
                state = self.store.get(self.job_id).state
                # Once per backend while the job keeps running; again after each new detach or disconnect.
                if state in ("running", "stopping") and self._reattached:
                    return
                if state in ("running", "stopping", "detached", "disconnected"):
                    leases = shared_leases()
                    self.fencing_token = self.store.reattach(self.job_id, worker_id=f"pid:{os.getpid()}",
                                                             lease_seconds=leases.lease_seconds)
                    leases.restamp_fence(self.job_id, self.fencing_token)  # a reservation follows its attempt's fence
                self._reattached = True
            except _LEDGER_ERRORS as exc:
                logger.warning("Job ledger could not reattach %s: %s", self.job_id, exc)

    def detached(self, reason: str) -> None:
        """A running worker is detached; a job not yet launched keeps its state with a recorded shutdown intent."""
        try:
            state = self.store.get(self.job_id).state
            if state in ("running", "stopping"):
                self._record("detach", {"reason": reason})
            elif state != "detached" and not self._shutdown_noted:  # the signal handler and the lifespan both run
                self.store.record_event(self.job_id, "shutdown_intent", {"reason": reason, "state": state})
                self._shutdown_noted = True
        except _LEDGER_ERRORS as exc:
            logger.warning("Job ledger could not record the shutdown intent of %s: %s", self.job_id, exc)

    def finished(self, status: str, error: Optional[Dict[str, Any]] = None) -> None:
        """Record the end state and publish the receipt only while this attempt owns the job."""
        event = _END_EVENTS.get(status)
        if event is None:
            return

        def publish():
            try:
                return _register_receipt_in_stored_scope(self.store, self.job_id)
            except (StoredScopeUnavailable, OSError, ValueError, KeyError) as exc:
                # A retained, visible failure: no reference is created in any other project.
                raise PublicationFailed(str(exc)) from exc
        try:
            self.store.finish(self.job_id, event, {"error": error} if error else None, fencing_token=self.fencing_token,
                              publish=publish if event != "disconnect" else None)
        except _LEDGER_ERRORS as exc:
            # A stale attempt, an already ended job or an unavailable ledger: nothing is published or ended here.
            logger.warning("Job ledger did not record %s for %s: %s", event, self.job_id, exc)


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
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    ledger: Optional[TrainingLedgerLink] = field(default=None, repr=False)


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
    local_model_id=(record.launch_spec or {}).get('local_model_id')
    if local_model_id:
        import re
        if (not record.remote_profile_id or record.task not in {'rotation','ocr','rotated_detection','enhancement','defect_gan'}
                or re.fullmatch('[0-9a-f]{32}',local_model_id) is None or record.job_id!='job_'+local_model_id or output_dir.name!=local_model_id):
            raise ValueError('Remote specialist and native model identity differ')
        receipt.update(job_id=local_model_id,remote_job_id=record.job_id)
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
    checkpoint = output_dir / 'best_model.pt'
    if record.status == 'completed' and checkpoint.is_file() and not checkpoint.is_symlink():
        from backend.engine.warm_start import _sha256
        receipt['checkpoint_sha256'] = _sha256(checkpoint)
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


def _release_reservation(leases: Any, job_id: str, status: str, link: Optional[TrainingLedgerLink]) -> None:
    """Release an ended job's reservation; a reservation actually removed is recorded apart from the job's end (S1-04)."""
    if leases.release(job_id, terminal=True) and link is not None:
        link.step("reservation_released", {"by": "job_end", "status": status})  # an operator's release names its actor


class TrainingJobManager:
    """Singleton coordinator for background PyTorch training jobs."""

    ACTIVE_STATES = ("running", "stopping", "disconnected")

    def __init__(self, *, local_execution: Literal['subprocess', 'embedded'] = 'subprocess'):
        # Embedded execution is an explicit library/test compatibility mode.
        # The application singleton and ordinary callers own a CLI subprocess.
        self.local_execution = local_execution
        self._lock = threading.Lock()
        self._jobs: Dict[str, JobRecord] = {}
        self._active_job_id: Optional[str] = None
        self._remote_queue: List[str] = []
        # Local jobs waiting for the device: their launch arguments, in arrival order (the ledger holds the queue).
        self._local_waiting: Dict[str, Dict[str, Any]] = {}
        self._local_watcher = None
        from backend.engine.shared_scheduler import shared_leases
        self._leases = shared_leases()
        self._queue_watcher = None
        # Set by a normal quit: waiting jobs stay queued in the ledger for the next start instead of being claimed now.
        self._shutdown = threading.Event()
        self._maintenance: Optional[threading.Thread] = None

    def _lease_host(self, profile):
        return f"ssh:{profile.ssh_target.rsplit('@', 1)[-1].lower()}:{profile.ssh_port}"

    def _watch_queue(self):
        if self._queue_watcher is not None and self._queue_watcher.is_alive(): return
        def watch():
            while True:
                time.sleep(.5)
                with self._lock:
                    if not self._remote_queue or self._shutdown.is_set(): return
                    self._start_waiting_remote_jobs_locked()
        self._queue_watcher = threading.Thread(target=watch,daemon=True,name="SharedComputeQueue")
        self._queue_watcher.start()

    def _heartbeat(self, record):
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(5):
                self._leases.heartbeat(record.job_id)
                if record.ledger is not None:
                    record.ledger.heartbeat(self._leases.lease_seconds)
                self._enforce_budget(record)
        threading.Thread(target=heartbeat,daemon=True,name=f"Lease-{record.job_id}").start()
        return stop

    def _enforce_budget(self, record: JobRecord) -> None:
        """A spent runtime budget is recorded as a cancel intent; the job's own manager then stops it the normal way."""
        if record.ledger is not None and record.status in ("running",) and record.ledger.runtime_budget_spent():
            self.abort_job(record.job_id)

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
        if not self._leases.available(self._lease_host(profile),profile.gpu_selector or 'all',
                memory_budget_mb=profile.memory_budget_mb or 0,allow_sharing=profile.allow_sharing):return True
        reserved={row['job_id'] for row in self._leases.list()}
        for record in self._jobs.values():
            if record.status not in self.ACTIVE_STATES or record.remote_profile_id is None:
                continue
            if record.job_id in reserved:continue
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
        if record.status not in ("completed", "aborted", "failed", "interrupted", "stopped"):
            raise ValueError("Only terminal jobs can be restored without a worker")
        with self._lock:
            self._jobs.setdefault(record.job_id, record)

    def restore_local_job(self, record: JobRecord, *, runner=None, leases=None) -> None:
        """Register an existing CLI child; its launch and optimizer are not replayed."""
        with self._lock:
            previous = self._jobs.get(record.job_id)
            if previous is not None and (previous.status not in ('disconnected', 'stopping')
                                        or previous.thread is not None and previous.thread.is_alive()):
                return
            self._jobs[record.job_id] = record
            self._refresh_active_id()
        if runner is None:
            return
        # The recovered worker reports under the job's existing attempt; it is never a new launch.
        if record.ledger is None:
            record.ledger = _existing_ledger_link(record.job_id)
        if record.ledger is not None:
            record.ledger.reattached()  # never raises: the monitor below must always start
        def monitor():
            stop = threading.Event()
            def heartbeat():
                while not stop.wait(5):
                    leases.heartbeat(record.job_id)
                    if record.ledger is not None:
                        record.ledger.heartbeat(leases.lease_seconds)
                    self._enforce_budget(record)
            threading.Thread(target=heartbeat, daemon=True, name=f'Lease-{record.job_id}').start()
            try:
                record.result = runner(WebSocketTelemetryCallback(job_id=record.job_id, max_hz=30.0))
                record.status = record.result['status']
                record.phase = record.status
                if record.result.get('error'):
                    record.error = {'message': record.result['error']}
            except Exception as exc:
                from backend.engine.local_training_worker import LocalWorkerUncertain
                record.status = record.phase = 'disconnected' if isinstance(exc, LocalWorkerUncertain) else 'failed'
                record.error = {'message': str(exc)}
            finally:
                stop.set()
                if record.status == 'disconnected':
                    leases.mark_uncertain(record.job_id)
                else:
                    try:
                        _write_job_receipt(record)
                    except OSError:
                        logger.exception('Could not persist recovered local receipt for %s', record.job_id)
                    _release_reservation(leases, record.job_id, record.status, record.ledger)
                if record.ledger is not None:
                    record.ledger.finished(record.status, record.error)
                with self._lock:
                    self._refresh_active_id()
        record.thread = threading.Thread(target=monitor, daemon=True, name=f'LocalReconnect-{record.job_id}')
        record.thread.start()

    def reconnect_local_job(self, job_id: str) -> Optional[JobRecord]:
        record = self.get_job(job_id)
        if record is None or record.remote_profile_id is not None or record.status not in ('disconnected', 'stopping'):
            return None
        from backend.engine.local_training_worker import recover_local_jobs
        recover_local_jobs(self, job_id=job_id)
        return self.get_job(job_id)

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
        ledger: Optional[TrainingLedgerLink] = None,
        queue_when_busy: bool = True,
        priority: int = 0,
        budget: Optional[Dict[str, Any]] = None,
    ) -> JobRecord:
        with self._lock:
            active_record = next(
                (record for record in self._jobs.values()
                 if record.remote_profile_id is None and record.status in self.ACTIVE_STATES), None,
            )
            if active_record is not None or not self._leases.acquire(job_id, "local-compute", "all"):
                if ledger is None or not queue_when_busy:  # no ledger record, or the client asked not to wait
                    if active_record is not None:
                        raise HTTPException(409, f"Another training job ({active_record.job_id}) is currently in progress.")
                    raise HTTPException(409, "Local compute is reserved by another training or a worker preflight")
                return self._wait_for_local_device_locked(job_id, ledger, priority, budget, dict(
                    task=task, dataset_path=dataset_path, output_dir=output_dir, preset=preset, device=device,
                    config_overrides=config_overrides, prepare_dataset=prepare_dataset, source_dataset_path=source_dataset_path,
                    dataset_fingerprint=dataset_fingerprint, split_manifest_root=split_manifest_root, warm_start=warm_start,
                    dataset_binding=dataset_binding))
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
                        rec.val_loss = None if val_loss is None else float(val_loss)
                        rec.metrics = metrics
                        rec.loss_history.append({"epoch": epoch + 1, "train_loss": float(train_loss),
                                                 "val_loss": rec.val_loss, "lr": float(lr)})

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
                ) if self.local_execution == 'embedded' else None
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
                ledger=ledger,
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
                        if record.ledger is not None:
                            # The attempt is committed before any worker exists, so a restart never launches it twice.
                            record.ledger.launched('local' if self.local_execution == 'subprocess' else 'embedded')
                        if self.local_execution == 'subprocess':
                            from backend.engine.local_training_worker import run_owned_training
                            result = run_owned_training(record, cb, config_overrides=config_overrides, device=device,
                                                        split_manifest_root=split_manifest_root, leases=self._leases)
                        else:
                            result = trainer.train(job_id=job_id)
                        validate_training_binding(record.dataset_binding)
                except LabelMePreparationCancelled:
                    result = {"status": "aborted"}
                    cb.on_training_aborted(0, "Training preparation cancelled by user request")
                except Exception as ex:
                    logger.exception("Training job %s failed: %s", job_id, ex)
                    err_card = classify_exception(ex, details=str(ex))
                    error = err_card.to_ws_payload()
                    from backend.engine.local_training_worker import LocalWorkerUncertain
                    if isinstance(ex, LocalWorkerUncertain):
                        record.phase = 'disconnected'
                    # Also notify via WebSocket
                    cb.on_error(ex, stage="training_loop")
                finally:
                    try:
                        clear_device_cache()
                    finally:
                        with self._lock:
                            if error is not None:
                                record.status = "disconnected" if record.phase == 'disconnected' else "failed"
                                record.error = error
                            elif record.status == "stopping":
                                record.status = "aborted"
                                record.result = {"status": "aborted"}
                            elif result is not None:
                                record.status = result.get("status", "completed")
                                record.result = result
                                record.best_metric = result.get("best_metric")
                                if result.get('error'):
                                    record.error = {'message': str(result['error'])}
                            if self._active_job_id == job_id and record.status != 'disconnected':
                                self._active_job_id = None
                            self._refresh_active_id()
                        try:
                            if record.status != 'disconnected':
                                _write_job_receipt(record)
                        except Exception as persistence_error:
                            logger.exception("Could not persist terminal receipt for job %s", job_id)
                            record.status = "failed"
                            record.error = {"message": f"Training provenance persistence failed: {persistence_error}"}
                            try:
                                _write_job_receipt(record)
                            except OSError:
                                logger.exception("Terminal training state could not be saved after a persistence failure")
                    heartbeat.set()
                    if record.status == 'disconnected':
                        self._leases.mark_uncertain(job_id)
                    else:
                        _release_reservation(self._leases, job_id, record.status, record.ledger)
                    if record.ledger is not None:
                        record.ledger.finished(record.status, record.error)
                    logger.info("Background training thread finished for job %s", job_id)
                    self._dispatch_local_queue()

            t = threading.Thread(target=_worker, name=f"Trainer-{job_id}", daemon=True)
            record.thread = t
            t.start()
            return record

    def _wait_for_local_device_locked(self, job_id: str, ledger: "TrainingLedgerLink", priority: int,
                                      budget: Optional[Dict[str, Any]], launch: Dict[str, Any]) -> JobRecord:
        record = JobRecord(
            job_id=job_id, task=launch["task"], preset=launch["preset"], dataset_path=launch["dataset_path"],
            output_dir=launch["output_dir"], status="queued", phase="queued",
            source_dataset_path=launch["source_dataset_path"], dataset_fingerprint=launch["dataset_fingerprint"],
            warm_start=launch["warm_start"], dataset_binding=launch["dataset_binding"], ledger=ledger,
        )
        self._jobs[job_id] = record
        self._local_waiting[job_id] = launch
        ledger.waiting_for_device({"host": "local-compute", "selector": "all"}, priority, budget)
        if self._local_watcher is None or not self._local_watcher.is_alive():
            def watch():
                while True:
                    time.sleep(1)
                    with self._lock:
                        if not self._local_waiting or self._shutdown.is_set():
                            return
                    self._dispatch_local_queue()
            self._local_watcher = threading.Thread(target=watch, daemon=True, name="LocalComputeQueue")
            self._local_watcher.start()
        return record

    def local_queue_waiting(self) -> bool:
        """Whether a local training of this process is queued for the local device (a preflight must not overtake it)."""
        with self._lock:
            return bool(self._local_waiting)

    def _dispatch_local_queue(self) -> None:
        """When the local device is free, the scheduler claims the next waiting local job (attempt + fenced
        reservation) and it launches under that claim; jobs left waiting keep their recorded reason."""
        with self._lock:
            waiting = list(self._local_waiting)
            if not waiting or self._shutdown.is_set() or any(r.remote_profile_id is None and r.status in self.ACTIVE_STATES for r in self._jobs.values()):
                return
        try:
            lease = JobScheduler(job_ledger(), self._leases).claim_job(
                f"pid:{os.getpid()}", {"hosts": ["local-compute"], "job_ids": waiting})
        except _LEDGER_ERRORS as exc:
            logger.warning("Local queue could not claim a job: %s", exc)
            return
        if lease is None:
            return
        with self._lock:
            launch = self._local_waiting.pop(lease.job_id, None)
            waiting_record = self._jobs.pop(lease.job_id, None) if launch is not None else None
        if launch is None or waiting_record is None or waiting_record.ledger is None:
            # Stopped while being claimed: end the claim under its fence and free the device; nothing launches.
            try:
                JobScheduler(job_ledger(), self._leases).publish_result(lease, "aborted", {"reason": "stopped while being claimed"})
            except _LEDGER_ERRORS as exc:
                logger.warning("Local queue could not end the claim of %s: %s", lease.job_id, exc)
            return
        link = waiting_record.ledger
        link.claimed(lease)
        try:
            self.start_job(job_id=lease.job_id, ledger=link, **launch)
        except Exception as exc:  # the claim is ended under its fence; the job is listed as failed
            logger.exception("Queued local job %s could not start", lease.job_id)
            waiting_record.status = waiting_record.phase = "failed"
            waiting_record.error = {"message": f"Training could not start: {exc}"}
            with self._lock:
                self._jobs[lease.job_id] = waiting_record
            _release_reservation(self._leases, lease.job_id, "failed", link)
            link.finished("failed", waiting_record.error)

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
        ledger: Optional[TrainingLedgerLink] = None,
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
            if ledger is None:
                # A run resumed at startup keeps its ledger history (first attempt or reattach).
                ledger = _existing_ledger_link(job_id)
            queued = profile is not None and recovery_state not in ("transferring", "launching", "launched", "artifacts_verified", "completed") and self._remote_slot_busy(profile)
            record = JobRecord(
                job_id=job_id, task=task, preset=preset, dataset_path=dataset_path,
                output_dir=output_dir, status="queued" if queued else "running", remote_profile_id=remote_profile_id,
                phase="queued" if queued else ("reconnecting" if recovery_state else "preparing"), source_dataset_path=source_dataset_path,
                dataset_fingerprint=dataset_fingerprint,
                remote_runner=remote_runner, remote_profile=profile, launch_spec=launch_spec,
                split_manifest_root=split_manifest_root,
                warm_start=warm_start, dataset_binding=dataset_binding, ledger=ledger,
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
            profile=record.remote_profile;launch=record.launch_spec or {}
            if not self._leases.acquire(job_id, self._lease_host(profile), profile.gpu_selector or "all", remote=True,
                    memory_budget_mb=profile.memory_budget_mb or 0,allow_sharing=profile.allow_sharing,task=record.task,
                    project_id=launch.get('project_id'),account_id=launch.get('account_id')):
                record.status = "queued"; record.phase = "resource_reserved"
                if job_id not in self._remote_queue: self._remote_queue.append(job_id)
                self._watch_queue()
                return

        def _worker() -> None:
            heartbeat = self._heartbeat(record)
            try:
                if record.ledger is not None:
                    record.ledger.launched('remote')
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
            else: _release_reservation(self._leases, job_id, record.status, record.ledger)
            if record.status != "disconnected":
                try:
                    _write_job_receipt(record)
                except OSError:
                    logger.exception("Could not persist terminal receipt for remote job %s", job_id)
            if record.status != "disconnected":
                with self._lock:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                    self._start_waiting_remote_jobs_locked()
                    self._refresh_active_id()
            if record.ledger is not None:
                record.ledger.finished(record.status, record.error)

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
                if record.ledger is not None:
                    record.ledger.reattached()
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
            else: _release_reservation(self._leases, job_id, record.status, record.ledger)
            if record.status != "disconnected":
                try:
                    _write_job_receipt(record)
                except OSError:
                    logger.exception("Could not persist terminal receipt for remote job %s", job_id)
            if record.status != "disconnected":
                with self._lock:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                    self._start_waiting_remote_jobs_locked()
                    self._refresh_active_id()
            if record.ledger is not None:
                record.ledger.finished(record.status, record.error)

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
                if job_id in self._remote_queue:
                    self._remote_queue.remove(job_id)
                if self._local_waiting.pop(job_id, None) is not None and record.ledger is not None:
                    record.ledger.finished("aborted")
                queued_record = record
            elif not record or record.status not in self.ACTIVE_STATES:
                return False
            else:
                if record.remote_profile_id:
                    from backend.remote.coordinator import request_remote_cancellation
                    request_remote_cancellation(record)
                elif self.local_execution == 'subprocess':
                    from backend.engine.local_training_worker import request_local_cancellation
                    request_local_cancellation(record)
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

    def detach_all_for_shutdown(self) -> None:
        """A normal shutdown keeps job ownership and the recovery policy instead of aborting.

        Owned CLI workers are detached with a recorded intent and reattached on the next start;
        in-process (embedded) jobs cannot outlive the daemon, so they stop as before. Jobs still waiting
        for the device are not claimed any more; they stay queued in the ledger for the next start.
        """
        self._shutdown.set()
        with self._lock:
            local = [record for record in self._jobs.values()
                     if record.remote_profile_id is None and record.status in self.ACTIVE_STATES]
        for record in local:
            if self.local_execution == 'subprocess':
                if record.ledger is not None:
                    record.ledger.detached('normal shutdown')
                if record.process is not None:  # a worker that never launched leaves an ordinary expiring reservation
                    self._leases.mark_uncertain_local(record.job_id)
            else:
                self.abort_job(record.job_id)


# Global singleton instance
training_job_manager = TrainingJobManager()


def reconcile_job_ledger(manager: TrainingJobManager) -> List[str]:
    """After worker recovery, every job the ledger accepted is accounted for and none is launched again.

    A recovered worker or receipt is authoritative for its job; a job with neither becomes interrupted
    and stays readable in its own project through the ledger. Cancel intents recorded while a worker was
    unreachable are applied once it is back.
    """
    store = job_ledger()
    if isinstance(manager, TrainingJobManager):
        _start_ledger_maintenance(manager, store)
    if not _own_data_folder(store.path.parent):
        # Another live backend uses this data folder and owns its jobs: interrupting or freeing anything here
        # without exit evidence could stop or double-book its work. Maintenance takes over once that backend exits.
        logger.warning("Another backend owns %s; job reconciliation waits until it exits", store.path.parent)
        return []
    known = {record.job_id: record for record in manager.list_jobs()}
    interrupted: List[str] = []
    for row in store.active("training"):
        try:
            _reconcile_one(store, manager, known, row, interrupted)
        except _LEDGER_ERRORS as exc:
            logger.warning("Job ledger could not reconcile %s: %s", row["id"], exc)
    try:  # a claim whose worker never launched (or that ended) must not keep the device reserved
        from backend.engine.shared_scheduler import shared_leases
        JobScheduler(store, getattr(manager, "_leases", None) or shared_leases()).sweep_orphaned_reservations()
    except _LEDGER_ERRORS as exc:
        logger.warning("Job ledger could not sweep orphaned reservations: %s", exc)
    return interrupted


_FOLDER_LOCKS: Dict[str, Any] = {}
_MAINTENANCE_SECONDS = 10.0


def _configured_folders() -> Dict[str, str]:
    return {key: value for key, value in os.environ.items() if key.startswith("VISION_")}


def _start_ledger_maintenance(manager: TrainingJobManager, store: JobStore) -> None:
    """One maintenance thread per manager session; a later startup of the same manager replaces it."""
    if threading.current_thread() is manager._maintenance:
        return  # the takeover recovery below runs reconcile from this thread
    manager._shutdown.clear()  # startup: this manager serves a new session
    thread = threading.Thread(target=_maintain_job_ledger, args=(manager, store, _configured_folders()),
                              daemon=True, name="JobLedgerMaintenance")
    manager._maintenance = thread
    thread.start()


def _maintain_job_ledger(manager: TrainingJobManager, store: JobStore, folders: Dict[str, str]) -> None:
    while not manager._shutdown.wait(_MAINTENANCE_SECONDS):
        if threading.current_thread() is not manager._maintenance or _configured_folders() != folders:
            return  # replaced by a later startup, or the configured data folders changed: never touch another folder
        try:
            _ledger_maintenance_tick(manager, store)
        except Exception:  # one bad tick (a locked file, a busy database) never ends maintenance for the session
            logger.exception("Job ledger maintenance tick failed; retrying at the next tick")


def _ledger_maintenance_tick(manager: TrainingJobManager, store: JobStore) -> bool:
    """A backend that found the data folder owned takes it over once that backend exits, recovering as at startup;
    the owner keeps freeing reservations of ended jobs once nothing refreshes them any more."""
    folder = store.path.parent
    took_over = str(Path(folder).resolve()) not in _FOLDER_LOCKS
    if not _own_data_folder(folder):
        return True
    try:
        if took_over:
            logger.warning("The backend that owned %s exited; recovering its jobs", folder)
            from backend.engine.local_training_worker import recover_local_jobs
            recover_local_jobs(manager)  # observe its workers first, then reconcile and sweep
        else:
            JobScheduler(store, manager._leases).sweep_orphaned_reservations()
    except _LEDGER_ERRORS as exc:
        logger.warning("Job ledger maintenance failed: %s", exc)
    return True


def _own_data_folder(folder: Path) -> bool:
    """Hold an exclusive, process-lifetime lock on the jobs folder; False when another process holds it."""
    key = str(Path(folder).resolve())
    if key in _FOLDER_LOCKS:
        return True
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        handle = open(Path(folder) / "owner.lock", "a+b")  # an antivirus or indexer may hold it for a moment
    except OSError as exc:
        logger.warning("Could not open the data folder lock in %s: %s", folder, exc)
        return False
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _FOLDER_LOCKS[key] = handle  # released when the process exits
    return True


def _reconcile_one(store: JobStore, manager: TrainingJobManager, known: Dict[str, JobRecord], row: Dict[str, Any],
                   interrupted: List[str]) -> None:
    job_id = row["id"]
    attempts = store.attempts(job_id)
    record = known.get(job_id)
    if record is not None:
        # Reuse the link recovery attached (it may already hold the rotated fence); otherwise start from the latest attempt.
        link = record.ledger if isinstance(record.ledger, TrainingLedgerLink) and record.ledger.job_id == job_id else None
        if link is None:
            link = TrainingLedgerLink(store, job_id)
            link.fencing_token = attempts[-1]["fencing_token"] if attempts else None
        record.ledger = link
        if record.status in TrainingJobManager.ACTIVE_STATES and record.status != "disconnected":
            link.reattached()
        else:
            link.finished(record.status, record.error)
        if store.cancel_intent(job_id) and record.status in TrainingJobManager.ACTIVE_STATES:
            manager.abort_job(job_id)
        return
    reason = ("no worker or receipt was found after the restart" if attempts
              else "accepted but not launched before the restart")
    try:
        store.transition(job_id, row["revision"], "interrupt", {"reason": reason})
    except (StaleRevision, IllegalTransition) as exc:
        logger.warning("Job ledger could not interrupt %s: %s", job_id, exc)
        return
    # Readback comes from the ledger, guarded by project namespace (_ledger_readback), never from an unscoped
    # in-memory record that every project of a local backend would list.
    interrupted.append(job_id)


# Ended without a receipt to verify, these jobs exist only in the ledger after a later restart. A ledger
# "completed" never becomes completed UI state without its verified receipt (_completed_receipt_record).
_LEDGER_READBACK_STATES = ("interrupted", "failed", "aborted")


def _ledger_job_record(row: Dict[str, Any], status: str, message: str) -> JobRecord:
    spec = json.loads(row["spec_json"])
    return JobRecord(
        job_id=row["id"], task=spec.get("task", "classification"), preset=spec.get("preset", "fast"),
        dataset_path=spec.get("dataset_path", ""), output_dir=row["output_dir"] or "", status=status,
        phase=status, error={"message": message},
        source_dataset_path=spec.get("dataset_path"), dataset_fingerprint=spec.get("dataset_fingerprint"),
        remote_profile_id=spec.get("compute_profile_id"),
    )


def _ledger_end_message(store: JobStore, row: Dict[str, Any]) -> str:
    for event in reversed(store.events(row["id"])):
        # Only the transition into the end state explains it; events recorded in that state afterwards (an operator's
        # reservation release, a retained publication failure) keep their own reasons.
        if (event["to_state"] == row["state"] and event["from_state"] != event["to_state"]
                and isinstance(event["payload"], dict)):
            error = event["payload"].get("error")
            message = event["payload"].get("reason") or (error.get("message") if isinstance(error, dict) else None)
            if message:
                return f"Training was {row['state']}: {message}."
    return f"Training was {row['state']} before the backend restarted."


def _ledger_readback(request: Optional[Request], job_id: Optional[str] = None) -> List[JobRecord]:
    """Ended jobs the ledger keeps for the request's own workspace and project namespace only."""
    if request is None:
        return []
    from backend.contracts.context import get_project_context
    try:
        context = get_project_context(request)
        project_key = request.app.state.context_registry.project_key(context)
        store = job_ledger()
        rows = [store.record(job_id)] if job_id else store.ended("training", project_key, _LEDGER_READBACK_STATES)
        return [_ledger_job_record(row, row["state"], _ledger_end_message(store, row)) for row in rows
                if row["kind"] == "training" and row["project_key"] == project_key
                and row["workspace_id"] == context.workspace_id and row["state"] in _LEDGER_READBACK_STATES]
    except HTTPException:
        return []  # no project context: nothing from the ledger is shown
    except (*_LEDGER_ERRORS, ValueError) as exc:
        if not isinstance(exc, KeyError):
            logger.warning("Job ledger readback failed: %s", exc)
        return []


class TrainingConfigOverrides(BaseModel):
    model_config = ConfigDict(extra="ignore")
    epochs: Optional[int] = Field(None, ge=1, le=500)
    batch_size: Optional[int] = Field(None, ge=1, le=128)
    learning_rate: Optional[float] = Field(None, gt=0.0, le=1.0)
    image_size: Optional[int] = Field(None, ge=64, le=1024)
    patience: Optional[int] = Field(None, ge=1, le=50)
    device: Optional[str] = None
    backbone: Optional[str] = None
    model_name: Optional[str] = None
    anomaly_mode: Optional[Literal['classification', 'segmentation']] = None
    anomaly_method: Optional[Literal['padim', 'patchcore', 'dino_synthetic']] = None
    anomaly_backbone: Optional[Literal['dinov3_vits16', 'dinov3_vitb16', 'dinov3_vitl16']] = None
    patch_size: Optional[int] = Field(None, strict=True, ge=32, le=1024)
    stride: Optional[int] = Field(None, strict=True, ge=1, le=1024)
    patches_per_image: Optional[int] = Field(None, strict=True, ge=1, le=128)
    inference_batch_size: Optional[int] = Field(None, strict=True, ge=1, le=128)
    pretrained: Optional[bool] = None
    pretrained_checkpoint: Optional[str] = None
    pretrained_sha256: Optional[str] = None
    train_mode: Optional[Literal['head_only', 'partial', 'full']] = None
    partial_blocks: Optional[int] = Field(None, strict=True, ge=1, le=24)
    resume_checkpoint: Optional[str] = None
    use_amp: Optional[bool] = None
    seed: Optional[int] = Field(None, strict=True, ge=0, le=2147483647)

    @model_validator(mode='after')
    def validate_synthetic_geometry(self):
        if self.anomaly_method == 'dino_synthetic':
            self.anomaly_backbone = self.anomaly_backbone or 'dinov3_vits16'
            self.patch_size = 256 if self.patch_size is None else self.patch_size
            self.stride = 128 if self.stride is None else self.stride
            self.patches_per_image = 8 if self.patches_per_image is None else self.patches_per_image
            self.inference_batch_size = 32 if self.inference_batch_size is None else self.inference_batch_size
            if self.patch_size % 16 or self.stride > self.patch_size:
                raise ValueError('DINO synthetic patch_size must be a multiple of 16 and stride must not exceed patch_size')
        return self


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
    # Scheduling: wait in the queue when the device is busy (false keeps the immediate refusal), the queue
    # priority, and a runtime budget that becomes a cancel intent (never a signal) once spent.
    queue: bool = True
    priority: int = Field(0, ge=-10, le=10)
    max_runtime_s: Optional[float] = Field(None, gt=0, le=7 * 24 * 3600)


class TrainingStopRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: Optional[str] = None


class ReservationReleaseRequest(BaseModel):
    """An operator's confirmation that a job whose exit could not be proven no longer uses its device (S1-04)."""
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=128)
    confirm: Literal[True]
    reason: str = Field(min_length=3, max_length=500)
    fence: Optional[int] = None


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


@router.get('/resume-states')
def list_resume_states(dataset_path: str, task: str, request: Request, preset: str = 'fast'):
    """List owned epoch states; selecting one still undergoes strict restore validation."""
    source = Path(dataset_path).expanduser().resolve()
    models = _warm_start_scope(request, source)
    from backend.engine.training_resume import read_training_state
    from backend.engine.training_resume import backend_numeric_flags
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    import torch
    fingerprint = fingerprint_dataset(source)
    rows = []
    for path in models.glob('*/latest_training_state.pt'):
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(models)): continue
        try:
            state = read_training_state(path); identity = state['identity']
            if (identity['task'] != task or identity['preset'] != preset
                    or identity['dataset_fingerprint'] != fingerprint
                    or identity['torch_version'] != str(torch.__version__)
                    or identity.get('backend_flags') != backend_numeric_flags()
                    or identity['device']=='mps'
                    or state['next_epoch'] >= identity['recipe']['epochs']
                    or state['early_stopping'].get('early_stop')): continue
            rows.append({'checkpoint_path': str(path), 'job_id': path.parent.name,
                'semantics': 'exact_resume', 'boundary': state['boundary'], 'next_epoch': state['next_epoch'],
                'global_step': state['global_step'], 'device': identity['device'], 'recipe': identity['recipe']})
        except (OSError, ValueError, KeyError, RuntimeError): continue
    return {'states': rows}


@router.get("/warm-start-parents")
def list_warm_start_parents(
    dataset_path: str, task: str, preset: str = "fast", request: Request = None,
    backbone: Optional[str] = None,
    model_name: Optional[str] = None,
    anomaly_method: Optional[str] = None,
    anomaly_backbone: Optional[str] = None,
    patch_size: Optional[int] = None,
    stride: Optional[int] = None,
):
    if request is None:
        raise HTTPException(status_code=409, detail="Open a project before selecting a warm-start parent")
    source = Path(dataset_path).expanduser().resolve()
    models = _warm_start_scope(request, source)
    try:
        architecture = architecture_for(task, preset, {key: value for key, value in {
            'backbone': backbone, 'model_name': model_name, 'anomaly_method': anomaly_method,
            'anomaly_backbone': anomaly_backbone, 'patch_size': patch_size, 'stride': stride}.items() if value is not None})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    parents = []
    from backend.engine.warm_start import training_classes
    try:
        current_classes = training_classes(task, source)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    for job_dir in sorted(models.iterdir()):
        if job_dir.is_symlink() or not job_dir.is_dir():
            continue
        try:
            parent = resolve_warm_start_parent(job_dir.name, models, source, task, architecture)
            if parent.classes != current_classes:
                continue
        except (OSError, ValueError):
            continue
        parents.append({
            "job_id": parent.job_id,
            "classes": list(parent.classes),
            "architecture": parent.architecture,
            "checkpoint_sha256": parent.checkpoint_sha256,
            "dataset_fingerprint": parent.dataset_fingerprint,
            'semantics': parent.semantics,
        })
    return {"parents": parents, "total": len(parents)}


_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


@router.post("/start")
def start_training(req: TrainingStartRequest, request: Request = None):
    """Initiates an asynchronous background AutoML training job."""
    reserved: List[TrainingLedgerLink] = []
    try:
        return _start_training(req, request, reserved)
    except Exception as exc:
        # A reserved job that could not be started ends as failed; a retry with the same key names it.
        if reserved and reserved[0].store.get(reserved[0].job_id).state == "accepted":
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            reserved[0].finished("failed", {"message": detail if isinstance(detail, str) else json.dumps(detail, default=str)})
        raise


def _reserve_training_job(request: Request, req: TrainingStartRequest, d_path: Path,
                          out_dir: Path) -> tuple[Optional[TrainingLedgerLink], Optional[Dict[str, Any]]]:
    """Record the job, and reserve its idempotency key, before any folder, project or process side effect."""
    from backend.contracts.context import get_project_context
    key = request.headers.get("Idempotency-Key")
    if key is not None and not _IDEMPOTENCY_KEY.fullmatch(key):
        raise HTTPException(422, "Idempotency-Key must be 1-128 letters, digits or the characters . _ : -")
    context = get_project_context(request)
    project_key = request.app.state.context_registry.project_key(context)
    # The key identifies the client's request; server-derived values (data fingerprint, imported
    # weights, resolved devices) may change between a request and its retry.
    spec = {**req.model_dump(), "dataset_path": str(d_path), "output_dir": str(out_dir.resolve())}
    from backend.api.routes_project import get_current_project
    store = job_ledger()
    job_id = f"job_{int(time.time())}_{str(uuid.uuid4())[:6]}"
    try:
        # The server-verified scope is stored with the job, so its receipt is registered there even after a restart.
        ref = store.submit(context, project_key, "training", spec, key, job_id=job_id, output_dir=str(out_dir / job_id),
                           parent_id=req.warm_start_job_id,
                           registry_root=str(request.app.state.context_registry.root),
                           project_dir=str(Path(get_current_project(request)["project_dir"]).resolve()))
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if ref.created:
        return TrainingLedgerLink(store, ref.id), None
    replay = store.response(ref.id) or {
        "job_id": ref.id, "status": ref.state, "preset": req.preset, "task": req.task,
        "output_dir": store.record(ref.id)["output_dir"], "compute_profile_id": req.compute_profile_id,
    }
    return None, {**replay, "idempotent_replay": True}


def _start_training(req: TrainingStartRequest, request: Optional[Request], reserved: List[TrainingLedgerLink]):
    requested = req.model_copy(deep=True)  # the client request, before server defaults are filled in
    options = req.config_overrides or {}
    if options.get('resume_checkpoint'):
        if req.compute_profile_id or req.warm_start_job_id:
            raise HTTPException(422, 'Exact resume currently uses local single-process epoch boundaries; choose it separately from remote/warm-start')
        if request is None: raise HTTPException(409, 'Open the checkpoint project before exact resume')
        from backend.api.routes_project import get_current_project
        models = Path(get_current_project(request)['models_dir']).resolve()
        checkpoint = Path(options['resume_checkpoint']).expanduser()
        if (checkpoint.name != 'latest_training_state.pt' or checkpoint.is_symlink()
                or not checkpoint.resolve().is_relative_to(models)
                or any(parent.is_symlink() for parent in checkpoint.parents if parent.is_relative_to(models))):
            raise HTTPException(422, 'Exact resume checkpoint must be an owned training state in the current project models folder')
        from backend.engine.training_resume import read_training_state
        try:
            state = read_training_state(checkpoint)
            if state['identity']['task'] != req.task or state['identity']['preset'] != req.preset:
                raise ValueError('Exact resume task/preset differs from the original recipe')
            if state['identity']['device']=='mps':
                raise ValueError('MPS exact resume is unsupported because its full device RNG state is not restored')
            if fingerprint_dataset(Path(req.dataset_path).expanduser().resolve()) != state['identity']['dataset_fingerprint']:
                raise ValueError('Exact resume dataset snapshot changed')
            if any(key != 'resume_checkpoint' and value != state['identity']['recipe'].get(key) for key, value in options.items()):
                raise ValueError('Exact resume recipe changed; use warm-start to change training conditions')
            if req.device is not None and req.device != state['identity']['device']:
                raise ValueError('Exact resume device changed')
            if state['identity'].get('torch_version') != str(__import__('torch').__version__):
                raise ValueError('Exact resume PyTorch runtime changed')
            from backend.engine.training_resume import backend_numeric_flags
            if state['identity'].get('backend_flags')!=backend_numeric_flags():
                raise ValueError('Exact resume numeric backend flags changed')
            if state['next_epoch'] >= state['identity']['recipe']['epochs'] or state['early_stopping'].get('early_stop'):
                raise ValueError('Exact resume has no remaining epochs')
            req.config_overrides = {**state['identity']['recipe'], **options}
            if req.device is None: req.device = state['identity']['device']
        except (ValueError, OSError, KeyError) as exc: raise HTTPException(422, str(exc)) from exc
    try:
        from backend.engine.model_backbones import validate_training_controls
        validate_training_controls(req.task, req.preset, req.config_overrides or {})
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    if req.config_overrides:
        # Origin aliases are populated only by the worker after verifying its transferred input.
        req.config_overrides = {key: value for key, value in req.config_overrides.items() if key != 'pretrained_origin'}
    if request is not None and not req.compute_profile_id:
        from backend.api.routes_project import get_current_project
        from backend.engine.training_workspace import imported_weight
        options=req.config_overrides or {}
        selected=options.get('model_name','dinov3_vits16') if req.task=='segmentation' else options.get('anomaly_backbone','dinov3_vits16') if req.task=='anomaly' and options.get('anomaly_method')=='dino_synthetic' else options.get('anomaly_method','padim') if req.task=='anomaly' else options.get('backbone','yolo26n' if req.task=='detection' else 'dinov3_vits16')
        if not options.get('pretrained_checkpoint'):
            prepared=imported_weight(get_current_project(request),req.task,selected)
            if prepared:req.config_overrides={**options,'pretrained_checkpoint':prepared}
    if req.task=='anomaly' and (req.config_overrides or {}).get('anomaly_mode','classification') not in ('classification','segmentation'):
        raise HTTPException(422,'Invalid anomaly purpose; choose image classification or region segmentation')
    synthetic_anomaly = (req.config_overrides or {}).get('anomaly_method') == 'dino_synthetic'
    if synthetic_anomaly:
        if req.task != 'anomaly':
            raise HTTPException(422, 'DINO synthetic training requires the anomaly task')
        try:
            validated = TrainingConfigOverrides.model_validate(req.config_overrides).model_dump(exclude_none=True)
        except ValidationError as exc:
            raise HTTPException(422, f'Invalid DINO synthetic training options: {exc}') from exc
        req.config_overrides = {**req.config_overrides, **validated}
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
        if not req.queue or req.priority != 0:
            raise HTTPException(422, 'Remote queue uses FIFO; queue=false and nonzero priority are unsupported')
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

    if synthetic_anomaly:
        from backend.engine.dataset_loaders import AnomalyDataset
        from backend.engine.grouped_dataset_views import load_manifest_dataset, NORMAL_NAMES
        from PIL import Image
        try:
            normal_train = load_manifest_dataset('anomaly', effective_dataset_path, 'train')
            if normal_train is None:
                normal_train = AnomalyDataset(root_dir=effective_dataset_path, split='train', image_size=None, max_dim=0)
            if not normal_train.samples:
                raise ValueError('DINO synthetic training requires nonempty verified normal/good training images')
            for image, label, _ in normal_train.samples:
                parts = image.relative_to(effective_dataset_path).parts[:-1]
                if label != 0 or not any(part.casefold() in NORMAL_NAMES for part in parts):
                    raise ValueError('DINO synthetic training requires explicitly labelled normal/good source images')
                with Image.open(image) as original:
                    if min(original.size) < req.config_overrides['patch_size']:
                        raise ValueError('DINO synthetic source dimensions must be at least patch_size; source images are never enlarged')
        except (OSError, ValueError) as exc:
            raise HTTPException(422, f'Invalid normal-only training dataset: {exc}') from exc

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
        try:
            from backend.remote.ssh_transport import require_training_runtime
            require_training_runtime(readiness, req.task, req.preset, req.config_overrides, warm_start=bool(req.warm_start_job_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Compute server is not ready: {exc}",
            ) from exc
    else:
        # An explicitly requested accelerator this computer does not have is refused, never replaced by another
        # device (S1-05); "auto" keeps its documented meaning.
        explicit_device = str(req.device or "").strip().lower()  # the trainer uses this parameter, not config_overrides
        if explicit_device and explicit_device not in ("auto", "cpu"):
            from backend.contracts.capabilities import local_device_kinds
            kind = ("cuda" if explicit_device.startswith("cuda") else "mps" if explicit_device.startswith("mps")
                    else explicit_device)
            if kind not in local_device_kinds():
                raise HTTPException(status_code=409, detail=(
                    f"이 컴퓨터에는 {kind.upper()} 장치가 없습니다. 다른 장치로 바꿔 실행하지 않습니다. "
                    "CPU 또는 자동을 고르거나 해당 장치가 있는 서버를 선택하세요."))

    if req.output_dir:
        out_dir = Path(req.output_dir).resolve()
    elif request is not None:
        from backend.api.routes_project import get_current_project

        out_dir = Path(get_current_project(request)["models_dir"])
    else:
        # Direct Python calls used by backend tests retain their historical default.
        out_dir = Path("./models").resolve()
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
            from backend.engine.warm_start import training_classes
            if warm_start.classes != training_classes(req.task, d_path):
                raise ValueError('Warm-start parent classes differ from current training classes')
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    ledger = None
    if request is not None:
        ledger, replay = _reserve_training_job(request, requested, d_path, out_dir)
        if replay is not None:
            return replay
        reserved.append(ledger)
        if req.max_runtime_s:
            # Both worker paths may start immediately. Commit the requested limit before either can launch.
            # A budget that could not be persisted must not silently become an unlimited run.
            try:
                ledger.store.set_budget(ledger.job_id, {"max_runtime_s": req.max_runtime_s, "max_attempts": 1})
            except _LEDGER_ERRORS as exc:
                raise HTTPException(503, 'The runtime budget could not be recorded; no worker was launched') from exc
    out_dir.mkdir(parents=True, exist_ok=True)
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
    job_id = ledger.job_id if ledger is not None else f"job_{int(time.time())}_{str(uuid.uuid4())[:6]}"
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
            "warm_start": {"job_id": warm_start.job_id, "checkpoint_path": str(warm_start.checkpoint_path), "checkpoint_sha256": warm_start.checkpoint_sha256, "task": warm_start.task, "architecture": warm_start.architecture, "classes": list(warm_start.classes), "dataset_fingerprint": warm_start.dataset_fingerprint, 'semantics': warm_start.semantics} if warm_start else None,
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
            warm_start=warm_start, dataset_binding=dataset_binding, ledger=ledger,
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
            warm_start=warm_start, dataset_binding=dataset_binding, ledger=ledger,
            queue_when_busy=req.queue, priority=req.priority,
            budget={"max_runtime_s": req.max_runtime_s, "max_attempts": 1} if req.max_runtime_s else None,
        )

    response = {
        "job_id": job_id,
        "status": "queued" if getattr(record, "status", None) == "queued" else "started",
        "preset": req.preset,
        "task": req.task,
        "output_dir": str(job_dir),
        "compute_profile_id": profile.id if profile is not None else None,
        "phase": record.phase if profile is not None or getattr(record, "status", None) == "queued" else None,
        "training_provenance": dataset_binding,
        "warm_start_parent_job_id": warm_start.job_id if warm_start is not None else None,
    }
    if ledger is not None:
        # Only a remote job can wait in the queue; a local job starts in its own worker.
        if profile is not None and record.status == "queued" and ledger.store.get(job_id).state == "accepted":
            ledger.queued()
        ledger.store.set_response(job_id, response)
    return response


def _ledger_wait_reason(job_id: str) -> Optional[str]:
    try:
        return job_ledger().record(job_id).get("wait_reason")
    except _LEDGER_ERRORS:
        return None


def _record_in_request_project(record,request):
    if record is None:return False
    if request is None or getattr(request.state,'account_user',None) is None:return True
    from backend.api.routes_project import get_current_project
    return Path(record.output_dir).resolve().is_relative_to(Path(get_current_project(request)['models_dir']).resolve())


@router.post("/stop")
def stop_training(req: TrainingStopRequest,request:Request=None):
    """Aborts the specified or currently active training job."""
    job_id = req.job_id or (
        training_job_manager.get_active_job().job_id
        if training_job_manager.get_active_job()
        else None
    )

    if not job_id:
        return {"status": "not_running", "job_id": None}

    previous = training_job_manager.get_job(job_id)
    if not _record_in_request_project(previous,request):
        return {'status':'not_running','job_id':None}
    disconnected = previous is not None and previous.status == "disconnected"
    if previous is not None and previous.status in TrainingJobManager.ACTIVE_STATES + ("queued",):
        # The intent survives a restart or an unreachable worker; recovery applies it.
        link = previous.ledger or _existing_ledger_link(job_id)
        if link is not None:
            context = getattr(getattr(request, "state", None), "project_context", None)
            try:
                link.store.request_cancel(job_id, context.actor_id if context is not None else "local", "stop requested")
            except _LEDGER_ERRORS as exc:
                logger.warning("Job ledger could not record the stop intent for %s: %s", job_id, exc)
    success = training_job_manager.abort_job(job_id)
    if success and disconnected:
        if previous.remote_profile_id:
            training_job_manager.reconnect_remote_job(job_id)
        else:
            training_job_manager.reconnect_local_job(job_id)
    return {
        "status": "stopping" if success else "not_running",
        "job_id": job_id,
    }


_RESERVATION_RELEASE_LOCK = threading.Lock()
_RESERVATION_RELEASE_LOCKS: Dict[str, list] = {}  # job id -> [lock, confirmations using it]


@router.post("/reservations/confirm-release")
def confirm_reservation_release(req: ReservationReleaseRequest, request: Request = None):
    """Release a reservation left uncertain because the worker's exit could not be proven (a crash, a reboot, a lost
    server), after an operator confirms the device is free. Refused while the job is active or its owned local worker
    is provably alive. The confirmation (who, why, what was known) is written to the job ledger before anything is
    released and the outcome after it, as separate events, so the ledger never states a release that did not happen;
    only the reservation the operator saw (same fence, still uncertain) is removed."""
    # One confirmation per job at a time: a second one is answered from the first one's outcome. The table holds only
    # jobs with a confirmation in progress.
    with _RESERVATION_RELEASE_LOCK:
        entry = _RESERVATION_RELEASE_LOCKS.setdefault(req.job_id, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            return _confirm_reservation_release(req, request)
    finally:
        with _RESERVATION_RELEASE_LOCK:
            entry[1] -= 1
            if not entry[1]:
                _RESERVATION_RELEASE_LOCKS.pop(req.job_id, None)


def _local_journal(record: "JobRecord") -> Dict[str, Any]:
    """The run journal of a local job: its run folder copy, else the copy kept in the user data folder."""
    from backend.engine.local_training_worker import _index
    for path in ((Path(record.output_dir) / "local_job.json") if record.output_dir else None,
                 _index() / f"{record.job_id}.json"):
        try:
            if path is not None and path.is_file():
                journal = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(journal, dict) and journal.get("job_id", record.job_id) == record.job_id:
                    return journal
        except (OSError, ValueError):
            continue
    return {}


def _confirm_reservation_release(req: ReservationReleaseRequest, request: Optional[Request]) -> Dict[str, Any]:
    record = training_job_manager.get_job(req.job_id)
    if record is None:
        # A job whose run journal could not be recovered after a restart is known only to the ledger, while its
        # uncertain reservation survives: read it back in the request's own workspace and project.
        record = next(iter(_ledger_readback(request, req.job_id)), None)
    if record is None or not _record_in_request_project(record, request):
        raise HTTPException(404, "Job unavailable in this project")
    lease = next((row for row in training_job_manager._leases.list() if row["job_id"] == req.job_id), None)
    if lease is None:
        raise HTTPException(409, "This job holds no reservation")
    if not lease.get("uncertain"):
        raise HTTPException(409, "The reservation is not uncertain: it is returned when the worker's exit is confirmed")
    if req.fence != lease.get("fence"):
        raise HTTPException(409, "The reservation changed since it was shown; reload and confirm again")
    if record.status in ("queued", "preparing", "running", "stopping", "cancelling"):
        raise HTTPException(409, "The job is still active; cancel it or wait for it to end first")
    # A server job's process is not inspected from here; the job's profile or, for a read-back without one, the
    # reservation's own flag says it ran on a server.
    remote = bool(record.remote_profile_id or lease.get("remote"))
    liveness = "not_checked" if remote else "unknown"
    if not remote:
        from backend.engine.local_training_worker import _liveness
        journal = _local_journal(record)
        alive = _liveness(journal) if journal else None
        if alive is True:
            raise HTTPException(409, "The job's worker process is still running; cancel it first")
        # Without proof of exit, a reservation refreshed until moments ago may belong to a worker that is still running
        # (a live local worker refreshes it every few seconds). Proof of exit outweighs an expiry ahead of the clock.
        expires = float(lease.get("expires") or 0)
        if alive is None and expires > time.time():
            refreshed = expires - training_job_manager._leases.lease_seconds
            raise HTTPException(409, f"The job's worker refreshed this reservation {max(0, round(time.time() - refreshed))} s ago "
                                     "and may still be running; confirm again once it is no longer refreshed")
        liveness = "gone" if alive is False else "unknown"
    link = record.ledger or _existing_ledger_link(req.job_id)
    if link is None:
        raise HTTPException(409, "The job ledger is unavailable, so the release cannot be recorded; nothing was released")
    context = getattr(getattr(request, "state", None), "project_context", None)
    actor = context.actor_id if context is not None else "local"
    try:
        link.store.record_event(req.job_id, "reservation_release_confirmed",
                                {"actor": actor, "reason": req.reason, "liveness": liveness, "fence": lease.get("fence"),
                                 "remote": remote, "host": lease.get("host"), "at": time.time()})
    except _LEDGER_ERRORS as exc:
        raise HTTPException(409, f"The release could not be recorded in the job ledger ({exc}); nothing was released") from exc
    failure = None
    try:
        released = training_job_manager._leases.release_uncertain(req.job_id, lease.get("fence"))
    except (OSError, sqlite3.Error) as exc:
        released, failure = False, exc
    outcome = {"by": "operator", "actor": actor, "fence": lease.get("fence"), "at": time.time()}
    if not released:
        outcome["reason"] = (f"the reservation store could not be written: {failure}" if failure is not None
                             else "the reservation changed or was already returned before it was released")
    try:
        link.store.record_event(req.job_id, "reservation_released" if released else "reservation_release_refused", outcome)
        outcome_recorded = True
    except _LEDGER_ERRORS as exc:
        # The confirmation is on record either way; it alone never claims that anything was released.
        logger.warning("Job ledger could not record the release outcome of %s: %s", req.job_id, exc)
        outcome_recorded = False
    if failure is not None:
        raise HTTPException(503, f"The reservation could not be released ({failure}); nothing was released")
    if not released:
        raise HTTPException(409, "The reservation changed while it was being released; reload and check again")
    return {"job_id": req.job_id, "released": True, "liveness": liveness, "recorded_by": actor,
            "outcome_recorded": outcome_recorded}


@router.post("/reconnect")
def reconnect_training(req: TrainingStopRequest,request:Request=None):
    if not req.job_id:
        raise HTTPException(status_code=422, detail="job_id is required to reconnect an owned worker")
    if not _record_in_request_project(training_job_manager.get_job(req.job_id),request):raise HTTPException(404,'Job unavailable in this project')
    current = training_job_manager.get_job(req.job_id)
    record = (training_job_manager.reconnect_remote_job(req.job_id) if current and current.remote_profile_id
              else training_job_manager.reconnect_local_job(req.job_id))
    if record is None:
        raise HTTPException(status_code=409, detail="This job cannot be reconnected")
    return {"job_id": record.job_id, "status": record.status, "compute_profile_id": record.remote_profile_id,
            "optimizer_resume": False}


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
        source = project.get('source_dataset_dir'); task = receipt.get('task') if receipt else None
        if (not source or not receipt or receipt.get('job_id') != job_id
                or task not in ('classification','segmentation','detection','anomaly','patch_classification')
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
                or (manifest['task'] != task and not (task=='patch_classification' and binding.get('family_task')==task))
                or binding.get('labelset_id') != labelset or original_binding.get('labelset_id') != labelset
                or binding.get('dataset_fingerprint') != manifest['dataset_fingerprint']
                or receipt['dataset_fingerprint'] != binding.get('dataset_fingerprint')):
            return None
        if task=='patch_classification':
            from backend.api.routes_patch_classification import _owned
            prepared=_owned(project,receipt['dataset_path'])
            if tuple(prepared.classes)!=tuple(payload.get('classes',[])) or metadata.get('dataset_path')!=receipt['dataset_path']:
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


def _job_device_name(record):
    if record.remote_device_name:return record.remote_device_name
    if record.status!='completed':return (record.launch_spec or {}).get('device')
    try:
        output=Path(record.output_dir)
        receipt=json.loads((output/'job_receipt.json').read_text(encoding='utf-8'))
        metadata=json.loads((output/'model_meta.json').read_text(encoding='utf-8'))
        value=receipt.get('device') or metadata.get('device')
        return value if isinstance(value,str) and re.fullmatch(r'cpu|mps|cuda(?::[0-9]+)?',value) else None
    except (ValueError,OSError,TypeError):return None

@router.get("/status")
def get_training_status(job_id: Optional[str] = Query(None), request: Request = None):
    """Retrieves current training status and progress for polling fallbacks."""
    if job_id:
        record = training_job_manager.get_job(job_id)
        if record is None and request is not None:
            record = _completed_receipt_record(job_id, request)
        if record is None:
            record = next(iter(_ledger_readback(request, job_id)), None)
    else:
        record = training_job_manager.get_active_job()

    if not _record_in_request_project(record,request):
        return {
            "job_id": None,
            "status": "idle",
            "is_training": False,
        }

    duration = time.time() - record.start_time if record.status in training_job_manager.ACTIVE_STATES else 0.0
    try:
        reserved = {row['job_id'] for row in training_job_manager._leases.list()}
    except (OSError, sqlite3.Error):
        reserved = None
    queue = None
    if record.status == 'queued':
        try:
            from backend.contracts.context import get_project_context
            project_key = request.app.state.context_registry.project_key(get_project_context(request)) if request else None
            queue = next((row for row in JobScheduler(job_ledger(), training_job_manager._leases).queue_view(project_key)
                          if row['job_id'] == record.job_id), None)
        except _LEDGER_ERRORS:
            pass  # The current queue order is unknown; do not invent a position.

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
        "device_name": _job_device_name(record),
        "observation": _job_observation(record, reserved),
        "queue_position": queue['position'] if queue else None,
        "wait_reason": queue.get('wait_reason') if queue else None,
    }


@router.get("/queue")
def training_queue(request: Request):
    """The current project's waiting training jobs: position, priority, budget and why each waits."""
    from backend.contracts.context import get_project_context
    project_key = request.app.state.context_registry.project_key(get_project_context(request))
    try:
        rows = JobScheduler(job_ledger(), training_job_manager._leases).queue_view(project_key)
    except _LEDGER_ERRORS as exc:
        raise HTTPException(503, f"The job ledger could not be read: {exc}") from exc
    return {"jobs": rows}


def _job_observation(record: "JobRecord", reserved: Optional[set]) -> Dict[str, Any]:
    """What happened to the job from evidence only, with one next action (S1-04): run journal, cancel intent, reservation."""
    from dataclasses import asdict
    from backend.engine.job_observation import cancellation_evidence, classify_observation
    journal: Dict[str, Any] = {}
    unreadable = False
    for name in ("local_job.json", "remote_job.json"):
        path = Path(record.output_dir) / name if record.output_dir else None
        if path is not None and path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                unreadable = True
                continue
            journal = loaded if isinstance(loaded, dict) else {}
            break
    if not journal and not getattr(record, "remote_profile_id", None):
        from backend.engine.local_training_worker import _index
        copy = _index() / f"{record.job_id}.json"  # the copy a local worker's journal keeps in the user data folder
        try:
            loaded = json.loads(copy.read_text(encoding='utf-8')) if copy.is_file() else {}
            if isinstance(loaded, dict) and loaded.get("job_id") == record.job_id:
                journal = loaded
        except (OSError, ValueError):
            unreadable = True
    try:
        intent = job_ledger().cancel_intent(record.job_id)
    except _LEDGER_ERRORS:
        intent = None
    evidence = cancellation_evidence(journal, intent, None if reserved is None else record.job_id in reserved)
    # A failed local job stores the error catalog payload: its text is in details/message_en, its kind in error_code.
    payload = record.error if isinstance(record.error, dict) else {"message": record.error} if record.error else {}
    error = " | ".join(str(payload[key]) for key in ("message", "details", "message_en") if payload.get(key)) or None
    observed = classify_observation(
        record.status, exit_code=journal.get("worker_exit_code"), error=error, error_code=payload.get("error_code"),
        connection_lost=record.status == "disconnected", remote=getattr(record, "remote_profile_id", None) is not None,
        app_restarted="restart" in (error or ""), cancel=evidence, cancel_reason=(intent or {}).get("reason"))
    # Without any run journal no worker of this job was ever recorded (it failed while preparing, or its folder is gone),
    # so there is no process exit to confirm; a journal that exists but cannot be read leaves that unknown (None).
    worker_recorded = True if journal else None if unreadable else False
    return {**asdict(observed), "cancel": asdict(evidence), "worker_recorded": worker_recorded}


@router.get("/jobs")
def list_training_jobs(request:Request=None):
    """Expose active and queued jobs so clients can reconnect by original ID."""
    queue_position = 0
    jobs = []
    try:
        reserved = {row["job_id"] for row in training_job_manager._leases.list()}
    except (OSError, sqlite3.Error):
        reserved = None  # the reservation table could not be read: release is not claimed either way
    records = [record for record in training_job_manager.list_jobs() if _record_in_request_project(record,request)]
    known = {record.job_id for record in records}
    records += [record for record in _ledger_readback(request)
                if record.job_id not in known and _record_in_request_project(record,request)]
    for record in records:
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
            "wait_reason": _ledger_wait_reason(record.job_id) if record.status == "queued" else None,
            "output_dir": record.output_dir,
            "observation": _job_observation(record, reserved),
        })
    return {"jobs": jobs}
