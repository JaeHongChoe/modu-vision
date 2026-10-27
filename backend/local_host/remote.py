"""Bound, same-origin remote broker. Local and remote identities never share a context."""
import asyncio
from dataclasses import dataclass, field, replace
import json
import math
import re
import secrets
import time
from urllib.parse import urlsplit, urlencode
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket
from starlette.responses import Response

PREFIX = '/api/local-host/remote'
BODY_LIMIT = 8 * 1024 * 1024
RESPONSE_LIMIT = 32 * 1024 * 1024
TIMEOUT = 30
REQUEST_HEADERS = {'accept', 'content-type', 'range', 'if-none-match', 'if-modified-since', 'x-modu-assistant-run'}
RESPONSE_HEADERS = {'content-type', 'content-disposition', 'content-range', 'accept-ranges', 'etag', 'last-modified'}


def origin(value):
    try:
        url = urlsplit(value)
        port = url.port
        if url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment or not url.hostname:
            raise ValueError()
        if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError()
        if any(ch.isspace() or ord(ch)<32 for ch in value) or '\\' in value: raise ValueError()
        host = '[' + url.hostname + ']' if ':' in url.hostname else url.hostname
        return url.scheme + '://' + host + ((':' + str(port)) if port and port != (443 if url.scheme == 'https' else 80) else '')
    except (ValueError, TypeError): raise HTTPException(422, 'Use an HTTPS origin or explicit loopback HTTP tunnel') from None


def allowed_path(path, prefix='/api/'):
    if not path.startswith(prefix) or len(path)>4096 or any(ch in path for ch in ('%', '\\', '#', '?', ':')) or any(ord(ch)<32 for ch in path):
        raise HTTPException(422, 'Invalid remote path')
    if any(part in {'.', '..', ''} for part in path[1:].split('/')):
        raise HTTPException(422, 'Invalid remote path')
    if path.startswith(('/api/local-host/', '/api/accounts/login', '/api/accounts/logout', '/api/accounts/bootstrap', '/api/accounts/oidc', '/api/assistant/')):
        raise HTTPException(403, 'This operation requires its dedicated local broker')
    return path


def clean_request(request):
    scope = dict(request.scope)
    # Remote context is carried only by the owned connection, never interpreted as local authority.
    scope['headers'] = [(k,v) for k,v in scope['headers'] if k.lower() not in {b'x-vision-context', b'x-vision-project'}]
    return Request(scope)


@dataclass(eq=False)
class Connection:
    id: str
    owner: tuple
    origin: str
    token: str
    expires: float
    user: dict
    epoch: int
    project_id: str | None = None
    context: dict | None = None
    streams: set = field(default_factory=set)
    active: int = 0
    retired: bool = False

    def public(self, local_origin):
        return {'remote_id':self.id, 'authority_epoch':self.epoch, 'server_url':self.origin,
                'transport_url':local_origin + PREFIX + '/' + self.id, 'expires_at':self.expires,
                'user':dict(self.user), **({'project_id':self.project_id} if self.project_id else {})}


class RemoteSessions:
    def __init__(self, controller):
        self.controller = controller
        self.connections = {}
        self.epochs = {}
        self.transport = None  # Optional HTTP transport; production uses verified TLS and no environment proxies.

    def authorize(self, request, action):
        request=clean_request(request)
        if request.method in {'GET','HEAD'} and not request.headers.get('x-vision-local-tab') and request.query_params.get('local_tab'):
            request.scope['headers']=[*request.scope['headers'],(b'x-vision-local-tab',request.query_params['local_tab'].encode())]
        return self.controller.authorize(Request(request.scope), 'remote.' + action)

    @staticmethod
    def owner(authority): return authority['session_id'], authority['tab_id'], authority['actor_id']

    def get(self, request, connection_id, action='request'):
        authority = self.authorize(request, action)
        row = self.connections.get(connection_id)
        if row is None: raise HTTPException(409, 'Remote connection changed; reconnect')
        if row.owner != self.owner(authority): raise HTTPException(403, 'This tab does not own this remote connection')
        if row.retired or self.epochs.get(row.owner)!=row.epoch: raise HTTPException(409, 'Remote authority changed; reconnect')
        if row.expires <= time.time(): raise HTTPException(401, 'Remote account session expired')
        return row

    def recheck(self, request, row):
        if self.get(request, row.id) is not row: raise HTTPException(409, 'Remote connection changed')

    def advance(self, owner):
        epoch=self.epochs.get(owner,0)+1
        self.epochs[owner]=epoch
        return epoch

    def check_epoch(self, request, owner, epoch, action):
        if self.owner(self.authorize(request,action))!=owner or self.epochs.get(owner)!=epoch:
            raise HTTPException(409,'Remote operation was superseded')

    def retire_rows(self, rows):
        # Detach all authority synchronously. Awaiting resource cleanup must never
        # change the generation or inspect/remove a newer connection afterwards.
        sockets=[]
        for row in rows:
            row.retired=True
            if self.connections.get(row.id) is row:self.connections.pop(row.id)
            sockets.extend(row.streams);row.streams.clear()
        return sockets

    async def close_streams(self, sockets):
        for socket in sockets:
            try:
                async with asyncio.timeout(5):await socket.close(code=1008)
            except Exception:pass

    async def drop(self, row):
        await self.close_streams(self.retire_rows([row]))

    async def revoke_tab(self, session_id, tab_id):
        owners={key for key in self.epochs if key[:2]==(session_id,tab_id)}
        rows=[row for row in self.connections.values() if row.owner[:2]==(session_id,tab_id)]
        owners.update(row.owner for row in rows)
        for owner in owners:self.advance(owner)
        await self.close_streams(self.retire_rows(rows))

    async def revoke_session(self, session_id):
        owners={key for key in self.epochs if key[0]==session_id}
        rows=[row for row in self.connections.values() if row.owner[0]==session_id]
        owners.update(row.owner for row in rows)
        for owner in owners:self.advance(owner)
        await self.close_streams(self.retire_rows(rows))

    async def exchange(self, method, url, headers=None, body=b'', limit=RESPONSE_LIMIT):
        try:
            async with asyncio.timeout(TIMEOUT):
                async with httpx.AsyncClient(verify=True, trust_env=False, follow_redirects=False,
                                            timeout=TIMEOUT, transport=self.transport) as client:
                    async with client.stream(method, url, headers=headers or {}, content=body) as response:
                        if 300 <= response.status_code < 400 and response.status_code != 304:
                            raise HTTPException(502, 'Remote redirects are not allowed')
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data)>limit: raise HTTPException(502, 'Remote response exceeds the transfer limit')
                        return response.status_code, response.headers, bytes(data)
        except (httpx.HTTPError, TimeoutError): raise HTTPException(502, 'Remote server request failed') from None

    async def body(self, request, limit=BODY_LIMIT):
        value = bytearray()
        try:
            async with asyncio.timeout(TIMEOUT):
                async for chunk in request.stream():
                    value.extend(chunk)
                    if len(value)>limit: raise HTTPException(413, 'Remote request exceeds the transfer limit')
        except TimeoutError:raise HTTPException(408,'Remote upload timed out') from None
        return bytes(value)

    async def json_body(self, request):
        try:
            value=json.loads(await self.body(request, 16384))
            if not isinstance(value,dict): raise ValueError()
            return value
        except (ValueError, UnicodeError): raise HTTPException(422, 'Invalid remote request') from None

    async def login(self, request: Request):
        authority=self.authorize(request,'login');owner=self.owner(authority)
        epoch=self.advance(owner)
        sockets=self.retire_rows([row for row in self.connections.values() if row.owner==owner])
        await self.close_streams(sockets)
        self.check_epoch(request,owner,epoch,'login')
        value=await self.json_body(request);server=origin(value.get('server_url'))
        self.check_epoch(request,owner,epoch,'login')
        username,password=value.get('username'),value.get('password')
        if not isinstance(username,str) or not re.fullmatch(r'[-A-Za-z0-9_.@]{3,80}',username) or not isinstance(password,str) or not 12<=len(password)<=1024:
            raise HTTPException(422,'Check the account name and password')
        if len(self.connections)>=256: raise HTTPException(429,'Too many remote connections')
        status,headers,body=await self.exchange('POST',server+'/api/accounts/login',{'Content-Type':'application/json'},
            json.dumps({'username':username,'password':password,'transport':'bearer'}).encode(),65536)
        self.check_epoch(request,owner,epoch,'login')
        if status!=200: raise HTTPException(401 if status in {401,403} else 502,'Remote login failed')
        try:
            result=json.loads(body);token=result['token'];expires=result['expires_at'];user=result['user']
            if not isinstance(token,str) or not 1<=len(token)<=4096 or '\r' in token or '\n' in token or not isinstance(expires,(float,int)) or not math.isfinite(expires) or expires<=time.time(): raise ValueError()
            if not isinstance(user,dict) or not isinstance(user.get('id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',user['id']) or not isinstance(user.get('username'),str): raise ValueError()
            user={key:user[key] for key in ('id','username','administrator')}
        except (ValueError,KeyError,TypeError): raise HTTPException(502,'Remote server returned an invalid account session') from None
        row=Connection(secrets.token_urlsafe(24),owner,server,token,expires,user,epoch)
        self.connections[row.id]=row
        return row.public(self.controller.origin)

    async def context(self, request: Request, connection_id):
        row=self.get(request,connection_id,'context');value=await self.json_body(request)
        project=value.get('project_id');context=value.get('context')
        if not isinstance(project,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',project): raise HTTPException(422,'Invalid remote project')
        if context is not None:
            if not isinstance(context,dict) or context.get('project_id')!=project or context.get('actor_id')!=row.user['id'] or len(json.dumps(context))>4096: raise HTTPException(422,'Invalid remote context')
            context={key:context[key] for key in ('workspace_id','project_id','actor_id','mode') if key in context}
        self.recheck(request,row)
        epoch=self.advance(row.owner)
        await self.drop(row)
        self.check_epoch(request,row.owner,epoch,'context')
        row=replace(row,id=secrets.token_urlsafe(24),epoch=epoch,project_id=project,context=context,streams=set(),active=0,retired=False)
        self.connections[row.id]=row
        return row.public(self.controller.origin)

    def remote_context(self, row, encoded):
        if not encoded:return row.context
        try:
            if len(encoded)>4096:raise ValueError()
            context=json.loads(encoded)
            if not isinstance(context,dict) or context.get('actor_id')!=row.user['id'] or context.get('project_id')!=row.project_id:raise ValueError()
            if context.get('mode') not in {'team','local'} or not all(isinstance(context.get(key),str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',context[key]) for key in ('workspace_id','project_id','actor_id')):raise ValueError()
            return {key:context[key] for key in ('workspace_id','project_id','actor_id','mode')}
        except (ValueError,TypeError):raise HTTPException(422,'Invalid remote context envelope') from None

    def upstream_headers(self, row):
        return {'Authorization':'Bearer '+row.token, **({'X-Vision-Project':row.project_id} if row.project_id else {}),
                **({'X-Vision-Context':json.dumps(row.context)} if row.context else {})}

    async def health(self, request: Request, connection_id):
        return await self.relay(request,connection_id,'/health')

    async def proxy(self, request: Request, connection_id, path):
        return await self.relay(request,connection_id,allowed_path('/api/'+path))

    async def relay(self, request: Request, connection_id, path):
        row=self.get(request,connection_id)
        if len(request.url.query)>8192: raise HTTPException(422,'Remote query is too long')
        if row.active>=4: raise HTTPException(429,'Too many remote transfers')
        row.active+=1
        try:
            body=await self.body(request);self.recheck(request,row)
            headers={k:v for k,v in request.headers.items() if k.lower() in REQUEST_HEADERS}
            headers.update(self.upstream_headers(row))
            context=self.remote_context(row,request.headers.get('x-vision-remote-context'))
            if context:headers['X-Vision-Context']=json.dumps(context)
            if path=='/health' or path.startswith('/api/accounts/'):
                headers.pop('X-Vision-Context',None);headers.pop('X-Vision-Project',None)
            query=urlencode([(k,v) for k,v in request.query_params.multi_items() if k!='local_tab'])
            status,upstream,data=await self.exchange(request.method,row.origin+path+('?' + query if query else ''),headers,body)
            self.recheck(request,row)
            if status==200 and path=='/api/project/current' and row.project_id and upstream.get('x-vision-context'):
                discovered=self.remote_context(row,upstream['x-vision-context'])
                if row.context and row.context!=discovered:raise HTTPException(409,'Remote project namespace changed; select it again')
                row.context=discovered
            out={k:v for k,v in upstream.items() if k.lower() in RESPONSE_HEADERS}
            if len(upstream.get('x-vision-context',''))<=4096 and upstream.get('x-vision-context'):
                out['X-Vision-Remote-Context']=upstream['x-vision-context']
            out['Cache-Control']='no-store';out['X-Content-Type-Options']='nosniff'
            out['Content-Security-Policy']="sandbox; default-src 'none'"
            return Response(data,status_code=status,headers=out)
        finally: row.active-=1

    @asynccontextmanager
    async def connect_socket(self, url, headers):
        from websockets.asyncio.client import connect
        class NoRedirect(connect):
            def process_redirect(self, exception): return exception
        async with NoRedirect(url, additional_headers=headers, proxy=None, open_timeout=10,
                              close_timeout=2, max_size=1024*1024, max_queue=16) as socket:
            yield socket

    async def socket(self, websocket: WebSocket, connection_id: str, path: str):
        tasks=[];row=None;upstream=None
        try:
            if websocket.url.scheme!='wss' or websocket.headers.get('origin')!=self.controller.origin:
                raise HTTPException(403,'WebSocket origin is not authorized')
            tab=websocket.query_params.get('local_tab','')
            scope=dict(websocket.scope);scope.update(type='http',scheme='https',method='GET')
            scope['headers']=[(k,v) for k,v in scope['headers'] if k.lower()!=b'x-vision-local-tab']+[(b'x-vision-local-tab',tab.encode())]
            request=Request(scope);row=self.get(request,connection_id,'socket')
            path=allowed_path('/ws/'+path,'/ws/')
            if row.active>=4:raise HTTPException(429,'Too many remote streams')
            row.active+=1
            context=self.remote_context(row,websocket.query_params.get('project_context'))
            query=[(k,v) for k,v in websocket.query_params.multi_items() if k not in {'local_tab','project_context'}]
            if context:query.append(('project_context',json.dumps(context)))
            if len(urlencode(query))>8192:raise HTTPException(422,'Remote query is too long')
            target=row.origin.replace('https:','wss:',1).replace('http:','ws:',1)+path
            if query:target+='?'+urlencode(query)
            async with self.connect_socket(target,self.upstream_headers(row)) as upstream:
                self.recheck(request,row);await websocket.accept();self.recheck(request,row);row.streams.update((websocket,upstream))
                async def forward():
                    while True:
                        message=await websocket.receive()
                        if message['type']=='websocket.disconnect':return
                        self.recheck(request,row)
                        value=message.get('text') if message.get('text') is not None else message.get('bytes',b'')
                        if len(value.encode() if isinstance(value,str) else value)>1024*1024:raise HTTPException(413,'WebSocket frame too large')
                        await upstream.send(value)
                async def backward():
                    while True:
                        value=await upstream.recv();self.recheck(request,row)
                        if len(value.encode() if isinstance(value,str) else value)>1024*1024:raise HTTPException(502,'Remote frame too large')
                        if isinstance(value,str):await websocket.send_text(value)
                        else:await websocket.send_bytes(value)
                async def supervise():
                    for _ in range(7200):
                        await asyncio.sleep(.5);self.recheck(request,row)
                tasks=[asyncio.create_task(work()) for work in (forward,backward,supervise)]
                await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        except (Exception, asyncio.CancelledError):
            pass  # No upstream URL, credentials or exception details are returned to the browser.
        finally:
            for task in tasks:task.cancel()
            if tasks:
                try:await asyncio.gather(*tasks,return_exceptions=True)
                except asyncio.CancelledError:pass
            if row:
                row.streams.discard(websocket);row.streams.discard(upstream)
                if row.active:row.active-=1
            try:await websocket.close(code=1008)
            except Exception:pass

    async def logout(self, request: Request, connection_id):
        row=self.get(request,connection_id,'logout');epoch=self.advance(row.owner)
        await self.drop(row)
        try: await self.exchange('POST',row.origin+'/api/accounts/logout',self.upstream_headers(row),limit=65536)
        except HTTPException: pass
        self.check_epoch(request,row.owner,epoch,'logout')
        return {'disconnected':True}


def install_remote(controller):
    remote=RemoteSessions(controller);controller.remote=remote
    router=APIRouter(prefix=PREFIX)
    router.add_api_route('/login',remote.login,methods=['POST'])
    router.add_api_route('/{connection_id}/health',remote.health,methods=['GET','HEAD'])
    router.add_api_route('/{connection_id}/context',remote.context,methods=['POST'])
    router.add_api_route('/{connection_id}/logout',remote.logout,methods=['POST'])
    router.add_api_route('/{connection_id}/api/{path:path}',remote.proxy,methods=['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'])
    router.add_api_websocket_route('/{connection_id}/ws/{path:path}',remote.socket)
    return router
