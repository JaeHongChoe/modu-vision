"""Exact original empty-session shared-auth denial and its SQLite/ASGI exits.

Pending is provisional, never a GET/status allowlist. Only the live original
authentication slice can activate the captured account observer. Any other
result retains the pending admission and original sticky unsupported flag.
"""
from contextlib import contextmanager, _GeneratorContextManager
from contextvars import ContextVar
import dis
import os
import sqlite3
import sys
import time
from types import MethodType

from backend.engine.original_shared_current_project import _SharedAttempt

_ACTIVE = ContextVar('original_shared_auth_denial', default=None)
_UNSELECTED_PATHS = frozenset({'/api/compute/reservations', '/api/compute/targets',
    '/api/compute/jobs', '/api/compute/inputs', '/api/dataset/revisions'})


class SharedDenialObservationError(ValueError): pass


def _headers(scope):
    value = scope.get('headers')
    if (type(value) is not list or any(type(row) is not tuple or len(row) != 2
            or type(row[0]) is not bytes or type(row[1]) is not bytes for row in value)):
        return None
    return tuple(value)


def _hook_offset(code):
    instructions = tuple(dis.get_instructions(code))
    offsets = []
    for position, item in enumerate(instructions):
        if item.opname == 'LOAD_GLOBAL' and item.argval == 'original_shared_denial_authentication':
            following = instructions[position+1:position+65]
            match = next((row.offset for row in following if row.opname == 'BEFORE_WITH'), None)
            if match is not None: offsets.append(match)
    return offsets[0] if len(offsets) == 1 else None


class OriginalSharedDenialAdapter:
    def __init__(self, app, admission):
        from backend import main
        from backend.api import shared_authorization
        from backend.engine import application_launch_handshake, original_shared_current_project, original_current_project_request
        self.app, self.state, self.admission = app, app.state, admission
        self.named, self.observer, self.condition = admission._current_project_requests, admission._activity_observer, admission._condition
        self.owner = original_shared_current_project.OriginalSharedAccountObservation(app, admission)
        self.project_for = self.owner.kind.project_for
        self.project_code = self.project_for.__code__
        self.auth_type = shared_authorization.SharedAuthorizationMiddleware
        self.wrapper = self.auth_type.__call__
        self.body = self.wrapper.__wrapped__
        self.body_code, self.body_globals = self.body.__code__, self.body.__globals__
        self.offset = _hook_offset(self.body_code)
        self.reject_code = tuple(c for c in self.body_code.co_consts if hasattr(c, 'co_name') and c.co_name == 'reject')
        self.hook = original_shared_denial_authentication.__wrapped__
        self.enter = _GeneratorContextManager.__enter__
        self.admission_enter = application_launch_handshake.BackendWorkAdmission.enter
        self.owned_call = main.OwnedWorkAdmissionMiddleware._observed_call
        self.context_owner = original_current_project_request._owner
        self.pid, self.context = os.getpid(), _ACTIVE
        self.pending, self.records, self.deadline = {}, [], None
        self.owned = self.downstream = self.auth = None
        self.eligible = self.named is not None and self.owner.original() and self.offset is not None and len(self.reject_code) == 1
        self.calls = tuple((owner, name, getattr(owner, name)) for owner, names in (
            (self.auth_type, ('__call__',)),
            (shared_authorization, ('Headers', 'SimpleCookie', 'JSONResponse')),
            (shared_authorization.Headers, ('__init__', 'get')),
            (shared_authorization.SimpleCookie, ('__init__', 'load', '__contains__', '__getitem__')),
            (shared_authorization.JSONResponse, ('__init__', '__call__', 'render', 'init_headers')),
            (original_current_project_request, ('_owner',)),
            (application_launch_handshake.BackendWorkAdmission, ('enter', 'leave', 'drain')),
            (main.OwnedWorkAdmissionMiddleware, ('_observed_call',)),
            (_GeneratorContextManager, ('__enter__', '__exit__')),
            (self.owner.kind, ('project_for',)),
            (sqlite3, ('Connection', 'ProgrammingError')),
            (dis, ('get_instructions',)),
            (original_shared_current_project, ('_auth',)),
            (sys.modules[__name__], ('_ACTIVE', '_headers', '_hook_offset', 'shared_denial_auth',
                'shared_denial_scope', 'original_shared_denial_authentication', '_UNSELECTED_PATHS',
                '_AuthenticatedUnselectedAttempt', '_AuthenticatedUnselectedAuth', 'sqlite3', 'MethodType', 'dis')),
            (OriginalSharedDenialAdapter, ('__init__', 'original', 'begin', 'bind_chain', 'find', 'finish')),
            (_SharedDenialAttempt, ('__init__', 'original', 'base', 'fail', 'entry', 'activate', 'returned')),
            (_AuthenticatedUnselectedAttempt, ('__init__', 'base')),
            (_AuthenticatedUnselectedAuth, ('__init__', 'original', '_authorization_frame',
                'method', 'begin_db', 'end_db', 'complete'))) for name in names)
        self.codes = tuple((function, function.__code__) for _, _, function in self.calls if hasattr(function, '__code__'))

    def original(self):
        return (self.eligible and self.pid == os.getpid() and self.context is _ACTIVE
            and self.app.state is self.state and self.state.accounts is self.owner.store
            and self.state.owned_work_admission is self.admission
            and self.admission._shared_denials is self and self.admission._current_project_requests is self.named
            and self.admission._condition is self.condition and self.admission._activity_observer is self.observer
            and self.owner.original() and self.named.original() and self.observer._original(self.admission)
            and type(getattr(self.owner.store, 'project_for', None)) is MethodType
            and self.owner.store.project_for.__self__ is self.owner.store
            and self.owner.store.project_for.__func__ is self.project_for
            and self.auth is self.owner.middleware and type(self.auth) is self.auth_type
            and self.auth.project_app is self.app and self.auth_type.__call__ is self.wrapper
            and self.wrapper.__wrapped__ is self.body and self.body.__code__ is self.body_code
            and self.owned is self.observer._middleware and self.owned.app is self.downstream
            and all(getattr(owner, name) is function for owner, name, function in self.calls)
            and all(function.__code__ is code for function, code in self.codes))

    def bind_chain(self, middleware, downstream):
        self.owned, self.downstream = middleware, downstream
        nodes = tuple(node for node, kind, _, _ in self.named.chain if kind is self.auth_type)
        if len(nodes) != 1:
            self.eligible = False
            return
        self.auth = self.owner.middleware = nodes[0]
        if not self.original(): self.eligible = False

    def begin(self, activity):
        frame = sys._getframe(1)
        if (not self.original() or frame.f_code is not self.admission_enter.__code__
                or frame.f_locals.get('self') is not self.admission or frame.f_locals.get('shared') is not self
                or frame.f_locals.get('activity') is not activity or activity is None
                or not self.observer._valid(activity) or len(self.records) >= 256): return False
        scope = activity.scope
        values = _headers(scope)
        if (type(scope) is not dict or scope.get('app') is not self.app or scope.get('type') != 'http'
                or scope.get('method') != 'GET' or scope.get('path') == '/health'
                or type(scope.get('query_string', b'')) is not bytes or values is None
                or scope.get('subprotocols')): return False
        row = self.observer._records.get(activity.token)
        if row is None or row['carrier'] is not activity or not row['accepted'] or row['released']: return False
        forbidden = {b'cookie', b'x-vision-context', b'x-vision-project'}
        bearer = [value for key, value in values if key.lower() == b'authorization']
        if bearer:
            if (scope.get('path') not in _UNSELECTED_PATHS or len(bearer) != 1
                    or not bearer[0].startswith(b'Bearer ') or not 7 < len(bearer[0]) <= 263
                    or len(scope.get('query_string', b'')) > 4096
                    or any(key.lower() in forbidden for key, _ in values)): return False
            try: token = bearer[0][7:].decode('ascii')
            except UnicodeError: return False
            attempt = _AuthenticatedUnselectedAttempt(self, activity, values, token)
        else:
            if any(key.lower() in forbidden for key, _ in values): return False
            attempt = _SharedDenialAttempt(self, activity, values)
        self.pending[activity.token] = attempt
        self.records.append(attempt)
        self.admission._active += 1
        return True

    def find(self, activity):
        if activity is None: return None
        value = self.pending.get(activity.token)
        return value if type(value) in (_SharedDenialAttempt, _AuthenticatedUnselectedAttempt) and value.activity is activity else None

    def finish(self, activity):
        attempt = self.find(activity)
        if attempt is None: return
        row = self.observer._records.get(activity.token)
        valid = (attempt.original() and attempt.auth is not None and attempt.auth_returned and not attempt.failed
            and row is not None and row['carrier'] is activity and row['released']
            and attempt.auth.complete(row) and not row['transport_failed'] and not row['framing_error']
            and row['start_forwarded'] and row['terminal_forwarded'] and row['send_attempt'] is None
            and not row['trailers_expected'] and (self.deadline is None or time.monotonic() < self.deadline))
        if not valid:
            attempt.fail('unconfirmed_or_late')
            return
        del self.pending[activity.token]
        self.admission._active -= 1
        attempt.settled = True

    def snapshot(self):
        with self.condition:
            return {'pending':len(self.pending), 'records':[
                {'settled':row.settled, 'failed':row.failed, 'original_auth_slice_returned':row.auth_returned,
                 'database_context_exits':row.auth.database_exits if row.auth is not None else 0,
                 'authentication_rejections':row.auth.authentication_rejections if row.auth is not None else 0}
                for row in self.records], 'whole_writer_coverage':False,
                'process_tree_exit_verified':False, 'can_release_launch_lease':False}


class _SharedDenialAttempt:
    def __init__(self, adapter, activity, headers):
        self.adapter, self.activity, self.scope, self.context = adapter, activity, activity.scope, activity.context
        self.token = object()
        self.binding = self, adapter, activity, activity.token, self.scope, self.context, self.token
        self.header_values = headers
        self.query = self.scope.get('query_string', b'')
        self.path, self.raw_path = self.scope.get('path'), self.scope.get('raw_path')
        self.auth_headers = self.auth = None
        self.auth_returned = self.failed = self.settled = False
        # These are the exact no-downstream prerequisites of the reused original
        # account observation, never positive project/effect observations.
        self.handler = self.storage = 0
        self.resolutions, self.events, self.inflight = [], [], set()

    def fail(self, reason):
        self.failed = True
        self.adapter.admission._unsupported.add('uncovered_request_protocol')
        self.adapter.observer.poison('shared_denial_' + reason)

    def __copy__(self):
        self.fail('copy_refused')
        raise SharedDenialObservationError('Original shared denial cannot be copied')
    def __deepcopy__(self, memo): return self.__copy__()
    def __reduce_ex__(self, protocol): return self.__copy__()

    def base(self):
        a = self.adapter
        return (len(self.binding) == 7 and all(held is current for held, current in zip(self.binding,
                (self, a, self.activity, self.activity.token, self.scope, self.context, self.token)))
            and a.pending.get(self.activity.token) is self and not self.settled and a.original()
            and a.observer._valid(self.activity)
            and all(a.context_owner()[i] is self.context[i] for i in (0, 1, 2))
            and a.context_owner()[3] == self.context[3] and self.context[0] is not None
            and self.scope.get('query_string', b'') is self.query
            and self.scope.get('path') is self.path and self.scope.get('raw_path') is self.raw_path
            and _headers(self.scope) == self.header_values)

    def original(self):
        return self.base() and self.auth_headers is not None and self.scope.get('headers') is self.auth_headers

    def entry(self, middleware, scope, receive, send, store, headers):
        if not self.base() or _ACTIVE.get() is not self or self.auth is not None: return False
        a = self.adapter
        call = sys._getframe(1)
        hook = call.f_back
        entered = hook.f_back if hook is not None else None
        auth = entered.f_back if entered is not None else None
        wrapper = auth.f_back if auth is not None else None
        if (call.f_code is not _SharedDenialAttempt.activate.__code__
                or hook is None or hook.f_code is not a.hook.__code__ or hook.f_globals is not a.hook.__globals__
                or entered is None or entered.f_code is not a.enter.__code__ or _GeneratorContextManager.__enter__ is not a.enter
                or auth is None or auth.f_code is not a.body_code or auth.f_globals is not a.body_globals
                or auth.f_lasti != a.offset or wrapper is None or wrapper.f_code is not a.wrapper.__code__): return False
        manager = entered.f_locals.get('self')
        if getattr(getattr(manager, 'gen', None), 'gi_frame', None) is not hook: return False
        values = auth.f_locals
        rejection = values.get('reject')
        if (middleware is not a.auth or scope is not self.scope or store is not a.owner.store
                or values.get('self') is not middleware or values.get('scope') is not scope
                or values.get('receive') is not receive or values.get('send') is not send
                or values.get('headers') is not headers or values.get('store') is not store
                or type(headers) is not a.body_globals['Headers']
                or getattr(headers, '_list', None) is not scope.get('headers')
                or values.get('path') is not self.path or values.get('method') != 'GET'
                or values.get('token') != (self.bearer if type(self) is _AuthenticatedUnselectedAttempt else '') or 'account' in values
                or getattr(rejection, '__code__', None) is not a.reject_code[0]
                or rejection.__globals__ is not a.body_globals
                or wrapper.f_locals.get('middleware') is not middleware
                or wrapper.f_locals.get('scope') is not scope): return False
        # Require the actual live original Owned ASGI invocation, not a manual
        # call to the original auth function with a constructed scope.
        ancestor = wrapper.f_back
        for _ in range(16):
            if ancestor is None: return False
            if ancestor.f_code is a.owned_call.__code__:
                outer = ancestor.f_locals
                return (outer.get('self') is a.owned and outer.get('scope') is scope
                    and outer.get('activity') is self.activity and outer.get('admitted') is True
                    and outer.get('tracked_receive') is receive)
            ancestor = ancestor.f_back
        return False

    def activate(self, middleware, scope, receive, send, store, headers):
        if not self.entry(middleware, scope, receive, send, store, headers):
            self.fail('foreign_or_replayed_auth_slice')
            return False
        self.auth_headers = scope.get('headers')
        if not self.original():
            self.fail('natural_header_rebind_unconfirmed')
            return False
        if type(self) is _AuthenticatedUnselectedAttempt:
            self.auth = _AuthenticatedUnselectedAuth(self.adapter.owner, self)
            self.adapter.owner.records.append(self.auth)
        else:
            self.auth = self.adapter.owner.new_attempt(self)
        return self.auth.begin_middleware(middleware, scope)

    def returned(self, valid, normal):
        if self.auth is None or not valid or not normal or not self.original():
            self.fail('auth_slice_failed_or_changed')
            return
        self.auth.end_middleware(valid)
        self.auth_returned = True


class _AuthenticatedUnselectedAttempt(_SharedDenialAttempt):
    """Provisional ownership until the original no-project rejection returns."""
    def __init__(self, adapter, activity, headers, bearer):
        super().__init__(adapter, activity, headers)
        self.bearer = bearer
        self.unselected_binding = self, bearer

    def base(self):
        return (super().base() and len(self.unselected_binding) == 2
            and self.unselected_binding[0] is self and self.unselected_binding[1] is self.bearer
            and type(self.bearer) is str
            and [value for key, value in self.header_values if key.lower() == b'authorization']
                == [b'Bearer ' + self.bearer.encode('ascii')])


class _AuthenticatedUnselectedAuth(_SharedAttempt):
    """The real auth slice, then a distinct original project_for(None) exit.

    Auth-slice return precedes project resolution. Full middleware completion is
    proved separately from retained original frames and the final ASGI row.
    """
    def __init__(self, owner, attempt):
        super().__init__(owner, attempt)
        self.bearer = attempt.bearer
        self.project_for = attempt.adapter.project_for
        self.project_code = attempt.adapter.project_code
        self.authorization = type(owner.middleware).__call__.__wrapped__
        self.authorization_code = self.authorization.__code__
        self.project_returns = frozenset(i.offset for i in dis.get_instructions(self.project_code)
            if i.opname in {'RETURN_VALUE', 'RETURN_CONST'})
        self.auth_returns = frozenset(i.offset for i in dis.get_instructions(self.authorization_code)
            if i.opname in {'RETURN_VALUE', 'RETURN_CONST'})
        self.authorization_frame = self._authorization_frame()
        self.project_frame = self.db_frame = None
        self.account = None
        self.databases = []

    def original(self):
        return (super().original() and type(self.attempt) is _AuthenticatedUnselectedAttempt
            and self.bearer is self.attempt.bearer
            and self.owner.kind.project_for is self.project_for
            and self.project_for.__code__ is self.project_code
            and type(self.owner.middleware).__call__.__wrapped__ is self.authorization
            and self.authorization.__code__ is self.authorization_code)

    def _authorization_frame(self):
        frame = sys._getframe(1)
        for _ in range(16):
            if frame is None: return None
            if frame.f_code is self.authorization_code:
                return frame if (frame.f_globals is self.authorization.__globals__
                    and frame.f_locals.get('self') is self.owner.middleware
                    and frame.f_locals.get('scope') is self.attempt.scope
                    and frame.f_locals.get('store') is self.owner.store) else None
            frame = frame.f_back
        return None

    def method(self, name, store, value, function, args, kwargs):
        expected = 'bind_workspace' if not self.methods else 'authenticate' if len(self.methods) == 1 else None
        valid = (self.original() and self.authorization_frame is not None
            and self._authorization_frame() is self.authorization_frame
            and self.middleware_calls == 1 and self.middleware_returns == 0
            and store is self.owner.store and name == expected and self.method_name is None
            and function is getattr(type(store), name).__wrapped__
            and (value is self.owner.workspace if name == 'bind_workspace' else value == self.bearer))
        if not valid: self.fail('authenticated_method_entry')
        self.method_name = name
        before = len(self.databases)
        try:
            result = function(store, *args, **kwargs)
        except BaseException:
            self.fail('authenticated_method_failed')
            raise
        else:
            if not (valid and self.original() and not self.db_live and len(self.databases) == before + 1):
                self.fail('authenticated_method_return')
            if name == 'authenticate':
                if type(result) is not dict or not result.get('id') or result.get('disabled'):
                    self.fail('authenticated_account')
                self.account = result
            self.methods.append(name)
            return result
        finally: self.method_name = None

    def begin_db(self, store):
        expected = ('bind_workspace', 'authenticate', 'project_for')[len(self.databases)] if len(self.databases) < 3 else None
        function = getattr(type(store), expected, None) if expected else None
        function = getattr(function, '__wrapped__', function)
        frame = sys._getframe(1)
        for _ in range(8):
            if frame is None or function is not None and frame.f_code is function.__code__: break
            frame = frame.f_back
        valid = (self.original() and store is self.owner.store and not self.db_live
            and self.authorization_frame is not None and self._authorization_frame() is self.authorization_frame
            and frame is not None and function is not None and frame.f_code is function.__code__
            and frame.f_locals.get('self') is store)
        if expected == 'project_for':
            valid = (valid and self.middleware_calls == self.middleware_returns == 1
                and self.attempt.auth_returned and self.method_name is None
                and type(self.account) is dict and frame.f_locals.get('user_id') == self.account.get('id')
                and frame.f_locals.get('project_id') is None
                and self.authorization_frame.f_locals.get('account') is self.account)
            self.project_frame = frame
        else:
            valid = (valid and self.middleware_calls == 1 and self.middleware_returns == 0
                and self.method_name == expected)
        if not valid: self.fail('authenticated_database_entry')
        self.db_live = True
        self.db_frame = frame
        return valid, expected

    def end_db(self, connection, entry, body_error, exit_error):
        valid, expected = entry
        closed = False
        if type(connection) is sqlite3.Connection:
            try: connection.in_transaction
            except sqlite3.ProgrammingError: closed = True
        if not (valid and self.original() and self.db_live and closed and body_error is None and exit_error is None):
            self.fail('authenticated_database_exit')
        else:
            self.database_exits += 1
            self.databases.append((expected, connection))
        self.db_live = False
        self.db_frame = None

    def complete(self, envelope):
        scope = self.attempt.scope
        state = scope.get('state', {})
        frame, project = self.authorization_frame, self.project_frame
        valid = (self.original() and not self.attempt.failed and self.attempt.auth_returned
            and type(self.account) is dict
            and self.middleware_calls == self.middleware_returns == 1
            and self.methods == ['bind_workspace', 'authenticate'] and self.database_exits == 3
            and [name for name, _ in self.databases] == ['bind_workspace', 'authenticate', 'project_for']
            and not self.db_live and self.method_name is None and self.db_frame is None
            and frame is not None and frame.f_code is self.authorization_code and frame.f_lasti in self.auth_returns
            and project is not None and project.f_code is self.project_code and project.f_lasti in self.project_returns
            and project.f_locals.get('self') is self.owner.store
            and project.f_locals.get('user_id') == self.account.get('id')
            and project.f_locals.get('selected') is None and project.f_locals.get('project_id') is None
            and frame.f_locals.get('account') is self.account and frame.f_locals.get('token') == self.bearer
            and frame.f_locals.get('selected') is None and frame.f_locals.get('project') is None
            and frame.f_locals.get('role') is None and frame.f_locals.get('declared') is None
            and state.get('account_user') is self.account and state.get('account_session_token') == self.bearer
            and not any(key in state for key in ('scoped_project', 'project_context', 'permission_decision'))
            and self.attempt.handler == self.attempt.storage == 0 and not self.attempt.resolutions
            and not self.attempt.events and not self.attempt.inflight
            and envelope.get('response_status') == 409 and envelope['normal_return'] and envelope['completed'])
        if not valid: return False
        for _, connection in self.databases:
            try: connection.in_transaction
            except sqlite3.ProgrammingError: continue
            return False
        return True


def bind_original_shared_denial(app, admission):
    if admission is not None and admission._activity_observer is not None and app.state.accounts is not None:
        admission._shared_denials = OriginalSharedDenialAdapter(app, admission)


def shared_denial_auth():
    attempt = _ACTIVE.get()
    return attempt.auth if type(attempt) in (_SharedDenialAttempt, _AuthenticatedUnselectedAttempt) and attempt.auth is not None else None


@contextmanager
def shared_denial_scope(admission, activity):
    adapter = admission._shared_denials
    attempt = adapter.find(activity) if type(adapter) is OriginalSharedDenialAdapter else None
    if attempt is None:
        yield
        return
    token = _ACTIVE.set(attempt)
    try: yield
    finally: _ACTIVE.reset(token)


@contextmanager
def original_shared_denial_authentication(middleware, scope, receive, send, store, headers):
    attempt = _ACTIVE.get()
    if type(attempt) not in (_SharedDenialAttempt, _AuthenticatedUnselectedAttempt):
        yield
        return
    with attempt.adapter.condition:
        valid = attempt.activate(middleware, scope, receive, send, store, headers)
    normal = False
    try:
        yield
        normal = True
    finally:
        with attempt.adapter.condition: attempt.returned(valid, normal)
