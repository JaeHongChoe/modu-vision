"""Separate saved whole-flow evaluation history, with immutable cohort inputs."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.routes_project import get_current_project
from backend.api.routes_image_truth import execute, require_role
from backend.engine import flow_evaluation

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
    return execute(lambda: flow_evaluation.evaluate_flow(project, **body.model_dump()))


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
