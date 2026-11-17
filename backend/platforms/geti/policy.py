"""The current Modu project/member authority always precedes child contact."""
from pathlib import Path
import re
from fastapi import HTTPException
from backend.api.routes_project import get_current_project
from backend.contracts.context import get_project_context

_TRAIN = {'owner', 'trainer', 'reviewer'}
_ADOPT = {'owner', 'reviewer'}


def authority(request, *, action='read'):
    project = get_current_project(request)
    context = get_project_context(request)
    account = getattr(request.state, 'account_user', None)
    registry = request.app.state.context_registry
    if context.project_id != project['id']:
        raise HTTPException(403, 'Project context no longer matches the selected project')
    if account is not None:
        if context.mode != 'team' or context.actor_id != account['id']:
            raise HTTPException(403, 'Project actor does not match the authenticated session')
        token = getattr(request.state, 'account_session_token', None)
        if token:
            try: current = request.app.state.accounts.authenticate(token)
            except ValueError as exc: raise HTTPException(401, 'Account session expired or unavailable') from exc
            if current['id'] != context.actor_id: raise HTTPException(403, 'Project actor changed')
        permission_key = getattr(request.state, 'account_project_key', None)
        role = (request.app.state.accounts.project_role_exact(account['id'], permission_key) if permission_key
                else request.app.state.accounts.project_role(account['id'], project['id']))
        if role is None: raise HTTPException(403, 'Project membership was revoked')
    else:
        if context.mode != 'local' or context.actor_id != registry.local_actor_id:
            raise HTTPException(403, 'A current local project authority is required')
        role = 'owner'
    if action == 'train' and role not in _TRAIN or action == 'adopt' and role not in _ADOPT:
        raise HTTPException(403, 'Current project role cannot perform this platform action')
    return project, context, registry.project_key(context), {'can_prepare': role in _TRAIN, 'can_train': role in _TRAIN, 'can_adopt': role in _ADOPT}


def admitted_image(project, receipt, requested):
    source = project.get('source_dataset_dir')
    if not source: raise HTTPException(409, '현재 프로젝트의 원본 데이터를 먼저 연결하세요.')
    root = Path(source).expanduser().resolve()
    path = Path(requested).expanduser().resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(403, 'Prediction image must belong to the admitted project source')
    from backend.engine.grouped_dataset_views import source_image_paths
    if path not in {image.resolve() for image in source_image_paths(root, project['task'])}:
        raise HTTPException(403, 'Prediction image is outside the admitted source inventory')
    return path


def public_data(value):
    """Retain provenance/hash/relative identity; host storage and transport stay private."""
    if isinstance(value, dict):
        return {key: public_data(item) for key, item in value.items()
                if key == 'relative_path' or not (key.endswith(('_path', '_dir', '_root')) or key in {'secret', 'port', 'base_url', 'python', 'entrypoint', 'source_path', 'instance_id'})}
    if isinstance(value, list): return [public_data(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r'https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?\S*', '[owned runtime]', value)
        value = re.sub(r'(?:/Users/|/home/|/private/|/tmp/|[A-Z]:\\)[^\s"\']+', '[local path]', value)
        return value[:3000]
    return value
