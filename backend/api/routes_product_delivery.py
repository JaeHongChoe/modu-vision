"""Project-scoped package, setup, operator and support workflows."""
from pathlib import Path
import json
import time
import httpx
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from backend.api.routes_project import get_current_project
from backend.engine import product_delivery as delivery
from backend.engine.managed_service import ManagedService

router=APIRouter(prefix='/api/product-delivery',tags=['product-delivery'])


def project(request):
    selected=get_current_project(request);account=getattr(request.state,'account_user',None)
    return {**selected,'_delivery_account_id':account['id']} if account else selected


def role(request,allowed):
    account=getattr(request.state,'account_user',None)
    if account:
        selected=project(request);value=request.app.state.accounts.project_role(account['id'],selected['id'])
        if value not in allowed:raise HTTPException(403,'This project role cannot perform this delivery action')


def execute(action):
    try:return action()
    except (ValueError,OSError,KeyError,RuntimeError,TimeoutError,httpx.HTTPError) as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/packages')
def packages(request:Request):return execute(lambda:delivery.package_library(project(request)))


@router.post('/packages/{identifier}/select')
def select(identifier:str,request:Request):return execute(lambda:delivery.select_package(project(request),identifier))


class ImageInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    image_path:str
    device:str='cpu'


def source_image(p,image_path):
    source=p.get('source_dataset_dir')
    if not source:raise ValueError('Select a project source first')
    image=Path(image_path).expanduser()
    if image.is_symlink() or not image.is_file() or not image.resolve().is_relative_to(Path(source).resolve()):raise ValueError('Image must belong to the active project source')
    return image.resolve()


@router.post('/packages/{identifier}/verify')
def verify(identifier:str,body:ImageInput,request:Request):
    role(request,{'owner','reviewer','trainer'})
    def action():
        p=project(request);row=delivery.select_package(p,identifier);image=source_image(p,body.image_path)
        from backend.engine.flow_package_runtime import run_flow_package
        result=run_flow_package(Path(row['package_path']),image,device=body.device,deadline_ms=30000,cpu_threads=1)
        if result.get('status') in ('timeout','error','failed'):raise ValueError('Package execution did not complete: '+str(result.get('status')))
        evidence=delivery.record_execution(p,row['package_path'],image,result,body.device)
        return {'result':result,'evidence':evidence}
    return execute(action)


@router.post('/servers/{profile_id}/preflight')
def preflight(profile_id:str,body:ImageInput,request:Request):
    from backend.api.routes_compute import _administrator
    from backend.remote.profiles import get_profile_store
    _administrator(request);profile=get_profile_store().get(profile_id)
    if profile is None:raise HTTPException(404,'Compute server profile not found')
    return execute(lambda:delivery.server_preflight(project(request),profile,body.image_path))


class ProtocolTest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    protocol:Literal['http','modbus']
    mode:Literal['success','reject','timeout']='success'


@router.post('/protocol-test')
def protocol_test(body:ProtocolTest,request:Request):
    role(request,{'owner','reviewer','trainer'})
    return execute(lambda:delivery.exercise_protocol(body.protocol,body.mode))


@router.get('/installation')
def installation(request:Request):return execute(lambda:delivery.installation_readiness(project(request)))


@router.get('/hardware')
def hardware(request:Request):return execute(lambda:delivery.hardware_matrix(project(request)))


class DiagnosticsOptions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    sections:list[Literal['installation','packages','hardware','operator_errors']]=Field(default_factory=lambda:['installation','packages','hardware','operator_errors'],min_length=1,max_length=4)


@router.post('/diagnostics')
def diagnostics(request:Request,body:DiagnosticsOptions|None=None):
    def action():
        p=project(request);sections=(body or DiagnosticsOptions()).sections
        report={'schema_version':1,'created_at':time.time(),'project_id':p['id'],'sections':sections}
        if 'installation' in sections:report['installation']=delivery.installation_readiness(p)
        if 'packages' in sections:report['packages']=[{key:row.get(key) for key in ('package_id','integrity','manifest_sha256','scope_matches','pipeline_id','error')} for row in delivery.package_library(p)['packages']]
        if 'hardware' in sections:report['hardware']=delivery.hardware_matrix(p)
        if 'operator_errors' in sections:report['operator_results']=[{key:r.get(key) for key in ('job_id','state','model_verdict','error')} for r in delivery.operator_records(p)]
        bundle=delivery.redact_diagnostics(report)
        from backend.engine.runtime_process_control import atomic_private_json
        atomic_private_json(delivery._storage(p)/'diagnostics.json',bundle)
        return {'filename':'vision-diagnostics.json','bundle':bundle,'redacted':True,'includes_source_images':False}
    return execute(action)


@router.get('/operator')
def operator(request:Request):
    def action():
        p=project(request);service=ManagedService(p['project_dir']);state=service.state();runtime=state['runtime'];active=state['active']
        expected=active.get('release',{}).get('manifest_sha256') if active else None
        matching=runtime.get('status')=='ready' and bool(expected) and runtime.get('manifest_sha256')==expected
        adapters={}
        if runtime.get('status')=='ready':
            try:
                with service.client() as client:
                    response=client.get('/v1/adapters');response.raise_for_status();adapters=response.json()
            except (httpx.HTTPError,ValueError):adapters={'state':'response_unavailable'}
        source=p.get('source_dataset_dir');exists=bool(source and Path(source).is_dir())
        account=getattr(request.state,'account_user',None);project_role=request.app.state.accounts.project_role(account['id'],p['id']) if account else 'owner'
        permissions={'can_control':project_role in {'owner','reviewer'},'can_inspect':project_role in {'owner','reviewer','trainer','labeler'},
                     'can_review':project_role in {'owner','reviewer'},'can_configure':project_role in {'owner','reviewer'}}
        return {'project':{'id':p['id'],'name':p['name'],'task':p['task']},'service':state,'runtime_matches_active':matching,
                'permissions':permissions,
                'input_health':{'source_exists':exists,'adapters':adapters,'manual_input': 'available' if exists and matching else 'not_ready','configuration':delivery.read_operator_inputs(p)},
                'results':delivery.operator_records(p),'review_policy':'operator record never replaces model evidence'}
    return execute(action)


class OperatorInputs(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['manual','folder','camera']
    folder:str|None=None
    camera:str|None=None


@router.put('/operator/inputs')
def operator_inputs(body:OperatorInputs,request:Request):
    role(request,{'owner','reviewer'})
    return execute(lambda:delivery.configure_operator_inputs(project(request),body.mode,body.folder,body.camera))


@router.post('/operator/inspect',status_code=202)
def inspect(body:ImageInput,request:Request):
    role(request,{'owner','reviewer','trainer','labeler'})
    def action():
        p=project(request);image=source_image(p,body.image_path);service=ManagedService(p['project_dir']);state=service.state()
        expected=(state.get('active') or {}).get('release',{}).get('manifest_sha256')
        if state['runtime'].get('status')!='ready' or not expected or state['runtime'].get('manifest_sha256')!=expected:raise ValueError('Start the verified active inspection service before submitting input')
        with service.client() as client:
            response=client.post('/v1/jobs/file',json={'image_path':str(image)});response.raise_for_status();return response.json()
    return execute(action)


class OperatorReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    verdict:Literal['OK','NG','REVIEW']
    reviewer:str=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=3,max_length=2000)


@router.post('/operator/results/{identifier}/review')
def review(identifier:str,body:OperatorReview,request:Request):
    role(request,{'owner','reviewer'})
    return execute(lambda:delivery.review_operator_result(project(request),identifier,body.verdict,body.reviewer,body.reason))


@router.post('/operator/results/{identifier}/retry-delivery',status_code=202)
def retry_delivery(identifier:str,request:Request):
    role(request,{'owner','reviewer'})
    def action():
        p=project(request)
        if not any(row['job_id']==identifier for row in delivery.operator_records(p)):raise ValueError('Inspection job not found in this project')
        service=ManagedService(p['project_dir'])
        with service.client() as client:
            response=client.post('/v1/jobs/'+identifier+'/retry-delivery');response.raise_for_status();return response.json()
    return execute(action)
