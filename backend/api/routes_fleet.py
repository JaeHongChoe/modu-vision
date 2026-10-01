"""Central-to-field deployment management for the selected project."""
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field
from backend.api.routes_project import get_current_project
from backend.engine.fleet import FleetRegistry
from backend.engine.managed_service import ManagedService

router=APIRouter(prefix='/api/fleet',tags=['fleet'])
class TargetRequest(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    url:str
    token:str=Field(min_length=16,max_length=4096)
    target_id:str|None=None
class DeployRequest(BaseModel):
    package_path:str
    device:str='cpu'
    reviewer:str=Field(min_length=1,max_length=100)
class RollbackRequest(BaseModel):
    deployment_id:str
    reviewer:str=Field(min_length=1,max_length=100)


def scope(request):
    project=get_current_project(request)
    if not project:raise HTTPException(409,'Open a project first')
    return FleetRegistry(project['project_dir']),project


def execute(action):
    import httpx
    try:return action()
    except (ValueError,OSError,KeyError,RuntimeError,httpx.HTTPError) as exc:raise HTTPException(409,str(exc)) from exc

@router.get('/targets')
def targets(request:Request):
    store,project=scope(request);return {'targets':store.targets()}
@router.post('/targets')
def target(payload:TargetRequest,request:Request):
    store,project=scope(request);return execute(lambda:store.save_target(**payload.model_dump()))
@router.get('/targets/{target_id}')
def readback(target_id:str,request:Request):
    store,project=scope(request);return execute(lambda:{**store.readback(target_id),'history':store.ledger(target_id).history()})
@router.post('/targets/{target_id}/deploy')
def deploy(target_id:str,payload:DeployRequest,request:Request):
    store,project=scope(request)
    def action():
        from backend.engine.runtime_device import resolve_runtime_device
        # The field device is resolved on the agent; accept supported identities
        # without requiring that hardware to exist on this central computer.
        import re
        if not re.fullmatch(r'cpu|mps|cuda(?::[0-9]+)?|openvino:(CPU|GPU|NPU)',payload.device):raise ValueError('Unsupported field execution device')
        release=ManagedService(project['project_dir']).stage(payload.package_path,project,device=payload.device)
        return store.apply(target_id,release,reviewer=payload.reviewer)
    return execute(action)
@router.post('/targets/{target_id}/rollback')
def rollback(target_id:str,payload:RollbackRequest,request:Request):
    store,project=scope(request);return execute(lambda:store.rollback(target_id,payload.deployment_id,reviewer=payload.reviewer))
