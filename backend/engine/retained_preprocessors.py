"""Explicit project-owned references to unchanged retained rotation inputs.

This is a model data binding, not a source alias, release approval or runtime
capability. The original specialist verifier checks the original model's own
training source and immutable backups on every use. Inspection models continue
to use the active project's source through their original resolvers.
"""
from __future__ import annotations
from backend.engine.original_owned_selected_compute_request import selected_pin_open, selected_pin_closed

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading

SCHEMA = "modu-vision.project-retained-rotation-binding/v1"
DIRECTORY = ".retained-preprocessors"
_JOB = re.compile(r"(?:[0-9a-f]{32}|job_[A-Za-z0-9][A-Za-z0-9_-]{0,119})\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PIN_NAMES = {"source_project", "checkpoint", "metadata", "job_receipt", "version_manifest", "family_manifest"}
_TRANSPORT = ContextVar("project_retained_rotation_transport", default=None)


class RetainedPreprocessorError(ValueError):
    pass


def _require(value, message):
    if not value:
        raise RetainedPreprocessorError(message)


def _raw(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_uid, info.st_gid]


def _path(value):
    path = Path(value)
    _require(path.is_absolute() and ".." not in path.parts and path.resolve(strict=True) == path,
             "Retained model paths must be exact registered paths")
    _require(all(not parent.is_symlink() for parent in (*path.parents, path)),
             "Retained model paths cannot contain aliases")
    return path


def _pin(value, *, maximum=4 * 1024 * 1024, body=False):
    path = _path(value)
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
             and 0 < before.st_size <= maximum, "Retained model file is unavailable or unsafe")
    digest = hashlib.sha256(); chunks = []
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        selected_pin_open(fd,path)
        _require(_raw(os.fstat(fd)) == _raw(before), "Retained model descriptor changed")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                if body:
                    chunks.append(chunk)
        _require(_raw(os.fstat(fd)) == _raw(before), "Retained model changed while reading")
    finally:
        os.close(fd)
        selected_pin_closed(fd)
    _require(_raw(path.lstat()) == _raw(before), "Retained model path changed while reading")
    result = {"path": str(path), "raw9": _raw(before), "size": before.st_size,
              "sha256": digest.hexdigest()}
    return (result, b"".join(chunks)) if body else result


def _json(value):
    pin, raw = _pin(value, body=True)
    def pairs(rows):
        result = {}
        for key, item in rows:
            _require(key not in result, "Retained binding has duplicate fields")
            result[key] = item
        return result
    def nonfinite(value):
        raise RetainedPreprocessorError("Retained binding has a nonfinite number")
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    _require(type(result) is dict, "Retained binding must be an object")
    return result, pin


def _exact(pin):
    _require(type(pin) is dict and set(pin) == {"path", "raw9", "size", "sha256"},
             "Retained model pin schema changed")
    _require(_pin(pin["path"], maximum=512 * 1024 * 1024) == pin,
             "Retained model or original provenance changed")


def read_registered_project(directory, identifier):
    """Read an already authorized registry entry without project normalization."""
    root = _path(directory)
    _require(root.is_dir(), "Registered retained source project is unavailable")
    value, pin = _json(root / "project.json")
    _require(value.get("id") == identifier and value.get("project_dir") == str(root),
             "Registered retained source project identity changed")
    for key, name in (("models_dir", "models"), ("dataset_dir", "dataset")):
        _require(value.get(key) == str(root / name) and _path(root / name).is_dir(),
                 "Registered retained source project storage changed")
    source = value.get("source_dataset_dir")
    _require(type(source) is str and _path(source).is_dir(),
             "Registered retained source dataset is unavailable")
    return {**value, "_retained_manifest_pin": pin}


def _target(project):
    root = _path(project["project_dir"])
    value, pin = _json(root / "project.json")
    source = project.get("source_dataset_dir")
    _require(value.get("id") == project.get("id") and value.get("project_dir") == str(root)
             and type(source) is str and value.get("source_dataset_dir") == source
             and _path(source).is_dir(), "Active project or source changed before retained binding")
    return {"project_id": project["id"], "project_dir": str(root), "source_dataset_dir": source,
            "active_labelset_id": project.get("active_labelset_id", "default"),
            "project_manifest_pin": pin}


def _location(project, job_id, *, create=False):
    _require(type(job_id) is str and _JOB.fullmatch(job_id), "Invalid retained rotation model job")
    root = _path(project["project_dir"])
    directory = root / DIRECTORY
    if not directory.exists() and not directory.is_symlink():
        if not create:
            return None
        directory.mkdir(mode=0o700)
    directory = _path(directory)
    info = directory.lstat()
    _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
             and info.st_uid == os.geteuid(), "Retained binding directory must be private")
    return directory / (job_id + ".json")


def retained_rotation_jobs(project):
    directory = Path(project["project_dir"]) / DIRECTORY
    if not directory.exists() and not directory.is_symlink():
        return []
    _path(directory)
    _require(directory.is_dir() and stat.S_IMODE(directory.stat().st_mode) == 0o700
             and directory.stat().st_uid == os.geteuid(), "Retained binding directory is unsafe")
    entries = list(directory.iterdir())
    _require(len(entries) <= 128 and all(p.suffix == ".json" and _JOB.fullmatch(p.stem) for p in entries),
             "Retained binding registry contains unknown entries")
    return sorted(path.stem for path in entries)


def _source_model(source_project, job_id):
    from backend.engine.specialized_models import resolve_specialized_checkpoint
    root = _path(source_project["project_dir"])
    checkpoint, metadata = resolve_specialized_checkpoint(source_project["models_dir"], job_id, "rotation",
                                                          source_project["source_dataset_dir"])
    checkpoint = _path(checkpoint)
    _require(checkpoint == root / "models" / "rotation" / job_id / "best_model.pt",
             "Retained rotation model left its original project")
    stored_metadata, metadata_pin = _json(checkpoint.parent / "model_meta.json")
    receipt, receipt_pin = _json(checkpoint.parent / "job_receipt.json")
    training = stored_metadata.get("training_provenance")
    _require(metadata == stored_metadata and type(training) is dict and training.get("family_task") == "rotation"
             and receipt.get("job_id") == job_id and receipt.get("task") == "rotation"
             and receipt.get("status") == "completed"
             and receipt.get("source_dataset_path") == source_project["source_dataset_dir"],
             "Retained rotation needs its original completed job and immutable family binding")
    version = _path(training["version_dir"])
    _require(version == root / "versions" / training["dataset_version_id"],
             "Retained rotation version left its original project")
    prepared = _path(stored_metadata["dataset_path"])
    _require(prepared.is_relative_to(root / "dataset"), "Retained rotation prepared data left its project")
    pins = {"source_project": source_project["_retained_manifest_pin"],
            "checkpoint": _pin(checkpoint, maximum=512 * 1024 * 1024),
            "metadata": metadata_pin, "job_receipt": receipt_pin,
            "version_manifest": _pin(version / "manifest.json"),
            "family_manifest": _pin(prepared / "rotation.json")}
    _require(stored_metadata.get("checkpoint_sha256") == receipt.get("checkpoint_sha256") == pins["checkpoint"]["sha256"],
             "Retained rotation metadata or job checkpoint digest changed")
    for key in ("dataset_version_id", "manifest_sha256", "dataset_fingerprint"):
        _require(receipt.get("training_provenance", {}).get(key) == training.get(key),
                 "Retained rotation job and model training binding differ")
    # Original guards run before and after pinning. No changed source/labels,
    # incomplete journal, wrong task, checkpoint or frozen backup is accepted.
    again, again_meta = resolve_specialized_checkpoint(source_project["models_dir"], job_id, "rotation",
                                                       source_project["source_dataset_dir"])
    _require(again == checkpoint and again_meta == metadata, "Retained rotation changed during validation")
    for pin in pins.values():
        _exact(pin)
    return checkpoint, metadata, pins


def bind_retained_rotation(project, source_project, *, job_id, source_project_manifest_sha256,
                           checkpoint_sha256, version_manifest_sha256):
    """Publish one explicit reference; never copy or rewrite original artifacts."""
    for digest in (source_project_manifest_sha256, checkpoint_sha256, version_manifest_sha256):
        _require(type(digest) is str and _HEX.fullmatch(digest), "Reviewed retained model digests are required")
    _require(type(job_id) is str and _JOB.fullmatch(job_id), "Invalid retained rotation model job")
    target = _target(project)
    _require(source_project["id"] != project["id"], "Use the original same-project model resolver")
    _require(not (Path(project["models_dir"]) / "rotation" / job_id).exists(),
             "An active-project rotation model already owns this job")
    checkpoint, _, pins = _source_model(source_project, job_id)
    _require(pins["source_project"]["sha256"] == source_project_manifest_sha256
             and pins["checkpoint"]["sha256"] == checkpoint_sha256
             and pins["version_manifest"]["sha256"] == version_manifest_sha256,
             "Reviewed retained model input digests changed")
    value = {"schema": SCHEMA, "job_id": job_id, "task": "rotation", "operation": "learned_rotation",
             "target": target, "source_project_id": source_project["id"], "pins": pins,
             "checkpoint_sha256": checkpoint_sha256, "model_quality_approved": False,
             "release_approval": False, "permission_authority": False}
    destination = _location(project, job_id, create=True)
    _require(not destination.exists(), "Retained rotation already has an explicit binding")
    _require(_target(project) == target, "Active project changed before retained binding publication")
    for pin in pins.values():
        _exact(pin)
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    # A partial own failed file is retained and refuses all later reads.
    return {"job_id": job_id, "task": "rotation", "operation": "learned_rotation",
            "source_project_id": source_project["id"], "checkpoint_sha256": checkpoint_sha256,
            "binding_sha256": hashlib.sha256(raw).hexdigest(), "model_quality_approved": False,
            "release_approval": False, "permission_authority": False}


def resolve_retained_rotation(project, job_id, source_dataset_path, source_project_resolver):
    destination = _location(project, job_id)
    if destination is None or (not destination.exists() and not destination.is_symlink()):
        return None
    _require(source_dataset_path == project.get("source_dataset_dir"), "Retained binding active source changed")
    value, binding_pin = _json(destination)
    _require(stat.S_IMODE(destination.stat().st_mode) == 0o600, "Retained binding must be private")
    _require(set(value) == {"schema", "job_id", "task", "operation", "target", "source_project_id", "pins",
                            "checkpoint_sha256", "model_quality_approved", "release_approval", "permission_authority"}
             and value["schema"] == SCHEMA and value["job_id"] == job_id and value["task"] == "rotation"
             and value["operation"] == "learned_rotation"
             and all(value[name] is False for name in ("model_quality_approved", "release_approval", "permission_authority"))
             and value["target"] == _target(project) and set(value["pins"]) == _PIN_NAMES,
             "Retained rotation binding or active project changed")
    _require(not (Path(project["models_dir"]) / "rotation" / job_id).exists(),
             "An active-project model conflicts with the retained reference")
    source_project = source_project_resolver(value["source_project_id"])
    _require(source_project["id"] != project["id"], "Retained source project changed")
    for pin in value["pins"].values():
        _exact(pin)
    checkpoint, metadata, fresh = _source_model(source_project, job_id)
    _require(fresh == value["pins"] and fresh["checkpoint"]["sha256"] == value["checkpoint_sha256"],
             "Retained source project or original model changed")
    _exact(binding_pin)
    return checkpoint, metadata


@contextmanager
def retained_rotation_transport(project, source_project_resolver):
    """Request-local data resolution for the original selected-compute call.

    It grants no runtime/worker permission. The ordinary operation path retains
    its own-project rule. Only the same active project object may revalidate an
    explicit original rotation binding while this original call is on stack.
    """
    _require(_TRANSPORT.get() is None and callable(source_project_resolver),
             "Retained transport scope is already active or unavailable")
    live = [True]
    token = _TRANSPORT.set((project, _target(project), source_project_resolver,
                             os.getpid(), threading.get_ident(), live))
    try:
        yield
    finally:
        live[0] = False
        _TRANSPORT.reset(token)


def retained_transport_model_root(project, job_id, task, requested):
    """Return only the exact revalidated original model root, otherwise None."""
    current = _TRANSPORT.get()
    if (type(current) is not tuple or len(current) != 6 or current[0] is not project or task != "rotation"
            or current[3] != os.getpid() or current[4] != threading.get_ident()
            or type(current[5]) is not list or len(current[5]) != 1 or current[5][0] is not True):
        return None
    _require(_target(project) == current[1], "Retained transport active project changed")
    if job_id not in retained_rotation_jobs(project):
        return None
    resolved = resolve_retained_rotation(project, job_id, project.get("source_dataset_dir"), current[2])
    _require(resolved is not None and resolved[0] == Path(requested)
             and _path(requested) == resolved[0], "Retained transport checkpoint differs from its explicit reference")
    return resolved[0].parents[2]
