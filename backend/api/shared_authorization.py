"""Authorization and request-scoped project selection for optional shared servers."""
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs,unquote
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from fastapi import HTTPException


def request_actor(request, declared):
    """Shared audit authorship is the session identity, including omitted fields."""
    account=getattr(getattr(request,'state',None),'account_user',None)
    return account['username'] if account else declared


class SharedAuthorizationMiddleware:
    def __init__(self,app,project_app):self.app=app;self.project_app=project_app

    async def __call__(self,scope,receive,send):
        if scope['type'] not in {'http','websocket'}:return await self.app(scope,receive,send)
        path=scope.get('path','');method=scope.get('method','GET');headers=Headers(scope=scope)
        if path in {'/health','/api/accounts/config','/api/accounts/login'} or method=='OPTIONS':return await self.app(scope,receive,send)
        async def reject(code,message):
            if scope['type']=='websocket':await send({'type':'websocket.close','code':1008})
            else:await JSONResponse({'detail':message},status_code=code)(scope,receive,send)
        if path=='/api/accounts/bootstrap':
            if not secrets.compare_digest(headers.get('x-vision-token',''),self.project_app.state.api_token):return await reject(401,'Administrator bootstrap requires the server process capability')
            return await self.app(scope,receive,send)
        token=headers.get('authorization','').removeprefix('Bearer ')
        if scope['type']=='websocket' and not token:
            token=next((p.removeprefix('vision.') for p in scope.get('subprotocols',[]) if p.startswith('vision.')),'')
        store=self.project_app.state.accounts
        try:account=store.authenticate(token)
        except ValueError:return await reject(401,'Account session expired or unavailable')
        state=scope.setdefault('state',{});state['account_user']=account
        state['account_session_token']=token
        if path.startswith('/api/accounts/'):
            return await self.app(scope,receive,send)
        try:
            selected=store.project_for(account['id'],headers.get('x-vision-project'))
            if selected:
                from backend.api.routes_project import _load_project
                project=_load_project(Path(selected['path']));role=store.project_role(account['id'],project['id'])
                state['scoped_project']=project
            else:project=None;role=None
        except (ValueError,OSError,HTTPException):return await reject(403,'Project permission required')
        if path=='/api/project/create':
            if not account['administrator']:return await reject(403,'Administrator permission required to create shared projects')
            return await self.app(scope,receive,send)
        if path=='/api/project/open':return await reject(403,'Select an authorized shared project through the account project selector')
        if path=='/api/project/list':return await self.app(scope,receive,send)
        if project is None:return await reject(409,'Select an authorized project first')
        if method not in {'GET','HEAD','OPTIONS'} and role!='owner':
            labeling=path.startswith(('/api/annotations/','/api/label-candidates/','/api/label-suggestions/','/api/dataset/metadata/','/api/dataset/formats/','/api/data-workbench/'))
            training=path.startswith(('/api/training/','/api/engine/','/api/automated-training/','/api/patch-classification/','/api/rotation/','/api/ocr/','/api/rotated-detection/','/api/enhancement/','/api/defect-gan/','/api/evaluation/','/api/training-workspace/'))
            parts=path.strip('/').split('/')
            precision_review=len(parts)==6 and parts[:4]==['api','export','flow','optimization-jobs'] and parts[5]=='approve'
            flow=not precision_review and path.startswith(('/api/flowchart/','/api/inspections/','/api/export/','/api/geometry/','/api/flow-workspace/'))
            review=precision_review or path.startswith(('/api/model-deployments/','/api/runtime-services/','/api/model-operations/','/api/fleet/','/api/product-delivery/'))
            compute_jobs=path.startswith('/api/compute/jobs')
            delivery_allowed=False
            team_allowed=False
            if path.startswith('/api/team-data/'):
                management=path in {'/api/team-data/books','/api/team-data/settings'} or path.endswith('/assign')
                voting=path.endswith(('/review','/adjudicate'))
                editing='/lease/' in path
                team_allowed=(management or voting) and role=='reviewer' or editing and role in {'labeler','trainer','reviewer'}
            if path.startswith('/api/product-delivery/'):
                suffix=path.removeprefix('/api/product-delivery/')
                if suffix=='diagnostics' or (suffix.startswith('packages/') and suffix.endswith('/select')):
                    delivery_allowed=role in {'viewer','labeler','trainer','reviewer'}
                elif suffix=='protocol-test' or (suffix.startswith('packages/') and suffix.endswith('/verify')):
                    delivery_allowed=role in {'trainer','reviewer'}
                elif suffix=='operator/inspect':
                    delivery_allowed=role in {'labeler','trainer','reviewer'}
                elif suffix.startswith('servers/') and suffix.endswith('/preflight'):
                    delivery_allowed=bool(account['administrator'])
            allowed=team_allowed or delivery_allowed or (labeling and role in {'labeler','trainer','reviewer'}) or ((training or flow) and role in {'trainer','reviewer'}) or (review and role=='reviewer') or (compute_jobs and role in {'labeler','trainer','reviewer'})
            if not allowed:return await reject(403,'This project role cannot perform the requested action')
        # File selectors must stay inside this project's storage or registered source.
        roots=[Path(project['project_dir']).resolve()]
        if project.get('source_dataset_dir'):roots.append(Path(project['source_dataset_dir']).resolve())
        def check_paths(value,key=''):
            if isinstance(value,dict):
                for child,data in value.items():check_paths(data,child)
            elif isinstance(value,list):
                for data in value:check_paths(data,key.removesuffix('s'))
            elif isinstance(value,str) and (key.endswith(('_path','_dir')) or key=='pretrained_checkpoint') and value:
                if account['administrator'] and path in {'/api/project/update','/api/label-candidates/setup'}:return
                if path=='/api/team-data/books' and key=='relative_path':
                    relative=Path(value)
                    if relative.is_absolute() or '..' in relative.parts or not project.get('source_dataset_dir'):raise ValueError('Example path is outside the project source')
                    candidate=(Path(project['source_dataset_dir'])/relative).resolve()
                else:candidate=Path(value).expanduser().resolve()
                if not any(candidate.is_relative_to(root) for root in roots):raise ValueError('File selector is outside the authorized project')
        try:
            for key,values in parse_qs(scope.get('query_string',b'').decode()).items():
                for value in values:check_paths(value,key)
            if path.startswith(('/api/dataset/raw/','/api/dataset/thumbnail/')):
                if not parse_qs(scope.get('query_string',b'').decode()).get('file_path'):raise ValueError('Shared image reads require an explicit authorized file_path')
            # Buffer only JSON request bodies to enforce approval/actor identity.
            media_type=headers.get('content-type','').split(';',1)[0].strip().lower()
            if method in {'POST','PUT','PATCH','DELETE'} and (not media_type or media_type=='application/json' or media_type.endswith('+json')):
                chunks=[];size=0
                while True:
                    message=await receive()
                    if message['type']!='http.request':break
                    chunk=message.get('body',b'');size+=len(chunk)
                    if size>20*1024*1024:raise ValueError('Shared JSON request exceeds 20 MiB')
                    chunks.append(chunk)
                    if not message.get('more_body'):break
                body=b''.join(chunks)
                if body:
                    value=json.loads(body);check_paths(value)
                    changes=value.get('changes',{}) if isinstance(value,dict) else {}
                    if role not in {'owner','reviewer'} and isinstance(changes,dict) and changes.get('workflow_state')=='approved':
                        return await reject(403,'Reviewer permission required for label approval')
                    def bind_identity(node):
                        if isinstance(node,dict):
                            for key,data in node.items():
                                if key in ('actor','reviewer'):node[key]=account['username']
                                else:bind_identity(data)
                        elif isinstance(node,list):
                            for item in node:bind_identity(item)
                    bind_identity(value)
                    body=json.dumps(value).encode()
                used=False;original_receive=receive
                async def replay():
                    nonlocal used
                    if not used:used=True;return {'type':'http.request','body':body,'more_body':False}
                    return await original_receive()
                receive=replay
        except (ValueError,TypeError,UnicodeError):return await reject(403,'Request data is outside this project or invalid')
        return await self.app(scope,receive,send)
