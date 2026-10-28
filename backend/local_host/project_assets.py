"""Narrow cookie-bound references for headerless image and ZIP reads."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from urllib.parse import parse_qsl, unquote_to_bytes

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers

from backend.contracts.context import ProjectContext, declared_context
from .location_identity import open_directory

QUERY = 'local_asset_ticket'
DOWNLOAD = '/api/local-host/project-assets/download'
_REFERENCE = re.compile(r'asset_[0-9a-f]{48}')
_POLICY = 'local-project-assets-v1'


def denied(message='이 이미지의 프로젝트 읽기 권한을 다시 확인하세요.', status=403):
    return HTTPException(status, {'code':'local_project_asset_ticket_required','message':message})


def has_ticket(scope):
    raw = scope.get('query_string',b'')
    return any(unquote_to_bytes(part.split(b'=',1)[0]) == QUERY.encode()
               for part in raw.split(b'&'))


def private_response(send):
    async def wrapped(message):
        if message['type']=='http.response.start':
            headers = [(key,value) for key,value in message.get('headers',[])
                       if key.lower() not in {b'cache-control',b'referrer-policy',b'x-content-type-options'}]
            message = {**message,'headers':headers+[(b'cache-control',b'no-store'),(b'referrer-policy',b'no-referrer'),(b'x-content-type-options',b'nosniff')]}
        await send(message)
    return wrapped


def _fingerprint(info):
    return (info.st_dev,info.st_ino,stat.S_IFMT(info.st_mode),info.st_nlink,
            info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _json_file(directory, name):
    fd = os.open(name,os.O_RDONLY|os.O_NOFOLLOW|getattr(os,'O_NONBLOCK',0),dir_fd=directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= 2*1024*1024:
            raise denied()
        raw = b''
        while len(raw) < before.st_size:
            chunk = os.read(fd,min(65536,before.st_size-len(raw)))
            if not chunk: break
            raw += chunk
        if (len(raw) != before.st_size or _fingerprint(before) != _fingerprint(os.fstat(fd))
                or _fingerprint(before) != _fingerprint(os.stat(name,dir_fd=directory,follow_symlinks=False))):
            raise denied()
        value = json.loads(raw)
        if type(value) is not dict: raise denied()
        return value
    finally: os.close(fd)


def _source(value):
    if value is None or value == '': return None
    if type(value) is not str or len(value)>4096: raise denied()
    return str(Path(value).expanduser().resolve())


def _read_scope(row):
    with open_directory(row['path']) as directory:
        manifest = _json_file(directory,'project.json')
        labels = _json_file(directory,'labelsets.json')
    active = labels.get('active_id')
    if (manifest.get('id') != row['project_id'] or type(manifest.get('task')) is not str
            or type(active) is not str or not re.fullmatch(r'default|ls_[0-9a-f]{12}',active)):
        raise denied()
    return {'source':_source(manifest.get('source_dataset_dir')),'labelset':active,'task':manifest['task']}


def _selector(scope):
    if scope['type'] != 'http' or scope.get('method') not in {'GET','HEAD'}: raise denied()
    raw = scope.get('query_string',b'')
    if len(raw)>16384: raise denied()
    try: pairs = parse_qsl(raw.decode('utf-8'),keep_blank_values=True,strict_parsing=True,max_num_fields=8)
    except (ValueError,UnicodeError): raise denied() from None
    query = dict(pairs)
    if len(query)!=len(pairs) or not _REFERENCE.fullmatch(query.get(QUERY,'')): raise denied()
    path = scope.get('path','')
    allowed = {QUERY}; required = {QUERY}
    if re.fullmatch(r'/api/dataset/(raw|thumbnail)/[^\x00-\x1f]{1,4096}',path):
        allowed.add('file_path');required.add('file_path')
        if path.startswith('/api/dataset/thumbnail/'): allowed.add('size')
    elif re.fullmatch(r'/api/annotations/[^/\x00-\x1f]{1,256}/mask',path):
        allowed.update(('file_path','v'));required.add('file_path')
    elif re.fullmatch(r'/api/data-workbench/derived/derived_[0-9a-f]{32}/image',path): pass
    else: raise denied()
    if not required <= set(query) or not set(query) <= allowed: raise denied()
    if 'file_path' in query and (not 0<len(query['file_path'])<=4096 or not Path(query['file_path']).is_absolute()
                                 or any(ord(c)<32 for c in query['file_path'])): raise denied()
    if 'file_path' in query:
        from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
        if Path(query['file_path']).suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS: raise denied()
    if 'size' in query and (not re.fullmatch('[0-9]{1,4}',query['size']) or not 32<=int(query['size'])<=4096): raise denied()
    if 'v' in query and not re.fullmatch('[A-Za-z0-9_.:-]{1,64}',query['v']): raise denied()
    return query


class ProjectAssetTickets:
    def __init__(self, app, registry):
        self.app,self.registry = app,registry
        self.lock=threading.RLock();self.rows={};self.limit=512;self.now=time.monotonic
        self.supported=(os.name=='posix' and bool(getattr(os,'O_NOFOLLOW',0))
                        and bool(getattr(os,'O_DIRECTORY',0)) and os.open in os.supports_dir_fd)

    def _row(self, identifier):
        with self.registry.db() as db:
            row=db.execute('SELECT * FROM locations WHERE registration_id=?',(identifier,)).fetchone()
        if row is None: raise denied(status=404)
        return dict(row)

    def _binding(self,row,account,token):
        accounts=self.app.state.accounts;actor=account['id'];session=hashlib.sha256(token.encode()).hexdigest()
        with accounts._db() as db:
            found=db.execute('SELECT expires,transport FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND user_id=? AND expires>? AND disabled=0',(session,actor,time.time())).fetchone()
            if not found or found['transport']!='cookie': raise denied()
            if accounts._role(db,actor,row['permission_key']) is None: raise denied()
            selected=db.execute('SELECT path FROM projects WHERE id=?',(row['permission_key'],)).fetchone()
            if not selected or selected['path']!=row['path']: raise denied()
            revision=db.execute('SELECT revision FROM member_revisions WHERE project_id=? AND user_id=?',(row['permission_key'],actor)).fetchone()
            revision=revision['revision'] if revision else 0
            deadline=self.now()+min(28800,found['expires']-time.time())
        self.registry.authorize_location(self.app,account,row['path'])
        current_scope=_read_scope(row)
        context=ProjectContext(workspace_id=row['workspace_id'],project_id=row['project_id'],actor_id=actor,mode='team')
        binding={'actor':actor,'session':session,'registration_id':row['registration_id'],'revision':row['revision'],
                 'identity':hashlib.sha256(row['identity'].encode()).hexdigest(),'permission_key':row['permission_key'],
                 'membership_revision':revision,'context':context.model_dump(),'scope':current_scope,'policy':_POLICY}
        return binding,deadline

    def _prune(self):
        self.rows={key:value for key,value in self.rows.items() if value['expires']>self.now()}

    def issue(self,request,identifier):
        if not self.supported: raise denied('이 호스트의 자산 읽기 기능을 확인하세요.',503)
        if request.headers.get('authorization') is not None: raise denied()
        context=declared_context(request.headers)
        if context is None: raise denied('프로젝트 선택 문맥을 확인하세요.',400)
        authority=self.app.state.local_host.authorize(request,'projects.assetTickets.issue',project=True)
        row=self._row(identifier)
        expected=ProjectContext(workspace_id=row['workspace_id'],project_id=row['project_id'],actor_id=authority['actor_id'],mode='team')
        if (context!=expected or authority['workspace_id']!=expected.workspace_id
                or authority['project_id']!=expected.project_id
                or str(Path(authority['project']['project_dir']).resolve())!=row['path']): raise denied()
        try: binding,deadline=self._binding(row,authority['account'],request.state.account_session_token)
        except (OSError,ValueError,TypeError,KeyError,RuntimeError): raise denied() from None
        project=authority['project']
        if binding['scope'] != {'source':_source(project.get('source_dataset_dir')),'labelset':project.get('active_labelset_id','default'),'task':project['task']}: raise denied()
        with self.lock:
            self._prune()
            reference=next((key for key,value in self.rows.items() if value['binding']==binding),None)
            if reference is None:
                if len(self.rows)>=self.limit: raise denied('열린 이미지 읽기 권한이 너무 많습니다. 잠시 뒤 다시 선택하세요.',429)
                reference='asset_'+secrets.token_hex(24)
                self.rows[reference]={'binding':binding,'expires':deadline,'issued_tab':authority['tab_id']}
            remaining=min(deadline,self.rows[reference]['expires'])-self.now()
        if remaining<=0: raise denied()
        return {'ticket':reference,'expires_in':remaining,'registration_id':identifier,'context':expected.model_dump()}

    def check(self,scope,account,token):
        if scope.get('path')==DOWNLOAD and scope.get('state',{}).get('_local_asset_download_form') is not None:
            return self.check_download(scope,account,token)
        if not has_ticket(scope): return None
        query=_selector(scope)
        context=self._check_reference(scope,account,token,query[QUERY])
        if 'file_path' in query:
            from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
            try:resolved=Path(query['file_path']).resolve()
            except (OSError,ValueError,RuntimeError):raise denied() from None
            if resolved.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:raise denied()
        return context

    def _check_reference(self,scope,account,token,reference):
        headers=Headers(scope=scope)
        if scope.get('scheme')!='https' or headers.get('authorization') is not None: raise denied()
        for name in ('x-vision-context','x-vision-project','x-vision-actor','x-vision-workspace','x-vision-local-tab'):
            if len(headers.getlist(name))>1: raise denied()
        if headers.get('x-vision-remote-context') is not None or headers.get('x-vision-remote-authority') is not None: raise denied()
        with self.lock:
            self._prune();record=self.rows.get(reference)
            if record is None: raise denied()
            binding=record['binding']
        if binding['actor']!=account['id'] or binding['session']!=hashlib.sha256(token.encode()).hexdigest(): raise denied()
        context=ProjectContext(**binding['context'])
        supplied=declared_context(headers)
        if supplied is not None and supplied!=context: raise denied()
        for name,value in (('x-vision-project',context.project_id),('x-vision-actor',context.actor_id),('x-vision-workspace',context.workspace_id)):
            if headers.get(name) is not None and headers[name]!=value: raise denied()
        if headers.get('x-vision-local-tab') is not None:
            self.registry.recovery.check_browser_proof(account['id'],token,headers['x-vision-local-tab'])
        try:
            row=self._row(binding['registration_id'])
            current,_=self._binding(row,account,token)
        except (OSError,ValueError,TypeError,KeyError,RuntimeError): raise denied() from None
        if current!=binding or record['expires']<=self.now(): raise denied()
        scope.setdefault('state',{})['_local_project_asset_binding']=binding
        return context

    def check_download(self,scope,account,token):
        form=scope.get('state',{}).get('_local_asset_download_form')
        headers=Headers(scope=scope)
        if (scope.get('type')!='http' or scope.get('path')!=DOWNLOAD or scope.get('method')!='POST'
                or scope.get('query_string') or type(form) is not dict
                or set(form)!={'ticket','csrf_token','kind','export_id'}
                or headers.get('origin')!=self.app.state.local_host.origin
                or len(headers.getlist('origin'))!=1): raise denied()
        prefix={'formats':'annotations_','masks':'masks_'}.get(form['kind'])
        if (prefix is None or not re.fullmatch(re.escape(prefix)+'[0-9a-f]{32}',form['export_id'])
                or not _REFERENCE.fullmatch(form['ticket'])
                or not re.fullmatch('[A-Za-z0-9_-]{20,256}',form['csrf_token'])): raise denied()
        try:self.app.state.accounts.cookie_session(token,form['csrf_token'])
        except ValueError:raise denied() from None
        return self._check_reference(scope,account,token,form['ticket'])

    async def prepare_download(self,scope,receive,account,token):
        headers=Headers(scope=scope)
        if (headers.get('content-type','').split(';',1)[0].strip().lower()!='application/x-www-form-urlencoded'
                or headers.get('content-encoding','identity')!='identity'): raise denied(status=400)
        length=headers.get('content-length')
        if len(headers.getlist('content-length'))>1 or length is not None and (not length.isdigit() or len(length)>8 or int(length)>2048):raise denied(status=400)
        raw=b''
        while True:
            message=await receive()
            if message['type']!='http.request':raise denied(status=400)
            chunk=message.get('body',b'')
            if len(raw)+len(chunk)>2048:raise denied(status=400)
            raw+=chunk
            if not message.get('more_body'):break
        try:pairs=parse_qsl(raw.decode('utf-8'),keep_blank_values=True,strict_parsing=True,max_num_fields=4)
        except (UnicodeError,ValueError):raise denied(status=400) from None
        form=dict(pairs)
        if len(form)!=len(pairs) or set(form)!={'ticket','csrf_token','kind','export_id'}:raise denied(status=400)
        scope.setdefault('state',{})['_local_asset_download_form']=form
        self.check_download(scope,account,token)
        if len(headers.getlist('x-vision-csrf'))>1 or headers.get('x-vision-csrf') not in {None,form['csrf_token']}:
            raise denied()
        # Only this exact, verified form may provide its CSRF through its body.
        # Inner shared authorization keeps its existing cookie+header check.
        scope['headers']=[(key,value) for key,value in scope.get('headers',[]) if key.lower()!=b'x-vision-csrf']+[(b'x-vision-csrf',form['csrf_token'].encode())]
        consumed=False
        async def replay():
            nonlocal consumed
            if consumed:return await receive()
            consumed=True;return {'type':'http.request','body':raw,'more_body':False}
        return replay

    def bind_download(self,scope,account,token,context):
        project,key,resolved=self.registry.resolve(self.app,account,context)
        decision=self.app.state.accounts.authorize(account['id'],'project.read',key,session_token=token,scoped=False)
        if not decision.allowed or resolved!=context:raise denied()
        self.check_project(scope,project)
        state=scope.setdefault('state',{})
        state.update(scoped_project=project,account_project_key=key,local_project_context=context,project_context=context)

    def download(self,request):
        account=request.state.account_user;token=request.state.account_session_token
        self.check_download(request.scope,account,token)
        project=request.state.scoped_project;self.check_project(request.scope,project)
        form=request.scope['state']['_local_asset_download_form']
        # Keep the export namespace beneath this exact held registration; never
        # inspect service/config files or accept a browser filesystem path.
        with open_directory(project['project_dir']) as directory:
            exports=os.open('exports',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            try:
                info=os.stat(form['export_id']+'.zip',dir_fd=exports,follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise denied()
            finally:os.close(exports)
        if form['kind']=='formats':
            from backend.api.routes_dataset_metadata import download_format
            return download_format(form['export_id'],request)
        from backend.api.routes_mask_exchange import download_masks
        return download_masks(form['export_id'],request)

    def check_project(self,scope,project):
        binding=scope.get('state',{}).get('_local_project_asset_binding')
        if binding and binding['scope']!={'source':_source(project.get('source_dataset_dir')),'labelset':project.get('active_labelset_id','default'),'task':project['task']}:
            raise denied()


def install_project_assets(app,registry):
    manager=ProjectAssetTickets(app,registry);app.state.project_assets=manager
    router=APIRouter()
    class AssetRequest(BaseModel):
        model_config=ConfigDict(extra='forbid',strict=True)
        registration_id:str=Field(min_length=1,max_length=128)
    @router.post('/api/local-host/project-assets')
    def issue(body:AssetRequest,request:Request):
        return manager.issue(request,body.registration_id)
    @router.post(DOWNLOAD)
    def download(request:Request):
        if request.scope.get('state',{}).get('_local_asset_download_form') is None:raise denied()
        try:return manager.download(request)
        except (OSError,ValueError,RuntimeError):raise denied() from None
    app.include_router(router)
    return manager
