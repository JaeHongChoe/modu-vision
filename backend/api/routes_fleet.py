"""Central-to-field deployment management for the selected project."""
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field,ConfigDict
from backend.api.routes_project import get_current_project
from backend.engine.fleet import FleetRegistry,EmergencyRollbackDenied,EmergencyReasonRequired
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
class EmergencyRollbackRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    deployment_id:str=Field(min_length=1,max_length=100)
    reason:str=Field(max_length=2000)


# The desktop process token is an explicit local-owner capability, not a
# human account identity. Shared servers always require the account session.
LOCAL_DESKTOP_CAPABILITIES={'can_rollback':True,'can_emergency_rollback':True}


def rollback_capabilities(request,project):
    account=getattr(request.state,'account_user',None)
    accounts=getattr(request.app.state,'accounts',None)
    if account is not None and accounts is not None:
        role=accounts.project_role(account['id'],project['id'])
        return {'authentication':'shared_account_session','actor_id':account['id'],'actor_name':account['username'],
                'actor_role':role or 'unavailable','can_rollback':role in {'owner','reviewer'},
                'can_emergency_rollback':role=='owner'}
    import secrets
    expected=getattr(request.app.state,'api_token',None)
    if accounts is not None or not expected or not secrets.compare_digest(request.headers.get('x-vision-token',''),expected):
        raise HTTPException(401,'Authenticated account or desktop process capability required')
    return {'authentication':'desktop_process_capability','actor_id':'desktop_process','actor_name':'Desktop process capability',
            'actor_role':'local_owner',**LOCAL_DESKTOP_CAPABILITIES}


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
    store,project=scope(request);return execute(lambda:{**store.readback(target_id),'history':store.ledger(target_id).history(),'release_failures':store.failures(target_id),'emergency_rollback_events':store.emergency_events(target_id)})
@router.post('/targets/{target_id}/deploy')
def deploy(target_id:str,payload:DeployRequest,request:Request):
    store,project=scope(request)
    def action():
        from backend.engine.runtime_device import resolve_runtime_device
        # The field device is resolved on the agent; accept supported identities
        # without requiring that hardware to exist on this central computer.
        import re
        store.target(target_id)
        try:
            if not re.fullmatch(r'cpu|mps|cuda(?::[0-9]+)?|openvino:(CPU|GPU|NPU)',payload.device):raise ValueError('Unsupported field execution device')
            release=ManagedService(project['project_dir']).stage(payload.package_path,project,device=payload.device)
        except (ValueError,OSError,RuntimeError) as exc:
            store.record_failure(target_id,action='apply',reviewer=payload.reviewer,error=exc)
            raise
        return store.apply(target_id,release,reviewer=payload.reviewer,project=project)
    return execute(action)
@router.post('/targets/{target_id}/rollback')
def rollback(target_id:str,payload:RollbackRequest,request:Request):
    store,project=scope(request);return execute(lambda:store.rollback(target_id,payload.deployment_id,reviewer=payload.reviewer,project=project))


@router.get('/capabilities')
def capabilities(request:Request):
    store,project=scope(request)
    return rollback_capabilities(request,project)


@router.post('/targets/{target_id}/emergency-rollback')
def emergency_rollback(target_id:str,payload:EmergencyRollbackRequest,request:Request):
    store,project=scope(request)
    actor=rollback_capabilities(request,project)
    def action():
        try:
            return store.emergency_rollback(target_id,payload.deployment_id,actor=actor,reason=payload.reason,project=project)
        except EmergencyRollbackDenied as exc:
            raise HTTPException(403,str(exc)) from exc
        except EmergencyReasonRequired as exc:
            raise HTTPException(422,str(exc)) from exc
    return execute(action)
