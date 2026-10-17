"""One exact original desktop denial envelope; never writer/tree/lease authority."""
from contextlib import contextmanager, _GeneratorContextManager
from contextvars import ContextVar
import dis
import os
import sys
import time

_ACTIVE = ContextVar('original_desktop_auth_denial', default=None)


class DesktopDenialObservationError(ValueError): pass


def _header_values(scope):
    headers = scope.get('headers')
    if (type(headers) is not list or any(type(pair) is not tuple or len(pair) != 2
            or type(pair[0]) is not bytes or type(pair[1]) is not bytes for pair in headers)):
        return None
    return tuple(headers)


class OriginalDesktopDenialAdapter:
    def __init__(self, app, admission):
        from backend import main
        from backend.engine import application_launch_handshake
        from backend.engine import original_current_project_request, original_request_activity
        from backend.engine.original_current_project_request import _owner, _configuration_capture, _configuration_original
        from starlette.middleware.cors import CORSMiddleware
        self.app, self.state, self.admission = app, app.state, admission
        self.observer, self.condition = admission._activity_observer, admission._condition
        self.named = admission._current_project_requests
        self.auth_type, self.cors_type = main.DesktopApiAuthMiddleware, CORSMiddleware
        self.auth_call = self.auth_type.__call__
        self.auth_code, self.auth_globals = self.auth_call.__code__, self.auth_call.__globals__
        self.entry_offsets = tuple(i.offset for i in dis.get_instructions(self.auth_code) if i.opname == 'BEFORE_WITH')
        self.hook_body = original_desktop_denial_response.__wrapped__
        self.hook_code = self.hook_body.__code__
        self.enter_call = _GeneratorContextManager.__enter__
        self.enter_code = self.enter_call.__code__
        self.admission_enter = application_launch_handshake.BackendWorkAdmission.enter
        self.owner, self.capture, self.config_original = _owner, _configuration_capture, _configuration_original
        self.api_token = app.state.api_token
        self.pid, self.active_context = os.getpid(), _ACTIVE
        self.auth = self.prefix = self.config = self.owned = self.downstream = None
        self.pending, self.records, self.deadline = {}, [], None
        self.eligible = self.named is not None and app.state.accounts is None and len(self.entry_offsets) == 1
        self.calls = tuple((owner, name, getattr(owner, name)) for owner, names in (
            (main, ('DesktopApiAuthMiddleware', 'JSONResponse', 'Headers')),
            (self.auth_type, ('__init__', '__call__')),
            (main.JSONResponse, ('__init__', '__call__', 'render', 'init_headers')),
            (main.Headers, ('__init__', 'get')),
            (main.secrets, ('compare_digest',)),
            (CORSMiddleware, ('__init__', '__call__', 'simple_response', 'send', 'is_allowed_origin', 'preflight_response')),
            (application_launch_handshake.BackendWorkAdmission, ('enter', 'leave', '_activity_transition', 'observe_activity')),
            (original_current_project_request, ('_owner', '_configuration_capture', '_configuration_original')),
            (original_request_activity.OriginalRequestActivityObserver, ('_original', '_valid', '_row', '_frame', 'event')),
            (_GeneratorContextManager, ('__enter__', '__exit__')),
            (sys.modules[__name__], ('original_desktop_denial_response', 'desktop_denial_scope', '_ACTIVE', '_header_values')),
            (OriginalDesktopDenialAdapter, ('original', 'begin', 'bind_chain', 'finish', 'find')),
            (_DenialAttempt, ('original', '_base_original', 'fail', '_entry', 'branch', 'finish_branch'))) for name in names)
        self.codes = tuple((function, function.__code__) for _, _, function in self.calls if hasattr(function, '__code__'))
        self.named_original = type(self.named).original if self.named is not None else None
        self.named_original_code = self.named_original.__code__ if self.named_original is not None else None

    def original(self):
        return (self.eligible and self.active_context is _ACTIVE and self.pid == os.getpid()
            and self.app.state is self.state and self.state.accounts is None
            and self.state.api_token is self.api_token and type(self.api_token) is str and bool(self.api_token)
            and self.state.owned_work_admission is self.admission
            and self.admission._condition is self.condition and self.admission._activity_observer is self.observer
            and self.admission._desktop_denials is self and self.admission._current_project_requests is self.named
            and self.observer._original(self.admission)
            and self.named_original is not None and type(self.named).original is self.named_original
            and self.named_original.__code__ is self.named_original_code and self.named_original(self.named)
            and all(getattr(owner, name) is function for owner, name, function in self.calls)
            and all(function.__code__ is code for function, code in self.codes)
            and self.owned is self.observer._middleware and self.owned.app is self.downstream
            and self.prefix is not None and self.config is not None
            and all(type(node) is kind and node.app is downstream
                    and self.config_original(vars(node), config)
                    for (node, kind, downstream), config in zip(self.prefix, self.config))
            and type(self.auth) is self.auth_type and self.auth.api_token is self.api_token)

    def bind_chain(self, middleware, downstream):
        self.owned, self.downstream = middleware, downstream
        chain = self.named.chain if self.named is not None else ()
        nodes = tuple((node, kind, following) for node, kind, following, _ in chain[:2])
        if len(nodes) != 2 or nodes[0][1] is not self.cors_type or nodes[1][1] is not self.auth_type:
            self.eligible = False
            return
        self.prefix, self.auth = nodes, nodes[1][0]
        try:
            self.config = tuple(self.capture(vars(node), (following,), set(), [512]) for node, _, following in nodes)
        except ValueError:
            self.eligible = False
        if not self.original(): self.eligible = False

    def begin(self, activity):
        frame = sys._getframe(1)
        if (not self.original() or frame.f_code is not self.admission_enter.__code__
                or frame.f_locals.get('self') is not self.admission
                or frame.f_locals.get('denials') is not self or frame.f_locals.get('activity') is not activity
                or activity is None or not self.observer._valid(activity)):
            return False
        scope = activity.scope
        if (type(scope) is not dict or scope.get('app') is not self.app or scope.get('type') != 'http'
                or scope.get('method') != 'GET' or scope.get('path') == '/health'
                or scope.get('query_string', b'') != b'' or len(self.records) >= 256): return False
        from backend import main
        values = _header_values(scope)
        if values is None: return False
        # Original Headers(raw=...) reads a private copy; never rebind scope.
        provided = main.Headers(raw=list(values)).get('x-vision-token', '')
        if main.secrets.compare_digest(provided, self.api_token): return False
        row = self.observer._records.get(activity.token)
        if row is None or row['carrier'] is not activity or not row['accepted'] or row['released']:
            return False
        attempt = _DenialAttempt(self, activity)
        self.pending[activity.token] = attempt
        self.records.append(attempt)
        self.admission._active += 1
        return True

    def find(self, activity):
        if activity is None: return None
        attempt = self.pending.get(activity.token)
        return attempt if type(attempt) is _DenialAttempt and attempt.activity is activity else None

    def finish(self, activity):
        attempt = self.find(activity)
        if attempt is None: return
        row = self.observer._records.get(activity.token)
        valid = (attempt.original() and not attempt.failed and attempt.branch_calls == 1
            and attempt.branch_completed and not attempt.inflight and row is not None
            and row['carrier'] is activity and row['released'] and row['completed'] and row['normal_return']
            and row['response_status'] == 401 and not row['transport_failed'] and not row['framing_error']
            and row['send_attempt'] is None and row['start_forwarded'] and row['terminal_forwarded']
            and not row['trailers_expected'] and (self.deadline is None or time.monotonic() < self.deadline))
        if not valid:
            attempt.fail('unconfirmed_or_late')
            return
        del self.pending[activity.token]
        self.admission._active -= 1
        attempt.settled = True

    def snapshot(self):
        with self.condition:
            return {'pending': len(self.pending), 'records': [
                {'settled': r.settled, 'failed': r.failed, 'original_denial_calls': r.branch_calls,
                 'original_denial_returned': r.branch_completed} for r in self.records],
                'whole_writer_coverage': False, 'process_tree_exit_verified': False, 'can_release_launch_lease': False}


class _DenialAttempt:
    def __init__(self, adapter, activity):
        self.adapter, self.activity, self.context = adapter, activity, activity.context
        self.headers = activity.scope.get('headers', ())
        self.header_values = tuple(self.headers)
        self.auth_headers = None
        self.scope = activity.scope
        self.binding = self, adapter, activity, activity.token, self.scope, self.context, self.headers
        self.branch_calls = 0
        self.inflight = self.branch_completed = self.settled = self.failed = False

    def fail(self, reason):
        self.failed = True
        self.adapter.admission._unsupported.add('uncovered_request_protocol')
        self.adapter.observer.poison('desktop_denial_' + reason)

    def __copy__(self):
        self.fail('copy_refused')
        raise DesktopDenialObservationError('Original desktop denial cannot be copied')
    def __deepcopy__(self, memo): return self.__copy__()
    def __reduce_ex__(self, protocol): return self.__copy__()

    def _base_original(self):
        a = self.adapter
        return (len(self.binding) == 7 and all(held is current for held, current in zip(self.binding,
                (self, a, self.activity, self.activity.token, self.scope, self.context, self.headers)))
            and a.pending.get(self.activity.token) is self and not self.settled and a.original()
            and a.observer._valid(self.activity) and all(a.owner()[i] is self.context[i] for i in (0, 1, 2))
            and a.owner()[3] == self.context[3] and self.context[0] is not None
            and tuple(self.headers) == self.header_values and _header_values(self.scope) == self.header_values
            and self.scope.get('query_string', b'') == b'')

    def original(self):
        expected = self.auth_headers if self.auth_headers is not None else self.headers
        return self._base_original() and self.scope.get('headers') is expected

    def _entry(self, middleware, scope, receive, send):
        a = self.adapter
        if not self._base_original(): return False
        begin = sys._getframe(1); hook = begin.f_back
        enter = hook.f_back if hook is not None else None
        auth = enter.f_back if enter is not None else None
        if (begin.f_code is not _DenialAttempt.branch.__code__
                or hook is None or hook.f_code is not a.hook_code or hook.f_globals is not a.hook_body.__globals__
                or enter is None or enter.f_code is not a.enter_code or _GeneratorContextManager.__enter__ is not a.enter_call
                or auth is None or auth.f_code is not a.auth_code or auth.f_globals is not a.auth_globals
                or auth.f_lasti not in a.entry_offsets): return False
        manager = enter.f_locals.get('self')
        if getattr(getattr(manager, 'gen', None), 'gi_frame', None) is not hook: return False
        values = auth.f_locals
        from backend import main
        return (middleware is a.auth and scope is self.scope and values.get('self') is middleware
            and values.get('scope') is scope and values.get('receive') is receive and values.get('send') is send
            and values.get('scope_type') == 'http' and scope.get('method') == 'GET'
            and values.get('provided_token') == main.Headers(raw=list(self.header_values)).get('x-vision-token', '')
            and not main.secrets.compare_digest(values.get('provided_token'), a.api_token))

    def branch(self, middleware, scope, receive, send):
        if self.branch_calls or self.auth_headers is not None or not self._entry(middleware, scope, receive, send):
            self.fail('foreign_or_replayed_branch')
            return False
        # Only this live original auth branch may bind its natural Headers(scope)
        # list. Pinned original CORS/auth code has not suspended before this hook.
        # Both original constructors preserve the exact initial raw byte pairs.
        natural = scope.get('headers')
        if type(natural) is not list or natural is self.headers or _header_values(scope) != self.header_values:
            self.fail('original_header_rebind_unconfirmed')
            return False
        self.auth_headers = natural
        if not self.original():
            self.fail('original_header_owner_changed')
            return False
        self.branch_calls += 1
        self.inflight = True
        return True

    def finish_branch(self, valid, normal):
        if not valid or not normal or not self.original() or not self.inflight:
            self.fail('branch_failed_or_changed')
            return
        self.inflight = False
        self.branch_completed = True


def bind_original_desktop_denial(app, admission):
    if admission is not None and admission._activity_observer is not None and app.state.accounts is None:
        admission._desktop_denials = OriginalDesktopDenialAdapter(app, admission)


@contextmanager
def desktop_denial_scope(admission, activity):
    adapter = admission._desktop_denials
    attempt = adapter.find(activity) if type(adapter) is OriginalDesktopDenialAdapter else None
    if attempt is None:
        yield
        return
    token = _ACTIVE.set(attempt)
    try: yield
    finally: _ACTIVE.reset(token)


@contextmanager
def original_desktop_denial_response(middleware, scope, receive, send):
    attempt = _ACTIVE.get()
    if type(attempt) is not _DenialAttempt:
        yield
        return
    with attempt.adapter.condition:
        valid = attempt.branch(middleware, scope, receive, send)
    normal = False
    try:
        yield
        normal = True
    finally:
        with attempt.adapter.condition: attempt.finish_branch(valid, normal)
