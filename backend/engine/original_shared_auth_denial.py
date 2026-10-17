"""Exact original empty-session shared-auth denial and its SQLite/ASGI exits.

Pending is provisional, never a GET/status allowlist. Only the live original
authentication slice can activate the captured account observer. Any other
result retains the pending admission and original sticky unsupported flag.
"""
from contextlib import contextmanager, _GeneratorContextManager
from contextvars import ContextVar
import dis
import os
import sys
import time

_ACTIVE = ContextVar('original_shared_auth_denial', default=None)


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
            (original_shared_current_project, ('_auth',)),
            (sys.modules[__name__], ('_ACTIVE', '_headers', '_hook_offset', 'shared_denial_auth',
                                    'shared_denial_scope', 'original_shared_denial_authentication')),
            (OriginalSharedDenialAdapter, ('__init__', 'original', 'begin', 'bind_chain', 'find', 'finish')),
            (_SharedDenialAttempt, ('__init__', 'original', 'base', 'fail', 'entry', 'activate', 'returned'))) for name in names)
        self.codes = tuple((function, function.__code__) for _, _, function in self.calls if hasattr(function, '__code__'))

    def original(self):
        return (self.eligible and self.pid == os.getpid() and self.context is _ACTIVE
            and self.app.state is self.state and self.state.accounts is self.owner.store
            and self.state.owned_work_admission is self.admission
            and self.admission._shared_denials is self and self.admission._current_project_requests is self.named
            and self.admission._condition is self.condition and self.admission._activity_observer is self.observer
            and self.owner.original() and self.named.original() and self.observer._original(self.admission)
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
                or any(key.lower() in {b'authorization', b'cookie', b'x-vision-context', b'x-vision-project'} for key, _ in values)
                or scope.get('subprotocols')): return False
        row = self.observer._records.get(activity.token)
        if row is None or row['carrier'] is not activity or not row['accepted'] or row['released']: return False
        attempt = _SharedDenialAttempt(self, activity, values)
        self.pending[activity.token] = attempt
        self.records.append(attempt)
        self.admission._active += 1
        return True

    def find(self, activity):
        if activity is None: return None
        value = self.pending.get(activity.token)
        return value if type(value) is _SharedDenialAttempt and value.activity is activity else None

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
                or values.get('token') != '' or 'account' in values
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
        self.auth = self.adapter.owner.new_attempt(self)
        return self.auth.begin_middleware(middleware, scope)

    def returned(self, valid, normal):
        if self.auth is None or not valid or not normal or not self.original():
            self.fail('auth_slice_failed_or_changed')
            return
        self.auth.end_middleware(valid)
        self.auth_returned = True


def bind_original_shared_denial(app, admission):
    if admission is not None and admission._activity_observer is not None and app.state.accounts is not None:
        admission._shared_denials = OriginalSharedDenialAdapter(app, admission)


def shared_denial_auth():
    attempt = _ACTIVE.get()
    return attempt.auth if type(attempt) is _SharedDenialAttempt and attempt.auth is not None else None


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
    if type(attempt) is not _SharedDenialAttempt:
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
