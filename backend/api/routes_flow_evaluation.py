"""Separate saved whole-flow evaluation history, with immutable cohort inputs."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from backend.api.routes_project import get_current_project
from backend.api.routes_image_truth import execute, require_role
from backend.engine import flow_evaluation
from backend.engine import whole_flow_approval
from backend.api.shared_authorization import request_actor

router = APIRouter(prefix='/api/flow-evaluations', tags=['flow-evaluations'])


class CohortRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    name: str = Field(default='Held-out test cohort', min_length=1, max_length=200)
    relative_paths: list[str] | None = Field(default=None, min_length=1, max_length=5000)


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    cohort_id: str = Field(pattern=r'^cohort_[0-9a-f]{32}$')


class FlowReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evaluation_id: str = Field(pattern=r'^eval_[0-9a-f]{32}$')
    policy: dict
    reviewer: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=8,max_length=2000)
    holdout_reviewed: bool
    expected_revision: str | None = Field(default=None,pattern=r'^flowapproval_[0-9a-f]{32}$')


class FlowSelectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: str | None = Field(default=None,pattern=r'^flowapproval_[0-9a-f]{32}$')
    reviewer: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=8,max_length=2000)


class FlowPackageReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    package_path: str
    device: str = Field(default='cpu',min_length=1,max_length=100)


class RuntimeFlowReviewRequest(FlowPackageReviewRequest):
    reviewer: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=8,max_length=2000)
    holdout_reviewed: StrictBool
    expected_revision: str | None = Field(default=None,pattern=r'^runtimeflow_[0-9a-f]{32}$')


def _review_package(project,path):
    from pathlib import Path
    package=Path(path).absolute()
    if (any(p.is_symlink() for p in (package,*package.parents)) or
            not package.resolve().is_relative_to(Path(project['project_dir']).resolve()/'exports')):
        raise ValueError('Select an unlinked package exported by the current project')
    return package


@router.post('/approvals/{revision_id}/runtime-preview')
def runtime_flow_preview(revision_id: str,body: FlowPackageReviewRequest,request: Request):
    from backend.engine.whole_flow_runtime_review import preview_runtime_flow
    project=get_current_project(request);require_role(request,project,{'owner','reviewer'})
    return execute(lambda:preview_runtime_flow(project,_review_package(project,body.package_path),revision_id,
        device=body.device,accounts=getattr(request.app.state,'accounts',None)))


@router.post('/approvals/{revision_id}/runtime-review')
def runtime_flow_review(revision_id: str,body: RuntimeFlowReviewRequest,request: Request):
    from backend.engine.whole_flow_runtime_review import approve_runtime_flow
    project=get_current_project(request);require_role(request,project,{'owner','reviewer'})
    payload=body.model_dump();package=payload.pop('package_path')
    payload['reviewer']=request_actor(request,body.reviewer)
    account=getattr(request.state,'account_user',None)
    return execute(lambda:approve_runtime_flow(project,_review_package(project,package),revision_id,**payload,
        authority_user_id=account['id'] if account else None,accounts=getattr(request.app.state,'accounts',None)))


@router.get('/approvals/active')
def active_flow_review(request: Request):
    return execute(lambda: whole_flow_approval.current_approval(get_current_project(request),accounts=getattr(request.app.state,'accounts',None)))


@router.post('/approvals')
def approve_flow_review(body: FlowReviewRequest, request: Request):
    project=get_current_project(request);require_role(request,project,{'owner','reviewer'})
    payload=body.model_dump();payload['reviewer']=request_actor(request,body.reviewer)
    account=getattr(request.state,'account_user',None)
    return execute(lambda: whole_flow_approval.approve_flow(project,**payload,
        authority_user_id=account['id'] if account else None,accounts=getattr(request.app.state,'accounts',None)))


@router.put('/approvals/{revision_id}/select')
def select_flow_review(revision_id: str, body: FlowSelectionRequest, request: Request):
    project=get_current_project(request);require_role(request,project,{'owner','reviewer'})
    payload=body.model_dump();payload['reviewer']=request_actor(request,body.reviewer)
    return execute(lambda: whole_flow_approval.select_approval(project,revision_id,**payload,
        accounts=getattr(request.app.state,'accounts',None)))


@router.post('/approvals/{revision_id}/qualify-package')
def qualify_flow_review(revision_id: str,body: FlowPackageReviewRequest,request: Request):
    from pathlib import Path
    project=get_current_project(request);require_role(request,project,{'owner','reviewer'})
    package=Path(body.package_path).absolute()
    def qualify():
        if not package.resolve().is_relative_to(Path(project['project_dir']).resolve()/'exports'):
            raise ValueError('Select a package exported by the current project')
        return whole_flow_approval.qualify_package(project,package,revision_id,device=body.device,
            accounts=getattr(request.app.state,'accounts',None))
    return execute(qualify)


@router.get('/scope/{version_id}')
def scope(version_id: str, request: Request):
    def describe():
        project = get_current_project(request)
        value = flow_evaluation.describe_scope(project, version_id)
        if value['task'] == 'mixed':
            graph, _ = flow_evaluation.saved_graph(project, version_id)
            value = {**value, 'participating_tasks': flow_evaluation._participating_tasks(graph)}
        return value
    return execute(describe)


@router.post('/cohorts')
def freeze(body: CohortRequest, request: Request):
    project = get_current_project(request); require_role(request, project, {'owner','reviewer','trainer'})
    return execute(lambda: flow_evaluation.freeze_cohort(project, **body.model_dump()))


@router.get('/cohorts')
def cohorts(request: Request):
    rows = execute(lambda: flow_evaluation.list_evidence(get_current_project(request), 'cohorts'))
    return {'cohorts':rows,'total':len(rows)}


@router.get('/cohorts/{cohort_id}')
def cohort(cohort_id: str, request: Request):
    return execute(lambda: flow_evaluation.read_cohort(get_current_project(request), cohort_id))


@router.post('')
def evaluate(body: EvaluationRequest, request: Request):
    project = get_current_project(request); require_role(request, project, {'owner','reviewer','trainer'})
    from backend.engine.spatial_calibration import calibration_scope, project_calibration_store

    def run():
        # Measurement nodes find the project's spatial calibrations by reference (E03).
        with calibration_scope(project_calibration_store(project).load):
            return flow_evaluation.evaluate_flow(project, **body.model_dump())
    return execute(run)


@router.get('')
def history(request: Request):
    rows = execute(lambda: flow_evaluation.list_evidence(get_current_project(request), 'runs'))
    return {'evaluations':rows,'total':len(rows)}


@router.get('/{evaluation_id}')
def evaluation(evaluation_id: str, request: Request):
    return execute(lambda: flow_evaluation.read_evaluation(get_current_project(request), evaluation_id))


@router.post('/{evaluation_id}/review-queue')
def review_queue(evaluation_id: str, request: Request):
    project = get_current_project(request); require_role(request, project, {'owner','reviewer','trainer'})
    return execute(lambda: flow_evaluation.create_review_queue(project,evaluation_id))
