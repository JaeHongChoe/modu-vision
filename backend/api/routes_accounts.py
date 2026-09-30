"""Optional shared-server account and project membership administration."""
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field,ConfigDict
from typing import Literal

router=APIRouter(prefix='/api/accounts',tags=['shared-accounts'])
class Credentials(BaseModel):
    model_config=ConfigDict(extra='forbid')
    username:str=Field(min_length=3,max_length=80)
    password:str=Field(min_length=12,max_length=1024)
class NewUser(Credentials):administrator:bool=False
class Membership(BaseModel):
    user_id:str
    role:Literal['viewer','labeler','trainer','reviewer','owner']
class ProjectSelection(BaseModel):project_id:str

def store(request):
    value=getattr(request.app.state,'accounts',None)
    if value is None:raise HTTPException(422,'Shared accounts require server --shared-auth-dir configuration')
    return value

def user(request):
    value=getattr(request.state,'account_user',None)
    if value is None:raise HTTPException(401,'Account login required')
    return value

def admin(request):
    value=user(request)
    if not value['administrator']:raise HTTPException(403,'Server administrator permission required')
    return value

@router.get('/config')
def configuration(request:Request):return {'enabled':getattr(request.app.state,'accounts',None) is not None,'roles':['viewer','labeler','trainer','reviewer','owner'],'session_transport':'bearer','bootstrap_transport':'desktop_process_capability'}

@router.post('/bootstrap')
def bootstrap(body:Credentials,request:Request):
    try:return store(request).bootstrap(body.username,body.password)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc

@router.post('/login')
def login(body:Credentials,request:Request):
    try:return store(request).login(body.username,body.password)
    except ValueError as exc:raise HTTPException(401,str(exc)) from exc

@router.post('/logout')
def logout(request:Request):
    store(request).logout(request.headers.get('authorization','').removeprefix('Bearer '))
    return {'logged_out':True}

@router.get('/me')
def me(request:Request):
    try:selected=store(request).project_for(user(request)['id'])
    except ValueError:selected=None
    return {'user':user(request),'projects':store(request).projects_for(user(request)['id']),'selected_project_id':selected['id'] if selected else None}

@router.get('/users')
def users(request:Request):admin(request);return {'users':store(request).users()}

@router.post('/users')
def create_user(body:NewUser,request:Request):
    admin(request)
    try:return store(request).create_user(body.username,body.password,body.administrator)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc

@router.put('/projects/{project_id}/members')
def membership(project_id:str,body:Membership,request:Request):
    try:store(request).set_membership(project_id,body.user_id,body.role,user(request)['id'])
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc
    return {'project_id':project_id,'user_id':body.user_id,'role':body.role}

@router.post('/select-project')
def select_project(body:ProjectSelection,request:Request):
    try:store(request).select_project(user(request)['id'],body.project_id)
    except ValueError as exc:raise HTTPException(403,str(exc)) from exc
    return {'project_id':body.project_id}
