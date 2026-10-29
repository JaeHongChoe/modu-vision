"""Project-independent plan preparation, behind existing account/capability checks."""
import json

from fastapi import APIRouter, HTTPException, Request

from backend.contracts.context import declared_context
from backend.engine.assistant_tasks import MAX_BODY_BYTES, TaskError, TaskScope

router = APIRouter(prefix='/api/assistant-tasks', tags=['assistant-tasks'])


def _scope(request):
    registry = request.app.state.context_registry
    account = getattr(request.state, 'account_user', None)
    accounts = getattr(request.app.state, 'accounts', None)
    if accounts is not None:
        if account is None: raise HTTPException(401, 'Account login required')
        # Recheck identity after body collection rather than treating the initial
        # middleware result as authority for the rest of a long request.
        try:
            token = request.state.account_session_token
            current = (accounts.authenticate(token) if request.headers.get('authorization')
                       else accounts.cookie_session(token))
        except ValueError as exc: raise HTTPException(401, 'Account session unavailable') from exc
        if current['id'] != account['id']: raise HTTPException(403, 'Account changed')
    actor = account['id'] if account else registry.local_actor_id
    explicit = declared_context(request.headers)
    if explicit:
        if explicit.actor_id != actor or explicit.mode != ('team' if account else 'local'):
            raise HTTPException(403, 'Explicit task context does not identify the authenticated actor')
        if explicit.workspace_id != registry.workspace_id:
            # A registered local copy may share the logical project ID while
            # retaining its distinct workspace. Do not load/normalize a project
            # just to store an actor's plan.
            local = getattr(request.app.state, 'local_projects', None)
            if local is None or account is None:
                raise HTTPException(403, 'Explicit task workspace is not registered')
            with local.db() as db:
                location = db.execute('SELECT permission_key FROM locations WHERE workspace_id=? AND project_id=?',
                                      (explicit.workspace_id, explicit.project_id)).fetchone()
            if not location or accounts.project_role_exact(actor, location['permission_key']) is None:
                raise HTTPException(403, 'Explicit task workspace requires current membership')
    # The server actor/workspace owns the whole task, even if one approved step
    # creates the first project or advances into a registered project copy.
    return TaskScope(registry.workspace_id, actor)


async def _body(request):
    media = request.headers.get('content-type', '').split(';', 1)[0].lower().strip()
    if media != 'application/json' and not media.endswith('+json'):
        raise HTTPException(415, 'Task preparation requires JSON')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BODY_BYTES: raise HTTPException(413, 'Task request exceeds 64 KiB')
        raw.extend(chunk)
    try:
        # Duplicate properties must not produce a different plan for a second
        # JSON implementation or silently replace a person-visible argument.
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError('Duplicate task JSON property')
                result[key] = value
            return result
        return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise HTTPException(422, 'Invalid task JSON') from exc


def _call(request, method, *args):
    try:
        return getattr(request.app.state.assistant_tasks, method)(_scope(request), *args)
    except TaskError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get('')
def list_tasks(request: Request):
    return {'tasks': _call(request, 'list')}


@router.post('')
async def create_task(request: Request):
    body = await _body(request)
    return {'task': _call(request, 'create', body)}


@router.get('/{task_id}')
def read_task(task_id: str, request: Request):
    return {'task': _call(request, 'read', task_id)}


@router.post('/{task_id}/events')
async def task_event(task_id: str, request: Request):
    body = await _body(request)
    if isinstance(body, dict) and body.get('type') == 'quality_result':
        raise HTTPException(422, 'Quality observations must be read from the authenticated assessment endpoint')
    return {'task': _call(request, 'event', task_id, body)}


@router.post('/{task_id}/quality')
async def task_quality(task_id: str, request: Request):
    body = await _body(request)
    if not isinstance(body, dict) or set(body) != {'binding', 'expected_revision', 'step_id', 'attempt_id', 'assessment_id'}:
        raise HTTPException(422, 'Quality result identity fields are required')
    from backend.api.routes_model_operations import quality_assessment
    assessment = quality_assessment(body['assessment_id'], request)
    task = _call(request, 'read', task_id)
    step = next((row for row in task['steps'] if row['id'] == body['step_id']), None)
    if (assessment.get('task_id') != task_id or assessment.get('step_id') != body['step_id']
            or step is None or assessment.get('evaluation_id') != (step.get('receipt') or {}).get('evaluation_id')):
        raise HTTPException(409, 'Quality assessment belongs to another completed task evaluation')
    keys = ('assessment_id', 'evaluation_id', 'subject_kind', 'status', 'decision_source', 'policy_version',
            'policy_revision', 'qualification_revision_id', 'quality_qualified', 'assessed_at')
    summary = {key: assessment[key] for key in keys if key in assessment}
    from backend.engine.image_truth import digest
    summary['assessment_sha256'] = digest(assessment)
    summary['reasons'] = [str(reason)[:300] for reason in assessment.get('reasons', [])[:8]]
    event = {key: body[key] for key in ('binding', 'expected_revision', 'step_id', 'attempt_id')}
    return {'task': _call(request, 'event', task_id, {**event, 'type': 'quality_result', 'receipt': {'quality_assessment': summary}})}
