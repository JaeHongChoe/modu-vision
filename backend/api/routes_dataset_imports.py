"""Durable dataset imports and indexed revisions (S3-01) over HTTP.

Every path comes from the request's scoped project: the registered source folder is read, the project folder owns the
image identities, and the context registry's project namespace keys the job and the index. A caller never names a
source, an output folder or an actor. A team server never follows links out of its registered source; a local desktop
may (a source that links a NAS folder). A finished import activates nothing: accept is explicit and compare-and-swap.
"""
from __future__ import annotations

import re
import threading
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.engine.dataset_import_job import DatasetImportJobs, ImportNotAcceptable, ImportSpec
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
                      invalid_policy=payload.invalid_policy, verify=payload.verify, follow_links=payload.follow_links)
    jobs = _jobs(request)
    try:
        ref = jobs.submit(context, project_key, spec, key)
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if ref.created:
        jobs.start(ref.id)
    return {**_view(jobs, ref.id, project_key), "idempotent_replay": not ref.created}


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
                    split: Optional[Literal["train", "val", "test"]] = None, valid: Optional[bool] = None):
    """Rows of one immutable revision, in relative-path order; the cursor is bound to the revision and filters."""
    _context, project_key, _project = _scope(request)
    try:
        return _jobs(request).index.page(project_key, revision_id, cursor=cursor, limit=limit, label=label,
                                         split=split, valid=valid)
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
