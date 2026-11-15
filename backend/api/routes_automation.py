"""Authenticated operation discovery, independent of selected project data."""
from fastapi import APIRouter, Query, Request

from backend.automation.catalog import operation_catalog

router = APIRouter(prefix='/api/automation', tags=['automation'])


@router.get('/catalog')
def catalog(request: Request, group: str | None = None, operation: str | None = None,
            offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000),
            schemas: bool | None = None):
    return operation_catalog(request.app, group=group, operation=operation, offset=offset, limit=limit, schemas=schemas)
