"""Authenticated upload/resume and immutable managed artifact content routes."""
from contextlib import contextmanager
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from backend.contracts.context import ArtifactRef, get_project_context
from backend.engine.artifact_store import ArtifactError, ArtifactStore, CHUNK_BYTES

router = APIRouter(prefix='/api/artifacts', tags=['artifacts'])


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: str
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    size_bytes: int = Field(ge=0)


class PinRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    artifact_ref: ArtifactRef
    name: str = Field(min_length=1, max_length=128)


def authorize_artifact(request, *, write=False, owner=False):
    """Recheck sessions and membership on every operation, including resumed IO."""
    context = get_project_context(request)
    accounts = request.app.state.accounts
    if accounts is not None:
        try:
            account = accounts.authenticate(getattr(request.state, 'account_session_token', ''))
            if account['id'] != context.actor_id:
                raise ValueError('Actor changed')
            role = accounts.project_role(account['id'], context.project_id)
        except ValueError as exc:
            raise HTTPException(401, 'Artifact session is unavailable') from exc
        allowed = {'owner'} if owner else {'labeler', 'trainer', 'reviewer', 'owner'} if write else {'viewer', 'labeler', 'trainer', 'reviewer', 'owner'}
        if role not in allowed:
            raise HTTPException(403, 'Current project permission does not allow this artifact operation')
    elif context.mode != 'local' or context.actor_id != request.app.state.context_registry.local_actor_id:
        raise HTTPException(403, 'Artifact operation requires the desktop process capability')
    return context


def get_artifact_store(request):
    store = getattr(request.app.state, 'artifact_store', None)
    if store is None:
        store = ArtifactStore.configured(request.app.state.context_registry)
        request.app.state.artifact_store = store
    return store


@contextmanager
def storage_errors():
    try:
        yield
    except ArtifactError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, 'Artifact storage is unavailable; retry the same upload after recovery') from exc


def receipt(context, **values):
    return {'project_context': context.model_dump(), **values}


@router.post('/uploads')
def begin_upload(body: UploadRequest, request: Request):
    context = authorize_artifact(request, write=True)
    with storage_errors():
        return receipt(context, upload=get_artifact_store(request).begin(context, body.kind, body.sha256, body.size_bytes))


@router.get('/uploads/{upload_id}')
def upload_status(upload_id: str, request: Request):
    context = authorize_artifact(request)
    with storage_errors():
        return receipt(context, upload=get_artifact_store(request).status(context, upload_id))


@router.put('/uploads/{upload_id}')
async def upload_chunk(upload_id: str, request: Request, offset: int = Query(ge=0)):
    context = authorize_artifact(request, write=True)
    if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
        raise HTTPException(415, 'Upload chunks require application/octet-stream')
    data = bytearray()
    async for block in request.stream():
        if len(data) + len(block) > CHUNK_BYTES:
            raise HTTPException(413, 'Upload chunk exceeds 4 MiB')
        data.extend(block)
    authorize_artifact(request, write=True)
    with storage_errors():
        upload = await run_in_threadpool(get_artifact_store(request).append, context, upload_id, offset, bytes(data))
        return receipt(context, upload=upload)


@router.post('/uploads/{upload_id}/complete')
def complete_upload(upload_id: str, request: Request):
    context = authorize_artifact(request, write=True)
    with storage_errors():
        ref = get_artifact_store(request).complete(context, upload_id, authorize=lambda: authorize_artifact(request, write=True))
        return receipt(context, artifact_ref=ref.model_dump(), state='referenced')


@router.delete('/uploads/{upload_id}')
def cancel_upload(upload_id: str, request: Request):
    context = authorize_artifact(request, write=True)
    with storage_errors():
        get_artifact_store(request).cancel(context, upload_id)
        return receipt(context, state='cancelled')


def _range(value, size):
    if value is None:
        return 0, size, False
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value) if len(value) <= 128 else None
    if match is None or not any(match.groups()) or size == 0:
        raise HTTPException(416, 'Invalid artifact byte range', headers={'Content-Range': f'bytes */{size}'})
    start_text, end_text = match.groups()
    if start_text:
        start, end = int(start_text), min(int(end_text) + 1, size) if end_text else size
    else:
        start, end = max(0, size - int(end_text)), size
    if start >= end or start >= size:
        raise HTTPException(416, 'Artifact byte range is unavailable', headers={'Content-Range': f'bytes */{size}'})
    return start, end, True


@router.get('/{artifact_id}/content')
def artifact_content(artifact_id: str, request: Request, revision: int = Query(gt=0), sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    context = authorize_artifact(request)
    try:
        ref = ArtifactRef(id=artifact_id, revision=revision, sha256=sha256)
    except ValueError as exc:
        raise HTTPException(422, 'Invalid artifact reference') from exc
    opened = None
    try:
        with storage_errors():
            opened = get_artifact_store(request).open(context, ref)
            handle = opened.__enter__()
            handle.seek(0, 2); size = handle.tell()
            start, end, partial = _range(request.headers.get('range'), size)
            authorize_artifact(request)
            handle.seek(start)
    except Exception:
        if opened is not None:
            opened.__exit__(None, None, None)
        raise
    def chunks():
        try:
            remaining = end - start
            while remaining:
                authorize_artifact(request)
                block = handle.read(min(CHUNK_BYTES, remaining))
                if not block:
                    raise ArtifactError('Verified download ended unexpectedly')
                remaining -= len(block)
                yield block
        finally:
            opened.__exit__(None, None, None)
    headers = {'ETag': '"' + ref.sha256 + '"', 'Accept-Ranges': 'bytes', 'Content-Length': str(end - start),
               'X-Artifact-Id': ref.id, 'X-Artifact-Revision': str(ref.revision), 'X-Artifact-Sha256': ref.sha256,
               'X-Artifact-Project': context.project_id}
    if partial:
        headers['Content-Range'] = f'bytes {start}-{end - 1}/{size}'
    return StreamingResponse(chunks(), status_code=206 if partial else 200, media_type='application/octet-stream', headers=headers)


@router.post('/{artifact_id}/pins')
def pin_artifact(artifact_id: str, body: PinRequest, request: Request):
    context = authorize_artifact(request, owner=True)
    if artifact_id != body.artifact_ref.id:
        raise HTTPException(422, 'Pin artifact ID does not match its reference')
    with storage_errors():
        return receipt(context, pin=get_artifact_store(request).pin(context, body.artifact_ref, body.name))


@router.delete('/pins/{pin_id}')
def unpin_artifact(pin_id: str, request: Request):
    context = authorize_artifact(request, owner=True)
    with storage_errors():
        get_artifact_store(request).unpin(context, pin_id)
        return receipt(context, state='unpinned')


@router.delete('/{artifact_id}')
def release_artifact(artifact_id: str, body: ArtifactRef, request: Request):
    context = authorize_artifact(request, owner=True)
    if artifact_id != body.id:
        raise HTTPException(422, 'Artifact ID does not match its reference')
    with storage_errors():
        get_artifact_store(request).release(context, body)
        return receipt(context, artifact_ref=body.model_dump(), state='released')


@router.post('/gc')
def collect_artifacts(request: Request):
    context = authorize_artifact(request, owner=True)
    with storage_errors():
        return receipt(context, collection=get_artifact_store(request).collect_garbage())
