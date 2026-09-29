"""Server-bound evaluation and inspection operations for completed remote jobs."""

from __future__ import annotations

import hashlib
import base64
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from backend.remote.coordinator import (
    ArtifactValidationError,
    RemoteDisconnected,
    _atomic_json,
    _bundle_backend,
    _remote_json,
    _remote_path,
    _sha256,
)
from backend.remote.profiles import ComputeProfile
from backend.remote.ssh_transport import SSHTransport
from backend.remote.snapshot import SnapshotValidationError, verify_snapshot_tree

logger = logging.getLogger("vision_ai_studio.remote_operations")
OP_POLL_INTERVAL_SECONDS = 2.0


@dataclass(frozen=True)
class RemoteJobContext:
    job_id: str
    task: str
    output_dir: Path
    dataset_path: Path
    profile: ComputeProfile
    input_manifest_sha256: str


def _verify_local_snapshot(context: RemoteJobContext) -> None:
    try:
        verify_snapshot_tree(
            context.dataset_path.parent, context.input_manifest_sha256, allow_archive=True,
        )
    except SnapshotValidationError as exc:
        raise ArtifactValidationError(f"Local training snapshot verification failed: {exc}") from exc


def remote_job_context(output_dir: Path, job_id: str) -> RemoteJobContext | None:
    """Return a remote owner only when the local completed receipt agrees."""
    output_dir = Path(output_dir).resolve()
    receipt_path = output_dir / "job_receipt.json"
    journal_path = output_dir / "remote_job.json"
    if not journal_path.is_file():
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if isinstance(receipt, dict) and receipt.get("compute_profile_id"):
                raise ArtifactValidationError("Remote model journal is missing; refusing local compute fallback")
        return None
    if not receipt_path.is_file():
        raise ArtifactValidationError("Remote model has no completed local receipt")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    profile = ComputeProfile.model_validate(journal["profile"])
    if (receipt.get("job_id") != job_id or journal.get("job_id") != job_id
            or receipt.get("status") != "completed"
            or receipt.get("compute_profile_id") != profile.id
            or receipt.get("dataset_fingerprint") != journal.get("dataset_fingerprint")
            or receipt.get("source_dataset_path") != journal.get("source_dataset_path")
            or journal.get("state") != "completed"):
        raise ArtifactValidationError("Remote model provenance does not match its completed receipt")
    artifact_file = output_dir / "remote_artifacts.json"
    if not artifact_file.is_file():
        raise ArtifactValidationError("Remote model artifact hashes are unavailable")
    artifact_manifest = json.loads(artifact_file.read_text(encoding="utf-8"))
    if (artifact_manifest.get("job_id") != job_id
            or artifact_manifest.get("input_manifest_sha256") != journal.get("input_manifest_sha256")):
        raise ArtifactValidationError("Remote model artifact manifest belongs to a different snapshot")
    rows = artifact_manifest.get("artifacts")
    by_path = {row.get("path"): row for row in rows if isinstance(row, dict)} if isinstance(rows, list) else {}
    for relative in ("outputs/best_model.pt", "outputs/model_meta.json"):
        row = by_path.get(relative)
        local = output_dir / PurePosixPath(relative).name
        if (row is None or not local.is_file() or local.stat().st_size != row.get("size")
                or _sha256(local) != row.get("sha256")):
            raise ArtifactValidationError(f"Local remote-model artifact has changed: {local.name}")
    dataset_path = Path(receipt["dataset_path"]).resolve()
    if dataset_path != Path(journal["dataset_path"]).resolve() or not dataset_path.is_dir():
        raise ArtifactValidationError("Original remote training snapshot is unavailable locally")
    digest = journal.get("input_manifest_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ArtifactValidationError("Remote snapshot identity is invalid")
    context = RemoteJobContext(
        job_id=job_id, task=str(journal["task"]), output_dir=output_dir,
        dataset_path=dataset_path, profile=profile,
        input_manifest_sha256=digest,
    )
    _verify_local_snapshot(context)
    return context


def _operation_journal_path(context: RemoteJobContext, operation: str, spec: dict[str, Any]) -> Path:
    key = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    return context.output_dir / "remote_operations" / f"{operation}_{key}.json"


def _save_operation(path: Path, journal: dict[str, Any]) -> None:
    _atomic_json(path, journal)


def _launch_or_resume(
    context: RemoteJobContext, operation: str, spec: dict[str, Any],
    transport: SSHTransport, *, force_new: bool = False,
    input_files: dict[str, Path] | None = None,
) -> dict[str, Any]:
    journal_path = _operation_journal_path(context, operation, spec)
    journal: dict[str, Any] | None = None
    if journal_path.is_file():
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if (journal.get("job_id") != context.job_id or journal.get("profile") != context.profile.model_dump()
                or journal.get("spec") != spec):
            raise ArtifactValidationError("Operation journal is bound to a different job or server")
        if journal.get("state") == "failed" or (journal.get("state") == "completed" and force_new):
            journal = None
    if journal is not None:
        return journal

    op_id = f"op_{uuid.uuid4().hex}"
    journal = {
        "protocol_version": 1, "operation": operation, "job_id": context.job_id,
        "op_id": op_id, "profile": context.profile.model_dump(), "spec": spec,
        "state": "preparing", "created_at": time.time(),
    }
    _save_operation(journal_path, journal)
    local_dir = context.output_dir / "remote_operations" / op_id
    local_dir.mkdir(parents=True, exist_ok=True)
    code_archive = _bundle_backend(local_dir)
    spec_path = local_dir / "spec.json"
    _atomic_json(spec_path, spec)
    for source, name in ((code_archive, "code.tar.gz"), (spec_path, "spec.json")):
        transport.upload(context.profile, source, f"runs/{op_id}/{name}")
    for relative, source in (input_files or {}).items():
        if not relative.startswith("inputs/") or any(part in ("", ".", "..") for part in relative.split("/")):
            raise ValueError("Remote operation input path is unsafe")
        if not source.is_file():
            raise FileNotFoundError(source)
        transport.upload(context.profile, source, f"runs/{op_id}/{relative}")
    code_dir = _remote_path(context.profile, op_id, "code")
    code_archive_remote = _remote_path(context.profile, op_id, "code.tar.gz")
    mkdir = transport.exec(context.profile, ["mkdir", "-p", code_dir])
    unpack = transport.exec(context.profile, ["tar", "-xzf", code_archive_remote, "-C", code_dir]) if mkdir.returncode == 0 else mkdir
    if unpack.returncode != 0:
        journal["state"] = "failed"
        journal["error"] = unpack.stderr
        _save_operation(journal_path, journal)
        raise RuntimeError(f"Could not prepare remote {operation} worker: {unpack.stderr}")
    journal["state"] = "launching"
    _save_operation(journal_path, journal)
    try:
        journal["remote_handle"] = transport.launch(
            context.profile,
            ["-m", "backend.remote.worker", operation, "--spec", _remote_path(context.profile, op_id, "spec.json")],
            op_id,
        )
    except Exception as exc:
        raise RemoteDisconnected(f"Cannot determine whether the remote {operation} worker started: {exc}") from exc
    journal["state"] = "launched"
    _save_operation(journal_path, journal)
    return journal


def run_remote_operation_artifacts(
    context: RemoteJobContext, operation: str, extra_spec: dict[str, Any],
    *, transport: SSHTransport | None = None, force_new: bool = False,
    timeout_seconds: int = 3600, input_files: dict[str, Path] | None = None,
) -> dict[str, Path]:
    """Run once and download only hash-verified run-relative output files."""
    _verify_local_snapshot(context)
    transport = transport or SSHTransport()
    spec = {
        "protocol_version": 1, "operation": operation, "job_id": context.job_id,
        "task": context.task, "input_manifest_sha256": context.input_manifest_sha256,
        **extra_spec,
    }
    journal_path = _operation_journal_path(context, operation, spec)
    try:
        journal = _launch_or_resume(context, operation, spec, transport, force_new=force_new,
                                    input_files=input_files)
    except RemoteDisconnected:
        raise
    except Exception:
        if journal_path.is_file():
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if journal.get("state") == "preparing":
                journal["state"] = "failed"
                _save_operation(journal_path, journal)
        raise
    op_id = journal["op_id"]
    status_path = _remote_path(context.profile, op_id, "status.json")
    start = time.monotonic()
    missing_status_polls = 0
    while True:
        status = _remote_json(transport, context.profile, status_path)
        if status is None:
            missing_status_polls += 1
            handle = journal.get("remote_handle")
            if missing_status_polls >= 2 and isinstance(handle, str) and handle:
                running = transport.is_running(context.profile, op_id, handle)
                if running is False:
                    journal["state"] = "failed"
                    journal["error"] = f"Remote {operation} worker exited before publishing status"
                    _save_operation(journal_path, journal)
                    raise RuntimeError(journal["error"])
        if status is not None:
            if (status.get("protocol_version") != 1 or status.get("job_id") != context.job_id
                    or status.get("operation") != operation):
                raise ArtifactValidationError("Operation status is bound to a different job")
            if status.get("status") == "completed":
                break
            if status.get("status") in ("failed", "aborted"):
                journal["state"] = status["status"]
                journal["error"] = status.get("error")
                _save_operation(journal_path, journal)
                raise RuntimeError(f"Remote {operation} {status['status']}: {status.get('error') or ''}")
        if time.monotonic() - start > timeout_seconds:
            raise RemoteDisconnected(f"Remote {operation} has not returned a terminal status; reconnect to the same run")
        time.sleep(OP_POLL_INTERVAL_SECONDS)

    manifest = _remote_json(transport, context.profile, _remote_path(context.profile, op_id, "artifacts.json"))
    if manifest is None or (manifest.get("protocol_version") != 1 or manifest.get("job_id") != context.job_id
                            or manifest.get("operation") != operation
                            or manifest.get("input_manifest_sha256") != context.input_manifest_sha256):
        raise ArtifactValidationError("Remote operation artifact manifest is invalid")
    if operation == "flowchart_run":
        expected_models = sorted(spec.get("models", []), key=lambda item: item["job_id"])
        if (manifest.get("model_refs") != expected_models
                or manifest.get("selected_image_sha256") != spec.get("image_sha256")):
            raise ArtifactValidationError("Remote flowchart model or image binding is invalid")
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or not entries or len(entries) > 1000:
        raise ArtifactValidationError("Remote operation artifact list is missing")
    local_dir = context.output_dir / "remote_operations" / op_id
    local_dir.mkdir(parents=True, exist_ok=True)
    downloads: dict[str, Path] = {}
    staged_paths: list[Path] = []
    try:
        for entry in entries:
            if not isinstance(entry, dict):
                raise ArtifactValidationError(f"Remote {operation} artifact entry is invalid")
            relative = entry.get("path")
            if (not isinstance(relative, str) or not relative.startswith("outputs/")
                    or any(part in ("", ".", "..") for part in relative.split("/"))
                    or "\\" in relative):
                raise ArtifactValidationError(f"Remote {operation} artifact path is unsafe")
            if (not isinstance(entry.get("size"), int) or entry["size"] <= 0
                    or not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64):
                raise ArtifactValidationError(f"Remote {operation} artifact metadata is invalid")
            with tempfile.NamedTemporaryFile(dir=local_dir, prefix=".artifact-", delete=False) as temporary:
                staged = Path(temporary.name)
            staged_paths.append(staged)
            try:
                transport.download(context.profile, f"runs/{op_id}/{relative}", staged)
            except Exception as exc:
                raise RemoteDisconnected(f"Could not retrieve remote {operation} output: {exc}") from exc
            if staged.stat().st_size != entry["size"] or _sha256(staged) != entry["sha256"]:
                raise ArtifactValidationError(f"Remote {operation} output hash mismatch")
            destination = local_dir.joinpath(*PurePosixPath(relative).parts)
            if not destination.resolve().is_relative_to(local_dir.resolve()):
                raise ArtifactValidationError("Remote operation output escaped its local directory")
            downloads[relative] = staged
        published: dict[str, Path] = {}
        for relative, staged in downloads.items():
            destination = local_dir.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, destination)
            published[relative] = destination
        journal["state"] = "completed"
        _save_operation(journal_path, journal)
        return published
    finally:
        for staged in staged_paths:
            staged.unlink(missing_ok=True)


def run_remote_operation(
    context: RemoteJobContext, operation: str, extra_spec: dict[str, Any], artifact_name: str,
    *, transport: SSHTransport | None = None, force_new: bool = False, timeout_seconds: int = 3600,
    input_files: dict[str, Path] | None = None,
) -> Path:
    outputs = run_remote_operation_artifacts(
        context, operation, extra_spec, transport=transport, force_new=force_new,
        timeout_seconds=timeout_seconds, input_files=input_files,
    )
    relative = f"outputs/{artifact_name}"
    if relative not in outputs:
        raise ArtifactValidationError(f"Remote {operation} did not return {artifact_name}")
    return outputs[relative]


def _local_snapshot_file(context: RemoteJobContext, reference: str) -> Path:
    if not isinstance(reference, str) or not reference.startswith("input/data/"):
        raise ArtifactValidationError("Remote evaluation referenced a file outside its snapshot")
    relative = reference.removeprefix("input/data/")
    parts = relative.split("/")
    if not parts or any(part in ("", ".", "..") for part in parts) or "\\" in relative:
        raise ArtifactValidationError("Remote evaluation file path is unsafe")
    path = context.dataset_path.joinpath(*PurePosixPath(relative).parts).resolve()
    if not path.is_relative_to(context.dataset_path) or not path.is_file():
        raise ArtifactValidationError("Remote evaluation file is absent from the local snapshot")
    return path


def run_remote_evaluation(
    context: RemoteJobContext, *, force_recompute: bool = False,
    transport: SSHTransport | None = None,
) -> dict[str, Any]:
    artifact = run_remote_operation(
        context, "evaluate", {}, "eval_results.json",
        transport=transport, force_new=force_recompute,
    )
    result = json.loads(artifact.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or result.get("job_id") != context.job_id or result.get("task") != context.task:
        raise ArtifactValidationError("Remote evaluation result belongs to a different model")
    matrix = result.get("confusion_matrix")
    predictions = result.get("test_predictions")
    if not isinstance(matrix, dict) or not isinstance(predictions, list):
        raise ArtifactValidationError("Remote evaluation result has no sample mapping")
    for key, references in matrix.get("cell_samples", {}).items():
        if not isinstance(references, list):
            raise ArtifactValidationError(f"Invalid cell samples for {key}")
        matrix["cell_samples"][key] = [str(_local_snapshot_file(context, item)) for item in references]
    for row in predictions:
        if not isinstance(row, dict):
            raise ArtifactValidationError("Invalid remote prediction row")
        image = _local_snapshot_file(context, row.get("file_path"))
        row["file_path"] = str(image)
        row["thumbnail_url"] = (
            f"/api/dataset/thumbnail/{urllib.parse.quote(image.name)}?"
            f"file_path={urllib.parse.quote(str(image))}"
        )
    _atomic_json(context.output_dir / "eval_results.json", result)
    return result


def run_remote_inference(
    context: RemoteJobContext, image: Path, threshold: float, image_id: str,
    *, transport: SSHTransport | None = None,
) -> tuple[dict[str, Any], bytes]:
    image = Path(image).resolve(strict=True)
    if not image.is_file():
        raise FileNotFoundError(image)
    suffix = image.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        raise ValueError("Unsupported inspection image format")
    relative = f"inputs/image{suffix}"
    image_hash = _sha256(image)
    artifacts = run_remote_operation_artifacts(
        context, "infer", {
            "image_path": relative, "image_sha256": image_hash,
            "threshold": float(threshold), "image_id": image_id,
        }, transport=transport, input_files={relative: image},
    )
    result_file = artifacts.get("outputs/result.json")
    overlay_file = artifacts.get("outputs/overlay.png")
    if result_file is None or overlay_file is None:
        raise ArtifactValidationError("Remote inference result or overlay is missing")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if (not isinstance(result, dict) or result.get("image_sha256") != image_hash
            or result.get("image_path") != relative or result.get("overlay_path") != "outputs/overlay.png"):
        raise ArtifactValidationError("Remote inference result does not match the selected image")
    png = overlay_file.read_bytes()
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ArtifactValidationError("Remote inference overlay is not a PNG image")
    result["image_id"] = image_id
    result["overlay_base64"] = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    result.pop("image_path", None)
    result.pop("overlay_path", None)
    result.pop("image_sha256", None)
    return result, png


def _inspection_image_input(image: Path) -> tuple[Path, str, str]:
    image = Path(image).resolve(strict=True)
    if not image.is_file():
        raise FileNotFoundError(image)
    suffix = image.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        raise ValueError("Unsupported inspection image format")
    return image, f"inputs/image{suffix}", _sha256(image)


def _verified_preview_uri(reference: str | None, artifacts: dict[str, Path]) -> str | None:
    if reference is None:
        return None
    if not isinstance(reference, str) or reference not in artifacts:
        raise ArtifactValidationError("Flowchart preview is absent from the verified artifact manifest")
    content = artifacts[reference].read_bytes()
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    else:
        raise ArtifactValidationError("Flowchart preview is not a supported image")
    return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")


def run_remote_flowchart(
    contexts: list[RemoteJobContext], pipeline: dict[str, Any], image: Path,
    image_id: str | None = None, *, transport: SSHTransport | None = None,
) -> dict[str, Any]:
    if not contexts:
        raise ValueError("A flowchart needs at least one trained model")
    profile = contexts[0].profile.model_dump()
    if any(context.profile.model_dump() != profile for context in contexts):
        raise ArtifactValidationError("All flowchart models must belong to the same compute server")
    image, relative, image_hash = _inspection_image_input(image)
    primary = contexts[0]
    artifacts = run_remote_operation_artifacts(
        primary, "flowchart_run", {
            "models": [
                {"job_id": context.job_id, "task": context.task,
                 "input_manifest_sha256": context.input_manifest_sha256}
                for context in contexts
            ],
            "pipeline": pipeline,
            "image_path": relative,
            "image_sha256": image_hash,
            "image_id": image_id,
        }, transport=transport, input_files={relative: image},
    )
    result_file = artifacts.get("outputs/flowchart_result.json")
    if result_file is None:
        raise ArtifactValidationError("Remote flowchart result is missing")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if (not isinstance(result, dict) or result.get("image_path") != relative
            or result.get("image_sha256") != image_hash
            or result.get("model_job_ids") != sorted(context.job_id for context in contexts)):
        raise ArtifactValidationError("Remote flowchart result references a different selected image")
    result["image_path"] = str(image)
    result["image_id"] = image_id
    result.pop("image_sha256", None)
    result.pop("model_job_ids", None)
    result["annotated_image"] = _verified_preview_uri(result.get("annotated_image"), artifacts)
    crops = result.get("crops")
    if not isinstance(crops, list):
        raise ArtifactValidationError("Remote flowchart result has no crop list")
    for crop in crops:
        if not isinstance(crop, dict):
            raise ArtifactValidationError("Remote flowchart crop is invalid")
        crop["crop_thumbnail"] = _verified_preview_uri(crop.get("crop_thumbnail"), artifacts)
    return result


def run_remote_benchmark(
    context: RemoteJobContext, iterations: int, resolution: int,
    *, transport: SSHTransport | None = None,
) -> dict[str, Any]:
    artifact = run_remote_operation(
        context, "benchmark", {"iterations": iterations, "resolution": resolution},
        "benchmark.json", transport=transport,
    )
    result = json.loads(artifact.read_text(encoding="utf-8"))
    if (not isinstance(result, dict) or result.get("model_job_id") != context.job_id
            or result.get("task") != context.task or result.get("status") != "success"):
        raise ArtifactValidationError("Remote benchmark belongs to a different model")
    result.pop("model_job_id", None)
    result["model_path"] = str(context.output_dir / "best_model.pt")
    result["compute_profile_id"] = context.profile.id
    result["compute_server_name"] = context.profile.name
    return result


def _safe_package_file(reference: str) -> PurePosixPath:
    if not isinstance(reference, str) or not reference or "\\" in reference:
        raise ArtifactValidationError("Remote export has an unsafe package path")
    parts = reference.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ArtifactValidationError("Remote export has an unsafe package path")
    path = PurePosixPath(reference)
    if path.is_absolute():
        raise ArtifactValidationError("Remote export has an absolute package path")
    return path


def run_remote_export(
    context: RemoteJobContext, export_format: str, resolution: int,
    quantize_fp16: bool, package_name: str,
    *, transport: SSHTransport | None = None,
) -> dict[str, Any]:
    if export_format not in ("onnx", "torchscript"):
        raise ValueError("Export format must be onnx or torchscript")
    if quantize_fp16:
        raise ValueError("FP16 export is not implemented or verified")
    if not 32 <= resolution <= 2048:
        raise ValueError("Export resolution must be between 32 and 2048")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", package_name):
        raise ValueError("Remote package name must use ASCII letters, digits, dot, dash, or underscore")
    extra: dict[str, Any] = {
        "export_format": export_format, "resolution": resolution,
        "quantize_fp16": False, "package_name": package_name,
    }
    inputs: dict[str, Path] = {}
    evaluation = context.output_dir / "eval_results.json"
    if evaluation.is_file():
        payload = json.loads(evaluation.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("job_id") == context.job_id and payload.get("task") == context.task:
            extra["evaluation_path"] = "inputs/eval_results.json"
            extra["evaluation_sha256"] = _sha256(evaluation)
            inputs["inputs/eval_results.json"] = evaluation
    artifacts = run_remote_operation_artifacts(
        context, "export", extra, transport=transport, input_files=inputs,
    )
    result_file = artifacts.get("outputs/export_result.json")
    if result_file is None:
        raise ArtifactValidationError("Remote export result is missing")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if (not isinstance(result, dict) or result.get("status") != "success"
            or result.get("model_job_id") != context.job_id
            or result.get("export_format") != export_format
            or result.get("package_name") != package_name):
        raise ArtifactValidationError("Remote export does not match the requested model and format")
    archive_reference = result.get("package_path")
    if archive_reference != f"outputs/{package_name}.tar.gz" or archive_reference not in artifacts:
        raise ArtifactValidationError("Remote export archive is missing")
    files = result.get("manifest")
    if not isinstance(files, list) or not files or result.get("total_files") != len(files):
        raise ArtifactValidationError("Remote export internal file list is invalid")
    expected: dict[str, dict[str, Any]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise ArtifactValidationError("Remote export file record is invalid")
        relative = str(_safe_package_file(entry.get("path")))
        if (relative in expected or type(entry.get("size")) is not int or entry["size"] <= 0
                or not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64):
            raise ArtifactValidationError("Remote export file metadata is invalid")
        expected[relative] = entry

    from backend.engine.exporter import EXPORTS_DIR

    base = EXPORTS_DIR.resolve()
    base.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".remote-export-", dir=base))
    try:
        seen: set[str] = set()
        with tarfile.open(artifacts[archive_reference], "r:gz") as archive:
            for member in archive:
                if not member.isfile() or member.issym() or member.islnk():
                    raise ArtifactValidationError("Remote export archive contains a link or special file")
                prefix = f"{package_name}/"
                if not member.name.startswith(prefix):
                    raise ArtifactValidationError("Remote export archive escaped its package directory")
                relative = str(_safe_package_file(member.name[len(prefix):]))
                entry = expected.get(relative)
                if entry is None or relative in seen or member.size != entry["size"]:
                    raise ArtifactValidationError("Remote export archive file list does not match its manifest")
                seen.add(relative)
                target = stage / package_name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                reader = archive.extractfile(member)
                if reader is None:
                    raise ArtifactValidationError("Remote export archive file cannot be read")
                with reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                if _sha256(target) != entry["sha256"]:
                    raise ArtifactValidationError("Remote export internal file hash mismatch")
        if seen != set(expected):
            raise ArtifactValidationError("Remote export archive is missing a listed file")
        destination = base / package_name
        if destination.exists() or destination.is_symlink():
            destination = base / f"{package_name}_{uuid.uuid4().hex[:8]}"
        os.replace(stage / package_name, destination)
        result["package_name"] = destination.name
        result["package_path"] = str(destination)
        result["manifest"] = [
            {"name": Path(path).name, "path": path, "size_kb": round(row["size"] / 1024, 1),
             "sha256": row["sha256"]}
            for path, row in sorted(expected.items())
        ]
        result["compute_profile_id"] = context.profile.id
        result["compute_server_name"] = context.profile.name
        result.pop("model_job_id", None)
        return result
    finally:
        if stage.exists():
            shutil.rmtree(stage)
