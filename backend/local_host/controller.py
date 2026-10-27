"""Authenticate each local operation before and after the native await boundary."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time

from fastapi import HTTPException, Request

from .grants import PathGrants, PURPOSES, literal_path

STAGED_PURPOSES = {'dataset-source', 'label-import', 'mask-import', 'model-directory', 'pretrained-weights',
                   'runtime-source', 'runtime-inventory', 'inspection-image'}
_AUTH_KEYS = ('actor_id', 'session_id', 'tab_id', 'workspace_id', 'project_id','role','permission_revision','labelset_id','resource_epoch')


class LocalHostController:
    def __init__(self, app, client, *, origin, state_dir, protected_paths=()):
        self.app, self.client, self.origin = app, client, origin
        self.state_dir = Path(state_dir).resolve()
        self.protected = [self.state_dir, self.state_dir.parent/'auth', self.state_dir.parent/'tls', *protected_paths]
        self.grants = PathGrants(protected_roots=self.protected)
        self.tabs = {}
        self.runs = {}
        self.resource_epochs = {}
        self.assistant_scopes = {}
        self.sessions = {}
        self.revoked_sessions = set()
        self.revocation_task = None

    def _account(self, request):
        if request.url.scheme != 'https': raise HTTPException(403, 'Local host operations require HTTPS')
        origin = request.headers.get('origin')
        if origin != self.origin and not (request.method in {'GET','HEAD'} and not origin
                                          and request.headers.get('sec-fetch-site') == 'same-origin'):
            raise HTTPException(403, 'Local host origin is not authorized')
        token = request.cookies.get('vision_session', '')
        if not token: raise HTTPException(401, 'Local account login required')
        store = getattr(self.app.state, 'accounts', None)
        if store is None: raise HTTPException(503, 'Local accounts are unavailable')
        try: account = store.cookie_session(token)
        except ValueError: raise HTTPException(401, 'Account session expired or unavailable') from None
        if request.method not in {'GET','HEAD','OPTIONS'}:
            try: store.cookie_session(token, request.headers.get('x-vision-csrf',''))
            except ValueError: raise HTTPException(403, 'Browser CSRF verification required') from None
        existing = getattr(request.state, 'account_user', None)
        if existing and existing['id'] != account['id']: raise HTTPException(403, 'Account authority changed')
        session = hashlib.sha256(token.encode()).hexdigest()
        if session in self.revoked_sessions: raise HTTPException(401, 'Reconnect this local browser session')
        self.sessions[session] = {'token':token,'actor_id':account['id']}
        request.state.account_user = account
        request.state.account_session_token = token
        return account, session

    def create_tab(self, request):
        account, session = self._account(request)
        self.tabs = {key: row for key, row in self.tabs.items() if row['expires'] > time.monotonic()}
        if len(self.tabs) >= 512: raise HTTPException(429, 'Too many local browser tabs')
        tab = secrets.token_hex(16)
        self.tabs[tab] = {'actor_id':account['id'], 'session_id':session, 'approved':False,
                          'expires':time.monotonic()+8*3600}
        return {'tab_id':tab}

    def capabilities(self, request):
        self._account(request)
        ready = bool(self.client.ready and not self.client.closed)
        return {'capabilities':{name:ready for name in ('pickPaths','openPaths','assistant','updates','projects')},
                'platform':self.client.ready.get('platform') if ready else None,
                'reasons':{} if ready else {'local_host':'로컬 보조 프로그램이 종료되었습니다. 실행 터미널을 확인하세요.'}}

    def process_status(self, request):
        self._account(request)
        process=getattr(self.client,'process',None)
        ready=bool(self.client.ready and not self.client.closed
                   and (process is None or process.returncode is None))
        pid=self.client.ready.get('pid') if ready else None
        if type(pid) is not int or pid<=0:pid=None
        return {'server':{'pid':os.getpid(),'state':'serving'},
                'helper':{'pid':pid,'supervisor_pid':getattr(process,'pid',None),
                          'state':'ready' if ready else 'unavailable',
                          'exit_code':getattr(process,'returncode',None)},
                'restart':'manual',
                'reason':None if ready else '보조 프로그램 연결이 끊겼습니다. 작업 상태를 확인하고 실행 터미널에서 웹을 다시 시작하세요.'}

    def _project(self, request, account, required):
        explicit = request.headers.get('x-vision-context') or request.headers.get('x-vision-project')
        registry = getattr(self.app.state, 'local_projects', None)
        if registry is not None and (explicit or required):
            return registry.resolve_request(request, account)
        project = getattr(request.state, 'scoped_project', None)
        if project:
            context = getattr(request.state, 'local_project_context', None)
            if context is None: context = getattr(request.state, 'project_context', None)
            return {'project':project,'role':self.app.state.accounts.project_role(account['id'],
                       getattr(request.state,'account_project_key',project['id'])), 'context':context}
        if explicit or required: raise HTTPException(409, 'Select an authorized local project first')
        return None

    def authorize(self, request, action, *, project=False):
        account, session = self._account(request)
        tab_id = request.headers.get('x-vision-local-tab','')
        tab = self.tabs.get(tab_id)
        if not tab or tab['expires'] <= time.monotonic() or tab['actor_id'] != account['id'] or tab['session_id'] != session:
            raise HTTPException(403, 'Local browser tab is unavailable; reconnect')
        if action not in {'session','approve','capabilities'} and not action.startswith('projects.') and not tab['approved']:
            raise HTTPException(403, 'Approve this web account in the local host dialog first')
        if not self.client.ready or self.client.closed: raise HTTPException(503, 'Local helper is unavailable')
        remote=getattr(self,'remote',None)
        owner={'actor_id':account['id'],'session_id':session,'tab_id':tab_id}
        reference=request.headers.get('x-vision-remote-authority')
        project_assistant=action in {'assistant.send','assistant.imageIntent','assistant.beginRun','assistant.endRun'}
        if reference and not project_assistant and not action.startswith('remote.'):
            raise HTTPException(403,'Remote assistant authority cannot authorize local device operations')
        native_project=project or action.startswith(('dialog.','shell.open','path.','projects.','linked-source.','host.linked'))
        if remote and remote.has_authority(owner) and not action.startswith('remote.') and native_project:
            raise HTTPException(409 if project_assistant else 403,'Remote project selected: use its separate assistant authority, server paths or ZIP upload')
        from .routes import ASSISTANT, UPDATES
        actor_only=(action in {'session','approve','capabilities'}
                    or action in {'assistant.'+name for name in ASSISTANT} and not project_assistant
                    or action in {'updates.'+name for name in UPDATES})
        # A remote context belongs to its broker connection, never this local
        # installation's project registry. Vault settings and device approval
        # likewise have no project roots; stale saved selections cannot own them.
        resolved = None if action.startswith('remote.') or actor_only else self._project(request, account, project)
        value = resolved['project'] if resolved else None
        context = resolved.get('context') if resolved else None
        if isinstance(context,dict): workspace=context.get('workspace_id')
        else: workspace=getattr(context,'workspace_id',None)
        if value and workspace is None:
            workspace = getattr(self.app.state.context_registry,'workspace_id',None)
        if action.startswith('updates.') and not account['administrator']:
            raise HTTPException(403, 'Local host administration requires an administrator')
        permission_revision=None
        if resolved and resolved.get('membership_key'):
            decision=self.app.state.accounts.authorize(account['id'],'project.read',resolved['membership_key'],
                                                       session_token=request.state.account_session_token)
            if not decision.allowed:raise HTTPException(403,'Current project membership required')
            permission_revision=decision.membership_revision
        return {'actor_id':account['id'],'session_id':session,'tab_id':tab_id,
                'workspace_id':workspace,'project_id':value['id'] if value else None,
                'account':account,'project':value,'role':resolved.get('role') if resolved else None,
                'permission_revision':permission_revision,
                'labelset_id':value.get('active_labelset_id','default') if value else None,
                'resource_epoch':self.resource_epochs.get((session,tab_id,account['id']),0)}

    def recheck(self, request, action, previous, *, project=False):
        current = self.authorize(request, action, project=project)
        if any(current[key] != previous[key] for key in _AUTH_KEYS):
            raise HTTPException(409, 'Local operation authority changed; retry with the current project')
        return current

    async def invoke(self, request, action, method, args, *, project=False):
        remote=getattr(self,'remote',None)
        if request.headers.get('x-vision-remote-authority'):
            if not remote:raise HTTPException(503,'Remote assistant broker unavailable')
            return await remote.invoke_assistant(request,action,method,args)
        authority = self.authorize(request, action, project=project)
        if project and action in {'assistant.send','assistant.imageIntent','assistant.beginRun','assistant.endRun'}:
            owner=(authority['session_id'],authority['tab_id'],authority['actor_id'])
            # The monotonic epoch is sent separately; hashing it would make each
            # next action change its own resource identity after a transition.
            resource=tuple(authority.get(key) for key in ('actor_id','session_id','tab_id','workspace_id','project_id','role','permission_revision','labelset_id'))
            scope='local:'+hashlib.sha256(json.dumps(resource).encode()).hexdigest()
            previous=self.assistant_scopes.get(owner)
            if previous is not None and previous!=scope:
                if remote:
                    remote.advance(owner)
                    await remote.close_streams([])
                else:
                    self.resource_epochs[owner]=self.resource_epochs.get(owner,0)+2
                    await self.client.call(authority,'assistant.retireProject',{'epoch':self.resource_epochs[owner]-1},timeout=5)
                current=self.authorize(request,action,project=True)
                if any(current[key]!=authority[key] for key in _AUTH_KEYS if key!='resource_epoch'):raise HTTPException(409,'Local assistant project authority changed')
                authority=current
            self.assistant_scopes[owner]=scope
            args={**args,'projectAuthority':{'epoch':authority['resource_epoch'],'scope':scope}}
            if remote and action=='assistant.send' and isinstance(args.get('input'),dict) and isinstance(args['input'].get('image'),dict):
                key=remote.run_key(authority,args['input']['image'].get('grantId'))
                if key in remote.image_grants:raise HTTPException(403,'Remote image confirmation cannot be used for a local project')
        try: result = await self.client.call(authority, method, args)
        except (RuntimeError, TimeoutError, ValueError) as error:
            raise HTTPException(503, str(error)[:500]) from None
        self.recheck(request, action, authority, project=project)
        return authority, result

    async def approve(self, request):
        authority, result = await self.invoke(request,'approve','host.approve',
            {'origin':self.origin,'accountLabel':self._account(request)[0]['username']})
        approved = isinstance(result,dict) and result.get('approved') is True
        self.tabs[authority['tab_id']]['approved'] = approved
        return {'approved':approved}

    def _tokens(self, scope):
        from starlette.datastructures import Headers
        raw = Headers(scope=scope).get('x-vision-path-grants','[]')
        if len(raw) > 4096: raise HTTPException(400, 'Too many file selection proofs')
        try: values = json.loads(raw)
        except ValueError: raise HTTPException(400, 'Invalid file selection proof') from None
        if not isinstance(values,list) or len(values)>16 or not all(isinstance(v,str) and len(v)<=128 for v in values):
            raise HTTPException(400, 'Invalid file selection proof')
        return values

    def check_path(self, scope, key, value, project):
        request = Request(scope)
        authority = self.authorize(request,'path.consume')
        token = self.grants.validate(authority,self._tokens(scope),value,key,scope['path'])
        if token:
            scope.setdefault('state',{}).setdefault('_local_host_pending_grants',set()).add(token)
            return True
        return False

    def commit_grants(self, scope):
        self.consume_run(scope)
        pending = scope.setdefault('state',{}).pop('_local_host_pending_grants',set())
        if not pending: return
        authority = self.authorize(Request(scope),'path.consume')
        try: self.grants.consume(authority,pending)
        except ValueError as error: raise HTTPException(409,str(error)) from None

    def arm_run(self, authority, run_id, tool):
        from .grants import _scope
        if not isinstance(run_id,str) or not re.fullmatch(r'run_[a-f0-9]{24}',run_id) or tool not in {
            'save_split','assign_class','generate_label_drafts','start_training'}:
            raise HTTPException(422,'Invalid assistant run attribution')
        remote=getattr(self,'remote',None)
        key=remote.run_key(authority,run_id) if remote and authority.get('remote') else run_id
        self.runs[key] = {'scope':_scope(authority),'tool':tool,'expires':time.monotonic()+120,
                          **({'remote_scope':remote.run_scope(authority)} if remote and authority.get('remote') else {})}

    def disarm_run(self, authority, run_id):
        from .grants import _scope
        remote=getattr(self,'remote',None)
        key=remote.run_key(authority,run_id) if remote and authority.get('remote') else run_id
        row=self.runs.get(key)
        if row and row['scope']==_scope(authority):self.runs.pop(key,None)

    def consume_run(self, scope):
        from starlette.datastructures import Headers
        from .grants import _scope
        run_id=Headers(scope=scope).get('x-modu-assistant-run')
        if not run_id:return
        authority=self.authorize(Request(scope),'assistant.write')
        row=self.runs.get(run_id)
        writes={'save_split':('POST','/api/dataset/split'),
                'assign_class':('PUT','/api/dataset/metadata/class-override'),
                'generate_label_drafts':('POST','/api/label-suggestions/batches'),
                'start_training':('POST','/api/training/start')}
        if not row or row.get('remote_scope') or row['scope']!=_scope(authority) or row['expires']<=time.monotonic() or writes[row['tool']]!=(scope['method'],scope['path']):
            raise HTTPException(403,'Assistant run attribution expired or does not match this write')
        del self.runs[run_id]

    async def dispose_tab(self, request):
        authority=self.authorize(request,'session')
        self.tabs.pop(authority['tab_id'],None)
        self.grants.revoke(session_id=authority['session_id'],tab_id=authority['tab_id'])
        remote=getattr(self,'remote',None)
        if remote:await remote.revoke_tab(authority['session_id'],authority['tab_id'])
        try:await self.client.call(authority,'session.dispose',{},timeout=5)
        except (RuntimeError,TimeoutError):pass
        return {'disposed':True}

    async def revoke_session(self, session):
        entry=self.sessions.pop(session,None)
        tabs=[(key,row) for key,row in self.tabs.items() if row['session_id']==session]
        self.revoked_sessions.add(session)
        self.grants.revoke(session_id=session)
        for key,_ in tabs:self.tabs.pop(key,None)
        self.runs={key:row for key,row in self.runs.items() if row['scope'][1]!=session}
        if entry and tabs and not self.client.closed:
            authority={'actor_id':entry['actor_id'],'session_id':session,'tab_id':tabs[0][0]}
            try:await self.client.call(authority,'session.disposeAll',{},timeout=5)
            except (RuntimeError,TimeoutError):pass
        remote=getattr(self,'remote',None)
        if remote:await remote.revoke_session(session)

    async def prune_sessions(self):
        for session,entry in list(self.sessions.items()):
            try:self.app.state.accounts.cookie_session(entry['token'])
            except ValueError:await self.revoke_session(session)

    async def watch_revocations(self):
        import asyncio
        try:
            while True:
                await self.prune_sessions()
                await asyncio.sleep(.5)
        except asyncio.CancelledError:pass

    def start_supervision(self):
        import asyncio
        self.revocation_task=asyncio.create_task(self.watch_revocations())

    async def close(self):
        import asyncio
        if self.revocation_task:
            self.revocation_task.cancel()
            await asyncio.gather(self.revocation_task,return_exceptions=True)
        for session in list(self.sessions):await self.revoke_session(session)

    def require_grants(self, request, fields, project=None):
        self.authorize(request,'path.consume')
        for key, value in fields:
            if not self.check_path(request.scope,key,str(value),project):
                request.scope.setdefault('state',{}).pop('_local_host_pending_grants',None)
                raise HTTPException(403,'Select this path for the current local operation')
        self.commit_grants(request.scope)

    async def pick(self, request, kind, options):
        purpose = options.get('purpose')
        if purpose not in PURPOSES: raise HTTPException(422, 'Select the intended file operation')
        needs_project = purpose in STAGED_PURPOSES
        authority, selected = await self.invoke(request,'dialog.'+kind,'dialog.'+kind,options,project=needs_project)
        if selected is None: return {'path':None}
        try:
            if purpose not in STAGED_PURPOSES:
                return self.grants.issue(authority,selected,purpose,'directory' if kind=='folder' else 'file')
            allowed = {'owner','reviewer','trainer','labeler'} if purpose in {'label-import','mask-import'} else {'owner','reviewer','trainer'}
            if authority.get('role') not in allowed: raise HTTPException(403,'Project role cannot import this input')
            from .staging import SourceSelection, stage_selection
            import asyncio
            # Pin the selection before one-use consent consumption. The worker
            # owns its own no-follow descriptor and must match this identity;
            # request cancellation cannot close a worker-owned source descriptor.
            with SourceSelection(selected) as source:
                selection = self.grants.issue(authority,source.path,purpose,'directory' if kind=='folder' else 'file')
                self.recheck(request,'dialog.'+kind,authority,project=True)
                self.grants.consume(authority,[selection['grant_id']])
                source.check_current()
                staged = await asyncio.to_thread(stage_selection,source.path,
                    Path(authority['project']['project_dir']),purpose,protected_roots=self.protected,
                    expected_source_identity=source.identity)
                self.recheck(request,'dialog.'+kind,authority,project=True)
                return {**self.grants.issue(authority,staged,purpose,'directory' if staged.is_dir() else 'file'),'staged':True}
        except (ValueError,OSError) as error: raise HTTPException(422,str(error)[:500]) from None

    async def open(self, request, path):
        authority = self.authorize(request,'shell.open')
        try: candidate=literal_path(path)
        except (OSError,ValueError): raise HTTPException(422,'Select an existing local result') from None
        if any(candidate==Path(p).resolve() or candidate.is_relative_to(Path(p).resolve())
               or candidate.is_dir() and Path(p).resolve().is_relative_to(candidate) for p in self.protected):
            raise HTTPException(403,'Private host state cannot be opened')
        project=authority.get('project')
        roots=[Path(project['project_dir']).resolve()] if project else []
        if project and project.get('source_dataset_dir'): roots.append(Path(project['source_dataset_dir']).resolve())
        if not any(candidate.is_relative_to(root) for root in roots):
            raise HTTPException(403,'Open a result from the currently authorized project')
        _, result=await self.invoke(request,'shell.open','shell.open',{'path':str(candidate)})
        return result
