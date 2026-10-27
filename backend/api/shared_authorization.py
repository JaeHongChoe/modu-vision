"""Authorization and request-scoped project selection for optional shared servers."""
import json
from dataclasses import replace
from pathlib import Path
import secrets
import re
from http.cookies import SimpleCookie
from backend.contracts.authentication import browser_origin_allowed, permission_action
from urllib.parse import parse_qs,unquote
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from fastapi import HTTPException
from backend.engine.assistant_tasks import is_task_preparation
from backend.engine.original_shared_current_project import observe_shared_authorization
from backend.engine.original_shared_auth_denial import original_shared_denial_authentication


def request_actor(request, declared):
    """Shared audit authorship is the session identity, including omitted fields."""
    account=getattr(getattr(request,'state',None),'account_user',None)
    return account['username'] if account else declared


def project_file_roots(project):
    """A shared project's file selectors stay inside its storage or its registered source."""
    roots=[Path(project['project_dir']).resolve()]
    if project.get('source_dataset_dir'):roots.append(Path(project['source_dataset_dir']).resolve())
    return roots


def shared_project_file(request, file_path):
    """The file a shared read may open: only the explicit selector, resolved inside the project.

    Returns None outside shared mode, where the caller keeps its own lookup. A shared read never
    falls back to a path segment or a name search of the server's folders."""
    if getattr(getattr(request,'state',None),'account_user',None) is None:return None
    # The project the middleware authorized for this request (no second project resolution).
    project=getattr(request.state,'scoped_project',None)
    if project is None:raise HTTPException(409,'먼저 권한이 있는 공유 프로젝트를 선택하세요.')
    if not file_path:raise HTTPException(404,'이미지 파일을 찾을 수 없습니다')
    candidate=Path(file_path).expanduser().resolve()
    if not any(candidate.is_relative_to(root) for root in project_file_roots(project)):
        raise HTTPException(403,'File selector is outside the authorized project')
    if not candidate.is_file():raise HTTPException(404,'이미지 파일을 찾을 수 없습니다')
    return candidate


class SharedAuthorizationMiddleware:
    def __init__(self,app,project_app):self.app=app;self.project_app=project_app

    @observe_shared_authorization
    async def __call__(self,scope,receive,send):
        if scope['type'] not in {'http','websocket'}:return await self.app(scope,receive,send)
        path=scope.get('path','');method=scope.get('method','GET');headers=Headers(scope=scope)
        if path in {'/health','/api/accounts/config','/api/accounts/login','/api/accounts/oidc/start','/api/accounts/oidc/callback'} or method=='OPTIONS':return await self.app(scope,receive,send)
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
        with original_shared_denial_authentication(self,scope,receive,send,store,headers):
            try:
                store.bind_workspace(self.project_app.state.context_registry.workspace_id)
                browser=False
                if not token:
                    cookies=SimpleCookie()
                    try:cookies.load(headers.get('cookie',''))
                    except Exception:return await reject(401,'Browser session unavailable')
                    token=cookies['vision_session'].value if 'vision_session' in cookies else ''
                    browser=bool(token)
                account=store.cookie_session(token) if browser else store.authenticate(token)
                if browser:
                    # A cookie handshake also needs a trusted Origin; a CSRF token
                    # is required for unsafe HTTP mutations, never a URL token.
                    origin=headers.get('origin','')
                    if scope.get('scheme','') not in {'https','wss'}:
                        return await reject(403,'Browser cookies require HTTPS')
                    if origin or scope['type']=='websocket' or method not in {'GET','HEAD','OPTIONS'}:
                        if not browser_origin_allowed(self.project_app,origin,scope.get('scheme','')):
                            return await reject(403,'Browser origin is not authorized')
                    if scope['type']=='http' and method not in {'GET','HEAD','OPTIONS'}:
                        try:store.cookie_session(token,headers.get('x-vision-csrf',''))
                        except ValueError:return await reject(403,'Browser CSRF verification required')
            except ValueError:return await reject(401,'Account session expired or unavailable')
        state=scope.setdefault('state',{});state['account_user']=account
        state['account_session_token']=token
        if is_task_preparation(path, method) or path.startswith('/api/accounts/') or (path.startswith('/api/local-host/') and getattr(self.project_app.state,'local_host',None) is not None):
            return await self.app(scope,receive,send)
        try:
            from backend.contracts.context import declared_context
            declared=declared_context(headers,query_string=scope.get('query_string') if scope['type']=='websocket' else None)
            local_projects=getattr(self.project_app.state,'local_projects',None)
            if local_projects is not None:
                project,permission_key,local_context=local_projects.resolve(self.project_app,account,declared,headers.get('x-vision-project'))
                role=store.project_role_exact(account['id'],permission_key) if project else None
                if project:
                    state['scoped_project']=project
                    state['account_project_key']=permission_key
                    state['local_project_context']=local_context
            else:
                if declared and (declared.actor_id!=account['id'] or declared.mode!='team'
                                 or declared.workspace_id!=self.project_app.state.context_registry.workspace_id):
                    return await reject(403,'Explicit context does not match the authenticated account workspace')
                selected=store.project_for(account['id'],declared.project_id if declared else headers.get('x-vision-project'))
                if selected:
                    from backend.api.routes_project import _load_project
                    project=_load_project(Path(selected['path']));role=store.project_role(account['id'],project['id'])
                    state['scoped_project']=project
                    permission_key=project['id']
                else:project=None;role=None;permission_key=None
        except HTTPException as exc:return await reject(exc.status_code,exc.detail)
        except (ValueError,OSError):return await reject(403,'Project permission required')
        if path=='/api/project/create':
            if not account['administrator']:return await reject(403,'Administrator permission required to create shared projects')
            return await self.app(scope,receive,send)
        if path=='/api/project/open':return await reject(403,'Select an authorized shared project through the account project selector')
        if path=='/api/project/list':return await self.app(scope,receive,send)
        if project is None:return await reject(409,'Select an authorized project first')
        if method not in {'GET','HEAD','OPTIONS'} and role!='owner':
            labeling=path.startswith(('/api/annotations/','/api/label-candidates/','/api/label-suggestions/','/api/dataset/metadata/','/api/dataset/formats/','/api/data-workbench/'))
            training=path.startswith(('/api/training/','/api/engine/','/api/automated-training/','/api/patch-classification/','/api/rotation/','/api/ocr/','/api/rotated-detection/','/api/enhancement/','/api/defect-gan/','/api/evaluation/','/api/training-workspace/'))
            training=training or path.startswith('/api/model-execution/')
            parts=path.strip('/').split('/')
            precision_review=len(parts)==6 and parts[:4]==['api','export','flow','optimization-jobs'] and parts[5]=='approve'
            flow=not precision_review and path.startswith(('/api/flowchart/','/api/inspections/','/api/export/','/api/geometry/','/api/flow-workspace/'))
            review=precision_review or path == '/api/image-truth' or path.startswith(('/api/image-truth/','/api/model-deployments/','/api/runtime-services/','/api/model-operations/','/api/fleet/','/api/product-delivery/'))
            flow=flow or path == '/api/flow-evaluations' or path.startswith('/api/flow-evaluations/')
            # Durable dataset imports: starting or stopping one prepares a revision (labelers and up); adopting it
            # changes the project's active dataset (reviewers).
            import_accept=path.startswith('/api/dataset/imports/') and path.endswith('/accept')
            labeling=labeling or (path=='/api/dataset/imports' or path.startswith('/api/dataset/imports/')) and not import_accept
            review=review or import_accept
            compute_jobs=path.startswith('/api/compute/jobs')
            delivery_allowed=False
            team_allowed=False
            if path.startswith('/api/capture-intake/'):
                delivery_allowed=(path.endswith('/register') and role in {'labeler','trainer','reviewer'}) or ((path.endswith(('/review','/adopt')) or path=='/api/capture-intake/drift/references') and role=='reviewer')
            if path.startswith('/api/team-data/'):
                # E05: the label review's profiles, reports, retirement and gold policy are a reviewer's work too.
                management=(path in {'/api/team-data/books','/api/team-data/settings'} or path.endswith('/assign')
                            or path.startswith('/api/team-data/quality/'))
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
            # This exact endpoint records authenticated denials itself. It still
            # requires project membership above and an owner/admin gate in-route.
            emergency_rollback=(method=='POST' and len(parts)==5 and parts[:3]==['api','fleet','targets']
                                and len(parts[3])==32 and all(c in '0123456789abcdef' for c in parts[3])
                                and parts[4]=='emergency-rollback')
            artifact_reference=(method=='POST' and path=='/api/context/artifacts' and role in {'labeler','trainer','reviewer'})
            artifact_upload=(method=='POST' and path in {'/api/artifacts/uploads','/api/dataset/artifacts/ingest'}
                or method in {'PUT','DELETE'} and re.fullmatch(r'/api/artifacts/uploads/[a-f0-9]{32}',path)
                or method=='POST' and re.fullmatch(r'/api/artifacts/uploads/[a-f0-9]{32}/complete',path))
            artifact_upload=bool(artifact_upload and role in {'labeler','trainer','reviewer'})
            library_read=method=='POST' and path=='/api/dataset/library/resolve'  # reads saved selections: any member
            allowed=emergency_rollback or library_read or artifact_reference or artifact_upload or team_allowed or delivery_allowed or (labeling and role in {'labeler','trainer','reviewer'}) or ((training or flow) and role in {'trainer','reviewer'}) or (review and role=='reviewer') or (compute_jobs and role in {'labeler','trainer','reviewer'})
            if not allowed:
                denied=store.authorize(account['id'],permission_action(path,method),permission_key,session_token=token,scoped=False)
                if denied.allowed:denied=replace(denied,allowed=False,reason='route_policy_required')
                store.record_decision(denied)
                return await reject(403,'This project role cannot perform the requested action')
        # Recheck session and membership after routing policy. This contract
        # deliberately does not claim resource revision verification: artifact,
        # job and release owners retain their existing authoritative checks.
        decision=store.authorize(account['id'],permission_action(path,method),permission_key,session_token=token,scoped=False)
        if not decision.allowed or decision.role!=role:
            if decision.allowed:decision=replace(decision,allowed=False,reason='permission_changed')
            store.record_decision(decision)
            return await reject(403,'Project permission changed; retry with current authority')
        state['permission_decision']=decision
        if method not in {'GET','HEAD','OPTIONS'}:store.record_decision(decision)
        # File selectors must stay inside this project's storage or registered source.
        roots=project_file_roots(project)
        def check_paths(value,key='',*,prospective_source=False):
            if isinstance(value,dict):
                for child,data in value.items():
                    check_paths(data,child,prospective_source=prospective_source and not key and child=='folder_path')
            elif isinstance(value,list):
                for data in value:check_paths(data,key.removesuffix('s'))
            elif isinstance(value,str) and (key.endswith(('_path','_dir')) or key=='pretrained_checkpoint') and value:
                # This endpoint validates relative selectors against its
                # registered kind root, including symlinks, before creating a ref.
                if path=='/api/context/artifacts' and key=='relative_path':return
                if account['administrator'] and path in {'/api/project/update','/api/label-candidates/setup'}:return
                if prospective_source:
                    # Match project.update's source registration boundary, but
                    # inspect only this top-level candidate without adopting it.
                    candidate=Path(value).expanduser().resolve()
                    workspace=Path(project['project_dir']).resolve()
                    if workspace==candidate or workspace.is_relative_to(candidate):
                        raise ValueError('Project workspace cannot be inside its dataset source')
                    return
                if path=='/api/team-data/books' and key=='relative_path':
                    relative=Path(value)
                    if relative.is_absolute() or '..' in relative.parts or not project.get('source_dataset_dir'):raise ValueError('Example path is outside the project source')
                    candidate=(Path(project['source_dataset_dir'])/relative).resolve()
                else:candidate=Path(value).expanduser().resolve()
                if not any(candidate.is_relative_to(root) for root in roots):
                    host=getattr(self.project_app.state,'local_host',None)
                    if host is None or not host.check_path(scope,key,value,project):
                        raise ValueError('File selector is outside the authorized project')
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
                    value=json.loads(body)
                    check_paths(value,prospective_source=(method=='POST' and path=='/api/dataset/import'
                        and bool(account['administrator']) and role=='owner'))
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
        except HTTPException as exc:return await reject(exc.status_code,exc.detail)
        except (ValueError,TypeError,UnicodeError):return await reject(403,'Request data is outside this project or invalid')
        host=getattr(self.project_app.state,'local_host',None)
        if host is not None:
            try:host.commit_grants(scope)
            except HTTPException as exc:return await reject(exc.status_code,exc.detail)
        # Passive data witness only after every original auth/path/CSRF check.
        from backend.engine.original_shared_required_flow import original_shared_flow_continuation
        with original_shared_flow_continuation(self,scope):
            return await self.app(scope,receive,send)
