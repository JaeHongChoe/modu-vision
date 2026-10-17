"""Original saved-flow/model-read HTTP lifetimes; no run/export/tree authority."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import builtins
import inspect
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from types import FunctionType, MethodType

from backend.engine.original_request_activity import _context
from backend.engine.original_owned_account_request import _headers, _SelectedCurrentAuth

_ACTIVE = ContextVar('original_owned_flow_request', default=None)
_HANDLERS = {}
_SPEC = {
    'list_saved_pipelines':('GET','/api/flowchart/pipelines'),
    'get_active_pipeline_record':('GET','/api/flowchart/pipeline/active/record'),
    'get_saved_pipeline_version':('GET','/api/flowchart/pipelines/{version_id}'),
    'save_pipeline':('POST','/api/flowchart/pipeline'),
    'catalog_flowchart_models':('GET','/api/flowchart/models/catalog'),
    'verify_flowchart_models':('POST','/api/flowchart/models/verify'),
}


def _methods_original(instance,kind,calls):
    return (type(instance) is kind and all(type(getattr(instance,name,None)) is MethodType
        and getattr(instance,name).__self__ is instance and getattr(instance,name).__func__ is function
        for owner,name,function in calls if owner is kind))


def _audit_store_original(owner,store):
    from backend.engine.config_audit import ConfigAuditStore
    return (type(store) is ConfigAuditStore and all(
        (getattr(store,name,None) is function if name=='_public' else
         type(getattr(store,name,None)) is MethodType and getattr(store,name).__self__ is store
         and getattr(store,name).__func__ is function)
        for kind,name,function in owner.calls if kind is ConfigAuditStore))


def observe_original_flow_handler(register):
    """Register the async observer initially, retaining the original sync API."""
    def decorate(function):
        if function.__name__ not in _SPEC: raise ValueError('Unknown original saved-flow handler')
        @wraps(function)
        async def observed(**values):
            from starlette.concurrency import run_in_threadpool
            attempt = _ACTIVE.get()
            if type(attempt) is not _FlowAttempt:
                return await run_in_threadpool(function, **values)
            valid = attempt.activate(values, observed, function)
            def invoke():
                attempt.enter_worker(valid)
                try: result = function(**values)
                except BaseException:
                    attempt.fail('handler_failed')
                    raise
                else:
                    attempt.returned(result)
                    return result
                finally: attempt.worker_live = False
            return await run_in_threadpool(invoke)
        observed.__signature__ = inspect.signature(function, eval_str=True)
        register(observed)
        _HANDLERS[function.__name__] = observed,function
        return function
    return decorate


def observe_original_flow_model_resolution(function):
    """Observe only the original two handlers' scoped checkpoint resolver."""
    @wraps(function)
    def observed(*args,**kwargs):
        attempt=_ACTIVE.get()
        if (type(attempt) is not _FlowAttempt or attempt.name not in
                {'catalog_flowchart_models','verify_flowchart_models'}):return function(*args,**kwargs)
        valid=attempt.model_entry(args,kwargs)
        try:result=function(*args,**kwargs)
        except BaseException:
            attempt.fail('model_resolution_failed');raise
        attempt.model_return(result,valid)
        return result
    return observed


def observe_original_flow_specialist_resolution(function):
    """Observe the original retained-rotation resolver inside the two reads."""
    @wraps(function)
    def observed(*args,**kwargs):
        attempt=_ACTIVE.get()
        if (type(attempt) is not _FlowAttempt or attempt.name not in
                {'catalog_flowchart_models','verify_flowchart_models'}):
            from backend.engine.original_owned_selected_compute_request import selected_source_resolution
            return selected_source_resolution(function,args,kwargs,'specialist')
        valid=attempt.specialist_entry(args,kwargs)
        try:result=function(*args,**kwargs)
        except BaseException:
            attempt.fail('specialist_resolution_failed');raise
        attempt.specialist_return(result,valid)
        return result
    return observed


def observe_original_flow_retained_source(function):
    """Bind the original registry callback to the live six-pin resolver."""
    @wraps(function)
    def observed(*args,**kwargs):
        attempt=_ACTIVE.get()
        if (type(attempt) is not _FlowAttempt or attempt.name not in
                {'catalog_flowchart_models','verify_flowchart_models'}):
            from backend.engine.original_owned_selected_compute_request import selected_source_resolution
            return selected_source_resolution(function,args,kwargs,'retained')
        valid=attempt.retained_entry(args,kwargs)
        try:result=function(*args,**kwargs)
        except BaseException:
            attempt.fail('retained_source_failed');raise
        attempt.retained_return(result,valid)
        return result
    return observed


def observe_original_flow_locked(function):
    @wraps(function)
    def observed(*args, **kwargs):
        attempt = _ACTIVE.get()
        if type(attempt) is not _FlowAttempt: return function(*args,**kwargs)
        valid = attempt.worker_original() and attempt.handler_frame() is not None
        held = attempt.owner.lock._is_owned()
        if not valid or (function.__name__ != '_project_dir' and not held): attempt.fail('lock_entry')
        try: result = function(*args,**kwargs)
        except BaseException:
            attempt.fail('locked_helper_failed')
            raise
        if not valid or not attempt.worker_original() or attempt.owner.lock._is_owned() != held:
            attempt.fail('lock_return')
        elif held: attempt.locked.append(function.__name__)
        return result
    return observed


def observe_original_flow_publication(function):
    @wraps(function)
    def observed(path,value):
        attempt = _ACTIVE.get()
        if type(attempt) is not _FlowAttempt: return function(path,value)
        valid = attempt.publication_entry(path,value)
        try: result = function(path,value)
        except BaseException:
            attempt.fail('publication_failed')
            raise
        attempt.publication_exit(path,value,valid)
        return result
    return observed


def observe_original_flow_audit_connection(function):
    @wraps(function)
    def observed(store):
        attempt = _ACTIVE.get()
        if type(attempt) is not _FlowAttempt: return function(store)
        try: connection = function(store)
        except BaseException:
            attempt.fail('audit_connection_failed')
            raise
        attempt.audit_connection(store,connection)
        return connection
    return observed


def observe_original_flow_audit_append(function):
    @wraps(function)
    def observed(store,**values):
        attempt = _ACTIVE.get()
        if type(attempt) is not _FlowAttempt: return function(store,**values)
        valid = attempt.audit_entry(store,values)
        try: result = function(store,**values)
        except BaseException:
            attempt.fail('audit_transaction_failed')
            raise
        attempt.audit_exit(store,values,result,valid)
        return result
    return observed


class _FlowIncluded:
    """Only this factory's literal flow inclusion and original cache producer."""
    def __init__(self,owner,branch):
        from fastapi import routing
        from backend.engine.original_included_current_project import _Fields
        self.owner,self.branch,self.framework = owner,branch,routing
        self.kind,self.router,self.context = type(branch),branch.original_router,branch.include_context
        self.fields,self.router_fields = _Fields(self.context),_Fields(self.router)
        self.routes,self.rows = self.router.routes,tuple(self.router.routes)
        self.route_fields = tuple(_Fields(route) for route in self.rows)
        self.version,self.lock = self.router._get_routes_version(),branch._effective_routes_lock
        if (branch._effective_candidates or branch._effective_candidates_version is not None
                or branch._effective_low_priority_routes or branch._effective_low_priority_routes_version is not None):
            raise ValueError('Original flow inclusion is not fresh')
        self.empty,self.low = branch._effective_candidates,branch._effective_low_priority_routes
        self.minted,self.contexts,self.cache = [],[],None
        self.calls = tuple((name,getattr(self.kind,name)) for name in (
            'effective_candidates','_build_effective_context','matches','handle','_match','_handle_selected'))
        self.codes=tuple((function,function.__code__) for _,function in self.calls)
        self.original_producer = self.kind._build_effective_context
        def observed(node,route):
            from backend.engine.original_owned_account_request import original_owned_login_production
            from backend.engine.original_owned_flow_execution_request import original_owned_execution_production
            from backend.engine.original_owned_UI_ancillary_request import original_owned_UI_ancillary_production
            from backend.engine.original_owned_telemetry_socket_request import original_owned_telemetry_production
            frame = sys._getframe(1)
            rows = frame.f_locals.get('effective_candidates')
            attempt = _ACTIVE.get()
            owned = (type(attempt) is _FlowAttempt and attempt.owner is owner and attempt.original()
                     or original_owned_login_production(owner.app,owner.admission)
                     or original_owned_execution_production(owner.app,owner.admission,owner)
                     or original_owned_UI_ancillary_production(owner.app,owner.admission)
                     or original_owned_telemetry_production(owner.app,owner.admission))
            valid = (self.original() and owned and node is branch
                and frame.f_code is self.kind.effective_candidates.__code__
                and frame.f_locals.get('self') is branch and frame.f_locals.get('route') is route
                and type(rows) is list and len(rows)==len(self.minted)
                and all(a is b for a,b in zip(rows,self.minted))
                and len(self.minted)<len(self.rows) and route is self.rows[len(self.minted)])
            context = self.original_producer(node,route)
            if (not valid or context.original_route is not route
                    or context.endpoint is not route.endpoint or context.dependant.call is not route.endpoint):
                owner.fail('foreign_flow_production')
            else:
                self.minted.append(context)
                self.contexts.append((_Fields(context),_Fields(context.dependant,slots=True)))
            return context
        self.producer = MethodType(observed,branch)
        self.producer_function,self.producer_code=observed,observed.__code__
        branch._build_effective_context = self.producer

    def original(self):
        node = self.branch
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
                or not all(a.original() and b.original() for a,b in self.contexts)):
            return False
        cache = node._effective_candidates
        if cache is self.empty and node._effective_candidates_version is None: return not cache
        valid = (type(cache) is list and node._effective_candidates_version==self.version
            and len(cache)==len(self.rows)==len(self.minted)
            and all(a is b for a,b in zip(cache,self.minted)))
        if valid and self.cache is None: self.cache=cache
        return valid and cache is self.cache

    def selected(self,scope,route,dependant):
        context = self.framework._get_scope_effective_route_context(scope)
        return (self.original() and any(context is row for row in self.minted)
            and context.original_route is route and context.endpoint is route.endpoint
            and context.dependant is dependant and dependant.call is route.endpoint
            and self.framework._get_scope_included_router(scope) is self.branch)


class OriginalOwnedFlowRequest:
    def __init__(self,app,admission):
        from backend.api import routes_flowchart as flows, routes_project, routes_evaluation, routes_dataset, shared_authorization
        from backend.contracts.context import ContextRegistry
        from backend.engine import (config_audit, original_current_project_request, shared_accounts,
            annotation_storage, checkpoint_paths, dataset_fingerprint, retained_preprocessors,
            specialized_models, rotation)
        from backend.api import routes_dataset_versions
        from backend.engine import original_owned_flow_execution_request
        from backend.engine import original_owned_UI_ancillary_request
        from backend.engine import original_owned_telemetry_socket_request
        from backend import main
        from fastapi import routing
        from starlette import concurrency, routing as starlette_routing
        self.app,self.admission,self.accounts = app,admission,admission._account_requests
        self.account_kind=type(self.accounts)
        self.manager,self.manager_kind=flows.training_job_manager,type(flows.training_job_manager)
        self.retained_constants=(retained_preprocessors.SCHEMA,retained_preprocessors.DIRECTORY,
            frozenset(retained_preprocessors._PIN_NAMES))
        self.observer,self.condition = admission._activity_observer,admission._condition
        self.registry,self.storage = self.accounts.named.registry,self.accounts.named.storage
        self.registry_binding=self.registry.root,self.registry.path
        self.registry_calls=self.accounts.named.registry_calls
        self.lock,self.pid,self.deadline = flows._FLOW_SAVE_LOCK,os.getpid(),None
        self.lock_type=type(threading.RLock())
        self.pending,self.records,self.failed = {},[],False
        self.binding = self,app,admission,self.accounts,self.observer,self.condition,self.registry,self.storage,self.lock
        self.handlers = dict(_HANDLERS)
        self.spec=dict(_SPEC)
        self.route_kind=routing.APIRoute
        self.routes = {}
        modern = getattr(routing,'_IncludedRouter',None)
        branches = [node for node in app.router.routes if modern is not None and type(node) is modern
                    and node.original_router is flows.router]
        self.included = _FlowIncluded(self,branches[0]) if len(branches)==1 else None
        selected_routes = flows.router.routes if self.included is not None else app.router.routes
        for name,(endpoint,function) in self.handlers.items():
            method,path = _SPEC[name]
            rows = [route for route in selected_routes if getattr(route,'path',None)==path
                    and getattr(route,'methods',None)=={method} and route.endpoint is endpoint
                    and route.dependant.call is endpoint]
            if len(rows)!=1: raise ValueError('Unknown original saved-flow route')
            self.routes[name] = rows[0]
        self.route_bindings={name:(route.endpoint,route.dependant,route.dependant.call,route.app,
            route.get_route_handler.__func__) for name,route in self.routes.items()}
        self.dispatch = routing.run_endpoint_function
        self.calls = [(module,name,getattr(module,name)) for module,names in (
            (flows, ('get_current_project','_FLOW_SAVE_LOCK','_project_dir','_get_active_pipeline_unlocked','_list_saved_pipelines_unlocked',
                '_write_json','_write_pipeline','_restore_flow_file','_save_version','_recipe_file','_version_dir',
                '_active_flow_file','_active_version','_audit_change','_stale','_valid_expected_version',
                'ordered_linear_nodes','validate_recorded_flow_classes','_save_class_model_metadata',
                '_resolve_job_artifacts','_catalog_model_settings','training_job_manager',
                'FlowchartModelVerificationRequest','FlowchartModelReference','request_shared_scope',
                '_resolve_project_specialist','_registered_retained_source','resolve_specialized_checkpoint',
                'valid_flow_job','SPECIALIZED_TASKS')),
            (retained_preprocessors, ('resolve_retained_rotation','retained_rotation_jobs','read_registered_project',
                '_source_model','_location','_target','_path','_pin','_json','_exact','_raw','_require',
                'SCHEMA','DIRECTORY','_PIN_NAMES')),
            (specialized_models, ('resolve_specialized_checkpoint','valid_flow_job','require_completed_checkpoint',
                '_verify_historical_source','specialized_dataset_provenance')),
            (rotation, ('load_rotation_manifest',)),
            (routes_dataset_versions, ('_manifest_digest','_file_hash','_safe_backup_path')),
            (flows.torch, ('load',)),
            (routes_evaluation, ('_resolve_job_artifacts','_record_for_selected_checkpoint','_matches_source_dataset',
                'active_project_models_dir','completed_job_receipt','trusted_checkpoint','fingerprint_dataset',
                'training_job_manager')),
            (checkpoint_paths, ('active_project_models_dir','completed_job_receipt','trusted_job_dir','trusted_checkpoint','is_job_id')),
            (dataset_fingerprint, ('fingerprint_dataset','_files_under','_update_file','request_project_root')),
            (routes_dataset, ('_split_manifest_file','STUDIO_ANNOTATIONS_DIR')),
            (annotation_storage, ('request_project_root','request_shared_scope')),
            (self.manager_kind, ('get_job',)), (Path, ('open','read_text','read_bytes')),
            (original_owned_telemetry_socket_request, ('original_owned_telemetry_production',)),
            (config_audit.ConfigAuditStore, ('__init__','append','_connect','_public')),
            (config_audit, ('ConfigAuditStore','sqlite3','closing','_row_hash','actor_from_request','observed_runtime_release')),
            (shared_accounts.AccountStore, ('record_decision',)),
            (self.account_kind, ('original',)),
            (ContextRegistry, ('_db','transaction','register_project','context')),
            (routes_project, ('get_current_project',)), (main, ('get_current_project',)),
            (shared_authorization, ('permission_action','declared_context') if hasattr(shared_authorization,'declared_context') else ('permission_action',)),
            (routing, ('APIRoute','run_endpoint_function','get_request_handler','request_response')),
            (routing.APIRoute, ('matches','handle')),
            (starlette_routing, ('Route',)), (starlette_routing.Route, ('matches','handle')),
            (concurrency, ('run_in_threadpool',)),
            (original_current_project_request, ('observe_resolution','observe_context_db','observe_storage_scope','_CURRENT')),
            (original_owned_flow_execution_request, ('original_owned_execution_production',)),
            (original_owned_UI_ancillary_request, ('original_owned_UI_ancillary_production',)),
            (main.OwnedWorkAdmissionMiddleware, ('__call__','_observed_call')),
            (os, ('replace','fsync','getpid')), (tempfile, ('NamedTemporaryFile',)),
            (builtins, ('open',)), (json, ('load','dump','loads','dumps')),
            (sys.modules[__name__], ('_ACTIVE','_HANDLERS','_SPEC','_context','_headers','_SelectedCurrentAuth',
                'OriginalOwnedFlowRequest','_FlowAttempt','_FlowAuth','_FlowIncluded')),
        ) for name in names]
        for name,(endpoint,function) in self.handlers.items(): self.calls.append((flows,name,function))
        for name,value in tuple(globals().items()):
            if type(value) is FunctionType and value.__module__==__name__: self.calls.append((sys.modules[__name__],name,value))
        for kind in (OriginalOwnedFlowRequest,_FlowAttempt,_FlowAuth,_FlowIncluded):
            for name in dir(kind):
                value=getattr(kind,name)
                if type(value) is FunctionType: self.calls.append((kind,name,value))
        self.codes=[]
        self.codes.extend((endpoint,endpoint.__code__) for endpoint,_ in self.handlers.values())
        for _,_,value in self.calls:
            for _ in range(5):
                if hasattr(value,'__code__'): self.codes.append((value,value.__code__))
                value=getattr(value,'__wrapped__',None)
                if value is None: break
        def nested(code):
            result={code}
            for child in code.co_consts:
                if type(child) is type(code): result.update(nested(child))
            return result
        self.handler_codes,self.response_codes = nested(routing.get_request_handler.__code__),nested(routing.request_response.__code__)
        self.eligible = (set(self.handlers)==set(_SPEC) and type(self.lock) is self.lock_type
            and all(type(route) is self.route_kind for route in self.routes.values())
            and flows._resolve_job_artifacts.__wrapped__ is routes_evaluation._resolve_job_artifacts
            and type(self.registry) is ContextRegistry
            and flows.get_current_project is routes_project.get_current_project is main.get_current_project
            and routes_project.get_current_project is original_current_project_request._DECORATED.get('resolution')
            and ContextRegistry._db is original_current_project_request._DECORATED.get('context_db'))

    def bind_chain(self,middleware,downstream):
        self.owned,self.downstream = middleware,downstream
        if (self.admission._account_requests is not self.accounts
                or getattr(self.accounts,'owned',None) is not middleware
                or getattr(self.accounts,'downstream',None) is not downstream):
            self.eligible=False;return
        if self.storage is None and not self.pending and not self.records:
            self.storage=self.accounts.named.storage
            self.binding=self,self.app,self.admission,self.accounts,self.observer,self.condition,self.registry,self.storage,self.lock

    def fail(self,reason):
        self.failed=True
        self.admission._unsupported.add('uncovered_request_protocol')
        self.observer.poison('owned_flow_'+reason)

    def __copy__(self):
        self.fail('owner_copy')
        raise ValueError('Original flow request owner cannot be copied')
    def __deepcopy__(self,memo): return self.__copy__()
    def __reduce_ex__(self,protocol): return self.__copy__()

    def original(self):
        from backend.contracts.context import ContextRegistry
        from backend.engine import retained_preprocessors as retained
        return (self.eligible and not self.failed and self.pid==os.getpid()
            and self.retained_constants==(retained.SCHEMA,retained.DIRECTORY,frozenset(retained._PIN_NAMES))
            and all(a is b for a,b in zip(self.binding,(self,self.app,self.admission,self.accounts,self.observer,
                self.condition,self.registry,self.storage,self.lock)))
            and self.admission._flow_requests is self
            and _methods_original(self.accounts,self.account_kind,self.calls) and self.accounts.original()
            and _methods_original(self.manager,self.manager_kind,self.calls)
            and any(kind is type(self.accounts.store) and name=='record_decision' for kind,name,_ in self.calls)
            and all(type(getattr(self.accounts.store,name,None)) is MethodType
                and getattr(self.accounts.store,name).__self__ is self.accounts.store
                and getattr(self.accounts.store,name).__func__ is function
                for kind,name,function in self.calls if kind is type(self.accounts.store))
            and _methods_original(self,OriginalOwnedFlowRequest,self.calls)
            and self.app.state.context_registry is self.registry and type(self.registry) is ContextRegistry
            and self.registry.root is self.registry_binding[0] and self.registry.path is self.registry_binding[1]
            and all(type(getattr(self.registry,name,None)) is MethodType
                and getattr(self.registry,name).__self__ is self.registry
                and getattr(self.registry,name).__func__ is function for name,function in self.registry_calls)
            and self.owned is self.accounts.owned and self.owned.app is self.downstream
            and self.storage is self.accounts.named.storage and type(self.lock) is self.lock_type
            and _HANDLERS==self.handlers and _SPEC==self.spec and all(getattr(owner,name) is value for owner,name,value in self.calls)
            and all(value.__code__ is code for value,code in self.codes)
            and all(endpoint.__wrapped__ is function for endpoint,function in self.handlers.values())
            and (self.included is None or _methods_original(self.included,_FlowIncluded,self.calls)
                and self.included.original())
            and all(_methods_original(route,self.route_kind,self.calls)
                and route.endpoint is self.handlers[name][0] and route.dependant.call is self.handlers[name][0]
                and route.path==_SPEC[name][1] and route.methods=={_SPEC[name][0]}
                and (route.endpoint,route.dependant,route.dependant.call,route.app,route.get_route_handler.__func__)==self.route_bindings[name]
                for name,route in self.routes.items()))

    def begin(self,activity):
        if (activity is None or not self.original() or not self.observer._valid(activity)
                or activity.ingress.get('type')!='http' or activity.scope.get('app') is not self.app
                or len(self.records)>=128): return False
        method,path=activity.ingress.get('method'),activity.ingress.get('path')
        name=next((name for name,(m,p) in _SPEC.items() if method==m and (path==p
            or name=='get_saved_pipeline_version' and re.fullmatch(r'/api/flowchart/pipelines/[0-9a-f]{32}',path or ''))),None)
        headers=_headers(activity.scope)
        if name is None or headers is None:return False
        tokens=[v for k,v in headers if k.lower()==b'authorization']
        projects=[v for k,v in headers if k.lower()==b'x-vision-project']
        contexts=[v for k,v in headers if k.lower()==b'x-vision-context']
        if (len(tokens)!=1 or not tokens[0].startswith(b'Bearer ') or not 7<len(tokens[0])<=263
                or len(projects)!=1 or not projects[0] or len(contexts)>1
                or any(not v or len(v)>4096 for v in contexts) or any(k.lower()==b'cookie' for k,_ in headers)):return False
        try: token,project=tokens[0][7:].decode('ascii'),projects[0].decode('ascii')
        except UnicodeError:return False
        attempt=_FlowAttempt(self,activity,name,headers,project,token)
        self.pending[activity.token]=attempt;self.records.append(attempt);self.admission._active+=1
        return True

    def finish(self,activity):
        attempt=self.pending.get(activity.token) if activity is not None else None
        if type(attempt) is not _FlowAttempt:return
        if (not _methods_original(attempt,_FlowAttempt,self.calls)
                or not _methods_original(attempt.auth,_FlowAuth,self.calls)):
            _FlowAttempt.fail(attempt,'changed_attempt_methods');return
        if not attempt.complete(self.observer._records.get(activity.token)):
            attempt.fail('unconfirmed_lifetime');return
        del self.pending[activity.token];self.admission._active-=1;attempt.settled=True


class _FlowAuth(_SelectedCurrentAuth):
    def __init__(self,attempt,headers,project,token):
        super().__init__(attempt.owner.accounts,attempt,headers,project,token)
        from backend.api.shared_authorization import permission_action
        self.action=permission_action(attempt.activity.ingress['path'],attempt.activity.ingress['method'])

    def begin_db(self,store):
        names=('bind_workspace','authenticate','project_role','project_for','project_role','authorize','record_decision')
        source=self.attempt.source_live if self.attempt.worker_live else None
        if source is not None:
            index=len(self.databases)-source['database_start']
            expected=('authorize','project_role','project_for')[index] if 0<=index<3 else None
        else:expected=names[len(self.databases)] if len(self.databases)<len(names) else None
        function=getattr(type(store),expected,None) if type(expected) is str else None
        function=getattr(function,'__wrapped__',function)
        frame=sys._getframe(1)
        for _ in range(10):
            if frame is None or function is not None and frame.f_code is function.__code__:break
            frame=frame.f_back
        valid=(self.original() and store is self.owner.store and not self.db_live and self.middleware_calls==1
            and not self.middleware_returns and frame is not None
            and function is not None and frame.f_code is function.__code__ and frame.f_locals.get('self') is store)
        if source is not None:
            valid=(valid and self.attempt.worker_original() and self.attempt.retained_frame() is not None
                and self.attempt.retained_source_frame() is not None)
            project_id=source['source_id'];action='flow.execute'
        else:
            valid=valid and not self.attempt.worker_live and self._authorization_frame() is not None
            project_id=self.project_id;action=self.action
        if expected in {'project_role','project_for'}:
            valid=(valid and type(self.account) is dict and frame.f_locals.get('user_id')==self.account.get('id')
                and frame.f_locals.get('project_id')==project_id)
        elif expected=='authorize':
            valid=(valid and type(self.account) is dict and frame.f_locals.get('actor')==self.account.get('id')
                and frame.f_locals.get('project')==project_id and frame.f_locals.get('session_token')==self.token
                and frame.f_locals.get('action')==action)
        elif expected=='record_decision':
            decision=self.attempt.activity.scope.get('state',{}).get('permission_decision')
            valid=(valid and self.attempt.name in {'save_pipeline','verify_flowchart_models'}
                and frame.f_locals.get('decision') is decision
                and decision is not None and decision.allowed and decision.action==self.action)
        if not valid:self.fail('database_entry')
        self.db_live=True
        return valid,expected


class _FlowAttempt:
    def __init__(self,owner,activity,name,headers,project,token):
        self.owner=self.adapter=owner;self.activity,self.name,self.headers=activity,name,headers
        self.token=object();self.binding=self,owner,activity,activity.token,self.token
        self.scope=activity.scope;self.query=self.scope.get('query_string',b'')
        self.header_container=None
        self.failed=self.settled=self.worker_live=self.handler_done=False
        self.entries=0;self.worker=None;self.values=None;self.request=None
        self.events=[];self.inflight={};self.resolutions=[];self.storage=0
        self.locked=[];self.publications=[];self.saved_version=None
        self.audit_store=self.audit_db=self.audit_result=None
        self.audit_live=False;self.result=None
        self.model_live=None;self.model_calls=[];self.model_body=None
        self.specialist_live=self.source_live=None;self.retained_sources=[]
        self.auth=_FlowAuth(self,headers,project,token)

    def fail(self,reason):
        self.failed=True
        self.owner.admission._unsupported.add('uncovered_request_protocol')
        self.owner.observer.poison('owned_flow_'+reason)
    def __copy__(self):
        self.fail('attempt_copy');raise ValueError('Original flow lifetime cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):return self.__copy__()

    def base(self):
        return (not self.settled and self.binding==(self,self.owner,self.activity,self.activity.token,self.token)
            and self.owner.pending.get(self.activity.token) is self and self.owner.original()
            and _methods_original(self,_FlowAttempt,self.owner.calls)
            and _methods_original(self.auth,_FlowAuth,self.owner.calls)
            and (self.audit_store is None or _audit_store_original(self.owner,self.audit_store))
            and (self.model_body is None or self.model_body_original())
            and (self.values is None or self.values.get(
                'http_request' if self.name=='verify_flowchart_models' else 'request') is self.request)
            and self.activity.scope is self.scope and self.scope.get('query_string',b'') is self.query
            and _headers(self.scope)==self.headers
            and (self.header_container is None or self.scope.get('headers') is self.header_container))

    def original(self):
        current=_context();context=self.activity.context
        task=all(current[i] is context[i] for i in (0,1,2)) and current[3]==context[3]
        worker=self.worker_live and self.worker==(os.getpid(),threading.current_thread()) and current[0] is None
        return self.base() and (task or worker)

    @contextmanager
    def effect(self,kind,identity):
        ticket=object();valid=self.original()
        if not valid:self.fail('effect_entry')
        self.inflight[ticket]=kind,identity
        try:yield
        except BaseException:
            self.fail(kind+'_failed');raise
        else:
            if (not valid or not self.original() or ticket not in self.inflight or len(self.events)>=32):
                self.fail('effect_exit')
            else:self.events.append((kind,identity));del self.inflight[ticket]

    def activate(self,values,endpoint,function):
        caller=sys._getframe(1).f_back
        request=values.get('http_request' if self.name=='verify_flowchart_models' else 'request');frame=caller
        handler=response=owned=False
        for _ in range(64):
            if frame is None:break
            if frame.f_code in self.owner.handler_codes:handler|=frame.f_locals.get('request') is request
            if frame.f_code in self.owner.response_codes:response|=frame.f_locals.get('scope') is self.scope
            if frame.f_code is type(self.owner.owned)._observed_call.__code__:
                owned|=(frame.f_locals.get('self') is self.owner.owned and frame.f_locals.get('activity') is self.activity)
            frame=frame.f_back
        route=self.owner.routes[self.name]
        dependant=caller.f_locals.get('dependant') if caller is not None else None
        valid=(self.original() and not self.failed and _ACTIVE.get() is self and self.entries==0
            and request is not None and request.scope is self.scope and self.scope.get('route') is route
            and self.scope.get('endpoint') is endpoint and self.owner.handlers[self.name]==(endpoint,function)
            and caller is not None and caller.f_code is self.owner.dispatch.__code__
            and dependant is not None and dependant.call is endpoint and caller.f_locals.get('is_coroutine') is True
            and set(caller.f_locals.get('values',{}))==set(values)
            and all(caller.f_locals['values'][key] is value for key,value in values.items())
            and handler and response and owned and (self.owner.included is None
                or self.owner.included.selected(self.scope,route,dependant)))
        self.entries+=1;self.values,self.request=values,request
        if self.name=='verify_flowchart_models':
            from backend.api.routes_flowchart import FlowchartModelVerificationRequest, FlowchartModelReference
            body=values.get('request')
            if (type(body) is not FlowchartModelVerificationRequest or type(body.models) is not list
                    or not 1<=len(body.models)<=24 or any(type(model) is not FlowchartModelReference for model in body.models)):
                valid=False
            else:self.model_body=body,body.source_dataset_path,tuple((model,model.job_id,model.task) for model in body.models)
        self.header_container=self.scope.get('headers');self.auth.header_container=self.header_container
        if not valid:self.fail('handler_entry')
        return valid

    def enter_worker(self,valid):
        caller=sys._getframe(1)
        endpoint,function=self.owner.handlers[self.name]
        invoke_codes=tuple(code for code in endpoint.__code__.co_consts
            if type(code) is type(endpoint.__code__) and code.co_name=='invoke')
        if (not valid or not self.base() or self.failed or self.worker is not None or _ACTIVE.get() is not self
                or len(invoke_codes)!=1 or caller.f_code is not invoke_codes[0]
                or caller.f_locals.get('attempt') is not self or caller.f_locals.get('valid') is not True
                or caller.f_locals.get('function') is not function or caller.f_locals.get('values') is not self.values):
            self.fail('worker_entry')
        self.worker=os.getpid(),threading.current_thread();self.worker_live=True

    def worker_original(self):
        return self.original() and not self.failed and self.worker_live and _ACTIVE.get() is self

    def handler_frame(self):
        function=self.owner.handlers[self.name][1];frame=sys._getframe(1)
        for _ in range(24):
            if frame is None:return None
            if frame.f_code is function.__code__:
                field='http_request' if self.name=='verify_flowchart_models' else 'request'
                return frame if frame.f_locals.get(field) is self.request else None
            frame=frame.f_back
        return None

    def model_body_original(self):
        body,source,models=self.model_body
        return (self.values.get('request') is body and body.source_dataset_path==source
            and type(body.models) is list and len(body.models)==len(models)
            and all(current is model and current.job_id==job and current.task==task
                for current,(model,job,task) in zip(body.models,models)))

    def model_entry(self,args,kwargs):
        from backend.engine.annotation_storage import request_project_root,request_shared_scope
        from backend.engine.checkpoint_paths import active_project_models_dir
        project=self.scope.get('state',{}).get('scoped_project');frame=self.handler_frame()
        valid=(self.worker_original() and frame is not None and self.model_live is None
            and self.specialist_live is None and self.source_live is None
            and len(self.model_calls)<24 and type(project) is dict and len(args)==1 and type(args[0]) is str
            and request_shared_scope() is True and request_project_root()==Path(project['project_dir'])
            and active_project_models_dir()==Path(project['models_dir']))
        if self.name=='verify_flowchart_models':
            if self.model_body is None:
                self.model_live=(args[0] if args else None,None,None)
                self.fail('model_resolution_entry');return False
            body,source,models=self.model_body
            index=len(self.model_calls)
            valid=(valid and index<len(models) and frame.f_locals.get('request') is body
                and frame.f_locals.get('model') is models[index][0]
                and args[0]==models[index][1] and set(kwargs)=={'source_dataset_path','source_task'}
                and kwargs['source_task']==models[index][2] and kwargs['source_dataset_path']==source)
            task=kwargs.get('source_task')
        else:
            source=self.values.get('source_dataset_path');task=None
            directory=frame.f_locals.get('directory') if frame is not None else None
            valid=(valid and set(kwargs)=={'source_dataset_path'} and kwargs['source_dataset_path']==source
                and frame.f_locals.get('job_id')==args[0] and type(directory) is type(Path('.'))
                and directory==Path(project['models_dir'])/args[0])
        valid=(valid and type(source) is str and Path(source).expanduser().resolve()==Path(project['source_dataset_dir']).resolve())
        self.model_live=args[0] if args else None,source,task
        if not valid:self.fail('model_resolution_entry')
        return valid

    def model_return(self,result,valid):
        project=self.scope.get('state',{}).get('scoped_project')
        job,source,task=self.model_live
        good=(valid and self.worker_original() and self.handler_frame() is not None
            and type(result) is tuple and len(result)==6 and type(project) is dict)
        if good:
            directory,checkpoint,meta,resolved_task,resolved_job,dataset=result
            root=Path(project['models_dir'])
            good=(type(directory) is type(root) and type(checkpoint) is type(root) and type(dataset) is type(root)
                and directory==root/job and checkpoint==directory/'best_model.pt'
                and type(meta) is dict and resolved_job==job and (task is None or resolved_task==task)
                and resolved_task in {'detection','anomaly','segmentation','classification','patch_classification'}
                and meta.get('task')==resolved_task and dataset==Path(source).resolve())
        if not good:self.fail('model_resolution_return')
        else:
            self.model_calls.append((job,resolved_task,checkpoint,meta));self.model_live=None

    def specialist_entry(self,args,kwargs):
        project=self.scope.get('state',{}).get('scoped_project');frame=self.handler_frame()
        valid=(self.worker_original() and frame is not None and self.model_live is None
            and self.specialist_live is None and self.source_live is None and len(self.model_calls)<24
            and len(args)==5 and not kwargs and args[0] is project and type(project) is dict
            and type(args[1]) is str and args[2]=='rotation' and args[4] is self.request
            and type(args[3]) is str and Path(args[3]).resolve()==Path(project['source_dataset_dir']).resolve())
        if self.name=='verify_flowchart_models':
            models=self.model_body[2] if self.model_body is not None else ()
            index=len(self.model_calls)
            valid=(valid and index<len(models) and frame.f_locals.get('model') is models[index][0]
                and args[1:3]==models[index][1:] and args[3]==self.model_body[1])
        else:
            valid=(valid and frame.f_locals.get('project') is project
                and frame.f_locals.get('job_id')==args[1] and args[3]==self.values.get('source_dataset_path'))
        self.specialist_live={'project':project,'job':args[1] if len(args)>1 else None,
            'source':args[3] if len(args)>3 else None,'source_count':len(self.retained_sources)}
        if not valid:self.fail('specialist_resolution_entry')
        return valid

    def retained_frame(self):
        from backend.engine import retained_preprocessors as retained
        from backend.api import routes_flowchart as flows
        frame=sys._getframe(1);resolver=specialist=None
        for _ in range(20):
            if frame is None:break
            if frame.f_code is retained.resolve_retained_rotation.__code__:resolver=frame
            if frame.f_code is flows._resolve_project_specialist.__wrapped__.__code__:specialist=frame
            frame=frame.f_back
        live=self.specialist_live
        if (live is None or resolver is None or specialist is None
                or specialist.f_locals.get('project') is not live['project']
                or specialist.f_locals.get('request') is not self.request
                or specialist.f_locals.get('job_id')!=live['job'] or specialist.f_locals.get('task')!='rotation'
                or specialist.f_locals.get('source_dataset_path')!=live['source']
                or specialist.f_locals.get('node') is not None
                or resolver.f_locals.get('project') is not live['project']
                or resolver.f_locals.get('job_id')!=live['job']
                or resolver.f_locals.get('source_dataset_path')!=live['source']):return None
        return resolver

    def retained_source_frame(self):
        from backend.api import routes_flowchart as flows
        frame=sys._getframe(1)
        for _ in range(16):
            if frame is None:return None
            if frame.f_code is flows._registered_retained_source.__wrapped__.__code__:
                return frame if (self.source_live is not None and frame.f_locals.get('request') is self.request
                    and frame.f_locals.get('source_project_id')==self.source_live['source_id']
                    and frame.f_locals.get('binding_target') is None) else None
            frame=frame.f_back
        return None

    def retained_entry(self,args,kwargs):
        from backend.engine import retained_preprocessors as retained
        frame=self.retained_frame();live=self.specialist_live
        value=frame.f_locals.get('value') if frame is not None else None
        binding=frame.f_locals.get('binding_pin') if frame is not None else None
        valid=(self.worker_original() and self.handler_frame() is not None and frame is not None
            and self.source_live is None and len(args)==2 and not kwargs and args[0] is self.request
            and type(value) is dict and type(binding) is dict and args[1]==value.get('source_project_id')
            and value.get('schema')==retained.SCHEMA and value.get('job_id')==live['job']
            and value.get('task')=='rotation' and value.get('operation')=='learned_rotation'
            and type(value.get('pins')) is dict and set(value['pins'])==retained._PIN_NAMES
            and all(value.get(key) is False for key in
                ('model_quality_approved','release_approval','permission_authority'))
            and binding.get('path')==str(Path(live['project']['project_dir'])/retained.DIRECTORY/(live['job']+'.json'))
            and len(self.retained_sources)==live['source_count'])
        self.source_live={'source_id':args[1] if len(args)>1 else None,'value':value,'binding':binding,
            'database_start':len(self.auth.databases),'job':live['job'] if live is not None else None}
        if not valid:self.fail('retained_source_entry')
        return valid

    def retained_return(self,result,valid):
        source=self.source_live;frame=self.retained_frame()
        good=(valid and self.worker_original() and frame is not None and self.handler_frame() is not None
            and source is not None and frame.f_locals.get('value') is source['value']
            and frame.f_locals.get('binding_pin') is source['binding'] and not self.auth.db_live
            and self.auth.databases[source['database_start']:]==['authorize','project_role','project_for']
            and type(result) is dict and result.get('id')==source['source_id']
            and result.get('id')!=self.specialist_live['project'].get('id')
            and result.get('_retained_manifest_pin')==source['value']['pins']['source_project']
            and result.get('models_dir')==str(Path(result['project_dir'])/'models'))
        if not good:self.fail('retained_source_return')
        else:
            source['project']=result
            self.retained_sources.append(source);self.source_live=None

    def specialist_return(self,result,valid):
        live=self.specialist_live
        good=(valid and self.worker_original() and self.handler_frame() is not None and live is not None
            and self.source_live is None and len(self.retained_sources)==live['source_count']+1
            and type(result) is tuple and len(result)==2)
        if good:
            checkpoint,metadata=result;source=self.retained_sources[-1]
            pins=source['value']['pins'];project=source['project']
            good=(type(checkpoint) is type(Path('.')) and type(metadata) is dict
                and checkpoint==Path(project['models_dir'])/'rotation'/live['job']/'best_model.pt'
                and str(checkpoint)==pins['checkpoint']['path'] and metadata.get('task')=='rotation'
                and metadata.get('checkpoint_sha256')==pins['checkpoint']['sha256']
                and source['job']==live['job'])
        if not good:self.fail('specialist_resolution_return')
        else:
            self.model_calls.append((live['job'],'rotation',checkpoint,metadata));self.specialist_live=None

    def publication_entry(self,path,value):
        from backend.api import routes_flowchart as flows
        frame=self.handler_frame();project=self.scope.get('state',{}).get('scoped_project')
        valid=(self.worker_original() and self.name=='save_pipeline' and frame is not None
            and self.owner.lock._is_owned() and type(project) is dict and type(value) is dict
            and frame.f_locals.get('project') is project)
        if valid:
            root=Path(project['project_dir']);path=Path(path)
            graph=self.values.get('pipeline')
            if path.parent==flows._version_dir(root) and re.fullmatch(r'[0-9a-f]{32}\.json',path.name):
                valid=(not self.publications and value.get('version_id')==path.stem
                    and value.get('pipeline')==graph.model_dump() and value.get('recipe_task')==self.values.get('recipe_task')
                    and value.get('source_dataset_path')==project.get('source_dataset_dir'))
            elif path==frame.f_locals.get('target_path'):
                valid=(len(self.publications)==1 and value==graph.model_dump())
            elif path==flows._active_flow_file(root):
                valid=(len(self.publications)==2 and value.get('version_id')==self.saved_version
                    and value.get('project_id')==project.get('id') and value.get('source_dataset_path')==project.get('source_dataset_dir'))
            else:valid=False
        if not valid:self.fail('publication_entry')
        return valid

    def publication_exit(self,path,value,valid):
        path=Path(path)
        try:
            info=path.lstat()
            with open(path,'rb') as handle:raw=handle.read(4*1024*1024+1)
            after=path.lstat()
            fields=('st_dev','st_ino','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns','st_uid','st_gid')
            good=(path.resolve(strict=True)==path and stat.S_ISREG(info.st_mode) and info.st_nlink==1
                and info.st_uid==os.getuid() and len(raw)<=4*1024*1024 and json.loads(raw)==value
                and all(getattr(after,name)==getattr(info,name) for name in fields))
        except (OSError,ValueError):good=False
        if not valid or not good or not self.worker_original() or not self.owner.lock._is_owned():self.fail('publication_exit')
        else:
            self.publications.append(path)
            if len(self.publications)==1:self.saved_version=value['version_id']

    def audit_entry(self,store,values):
        project=self.scope.get('state',{}).get('scoped_project');frame=self.handler_frame()
        valid=(self.worker_original() and self.name=='save_pipeline' and self.owner.lock._is_owned()
            and frame is not None and not self.audit_live and self.audit_result is None and len(self.publications)==3
            and _audit_store_original(self.owner,store)
            and store.path==Path(project['project_dir'])/'configuration_changes.sqlite3'
            and values.get('action')=='save' and values.get('next_revision')==self.saved_version
            and values.get('parent_revision')==frame.f_locals.get('parent_version')
            and values.get('actor',{}).get('id')==self.auth.account.get('id')
            and values.get('subject',{}).get('project_id')==project.get('id'))
        self.audit_live=True;self.audit_store=store
        if not valid:self.fail('audit_entry')
        return valid

    def audit_connection(self,store,connection):
        from backend.engine.config_audit import ConfigAuditStore
        frame=sys._getframe(1)
        original=ConfigAuditStore.append.__wrapped__
        for _ in range(8):
            if frame is None or frame.f_code is original.__code__:break
            frame=frame.f_back
        if (not self.worker_original() or not self.audit_live or store is not self.audit_store
                or self.audit_db is not None or type(connection) is not sqlite3.Connection
                or frame is None or frame.f_code is not original.__code__ or frame.f_locals.get('self') is not store):
            self.fail('audit_connection')
        self.audit_db=connection

    def audit_exit(self,store,values,result,valid):
        closed=False
        if type(self.audit_db) is sqlite3.Connection:
            try:self.audit_db.in_transaction
            except sqlite3.ProgrammingError:closed=True
        if (not valid or not self.worker_original() or not closed or store is not self.audit_store
                or not self.owner.lock._is_owned() or type(result) is not dict
                or result.get('next_revision')!=self.saved_version or result.get('parent_revision')!=values.get('parent_revision')
                or result.get('actor')!=values.get('actor') or result.get('subject')!=values.get('subject')):
            self.fail('audit_exit')
        else:self.audit_result=result
        self.audit_live=False

    def returned(self,result):
        caller=sys._getframe(1)
        endpoint,function=self.owner.handlers[self.name]
        invoke_codes=tuple(code for code in endpoint.__code__.co_consts
            if type(code) is type(endpoint.__code__) and code.co_name=='invoke')
        live=tuple(self.inflight.values())
        valid=(self.worker_original() and not self.owner.lock._is_owned() and self.entries==1
            and len(invoke_codes)==1 and caller.f_code is invoke_codes[0]
            and caller.f_locals.get('attempt') is self and caller.f_locals.get('function') is function
            and caller.f_locals.get('values') is self.values and caller.f_locals.get('result') is result
            and len(live)==1 and live[0][0]=='storage_scope' and live[0][1] is self.owner.storage)
        if self.name=='save_pipeline':
            valid=(valid and len(self.publications)==3 and self.audit_result is not None and not self.audit_live
                and type(result) is dict and result.get('version_id')==self.saved_version
                and result.get('change_id')==self.audit_result.get('change_id'))
        elif self.name in {'catalog_flowchart_models','verify_flowchart_models'}:
            valid=(valid and self.model_live is None and self.specialist_live is None and self.source_live is None
                and not self.locked and not self.publications and self.audit_db is None)
            if self.name=='catalog_flowchart_models':
                valid=(valid and type(result) is dict and set(result)=={'models','total'}
                    and type(result['models']) is list and type(result['total']) is int
                    and result['total']==len(result['models'])==len(self.model_calls)
                    and {(row.get('job_id'),row.get('task')) for row in result['models'] if type(row) is dict}
                        =={(job,task) for job,task,_,_ in self.model_calls})
            else:
                models=self.model_body[2] if self.model_body is not None else ()
                valid=(valid and len(self.model_calls)==len(models)
                    and [(job,task) for job,task,_,_ in self.model_calls]==[(job,task) for _,job,task in models]
                    and type(result) is dict and result=={'verified_job_ids':[job for _,job,_ in models]})
        else:valid=valid and bool(self.locked) and not self.publications and self.audit_db is None
        if not valid:self.fail('handler_return')
        else:self.handler_done,self.result=True,result

    def complete(self,row):
        state=self.scope.get('state',{});project=state.get('scoped_project')
        decision,context=state.get('permission_decision'),state.get('project_context')
        expected=['bind_workspace','authenticate','project_role','project_for','project_role','authorize']
        if self.name in {'save_pipeline','verify_flowchart_models'}:expected.append('record_decision')
        expected+=['authorize','project_role','project_for']*len(self.retained_sources)
        return bool(self.original() and not self.failed and self.handler_done and not self.worker_live
            and not self.inflight and self.storage==1 and self.entries==1 and not self.owner.lock._is_owned()
            and len(self.events)==4
            and all(kind=='context_db' and registry is self.owner.registry for kind,registry in self.events[:3])
            and self.events[3][0]=='storage_scope' and self.events[3][1] is self.owner.storage
            and self.model_live is None and self.specialist_live is None and self.source_live is None
            and len(self.resolutions)==(4 if self.name=='get_active_pipeline_record' else
                1+len(self.retained_sources) if self.name=='verify_flowchart_models' else 2)
            and all(result is project for result in self.resolutions)
            and self.auth.original() and self.auth.middleware_calls==self.auth.middleware_returns==1
            and self.auth.databases==expected and not self.auth.db_live and self.auth.method_name is None
            and type(self.auth.account) is dict and state.get('account_user') is self.auth.account
            and type(project) is dict and project.get('id')==self.auth.project_id
            and decision is not None and decision.allowed and decision.action==self.auth.action
            and decision.actor_id==self.auth.account.get('id') and decision.project_id==self.auth.project_id
            and context is not None and context.actor_id==self.auth.account.get('id')
            and context.project_id==self.auth.project_id and context.workspace_id==self.owner.accounts.workspace
            and row is not None and row['carrier'] is self.activity and row['released'] and row['completed']
            and row['normal_return'] and row['response_status']==200 and not row['transport_failed']
            and not row['framing_error'] and row['send_attempt'] is None and row['start_forwarded']
            and row['terminal_forwarded'] and not row['trailers_expected']
            and (self.owner.deadline is None or time.monotonic()<self.owner.deadline))


def bind_original_owned_flow_request(app,admission):
    if admission is not None and admission._account_requests is not None:
        admission._flow_requests=OriginalOwnedFlowRequest(app,admission)


@contextmanager
def original_owned_flow_request_scope(admission,activity):
    from backend.engine.original_current_project_request import _CURRENT
    owner=admission._flow_requests
    attempt=owner.pending.get(activity.token) if owner is not None and activity is not None else None
    if type(attempt) is not _FlowAttempt:
        yield;return
    token=_ACTIVE.set(attempt);current=_CURRENT.set(attempt)
    try:yield
    finally:_CURRENT.reset(current);_ACTIVE.reset(token)
