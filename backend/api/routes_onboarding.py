"""The first-run guide and its example project (S2-01).

The app builds the example through the same project, data, training, flow and inspection calls a user makes; this
router only keeps the guide's state, provides the synthetic example dataset (drawn on this computer, nothing downloaded)
and marks a project made from it as an example, which approval and release then refuse.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix='/api/onboarding', tags=['onboarding'])


class DismissRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dismissed: bool = True


def _store():
    from backend.engine.onboarding import OnboardingStore
    return OnboardingStore()


def _team_request(request: Request) -> bool:
    """A request made with a team account (shared server): the guide's record and the example belong to one person's
    own computer, never to the shared server."""
    return getattr(request.state, 'account_user', None) is not None


def _refuse_on_team_server(request: Request) -> None:
    if _team_request(request):
        raise HTTPException(409, '예제는 개인 모드의 이 컴퓨터에서만 만들 수 있습니다. 팀 서버 연결을 끊고 다시 시작하세요.')


def _example_projects(request: Request) -> list[dict]:
    """The listed projects (this user's recent ones, or their team projects) that are examples, so the guide offers to
    open them instead of making another."""
    from backend.api.routes_project import list_projects
    try:
        rows = list_projects(request)['projects']
    except Exception:  # a broken history hides no guide; it only lists nothing
        return []
    found = []
    for row in rows:
        try:
            saved = row if row.get('example') else json.loads((Path(row['project_dir']) / 'project.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if isinstance(saved, dict) and saved.get('example'):
            found.append({'id': saved.get('id'), 'name': saved.get('name'), 'project_dir': str(row['project_dir'])})
    return found


@router.get('')
def onboarding_state(request: Request):
    from backend.engine.demo_dataset import DEMO_CLASSES, DEMO_ID, DEMO_SPLITS, DEMO_TASK, DEMO_VERSION
    example = {'id': DEMO_ID, 'version': DEMO_VERSION, 'task': DEMO_TASK, 'classes': list(DEMO_CLASSES),
               'images': sum(DEMO_SPLITS.values()) * len(DEMO_CLASSES)}
    if _team_request(request):
        # On a shared server the guide never opens by itself and offers no example.
        return {'version': 1, 'dismissed': True, 'team': True, 'example': example, 'example_projects': []}
    return {**_store().state(), 'team': False, 'example': example, 'example_projects': _example_projects(request)}


@router.post('/dismiss')
def dismiss(req: DismissRequest, request: Request):
    if _team_request(request):
        return {'version': 1, 'dismissed': True}  # nothing to keep on a shared server
    from backend.engine.onboarding import OnboardingRecordError
    try:
        return _store().dismiss(req.dismissed)
    except OnboardingRecordError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/example-dataset')
def example_dataset(request: Request):
    """The synthetic example dataset folder (drawn from its seed when needed) for the app to import like any folder."""
    from backend.engine.onboarding import ensure_example_dataset
    _refuse_on_team_server(request)
    try:
        made = ensure_example_dataset()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(500, f'예제 데이터를 만들지 못했습니다: {exc}') from exc
    manifest = made['manifest']
    return {'folder': made['folder'], 'created': made['created'], 'demo_id': manifest['demo_id'], 'version': manifest['version'],
            'task': manifest['task'], 'classes': manifest['classes'], 'images': len(manifest['images']), 'synthetic': True}


@router.post('/example-project')
def mark_example_project(request: Request):
    """Mark the current project as the example: only a project whose source is the app's example dataset. An example
    project's results are never a quality approval: approval and release refuse it."""
    from backend.api.routes_project import (ProjectConfigResponse, _activate_project, _project_context_mutation,
                                            _write_json, get_current_project)
    from backend.engine.demo_dataset import DEMO_ID, DEMO_VERSION
    from backend.engine.onboarding import is_example_source
    _refuse_on_team_server(request)
    with _project_context_mutation(get_current_project(request)) as project:
        if not is_example_source(project.get('source_dataset_dir')):
            raise HTTPException(409, '예제 데이터로 만든 프로젝트만 예제로 표시할 수 있습니다.')
        project['example'] = {'id': DEMO_ID, 'version': DEMO_VERSION, 'synthetic': True, 'quality_approval': 'not_applicable',
                              'marked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        project['updated_at'] = project['example']['marked_at']
        validated = ProjectConfigResponse.model_validate(project).model_dump()
        _write_json(Path(validated['project_dir']) / 'project.json', validated)
        return _activate_project(request, validated)
