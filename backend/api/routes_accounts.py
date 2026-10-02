"""Optional shared-server accounts with explicit project and browser authority."""
import base64
import hashlib
import time
import math
from urllib.parse import urlsplit,parse_qs
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field,ConfigDict
from typing import Literal
from backend.contracts.authentication import browser_origin_allowed, VerifiedIdentity

router=APIRouter(prefix='/api/accounts',tags=['shared-accounts'])
COOKIE='vision_session'

class Credentials(BaseModel):
    model_config=ConfigDict(extra='forbid')
    username:str=Field(min_length=3,max_length=80)
    password:str=Field(min_length=12,max_length=1024)
class Login(Credentials):transport:Literal['bearer','cookie']='bearer'
class NewUser(Credentials):administrator:bool=False
class Membership(BaseModel):
    model_config=ConfigDict(extra='forbid')
    user_id:str=Field(min_length=1,max_length=128)
    role:Literal['viewer','labeler','trainer','reviewer','owner']
class ProjectSelection(BaseModel):project_id:str
class AccountState(BaseModel):
    model_config=ConfigDict(extra='forbid')
    disabled:bool
class Enrollment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason:str=Field(min_length=8,max_length=1000)
class OidcStart(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider:str=Field(min_length=1,max_length=80)
class OidcCallback(BaseModel):
    model_config=ConfigDict(extra='forbid')
    state:str=Field(min_length=1,max_length=256)
    code:str=Field(min_length=1,max_length=4096)
class IdentityEnrollment(OidcStart):
    user_id:str=Field(min_length=1,max_length=128)
    subject:str=Field(min_length=1,max_length=256)


def store(request):
    value=getattr(request.app.state,'accounts',None)
    if value is None:raise HTTPException(422,'Shared accounts require server --shared-auth-dir configuration')
    value.bind_workspace(request.app.state.context_registry.workspace_id)
    return value

def user(request):
    value=getattr(request.state,'account_user',None)
    if value is None:raise HTTPException(401,'Account login required')
    return value

def admin(request):
    value=user(request)
    if not value['administrator']:raise HTTPException(403,'Server administrator permission required')
    return value

def browser_origin(request):
    origin=request.headers.get('origin','')
    if not browser_origin_allowed(request.app,origin,request.url.scheme):
        raise HTTPException(403,'Browser sessions require a configured HTTPS origin')
    return origin

def cookie_response(value):
    token=value['token']
    response=JSONResponse({k:v for k,v in value.items() if k!='token'})
    response.set_cookie(COOKIE,token,httponly=True,secure=True,samesite='strict',
                        max_age=max(0,int(value['expires_at']-time.time())),path='/')
    response.headers['Cache-Control']='no-store'
    return response

@router.get('/config')
def configuration(request:Request):
    return {'enabled':getattr(request.app.state,'accounts',None) is not None,
            'roles':['viewer','labeler','trainer','reviewer','owner'],
            'session_transport':'bearer','session_transports':['bearer','cookie'],
            'oidc_providers':sorted(getattr(request.app.state,'oidc_providers',{})),
            'bootstrap_transport':'desktop_process_capability'}

@router.post('/bootstrap')
def bootstrap(body:Credentials,request:Request):
    try:return store(request).bootstrap(body.username,body.password)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc

@router.post('/login')
def login(body:Login,request:Request):
    if body.transport=='cookie':browser_origin(request)
    try:
        value=store(request).login(body.username,body.password,transport=body.transport)
        return cookie_response(value) if body.transport=='cookie' else value
    except ValueError as exc:raise HTTPException(401,str(exc)) from exc

@router.post('/logout')
def logout(request:Request):
    store(request).logout(request.state.account_session_token)
    response=JSONResponse({'logged_out':True})
    response.delete_cookie(COOKIE,path='/',httponly=True,secure=True,samesite='strict')
    return response

@router.get('/me')
def me(request:Request):
    accounts=store(request)
    try:selected=accounts.project_for(user(request)['id'])
    except ValueError:selected=None
    metadata=accounts.metadata()
    return {'user':user(request),'projects':accounts.projects_for(user(request)['id']),
            'selected_project_id':selected['id'] if selected else None,
            'workspace_id':metadata['workspace_id'],'organization_id':metadata['organization_id']}

@router.get('/users')
def users(request:Request):admin(request);return {'users':store(request).users()}

@router.post('/users')
def create_user(body:NewUser,request:Request):
    actor=admin(request)
    try:return store(request).create_user(body.username,body.password,body.administrator,actor=actor['id'],session_token=request.state.account_session_token)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc

@router.patch('/users/{user_id}')
def account_state(user_id:str,body:AccountState,request:Request):
    try:return store(request).set_disabled(user_id,body.disabled,user(request)['id'],session_token=request.state.account_session_token)
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc

@router.delete('/users/{user_id}/sessions')
def revoke_sessions(user_id:str,request:Request):
    try:store(request).revoke_sessions(user_id,user(request)['id'],session_token=request.state.account_session_token)
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc
    return {'revoked':True}

@router.put('/projects/{project_id}/members')
def membership(project_id:str,body:Membership,request:Request):
    try:store(request).set_membership(project_id,body.user_id,body.role,user(request)['id'],session_token=request.state.account_session_token)
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc
    return {'project_id':project_id,'user_id':body.user_id,'role':body.role}

@router.delete('/projects/{project_id}/members/{user_id}')
def remove_membership(project_id:str,user_id:str,request:Request):
    try:store(request).remove_membership(project_id,user_id,user(request)['id'],session_token=request.state.account_session_token)
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc
    return {'project_id':project_id,'user_id':user_id,'removed':True}

@router.post('/projects/{project_id}/enroll-administrator')
def enroll_administrator(project_id:str,body:Enrollment,request:Request):
    try:store(request).enroll_administrator(project_id,user(request)['id'],body.reason,session_token=request.state.account_session_token)
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc
    return {'project_id':project_id,'role':'owner'}

@router.get('/projects/{project_id}/audit')
def audit(project_id:str,request:Request,after:int=0,limit:int=100):
    if after<0 or not 1<=limit<=200:raise HTTPException(422,'Audit pagination is out of bounds')
    try:return {'events':store(request).audit_events(project_id,user(request)['id'],after,limit,session_token=request.state.account_session_token)}
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc

@router.get('/projects/{project_id}/members')
def project_members(project_id:str,request:Request):
    try:return {'members':store(request).project_members(project_id,user(request)['id'])}
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc

@router.post('/select-project')
def select_project(body:ProjectSelection,request:Request):
    try:store(request).select_project(user(request)['id'],body.project_id)
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc
    return {'project_id':body.project_id}

@router.post('/oidc/start')
def oidc_start(body:OidcStart,request:Request):
    origin=browser_origin(request)
    provider=getattr(request.app.state,'oidc_providers',{}).get(body.provider)
    if provider is None:raise HTTPException(422,'OIDC provider is not configured')
    state,nonce,verifier,browser=store(request).begin_oidc(body.provider,origin)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    try:url=provider.adapter.authorization_url(state=state,nonce=nonce,code_challenge=challenge,redirect_uri=provider.redirect_uri)
    except (ValueError,OSError,TimeoutError) as exc:raise HTTPException(502,'Configured identity provider is unavailable') from exc
    parts=urlsplit(url)
    forbidden={'token','access_token','id_token','session_token','password','client_secret'}
    if (parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.fragment
            or len(url)>16384 or forbidden.intersection(parse_qs(parts.query))):
        raise HTTPException(502,'Configured identity provider returned an invalid authorization URL')
    response=JSONResponse({'authorization_url':url,'state':state})
    response.set_cookie('vision_oidc_flow',browser,httponly=True,secure=True,samesite='strict',max_age=300,path='/api/accounts/oidc')
    response.headers['Cache-Control']='no-store'
    return response

@router.post('/oidc/callback')
def oidc_callback(body:OidcCallback,request:Request):
    origin=browser_origin(request)
    accounts=store(request)
    try:
        pending=accounts.consume_oidc(body.state,origin,request.cookies.get('vision_oidc_flow',''))
        provider=getattr(request.app.state,'oidc_providers',{}).get(pending['provider'])
        if provider is None:raise ValueError('OIDC provider is no longer configured')
        identity=provider.adapter.exchange(code=body.code,code_verifier=pending['verifier'],
            nonce=pending['nonce'],redirect_uri=provider.redirect_uri)
        if (not isinstance(identity,VerifiedIdentity) or identity.issuer!=provider.issuer
                or identity.audience!=provider.client_id or identity.nonce!=pending['nonce']
                or not isinstance(identity.expires_at,(int,float)) or isinstance(identity.expires_at,bool)
                or not math.isfinite(identity.expires_at) or identity.expires_at<=time.time()
                or not isinstance(identity.subject,str) or not identity.subject or len(identity.subject)>256):
            raise ValueError('OIDC identity verification failed')
        response=cookie_response(accounts.external_session(pending['provider'],identity))
        response.delete_cookie('vision_oidc_flow',path='/api/accounts/oidc',httponly=True,secure=True,samesite='strict')
        return response
    except (ValueError,OSError,TimeoutError) as exc:raise HTTPException(401,'OIDC sign-in failed; verify provider enrollment and try again') from exc

@router.post('/oidc/identities')
def oidc_enroll(body:IdentityEnrollment,request:Request):
    provider=getattr(request.app.state,'oidc_providers',{}).get(body.provider)
    if provider is None:raise HTTPException(422,'OIDC provider is not configured')
    try:store(request).bind_external_identity(body.provider,provider.issuer,body.subject,body.user_id,user(request)['id'],session_token=request.state.account_session_token)
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc
    return {'enrolled':True,'user_id':body.user_id,'provider':body.provider}
