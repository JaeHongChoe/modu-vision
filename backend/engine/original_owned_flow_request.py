"""Original saved-flow/model-read HTTP lifetimes; no run/export/tree authority."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import builtins
import hashlib
import inspect
import io
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
_TEMPLATE_CONSTRUCTOR_ORIGIN = None
_RESIDENT_RESOURCE_ORIGIN = None
_HISTORY_ORIGINS = {}
_HISTORY_ACTIVE_ORIGIN = None
_SPEC = {
    'list_configuration_changes':('GET','/api/flowchart/changes'),
    'execution_resources':('GET','/api/flowchart/execution-resources'),
    'get_pipeline':('GET','/api/flowchart/pipeline'),
    'get_single_segmentation_template':('GET','/api/flowchart/templates/single-segmentation'),
    'list_saved_pipelines':('GET','/api/flowchart/pipelines'),
    'get_active_pipeline_record':('GET','/api/flowchart/pipeline/active/record'),
    'get_saved_pipeline_version':('GET','/api/flowchart/pipelines/{version_id}'),
    'save_pipeline':('POST','/api/flowchart/pipeline'),
    'save_flow_draft':('PUT','/api/flowchart/draft'),
    'catalog_flowchart_models':('GET','/api/flowchart/models/catalog'),
    'verify_flowchart_models':('POST','/api/flowchart/models/verify'),
}


def observe_original_flow_resource_engines(default_engine, cpu_engine, kind):
    """First route-import resident identities; later replacements never rebind."""
    global _RESIDENT_RESOURCE_ORIGIN
    if _RESIDENT_RESOURCE_ORIGIN is None:
        function = kind.execution_resources
        _RESIDENT_RESOURCE_ORIGIN = (default_engine,cpu_engine,kind,function,function.__code__,
            default_engine._execution_state_lock,cpu_engine._execution_state_lock)


def original_flow_resource_call(engine):
    """Call the original resident getter once; inspect its result under its real RLock."""
    method = engine.execution_resources
    attempt = _ACTIVE.get()
    if type(attempt)is not _FlowAttempt or attempt.name!='execution_resources':return method()
    lock = engine._execution_state_lock
    frame = sys._getframe()
    try:valid = attempt.resource_entry(engine,method,lock,frame)
    except Exception:
        attempt.fail('resource_inspection_failed');valid=False
    if not valid:return method()
    call = attempt.resource_call
    try:
        with lock:
            result = method()
            try:attempt.resource_observation(engine,result,frame)
            except Exception:attempt.fail('resource_inspection_failed')
            return result
    finally:
        # The outer lock has exited before the handler can claim this result.
        if attempt.resource_call is call:attempt.resource_call=(*call[:5],None)
        else:attempt.fail('resource_call_changed')
        frame=call=None


def register_original_flow_history_sources(kind, runtime_status=None):
    """Remember each first module definition without wrapping business methods."""
    label='audit' if runtime_status is not None else 'ledger'
    names=(('__init__','_connect','list','verify','_public') if label=='audit' else
        ('__init__','connect','diagnostics','active','history','_operation'))
    functions=[]
    for name in names:
        function=getattr(kind,name)
        chain=[];current=function
        for _ in range(5):
            chain.append((current,current.__code__))
            current=getattr(current,'__wrapped__',None)
            if current is None:break
        functions.append((name,function,tuple(chain)))
    module=sys.modules[kind.__module__]
    from backend.engine import sqlite_wal
    owners=[(module,name)for name in ('sqlite3','closing','use_wal','Path')]
    owners+=[(sqlite3,'connect'),(sqlite_wal,'use_wal'),(sqlite_wal,'_busy')]
    if label=='audit':owners+=[(module,name)for name in ('_row_hash','SCHEMA','_GENESIS','_FIELDS')]
    if label=='ledger':
        from backend.engine import artifact_retention
        owners.append((artifact_retention,'retention_project_root'))
        owners.append((module,'observe_original_flow_history_ledger_connection'))
    dependencies=tuple((owner,name,getattr(owner,name),getattr(getattr(owner,name),'__code__',None))
        for owner,name in owners)
    _HISTORY_ORIGINS.setdefault(label,(kind,tuple(functions),runtime_status,
        runtime_status.__code__ if runtime_status is not None else None,dependencies))


def register_original_flow_history_active(function):
    global _HISTORY_ACTIVE_ORIGIN
    if _HISTORY_ACTIVE_ORIGIN is None:_HISTORY_ACTIVE_ORIGIN=(function,function.__code__)


def _history_attempt():
    attempt=_ACTIVE.get()
    return attempt if type(attempt)is _FlowAttempt and attempt.name=='list_configuration_changes' else None


def original_flow_history_call(operation,*args):
    """Invoke the existing list, verify or ledger projection exactly once."""
    attempt=_history_attempt()
    if attempt is None:return operation(*args)
    frame=sys._getframe()
    try:
        try:attempt.history_entry(operation,args,frame)
        except Exception:attempt.fail('history_inspection_failed')
        result=operation(*args)
        try:attempt.history_return(operation,args,result,frame)
        except Exception:attempt.fail('history_inspection_failed')
        return result
    finally:
        attempt.history_live=None
        frame=None


def original_flow_history_read_text(path,encoding=None,errors=None,*,role):
    """Retain the actual active-pointer/version handles only in this history lane."""
    from backend.engine.original_owned_UI_ancillary_request import original_UI_ancillary_read_text
    attempt=_history_attempt()
    if attempt is None:
        return original_UI_ancillary_read_text(path,encoding=encoding,errors=errors,role=role)
    try:attempt.history_read('before',path,None,None,role,sys._getframe(1))
    except Exception:attempt.fail('history_inspection_failed')
    with path.open(mode='r',encoding=io.text_encoding(encoding),errors=errors)as handle:
        try:attempt.history_read('open',path,handle,None,role,sys._getframe(1))
        except Exception:attempt.fail('history_inspection_failed')
        text=handle.read()
        try:attempt.history_read('read',path,handle,text,role,sys._getframe(1))
        except Exception:attempt.fail('history_inspection_failed')
        return text


def observe_original_flow_history_active(project_dir,version,pipeline):
    attempt=_history_attempt()
    if attempt is not None:
        try:attempt.history_active(project_dir,version,pipeline,sys._getframe(1))
        except Exception:attempt.fail('history_inspection_failed')


def observe_original_flow_history_result(project_dir,store,result):
    attempt=_history_attempt()
    if attempt is not None:
        try:attempt.history_result(project_dir,store,result,sys._getframe(1))
        except Exception:attempt.fail('history_inspection_failed')


def observe_original_flow_history_ledger_connection(store,connection):
    attempt=_history_attempt()
    if attempt is not None:
        try:attempt.history_connection('ledger',store,connection,sys._getframe(1))
        except Exception:attempt.fail('history_inspection_failed')


def _history_closed(connection):
    if type(connection)is not sqlite3.Connection:return False
    try:connection.in_transaction
    except sqlite3.ProgrammingError:return True
    return False


def _history_path(path,project):
    """No links or foreign path component; absence is a distinct state."""
    path,project=Path(path),Path(project)
    relative=path.relative_to(project)
    root=project.lstat()
    if (project.resolve(strict=True)!=project or not stat.S_ISDIR(root.st_mode)
            or root.st_uid!=os.getuid()):raise ValueError('Unproved history root')
    current=project
    for index,part in enumerate(relative.parts):
        current=current/part
        try:info=current.lstat()
        except FileNotFoundError:
            return ('absent',str(current),tuple(relative.parts[index:]))
        final=index==len(relative.parts)-1
        if (current.is_symlink() or current.resolve(strict=True)!=current or info.st_uid!=os.getuid()
                or (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 if final else not stat.S_ISDIR(info.st_mode))):
            raise ValueError('Unproved history path')
    return ('file',str(path),(info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_uid,info.st_gid))


def _history_sources_original():
    from backend.engine import config_audit, runtime_deployment
    for label,kind in (('audit',config_audit.ConfigAuditStore),('ledger',runtime_deployment.DeploymentLedger)):
        origin=_HISTORY_ORIGINS.get(label)
        if (type(origin)is not tuple or len(origin)!=5 or kind is not origin[0]
                or any(getattr(kind,name) is not function or any(current.__code__ is not code for current,code in chain)
                    or any(getattr(current,'__wrapped__',None)is not following
                        for (current,_),(following,_)in zip(chain,chain[1:]))
                    for name,function,chain in origin[1])
                or any(getattr(owner,name)is not value or getattr(value,'__code__',None)is not code
                    for owner,name,value,code in origin[4])):return False
    origin=_HISTORY_ORIGINS['audit']
    from backend.api import routes_flowchart as flows
    active=_HISTORY_ACTIVE_ORIGIN
    return (config_audit.runtime_release_status is origin[2] and origin[2].__code__ is origin[3]
        and type(active)is tuple and len(active)==2 and flows._active_version is active[0]
        and active[0].__code__ is active[1])


def _observe_flow_get(kind, *values):
    """New optional inspection only; never replace the original business error."""
    attempt = _ACTIVE.get()
    if type(attempt) is not _FlowAttempt or attempt.name not in {'get_pipeline', 'get_single_segmentation_template'}:
        return
    try:
        attempt.get_observation(kind, values, sys._getframe(1))
    except Exception:
        attempt.fail('get_inspection_failed')


def observe_original_flow_no_legacy(project_dir, claim):
    _observe_flow_get('no_legacy', project_dir, claim, sys._getframe(1))


def observe_original_flow_recipe_read(stage, path, handle, data=None):
    """Inspect the actual original json.load handle; never consume it again."""
    _observe_flow_get('recipe_'+stage, path, handle, data, sys._getframe(1))


def observe_original_flow_recipe_return(path, target, pipeline):
    _observe_flow_get('recipe_return', path, target, pipeline, sys._getframe(1))


def observe_original_jobless_template_constructor(function):
    """Retain the first real route-import constructor, never a later clone."""
    global _TEMPLATE_CONSTRUCTOR_ORIGIN
    @wraps(function)
    def observed(*args, **kwargs):
        _observe_flow_get('template_enter', function, args, kwargs, sys._getframe(1))
        result = function(*args, **kwargs)
        _observe_flow_get('template_return', function, result)
        return result
    if _TEMPLATE_CONSTRUCTOR_ORIGIN is None:
        _TEMPLATE_CONSTRUCTOR_ORIGIN = (observed,function,observed.__code__,function.__code__)
    return observed


def _draft_capture(value,kinds,budget,depth=0):
    """Finite identity tree of the actual parsed body, including graph models."""
    budget[0]-=1
    if budget[0]<0 or depth>32:raise ValueError('Draft body exceeds ownership bounds')
    if type(value) in (str,int,float,bool,type(None)):
        return 'atom',value
    if type(value) in kinds:
        return 'model',value,_draft_capture(value.__dict__,kinds,budget,depth+1)
    if type(value) is dict:
        return 'dict',value,tuple((key,_draft_capture(item,kinds,budget,depth+1))for key,item in value.items())
    if type(value) is list:
        return 'list',value,tuple(_draft_capture(item,kinds,budget,depth+1)for item in value)
    raise ValueError('Unknown original draft body value')


def _draft_original(value,held):
    kind,original,*children=held
    if value is not original:return False
    if kind=='atom':return True
    if kind=='model':return _draft_original(value.__dict__,children[0])
    if kind=='dict':
        return (type(value)is dict and len(value)==len(children[0]) and all(
            key is old and _draft_original(item,captured)
            for (key,item),(old,captured)in zip(value.items(),children[0])))
    return type(value)is list and len(value)==len(children[0]) and all(
        _draft_original(item,captured)for item,captured in zip(value,children[0]))


def _draft_raw9(info):
    return [getattr(info,name)for name in ('st_dev','st_ino','st_mode','st_nlink','st_size',
        'st_mtime_ns','st_ctime_ns','st_uid','st_gid')]


def _draft_closed_file(path):
    """One bounded original read; a missing/foreign file supplies no witness."""
    path=Path(path);before=path.lstat()
    if (path.resolve(strict=True)!=path or not stat.S_ISREG(before.st_mode)
            or before.st_nlink!=1 or before.st_uid!=os.getuid() or before.st_size>4*1024*1024):
        return None
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:handle=io.open(descriptor,mode='r',encoding='utf-8',closefd=True)
    except BaseException:
        os.close(descriptor);raise
    with handle:
        if type(handle)is not io.TextIOWrapper or _draft_raw9(os.fstat(handle.fileno()))!=_draft_raw9(before):
            return None
        text=handle.read(4*1024*1024+1);raw=text.encode('utf-8')
        if len(raw)!=before.st_size or _draft_raw9(os.fstat(handle.fileno()))!=_draft_raw9(before):return None
    if not handle.closed or _draft_raw9(path.lstat())!=_draft_raw9(before):return None
    return ({'path':str(path),'raw9':_draft_raw9(before),'sha256':hashlib.sha256(raw).hexdigest(),
        'text':text,'value':json.loads(text)},handle)


def observe_original_flow_draft_atomic(stage,path,value,temporary_path,handle):
    attempt=_ACTIVE.get()
    if type(attempt)is _FlowAttempt and attempt.name=='save_flow_draft':
        attempt.draft_atomic(stage,path,value,temporary_path,handle,sys._getframe(1))


def original_flow_draft_read_text(path,encoding=None,errors=None,*,role):
    """Retain actual draft helper handles; other owners keep their old read."""
    from backend.engine.original_owned_UI_ancillary_request import original_UI_ancillary_read_text
    attempt=_ACTIVE.get()
    if type(attempt)is not _FlowAttempt or attempt.name!='save_flow_draft':
        return original_UI_ancillary_read_text(path,encoding=encoding,errors=errors,role=role)
    with path.open(mode='r',encoding=io.text_encoding(encoding),errors=errors)as handle:
        attempt.draft_read(handle,path,role,sys._getframe(1))
        return handle.read()


def observe_original_flow_draft_active_return(project,context,checksum,active,version,pipeline,result):
    attempt=_ACTIVE.get()
    if type(attempt)is _FlowAttempt and attempt.name=='save_flow_draft':
        attempt.draft_active_return(project,context,checksum,active,version,pipeline,result,sys._getframe(1))


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
        if attempt.name=='list_configuration_changes':
            try:attempt.history_connection('audit',store,connection,sys._getframe())
            except Exception:attempt.fail('history_inspection_failed')
        else:attempt.audit_connection(store,connection)
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
            specialized_models, rotation, flowchart_engine, runtime_deployment, artifact_retention, sqlite_wal)
        from backend.api import routes_dataset_versions, routes_patch_classification
        from backend.engine import patch_classification, source_aliases
        from backend.engine import original_owned_flow_execution_request
        from backend.engine import original_owned_UI_ancillary_request
        from backend.engine import original_owned_telemetry_socket_request
        from backend import main
        from fastapi import routing
        from starlette import concurrency, routing as starlette_routing
        from backend.engine.flowchart_engine import FlowchartPipeline,FlowNode,FlowNodeData,FlowEdge,FlowExecutionConfig
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
        self.draft_model_calls=tuple((kind,kind.model_dump,kind.model_validate.__func__,
            kind.__pydantic_validator__,kind.__pydantic_serializer__)for kind in (
                flows.FlowDraftSaveRequest,FlowchartPipeline,FlowNode,FlowNodeData,FlowEdge,FlowExecutionConfig))
        self.draft_model_binding=self.draft_model_calls
        self.spec=dict(_SPEC)
        self.history_origins=dict(_HISTORY_ORIGINS)
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
                'valid_flow_job','SPECIALIZED_TASKS','_draft_context','_draft_file','_draft_active_version',
                'FlowDraftSaveRequest','FlowchartPipeline','pipeline_sha256','original_flow_draft_read_text',
                'observe_original_flow_draft_atomic','observe_original_flow_draft_active_return',
                '_legacy_owner','_get_pipeline_unlocked','_single_inspection_template','_pipeline_matches_recipe',
                'DEFAULT_PIPELINE_FILE','get_single_segmentation_flowchart','observe_original_flow_recipe_read',
                '_ENGINE','_CPU_ENGINE','FlowchartEngine','original_flow_resource_call',
                'observe_original_flow_no_legacy','observe_original_flow_recipe_return',
                'original_flow_history_call','original_flow_history_read_text',
                'observe_original_flow_history_active','observe_original_flow_history_result')),
            (flowchart_engine, ('get_single_segmentation_flowchart','FlowchartPipeline','FlowNode','FlowNodeData','FlowEdge',
                'FlowchartEngine','os')),
            (flowchart_engine.FlowchartEngine, ('execution_resources',)),
            (retained_preprocessors, ('resolve_retained_rotation','retained_rotation_jobs','read_registered_project',
                '_source_model','_location','_target','_path','_pin','_json','_exact','_raw','_require',
                'SCHEMA','DIRECTORY','_PIN_NAMES')),
            (specialized_models, ('resolve_specialized_checkpoint','valid_flow_job','require_completed_checkpoint',
                '_verify_historical_source','specialized_dataset_provenance')),
            (rotation, ('load_rotation_manifest',)),
            (routes_dataset_versions, ('_manifest_digest','_file_hash','_safe_backup_path')),
            (routes_patch_classification, ('_owned','load_patch_manifest')),
            (patch_classification, ('load_patch_manifest','_sha256','_check_box','PatchManifest','PatchRecord')),
            (patch_classification.Image, ('open',)),
            (source_aliases, ('resolve_source_root',)),
            (flows.torch, ('load',)),
            (routes_evaluation, ('_resolve_job_artifacts','_record_for_selected_checkpoint','_matches_source_dataset',
                'active_project_models_dir','completed_job_receipt','trusted_checkpoint','fingerprint_dataset',
                'training_job_manager')),
            (checkpoint_paths, ('active_project_models_dir','completed_job_receipt','trusted_job_dir','trusted_checkpoint','is_job_id')),
            (dataset_fingerprint, ('fingerprint_dataset','_files_under','_update_file','request_project_root')),
            (routes_dataset, ('_split_manifest_file','STUDIO_ANNOTATIONS_DIR')),
            (annotation_storage, ('request_project_root','request_shared_scope')),
            (self.manager_kind, ('get_job',)), (Path, ('open','read_text','read_bytes','mkdir','unlink','lstat','stat','resolve','is_file','exists','glob','_glob_selector','is_symlink')),
            (io, ('text_encoding','TextIOWrapper','open')),
            (io.TextIOWrapper, ('write','flush','close','read','fileno','tell','__enter__','__exit__')),
            (tempfile._TemporaryFileWrapper, ('__enter__','__exit__')),
            (tempfile._TemporaryFileCloser, ('cleanup','close')),
            (original_owned_UI_ancillary_request, ('original_UI_ancillary_read_text','observe_original_UI_draft_active_return')),
            (original_owned_telemetry_socket_request, ('original_owned_telemetry_production',)),
            (config_audit.ConfigAuditStore, ('__init__','append','_connect','_public','list','verify')),
            (config_audit, ('ConfigAuditStore','sqlite3','closing','_row_hash','actor_from_request','observed_runtime_release',
                'runtime_release_status','use_wal','SCHEMA','_GENESIS','_FIELDS')),
            (runtime_deployment, ('DeploymentLedger','sqlite3','closing','use_wal','json',
                'observe_original_flow_history_ledger_connection')),
            (runtime_deployment.DeploymentLedger, ('__init__','connect','diagnostics','active','history','_operation')),
            (artifact_retention, ('retention_project_root',)),
            (sqlite_wal, ('use_wal','_busy')),
            (config_audit.closing, ('__init__','__enter__','__exit__')),
            (sqlite3, ('connect','Connection','ProgrammingError','Row')),
            (sqlite3.Connection, ('execute','executescript','close','__enter__','__exit__')),
            (stat, ('S_ISDIR','S_ISREG')),
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
            (os, ('replace','fsync','getpid','getuid','fstat','open','close','unlink','cpu_count')),
            (tempfile, ('NamedTemporaryFile','_mkstemp_inner','_sanitize_params','_infer_return_type')),
            (time, ('time_ns',)),
            (builtins, ('open','globals')), (json, ('load','dump','loads','dumps')), (hashlib, ('sha256',)),
            (sys.modules[__name__], ('_ACTIVE','_HANDLERS','_SPEC','_TEMPLATE_CONSTRUCTOR_ORIGIN','_RESIDENT_RESOURCE_ORIGIN','_HISTORY_ORIGINS','_HISTORY_ACTIVE_ORIGIN','_context','_headers','_SelectedCurrentAuth',
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
        self.codes.extend((function,function.__code__)for _,dump,validate,_,_ in self.draft_model_calls for function in (dump,validate))
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
            and type(_TEMPLATE_CONSTRUCTOR_ORIGIN)is tuple and len(_TEMPLATE_CONSTRUCTOR_ORIGIN)==4
            and flows.get_single_segmentation_flowchart is _TEMPLATE_CONSTRUCTOR_ORIGIN[0]
            and flowchart_engine.get_single_segmentation_flowchart is _TEMPLATE_CONSTRUCTOR_ORIGIN[1]
            and flows.get_single_segmentation_flowchart.__wrapped__ is _TEMPLATE_CONSTRUCTOR_ORIGIN[1]
            and flows.get_single_segmentation_flowchart.__code__ is _TEMPLATE_CONSTRUCTOR_ORIGIN[2]
            and flowchart_engine.get_single_segmentation_flowchart.__code__ is _TEMPLATE_CONSTRUCTOR_ORIGIN[3]
            and type(_RESIDENT_RESOURCE_ORIGIN)is tuple and len(_RESIDENT_RESOURCE_ORIGIN)==7
            and flows._ENGINE is _RESIDENT_RESOURCE_ORIGIN[0] and flows._CPU_ENGINE is _RESIDENT_RESOURCE_ORIGIN[1]
            and flows.FlowchartEngine is flowchart_engine.FlowchartEngine is _RESIDENT_RESOURCE_ORIGIN[2]
            and flowchart_engine.FlowchartEngine.execution_resources is _RESIDENT_RESOURCE_ORIGIN[3]
            and _RESIDENT_RESOURCE_ORIGIN[3].__code__ is _RESIDENT_RESOURCE_ORIGIN[4]
            and type(self.registry) is ContextRegistry
            and flows.get_current_project is routes_project.get_current_project is main.get_current_project
            and routes_project.get_current_project is original_current_project_request._DECORATED.get('resolution')
            and ContextRegistry._db is original_current_project_request._DECORATED.get('context_db'))

    def resident_resources_original(self):
        from backend.api import routes_flowchart as flows
        origin=_RESIDENT_RESOURCE_ORIGIN
        return (type(origin)is tuple and len(origin)==7 and flows._ENGINE is origin[0] and flows._CPU_ENGINE is origin[1]
            and flows.FlowchartEngine is origin[2] and origin[2].execution_resources is origin[3]
            and origin[3].__code__ is origin[4] and all(type(engine)is origin[2]
                and type(engine.execution_resources)is MethodType and engine.execution_resources.__self__ is engine
                and engine.execution_resources.__func__ is origin[3]
                and engine._execution_state_lock is origin[5+index] and type(origin[5+index])is self.lock_type
                and type(engine.device)is flows.torch.device
                for index,engine in enumerate(origin[:2])))

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
            and self.draft_model_calls is self.draft_model_binding and len(self.draft_model_calls)==6
            and all(kind.model_dump is dump and type(kind.model_validate)is MethodType
                and kind.model_validate.__self__ is kind and kind.model_validate.__func__ is validate
                and kind.__pydantic_validator__ is validator and kind.__pydantic_serializer__ is serializer
                for kind,dump,validate,validator,serializer in self.draft_model_calls)
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
            and _HISTORY_ORIGINS==self.history_origins
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
            valid=(valid and self.attempt.name in {'save_pipeline','save_flow_draft','verify_flowchart_models'}
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
        self.draft_body=self.draft_raw=self.draft_parsed=None
        self.draft_stages=[];self.draft_handle=self.draft_temporary=self.draft_record=None
        self.draft_pin=None;self.draft_reads=[];self.draft_active=[]
        self.draft_record_binding=None
        self.draft_prior=self.draft_prior_binding=None;self.draft_read_pins=[]
        self.get_stages=();self.get_read=None;self.get_no_legacy=None;self.get_return=None
        self.template_constructor=None;self.template_value=None;self.get_result_binding=None
        self.resource_state=None;self.resource_result=None;self.resource_result_binding=None;self.resource_call=None
        self.history_project=self.history_store=self.history_live=self.history_active_value=None
        self.history_reads=[];self.history_calls=[];self.history_result_binding=None
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
            and (self.draft_body is None or self.draft_body_original())
            and (self.draft_record is None or self.draft_record_binding is not None
                and self.draft_record is self.draft_record_binding[0]
                and _draft_original(self.draft_record,self.draft_record_binding[1]))
            and (self.draft_prior is None or self.draft_prior_binding is not None
                and self.draft_prior is self.draft_prior_binding[0]
                and _draft_original(self.draft_prior,self.draft_prior_binding[1]))
            and (self.values is None or self.values.get(
                'http_request' if self.name=='verify_flowchart_models' else 'request') is self.request)
            and (self.get_result_binding is None or _draft_original(self.result,self.get_result_binding))
            and (self.resource_result is None or self.resource_result_binding is not None
                and _draft_original(self.resource_result,self.resource_result_binding))
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
        if self.name=='save_flow_draft':
            from backend.api.routes_flowchart import FlowDraftSaveRequest
            from backend.engine.flowchart_engine import FlowchartPipeline,FlowNode,FlowNodeData,FlowEdge,FlowExecutionConfig
            body=values.get('req');raw=getattr(request,'_body',None);parsed=getattr(request,'_json',None)
            try:
                good=(type(body)is FlowDraftSaveRequest and type(raw)is bytes and 0<len(raw)<=20*1024*1024
                    and type(parsed)is dict and set(parsed)<={'pipeline','context','base_version_id'}
                    and {'pipeline','context'}<=set(parsed) and json.loads(raw)==parsed
                    and FlowDraftSaveRequest.model_validate(parsed).model_dump()==body.model_dump())
                if good:
                    kinds=(FlowDraftSaveRequest,FlowchartPipeline,FlowNode,FlowNodeData,FlowEdge,FlowExecutionConfig)
                    self.draft_body=_draft_capture(body,kinds,[65536])
                    self.draft_raw=raw;self.draft_parsed=_draft_capture(parsed,(),[65536])
                else:valid=False
            except (ValueError,TypeError,AttributeError):valid=False
        if self.name=='verify_flowchart_models':
            from backend.api.routes_flowchart import FlowchartModelVerificationRequest, FlowchartModelReference
            body=values.get('request')
            if (type(body) is not FlowchartModelVerificationRequest or type(body.models) is not list
                    or not 1<=len(body.models)<=24 or any(type(model) is not FlowchartModelReference for model in body.models)):
                valid=False
            else:self.model_body=body,body.source_dataset_path,tuple((model,model.job_id,model.task) for model in body.models)
        if self.name=='list_configuration_changes':
            valid=(valid and type(values.get('limit'))is int and 1<=values['limit']<=1000 and _history_sources_original())
        if self.name=='execution_resources':
            valid=(valid and (values.get('device') is None or type(values.get('device'))is str and values['device']=='cpu'))
        if self.name=='get_single_segmentation_template':
            valid=(valid and values.get('job_id') is None and type(values.get('inspection_task'))is str
                and values['inspection_task'] in {'segmentation','classification','anomaly','patch_classification','ocr','rotated_detection'})
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

    def history_entry(self,operation,args,frame):
        from backend.engine import config_audit
        caller=frame.f_back
        stages=('list','verify','runtime')
        index=len(self.history_calls)
        if index>=3:raise ValueError('Extra history call')
        stage=stages[index]
        if stage=='runtime':
            valid=operation is config_audit.runtime_release_status and args==(self.history_project,)
            path=self.history_project/'runtime_service'/'runtime_deployments.sqlite3'
        else:
            valid=(type(operation)is MethodType and operation.__self__ is self.history_store
                and operation.__func__ is getattr(config_audit.ConfigAuditStore,stage)
                and args==((self.values['limit'],)if stage=='list' else ()))
            path=self.history_store.path
        valid=(valid and self.worker_original() and _history_sources_original() and self.history_live is None
            and self.history_active_value is not None and not self.owner.lock._is_owned()
            and caller is not None and caller.f_code is self.owner.handlers[self.name][1].__code__
            and caller.f_locals.get('request')is self.request and caller.f_locals.get('store')is self.history_store
            and caller.f_locals.get('project_dir')is self.history_project and frame.f_code is original_flow_history_call.__code__)
        if not valid:self.fail('history_call_entry')
        self.history_live=(stage,operation,args,frame,_history_path(path,self.history_project),[])

    def history_connection(self,kind,store,connection,frame):
        from backend.engine import config_audit,runtime_deployment
        live=self.history_live
        if not self.worker_original() or live is None or type(connection)is not sqlite3.Connection:
            self.fail('history_connection');return
        stage,operation,args,_,before,connections=live
        if kind=='audit':
            function=getattr(config_audit.ConfigAuditStore,stage)if stage in {'list','verify'}else None
            caller=frame
            for _ in range(8):
                if caller is None or function is not None and caller.f_code is function.__code__:break
                caller=caller.f_back
            valid=(function is not None and store is self.history_store and not connections
                and caller is not None and caller.f_locals.get('self')is store
                and _audit_store_original(self.owner,store) and before[0]=='file')
            label=stage
        else:
            labels=('__init__','diagnostics','active','history')
            label=labels[len(connections)]if len(connections)<4 else None
            caller=frame.f_back
            valid=(stage=='runtime' and label is not None and type(store)is runtime_deployment.DeploymentLedger
                and frame.f_code is runtime_deployment.DeploymentLedger.connect.__code__
                and frame.f_locals.get('self')is store and frame.f_locals.get('conn')is connection
                and caller is not None and caller.f_code is getattr(runtime_deployment.DeploymentLedger,label).__code__
                and caller.f_locals.get('self')is store and before[0]=='file'
                and store.path==self.history_project/'runtime_service'/'runtime_deployments.sqlite3'
                and store.directory==self.history_project/'runtime_service' and store.project_dir==self.history_project
                and store.pin_owner=='derived:active-release:runtime_service'
                and (not connections or all(row[1]is store for row in connections)))
            if valid:
                runtime_frame=caller
                for _ in range(12):
                    if runtime_frame is None or runtime_frame.f_code is config_audit.runtime_release_status.__code__:break
                    runtime_frame=runtime_frame.f_back
                valid=(runtime_frame is not None and runtime_frame.f_locals.get('project_dir')is self.history_project)
        if (not valid or not _history_sources_original() or _history_path(store.path,self.history_project)!=before
                or connection.in_transaction):self.fail('history_connection')
        if len(connections)>=4:self.fail('history_connection_limit');return
        connections.append((label,store,connection,caller))

    def history_return(self,operation,args,result,frame):
        live=self.history_live
        if live is None:self.fail('history_call_return');return
        stage,held,args_held,live_frame,before,connections=live
        path=(self.history_project/'runtime_service'/'runtime_deployments.sqlite3'if stage=='runtime'
            else self.history_store.path)
        valid=(self.worker_original() and operation is held and args is args_held and frame is live_frame
            and frame.f_code is original_flow_history_call.__code__ and frame.f_locals.get('result')is result
            and _history_sources_original() and _history_path(path,self.history_project)==before
            and all(_history_closed(row[2])and row[3]is not None and row[3].f_locals.get('self')is row[1]
                and row[3].f_locals.get('conn'if stage=='runtime'else'db')is row[2]for row in connections))
        if before[0]=='absent':
            valid=(valid and not connections and (result==[]if stage=='list'else
                result=={'intact':True,'rows':0}if stage=='verify'else result=={'active':None,'pending':None}))
        elif stage=='runtime':
            valid=(valid and len(connections)==4 and [row[0]for row in connections]==['__init__','diagnostics','active','history']
                and type(result)is dict and set(result)=={'active','pending'})
        else:
            valid=(valid and len(connections)==1 and (type(result)is list and len(result)<=self.values['limit']
                if stage=='list'else type(result)is dict and type(result.get('intact'))is bool
                and type(result.get('rows'))is int and result['rows']>=0))
        if not valid:self.fail('history_call_return')
        # Keep actual closed connections and values, but retire completed Python frames.
        self.history_calls.append((stage,result,_draft_capture(result,(),[65536]),path,before,
            tuple((label,store,connection)for label,store,connection,_ in connections)))
        connections.clear()

    def history_read(self,stage,path,handle,text,role,caller):
        from backend.api import routes_flowchart as flows
        handler=self.handler_frame()
        if (not self.worker_original() or not self.owner.lock._is_owned() or handler is None
                or caller.f_code is not flows._active_version.__code__ or role!='flow'
                or caller.f_locals.get('project_dir')is not handler.f_locals.get('project_dir')):
            self.fail('history_active_read');return
        project_dir=handler.f_locals['project_dir']
        if self.history_project is None:
            self.history_project=project_dir;self.history_store=handler.f_locals.get('store')
        if stage=='before':
            expected=(flows._active_flow_file(project_dir)if not self.history_reads else
                flows._version_dir(project_dir)/(self.history_reads[0]['value']['version_id']+'.json'))
            if len(self.history_reads)>=2 or path!=expected:self.fail('history_active_read');return
            pin=_history_path(path,project_dir)
            self.history_reads.append({'path':path,'pin':pin,'raw9':None,'handle':None,'value':None})
            return
        row=self.history_reads[-1]
        if path!=row['path']or row['pin'][0]!='file' or type(handle)is not io.TextIOWrapper:
            self.fail('history_active_read');return
        raw9=_draft_raw9(path.lstat())
        if (raw9!=_draft_raw9(os.fstat(handle.fileno()))or _history_path(path,project_dir)!=row['pin']
                or raw9[4]>4*1024*1024):self.fail('history_active_read');return
        if stage=='open':
            row['raw9']=raw9;row['handle']=handle
        elif stage=='read':
            if (row['handle']is not handle or row['raw9']!=raw9 or type(text)is not str
                    or len(text.encode('utf-8'))!=raw9[4]):self.fail('history_active_read');return
            row['value']=json.loads(text)
        else:self.fail('history_active_read')

    def history_active(self,project_dir,version,pipeline,caller):
        from backend.api import routes_flowchart as flows
        reads=self.history_reads
        valid=(self.worker_original() and _history_sources_original() and self.owner.lock._is_owned()
            and caller.f_code is self.owner.handlers[self.name][1].__code__
            and caller.f_locals.get('request')is self.request and caller.f_locals.get('project_dir')is project_dir
            and self.history_project is project_dir and self.history_store is caller.f_locals.get('store')
            and _audit_store_original(self.owner,self.history_store) and self.history_store.timeout==10.0
            and self.history_store.path==project_dir/'configuration_changes.sqlite3'
            and self.history_active_value is None and not self.history_calls
            and all(row['pin']==_history_path(row['path'],project_dir)for row in reads))
        if len(reads)==1 and reads[0]['pin'][0]=='absent':
            valid=valid and version is pipeline is None
        else:
            valid=(valid and len(reads)==2 and all(row['handle']is not None and row['handle'].closed
                and row['raw9']==_draft_raw9(row['path'].lstat())for row in reads)
                and type(version)is str and re.fullmatch('[0-9a-f]{32}',version)is not None
                and reads[0]['value']['version_id']==version and type(pipeline)is flows.FlowchartPipeline
                and pipeline.model_dump()==reads[1]['value']['pipeline'])
        if not valid:self.fail('history_active_return')
        self.history_active_value=(version,pipeline)

    def history_result(self,project_dir,store,result,caller):
        project=self.scope.get('state',{}).get('scoped_project')
        valid=(self.worker_original() and _history_sources_original() and not self.owner.lock._is_owned()
            and self.history_live is None and self.history_project is project_dir and self.history_store is store
            and type(project)is dict and project_dir==Path(project['project_dir']).resolve()
            and caller.f_code is self.owner.handlers[self.name][1].__code__
            and caller.f_locals.get('request')is self.request and caller.f_locals.get('result')is result
            and len(self.history_calls)==3 and [row[0]for row in self.history_calls]==['list','verify','runtime']
            and type(result)is dict and list(result)==['changes','integrity','active_version_id','runtime']
            and result['changes']is self.history_calls[0][1] and result['integrity']is self.history_calls[1][1]
            and result['runtime']is self.history_calls[2][1]
            and result['active_version_id']is self.history_active_value[0])
        if not valid:self.fail('history_result')
        self.history_result_binding=_draft_capture(result,(),[65536])

    def history_response_original(self):
        try:return self._history_response_original()
        except Exception:return False

    def _history_response_original(self):
        return (self.history_live is None and self.history_active_value is not None and _history_sources_original()
            and self.history_result_binding is not None and _draft_original(self.result,self.history_result_binding)
            and len(self.history_calls)==3 and all(_draft_original(value,binding)
                and _history_path(path,self.history_project)==pin and all(_history_closed(row[2])for row in connections)
                for _,value,binding,path,pin,connections in self.history_calls)
            and all(row['pin']==_history_path(row['path'],self.history_project)
                and (row['pin'][0]=='absent'or row['handle'].closed
                     and row['raw9']==_draft_raw9(row['path'].lstat()))for row in self.history_reads)
            and not self.audit_live and self.audit_store is self.audit_db is self.audit_result is None
            and not self.publications and not self.locked)

    def resource_entry(self,engine,method,lock,frame):
        from backend.api import routes_flowchart as flows
        origin=_RESIDENT_RESOURCE_ORIGIN
        caller=frame.f_back;function=self.owner.handlers[self.name][1]
        selected=origin[1] if self.values.get('device')=='cpu' else origin[0]
        project=self.scope.get('state',{}).get('scoped_project')
        valid=(self.worker_original() and self.name=='execution_resources'
            and self.owner.resident_resources_original() and engine is selected and self.resource_call is None
            and self.resource_state is None and self.resource_result is None
            and type(project)is dict and self.resolutions and all(item is project for item in self.resolutions)
            and sys._getframe(1) is frame and frame.f_code is original_flow_resource_call.__code__
            and frame.f_locals.get('engine') is engine and frame.f_locals.get('method') is method
            and frame.f_locals.get('lock') is lock and frame.f_locals.get('attempt') is self
            and caller is not None and self.handler_frame() is caller and caller.f_code is function.__code__
            and caller.f_locals.get('request') is self.request and caller.f_locals.get('selected') is engine
            and caller.f_locals.get('device') is self.values.get('device')
            and type(method)is MethodType and method.__self__ is engine and method.__func__ is origin[3]
            and method.__func__.__code__ is origin[4] and lock is origin[6 if self.values.get('device')=='cpu' else 5]
            and engine._execution_state_lock is lock and type(lock)is self.owner.lock_type and not lock._is_owned())
        if not valid:self.fail('resident_resource_entry');return False
        self.resource_call=(self,self.token,engine,method,lock,frame)
        return True

    def resource_observation(self,engine,result,frame):
        origin=_RESIDENT_RESOURCE_ORIGIN
        caller=frame.f_back;function=self.owner.handlers[self.name][1];call=self.resource_call
        valid=(self.worker_original() and self.name=='execution_resources'
            and self.owner.resident_resources_original() and type(call)is tuple and len(call)==6
            and call[0] is self and call[1] is self.token and call[2] is engine and call[5] is frame
            and self.resource_state is None and sys._getframe(1) is frame
            and frame.f_code is original_flow_resource_call.__code__ and frame.f_locals.get('engine') is engine
            and frame.f_locals.get('method') is call[3] and frame.f_locals.get('lock') is call[4]
            and frame.f_locals.get('attempt') is self and frame.f_locals.get('valid') is True
            and frame.f_locals.get('call') is call and frame.f_locals.get('result') is result
            and caller is not None and self.handler_frame() is caller and caller.f_code is function.__code__
            and caller.f_locals.get('request') is self.request and caller.f_locals.get('selected') is engine
            and caller.f_locals.get('device') is self.values.get('device')
            and type(call[3])is MethodType and call[3].__self__ is engine and call[3].__func__ is origin[3]
            and call[3].__func__.__code__ is origin[4] and engine._execution_state_lock is call[4] and call[4]._is_owned()
            and type(engine._max_device_concurrency)is int and 1<=engine._max_device_concurrency<=8
            and type(engine._active_executions)is int and engine._active_executions>=0
            and type(result)is dict and set(result)=={'device','engine_device_capacity','active_executions',
                'maximum_cpu_slots','configuration_available','cpu_configuration_available','gpu_capacity_requires_reservation'}
            and type(result['device'])is str
            and all(type(result[key])is int for key in ('engine_device_capacity','active_executions','maximum_cpu_slots'))
            and all(type(result[key])is bool for key in ('configuration_available','cpu_configuration_available','gpu_capacity_requires_reservation'))
            and result=={'device':str(engine.device),'engine_device_capacity':engine._max_device_concurrency,
                'active_executions':engine._active_executions,'maximum_cpu_slots':min(8,os.cpu_count()or 1),
                'configuration_available':engine.device.type=='cpu','cpu_configuration_available':True,
                'gpu_capacity_requires_reservation':engine.device.type!='cpu'})
        if not valid:self.fail('resident_resource_result');return
        self.resource_state=(self,self.token,engine,call[4],engine.device,
            engine._max_device_concurrency,engine._active_executions)
        self.resource_result=result;self.resource_result_binding=_draft_capture(result,(),[32])

    def resource_call_closed(self):
        call=self.resource_call;state=self.resource_state;origin=_RESIDENT_RESOURCE_ORIGIN
        return (type(call)is tuple and len(call)==6 and call[0] is self and call[1] is self.token
            and state is not None and call[2] is state[2] and call[4] is state[3] and call[5] is None
            and type(call[3])is MethodType and call[3].__self__ is call[2]
            and call[3].__func__ is origin[3] and call[3].__func__.__code__ is origin[4]
            and not call[4]._is_owned())

    def resource_response_original(self):
        """The same actual result stays in custody after both RLock acquisitions exit."""
        try:
            state=self.resource_state
            return (state is not None and state[0] is self and state[1] is self.token and self.resource_call_closed()
                and self.owner.resident_resources_original() and self.resource_result_binding is not None
                and self.result is self.resource_result and _draft_original(self.result,self.resource_result_binding)
                and state[2]._execution_state_lock is state[3] and not state[3]._is_owned()
                and state[2].device is state[4] and state[2]._max_device_concurrency==state[5]
                and state[2]._active_executions==state[6])
        except Exception:return False

    def resource_completed_result(self,result):
        # self.result is assigned by the unchanged outer returned() only afterward.
        held=self.resource_result
        return (result is held and self.resource_state is not None and self.resource_result_binding is not None
            and self.resource_call_closed() and _draft_original(result,self.resource_result_binding)
            and self.owner.resident_resources_original() and not self.resource_state[3]._is_owned()
            and not self.locked and not self.publications and self.audit_db is None and self.audit_result is None
            and not self.model_calls and not self.retained_sources
            and not self.get_stages and self.get_read is None and self.template_value is None)

    def get_observation(self,kind,values,helper_frame):
        from backend.api import routes_flowchart as flows
        if not self.worker_original() or self.handler_frame() is None:
            self.fail('get_observation_owner');return
        project=self.scope.get('state',{}).get('scoped_project')
        if type(project)is not dict or not self.resolutions or any(item is not project for item in self.resolutions):
            self.fail('get_observation_context');return
        if self.name=='get_pipeline':
            task=self.values.get('inspection_task');source=self.values.get('source_dataset_path')
            if type(task)is not str or type(source)is not str or source!=project.get('source_dataset_dir'):
                self.fail('recipe_scope');return
            root=Path(project['project_dir'])
            expected=root/'flowcharts'/('pipeline_'+task+'_'+hashlib.sha256(source.encode()).hexdigest()[:16]+'.json')
            if kind=='no_legacy':
                directory,claim,frame=values
                valid=(self.get_no_legacy is None and self.get_read is None
                    and helper_frame.f_code is observe_original_flow_no_legacy.__code__
                    and frame.f_code is flows._legacy_owner.__code__ and frame.f_locals.get('project_dir') is directory
                    and frame.f_locals.get('claim') is claim and directory==root
                    and claim==flows.DEFAULT_PIPELINE_FILE.parent/'.legacy_flowchart_owner.json'
                    and not claim.exists() and not claim.is_symlink()
                    and self.owner.lock._is_owned())
                if not valid:self.fail('recipe_legacy_branch')
                else:self.get_no_legacy=(self,self.token,directory,claim)
                return
            if kind=='recipe_open':
                path,handle,data,frame=values
                valid=(self.get_no_legacy is not None and self.get_read is None and data is None
                    and helper_frame.f_code is observe_original_flow_recipe_read.__code__
                    and frame.f_code is flows._get_pipeline_unlocked.__wrapped__.__code__
                    and frame.f_locals.get('f') is handle and frame.f_locals.get('path') is path
                    and frame.f_locals.get('project_dir')==root and frame.f_locals.get('candidates',[None])[0]==(path,path)
                    and type(handle)is io.TextIOWrapper and path==expected and path.resolve(strict=True)==path
                    and not any(part.is_symlink() for part in (path,*path.parents)) and self.owner.lock._is_owned())
                info=path.lstat();fd=os.fstat(handle.fileno())
                valid=(valid and stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid()
                    and info.st_size<=4*1024*1024 and _draft_raw9(fd)==_draft_raw9(info))
                if not valid:self.fail('recipe_open_identity')
                else:self.get_read=(self,self.token,path,handle,tuple(_draft_raw9(info)),None)
            elif kind in {'recipe_read','recipe_closed'}:
                path,handle,data,frame=values;held=self.get_read
                valid=(held is not None and held[0] is self and held[1] is self.token and held[2]==path and held[3] is handle
                    and helper_frame.f_code is observe_original_flow_recipe_read.__code__
                    and frame.f_code is flows._get_pipeline_unlocked.__wrapped__.__code__
                    and frame.f_locals.get('f') is handle and frame.f_locals.get('path') is path
                    and frame.f_locals.get('data') is data
                    and self.owner.lock._is_owned() and tuple(_draft_raw9(path.lstat()))==held[4])
                if kind=='recipe_read':
                    valid=(valid and self.get_stages==('recipe_open',) and not handle.closed
                        and tuple(_draft_raw9(os.fstat(handle.fileno())))==held[4] and handle.tell()==held[4][4])
                    if valid:self.get_read=(*held[:5],_draft_capture(data,(),[65536]))
                else:
                    valid=(valid and self.get_stages==('recipe_open','recipe_read') and handle.closed
                        and _draft_original(data,held[5]))
                if not valid:self.fail('recipe_read_lifetime')
            elif kind=='recipe_return':
                path,target,pipeline,frame=values;held=self.get_read
                valid=(held is not None and self.get_return is None and self.get_stages==('recipe_open','recipe_read','recipe_closed')
                    and helper_frame.f_code is observe_original_flow_recipe_return.__code__
                    and frame.f_code is flows._get_pipeline_unlocked.__wrapped__.__code__
                    and frame.f_locals.get('path') is path and frame.f_locals.get('migration_target') is target
                    and path is target and path==expected and frame.f_locals.get('pipeline') is pipeline
                    and _draft_original(frame.f_locals.get('data'),held[5]) and held[3].closed
                    and tuple(_draft_raw9(path.lstat()))==held[4] and self.owner.lock._is_owned())
                if not valid:self.fail('recipe_return_identity')
                else:self.get_return=(self,self.token,pipeline)
                return
            else:
                self.fail('unexpected_recipe_effect');return
            self.get_stages+=kind,
        else:
            if kind=='template_enter':
                function,args,kwargs,frame=values
                origin=_TEMPLATE_CONSTRUCTOR_ORIGIN
                valid=(self.name=='get_single_segmentation_template' and self.template_constructor is None
                    and helper_frame.f_code is origin[2] and helper_frame.f_locals.get('function') is origin[1] is function
                    and helper_frame.f_locals.get('args') is args and helper_frame.f_locals.get('kwargs') is kwargs
                    and not args and kwargs=={'job_id':None} and self.values.get('job_id') is None
                    and frame.f_code is flows._single_inspection_template.__code__
                    and frame.f_locals.get('job_id') is None and not self.owner.lock._is_owned()
                    and flows.get_single_segmentation_flowchart.__wrapped__ is function)
                if not valid:self.fail('template_constructor_entry')
                else:self.template_constructor=(self,self.token,function)
            elif kind=='template_return':
                function,result=values
                held=self.template_constructor
                origin=_TEMPLATE_CONSTRUCTOR_ORIGIN
                valid=(held is not None and held[0] is self and held[1] is self.token and held[2] is function and self.template_value is None
                    and helper_frame.f_code is origin[2] and helper_frame.f_locals.get('function') is origin[1] is function
                    and helper_frame.f_locals.get('result') is result
                    and not self.owner.lock._is_owned() and type(result)is flows.FlowchartPipeline
                    and len(result.nodes)==4 and len(result.edges)==3 and all(node.data.model_job_id is None for node in result.nodes))
                if not valid:self.fail('template_constructor_return')
                else:self.template_value=(self,self.token,result)
            else:self.fail('unexpected_template_effect')

    def get_completed_result(self,result):
        from backend.api import routes_flowchart as flows
        if (type(result)is not flows.FlowchartPipeline or self.publications or self.audit_db is not None
                or self.audit_result is not None or self.model_calls or self.retained_sources):return False
        if self.name=='get_pipeline':
            held=self.get_read
            returned=self.get_return;legacy=self.get_no_legacy
            if (held is None or returned is None or returned[0] is not self or returned[1] is not self.token or returned[2] is not result
                    or legacy is None or legacy[0] is not self or legacy[1] is not self.token
                    or legacy[3].exists() or legacy[3].is_symlink()
                    or self.get_stages!=('recipe_open','recipe_read','recipe_closed') or not held[3].closed
                    or tuple(_draft_raw9(held[2].lstat()))!=held[4]
                    or self.locked!=['_project_dir','_get_pipeline_unlocked']):return False
        else:
            returned=self.template_value
            if (returned is None or returned[0] is not self or returned[1] is not self.token or returned[2] is not result
                    or self.template_constructor is None
                    or self.get_stages or self.get_read is not None or self.get_no_legacy is not None
                    or self.locked or self.values.get('job_id') is not None
                    or len(result.nodes)!=4 or len(result.edges)!=3
                    or any(node.data.model_job_id is not None for node in result.nodes)):return False
        kinds=tuple(item[0]for item in self.owner.draft_model_calls)
        self.get_result_binding=_draft_capture(result,kinds,[65536])
        return True

    def get_response_original(self):
        """Only the new optional proof; missing/changed files refuse, not escape."""
        try:
            if self.get_result_binding is None or not _draft_original(self.result,self.get_result_binding):return False
            if self.name=='get_pipeline':
                held=self.get_read;legacy=self.get_no_legacy
                return (held is not None and held[0] is self and held[1] is self.token and held[3].closed
                    and tuple(_draft_raw9(held[2].lstat()))==held[4]
                    and legacy is not None and legacy[0] is self and legacy[1] is self.token
                    and not legacy[3].exists() and not legacy[3].is_symlink())
            return self.name=='get_single_segmentation_template'
        except Exception:
            return False

    def model_body_original(self):
        body,source,models=self.model_body
        return (self.values.get('request') is body and body.source_dataset_path==source
            and type(body.models) is list and len(body.models)==len(models)
            and all(current is model and current.job_id==job and current.task==task
                for current,(model,job,task) in zip(body.models,models)))

    def draft_body_original(self):
        return (self.request is not None and self.values.get('req')is self.draft_body[1]
            and getattr(self.request,'_body',None)is self.draft_raw
            and _draft_original(self.values['req'],self.draft_body)
            and _draft_original(getattr(self.request,'_json',None),self.draft_parsed))

    def draft_entry(self,frame):
        from backend.api import routes_flowchart as flows
        project=self.scope.get('state',{}).get('scoped_project')
        return (self.worker_original() and self.handler_frame()is not None and self.owner.lock._is_owned()
            and self.draft_body is not None and type(project)is dict and frame is not None
            and self.handler_frame().f_locals.get('req')is self.values['req']
            and self.handler_frame().f_locals.get('project')is project
            and self.values['req'].context==flows._draft_context(project))

    def draft_publication_entry(self,path,value):
        from backend.api import routes_flowchart as flows
        frame=self.handler_frame();project=self.scope.get('state',{}).get('scoped_project')
        body=self.values.get('req')
        valid=(self.draft_entry(frame) and not self.publications and not self.draft_stages
            and type(value)is dict and value is frame.f_locals.get('record')
            and set(value)=={'version','context','draft_sha256','saved_at_ns','pipeline','base_version_id'}
            and value['version']==1 and type(value['saved_at_ns'])is int and value['saved_at_ns']>0
            and value['context']is frame.f_locals.get('context') and value['context']==body.context
            and value['pipeline']==body.pipeline.model_dump() and value['base_version_id']==body.base_version_id
            and value['draft_sha256']==flows.pipeline_sha256(body.pipeline)
            and Path(path)==flows._draft_file(project,value['context']) and Path(path).resolve()==Path(path))
        if valid:
            try:self.draft_record_binding=value,_draft_capture(value,(),[65536])
            except (ValueError,TypeError):valid=False
        if not valid:self.fail('draft_publication_entry')
        else:
            self.draft_record=value
            self.draft_prior_entry(path)
        return valid

    def draft_prior_entry(self,path):
        """Bind an existing draft in this namespace, never a historical labelset."""
        from backend.api import routes_flowchart as flows
        try:
            project=self.scope.get('state',{}).get('scoped_project');base=self.values['req'].base_version_id
            if not self.draft_entry(self.handler_frame()) or type(base)is not str or re.fullmatch('[0-9a-f]{32}',base)is None:return
            prior=_draft_closed_file(path)
            if prior is None:return
            record=prior[0]['value'];context=self.draft_record['context']
            if (type(record)is not dict or set(record)!={'version','context','draft_sha256','saved_at_ns','pipeline','base_version_id'}
                    or type(record['version'])is not int or record['version']!=1
                    or type(record['saved_at_ns'])is not int or record['saved_at_ns']<=0
                    or record['context']!=context or record['base_version_id']!=base
                    or flows.pipeline_sha256(flows.FlowchartPipeline.model_validate(record['pipeline']))!=record['draft_sha256']):return
            previous=_draft_closed_file(flows._version_dir(Path(project['project_dir']))/(base+'.json'))
            if previous is None:return
            version=previous[0]['value']
            fields={'version_id','saved_at','saved_at_ns','recipe_task','source_dataset_path','pipeline'}
            if (type(version)is not dict or not fields<=set(version)<=fields|{'project_id','labelset_id'}
                    or version.get('version_id')!=base or type(version['saved_at'])is not str
                    or re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z',version['saved_at'])is None
                    or type(version['saved_at_ns'])is not int or not 0<version['saved_at_ns']<=record['saved_at_ns']
                    or type(version['recipe_task'])is not str
                    or version.get('source_dataset_path')!=context['source_dataset_path']
                    or ('project_id'in version and version['project_id']!=context['project_id'])
                    or ('labelset_id'in version and version['labelset_id']!=context['labelset_id'])):return
            flows.FlowchartPipeline.model_validate(version['pipeline'])
            held={'draft':prior[0],'base':previous[0]}
            if not self.draft_entry(self.handler_frame()):return
            captured=_draft_capture(held,(),[65536])
            self.draft_prior=held
            self.draft_prior_binding=held,captured,(prior[1],previous[1])
        except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):return

    def draft_prior_unchanged(self):
        try:
            proof=self.draft_prior
            if (proof is None or self.draft_prior_binding is None
                    or not all(handle.closed for handle in self.draft_prior_binding[2])):return False
            return all(_draft_raw9(Path(row['path']).lstat())==row['raw9']for row in proof.values())
        except (OSError,ValueError,TypeError,KeyError,AttributeError):return False

    def draft_prior_active(self,active,version):
        """The old target may be replaced only by this original atomic writer."""
        try:
            proof=self.draft_prior
            if (proof is None or not self.draft_prior_binding
                    or not all(handle.closed for handle in self.draft_prior_binding[2])
                    or proof['draft']['value']['base_version_id']!=self.values['req'].base_version_id):return False
            current_A=_draft_closed_file(proof['base']['path'])
            if current_A is None or current_A[0]!=proof['base']:return False
            if len(self.draft_read_pins)!=2:return False
            for (handle,path),pin,wanted in zip(self.draft_reads,self.draft_read_pins,(active,version)):
                current=_draft_closed_file(path)
                if (not handle.closed or current is None or current[0]['raw9']!=pin
                        or current[0]['value']!=wanted):return False
            return self.draft_entry(self.handler_frame())
        except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):return False

    def draft_atomic(self,stage,path,value,temporary,handle,frame):
        from backend.api import routes_flowchart as flows
        try:
            valid=(self.draft_entry(frame) and frame.f_code is flows._write_json.__wrapped__.__code__
                and frame.f_locals.get('path')is path and frame.f_locals.get('value')is value
                and frame.f_locals.get('temporary_path')is temporary and frame.f_locals.get('handle')is handle
                and value is self.draft_record and type(handle)is tempfile._TemporaryFileWrapper
                and type(handle.file)is io.TextIOWrapper and stage==('open','flushed','published','clean')[len(self.draft_stages)]
                and Path(temporary).parent==Path(path).parent and Path(temporary).name.startswith('.'+Path(path).name+'.')
                and Path(temporary).suffix=='.tmp')
            if valid and stage=='open':
                info=os.fstat(handle.fileno());listed=Path(temporary).lstat()
                valid=(not handle.closed and stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid()
                    and info.st_dev==listed.st_dev and info.st_ino==listed.st_ino)
                if valid:self.draft_handle,self.draft_temporary=handle,temporary;self.draft_pin=(info.st_dev,info.st_ino)
            elif valid:
                valid=handle is self.draft_handle and temporary is self.draft_temporary
                if stage=='flushed':valid=valid and not handle.closed and self.draft_pin==(os.fstat(handle.fileno()).st_dev,os.fstat(handle.fileno()).st_ino)
                else:
                    info=Path(path).lstat()
                    valid=(valid and handle.closed and not Path(temporary).exists()
                        and stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid()
                        and self.draft_pin==(info.st_dev,info.st_ino))
            if valid and stage in ('open','flushed') and self.draft_prior is not None:
                # This is a before-replace target check, not an immutable-draft
                # claim after a normal last-writer-wins publication.
                if not self.draft_prior_unchanged():self.draft_prior=None;self.draft_prior_binding=None
            if not valid:self.fail('draft_atomic_'+str(stage))
            else:self.draft_stages.append(stage)
        except (OSError,ValueError,TypeError,AttributeError,IndexError):self.fail('draft_atomic_observation')

    def draft_publication_exit(self,path,value,valid):
        try:
            before=Path(path).lstat()
            with open(path,'rb')as handle:raw=handle.read(4*1024*1024+1)
            after=Path(path).lstat()
            fields=('st_dev','st_ino','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns','st_uid','st_gid')
            good=(len(raw)<=4*1024*1024 and json.loads(raw)==value
                and self.draft_pin==(after.st_dev,after.st_ino) and all(getattr(before,name)==getattr(after,name)for name in fields)
                and self.draft_stages==['open','flushed','published','clean'] and self.draft_handle.closed)
        except (OSError,ValueError,TypeError):good=False
        if not valid or not good or not self.draft_entry(self.handler_frame()):self.fail('draft_publication_exit')
        else:self.publications.append(Path(path))

    def draft_read(self,handle,path,role,frame):
        from backend.api import routes_flowchart as flows
        try:
            project=self.scope.get('state',{}).get('scoped_project')
            pointer=flows._active_flow_file(Path(project['project_dir']))
            expected=pointer if not self.draft_reads else flows._version_dir(Path(project['project_dir']))/(frame.f_locals['active']['version_id']+'.json')
            valid=(self.draft_entry(frame) and frame.f_code is flows._draft_active_version.__code__
                and len(self.publications)==1 and not self.draft_active and len(self.draft_reads)<2
                and frame.f_locals.get('project')is project and frame.f_locals.get('context')is self.draft_record['context']
                and frame.f_locals.get('checksum')==self.draft_record['draft_sha256'] and role=='flow'
                and type(handle)is io.TextIOWrapper and not handle.closed and Path(path)==expected
                and Path(path).resolve(strict=True)==Path(path))
            info=os.fstat(handle.fileno())
            valid=(valid and stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid()
                and info.st_size<=4*1024*1024)
            if not valid:self.fail('draft_read_entry')
            else:
                self.draft_reads.append((handle,Path(path)));self.draft_read_pins.append(_draft_raw9(info))
        except (OSError,ValueError,TypeError,KeyError,AttributeError):self.fail('draft_read_observation')

    def draft_active_return(self,project,context,checksum,active,version,pipeline,result,frame):
        from backend.api import routes_flowchart as flows
        try:
            values=(project,context,checksum,active,version,pipeline,result)
            names=('project','context','checksum','active','version','pipeline','result')
            valid=(self.draft_entry(frame) and frame.f_code is flows._draft_active_version.__code__
                and not self.draft_active and project is self.scope.get('state',{}).get('scoped_project')
                and context is self.draft_record['context'] and checksum==self.draft_record['draft_sha256']
                and len(self.publications)==1 and all(handle.closed for handle,_ in self.draft_reads))
            if active is None:
                pointer=flows._active_flow_file(Path(project['project_dir']))
                try:pointer.lstat()
                except FileNotFoundError:absent=True
                else:absent=False
                valid=(valid and not self.draft_reads and version is pipeline is result is None
                    and self.values['req'].base_version_id in (None,'none')
                    and absent
                    and all(frame.f_locals.get(name)is value for name,value in zip(names[:3],values[:3])))
            else:
                identifier=active.get('version_id')if type(active)is dict else None
                valid=(valid and len(self.draft_reads)==2 and all(frame.f_locals.get(name)is value for name,value in zip(names,values))
                    and type(identifier)is str and re.fullmatch('[0-9a-f]{32}',identifier)is not None
                    and (self.values['req'].base_version_id==identifier or self.draft_prior_active(active,version)) and type(version)is dict
                    and version.get('version_id')==identifier and version.get('source_dataset_path')==context['source_dataset_path']
                    and active.get('project_id')==context['project_id'] and active.get('source_dataset_path')==context['source_dataset_path']
                    and active.get('labelset_id','default')==context['labelset_id'] and type(pipeline)is flows.FlowchartPipeline
                    and result==(identifier if flows.pipeline_sha256(pipeline)==checksum else None))
            if not valid:self.fail('draft_active_return')
            else:self.draft_active.append(result)
        except (OSError,ValueError,TypeError,KeyError,AttributeError):self.fail('draft_active_observation')

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
                and meta.get('task')==resolved_task
                and (dataset==Path(source).resolve() or resolved_task=='patch_classification'
                    and self.prepared_patch_dataset(directory,checkpoint,meta,dataset,source,job)))
        if not good:self.fail('model_resolution_return')
        else:
            self.model_calls.append((job,resolved_task,checkpoint,meta));self.model_live=None

    def prepared_patch_dataset(self,directory,checkpoint,meta,dataset,source,job):
        """Admit only the original completed, same-project prepared PATCH view."""
        from backend.api import routes_patch_classification as patches
        from backend.engine import retained_preprocessors as retained
        project=self.scope.get('state',{}).get('scoped_project')
        try:
            root=retained._path(Path(project['dataset_dir'])/'patch')
            if not dataset.is_relative_to(root) or retained._path(dataset)!=dataset:return False
            receipt,receipt_pin=retained._json(directory/'job_receipt.json')
            metadata,metadata_pin=retained._json(directory/'model_meta.json')
            manifest_pin=retained._pin(dataset/'patches.json')
            checkpoint_pin=retained._pin(checkpoint,maximum=512*1024*1024)
            if not (receipt.get('status')=='completed' and receipt.get('task')=='patch_classification'
                    and receipt.get('job_id')==job and receipt.get('dataset_path')==str(dataset)
                    and receipt.get('source_dataset_path')==str(Path(source).resolve())
                    and metadata==meta and receipt.get('checkpoint_sha256')==metadata.get('checkpoint_sha256')
                    ==checkpoint_pin['sha256']):return False
            manifest=patches._owned(project,str(dataset))
            valid=(manifest.root==dataset and manifest.classes==meta.get('classes')
                and manifest.provenance.get('source_dataset_path')==str(Path(source).resolve()))
            for pin in (receipt_pin,metadata_pin,manifest_pin,checkpoint_pin):retained._exact(pin)
            return valid and self.worker_original() and self.handler_frame() is not None
        except (ValueError,OSError,KeyError,TypeError):return False

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
        if self.name=='save_flow_draft':return self.draft_publication_entry(path,value)
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
        if self.name=='save_flow_draft':return self.draft_publication_exit(path,value,valid)
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
        if self.name=='list_configuration_changes':
            self.result=result
            valid=valid and self.history_response_original()
        elif self.name=='execution_resources':
            try:valid=valid and self.resource_completed_result(result)
            except Exception:valid=False
        elif self.name in {'get_pipeline','get_single_segmentation_template'}:
            try:valid=valid and self.get_completed_result(result)
            except Exception:valid=False  # New optional inspection cannot replace business return.
        elif self.name=='save_pipeline':
            valid=(valid and len(self.publications)==3 and self.audit_result is not None and not self.audit_live
                and type(result) is dict and result.get('version_id')==self.saved_version
                and result.get('change_id')==self.audit_result.get('change_id'))
        elif self.name=='save_flow_draft':
            valid=(valid and len(self.publications)==1 and len(self.draft_active)==1
                and self.draft_stages==['open','flushed','published','clean'] and self.draft_handle.closed
                and all(handle.closed for handle,_ in self.draft_reads) and self.audit_db is None
                and self.audit_result is None and not self.audit_live and type(result)is dict
                and result=={**self.draft_record,'active_version_id':self.draft_active[0]})
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
        if self.name in {'save_pipeline','save_flow_draft','verify_flowchart_models'}:expected.append('record_decision')
        expected+=['authorize','project_role','project_for']*len(self.retained_sources)
        return bool(self.original() and not self.failed and self.handler_done and not self.worker_live
            and not self.inflight and self.storage==1 and self.entries==1 and not self.owner.lock._is_owned()
            and len(self.events)==4
            and all(kind=='context_db' and registry is self.owner.registry for kind,registry in self.events[:3])
            and self.events[3][0]=='storage_scope' and self.events[3][1] is self.owner.storage
            and self.model_live is None and self.specialist_live is None and self.source_live is None
            and (self.name not in {'get_pipeline','get_single_segmentation_template'} or self.get_response_original())
            and (self.name!='execution_resources' or self.resource_response_original())
            and (self.name!='list_configuration_changes' or self.history_response_original())
            and len(self.resolutions)==(1 if self.name in {'get_single_segmentation_template','execution_resources'} else
                4 if self.name=='get_active_pipeline_record' else
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
