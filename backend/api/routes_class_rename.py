"""Class rename and merge for the active label set: a preview with counts per store, then an apply behind a backup.

The engine (backend/engine/class_rename.py) decides what changes and validates the mapping (so every refusal is Korean);
this module adds the project context, the shared account role check (a bulk rewrite of every overlay is an owner action,
as SharedAuthorizationMiddleware already maps /api/class-rename/* to project.manage), the version lock and the project
mutation fence that label-set changes already use, and the data version taken before anything is written.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api import routes_dataset, routes_dataset_metadata as exchange, routes_team_data
from backend.api.routes_dataset_versions import _VERSION_LOCK, _snapshot, _source_path
from backend.api.routes_project import _project_context_mutation, get_current_project
from backend.engine import class_rename
from backend.engine.annotation_storage import (
    reset_request_annotation_root, reset_request_project_root, set_request_annotation_root, set_request_project_root,
)
from backend.engine.dataset_fingerprint import fingerprint_dataset

router = APIRouter(prefix='/api/class-rename', tags=['class-rename'])
ROLES = {'owner'}
NO_SOURCE = '이 프로젝트에 데이터 폴더가 연결되지 않았습니다. 1단계에서 데이터 폴더를 연결한 뒤 다시 시도하세요.'


# Lengths, counts and the confirmation value are checked by the engine, whose refusals are Korean (pydantic's are not).
class ClassMapping(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    from_name: str = Field(..., alias='from')
    to: str


class ClassRenamePreviewRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    mapping: list[ClassMapping]
    actor: str = 'operator'


class ClassRenameApplyRequest(ClassRenamePreviewRequest):
    # The preview's confirmation value: the apply recomputes it and refuses when any overlay changed since.
    expected_preview_sha256: str


def _mapping(body: ClassRenamePreviewRequest) -> list[dict[str, str]]:
    return [{'from': row.from_name, 'to': row.to} for row in body.mapping]


def _source(project: dict) -> Path:
    if not project.get('source_dataset_dir'):
        raise HTTPException(status_code=422, detail=NO_SOURCE)
    return _source_path(project)


def _scoped(project: dict) -> tuple:
    return (set_request_annotation_root(Path(project['annotations_dir'])),
            set_request_project_root(Path(project['project_dir'])))


def _release(tokens: tuple) -> None:
    reset_request_project_root(tokens[1])
    reset_request_annotation_root(tokens[0])


def _refusal(exc: Exception) -> HTTPException:
    if isinstance(exc, class_rename.Refused):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail='현재 프로젝트에서 이미지를 찾지 못했습니다. 이미지 목록을 새로고침한 뒤 다시 미리보기하세요.')
    if isinstance(exc, OSError):
        # Never the OS text: it is English and carries absolute paths.
        return HTTPException(status_code=422, detail='라벨 파일을 읽거나 쓰지 못했습니다. 라벨 파일을 연 프로그램을 닫고 다시 시도하세요.')
    return exchange._errors(exc)


@router.post('/preview')
def preview_class_rename(body: ClassRenamePreviewRequest, request: Request):
    project = get_current_project(request)
    source = _source(project)
    actor = routes_team_data._identity(request, body.actor, ROLES)
    tokens = _scoped(project)
    try:
        return class_rename.preview(project, source, _mapping(body), actor)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise _refusal(exc) from exc
    finally:
        _release(tokens)


@router.post('/apply')
def apply_class_rename(body: ClassRenameApplyRequest, request: Request):
    project = get_current_project(request)
    _source(project)
    actor = routes_team_data._identity(request, body.actor, ROLES)
    with _project_context_mutation(project) as current:
        # The source is read again inside the fence, from the same project record the rewrite uses.
        source = _source(current)
        tokens = _scoped(current)
        try:
            with _VERSION_LOCK:
                result = class_rename.apply(
                    current, source, _mapping(body), actor, body.expected_preview_sha256,
                    snapshot=lambda name, note: _snapshot(current, source, name, note, 'auto_backup'))
        except class_rename.RollbackFailed as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise _refusal(exc) from exc
        finally:
            _release(tokens)
        # The rename has landed: a fingerprint that cannot be read now must not look like a failed apply.
        try:
            result['dataset_fingerprint'] = fingerprint_dataset(
                source, studio_root=Path(current['annotations_dir']),
                split_manifest=routes_dataset._split_manifest_file(source), use_scope=False)
        except (ValueError, OSError):
            result['dataset_fingerprint'] = ''
        return result
