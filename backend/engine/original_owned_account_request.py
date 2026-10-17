"""One original bearer-login lifetime; no generic HTTP or release authority."""
from contextvars import ContextVar
from functools import wraps
import os
from pathlib import Path
import sqlite3
import stat
import sys
import threading
import time
from types import FunctionType, MethodType

from backend.engine.original_request_activity import _context

_ACTIVE = ContextVar('original_owned_account_request', default=None)
_LOGIN = None
_ACCOUNT_HANDLERS = {}


def _identity(path, directory=False):
    info = path.lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid or path.resolve(strict=True) != path or info.st_uid != os.getuid(): return None
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _headers(scope):
    rows = scope.get('headers')
    if type(rows) not in (list, tuple): return None
    if any(type(row) not in (list, tuple) or len(row) != 2
           or any(type(value) is not bytes for value in row) for row in rows): return None
    return tuple((key, value) for key, value in rows)


def original_owned_login_production(app, admission):
    """Non-authorizing cache provenance during a genuine owned login only."""
    attempt = _ACTIVE.get()
    if (type(attempt) is not _LoginAttempt or attempt.adapter.app is not app
            or attempt.adapter.admission is not admission or not attempt.original() or attempt.failed
            or attempt.entries != 0 or attempt.worker is not None or attempt.worker_live):
        return False
    from fastapi.routing import APIRouter
    scope = attempt.activity.scope
    frame = sys._getframe(1)
    owned_frame = router_frame = False
    for _ in range(64):
        if frame is None: break
        if frame.f_code is type(attempt.adapter.owned)._observed_call.__code__:
            owned_frame |= (frame.f_locals.get('self') is attempt.adapter.owned
                and frame.f_locals.get('activity') is attempt.activity and frame.f_locals.get('scope') is scope)
        if frame.f_code is APIRouter.app.__code__:
            router_frame |= frame.f_locals.get('self') is app.router and frame.f_locals.get('scope') is scope
        frame = frame.f_back
    return owned_frame and router_frame


def observe_original_owned_login(function):
    """Keep the original sync handler in the original framework thread pool."""
    @wraps(function)
    async def observed(body, request):
        from starlette.concurrency import run_in_threadpool
        attempt = _ACTIVE.get()
        if type(attempt) is not _LoginAttempt:
            return await run_in_threadpool(function, body, request)
        with attempt.adapter.condition:
            valid = attempt.activate(body, request, observed, function)

        def invoke():
            with attempt.adapter.condition:
                attempt.enter_worker(valid)
            try:
                result = function(body, request)
            except BaseException:
                with attempt.adapter.condition: attempt.fail('handler_failed')
                raise
            else:
                with attempt.adapter.condition:
                    attempt.worker_return(result)
                return result
            finally:
                with attempt.adapter.condition: attempt.worker_live = False

        return await run_in_threadpool(invoke)

    global _LOGIN
    _LOGIN = observed, function
    return observed


def observe_original_owned_account(function):
    """Three literal account handlers, with their original sync worker."""
    if function.__name__ not in {'me', 'users', 'select_project'}:
        raise ValueError('Unknown original account handler')
    @wraps(function)
    async def observed(**values):
        from starlette.concurrency import run_in_threadpool
        attempt = _ACTIVE.get()
        if type(attempt) is not _AccountAttempt:
            return await run_in_threadpool(function, **values)
        with attempt.adapter.condition:
            valid = attempt.activate(values, observed, function)
        def invoke():
            with attempt.adapter.condition: attempt.enter_worker(valid)
            try: result = function(**values)
            except BaseException:
                with attempt.adapter.condition: attempt.fail('handler_failed')
                raise
            else:
                with attempt.adapter.condition: attempt.worker_return(result)
                return result
            finally:
                with attempt.adapter.condition: attempt.worker_live = False
        return await run_in_threadpool(invoke)
    _ACCOUNT_HANDLERS[function.__name__] = observed, function
    return observed


class OriginalOwnedAccountRequest:
    def __init__(self, app, admission):
        from backend import main
        from backend.api import routes_accounts
        from backend.engine import shared_accounts
        from backend.engine import original_owned_UI_ancillary_request as ancillary
        from backend.engine.application_launch_handshake import BackendWorkAdmission
        from fastapi import routing
        from starlette import concurrency
        self.app, self.admission = app, admission
        self.condition, self.observer = admission._condition, admission._activity_observer
        self.store = app.state.accounts
        self.named = admission._current_project_requests
        self.binding = self,app,admission,self.store,self.named,self.observer,self.condition
        self.path = self.store.path
        self.path_identity, self.parent_identity = _identity(self.path), _identity(self.path.parent, True)
        self.pid = os.getpid()
        self.endpoint, self.handler = _LOGIN
        self.account_handlers = tuple((name, *pair) for name, pair in _ACCOUNT_HANDLERS.items())
        self.model = routes_accounts.Login
        self.workspace = app.state.context_registry.workspace_id
        self.dispatch = routing.run_endpoint_function
        self.chain = ()
        self.pending, self.records = {}, []
        self.deadline = None
        self.role_wrapper = ancillary._RESOURCES.get('global_project_role')
        self.role_origin = ancillary._RESOURCES.get('global_project_role_origin')
        self.role_body, self.role_body_code = (self.role_origin
            if type(self.role_origin) is tuple and len(self.role_origin) == 2 else (None, None))
        self.role_closure = getattr(self.role_wrapper, '__closure__', None)
        role_codes = tuple(code for code in ancillary.observe_original_UI_global_project_role.__code__.co_consts
            if type(code) is type(ancillary.observe_original_UI_global_project_role.__code__) and code.co_name == 'observed')
        self.role_code = role_codes[0] if len(role_codes) == 1 else None
        self.calls = tuple((owner, name, getattr(owner, name)) for owner, names in (
            (shared_accounts.AccountStore, ('login', 'bind_workspace', '_db', '_session', '_password', '_public',
                 'authenticate', 'project_for', 'project_role', 'authorize', '_role',
                 'metadata', 'projects_for', 'users', 'select_project')),
            (shared_accounts, ('sqlite3', 'store_admission', 'secrets', 'hmac', 'time')),
            (ancillary, ('_RESOURCES', '_ACTIVE', '_AncillaryAttempt', '_global_attempt',
                'observe_original_UI_global_project_role')),
            (sqlite3, ('connect', 'Connection')),
            (routes_accounts, ('login', 'store', 'Login', 'me', 'users', 'select_project',
                'user', 'admin', 'ProjectSelection')),
            (routing, ('run_endpoint_function', 'get_request_handler', 'request_response')),
            (concurrency, ('run_in_threadpool',)),
            (main.OwnedWorkAdmissionMiddleware, ('__call__', '_observed_call')),
            (BackendWorkAdmission, ('enter', 'leave')),
            (sys.modules[__name__], ('_ACTIVE', '_LOGIN', '_ACCOUNT_HANDLERS', '_identity', '_headers', 'FunctionType', 'OriginalOwnedAccountRequest',
                'observe_original_owned_login', 'original_owned_login_production', 'bind_original_owned_account_request',
                'observe_original_owned_account', 'owned_login_database_observation', 'original_owned_account_request_scope')),
            (OriginalOwnedAccountRequest, ('__init__', 'bind_chain', 'original', '_role_wrapper_original', 'begin', 'begin_current', 'begin_account',
                'finish', '__copy__', '__deepcopy__', '__reduce_ex__')),
            (_LoginAttempt, ('__init__', 'fail', 'base', 'original', 'activate', 'enter_worker',
                'worker_original', 'worker_return', 'begin_db', 'end_db', 'method',
                '__copy__', '__deepcopy__', '__reduce_ex__')),
            (_SelectedCurrentAuth, ('__init__', 'fail', 'original', '_authorization_frame',
                'begin_middleware', 'end_middleware', 'method', 'begin_db', 'end_db', 'activate', 'complete')),
            (_AccountAttempt, ('__init__', 'fail', '_execution', '_authorization_frame', '_handler_frame',
                'begin_middleware', 'end_middleware', 'activate', 'worker_return', 'method',
                'begin_db', 'end_db', 'complete', 'base', 'original', 'enter_worker', 'worker_original',
                '__copy__', '__deepcopy__', '__reduce_ex__')),
        ) for name in names)
        self.codes = []
        for _, _, function in self.calls:
            for _ in range(4):
                if hasattr(function, '__code__'): self.codes.append((function, function.__code__))
                function = getattr(function, '__wrapped__', None)
                if function is None: break
        def nested_codes(code):
            result = {code}
            for value in code.co_consts:
                if type(value) is type(code): result.update(nested_codes(value))
            return result
        self.handler_codes = nested_codes(routing.get_request_handler.__code__)
        self.response_codes = nested_codes(routing.request_response.__code__)
        self.eligible = (type(self.store) is shared_accounts.AccountStore and self.path_identity is not None
                         and self.parent_identity is not None and routes_accounts.login is self.endpoint
                         and self.endpoint.__wrapped__ is self.handler
                         and {row[0] for row in self.account_handlers} == {'me','users','select_project'}
                         and OriginalOwnedAccountRequest._role_wrapper_original(self))
        self.selection_model = routes_accounts.ProjectSelection

    def _role_wrapper_original(self):
        from backend.engine import shared_accounts
        from backend.engine import original_owned_UI_ancillary_request as ancillary
        wrapper, body, closure = self.role_wrapper, self.role_body, self.role_closure
        return (type(wrapper) is FunctionType and type(body) is FunctionType
            and shared_accounts.AccountStore.project_role is wrapper
            and ancillary._RESOURCES.get('global_project_role') is wrapper
            and body.__code__ is self.role_body_code
            and ancillary._RESOURCES.get('global_project_role_origin') is self.role_origin
            and type(self.role_origin) is tuple and len(self.role_origin) == 2
            and self.role_origin[0] is body and self.role_origin[1] is self.role_body_code
            and wrapper.__globals__ is ancillary.__dict__ and body.__globals__ is shared_accounts.__dict__
            and wrapper.__code__ is self.role_code and wrapper.__wrapped__ is body
            and wrapper.__code__.co_freevars == ('function',)
            and wrapper.__closure__ is closure and type(closure) is tuple and len(closure) == 1
            and closure[0].cell_contents is body)

    def bind_chain(self, middleware, downstream):
        named = self.admission._current_project_requests
        if named is None or not named.chain or named.chain[0][0] is not downstream:
            self.eligible = False
            return
        self.owned, self.downstream = middleware, downstream
        self.chain = tuple((node, kind, child, project_app, kind.__call__) for node, kind, child, project_app in named.chain)

    def original(self):
        try:
            identities = (_identity(self.path) == self.path_identity
                          and _identity(self.path.parent, True) == self.parent_identity)
        except OSError: return False
        return (self.eligible and self.chain and os.getpid() == self.pid
                and all(a is b for a,b in zip(self.binding,
                    (self,self.app,self.admission,self.store,self.named,self.observer,self.condition)))
                and self.admission._account_requests is self and self.admission._current_project_requests is self.named
                and OriginalOwnedAccountRequest._role_wrapper_original(self)
                and all(type(getattr(self.store,name)) is MethodType
                    and getattr(self.store,name).__self__ is self.store
                    and getattr(self.store,name).__func__ is value
                    for owner,name,value in self.calls if owner is type(self.store)
                    and name not in {'_password','_public','_role'})
                and all(getattr(self.store,name) is value for owner,name,value in self.calls
                    if owner is type(self.store) and name in {'_password','_public','_role'})
                and self.app.state.accounts is self.store and self.store.path is self.path
                and self.admission._condition is self.condition and self.admission._activity_observer is self.observer
                and self.owned.app is self.downstream and self.owned.admission is self.admission
                and all(type(node) is kind and node.app is child and kind.__call__ is function
                        and getattr(node, 'project_app', None) is project_app
                        for node, kind, child, project_app, function in self.chain)
                and all(getattr(owner, name) is value for owner, name, value in self.calls)
                and all(function.__code__ is code for function, code in self.codes)
                and len(_ACCOUNT_HANDLERS) == len(self.account_handlers)
                and all(_ACCOUNT_HANDLERS.get(name) == (endpoint, handler)
                    and endpoint.__wrapped__ is handler for name, endpoint, handler in self.account_handlers)
                and self.endpoint.__wrapped__ is self.handler and identities)

    def begin(self, activity):
        if activity is not None and activity.ingress == {'type':'http','method':'GET','path':'/api/project/current'}:
            return self.begin_current(activity)
        if activity is not None and activity.ingress.get('path') in {
                '/api/accounts/me','/api/accounts/users','/api/accounts/select-project'}:
            return self.begin_account(activity)
        if (not self.original() or activity is None or not self.observer._valid(activity)
                or activity.ingress != {'type': 'http', 'method': 'POST', 'path': '/api/accounts/login'}
                or activity.scope.get('app') is not self.app or activity.scope.get('query_string', b'') != b''
                or len(self.records) >= 128): return False
        headers = _headers(activity.scope)
        if headers is None or any(key.lower() in {b'authorization', b'cookie', b'x-vision-context', b'x-vision-project'}
                                  for key, _ in headers): return False
        attempt = _LoginAttempt(self, activity, headers)
        self.pending[activity.token] = attempt
        self.records.append(attempt)
        self.admission._active += 1
        return True

    def __copy__(self):
        self.admission._unsupported.add('uncovered_request_protocol')
        self.observer.poison('owned_account_owner_copy')
        raise ValueError('Original account request owner cannot be copied')

    def __deepcopy__(self, memo): return self.__copy__()
    def __reduce_ex__(self, protocol): return self.__copy__()

    def begin_account(self, activity):
        routes = {('GET','/api/accounts/me'):'me', ('GET','/api/accounts/users'):'users',
                  ('POST','/api/accounts/select-project'):'select_project'}
        name = routes.get((activity.ingress.get('method'),activity.ingress.get('path')))
        headers = _headers(activity.scope)
        if (name is None or not self.original() or not self.observer._valid(activity)
                or activity.ingress.get('type') != 'http' or activity.scope.get('app') is not self.app
                or activity.scope.get('query_string',b'') != b'' or headers is None
                or len(self.records) >= 128): return False
        bearer = [v for k,v in headers if k.lower()==b'authorization']
        if (len(bearer)!=1 or not bearer[0].startswith(b'Bearer ') or not 7<len(bearer[0])<=263
                or any(k.lower() in {b'cookie',b'x-vision-context',b'x-vision-project'} for k,_ in headers)):
            return False
        try: token = bearer[0][7:].decode('ascii')
        except UnicodeError: return False
        attempt = _AccountAttempt(self,activity,headers,name,token)
        self.pending[activity.token] = attempt
        self.records.append(attempt)
        self.admission._active += 1
        return True

    def begin_current(self, activity):
        from backend.engine.original_current_project_request import _Attempt
        headers = _headers(activity.scope)
        if (not self.original() or self.named is None or not self.named.original()
                or not self.observer._valid(activity) or activity.scope.get('app') is not self.app
                or activity.scope.get('query_string', b'') != b'' or headers is None
                or len(self.named.records) >= 128): return False
        selected = [value for key,value in headers if key.lower() == b'x-vision-project']
        bearer = [value for key,value in headers if key.lower() == b'authorization']
        contexts = [value for key,value in headers if key.lower() == b'x-vision-context']
        if (len(selected) != 1 or len(bearer) != 1 or len(contexts)>1 or not bearer[0].startswith(b'Bearer ')
                or not 7 < len(bearer[0]) <= 263 or not selected[0]
                or any(key.lower() == b'cookie' for key,_ in headers)
                or any(not value or len(value)>4096 for value in contexts)):
            return False
        try:
            project_id, token = selected[0].decode('ascii'), bearer[0][7:].decode('ascii')
        except UnicodeError: return False
        attempt = _Attempt(self.named, activity,
            auth_factory=lambda attempt: _SelectedCurrentAuth(self,attempt,headers,project_id,token))
        self.named.pending[attempt.token] = attempt
        self.named.records.append(attempt)
        self.admission._active += 1
        return True

    def finish(self, activity):
        attempt = self.pending.get(activity.token) if activity is not None else None
        if type(attempt) not in (_LoginAttempt,_AccountAttempt): return
        row = self.observer._records.get(activity.token)
        if type(attempt) is _AccountAttempt:
            if not attempt.complete(row):
                attempt.fail('unconfirmed_lifetime')
                return
            del self.pending[activity.token]
            self.admission._active -= 1
            attempt.settled,attempt.live = True,False
            return
        if (not attempt.original() or attempt.failed or not attempt.handler_done or attempt.worker_live
                or attempt.database_exits != 2 or not attempt.workspace_done or attempt.db_live
                or row is None or row['carrier'] is not activity
                or not row['released'] or not row['completed'] or not row['normal_return']
                or row['response_status'] != 200 or row['transport_failed'] or row['framing_error']
                or row['send_attempt'] is not None or not row['start_forwarded'] or not row['terminal_forwarded']
                or row['trailers_expected'] or (self.deadline is not None and time.monotonic() >= self.deadline)):
            attempt.fail('unconfirmed_lifetime')
            return
        del self.pending[activity.token]
        self.admission._active -= 1
        attempt.settled = True
        attempt.live = False


class _LoginAttempt:
    def __init__(self, adapter, activity, headers):
        self.adapter, self.activity, self.headers = adapter, activity, headers
        self.scope = activity.scope
        self.query, self.path = self.scope.get('query_string', b''), self.scope.get('path')
        self.header_container = None
        self.failed = self.settled = self.handler_done = self.worker_live = self.db_live = False
        self.live = True
        self.worker = None
        self.database_exits = self.entries = 0
        self.workspace_done = False
        self.method_name = None

    def fail(self, reason):
        self.failed = True
        self.adapter.admission._unsupported.add('uncovered_request_protocol')
        self.adapter.observer.poison('owned_login_' + reason)

    def __copy__(self):
        self.fail('copied_attempt')
        raise ValueError('Original login lifetime cannot be copied')

    def __deepcopy__(self, memo): return self.__copy__()
    def __reduce_ex__(self, protocol): return self.__copy__()

    def base(self):
        return (self.live and not self.settled and self.adapter.original()
                and self.adapter.pending.get(self.activity.token) is self
                and self.scope is self.activity.scope and self.scope.get('app') is self.adapter.app
                and self.scope.get('path') is self.path and self.scope.get('query_string', b'') is self.query
                and _headers(self.scope) == self.headers)

    def original(self):
        return (self.base() and self.adapter.observer._valid(self.activity)
                and (self.header_container is None or self.scope.get('headers') is self.header_container))

    def activate(self, body, request, observed, function):
        invocation = sys._getframe(1)
        caller = invocation.f_back
        frame = caller
        handler_frame = response_frame = owned_frame = False
        for _ in range(64):
            if frame is None: break
            if frame.f_code in self.adapter.handler_codes:
                handler_frame |= frame.f_locals.get('request') is request
            if frame.f_code in self.adapter.response_codes:
                response_frame |= frame.f_locals.get('scope') is self.scope
            if frame.f_code is type(self.adapter.owned)._observed_call.__code__:
                owned_frame |= (frame.f_locals.get('self') is self.adapter.owned
                                and frame.f_locals.get('activity') is self.activity
                                and frame.f_locals.get('scope') is self.scope)
            frame = frame.f_back
        valid = (self.original() and _ACTIVE.get() is self and self.entries == 0
                 and request.scope is self.scope and type(body) is self.adapter.model
                 and body.transport == 'bearer' and observed is self.adapter.endpoint and function is self.adapter.handler
                 and invocation.f_code is observed.__code__ and caller is not None
                 and caller.f_code is self.adapter.dispatch.__code__
                 and caller.f_locals.get('dependant') is not None
                 and caller.f_locals['dependant'].call is observed
                 and caller.f_locals.get('is_coroutine') is True
                 and caller.f_locals.get('values', {}).get('body') is body
                 and caller.f_locals.get('values', {}).get('request') is request
                 and self.scope.get('endpoint') is observed
                 and handler_frame and response_frame and owned_frame)
        self.entries += 1
        if not valid: self.fail('original_handler_entry')
        # Original middleware may naturally rebind Headers before the handler.
        # The raw bytes were held from ingress; identity stays fixed from here.
        self.header_container = self.scope.get('headers')
        return valid

    def enter_worker(self, valid):
        if not valid or not self.base() or self.worker is not None or _ACTIVE.get() is not self:
            self.fail('original_worker_entry')
        self.worker = os.getpid(), threading.current_thread()
        self.worker_live = True

    def worker_original(self):
        return (self.base() and not self.failed and self.worker_live and _ACTIVE.get() is self
                and self.worker == (os.getpid(), threading.current_thread())
                and self.scope.get('headers') is self.header_container)

    def worker_return(self, result):
        if (not self.worker_original() or self.db_live or self.database_exits != 2 or not self.workspace_done
                or type(result) is not dict or type(result.get('token')) is not str):
            self.fail('original_handler_return')
        else: self.handler_done = True

    def begin_db(self, store):
        frame = sys._getframe(1)
        for _ in range(6):
            if frame is None or frame.f_code is type(store).login.__code__: break
            frame = frame.f_back
        workspace = self.method_name == 'bind_workspace' and self.database_exits == 0 and not self.workspace_done
        login = (self.method_name is None and self.workspace_done and self.database_exits == 1
                 and frame is not None and frame.f_code is type(store).login.__code__
                 and frame.f_locals.get('self') is store)
        valid = self.worker_original() and store is self.adapter.store and not self.db_live and (workspace or login)
        if not valid: self.fail('original_database_entry')
        self.db_live = True
        return valid

    def end_db(self, connection, valid, body_error, exit_error):
        closed = False
        if type(connection) is sqlite3.Connection:
            try: connection.in_transaction
            except sqlite3.ProgrammingError: closed = True
        if not (valid and self.worker_original() and self.db_live and closed
                and body_error is None and exit_error is None): self.fail('original_database_exit')
        else: self.database_exits += 1
        self.db_live = False

    def method(self, name, store, value, function, args, kwargs):
        with self.adapter.condition:
            valid = (self.worker_original() and name == 'bind_workspace' and store is self.adapter.store
                     and value is self.adapter.workspace and self.method_name is None
                     and not self.workspace_done and self.database_exits == 0
                     and function is type(store).bind_workspace.__wrapped__)
            if not valid: self.fail('workspace_method_entry')
            self.method_name = name
        normal = False
        try:
            result = function(store, *args, **kwargs)
            normal = True
            return result
        finally:
            with self.adapter.condition:
                if valid and normal and self.worker_original() and not self.db_live and self.database_exits == 1:
                    self.workspace_done = True
                else: self.fail('workspace_method_exit')
                self.method_name = None


class _AccountAttempt(_LoginAttempt):
    """Original me/users/selection, including auth and handler DB closes."""
    def __init__(self, adapter, activity, headers, name, token):
        super().__init__(adapter,activity,headers)
        self.name,self.token = name,token
        _,self.endpoint,self.handler = next(row for row in adapter.account_handlers if row[0]==name)
        self.middleware = adapter.named.shared.middleware
        self.authorization = type(self.middleware).__call__.__wrapped__
        self.middleware_calls = self.middleware_returns = 0
        self.account,self.request,self.body,self.result = None,None,None,None
        self.databases = []
        self.project_rows = None
        self.db_frame = None

    def fail(self,reason):
        self.failed = True
        self.adapter.admission._unsupported.add('uncovered_request_protocol')
        self.adapter.observer.poison('owned_account_'+reason)

    def _execution(self):
        return self.worker_original() if self.worker_live else self.original()

    def _authorization_frame(self):
        frame = sys._getframe(1)
        for _ in range(16):
            if frame is None: return None
            if frame.f_code is self.authorization.__code__:
                return frame if (frame.f_locals.get('self') is self.middleware
                    and frame.f_locals.get('scope') is self.scope
                    and frame.f_locals.get('store') is self.adapter.store) else None
            frame = frame.f_back
        return None

    def _handler_frame(self):
        frame = sys._getframe(1)
        for _ in range(20):
            if frame is None: return None
            if frame.f_code is self.handler.__code__:
                return frame if frame.f_locals.get('request') is self.request else None
            frame = frame.f_back
        return None

    def begin_middleware(self,middleware,scope):
        valid = (self.original() and _ACTIVE.get() is self and middleware is self.middleware
            and scope is self.scope and self.middleware_calls==0 and self.entries==0)
        self.middleware_calls += 1
        if not valid: self.fail('middleware_entry')
        return valid

    def end_middleware(self,valid):
        if (valid and self.original() and not self.failed and not self.db_live
                and self.method_name is None and self.handler_done and not self.worker_live):
            self.middleware_returns += 1
        else: self.fail('middleware_return')

    def activate(self,values,observed,function):
        invocation = sys._getframe(1)
        caller = invocation.f_back
        request = values.get('request')
        frame = caller
        handler_frame = response_frame = owned_frame = False
        for _ in range(64):
            if frame is None: break
            if frame.f_code in self.adapter.handler_codes:
                handler_frame |= frame.f_locals.get('request') is request
            if frame.f_code in self.adapter.response_codes:
                response_frame |= frame.f_locals.get('scope') is self.scope
            if frame.f_code is type(self.adapter.owned)._observed_call.__code__:
                owned_frame |= (frame.f_locals.get('self') is self.adapter.owned
                    and frame.f_locals.get('activity') is self.activity and frame.f_locals.get('scope') is self.scope)
            frame = frame.f_back
        body = values.get('body')
        valid = (self.original() and not self.failed and _ACTIVE.get() is self and self.entries==0
            and request is not None and request.scope is self.scope and observed is self.endpoint
            and function is self.handler and invocation.f_code is observed.__code__
            and caller is not None and caller.f_code is self.adapter.dispatch.__code__
            and caller.f_locals.get('dependant') is not None and caller.f_locals['dependant'].call is observed
            and caller.f_locals.get('is_coroutine') is True
            and set(caller.f_locals.get('values',{}))==set(values)
            and all(caller.f_locals['values'][key] is value for key,value in values.items())
            and set(values)==({'request','body'} if self.name=='select_project' else {'request'})
            and (type(body) is self.adapter.selection_model if self.name=='select_project' else body is None)
            and self.scope.get('endpoint') is observed and handler_frame and response_frame and owned_frame
            and self.middleware_calls==1 and self.middleware_returns==0
            and self.databases==[('authorization','bind_workspace'),('authorization','authenticate')]
            and type(self.account) is dict and self.scope.get('state',{}).get('account_user') is self.account
            and self.scope['state'].get('account_session_token')==self.token)
        self.entries += 1
        self.request,self.body = request,body
        self.header_container = self.scope.get('headers')
        if not valid: self.fail('original_handler_entry')
        return valid

    def method(self,name,store,value,function,args,kwargs):
        phase = 'handler' if self.worker_live else 'authorization'
        expected = ('bind_workspace' if not self.databases else
                    'authenticate' if len(self.databases)==1 else
                    'bind_workspace' if len(self.databases)==2 and self.worker_live else None)
        valid = (self._execution() and not self.failed and store is self.adapter.store
            and name==expected and self.method_name is None
            and (value is self.adapter.workspace if name=='bind_workspace' else value==self.token)
            and function is getattr(type(store),name).__wrapped__
            and (self._handler_frame() is not None if self.worker_live
                 else self._authorization_frame() is not None))
        if not valid: self.fail('method_entry')
        self.method_name = name
        before = len(self.databases)
        try: result = function(store,*args,**kwargs)
        except BaseException:
            self.fail('method_failed')
            raise
        else:
            if (not valid or not self._execution() or self.db_live
                    or self.databases[before:]!=[(phase,name)]): self.fail('method_return')
            if name=='authenticate': self.account = result
            return result
        finally: self.method_name = None

    def begin_db(self,store):
        names = ('bind_workspace','authenticate','project_for','project_role','metadata','projects_for','users','select_project')
        functions = {name:getattr(getattr(type(store),name),'__wrapped__',getattr(type(store),name)) for name in names}
        frame = sys._getframe(1)
        selected = None
        for _ in range(12):
            if frame is None: break
            selected = next((name for name,function in functions.items() if frame.f_code is function.__code__),None)
            if selected is not None: break
            frame = frame.f_back
        phase = 'handler' if self.worker_live else 'authorization'
        valid = (self._execution() and not self.failed and store is self.adapter.store and not self.db_live
            and self.middleware_calls==1 and not self.middleware_returns
            and (self._handler_frame() is not None if self.worker_live
                 else self._authorization_frame() is not None)
            and frame is not None and selected is not None and frame.f_locals.get('self') is store)
        if phase=='authorization':
            expected = ('bind_workspace','authenticate')
            valid = (valid and len(self.databases)<2 and selected==expected[len(self.databases)]
                and self.method_name==selected)
        else:
            parent = frame.f_back if frame is not None else None
            handler_frame = None
            for _ in range(16):
                if parent is None: break
                if parent.f_code is self.handler.__code__:
                    handler_frame = parent; break
                parent = parent.f_back
            valid = (valid and handler_frame is not None and handler_frame.f_locals.get('request') is self.request
                and self.entries==1 and len(self.databases)>=2)
            allowed = ({'bind_workspace','project_for','project_role','metadata','projects_for'} if self.name=='me'
                else {'bind_workspace','users'} if self.name=='users' else {'bind_workspace','project_role','select_project'})
            valid = valid and selected in allowed
            if selected=='bind_workspace':
                valid = valid and len(self.databases)==2 and self.method_name==selected
            elif selected in {'project_for','projects_for','project_role','select_project'}:
                valid = valid and frame.f_locals.get('user_id')==self.account.get('id')
                if self.name=='select_project':
                    valid = valid and frame.f_locals.get('project_id')==self.body.project_id
                elif selected=='project_role':
                    parent = frame.f_back
                    # Exactly the registered pass-through role wrapper may sit
                    # between its original body and the original caller below.
                    if parent is not None and parent.f_code is self.adapter.role_code:
                        valid = (valid and OriginalOwnedAccountRequest._role_wrapper_original(self.adapter)
                            and frame.f_code is self.adapter.role_body.__code__
                            and parent.f_globals is self.adapter.role_wrapper.__globals__
                            and parent.f_locals.get('function') is self.adapter.role_body
                            and parent.f_locals.get('attempt') is None
                            and parent.f_locals.get('store') is store
                            and parent.f_locals.get('user_id') == frame.f_locals.get('user_id')
                            and parent.f_locals.get('project_id') == frame.f_locals.get('project_id'))
                        parent = parent.f_back
                    if parent is not None and parent.f_code is functions['project_for'].__code__:
                        valid = valid and frame.f_locals.get('project_id')==parent.f_locals.get('project_id')
                    elif parent is not None and parent.f_code is functions['projects_for'].__code__:
                        row = parent.f_locals.get('r')
                        valid = valid and type(row) is dict and frame.f_locals.get('project_id')==row.get('id')
                    # Comprehensions in older Python have their own original
                    # nested code; require that precise parent before its owner.
                    elif (parent is not None and parent.f_code in functions['projects_for'].__code__.co_consts
                            and parent.f_back is not None and parent.f_back.f_code is functions['projects_for'].__code__):
                        row = parent.f_locals.get('r')
                        valid = valid and type(row) is dict and frame.f_locals.get('project_id')==row.get('id')
                    else: valid = False
        if not valid: self.fail('database_entry')
        self.db_live = True
        self.db_frame = frame
        return valid,phase,selected

    def end_db(self,connection,entry,body_error,exit_error):
        valid,phase,name = entry
        closed = False
        if type(connection) is sqlite3.Connection:
            try: connection.in_transaction
            except sqlite3.ProgrammingError: closed=True
        if (not valid or not self._execution() or not self.db_live or not closed
                or body_error is not None or exit_error is not None): self.fail('database_exit')
        else:
            self.databases.append((phase,name))
            if phase=='handler' and name=='projects_for':
                rows = self.db_frame.f_locals.get('rows')
                if type(rows) is list and len(rows)<=128 and all(type(row) is dict for row in rows):
                    self.project_rows = tuple(row['id'] for row in rows)
                else: self.fail('project_rows')
        self.db_live,self.db_frame = False,None

    def worker_return(self,result):
        trace = [name for phase,name in self.databases if phase=='handler']
        valid = (self.worker_original() and not self.failed and not self.db_live and type(result) is dict)
        if self.name=='users':
            valid = (valid and trace==['bind_workspace','users'] and self.account.get('administrator')
                and type(result.get('users')) is list and all(type(row) is dict for row in result['users']))
        elif self.name=='select_project':
            valid = (valid and trace==['bind_workspace','project_role','select_project']
                and result=={'project_id':self.body.project_id})
        else:
            projects = result.get('projects')
            selected = result.get('selected_project_id')
            prefix = ['bind_workspace','project_for']
            if selected is not None: prefix += ['project_role','project_for']
            prefix += ['metadata','projects_for']
            valid = (valid and result.get('user') is self.account and result.get('workspace_id')==self.adapter.workspace
                and type(projects) is list and self.project_rows is not None and len(projects)<=len(self.project_rows)
                and all(type(row) is dict and row.get('id') in self.project_rows and row.get('role') for row in projects)
                and trace==prefix+['project_role']*(len(self.project_rows)+len(projects)))
        if not valid: self.fail('original_handler_return')
        else: self.handler_done,self.result = True,result

    def complete(self,row):
        return bool(self.original() and not self.failed and self.handler_done and not self.worker_live
            and not self.db_live and self.method_name is None and self.entries==1
            and self.middleware_calls==self.middleware_returns==1
            and row is not None and row['carrier'] is self.activity and row['released'] and row['completed']
            and row['normal_return'] and row['response_status']==200 and not row['transport_failed']
            and not row['framing_error'] and row['send_attempt'] is None and row['start_forwarded']
            and row['terminal_forwarded'] and not row['trailers_expected']
            and (self.adapter.deadline is None or time.monotonic()<self.adapter.deadline))


class _SelectedCurrentAuth:
    """The original selected bearer current-GET, including every DB close."""
    selected_current = True

    def __init__(self, owner, attempt, headers, project_id, token):
        from backend import main
        from backend.api import routes_project, shared_authorization
        from backend.engine import project_labelsets
        self.owner,self.attempt,self.headers = owner,attempt,headers
        self.project_id,self.token = project_id,token
        self.binding = self,owner,attempt,attempt.activity,attempt.token
        self.middleware = owner.named.shared.middleware
        self.calls = tuple((module,name,getattr(module,name)) for module,names in (
            (routes_project, ('get_current_project','_load_project','_load_project_unfenced',
                'normalize_legacy_manifest','legacy_preview_migration','load_labelsets','_write_json')),
            (main, ('get_current_project',)),
            (project_labelsets, ('load_labelsets','_atomic_json')),
            (shared_authorization.SharedAuthorizationMiddleware, ('__call__',)),
        ) for name in names)
        self.codes = []
        for _,_,function in self.calls:
            for _ in range(4):
                if hasattr(function,'__code__'): self.codes.append((function,function.__code__))
                function = getattr(function,'__wrapped__',None)
                if function is None: break
        self.authorization = shared_authorization.SharedAuthorizationMiddleware.__call__.__wrapped__
        self.method_name,self.db_live = None,False
        self.databases = []
        self.middleware_calls = self.middleware_returns = self.entries = 0
        self.account = None
        self.header_container = None

    def fail(self,reason): self.attempt.fail('selected_account_'+reason)

    def original(self):
        return (all(a is b for a,b in zip(self.binding,
                    (self,self.owner,self.attempt,self.attempt.activity,self.attempt.token)))
                and self.attempt.auth is self and self.owner.original() and self.attempt.original()
                and _headers(self.attempt.activity.scope) == self.headers
                and all(getattr(module,name) is function for module,name,function in self.calls)
                and all(function.__code__ is code for function,code in self.codes)
                and (self.header_container is None or self.attempt.activity.scope.get('headers') is self.header_container))

    def _authorization_frame(self):
        frame = sys._getframe(1)
        for _ in range(12):
            if frame is None: return None
            if frame.f_code is self.authorization.__code__:
                return frame if (frame.f_locals.get('self') is self.middleware
                    and frame.f_locals.get('scope') is self.attempt.activity.scope
                    and frame.f_locals.get('store') is self.owner.store) else None
            frame = frame.f_back
        return None

    def begin_middleware(self,middleware,scope):
        valid = (self.original() and middleware is self.middleware
            and scope is self.attempt.activity.scope and self.middleware_calls == 0)
        self.middleware_calls += 1
        if not valid: self.fail('middleware_entry')
        return valid

    def end_middleware(self,valid):
        if valid and self.original() and not self.db_live and self.method_name is None:
            self.middleware_returns += 1
        else: self.fail('middleware_return')

    def method(self,name,store,value,function,args,kwargs):
        expected = 'bind_workspace' if not self.databases else 'authenticate' if len(self.databases)==1 else None
        valid = (self.original() and self._authorization_frame() is not None
            and store is self.owner.store and name == expected and self.method_name is None
            and (value is self.owner.workspace if name=='bind_workspace' else type(value) is str and value==self.token)
            and function is getattr(type(store),name).__wrapped__)
        if not valid: self.fail('method_entry')
        self.method_name = name
        before = len(self.databases)
        try:
            result = function(store,*args,**kwargs)
        except BaseException:
            self.fail('method_failed')
            raise
        else:
            if (not valid or not self.original() or self.db_live or len(self.databases)!=before+1):
                self.fail('method_return')
            if name == 'authenticate': self.account = result
            return result
        finally: self.method_name = None

    def begin_db(self,store):
        frame = sys._getframe(1)
        names = ('bind_workspace','authenticate','project_role','project_for','project_role','authorize')
        expected = names[len(self.databases)] if len(self.databases)<len(names) else None
        function = getattr(type(store),expected,None)
        function = getattr(function,'__wrapped__',function)
        for _ in range(8):
            if frame is None or (function is not None and frame.f_code is function.__code__): break
            frame = frame.f_back
        valid = (self.original() and store is self.owner.store and not self.db_live
            and self.middleware_calls==1 and not self.middleware_returns
            and self._authorization_frame() is not None and frame is not None
            and function is not None and frame.f_code is function.__code__ and frame.f_locals.get('self') is store)
        if expected in {'project_role','project_for'}:
            valid = (valid and type(self.account) is dict
                and frame.f_locals.get('user_id')==self.account.get('id')
                and frame.f_locals.get('project_id')==self.project_id)
        elif expected=='authorize':
            valid = (valid and type(self.account) is dict and frame.f_locals.get('actor')==self.account.get('id')
                and frame.f_locals.get('project')==self.project_id and frame.f_locals.get('session_token')==self.token
                and frame.f_locals.get('action')=='project.read')
        if not valid: self.fail('database_entry')
        self.db_live = True
        return valid,expected

    def end_db(self,connection,entry,body_error,exit_error):
        valid,expected = entry
        closed = False
        if type(connection) is sqlite3.Connection:
            try: connection.in_transaction
            except sqlite3.ProgrammingError: closed=True
        if not (valid and self.original() and self.db_live and closed and body_error is None and exit_error is None):
            self.fail('database_exit')
        else: self.databases.append(expected)
        self.db_live = False

    def activate(self,request,matched):
        caller = sys._getframe(1).f_back
        frame = caller
        handler_frame = response_frame = owned_frame = False
        for _ in range(64):
            if frame is None: break
            if frame.f_code in self.owner.handler_codes:
                handler_frame |= frame.f_locals.get('request') is request
            if frame.f_code in self.owner.response_codes:
                response_frame |= frame.f_locals.get('scope') is self.attempt.activity.scope
            if frame.f_code is type(self.owner.owned)._observed_call.__code__:
                owned_frame |= (frame.f_locals.get('self') is self.owner.owned
                    and frame.f_locals.get('activity') is self.attempt.activity)
            frame = frame.f_back
        valid = (self.original() and self.entries==0 and request.scope is self.attempt.activity.scope
            and caller is not None and caller.f_code is self.owner.dispatch.__code__
            and caller.f_locals.get('dependant') is not None and caller.f_locals['dependant'].call is matched
            and caller.f_locals.get('values',{}).get('request') is request
            and caller.f_locals.get('is_coroutine') is True and handler_frame and response_frame and owned_frame)
        self.entries += 1
        self.header_container = self.attempt.activity.scope.get('headers')
        if not valid: self.fail('original_handler_entry')
        return valid

    def complete(self,row):
        state = self.attempt.activity.scope.get('state',{})
        project,decision,context = state.get('scoped_project'),state.get('permission_decision'),state.get('project_context')
        events = self.attempt.events
        databases = [identity for kind,identity in events if kind=='context_db']
        files = [(kind,path) for kind,path in events if kind in {'project_json','labelset_json'}]
        valid = (self.original() and not self.attempt.failed and not self.db_live and self.method_name is None
            and self.databases==['bind_workspace','authenticate','project_role','project_for','project_role','authorize']
            and self.middleware_calls==self.middleware_returns==1 and self.entries==1
            and type(self.account) is dict and state.get('account_user') is self.account
            and state.get('account_session_token')==self.token and type(project) is dict
            and project.get('id')==self.project_id and self.attempt.result is project
            and decision is not None and context is not None
            and getattr(decision,'allowed',None) is True and decision.actor_id==self.account.get('id')
            and decision.project_id==self.project_id and decision.workspace_id==self.owner.workspace
            and context.actor_id==self.account.get('id') and context.project_id==self.project_id
            and context.workspace_id==self.owner.workspace and context.mode=='team'
            and self.attempt.handler==1 and self.attempt.worker_done and self.attempt.storage==1
            and len(self.attempt.resolutions)==2 and all(result is project for result in self.attempt.resolutions)
            and len(databases)==3 and all(registry is self.owner.named.registry for registry in databases)
            and all((kind,path) in {('project_json',Path(project['project_dir'])/'project.json'),
                ('labelset_json',Path(project['project_dir'])/'labelsets.json')} for kind,path in files)
            and not self.attempt.inflight and row['normal_return'] and row['completed'] and row['response_status']==200
            and not row['transport_failed'] and not row['framing_error'] and row['send_attempt'] is None
            and row['start_forwarded'] and row['terminal_forwarded'] and not row['trailers_expected'])
        return bool(valid)


def bind_original_owned_account_request(app, admission):
    if admission is not None and admission._activity_observer is not None and app.state.accounts is not None:
        admission._account_requests = OriginalOwnedAccountRequest(app, admission)


def owned_login_database_observation():
    attempt = _ACTIVE.get()
    return attempt if (type(attempt) is _AccountAttempt
        or type(attempt) is _LoginAttempt and attempt.worker_live) else None


from contextlib import contextmanager


@contextmanager
def original_owned_account_request_scope(admission, activity):
    adapter = admission._account_requests
    attempt = adapter.pending.get(activity.token) if adapter is not None and activity is not None else None
    if type(attempt) not in (_LoginAttempt,_AccountAttempt):
        yield
        return
    token = _ACTIVE.set(attempt)
    try: yield
    finally: _ACTIVE.reset(token)
