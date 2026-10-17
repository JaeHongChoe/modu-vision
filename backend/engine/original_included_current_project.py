"""One literal owned modern FastAPI inclusion; no general route flattening."""
from __future__ import annotations

import dataclasses
import sys
from types import MethodType, FunctionType


class IncludedCurrentProjectError(ValueError): pass


def _pin(value, seen=None, depth=0):
    if depth > 8: raise IncludedCurrentProjectError('Configuration depth')
    seen = set() if seen is None else seen
    kind = type(value)
    if kind not in (list, tuple, dict, set): return ('leaf', kind, value)
    if id(value) in seen or len(value) > 128: raise IncludedCurrentProjectError('Configuration cycle or size')
    seen.add(id(value))
    try:
        if kind is dict:
            children = tuple((_pin(k, seen, depth+1), _pin(v, seen, depth+1)) for k,v in value.items())
        elif kind is set:
            children = tuple(_pin(v, seen, depth+1) for v in sorted(value, key=id))
        else: children = tuple(_pin(v, seen, depth+1) for v in value)
        return ('container', kind, value, children)
    finally: seen.remove(id(value))


def _same(value, pin):
    if type(value) is not pin[1] or value is not pin[2]: return False
    if pin[0] == 'leaf': return True
    children = pin[3]
    if len(value) != len(children): return False
    if type(value) is dict:
        return all(_same(k, kp) and _same(v, vp) for (k,v),(kp,vp) in zip(value.items(),children))
    values = sorted(value,key=id) if type(value) is set else value
    return all(_same(v,p) for v,p in zip(values,children))


class _Fields:
    def __init__(self, owner, *, slots=False):
        self.owner, self.kind, self.slots = owner, type(owner), slots
        if slots:
            self.names = tuple(f.name for f in dataclasses.fields(owner))
            self.descriptors = tuple((n, vars(self.kind)[n]) for n in self.names)
            self.values = tuple((n,_pin(getattr(owner,n))) for n in self.names)
        else:
            self.fields = vars(owner)
            self.values = _pin(self.fields)
        from fastapi.datastructures import DefaultPlaceholder
        self.placeholders = tuple((v,type(v),v.value) for v in
            (getattr(owner,'default_response_class',None),getattr(owner,'generate_unique_id_function',None),
             getattr(owner,'response_class',None),getattr(owner,'strict_content_type',None))
            if type(v) is DefaultPlaceholder)

    def original(self):
        if type(self.owner) is not self.kind: return False
        if self.slots:
            if any(vars(self.kind).get(n) is not d for n,d in self.descriptors): return False
            if any(not _same(getattr(self.owner,n),p) for n,p in self.values): return False
        elif vars(self.owner) is not self.fields or not _same(self.fields,self.values): return False
        return all(type(v) is kind and v.value is original for v,kind,original in self.placeholders)


def _bound(owner,name,function):
    method = getattr(owner,name)
    return type(method) is MethodType and method.__self__ is owner and method.__func__ is function


class OriginalIncludedCurrentProject:
    """Observe real owned producers and the selected effective request context."""
    def __init__(self, app, admission, project_router, artifacts_router):
        from fastapi import routing as framework
        from fastapi.routing import APIRouter,APIRoute
        from starlette.routing import Route
        from backend.api import routes_project
        from backend import main
        required = ('_IncludedRouter','_RouterIncludeContext','_EffectiveRouteContext',
                    '_effective_route_context_var','_get_scope_effective_route_context',
                    '_get_scope_included_router','_get_fastapi_scope','_restore_fastapi_scope_key',
                    '_populate_api_route_state','get_request_handler','request_response','get_value_or_default',
                    'get_name','compile_path','is_body_allowed_for_status_code','create_model_field',
                    '_build_dependant_with_parameterless_dependencies','_get_body_field',
                    '_is_async_gen_callable','_is_gen_callable','lenient_issubclass',
                    'get_typed_return_annotation','get_stream_item_type')
        if any(name not in vars(framework) for name in required): raise IncludedCurrentProjectError('Not the known modern dispatcher')
        self.framework,self.app,self.admission = framework,app,admission
        self.condition,self.failed = admission._condition,False
        self.included_type,self.context_type = framework._IncludedRouter,framework._EffectiveRouteContext
        self.route_type,self.router_type = APIRoute,APIRouter
        if (project_router is not routes_project.router or project_router is not main.project_router
                or artifacts_router is not main.artifacts_router or type(app.router) is not APIRouter):
            raise IncludedCurrentProjectError('Unknown factory routers')
        branches = [r for r in app.router.routes if type(r) is self.included_type and r.original_router is project_router]
        if len(branches) != 1: raise IncludedCurrentProjectError('Unknown project inclusion')
        self.branch = branches[0]
        top = app.router.routes[:app.router.routes.index(self.branch)+1]
        self.top,self.top_rows = app.router.routes,tuple(top)
        self.top_all = tuple(app.router.routes)
        selected = [r for r in project_router.routes if type(r) is APIRoute and r.path == '/api/project/current']
        if len(selected)!=1 or selected[0].endpoint is not routes_project.get_current_project or selected[0].methods!={'GET'}:
            raise IncludedCurrentProjectError('Unknown original current endpoint')
        self.route,self.adapter,self.context,self.attempt = selected[0],None,None,None
        self.branches,self.direct = [],[]
        for node in top:
            if type(node) in (APIRoute,Route):
                self.direct.append((node,type(node),node.matches.__func__,node.handle.__func__))
                continue
            if type(node) is not self.included_type or not (node.original_router is project_router or node.original_router is artifacts_router):
                raise IncludedCurrentProjectError('Unknown preceding node')
            router,context = node.original_router,node.include_context
            if (type(router) is not APIRouter or type(context) is not framework._RouterIncludeContext
                    or context.included_router is not router or context.prefix != ''
                    or context.dependencies or context.callbacks or context.responses
                    or context.dependency_overrides_provider is not app
                    or node._effective_candidates or node._effective_candidates_version is not None
                    or node._effective_low_priority_routes or node._effective_low_priority_routes_version is not None
                    or router._low_priority_routes or router.dependencies or router.callbacks):
                raise IncludedCurrentProjectError('Nonfresh or configured original inclusion')
            if any(type(r) is not APIRoute for r in router.routes): raise IncludedCurrentProjectError('Unknown nested node')
            methods = tuple((n,getattr(self.included_type,n)) for n in
                ('matches','handle','_match','_handle_selected','effective_candidates','_build_effective_context'))
            router_methods = tuple((n,getattr(APIRouter,n)) for n in ('matches','handle','app','_get_routes_version'))
            if any(not _bound(node,n,f) for n,f in methods) or any(not _bound(router,n,f) for n,f in router_methods):
                raise IncludedCurrentProjectError('Substituted original dispatcher')
            rows = tuple((r,r.matches.__func__,r.handle.__func__,r.endpoint,r.dependant.call,r.app,
                          r.path,_pin(r.methods)) for r in router.routes)
            self.branches.append({'node':node,'router':router,'context':context,'context_fields':_Fields(context),
                'lock':node._effective_routes_lock,'routes':router.routes,'rows':rows,
                'router_fields':_Fields(router),'route_fields':tuple(_Fields(r) for r in router.routes),
                'handler_calls':tuple((r,r.get_route_handler.__func__) for r in router.routes),
                'version':router._get_routes_version(),'methods':methods,'router_methods':router_methods,
                'empty_cache':node._effective_candidates,'cache':None,'minted':[],
                'low_cache':node._effective_low_priority_routes,'producer':None})
        self.class_calls = tuple((kind,n,getattr(kind,n)) for kind,names in (
            (APIRoute,('matches','handle','get_route_handler')),(Route,('matches','handle')),
            (self.context_type,('matches','__init__','__new__')),
            (framework._RouterIncludeContext,('path_for',)),
            (APIRouter,('__call__','app','matches','handle','_get_routes_version')))
            for n in names)
        self.functions = tuple((n,getattr(framework,n)) for n in
            ('_get_scope_effective_route_context','_get_scope_included_router','_get_fastapi_scope',
             '_restore_fastapi_scope_key','_populate_api_route_state','get_request_handler','request_response',
             'get_value_or_default','get_name','compile_path','is_body_allowed_for_status_code',
             'create_model_field','_build_dependant_with_parameterless_dependencies','_get_body_field',
             '_is_async_gen_callable','_is_gen_callable','lenient_issubclass',
             'get_typed_return_annotation','get_stream_item_type'))
        self.producer = self.context_type.from_api_route.__func__
        self.context_var = framework._effective_route_context_var
        self.function_codes = tuple((f,f.__code__) for f in
            (self.producer,*(f for _,_,f in self.class_calls),*(f for _,f in self.functions))
            if type(f) is FunctionType)
        self.context_fields,self.dependant_fields = None,None
        self.owned_scope = None
        self.observation_binding = None
        self.selected_once = False

    def fail(self,reason):
        self.failed = True
        with self.condition:
            self.admission._unsupported.add('uncovered_request_protocol')
            self.admission._activity_observer.poison('included_current_'+reason)

    def install(self,matched,adapter):
        self.adapter = adapter
        for branch in self.branches:
            node = branch['node']
            original = self.included_type._build_effective_context.__get__(node,self.included_type)
            def observed(owner,route,*,branch=branch,original=original):
                from backend.engine.original_current_project_request import _CURRENT
                attempt = _CURRENT.get()
                from backend.engine.original_owned_account_request import original_owned_login_production
                login_production = (attempt is None and original_owned_login_production(self.app,self.admission))
                # Cache provenance is independent of request admission. Only
                # the real original effective_candidates loop may mint a row.
                producer_frame = sys._getframe(1)
                live_rows = producer_frame.f_locals.get('effective_candidates')
                live_producer = (producer_frame.f_code is self.included_type.effective_candidates.__code__
                    and producer_frame.f_locals.get('self') is owner
                    and producer_frame.f_locals.get('route') is route
                    and type(live_rows) is list and len(live_rows) == len(branch['minted'])
                    and all(a is b for a,b in zip(live_rows,branch['minted'])))
                # Reject observation authority before invoking a changed producer
                # callee. Original business dispatch still runs exactly once.
                with self.condition:
                    if not self.original(): self.fail('changed_original_producer_callee')
                try: context = original(route)
                except BaseException:
                    self.fail('producer_failed'); raise
                with self.condition:
                    if (self.failed or not live_producer or (attempt is None and not login_production)
                            or (attempt is not None and (attempt.adapter is not adapter or not attempt.original()))
                            or owner is not branch['node'] or len(branch['minted']) >= len(branch['rows'])
                            or route is not branch['rows'][len(branch['minted'])][0]
                            or type(context) is not self.context_type or context.original_route is not route):
                        self.fail('unknown_original_production'); return context
                    branch['minted'].append(context)
                    if route is self.route:
                        if self.context is not None or context.endpoint is not route.endpoint or context.dependant.call is not route.endpoint:
                            self.fail('duplicate_or_foreign_context'); return context
                        self.context = context
                        # Same original producer result, never the shared module route.
                        context.dependant.call = matched
                        token = self.context_var.set(context)
                        try: context.app = self.framework.request_response(route.get_route_handler())
                        finally: self.context_var.reset(token)
                        self.context_fields = _Fields(context)
                        self.dependant_fields = _Fields(context.dependant,slots=True)
                return context
            branch['producer'] = MethodType(observed,node)
            node._build_effective_context = branch['producer']

    def original(self):
        from backend.api import routes_project
        from backend import main
        if (self.failed or self.app.router.routes is not self.top or len(self.top)!=len(self.top_all)
                or any(a is not b for a,b in zip(self.top,self.top_all))
                or self.branch.original_router is not routes_project.router
                or main.project_router is not routes_project.router
                or self.context_var is not self.framework._effective_route_context_var
                or self.context_type.from_api_route.__func__ is not self.producer
                or any(getattr(kind,n) is not f for kind,n,f in self.class_calls)
                or any(getattr(self.framework,n) is not f for n,f in self.functions)
                or any(f.__code__ is not code for f,code in self.function_codes)):
            return False
        for node,kind,matches,handle in self.direct:
            if type(node) is not kind or not _bound(node,'matches',matches) or not _bound(node,'handle',handle):return False
        for b in self.branches:
            node,router = b['node'],b['router']
            if (type(node) is not self.included_type or node.original_router is not router
                    or node.include_context is not b['context'] or not b['context_fields'].original()
                    or not b['router_fields'].original() or not all(fields.original() for fields in b['route_fields'])
                    or any(not _bound(r,'get_route_handler',f) for r,f in b['handler_calls'])
                    or node._effective_routes_lock is not b['lock'] or router.routes is not b['routes']
                    or len(router.routes)!=len(b['rows'])
                    or node._build_effective_context is not b['producer']
                    or any(getattr(self.included_type,n) is not f or (n!='_build_effective_context' and not _bound(node,n,f)) for n,f in b['methods'])
                    or any(getattr(self.router_type,n) is not f or not _bound(router,n,f) for n,f in b['router_methods'])
                    or node._effective_low_priority_routes is not b['low_cache'] or node._effective_low_priority_routes
                    or node._effective_low_priority_routes_version is not None):return False
            current_version = router._get_routes_version()
            if type(current_version) is not int or current_version != b['version']:return False
            for current,(route,matches,handle,endpoint,call,app,path,methods) in zip(router.routes,b['rows']):
                if (current is not route or type(route) is not self.route_type
                        or not _bound(route,'matches',matches) or not _bound(route,'handle',handle)
                        or route.endpoint is not endpoint or route.dependant.call is not call or route.app is not app
                        or route.path is not path or not _same(route.methods,methods)):return False
            cache = node._effective_candidates
            if cache is b['empty_cache'] and node._effective_candidates_version is None:
                if cache:return False
            else:
                if (type(cache) is not list or type(node._effective_candidates_version) is not int
                        or node._effective_candidates_version != b['version']
                        or len(cache)!=len(b['rows']) or len(cache)!=len(b['minted'])
                        or any(a is not c for a,c in zip(cache,b['minted']))):return False
                if b['cache'] is None:b['cache']=cache
                elif cache is not b['cache']:return False
        if self.observation_binding is None:
            return (self.attempt is None and self.owned_scope is None and not self.selected_once
                and (self.context is None and self.context_fields is None and self.dependant_fields is None
                    or self.context is not None and self.context_fields is not None and self.dependant_fields is not None
                    and self.context_fields.original() and self.dependant_fields.original()))
        context,attempt,activity,scope,token = self.observation_binding
        return (self.context is context and self.context is self.context_fields.owner
                and self.context.dependant is self.dependant_fields.owner
                and self.attempt is attempt and attempt.activity is activity and activity.token is token
                and self.owned_scope is scope and activity.scope is scope
                and self.context_fields.original() and self.dependant_fields.original()
                and (not self.selected_once or (self.framework._get_scope_effective_route_context(scope) is context
                    and self.framework._get_scope_included_router(scope) is self.branch
                    and scope.get('route') is self.route
                    and type(scope.get('query_string')) is bytes and scope['query_string']==b'')))

    def selected(self,scope,attempt):
        from backend.engine.original_owned_account_request import _SelectedCurrentAuth
        previous = self.attempt
        row = (self.admission._activity_observer._records.get(previous.activity.token)
               if previous is not None else None)
        # The shared effective route remains the original producer's object.
        # Only completed typed bearer requests may receive a fresh per-request
        # binding; the original desktop one-request protocol stays unchanged.
        repeat = (previous is not None and previous is not attempt
            and type(previous.auth) is _SelectedCurrentAuth and type(attempt.auth) is _SelectedCurrentAuth
            and previous.auth.owner is attempt.auth.owner
            and previous.settled and not previous.failed and not previous.inflight
            and previous.token not in self.adapter.pending and previous.worker_done
            and previous.handler==1 and previous.storage==1
            and row is not None and row['carrier'] is previous.activity
            and row['released'] and row['completed'] and row['normal_return']
            and row['response_status']==200 and not row['transport_failed'] and not row['framing_error']
            and row['send_attempt'] is None and row['start_forwarded'] and row['terminal_forwarded']
            and not row['trailers_expected'])
        valid = (self.original() and self.context is not None
                and (self.attempt is None or self.attempt is attempt or repeat)
                and scope is attempt.activity.scope
                and self.framework._get_scope_effective_route_context(scope) is self.context
                and self.framework._get_scope_included_router(scope) is self.branch
                and scope.get('route') is self.route
                and self.context.original_route is self.route and self.context.path=='/api/project/current'
                and self.context.methods=={'GET'} and self.context.dependencies==[]
                and self.context.callbacks==[] and self.context.dependency_overrides_provider is self.app
                and all(b['cache'] is not None for b in self.branches))
        if valid:
            self.attempt,self.owned_scope,self.selected_once = attempt,scope,True
            self.observation_binding = (self.context,attempt,attempt.activity,scope,attempt.activity.token)
        return valid
