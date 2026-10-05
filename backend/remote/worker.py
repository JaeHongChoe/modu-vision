"""Durable, versioned compute worker invoked over an existing SSH session.

The local daemon uploads a run-local spec and snapshot archive, then invokes
``python -m backend.remote.worker train --spec <path>``. A dropped SSH channel
does not erase status: all state and hashes live in the run directory.
"""

from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
import traceback
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from backend.remote.file_replace import replace_file
from backend.remote.snapshot import (
    PROTOCOL_VERSION,
    SnapshotCancelled,
    SnapshotValidationError,
    extract_snapshot,
    verify_snapshot_tree,
)


OPERATIONS = ("train", "evaluate", "infer", "flowchart_run", "benchmark", "export", "label", "package_parity")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TASKS = {"classification", "detection", "segmentation", "anomaly"}
_TRAIN_TASKS = _TASKS | {'patch_classification','rotation','ocr','rotated_detection','enhancement','defect_gan'}
_ARTIFACTS = ("outputs/best_model.pt", "outputs/model_meta.json")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish a full JSON document with one same-directory rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}-", suffix=".tmp", delete=False) as writer:
            temporary = Path(writer.name)
            json.dump(payload, writer, ensure_ascii=False, sort_keys=True)
            writer.write("\n")
            writer.flush()
            os.fsync(writer.fileno())
        # the app reads this status many times a second; on Windows a file it holds open cannot be replaced at once
        replace_file(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class _StatusWriter:
    def __init__(self, run_dir: Path, job_id: str, operation: str = "train",
                 spec_sha256: str | None = None):
        self.path = run_dir / "status.json"
        self._lock = threading.Lock()
        self._payload: dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "job_id": job_id,
            "operation": operation,
            "spec_sha256": spec_sha256,
            "status": "preparing",
            "current_epoch": 0,
            "total_epochs": 0,
            "current_step": 0,
            "total_steps": 0,
            "train_loss": None,
            "val_loss": None,
            "loss_history": [],
            "best_metric": None,
            "device": None,
            "error": None,
            "updated_at": _timestamp(),
        }
        _atomic_json(self.path, self._payload)
        if Path('/proc/self/stat').is_file():
            from backend.remote.process_control import _entry
            token = os.environ.get('MODU_VISION_WORKER_TOKEN') or uuid4().hex
            os.environ['MODU_VISION_WORKER_TOKEN'] = token
            process = _entry(Path('/proc'), os.getpid())
            if process:
                identity = {key: process[key] for key in ('pid', 'group', 'session', 'start_ticks')}
                identity.update(token=token, job_id=job_id, run_id=run_dir.name, operation=operation,
                                protocol_version=PROTOCOL_VERSION, spec_sha256=spec_sha256)
                if os.environ.get('MODU_VISION_RUNTIME_KIND') == 'docker':
                    identifier = os.environ.get('HOSTNAME', '')
                    if re.fullmatch(r'[a-fA-F0-9]{12,64}', identifier):
                        identity.update(control_kind='docker', control_handle=identifier)
                elif process['pid'] == process['group'] == process['session']:
                    identity.update(control_kind='python', control_handle=f"{process['pid']}:{token}")
                _atomic_json(run_dir / 'worker_identity.json', identity)
        # Allocate data blocks while space is available. Terminal failure can
        # overwrite these blocks even when an atomic replacement needs space.
        self.terminal_path = run_dir / "terminal_status.json"
        with self.terminal_path.open("w", encoding="utf-8") as reserve:
            os.chmod(self.terminal_path, 0o600)
            reserve.write(json.dumps({"protocol_version": PROTOCOL_VERSION, "job_id": job_id,
                                      "operation": operation, "status": "preparing"}).ljust(4096))
            reserve.flush()
            os.fsync(reserve.fileno())

    def update(self, **changes: Any) -> dict[str, Any]:
        with self._lock:
            return self._apply(changes)

    def acknowledge_cancel(self, at: float | None = None) -> dict[str, Any]:
        """Record the worker's cancel acknowledgement (S1-04). The status becomes 'stopping' unless it is already
        terminal; the check and the write happen under the same lock, so a finishing run is never reopened."""
        with self._lock:
            changes: dict[str, Any] = {"cancel_acknowledged_at": time.time() if at is None else at}
            if self._payload.get("status") not in ("completed", "failed", "aborted"):
                changes["status"] = "stopping"
            return self._apply(changes)

    def _apply(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Write the payload with these changes; the caller holds self._lock."""
        self._payload.update(changes)
        self._payload["updated_at"] = _timestamp()
        try:
            _atomic_json(self.path, self._payload)
        except OSError as exc:
            if exc.errno not in (errno.ENOSPC, errno.EDQUOT) or self._payload["status"] not in {"failed", "aborted"}:
                raise
            terminal = {key: self._payload[key] for key in
                        ("protocol_version", "job_id", "operation", "spec_sha256", "status", "error", "updated_at")}
            terminal["status_storage"] = "reserved_blocks"
            encoded = json.dumps(terminal, ensure_ascii=True).encode("utf-8")
            if len(encoded) > 4096:
                terminal["error"] = str(terminal.get("error") or "")[:500]
                encoded = json.dumps(terminal, ensure_ascii=True).encode("utf-8")
            with self.terminal_path.open("r+b", buffering=0) as reserve:
                reserve.write(encoded.ljust(4096, b" "))
                os.fsync(reserve.fileno())
        return self._payload.copy()


class _TrainingStatusCallback:
    """Match UnifiedAutoMLTrainer's callback methods without loading torch."""

    def __init__(self, writer: _StatusWriter):
        self.writer = writer

    def on_training_start(self, config: dict[str, Any]) -> None:
        self.writer.update(total_epochs=int(config.get("epochs") or 0), device=config.get("device"))

    def on_step_end(self, step: int, total_steps: int, current_loss: float, epoch: int) -> None:
        self.writer.update(current_epoch=epoch + 1, current_step=step + 1, total_steps=total_steps,
                           train_loss=current_loss)

    def on_epoch_end(self, epoch: int, total_epochs: int, train_loss: float, val_loss: float,
                     lr: float, metrics: dict[str, float]) -> None:
        history = [row for row in self.writer._payload["loss_history"] if row["epoch"] != epoch + 1]
        history.append({"epoch": epoch + 1, "train_loss": train_loss, "val_loss": val_loss, "lr": lr})
        self.writer.update(current_epoch=epoch + 1, total_epochs=total_epochs,
                           train_loss=train_loss, val_loss=val_loss,
                           metrics=metrics, loss_history=history[-500:])

    def on_hardware_stats(self, stats: dict[str, Any]) -> None:
        self.writer.update(device_type=stats.get("device_type"), gpu_name=stats.get("gpu_name"))

    def on_training_completed(self, job_id: str, duration_seconds: float, best_metric: float,
                              model_path: str) -> None:
        # Completion is published only after artifact hashes are recorded.
        self.writer.update(best_metric=best_metric, duration_seconds=duration_seconds)

    def on_training_aborted(self, epoch: int, reason: str) -> None:
        # Keep the last displayed progress: the trainer's abort epoch is zero
        # based and can also be zero when cancelled during preparation.
        self.writer.update(status="stopping")

    def on_error(self, error: Exception, stage: str) -> None:
        self.writer.update(error=f"{stage}: {error}")


def _run_relative_file(run_dir: Path, value: Any, field_name: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError(f"Invalid {field_name}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"Unsafe {field_name}")
    path = run_dir.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(run_dir.resolve()):
        raise ValueError(f"Unsafe {field_name}")
    return path


def _read_train_spec(spec_path: Path, run_dir: Path) -> dict[str, Any]:
    data = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Worker spec must be a JSON object")
    if type(data.get("protocol_version")) is not int or data["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("Unsupported worker protocol version")
    if data.get("operation") != "train":
        raise ValueError("Worker spec operation does not match train")
    if not isinstance(data.get("job_id"), str) or not _JOB_ID_RE.fullmatch(data["job_id"]):
        raise ValueError("Invalid job_id")
    if data.get("task") not in _TRAIN_TASKS:
        raise ValueError("Invalid training task")
    if data.get("preset", "fast") not in ("fast", "precision"):
        raise ValueError("Invalid training preset")
    overrides = data.get("config_overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError("Invalid config_overrides")
    device = data.get("device")
    if device is not None and not isinstance(device, str):
        raise ValueError("Invalid device")
    digest = data.get("input_manifest_sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError("Invalid input_manifest_sha256")
    data["archive_path"] = _run_relative_file(run_dir, data.get("snapshot_archive"), "snapshot_archive")
    expected = data.get("expected_artifacts", list(_ARTIFACTS))
    if not isinstance(expected, list) or len(expected) != len(_ARTIFACTS) or set(expected) != set(_ARTIFACTS):
        raise ValueError("Invalid expected_artifacts for train")
    data["expected_artifacts"] = expected
    data["config_overrides"] = overrides
    if 'measured_candidate' in data and type(data['measured_candidate']) is not bool:
        raise ValueError('Invalid measured candidate selection')
    if data.get('measured_candidate') and data.get('distributed'):
        raise ValueError('Measured candidate DDP is unsupported')
    distributed=data.get('distributed')
    if distributed is not None:
        if not isinstance(distributed,dict) or set(distributed)-{'processes'}:raise ValueError('Invalid distributed training configuration')
        from backend.remote.distributed import validate_distributed_request
        validate_distributed_request(data['task'],device or 'cuda',distributed.get('processes'))
    weights = data.get('pretrained_weights')
    if weights is not None:
        if not isinstance(weights, dict) or weights.get('checkpoint') not in ('pretrained.pt', 'pretrained.safetensors'):
            raise ValueError('Invalid pretrained weights transfer identity')
        if overrides.get('pretrained_checkpoint') != weights['checkpoint']:
            raise ValueError('Pretrained checkpoint must match the verified run input')
        path = _run_relative_file(run_dir, weights['checkpoint'], 'pretrained checkpoint')
        digest = weights.get('sha256')
        if (path.is_symlink() or not path.is_file() or not isinstance(digest, str)
                or not _SHA256_RE.fullmatch(digest) or _sha256_file(path)[1] != digest
                or overrides.get('pretrained_sha256') != digest):
            raise ValueError('Pretrained checkpoint SHA256 hash differs from the verified run input')
        if not isinstance(weights.get('source'), str) or not weights['source']:
            raise ValueError('Pretrained transfer origin is missing')
        from backend.engine.trainer import PRESET_CONFIGS
        from backend.engine.model_backbones import canonical_dino_name
        config = PRESET_CONFIGS[data.get('preset', 'fast')]
        if data['task'] == 'anomaly':
            if overrides.get('anomaly_method') != 'dino_synthetic':
                raise ValueError('Pretrained transfer is unsupported for this anomaly method')
            selected = overrides.get('anomaly_backbone', 'dinov3_vits16')
        else:
            selected = overrides.get('model_name', config.backbone_segmentation) if data['task'] == 'segmentation' else overrides.get(
                'backbone', config.backbone_detection if data['task'] == 'detection' else config.backbone_classification)
        if weights.get('model') != canonical_dino_name(str(selected)):
            raise ValueError('Pretrained transfer architecture differs from the selected model')
        overrides['pretrained_checkpoint'] = str(path)
        overrides['pretrained_origin'] = weights['source']
    elif overrides.get('pretrained_checkpoint') or overrides.get('pretrained_origin'):
        raise ValueError('Pretrained checkpoint needs a hash-bound run input')
    return data


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as reader:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _spec_sha256(path: Path) -> str | None:
    try:
        return _sha256_file(path)[1]
    except OSError:
        return None


def _public_error(exc: Exception, run_dir: Path) -> str:
    """Keep worker filesystem locations out of status returned to the app."""
    if isinstance(exc, OSError):
        message = exc.strerror or type(exc).__name__
    else:
        message = str(exc)
    message = message.replace(str(run_dir.parent.resolve()), "<remote-runs>")
    message = re.sub(r"(?<![A-Za-z0-9])/(?:[^\s'\"<>:])+", "<remote-path>", message)
    return f"{type(exc).__name__}: {message}"[:500]


def _failed_status(status: "_StatusWriter", exc: Exception, run_dir: Path) -> dict[str, Any]:
    """Keep a private traceback for diagnosis while the API gets a redacted error."""
    try:
        log_path = run_dir / "worker_error.log"
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as log:
            log.write(traceback.format_exc())
        os.chmod(log_path, 0o600)
    except OSError:
        pass
    return status.update(status="failed", error=_public_error(exc, run_dir))


def _artifact_manifest(run_dir: Path, spec: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for relative in _ARTIFACTS:
        artifact = _run_relative_file(run_dir, relative, "artifact path")
        if not artifact.is_file() or artifact.is_symlink():
            raise FileNotFoundError(f"Required training artifact missing: {relative}")
        size, digest = _sha256_file(artifact)
        if size == 0:
            raise SnapshotValidationError(f"Required training artifact is empty: {relative}")
        rows.append({"path": relative, "size": size, "sha256": digest})
    return {
        "protocol_version": PROTOCOL_VERSION,
        "job_id": spec["job_id"],
        "operation": "train",
        "input_manifest_sha256": spec["input_manifest_sha256"],
        "artifacts": rows,
    }


def _operation_artifact_manifest(run_dir: Path, spec: dict[str, Any], operation: str,
                                 relative_paths: tuple[str, ...]) -> dict[str, Any]:
    rows = []
    for relative in relative_paths:
        path = _run_relative_file(run_dir, relative, "artifact path")
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"Required operation artifact missing: {relative}")
        size, digest = _sha256_file(path)
        if size == 0:
            raise SnapshotValidationError(f"Required operation artifact is empty: {relative}")
        rows.append({"path": relative, "size": size, "sha256": digest})
    return {
        "protocol_version": PROTOCOL_VERSION,
        "job_id": spec["job_id"],
        "operation": operation,
        "input_manifest_sha256": spec["input_manifest_sha256"],
        "artifacts": rows,
    }


class _SentinelCancel:
    def __init__(self, path: Path, event: threading.Event | None = None):
        self.path, self.event = path, event

    def is_set(self) -> bool:
        return self.path.exists() or (self.event is not None and self.event.is_set())


class _FamilyTrainer:
    """The same measured fit/heldout adapters used by native specialist training."""
    def __init__(self,**kwargs):
        self.kwargs=kwargs;self.cancel=threading.Event()
    def abort(self):self.cancel.set()
    def train(self,job_id):
        from backend.engine.automated_trials import run_measured_candidate
        args=self.kwargs;model_id=getattr(self,'local_model_id',job_id);output=Path(args['output_dir']);candidate=output/model_id
        callback=args['callback']
        callback.on_training_start({'epochs':args['config_overrides'].get('epochs',1),'device':args['device']})
        def progress(row):
            epoch=int(row.get('epoch',row.get('current_epoch',0)))
            loss=row.get('loss',row.get('train_loss',0.))
            callback.on_epoch_end(max(0,epoch-1),int(row.get('epochs',1)),loss,row.get('val_loss'),0.,{})
        result=run_measured_candidate(task=args['task'],dataset_path=args['dataset_path'],output_dir=candidate,job_id=model_id,
            config_overrides=args['config_overrides'],preset=args['preset'],device=args['device'] or 'auto',
            warm_start=args.get('warm_start'),cancel_event=self.cancel,on_progress=progress)
        for name in ('best_model.pt','model_meta.json'):
            shutil.move(str(candidate/name),str(output/name))
        return {**result,'model_path':str(output/'best_model.pt')}


def run_train(spec_path: Path, trainer_factory: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Verify input, train once, and publish a terminal run-local receipt."""
    spec_path = Path(spec_path).absolute()
    run_dir = spec_path.parent
    job_id = "unknown"
    raw: Any = None
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("job_id"), str) and _JOB_ID_RE.fullmatch(raw["job_id"]):
            job_id = raw["job_id"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    status_path = run_dir / "status.json"
    current_spec_sha256 = _spec_sha256(spec_path)
    if status_path.exists():
        try:
            previous = json.loads(status_path.read_text(encoding="utf-8"))
            if (isinstance(previous, dict) and previous.get("job_id") == job_id
                    and previous.get("operation") == "train"
                    and previous.get("protocol_version") == PROTOCOL_VERSION
                    and current_spec_sha256 is not None
                    and previous.get("spec_sha256") == current_spec_sha256
                    and isinstance(raw, dict) and raw.get("operation") == "train"):
                return previous
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        return {"status": "failed", "error": "Run already has a status for another job or operation"}
    try:
        claim = os.open(run_dir / "worker.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return {"status": "failed", "error": "Run is already claimed by a worker"}
    else:
        os.close(claim)
    status = _StatusWriter(run_dir, job_id, spec_sha256=current_spec_sha256)
    cancel_path = run_dir / "cancel"
    stop_watcher = threading.Event()
    watcher: threading.Thread | None = None
    budget = None
    trainer = None
    try:
        spec = _read_train_spec(spec_path, run_dir)
        from backend.engine.runtime_budget import RuntimeBudget
        def budget_expired():
            # Always reach the owned trainer even if recording the stop fails.
            if trainer is not None: trainer.abort()
            status.update(cancel_requested_at=time.time())
            status.acknowledge_cancel()
            status.update(stop_reason='time_limit',error='Training runtime limit exceeded')
        budget = RuntimeBudget(spec.get('max_runtime_s'),threading.Event(),
            lambda started:status.update(runtime_started_at=started,
                budget={'max_runtime_s':spec['max_runtime_s']} if spec.get('max_runtime_s') is not None else {}),
            budget_expired)
        if not spec.get('distributed'):apply_memory_budget(spec)
        if spec.get('local_model_id') is not None:
            model_id=spec['local_model_id']
            if spec['task'] not in {'rotation','ocr','rotated_detection','enhancement','defect_gan'} or not isinstance(model_id,str) or re.fullmatch('[0-9a-f]{32}',model_id) is None or spec['job_id']!='job_'+model_id:
                raise ValueError('Invalid native and remote specialist identity pair')
        status.update(job_id=spec["job_id"], device=spec.get("device"))
        if cancel_path.exists():
            return status.update(status="aborted")
        snapshot = extract_snapshot(spec["archive_path"], run_dir / "input",
                                    spec["input_manifest_sha256"], _SentinelCancel(cancel_path))
        if snapshot.manifest_sha256 != spec["input_manifest_sha256"]:
            raise SnapshotValidationError("Input manifest hash mismatch")
        aliases={}
        if spec.get('source_snapshot'):
            source_spec=spec['source_snapshot']
            if not isinstance(source_spec,dict) or set(source_spec)!={'archive','manifest_sha256','canonical_root'}:
                raise ValueError('Invalid portable original source identity')
            original=Path(source_spec['canonical_root'])
            if not original.is_absolute():raise ValueError('Canonical original root must be absolute')
            source_snapshot=extract_snapshot(_run_relative_file(run_dir,source_spec['archive'],'source snapshot'),run_dir/'source',source_spec['manifest_sha256'],_SentinelCancel(cancel_path))
            aliases[str(original)]=source_snapshot.data_path
        if cancel_path.exists():
            return status.update(status="aborted")
        output_dir = run_dir / "outputs"
        output_dir.mkdir(exist_ok=False)
        if spec.get('distributed'):
            from backend.remote.distributed import launch_distributed
            status.update(status='running')
            budget.__enter__()
            result=launch_distributed(spec_path,cancel_event=_SentinelCancel(cancel_path,budget.cancel),status_writer=status)
            budget.check()
            if result.get('status')=='aborted':return status.update(status='aborted')
            if result.get('status')!='completed':raise RuntimeError('Distributed training did not complete')
            from backend.engine.training_provenance import persist_model_binding
            persist_model_binding(output_dir,spec.get('dataset_binding'))
            manifest = _artifact_manifest(run_dir,spec)
            budget.seal()
            _atomic_json(run_dir/'artifacts.json',manifest)
            return status.update(status='completed',best_metric=result.get('best_metric'),distributed=result.get('distributed'))
        if trainer_factory is None and (spec.get('measured_candidate') or spec['task'] in {'rotation','ocr','rotated_detection','enhancement','defect_gan'}):
            trainer_factory=_FamilyTrainer
        if trainer_factory is None:
            from backend.engine.trainer import UnifiedAutoMLTrainer

            trainer_factory = UnifiedAutoMLTrainer
        callback = _TrainingStatusCallback(status)
        warm_start_args = {}
        if spec.get("warm_start"):
            from backend.engine.warm_start import restore_portable_parent
            warm_start_args["warm_start"] = restore_portable_parent(run_dir, spec["warm_start"], spec["task"])
        trainer = trainer_factory(
            task=spec["task"], dataset_path=snapshot.data_path, output_dir=output_dir,
            preset=spec.get("preset", "fast"), device=spec.get("device"), callback=callback,
            config_overrides=spec["config_overrides"], **warm_start_args,
        )
        if isinstance(trainer,_FamilyTrainer):trainer.local_model_id=spec.get('local_model_id',spec['job_id'])
        if cancel_path.exists():
            trainer.abort()
            return status.update(status="aborted", cancel_acknowledged_at=time.time())

        def watch_cancel() -> None:
            while not stop_watcher.wait(0.05):
                if cancel_path.exists():
                    status.acknowledge_cancel()  # a run that already ended keeps its terminal status
                    trainer.abort()
                    break

        watcher = threading.Thread(target=watch_cancel, name=f"Cancel-{spec['job_id']}", daemon=True)
        watcher.start()
        status.update(status="running")
        budget.__enter__()
        from backend.engine.source_aliases import source_alias_scope
        with source_alias_scope(aliases):result = trainer.train(job_id=spec["job_id"])
        budget.check()
        stop_watcher.set()
        watcher.join(timeout=1)
        if cancel_path.exists() or not isinstance(result, dict) or result.get("status") == "aborted":
            return status.update(status="aborted")
        if result.get("status") != "completed":
            raise RuntimeError(f"Trainer returned non-completed status: {result.get('status')}")
        from backend.engine.training_provenance import persist_model_binding
        persist_model_binding(output_dir, spec.get("dataset_binding"))
        if spec.get('measured_candidate'):
            import torch
            checkpoint=output_dir/'best_model.pt';payload=torch.load(checkpoint,map_location='cpu',weights_only=True)
            measurement=payload.get('measured_candidate')
            if not isinstance(measurement,dict):raise ValueError('Measured candidate receipt is unavailable')
            lineage={**(spec.get('automated_training') or {}),'metrics':measurement['metrics'],'latency_ms':measurement['latency_ms']}
            payload['automated_training']=lineage
            temporary=checkpoint.with_suffix('.tmp');torch.save(payload,temporary);temporary.replace(checkpoint)
            metadata_path=output_dir/'model_meta.json';metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
            metadata.update(automated_training=lineage,checkpoint_sha256=_sha256_file(checkpoint)[1]);_atomic_json(metadata_path,metadata)
        manifest = _artifact_manifest(run_dir, spec)
        if cancel_path.exists():
            return status.update(status="aborted")
        budget.seal()
        _atomic_json(run_dir / "artifacts.json", manifest)
        return status.update(status="completed", best_metric=result.get("best_metric"))
    except SnapshotCancelled:
        return status.update(status="aborted")
    except InterruptedError:
        return status.update(status='aborted',**({'stop_reason':'time_limit','error':'Training runtime limit exceeded'}
            if budget is not None and budget.spent else {}))
    except Exception as exc:
        return _failed_status(status, exc, run_dir)
    finally:
        if budget is not None: budget.__exit__()
        stop_watcher.set()
        if watcher is not None and watcher.is_alive():
            watcher.join(timeout=1)


def apply_memory_budget(spec):
    """Enforce the scheduler's explicit claim in this worker's CUDA allocator."""
    resources=spec.get('resources')
    if not resources:return
    if not isinstance(resources,dict) or set(resources)-{'memory_budget_mb','allow_sharing'}:
        raise ValueError('Invalid compute resources')
    budget=resources.get('memory_budget_mb');sharing=resources.get('allow_sharing',False)
    if type(budget) is not int or budget<=0 or type(sharing) is not bool:
        raise ValueError('Invalid compute resources memory budget')
    device=str(spec.get('device') or 'cuda')
    if not device.startswith('cuda'):
        raise ValueError('CUDA memory reservations require a CUDA worker')
    import torch
    if not torch.cuda.is_available():raise ValueError('CUDA memory reservation requires an available device')
    index=int(device.split(':',1)[1]) if ':' in device else 0
    if index>=torch.cuda.device_count():raise ValueError('CUDA worker device is unavailable')
    observed=torch.cuda.get_device_properties(index).total_memory
    requested=budget*1024*1024
    if requested>observed:raise ValueError('Memory budget exceeds worker observed capacity')
    torch.cuda.set_per_process_memory_fraction(requested/observed,index)


def run_label(spec_path:Path)->dict[str,Any]:
    """Generate real foundation candidates from a hash-verified image snapshot."""
    spec_path=Path(spec_path).absolute();run=spec_path.parent
    started=_start_operation(spec_path,'label')
    if isinstance(started,dict):return started
    status=started
    try:
        spec=_read_operation_spec(spec_path,'label')
        status.update(device=spec.get('device'))
        apply_memory_budget(spec)
        archive=_run_relative_file(run,spec.get('snapshot_archive'),'snapshot_archive')
        cancel=_SentinelCancel(run/'cancel')
        if cancel.is_set():return status.update(status='aborted')
        snapshot=extract_snapshot(archive,run/'input',spec['input_manifest_sha256'],cancel)
        from backend.engine.foundation_labeling import foundation_candidates,foundation_readiness,LabelingCancelled
        options=dict(spec.get('labeling') or {});setup=dict(options.pop('setup',{}) or {})
        # Model paths are deployment prerequisites on the worker host. API jobs
        # source these from worker environment; a signed run spec may also carry
        # administrator-configured paths for direct worker installations.
        setup={**{'mask_model_dir':os.environ.get('VISION_MASK_MODEL_DIR'),
                  'model_dir':os.environ.get('VISION_GROUNDING_MODEL_DIR'),
                  'feature_checkpoint':os.environ.get('VISION_DINO_CHECKPOINT'),
                  'feature_sha256':os.environ.get('VISION_DINO_SHA256')},**setup}
        device=spec.get('device') or 'auto'
        readiness=foundation_readiness(setup,device)
        if not readiness['ready']:raise ValueError(readiness['error'])
        for group in ('positive_examples','negative_examples'):
            if group in options:
                options[group]=[{**item,'image_path':str(_run_relative_file(snapshot.data_path,item['image_path'],'example image_path'))} for item in options[group]]
        selected=spec.get('images')
        images=[_run_relative_file(snapshot.data_path,p,'image') for p in selected] if selected else sorted(p for p in snapshot.data_path.rglob('*') if p.suffix.lower() in {'.png','.jpg','.jpeg','.bmp','.tif','.tiff','.dcm','.dicom'})
        if not images:raise ValueError('Labeling snapshot has no supported images')
        if len(images)>10000:raise ValueError('Labeling job exceeds the 10000 image limit')
        rows=[];status.update(status='running',total_steps=len(images))
        def portable(value):
            if isinstance(value,dict):return {key:portable(child) for key,child in value.items() if key not in {'model_dir','mask_model_dir','feature_model_dir','feature_checkpoint'}}
            if isinstance(value,list):return [portable(child) for child in value]
            if isinstance(value,str):return value.replace(str(snapshot.data_path),'input/data')
            return value
        for index,image in enumerate(images):
            if cancel.is_set():return status.update(status='aborted')
            proposals=foundation_candidates(image,setup,device=device,cancel=cancel,**options)
            rows.append({'image_path':str(image.relative_to(snapshot.data_path)),'image_sha256':_sha256_file(image)[1],'candidates':portable(proposals),'review_state':'pending'})
            status.update(current_step=index+1)
        output=run/'outputs';output.mkdir(exist_ok=True)
        _atomic_json(output/'label_results.json',{'job_id':spec['job_id'],'input_manifest_sha256':spec['input_manifest_sha256'],'results':rows,'automatically_approved':False})
        _atomic_json(run/'artifacts.json',_operation_artifact_manifest(run,spec,'label',('outputs/label_results.json',)))
        return status.update(status='completed',image_count=len(rows),candidate_count=sum(len(row['candidates']) for row in rows))
    except (SnapshotCancelled,InterruptedError):return status.update(status='aborted')
    except Exception as exc:
        from backend.engine.foundation_labeling import LabelingCancelled
        if isinstance(exc,LabelingCancelled):return status.update(status='aborted')
        return _failed_status(status,exc,run)


def _read_operation_spec(spec_path: Path, operation: str) -> dict[str, Any]:
    data = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Worker spec must be a JSON object")
    if type(data.get("protocol_version")) is not int or data["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("Unsupported worker protocol version")
    if data.get("operation") != operation:
        raise ValueError(f"Worker spec operation does not match {operation}")
    job_id = data.get("job_id")
    if not isinstance(job_id, str) or not re.fullmatch(r"job_[A-Za-z0-9][A-Za-z0-9_-]{0,119}", job_id):
        raise ValueError("Invalid training job_id")
    digest = data.get("input_manifest_sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError("Invalid input_manifest_sha256")
    device = data.get("device")
    if device is not None and not isinstance(device, str):
        raise ValueError("Invalid device")
    if operation!='label' and (spec_path.parent.name.startswith("op_") is False or spec_path.parent.parent.name != "runs"):
        raise ValueError("Operation spec must live in a runs/op_<id> directory")
    return data


def _source_training_run(operation_dir: Path, spec: dict[str, Any]) -> tuple[Path, Path, Path, dict[str, Any]]:
    """Verify the job-bound source snapshot and both checkpoint artifacts."""
    job_id = spec["job_id"]
    source_run = operation_dir.parent / job_id
    if source_run.is_symlink() or not source_run.is_dir():
        raise SnapshotValidationError("Source training run is missing or linked")
    train_status = json.loads((source_run / "status.json").read_text(encoding="utf-8"))
    if (not isinstance(train_status, dict) or train_status.get("protocol_version") != PROTOCOL_VERSION
            or train_status.get("job_id") != job_id or train_status.get("operation") != "train"
            or train_status.get("status") != "completed"):
        raise SnapshotValidationError("Source training run is not completed")
    manifest = json.loads((source_run / "artifacts.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("protocol_version") != PROTOCOL_VERSION
            or manifest.get("job_id") != job_id or manifest.get("operation") != "train"
            or manifest.get("input_manifest_sha256") != spec["input_manifest_sha256"]):
        raise SnapshotValidationError("Source training artifact manifest does not match")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(_ARTIFACTS):
        raise SnapshotValidationError("Source training artifacts are incomplete")
    by_name = {row.get("path"): row for row in artifacts if isinstance(row, dict)}
    if set(by_name) != set(_ARTIFACTS):
        raise SnapshotValidationError("Source training artifacts are incomplete")
    for relative in _ARTIFACTS:
        artifact = _run_relative_file(source_run, relative, "source artifact")
        row = by_name[relative]
        if not artifact.is_file() or artifact.is_symlink():
            raise SnapshotValidationError(f"Source artifact is missing or linked: {relative}")
        if row.get("size") != artifact.stat().st_size or row.get("sha256") != _sha256_file(artifact)[1]:
            raise SnapshotValidationError(f"Source artifact hash mismatch: {relative}")
    data_path = verify_snapshot_tree(source_run / "input", spec["input_manifest_sha256"])
    metadata = json.loads((source_run / "outputs" / "model_meta.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise SnapshotValidationError("Source model metadata is invalid")
    return source_run, data_path, source_run / "outputs" / "best_model.pt", metadata


def _snapshot_file_reference(path: str, data_path: Path) -> str:
    candidate = Path(path).resolve(strict=True)
    try:
        relative = candidate.relative_to(data_path.resolve(strict=True))
    except ValueError as exc:
        raise SnapshotValidationError(f"Evaluation referenced an image outside snapshot: {path}") from exc
    if not candidate.is_file() or relative == Path("."):
        raise SnapshotValidationError(f"Evaluation referenced a missing image: {path}")
    return f"input/data/{relative.as_posix()}"


def _evaluate_model(spec: dict[str, Any], checkpoint: Path, metadata: dict[str, Any],
                    data_path: Path) -> dict[str, Any]:
    from backend.api import routes_evaluation
    from backend.engine.device import get_device
    from backend.engine.zero_escape_analyzer import compute_sample_defect_score, is_defect_label

    task = spec["task"]
    evaluator = {
        "classification": routes_evaluation._evaluate_classification,
        "detection": routes_evaluation._evaluate_detection,
        "segmentation": routes_evaluation._evaluate_segmentation,
        "anomaly": routes_evaluation._evaluate_anomaly,
    }[task]
    effective_data = routes_evaluation._resolve_dataset_dir(data_path, task)
    result = evaluator(checkpoint, metadata, effective_data, get_device(spec.get("device")))
    for prediction in result.get("test_predictions", []):
        prediction["file_path"] = _snapshot_file_reference(prediction["file_path"], data_path)
        prediction.pop("thumbnail_url", None)
        if "is_defect" not in prediction:
            prediction["is_defect"] = is_defect_label(prediction.get("ground_truth"))
        if "defect_score" not in prediction:
            prediction["defect_score"] = compute_sample_defect_score(prediction, task=task)
    cells = result.get("confusion_matrix", {}).get("cell_samples", {})
    for key, paths in cells.items():
        cells[key] = [_snapshot_file_reference(path, data_path) for path in paths]
    return {
        "job_id": spec["job_id"],
        "task": task,
        "evaluation_contract_version": routes_evaluation.EVALUATION_CONTRACT_VERSION,
        "metrics": result["metrics"],
        "confusion_matrix": result["confusion_matrix"],
        "test_predictions": result["test_predictions"],
        "evaluated_at": _timestamp(),
    }


def _start_operation(spec_path: Path, operation: str) -> _StatusWriter | dict[str, Any]:
    """Claim one operation directory, returning an old receipt on retry."""
    spec_path = Path(spec_path).absolute()
    run_dir = spec_path.parent
    current_spec_sha256 = _spec_sha256(spec_path)
    job_id = "unknown"
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("job_id"), str) and _JOB_ID_RE.fullmatch(raw["job_id"]):
            job_id = raw["job_id"]
    except (OSError, ValueError):
        pass
    previous = run_dir / "status.json"
    if previous.exists():
        try:
            saved = json.loads(previous.read_text(encoding="utf-8"))
            if (isinstance(saved, dict) and saved.get("operation") == operation
                    and saved.get("job_id") == job_id and saved.get("protocol_version") == PROTOCOL_VERSION
                    and current_spec_sha256 is not None and saved.get("spec_sha256") == current_spec_sha256):
                return saved
        except (OSError, ValueError):
            pass
        return {"status": "failed", "error": "Operation run already has an invalid status"}
    try:
        claim = os.open(run_dir / "worker.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return {"status": "failed", "error": "Operation run is already claimed"}
    else:
        os.close(claim)
    return _StatusWriter(run_dir, job_id, operation, current_spec_sha256)


def _selected_image(run_dir: Path, spec: dict[str, Any]) -> Path:
    relative = spec.get("image_path")
    if not isinstance(relative, str) or not relative.startswith("inputs/"):
        raise ValueError("image_path must be under inputs/")
    image = _run_relative_file(run_dir, relative, "image_path")
    digest = spec.get("image_sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError("Invalid image_sha256")
    if image.is_symlink() or not image.is_file() or _sha256_file(image)[1] != digest:
        raise SnapshotValidationError("Selected image is missing, linked, or has a hash mismatch")
    from PIL import Image

    with Image.open(image) as opened:
        opened.verify()
    with Image.open(image) as opened:
        opened.load()
    return image


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}-",
                                         suffix=".tmp", delete=False) as writer:
            temporary = Path(writer.name)
            writer.write(data)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_evaluate(spec_path: Path) -> dict[str, Any]:
    """Evaluate a completed training run without publishing remote paths."""
    spec_path = Path(spec_path).absolute()
    run_dir = spec_path.parent
    started = _start_operation(spec_path, "evaluate")
    if isinstance(started, dict):
        return started
    status = started
    try:
        spec = _read_operation_spec(spec_path, "evaluate")
        apply_memory_budget(spec)
        if spec.get("task") not in _TASKS:
            raise ValueError("Invalid evaluation task")
        status.update(job_id=spec["job_id"], device=spec.get("device"))
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        _, data_path, checkpoint, metadata = _source_training_run(run_dir, spec)
        if metadata.get("task") != spec["task"]:
            raise SnapshotValidationError("Evaluation task does not match source model")
        status.update(status="running")
        if 'evaluation_cohort' in spec or 'common_cohort_contract' in spec:
            from backend.remote.evaluation_cohort import extract_cohort,worker_evaluate
            common_data,descriptor=extract_cohort(run_dir,spec)
            payload,artifact_paths=worker_evaluate(spec,checkpoint,metadata,common_data,descriptor,run_dir,_SentinelCancel(run_dir/'cancel'))
        else:
            payload = _evaluate_model(spec, checkpoint, metadata, data_path)
            artifact_paths=("outputs/eval_results.json",)
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        output_path = run_dir / "outputs" / "eval_results.json"
        _atomic_json(output_path, payload)
        manifest = _operation_artifact_manifest(run_dir, spec, "evaluate", artifact_paths)
        _atomic_json(run_dir / "artifacts.json", manifest)
        return status.update(status="completed")
    except InterruptedError:
        status.acknowledge_cancel()
        return status.update(status="aborted")
    except Exception as exc:
        return _failed_status(status, exc, run_dir)


def run_infer(spec_path: Path) -> dict[str, Any]:
    """Infer one uploaded image with the job-bound model and hash the overlay."""
    spec_path = Path(spec_path).absolute()
    run_dir = spec_path.parent
    started = _start_operation(spec_path, "infer")
    if isinstance(started, dict):
        return started
    status = started
    try:
        spec = _read_operation_spec(spec_path, "infer")
        apply_memory_budget(spec)
        if spec.get("task") not in _TRAIN_TASKS:
            raise ValueError("Invalid inference task")
        threshold = spec.get("threshold")
        status.update(job_id=spec["job_id"], device=spec.get("device"))
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        _, _, checkpoint, metadata = _source_training_run(run_dir, spec)
        if metadata.get("task") != spec["task"]:
            raise SnapshotValidationError("Inference task does not match source model")
        from backend.engine.score_contract import resolve_inference_score
        threshold, resolved_score_spec = resolve_inference_score(checkpoint, spec['task'], threshold, spec.get('score_spec'))
        image = _selected_image(run_dir, spec)
        status.update(status="running")
        import cv2

        from backend.engine.device import get_device
        from backend.engine.trainer import infer

        if spec['task'] in {'rotation','ocr','rotated_detection','enhancement','defect_gan'}:
            from backend.remote.specialist_inference import infer_specialist
            inference=infer_specialist(spec['task'],checkpoint,image,threshold=float(threshold),device=str(get_device(spec.get('device'))),output_dir=run_dir/'outputs')
        else:
            inference = infer(task=spec["task"], model_path=checkpoint, image_input=image,
                              threshold=float(threshold), device=get_device(spec.get("device")),
                              **({'score_spec':resolved_score_spec} if resolved_score_spec is not None else {}))
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        overlay_bgr = cv2.cvtColor(inference.visual_overlay, cv2.COLOR_RGB2BGR)
        encoded, png_buffer = cv2.imencode(".png", overlay_bgr)
        if not encoded:
            raise RuntimeError("Failed to encode inference overlay")
        _atomic_bytes(run_dir / "outputs" / "overlay.png", png_buffer.tobytes())
        payload = {
            "image_id": spec.get("image_id") or image.stem,
            "image_path": spec["image_path"],
            "image_sha256": spec["image_sha256"],
            "threshold": float(threshold),
            "confidence_score": round(float(inference.confidence_score), 4) if inference.confidence_score is not None else None,
            "predictions": inference.predictions,
            "latency_ms": round(float(inference.latency_ms), 2),
            "overlay_path": "outputs/overlay.png",
        }
        _atomic_json(run_dir / "outputs" / "result.json", payload)
        manifest = _operation_artifact_manifest(run_dir, spec, "infer",
                                                 ("outputs/result.json", "outputs/overlay.png"))
        _atomic_json(run_dir / "artifacts.json", manifest)
        return status.update(status="completed")
    except Exception as exc:
        return _failed_status(status, exc, run_dir)


def _verified_flowchart_models(run_dir: Path, spec: dict[str, Any], pipeline: Any) -> dict[str, dict[str, Any]]:
    from backend.engine.flowchart_engine import ordered_linear_nodes,debug_ancestor_ids
    from backend.engine.specialized_models import flow_model_task,valid_flow_job,FLOW_TASKS

    references = spec.get("models")
    portable=spec.get('portable_models') is True
    if 'portable_models' in spec and type(spec['portable_models']) is not bool:
        raise ValueError('Invalid portable model mode')
    scope=debug_ancestor_ids(pipeline,spec.get('stop_node_id'))
    if not isinstance(references, list) or not (0 if portable and spec.get('stop_node_id') else 1) <= len(references) <= (24 if portable else 8):
        raise ValueError("Flowchart model references exceed the supported bound")
    if portable:
        binding=hashlib.sha256(json.dumps(references,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if binding!=spec['input_manifest_sha256']:
            raise SnapshotValidationError('Portable checkpoint bundle identity differs')
    by_id: dict[str, dict[str, Any]] = {}
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError("Invalid flowchart model reference")
        job_id = reference.get("job_id")
        task = reference.get("task")
        digest = reference.get("input_manifest_sha256")
        valid = (valid_flow_job(job_id,task) and task in FLOW_TASKS) if portable else (
            isinstance(job_id,str) and re.fullmatch(r"job_[A-Za-z0-9][A-Za-z0-9_-]{0,119}",job_id)
            and task in _TASKS and isinstance(digest,str) and _SHA256_RE.fullmatch(digest))
        if not valid or job_id in by_id:
            raise ValueError("Invalid or duplicate flowchart model reference")
        by_id[job_id] = reference
    primary = by_id.get(spec["job_id"])
    if not portable and (primary is None or primary["input_manifest_sha256"] != spec["input_manifest_sha256"]):
        raise SnapshotValidationError("Primary flowchart model does not match source snapshot")
    needed: dict[str, str] = {}
    for node in ordered_linear_nodes(pipeline):
        if node.id not in scope: continue
        node_task=flow_model_task(node)
        if node_task is None:
            continue
        node_job = node.data.model_job_id
        if not node_job or node_task not in (FLOW_TASKS if portable else _TASKS):
            raise ValueError("Flowchart node is missing a model job or task")
        if node_job in needed and needed[node_job] != node_task:
            raise ValueError("Flowchart model job has conflicting tasks")
        needed[node_job] = node_task
    if set(needed) != set(by_id):
        raise SnapshotValidationError("Flowchart pipeline models differ from verified model references")
    checkpoints = {}
    total_size=0
    for job_id, task in needed.items():
        reference = by_id[job_id]
        if reference["task"] != task:
            raise SnapshotValidationError(f"Flowchart model task mismatch: {job_id}")
        if portable:
            assets={}
            for name,limit in (('checkpoint',512*1024*1024),('metadata',4*1024*1024)):
                relative=reference.get(name+'_path')
                expected=f"inputs/models/{job_id}/{'best_model.pt' if name=='checkpoint' else 'model_meta.json'}"
                if relative!=expected:raise SnapshotValidationError('Portable model asset path is invalid')
                path=_run_relative_file(run_dir,relative,'portable model asset')
                if any(run_dir.joinpath(*PurePosixPath(relative).parts[:index]).is_symlink() for index in range(1,len(PurePosixPath(relative).parts)+1)) or not path.is_file():
                    raise SnapshotValidationError('Portable model asset is missing or linked')
                size,digest=_sha256_file(path);total_size+=size
                if (not 0<size<=limit or type(reference.get(name+'_size')) is not int or size!=reference[name+'_size']
                        or digest!=reference.get(name+'_sha256') or total_size>2*1024*1024*1024):
                    raise SnapshotValidationError('Portable model asset hash, size or transfer bound differs')
                assets[name]=path
            checkpoint=assets['checkpoint'];metadata=json.loads(assets['metadata'].read_text(encoding='utf-8'))
            import torch
            payload=torch.load(checkpoint,map_location='cpu',weights_only=True)
            if (not isinstance(payload,dict) or payload.get('task')!=task or not isinstance(payload.get('model_state_dict'),dict)
                    or not isinstance(metadata,dict) or metadata.get('checkpoint_sha256',reference['checkpoint_sha256'])!=reference['checkpoint_sha256']):
                raise SnapshotValidationError('Portable checkpoint task or metadata hash differs')
        else:
            _, _, checkpoint, metadata = _source_training_run(run_dir, reference)
        if metadata.get("task") != task:
            raise SnapshotValidationError(f"Flowchart source model task mismatch: {job_id}")
        checkpoints[job_id] = {"task": task, "checkpoint": checkpoint}
    return checkpoints


def _flowchart_engine(checkpoints: dict[str, dict[str, Any]], device: str | None) -> Any:
    from backend.engine.flowchart_engine import FlowchartEngine

    class VerifiedFlowchartEngine(FlowchartEngine):
        def _resolve_checkpoint(self, job_id: str | None, task: str) -> Path | None:
            reference = checkpoints.get(job_id or "")
            if reference is None or reference["task"] != task:
                raise SnapshotValidationError("Flowchart tried to load an unverified model")
            return reference["checkpoint"]

    return VerifiedFlowchartEngine(device=device)


def _png_data_url_bytes(value: Any, field_name: str) -> bytes:
    prefix = "data:image/png;base64,"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(f"Flowchart {field_name} is not a PNG data URL")
    try:
        data = base64.b64decode(value[len(prefix):], validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"Flowchart {field_name} has invalid base64") from exc
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"Flowchart {field_name} has invalid PNG bytes")
    return data


def run_flowchart(spec_path: Path, engine_factory: Callable[[dict[str, dict[str, Any]], str | None], Any] | None = None) -> dict[str, Any]:
    """Inspect the exact uploaded image with verified model assets."""
    spec_path = Path(spec_path).absolute()
    run_dir = spec_path.parent
    started = _start_operation(spec_path, "flowchart_run")
    if isinstance(started, dict):
        return started
    status = started
    try:
        spec = _read_operation_spec(spec_path, "flowchart_run")
        if spec.get('comparison_operation_contract') is not None:
            if spec['comparison_operation_contract']!=1 or spec.get('portable_models') is not True or not isinstance(spec.get('comparison_binding_sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',spec['comparison_binding_sha256']):
                raise ValueError('Invalid portable comparison binding')
        apply_memory_budget(spec)
        from backend.engine.flowchart_engine import FlowchartPipeline

        pipeline = FlowchartPipeline.model_validate(spec.get("pipeline"))
        status.update(job_id=spec["job_id"], device=spec.get("device"))
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        image = _selected_image(run_dir, spec)
        if image.stat().st_size>64*1024*1024:raise ValueError('Inspection image exceeds transfer limit')
        checkpoints = _verified_flowchart_models(run_dir, spec, pipeline)
        if spec.get('portable_models'):
            from backend.engine.runtime_device import resolve_runtime_device
            actual_device=resolve_runtime_device(spec.get('device'))
        else:
            from backend.engine.device import get_device
            actual_device=get_device(spec.get('device'))
        status.update(status="running")
        engine = (engine_factory or _flowchart_engine)(checkpoints, str(actual_device))
        result = engine.execute(pipeline=pipeline, image_path=str(image),
                                image_id=spec.get("image_id") or image.stem,
                                **({"stop_node_id":spec["stop_node_id"]} if spec.get("stop_node_id") else {}))
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        if not isinstance(result, dict):
            raise ValueError("Flowchart engine returned an invalid result")
        payload = dict(result)
        payload["image_path"] = spec["image_path"]
        payload["image_sha256"] = spec["image_sha256"]
        payload["model_job_ids"] = sorted(checkpoints)
        payload['execution_device']=str(actual_device)
        if spec.get('comparison_operation_contract')==1:
            payload['comparison_binding_sha256']=spec['comparison_binding_sha256']
        if actual_device.type=='cuda':
            import torch
            payload['device_name']=torch.cuda.get_device_name(actual_device)
        else:payload['device_name']='CPU' if actual_device.type=='cpu' else 'Metal GPU'
        preview = _png_data_url_bytes(payload.get("annotated_image"), "preview")
        _atomic_bytes(run_dir / "outputs" / "preview.png", preview)
        payload["annotated_image"] = "outputs/preview.png"
        files = ["outputs/flowchart_result.json", "outputs/preview.png"]
        crops = payload.get("crops")
        if not isinstance(crops, list):
            raise ValueError("Flowchart result has no crops list")
        for index, crop in enumerate(crops):
            if not isinstance(crop, dict):
                raise ValueError("Flowchart crop is invalid")
            path = f"outputs/crops/crop_{index:03d}.png"
            _atomic_bytes(run_dir / path, _png_data_url_bytes(crop.get("crop_thumbnail"), "crop thumbnail"))
            crop["crop_thumbnail"] = path
            files.append(path)
        _atomic_json(run_dir / "outputs" / "flowchart_result.json", payload)
        manifest = _operation_artifact_manifest(run_dir, spec, "flowchart_run", tuple(files))
        manifest["model_refs"] = sorted(spec['models'],key=lambda row:row['job_id'])
        manifest["selected_image_sha256"] = spec["image_sha256"]
        _atomic_json(run_dir / "artifacts.json", manifest)
        return status.update(status="completed")
    except Exception as exc:
        return _failed_status(status, exc, run_dir)


def run_benchmark(spec_path: Path) -> dict[str, Any]:
    """Measure verified model forward latency on the selected worker device."""
    spec_path = Path(spec_path).absolute()
    run_dir = spec_path.parent
    started = _start_operation(spec_path, "benchmark")
    if isinstance(started, dict):
        return started
    status = started
    try:
        spec = _read_operation_spec(spec_path, "benchmark")
        apply_memory_budget(spec)
        iterations = spec.get("iterations", 25)
        resolution = spec.get("resolution", 256)
        if type(iterations) is not int or not 5 <= iterations <= 100:
            raise ValueError("Benchmark iterations must be between 5 and 100")
        if type(resolution) is not int or not 32 <= resolution <= 2048:
            raise ValueError("Benchmark resolution must be between 32 and 2048 pixels")
        status.update(job_id=spec["job_id"], device=spec.get("device"))
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        _, _, checkpoint, metadata = _source_training_run(run_dir, spec)
        import numpy as np
        import torch

        from backend.engine.device import get_device
        from backend.engine.exporter import load_checkpoint_and_reconstruct_model

        device = get_device(spec.get("device"))
        model, loaded_meta, _ = load_checkpoint_and_reconstruct_model(checkpoint)
        if loaded_meta.get("task") != metadata.get("task"):
            raise SnapshotValidationError("Benchmark model task differs from verified metadata")
        model = model.to(device).eval()
        dummy_input = torch.rand((1, 3, resolution, resolution), dtype=torch.float32, device=device)
        device_name = (torch.cuda.get_device_name(device) if device.type == "cuda"
                       else "Metal MPS" if device.type == "mps" else "CPU")
        status.update(status="running", device=str(device), gpu_name=device_name if device.type == "cuda" else None)

        def synchronize() -> None:
            if device.type == "cuda":
                torch.cuda.synchronize(device)
            elif device.type == "mps":
                torch.mps.synchronize()

        latencies = []
        with torch.inference_mode():
            for _ in range(3):
                model(dummy_input)
                synchronize()
            for _ in range(iterations):
                if (run_dir / "cancel").exists():
                    return status.update(status="aborted")
                start = time.perf_counter()
                model(dummy_input)
                synchronize()
                latencies.append((time.perf_counter() - start) * 1000.0)
        measured = np.array(latencies)
        mean_ms = float(np.mean(measured))
        payload = {
            "status": "success",
            "model_job_id": spec["job_id"],
            "task": metadata["task"],
            "input_kind": "synthetic_random_tensor",
            "measurement_scope": "model_forward_only",
            "device": device.type,
            "device_name": device_name,
            "iterations": iterations,
            "mean_latency_ms": round(mean_ms, 2),
            "p95_latency_ms": round(float(np.percentile(measured, 95)), 2),
            "min_latency_ms": round(float(np.min(measured)), 2),
            "max_latency_ms": round(float(np.max(measured)), 2),
            "std_latency_ms": round(float(np.std(measured)), 2),
            "fps": round(1000.0 / mean_ms, 1) if mean_ms > 0 else 0.0,
            "resolution": f"{resolution}x{resolution}",
            "batch_size": 1,
        }
        _atomic_json(run_dir / "outputs" / "benchmark.json", payload)
        manifest = _operation_artifact_manifest(run_dir, spec, "benchmark", ("outputs/benchmark.json",))
        _atomic_json(run_dir / "artifacts.json", manifest)
        return status.update(status="completed", device=str(device))
    except Exception as exc:
        return _failed_status(status, exc, run_dir)


def _verified_evaluation_input(run_dir: Path, spec: dict[str, Any]) -> Path | None:
    relative = spec.get("evaluation_path")
    digest = spec.get("evaluation_sha256")
    if relative is None and digest is None:
        return None
    if not isinstance(relative, str) or not relative.startswith("inputs/"):
        raise ValueError("evaluation_path must be under inputs/")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError("Invalid evaluation_sha256")
    path = _run_relative_file(run_dir, relative, "evaluation_path")
    if path.is_symlink() or not path.is_file() or _sha256_file(path)[1] != digest:
        raise SnapshotValidationError("Evaluation file is missing, linked, or has a hash mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("job_id") != spec["job_id"] or payload.get("task") != spec["task"]:
        raise SnapshotValidationError("Evaluation file does not belong to this model job and task")
    return path


def _link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def _runtime_package_archive(package_dir: Path, archive_path: Path) -> list[dict[str, Any]]:
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise SnapshotValidationError("Exporter returned an invalid package directory")
    files: list[tuple[Path, str]] = []
    for path in sorted(package_dir.rglob("*")):
        if path.is_symlink():
            raise SnapshotValidationError("Export package contains a link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise SnapshotValidationError("Export package contains a special file")
        files.append((path, path.relative_to(package_dir).as_posix()))
    if not files:
        raise SnapshotValidationError("Exporter returned an empty package")
    internal = []
    with tarfile.open(archive_path, "w:gz") as archive:
        for path, relative in files:
            size, digest = _sha256_file(path)
            member = tarfile.TarInfo(f"{package_dir.name}/{relative}")
            member.size = size
            member.mode = 0o644
            member.mtime = 0
            with path.open("rb") as reader:
                archive.addfile(member, reader)
            internal.append({"path": relative, "size": size, "sha256": digest})
    return internal


def run_export(spec_path: Path) -> dict[str, Any]:
    """Export a verified model into a portable archive with internal hashes."""
    spec_path = Path(spec_path).absolute()
    run_dir = spec_path.parent
    started = _start_operation(spec_path, "export")
    if isinstance(started, dict):
        return started
    status = started
    try:
        spec = _read_operation_spec(spec_path, "export")
        apply_memory_budget(spec)
        export_format = spec.get("export_format", "onnx")
        resolution = spec.get("resolution", 256)
        quantize_fp16 = spec.get("quantize_fp16", False)
        package_name = spec.get("package_name") or f"modu_vision_export_{spec['job_id']}"
        if export_format not in ("onnx", "torchscript"):
            raise ValueError("Invalid export_format")
        if type(resolution) is not int or not 32 <= resolution <= 2048:
            raise ValueError("Export resolution must be between 32 and 2048 pixels")
        if quantize_fp16 is not False:
            raise ValueError("FP16 export is not implemented")
        if not isinstance(package_name, str) or not re.fullmatch(r"[\w][\w.-]{0,95}", package_name):
            raise ValueError("Invalid package_name")
        status.update(job_id=spec["job_id"], device=spec.get("device"))
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        _, _, checkpoint, metadata = _source_training_run(run_dir, spec)
        spec["task"] = metadata.get("task")
        if spec["task"] not in _TASKS:
            raise SnapshotValidationError("Source model has an invalid task")
        evaluation = _verified_evaluation_input(run_dir, spec)

        # The existing exporter deliberately resolves only a completed local
        # models/job_* directory. Give it an isolated, hash-verified view.
        view_root = run_dir / "model_view"
        model_dir = view_root / "models" / spec["job_id"]
        model_dir.mkdir(parents=True, exist_ok=False)
        source_meta = checkpoint.parent / "model_meta.json"
        _link_or_copy(checkpoint, model_dir / "best_model.pt")
        _link_or_copy(source_meta, model_dir / "model_meta.json")
        if evaluation is not None:
            shutil.copyfile(evaluation, model_dir / "eval_results.json")
        _atomic_json(model_dir / "job_receipt.json", {
            "job_id": spec["job_id"], "status": "completed", "task": spec["task"],
        })
        status.update(status="running")
        from backend.engine.exporter import export_runtime_package

        former_cwd = Path.cwd()
        try:
            os.chdir(view_root)
            exported = export_runtime_package(
                job_id=spec["job_id"], export_format=export_format, resolution=resolution,
                quantize_fp16=False, package_name=package_name,
                output_base_dir=run_dir / "outputs",
            )
        finally:
            os.chdir(former_cwd)
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        package_dir = Path(exported["package_path"]).resolve(strict=True)
        output_root = (run_dir / "outputs").resolve(strict=True)
        if not package_dir.is_relative_to(output_root) or package_dir.name != exported["package_name"]:
            raise SnapshotValidationError("Exporter returned a package outside operation outputs")
        package_relative = f"outputs/{package_dir.name}.tar.gz"
        internal = _runtime_package_archive(package_dir, run_dir / package_relative)
        payload = {
            "status": "success",
            "model_job_id": spec["job_id"],
            "package_name": package_dir.name,
            "package_path": package_relative,
            "manifest": internal,
            "total_files": len(internal),
            "export_format": exported["export_format"],
            "model_file": exported["model_file"],
            "optimal_threshold": exported["optimal_threshold"],
        }
        _atomic_json(run_dir / "outputs" / "export_result.json", payload)
        manifest = _operation_artifact_manifest(run_dir, spec, "export",
                                                 ("outputs/export_result.json", package_relative))
        if evaluation is not None:
            manifest["evaluation_sha256"] = spec["evaluation_sha256"]
        _atomic_json(run_dir / "artifacts.json", manifest)
        return status.update(status="completed")
    except Exception as exc:
        return _failed_status(status, exc, run_dir)


def main(argv: list[str] | None = None, trainer_factory: Callable[..., Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Versioned remote compute worker")
    operations = parser.add_subparsers(dest="operation", required=True)
    for name in OPERATIONS:
        command = operations.add_parser(name)
        command.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == "train":
        result = run_train(args.spec, trainer_factory=trainer_factory)
        return 0 if result["status"] == "completed" else 3 if result["status"] == "aborted" else 1
    if args.operation=='label':
        result=run_label(args.spec)
        return 0 if result['status']=='completed' else 3 if result['status']=='aborted' else 1
    if args.operation == "evaluate":
        result = run_evaluate(args.spec)
        return 0 if result["status"] == "completed" else 3 if result["status"] == "aborted" else 1
    if args.operation == "infer":
        result = run_infer(args.spec)
        return 0 if result["status"] == "completed" else 3 if result["status"] == "aborted" else 1
    if args.operation == "flowchart_run":
        result = run_flowchart(args.spec)
        return 0 if result["status"] == "completed" else 3 if result["status"] == "aborted" else 1
    if args.operation == 'package_parity':
        from backend.remote.package_parity import run_package_parity
        result = run_package_parity(args.spec)
        return 0 if result['status'] == 'completed' else 3 if result['status'] == 'aborted' else 1
    if args.operation == "benchmark":
        result = run_benchmark(args.spec)
        return 0 if result["status"] == "completed" else 3 if result["status"] == "aborted" else 1
    if args.operation == "export":
        result = run_export(args.spec)
        return 0 if result["status"] == "completed" else 3 if result["status"] == "aborted" else 1
    parser.error(f"{args.operation} is reserved for a future worker protocol operation")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
