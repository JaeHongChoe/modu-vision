"""The built-in assistant's run log (docs/product-direction-easy-labeling-agent.md §4.6).

The assistant itself (keys, provider calls) lives in the desktop main process; the backend only keeps the project-local
log of what it did, so the log stays with the project like the other audit records. The routes follow the sibling
pattern: the current project comes from the request scope and, on a shared server, SharedAuthorizationMiddleware decides
who may write (these paths have no route policy of their own, so writes need the project owner role there).
"""
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from backend.api.routes_project import get_current_project
from backend.api.shared_authorization import request_actor
from backend.engine import assistant_log

router = APIRouter(prefix='/api/assistant', tags=['assistant'])


class AssistantRunEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str = Field(pattern=r'^run_[a-f0-9]{24}$')
    tool: str = Field(pattern=r'^[a-z][a-z0-9_]{1,63}$')
    tier: Literal['read', 'navigate', 'write', 'forbidden', 'conversation']
    summary: str = Field(default='', max_length=300)
    args_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: StrictBool
    result: Literal['ok', 'refused', 'error', 'cancelled']
    error_code: Optional[str] = Field(default=None, pattern=r'^[a-z0-9_.-]{1,64}$')
    provider: Optional[Literal['openai-compatible', 'anthropic', 'chatgpt']] = None
    link: Optional[str] = Field(default=None, pattern=r'^[A-Za-z0-9_.:-]{1,120}$')


def _project_dir(request: Request) -> Path:
    return Path(get_current_project(request)['project_dir'])


@router.get('/runs')
def assistant_runs(request: Request, limit: int = Query(200, ge=1, le=1000)):
    try:
        return assistant_log.list_runs(_project_dir(request), limit)
    except assistant_log.UnsafeRunEntry as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/runs')
def record_assistant_run(body: AssistantRunEntry, request: Request):
    try:
        return assistant_log.append_run(_project_dir(request), body.model_dump(exclude_none=True),
                                        actor=request_actor(request, 'local'))
    except assistant_log.UnsafeRunEntry as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/runs/archive')
def archive_assistant_runs(request: Request):
    try:
        return assistant_log.archive_runs(_project_dir(request))
    except assistant_log.UnsafeRunEntry as exc:
        raise HTTPException(409, str(exc)) from exc
