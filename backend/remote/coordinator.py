"""Local ownership and verification for detached SSH training runs.

The desktop daemon stays the only public API.  A remote worker receives a
content-addressed snapshot and returns artifacts that are verified before a
local training receipt is allowed to say ``completed``.
"""

from __future__ import annotations

import hashlib
import errno
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional

from backend.remote.profiles import ComputeProfile
from backend.remote.snapshot import build_snapshot
from backend.remote.ssh_transport import SSHTransport, SSHTransportError

logger = logging.getLogger("vision_ai_studio.remote_coordinator")

PROTOCOL_VERSION = 1
POLL_INTERVAL_SECONDS = 2.0
START_TIMEOUT_SECONDS = 120.0
CANCEL_GRACE_SECONDS = 15.0
CANCEL_TERMINATE_SECONDS = 5.0
CANCEL_CONFIRM_SECONDS = 5.0
TERMINAL_EXIT_GRACE_SECONDS = 5.0
TERMINAL_EXIT_TERMINATE_SECONDS = 5.0
TERMINAL_EXIT_CONFIRM_SECONDS = 5.0
_JOURNAL_LOCK = threading.RLock()


class RemoteDisconnected(RuntimeError):
    """The worker outcome is unknown because SSH stopped responding."""


class ArtifactValidationError(ValueError):
    """The remote result is known, but cannot be registered locally."""


class RemoteWorkerExited(RuntimeError):
    """The remote process ended before it published a status receipt."""


def _confirm_owned_exit(transport, profile, run_id, handle):
    """Terminal publication precedes teardown; only owned exit frees compute."""
    started = time.monotonic()
    terminated_at = killed_at = None
    while True:
        try:
            running = transport.is_running(profile, run_id, handle) if isinstance(handle, str) and handle else None
        except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
            raise RemoteDisconnected(f'Could not confirm terminal worker exit: {exc}') from exc
        if running is False:
            return
        if running is None:
            raise RemoteDisconnected('Terminal worker ownership is uncertain; its reservation requires reconciliation')
        now = time.monotonic()
        if terminated_at is None and now - started >= TERMINAL_EXIT_GRACE_SECONDS:
            if transport.stop_owned(profile, run_id, handle, force=False) is not True:
                raise RemoteDisconnected('Could not confirm owned terminal worker cleanup')
            terminated_at = now
        elif terminated_at is not None and killed_at is None and now - terminated_at >= TERMINAL_EXIT_TERMINATE_SECONDS:
            if transport.stop_owned(profile, run_id, handle, force=True) is not True:
                raise RemoteDisconnected('Could not confirm final owned terminal worker cleanup')
            killed_at = now
        elif killed_at is not None and now - killed_at >= TERMINAL_EXIT_CONFIRM_SECONDS:
            raise RemoteDisconnected('Terminal status was published, but owned worker exit remains unconfirmed')
        time.sleep(min(POLL_INTERVAL_SECONDS, .2))


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


def _read_journal(output: Path, job_id: str) -> dict[str, Any]:
    """Read the newest matching copy when either storage volume is exhausted."""
    candidates = []
    for path in (output / 'remote_job.json', _journal_index() / f'{job_id}.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('job_id') == job_id and Path(value['output_dir']).resolve() == output.resolve():
                candidates.append(value)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not candidates:
        raise ValueError('Owned remote journal is unavailable')
    return max(candidates, key=lambda value: value.get('journal_updated_at', 0))


def _save_journal(journal: dict[str, Any]) -> None:
    with _JOURNAL_LOCK:
        output = Path(journal["output_dir"])
        path = output / "remote_job.json"
        # A cancellation can arrive from the API while the monitor holds its
        # earlier journal copy. Never erase an acknowledged cancellation.
        if path.is_file() or (_journal_index() / f"{journal['job_id']}.json").is_file():
            saved = _read_journal(output, journal['job_id'])
            if saved.get("job_id") == journal["job_id"]:
                for key, value in saved.items():
                    if key.startswith("cancel_") and key not in journal:
                        journal[key] = value
        journal['journal_updated_at'] = time.time_ns()
        errors = []
        for target in (path, _journal_index() / f"{journal['job_id']}.json"):
            try:
                _atomic_json(target, journal)
            except OSError as exc:
                errors.append(exc)
                logger.warning("Could not persist remote journal at %s: %s", target, exc)
        if len(errors) == 2:
            raise errors[0]


def request_remote_cancellation(record: Any) -> None:
    """Persist cancel intent before acknowledging it or signaling a worker."""
    with _JOURNAL_LOCK:
        path = Path(record.output_dir) / "remote_job.json"
        if not path.is_file():
            # Compatibility runners may predate the profile journal. Keep the
            # control intent durable without inventing a launch specification.
            _atomic_json(Path(record.output_dir) / 'remote_cancel.json',
                         {'job_id': record.job_id, 'cancel_requested_at': time.time()})
            return
        journal = _read_journal(Path(record.output_dir), record.job_id)
        if journal.get("job_id") != record.job_id:
            raise ValueError("Cancellation does not match the owned remote run")
        journal.setdefault("cancel_requested_at", time.time())
        _save_journal(journal)


def _terminal_journal(journal: dict[str, Any], state: str, **changes: Any) -> bool:
    """An exhausted local disk must not turn confirmed death into uncertainty."""
    journal.update(state=state, **changes)
    try:
        _save_journal(journal)
        return True
    except OSError:
        logger.exception("Could not persist confirmed terminal remote run %s", journal["job_id"])
        return False


def persist_queued_remote_job(record: Any, profile: ComputeProfile, launch_spec: dict[str, Any]) -> None:
    """Write the launch intent before a slot is granted or a worker can start."""
    _save_journal({
        "protocol_version": PROTOCOL_VERSION,
        "job_id": record.job_id,
        "operation": launch_spec.get('operation','train'),
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


def _local_pretrained_weights(task, preset, overrides):
    """Resolve fresh default weights locally so a worker never needs host paths or credentials."""
    from backend.engine.trainer import PRESET_CONFIGS
    from backend.engine.model_backbones import _dino_weights, is_dino_backbone, canonical_dino_name
    config = PRESET_CONFIGS[preset]
    model = overrides.get('model_name', config.backbone_segmentation) if task == 'segmentation' else overrides.get(
        'backbone', config.backbone_detection if task == 'detection' else config.backbone_classification)
    synthetic_anomaly = task in ('anomaly', 'anomaly_detection') and overrides.get('anomaly_method') == 'dino_synthetic'
    if synthetic_anomaly:
        model = overrides.get('anomaly_backbone', 'dinov3_vits16')
    explicit = overrides.get('pretrained_checkpoint')
    expected = overrides.get('pretrained_sha256')
    if (task in ('classification', 'patch_classification', 'segmentation') or synthetic_anomaly) and is_dino_backbone(str(model)):
        path, digest, origin = _dino_weights(canonical_dino_name(str(model)), explicit, expected)
        return path, digest, origin, canonical_dino_name(str(model))
    if task == 'detection' and str(model) in ('yolo26n', 'yolo26s'):
        from backend.engine.model_backbones import _yolo_weights
        path, digest, origin = _yolo_weights(str(model), explicit, expected)
        return path, digest, origin, model
    if explicit:
        raise ValueError('Explicit pretrained checkpoint is supported only for DINOv3 and YOLO adapters')
    return None


def _pretrained_transfer(output, task, preset, overrides, parent=None):
    options = dict(overrides or {})
    # Parent checkpoints already contain all initialized weights and their provenance.
    if parent is not None or not options.get('pretrained', True):
        options.pop('pretrained_checkpoint', None)
        return options, None, None
    receipt = _local_pretrained_weights(task, preset, options)
    if receipt is None:
        return options, None, None
    source, digest, origin, model = receipt
    target_name = 'pretrained.safetensors' if Path(source).suffix.lower() == '.safetensors' else 'pretrained.pt'
    transfer_root = Path(output) / 'pretrained_transfer'
    transfer_root.mkdir(parents=True, exist_ok=True)
    target = transfer_root / target_name
    shutil.copyfile(source, target)
    if _sha256(target) != digest:
        raise ValueError('Pretrained transfer SHA256 hash changed during copy')
    options['pretrained_checkpoint'] = target_name
    options['pretrained_sha256'] = digest
    options.pop('pretrained_origin', None)
    envelope = {'checkpoint': target_name, 'sha256': digest, 'source': origin, 'model': model}
    return options, envelope, (target, target_name)


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
            or manifest.get("operation") != journal.get('operation','train')
            or manifest.get("input_manifest_sha256") != journal["input_manifest_sha256"]):
        raise ArtifactValidationError("Remote artifact manifest does not match this training run")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ArtifactValidationError("Remote artifact list is missing")
    by_name = {entry.get("path"): entry for entry in artifacts if isinstance(entry, dict)}
    required = {'outputs/label_results.json'} if journal.get('operation')=='label' else {"outputs/best_model.pt", "outputs/model_meta.json"}
    if not required.issubset(by_name):
        raise ArtifactValidationError("Remote checkpoint or model metadata is missing")

    staged: dict[str, Path] = {}
    preserve_partial = False
    try:
        for relative in sorted(required):
            entry = by_name[relative]
            expected_hash = entry.get("sha256")
            expected_size = entry.get("size")
            if not isinstance(expected_hash, str) or len(expected_hash) != 64 or not isinstance(expected_size, int) or expected_size <= 0:
                raise ArtifactValidationError(f"Invalid remote artifact metadata: {relative}")
            target = output_dir / PurePosixPath(relative).name
            staging = output_dir / '.remote-downloads'
            staging.mkdir(mode=0o700, exist_ok=True)
            staged_path = staging / target.name
            if staged_path.is_symlink():
                raise ArtifactValidationError('A remote artifact staging path is linked')
            staged[relative] = staged_path
            try:
                transport.download(profile, f"runs/{job_id}/{relative}", staged_path)
            except Exception as exc:
                preserve_partial = True
                if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
                    raise ArtifactValidationError('Local storage capacity is insufficient to receive the completed model') from exc
                raise RemoteDisconnected(f"Could not download {relative}: {exc}") from exc
            if staged_path.stat().st_size != expected_size or _sha256(staged_path) != expected_hash:
                raise ArtifactValidationError(f"Remote artifact hash mismatch: {relative}")
        if journal.get('operation')=='label':
            labels=json.loads(staged['outputs/label_results.json'].read_text(encoding='utf-8'))
            if labels.get('job_id')!=job_id or labels.get('input_manifest_sha256')!=journal['input_manifest_sha256'] or labels.get('automatically_approved') is not False:
                raise ArtifactValidationError('Remote label candidates differ from the owned snapshot')
        else:
            metadata = json.loads(staged["outputs/model_meta.json"].read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or metadata.get("task") != journal["task"]:
                raise ArtifactValidationError("Remote model metadata does not match the requested task")
            expected_binding = (journal.get("launch_spec") or {}).get("dataset_binding")
            if expected_binding and metadata.get("training_provenance") != expected_binding:
                raise ArtifactValidationError("Remote checkpoint provenance differs from the pinned training version")
        for relative, staged_path in staged.items():
            os.replace(staged_path, output_dir / PurePosixPath(relative).name)
        if journal['task'] in {'rotation','ocr','rotated_detection','enhancement','defect_gan','patch_classification'}:
            # Verify the received bytes first, then record the deterministic
            # relocation separately so reopened native engines can read them.
            remote_root=f'{profile.remote_root}/runs/{job_id}/input/data'
            local_root=str(output_dir/'remote_snapshot'/'data')
            def relocate(value):
                if isinstance(value,dict):return {key:relocate(child) for key,child in value.items()}
                if isinstance(value,list):return [relocate(child) for child in value]
                if isinstance(value,str) and (value==remote_root or value.startswith(remote_root+'/')):return local_root+value[len(remote_root):]
                return value
            metadata_path=output_dir/'model_meta.json';metadata=relocate(json.loads(metadata_path.read_text(encoding='utf-8')))
            import torch
            checkpoint=output_dir/'best_model.pt';payload=relocate(torch.load(checkpoint,map_location='cpu',weights_only=True))
            temporary=checkpoint.with_suffix('.relocated');torch.save(payload,temporary);os.replace(temporary,checkpoint)
            metadata['checkpoint_sha256']=_sha256(checkpoint);_atomic_json(metadata_path,metadata)
            _atomic_json(output_dir/'remote_received_artifacts.json',manifest)
            manifest={**manifest,'artifacts':[{'path':row['path'],'size':(output_dir/PurePosixPath(row['path']).name).stat().st_size,
                'sha256':_sha256(output_dir/PurePosixPath(row['path']).name),'received_sha256':row['sha256']} for row in manifest['artifacts']],
                'relocation':{'remote_dataset_root':remote_root,'local_dataset_root':local_root}}
        _atomic_json(output_dir / "remote_artifacts.json", manifest)
    finally:
        for staged_path in staged.values():
            if not preserve_partial:
                staged_path.unlink(missing_ok=True)


def _monitor(record: Any, profile: ComputeProfile, transport: SSHTransport, journal: dict[str, Any]) -> dict[str, Any]:
    job_id = journal["job_id"]
    status_path = _remote_path(profile, job_id, "status.json")
    output = Path(journal["output_dir"])
    started = time.monotonic()
    missing_status_polls = 0
    while True:
        # Re-read durable intent after an API request or daemon restart.
        path = output / "remote_job.json"
        if path.is_file():
            saved = _read_journal(output, job_id)
            if saved.get("job_id") == job_id:
                journal.update({key: value for key, value in saved.items() if key.startswith("cancel_")})
        if record.preparation_cancel.is_set() and not journal.get("cancel_requested_at"):
            request_remote_cancellation(record)
            journal["cancel_requested_at"] = time.time()
        if journal.get("cancel_requested_at") and not journal.get("cancel_signal_sent_at"):
            try:
                transport.touch_cancel(profile, job_id)
            except Exception as exc:
                raise RemoteDisconnected(f"Could not confirm remote cancellation: {exc}") from exc
            journal["cancel_signal_sent_at"] = time.time()
            _save_journal(journal)
            record.phase = "stopping"
        status = _remote_json(transport, profile, status_path)
        handle = journal.get("remote_handle")
        if not handle and status is not None and hasattr(transport, 'recover_handle'):
            spec_hash = next((row.get('sha256') for row in journal.get('transfers', []) if row.get('target') == 'spec.json'), None)
            handle = transport.recover_handle(profile, job_id, job_id=job_id,
                operation=journal.get('operation', 'train'), spec_sha256=spec_hash)
            if handle:
                journal.update(remote_handle=handle, state='launched', launch_acknowledgment_recovered=True)
                _save_journal(journal)
        state = status.get("status") if status else None
        if (journal.get("cancel_requested_at") and not journal.get("cancel_acknowledged_at")
                and state in {"stopping", "aborted"} and status.get("job_id") == job_id):
            # The worker reports 'stopping' (or an immediate 'aborted') only after it has seen the cancel file: that is
            # its acknowledgement, recorded apart from the signals sent and from the confirmed exit.
            acknowledged = status.get("cancel_acknowledged_at")
            journal["cancel_acknowledged_at"] = acknowledged if isinstance(acknowledged, (int, float)) else time.time()
            try:
                _save_journal(journal)
            except OSError:
                logger.exception("Could not record the cancel acknowledgement of %s", job_id)
        if state not in {"completed", "aborted", "failed"}:
            if status is None:
                missing_status_polls += 1
            if status is not None or missing_status_polls >= 2:
                try:
                    running = transport.is_running(profile, job_id, handle) if isinstance(handle, str) and handle else None
                except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
                    raise RemoteDisconnected(f"Could not check remote worker: {exc}") from exc
                if running is None:
                    raise RemoteDisconnected("The owned worker could not be checked; its reservation requires reconciliation")
                if running is False:
                    # The worker preallocates this small terminal receipt so a
                    # full disk can still publish failure over stale progress.
                    fallback = _remote_json(transport, profile, _remote_path(profile, job_id, "terminal_status.json"))
                    if fallback and fallback.get("status") in {"completed", "aborted", "failed"}:
                        status = fallback
                    elif journal.get("cancel_requested_at"):
                        persisted = _terminal_journal(journal, "aborted", worker_exit_confirmed=True)
                        result = {"status": "aborted", "worker_exit_confirmed": True}
                        if not persisted: result["journal_persisted"] = False
                        return result
                    else:
                        raise RemoteWorkerExited("Remote worker exited before publishing status; inspect the run's worker.log")
                elif journal.get("cancel_requested_at"):
                    now = time.time()
                    if not journal.get("cancel_terminate_sent_at") and now - journal["cancel_signal_sent_at"] >= CANCEL_GRACE_SECONDS:
                        if transport.stop_owned(profile, job_id, handle, force=False) is not True:
                            raise RemoteDisconnected("Could not confirm termination of the owned worker")
                        journal["cancel_terminate_sent_at"] = now
                        _save_journal(journal)
                    elif journal.get("cancel_terminate_sent_at") and not journal.get("cancel_kill_sent_at") and now - journal["cancel_terminate_sent_at"] >= CANCEL_TERMINATE_SECONDS:
                        if transport.stop_owned(profile, job_id, handle, force=True) is not True:
                            raise RemoteDisconnected("Could not confirm final cancellation of the owned worker")
                        journal["cancel_kill_sent_at"] = now
                        _save_journal(journal)
                    elif journal.get("cancel_kill_sent_at") and now - journal["cancel_kill_sent_at"] >= CANCEL_CONFIRM_SECONDS:
                        raise RemoteDisconnected("Cancellation was sent, but worker exit remains unconfirmed")
        if status is None:
            if time.monotonic() - started > START_TIMEOUT_SECONDS:
                raise RuntimeError("Remote worker did not publish its status in time")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if status.get("protocol_version") != PROTOCOL_VERSION or status.get("job_id") != job_id or status.get("operation") != journal.get('operation','train'):
            raise ValueError("Remote status belongs to a different run or protocol")
        state = status.get("status")
        if state not in {"queued", "preparing", "running", "stopping", "syncing", "completed", "aborted", "failed"}:
            raise ValueError(f"Unknown remote worker status: {state}")
        if state in {'completed', 'aborted', 'failed'}:
            journal['worker_terminal_state'] = state
            try:
                _save_journal(journal)
            except OSError:
                logger.exception('Could not save terminal publication; reconciling owned worker exit %s', job_id)
            _confirm_owned_exit(transport, profile, job_id, handle)
            journal['worker_exit_confirmed'] = True
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
            try:
                _copy_artifacts(transport, profile, journal, output)
            except OSError as exc:
                if exc.errno in (errno.ENOSPC, errno.EDQUOT):
                    raise ArtifactValidationError('Local storage capacity is insufficient to publish the completed model') from exc
                raise
            # The manager still has to publish its local provenance receipt.
            # If the app exits in between, a restart can verify/download again.
            journal["state"] = "artifacts_verified"
            _save_journal(journal)
            return {"status": "completed", "best_metric": record.best_metric, "worker_exit_confirmed": True}
        if state in ("aborted", "failed"):
            persisted = _terminal_journal(journal, state, worker_exit_confirmed=True)
            result = {"status": state, "error": status.get("error"), "worker_exit_confirmed": True}
            if not persisted: result["journal_persisted"] = False
            return result
        time.sleep(POLL_INTERVAL_SECONDS)


def _transfer_and_launch(record: Any, profile: ComputeProfile, transport: SSHTransport, journal: dict[str, Any]) -> bool:
    """Replay verified staged files; launching is the no-relaunch boundary."""
    transfers = journal.get('transfers')
    if not isinstance(transfers, list) or not transfers:
        raise ValueError('Remote transfer recovery is missing its durable file manifest')
    output = Path(journal['output_dir']).resolve()
    for row in transfers:
        source = Path(row['source'])
        if (source.is_symlink() or not source.resolve().is_relative_to(output) or not source.is_file()
                or source.stat().st_size != row['size'] or _sha256(source) != row['sha256']):
            raise ValueError('A staged remote upload changed; start a new run from the source version')
        _remote_path(profile, journal['job_id'], row['target'])
    journal['state'] = 'transferring'
    _save_journal(journal)
    record.phase = 'transferring'
    record.total_bytes = sum(row['size'] for row in transfers)
    record.transferred_bytes = 0
    for row in transfers:
        if record.preparation_cancel.is_set() or journal.get('cancel_requested_at'):
            _terminal_journal(journal, 'aborted')
            return False
        transport.upload(profile, Path(row['source']), f"runs/{journal['job_id']}/{row['target']}", cancel=record.preparation_cancel)
        record.transferred_bytes += row['size']
    if record.preparation_cancel.is_set():
        _terminal_journal(journal, 'aborted')
        return False
    job_id = journal['job_id']
    code_dir = _remote_path(profile, job_id, 'code')
    remote_archive = _remote_path(profile, job_id, 'code.tar.gz')
    for command in (['mkdir', '-p', code_dir], ['tar', '-xzf', remote_archive, '-C', code_dir]):
        result = transport.exec(profile, command)
        if result.returncode != 0:
            raise SSHTransportError(f'Could not stage isolated remote code: {result.stderr}')
    journal['state'] = 'launching'
    _save_journal(journal)
    handle = transport.launch(profile, ['-m', 'backend.remote.worker', journal.get('operation', 'train'),
                                      '--spec', _remote_path(profile, job_id, 'spec.json')], job_id)
    journal.update(state='launched', remote_handle=handle)
    _save_journal(journal)
    record.phase = 'running'
    return True


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
            journal = _read_journal(output, record.job_id)
            if journal.get("job_id") != record.job_id or ComputeProfile.model_validate(journal['profile']) != profile:
                raise ValueError("Remote journal does not match this job and server")
            record.dataset_path = journal["dataset_path"]
            if journal.get('state') == 'transferring':
                launched = False
                if not _transfer_and_launch(record, profile, transport, journal):
                    return {'status': 'aborted'}
                launched = True
        else:
            if journal_path.is_file():
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                if (journal.get("job_id") != record.job_id
                        or ComputeProfile.model_validate(journal['profile']) != profile
                        or journal.get("state") != "queued"):
                    raise ValueError("Remote launch intent does not match this job and server")
            else:
                journal = {
                    "protocol_version": PROTOCOL_VERSION,
                    "job_id": record.job_id,
                    "operation": (getattr(record,'launch_spec',None) or {}).get('operation','train'),
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
            source_snapshot=None
            if record.task in {'patch_classification','rotation','ocr','rotated_detection','enhancement','defect_gan'} and record.source_dataset_path and Path(record.source_dataset_path).resolve()!=Path(record.dataset_path).resolve():
                source_snapshot=build_snapshot(Path(record.source_dataset_path),output/'remote_source_snapshot',record.preparation_cancel)
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
                'source_snapshot_manifest_sha256':source_snapshot.manifest_sha256 if source_snapshot else None,
            })
            _save_journal(journal)
            record.phase = "transferring"
            job_id = record.job_id
            operation=journal.get('operation','train')
            remote_overrides, pretrained_envelope, pretrained_transfer = _pretrained_transfer(
                output, record.task, record.preset, config_overrides, getattr(record, 'warm_start', None)) if operation=='train' else ({},None,None)
            spec = {
                "protocol_version": PROTOCOL_VERSION,
                "job_id": job_id,
                "operation": operation,
                "task": record.task,
                "preset": record.preset,
                "config_overrides": remote_overrides,
                "device": None if device in ("auto", "mps") else device,
                "snapshot_archive": "snapshot.tar.gz",
                "input_manifest_sha256": snapshot.manifest_sha256,
            }
            if getattr(record, "dataset_binding", None): spec["dataset_binding"] = record.dataset_binding
            launch = getattr(record, 'launch_spec', None) or {}
            if launch.get('measured_candidate'):
                spec['measured_candidate'] = True
                spec['automated_training'] = launch.get('automated_training')
            local_model_id=(getattr(record,'launch_spec',None) or {}).get('local_model_id')
            if local_model_id:spec['local_model_id']=local_model_id
            if source_snapshot:
                spec['source_snapshot']={'archive':'source.tar.gz','manifest_sha256':source_snapshot.manifest_sha256,'canonical_root':record.source_dataset_path}
            if operation=='label':
                spec['labeling']=(getattr(record,'launch_spec',None) or {}).get('labeling',{})
                spec['images']=(getattr(record,'launch_spec',None) or {}).get('label_images')
            if profile.distributed_processes>1:spec['distributed']={'processes':profile.distributed_processes}
            if profile.memory_budget_mb:spec['resources']={'memory_budget_mb':profile.memory_budget_mb,'allow_sharing':profile.allow_sharing}
            if pretrained_envelope:
                spec['pretrained_weights'] = pretrained_envelope
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
            if pretrained_transfer: transfers = transfers + (pretrained_transfer,)
            if source_snapshot:transfers=transfers+((source_snapshot.archive_path,'source.tar.gz'),)
            journal['transfers'] = [{'source': str(source.absolute()), 'target': target,
                                     'size': source.stat().st_size, 'sha256': _sha256(source)} for source, target in transfers]
            journal['state'] = 'transferring'
            _save_journal(journal)
            if not _transfer_and_launch(record, profile, transport, journal):
                return {'status': 'aborted'}
            launched = True
        return _monitor(record, profile, transport, journal)
    except RemoteDisconnected as exc:
        logger.warning("Remote run %s disconnected: %s", record.job_id, exc)
        record.phase = "disconnected"
        return {"status": "disconnected", "error": str(exc)}
    except ArtifactValidationError as exc:
        persisted = _terminal_journal(journal, 'failed', verification_error=str(exc))
        result = {"status": "failed", "error": str(exc)}
        if not persisted: result['journal_persisted'] = False
        return result
    except RemoteWorkerExited as exc:
        persisted = _terminal_journal(journal, "failed", error=str(exc), worker_exit_confirmed=True)
        result = {"status": "failed", "error": str(exc)}
        if not persisted: result["journal_persisted"] = False
        return result
    except Exception as exc:
        launched = launched or journal.get('state') in {'launching', 'launched', 'artifacts_verified'}
        if record.preparation_cancel.is_set() and not launched:
            if journal_path.is_file():
                saved = json.loads(journal_path.read_text(encoding="utf-8"))
                saved["state"] = "aborted"
                _save_journal(saved)
            return {"status": "aborted"}
        if journal.get('state') == 'transferring' and isinstance(exc, (SSHTransportError, OSError, TimeoutError, subprocess.TimeoutExpired)):
            record.phase = 'disconnected'
            return {'status': 'disconnected', 'error': str(exc), 'recovery_mode': 'transfer', 'optimizer_resume': False}
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
    journal = _read_journal(Path(record.output_dir), record.job_id)
    profile = ComputeProfile.model_validate(journal["profile"])
    return run_remote_training(record, profile, transport=transport, resume=True)


def make_remote_runner(profile: ComputeProfile, launch_spec: Optional[dict[str, Any]]) -> Callable[[Any], dict[str, Any]]:
    """Rebuild a queued launch from serializable inputs after daemon restart."""
    def runner(record: Any) -> dict[str, Any]:
        journal_path = Path(record.output_dir) / "remote_job.json"
        if journal_path.is_file():
            state = _read_journal(Path(record.output_dir), record.job_id).get("state")
            if state in ("transferring", "launching", "launched", "artifacts_verified", "completed"):
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
            if journal.get("operation") in {'train','label'}:
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
            if journal.get("state") in ("preparing", "prepared") or (journal.get('state') == 'transferring' and not journal.get('transfers')):
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
                    launch_spec=journal.get('launch_spec'),
                    source_dataset_path=journal.get("source_dataset_path"),
                    dataset_fingerprint=journal.get("dataset_fingerprint"),
                    error={"message": str(journal["error"])} if journal.get("error") else None,
                )
                if receipt_exists:
                    saved = json.loads((output / "job_receipt.json").read_text(encoding='utf-8'))
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
