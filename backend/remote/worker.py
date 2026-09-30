"""Durable, versioned compute worker invoked over an existing SSH session.

The local daemon uploads a run-local spec and snapshot archive, then invokes
``python -m backend.remote.worker train --spec <path>``. A dropped SSH channel
does not erase status: all state and hashes live in the run directory.
"""

from __future__ import annotations

import argparse
import base64
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
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from backend.remote.snapshot import (
    PROTOCOL_VERSION,
    SnapshotCancelled,
    SnapshotValidationError,
    extract_snapshot,
    verify_snapshot_tree,
)


OPERATIONS = ("train", "evaluate", "infer", "flowchart_run", "benchmark", "export")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TASKS = {"classification", "detection", "segmentation", "anomaly"}
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
        os.replace(temporary, path)
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
            "best_metric": None,
            "device": None,
            "error": None,
            "updated_at": _timestamp(),
        }
        _atomic_json(self.path, self._payload)

    def update(self, **changes: Any) -> dict[str, Any]:
        with self._lock:
            self._payload.update(changes)
            self._payload["updated_at"] = _timestamp()
            _atomic_json(self.path, self._payload)
            return self._payload.copy()


class _TrainingStatusCallback:
    """Match UnifiedAutoMLTrainer's callback methods without loading torch."""

    def __init__(self, writer: _StatusWriter):
        self.writer = writer

    def on_training_start(self, config: dict[str, Any]) -> None:
        self.writer.update(total_epochs=int(config.get("epochs") or 0), device=config.get("device"))

    def on_step_end(self, step: int, total_steps: int, current_loss: float, epoch: int) -> None:
        self.writer.update(current_epoch=epoch, current_step=step, total_steps=total_steps,
                           train_loss=current_loss)

    def on_epoch_end(self, epoch: int, total_epochs: int, train_loss: float, val_loss: float,
                     lr: float, metrics: dict[str, float]) -> None:
        self.writer.update(current_epoch=epoch, total_epochs=total_epochs,
                           train_loss=train_loss, val_loss=val_loss)

    def on_hardware_stats(self, stats: dict[str, Any]) -> None:
        self.writer.update(device_type=stats.get("device_type"), gpu_name=stats.get("gpu_name"))

    def on_training_completed(self, job_id: str, duration_seconds: float, best_metric: float,
                              model_path: str) -> None:
        # Completion is published only after artifact hashes are recorded.
        self.writer.update(best_metric=best_metric, duration_seconds=duration_seconds)

    def on_training_aborted(self, epoch: int, reason: str) -> None:
        self.writer.update(current_epoch=epoch, status="stopping")

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
    if data.get("task") not in _TASKS:
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
    def __init__(self, path: Path):
        self.path = path

    def is_set(self) -> bool:
        return self.path.exists()


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
    try:
        spec = _read_train_spec(spec_path, run_dir)
        status.update(job_id=spec["job_id"], device=spec.get("device"))
        if cancel_path.exists():
            return status.update(status="aborted")
        snapshot = extract_snapshot(spec["archive_path"], run_dir / "input",
                                    spec["input_manifest_sha256"], _SentinelCancel(cancel_path))
        if snapshot.manifest_sha256 != spec["input_manifest_sha256"]:
            raise SnapshotValidationError("Input manifest hash mismatch")
        if cancel_path.exists():
            return status.update(status="aborted")
        output_dir = run_dir / "outputs"
        output_dir.mkdir(exist_ok=False)
        if trainer_factory is None:
            from backend.engine.trainer import UnifiedAutoMLTrainer

            trainer_factory = UnifiedAutoMLTrainer
        callback = _TrainingStatusCallback(status)
        trainer = trainer_factory(
            task=spec["task"], dataset_path=snapshot.data_path, output_dir=output_dir,
            preset=spec.get("preset", "fast"), device=spec.get("device"), callback=callback,
            config_overrides=spec["config_overrides"],
        )
        if cancel_path.exists():
            trainer.abort()
            return status.update(status="aborted")

        def watch_cancel() -> None:
            while not stop_watcher.wait(0.05):
                if cancel_path.exists():
                    status.update(status="stopping")
                    trainer.abort()
                    break

        watcher = threading.Thread(target=watch_cancel, name=f"Cancel-{spec['job_id']}", daemon=True)
        watcher.start()
        status.update(status="running")
        result = trainer.train(job_id=spec["job_id"])
        stop_watcher.set()
        watcher.join(timeout=1)
        if cancel_path.exists() or not isinstance(result, dict) or result.get("status") == "aborted":
            return status.update(status="aborted")
        if result.get("status") != "completed":
            raise RuntimeError(f"Trainer returned non-completed status: {result.get('status')}")
        manifest = _artifact_manifest(run_dir, spec)
        if cancel_path.exists():
            return status.update(status="aborted")
        _atomic_json(run_dir / "artifacts.json", manifest)
        return status.update(status="completed", best_metric=result.get("best_metric"))
    except SnapshotCancelled:
        return status.update(status="aborted")
    except Exception as exc:
        return _failed_status(status, exc, run_dir)
    finally:
        stop_watcher.set()
        if watcher is not None and watcher.is_alive():
            watcher.join(timeout=1)


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
    if spec_path.parent.name.startswith("op_") is False or spec_path.parent.parent.name != "runs":
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
        if spec.get("task") not in _TASKS:
            raise ValueError("Invalid evaluation task")
        status.update(job_id=spec["job_id"], device=spec.get("device"))
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        _, data_path, checkpoint, metadata = _source_training_run(run_dir, spec)
        if metadata.get("task") != spec["task"]:
            raise SnapshotValidationError("Evaluation task does not match source model")
        status.update(status="running")
        payload = _evaluate_model(spec, checkpoint, metadata, data_path)
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        output_path = run_dir / "outputs" / "eval_results.json"
        _atomic_json(output_path, payload)
        manifest = _operation_artifact_manifest(run_dir, spec, "evaluate", ("outputs/eval_results.json",))
        _atomic_json(run_dir / "artifacts.json", manifest)
        return status.update(status="completed")
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
        if spec.get("task") not in _TASKS:
            raise ValueError("Invalid inference task")
        threshold = spec.get("threshold", 0.5)
        if type(threshold) not in (int, float) or not 0 <= threshold <= 1:
            raise ValueError("Invalid inference threshold")
        status.update(job_id=spec["job_id"], device=spec.get("device"))
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        _, _, checkpoint, metadata = _source_training_run(run_dir, spec)
        if metadata.get("task") != spec["task"]:
            raise SnapshotValidationError("Inference task does not match source model")
        image = _selected_image(run_dir, spec)
        status.update(status="running")
        import cv2

        from backend.engine.device import get_device
        from backend.engine.trainer import infer

        inference = infer(task=spec["task"], model_path=checkpoint, image_input=image,
                          threshold=float(threshold), device=get_device(spec.get("device")))
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
            "confidence_score": round(float(inference.confidence_score), 4),
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
    from backend.engine.flowchart_engine import ordered_linear_nodes

    references = spec.get("models")
    if not isinstance(references, list) or not 1 <= len(references) <= 8:
        raise ValueError("Flowchart models must be a nonempty list of up to eight training jobs")
    by_id: dict[str, dict[str, Any]] = {}
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError("Invalid flowchart model reference")
        job_id = reference.get("job_id")
        task = reference.get("task")
        digest = reference.get("input_manifest_sha256")
        if (not isinstance(job_id, str) or not re.fullmatch(r"job_[A-Za-z0-9][A-Za-z0-9_-]{0,119}", job_id)
                or task not in _TASKS or not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)
                or job_id in by_id):
            raise ValueError("Invalid or duplicate flowchart model reference")
        by_id[job_id] = reference
    primary = by_id.get(spec["job_id"])
    if primary is None or primary["input_manifest_sha256"] != spec["input_manifest_sha256"]:
        raise SnapshotValidationError("Primary flowchart model does not match source snapshot")
    needed: dict[str, str] = {}
    for node in ordered_linear_nodes(pipeline):
        if node.data.node_type not in ("detection_crop", "inspection"):
            continue
        node_job = node.data.model_job_id
        node_task = "detection" if node.data.node_type == "detection_crop" else node.data.task
        if not node_job or node_task not in _TASKS:
            raise ValueError("Flowchart node is missing a model job or task")
        if node_job in needed and needed[node_job] != node_task:
            raise ValueError("Flowchart model job has conflicting tasks")
        needed[node_job] = node_task
    if set(needed) != set(by_id):
        raise SnapshotValidationError("Flowchart pipeline models differ from verified model references")
    checkpoints = {}
    for job_id, task in needed.items():
        reference = by_id[job_id]
        if reference["task"] != task:
            raise SnapshotValidationError(f"Flowchart model task mismatch: {job_id}")
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
    """Inspect the exact uploaded image with model jobs bound to one server."""
    spec_path = Path(spec_path).absolute()
    run_dir = spec_path.parent
    started = _start_operation(spec_path, "flowchart_run")
    if isinstance(started, dict):
        return started
    status = started
    try:
        spec = _read_operation_spec(spec_path, "flowchart_run")
        from backend.engine.flowchart_engine import FlowchartPipeline

        pipeline = FlowchartPipeline.model_validate(spec.get("pipeline"))
        status.update(job_id=spec["job_id"], device=spec.get("device"))
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        image = _selected_image(run_dir, spec)
        checkpoints = _verified_flowchart_models(run_dir, spec, pipeline)
        status.update(status="running")
        engine = (engine_factory or _flowchart_engine)(checkpoints, spec.get("device"))
        result = engine.execute(pipeline=pipeline, image_path=str(image),
                                image_id=spec.get("image_id") or image.stem)
        if (run_dir / "cancel").exists():
            return status.update(status="aborted")
        if not isinstance(result, dict):
            raise ValueError("Flowchart engine returned an invalid result")
        payload = dict(result)
        payload["image_path"] = spec["image_path"]
        payload["image_sha256"] = spec["image_sha256"]
        payload["model_job_ids"] = sorted(checkpoints)
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
        manifest["model_refs"] = [
            {"job_id": job_id, "task": reference["task"],
             "input_manifest_sha256": next(row["input_manifest_sha256"] for row in spec["models"] if row["job_id"] == job_id)}
            for job_id, reference in sorted(checkpoints.items())
        ]
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
    if args.operation == "evaluate":
        result = run_evaluate(args.spec)
        return 0 if result["status"] == "completed" else 3 if result["status"] == "aborted" else 1
    if args.operation == "infer":
        result = run_infer(args.spec)
        return 0 if result["status"] == "completed" else 3 if result["status"] == "aborted" else 1
    if args.operation == "flowchart_run":
        result = run_flowchart(args.spec)
        return 0 if result["status"] == "completed" else 3 if result["status"] == "aborted" else 1
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
