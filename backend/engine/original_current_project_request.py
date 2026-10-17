"""One original fresh current-project request; never whole-writer authority.

This observes invocation, atomic publication and original SQLite context exit.
It does not certify a pooled thread's lifetime or accept a database snapshot as
process authority. Missing observations retain a separate pending admission.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import os
from pathlib import Path
import sqlite3
import threading
import time
from types import MethodType

_CURRENT = ContextVar('original_current_project_request', default=None)
_DECORATED = {}


def _owner():
    try:
        return asyncio.current_task(), asyncio.get_running_loop(), threading.current_thread(), os.getpid()
    except RuntimeError:
        return None, None, threading.current_thread(), os.getpid()


class CurrentProjectObservationError(ValueError): pass


def _bound_function(owner, name):
    method = getattr(owner, name)
    return method.__func__ if type(method) is MethodType and method.__self__ is owner else None


def _configuration_capture(value, opaque, seen, budget, depth=0):
    """Retain exact original configuration containers without invoking owners."""
    budget[0] -= 1
    if budget[0] < 0 or depth > 8:
        raise CurrentProjectObservationError('Original middleware configuration exceeds finite bounds')
    if any(value is owner for owner in opaque): return ('owner', value)
    if type(value) in (type(None), bool, int, float, str, bytes): return ('atom', value)
    if type(value) not in (dict, list, tuple) or id(value) in seen:
        raise CurrentProjectObservationError('Unknown or cyclic original middleware configuration')
    seen.add(id(value))
    try:
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise CurrentProjectObservationError('Unknown original middleware configuration key')
            children = tuple((key, _configuration_capture(item, opaque, seen, budget, depth+1))
                             for key, item in value.items())
            return ('dict', value, children)
        return (type(value).__name__, value, tuple(_configuration_capture(item, opaque, seen, budget, depth+1)
                                                   for item in value))
    finally: seen.remove(id(value))


def _configuration_original(value, captured):
    kind, original, *children = captured
    if value is not original: return False
    if kind in ('owner', 'atom'): return True
    if kind == 'dict':
        entries = children[0]
        return (type(value) is dict and len(value) == len(entries)
                and all(key is held_key and _configuration_original(item, held_value)
                        for (key, item), (held_key, held_value) in zip(value.items(), entries)))
    entries = children[0]
    return (type(value) is {'list': list, 'tuple': tuple}[kind] and len(value) == len(entries)
            and all(_configuration_original(item, held) for item, held in zip(value, entries)))


class _OriginalMiddlewareConfiguration:
    """The two explicit original Starlette Middleware layouts, never a fallback."""
    def __init__(self, entries, allowed, opaque):
        from starlette.middleware import Middleware
        self.middleware_type = Middleware
        self.class_calls = Middleware.__init__, Middleware.__iter__
        rows = []
        budget = [256]
        if type(entries) is not list or len(entries) > 10:
            raise CurrentProjectObservationError('Unknown original middleware collection')
        for entry in entries:
            if type(entry) is not Middleware or type(vars(entry)) is not dict:
                raise CurrentProjectObservationError('Unknown original middleware entry')
            fields = vars(entry)
            keys = frozenset(fields)
            if keys == {'cls', 'options'}:
                layout, args, options = 'legacy', None, fields['options']
            elif keys == {'cls', 'args', 'kwargs'} and type(fields['args']) is tuple:
                layout, args, options = 'modern', fields['args'], fields['kwargs']
            else: raise CurrentProjectObservationError('Unknown original Middleware field layout')
            if not any(fields['cls'] is kind for kind in allowed) or type(options) is not dict:
                raise CurrentProjectObservationError('Unknown original middleware class or options')
            rows.append((entry, fields, fields['cls'], layout,
                         _configuration_capture(args, opaque, set(), budget),
                         _configuration_capture(options, opaque, set(), budget)))
        self.entries, self.rows = entries, tuple(rows)

    def original(self, entries):
        Middleware = self.middleware_type
        if (entries is not self.entries or type(entries) is not list or len(entries) != len(self.rows)
                or Middleware.__init__ is not self.class_calls[0]
                or Middleware.__iter__ is not self.class_calls[1]): return False
        for current, (entry, fields, cls, layout, args, options) in zip(entries, self.rows):
            if current is not entry or type(current) is not Middleware or vars(current) is not fields: return False
            if layout == 'legacy':
                if frozenset(fields) != {'cls', 'options'}: return False
                current_args, current_options = None, fields['options']
            else:
                if frozenset(fields) != {'cls', 'args', 'kwargs'} or type(fields['args']) is not tuple: return False
                current_args, current_options = fields['args'], fields['kwargs']
            if (fields['cls'] is not cls or not _configuration_original(current_args, args)
                    or not _configuration_original(current_options, options)): return False
        return True


class _Attempt:
    def __init__(self, adapter, activity, *, auth_factory=None):
        self.adapter, self.activity = adapter, activity
        self.token = object()
        self.binding = self, adapter, activity, activity.token, activity.context, self.token
        self.failed = False
        self.settled = False
        self.events = []
        self.inflight = set()
        self.resolutions = []
        self.storage = 0
        self.handler = 0
        self.worker = None
        self.worker_done = False
        self.result = None
        self.auth = (auth_factory(self) if auth_factory is not None else
                     adapter.shared.new_attempt(self) if adapter.shared is not None else None)

    def fail(self, reason):
        self.failed = True
        self.adapter.admission._unsupported.add('uncovered_request_protocol')
        self.adapter.observer.poison('current_project_' + reason)

    def __copy__(self):
        with self.adapter.condition: self.fail('copy_refused')
        raise CurrentProjectObservationError('Original named request cannot be copied')
    def __deepcopy__(self, memo): return self.__copy__()
    def __reduce_ex__(self, protocol): return self.__copy__()

    def original(self):
        if (self.binding != (self, self.adapter, self.activity, self.activity.token, self.activity.context, self.token)
                or self.adapter.pending.get(self.token) is not self or self.settled
                or not self.adapter.original()):
            self.fail('changed_or_replayed'); return False
        current = _owner()
        context = self.activity.context
        task_owner = all(current[i] is context[i] for i in (0, 1, 2)) and current[3] == context[3]
        worker_owner = (self.worker is not None and self.worker[0] is current[2]
                        and self.worker[1] == current[3] and not self.worker_done and current[0] is None)
        if not task_owner and not worker_owner:
            self.fail('foreign_execution_owner'); return False
        return True

    @contextmanager
    def effect(self, kind, identity):
        with self.adapter.condition:
            ticket = object()
            valid = self.original()
            if valid: self.inflight.add(ticket)
        try:
            yield
        except BaseException:
            with self.adapter.condition: self.fail(kind + '_failed')
            raise
        else:
            with self.adapter.condition:
                if valid and self.original() and ticket in self.inflight:
                    self.inflight.remove(ticket)
                    if len(self.events) < 16: self.events.append((kind, identity))
                    else: self.fail('effect_overflow')


def observe_publication(kind):
    def decorate(function):
        @wraps(function)
        def observed(path, value):
            attempt = _CURRENT.get()
            if attempt is None: return function(path, value)
            with attempt.effect(kind, Path(path).resolve()): return function(path, value)
        _DECORATED[kind] = observed
        return observed
    return decorate


def observe_resolution(function):
    @wraps(function)
    def observed(request):
        attempt = _CURRENT.get()
        if attempt is None: return function(request)
        with attempt.adapter.condition:
            valid = attempt.original() and request.scope is attempt.activity.scope and request.app is attempt.adapter.app
            if not valid: attempt.fail('resolution_binding')
        try: result = function(request)
        except BaseException:
            with attempt.adapter.condition: attempt.fail('resolution_failed')
            raise
        with attempt.adapter.condition:
            if valid and attempt.original(): attempt.resolutions.append(result)
        return result
    # Resolve the original module's future annotations before FastAPI builds
    # its dependency signature; the Request argument remains the same API.
    observed.__signature__ = inspect.signature(function, eval_str=True)
    _DECORATED['resolution'] = observed
    return observed


def observe_context_db(function):
    @wraps(function)
    def observed(registry):
        original_context = function(registry)
        attempt = _CURRENT.get()
        if attempt is None: return original_context
        @contextmanager
        def retained():
            connection = None
            with attempt.effect('context_db', registry):
                if registry is not attempt.adapter.registry:
                    with attempt.adapter.condition: attempt.fail('foreign_registry')
                with original_context as db:
                    connection = db
                    if type(db) is not sqlite3.Connection:
                        with attempt.adapter.condition: attempt.fail('foreign_connection')
                    yield db
                # The original context has committed/rolled back and closed the
                # exact connection. No rows or copied DB state grant authority.
                if connection is None:
                    with attempt.adapter.condition: attempt.fail('missing_connection')
        return retained()
    _DECORATED['context_db'] = observed
    return observed


def observe_storage_scope(function):
    @wraps(function)
    async def observed(middleware, scope, receive, send):
        attempt = _CURRENT.get()
        if attempt is None: return await function(middleware, scope, receive, send)
        with attempt.effect('storage_scope', middleware):
            if middleware is not attempt.adapter.storage or scope is not attempt.activity.scope:
                with attempt.adapter.condition: attempt.fail('storage_binding')
            await function(middleware, scope, receive, send)
            with attempt.adapter.condition: attempt.storage += 1
    _DECORATED['storage_scope'] = observed
    return observed


class OriginalCurrentProjectAdapter:
    def __init__(self, app, admission):
        from backend.api import routes_project
        from backend.contracts.context import ContextRegistry
        from backend.engine import project_labelsets
        from backend import main
        from backend.engine.migration_guard import ProjectMaintenanceMiddleware
        from fastapi.routing import APIRouter, APIRoute
        from starlette.routing import Route
        from starlette.middleware.cors import CORSMiddleware
        from starlette.middleware.exceptions import ExceptionMiddleware
        self.app, self.admission = app, admission
        self.observer, self.condition = admission._activity_observer, admission._condition
        self.pid = os.getpid()
        self.root_object = app.state.project_dir
        self.root = Path(self.root_object).resolve()
        self.registry = app.state.context_registry
        self.registry_binding = self.registry.root, self.registry.path
        self.registry_calls = tuple((name, _bound_function(self.registry, name)) for name in
            ('_db', 'transaction', 'register_project', 'context'))
        self.pending = {}
        self.records = []
        self.deadline = None
        self.chain = None
        self.storage = None
        self.router, self.router_type, self.router_call = app.router, APIRouter, APIRouter.__call__
        self.chain_types = frozenset((CORSMiddleware, main.DesktopApiAuthMiddleware,
            main.ProjectContextMiddleware, main.ProjectStorageScopeMiddleware,
            ProjectMaintenanceMiddleware, ExceptionMiddleware))
        self.shared = None
        self.shared_original = None
        self.shared_original_code = None
        if app.state.accounts is not None:
            from backend.engine.original_shared_current_project import OriginalSharedAccountObservation
            from backend.api.shared_authorization import SharedAuthorizationMiddleware
            self.shared = OriginalSharedAccountObservation(app, admission)
            self.shared_original = OriginalSharedAccountObservation.original
            self.shared_original_code = self.shared_original.__code__
            self.chain_types = self.chain_types | {SharedAuthorizationMiddleware}
        self.eligible = ((app.state.accounts is None or self.shared is not None and self.shared.original()) and type(self.registry) is ContextRegistry
            and type(self.router) is self.router_type
            and routes_project.get_current_project is _DECORATED.get('resolution')
            and main.get_current_project is routes_project.get_current_project
            and routes_project._write_json is _DECORATED.get('project_json')
            and project_labelsets._atomic_json is _DECORATED.get('labelset_json')
            and ContextRegistry._db is _DECORATED.get('context_db')
            and main.ProjectStorageScopeMiddleware.__call__ is _DECORATED.get('storage_scope'))
        self.route = None
        self.included_current = None
        routes = [route for route in app.router.routes if getattr(route, 'path', None) == '/api/project/current']
        if not routes and self.eligible:
            from backend.engine.original_included_current_project import OriginalIncludedCurrentProject, IncludedCurrentProjectError
            try:
                self.included_current = OriginalIncludedCurrentProject(app, admission, routes_project.router, main.artifacts_router)
                routes = [self.included_current.route]
                from fastapi.middleware.asyncexitstack import AsyncExitStackMiddleware
                self.chain_types = self.chain_types | {AsyncExitStackMiddleware}
            except IncludedCurrentProjectError:
                self.included_current = None
        if len(routes) != 1 or routes[0].endpoint is not routes_project.get_current_project or routes[0].methods != {'GET'}:
            self.eligible = False
        else: self.route = routes[0]
        self.route_type = APIRoute
        self.route_class_calls = tuple((kind, name, getattr(kind, name))
            for kind in (APIRoute, Route) for name in ('matches', 'handle'))
        self.route_calls = tuple((name, _bound_function(self.route, name)) for name in ('matches', 'handle')) if self.route is not None else ()
        self.match_prefix = tuple((route, type(route), _bound_function(route, 'matches'))
            for route in app.router.routes[:app.router.routes.index(self.route)+1]) if self.route is not None and self.included_current is None else ()
        if (type(self.route) is not APIRoute or any(function is None for _, function in self.registry_calls)
                or any(kind not in (APIRoute, Route) or function is None for _, kind, function in self.match_prefix)):
            self.eligible = False
        self.functions = tuple((module, name, getattr(module, name)) for module, names in (
            (routes_project, ('get_current_project', '_write_json', 'create_project', '_activate_project', 'load_labelsets')),
            (project_labelsets, ('_atomic_json', 'load_labelsets')),
            (ContextRegistry, ('_db', 'register_project', 'context', 'transaction')),
            (main, ('get_current_project',))) for name in names)
        self.middleware_functions = tuple((kind, kind.__call__) for kind in
            (*self.chain_types, main.OwnedWorkAdmissionMiddleware))
        if self.eligible:
            from starlette.concurrency import run_in_threadpool
            from starlette.routing import request_response
            endpoint = routes_project.get_current_project
            @wraps(endpoint)
            async def matched(request):
                attempt = _CURRENT.get()
                if attempt is None: return await run_in_threadpool(endpoint, request)
                with self.condition:
                    selected = getattr(attempt.auth, 'selected_current', False)
                    if (selected and not attempt.auth.activate(request, matched)
                            or not attempt.original() or request.scope is not attempt.activity.scope
                            or request.scope.get('route') is not self.route or attempt.handler
                            or (self.included_current is not None and not self.included_current.selected(request.scope, attempt))):
                        attempt.fail('handler_binding')
                    attempt.handler += 1
                def invoke():
                    with self.condition:
                        if attempt.worker is not None: attempt.fail('worker_replay')
                        attempt.worker = threading.current_thread(), os.getpid()
                    try: return endpoint(request)
                    except BaseException:
                        with self.condition: attempt.fail('worker_failed')
                        raise
                try:
                    result = await run_in_threadpool(invoke)
                    with self.condition:
                        if not attempt.original(): attempt.fail('handler_owner')
                        attempt.worker_done = True
                        attempt.result = result
                    return result
                except BaseException:
                    with self.condition: attempt.fail('handler_failed')
                    raise
            if self.included_current is None:
                self.route.endpoint = self.route.dependant.call = matched
                self.route.app = request_response(self.route.get_route_handler())
            else: self.included_current.install(matched, self)
        self.routes = tuple(app.router.routes)
        self.route_binding = ((self.route.endpoint, self.route.dependant.call, self.route.app)
                              if self.route is not None else None)
        try:
            self.middleware_binding = _OriginalMiddlewareConfiguration(app.user_middleware,
                self.chain_types | {main.OwnedWorkAdmissionMiddleware}, (app, admission))
        except CurrentProjectObservationError:
            self.middleware_binding = None
            self.eligible = False

    def original(self):
        from backend.contracts.context import ContextRegistry
        return (self.eligible and self.pid == os.getpid() and self.admission._condition is self.condition
            and self.admission._activity_observer is self.observer and self.observer._original(self.admission)
            and self.app.state.owned_work_admission is self.admission and self.app.state.project_dir is self.root_object
            and Path(self.root_object).resolve() == self.root and self.app.state.context_registry is self.registry
            and type(self.registry) is ContextRegistry and self.registry.root is self.registry_binding[0]
            and self.registry.path is self.registry_binding[1]
            and all(_bound_function(self.registry, name) is function for name, function in self.registry_calls)
            and (self.app.state.accounts is None if self.shared is None else
                self.shared_original.__code__ is self.shared_original_code and self.shared_original(self.shared))
            and tuple(self.app.router.routes) == self.routes
            and self.app.router is self.router and type(self.router) is self.router_type
            and self.router_type.__call__ is self.router_call
            and getattr(self.router.__call__, '__self__', None) is self.router
            and getattr(self.router.__call__, '__func__', None) is self.router_call
            and (self.route.endpoint, self.route.dependant.call, self.route.app) == self.route_binding
            and type(self.route) is self.route_type
            and all(getattr(kind, name) is function for kind, name, function in self.route_class_calls)
            and all(_bound_function(self.route, name) is function for name, function in self.route_calls)
            and all(type(route) is kind and _bound_function(route, 'matches') is function
                for route, kind, function in self.match_prefix)
            and all(getattr(module, name) is function for module, name, function in self.functions)
            and all(kind.__call__ is function for kind, function in self.middleware_functions)
            and self.middleware_binding is not None and self.middleware_binding.original(self.app.user_middleware)
            and (self.included_current is None or self.included_current.original())
            and self.chain is not None and all(type(node) is kind and node.app is downstream
                and (project_app is None or node.project_app is project_app)
                for node, kind, downstream, project_app in self.chain))

    def bind_chain(self, middleware, downstream):
        from backend import main
        allowed = self.chain_types
        nodes, node = [], downstream
        while node is not self.router and hasattr(node, 'app') and len(nodes) < 10:
            if type(node) not in allowed:
                self.eligible = False; break
            nodes.append((node, type(node), node.app, getattr(node, 'project_app', None)))
            if type(node) is main.ProjectStorageScopeMiddleware: self.storage = node
            if self.shared is not None:
                from backend.api.shared_authorization import SharedAuthorizationMiddleware
                if type(node) is SharedAuthorizationMiddleware: self.shared.middleware = node
            if hasattr(node, 'project_app') and node.project_app is not self.app: self.eligible = False
            node = node.app
        self.chain = tuple(nodes)
        if (node is not self.router or self.app.router is not self.router or self.storage is None
                or middleware.app is not downstream or not self.original()): self.eligible = False

    def begin(self, activity):
        if (not self.original() or activity is None or not self.observer._valid(activity)
                or activity.ingress != {'type': 'http', 'method': 'GET', 'path': '/api/project/current'}
                or activity.scope.get('query_string') or activity.token not in self.observer._records
                or getattr(self.app.state, 'current_project', None) is not None
                or any((self.root / name).exists() for name in ('default_project', '.active_project.json', '.recent_projects.json'))
                or any(key.lower() in {b'x-vision-context', b'x-vision-project'} for key, _ in activity.scope.get('headers', ()))):
            return False
        if self.shared is not None and not self.shared.accepts(activity): return False
        attempt = _Attempt(self, activity)
        self.pending[attempt.token] = attempt
        self.records.append(attempt)
        self.admission._active += 1
        return True

    def find(self, activity):
        return next((attempt for attempt in self.pending.values() if attempt.activity is activity), None)

    def finish(self, activity):
        attempt = self.find(activity)
        if attempt is None: return
        row = self.observer._records.get(activity.token)
        if attempt.auth is not None:
            valid = (row is not None and row['carrier'] is activity and row['released']
                and attempt.auth.complete(row) and (self.deadline is None or time.monotonic() < self.deadline))
            if not valid:
                attempt.fail('shared_unconfirmed_or_late'); return
            del self.pending[attempt.token]
            self.admission._active -= 1
            attempt.settled = True
            return
        expected_files = {('labelset_json', self.root / 'default_project/labelsets.json'),
            ('project_json', self.root / 'default_project/project.json'),
            ('project_json', self.root / '.active_project.json'), ('project_json', self.root / '.recent_projects.json')}
        project = attempt.result
        state = activity.scope.get('state', {})
        files = [entry for entry in attempt.events if entry[0] in {'project_json', 'labelset_json'}]
        databases = [entry for entry in attempt.events if entry[0] == 'context_db']
        valid = (attempt.original() and not attempt.failed and not attempt.inflight
            and row is not None and row['carrier'] is activity and row['released'] and row['completed']
            and row['normal_return'] and attempt.handler == 1 and attempt.worker_done and attempt.storage == 1
            and len(attempt.resolutions) == 3 and all(result is project for result in attempt.resolutions)
            and type(project) is dict and project is state.get('scoped_project')
            and project is getattr(self.app.state, 'current_project', None) and state.get('account_user') is None
            and project.get('source_dataset_dir') is None and project.get('project_dir') == str(self.root / 'default_project')
            and len(files) == 4 and set(files) == expected_files and len(databases) == 3
            and all(registry is self.registry for _, registry in databases)
            and (self.deadline is None or time.monotonic() < self.deadline))
        if not valid:
            attempt.fail('unconfirmed_or_late'); return
        del self.pending[attempt.token]
        self.admission._active -= 1
        attempt.settled = True

    def snapshot(self):
        with self.condition:
            return {'pending': len(self.pending), 'records': [{'settled': attempt.settled, 'failed': attempt.failed,
                'handler_calls': attempt.handler, 'awaited_worker_return': attempt.worker_done,
                'storage_scopes': attempt.storage, 'resolution_calls': len(attempt.resolutions),
                'publications': sum(kind in {'project_json', 'labelset_json'} for kind, _ in attempt.events),
                'database_context_exits': sum(kind == 'context_db' for kind, _ in attempt.events)} for attempt in self.records],
                'whole_writer_coverage': False, 'process_tree_exit_verified': False, 'can_release_launch_lease': False}


def bind_current_project_request(app, admission):
    if admission is not None and admission._activity_observer is not None:
        admission._current_project_requests = OriginalCurrentProjectAdapter(app, admission)


@contextmanager
def current_project_request_scope(admission, activity):
    adapter = admission._current_project_requests
    attempt = adapter.find(activity) if adapter is not None else None
    if attempt is None:
        yield; return
    token = _CURRENT.set(attempt)
    try: yield
    finally: _CURRENT.reset(token)
