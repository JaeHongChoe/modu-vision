"""Explicit service-capture intake; adoption returns a new source for user selection."""
from fastapi import APIRouter, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
import base64
import io
from PIL import Image

from backend.api.routes_project import get_current_project
from backend.api.routes_image_truth import execute, require_role
from backend.api.shared_authorization import request_actor
from backend.engine import capture_intake, capture_drift

router=APIRouter(prefix='/api/capture-intake',tags=['capture-intake'])


class RegisterRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_ids:list[str]|None=Field(default=None,min_length=1,max_length=5000)
    limit:int=Field(default=100,ge=1,le=5000)


class ReviewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1)
    actor:str=Field(min_length=1,max_length=100)
    decision:Literal['adopt','reject']
    note:str=Field(default='',max_length=2000)


class AdoptRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    candidate_ids:list[str]=Field(min_length=1,max_length=1000)
    actor:str=Field(min_length=1,max_length=100)
    name:str=Field(min_length=1,max_length=200)


class DriftReferenceRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    candidate_ids:list[str]=Field(min_length=1,max_length=2000)
    actor:str=Field(min_length=1,max_length=100)
    name:str=Field(min_length=1,max_length=200)


@router.get('')
def candidates(request:Request):
    return execute(lambda:capture_intake.list_candidates(get_current_project(request)))


@router.get('/review-queue')
def review_queue(request:Request, threshold:float=Query(.5,ge=0,le=1), margin:float=Query(.05,ge=0,le=1)):
    return execute(lambda:capture_intake.review_queue(get_current_project(request),threshold=threshold,margin=margin))


@router.get('/drift/references')
def drift_references(request:Request):
    return execute(lambda:capture_drift.references(get_current_project(request)))


@router.post('/drift/references')
def drift_reference(body:DriftReferenceRequest,request:Request):
    project=get_current_project(request);require_role(request,project,{'owner','reviewer'})
    return execute(lambda:capture_drift.create_reference(project,body.candidate_ids,actor=request_actor(request,body.actor),name=body.name))


@router.get('/drift/references/{identifier}/report')
def drift_report(identifier:str,request:Request):
    return execute(lambda:capture_drift.report(get_current_project(request),identifier))


@router.post('/register')
def register(body:RegisterRequest,request:Request):
    project=get_current_project(request);require_role(request,project,{'owner','reviewer','trainer','labeler'})
    return execute(lambda:capture_intake.register_service_jobs(project,**body.model_dump()))


@router.get('/candidates/{identifier}/preview')
def preview(identifier:str,request:Request):
    def action():
        path=capture_intake.candidate_image(get_current_project(request),identifier)
        with Image.open(path) as opened:
            width,height=opened.size;image=opened.convert('RGB');image.thumbnail((640,640));buffer=io.BytesIO();image.save(buffer,format='PNG')
        return {'candidate_id':identifier,'width':width,'height':height,'data_url':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()}
    return execute(action)


@router.post('/candidates/{identifier}/review')
def review(identifier:str,body:ReviewRequest,request:Request):
    project=get_current_project(request);require_role(request,project,{'owner','reviewer'})
    payload=body.model_dump();payload['actor']=request_actor(request,body.actor)
    return execute(lambda:capture_intake.review_candidate(project,identifier,**payload))


@router.post('/adopt')
def adopt(body:AdoptRequest,request:Request):
    project=get_current_project(request);require_role(request,project,{'owner','reviewer'})
    return execute(lambda:capture_intake.adopt_candidates(project,body.candidate_ids,actor=request_actor(request,body.actor),name=body.name))


@router.get('/versions')
def versions(request:Request):
    return execute(lambda:capture_intake.list_versions(get_current_project(request)))


@router.get('/versions/{identifier}')
def version(identifier:str,request:Request):
    return execute(lambda:capture_intake.read_version(get_current_project(request),identifier))
