"""Explicit approved runtime controls scoped to the currently opened project."""
import httpx
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,Field
from backend.api.routes_project import get_current_project
from backend.engine.managed_service import ManagedService
from backend.engine.field_adapters import FieldAdapterConfig

router=APIRouter(prefix='/api/runtime-services',tags=['runtime-services'])
class ApplyRequest(BaseModel):
    package_path:str
    device:str='cpu'
    reviewer:str=Field(min_length=1,max_length=100)
class RollbackRequest(BaseModel):
    deployment_id:str
    reviewer:str=Field(min_length=1,max_length=100)

def manager(request):
    project=get_current_project(request)
    if project is None:raise HTTPException(409,'Open a project before managing the inspection service')
    return ManagedService(project['project_dir']),project

def execute(action):
    try:return action()
    except (ValueError,KeyError,OSError,RuntimeError,TimeoutError,httpx.HTTPError) as exc:raise HTTPException(409,str(exc)) from exc

@router.get('')
def state(request:Request):
    service,_=manager(request);return execute(service.state)
@router.post('/apply')
def apply(payload:ApplyRequest,request:Request):
    service,project=manager(request);return execute(lambda:service.apply(payload.package_path,payload.device,payload.reviewer,project))
@router.post('/rollback')
def rollback(payload:RollbackRequest,request:Request):
    service,_=manager(request);return execute(lambda:service.rollback(payload.deployment_id,payload.reviewer))
@router.post('/start')
def start(request:Request):
    service,_=manager(request);return execute(service.start)
@router.post('/stop')
def stop(request:Request):
    service,_=manager(request);return execute(service.stop)
@router.put('/adapters')
def adapters(payload:FieldAdapterConfig,request:Request):
    service,_=manager(request);return execute(lambda:service.configure_adapters(payload.model_dump()))
@router.post('/install')
def install(request:Request):
    service,_=manager(request);return execute(service.install_files)

@router.post('/install/activate')
def activate_install(request:Request):
    service,_=manager(request);return execute(service.activate_install)
@router.post('/install/remove')
def remove_install(request:Request):
    service,_=manager(request);return execute(service.uninstall_native)
