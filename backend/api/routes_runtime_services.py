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
class ScmConfiguration(BaseModel):
    service_account:str=Field(min_length=1,max_length=160)
    network_required:bool=False
    warmup_image:str|None=None

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
    service,project=manager(request)
    def action():
        if payload.modbus and payload.modbus.trigger_image_path:
            from backend.api.routes_product_delivery import source_image
            payload.modbus.trigger_image_path=str(source_image(project,payload.modbus.trigger_image_path))
        return service.configure_adapters(payload.model_dump())
    return execute(action)
@router.post('/install')
def install(request:Request):
    service,_=manager(request);return execute(service.install_files)

@router.post('/install/activate')
def activate_install(request:Request):
    service,_=manager(request);return execute(service.activate_install)
@router.post('/install/remove')
def remove_install(request:Request):
    service,_=manager(request);return execute(service.uninstall_native)

@router.post('/scm/preflight')
def scm_preflight(payload:ScmConfiguration,request:Request):
    service,_=manager(request);return execute(lambda:service.scm_preflight(payload.model_dump()))
@router.post('/scm/prepare')
def prepare_scm(payload:ScmConfiguration,request:Request):
    service,_=manager(request);return execute(lambda:service.prepare_scm(payload.model_dump()))
@router.post('/scm/activate')
def activate_scm(request:Request):
    service,_=manager(request);return execute(service.activate_scm)


class CaptureGroupPolicyRequest(BaseModel):
    expected_revision:int=Field(ge=0)
    policy:dict


def _capture_store(request):
    service,project=manager(request)
    from backend.engine.inspection_service import InspectionStore
    return InspectionStore(service.root/'state'),project


@router.get('/capture-groups')
def capture_groups(request:Request,limit:int=100):
    store,_=_capture_store(request)
    return execute(lambda:store.capture_group_status(limit))


@router.put('/capture-groups/policy')
def capture_group_policy(body:CaptureGroupPolicyRequest,request:Request):
    store,project=_capture_store(request)
    from backend.api.routes_image_truth import require_role
    require_role(request,project,{'owner'})
    return execute(lambda:store.configure_capture_groups(body.policy,expected_revision=body.expected_revision))
