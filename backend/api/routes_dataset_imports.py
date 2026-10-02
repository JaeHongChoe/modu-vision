"""Durable dataset imports and indexed revisions (S3-01) over HTTP.

Every path comes from the request's scoped project: the registered source folder is read, the project folder owns the
image identities, and the context registry's project namespace keys the job and the index. A caller never names a
source, an output folder or an actor. A team server never follows links out of its registered source; a local desktop
may (a source that links a NAS folder). A finished import activates nothing: accept is explicit and compare-and-swap.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.engine.dataset_import_job import KIND as IMPORT_KIND, DatasetImportJobs, ImportNotAcceptable, ImportSpec
from backend.engine.dataset_index import DatasetIndex, RevisionNotActivatable, StaleActiveRevision, index_path
from backend.engine.job_store import JobConflict, ledger

router = APIRouter(prefix="/api/dataset", tags=["Dataset imports"])
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_JOBS_LOCK = threading.Lock()


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task: Literal["classification", "detection", "segmentation", "anomaly", "patch_classification"]
    invalid_policy: Literal["reject", "exclude"] = "exclude"
    verify: bool = False
    follow_links: bool = False


class AcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_active: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{32}$")


def _scope(request: Request) -> tuple:
    """(context, project_key, project) of the request; a request without a project context reads nothing."""
    from backend.api.routes_project import get_current_project
    from backend.contracts.context import get_project_context
    context = get_project_context(request)
    return context, request.app.state.context_registry.project_key(context), get_current_project(request)


def _jobs(request: Request) -> DatasetImportJobs:
    """One coordinator per ledger and index, so live progress survives between requests of this process."""
    store, path = ledger(), index_path(request.app.state.context_registry.root)
    with _JOBS_LOCK:
        jobs = getattr(request.app.state, "dataset_imports", None)
        if jobs is None or jobs.store.path != store.path or jobs.index.path != path:
            jobs = DatasetImportJobs(store, DatasetIndex(path))
            request.app.state.dataset_imports = jobs
        return jobs


def _view(jobs: DatasetImportJobs, job_id: str, project_key: str) -> dict:
    try:
        return jobs.view(job_id, project_key)
    except KeyError:
        raise HTTPException(404, "Import not found in this project") from None


@router.post("/imports")
def submit_import(payload: ImportRequest, request: Request):
    context, project_key, project = _scope(request)
    key = request.headers.get("Idempotency-Key")
    if key is not None and not _IDEMPOTENCY_KEY.fullmatch(key):
        raise HTTPException(422, "Idempotency-Key must be 1-128 letters, digits or the characters . _ : -")
    if not project.get("source_dataset_dir"):
        raise HTTPException(409, "Register the project's source folder before importing")
    if payload.follow_links and context.mode != "local":
        raise HTTPException(403, "A team server reads only its registered source folder; links are not followed")
    spec = ImportSpec(project_root=project["project_dir"], source_root=project["source_dataset_dir"], task=payload.task,
                      invalid_policy=payload.invalid_policy, verify=payload.verify, follow_links=payload.follow_links,
                      annotation_root=project.get("annotations_dir"))
    jobs = _jobs(request)
    try:
        ref = jobs.submit(context, project_key, spec, key)
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if ref.created:
        jobs.start(ref.id)
    return {**_view(jobs, ref.id, project_key), "idempotent_replay": not ref.created}


class ArchiveImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    artifact: dict
    task: Literal["classification", "detection", "segmentation", "anomaly", "patch_classification"]
    invalid_policy: Literal["reject", "exclude"] = "exclude"
    verify: bool = False


@router.post("/imports/archive")
def submit_archive_import(payload: ArchiveImportRequest, request: Request):
    """Import an uploaded ZIP: it is extracted into a project-owned folder (never into a source), then indexed like
    a registered source. The reference is checked against this project on every request; the same archive content is
    extracted once, and an existing folder is reused only while it still holds exactly the files of its extraction
    receipt (otherwise the archive is extracted again into a new folder). The job records which artifact it read.

    Extraction runs inside this request (bounded by the archive limits); very large archives belong in the job thread,
    which is not implemented yet. Reusing a folder reads every extracted byte again to verify it."""
    from backend.api.routes_artifacts import authorize_artifact, get_artifact_store
    from backend.contracts.context import ArtifactRef
    from backend.engine.artifact_store import ArtifactError
    from backend.engine.dataset_archive_input import (ArchiveNoSpace, ArchiveRefused, extract_dataset_archive,
                                                      verify_extraction)
    context, project_key, project = _scope(request)
    key = request.headers.get("Idempotency-Key")
    if key is not None and not _IDEMPOTENCY_KEY.fullmatch(key):
        raise HTTPException(422, "Idempotency-Key must be 1-128 letters, digits or the characters . _ : -")
    try:
        ref = ArtifactRef(**payload.artifact)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"artifact must be an artifact reference (id, revision, sha256): {exc}") from exc
    authorize_artifact(request)
    store = get_artifact_store(request)
    try:
        store.reference(context, ref)  # a forged or foreign reference never reaches an existing folder
    except ArtifactError as exc:
        raise HTTPException(getattr(exc, "status", 409), str(exc)) from exc
    artifact = {"id": ref.id, "revision": ref.revision, "sha256": ref.sha256}
    jobs = _jobs(request)
    if key is not None:  # a retried request is answered before anything is extracted or verified again
        reserved = jobs.store.reserved(context, project_key, IMPORT_KIND, key)
        if reserved is not None:
            spec_json = json.loads(reserved["spec_json"])
            intended = {"project_root": project["project_dir"], "task": payload.task, "invalid_policy": payload.invalid_policy,
                        "verify": payload.verify, "follow_links": False, "artifact": artifact,
                        "annotation_root": project.get("annotations_dir")}
            recorded = {name: spec_json.get(name) for name in intended}  # every field but the extracted folder
            if recorded != intended:
                raise HTTPException(409, "This idempotency key was already used for a different request.")
            return {**_view(jobs, reserved["id"], project_key), "idempotent_replay": True, "source_root": spec_json["source_root"]}
    imports = Path(project["project_dir"]) / "dataset_imports"
    target = imports / ref.sha256
    if target.exists() and not verify_extraction(target, ref.sha256):
        # The first folder was changed (files added, moved or edited): keep it as it is, reuse an earlier verified
        # copy of this archive, or extract a new one.
        copies = sorted(path for path in imports.glob(f"{ref.sha256}.*") if path.is_dir())
        target = next((path for path in copies if verify_extraction(path, ref.sha256)),
                      imports / f"{ref.sha256}.{uuid.uuid4().hex[:12]}")
    if not target.exists():
        try:
            with store.open(context, ref) as handle:
                extract_dataset_archive(handle, target, archive_sha256=ref.sha256)
        except ArtifactError as exc:
            raise HTTPException(getattr(exc, "status", 409), str(exc)) from exc
        except ArchiveNoSpace as exc:
            raise HTTPException(507, str(exc)) from exc
        except ArchiveRefused as exc:
            raise HTTPException(422, str(exc)) from exc
    spec = ImportSpec(project_root=project["project_dir"], source_root=str(target), task=payload.task,
                      invalid_policy=payload.invalid_policy, verify=payload.verify, follow_links=False, artifact=artifact,
                      annotation_root=project.get("annotations_dir"))
    try:
        submitted = jobs.submit(context, project_key, spec, key)
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if submitted.created:
        jobs.start(submitted.id)
    return {**_view(jobs, submitted.id, project_key), "idempotent_replay": not submitted.created,
            "source_root": str(target)}


@router.get("/imports/{job_id}")
def get_import(job_id: str, request: Request):
    _context, project_key, _project = _scope(request)
    return _view(_jobs(request), job_id, project_key)


@router.post("/imports/{job_id}/cancel")
def cancel_import(job_id: str, request: Request):
    context, project_key, _project = _scope(request)
    jobs = _jobs(request)
    _view(jobs, job_id, project_key)
    return jobs.cancel(job_id, project_key, context.actor_id)


@router.post("/imports/{job_id}/accept")
def accept_import(job_id: str, payload: AcceptRequest, request: Request):
    _context, project_key, _project = _scope(request)
    jobs = _jobs(request)
    _view(jobs, job_id, project_key)
    try:
        active = jobs.accept(job_id, project_key, payload.revision_id, payload.expected_active)
    except (ImportNotAcceptable, RevisionNotActivatable, StaleActiveRevision) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"active_revision": active, "job_id": job_id}


@router.get("/revisions")
def list_revisions(request: Request):
    _context, project_key, _project = _scope(request)
    jobs = _jobs(request)
    return {"active_revision": jobs.index.active(project_key), "revisions": jobs.index.revisions(project_key)}


@router.get("/revisions/{revision_id}/images")
def revision_images(revision_id: str, request: Request, cursor: Optional[str] = Query(None, max_length=4096),
                    limit: int = Query(100, ge=1, le=500), label: Optional[str] = Query(None, max_length=512),
                    split: Optional[Literal["train", "val", "test"]] = None, valid: Optional[bool] = None,
                    annotation_label: Optional[str] = Query(None, max_length=512), annotation_error: Optional[bool] = None):
    """Rows of one immutable revision with their source annotations, in relative-path order; the cursor is bound to
    the revision and filters. label is the folder label, annotation_label a label of the source annotations."""
    _context, project_key, _project = _scope(request)
    try:
        return _jobs(request).index.page(project_key, revision_id, cursor=cursor, limit=limit, label=label,
                                         split=split, valid=valid, annotation_label=annotation_label,
                                         annotation_error=annotation_error)
    except KeyError:
        raise HTTPException(404, "Revision not found in this project") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/revisions/{revision_id}/duplicates")
def revision_duplicates(revision_id: str, request: Request, cursor: Optional[str] = Query(None, max_length=4096),
                        limit: int = Query(50, ge=1, le=200),
                        kind: Optional[Literal["conflicting", "cross_split"]] = None):
    """Groups of valid images with the same bytes; conflicting groups carry different labels, cross-split groups sit in
    more than one split. Duplicates are reported, never removed."""
    _context, project_key, _project = _scope(request)
    try:
        return _jobs(request).index.duplicates(project_key, revision_id, cursor=cursor, limit=limit, kind=kind)
    except KeyError:
        raise HTTPException(404, "Revision not found in this project") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/revisions/{revision_id}/gaps")
def revision_gaps(revision_id: str, request: Request):
    """Folders the revision could not read; under the exclude policy they are the receipt of what is missing."""
    _context, project_key, _project = _scope(request)
    try:
        return {"revision_id": revision_id, "gaps": _jobs(request).index.gaps(project_key, revision_id)}
    except KeyError:
        raise HTTPException(404, "Revision not found in this project") from None


def recover_imports_at_startup(app) -> dict:
    """No import thread survives a restart: the backend that owns the data folder completes imports whose revision
    was sealed and marks the others interrupted. Another live backend's imports are left alone."""
    import logging
    from backend.api.routes_training import _own_data_folder
    store = ledger()
    if not _own_data_folder(store.path.parent):
        return {"completed": [], "interrupted": [], "skipped": "another backend owns the data folder"}
    try:
        jobs = DatasetImportJobs(store, DatasetIndex(index_path(app.state.context_registry.root)))
        app.state.dataset_imports = jobs
        return jobs.recover_orphans()
    except Exception:  # a recovery problem never blocks the backend from starting
        logging.getLogger(__name__).exception("Dataset import recovery failed at startup")
        return {"completed": [], "interrupted": [], "error": True}
