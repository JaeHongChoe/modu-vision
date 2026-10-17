"""Two original flow lifetimes: local linear CPU Run and no-parity Export.

The common original ASGI/auth/context witnesses remain authoritative. These
observations cannot grant work, clear uncertainty or qualify remote/WS/parity.
"""
from contextlib import contextmanager
from functools import wraps
import inspect
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from types import FunctionType, MethodType

from backend.engine import original_owned_flow_request as _common
from backend.engine.original_owned_account_request import _headers
from backend.engine.original_owned_selected_compute_request import SelectedComputeLifetime

_ACTIVE = _common._ACTIVE
_HANDLERS = {}
_DECORATED = {}
_SPEC = {'run_flowchart':('POST','/api/flowchart/run'),
         'export_saved_flow':('POST','/api/export/flow')}


def _methods(instance, kind, calls):
    return type(instance) is kind and all(
        type(getattr(instance,name,None)) is MethodType
        and getattr(instance,name).__self__ is instance
        and getattr(instance,name).__func__ is function
        for owner,name,function in calls if owner is kind)


def _attempt():
    value = _ACTIVE.get()
    return value if type(value) is _ExecutionAttempt else None


def original_owned_execution_production(app,admission,producer_owner):
    """Only a pending original execution ingress may warm its route cache."""
    attempt=_attempt()
    owner=getattr(admission,'_flow_execution_requests',None)
    if (type(owner) is not OriginalOwnedFlowExecutionRequest or type(attempt) is not _ExecutionAttempt
            or attempt.owner is not owner or owner.app is not app or owner.admission is not admission
            or producer_owner not in (owner,owner.common) or not attempt.original() or attempt.failed
            or attempt.entries!=0 or attempt.worker is not None or attempt.worker_live):return False
    scope=attempt.scope;frame=sys._getframe(1);owned=False
    for _ in range(64):
        if frame is None:break
        if frame.f_code is type(owner.owned)._observed_call.__code__:
            owned|=(frame.f_locals.get('self') is owner.owned and frame.f_locals.get('scope') is scope
                and frame.f_locals.get('activity') is attempt.activity)
        frame=frame.f_back
    return owned


def _raw(info):
    return tuple(getattr(info,name) for name in ('st_dev','st_ino','st_mode','st_nlink',
        'st_size','st_mtime_ns','st_ctime_ns','st_uid','st_gid'))


def observe_original_flow_execution_handler(register):
    def decorate(function):
        if function.__name__ not in _SPEC:
            raise ValueError('Unknown original flow execution handler')
        @wraps(function)
        async def observed(**values):
            from starlette.concurrency import run_in_threadpool
            attempt = _attempt()
            if attempt is None: return await run_in_threadpool(function, **values)
            valid = attempt.activate(values, observed, function)
            valid = attempt.supported_variant(values) and valid
            def invoke():
                attempt.enter_worker(valid)
                try: result = function(**values)
                except BaseException:
                    attempt.fail('execution_handler_failed')
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


def observe_original_execution_effect(kind):
    if kind not in {'engine','package','record','publish'}:
        raise ValueError('Unknown original execution effect')
    def decorate(function):
        @wraps(function)
        def observed(*args, **kwargs):
            attempt = _attempt()
            if attempt is None: return function(*args, **kwargs)
            ticket = attempt.resource_entry(kind, function, args, kwargs)
            try: result = function(*args, **kwargs)
            except BaseException:
                attempt.fail(kind+'_failed')
                raise
            attempt.resource_return(kind, function, args, kwargs, result, ticket)
            return result
        _DECORATED[kind] = observed
        return observed
    return decorate


def observe_original_cpu_layers(function):
    @wraps(function)
    def observed(*args, **kwargs):
        attempt = _attempt()
        iterator = function(*args, **kwargs)
        if attempt is None: return iterator
        def retained():
            pool = None
            valid = attempt.layers_entry(function, args, kwargs, iterator)
            try:
                while True:
                    try: value = next(iterator)
                    except StopIteration: break
                    frame = iterator.gi_frame
                    current = frame.f_locals.get('pool') if frame is not None else None
                    if pool is None: pool = current
                    if (not valid or current is not pool or not attempt.worker_original()
                            or type(pool) is not attempt.owner.pool_type
                            or pool._max_workers != 1 or pool._threads or pool._shutdown):
                        attempt.fail('original_CPU_pool_yield')
                    yield value
            except BaseException:
                attempt.fail('original_CPU_layers_failed')
                raise
            finally: iterator.close()
            if (not valid or not attempt.worker_original() or pool is None
                    or type(pool) is not attempt.owner.pool_type or not pool._shutdown
                    or pool._threads or iterator.gi_frame is not None):
                attempt.fail('original_CPU_pool_not_closed')
            else: attempt.layers_done += 1
        return retained()
    _DECORATED['layers'] = observed
    return observed


def observe_original_export_lock(function):
    @wraps(function)
    def observed(root):
        manager = function(root)
        attempt = _attempt()
        if attempt is None: return manager
        @contextmanager
        def retained():
            ticket = attempt.lock_entry(function, root)
            handle = lock = None
            try:
                with manager as value:
                    frame = manager.gen.gi_frame
                    handle = frame.f_locals.get('handle') if frame is not None else None
                    lock = frame.f_locals.get('lock') if frame is not None else None
                    if (ticket is None or frame is None or manager.gen.gi_code is not function.__wrapped__.__code__
                            or frame.f_locals.get('held') is not True or type(handle) is not io.BufferedRandom or handle.closed
                            or type(lock) is not attempt.owner.lock_type or not lock._is_owned()
                            or frame.f_locals.get('depths',{}).get(str(Path(root).resolve()/'runtime_lifecycle.lock')) != 1):
                        attempt.fail('original_export_lock_entry')
                    else:
                        info=os.fstat(handle.fileno());named=(Path(root)/'runtime_lifecycle.lock').lstat()
                        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid()
                                or stat.S_IMODE(info.st_mode)!=0o600 or _raw(info)!=_raw(named)):
                            attempt.fail('original_export_lock_file')
                    yield value
            except BaseException:
                attempt.fail('original_export_lock_failed')
                raise
            if (ticket is None or not attempt.worker_original() or handle is None or not handle.closed
                    or lock is None or lock._is_owned() or manager.gen.gi_frame is not None):
                attempt.fail('original_export_lock_not_closed')
            else:
                attempt.library_lock_done += 1
                attempt.library_lock_live = False
        return retained()
    _DECORATED['lock'] = observed
    return observed


class OriginalOwnedFlowExecutionRequest:
    def __init__(self, app, admission):
        from backend import main
        from backend.api import routes_export as exports, routes_flowchart as flows
        from backend.engine import flowchart_engine as engine, flow_executor, flow_package, product_delivery, runtime_process_control
        from backend.remote import file_replace
        from backend.engine import original_owned_selected_compute_request as selected_source
        from concurrent.futures import ThreadPoolExecutor
        from fastapi import routing
        from starlette import concurrency
        self.app,self.admission,self.common = app,admission,admission._flow_requests
        self.accounts,self.observer,self.condition = self.common.accounts,admission._activity_observer,admission._condition
        self.registry,self.storage = self.common.registry,self.common.storage
        self.owned=self.downstream=None
        self.engine,self.engine_type,self.pool_type = flows._CPU_ENGINE,engine.FlowchartEngine,ThreadPoolExecutor
        self.lock_type=type(threading.RLock())
        self.pid,self.deadline = os.getpid(),None
        self.failed=False;self.pending={};self.records=[]
        self.handlers=dict(_HANDLERS);self.spec=dict(_SPEC);self.decorated=dict(_DECORATED)
        self.request_types={'run_flowchart':flows.FlowchartRunRequest,'export_saved_flow':exports.ExportFlowRequest}
        self.dispatch=routing.run_endpoint_function
        self.handler_codes,self.response_codes=self.common.handler_codes,self.common.response_codes
        self.route_kind=routing.APIRoute
        modern=getattr(routing,'_IncludedRouter',None)
        branches=[node for node in app.router.routes if modern is not None and type(node) is modern
                  and node.original_router is exports.router]
        self.export_included=_common._FlowIncluded(self,branches[0]) if len(branches)==1 else None
        self.routes={};self.included={}
        for name,(endpoint,function) in self.handlers.items():
            router = flows.router if name=='run_flowchart' else exports.router
            included = self.common.included if name=='run_flowchart' else self.export_included
            candidates = router.routes if included is not None else app.router.routes
            method,path=self.spec[name]
            rows=[route for route in candidates if type(route) is self.route_kind
                  and route.path==path and route.methods=={method} and route.endpoint is endpoint
                  and route.dependant.call is endpoint]
            if len(rows)!=1: raise ValueError('Unknown original flow execution route')
            self.routes[name]=rows[0];self.included[name]=included
        self.included_bindings=tuple(self.included.items())
        self.route_bindings={name:(route,route.endpoint,route.dependant,route.dependant.call,route.app,
            route.get_route_handler.__func__) for name,route in self.routes.items()}
        resources={'engine':engine.FlowchartEngine.execute,'layers':flow_executor.execute_layers,
            'package':flow_package.build_flow_package,
            'record':product_delivery.record_package,'lock':runtime_process_control.runtime_state_lock,
            'publish':runtime_process_control.atomic_private_json}
        self.resources={kind:(function,getattr(function,'__wrapped__',None)) for kind,function in resources.items()}
        self.resource_bindings=tuple(self.resources.items())
        self.selected_calls=tuple(selected_source._original_sources())
        self.selected_codes=[]
        for _,_,value in self.selected_calls:
            for _ in range(8):
                if hasattr(value,"__code__"):self.selected_codes.append((value,value.__code__))
                value=getattr(value,"__wrapped__",None)
                if value is None:break
        self.calls=[(module,name,getattr(module,name)) for module,names in (
            (flows,('run_flowchart','_CPU_ENGINE','_local_execution_engine','local_debug_run','verified_checkpoint_scope',
                '_project_calibrations','get_current_project','_resolve_project_specialist','ordered_linear_nodes')),
            (exports,('export_saved_flow','get_current_project','build_flow_package','_saved_flow_models','_saved_flow','_FLOW_SAVE_LOCK')),
            (engine,('FlowchartEngine','_tracked_execution','execute_layers','verified_checkpoint_scope','_VERIFIED_CHECKPOINTS')),
            (engine.FlowchartEngine,('execute','execution_resources')),
            (flow_executor,('execute_layers','execution_layers','ThreadPoolExecutor')),
            (ThreadPoolExecutor,('__enter__','__exit__','shutdown','submit')),
            (flow_package,('build_flow_package','_sha256','_runtime_requirements')),
            (product_delivery,('record_package','verify_flow_package','runtime_state_lock','atomic_private_json','_journal','_storage','_owned_package')),
            (runtime_process_control,('runtime_state_lock','atomic_private_json','replace_file','_REGISTRY_LOCK','_THREAD_LOCKS','_LOCAL')),
            (file_replace,('rename_directory','replace_file')),
            (main.OwnedWorkAdmissionMiddleware,('__call__','_observed_call')),
            (concurrency,('run_in_threadpool',)),(routing,('run_endpoint_function',)),
            (routing.APIRoute,('matches','handle','get_route_handler')),
            (os,('open','fdopen','close','fstat','getpid','fsync','replace')),
            (json,('dump','load','loads')),
            (sys.modules[__name__],('_ACTIVE','_HANDLERS','_DECORATED','_SPEC','_common','OriginalOwnedFlowExecutionRequest','_ExecutionAttempt','_ExecutionAuth','SelectedComputeLifetime')),
        ) for name in names]
        self.calls.extend(self.selected_calls)
        for name,value in tuple(globals().items()):
            if type(value) is FunctionType and value.__module__==__name__: self.calls.append((sys.modules[__name__],name,value))
        for name,value in tuple(vars(selected_source).items()):
            if type(value) is FunctionType and value.__module__==selected_source.__name__:
                self.calls.append((selected_source,name,value))
        for kind in (OriginalOwnedFlowExecutionRequest,_ExecutionAttempt,_ExecutionAuth,SelectedComputeLifetime):
            for name in dir(kind):
                value=getattr(kind,name)
                if type(value) is FunctionType:self.calls.append((kind,name,value))
        self.codes=[]
        for value in [*(value for _,_,value in self.calls),*(endpoint for endpoint,_ in self.handlers.values()),
                      *(route.app for route in self.routes.values()),*resources.values()]:
            for _ in range(6):
                if hasattr(value,'__code__'):self.codes.append((value,value.__code__))
                value=getattr(value,'__wrapped__',None)
                if value is None:break
        self.eligible=(set(self.handlers)==set(_SPEC) and set(self.decorated)==set(resources)
            and all(self.decorated.get(kind) is function for kind,function in resources.items())
            and exports.build_flow_package is flow_package.build_flow_package
            and exports._FLOW_SAVE_LOCK is self.common.lock
            and engine.execute_layers is flow_executor.execute_layers
            and product_delivery.runtime_state_lock is runtime_process_control.runtime_state_lock
            and product_delivery.atomic_private_json is runtime_process_control.atomic_private_json)
        self.binding=self,app,admission,self.common,self.accounts,self.observer,self.condition,self.registry,self.engine

    def bind_chain(self,middleware,downstream):
        self.owned,self.downstream=middleware,downstream
        self.storage=self.common.storage
        if self.common.owned is not middleware or self.common.downstream is not downstream:self.eligible=False

    def fail(self,reason):
        self.failed=True
        self.admission._unsupported.add('uncovered_request_protocol')
        self.observer.poison('owned_execution_'+reason)

    def __copy__(self):
        self.fail('owner_copy');raise ValueError('Original execution owner cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):return self.__copy__()

    def original(self):
        return (self.eligible and not self.failed and self.pid==os.getpid()
            and all(a is b for a,b in zip(self.binding,(self,self.app,self.admission,self.common,self.accounts,
                self.observer,self.condition,self.registry,self.engine)))
            and self.admission._flow_execution_requests is self and self.admission._flow_requests is self.common
            and self.common.original() and self.storage is self.common.storage
            and self.owned is self.common.owned and self.downstream is self.common.downstream
            and _methods(self.engine,self.engine_type,self.calls) and str(self.engine.device)=='cpu'
            and _methods(self,OriginalOwnedFlowExecutionRequest,self.calls)
            and _HANDLERS==self.handlers and _SPEC==self.spec and _DECORATED==self.decorated
            and tuple(self.included.items())==self.included_bindings
            and tuple(self.resources.items())==self.resource_bindings
            and all(getattr(owner,name) is function for owner,name,function in self.calls)
            and all(function.__code__ is code for function,code in self.codes)
            and all(endpoint.__wrapped__ is function for endpoint,function in self.handlers.values())
            and (self.export_included is None or _common._methods_original(
                self.export_included,_common._FlowIncluded,self.common.calls) and self.export_included.original())
            and all(type(route) is self.route_kind and route.path==self.spec[name][1]
                and _methods(route,self.route_kind,self.calls)
                and route.methods=={self.spec[name][0]} and route.endpoint is self.handlers[name][0]
                and (route,route.endpoint,route.dependant,route.dependant.call,route.app,route.get_route_handler.__func__)==self.route_bindings[name]
                for name,route in self.routes.items()))

    def begin(self,activity):
        if (activity is None or not self.original() or not self.observer._valid(activity)
                or activity.ingress.get('type')!='http' or activity.scope.get('app') is not self.app
                or len(self.records)>=128):return False
        name=next((name for name,(method,path) in self.spec.items()
            if activity.ingress.get('method')==method and activity.ingress.get('path')==path),None)
        headers=_headers(activity.scope)
        if name is None or headers is None:return False
        tokens=[v for k,v in headers if k.lower()==b'authorization']
        projects=[v for k,v in headers if k.lower()==b'x-vision-project']
        contexts=[v for k,v in headers if k.lower()==b'x-vision-context']
        if (len(tokens)!=1 or not tokens[0].startswith(b'Bearer ') or not 7<len(tokens[0])<=263
                or len(projects)!=1 or not projects[0] or len(contexts)>1
                or any(not v or len(v)>4096 for v in contexts) or any(k.lower()==b'cookie' for k,_ in headers)):return False
        try:token,project=tokens[0][7:].decode('ascii'),projects[0].decode('ascii')
        except UnicodeError:return False
        attempt=_ExecutionAttempt(self,activity,name,headers,project,token)
        self.pending[activity.token]=attempt;self.records.append(attempt);self.admission._active+=1
        return True

    def finish(self,activity):
        attempt=self.pending.get(activity.token) if activity is not None else None
        if type(attempt) is not _ExecutionAttempt:return
        if (not _methods(attempt,_ExecutionAttempt,self.calls) or not _methods(attempt.auth,_ExecutionAuth,self.calls)):
            _common._FlowAttempt.fail(attempt,'changed_execution_methods');return
        if not attempt.complete(self.observer._records.get(activity.token)):
            attempt.fail('unconfirmed_execution_lifetime');return
        del self.pending[activity.token];self.admission._active-=1;attempt.settled=True


class _ExecutionAuth(_common._FlowAuth):
    def begin_db(self,store):
        selected = getattr(self.attempt, 'selected', None)
        if type(selected) is SelectedComputeLifetime and selected.source_live is not None:
            return selected.source_database_entry(store)
        names=('bind_workspace','authenticate','project_role','project_for','project_role','authorize','record_decision')
        expected=names[len(self.databases)] if len(self.databases)<len(names) else None
        function=getattr(type(store),expected,None) if type(expected) is str else None
        function=getattr(function,'__wrapped__',function)
        frame=sys._getframe(1)
        for _ in range(10):
            if frame is None or function is not None and frame.f_code is function.__code__:break
            frame=frame.f_back
        valid=(self.original() and store is self.owner.store and not self.db_live and self.middleware_calls==1
            and not self.middleware_returns and self._authorization_frame() is not None and frame is not None
            and function is not None and frame.f_code is function.__code__ and frame.f_locals.get('self') is store)
        if expected in {'project_role','project_for'}:
            valid=(valid and type(self.account) is dict and frame.f_locals.get('user_id')==self.account.get('id')
                and frame.f_locals.get('project_id')==self.project_id)
        elif expected=='authorize':
            valid=(valid and type(self.account) is dict and frame.f_locals.get('actor')==self.account.get('id')
                and frame.f_locals.get('project')==self.project_id and frame.f_locals.get('session_token')==self.token
                and frame.f_locals.get('action')==self.action=='flow.execute')
        elif expected=='record_decision':
            decision=self.attempt.activity.scope.get('state',{}).get('permission_decision')
            valid=(valid and frame.f_locals.get('decision') is decision and decision is not None
                and decision.allowed and decision.action==self.action=='flow.execute')
        if not valid:self.fail('database_entry')
        self.db_live=True
        return valid,expected


class _ExecutionAttempt(_common._FlowAttempt):
    def __init__(self,owner,activity,name,headers,project,token):
        super().__init__(owner,activity,name,headers,project,token)
        self.auth=_ExecutionAuth(self,headers,project,token)
        self.selected=None
        self.resources={};self.completed_resources=[]
        self.layers_started=self.layers_done=0;self.engine_live=False
        self.library_lock_live=False;self.library_lock_done=0
        self.package=self.record=self.publication=None
        self.package_parent=self.package_before=None

    def base(self):
        return (not self.settled and self.binding==(self,self.owner,self.activity,self.activity.token,self.token)
            and self.owner.pending.get(self.activity.token) is self and self.owner.original()
            and _methods(self,_ExecutionAttempt,self.owner.calls) and _methods(self.auth,_ExecutionAuth,self.owner.calls)
            and (self.values is None or self.values.get('request') is self.request)
            and self.activity.scope is self.scope and self.scope.get('query_string',b'') is self.query
            and _headers(self.scope)==self.headers
            and (self.header_container is None or self.scope.get('headers') is self.header_container))

    def activate(self,values,endpoint,function):
        # The original common witness is retained verbatim apart from selecting
        # this endpoint's original flow or export inclusion.
        caller=sys._getframe(1).f_back
        request=values.get('request');frame=caller
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
        included=self.owner.included[self.name]
        valid=(self.original() and not self.failed and _ACTIVE.get() is self and self.entries==0
            and request is not None and request.scope is self.scope and self.scope.get('route') is route
            and self.scope.get('endpoint') is endpoint and self.owner.handlers[self.name]==(endpoint,function)
            and caller is not None and caller.f_code is self.owner.dispatch.__code__
            and dependant is not None and dependant.call is endpoint and caller.f_locals.get('is_coroutine') is True
            and set(caller.f_locals.get('values',{}))==set(values)
            and all(caller.f_locals['values'][key] is value for key,value in values.items())
            and handler and response and owned and (included is None
                or included.selected(self.scope,route,dependant)))
        self.entries+=1;self.values,self.request=values,request
        self.header_container=self.scope.get('headers');self.auth.header_container=self.header_container
        if not valid:self.fail('handler_entry')
        return valid

    def supported_variant(self,values):
        caller=sys._getframe(1);endpoint,function=self.owner.handlers[self.name]
        valid=(self.original() and not self.failed and self.entries==1 and values is self.values
            and caller.f_code is endpoint.__code__ and caller.f_locals.get('attempt') is self
            and caller.f_locals.get('function') is function and caller.f_locals.get('values') is values)
        req=values.get('req');self.req=req
        if self.name=='run_flowchart':
            supported=(type(req) is self.owner.request_types[self.name] and req.pipeline is not None
                and req.execution_target=='local' and req.device=='cpu' and req.stop_node_id is None
                and req.project_id==self.auth.project_id and req.pipeline.execution_config.max_workers==1
                and req.pipeline.execution_config.device_slots==1)
            selected=(type(req) is self.owner.request_types[self.name] and req.pipeline is not None
                and os.name=='posix' and req.execution_target=='selected_compute' and type(req.device) is str
                and re.fullmatch(r'cuda(?::[0-9]+)?',req.device) is not None
                and req.stop_node_id is None and req.project_id==self.auth.project_id
                and type(req.compute_profile_id) is str and bool(req.compute_profile_id)
                and req.pipeline.execution_config.max_workers==1
                and req.pipeline.execution_config.device_slots==1)
            if valid and selected:
                self.selected=SelectedComputeLifetime(self)
                supported=True
        else:
            supported=(type(req) is self.owner.request_types[self.name] and req.version_id is not None
                and req.verification_image_path is None and req.parity_images is None and req.parity_device is None
                and req.compute_profile_id is None and req.approval_revision_ids is None
                and req.deployment_profile=='standard' and req.target_os is None and req.target_arch is None)
        if not valid or not supported:self.fail('unsupported_execution_variant')
        return valid and supported

    def resource_entry(self,kind,function,args,kwargs):
        frame=self.handler_frame();caller=sys._getframe(1)
        valid=(self.worker_original() and frame is not None and self.owner.resources[kind][1] is function
            and caller.f_code is self.owner.resources[kind][0].__code__
            and caller.f_locals.get('attempt') is self and caller.f_locals.get('function') is function
            and caller.f_locals.get('args') is args and caller.f_locals.get('kwargs') is kwargs)
        project=self.scope.get('state',{}).get('scoped_project')
        if kind=='engine':
            from backend.engine import flowchart_engine as engine
            actual=args[0] if args else None
            valid=(valid and self.name=='run_flowchart' and not self.engine_live and not self.completed_resources
                and actual is self.owner.engine and type(actual) is self.owner.engine_type
                and frame.f_locals.get('engine') is actual and str(frame.f_locals.get('device'))=='cpu'
                and kwargs.get('pipeline') is self.req.pipeline and kwargs.get('image_path')==self.req.image_path
                and kwargs.get('image_id')==self.req.image_id and kwargs.get('stop_node_id') is None
                and engine._VERIFIED_CHECKPOINTS.get()==frame.f_locals.get('verified_checkpoints'))
            self.engine_live=True
        elif kind=='package':
            valid=(valid and self.name=='export_saved_flow' and self.package is None and not self.resources
                and not args and frame.f_locals.get('project') is project
                and kwargs.get('pipeline') is frame.f_locals.get('pipeline')
                and kwargs.get('checkpoints') is frame.f_locals.get('checkpoints')
                and Path(kwargs.get('output_base_dir',''))==Path(project['project_dir']).resolve()/'exports'/'flows'
                and kwargs.get('approved_revisions') is None)
            if valid:
                parent=Path(kwargs['output_base_dir'])
                rows=tuple(parent.iterdir()) if parent.exists() else ()
                valid=not parent.is_symlink() and len(rows)<=1024
                if valid:self.package_parent,self.package_before=parent,frozenset(path.name for path in rows)
        elif kind=='record':
            valid=(valid and self.name=='export_saved_flow' and self.package is not None and self.record is None
                and not self.resources
                and len(args)==2 and args[0] is project and Path(args[1])==self.package
                and kwargs=={'version_id':self.req.version_id,'recipe_task':self.req.recipe_task,'parity':{'status':'not_run'}})
        elif kind=='publish':
            producer=self.producer_frame('record')
            valid=(valid and self.name=='export_saved_flow' and producer is not None and self.library_lock_live
                and 'record' in self.resources.values()
                and len(args)==2 and not kwargs and Path(args[0])==Path(project['project_dir'])/'delivery'/'library.json'
                and args[1] is producer.f_locals.get('journal') and self.publication is None)
            if valid:
                identifier=producer.f_locals.get('identifier');row=args[1].get('packages',{}).get(identifier)
                valid=(type(identifier) is str and re.fullmatch('[0-9a-f]{32}',identifier) is not None
                    and type(row) is dict and row.get('project_id')==project['id']
                    and row.get('version_id')==self.req.version_id and row.get('recipe_task')==self.req.recipe_task
                    and row.get('source_dataset_path')==project.get('source_dataset_dir')
                    and row.get('parity')=={'status':'not_run'})
        if not valid:self.fail(kind+'_entry')
        ticket=object();self.resources[ticket]=kind
        return ticket,valid

    def producer_frame(self,kind):
        function=self.owner.resources[kind][1];frame=sys._getframe(1)
        for _ in range(24):
            if frame is None:return None
            if frame.f_code is function.__code__:return frame
            frame=frame.f_back
        return None

    def resource_return(self,kind,function,args,kwargs,result,ticket):
        token,valid=ticket
        valid=(valid and self.worker_original() and self.resources.get(token)==kind
            and self.owner.resources[kind][1] is function)
        if kind=='engine':
            valid=(valid and self.engine_live and self.layers_started==self.layers_done==1
                and type(result) is dict and args[0]._active_executions==0)
            self.engine_live=False
        elif kind=='package':
            path=Path(result.get('package_path','')) if type(result) is dict else None
            valid=(valid and type(result) is dict and result.get('status')=='success' and self.package_parent is not None
                and path.parent==self.package_parent and path.name not in self.package_before
                and path.name==result.get('package_name') and path.is_dir() and not path.is_symlink()
                and path.resolve(strict=True)==path and (path/'manifest.json').is_file()
                and not (path/'manifest.json').is_symlink())
            if valid:self.package=path
        elif kind=='publish':
            path=Path(args[0])
            try:
                before=path.lstat()
                descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                with os.fdopen(descriptor,'rb') as handle:
                    first=os.fstat(handle.fileno());body=handle.read(4*1024*1024+1)
                    last=os.fstat(handle.fileno())
                after=path.lstat()
                valid=(valid and path.resolve(strict=True)==path and stat.S_ISREG(before.st_mode)
                    and before.st_nlink==1 and before.st_uid==os.geteuid() and stat.S_IMODE(before.st_mode)==0o600
                    and len(body)<=4*1024*1024 and json.loads(body)==args[1]
                    and _raw(before)==_raw(first)==_raw(last)==_raw(after))
            except (OSError,ValueError):valid=False
            if valid:self.publication=path
        elif kind=='record':
            valid=(valid and self.library_lock_done==1 and not self.library_lock_live and self.publication is not None
                and type(result) is str and re.fullmatch('[0-9a-f]{32}',result) is not None)
            if valid:self.record=result
        if not valid:self.fail(kind+'_return')
        else:self.completed_resources.append(kind)
        self.resources.pop(token,None)

    def layers_entry(self,function,args,kwargs,iterator):
        frame=self.producer_frame('engine')
        body=frame.f_locals.get('function') if frame is not None else None
        valid=(self.worker_original() and self.name=='run_flowchart' and self.engine_live
            and self.layers_started==0 and self.owner.resources['layers'][1] is function
            and frame is not None and body is self.owner.resources['engine'][1].__wrapped__
            and len(args)==4 and not kwargs and args[3]==1 and iterator.gi_code is function.__code__)
        self.layers_started+=1
        if not valid:self.fail('original_CPU_layers_entry')
        return valid

    def lock_entry(self,function,root):
        producer=self.producer_frame('record');project=self.scope.get('state',{}).get('scoped_project')
        valid=(self.worker_original() and self.name=='export_saved_flow' and producer is not None
            and 'record' in self.resources.values()
            and self.owner.resources['lock'][1] is function and not self.library_lock_live and self.library_lock_done==0
            and Path(root)==Path(project['project_dir'])/'delivery' and producer.f_locals.get('root') is root)
        if not valid:self.fail('original_export_lock_source');return None
        self.library_lock_live=True
        return object()

    def returned(self,result):
        caller=sys._getframe(1);endpoint,function=self.owner.handlers[self.name]
        invoke_codes=tuple(code for code in endpoint.__code__.co_consts if type(code) is type(endpoint.__code__) and code.co_name=='invoke')
        live=tuple(self.inflight.values())
        valid=(self.worker_original() and self.entries==1 and len(invoke_codes)==1
            and caller.f_code is invoke_codes[0] and caller.f_locals.get('attempt') is self
            and caller.f_locals.get('function') is function and caller.f_locals.get('values') is self.values
            and caller.f_locals.get('result') is result and len(live)==1
            and live[0][0]=='storage_scope' and live[0][1] is self.owner.storage
            and not self.resources and not self.engine_live and not self.library_lock_live)
        if self.name=='run_flowchart':
            if self.selected is None:valid=valid and self.completed_resources==['engine'] and self.layers_done==1
            else:valid=valid and type(self.selected) is SelectedComputeLifetime and self.selected.complete() and not self.completed_resources and self.layers_done==0
        else:valid=valid and self.completed_resources==['package','publish','record'] and self.record is not None
        if not valid:self.fail('execution_handler_return')
        else:self.handler_done=True

    def complete(self,row):
        state=self.scope.get('state',{});project=state.get('scoped_project')
        decision,context=state.get('permission_decision'),state.get('project_context')
        return bool(self.original() and not self.failed and self.handler_done and not self.worker_live
            and not self.inflight and not self.resources and self.storage==1 and self.entries==1
            and len(self.events)==4 and all(kind=='context_db' and registry is self.owner.registry for kind,registry in self.events[:3])
            and self.events[3]==('storage_scope',self.owner.storage) and len(self.resolutions)==2
            and all(result is project for result in self.resolutions)
            and self.auth.original() and self.auth.middleware_calls==self.auth.middleware_returns==1
            and self.auth.databases==(['bind_workspace','authenticate','project_role','project_for','project_role','authorize','record_decision']
                + (['authorize','project_role','project_for']*3 if self.selected is not None else []))
            and (self.selected is None or type(self.selected) is SelectedComputeLifetime and self.selected.complete())
            and not self.auth.db_live and self.auth.method_name is None
            and type(self.auth.account) is dict and state.get('account_user') is self.auth.account
            and type(project) is dict and project.get('id')==self.auth.project_id
            and decision is not None and decision.allowed and decision.action=='flow.execute'
            and decision.actor_id==self.auth.account.get('id') and decision.project_id==self.auth.project_id
            and context is not None and context.actor_id==self.auth.account.get('id')
            and context.project_id==self.auth.project_id and context.workspace_id==self.owner.accounts.workspace
            and row is not None and row['carrier'] is self.activity and row['released'] and row['completed']
            and row['normal_return'] and row['response_status']==200 and not row['transport_failed']
            and not row['framing_error'] and row['send_attempt'] is None and row['start_forwarded']
            and row['terminal_forwarded'] and not row['trailers_expected']
            and (self.owner.deadline is None or time.monotonic()<self.owner.deadline))


def bind_original_owned_flow_execution_request(app,admission):
    if admission is not None and admission._flow_requests is not None:
        admission._flow_execution_requests=OriginalOwnedFlowExecutionRequest(app,admission)


@contextmanager
def original_owned_flow_execution_request_scope(admission,activity):
    from backend.engine.original_current_project_request import _CURRENT
    owner=admission._flow_execution_requests
    attempt=owner.pending.get(activity.token) if owner is not None and activity is not None else None
    if type(attempt) is not _ExecutionAttempt:
        yield;return
    token=_ACTIVE.set(attempt);current=_CURRENT.set(attempt)
    try:yield
    finally:_CURRENT.reset(current);_ACTIVE.reset(token)
