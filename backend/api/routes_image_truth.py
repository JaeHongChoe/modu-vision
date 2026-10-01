"""Reviewer-authored truth; approval of annotations supplies no default verdict."""
from typing import Literal
import json

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.routes_project import get_current_project
from backend.api.shared_authorization import request_actor
from backend.engine import dataset_metadata as dm, image_truth

router = APIRouter(prefix='/api/image-truth', tags=['image-truth'])


def require_role(request, project, allowed):
    account = getattr(request.state, 'account_user', None)
    if account and request.app.state.accounts.project_role(account['id'], project['id']) not in allowed:
        raise HTTPException(403, 'This project role cannot perform this truth/evaluation action')


class TruthRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_path: str
    task: str = Field(min_length=1, max_length=100)
    classes: list[str] = Field(min_length=1, max_length=255)
    class_roles: dict[str, Literal['normal','defect','unknown']] | None = None
    participating_tasks: list[str] | None = Field(default=None, min_length=2, max_length=16)
    verdict: Literal['OK','NG','UNKNOWN']
    defect_classes: list[str] = Field(default_factory=list, max_length=255)
    reviewer: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    expected_image_revision: int = Field(ge=1)
    note: str = Field(default='', max_length=2000)


def execute(action):
    try: return action()
    except dm.RevisionConflict as exc:
        raise HTTPException(409, {'message':str(exc),'current':exc.current}) from exc
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('')
def get_truth(request: Request, image_path: str, task: str, classes: list[str] = Query(...), class_roles: str | None = None,
              participating_tasks: list[str] | None = Query(default=None)):
    project = get_current_project(request)
    return execute(lambda: image_truth.read_truth(project, image_path, task=task, classes=classes,
                                                class_roles=json.loads(class_roles) if class_roles else None,
                                                participating_tasks=participating_tasks))


@router.put('')
def put_truth(body: TruthRequest, request: Request):
    project = get_current_project(request); require_role(request, project, {'owner','reviewer'})
    payload = body.model_dump(); payload['reviewer'] = request_actor(request, body.reviewer)
    return execute(lambda: image_truth.declare_truth(project, **payload))
