"""Team guidance, scoped work queues, edit ownership and review endpoints."""
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.routes_project import get_current_project
from backend.api.routes_dataset_versions import _source_path
from backend.engine import team_data as td, dataset_metadata as dm


router = APIRouter(prefix='/api/team-data', tags=['team-data'])


class ActorRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    actor: str = Field('operator', min_length=1, max_length=100)


class BookRequest(ActorRequest):
    expected_version: int = Field(..., ge=0)
    title: str = Field(..., min_length=1, max_length=200)
    categories: list[dict[str, Any]] = Field(..., min_length=1, max_length=255)


class SettingsRequest(ActorRequest):
    expected_revision: int = Field(..., ge=1)
    changes: dict[str, Any]


class ImageRequest(ActorRequest):
    expected_revision: int = Field(..., ge=1)


class AssignmentRequest(ImageRequest):
    assignee: Optional[str] = Field(None, min_length=1, max_length=100)
    priority: int = Field(50, ge=0, le=100)


class LeaseRequest(ImageRequest):
    ttl_seconds: int = Field(120, ge=30, le=900)


class OwnedLeaseRequest(LeaseRequest):
    lease_token: str = Field(..., min_length=1, max_length=200)


class ReviewRequest(ImageRequest):
    decision: Literal['approve', 'reject']
    reason: str = Field('', max_length=4000)


def _context(request):
    project = get_current_project(request)
    return project, _source_path(project)


def _identity(request, fallback, roles=None):
    account = getattr(request.state, 'account_user', None)
    if account is None: return fallback
    project = get_current_project(request); store = request.app.state.accounts
    role = store.project_role(account['id'], project['id'])
    if roles is not None and role not in roles: raise HTTPException(403, '현재 프로젝트 역할로 이 작업을 수행할 수 없습니다.')
    return account['username']


def _error(exc):
    if isinstance(exc, dm.RevisionConflict): return HTTPException(409, {'message': str(exc), 'current': exc.current})
    if isinstance(exc, KeyError): return HTTPException(404, '현재 프로젝트에서 이미지를 찾지 못했습니다.')
    return HTTPException(422, str(exc))


def _run(action):
    try: return action()
    except (ValueError, KeyError, OSError) as exc: raise _error(exc) from exc


def _members(request, project):
    account = getattr(request.state, 'account_user', None)
    if account is None: return []
    return request.app.state.accounts.project_members(project['id'], account['id'])


@router.get('')
def workspace(request: Request):
    project, source = _context(request)
    result = _run(lambda: td.workspace(project, source)); result['members'] = _members(request, project)
    account = getattr(request.state, 'account_user', None)
    if account:
        result['actor'] = {'id': account['id'], 'name': account['username'],
                           'role': request.app.state.accounts.project_role(account['id'], project['id'])}
    return result


@router.post('/books')
def publish_book(body: BookRequest, request: Request):
    project, source = _context(request); actor = _identity(request, body.actor, {'owner', 'reviewer'})
    return _run(lambda: td.publish_book(project, source, body.expected_version, actor, body.title, body.categories))


@router.put('/settings')
def settings(body: SettingsRequest, request: Request):
    project, source = _context(request); actor = _identity(request, body.actor, {'owner', 'reviewer'})
    return _run(lambda: td.update_settings(project, source, body.expected_revision, actor, body.changes))


@router.get('/queue')
def queue(request: Request, assignee: Optional[str] = None, state: Optional[str] = None,
          offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    project, source = _context(request)
    return _run(lambda: td.work_queue(project, source, assignee, state, offset, limit))


@router.get('/readiness')
def readiness(request: Request):
    project, source = _context(request)
    return _run(lambda: td.training_readiness(project, source))


@router.get('/images/{image_uuid}')
def get_image(image_uuid: str, request: Request):
    project, source = _context(request)
    def load():
        root, selected, annotations = td._context(project, source)
        with dm.metadata_transaction(root, selected, annotations) as ledger:
            return {'image': td.public_image(td._current_row(ledger, project, selected, image_uuid))}
    return _run(load)


@router.post('/images/{image_uuid}/assign')
def assign(image_uuid: str, body: AssignmentRequest, request: Request):
    project, source = _context(request); actor = _identity(request, body.actor, {'owner', 'reviewer'})
    members = _members(request, project)
    allowed = {member['name'] for member in members if member['role'] in {'owner','labeler','trainer','reviewer'}} if members else None
    return _run(lambda: td.assign_image(project, source, image_uuid, body.expected_revision, actor, body.assignee, body.priority, allowed))


@router.post('/images/{image_uuid}/lease/acquire')
def acquire(image_uuid: str, body: LeaseRequest, request: Request):
    project, source = _context(request); actor = _identity(request, body.actor, {'owner','labeler','trainer','reviewer'})
    return _run(lambda: td.acquire_lease(project, source, image_uuid, body.expected_revision, actor, body.ttl_seconds))


@router.post('/images/{image_uuid}/lease/renew')
def renew(image_uuid: str, body: OwnedLeaseRequest, request: Request):
    project, source = _context(request); actor = _identity(request, body.actor, {'owner','labeler','trainer','reviewer'})
    return _run(lambda: td.renew_lease(project, source, image_uuid, body.expected_revision, actor, body.lease_token, body.ttl_seconds))


@router.post('/images/{image_uuid}/lease/release')
def release(image_uuid: str, body: OwnedLeaseRequest, request: Request):
    project, source = _context(request); actor = _identity(request, body.actor, {'owner','labeler','trainer','reviewer'})
    return _run(lambda: td.release_lease(project, source, image_uuid, body.expected_revision, actor, body.lease_token))


@router.post('/images/{image_uuid}/review')
def review(image_uuid: str, body: ReviewRequest, request: Request):
    project, source = _context(request); actor = _identity(request, body.actor, {'owner','reviewer'})
    return _run(lambda: td.review_image(project, source, image_uuid, body.expected_revision, actor, body.decision, body.reason))


@router.post('/images/{image_uuid}/adjudicate')
def adjudicate(image_uuid: str, body: ReviewRequest, request: Request):
    project, source = _context(request); actor = _identity(request, body.actor, {'owner','reviewer'})
    return _run(lambda: td.adjudicate_image(project, source, image_uuid, body.expected_revision, actor, body.decision, body.reason))


class QualityProfileRequest(ActorRequest):
    """A gold-sample label review profile (E05)."""
    task: Literal['detection', 'segmentation']
    reference_labelset: str = Field(..., min_length=1, max_length=64)
    candidate_labelset: str = Field(..., min_length=1, max_length=64)
    gold_images: list[str] = Field(..., min_length=1, max_length=2000)
    tolerance: float = Field(0.5, gt=0.1, le=1.0)


class GoldPolicyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    include_gold_in_training: bool
    actor: str = Field('this computer', min_length=1, max_length=100)


QUALITY_ROLES = {'owner', 'reviewer'}  # E05: who makes profiles, runs reports, retires them and sets the gold policy


def _quality(call):
    from backend.engine import annotation_quality as aq
    try:
        return call(aq)
    except FileNotFoundError as exc:
        raise HTTPException(404, 'No such label review profile or report.') from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/quality/profiles')
def quality_profiles(request: Request):
    project, _source = _context(request)
    return _quality(lambda aq: {'profiles': aq.list_profiles(project), 'gold_policy': aq.gold_policy(project)})


@router.post('/quality/profiles')
def create_quality_profile(body: QualityProfileRequest, request: Request):
    project, _source = _context(request)
    actor = _identity(request, body.actor, QUALITY_ROLES)
    return _quality(lambda aq: aq.create_profile(project, task=body.task, reference_labelset=body.reference_labelset,
                                                 candidate_labelset=body.candidate_labelset, gold_images=body.gold_images,
                                                 tolerance=body.tolerance, actor=actor))


@router.post('/quality/profiles/{profile_id}/reports')
def run_quality_report(profile_id: str, request: Request):
    project, _source = _context(request)
    _identity(request, 'this computer', QUALITY_ROLES)
    return _quality(lambda aq: aq.run_report(project, profile_id))


@router.post('/quality/profiles/{profile_id}/retire')
def retire_quality_profile(profile_id: str, body: ActorRequest, request: Request):
    """Stop using a profile: its gold images return to ordinary use; its reports stay readable (stale)."""
    project, _source = _context(request)
    actor = _identity(request, body.actor, QUALITY_ROLES)
    return _quality(lambda aq: {'profile_id': profile_id, **aq.retire_profile(project, profile_id, actor)})


@router.get('/quality/reports')
def quality_reports(request: Request, profile_id: Optional[str] = None):
    project, _source = _context(request)
    return _quality(lambda aq: {'reports': aq.list_reports(project, profile_id)})


@router.get('/quality/reports/{report_id}')
def read_quality_report(report_id: str, request: Request):
    project, _source = _context(request)
    return _quality(lambda aq: aq.read_report(project, report_id))


@router.put('/quality/gold-policy')
def set_quality_gold_policy(body: GoldPolicyRequest, request: Request):
    project, _source = _context(request)
    actor = _identity(request, body.actor, QUALITY_ROLES)
    return _quality(lambda aq: aq.set_gold_policy(project, body.include_gold_in_training, actor))
