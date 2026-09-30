"""Local ownership and verification for detached SSH training runs.

The desktop daemon stays the only public API.  A remote worker receives a
content-addressed snapshot and returns artifacts that are verified before a
local training receipt is allowed to say ``completed``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional

from backend.remote.profiles import ComputeProfile
from backend.remote.snapshot import build_snapshot
from backend.remote.ssh_transport import SSHTransport

logger = logging.getLogger("vision_ai_studio.remote_coordinator")

PROTOCOL_VERSION = 1
POLL_INTERVAL_SECONDS = 2.0
START_TIMEOUT_SECONDS = 120.0


class RemoteDisconnected(RuntimeError):
    """The worker outcome is unknown because SSH stopped responding."""


class ArtifactValidationError(ValueError):
    """The remote result is known, but cannot be registered locally."""


class RemoteWorkerExited(RuntimeError):
    """The remote process ended before it published a status receipt."""


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _journal_index() -> Path:
    root = Path(os.environ.get("VISION_AI_STUDIO_USER_DATA_DIR") or Path.home() / ".modu_vision")
    return root / "remote_jobs"


def _save_journal(journal: dict[str, Any]) -> None:
    output = Path(journal["output_dir"])
    _atomic_json(output / "remote_job.json", journal)
    _atomic_json(_journal_index() / f"{journal['job_id']}.json", journal)


def persist_queued_remote_job(record: Any, profile: ComputeProfile, launch_spec: dict[str, Any]) -> None:
    """Write the launch intent before a slot is granted or a worker can start."""
    _save_journal({
        "protocol_version": PROTOCOL_VERSION,
        "job_id": record.job_id,
        "operation": "train",
        "state": "queued",
        "enqueued_at": record.start_time,
        "profile": profile.model_dump(),
        "task": record.task,
        "preset": record.preset,
        "output_dir": record.output_dir,
        "dataset_path": record.dataset_path,
        "source_dataset_path": record.source_dataset_path,
        "dataset_fingerprint": record.dataset_fingerprint,
        "split_manifest_root": record.split_manifest_root,
        "launch_spec": launch_spec,
    })


def _bundle_backend(destination: Path) -> Path:
    """Bundle only application Python source; no user data or local secrets."""
    root = Path(__file__).resolve().parents[2]
    backend = root / "backend"
    output = destination / "remote_code.tar.gz"
    with tarfile.open(output, "w:gz") as archive:
        for source in sorted(backend.rglob("*.py")):
            relative = source.relative_to(root)
            if "tests" in relative.parts or "__pycache__" in relative.parts or source.is_symlink():
                continue
            archive.add(source, arcname=relative.as_posix(), recursive=False)
    return output


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_path(profile: ComputeProfile, job_id: str, relative: str) -> str:
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"Invalid remote run path: {relative}")
    return str(PurePosixPath(profile.remote_root) / "runs" / job_id / path)


def _remote_json(transport: SSHTransport, profile: ComputeProfile, path: str) -> Optional[dict[str, Any]]:
    try:
        result = transport.exec(profile, ["cat", path], timeout=30)
    except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        raise RemoteDisconnected(str(exc)) from exc
    if result.returncode == 255:
        raise RemoteDisconnected(result.stderr or "SSH connection lost")
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed remote JSON at {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Unexpected remote JSON at {path}")
    return value


def _copy_artifacts(
    transport: SSHTransport, profile: ComputeProfile,
    journal: dict[str, Any], output_dir: Path,
) -> None:
    job_id = journal["job_id"]
    manifest = _remote_json(transport, profile, _remote_path(profile, job_id, "artifacts.json"))
    if manifest is None:
        raise ArtifactValidationError("Remote worker completed without an artifact manifest")
    if (manifest.get("protocol_version") != PROTOCOL_VERSION
            or manifest.get("job_id") != job_id
            or manifest.get("operation") != "train"
            or manifest.get("input_manifest_sha256") != journal["input_manifest_sha256"]):
        raise ArtifactValidationError("Remote artifact manifest does not match this training run")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ArtifactValidationError("Remote artifact list is missing")
    by_name = {entry.get("path"): entry for entry in artifacts if isinstance(entry, dict)}
    required = {"outputs/best_model.pt", "outputs/model_meta.json"}
    if not required.issubset(by_name):
        raise ArtifactValidationError("Remote checkpoint or model metadata is missing")

    staged: dict[str, Path] = {}
    try:
        for relative in sorted(required):
            entry = by_name[relative]
            expected_hash = entry.get("sha256")
            expected_size = entry.get("size")
            if not isinstance(expected_hash, str) or len(expected_hash) != 64 or not isinstance(expected_size, int) or expected_size <= 0:
                raise ArtifactValidationError(f"Invalid remote artifact metadata: {relative}")
            target = output_dir / PurePosixPath(relative).name
            with tempfile.NamedTemporaryFile(dir=output_dir, prefix=f".{target.name}-", delete=False) as handle:
                staged_path = Path(handle.name)
            staged[relative] = staged_path
            try:
                transport.download(profile, f"runs/{job_id}/{relative}", staged_path)
            except Exception as exc:
                raise RemoteDisconnected(f"Could not download {relative}: {exc}") from exc
            if staged_path.stat().st_size != expected_size or _sha256(staged_path) != expected_hash:
                raise ArtifactValidationError(f"Remote artifact hash mismatch: {relative}")
        metadata = json.loads(staged["outputs/model_meta.json"].read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or metadata.get("task") != journal["task"]:
            raise ArtifactValidationError("Remote model metadata does not match the requested task")
        expected_binding = (journal.get("launch_spec") or {}).get("dataset_binding")
        if expected_binding and metadata.get("training_provenance") != expected_binding:
            raise ArtifactValidationError("Remote checkpoint provenance differs from the pinned training version")
        for relative, staged_path in staged.items():
            os.replace(staged_path, output_dir / PurePosixPath(relative).name)
        _atomic_json(output_dir / "remote_artifacts.json", manifest)
    finally:
        for staged_path in staged.values():
            staged_path.unlink(missing_ok=True)


def _monitor(record: Any, profile: ComputeProfile, transport: SSHTransport, journal: dict[str, Any]) -> dict[str, Any]:
    job_id = journal["job_id"]
    status_path = _remote_path(profile, job_id, "status.json")
    output = Path(journal["output_dir"])
    started = time.monotonic()
    cancellation_sent = False
    missing_status_polls = 0
    while True:
        if record.preparation_cancel.is_set() and not cancellation_sent:
            try:
                transport.touch_cancel(profile, job_id)
            except Exception as exc:
                raise RemoteDisconnected(f"Could not confirm remote cancellation: {exc}") from exc
            cancellation_sent = True
            record.phase = "stopping"
        status = _remote_json(transport, profile, status_path)
        if status is None:
            missing_status_polls += 1
            handle = journal.get("remote_handle")
            if missing_status_polls >= 2 and isinstance(handle, str) and handle:
                try:
                    running = transport.is_running(profile, job_id, handle)
                except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
                    raise RemoteDisconnected(f"Could not check remote worker: {exc}") from exc
                if running is False:
                    raise RemoteWorkerExited(
                        "Remote worker exited before publishing status; inspect the run's worker.log"
                    )
            if time.monotonic() - started > START_TIMEOUT_SECONDS:
                raise RuntimeError("Remote worker did not publish its status in time")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if status.get("protocol_version") != PROTOCOL_VERSION or status.get("job_id") != job_id or status.get("operation") != "train":
            raise ValueError("Remote status belongs to a different run or protocol")
        state = status.get("status")
        if state not in {"queued", "preparing", "running", "stopping", "syncing", "completed", "aborted", "failed"}:
            raise ValueError(f"Unknown remote worker status: {state}")
        record.phase = "syncing" if state == "completed" else state
        for attr in ("current_epoch", "total_epochs", "current_step", "total_steps"):
            value = status.get(attr)
            if isinstance(value, int) and value >= 0:
                setattr(record, attr, value)
        for attr in ("train_loss", "val_loss", "best_metric"):
            value = status.get(attr)
            if isinstance(value, (int, float)):
                setattr(record, attr, float(value))
        if isinstance(status.get("device"), str):
            record.remote_device_name = status["device"]
        if isinstance(status.get("loss_history"), list):
            record.loss_history = status["loss_history"][-500:]
        if isinstance(status.get("metrics"), dict):
            record.metrics = status["metrics"]
        if state == "completed":
            _copy_artifacts(transport, profile, journal, output)
            # The manager still has to publish its local provenance receipt.
            # If the app exits in between, a restart can verify/download again.
            journal["state"] = "artifacts_verified"
            _save_journal(journal)
            return {"status": "completed", "best_metric": record.best_metric}
        if state in ("aborted", "failed"):
            journal["state"] = state
            _save_journal(journal)
            return {"status": state, "error": status.get("error")}
        time.sleep(POLL_INTERVAL_SECONDS)


def run_remote_training(
    record: Any,
    profile: ComputeProfile,
    *,
    prepare_dataset: Optional[Callable[[Any], Any]] = None,
    config_overrides: Optional[dict[str, Any]] = None,
    device: Optional[str] = None,
    transport: Optional[SSHTransport] = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Stage once, launch once, then follow the job-owned remote receipt."""
    transport = transport or SSHTransport()
    output = Path(record.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    journal_path = output / "remote_job.json"
    launched = resume
    journal: dict[str, Any] = {}
    try:
        if resume:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if journal.get("job_id") != record.job_id or journal.get("profile") != profile.model_dump():
                raise ValueError("Remote journal does not match this job and server")
            record.dataset_path = journal["dataset_path"]
        else:
            if journal_path.is_file():
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                if (journal.get("job_id") != record.job_id
                        or journal.get("profile") != profile.model_dump()
                        or journal.get("state") != "queued"):
                    raise ValueError("Remote launch intent does not match this job and server")
            else:
                journal = {
                    "protocol_version": PROTOCOL_VERSION,
                    "job_id": record.job_id,
                    "operation": "train",
                    "profile": profile.model_dump(),
                    "task": record.task,
                    "preset": record.preset,
                    "output_dir": str(output),
                    "dataset_path": record.dataset_path,
                    "source_dataset_path": record.source_dataset_path,
                    "dataset_fingerprint": record.dataset_fingerprint,
                }
            journal["state"] = "preparing"
            _save_journal(journal)
            record.phase = "preparing"
            from backend.engine.training_provenance import validate_training_binding
            validate_training_binding(getattr(record, "dataset_binding", None))
            if prepare_dataset is not None:
                prepare_dataset(record.preparation_cancel)
            if record.preparation_cancel.is_set():
                journal["state"] = "aborted"
                _save_journal(journal)
                return {"status": "aborted"}
            snapshot = build_snapshot(
                Path(record.dataset_path), output / "remote_snapshot", record.preparation_cancel,
                exclude_relative_paths=frozenset({"source_manifest.json"})
                if record.task in ("segmentation", "detection") else frozenset(),
            )
            validate_training_binding(getattr(record, "dataset_binding", None))
            if record.preparation_cancel.is_set():
                journal["state"] = "aborted"
                _save_journal(journal)
                return {"status": "aborted"}
            record.dataset_path = str(snapshot.data_path)
            code_archive = _bundle_backend(output)
            journal.update({
                "state": "prepared",
                "dataset_path": record.dataset_path,
                "input_manifest_sha256": snapshot.manifest_sha256,
                "snapshot_archive_sha256": snapshot.archive_sha256,
            })
            _save_journal(journal)
            record.phase = "transferring"
            job_id = record.job_id
            spec = {
                "protocol_version": PROTOCOL_VERSION,
                "job_id": job_id,
                "operation": "train",
                "task": record.task,
                "preset": record.preset,
                "config_overrides": config_overrides or {},
                "device": None if device in ("auto", "mps") else device,
                "snapshot_archive": "snapshot.tar.gz",
                "input_manifest_sha256": snapshot.manifest_sha256,
            }
            if getattr(record, "dataset_binding", None): spec["dataset_binding"] = record.dataset_binding
            parent_transfer = None
            if getattr(record, "warm_start", None):
                from backend.engine.warm_start import portable_parent
                spec["warm_start"] = portable_parent(record.warm_start, output / "warm_start_transfer")
                parent_transfer = (output / "warm_start_transfer" / "parent.pt", "parent.pt")
            spec_path = output / "remote_spec.json"
            _atomic_json(spec_path, spec)
            transfers = (
                (snapshot.archive_path, "snapshot.tar.gz"),
                (code_archive, "code.tar.gz"),
                (spec_path, "spec.json"),
            )
            if parent_transfer: transfers = transfers + (parent_transfer,)
            record.total_bytes = sum(source.stat().st_size for source, _ in transfers)
            for source, target in transfers:
                if record.preparation_cancel.is_set():
                    journal["state"] = "aborted"
                    _save_journal(journal)
                    return {"status": "aborted"}
                transport.upload(profile, source, f"runs/{job_id}/{target}", cancel=record.preparation_cancel)
                record.transferred_bytes += source.stat().st_size
            if record.preparation_cancel.is_set():
                journal["state"] = "aborted"
                _save_journal(journal)
                return {"status": "aborted"}
            code_dir = _remote_path(profile, job_id, "code")
            remote_archive = _remote_path(profile, job_id, "code.tar.gz")
            mkdir = transport.exec(profile, ["mkdir", "-p", code_dir])
            if mkdir.returncode != 0:
                raise RuntimeError(f"Could not create isolated remote code directory: {mkdir.stderr}")
            unpack = transport.exec(profile, ["tar", "-xzf", remote_archive, "-C", code_dir])
            if unpack.returncode != 0:
                raise RuntimeError(f"Could not unpack remote worker code: {unpack.stderr}")
            journal["state"] = "launching"
            _save_journal(journal)
            spec_remote = _remote_path(profile, job_id, "spec.json")
            # An SSH timeout here cannot prove whether the detached worker
            # started. Never schedule a duplicate run after this point.
            launched = True
            handle = transport.launch(profile, ["-m", "backend.remote.worker", "train", "--spec", spec_remote], job_id)
            journal["state"] = "launched"
            journal["remote_handle"] = handle
            _save_journal(journal)
            record.phase = "running"
        return _monitor(record, profile, transport, journal)
    except RemoteDisconnected as exc:
        logger.warning("Remote run %s disconnected: %s", record.job_id, exc)
        record.phase = "disconnected"
        return {"status": "disconnected", "error": str(exc)}
    except ArtifactValidationError as exc:
        journal["state"] = "failed"
        journal["verification_error"] = str(exc)
        _save_journal(journal)
        return {"status": "failed", "error": str(exc)}
    except RemoteWorkerExited as exc:
        journal["state"] = "failed"
        journal["error"] = str(exc)
        _save_journal(journal)
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:
        if record.preparation_cancel.is_set() and not launched:
            if journal_path.is_file():
                saved = json.loads(journal_path.read_text(encoding="utf-8"))
                saved["state"] = "aborted"
                _save_journal(saved)
            return {"status": "aborted"}
        if launched:
            logger.warning("Remote run %s outcome needs reconciliation: %s", record.job_id, exc)
            record.phase = "disconnected"
            return {"status": "disconnected", "error": str(exc)}
        logger.exception("Could not prepare remote run %s", record.job_id)
        if journal_path.is_file():
            saved = json.loads(journal_path.read_text(encoding="utf-8"))
            saved["state"] = "failed"
            saved["error"] = str(exc)
            _save_journal(saved)
        return {"status": "failed", "error": str(exc)}


def reconnect_remote_training(record: Any, *, transport: Optional[SSHTransport] = None) -> dict[str, Any]:
    journal = json.loads((Path(record.output_dir) / "remote_job.json").read_text(encoding="utf-8"))
    profile = ComputeProfile.model_validate(journal["profile"])
    return run_remote_training(record, profile, transport=transport, resume=True)


def make_remote_runner(profile: ComputeProfile, launch_spec: Optional[dict[str, Any]]) -> Callable[[Any], dict[str, Any]]:
    """Rebuild a queued launch from serializable inputs after daemon restart."""
    def runner(record: Any) -> dict[str, Any]:
        journal_path = Path(record.output_dir) / "remote_job.json"
        if journal_path.is_file():
            state = json.loads(journal_path.read_text(encoding="utf-8")).get("state")
            if state in ("launching", "launched", "artifacts_verified", "completed"):
                return reconnect_remote_training(record)
            if state != "queued":
                raise ValueError(f"Remote run cannot be launched from journal state {state!r}")
        if launch_spec is None:
            raise ValueError("Queued run is missing its durable launch specification")

        preparation = launch_spec.get("preparation", "none")
        prepare_dataset = None
        if preparation == "remote_classification":
            from backend.remote.preparation import prepare_remote_classification

            def prepare_dataset(cancel):
                prepare_remote_classification(Path(launch_spec["prepare_source_path"]), Path(record.dataset_path), cancel)
        elif preparation in ("labelme_segmentation", "labelme_detection"):
            if preparation == "labelme_detection":
                from backend.engine.labelme_detection_preparation import prepare_labelme_detection as prepare_labelme
            else:
                from backend.engine.labelme_preparation import prepare_labelme_segmentation as prepare_labelme

            def prepare_dataset(cancel):
                prepare_labelme(
                    Path(launch_spec["prepare_source_path"]), Path(record.dataset_path),
                    image_size=int(launch_spec["image_size"]),
                    assignments=launch_spec.get("assignments", {}),
                    require_complete_assignments=bool(launch_spec.get("require_complete_assignments")),
                    cancellation_requested=cancel.is_set,
                    annotation_root=Path(launch_spec["annotation_root"]),
                )
        elif preparation != "none":
            raise ValueError(f"Unknown remote training preparation: {preparation}")

        if launch_spec.get("warm_start"):
            from backend.engine.warm_start import WarmStartParent
            parent = dict(launch_spec["warm_start"]); parent["checkpoint_path"] = Path(parent["checkpoint_path"]); parent["classes"] = tuple(parent["classes"])
            record.warm_start = WarmStartParent(**parent)
        record.dataset_binding = launch_spec.get("dataset_binding")
        return run_remote_training(
            record, profile, prepare_dataset=prepare_dataset,
            config_overrides=launch_spec.get("config_overrides") or {},
            device=launch_spec.get("device"),
        )

    return runner


def mark_remote_journal_terminal(output_dir: Path, job_id: str, status: str) -> None:
    path = Path(output_dir) / "remote_job.json"
    if not path.is_file():
        return
    journal = json.loads(path.read_text(encoding="utf-8"))
    if journal.get("job_id") != job_id or status not in ("completed", "aborted", "failed"):
        raise ValueError("Terminal receipt does not match remote journal")
    journal["state"] = status
    _save_journal(journal)


def recover_remote_jobs(manager: Any) -> None:
    """Reattach launched runs first, then reclaim queued launch intents."""
    index = _journal_index()
    if not index.is_dir():
        return
    paths = sorted(index.glob("job_*.json"))
    journals = []
    for path in paths:
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
            if journal.get("operation") == "train":
                journals.append((path, journal))
        except (OSError, ValueError):
            logger.exception("Could not read remote training journal %s", path)
    def recovery_order(item):
        enqueued_at = item[1].get("enqueued_at")
        if not isinstance(enqueued_at, (int, float)):
            enqueued_at = 0
        return (item[1].get("state") == "queued", enqueued_at, str(item[0]))

    journals.sort(key=recovery_order)
    for path, journal in journals:
        try:
            if journal.get("state") in ("preparing", "prepared", "transferring"):
                # No launch was attempted. Reusing a half-prepared snapshot is
                # unsafe; make the interruption explicit and release its slot.
                journal["state"] = "failed"
                journal["error"] = "The desktop app closed before the remote worker was launched"
                _save_journal(journal)
            output = Path(journal["output_dir"])
            if manager.get_job(journal["job_id"]):
                continue
            profile = ComputeProfile.model_validate(journal["profile"])
            if journal.get("state") == "queued" and not isinstance(journal.get("launch_spec"), dict):
                journal["state"] = "failed"
                journal["error"] = "Queued run is missing its launch specification"
                _save_journal(journal)
            receipt_exists = (output / "job_receipt.json").is_file()
            if journal.get("state") in ("aborted", "failed") or (journal.get("state") == "completed" and receipt_exists):
                from backend.api.routes_training import JobRecord, _write_job_receipt

                record = JobRecord(
                    job_id=journal["job_id"], task=journal["task"], preset=journal["preset"],
                    dataset_path=journal["dataset_path"], output_dir=str(output),
                    status=journal["state"], phase=journal["state"], remote_profile_id=profile.id,
                    remote_profile=profile,
                    source_dataset_path=journal.get("source_dataset_path"),
                    dataset_fingerprint=journal.get("dataset_fingerprint"),
                    error={"message": str(journal["error"])} if journal.get("error") else None,
                )
                if receipt_exists:
                    saved = json.loads((output / "job_receipt.json").read_text())
                    if saved.get("job_id") == record.job_id:
                        for key in ("current_epoch", "total_epochs", "current_step", "total_steps", "best_metric", "metrics", "loss_history", "error"):
                            if key in saved: setattr(record, key, saved[key])
                        record.train_loss = saved.get("current_train_loss")
                        record.val_loss = saved.get("current_val_loss")
                        record.phase = record.status
                if not receipt_exists:
                    _write_job_receipt(record)
                manager.restore_terminal_job(record)
                continue
            if receipt_exists:
                continue
            manager.start_remote_job(
                job_id=journal["job_id"], task=journal["task"],
                dataset_path=journal["dataset_path"], output_dir=str(output),
                preset=journal["preset"], remote_profile_id=profile.id,
                source_dataset_path=journal.get("source_dataset_path"),
                dataset_fingerprint=journal.get("dataset_fingerprint"),
                remote_runner=make_remote_runner(profile, journal.get("launch_spec")),
                profile=profile, launch_spec=journal.get("launch_spec"),
                split_manifest_root=journal.get("split_manifest_root"),
                recovery_state=journal["state"],
                dataset_binding=(journal.get("launch_spec") or {}).get("dataset_binding"),
            )
        except Exception:
            logger.exception("Could not reconnect remote training journal %s", path)
