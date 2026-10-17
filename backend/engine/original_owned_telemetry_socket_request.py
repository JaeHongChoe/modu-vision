"""One original shared telemetry socket; no task/tree/release authority."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import asyncio
import inspect
import os
import sys
import time
import weakref
from types import FunctionType, MethodType

from backend.engine.original_request_activity import _context
from backend.engine.original_owned_account_request import _headers, _SelectedCurrentAuth
from backend.engine.original_owned_flow_request import _methods_original

_ACTIVE = ContextVar('original_owned_telemetry_socket_request', default=None)
_HANDLER = None
_OWNERS = weakref.WeakKeyDictionary()
_TASKS = weakref.WeakKeyDictionary()
_DRAINS = weakref.WeakKeyDictionary()


def _nested(code):
    result={code}
    for child in code.co_consts:
        if type(child) is type(code):result.update(_nested(child))
    return result


def original_owned_telemetry_production(app,admission):
    """Cache provenance only during this live original authenticated WS call."""
    attempt=_ACTIVE.get()
    if (type(attempt) is not _SocketAttempt or attempt.owner.app is not app
            or attempt.owner.admission is not admission or not attempt.original() or attempt.failed
            or attempt.entries or attempt.socket is not None or not attempt.authenticated()):return False
    from fastapi.routing import APIRouter
    frame=sys._getframe(1);owned=router=False
    for _ in range(64):
        if frame is None:break
        if frame.f_code is type(attempt.owner.owned)._observed_call.__code__:
            owned|=(frame.f_locals.get('self') is attempt.owner.owned
                and frame.f_locals.get('activity') is attempt.activity and frame.f_locals.get('scope') is attempt.scope)
        if frame.f_code is APIRouter.app.__code__:
            router|=frame.f_locals.get('self') is app.router and frame.f_locals.get('scope') is attempt.scope
        frame=frame.f_back
    return owned and router


def observe_original_telemetry_handler(register):
    def decorate(function):
        global _HANDLER
        if function.__name__!='websocket_telemetry_endpoint':raise ValueError('Unknown original telemetry socket')
        @wraps(function)
        async def observed(websocket):
            attempt=_ACTIVE.get()
            if type(attempt) is not _SocketAttempt:return await function(websocket)
            valid=attempt.activate(websocket,observed,function)
            try:result=await function(websocket)
            except BaseException:
                attempt.fail('handler_failed');raise
            attempt.returned(result,valid)
            return result
        observed.__signature__=inspect.signature(function,eval_str=True)
        register(observed);_HANDLER=observed,function
        return function
    return decorate


def observe_original_telemetry_register(function):
    @wraps(function)
    async def observed(broadcaster,websocket):
        attempt=_ACTIVE.get()
        if type(attempt) is not _SocketAttempt:return await function(broadcaster,websocket)
        valid=attempt.register_entry(broadcaster,websocket)
        try:result=await function(broadcaster,websocket)
        except BaseException:
            attempt.fail('register_failed');raise
        attempt.register_return(broadcaster,websocket,valid)
        return result
    return observed


def observe_original_telemetry_unregister(function):
    @wraps(function)
    def observed(broadcaster,websocket):
        attempt=_ACTIVE.get()
        if type(attempt) is not _SocketAttempt:return function(broadcaster,websocket)
        valid=attempt.unregister_entry(broadcaster,websocket)
        try:result=function(broadcaster,websocket)
        except BaseException:
            attempt.fail('unregister_failed');raise
        attempt.unregister_return(broadcaster,websocket,valid)
        return result
    return observed


def observe_original_telemetry_drain_creation(function):
    """Retain the original shutdown coroutine's actual creation call."""
    @wraps(function)
    def observed(broadcaster,loop,records,deadline,observer):
        owner=_OWNERS.get(broadcaster)
        frame=sys._getframe(1)
        valid=(type(owner) is OriginalOwnedTelemetrySocketRequest and owner.original()
            and frame.f_code in {type(broadcaster).prepare_shutdown.__code__,type(broadcaster).shutdown.__code__}
            and frame.f_locals.get('self') is broadcaster and frame.f_locals.get('observer') is observer
            and frame.f_locals.get('loop') is loop and frame.f_locals.get('records') is records
            and frame.f_locals.get('deadline')==deadline)
        coroutine=function(broadcaster,loop,records,deadline,observer)
        if valid:_DRAINS[coroutine]=(owner,broadcaster,loop,records,deadline,observer,coroutine)
        elif owner is not None:owner.fail('drain_creation')
        return coroutine
    return observed


def observe_original_telemetry_task_creation(function):
    """Only original start/drain frames may mint a producer task witness."""
    @wraps(function)
    def observed(broadcaster,loop,role,coroutine,deadline=None):
        owner=_OWNERS.get(broadcaster)
        if owner is None:return function(broadcaster,loop,role,coroutine,deadline)
        frame=sys._getframe(1)
        kind=type(broadcaster);current=asyncio.current_task()
        expected=(kind._event_dispatch_loop if role=='dispatch' else kind._hardware_telemetry_loop
                  if role=='hardware' else kind._safe_close if role=='connection_close' else None)
        valid=(type(owner) is OriginalOwnedTelemetrySocketRequest and owner.original()
            and asyncio.get_running_loop() is loop and expected is not None and inspect.iscoroutine(coroutine)
            and coroutine.cr_code is expected.__code__ and coroutine.cr_frame is not None
            and coroutine.cr_frame.f_locals.get('self') is broadcaster)
        if role in {'dispatch','hardware'}:
            valid=(valid and frame.f_code is kind.start.__code__ and frame.f_locals.get('self') is broadcaster
                and frame.f_locals.get('loop') is loop and deadline is None)
        elif role=='connection_close':
            birth=_DRAINS.get(current.get_coro()) if current is not None else None
            valid=(valid and birth is not None and birth[0] is owner and birth[1] is broadcaster
                and birth[2] is loop and frame.f_code is kind._drain_original_loop.__wrapped__.__code__
                and frame.f_locals.get('self') is broadcaster and frame.f_locals.get('loop') is loop
                and frame.f_locals.get('records') is birth[3] and frame.f_locals.get('deadline')==birth[4]
                and frame.f_locals.get('observer') is birth[5] and birth[5].get('original_task') is current
                and current.get_coro() is birth[6] and coroutine.cr_frame.f_locals.get('ws') is frame.f_locals.get('ws'))
        result=function(broadcaster,loop,role,coroutine,deadline)
        if (valid and type(result) is dict and set(result)=={'task','loop','role','joined'}
                and type(result['task']) is asyncio.Task and result['task'].get_coro() is coroutine
                and result['task'].get_loop() is loop and result['loop'] is loop and result['role']==role
                and result['joined'] is False and len(_TASKS)<256):
            birth=_TaskBirth(owner,broadcaster,loop,role,coroutine,result)
            _TASKS[result['task']]=birth
        elif owner is not None:owner.fail('task_creation')
        return result
    return observed


class _TaskBirth:
    def __init__(self,owner,broadcaster,loop,role,coroutine,record):
        self.owner,self.broadcaster,self.loop,self.role=owner,broadcaster,loop,role
        self.coroutine,self.record,self.task=coroutine,record,record['task']
        self.queue=broadcaster._queue;self.pid=os.getpid()
        self.task_calls=tuple((name,getattr(asyncio.Task,name),getattr(self.task,name)) for name in
            ('get_coro','get_loop','done','cancelled','exception','result'))
        self.binding=self,owner,broadcaster,loop,coroutine,record,self.task,self.queue,self.task_calls

    def original(self,completed=False):
        return (type(self) is _TaskBirth and _TASKS.get(self.task) is self and self.pid==os.getpid()
            and all(a is b for a,b in zip(self.binding,(self,self.owner,self.broadcaster,self.loop,
                self.coroutine,self.record,self.task,self.queue,self.task_calls))) and self.owner.original()
            and _methods_original(self,_TaskBirth,self.owner.calls)
            and all(getattr(asyncio.Task,name) is descriptor
                and type(method) is type(descriptor.__get__(self.task,asyncio.Task))
                and type(getattr(self.task,name,None)) is type(method)
                and getattr(self.task,name).__self__ is self.task and getattr(self.task,name)==method
                and method==descriptor.__get__(self.task,asyncio.Task) for name,descriptor,method in self.task_calls)
            and type(self.task) is asyncio.Task and self.task.get_coro() is self.coroutine
            and self.task.get_loop() is self.loop and self.broadcaster._loop is self.loop
            and self.broadcaster._queue is self.queue and _methods_original(self.queue,asyncio.Queue,self.owner.calls)
            and any(row is self.record for row in self.broadcaster._task_lifetimes)
            and self.record.get('task') is self.task and self.record.get('loop') is self.loop
            and self.record.get('role')==self.role
            and (completed and self.task.done() and not self.task.cancelled() and self.task.exception() is None
                or not completed and not self.task.done() and self.record.get('joined') is False)
            and (self.role=='connection_close' or self.broadcaster._task_heads.get(self.role) is self.record))


class _SocketIncluded:
    """The original two-row telemetry inclusion, with its actual WS clone."""
    def __init__(self,owner,branch):
        from fastapi import routing
        from backend.engine.original_included_current_project import _Fields
        self.owner,self.branch,self.framework=owner,branch,routing
        self.kind,self.router,self.context=type(branch),branch.original_router,branch.include_context
        self.fields,self.router_fields=_Fields(self.context),_Fields(self.router)
        self.routes,self.rows=self.router.routes,tuple(self.router.routes)
        self.route_fields=tuple(_Fields(route) for route in self.rows)
        if (len(self.rows)!=2 or {route.path for route in self.rows}!={'/ws/telemetry','/ws/training'}
                or any(type(route) is not routing.APIWebSocketRoute for route in self.rows)):
            raise ValueError('Unknown original telemetry router')
        self.version,self.lock=self.router._get_routes_version(),branch._effective_routes_lock
        if (branch._effective_candidates or branch._effective_candidates_version is not None
                or branch._effective_low_priority_routes or branch._effective_low_priority_routes_version is not None):
            raise ValueError('Original telemetry inclusion is not fresh')
        self.empty,self.low=branch._effective_candidates,branch._effective_low_priority_routes
        self.minted,self.contexts,self.cache=[],[],None
        self.calls=tuple((name,getattr(self.kind,name)) for name in
            ('effective_candidates','_build_effective_context','matches','handle','_match','_handle_selected'))
        self.codes=tuple((function,function.__code__) for _,function in self.calls)
        self.original_producer=self.kind._build_effective_context
        def observed(node,route):
            from backend.engine.original_owned_account_request import original_owned_login_production
            frame=sys._getframe(1);rows=frame.f_locals.get('effective_candidates')
            valid=(self.original() and node is branch
                and (original_owned_telemetry_production(owner.app,owner.admission)
                     or original_owned_login_production(owner.app,owner.admission))
                and frame.f_code is self.kind.effective_candidates.__code__
                and frame.f_locals.get('self') is branch and frame.f_locals.get('route') is route
                and type(rows) is list and len(rows)==len(self.minted)
                and all(a is b for a,b in zip(rows,self.minted))
                and len(self.minted)<len(self.rows) and route is self.rows[len(self.minted)])
            context=self.original_producer(node,route)
            clone=context.starlette_route
            if (not valid or context.original_route is not route or type(clone) is not routing.APIWebSocketRoute
                    or clone.path!=route.path or clone.endpoint is not route.endpoint
                    or clone.dependant.call is not route.endpoint):owner.fail('foreign_socket_production')
            else:
                self.minted.append(context)
                self.contexts.append((_Fields(context),_Fields(clone),_Fields(clone.dependant,slots=True)))
            return context
        self.producer=MethodType(observed,branch)
        self.producer_function,self.producer_code=observed,observed.__code__
        branch._build_effective_context=self.producer

    def original(self):
        node=self.branch
        if (type(node) is not self.kind or node.original_router is not self.router
                or node.include_context is not self.context or not self.fields.original() or not self.router_fields.original()
                or self.router.routes is not self.routes or tuple(self.routes)!=self.rows
                or not all(fields.original() for fields in self.route_fields)
                or node._effective_routes_lock is not self.lock or node._build_effective_context is not self.producer
                or self.producer.__func__ is not self.producer_function or self.producer_function.__code__ is not self.producer_code
                or self.router._get_routes_version()!=self.version
                or node._effective_low_priority_routes is not self.low or node._effective_low_priority_routes
                or node._effective_low_priority_routes_version is not None
                or any(getattr(self.kind,name) is not function for name,function in self.calls)
                or any(function.__code__ is not code for function,code in self.codes)
                or any(type(getattr(node,name,None)) is not MethodType
                    or getattr(node,name).__self__ is not node or getattr(node,name).__func__ is not function
                    for name,function in self.calls if name!='_build_effective_context')
                or not all(a.original() and b.original() and c.original() for a,b,c in self.contexts)):return False
        cache=node._effective_candidates
        if cache is self.empty and node._effective_candidates_version is None:return not cache
        valid=(type(cache) is list and node._effective_candidates_version==self.version
            and len(cache)==len(self.rows)==len(self.minted) and all(a is b for a,b in zip(cache,self.minted)))
        if valid and self.cache is None:self.cache=cache
        return valid and cache is self.cache

    def selected(self,scope,route,dependant):
        context=self.framework._get_scope_effective_route_context(scope)
        return (self.original() and any(context is value for value in self.minted)
            and context.original_route is route and context.starlette_route is scope.get('route')
            and context.starlette_route.endpoint is route.endpoint
            and context.starlette_route.dependant is dependant and dependant.call is route.endpoint
            and self.framework._get_scope_included_router(scope) is self.branch)


class OriginalOwnedTelemetrySocketRequest:
    def __init__(self,app,admission):
        from backend.api import websocket_telemetry as telemetry, shared_authorization
        from backend.contracts import context
        from backend.engine import original_current_project_request, original_owned_account_request
        from backend.engine import original_included_current_project as included
        from backend.engine.original_request_activity import OriginalRequestActivityObserver
        from backend import main
        from fastapi import routing
        from starlette import routing as starlette_routing, websockets
        self.app,self.admission,self.accounts=app,admission,admission._account_requests
        self.observer,self.condition=admission._activity_observer,admission._condition
        self.account_kind=type(self.accounts)
        self.registry,self.storage=self.accounts.named.registry,self.accounts.named.storage
        self.registry_binding=self.registry.root,self.registry.path
        self.registry_calls=self.accounts.named.registry_calls
        self.broadcaster,self.broadcaster_kind=telemetry.broadcaster,telemetry.TelemetryBroadcaster
        self.connections=self.broadcaster._active_connections
        self.endpoint,self.handler=_HANDLER
        self.pid,self.deadline=os.getpid(),None
        self.pending,self.records,self.failed={},[],False
        self.binding=self,app,admission,self.accounts,self.observer,self.condition,self.registry,self.storage,self.broadcaster,self.connections
        modern=getattr(routing,'_IncludedRouter',None)
        branches=[node for node in app.router.routes if modern is not None and type(node) is modern
            and node.original_router is telemetry.router]
        self.included=_SocketIncluded(self,branches[0]) if len(branches)==1 else None
        selected=telemetry.router.routes if self.included is not None else app.router.routes
        routes=[route for route in selected if type(route) is routing.APIWebSocketRoute
            and route.path=='/ws/telemetry' and route.endpoint is self.endpoint and route.dependant.call is self.endpoint]
        if len(routes)!=1:raise ValueError('Unknown original telemetry endpoint')
        self.route=routes[0];self.route_binding=self.route.dependant,self.route.app
        self.route_kind,self.socket_kind=routing.APIWebSocketRoute,websockets.WebSocket
        self.disconnected=websockets.WebSocketState.DISCONNECTED
        self.dispatch_codes=_nested(routing.get_websocket_app.__code__)
        self.session_codes=_nested(routing.websocket_session.__code__)
        self.calls=[(module,name,getattr(module,name)) for module,names in (
            (telemetry,('broadcaster','TelemetryBroadcaster','_handle_websocket_connection','websocket_telemetry_endpoint',
                'get_host_telemetry','WebSocket','WebSocketDisconnect','asyncio')),
            (self.broadcaster_kind,('register','unregister','active_connections','start','_record_task',
                '_event_dispatch_loop','_hardware_telemetry_loop','broadcast_sync','broadcast','_can_receive',
                '_safe_close','_drain_original_loop','prepare_shutdown','shutdown','_join_records','_begin_shutdown')),
            (asyncio,('Queue','current_task','get_running_loop','Task')),
            (asyncio.Task,('get_coro','get_loop','done','cancelled','exception','result')),
            (asyncio.Queue,('__init__','get','put_nowait','task_done')),
            (OriginalRequestActivityObserver,('bind_telemetry_sender','event','_row','_valid','_original')),
            (self.account_kind,('original',)),
            (context,('ContextRegistry','declared_context','current_project_context')),
            (context.ContextRegistry,('_db','transaction','register_project','context')),
            (main,('ProjectContextMiddleware','ProjectStorageScopeMiddleware')),
            (main.ProjectContextMiddleware,('__call__',)),(main.ProjectStorageScopeMiddleware,('__call__',)),
            (main.OwnedWorkAdmissionMiddleware,('__call__','_observed_call')),
            (shared_authorization,('permission_action',)),
            (original_owned_account_request,('original_owned_login_production','_headers','_SelectedCurrentAuth')),
            (original_current_project_request,('_CURRENT','observe_context_db','observe_storage_scope')),
            (included,('_Fields','_pin','_same')),(included._Fields,('__init__','original')),
            (routing,('APIWebSocketRoute','get_websocket_app','websocket_session','APIRouter','solve_dependencies')),
            (routing.APIWebSocketRoute,('__init__','matches','handle')),(routing.APIRouter,('app',)),
            (starlette_routing,('WebSocketRoute',)),(starlette_routing.WebSocketRoute,('__init__','matches','handle')),
            (websockets,('WebSocket','WebSocketState')),
            (websockets.WebSocket,('__init__','accept','close','send','send_text','receive','receive_text')),
            (sys.modules[__name__],('_ACTIVE','_HANDLER','_OWNERS','_TASKS','_DRAINS','_headers','_context','_methods_original','_SelectedCurrentAuth',
                'OriginalOwnedTelemetrySocketRequest','_SocketAttempt','_SocketAuth','_SocketIncluded','_TaskBirth')),
        ) for name in names]
        for name,function in tuple(globals().items()):
            if type(function) is FunctionType and function.__module__==__name__:self.calls.append((sys.modules[__name__],name,function))
        for kind in (OriginalOwnedTelemetrySocketRequest,_SocketAttempt,_SocketAuth,_SocketIncluded,_TaskBirth):
            for name in dir(kind):
                value=getattr(kind,name)
                if type(value) is FunctionType:self.calls.append((kind,name,value))
        self.codes=[(self.endpoint,self.endpoint.__code__)]
        for _,_,function in self.calls:
            for _ in range(5):
                if hasattr(function,'__code__'):self.codes.append((function,function.__code__))
                function=getattr(function,'__wrapped__',None)
                if function is None:break
        self.eligible=(type(self.broadcaster) is self.broadcaster_kind and type(self.connections) is set
            and telemetry.websocket_telemetry_endpoint is self.handler and self.endpoint.__wrapped__ is self.handler
            and self.broadcaster_kind.register.__wrapped__ is not None
            and type(self.registry) is context.ContextRegistry)
        _OWNERS[self.broadcaster]=self
        self.observer.bind_telemetry_sender(admission,self,self.sender)

    def bind_chain(self,middleware,downstream):
        self.owned,self.downstream=middleware,downstream
        if (getattr(self.accounts,'owned',None) is not middleware
                or getattr(self.accounts,'downstream',None) is not downstream):self.eligible=False;return
        self.storage=self.accounts.named.storage
        self.binding=self,self.app,self.admission,self.accounts,self.observer,self.condition,self.registry,self.storage,self.broadcaster,self.connections

    def fail(self,reason):
        self.failed=True;self.admission._unsupported.add('uncovered_request_protocol')
        self.observer.poison('owned_socket_'+reason)
    def __copy__(self):
        self.fail('owner_copy');raise ValueError('Original socket owner cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):return self.__copy__()

    def original(self):
        return (self.eligible and not self.failed and self.pid==os.getpid()
            and all(a is b for a,b in zip(self.binding,(self,self.app,self.admission,self.accounts,self.observer,
                self.condition,self.registry,self.storage,self.broadcaster,self.connections)))
            and self.admission._telemetry_socket_requests is self
            and _methods_original(self,OriginalOwnedTelemetrySocketRequest,self.calls)
            and _methods_original(self.observer,type(self.observer),self.calls)
            and _methods_original(self.accounts,self.account_kind,self.calls) and self.accounts.original()
            and _methods_original(self.broadcaster,self.broadcaster_kind,
                tuple(row for row in self.calls if row[1]!='active_connections'))
            and self.broadcaster._active_connections is self.connections
            and self.app.state.context_registry is self.registry
            and self.registry.root is self.registry_binding[0] and self.registry.path is self.registry_binding[1]
            and all(type(getattr(self.registry,name,None)) is MethodType
                and getattr(self.registry,name).__self__ is self.registry
                and getattr(self.registry,name).__func__ is function for name,function in self.registry_calls)
            and self.owned is self.accounts.owned and self.owned.app is self.downstream
            and all(getattr(module,name) is value for module,name,value in self.calls)
            and all(function.__code__ is code for function,code in self.codes)
            and _HANDLER==(self.endpoint,self.handler) and self.endpoint.__wrapped__ is self.handler
            and (self.included is None or _methods_original(self.included,_SocketIncluded,self.calls) and self.included.original())
            and _methods_original(self.route,self.route_kind,self.calls)
            and self.route.path=='/ws/telemetry' and self.route.endpoint is self.endpoint
            and self.route.dependant is self.route_binding[0] and self.route.dependant.call is self.endpoint
            and self.route.app is self.route_binding[1])

    def begin(self,activity):
        from backend.contracts.context import declared_context
        from starlette.datastructures import Headers
        if (activity is None or activity.ingress.get('type')!='websocket'
                or activity.ingress.get('path')!='/ws/telemetry' or activity.scope.get('app') is not self.app
                or not self.original() or not self.observer._valid(activity) or len(self.records)>=128):return False
        rows=_headers(activity.scope)
        if rows is None:return False
        tokens=[v for k,v in rows if k.lower()==b'authorization']
        projects=[v for k,v in rows if k.lower()==b'x-vision-project']
        contexts=[v for k,v in rows if k.lower()==b'x-vision-context']
        if (len(tokens)!=1 or not tokens[0].startswith(b'Bearer ') or not 7<len(tokens[0])<=263
                or len(projects)>1 or len(contexts)>1 or any(k.lower()==b'cookie' for k,_ in rows)):return False
        try:
            token=tokens[0][7:].decode('ascii')
            declared=declared_context(Headers(scope=activity.scope),query_string=activity.scope.get('query_string'))
            project=declared.project_id if declared is not None else projects[0].decode('ascii') if projects else None
        except (ValueError,UnicodeError):return False
        if type(project) is not str or not project:return False
        attempt=_SocketAttempt(self,activity,rows,project,token)
        self.pending[activity.token]=attempt;self.records.append(attempt);self.admission._active+=1
        return True

    def finish(self,activity):
        attempt=self.pending.get(activity.token) if activity is not None else None
        if type(attempt) is not _SocketAttempt:return
        if (not _methods_original(attempt,_SocketAttempt,self.calls)
                or not _methods_original(attempt.auth,_SocketAuth,self.calls)):
            _SocketAttempt.fail(attempt,'changed_attempt_methods');return
        if not attempt.complete(self.observer._records.get(activity.token)):
            attempt.fail('unconfirmed_lifetime');return
        del self.pending[activity.token];self.admission._active-=1;attempt.settled=True

    def sender(self,activity,action,value):
        """Internally fetched source frames, never a supplied task/frame grant."""
        if not _methods_original(self,OriginalOwnedTelemetrySocketRequest,self.calls) or not self.original():return False
        attempt=self.pending.get(activity.token) if activity is not None else None
        task=asyncio.current_task();birth=_TASKS.get(task)
        if (type(attempt) is _SocketAttempt and attempt.owner is self and attempt.activity is activity
                and type(birth) is _TaskBirth and birth.owner is self
                and (not _methods_original(birth,_TaskBirth,self.calls) or not birth.original())):
            _SocketAttempt.fail(attempt,'changed_producer_task');return False
        if (type(attempt) is not _SocketAttempt or type(birth) is not _TaskBirth or birth.owner is not self
                or not _methods_original(birth,_TaskBirth,self.calls) or not birth.original()
                or not attempt.base() or attempt.failed or attempt.handler_done or attempt.registered!=1
                or attempt.unregistered or birth.loop is not activity.context[1]
                or _context()[2] is not activity.context[2] or activity.context[3]!=os.getpid()
                or birth.role not in {'dispatch','connection_close'}):return False
        frame=sys._getframe(1);tracked=source=None
        for _ in range(64):
            if frame is None:break
            if frame.f_code in _nested(type(self.owned)._observed_call.__code__) and frame.f_code.co_name=='tracked_send':
                if frame.f_locals.get('activity') is activity:tracked=frame
            expected=(self.broadcaster_kind._event_dispatch_loop if birth.role=='dispatch' else self.broadcaster_kind._safe_close)
            if frame.f_code is expected.__code__ and frame.f_locals.get('self') is self.broadcaster:source=frame
            frame=frame.f_back
        if (tracked is None or source is None or tracked.f_locals.get('self') is not self.owned
                or tracked.f_locals.get('activity') is not activity or tracked.f_locals.get('http_capture') is not None):return False
        message=tracked.f_locals.get('message')
        valid=(type(message) is dict and source.f_locals.get('ws') is attempt.socket
            and attempt.socket in self.connections
            and (tracked.f_locals.get('error') is value if action=='send_error' else message is value))
        if birth.role=='dispatch':
            valid=(valid and message.get('type')=='websocket.send' and type(message.get('text')) is str
                and message['text']==source.f_locals.get('text') and type(source.f_locals.get('msg')) is dict)
        else:
            valid=(valid and birth.record.get('connection') is attempt.socket
                and message=={'type':'websocket.close','code':1001,'reason':'Server shutting down'})
        if not valid:return False
        pending=attempt.sender_pending
        if action=='send_attempt':
            if task in pending:return False
            pending[task]=(birth,message,tuple(message.items()))
        elif action in {'send_success','send_error'}:
            held=pending.get(task)
            if held is None or held[0] is not birth or held[1] is not message or held[2]!=tuple(message.items()):return False
            del pending[task]
            if action=='send_error':attempt.fail('producer_send_failed')
            elif birth.role=='connection_close':attempt.server_close=birth
        else:return False
        return birth


class _SocketAuth(_SelectedCurrentAuth):
    pass


class _SocketAttempt:
    def __init__(self,owner,activity,headers,project,token):
        self.owner=self.adapter=owner;self.activity,self.scope=activity,activity.scope
        self.token=object();self.binding=self,owner,activity,activity.token,self.token
        self.headers,self.header_container=headers,None
        self.query=self.scope.get('query_string',b'')
        self.protocols=self.scope.get('subprotocols',[]);self.protocol_values=tuple(self.protocols)
        self.failed=self.settled=self.handler_done=False
        self.entries=self.registered=self.unregistered=self.storage=0
        self.socket=self.values=None;self.register_live=self.unregister_live=False
        self.sender_pending={};self.server_close=None
        self.events=[];self.inflight={};self.resolutions=[]
        self.auth=_SocketAuth(owner.accounts,self,headers,project,token)

    def fail(self,reason):
        self.failed=True;self.owner.admission._unsupported.add('uncovered_request_protocol')
        self.owner.observer.poison('owned_socket_'+reason)
    def __copy__(self):
        self.fail('attempt_copy');raise ValueError('Original socket attempt cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):return self.__copy__()

    def base(self):
        return (not self.settled and all(a is b for a,b in zip(self.binding,
                (self,self.owner,self.activity,self.activity.token,self.token)))
            and self.owner.pending.get(self.activity.token) is self and self.owner.original()
            and _methods_original(self,_SocketAttempt,self.owner.calls) and _methods_original(self.auth,_SocketAuth,self.owner.calls)
            and self.activity.scope is self.scope
            and self.scope.get('query_string',b'') is self.query and _headers(self.scope)==self.headers
            and self.scope.get('subprotocols',[]) is self.protocols and tuple(self.protocols)==self.protocol_values
            and (self.header_container is None or self.scope.get('headers') is self.header_container)
            and (self.socket is None or _methods_original(self.socket,self.owner.socket_kind,self.owner.calls)
                and self.socket.scope is self.scope
                and self.values is not None and self.values.get('websocket') is self.socket))

    def original(self):
        now=self.activity.context;current=_context()
        return self.base() and all(current[i] is now[i] for i in (0,1,2)) and current[3]==now[3]

    def authenticated(self):
        state=self.scope.get('state',{});account=state.get('account_user');project=state.get('scoped_project')
        decision,context=state.get('permission_decision'),state.get('project_context')
        return (self.auth.original() and self.auth.databases==['bind_workspace','authenticate','project_role','project_for','project_role','authorize']
            and self.auth.middleware_calls==1 and not self.auth.db_live and self.auth.method_name is None
            and type(account) is dict and account is self.auth.account and state.get('account_session_token')==self.auth.token
            and type(project) is dict and project.get('id')==self.auth.project_id
            and decision is not None and decision.allowed and decision.actor_id==account.get('id')
            and decision.project_id==project.get('id') and decision.action=='project.read'
            and context is not None and context.actor_id==account.get('id') and context.project_id==project.get('id')
            and context.workspace_id==self.owner.accounts.workspace and context.mode=='team')

    @contextmanager
    def effect(self,kind,identity):
        valid=(self.original() and _ACTIVE.get() is self and ((kind=='context_db' and identity is self.owner.registry)
            or (kind=='storage_scope' and identity is self.owner.storage and self.scope.get('type')=='websocket')))
        ticket=object();self.inflight[ticket]=kind,identity
        if not valid:self.fail('effect_entry')
        try:yield
        except BaseException:
            self.fail(kind+'_failed');raise
        else:
            if not valid or not self.original() or ticket not in self.inflight or len(self.events)>=8:self.fail('effect_return')
            else:self.events.append((kind,identity));del self.inflight[ticket]

    def activate(self,websocket,observed,function):
        caller=sys._getframe(1).f_back
        values=caller.f_locals.get('values') if caller is not None else None
        solved=caller.f_locals.get('solved_result') if caller is not None else None
        if values is None and solved is not None:values=getattr(solved,'values',None)
        dependant=caller.f_locals.get('dependant') if caller is not None else None
        frame=caller;session=owned=False
        for _ in range(64):
            if frame is None:break
            if frame.f_code in self.owner.session_codes:
                session|=frame.f_locals.get('scope') is self.scope and frame.f_locals.get('session') is websocket
            if frame.f_code is type(self.owner.owned)._observed_call.__code__:
                owned|=(frame.f_locals.get('self') is self.owner.owned
                    and frame.f_locals.get('activity') is self.activity and frame.f_locals.get('scope') is self.scope)
            frame=frame.f_back
        valid=(self.original() and _ACTIVE.get() is self and not self.failed and self.entries==0 and self.authenticated()
            and type(websocket) is self.owner.socket_kind and websocket.scope is self.scope
            and observed is self.owner.endpoint and function is self.owner.handler
            and caller is not None and caller.f_code in self.owner.dispatch_codes
            and caller.f_locals.get('websocket') is websocket and dependant is not None and dependant.call is observed
            and type(values) is dict and set(values)=={'websocket'} and values['websocket'] is websocket
            and self.scope.get('endpoint') is observed and session and owned
            and (self.owner.included.selected(self.scope,self.owner.route,dependant) if self.owner.included is not None
                 else self.scope.get('route') is self.owner.route and dependant is self.owner.route.dependant))
        self.entries+=1;self.socket,self.values=websocket,values
        self.header_container=self.scope.get('headers');self.auth.header_container=self.header_container
        if not valid:self.fail('handler_entry')
        return valid

    def handler_frame(self):
        frame=sys._getframe(1);endpoint=connection=False
        from backend.api import websocket_telemetry as telemetry
        for _ in range(24):
            if frame is None:break
            if frame.f_code is self.owner.handler.__code__:endpoint|=frame.f_locals.get('websocket') is self.socket
            if frame.f_code is telemetry._handle_websocket_connection.__code__:
                connection|=frame.f_locals.get('websocket') is self.socket
            frame=frame.f_back
        return endpoint and connection

    def register_entry(self,broadcaster,websocket):
        valid=(self.original() and _ACTIVE.get() is self and not self.failed and self.entries==1 and self.handler_frame()
            and broadcaster is self.owner.broadcaster and websocket is self.socket and not self.registered
            and not self.register_live and not self.unregister_live and websocket not in self.owner.connections)
        self.register_live=True
        if not valid:self.fail('register_entry')
        return valid

    def register_return(self,broadcaster,websocket,valid):
        if (not valid or not self.original() or not self.handler_frame() or broadcaster is not self.owner.broadcaster
                or websocket is not self.socket or not self.register_live or websocket not in self.owner.connections):
            self.fail('register_return')
        else:self.registered+=1
        self.register_live=False

    def unregister_entry(self,broadcaster,websocket):
        closed=self.server_close
        confirmed=(type(closed) is _TaskBirth and _methods_original(closed,_TaskBirth,self.owner.calls)
            and closed.original(completed=True) and closed.role=='connection_close'
            and closed.record.get('connection') is websocket and closed.record.get('joined') is True
            and closed.task.result() is True)
        valid=(self.original() and _ACTIVE.get() is self and not self.failed and self.handler_frame() and broadcaster is self.owner.broadcaster
            and websocket is self.socket and self.registered==1 and not self.unregistered
            and not self.register_live and not self.unregister_live and (websocket in self.owner.connections or confirmed))
        self.unregister_live=True
        if not valid:self.fail('unregister_entry')
        return valid

    def unregister_return(self,broadcaster,websocket,valid):
        if (not valid or not self.original() or not self.handler_frame() or broadcaster is not self.owner.broadcaster
                or websocket is not self.socket or not self.unregister_live or websocket in self.owner.connections):
            self.fail('unregister_return')
        else:self.unregistered+=1
        self.unregister_live=False

    def returned(self,result,valid):
        caller=sys._getframe(1)
        if (not valid or not self.original() or self.failed or caller.f_code is not self.owner.endpoint.__code__
                or caller.f_locals.get('attempt') is not self or caller.f_locals.get('websocket') is not self.socket
                or caller.f_locals.get('function') is not self.owner.handler or caller.f_locals.get('result') is not result
                or result is not None or self.entries!=1 or self.registered!=1 or self.unregistered!=1
                or self.register_live or self.unregister_live or self.socket in self.owner.connections):self.fail('handler_return')
        else:self.handler_done=True

    def complete(self,row):
        return bool(self.original() and not self.failed and self.handler_done and self.authenticated()
            and self.auth.middleware_returns==1 and self.entries==self.registered==self.unregistered==1
            and not self.register_live and not self.unregister_live and not self.sender_pending and not self.inflight and not self.resolutions
            and self.storage==1 and len(self.events)==4
            and all(kind=='context_db' and identity is self.owner.registry for kind,identity in self.events[:3])
            and self.events[3]==('storage_scope',self.owner.storage)
            and self.socket not in self.owner.connections and self.socket.client_state is self.owner.disconnected
            and row is not None and row['carrier'] is self.activity and row['released'] and row['completed']
            and row['normal_return'] and row['ws_accepted'] and row['ws_disconnect']
            and not row['transport_failed'] and not row['framing_error'] and row['send_attempt'] is None
            and (self.owner.deadline is None or time.monotonic()<self.owner.deadline))


def bind_original_owned_telemetry_socket_request(app,admission):
    if admission is not None and admission._account_requests is not None:
        admission._telemetry_socket_requests=OriginalOwnedTelemetrySocketRequest(app,admission)


@contextmanager
def original_owned_telemetry_socket_scope(admission,activity):
    from backend.engine.original_current_project_request import _CURRENT
    owner=admission._telemetry_socket_requests
    attempt=owner.pending.get(activity.token) if owner is not None and activity is not None else None
    if type(attempt) is not _SocketAttempt:
        yield;return
    token=_ACTIVE.set(attempt);current=_CURRENT.set(attempt)
    try:yield
    finally:_CURRENT.reset(current);_ACTIVE.reset(token)
