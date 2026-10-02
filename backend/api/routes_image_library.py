"""Searching the project's validated images and resolving saved selections by identity (S2-07) over HTTP.

Every query reads one immutable revision of the persistent index: the project's active revision unless a revision of
this project is named. Selections are kept as {image_uuid, sha256, relative_path} and resolved against the revision,
so a moved or replaced file is reported instead of a different image being picked by its path.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.engine.image_query import load_ledger_rows, query_images, resolve_image_ids

router = APIRouter(prefix="/api/dataset/library", tags=["Image library"])


class Selection(BaseModel):
    """A saved choice is identified by its image id and content digest only; no path is sent (none is matched, and a
    client-supplied path field would be read as a file location by the shared-server guard)."""
    model_config = ConfigDict(extra="forbid")
    image_uuid: Optional[str] = Field(default=None, max_length=64)
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    selections: List[Selection] = Field(max_length=500)


def _revision(request: Request, revision_id: Optional[str]) -> tuple:
    """(index, project_key, project, revision row) of the named revision or the project's active one."""
    from backend.api.routes_dataset_imports import _jobs, _scope
    _context, project_key, project = _scope(request)
    index = _jobs(request).index
    rows = {row["revision_id"]: row for row in index.revisions(project_key)}
    chosen = revision_id or index.active(project_key)
    if chosen is None:
        raise HTTPException(409, "No validated dataset revision is active; validate the source and accept a revision first")
    if chosen not in rows:
        raise HTTPException(404, "Revision not found in this project")
    return index, project_key, project, rows[chosen]


def _with_paths(items: list, source_root: str) -> list:
    for item in items:
        item["file_path"] = str(Path(source_root) / item["relative_path"])
    return items


@router.get("/images")
def library_images(request: Request, revision_id: Optional[str] = Query(None, pattern=r"^[0-9a-f]{32}$"),
                   q: Optional[str] = Query(None, max_length=256), label: Optional[str] = Query(None, max_length=512),
                   split: Optional[Literal["train", "val", "test"]] = None, state: Optional[Literal["valid", "invalid"]] = None,
                   annotation_label: Optional[str] = Query(None, max_length=512), tag: Optional[str] = Query(None, max_length=256),
                   product: Optional[str] = Query(None, max_length=256), lot: Optional[str] = Query(None, max_length=256),
                   workflow_state: Optional[Literal["unworked", "needs_review", "approved"]] = None,
                   usage_state: Optional[Literal["active", "not_used"]] = None,
                   cursor: Optional[str] = Query(None, max_length=8192), limit: int = Query(100, ge=1, le=200)):
    """One page of images in relative-path order; filters on the metadata ledger scan a bounded number of rows per
    page and return a cursor that continues the scan."""
    from backend.engine.dataset_metadata import ledger_path
    index, project_key, project, revision = _revision(request, revision_id)
    filters = {"label": label, "split": split, "state": state, "annotation_label": annotation_label, "tag": tag,
               "product": product, "lot": lot, "workflow_state": workflow_state, "usage_state": usage_state}
    # rows show tags, product, lot and review state, so the ledger is read for every page (cached while unchanged)
    ledger = load_ledger_rows(ledger_path(project["project_dir"], revision["source_root"], project.get("annotations_dir")))
    try:
        page = query_images(index.path, project_key, revision["revision_id"], ledger=ledger, query=q, filters=filters,
                            cursor=cursor, limit=limit)
    except KeyError:
        raise HTTPException(404, "Revision not found in this project") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _with_paths(page["items"], revision["source_root"])
    return {**page, "active": revision["active"], "source_root": revision["source_root"]}


@router.post("/resolve")
def resolve_selections(payload: ResolveRequest, request: Request):
    """The state of each saved selection in the revision: found, changed (other bytes at its path), moved (its bytes
    elsewhere, with candidates) or missing. A selection without an identity is never matched by its path."""
    index, project_key, _project, revision = _revision(request, payload.revision_id)
    try:
        results = resolve_image_ids(index.path, project_key, revision["revision_id"],
                                    [selection.model_dump() for selection in payload.selections])
    except KeyError:
        raise HTTPException(404, "Revision not found in this project") from None
    for result in results:
        for row in ([result["current"]] if result["current"] else []) + result["candidates"]:
            row["file_path"] = str(Path(revision["source_root"]) / row["relative_path"])
    return {"revision_id": revision["revision_id"], "active": revision["active"], "results": results}
