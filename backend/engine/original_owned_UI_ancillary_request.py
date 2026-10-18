"""Finite original UI read lifetimes; no Run/export/WS/tree authority.

The original business handlers run once in their original AnyIO worker. Only
nine literal handlers (eleven declared UI cases) are observed. Real retained
IO/PIL/lock/generator/Popen objects, never returned bytes/status alone, close
resource effects. The normal missing-draft 404 is a separate original branch.
"""
from contextlib import contextmanager, _GeneratorContextManager
from contextvars import ContextVar
from functools import wraps
from _thread import LockType
import builtins
import dis
import inspect
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from types import FunctionType, MethodType, GeneratorType

from backend.engine.original_request_activity import _context
from backend.engine.original_owned_account_request import _headers, _SelectedCurrentAuth

_ACTIVE = ContextVar('original_owned_UI_ancillary_request', default=None)
_HANDLERS = {}
_RESOURCES = {}
_PROFILE_KIND = None
_DATASET_NAMES = frozenset({'current_dataset_summary','list_revisions','revision_source_status','preferences','dataset_statistics','saved_diagnostics','readiness','workflow_impact','library_images'})
_DATASET_METADATA_NAMES = frozenset({'dataset_statistics','readiness','saved_diagnostics','workflow_impact'})
_SPEC = {
    'list_profiles':('GET','/api/compute/profiles','routes_compute'),
    'get_selection':('GET','/api/compute/selection','routes_compute'),
    'reservations':('GET','/api/compute/reservations','routes_compute'),
    'targets':('GET','/api/compute/targets','routes_compute'),
    'jobs':('GET','/api/compute/jobs','routes_compute'),
    'inputs':('GET','/api/compute/inputs','routes_compute'),
    'templates':('GET','/api/flow-workspace/templates','routes_flow_workspace'),
    'comparisons':('GET','/api/flow-workspace/comparisons','routes_flow_workspace'),
    'get_active_version_id':('GET','/api/flowchart/pipeline/active-version','routes_flowchart'),
    'get_flow_draft':('GET','/api/flowchart/draft','routes_flowchart'),
    'preview_pipeline_change':('POST','/api/flowchart/pipeline/diff','routes_flowchart'),
    'image_info':('GET','/api/flow-workspace/image-info','routes_flow_workspace'),
    'runtime_capabilities':('GET','/api/export/runtime-capabilities','routes_export'),
    'edge_targets':('GET','/api/export/edge-targets','routes_export'),
    'list_flow_preflights':('GET','/api/export/flow/preflights','routes_export'),
    'list_dataset_images':('GET','/api/dataset/images','routes_dataset'),
    'packages':('GET','/api/product-delivery/packages','routes_product_delivery'),
    # GLOBAL3: selected-team onboarding, member projects, original initial/cursor ledger reads.
    'onboarding_state':('GET','/api/onboarding','routes_onboarding'),
    'list_projects':('GET','/api/project/list','routes_project'),
    'job_events':('GET','/api/job-events','routes_job_events'),
    'get_thumbnail':('GET','/api/dataset/thumbnail/{image_path:path}','routes_dataset'),
    'current_dataset_summary':('GET','/api/dataset/current-summary','routes_dataset'),
    'list_revisions':('GET','/api/dataset/revisions','routes_dataset_imports'),
    'revision_source_status':('GET','/api/dataset/revisions/{revision_id}/source-status','routes_dataset_imports'),
    'preferences':('GET','/api/project/preferences','routes_project_preferences'),
    'dataset_statistics':('GET','/api/dataset/metadata/statistics','routes_dataset_metadata'),
    'saved_diagnostics':('GET','/api/data-workbench/diagnostics','routes_data_workbench'),
    'readiness':('GET','/api/team-data/readiness','routes_team_data'),
    'workflow_impact':('GET','/api/provenance/impact','routes_provenance'),
    'library_images':('GET','/api/dataset/library/images','routes_image_library'),
}


def _methods_original(instance,kind,calls):
    rows=tuple((name,function) for owner,name,function in calls if owner is kind)
    return (type(instance) is kind and bool(rows) and all(type(getattr(instance,name,None)) is MethodType
        and getattr(instance,name).__self__ is instance and getattr(instance,name).__func__ is function
        for name,function in rows))






def register_original_UI_preflight_store(kind):
    """Definition-origin anchor; return the exact original class unchanged."""
    _RESOURCES.setdefault('preflight_store_origin',(kind,kind.__init__,kind.__init__.__code__,
        kind.list,kind.list.__code__,kind.list.__globals__))
    return kind


def _preflight_raw9(info):
    return tuple(getattr(info,name)for name in ('st_dev','st_ino','st_mode','st_nlink','st_size',
        'st_mtime_ns','st_ctime_ns','st_uid','st_gid'))


def _preflight_path_pin(path,directory=False,missing=False):
    if type(path)is not type(Path('.')) or not path.is_absolute() or any(p.is_symlink()for p in (path,*path.parents)):
        raise ValueError('Unproved original preflight path')
    try:info=path.lstat()
    except FileNotFoundError:
        if missing and path.parent.resolve(strict=True)==path.parent:return None
        raise
    if (path.resolve(strict=True)!=path or info.st_uid!=os.getuid()
            or (not stat.S_ISDIR(info.st_mode)if directory else
                not stat.S_ISREG(info.st_mode)or info.st_nlink!=1 or info.st_size>4*1024*1024)):
        raise ValueError('Unproved original preflight file')
    return _preflight_raw9(info)


def observe_original_UI_preflight(stage,*values):
    attempt=_ACTIVE.get()
    if type(attempt)is not _AncillaryAttempt or attempt.name!='list_flow_preflights':return
    try:attempt.preflight_observation(stage,values,sys._getframe(1))
    except Exception:attempt.fail('preflight_inspection')


def original_UI_preflight_glob(store,present):
    """Retain the actual original conditional glob, without a second scan."""
    original=store.folder.glob('*.json')if present else []
    attempt=_ACTIVE.get()
    if type(attempt)is not _AncillaryAttempt or attempt.name!='list_flow_preflights':return original
    frame=sys._getframe(1)
    try:
        valid=(attempt.worker_original() and attempt.preflight_store_original() and store is attempt.preflight_store
            and attempt.preflight_listing is None and type(present)is bool
            and frame.f_code is attempt.owner.preflight_origin[4] and frame.f_locals.get('self')is store
            and frame.f_locals.get('present')is present and frame.f_back is attempt.preflight_handler
            and _preflight_path_pin(store.folder,True,True)==attempt.preflight_folder_pin
            and present==(attempt.preflight_folder_pin is not None))
    except Exception:valid=False
    row={'iterator':original if present else None,'kind':type(original),'path':store.folder,'members':[],
        'member_binding':None,'complete':False,'live':bool(present),'wrapper':None,'methods':()}
    attempt.preflight_listing=row
    if not present:
        if not valid:attempt.fail('preflight_absent_listing')
        else:row['member_binding']=();row['complete']=True
        return original
    if type(original)in (GeneratorType,map):
        row['methods']=tuple((name,getattr(type(original),name),getattr(original,name))for name in ('__iter__','__next__'))
    else:valid=False
    def retained():
        if not valid:attempt.fail('preflight_listing_entry')
        try:
            for path in original:
                if (not valid or not attempt.worker_original() or not _workspace_iterator_original(row)
                        or type(path)is not type(Path('.')) or path.parent!=store.folder or len(row['members'])>=128):
                    attempt.fail('preflight_listing_member')
                else:row['members'].append(path)
                yield path
        except BaseException:attempt.fail('preflight_listing_failed');raise
        else:
            if (not valid or not attempt.worker_original() or not _workspace_iterator_original(row)
                    or type(original)is GeneratorType and original.gi_frame is not None):
                attempt.fail('preflight_listing_exit')
            else:row['member_binding']=tuple(row['members']);row['complete']=True
        finally:row['live']=False
    row['wrapper']=retained()
    return row['wrapper']


def original_UI_preflight_read_text(store,path,*,encoding):
    attempt=_ACTIVE.get()
    if type(attempt)is not _AncillaryAttempt or attempt.name!='list_flow_preflights':return path.read_text(encoding=encoding)
    frame=sys._getframe(1)
    try:
        with path.open(mode='r',encoding=io.text_encoding(encoding))as handle:
            try:row=attempt.preflight_read(store,path,handle,frame)
            except Exception:attempt.fail('preflight_file_inspection');row=None
            text=handle.read()
            if row is not None:
                try:
                    raw=text.encode('utf-8')
                    valid=(attempt.worker_original() and encoding=='utf-8' and type(text)is str
                        and len(raw)==row['raw9'][4] and handle.tell()==row['raw9'][4]
                        and _preflight_raw9(os.fstat(handle.fileno()))==row['raw9']
                        and _preflight_path_pin(path)==row['raw9'])
                except Exception:valid=False
                if not valid:attempt.fail('preflight_file_return')
                else:row['text']=text
            return text
    except BaseException:attempt.fail('preflight_file_failed');raise


def register_original_preview_denial_audit(function):
    _RESOURCES.setdefault('preview_denial_audit_origin',(function,function.__code__))
    return function


def register_original_library_revision(function):
    _RESOURCES.setdefault('library_revision_origin',(function,function.__code__))
    return function


def observe_original_UI_ancillary_handler(register):
    def decorate(function):
        if function.__name__ not in _SPEC:raise ValueError('Unknown literal ancillary handler')
        signature=inspect.signature(function,eval_str=True)
        injected='request' not in signature.parameters
        if injected:
            from starlette.requests import Request
            signature=signature.replace(parameters=(*signature.parameters.values(),
                inspect.Parameter('_owned_http_request',inspect.Parameter.KEYWORD_ONLY,annotation=Request)))
        @wraps(function)
        async def observed(**values):
            from starlette.concurrency import run_in_threadpool
            attempt=_ACTIVE.get()
            request=values.get('_owned_http_request') if injected else values.get('request')
            original_values={key:value for key,value in values.items() if key!='_owned_http_request'}
            if type(attempt) is not _AncillaryAttempt:
                return await run_in_threadpool(function,**original_values)
            valid=attempt.activate(values,original_values,request,observed,function)
            def invoke():
                attempt.enter_worker(valid,original_values)
                try:result=function(**original_values)
                except BaseException as error:
                    attempt.returned(None,error)
                    raise
                else:
                    attempt.returned(result,None)
                    return result
                finally:attempt.worker_live=False
            return await run_in_threadpool(invoke)
        observed.__signature__=signature
        register(observed)
        _HANDLERS[function.__name__]=observed,function
        return function
    return decorate


def _original_flow_production(owner):
    """Non-authorizing cache production by this factory's pending Flow only.

    The original observer has accepted the same carrier, but no handler or
    worker has started. The same original router/middleware frames must still
    own this real ASGI scope. Unrelated/late/copied/foreign producers get none.
    """
    from backend.engine import original_owned_flow_request as flows
    from backend.engine.original_current_project_request import _CURRENT
    from fastapi.routing import APIRouter
    attempt=flows._ACTIVE.get();flow=owner.flow
    if (type(flow) is not flows.OriginalOwnedFlowRequest or type(attempt) is not flows._FlowAttempt
            or attempt.owner is not flow or flow.app is not owner.app or flow.admission is not owner.admission
            or owner.admission._flow_requests is not flow or flow.observer is not owner.observer
            or not owner.original() or not attempt.original() or attempt.failed or attempt.settled
            or flow.pending.get(attempt.activity.token) is not attempt or _CURRENT.get() is not attempt
            or not flow.observer._valid(attempt.activity) or attempt.activity.scope is not attempt.scope
            or attempt.scope.get('app') is not owner.app or attempt.activity.ingress.get('type')!='http'
            or attempt.entries!=0 or attempt.values is not None or attempt.request is not None
            or attempt.worker is not None or attempt.worker_live
            or flow.owned is not owner.owned or flow.downstream is not owner.downstream
            or type(owner.app.router) is not APIRouter
            or not _methods_original(owner.app.router,APIRouter,owner.calls)):
        return False
    frame=sys._getframe(1);owned_frame=router_frame=False
    for _ in range(64):
        if frame is None:break
        if frame.f_code is type(owner.owned)._observed_call.__code__:
            owned_frame|=(frame.f_locals.get('self') is owner.owned
                and frame.f_locals.get('activity') is attempt.activity
                and frame.f_locals.get('scope') is attempt.scope)
        if frame.f_code is APIRouter.app.__code__:
            router_frame|=(frame.f_locals.get('self') is owner.app.router
                and frame.f_locals.get('scope') is attempt.scope)
        frame=frame.f_back
    return owned_frame and router_frame


def _original_execution_production(owner):
    """This factory's pending original execution may warm ancillary caches."""
    from backend.engine import original_owned_flow_execution_request as executions
    from backend.engine.original_current_project_request import _CURRENT
    from fastapi.routing import APIRouter
    attempt=executions._ACTIVE.get();execution=owner.execution
    if (type(execution) is not executions.OriginalOwnedFlowExecutionRequest
            or type(attempt) is not executions._ExecutionAttempt or attempt.owner is not execution
            or owner.admission._flow_execution_requests is not execution or execution.common is not owner.flow
            or execution.app is not owner.app or execution.admission is not owner.admission
            or execution.observer is not owner.observer or not owner.original()
            or not executions.original_owned_execution_production(owner.app,owner.admission,execution)
            or attempt.settled or execution.pending.get(attempt.activity.token) is not attempt
            or _CURRENT.get() is not attempt or not execution.observer._valid(attempt.activity)
            or attempt.activity.scope is not attempt.scope or attempt.scope.get('app') is not owner.app
            or attempt.activity.ingress.get('type')!='http' or attempt.values is not None or attempt.request is not None
            or execution.owned is not owner.owned or execution.downstream is not owner.downstream
            or type(owner.app.router) is not APIRouter
            or not _methods_original(owner.app.router,APIRouter,owner.calls)):
        return False
    frame=sys._getframe(1);owned_frame=router_frame=False
    for _ in range(64):
        if frame is None:break
        if frame.f_code is type(owner.owned)._observed_call.__code__:
            owned_frame|=(frame.f_locals.get('self') is owner.owned
                and frame.f_locals.get('activity') is attempt.activity
                and frame.f_locals.get('scope') is attempt.scope)
        if frame.f_code is APIRouter.app.__code__:
            router_frame|=(frame.f_locals.get('self') is owner.app.router
                and frame.f_locals.get('scope') is attempt.scope)
        frame=frame.f_back
    return owned_frame and router_frame


class _AncillaryIncluded:
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
            from backend.engine.original_owned_telemetry_socket_request import original_owned_telemetry_production
            frame = sys._getframe(1)
            rows = frame.f_locals.get('effective_candidates')
            attempt = _ACTIVE.get()
            owned = (type(attempt) is _AncillaryAttempt and attempt.owner is owner and attempt.original()
                     or original_owned_login_production(owner.app,owner.admission)
                     or _original_flow_production(owner)
                     or _original_execution_production(owner)
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

class OriginalOwnedUIAncillaryRequest:
    def __init__(self,app,admission):
        from backend.api import routes_compute,routes_flowchart,routes_flow_workspace,routes_export,routes_dataset,routes_product_delivery,routes_project,routes_onboarding,routes_job_events,routes_training,shared_authorization
        from backend.contracts.context import ContextRegistry
        from backend.contracts import context as project_context
        from backend.contracts import authentication
        from backend.engine import original_current_project_request,shared_accounts,global_store_paths,dataset_metadata,dataset_summary,grouped_dataset_views,dicom_input,openvino_runtime,product_delivery,runtime_deadline,job_store,project_labelsets,project_migration,migration_guard,original_lock_acquisition,demo_dataset
        from backend.engine import original_owned_flow_request,original_owned_flow_execution_request
        from backend.engine import original_owned_telemetry_socket_request
        from backend.remote import profiles
        from backend.engine import shared_scheduler,live_control_migration,flow_workspace
        from backend import main
        from backend.api import routes_dataset_imports,routes_project_preferences,routes_dataset_metadata,routes_data_workbench,routes_team_data,routes_provenance
        from backend.engine import dataset_index,job_store,dataset_loaders,source_text,project_preferences,team_data,workflow_impact,evaluation_history,flow_package_runtime
        from backend.api import routes_dataset_versions,routes_image_library
        from backend.engine import data_workbench,flow_preflight
        from fastapi import routing
        from starlette import concurrency,routing as starlette_routing
        from PIL import Image,ImageFile,PngImagePlugin
        import anyio,tempfile
        from starlette import responses as thumbnail_responses
        self.app,self.admission,self.accounts=app,admission,admission._account_requests
        self.account_kind=type(self.accounts);self.account_store_kind=shared_accounts.AccountStore
        self.observer,self.condition=admission._activity_observer,admission._condition
        self.registry,self.storage=self.accounts.named.registry,self.accounts.named.storage
        self.registry_binding=self.registry.root,self.registry.path
        self.registry_calls=self.accounts.named.registry_calls
        self.pid,self.deadline=os.getpid(),None
        self.pending,self.records,self.failed={},[],False
        self.denial_audit_origin=_RESOURCES.get('preview_denial_audit_origin')
        self.denial_audit_descriptor=vars(shared_accounts.AccountStore).get('_audit')
        self.denial_audit_globals=shared_accounts.__dict__
        authorization_code=shared_authorization.SharedAuthorizationMiddleware.__call__.__wrapped__.__code__
        instructions=tuple(dis.get_instructions(authorization_code))
        markers=[index for index,row in enumerate(instructions) if row.opname=='LOAD_CONST'
            and row.argval=='This project role cannot perform the requested action']
        self.denial_returns=tuple(next((row.offset for row in instructions[index+1:index+25]
            if row.opname=='RETURN_VALUE'),None) for index in markers)
        self.denial_return_binding=authorization_code,self.denial_returns
        self._settled_history=(0,hashlib.sha256(b'').hexdigest())
        self._settled_history_binding=self._settled_history
        self._settled_seals={};self._attempt_count=0
        self.handlers,self.spec=dict(_HANDLERS),dict(_SPEC)
        self.flow=admission._flow_requests
        self.execution=admission._flow_execution_requests
        if type(self.execution) is not original_owned_flow_execution_request.OriginalOwnedFlowExecutionRequest:
            raise ValueError('Original execution owner must precede ancillary binding')
        self.shared_export=self.execution.export_included
        self.binding=self,app,admission,self.accounts,self.observer,self.condition,self.registry,self.storage,self.flow,self.execution,self.shared_export
        self.modules={m.__name__.split('.')[-1]:m for m in (routes_compute,routes_flowchart,routes_flow_workspace,routes_export,routes_dataset,routes_product_delivery,routes_onboarding,routes_project,routes_job_events,routes_dataset_imports,routes_project_preferences,routes_dataset_metadata,routes_data_workbench,routes_team_data,routes_provenance,routes_image_library)}
        self.route_kind=routing.APIRoute;self.routes={};self.included={};self.route_included={}
        modern=getattr(routing,'_IncludedRouter',None)
        for module in self.modules.values():
            branches=[row for row in app.router.routes if modern is not None and type(row) is modern and row.original_router is module.router]
            if len(branches)>1:raise ValueError('Repeated original ancillary inclusion')
            if branches:
                selected=(self.flow.included if module is routes_flowchart else
                    self.shared_export if module is routes_export else
                    self.accounts.named.included_current if module is routes_project else _AncillaryIncluded(self,branches[0]))
                if selected is None or selected.branch is not branches[0]:raise ValueError('Unknown original ancillary inclusion')
                self.included[module.__name__]=selected
        for name,(endpoint,function) in self.handlers.items():
            method,path,module_name=_SPEC[name];module=self.modules[module_name]
            selected=self.included.get(module.__name__)
            rows=module.router.routes if selected is not None else app.router.routes
            matches=[row for row in rows if type(row) is self.route_kind and row.path==path and row.methods=={method}
                and row.endpoint is endpoint and row.dependant.call is endpoint]
            if len(matches)!=1:raise ValueError('Unknown literal ancillary route')
            self.routes[name]=matches[0];self.route_included[name]=selected
        self.route_bindings={name:(route.endpoint,route.dependant,route.dependant.call,route.app,
            route.get_route_handler.__func__) for name,route in self.routes.items()}
        self.dispatch=routing.run_endpoint_function
        self.profile_kind=_PROFILE_KIND;self.profile_lock=profiles._STORE_LOCK
        self.lease_kind=shared_scheduler.ResourceLeases
        self.lease_generator=self.lease_kind.connect.__wrapped__.__wrapped__.__wrapped__
        self.lease_path=global_store_paths.resolve_store_path(Path(os.environ.get('VISION_RESOURCE_LEASE_DB',str(Path(os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR',str(Path.home()/'.modu-vision')))/'resource_leases.sqlite3'))))
        self.job_kind=routes_training.TrainingJobManager;self.job_manager=routes_training.training_job_manager
        self.job_lock,self.job_records,self.job_lease=self.job_manager._lock,self.job_manager._jobs,self.job_manager._leases
        self.job_lease_path=self.job_lease.path
        self.workspace_template_root=Path(os.environ.get('MODU_FLOW_TEMPLATE_DIR',str(Path.home()/'.modu_vision'/'flow_templates')))
        self.flow_lock=routes_flowchart._FLOW_SAVE_LOCK
        self.png_kind=PngImagePlugin.PngImageFile
        self.process_kind=subprocess.Popen
        self.preflight_module=flow_preflight;self.preflight_origin=_RESOURCES.get('preflight_store_origin')
        self.preflight_capture=original_owned_flow_request._draft_capture
        self.preflight_original=original_owned_flow_request._draft_original
        # GLOBAL3 source identities are retained through the final ASGI finish.
        self.global_job_kind=job_store.JobStore
        self.global_demo=(demo_dataset.DEMO_ID,demo_dataset.DEMO_VERSION,demo_dataset.DEMO_TASK,
            tuple(demo_dataset.DEMO_CLASSES),tuple(sorted(demo_dataset.DEMO_SPLITS.items())))
        self.calls=[(module,name,getattr(module,name)) for module,names in (
            (inspect,('getattr_static',)),
            (routing,('APIRouter','APIRoute','run_endpoint_function','get_request_handler','request_response')),
            (routing.APIRouter,('app',)),
            (routing.APIRoute,('matches','handle')),(starlette_routing,('Route',)),(starlette_routing.Route,('matches','handle')),
            (_GeneratorContextManager,('__enter__','__exit__')),(concurrency,('run_in_threadpool',)),(self.account_kind,('original',)),
            (shared_accounts,('AccountStore',)),(shared_accounts.AccountStore,('record_decision',)),
            (ContextRegistry,('_db','transaction','register_project','context','project_key')),
            (project_context,('get_project_context','declared_context')),
            (authentication,('PermissionDecision',)),
            (authentication.PermissionDecision,('__init__','__setattr__')),
            (dis,('get_instructions',)),
            (shared_accounts,('uuid','json','time')),
            (shared_accounts.uuid,('uuid4',)),(shared_accounts.json,('dumps',)),(shared_accounts.time,('time',)),
            (shared_accounts.AccountStore,('projects_for','project_role')),
            (routes_onboarding,('_team_request',)),
            (demo_dataset,('DEMO_ID','DEMO_VERSION','DEMO_TASK','DEMO_CLASSES','DEMO_SPLITS')),
            (routes_project,('_load_project','_load_project_unfenced','legacy_preview_migration','normalize_legacy_manifest','load_labelsets','labelset_root','ProjectConfigResponse','_write_json')),
            (project_labelsets,('load_labelsets','labelset_root','_atomic_json')),
            (project_migration,('_read','legacy_preview_migration','normalize_legacy_manifest','_atomic_bytes','_atomic_json')),
            (migration_guard,('maintenance_guard','_HELD','_execution_owner','_admission_capability')),
            (original_lock_acquisition,('OriginalLockAcquisition','_capture_successful_acquisition','_identity','_BINDINGS')),
            (original_lock_acquisition.OriginalLockAcquisition,('current','invalidate')),
            (routes_training,('job_ledger','_shared_ledger')),
            (routes_job_events,('_digest','_cursor','_position','_event')),
            (job_store,('JobStore','ledger','default_ledger_path','user_data_root','resolve_store_path','store_admission','use_wal','_shared_lock')),
            (job_store.JobStore,('__init__','_connect','_tx','ledger_id','newest_event','event_at','events_after')),
            (sqlite3,('connect','Connection')),
            (sqlite3.Connection,('execute','commit','rollback','close','__enter__','__exit__')),
            (routes_project,('get_current_project',)),(main,('get_current_project',)),
            (shared_authorization,('permission_action','Headers','HTTPException','JSONResponse')),
            (shared_authorization.JSONResponse,('__init__','__call__','render','init_headers')),
            (original_current_project_request,('observe_resolution','observe_context_db','observe_storage_scope','_CURRENT')),
            (original_owned_flow_request,('_FlowIncluded','_methods_original','_draft_capture','_draft_original')),
            (original_owned_flow_execution_request,('OriginalOwnedFlowExecutionRequest','original_owned_execution_production')),
            (original_owned_telemetry_socket_request,('original_owned_telemetry_production',)),
            (original_owned_flow_execution_request.OriginalOwnedFlowExecutionRequest,('original',)),
            (main.OwnedWorkAdmissionMiddleware,('__call__','_observed_call')),
            (routes_compute,('get_profile_store','_administrator','_project','_owned','_row')),
            (routes_training,('TrainingJobManager','JobRecord','training_job_manager')),
            (routes_training.TrainingJobManager,('list_jobs',)),
            (shared_scheduler,('ResourceLeases','shared_leases','resolve_store_path','store_admission','use_wal','sqlite3')),
            (shared_scheduler.ResourceLeases,('__init__','connect','list')),
            (live_control_migration,('lease_admission',)),
            (sqlite3,('connect','Connection','ProgrammingError')),
            (sqlite3.Connection,('close','execute','__enter__','__exit__')),
            (profiles,('ProfileStore','default_profile_path','get_profile_store','resolve_store_path','store_admission','_STORE_LOCK','ComputeProfile')),
            (profiles.ProfileStore,('__init__','_read','list','get_selected')),
            (global_store_paths,('resolve_store_path','store_admission','owned_root','active_generation','_assert_current_store')),
            (routes_flowchart,('get_current_project','_FLOW_SAVE_LOCK','_draft_context','_draft_file','_draft_active_version','observe_original_UI_draft_active_return','_active_flow_file','_version_dir','_active_version','_valid_expected_version','_stale','pipeline_sha256','FlowchartPipeline')),
            (routes_flow_workspace,('get_current_project','_image','open_source_image','_library','_comparisons','load_templates','_comparison_digest','_unique_record_fields','MAX_COMPARISON_RECORD_BYTES')),
            (flow_workspace,('load_templates','_digest')),
            (routes_dataset,('open_source_image','_split_manifest_file','STUDIO_ANNOTATIONS_DIR')),
            (dataset_metadata,('list_metadata','metadata_transaction','_file_lock','_hash','_ensure','_geometry','ledger_path','_root','open_source_image','_ACTIVE_TRANSACTIONS')),
            (dataset_summary,('dataset_summary','source_image_paths','_annotations')),
            (grouped_dataset_views,('source_image_paths','_annotations','source_annotations_for_image')),
            (dicom_input,('open_source_image','is_dicom','read_dicom')),
            (routes_product_delivery,('get_current_project','project','execute')),
            (product_delivery,('package_library','_storage','_journal','_scope','_root','_preference_key')),
            (routes_export,('normalize_target','SUPPORTED_TARGETS','torch','_preflight_store','get_current_project','Path','observe_original_UI_preflight')),
            (flow_preflight,('PreflightStore','Path','json','observe_original_UI_preflight','original_UI_preflight_glob','original_UI_preflight_read_text')),
            (flow_preflight.PreflightStore,('__init__','list')),
            (io.TextIOWrapper,('read','tell','fileno','__enter__','__exit__')),
            (builtins,('sorted',)),
            (openvino_runtime,('available_openvino_devices',)),
            (runtime_deadline,('execute_owned_process','validate_deadline','_writer_transport','_OwnedGroup','_observe_owned','_optional_psutil')),
            (subprocess,('Popen',)),(subprocess.Popen,('__init__','wait','poll','_internal_poll','_wait')),
            (Image,('open','Image')),(Image.Image,('__enter__','__exit__','close')),
            (ImageFile.ImageFile,('load',)),(PngImagePlugin,('PngImageFile',)),(PngImagePlugin.PngImageFile,('_open','load')),
            (Path,('open','read_text','read_bytes','glob','_glob_selector','resolve','is_symlink','is_relative_to','is_absolute','lstat','stat','is_dir')),(builtins,('open','map','iter','next','staticmethod')),(io,('open','text_encoding','TextIOWrapper','BufferedReader','BufferedRandom','FileIO')),
            (io.BufferedReader,('fileno',)),
            (json,('load','loads','dump','dumps')),(os,('replace','fsync','getpid','getuid','open','fdopen','fstat','close')),
            (stat,('S_ISDIR','S_ISREG')),
            (sys.modules[__name__],('_ACTIVE','_HANDLERS','_SPEC','_RESOURCES','_PROFILE_KIND','_context','_headers','_SelectedCurrentAuth',
                'OriginalOwnedUIAncillaryRequest','_AncillaryAttempt','_AncillaryAuth','_AncillaryIncluded','GeneratorType','LockType','Path','os','stat','_DATASET_NAMES','_DATASET_METADATA_NAMES','_DATASET_OPERATION_ROUTES')),
        ) for name in names]
        if os.name!='nt':
            import fcntl
            self.calls.extend(((fcntl,'flock',fcntl.flock),(fcntl,'LOCK_SH',fcntl.LOCK_SH),(fcntl,'LOCK_NB',fcntl.LOCK_NB),(fcntl,'LOCK_UN',fcntl.LOCK_UN)))
        for name,(_,function) in self.handlers.items():self.calls.append((self.modules[_SPEC[name][2]],name,function))
        for name,function in tuple(globals().items()):
            if type(function) is FunctionType and function.__module__==__name__:self.calls.append((sys.modules[__name__],name,function))
        for kind in (OriginalOwnedUIAncillaryRequest,_AncillaryAttempt,_AncillaryAuth,_AncillaryIncluded):
            for name in dir(kind):
                value=getattr(kind,name)
                if type(value) is FunctionType:self.calls.append((kind,name,value))
        self.thumbnail_async_kind,self.thumbnail_spool_kind=anyio.AsyncFile,tempfile.SpooledTemporaryFile
        self.thumbnail_simple=_ThumbnailFileResponseBase._handle_simple
        self.thumbnail_cache=routes_dataset.THUMBNAIL_CACHE_DIR
        for kind in (_ThumbnailFileResponseBase,_DatasetThumbnailFileResponse,_ThumbnailNotModifiedResponseBase,_DatasetThumbnailNotModifiedResponse,anyio.AsyncFile,tempfile.SpooledTemporaryFile):
            for name in dir(kind):
                function=getattr(kind,name)
                if type(function) is FunctionType:self.calls.append((kind,name,function))
        self.calls.extend((module,name,getattr(module,name)) for module,names in (
            (anyio,('AsyncFile','open_file')),(tempfile,('SpooledTemporaryFile',)),
            (io,('BufferedWriter',)),
            (thumbnail_responses,('formatdate','Headers')),
            (thumbnail_responses.Headers,('__init__','get','__getitem__')),
            (routes_dataset,('FileResponse','Response','Image','tempfile','THUMBNAIL_CACHE_DIR','_memo_get','_memo_put','_hash_source','_probe_header','_render_jpeg','_render_thumbnail','_thumbnail_key','_decoder_tag','_etag_matches','_decoder_tag','_DIGESTS','_DIGEST_LOCK')),
            (sys.modules[__name__],('_ThumbnailFileResponseBase','_DatasetThumbnailFileResponse','_ThumbnailNotModifiedResponseBase','_DatasetThumbnailNotModifiedResponse')),
        )for name in names)
        self.dataset_index_kind,self.dataset_job_kind=dataset_index.DatasetIndex,job_store.JobStore
        self.library_kind=routes_dataset_imports.DatasetImportJobs
        self.library_origin=_RESOURCES.get('library_revision_origin')
        self.dataset_evaluation_kind=evaluation_history.EvaluationHistory
        self.dataset_modules=(routes_dataset_imports,routes_project_preferences,routes_dataset_metadata,routes_data_workbench,routes_team_data,routes_provenance,dataset_index,job_store,dataset_loaders,source_text,project_preferences,team_data,workflow_impact,evaluation_history,flow_package_runtime,routes_dataset_versions,product_delivery,routes_image_library)
        self.dataset_binding=self.dataset_modules,self.dataset_index_kind,self.dataset_job_kind,routes_dataset_imports._JOBS_LOCK
        for module in self.dataset_modules:
            for name,function in tuple(vars(module).items()):
                if type(function) is FunctionType:self.calls.append((module,name,function))
        for kind in (dataset_index.DatasetIndex,job_store.JobStore,evaluation_history.EvaluationHistory):
            for name in dir(kind):
                function=getattr(kind,name)
                if type(function) is FunctionType:self.calls.append((kind,name,function))
        self.calls.extend((module,name,getattr(module,name)) for module,names in (
            (sqlite3,('connect','Connection','ProgrammingError')),
            (dataset_index,('DatasetIndex','index_path')), (job_store,('JobStore',)),
            (routes_dataset_imports,('_JOBS_LOCK','DatasetImportJobs')),
            (evaluation_history,('EvaluationHistory',)),
            (routes_dataset,('import_dataset','decode_image_file')),
            (routes_dataset_metadata,('dm',)),(routes_team_data,('td','dm')),
            (routes_data_workbench,('dw',)),(data_workbench,('_storage',)),
            (workflow_impact,('sqlite3',)),(flow_package_runtime,('FlowchartPipeline',)),
            (workflow_impact,('closing',)),(workflow_impact.closing,('__enter__','__exit__')),
        )for name in names)
        self.resources=dict(_RESOURCES);self.codes=[]
        self.codes.extend((endpoint,endpoint.__code__) for endpoint,_ in self.handlers.values())
        for _,_,value in self.calls:
            for _ in range(6):
                if hasattr(value,'__code__'):self.codes.append((value,value.__code__))
                value=getattr(value,'__wrapped__',None)
                if value is None:break
        def nested(code):
            result={code}
            for child in code.co_consts:
                if type(child) is type(code):result.update(nested(child))
            return result
        self.handler_codes,self.response_codes=nested(routing.get_request_handler.__code__),nested(routing.request_response.__code__)
        self.eligible=(set(self.handlers)==set(_SPEC) and self.profile_kind is profiles.ProfileStore
            and type(self.registry) is ContextRegistry and type(self.flow_lock) is type(threading.RLock())
            and type(self.profile_lock) is type(threading.RLock())
            and type(routes_dataset._DIGEST_LOCK) is LockType
            and routes_flowchart.get_current_project is routes_flow_workspace.get_current_project is routes_product_delivery.get_current_project is routes_project.get_current_project is main.get_current_project
            and routes_project.get_current_project is original_current_project_request._DECORATED.get('resolution')
            and ContextRegistry._db is original_current_project_request._DECORATED.get('context_db'))

    def fail(self,reason):
        self.failed=True;self.admission._unsupported.add('uncovered_request_protocol')
        self.observer.poison('owned_ancillary_'+reason)

    def __copy__(self):
        self.fail('owner_copy');raise ValueError('Original ancillary owner cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):return self.__copy__()

    def bind_chain(self,middleware,downstream):
        self.owned,self.downstream=middleware,downstream
        if (self.admission._account_requests is not self.accounts or getattr(self.accounts,'owned',None) is not middleware
                or getattr(self.accounts,'downstream',None) is not downstream):self.eligible=False;return
        if self.storage is None and not self.pending and not self.records:
            self.storage=self.accounts.named.storage
            self.binding=self,self.app,self.admission,self.accounts,self.observer,self.condition,self.registry,self.storage,self.flow,self.execution,self.shared_export

    def original(self):
        from backend.engine import original_owned_flow_request,original_owned_flow_execution_request
        return (self.eligible and not self.failed and self.pid==os.getpid() and self.denial_audit_original()
            and self._settled_history_original() and self.global_original()
            and self.binding==(self,self.app,self.admission,self.accounts,self.observer,self.condition,self.registry,self.storage,self.flow,self.execution,self.shared_export)
            and self.admission._UI_ancillary_requests is self and _methods_original(self,OriginalOwnedUIAncillaryRequest,self.calls)
            and _methods_original(self.accounts,self.account_kind,self.calls) and self.accounts.original()
            and type(self.accounts.store) is self.account_store_kind
            and _methods_original(self.accounts.store,self.account_store_kind,self.calls)
            and self.app.state.context_registry is self.registry and self.registry.root is self.registry_binding[0]
            and self.registry.path is self.registry_binding[1]
            and all(type(getattr(self.registry,name,None)) is MethodType and getattr(self.registry,name).__self__ is self.registry
                and getattr(self.registry,name).__func__ is function for name,function in self.registry_calls)
            and self.storage is self.accounts.named.storage and self.owned is self.accounts.owned and self.owned.app is self.downstream
            and self.admission._flow_requests is self.flow and self.flow.original()
            and self.admission._flow_execution_requests is self.execution
            and type(self.execution) is original_owned_flow_execution_request.OriginalOwnedFlowExecutionRequest
            and self.execution.app is self.app and self.execution.admission is self.admission
            and self.execution.common is self.flow and self.execution.export_included is self.shared_export
            and _methods_original(self.execution,original_owned_flow_execution_request.OriginalOwnedFlowExecutionRequest,self.calls)
            and self.execution.original()
            and self.preflight_capture is original_owned_flow_request._draft_capture
            and self.preflight_original is original_owned_flow_request._draft_original
            and type(self.job_manager) is self.job_kind and type(self.job_lock) is LockType
            and self.job_manager._lock is self.job_lock and self.job_manager._jobs is self.job_records
            and _methods_original(self.job_manager,self.job_kind,self.calls)
            and type(self.job_lease) is self.lease_kind and self.job_manager._leases is self.job_lease
            and self.job_lease.path==self.job_lease_path and _methods_original(self.job_lease,self.lease_kind,self.calls)
            and self.dataset_binding==(self.dataset_modules,self.dataset_index_kind,self.dataset_job_kind,self.dataset_modules[0]._JOBS_LOCK)
            and self.library_kind is self.dataset_modules[0].DatasetImportJobs
            and _HANDLERS==self.handlers and _SPEC==self.spec and _RESOURCES==self.resources
            and all(getattr(module,name) is function for module,name,function in self.calls)
            and all(function.__code__ is code for function,code in self.codes)
            and all(endpoint.__wrapped__ is function for endpoint,function in self.handlers.values())
            and all((selected is self.flow.included
                or selected is self.accounts.named.included_current
                or selected is self.shared_export and selected.owner is self.execution
                    and original_owned_flow_request._methods_original(selected,original_owned_flow_request._FlowIncluded,self.flow.calls)
                or _methods_original(selected,_AncillaryIncluded,self.calls))
                and selected.original() for selected in self.included.values())
            and all(type(route) is self.route_kind and route.path==_SPEC[name][1] and route.methods=={_SPEC[name][0]}
                and _methods_original(route,self.route_kind,self.calls)
                and (route.endpoint,route.dependant,route.dependant.call,route.app,route.get_route_handler.__func__)==self.route_bindings[name]
                for name,route in self.routes.items()))

    def denial_audit_original(self):
        origin=self.denial_audit_origin;kind=self.account_store_kind
        return (type(origin) is tuple and len(origin)==2
            and _RESOURCES.get('preview_denial_audit_origin') is origin
            and type(origin[0]) is FunctionType and origin[0].__code__ is origin[1]
            and origin[0].__globals__ is self.denial_audit_globals
            and vars(kind).get('_audit') is self.denial_audit_descriptor
            and type(self.denial_audit_descriptor) is staticmethod
            and self.denial_audit_descriptor.__func__ is origin[0]
            and kind._audit is origin[0] and self.accounts.store._audit is origin[0])

    def global_original(self):
        from backend.engine import demo_dataset,job_store
        return (self.global_demo==(demo_dataset.DEMO_ID,demo_dataset.DEMO_VERSION,demo_dataset.DEMO_TASK,
                    tuple(demo_dataset.DEMO_CLASSES),tuple(sorted(demo_dataset.DEMO_SPLITS.items())))
            and self.global_job_kind is job_store.JobStore
            and all(attempt.global_store is None or
                _store_methods_original(attempt.global_store,self.global_job_kind,self.calls)
                and attempt.global_store.path is attempt.global_store_path
                for attempt in self.records))

    def _settled_history_original(self):
        value=self._settled_history
        return (type(value)is tuple and len(value)==2 and value is self._settled_history_binding
            and type(value[0])is int and value[0]>=0 and type(self._attempt_count)is int
            and value[0]+len(self.records)==self._attempt_count
            and type(value[1])is str and len(value[1])==64 and all(c in '0123456789abcdef'for c in value[1])
            and set(self._settled_seals)<=set(self.records)
            and all(attempt.settled is True and attempt.activity is seal[0]
                and attempt.activity.token is seal[1] and attempt.name==seal[2]
                and self.pending.get(attempt.activity.token)is not attempt
                for attempt,seal in self._settled_seals.items()))

    def _room_for_original_attempt(self):
        if not self.condition._is_owned() or not self._settled_history_original():return False
        count,digest=self._settled_history
        if len(self.records)+int(count>0)<128:return True
        if self.failed or self.admission._unsupported or self.observer._uncertain:return False
        candidates=[]
        for attempt in self.records:
            seal=self._settled_seals.get(attempt)
            if (seal is None or attempt.settled is not True or attempt in self.pending.values()
                    or not _methods_original(attempt,_AncillaryAttempt,self.calls)):continue
            candidates.append(attempt)
            if len(self.records)-len(candidates)+1+1<=128:break
        else:return False
        for attempt in candidates:
            seal=self._settled_seals.pop(attempt)
            digest=hashlib.sha256(bytes.fromhex(digest)+seal[3]).hexdigest();count+=1
        self.records[:]=[attempt for attempt in self.records if attempt not in candidates]
        self._settled_history=(count,digest);self._settled_history_binding=self._settled_history
        return True

    def library_cache(self):
        """Only an already initialized original coordinator; never warm it here."""
        try:
            from backend.engine.dataset_index import index_path
            origin=self.library_origin;module=self.modules['routes_image_library']
            if (type(origin) is not tuple or len(origin)!=2 or type(origin[0]) is not FunctionType
                    or _RESOURCES.get('library_revision_origin') is not origin
                    or self.resources.get('library_revision_origin') is not origin
                    or module._revision is not origin[0] or origin[0].__code__ is not origin[1]
                    or origin[0].__globals__ is not module.__dict__):return None
            jobs=getattr(self.app.state,'dataset_imports',None)
            if (type(jobs) is not self.library_kind
                    or not _store_methods_original(jobs.index,self.dataset_index_kind,self.calls)
                    or not _store_methods_original(jobs.store,self.dataset_job_kind,self.calls)
                    or any(getattr(self.dataset_index_kind,name) is not self.resources[resource]
                        for name,resource in (('active','dataset_operation_index_active'),
                            ('revisions','dataset_operation_index_revisions'),('_connect','dataset_connection_index')))):
                return None
            path=jobs.index.path
            if (path!=index_path(self.registry.root)
                    or path.resolve(strict=True)!=path
                    or any(part.is_symlink() for part in (path,*path.parents))):return None
            return jobs,jobs.index,jobs.store,path,jobs.store.path
        except Exception:return None  # New optional inspection cannot alter business admission.

    def begin(self,activity):
        if (activity is None or not self.original() or not self.observer._valid(activity)
                or activity.ingress.get('type')!='http' or activity.scope.get('app') is not self.app):return False
        if not self._room_for_original_attempt():return False
        name=next((name for name,(method,path,_) in _SPEC.items()
            if activity.ingress.get('method')==method and (activity.ingress.get('path')==path
                or name=='get_thumbnail' and activity.ingress.get('path','').startswith('/api/dataset/thumbnail/')
                or name=='revision_source_status' and re.fullmatch(r'/api/dataset/revisions/[0-9a-f]{32}/source-status',activity.ingress.get('path','')))),None)
        headers=_headers(activity.scope)
        if name is None or headers is None:return False
        library=self.library_cache() if name=='library_images' else None
        if name=='library_images' and library is None:return False
        tokens=[v for k,v in headers if k.lower()==b'authorization'];projects=[v for k,v in headers if k.lower()==b'x-vision-project']
        contexts=[v for k,v in headers if k.lower()==b'x-vision-context']
        if (len(tokens)!=1 or not tokens[0].startswith(b'Bearer ') or not 7<len(tokens[0])<=263
                or len(projects)!=1 or not projects[0] or len(contexts)>1 or any(not v or len(v)>4096 for v in contexts)
                or any(k.lower()==b'cookie' for k,_ in headers)):return False
        try:token,project=tokens[0][7:].decode('ascii'),projects[0].decode('ascii')
        except UnicodeError:return False
        from backend.api.shared_authorization import Headers,HTTPException
        from backend.contracts.context import declared_context
        try:declared=declared_context(Headers(scope=activity.scope))
        except HTTPException:return False
        # The original authorization contract gives explicit context precedence
        # over a legacy selected-project header captured during a switch.
        if declared is not None:project=declared.project_id
        attempt=_AncillaryAttempt(self,activity,name,headers,project,token)
        attempt.library_binding=attempt._library_binding=library
        self.pending[activity.token]=attempt;self.records.append(attempt);self._attempt_count+=1;self.admission._active+=1
        return True

    def finish(self,activity):
        attempt=self.pending.get(activity.token) if activity is not None else None
        if type(attempt) is not _AncillaryAttempt:return
        if not _methods_original(attempt,_AncillaryAttempt,self.calls) or not _methods_original(attempt.auth,_AncillaryAuth,self.calls):
            _AncillaryAttempt.fail(attempt,'changed_attempt_methods');return
        if not attempt.complete(self.observer._records.get(activity.token)):
            attempt.fail('unconfirmed_lifetime');return
        row=self.observer._records[activity.token]
        seal=json.dumps({'name':attempt.name,'identity':row['identity'],
            'entered_sequence':row['entered_sequence'],'released_sequence':row['released_sequence'],
            'status':row['response_status'],'completed':row['completed'],'normal_return':row['normal_return']},
            sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        del self.pending[activity.token];self.admission._active-=1;attempt.settled=True
        self._settled_seals[attempt]=(activity,activity.token,attempt.name,seal)


class _AncillaryAuth(_SelectedCurrentAuth):
    def __init__(self,attempt,headers,project,token):
        super().__init__(attempt.owner.accounts,attempt,headers,project,token)
        from backend.api.shared_authorization import permission_action
        self.action=permission_action(attempt.activity.ingress['path'],attempt.activity.ingress['method'])
        self.denial_frame=self.denial_binding=self._denial_binding=self.denial_preload=None
        self.denial_preload_live=False;self.denial_databases=[];self.denial_token=object();self.denial_channels=None

    def begin_db(self,store):
        if self.attempt.name=='list_projects' and self.attempt.worker_live:return self.global_project_db(store)
        names=('bind_workspace','authenticate','project_role','project_for','project_role','authorize','record_decision')
        count=5 if self.attempt.name=='list_projects' else (7 if self.attempt.name=='preview_pipeline_change' else 6)
        expected=names[len(self.databases)] if len(self.databases)<count else None
        function=getattr(type(store),expected,None) if expected is not None else None;function=getattr(function,'__wrapped__',function)
        frame=sys._getframe(1)
        for _ in range(10):
            if frame is None or function is not None and frame.f_code is function.__code__:break
            frame=frame.f_back
        authorization=self._authorization_frame()
        valid=(self.original() and store is self.owner.store and not self.db_live and self.middleware_calls==1
            and not self.middleware_returns and authorization is not None and frame is not None
            and function is not None and frame.f_code is function.__code__ and frame.f_locals.get('self') is store)
        if valid and self.attempt.name=='preview_pipeline_change':
            if self.denial_frame is None:
                self.denial_frame=authorization
                self.denial_channels=authorization.f_locals.get('receive'),authorization.f_locals.get('send')
            valid=authorization is self.denial_frame
        if expected in {'project_role','project_for'}:
            valid=(valid and type(self.account) is dict and frame.f_locals.get('user_id')==self.account.get('id')
                and frame.f_locals.get('project_id')==self.project_id)
        elif expected=='authorize':
            valid=(valid and type(self.account) is dict and frame.f_locals.get('actor')==self.account.get('id')
                and frame.f_locals.get('project')==self.project_id and frame.f_locals.get('session_token')==self.token
                and frame.f_locals.get('action')==self.action)
        elif expected=='record_decision':
            decision=self.attempt.scope.get('state',{}).get('permission_decision')
            valid=(valid and self.attempt.name=='preview_pipeline_change' and frame.f_locals.get('decision') is decision
                and decision is not None and decision.allowed and decision.action==self.action
                or valid and self.denial_entry(frame,authorization))
        if not valid:self.fail('database_entry')
        self.db_live=True
        return valid,expected

    def denial_entry(self,frame,authorization):
        from backend.contracts.authentication import PermissionDecision
        attempt=self.attempt;state=attempt.scope.get('state',{})
        decision=frame.f_locals.get('decision')
        valid=(attempt.name=='preview_pipeline_change' and type(self.account) is dict
            and self.denial_parent_original(authorization) and attempt.scope.get('method')=='POST'
            and attempt.scope.get('path')=='/api/flowchart/pipeline/diff'
            and authorization is self.denial_frame and frame.f_back is authorization
            and type(decision) is PermissionDecision and decision is authorization.f_locals.get('denied')
            and decision.allowed is False and decision.role=='viewer'
            and decision.reason=='role_or_membership_required' and decision.action==self.action=='flow.execute'
            and decision.actor_id==self.account.get('id') and decision.workspace_id==self.owner.workspace
            and decision.project_id==self.project_id and type(decision.membership_revision) is int
            and decision.membership_revision>0 and decision.resource_revision is None
            and decision.resource_revision_verified is False and authorization.f_locals.get('role')=='viewer'
            and authorization.f_locals.get('allowed') is False and 'permission_decision' not in state
            and authorization.f_locals.get('account') is self.account
            and authorization.f_locals.get('token')==self.token
            and authorization.f_locals.get('project') is state.get('scoped_project')
            and type(state.get('scoped_project')) is dict and state['scoped_project'].get('id')==self.project_id
            and self.databases==['bind_workspace','authenticate','project_role','project_for','project_role','authorize']
            and self.denial_binding is None)
        if valid:
            self.denial_binding=(self,attempt,attempt.activity,authorization,decision,self.denial_preload,self.denial_token)
            self._denial_binding=self.denial_binding
        return valid

    def denial_parent_original(self,authorization):
        if (authorization is None or authorization is not self.denial_frame or type(self.denial_channels) is not tuple
                or len(self.denial_channels)!=2 or authorization.f_locals.get('receive') is not self.denial_channels[0]
                or authorization.f_locals.get('send') is not self.denial_channels[1]):return False
        owner=self.attempt.owner;frame=sys._getframe(1);authorization_seen=False
        for _ in range(64):
            if frame is None:return False
            if frame is authorization:authorization_seen=True
            if frame.f_code is type(owner.owned)._observed_call.__code__:
                return (authorization_seen and frame.f_locals.get('self') is owner.owned and frame.f_locals.get('admitted') is True
                    and frame.f_locals.get('scope') is self.attempt.scope
                    and frame.f_locals.get('activity') is self.attempt.activity
                    and frame.f_locals.get('tracked_receive') is self.denial_channels[0])
            frame=frame.f_back
        return False

    def preload_original(self):
        attempt=self.attempt;frame=self.denial_frame
        return (self.original() and attempt.name=='preview_pipeline_change' and not attempt.failed
            and not attempt.worker_live and attempt.worker is None and attempt.entries==0
            and frame is self.denial_frame and frame is not None and self.denial_parent_original(frame)
            and self.databases==['bind_workspace','authenticate','project_role','project_for']
            and self.middleware_calls==1 and self.middleware_returns==0 and not self.db_live
            and _ACTIVE.get() is attempt)

    def preload(self,function,path):
        frame=self._authorization_frame();selected=frame.f_locals.get('selected') if frame is not None else None
        valid=(self.preload_original() and type(selected) is dict and selected.get('id')==self.project_id
            and Path(selected.get('path',''))==path and self.denial_preload is None
            and sys._getframe(1).f_back is frame)
        live={'path':path,'valid':valid,'reads':[],'guard':None,'normal':None,'returned':None}
        self.denial_preload=live;self.denial_preload_live=True
        try:
            result=function(path)
            live['valid'] &= (self.preload_original() and type(result) is dict
                and result.get('id')==self.project_id and result.get('project_dir')==str(path))
            live['returned']=result
            return result
        finally:self.denial_preload_live=False

    def preload_read(self,path,role,mode,encoding,errors,caller):
        from backend.api import routes_project
        from backend.engine import project_migration,project_labelsets
        live=self.denial_preload;root=live['path']
        expected=(routes_project._load_project_unfenced if role=='manifest_text' else
            project_migration._read if role=='manifest_bytes' else project_labelsets.load_labelsets if role=='labelsets'
            else project_migration.normalize_legacy_manifest.__wrapped__ if role=='migration_receipt' else None)
        live['valid'] &= (self.preload_original() and expected is not None and caller.f_code is expected.__code__
            and not any(member.is_symlink() for member in (path,*path.parents))
            and (path==root/'project.json' if role in {'manifest_text','manifest_bytes'} else
                 path==root/'labelsets.json' if role=='labelsets' else
                 role=='migration_receipt' and path.is_relative_to(root/'.migrations') and path.name=='receipt.json'))
        with path.open(mode=mode,encoding=encoding,errors=errors) as handle:
            good=type(handle) in (io.TextIOWrapper,io.BufferedReader) and not handle.closed
            result=handle.read()
        live['valid'] &= good and handle.closed and self.preload_original() and len(live['reads'])<64
        if len(live['reads'])<64:live['reads'].append((handle,path,role))
        return result

    @contextmanager
    def preload_maintenance(self,manager,path):
        from backend.engine import migration_guard,original_lock_acquisition
        live=self.denial_preload;handle=record=None
        live['valid'] &= (self.preload_original() and path==live['path'] and type(manager) is _GeneratorContextManager
            and manager.gen.gi_code is migration_guard.maintenance_guard.__wrapped__.__wrapped__.__code__)
        with manager as value:
            frame=manager.gen.gi_frame;handle=frame.f_locals.get('handle') if frame is not None else None
            record=frame.f_locals.get('ownership') if frame is not None else None
            observation=record.get('original_lock_observation') if type(record) is dict else None
            good=(type(record) is dict and record.get('active') is True and record.get('exclusive') is False
                and record.get('key')==str(path.resolve()) and record.get('owner')==migration_guard._execution_owner()
                and type(observation) is original_lock_acquisition.OriginalLockAcquisition
                and _methods_original(observation,original_lock_acquisition.OriginalLockAcquisition,self.attempt.owner.calls)
                and isinstance(handle,io.IOBase) and not handle.closed)
            if good:
                try:observation.current(record=record)
                except (OSError,ValueError):good=False
            live['valid'] &= good
            yield value
        live['valid'] &= (self.preload_original() and manager.gen.gi_frame is None
            and handle is not None and handle.closed and type(record) is dict and record.get('active') is False)
        live['guard']=manager,handle,record

    def denial_complete(self,row):
        attempt=self.attempt;state=attempt.scope.get('state',{});bound=self.denial_binding;live=self.denial_preload
        frame=self.denial_frame;owner=attempt.owner
        if (type(bound) is not tuple or len(bound)!=7 or bound is not self._denial_binding
                or any(a is not b for a,b in zip(bound,(self,attempt,attempt.activity,frame,bound[4],live,self.denial_token)))
                or type(live) is not dict or live.get('valid') is not True or live.get('guard') is None
                or type(live.get('reads')) is not list or not live['reads'] or live.get('normal') is None):return False
        for _,connection in self.denial_databases:
            if type(connection) is not sqlite3.Connection:return False
            try:connection.in_transaction
            except sqlite3.ProgrammingError:pass
            else:return False
        manager,handle,record=live['guard']
        if (type(manager) is not _GeneratorContextManager or manager.gen.gi_frame is not None
                or not isinstance(handle,io.IOBase) or not handle.closed
                or type(record) is not dict or record.get('active') is not False):return False
        project=live['returned'];decision=bound[4];reject=frame.f_locals.get('reject') if frame is not None else None
        code,returns=owner.denial_return_binding
        rejection=tuple(c for c in code.co_consts if hasattr(c,'co_name') and c.co_name=='reject')
        return bool(self.original() and not attempt.failed and self.middleware_calls==self.middleware_returns==1
            and not self.db_live and self.method_name is None and not self.denial_preload_live
            and self.databases==[name for name,_ in self.denial_databases]
            ==['bind_workspace','authenticate','project_role','project_for','project_role','authorize','record_decision']
            and len({id(connection) for _,connection in self.denial_databases})==7
            and frame is not None and frame.f_code is code and len(returns)==1 and returns[0] is not None
            and frame.f_lasti in returns and frame.f_locals.get('denied') is decision
            and type(reject) is FunctionType and len(rejection)==1 and reject.__code__ is rejection[0]
            and reject.__globals__ is self.authorization.__globals__ and reject.__closure__ is not None
            and all(dict(zip(reject.__code__.co_freevars,(cell.cell_contents for cell in reject.__closure__))).get(name) is value
                for name,value in zip(('scope','receive','send'),(attempt.scope,*self.denial_channels)))
            and project is state.get('scoped_project') is frame.f_locals.get('project')
            and state.get('account_user') is self.account and state.get('account_session_token')==self.token
            and 'permission_decision' not in state and 'project_context' not in state
            and all(handle.closed for handle,_,_ in live['reads'])
            and {'manifest_text','manifest_bytes','labelsets'}<=set(role for _,_,role in live['reads'])
            and live['normal'].get('migration_required') is False and live['normal'].get('status')=='current'
            and not attempt.entries and attempt.worker is None and not attempt.worker_live and not attempt.storage
            and not attempt.events and not attempt.resolutions and not attempt.inflight
            and not attempt.files and not attempt.images and not attempt.processes and not attempt.dataset_live
            and row is not None and row['carrier'] is attempt.activity and row['released'] and row['normal_return']
            and row['completed'] and row['response_status']==403 and not row['transport_failed'] and not row['framing_error']
            and row['send_attempt'] is None and row['start_forwarded'] and row['terminal_forwarded']
            and not row['trailers_expected'] and (owner.deadline is None or time.monotonic()<owner.deadline))


    def global_project_db(self,store):
        attempt=self.attempt
        functions={name:getattr(type(store),name) for name in ('projects_for','project_role')}
        functions={name:getattr(value,'__wrapped__',value) for name,value in functions.items()}
        frame=sys._getframe(1);selected=None
        for _ in range(16):
            if frame is None:break
            selected=next((name for name,function in functions.items() if frame.f_code is function.__code__),None)
            if selected is not None:break
            frame=frame.f_back
        valid=(self.original() and attempt.worker_original() and not attempt.failed
            and attempt.handler_frame() is not None and store is self.owner.store
            and not self.db_live and self.middleware_calls==1 and not self.middleware_returns
            and self.databases==['bind_workspace','authenticate','project_role','project_for','project_role']
            and attempt.global_projects_live and selected is not None and frame.f_locals.get('self') is store
            and type(self.account) is dict and frame.f_locals.get('user_id')==self.account.get('id'))
        if selected=='projects_for':
            valid=valid and not attempt.global_account_dbs and not attempt.global_project_roles
        elif selected=='project_role':
            parent=frame.f_back
            if (parent is not None and parent.f_code is _RESOURCES['global_project_role'].__code__
                    and parent.f_locals.get('store') is store and parent.f_locals.get('function') is functions['project_role']):
                parent=parent.f_back
            if parent is not None and parent.f_code in functions['projects_for'].__code__.co_consts:
                parent=parent.f_back
            valid=(valid and parent is not None and parent.f_code is functions['projects_for'].__code__
                and parent.f_locals.get('self') is store and type(frame.f_locals.get('project_id')) is str
                and any(type(row) is dict and row.get('id')==frame.f_locals.get('project_id')
                    for row in parent.f_locals.get('rows',())))
        if not valid:self.fail('project_list_database_entry')
        self.db_live=True
        return valid,('global_project',selected,frame)

    def end_db(self,connection,entry,body_error,exit_error):
        valid,expected=entry
        if type(expected) is not tuple or expected[:1]!=('global_project',):
            before=len(self.databases)
            result=super().end_db(connection,entry,body_error,exit_error)
            if self.attempt.name=='preview_pipeline_change' and len(self.databases)==before+1:
                self.denial_databases.append((expected,connection))
            return result
        _,name,frame=expected;attempt=self.attempt;closed=False
        if type(connection) is sqlite3.Connection:
            try:connection.in_transaction
            except sqlite3.ProgrammingError:closed=True
        if (not valid or not attempt.worker_original() or self.db_live is not True
                or not closed or body_error is not None or exit_error is not None):
            self.fail('project_list_database_exit')
        else:
            attempt.global_account_dbs.append(name)
            if name=='projects_for':
                rows=frame.f_locals.get('rows')
                if type(rows) is not list or len(rows)>128 or not all(type(row) is dict and set(row)=={'id','path'} for row in rows):
                    self.fail('project_list_rows')
                else:attempt.global_project_rows=tuple(dict(row) for row in rows)
            else:attempt.global_project_roles.append(frame.f_locals.get('project_id'))
        self.db_live=False


class _AncillaryAttempt:
    def __init__(self,owner,activity,name,headers,project,token):
        self.owner=self.adapter=owner;self.activity,self.name,self.headers=activity,name,headers
        self.token=object();self.binding=self,owner,activity,activity.token,self.token
        self.scope=activity.scope;self.query=self.scope.get('query_string',b'');self.header_container=None
        self.failed=self.settled=self.worker_live=self.handler_done=False
        self.entries=0;self.worker=None;self.values=self.original_values=self.request=None
        self.values_identity=self.original_values_identity=None;self.value_rows=None
        self.events=[];self.inflight={};self.resolutions=[];self.storage=0
        self.files=[];self.images=[];self.contexts=[];self.profiles=[];self.profile_live=None
        self.selected_image_inputs=[]
        self.preflight_store=self.preflight_folder=self.preflight_handler=None
        self.preflight_root_pin=self.preflight_folder_pin=self.preflight_listing=None
        self.preflight_list_result=self.preflight_list_binding=self.preflight_result_binding=None
        self.preflight_reads=[];self.preflight_rows=[]
        self.metadata=[];self.metadata_live=None;self.processes=[];self.process_live=False
        self.package_rows=[];self.result=None;self.status=None;self.body_binding=None
        self.lease_connections=[];self.job_lists=[];self.job_snapshots=[];self.job_rows=[];self.job_live=None
        self.workspace_listings=[]
        self.draft_active_reads=[]
        self.dataset_operations=[];self.dataset_connections=[];self.dataset_live=[]
        self.library_binding=self._library_binding=None;self.library_connections=[]
        self.dataset_objects=[];self.provenance_operations=[];self.provenance_connections=[]
        self.thumbnail_operations=[];self.thumbnail_spools=[];self.thumbnail_publications=[]
        self.thumbnail_response=None;self.thumbnail_binding=None;self.thumbnail_response_live=self.thumbnail_response_done=False
        self.thumbnail_stat_bound=False
        self.thumbnail_headers_published=False;self.thumbnail_headers_receipt=None
        self.auth=_AncillaryAuth(self,headers,project,token)
        self.global_projects_live=False;self.global_project_rows=None;self.global_project_roles=[]
        self.global_account_dbs=[];self.global_role_results=[];self.global_member_rows=None;self.global_loaded=[];self.global_project_live=None
        self.global_project_guards=[];self.global_project_reads=[];self.global_project_normalized=[]
        self.global_team=[];self.global_factory_live=False;self.global_store=self.global_store_path=None
        self.global_job_live=None;self.global_jobs=[];self.global_databases=[];self.global_transactions=[]
        self.global_job_calls=[];self.global_cursor=None;self.global_cursor_live=False

    def fail(self,reason):
        self.failed=True;self.owner.admission._unsupported.add('uncovered_request_protocol')
        observed_reason='owned_ancillary_'+reason
        with self.owner.observer._lock:
            failures=self.owner.observer._failures
            first=(type(failures) is list and len(failures)<=32
                and all(type(value) is str for value in failures) and observed_reason not in failures)
            self.owner.observer.poison(observed_reason)
            try:self.owner.observer.observe_ancillary_failure(self,observed_reason,first)
            except Exception:pass  # Optional observation must not replace the original failure.
    def __copy__(self):self.fail('attempt_copy');raise ValueError('Original ancillary attempt cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):return self.__copy__()

    def base(self):
        return (not self.settled and self.binding==(self,self.owner,self.activity,self.activity.token,self.token)
            and self.owner.pending.get(self.activity.token) is self and self.owner.original()
            and _methods_original(self,_AncillaryAttempt,self.owner.calls) and _methods_original(self.auth,_AncillaryAuth,self.owner.calls)
            and all(_methods_original(process,self.owner.process_kind,self.owner.calls) for process,_,_,_,_ in self.processes)
            and all(type(store) is self.owner.lease_kind and _methods_original(store,self.owner.lease_kind,self.owner.calls)
                and store.path==path for store,_,_,path in self.lease_connections)
            and all(kind in {'index','ledger'} and closed and _store_methods_original(store,
                self.owner.dataset_index_kind if kind=='index' else self.owner.dataset_job_kind,self.owner.calls)
                for kind,store,closed in self.dataset_connections)
            and self.activity.scope is self.scope and self.scope.get('query_string',b'') is self.query
            and _headers(self.scope)==self.headers and (self.header_container is None or self.scope.get('headers') is self.header_container)
            and (self.values is None or self.values is self.values_identity
                and self.original_values is self.original_values_identity
                and set(self.values)=={key for key,_ in self.value_rows}
                and all(self.values.get(key) is value for key,value in self.value_rows)
                and (self.values.get('_owned_http_request') if '_owned_http_request' in self.values else self.values.get('request')) is self.request)
            and all(_workspace_iterator_original(row) and row['path']==self.workspace_root(row['role'])
                for row in self.workspace_listings)

            and all(_methods_original(value,kind,self.owner.calls) for value,kind in self.dataset_objects)
            and (self.library_binding is None or self.library_original())
            and (self.preflight_store is None or self.preflight_store_original())
            and (self.body_binding is None or self.original_values.get('pipeline') is self.body_binding[0]
                and self.body_binding[0].model_dump()==self.body_binding[1]))

    def original(self):
        current=_context();context=self.activity.context
        task=all(current[i] is context[i] for i in (0,1,2)) and current[3]==context[3]
        worker=self.worker_live and self.worker==(os.getpid(),threading.current_thread()) and current[0] is None
        return self.base() and (task or worker)

    @contextmanager
    def effect(self,kind,identity):
        ticket=object();valid=self.original();self.inflight[ticket]=kind,identity
        if not valid:self.fail('effect_entry')
        try:yield
        except BaseException:self.fail(kind+'_failed');raise
        else:
            if not valid or not self.original() or ticket not in self.inflight or len(self.events)>=32:self.fail('effect_exit')
            else:self.events.append((kind,identity));del self.inflight[ticket]

    def activate(self,values,original_values,request,endpoint,function):
        caller=sys._getframe(1).f_back;frame=caller;handler=response=owned=False
        for _ in range(64):
            if frame is None:break
            if frame.f_code in self.owner.handler_codes:handler|=frame.f_locals.get('request') is request
            if frame.f_code in self.owner.response_codes:response|=frame.f_locals.get('scope') is self.scope
            if frame.f_code is type(self.owner.owned)._observed_call.__code__:
                owned|=frame.f_locals.get('self') is self.owner.owned and frame.f_locals.get('activity') is self.activity
            frame=frame.f_back
        route=self.owner.routes[self.name];dependant=caller.f_locals.get('dependant') if caller is not None else None
        included=self.owner.route_included[self.name]
        valid=(self.original() and not self.failed and _ACTIVE.get() is self and self.entries==0
            and request is not None and request.scope is self.scope and self.scope.get('route') is route
            and self.scope.get('endpoint') is endpoint and self.owner.handlers[self.name]==(endpoint,function)
            and caller is not None and caller.f_code is self.owner.dispatch.__code__ and dependant is not None
            and dependant.call is endpoint and caller.f_locals.get('is_coroutine') is True
            and set(caller.f_locals.get('values',{}))==set(values)
            and all(caller.f_locals['values'][key] is value for key,value in values.items())
            and handler and response and owned and (included is None or
                (self.global_project_selected(included,route,dependant) if self.name=='list_projects'
                 else included.selected(self.scope,route,dependant))))
        self.entries+=1;self.values,self.original_values,self.request=values,original_values,request
        self.values_identity,self.original_values_identity=values,original_values;self.value_rows=tuple(values.items())
        self.header_container=self.scope.get('headers');self.auth.header_container=self.header_container
        if self.name=='preview_pipeline_change':
            from backend.api.routes_flowchart import FlowchartPipeline
            graph=original_values.get('pipeline')
            if type(graph) is not FlowchartPipeline:valid=False
            else:self.body_binding=graph,graph.model_dump()
        if not valid:self.fail('handler_entry')
        return valid

    def enter_worker(self,valid,values):
        caller=sys._getframe(1);endpoint,function=self.owner.handlers[self.name]
        codes=tuple(code for code in endpoint.__code__.co_consts if type(code) is type(endpoint.__code__) and code.co_name=='invoke')
        if (not valid or not self.base() or self.failed or self.worker is not None or _ACTIVE.get() is not self
                or len(codes)!=1 or caller.f_code is not codes[0] or caller.f_locals.get('attempt') is not self
                or caller.f_locals.get('valid') is not True or caller.f_locals.get('function') is not function
                or caller.f_locals.get('original_values') is not values):self.fail('worker_entry')
        self.worker=os.getpid(),threading.current_thread();self.worker_live=True

    def worker_original(self):return self.original() and not self.failed and self.worker_live and _ACTIVE.get() is self

    def handler_frame(self):
        function=self.owner.handlers[self.name][1];frame=sys._getframe(1)
        for _ in range(32):
            if frame is None:return None
            if frame.f_code is function.__code__:
                return frame if all(frame.f_locals.get(key) is value for key,value in self.original_values.items()) else None
            frame=frame.f_back
        return None

    def resource_entry(self,kind):
        if not self.worker_original() or self.handler_frame() is None:self.fail(kind+'_entry');return False
        return True

    def preflight_store_original(self):
        origin=self.owner.preflight_origin;store=self.preflight_store
        return (type(origin)is tuple and len(origin)==6
            and _RESOURCES.get('preflight_store_origin')is origin
            and origin[0]is self.owner.preflight_module.PreflightStore
            and origin[0].__init__ is origin[1] and origin[1].__code__ is origin[2]
            and origin[0].list is origin[3] and origin[3].__code__ is origin[4]
            and origin[1].__globals__ is origin[3].__globals__ is origin[5]
            and origin[5] is self.owner.preflight_module.__dict__
            and (store is None or type(store)is origin[0] and store.folder is self.preflight_folder
                and _methods_original(store,origin[0],self.owner.calls)))

    def preflight_observation(self,stage,values,frame):
        origin=self.owner.preflight_origin;function=self.owner.handlers[self.name][1]
        if stage=='store':
            store,project=values;handler=self.handler_frame()
            version=self.original_values.get('version_id')
            valid=(self.worker_original() and self.name=='list_flow_preflights' and self.preflight_store is None
                and self.preflight_store_original() and type(project)is dict
                and project is self.scope.get('state',{}).get('scoped_project')
                and (version is None or type(version)is str and re.fullmatch('[0-9a-f]{32}',version))
                and frame.f_code is self.owner.modules['routes_export']._preflight_store.__code__
                and frame.f_locals.get('project')is project and frame.f_locals.get('store')is store
                and frame.f_back is handler
                and handler is not None and handler.f_code is function.__code__)
            if not valid:self.fail('preflight_store');return
            root=Path(project['project_dir']);folder=root/'deployment_preflight'
            root_pin=_preflight_path_pin(root,True)
            folder_pin=_preflight_path_pin(folder,True,True)
            if type(store)is not origin[0] or store.folder!=folder:self.fail('preflight_store_path');return
            self.preflight_store,self.preflight_folder,self.preflight_handler=store,store.folder,handler
            self.preflight_root_pin,self.preflight_folder_pin=root_pin,folder_pin
            return
        store=values[0]
        valid=(self.worker_original() and self.name=='list_flow_preflights' and store is self.preflight_store
            and self.preflight_store_original() and frame.f_code is origin[4] and frame.f_locals.get('self') is store
            and frame.f_back is self.preflight_handler and self.handler_frame() is self.preflight_handler)
        if not valid:self.fail('preflight_observation');return
        if stage=='row':
            _,path,report,row,rows=values
            held=next((item for item in self.preflight_reads if item['path'] is path),None)
            keys=('report_id','checked_at','status','recipe_release','target_identity','counts')
            valid=(held is not None and held['text'] is not None and held['handle'].closed
                and frame.f_locals.get('path') is path and frame.f_locals.get('report') is report
                and frame.f_locals.get('row') is row and frame.f_locals.get('rows') is rows
                and type(report)is dict and type(row)is dict and tuple(row)==keys
                and all(row[key]is report.get(key) for key in keys)
                and type(rows)is list and len(rows)==len(self.preflight_rows)+1 and rows[-1]is row
                and all(now is old for now,old in zip(rows,self.preflight_rows))
                and len(self.preflight_rows)<128
                and all(row[key]is None or type(row[key])is str for key in ('report_id','checked_at')))
            if not valid:self.fail('preflight_row');return
            held['report']=report;self.preflight_rows.append(row)
        elif stage=='return':
            _,rows,result=values;listing=self.preflight_listing
            valid=(self.preflight_list_result is None and type(rows)is list and type(result)is list
                and frame.f_locals.get('rows')is rows and frame.f_locals.get('result')is result
                and len(rows)==len(self.preflight_rows)==len(self.preflight_reads)==len(result)
                and all(now is old for now,old in zip(rows,self.preflight_rows))
                and len({id(row)for row in result})==len(result)
                and all(any(row is original for original in rows)for row in result)
                and result==sorted(rows,key=lambda row:(row['checked_at']or'',row['report_id']or''),reverse=True)
                and listing is not None and listing['complete'] and not listing['live']
                and (listing['iterator']is None or _workspace_iterator_original(listing)
                    and listing['wrapper'].gi_frame is None)
                and len(listing['members'])==len(self.preflight_reads)
                and all(any(item['path']is member for item in self.preflight_reads)for member in listing['members']))
            if not valid:self.fail('preflight_list_return');return
            self.preflight_list_result=result
            self.preflight_list_binding=self.owner.preflight_capture(result,(),[4096])
        else:self.fail('preflight_stage')

    def preflight_read(self,store,path,handle,frame):
        listing=self.preflight_listing
        valid=(self.worker_original() and self.name=='list_flow_preflights' and self.preflight_store_original()
            and store is self.preflight_store and listing is not None and listing['live']
            and _workspace_iterator_original(listing) and any(path is member for member in listing['members'])
            and frame.f_code is self.owner.preflight_origin[4] and frame.f_locals.get('self')is store
            and frame.f_locals.get('path')is path and frame.f_back is self.preflight_handler
            and type(path)is type(Path('.')) and path.parent==self.preflight_folder and path.suffix=='.json'
            and not any(item['path']is path for item in self.preflight_reads) and len(self.preflight_reads)<128
            and type(handle)is io.TextIOWrapper and not handle.closed)
        if not valid:self.fail('preflight_file_entry');return None
        pin=_preflight_path_pin(path)
        if _preflight_raw9(os.fstat(handle.fileno()))!=pin:self.fail('preflight_file_descriptor');return None
        row={'path':path,'handle':handle,'raw9':pin,'text':None,'report':None}
        self.preflight_reads.append(row)
        return row

    def preflight_files_current(self):
        project=self.scope.get('state',{}).get('scoped_project')
        return (type(project)is dict and self.preflight_store_original()
            and _preflight_path_pin(Path(project['project_dir']),True)==self.preflight_root_pin
            and _preflight_path_pin(self.preflight_folder,True,True)==self.preflight_folder_pin
            and all(item['handle'].closed and type(item['text'])is str and type(item['report'])is dict
                and _preflight_path_pin(item['path'])==item['raw9']for item in self.preflight_reads))

    def preflight_returned(self,result):
        frame=self.preflight_handler;rows=self.preflight_list_result
        version=self.original_values.get('version_id')
        valid=(frame is not None and frame.f_code is self.owner.handlers[self.name][1].__code__
            and frame.f_locals.get('request')is self.request and frame.f_locals.get('version_id')is version
            and frame.f_locals.get('store')is self.preflight_store and frame.f_locals.get('rows')is rows
            and type(rows)is list and self.preflight_list_binding is not None
            and self.owner.preflight_original(rows,self.preflight_list_binding)
            and type(result)is dict and set(result)=={'reports'} and type(result['reports'])is list
            and len(result['reports'])==len([row for row in rows if version is None or(row.get('recipe_release')or{}).get('version_id')==version])
            and all(actual is wanted for actual,wanted in zip(result['reports'],
                [row for row in rows if version is None or(row.get('recipe_release')or{}).get('version_id')==version]))
            and self.preflight_files_current())
        if not valid:return False
        self.preflight_result_binding=self.owner.preflight_capture(result,(),[4096])
        self.preflight_handler=None
        return True

    def preflight_response_original(self):
        try:
            return (self.preflight_handler is None and self.preflight_result_binding is not None
                and self.owner.preflight_original(self.result,self.preflight_result_binding)
                and self.owner.preflight_original(self.preflight_list_result,self.preflight_list_binding)
                and self.preflight_files_current())
        except Exception:return False

    def library_original(self):
        current=self.owner.library_cache()
        return (self.library_binding is self._library_binding
            and type(self.library_binding) is tuple and type(current) is tuple
            and len(current)==len(self.library_binding)==5
            and all(now is held for now,held in zip(current,self.library_binding)))

    def library_revision_frame(self):
        function=self.owner.modules['routes_image_library']._revision
        frame=sys._getframe(1)
        for _ in range(16):
            if frame is None:return None
            if frame.f_code is function.__code__:
                return frame if frame.f_locals.get('request') is self.request else None
            frame=frame.f_back
        return None

    def library_no_revision_return(self,error):
        """The original warm two-read refusal, not a status-only GET allowance."""
        from fastapi import HTTPException
        function=self.owner.modules['routes_image_library']._revision
        if (self.name!='library_images' or not self.library_original()
                or type(error) is not HTTPException or error.status_code!=409
                or error.detail!='No validated dataset revision is active; validate the source and accept a revision first'
                or error.headers is not None or self.original_values.get('revision_id') is not None):return False
        frame=None;tb=error.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code is function.__code__:frame=tb.tb_frame
            tb=tb.tb_next
        operations=self.dataset_operations
        if (frame is None or frame.f_locals.get('request') is not self.request
                or frame.f_locals.get('project') is not self.scope['state'].get('scoped_project')
                or frame.f_locals.get('_context') is not self.scope['state'].get('project_context')
                or frame.f_locals.get('index') is not self.library_binding[1]
                or frame.f_locals.get('chosen') is not None
                or [kind for kind,_ in operations]!=['index_revisions','index_active']
                or type(operations[0][1]) is not list or operations[1][1] is not None
                or frame.f_locals.get('rows')!={row['revision_id']:row for row in operations[0][1]}
                or len(self.dataset_connections)!=2 or len(self.library_connections)!=2
                or any(kind!='index' or store is not self.library_binding[1] or not closed
                    for kind,store,closed in self.dataset_connections)
                or self.files or self.images or self.profiles or self.metadata or self.processes):return False
        for connection in self.library_connections:
            if type(connection) is not sqlite3.Connection:return False
            try:connection.in_transaction
            except sqlite3.ProgrammingError:pass
            else:return False
        return True

    def workspace_root(self,role):
        project=self.scope.get('state',{}).get('scoped_project')
        if role=='workspace_template' and self.name=='templates':
            actor=self.scope.get('state',{}).get('account_user')
            if type(actor) is not dict or type(actor.get('id')) is not str:return None
            import hashlib
            return self.owner.workspace_template_root/'accounts'/hashlib.sha256(actor['id'].encode()).hexdigest()
        if role=='workspace_comparison' and self.name=='comparisons' and type(project) is dict:
            return Path(project['project_dir'])/'flowcharts'/'comparisons'
        return None

    def selected_image_input(self,path,include_source=False):
        """Narrow additional proof for the original current-project file picker.

        The imported-source/name branch remains separate. New internal PNG and
        literal preview reads retain their canonical original query and file
        identity until original HTTP completion; no alias grants ownership.
        """
        project=self.scope.get('state',{}).get('scoped_project')
        if (type(project) is not dict or type(include_source) is not bool
                or type(path) is not type(Path('.')) or self.original_values is None
                or self.name not in {'image_info','get_thumbnail'}):return False
        key='image_path' if self.name=='image_info' else 'file_path'
        raw=self.original_values.get(key)
        fields=tuple(project.get(name) for name in ('project_dir','dataset_dir','source_dataset_dir'))
        if type(raw) is not str or raw!=str(path) or not all(type(value) is str for value in fields):return False
        root,dataset,source=map(Path,fields)
        try:
            roots=(root,dataset,source) if include_source else (root,dataset)
            if (any(not item.is_absolute() or item.resolve()!=item
                    or any(part.is_symlink() for part in (item,*item.parents)) for item in (*roots,path))
                    or dataset!=root/'dataset' or not all(item.is_dir() for item in roots)
                    or not (path.is_relative_to(dataset) or include_source and path.is_relative_to(source))):return False
            value=path.lstat()
            if value.st_mode&0o170000!=0o100000 or value.st_nlink!=1:return False
            identity=(value.st_dev,value.st_ino,value.st_mode,value.st_nlink,value.st_size,
                      value.st_mtime_ns,value.st_ctime_ns,value.st_uid,value.st_gid)
            for held in self.selected_image_inputs:
                if held[2]==path:
                    return held[0] is project and held[1]==fields and held[3]==include_source and held[4]==identity
            if not self.worker_original() or self.handler_frame() is None:return False
            self.selected_image_inputs.append((project,fields,path,include_source,identity))
            return True
        except (OSError,ValueError,TypeError):return False

    def selected_image_handle(self,path,handle,include_source=False):
        if (type(handle) is not io.BufferedReader or handle.closed
                or not self.selected_image_input(path,include_source)):return False
        try:value=os.fstat(io.BufferedReader.fileno(handle))
        except (OSError,ValueError,TypeError):return False
        identity=(value.st_dev,value.st_ino,value.st_mode,value.st_nlink,value.st_size,
                  value.st_mtime_ns,value.st_ctime_ns,value.st_uid,value.st_gid)
        return any(row[2]==path and row[3]==include_source and row[4]==identity
                   for row in self.selected_image_inputs)

    def selected_image_inputs_valid(self):
        if self.name not in {'image_info','get_thumbnail'}:return not self.selected_image_inputs
        try:
            project=self.scope['state']['scoped_project']
            key='image_path' if self.name=='image_info' else 'file_path'
            path=Path(self.original_values[key]).resolve()
            imported=path.is_relative_to(Path(project['source_dataset_dir']).resolve())
            legacy=imported and (self.name=='image_info' or path.name==self.original_values['image_path'])
            return (len(self.selected_image_inputs)==(0 if legacy else 1)
                and all(self.selected_image_input(row[2],row[3]) for row in self.selected_image_inputs))
        except (OSError,ValueError,TypeError,KeyError):return False

    def record_file(self,handle,path,role):
        original_path=path;project=self.scope.get('state',{}).get('scoped_project');path=Path(path)
        valid=self.resource_entry('file') and type(project) is dict and (type(handle) in (io.TextIOWrapper,io.BufferedReader,io.BufferedRandom,io.FileIO) or role=='thumbnail_cache' and type(handle) is io.BufferedWriter) and not handle.closed
        root=Path(project['project_dir']);source=Path(project['source_dataset_dir'])
        if role=='thumbnail_source':valid=valid and self.name=='get_thumbnail' and path.resolve()==self.thumbnail_source()
        elif role=='thumbnail_cache':valid=valid and self.name=='get_thumbnail' and path.parent==self.owner.thumbnail_cache
        elif role=='dataset_UI':
            roots=(root,source,Path(project['annotations_dir']))
            if self.name=='packages':
                roots=(root/'exports'/'flows',root/'exports'/'optimization_jobs',root/'flowcharts'/'versions')
                valid=(valid and not any(member.is_symlink() for member in (path,*path.parents))
                    and any(path.resolve().is_relative_to(r.resolve()) for r in roots))
            else:valid=valid and self.name in _DATASET_NAMES and any(path.resolve().is_relative_to(r.resolve()) for r in roots)
        elif role=='profile':valid=valid and self.profile_live is not None and path==self.profile_live[0].path
        elif role=='image':valid=valid and (path.is_relative_to(source) and self.name in {'image_info','list_dataset_images'}
            or self.name=='image_info' and self.selected_image_input(path))
        elif role=='flow':
            valid=valid and self.name in {'get_flow_draft','preview_pipeline_change','get_active_version_id'} and path.is_relative_to(root/'flowcharts')
            if self.name=='get_active_version_id':valid=valid and self.owner.flow_lock._is_owned()
        elif role in {'workspace_template','workspace_comparison'}:
            expected=self.workspace_root(role)
            valid=(valid and expected is not None and path.parent==expected and path.suffix=='.json' and not path.is_symlink()
                and any(row['role']==role and row['path']==expected
                    and any(original_path is member for member in row['members'])
                    and _workspace_iterator_original(row) and (row['live'] or row['complete'])
                    for row in self.workspace_listings))
        elif role in {'dataset','dataset_lock','dataset_publish'}:valid=valid and self.name in {'list_dataset_images','current_dataset_summary',*_DATASET_METADATA_NAMES} and (path.is_relative_to(root) or path.is_relative_to(source))
        elif role=='package':valid=valid and self.name=='packages' and path==root/'delivery'/'library.json'
        elif role=='global_project':valid=valid and self.name=='list_projects' and self.global_project_live is not None and path.is_relative_to(self.global_project_live['path'])
        else:valid=False
        if valid and self.selected_image_inputs and role in {'image','thumbnail_source'}:
            valid=self.selected_image_handle(path,handle,self.selected_image_inputs[0][3])
        if not valid:self.fail('file_binding')
        self.files.append((handle,path,role))

    def record_image(self,image,path):
        valid=self.resource_entry('image') and self.name in {'image_info','list_dataset_images','current_dataset_summary',*_DATASET_METADATA_NAMES}
        handle=getattr(image,'fp',None)
        if type(image) is not self.owner.png_kind or not isinstance(handle,io.IOBase) or handle.closed:valid=False
        project=self.scope.get('state',{}).get('scoped_project');path=Path(path).resolve()
        if type(project) is not dict or not (path.is_relative_to(Path(project['source_dataset_dir']))
                or self.name=='image_info' and self.selected_image_input(path)):valid=False
        if valid and self.selected_image_inputs:
            valid=self.selected_image_handle(path,handle,self.selected_image_inputs[0][3])
        if not valid:self.fail('image_binding')
        self.images.append((image,handle,path))

    def thumbnail_source(self):
        project=self.scope.get('state',{}).get('scoped_project')
        if type(project) is not dict or self.original_values is None:self.fail('thumbnail_source_scope');return Path('.')
        value=self.original_values.get('file_path')
        source=Path(value).resolve() if type(value) is str else Path('.')
        original=(source.is_relative_to(Path(project['source_dataset_dir']).resolve())
            and source.name==self.original_values.get('image_path'))
        name=self.original_values.get('image_path')
        selected=(not original and name in {source.name,'preview'}
            and self.selected_image_input(source,name=='preview'))
        if not (original or selected):self.fail('thumbnail_source_binding')
        return source

    def thumbnail_return(self,result):
        rows=self.thumbnail_operations
        memo=[row for row in rows if row[0]=='memo_get'];rendered=[row for row in rows if row[0]=='render_thumbnail']
        valid=(type(result) is _DatasetThumbnailFileResponse and result is self.thumbnail_response
            and _methods_original(result,_DatasetThumbnailFileResponse,self.owner.calls)
            and self.thumbnail_binding==(result,result.path,tuple(result.raw_headers),result.status_code,result.background)
            and Path(result.path).parent==self.owner.thumbnail_cache and len(memo)==1
            and result.status_code==200 and result.media_type=='image/jpeg' and not self.dataset_live
            and all(spool.closed for spool in self.thumbnail_spools))
        if type(result) is _DatasetThumbnailNotModifiedResponse:
            return bool(self.thumbnail_response is result and _methods_original(result,_DatasetThumbnailNotModifiedResponse,self.owner.calls)
                and self.thumbnail_binding==(result,None,tuple(result.raw_headers),result.status_code,result.background)
                and result.status_code==304 and result.body==b'' and result.background is None and result.media_type is None
                and len(memo)==1 and type(memo[0][1]) is str and re.fullmatch(r'[0-9a-f]{64}',memo[0][1])
                and len(rows)==1 and not rendered and not self.thumbnail_spools and not self.images and not self.files
                and not self.thumbnail_publications and not self.dataset_live)
        if memo and memo[0][1] is None:
            hashes=[row for row in rows if row[0]=='hash_source'];probes=[row for row in rows if row[0]=='probe_header'];renders=[row for row in rows if row[0]=='render_jpeg']
            valid=valid and len(rendered)==1 and rendered[0][1] is result and len(probes)==1
            if self.thumbnail_spools:
                valid=valid and len(self.thumbnail_spools)==1 and len(self.images)==2 and len(self.thumbnail_publications)==1 and len(hashes)==2 and len(renders)==1
            else:
                # Original _render_thumbnail's existing content-cache return:
                # one source hash/header, no decode copy, renderer or writer.
                valid=valid and len(self.images)==1 and len(hashes)==1 and not renders and not self.thumbnail_publications and len(self.files)==1 and self.files[0][2]=='thumbnail_source'
        else:valid=valid and not rendered and not self.thumbnail_spools and not self.images and not self.files and not self.thumbnail_publications
        return bool(valid)

    def thumbnail_response_entry(self,response,scope,receive,send):
        valid=(self.original() and not self.failed and _ACTIVE.get() is self and self.name=='get_thumbnail'
            and self.handler_done and not self.worker_live and scope is self.scope and scope.get('method')=='GET'
            and response is self.result is self.thumbnail_response and type(response) in {_DatasetThumbnailFileResponse,_DatasetThumbnailNotModifiedResponse}
            and _methods_original(response,type(response),self.owner.calls)
            and self.thumbnail_binding==(response,getattr(response,'path',None),tuple(response.raw_headers),response.status_code,response.background)
            and not any(name in {b'range',b'if-range'} for name,value in scope.get('headers',()))
            and 'http.response.pathsend' not in scope.get('extensions',{}))
        frame=sys._getframe(1);framework=False;owned=False
        for _ in range(64):
            if frame is None:break
            if frame.f_code in self.owner.response_codes:
                framework|=(frame.f_locals.get('scope') is scope and frame.f_locals.get('response') is response)
            if frame.f_code is type(self.owner.owned)._observed_call.__code__:
                owned|=(frame.f_locals.get('self') is self.owner.owned and frame.f_locals.get('activity') is self.activity and frame.f_locals.get('scope') is scope)
            frame=frame.f_back
        return bool(valid and framework and owned)


    def package_return(self,result):
        # Direct library reads keep the real verifier and every original file
        # handle, rather than treating a successful nonempty HTTP body as proof.
        from backend.engine import flow_package_runtime
        project=self.scope.get('state',{}).get('scoped_project');root=Path(project['project_dir']).resolve()
        rows=result.get('packages');libraries=[row for row in self.provenance_operations if row[0]=='package_library']
        verified=[row for row in self.provenance_operations if row[0]=='verify_package']
        scope={'project_id':project['id'],'source_dataset_path':str(Path(project['source_dataset_dir']).resolve()),'task':project.get('task')}
        if (set(result)!={'packages','selected_package_id','scope'} or type(rows) is not list or not rows
                or result['scope']!=scope or len(libraries)!=1 or libraries[0][3] is not result
                or libraries[0][1]!={**project,'_delivery_account_id':self.auth.account['id']}
                or len(verified)!=len(rows) or len(self.package_rows)!=1 or self.dataset_live):return False
        reads={path.resolve() for _,path,_ in self.files}
        paths=set();identifiers=set()
        for row,operation in zip(rows,verified):
            path=operation[2];value=operation[3]
            if (type(row) is not dict or type(path) is not type(Path('.'))
                    or not path.is_relative_to(root/'exports'/'flows') or path in paths
                    or any(member.is_symlink() for member in (path,*path.parents))
                    or row.get('package_path')!=str(path) or row.get('integrity')!='verified'
                    or row.get('scope_matches') is not True or type(value) is not tuple or len(value)!=2
                    or type(value[0]) is not flow_package_runtime.FlowchartPipeline or type(value[1]) is not dict
                    or row.get('pipeline_id')!=value[0].id
                    or row.get('package_id')!=hashlib.sha256(path.relative_to(root).as_posix().encode()).hexdigest()[:32]
                    or not {path/'manifest.json',path/'pipeline.json'}<=reads
                    or any(type(checkpoint) is not type(Path('.')) or checkpoint!=path/'models'/job/'best_model.pt'
                        or checkpoint not in reads for job,checkpoint in value[1].items())):return False
            paths.add(path);identifiers.add(row['package_id'])
        return (root/'delivery'/'library.json' in reads
            and result['selected_package_id'] in {None,*identifiers})


    def provenance_return(self,result):
        project=self.scope.get('state',{}).get('scoped_project');root=Path(project['project_dir']).resolve()
        operations=self.provenance_operations
        models=result.get('models');evaluations=result.get('model_evaluations');packages=result.get('packages')
        valid=(type(models) is list and type(evaluations) is list and type(packages) is list
            and result.get('flow_evaluations')==[] and bool(self.files) and not self.dataset_live
            and all(_methods_original(value,kind,self.owner.calls) for value,kind in self.dataset_objects))
        if not valid:return False
        reads={path.resolve() for _,path,_ in self.files}
        hashes=[row for row in operations if row[0]=='checkpoint_hash']
        model_reads={path for path in reads if path.name=='model_meta.json' and path.is_relative_to(Path(project['models_dir']).resolve())}
        valid=(len(hashes)==len(models)==len([row for row in operations if row[0]=='data_state'])
            and sorted(path.parent.name for path in model_reads)==sorted(row['job_id'] for row in models)
            and {row[2] for row in hashes}=={path.parent/'best_model.pt' for path in model_reads}
            and len([row for row in operations if row[0]=='evaluation_get'])==len(evaluations))
        if (root/'exports'/'flows').is_dir():
            libraries=[row for row in operations if row[0]=='package_library']
            valid=(valid and len(libraries)==1 and len(libraries[0][3]['packages'])==len(packages)
                and len([row for row in operations if row[0]=='verify_package'])==len(packages)
                and len(self.package_rows)==1)
        else:valid=valid and not packages and not self.package_rows
        database=root/'model_deployments.sqlite3'
        expected=1 if database.is_file() and not database.is_symlink() else 0
        valid=valid and len(self.provenance_connections)==expected
        for connection in self.provenance_connections:
            try:connection.in_transaction
            except sqlite3.ProgrammingError:pass
            else:valid=False
        return bool(valid)


    def dataset_return(self,result):
        operations=self.dataset_operations
        valid=not self.dataset_live and type(result) is dict
        if self.name=='current_dataset_summary':
            rows=[r for r in operations if r[0]=='import_dataset']
            decodes=[r for r in operations if r[0]=='decode_image_file']
            valid=valid and len(rows)==1 and rows[0][1] is result and result.get('status')=='success'
            valid=valid and len(decodes)==result.get('validation',{}).get('checked_images')
        elif self.name=='list_revisions':
            active=[r for r in operations if r[0]=='index_active'];listed=[r for r in operations if r[0]=='index_revisions']
            valid=valid and len(active)==len(listed)==1 and result=={'active_revision':active[0][1],'revisions':listed[0][1]} and len(self.dataset_connections)>=2
        elif self.name=='revision_source_status':
            rows=[r for r in operations if r[0]=='index_source_status']
            valid=valid and len(rows)==1 and rows[0][1] is result and len(self.dataset_connections)>=1
        elif self.name=='preferences':
            rows=[r for r in operations if r[0]=='read_preferences']
            valid=valid and len(rows)==1 and rows[0][1] is result
        elif self.name=='dataset_statistics':
            rows=[r for r in operations if r[0]=='dataset_summary']
            valid=valid and len(rows)==1 and rows[0][1] is result and len(self.metadata)==1
        elif self.name=='saved_diagnostics':
            # Initial mounted UI has no report. Existing report branch remains
            # unadmitted until its extra report/split effects are proven.
            rows=[r for r in operations if r[0]=='diagnostics_missing']
            valid=valid and len(rows)==1 and result=={'report':None} and not self.metadata and not self.files
        elif self.name=='readiness':
            rows=[r for r in operations if r[0]=='training_readiness']
            valid=valid and len(rows)==1 and rows[0][1] is result and len(self.metadata)==1
        elif self.name=='workflow_impact':
            rows=[r for r in operations if r[0]=='workflow_analyze']
            # First focused cohort has only saved flow records. Other original
            # project branches continue business but cannot mint managed credit.
            valid=valid and len(rows)==1 and rows[0][1] is result and self.provenance_return(result)
        else:valid=False
        return bool(valid and all(closed for _,_,closed in self.dataset_connections))


    def draft_superseded_return(self,error,frame,project):
        """A normal 404 needs the original valid, different saved graph read."""
        from backend.api import routes_flowchart as flows
        from fastapi import HTTPException
        if (self.name!='get_flow_draft' or type(error) is not HTTPException or error.status_code!=404
                or error.detail!='A newer saved flow supersedes this draft' or error.headers is not None
                or frame is None or type(project) is not dict or frame.f_locals.get('project') is not project
                or len(self.draft_active_reads)!=1 or self.images or self.profiles or self.metadata or self.processes):
            return False
        held=self.draft_active_reads[0]
        if type(held) is not tuple or len(held)!=7:return False
        context=frame.f_locals.get('context');record=frame.f_locals.get('record')
        pipeline=frame.f_locals.get('pipeline');checksum=frame.f_locals.get('checksum')
        active=frame.f_locals.get('active');file=frame.f_locals.get('file')
        if (type(context) is not dict or context!=flows._draft_context(project) or type(record) is not dict
                or type(pipeline) is not flows.FlowchartPipeline or type(checksum) is not str
                or record.get('context')!=context or record.get('pipeline')!=pipeline.model_dump()
                or record.get('draft_sha256')!=checksum or flows.pipeline_sha256(pipeline)!=checksum
                or type(active) is not dict or type(file) is not type(Path('.'))
                or type(record.get('saved_at_ns')) is not int or record['saved_at_ns']<=0
                or type(active.get('activated_at_ns')) is not int
                or active['activated_at_ns']<=record['saved_at_ns']):
            return False
        actual_project,actual_context,actual_checksum,actual_active,version,saved_pipeline,returned=held
        identifier=active.get('version_id')
        if (actual_project is not project or actual_context is not context or actual_checksum is not checksum
                or actual_active!=active or returned is not None or type(identifier) is not str
                or re.fullmatch(r'[0-9a-f]{32}',identifier) is None
                or active.get('project_id')!=context['project_id']
                or active.get('source_dataset_path')!=context['source_dataset_path']
                or active.get('labelset_id','default')!=context['labelset_id']
                or type(version) is not dict or version.get('version_id')!=identifier
                or version.get('source_dataset_path')!=context['source_dataset_path']
                or type(saved_pipeline) is not flows.FlowchartPipeline
                or version.get('pipeline')!=saved_pipeline.model_dump()
                or flows.pipeline_sha256(saved_pipeline)==checksum):
            return False
        root=Path(project['project_dir']);pointer=flows._active_flow_file(root)
        version_file=flows._version_dir(root)/f'{identifier}.json'
        paths=[flows._draft_file(project,context),pointer,pointer,version_file]
        return (file==paths[0] and frame.f_locals.get('active_file')==pointer and len(self.files)==4
            and all(path==expected and role=='flow' and handle.closed
                and not any(part.is_symlink() for part in (path,*path.parents))
                for (handle,path,role),expected in zip(self.files,paths)))

    def returned(self,result,error):
        caller=sys._getframe(1);endpoint,function=self.owner.handlers[self.name]
        codes=tuple(code for code in endpoint.__code__.co_consts if type(code) is type(endpoint.__code__) and code.co_name=='invoke')
        live=tuple(self.inflight.values())
        valid=(self.worker_original() and self.entries==1 and len(codes)==1 and caller.f_code is codes[0]
            and caller.f_locals.get('attempt') is self and caller.f_locals.get('function') is function
            and caller.f_locals.get('original_values') is self.original_values
            and len(live)==1 and live[0][0]=='storage_scope' and live[0][1] is self.owner.storage
            and not self.owner.flow_lock._is_owned() and not self.owner.profile_lock._is_owned()
            and all(handle.closed for handle,_,_ in self.files)
            and all(handle is not None and handle.closed and getattr(image,'fp',None) is None for image,handle,_ in self.images)
            and not self.profile_live and not self.metadata_live and not self.process_live and self.job_live is None
            and all(context.gen.gi_frame is None for context in self.contexts))
        status=200
        if error is not None:
            from fastapi import HTTPException
            tb=error.__traceback__;frame=None
            while tb is not None:
                if tb.tb_frame.f_code is function.__code__:frame=tb.tb_frame
                tb=tb.tb_next
            project=self.scope.get('state',{}).get('scoped_project')
            original_valid=valid
            valid=(valid and self.name=='get_flow_draft' and type(error) is HTTPException and error.status_code==404
                and error.detail=='No draft for this project, source and labelset' and error.headers is None
                and frame is not None and frame.f_locals.get('project') is project
                and type(frame.f_locals.get('file')) is type(Path('.')) and not frame.f_locals['file'].is_file()
                and not self.files and not self.images and not self.profiles and not self.metadata and not self.processes)
            if not valid and original_valid:valid=self.draft_superseded_return(error,frame,project)
            status=404
            if not valid and original_valid and self.name=='library_images':
                try:valid=self.library_no_revision_return(error)
                except Exception:valid=False  # Optional proof cannot replace the original exception.
                status=409
        elif self.name in {'list_profiles','get_selection'}:
            valid=valid and len(self.profiles)==1 and type(result) is dict
            if valid:
                value=self.profiles[0][2]
                valid=result==({'profiles':[row.model_dump() for row in value]} if self.name=='list_profiles' else {'compute_profile_id':value})
        elif self.name=='targets':
            valid=valid and len(self.profiles)==1 and type(result) is dict
            if valid:
                keys=('id','name','gpu_selector','memory_budget_mb','allow_sharing','distributed_processes')
                valid=result=={'targets':[{key:getattr(profile,key) for key in keys} for profile in self.profiles[0][2]]}
        elif self.name=='reservations':
            project=self.scope.get('state',{}).get('scoped_project')
            valid=(valid and type(result) is dict and set(result)=={'reservations'} and type(result['reservations']) is list
                and type(project) is dict and len(self.lease_connections)==2
                and [kind for _,_,kind,_ in self.lease_connections]==['__init__','list']
                and self.lease_connections[0][0] is self.lease_connections[1][0]
                and all(path==self.owner.lease_path for _,_,_,path in self.lease_connections)
                and all(row.get('project_id')==project['id'] and 'owner' not in row for row in result['reservations']))
        elif self.name=='jobs':
            valid=(valid and type(result) is dict and set(result)=={'jobs'} and type(result['jobs']) is list
                and len(self.job_lists)==1+len(self.job_rows) and len(self.job_snapshots)==len(self.job_lists)
                and len(self.lease_connections)==len(self.job_rows)
                and all(store is self.owner.job_lease and kind=='list' and path==self.owner.job_lease_path
                    for store,_,kind,path in self.lease_connections)
                and len(result['jobs'])==len(self.job_rows)
                and all(actual is retained for actual,(_,retained) in zip(result['jobs'],self.job_rows)))
        elif self.name=='inputs':
            project=self.scope.get('state',{}).get('scoped_project');task=self.original_values.get('task')
            valid=(valid and task in {'classification','segmentation'} and type(project) is dict
                and result=={'inputs':[{'dataset_path':project.get('source_dataset_dir'),'name':'프로젝트 원본'}] if project.get('source_dataset_dir') else []}
                and not self.files and not self.images and not self.processes)
        elif self.name in {'templates','comparisons'}:
            role='workspace_template' if self.name=='templates' else 'workspace_comparison'
            key='templates' if self.name=='templates' else 'comparisons'
            valid=(valid and type(result) is dict and type(result.get(key)) is list
                and len(self.workspace_listings)==1 and self.workspace_listings[0]['role']==role
                and self.workspace_listings[0]['complete'] and not self.workspace_listings[0]['live']
                and _workspace_iterator_original(self.workspace_listings[0])
                and (self.workspace_listings[0]['kind'] is map or self.workspace_listings[0]['iterator'].gi_frame is None))
        elif self.name=='get_active_version_id':
            project=self.scope.get('state',{}).get('scoped_project')
            valid=valid and type(result) is dict and set(result)=={'version_id'} and type(project) is dict
            if valid:
                # Original paths come from the pinned original path functions.
                from backend.api import routes_flowchart as flows
                pointer=flows._active_flow_file(Path(project['project_dir']))
                valid=(len(self.files)==2 and type(result['version_id']) is str
                    or result['version_id'] is None and not pointer.exists() and not self.files)
        elif self.name=='get_flow_draft':valid=valid and type(result) is dict and bool(self.files) and 'pipeline' in result
        elif self.name=='preview_pipeline_change':valid=valid and type(result) is dict and set(result)=={'parent_revision','stale','semantic_delta','layout_only'} and len(self.files)>=2
        elif self.name=='image_info':valid=valid and type(result) is dict and set(result)=={'width','height','sha256'} and len(self.images)==1 and any(role=='image' for _,_,role in self.files)
        elif self.name=='list_dataset_images':valid=valid and type(result) is dict and type(result.get('items')) is list and len(self.metadata)==1 and all(row['file_path'] in {str(path) for _,_,path in self.images} for row in result['items'])
        elif self.name=='runtime_capabilities':valid=valid and type(result) is dict and type(result.get('openvino')) is dict and len(self.processes)==1 and self.processes[0][-1] is True
        elif self.name=='edge_targets':valid=valid and type(result) is dict and result.get('device')=='cpu' and not self.files and not self.images and not self.processes
        elif self.name=='list_flow_preflights':
            try:valid=valid and self.preflight_returned(result)
            except Exception:valid=False  # New optional witness cannot replace the original result.
        elif self.name=='packages':
            valid=(valid and type(result) is dict and len(self.package_rows)==1
                and (result.get('packages')==[] and result.get('selected_package_id') is None
                     or self.package_return(result)))
        elif self.name in {'onboarding_state','list_projects','job_events'}:valid=valid and self.global_returned(result)
        elif self.name=='get_thumbnail':
            valid=valid and self.thumbnail_return(result)
            if type(result) is _DatasetThumbnailNotModifiedResponse:status=304
        elif self.name in _DATASET_NAMES:valid=valid and self.dataset_return(result)
        else:valid=False
        if not valid:self.fail('handler_return')
        else:self.handler_done,self.result,self.status=True,result,status

    def complete(self,row):
        if self.auth.denial_binding is not None:
            try:return self.auth.denial_complete(row)
            except Exception:return False  # Optional witness inspection cannot replace the original reply.
        state=self.scope.get('state',{});project=state.get('scoped_project');decision=state.get('permission_decision');context=state.get('project_context')
        expected=['bind_workspace','authenticate','project_role','project_for','project_role','authorize']
        if self.name=='preview_pipeline_change':expected.append('record_decision')
        if self.name=='list_projects':expected=expected[:5]
        resolutions=2 if self.name in {'get_flow_draft','preview_pipeline_change','image_info','packages','reservations','jobs','inputs','comparisons','get_active_version_id','list_flow_preflights',*_DATASET_NAMES} else 1
        return bool(self.original() and not self.failed and self.handler_done and not self.worker_live and not self.inflight and not self.dataset_live
            and self.selected_image_inputs_valid()
            and (self.name!='list_flow_preflights'or self.preflight_response_original())
            and (self.name!='get_thumbnail' or self.thumbnail_response_done and not self.thumbnail_response_live)
            and self.storage==1 and self.entries==1 and len(self.events)==(5 if self.name in {'job_events','list_revisions','revision_source_status','library_images'} else 4) and len(self.resolutions)==resolutions
            and all(kind=='context_db' and registry is self.owner.registry for kind,registry in self.events[:-1])
            and self.events[-1]==('storage_scope',self.owner.storage) and all(result is project for result in self.resolutions)
            and self.auth.original() and self.auth.middleware_calls==self.auth.middleware_returns==1
            and self.auth.databases==expected and not self.auth.db_live and self.auth.method_name is None
            and (self.name not in {'onboarding_state','list_projects','job_events'} or self.global_returned(self.result))
            and state.get('account_user') is self.auth.account and type(self.auth.account) is dict
            and type(project) is dict and project.get('id')==self.auth.project_id
            and ((self.name=='list_projects' and decision is None)
                or decision is not None and decision.allowed and decision.action==self.auth.action
                    and decision.actor_id==self.auth.account['id'] and decision.project_id==project['id']) and context is not None and context.actor_id==self.auth.account['id']
            and context.project_id==project['id'] and context.workspace_id==self.owner.accounts.workspace
            and row is not None and row['carrier'] is self.activity and row['released'] and row['completed'] and row['normal_return']
            and row['response_status']==self.status and not row['transport_failed'] and not row['framing_error']
            and row['send_attempt'] is None and row['start_forwarded'] and row['terminal_forwarded'] and not row['trailers_expected']
            and (self.owner.deadline is None or time.monotonic()<self.owner.deadline))

    def global_project_selected(self,included,route,dependant):
        from backend.engine.original_included_current_project import OriginalIncludedCurrentProject
        if (type(included) is not OriginalIncludedCurrentProject or included is not self.owner.accounts.named.included_current
                or not included.original() or included.branch.original_router is not self.owner.modules['routes_project'].router):
            return False
        context=included.framework._get_scope_effective_route_context(self.scope)
        return (included.framework._get_scope_included_router(self.scope) is included.branch
            and any(branch['node'] is included.branch and branch['cache'] is not None
                and any(context is minted for minted in branch['minted']) for branch in included.branches)
            and context.original_route is route and context.endpoint is route.endpoint
            and context.dependant is dependant and dependant.call is route.endpoint
            and self.scope.get('route') is route and route.path=='/api/project/list' and route.methods=={'GET'})

    def global_job_method_entry(self,store,function,args,kwargs,caller):
        from backend.api import routes_job_events
        frame=self.handler_frame();name=function.__name__;names=[n for n,_ in self.global_jobs]
        valid=(self.resource_entry('ledger_method') and not self.global_factory_live
            and self.global_job_live is None and store is self.global_store
            and _store_methods_original(store,self.owner.global_job_kind,self.owner.calls)
            and not kwargs and frame is not None and frame.f_locals.get('store') is store)
        role=None
        if name=='ledger_id':
            role='identity';valid=valid and not args and not names and caller is frame
        elif name=='newest_event':
            role='reset_head'
            valid=(valid and not args and caller is frame and self.global_cursor_live is False
                and (self.original_values.get('after') is None and names==['ledger_id']
                    or self.global_cursor is not None and self.global_cursor[-1] is None
                        and all(n=='event_at' for n in names[1:]) and names[:1]==['ledger_id']))
        elif name=='events_after':
            role='page';context=self.scope.get('state',{}).get('project_context')
            valid=(valid and caller is frame and self.global_cursor_live is False and self.global_cursor is not None
                and type(self.global_cursor[-1]) is int and len(args)==4 and context is not None
                and args[0] is context.workspace_id and args[1] is frame.f_locals.get('project_key')
                and args[2] is frame.f_locals.get('position') and args[2]==self.global_cursor[-1]
                and args[3] is frame.f_locals.get('limit') and type(args[3]) is int and args[3]==200
                and names[:1]==['ledger_id'] and all(n=='event_at' for n in names[1:]))
        elif name=='event_at':
            if self.global_cursor_live:
                role='cursor_anchor'
                valid=(valid and caller.f_code is routes_job_events._position.__wrapped__.__code__
                    and caller.f_locals.get('store') is store and len(args)==1
                    and args[0] is caller.f_locals.get('value') and type(args[0]) is int and args[0]>0
                    and names==['ledger_id'])
            else:
                role='page_anchor'
                valid=(valid and caller is frame and len(args)==1 and args[0] is frame.f_locals.get('head')
                    and type(args[0]) is int and args[0]>0 and names[-1:] == ['events_after'])
        else:valid=False
        if not valid:self.fail('ledger_method_entry')
        return valid,role,frame

    def global_job_returned(self,result):
        from backend.api import routes_job_events
        context=self.scope.get('state',{}).get('project_context')
        frame=getattr(self,'global_job_frame',None)
        limit=self.original_values.get('limit');after=self.original_values.get('after')
        if (self.global_factory_live or self.global_job_live is not None or self.global_cursor_live
                or self.global_store is None or context is None or type(result) is not dict
                or set(result)!={'cursor','events','reset','reason','more'} or type(limit) is not int or limit!=200
                or after is not None and (type(after) is not str or len(after)>200)
                or not self.global_jobs or self.global_jobs[0][0]!='ledger_id'
                or len(self.global_job_calls)!=len(self.global_jobs)
                or len(self.global_transactions)!=1 or not all(manager.gen.gi_frame is None for manager in self.global_transactions)
                or not self.global_databases or any(not closed or store is not self.global_store for store,_,closed in self.global_databases)
                or not _store_methods_original(self.global_store,self.owner.global_job_kind,self.owner.calls)
                or self.global_store.path is not self.global_store_path or frame is None
                or frame.f_code is not self.owner.handlers['job_events'][1].__code__
                or frame.f_locals.get('request') is not self.request or frame.f_locals.get('context') is not context
                or frame.f_locals.get('store') is not self.global_store or frame.f_locals.get('ledger_id')!=self.global_jobs[0][1]):
            return False
        roles=[role for _,role,_ in self.global_databases]
        names=[name for name,_ in self.global_jobs]
        if roles != names and roles != ['__init__',*names]:return False
        ledger_id=self.global_jobs[0][1];key=frame.f_locals.get('project_key')
        stream=routes_job_events._digest(ledger_id,context.workspace_id,key)[:24]
        if frame.f_locals.get('stream')!=stream:return False
        position=frame.f_locals.get('position')
        if after is None:
            if self.global_cursor is not None or position is not None:return False
        elif (self.global_cursor is None or self.global_cursor[:4]!=(after,stream,ledger_id,self.global_store)
                or self.global_cursor[4] is not position):return False
        if position is None:
            if names not in (['ledger_id','newest_event'],['ledger_id','event_at','newest_event']):return False
            newest=self.global_jobs[-1][1]
            cursor=routes_job_events._cursor(stream,ledger_id,newest[0],newest[1:]) if newest else routes_job_events._cursor(stream,ledger_id,0,None)
            expected={'cursor':cursor,'events':[],'reset':True,'reason':'cursor_expired' if after else 'initial','more':False}
        else:
            pages=[(index,row) for index,row in enumerate(self.global_job_calls) if row['role']=='page']
            if len(pages)!=1:return False
            index,call=pages[0];rows,head,more=call['result']
            if (frame.f_locals.get('rows') is not rows or frame.f_locals.get('head') is not head
                    or frame.f_locals.get('more') is not more or index not in (1,2)):
                return False
            if head:
                if index+2!=len(names) or self.global_job_calls[-1]['role']!='page_anchor':return False
                anchor=self.global_jobs[-1][1]
            else:
                if index+1!=len(names):return False
                anchor=None
            if frame.f_locals.get('anchor') is not anchor:return False
            expected={'cursor':routes_job_events._cursor(stream,ledger_id,head,anchor),
                'events':[routes_job_events._event(row) for row in rows],'reset':False,'reason':None,'more':more}
        return result==expected

    def global_returned(self,result):
        if self.name=='onboarding_state':
            identifier,version,task,classes,splits=self.owner.global_demo
            expected={'version':1,'dismissed':True,'team':True,'example':{
                'id':identifier,'version':version,'task':task,'classes':list(classes),
                'images':sum(value for _,value in splits)*len(classes)},'example_projects':[]}
            return (type(result) is dict and result==expected and self.global_team==[True]
                and not self.files and not self.global_jobs and not self.global_account_dbs)
        if self.name=='list_projects':
            return (type(result) is dict and set(result)=={'projects'} and type(result['projects']) is list
                and self.global_project_rows is not None and self.global_member_rows is not None
                and not self.global_projects_live and self.global_project_live is None
                and self.global_account_dbs==['projects_for',*(['project_role']*len(self.global_project_roles))]
                and all(row['id'] in {item['id'] for item in self.global_project_rows} for row in self.global_member_rows)
                and result['projects']==[value for _,value in self.global_loaded]
                and [path for path,_ in self.global_loaded]==[Path(row['path']) for row in self.global_member_rows]
                and len(self.global_project_guards)==len(self.global_loaded)
                and len(self.global_project_normalized)==len(self.global_loaded)
                and all(context.gen.gi_frame is None and handle.closed and record.get('active') is False
                    for context,handle,record in self.global_project_guards)
                and all(handle.closed for handle,_,_ in self.files)
                and all({'manifest_text','manifest_bytes','labelsets'}.issubset({role for root,role in self.global_project_reads if root==path})
                    for path,_ in self.global_loaded)
                and all(normal.get('migration_required') is False and normal.get('status')=='current'
                    for _,normal in self.global_project_normalized))
        if self.name=='job_events':return self.global_job_returned(result)
        return False


def bind_original_owned_UI_ancillary_request(app,admission):
    if admission is not None and admission._account_requests is not None:
        admission._UI_ancillary_requests=OriginalOwnedUIAncillaryRequest(app,admission)


def original_owned_UI_ancillary_production(app,admission):
    attempt=_ACTIVE.get()
    return bool(type(attempt) is _AncillaryAttempt and attempt.owner.app is app
        and attempt.owner.admission is admission and attempt.original() and not attempt.failed
        and attempt.entries==0 and attempt.worker is None and not attempt.worker_live)


@contextmanager
def original_owned_UI_ancillary_request_scope(admission,activity):
    from backend.engine.original_current_project_request import _CURRENT
    owner=admission._UI_ancillary_requests
    attempt=owner.pending.get(activity.token) if owner is not None and activity is not None else None
    if type(attempt) is not _AncillaryAttempt:yield;return
    token=_ACTIVE.set(attempt);current=_CURRENT.set(attempt)
    try:yield
    finally:_CURRENT.reset(current);_ACTIVE.reset(token)



def observe_original_UI_ancillary_profile_store(kind):
    """Retain the original class and exactly its two read-method lifetimes."""
    global _PROFILE_KIND
    if _PROFILE_KIND is not None:raise ValueError('Original profile kind repeated')
    _PROFILE_KIND=kind
    def decorate(function):
        @wraps(function)
        def observed(store):
            attempt=_ACTIVE.get()
            if type(attempt) is not _AncillaryAttempt or attempt.name not in {'list_profiles','get_selection','targets'}:
                return function(store)
            from backend.remote import profiles
            expected='list' if attempt.name in {'list_profiles','targets'} else 'get_selected'
            valid=(attempt.resource_entry('profile') and type(store) is _PROFILE_KIND
                and function.__name__==expected and attempt.profile_live is None
                and store.path==profiles.resolve_store_path(profiles.default_profile_path())
                and not attempt.owner.profile_lock._is_owned()
                and all(type(getattr(store,name,None)) is MethodType and getattr(store,name).__self__ is store
                    and getattr(store,name).__func__ is value for owner,name,value in attempt.owner.calls if owner is _PROFILE_KIND))
            if not valid:attempt.fail('profile_method_entry')
            attempt.profile_live=store,function
            try:result=function(store)
            except BaseException:attempt.fail('profile_failed');raise
            else:
                good=(valid and attempt.worker_original() and not attempt.owner.profile_lock._is_owned()
                    and attempt.profile_live==(store,function)
                    and all(handle.closed for handle,path,role in attempt.files if role=='profile'))
                if not good:attempt.fail('profile_method_return')
                else:attempt.profiles.append((store,function.__name__,result))
                return result
            finally:attempt.profile_live=None
        _RESOURCES['profile_'+function.__name__]=observed
        return observed
    kind.list=decorate(kind.list);kind.get_selected=decorate(kind.get_selected)
    return kind


def observe_original_UI_ancillary_context(kind):
    if kind not in {'profile_admission','metadata_transaction','metadata_lock','compute_lease_connection'}:raise ValueError('Unknown ancillary original context')
    def decorate(function):
        @wraps(function)
        def observed(*args,**kwargs):
            original=function(*args,**kwargs)
            attempt=_ACTIVE.get()
            used=(type(attempt) is _AncillaryAttempt and
                (kind=='profile_admission' and attempt.profile_live is not None or
                 kind in {'metadata_transaction','metadata_lock'} and attempt.name in {'list_dataset_images','current_dataset_summary',*_DATASET_METADATA_NAMES} or
                 kind=='compute_lease_connection' and attempt.name in {'reservations','jobs'}))
            if not used:return original
            if kind=='compute_lease_connection':
                producer=sys._getframe(1);store=args[0] if args else None
                name=producer.f_code.co_name
                permitted=('__init__','list') if attempt.name=='reservations' else ('list',)
                producer_function=getattr(attempt.owner.lease_kind,name,None) if name in permitted else None
                valid=(attempt.resource_entry(kind) and len(args)==1 and not kwargs
                    and type(store) is attempt.owner.lease_kind and _methods_original(store,attempt.owner.lease_kind,attempt.owner.calls)
                    and producer_function is not None and producer.f_code is producer_function.__code__ and producer.f_locals.get('self') is store
                    and type(original) is _GeneratorContextManager and original.func is attempt.owner.lease_generator
                    and store.path==(attempt.owner.lease_path if attempt.name=='reservations' else attempt.owner.job_lease_path)
                    and (attempt.name!='jobs' or store is attempt.owner.job_lease))
                @contextmanager
                def database():
                    connection=None
                    if not valid:attempt.fail('compute_lease_entry')
                    attempt.contexts.append(original)
                    try:
                        with original as connection:
                            frame=original.gen.gi_frame
                            if (not valid or not attempt.worker_original() or type(connection) is not sqlite3.Connection
                                    or frame is None or frame.f_code is not attempt.owner.lease_generator.__code__
                                    or frame.f_locals.get('self') is not store or frame.f_locals.get('conn') is not connection):
                                attempt.fail('compute_lease_source')
                            yield connection
                    except BaseException:attempt.fail('compute_lease_failed');raise
                    else:
                        closed=False
                        if type(connection) is sqlite3.Connection:
                            try:connection.in_transaction
                            except sqlite3.ProgrammingError:closed=True
                        if not valid or not closed or original.gen.gi_frame is not None or not attempt.worker_original():
                            attempt.fail('compute_lease_return')
                        else:attempt.lease_connections.append((store,connection,name,store.path))
                return database()
            @contextmanager
            def retained():
                valid=attempt.resource_entry(kind) and type(original) is _GeneratorContextManager
                if kind=='profile_admission':
                    valid=(valid and len(args)==1 and not kwargs and Path(args[0])==attempt.profile_live[0].path)
                elif kind=='metadata_transaction':
                    project=attempt.scope.get('state',{}).get('scoped_project')
                    valid=(valid and attempt.metadata_live is None and 2<=len(args)<=3 and not kwargs
                        and type(project) is dict and Path(args[0]).resolve()==Path(project['project_dir'])
                        and Path(args[1]).resolve()==Path(project['source_dataset_dir']))
                    attempt.metadata_live={'ledger':None,'path':None}
                else:
                    valid=(valid and attempt.metadata_live is not None and len(args)==1 and not kwargs)
                if not valid:attempt.fail(kind+'_entry')
                attempt.contexts.append(original)
                try:
                    with original as value:
                        frame=original.gen.gi_frame
                        if frame is None:attempt.fail(kind+'_missing_frame')
                        elif kind=='metadata_lock':
                            handle=frame.f_locals.get('handle')
                            if not isinstance(handle,io.IOBase):attempt.fail('metadata_lock_handle')
                            else:attempt.record_file(handle,Path(args[0]),'dataset_lock')
                        elif kind=='metadata_transaction':
                            if type(value) is not dict:attempt.fail('metadata_ledger')
                            attempt.metadata_live={'ledger':value,'path':frame.f_locals.get('path')}
                        yield value
                except BaseException:attempt.fail(kind+'_failed');raise
                else:
                    if not valid or not attempt.worker_original() or original.gen.gi_frame is not None:
                        attempt.fail(kind+'_return')
                    elif kind=='metadata_transaction':
                        live=attempt.metadata_live
                        if live is None or type(live['path']) is not type(Path('.')):
                            attempt.fail('metadata_binding')
                        else:
                            path=live['path']
                            with path.open('r',encoding='utf-8') as handle:
                                actual=json.load(handle)
                            if actual!=live['ledger']:attempt.fail('metadata_publication')
                            else:attempt.metadata.append((path,live['ledger']))
                finally:
                    if kind=='metadata_transaction':attempt.metadata_live=None
            return retained()
        _RESOURCES[kind]=observed
        return observed
    return decorate


def observe_original_UI_ancillary_file(handle,path,role):
    """Called inside the original with body, before its own normal close."""
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt:return
    if role=='profile' and attempt.profile_live is None:return
    if role.startswith('dataset') and attempt.name not in {'list_dataset_images',*_DATASET_NAMES}:return
    if role=='dataset_publish':handle=getattr(handle,'file',handle)
    if role=='profile' and not attempt.owner.profile_lock._is_owned():attempt.fail('profile_lock_not_held')
    attempt.record_file(handle,path,role)


def original_UI_ancillary_read_text(path,encoding=None,errors=None,*,role):
    """Original Path.open/with/read semantics, retaining the real text handle."""
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt:return path.read_text(encoding=encoding,errors=errors)
    encoding=io.text_encoding(encoding)
    with path.open(mode='r',encoding=encoding,errors=errors) as handle:
        attempt.record_file(handle,path,role)
        return handle.read()


def observe_original_UI_draft_active_return(project,context,checksum,active,version,pipeline,result):
    """Retain only the original helper's normal, fully parsed return boundary."""
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='get_flow_draft':return
    from backend.api import routes_flowchart as flows
    frame=sys._getframe(1)
    values=(project,context,checksum,active,version,pipeline,result)
    names=('project','context','checksum','active','version','pipeline','result')
    valid=(attempt.resource_entry('draft_active_return') and attempt.owner.flow_lock._is_owned()
        and frame.f_code is flows._draft_active_version.__code__
        and all(frame.f_locals.get(name) is value for name,value in zip(names,values))
        and project is attempt.scope.get('state',{}).get('scoped_project')
        and type(context) is dict and type(active) is dict and type(version) is dict
        and type(pipeline) is flows.FlowchartPipeline and type(checksum) is str
        and result==(active.get('version_id') if flows.pipeline_sha256(pipeline)==checksum else None)
        and len(attempt.draft_active_reads)<2)
    if not valid:attempt.fail('draft_active_return');return
    attempt.draft_active_reads.append(values)


def original_UI_ancillary_read_bytes(path,*,role):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt:return path.read_bytes()
    with path.open(mode='rb') as handle:
        attempt.record_file(handle,path,role)
        return handle.read()


def observe_original_UI_ancillary_image(function):
    @wraps(function)
    def observed(path):
        image=function(path)
        attempt=_ACTIVE.get()
        if type(attempt) is _AncillaryAttempt and attempt.name in {'image_info','list_dataset_images','current_dataset_summary',*_DATASET_METADATA_NAMES}:
            attempt.record_image(image,path)
        return image
    return observed


def observe_original_UI_ancillary_process(function):
    """A completed result must join the exact original Popen/with handles."""
    @wraps(function)
    def observed(command,*,deadline_ms,env=None,cwd=None,cancel_event=None,pass_fds=()):
        attempt=_ACTIVE.get()
        if type(attempt) is not _AncillaryAttempt or attempt.name!='runtime_capabilities':
            return function(command,deadline_ms=deadline_ms,env=env,cwd=cwd,cancel_event=cancel_event,pass_fds=pass_fds)
        from backend.engine import openvino_runtime
        frame=sys._getframe(1)
        valid=(attempt.resource_entry('discovery') and frame.f_code is openvino_runtime.available_openvino_devices.__code__
            and deadline_ms==15000 and type(deadline_ms) is int and env is None and cwd is None
            and cancel_event is None and pass_fds==() and not attempt.process_live and not attempt.processes
            and type(command) is list and frame.f_locals.get('command') is command)
        if not valid:attempt.fail('discovery_entry')
        attempt.process_live=True
        try:result=function(command,deadline_ms=deadline_ms,env=env,cwd=cwd,cancel_event=cancel_event,pass_fds=pass_fds)
        except BaseException:attempt.fail('discovery_failed');raise
        else:
            good=valid and attempt.worker_original() and len(attempt.processes)==1 and type(result) is dict
            if good:
                process,stdout,stderr,_,_=attempt.processes[0]
                good=(type(process) is attempt.owner.process_kind and type(process.returncode) is int
                    and _methods_original(process,attempt.owner.process_kind,attempt.owner.calls)
                    and stdout.closed and stderr.closed and result.get('status')=='completed'
                    and result.get('returncode')==process.returncode and result.get('pid')==process.pid)
                attempt.processes[0]=(process,stdout,stderr,process.returncode,good)
            if not good:attempt.fail('discovery_return')
            return result
        finally:attempt.process_live=False
    _RESOURCES['process']=observed
    return observed


def observe_original_UI_ancillary_spawn(process,stdout,stderr,command,deadline_ms):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='runtime_capabilities':return
    from backend.engine import runtime_deadline
    frame=sys._getframe(1);function=runtime_deadline.execute_owned_process.__wrapped__
    valid=(attempt.resource_entry('spawn') and attempt.process_live and not attempt.processes
        and frame.f_code is function.__code__ and frame.f_locals.get('process') is process
        and frame.f_locals.get('stdout') is stdout and frame.f_locals.get('stderr') is stderr
        and frame.f_locals.get('command') is command and frame.f_locals.get('deadline_ms')==deadline_ms==15000
        and type(process) is attempt.owner.process_kind and process.args is command
        and isinstance(stdout,io.IOBase) and isinstance(stderr,io.IOBase) and not stdout.closed and not stderr.closed)
    if not valid:attempt.fail('spawn_binding')
    attempt.processes.append((process,stdout,stderr,None,False))


def observe_original_UI_ancillary_package_storage(function):
    @wraps(function)
    def observed(project):
        result=function(project)
        attempt=_ACTIVE.get()
        if type(attempt) is not _AncillaryAttempt or attempt.name not in {'packages','workflow_impact'}:return result
        selected=attempt.scope.get('state',{}).get('scoped_project')
        valid=(attempt.resource_entry('package_storage') and type(project) is dict and type(selected) is dict
            and project==({**selected,'_delivery_account_id':attempt.auth.account['id']} if attempt.name=='packages' else selected)
            and type(result) is type(Path('.')) and result==Path(selected['project_dir'])/'delivery'
            and result.is_dir() and not result.is_symlink())
        if not valid:attempt.fail('package_storage_return')
        else:attempt.package_rows.append(result)
        return result
    _RESOURCES['package_storage']=observed
    return observed


def observe_original_UI_ancillary_job_list(function):
    """One real original singleton snapshot under its existing mutex."""
    @wraps(function)
    def observed(manager):
        attempt=_ACTIVE.get()
        if type(attempt) is not _AncillaryAttempt or attempt.name!='jobs':return function(manager)
        valid=(attempt.resource_entry('job_list') and manager is attempt.owner.job_manager
            and _methods_original(manager,attempt.owner.job_kind,attempt.owner.calls)
            and manager._lock is attempt.owner.job_lock and manager._jobs is attempt.owner.job_records
            and attempt.job_live is None)
        if not valid:attempt.fail('job_list_entry')
        attempt.job_live=manager,function
        before=len(attempt.job_snapshots)
        try:result=function(manager)
        except BaseException:attempt.fail('job_list_failed');raise
        else:
            # Normal return from the pinned original builtin-lock with block
            # proves this call released its lock; another worker may now hold it.
            if (not valid or not attempt.worker_original() or attempt.job_live!=(manager,function)
                    or len(attempt.job_snapshots)!=before+1 or attempt.job_snapshots[-1] is not result):attempt.fail('job_list_return')
            else:attempt.job_lists.append(result)
            return result
        finally:attempt.job_live=None
    _RESOURCES['compute_job_list']=observed
    return observed


def original_UI_ancillary_job_snapshot(manager,result):
    """Called inside the original list expression's locked body."""
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='jobs':return result
    frame=sys._getframe(1)
    valid=(attempt.worker_original() and attempt.handler_frame() is not None
        and manager is attempt.owner.job_manager and attempt.job_live is not None
        and attempt.job_live[0] is manager and frame.f_code is attempt.job_live[1].__code__
        and frame.f_locals.get('self') is manager and manager._lock is attempt.owner.job_lock
        and manager._lock.locked() and manager._jobs is attempt.owner.job_records
        and type(result) is list and len(result)==len(manager._jobs)
        and all(a is b for a,b in zip(result,manager._jobs.values())))
    if not valid:attempt.fail('job_snapshot')
    else:attempt.job_snapshots.append(result)
    return result


def observe_original_UI_ancillary_job_row(function):
    @wraps(function)
    def observed(record):
        attempt=_ACTIVE.get()
        if type(attempt) is not _AncillaryAttempt or attempt.name!='jobs':return function(record)
        from backend.api import routes_compute,routes_training
        project=attempt.scope.get('state',{}).get('scoped_project');caller=sys._getframe(1)
        expected=attempt.owner.handlers['jobs'][1]
        frames=(expected.__code__,*(code for code in expected.__code__.co_consts if type(code) is type(expected.__code__)))
        valid=(attempt.resource_entry('job_row') and type(record) is routes_training.JobRecord
            and type(project) is dict and routes_compute._owned(record,project)
            and caller.f_code in frames and caller.f_locals.get('row') is record
            and attempt.job_lists and any(record is row for row in attempt.job_lists[0]))
        if not valid:attempt.fail('job_row_entry')
        try:result=function(record)
        except BaseException:attempt.fail('job_row_failed');raise
        else:
            if not valid or not attempt.worker_original() or type(result) is not dict:attempt.fail('job_row_return')
            else:attempt.job_rows.append((record,result))
            return result
    _RESOURCES['compute_job_row']=observed
    return observed



def original_UI_ancillary_workspace_glob(path,pattern,*,role):
    """Retain and exhaust only the two original scoped workspace listings."""
    attempt=_ACTIVE.get()
    original=path.glob(pattern)
    if type(attempt) is not _AncillaryAttempt or attempt.name not in {'templates','comparisons'}:return original
    from backend.api import routes_flow_workspace as workspace
    caller=sys._getframe(1)
    function=workspace.load_templates if role=='workspace_template' else attempt.owner.handlers['comparisons'][1]
    valid=(attempt.resource_entry('workspace_listing') and pattern=='*.json' and type(path) is type(Path('.'))
        and path==attempt.workspace_root(role) and caller.f_code is function.__code__
        and type(original) in (GeneratorType,map)
        and (type(original) is map or original.gi_frame is not None))
    row={'iterator':original,'kind':type(original),'path':path,'role':role,'members':[],'member_binding':None,'complete':False,'live':True,
         'methods':tuple((name,getattr(type(original),name),getattr(original,name)) for name in ('__iter__','__next__'))}
    attempt.workspace_listings.append(row)
    def retained():
        if not valid:attempt.fail('workspace_listing_entry')
        try:
            for item in original:
                if not valid or not attempt.worker_original() or not _workspace_iterator_original(row) or type(item) is not type(Path('.')) or item.parent!=path:
                    attempt.fail('workspace_listing_item')
                else:row['members'].append(item)
                yield item
        except BaseException:attempt.fail('workspace_listing_failed');raise
        else:
            if (not valid or not attempt.worker_original() or not _workspace_iterator_original(row)
                    or type(original) is GeneratorType and original.gi_frame is not None):
                attempt.fail('workspace_listing_return')
            else:row['member_binding']=tuple(row['members']);row['complete']=True
        finally:row['live']=False
    return retained()


def _workspace_iterator_original(row):
    """Exactly the original CPython generator or current Path.glob map layout."""
    iterator=row['iterator'];kind=row['kind']
    if type(iterator) is not kind or kind not in (GeneratorType,map):return False
    members=row['members'];binding=row['member_binding']
    if type(members) is not list:return False
    if row['complete']:
        if type(binding) is not tuple or len(members)!=len(binding) or not all(a is b for a,b in zip(members,binding)):return False
    elif binding is not None:return False
    for name,descriptor,held in row['methods']:
        if getattr(kind,name) is not descriptor:return False
        expected=descriptor.__get__(iterator,kind);live=getattr(iterator,name)
        if (type(live) is not type(expected) or type(held) is not type(expected)
                or live!=expected or held!=expected
                or getattr(live,'__self__',None) is not iterator):return False
    return True

# GLOBAL3: finite selected-team readers. Other handlers keep their original calls.
def _global_attempt(name):
    attempt=_ACTIVE.get()
    return attempt if type(attempt) is _AncillaryAttempt and attempt.name==name else None


def _global_frame(function):
    frame=sys._getframe(1)
    for _ in range(40):
        if frame is None:return None
        if frame.f_code is function.__code__:return frame
        frame=frame.f_back
    return None


def observe_original_UI_global_team(function):
    @wraps(function)
    def observed(request):
        result=function(request)
        attempt=_global_attempt('onboarding_state')
        if attempt is not None:
            valid=(attempt.resource_entry('team_onboarding') and request is attempt.request
                and request.state.account_user is attempt.auth.account and result is True
                and not attempt.global_team)
            if not valid:attempt.fail('team_onboarding_return')
            else:attempt.global_team.append(result)
        return result
    _RESOURCES['global_team']=observed
    return observed


def observe_original_UI_global_project_role(function):
    @wraps(function)
    def observed(store,user_id,project_id):
        attempt=_global_attempt('list_projects')
        if attempt is None or not attempt.worker_live:return function(store,user_id,project_id)
        from backend.engine import shared_accounts
        parent=sys._getframe(1);original=shared_accounts.AccountStore.projects_for.__wrapped__
        if parent.f_code in original.__code__.co_consts:parent=parent.f_back
        valid=(attempt.resource_entry('member_role') and attempt.global_projects_live
            and parent is not None and parent.f_code is original.__code__ and parent.f_locals.get('self') is store
            and store is attempt.owner.accounts.store and user_id==attempt.auth.account['id']
            and any(row.get('id')==project_id for row in parent.f_locals.get('rows',())))
        if not valid:attempt.fail('member_role_entry')
        try:result=function(store,user_id,project_id)
        except BaseException:attempt.fail('member_role_failed');raise
        if not valid or not attempt.worker_original() or result is not None and result not in {'viewer','labeler','trainer','reviewer','owner'}:
            attempt.fail('member_role_return')
        else:attempt.global_role_results.append((project_id,result))
        return result
    _RESOURCES['global_project_role']=observed
    _RESOURCES['global_project_role_origin']=(function,function.__code__)
    return observed


def observe_original_UI_global_projects_for(function):
    @wraps(function)
    def observed(store,user_id):
        attempt=_global_attempt('list_projects')
        if attempt is None or not attempt.worker_live:return function(store,user_id)
        valid=(attempt.resource_entry('member_projects') and not attempt.global_projects_live
            and attempt.global_member_rows is None and store is attempt.owner.accounts.store
            and user_id==attempt.auth.account['id'])
        if not valid:attempt.fail('member_projects_entry')
        attempt.global_projects_live=True
        try:result=function(store,user_id)
        except BaseException:attempt.fail('member_projects_failed');raise
        else:
            expected=[];position=0;roles=attempt.global_role_results
            if attempt.global_project_rows is None:valid=False
            else:
                for row in attempt.global_project_rows:
                    if position>=len(roles) or roles[position][0]!=row['id']:valid=False;break
                    _,role=roles[position];position+=1
                    if role is not None:
                        if position>=len(roles) or roles[position]!=(row['id'],role):valid=False;break
                        expected.append({**row,'role':roles[position][1]});position+=1
            valid=(valid and attempt.worker_original() and not attempt.auth.db_live
                and position==len(roles) and type(result) is list and result==expected
                and attempt.global_project_roles==[identifier for identifier,_ in roles])
            if not valid:attempt.fail('member_projects_return')
            else:attempt.global_member_rows=tuple(dict(row) for row in result)
            return result
        finally:attempt.global_projects_live=False
    _RESOURCES['global_projects_for']=observed
    return observed


def observe_original_UI_global_project(function):
    @wraps(function)
    def observed(path):
        preview=_global_attempt('preview_pipeline_change')
        if preview is not None and preview.auth.preload_original():
            return preview.auth.preload(function,Path(path))
        attempt=_global_attempt('list_projects')
        if attempt is None or not attempt.worker_live:return function(path)
        root=Path(path)
        rows=attempt.global_member_rows
        valid=(attempt.resource_entry('member_project_load') and rows is not None
            and attempt.global_project_live is None and len(attempt.global_loaded)<len(rows)
            and root==Path(rows[len(attempt.global_loaded)]['path']) and not root.is_symlink())
        if not valid:attempt.fail('member_project_entry')
        attempt.global_project_live={'path':root,'function':function,'guard':False}
        try:result=function(path)
        except BaseException:attempt.fail('member_project_failed');raise
        else:
            valid=(valid and attempt.worker_original() and type(result) is dict
                and result.get('id')==rows[len(attempt.global_loaded)]['id']
                and result.get('project_dir')==str(root) and attempt.global_project_live['guard'] is True)
            if not valid:attempt.fail('member_project_return')
            else:attempt.global_loaded.append((root,result))
            return result
        finally:attempt.global_project_live=None
    _RESOURCES['global_project_load']=observed
    return observed


def original_UI_global_maintenance(manager,path):
    preview=_global_attempt('preview_pipeline_change')
    if preview is not None and preview.auth.denial_preload_live:
        return preview.auth.preload_maintenance(manager,Path(path))
    attempt=_global_attempt('list_projects')
    if attempt is None or not attempt.worker_live:return manager
    @contextmanager
    def retained():
        from backend.engine import migration_guard,original_lock_acquisition
        live=attempt.global_project_live
        valid=(attempt.resource_entry('member_project_guard') and live is not None and Path(path)==live['path']
            and type(manager) is _GeneratorContextManager and manager.gen.gi_code is migration_guard.maintenance_guard.__wrapped__.__wrapped__.__code__)
        handle=record=None
        if not valid:attempt.fail('member_project_guard_entry')
        try:
            with manager as value:
                frame=manager.gen.gi_frame
                handle=frame.f_locals.get('handle') if frame is not None else None
                record=frame.f_locals.get('ownership') if frame is not None else None
                observation=record.get('original_lock_observation') if type(record) is dict else None
                good=(valid and type(record) is dict and record.get('active') is True and record.get('exclusive') is False
                    and record.get('key')==str(live['path'].resolve())
                    and record.get('owner')==migration_guard._execution_owner()
                    and isinstance(handle,io.IOBase) and not handle.closed
                    and type(observation) is original_lock_acquisition.OriginalLockAcquisition
                    and _methods_original(observation,original_lock_acquisition.OriginalLockAcquisition,attempt.owner.calls))
                if good:
                    try:observation.current(record=record)
                    except (OSError,ValueError):good=False
                if not good:attempt.fail('member_project_guard_acquisition')
                yield value
        except BaseException:attempt.fail('member_project_guard_failed');raise
        else:
            if (not valid or not attempt.worker_original() or manager.gen.gi_frame is not None
                    or handle is None or not handle.closed or record is None or record.get('active') is not False):
                attempt.fail('member_project_guard_exit')
            else:
                live['guard']=True
                attempt.global_project_guards.append((manager,handle,record))
    return retained()


def _global_project_read(path,role,mode,encoding=None,errors=None):
    preview=_global_attempt('preview_pipeline_change')
    if preview is not None and preview.auth.denial_preload_live:
        return preview.auth.preload_read(Path(path),role,mode,encoding,errors,sys._getframe(1).f_back)
    attempt=_global_attempt('list_projects')
    if attempt is None or not attempt.worker_live or attempt.global_project_live is None:
        return path.read_bytes() if mode=='rb' else path.read_text(encoding=encoding,errors=errors)
    from backend.api import routes_project
    from backend.engine import project_labelsets,project_migration
    live=attempt.global_project_live;root=live['path'];path=Path(path)
    caller=sys._getframe(1).f_back
    expected=(routes_project._load_project_unfenced if role=='manifest_text' else
        project_migration._read if role=='manifest_bytes' else
        project_labelsets.load_labelsets if role=='labelsets' else
        project_migration.normalize_legacy_manifest.__wrapped__ if role=='migration_receipt' else None)
    valid=(attempt.resource_entry('member_project_read') and expected is not None
        and caller is not None and caller.f_code is expected.__code__
        and (path==root/'project.json' if role in {'manifest_text','manifest_bytes'} else
             path==root/'labelsets.json' if role=='labelsets' else path.is_relative_to(root/'.migrations') and path.name=='receipt.json'))
    if not valid:attempt.fail('member_project_read_source')
    with path.open(mode=mode,encoding=encoding,errors=errors) as handle:
        attempt.record_file(handle,path,'global_project')
        result=handle.read()
    if not valid or not handle.closed or not attempt.worker_original():attempt.fail('member_project_read_exit')
    else:attempt.global_project_reads.append((root,role))
    return result


def original_UI_global_read_text(path,encoding=None,errors=None,*,role):
    return _global_project_read(path,role,'r',encoding,errors)


def original_UI_global_read_bytes(path,*,role):
    return _global_project_read(path,role,'rb')


def observe_original_UI_global_normalization(function):
    @wraps(function)
    def observed(project_dir,expected_manifest_sha256=None):
        result=function(project_dir,expected_manifest_sha256)
        preview=_global_attempt('preview_pipeline_change')
        if preview is not None and preview.auth.denial_preload_live:
            from backend.api import routes_project
            live=preview.auth.denial_preload
            live['valid'] &= (preview.auth.preload_original() and Path(project_dir)==live['path']
                and expected_manifest_sha256 is None and type(result) is dict
                and sys._getframe(1).f_code is routes_project._load_project_unfenced.__code__
                and result.get('migration_required') is False and result.get('status')=='current')
            live['normal']=result
        attempt=_global_attempt('list_projects')
        if attempt is not None and attempt.worker_live:
            live=attempt.global_project_live
            valid=(attempt.resource_entry('member_project_normalization') and live is not None
                and Path(project_dir)==live['path'] and expected_manifest_sha256 is None
                and type(result) is dict and result.get('migration_required') is False and result.get('status')=='current')
            if not valid:attempt.fail('member_project_normalization_return')
            else:attempt.global_project_normalized.append((Path(project_dir),result))
        return result
    _RESOURCES['global_project_normalization']=observed
    return observed


def observe_original_UI_global_ledger(function):
    @wraps(function)
    def observed():
        attempt=_global_attempt('job_events')
        if attempt is None:return function()
        from backend.api import routes_training
        from backend.engine import job_store
        caller=sys._getframe(1)
        valid=(attempt.resource_entry('initial_ledger_factory') and not attempt.global_factory_live
            and attempt.global_store is None and caller.f_code is routes_training.job_ledger.__code__
            and routes_training._shared_ledger is observed and job_store.ledger is observed)
        if not valid:attempt.fail('initial_ledger_factory_entry')
        attempt.global_factory_live=True
        try:result=function()
        except BaseException:attempt.fail('initial_ledger_factory_failed');raise
        else:
            frame=attempt.handler_frame()
            if (not valid or not attempt.worker_original() or type(result) is not attempt.owner.global_job_kind
                    or result is not job_store._shared or frame is None
                    or not _store_methods_original(result,attempt.owner.global_job_kind,attempt.owner.calls)
                    or attempt.global_store is not None and attempt.global_store is not result):
                attempt.fail('initial_ledger_factory_return')
            attempt.global_store,attempt.global_store_path=result,result.path
            return result
        finally:attempt.global_factory_live=False
    _RESOURCES['global_ledger_factory']=observed
    return observed


def observe_original_UI_global_job_store(kind):
    def method(function):
        @wraps(function)
        def observed(store,*args,**kwargs):
            attempt=_global_attempt('job_events')
            if attempt is None:return function(store,*args,**kwargs)
            caller=sys._getframe(1)
            valid,role,frame=attempt.global_job_method_entry(store,function,args,kwargs,caller)
            attempt.global_job_live=store,function.__name__,function
            attempt.global_job_frame=frame
            try:result=function(store,*args,**kwargs)
            except BaseException:attempt.fail('initial_ledger_method_failed');raise
            else:
                good=(valid and attempt.worker_original() and
                    _store_methods_original(store,attempt.owner.global_job_kind,attempt.owner.calls))
                if function.__name__=='ledger_id':good=good and type(result) is str and bool(re.fullmatch('[a-f0-9]{32}',result))
                elif function.__name__=='newest_event':good=good and (result is None or type(result) is tuple and len(result)==3 and type(result[0]) is int and type(result[1]) is str and type(result[2]) is int)
                elif function.__name__=='event_at':good=good and (result is None or type(result) is tuple and len(result)==2 and type(result[0]) is str and type(result[1]) is int)
                else:good=good and type(result) is tuple and len(result)==3 and type(result[0]) is list and len(result[0])<=200 and all(type(row) is dict for row in result[0]) and type(result[1]) is int and result[1]>=0 and type(result[2]) is bool
                if not good:attempt.fail('initial_ledger_method_return')
                else:
                    attempt.global_jobs.append((function.__name__,result))
                    attempt.global_job_calls.append({'role':role,'args':args,'result':result,'function':function})
                return result
            finally:attempt.global_job_live=None
        _RESOURCES['global_job_'+function.__name__]=observed
        return observed
    def connection(function):
        @wraps(function)
        def observed(store):
            original=function(store)
            attempt=_global_attempt('job_events')
            if attempt is None:return original
            @contextmanager
            def retained():
                from backend.engine import job_store
                role=None;frame=sys._getframe(1)
                for _ in range(32):
                    if frame is None:break
                    if frame.f_code is kind.__init__.__code__ and frame.f_locals.get('self') is store:role='__init__';break
                    if attempt.global_job_live is not None and frame.f_code is attempt.global_job_live[2].__code__ and frame.f_locals.get('self') is store:
                        role=attempt.global_job_live[1];break
                    frame=frame.f_back
                factory=_global_frame(job_store.ledger.__wrapped__)
                valid=(attempt.resource_entry('initial_ledger_connection') and type(store) is kind
                    and _store_methods_original(store,kind,attempt.owner.calls) and type(original) is _GeneratorContextManager
                    and original.gen.gi_code is (function.__wrapped__.__wrapped__.__code__
                        if function is _RESOURCES.get('dataset_connection_ledger') else function.__wrapped__.__code__)
                    and (role=='__init__' and attempt.global_factory_live and factory is not None
                        and factory.f_locals.get('path')==store.path
                        or role in {'ledger_id','newest_event','event_at','events_after'} and store is attempt.global_store
                        and store.path is attempt.global_store_path))
                if role=='__init__' and attempt.global_store is None:
                    attempt.global_store,attempt.global_store_path=store,store.path
                if not valid:attempt.fail('initial_ledger_connection_entry')
                db=None
                try:
                    with original as value:
                        db=value
                        if type(db) is not sqlite3.Connection:attempt.fail('initial_ledger_foreign_connection')
                        yield db
                except BaseException:attempt.fail('initial_ledger_connection_failed');raise
                else:
                    closed=False
                    if type(db) is sqlite3.Connection:
                        try:db.in_transaction
                        except sqlite3.ProgrammingError:closed=True
                    good=(valid and attempt.worker_original() and original.gen.gi_frame is None and closed
                        and _store_methods_original(store,kind,attempt.owner.calls))
                    if not good:attempt.fail('initial_ledger_connection_exit')
                    else:attempt.global_databases.append((store,role,closed))
            return retained()
        _RESOURCES['global_job_connect']=observed
        return observed
    def transaction(function):
        @wraps(function)
        def observed(store):
            original=function(store)
            attempt=_global_attempt('job_events')
            if attempt is None:return original
            @contextmanager
            def retained():
                valid=(attempt.resource_entry('initial_ledger_transaction') and attempt.global_job_live is not None
                    and attempt.global_job_live[:2]==(store,'ledger_id') and not attempt.global_transactions
                    and type(original) is _GeneratorContextManager and original.gen.gi_code is function.__wrapped__.__code__)
                if not valid:attempt.fail('initial_ledger_transaction_entry')
                try:
                    with original as db:yield db
                except BaseException:attempt.fail('initial_ledger_transaction_failed');raise
                else:
                    if not valid or not attempt.worker_original() or original.gen.gi_frame is not None:
                        attempt.fail('initial_ledger_transaction_exit')
                    else:attempt.global_transactions.append(original)
            return retained()
        _RESOURCES['global_job_tx']=observed
        return observed
    kind._connect=connection(kind._connect);kind._tx=transaction(kind._tx)
    kind.ledger_id=method(kind.ledger_id);kind.newest_event=method(kind.newest_event)
    kind.event_at=method(kind.event_at);kind.events_after=method(kind.events_after)
    return kind
# Finite mounted-dataset observations. No method/path-only GET authority.
_DATASET_OPERATION_ROUTES = {
    'import_dataset':frozenset({'current_dataset_summary'}),
    'decode_image_file':frozenset({'current_dataset_summary'}),
    'index_active':frozenset({'list_revisions','library_images'}),
    'index_revisions':frozenset({'list_revisions','library_images'}),
    'index_source_status':frozenset({'revision_source_status'}),
    'read_preferences':frozenset({'preferences'}),
    'dataset_summary':frozenset({'dataset_statistics'}),
    'training_readiness':frozenset({'readiness'}),
    'workflow_analyze':frozenset({'workflow_impact'}),
}


def observe_original_dataset_UI_operation(kind):
    if kind not in _DATASET_OPERATION_ROUTES:raise ValueError('Unknown original dataset UI operation')
    def decorate(function):
        @wraps(function)
        def observed(*args,**kwargs):
            attempt=_ACTIVE.get()
            if type(attempt) is not _AncillaryAttempt or attempt.name not in _DATASET_OPERATION_ROUTES[kind]:
                return function(*args,**kwargs)
            valid=(attempt.resource_entry('dataset_operation') and
                _RESOURCES.get('dataset_operation_'+kind) is observed)
            ticket=object();attempt.dataset_live.append(ticket)
            project=attempt.scope.get('state',{}).get('scoped_project')
            if type(project) is not dict:valid=False
            elif kind=='import_dataset':
                body=args[0] if len(args)==1 and not kwargs else None
                valid=valid and body is not None and body.folder_path==project['source_dataset_dir'] and body.task==project['task'] and body.validate_images is True
            elif kind=='decode_image_file':
                valid=valid and len(args)==1 and not kwargs and Path(args[0]).resolve().is_relative_to(Path(project['source_dataset_dir']).resolve())
            elif kind.startswith('index_'):
                from backend.engine.dataset_index import index_path
                index=args[0] if args else None
                valid=(valid and type(index) is attempt.owner.dataset_index_kind
                    and _store_methods_original(index,attempt.owner.dataset_index_kind,attempt.owner.calls)
                    and index.path==index_path(attempt.owner.registry.root)
                    and len(args)==(3 if kind=='index_source_status' else 2) and not kwargs)
                if valid and kind=='index_source_status':
                    valid=args[2]==attempt.original_values.get('revision_id')
                if attempt.name=='library_images':
                    frame=attempt.library_revision_frame()
                    valid=(valid and frame is not None and attempt.library_original()
                        and index is attempt.library_binding[1]
                        and args[1] is frame.f_locals.get('project_key')
                        and frame.f_locals.get('_context') is attempt.scope['state'].get('project_context'))
            elif kind=='read_preferences':
                valid=valid and len(args)==1 and not kwargs and Path(args[0]).resolve()==Path(project['project_dir']).resolve()
            elif kind in {'workflow_analyze','training_readiness'}:
                valid=valid and bool(args) and args[0] is project
                if kind=='training_readiness':valid=valid and len(args)==2 and Path(args[1]).resolve()==Path(project['source_dataset_dir']).resolve()
            elif kind=='dataset_summary':
                valid=valid and len(args)==2 and set(kwargs)=={'assignments','metadata'} and type(kwargs['assignments']) is dict and type(kwargs['metadata']) is dict and Path(args[0]).resolve()==Path(project['source_dataset_dir']).resolve() and args[1]==project['task']
            if not valid:attempt.fail('dataset_operation_binding')
            try:result=function(*args,**kwargs)
            except BaseException:attempt.fail('dataset_operation_failed');raise
            else:
                if not valid or not attempt.resource_entry('dataset_operation_return'):attempt.fail('dataset_operation_return')
                else:attempt.dataset_operations.append((kind,result))
                return result
            finally:
                if ticket not in attempt.dataset_live:attempt.fail('dataset_operation_ticket')
                else:attempt.dataset_live.remove(ticket)
        _RESOURCES['dataset_operation_'+kind]=observed
        return observed
    return decorate


def observe_original_dataset_UI_connection(kind):
    if kind not in {'index','ledger'}:raise ValueError('Unknown original dataset database')
    def decorate(function):
        @wraps(function)
        def observed(store):
            original=function(store);attempt=_ACTIVE.get()
            if type(attempt) is not _AncillaryAttempt or attempt.name not in {'list_revisions','revision_source_status','library_images'}:return original
            @contextmanager
            def retained():
                from backend.engine import dataset_index,job_store
                expected=attempt.owner.dataset_index_kind if kind=='index' else attempt.owner.dataset_job_kind
                path=dataset_index.index_path(attempt.owner.registry.root) if kind=='index' else job_store.resolve_store_path(job_store.default_ledger_path())
                valid=(attempt.resource_entry('dataset_database') and type(store) is expected
                    and _store_methods_original(store,expected,attempt.owner.calls)
                    and store.path==path and type(original) is _GeneratorContextManager)
                ticket=object();attempt.dataset_live.append(ticket);attempt.contexts.append(original)
                if not valid:attempt.fail('dataset_database_binding')
                connection=None
                try:
                    with original as connection:
                        if type(connection) is not sqlite3.Connection:attempt.fail('dataset_database_type')
                        yield connection
                except BaseException:attempt.fail('dataset_database_failed');raise
                else:
                    closed=False
                    try:connection.in_transaction
                    except sqlite3.ProgrammingError:closed=True
                    if not valid or not closed or original.gen.gi_frame is not None or not attempt.resource_entry('dataset_database_return'):
                        attempt.fail('dataset_database_not_closed')
                    else:
                        attempt.dataset_connections.append((kind,store,closed))
                        if attempt.name=='library_images':attempt.library_connections.append(connection)
                finally:
                    if ticket not in attempt.dataset_live:attempt.fail('dataset_database_ticket')
                    else:attempt.dataset_live.remove(ticket)
            return retained()
        _RESOURCES['dataset_connection_'+kind]=observed
        return observed
    return decorate


def original_dataset_UI_read_text(path,encoding=None,errors=None):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name not in {*_DATASET_NAMES,'packages'}:
        return path.read_text(encoding=encoding,errors=errors)
    with path.open('r',encoding=io.text_encoding(encoding),errors=errors) as handle:
        attempt.record_file(handle,path,'dataset_UI')
        return handle.read()


def original_dataset_UI_read_bytes(path):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name not in {*_DATASET_NAMES,'packages'}:return path.read_bytes()
    with path.open('rb') as handle:
        attempt.record_file(handle,path,'dataset_UI')
        return handle.read()


def original_dataset_UI_image_open(function,*args,**kwargs):
    image=function(*args,**kwargs);attempt=_ACTIVE.get()
    if type(attempt) is _AncillaryAttempt and attempt.name in {'current_dataset_summary'}:
        if not args:attempt.fail('dataset_image_arguments')
        else:attempt.record_image(image,args[0])
    return image


def original_dataset_UI_file(handle,path):
    attempt=_ACTIVE.get()
    if type(attempt) is _AncillaryAttempt and attempt.name in _DATASET_NAMES:
        attempt.record_file(handle,path,'dataset_UI')


def original_dataset_UI_diagnostics_missing(path):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='saved_diagnostics':return
    frame=attempt.handler_frame();project=attempt.scope.get('state',{}).get('scoped_project')
    from backend.engine import data_workbench
    valid=(attempt.resource_entry('dataset_diagnostics_missing') and frame is not None
        and frame.f_locals.get('path') is path and type(project) is dict
        and path==data_workbench._storage(project['project_dir'],Path(project['source_dataset_dir']),create=False)/f"diagnostics_{project.get('active_labelset_id','default')}.json"
        and not path.is_file())
    if not valid:attempt.fail('dataset_missing_report_binding')
    else:attempt.dataset_operations.append(('diagnostics_missing',path))

# Original thumbnail file response: join the original asynchronous reader after
# its own context closes. No image conversion or response-body replacement.
from starlette.responses import FileResponse as _ThumbnailFileResponseBase,Response as _ThumbnailNotModifiedResponseBase


class _DatasetThumbnailFileResponse(_ThumbnailFileResponseBase):
    async def _handle_simple(self,send,send_header_only,send_pathsend):
        attempt=self._dataset_attempt
        from starlette import responses
        frame=sys._getframe(1);scope=frame.f_locals.get('scope');headers=frame.f_locals.get('headers')
        prior=attempt.header_container;current=attempt.scope.get('headers')
        valid=(type(attempt) is _AncillaryAttempt and attempt.owner.original() and not attempt.failed
            and _ACTIVE.get() is attempt and not attempt.thumbnail_headers_published
            and frame.f_code is _ThumbnailFileResponseBase.__call__.__code__
            and frame.f_locals.get('self') is self and scope is attempt.scope
            and frame.f_locals.get('send') is send and frame.f_locals.get('receive') is not None
            and type(headers) is responses.Headers and headers._list is current
            and type(current) is list and current is not prior and prior is attempt.auth.header_container
            and type(prior) in (list,tuple) and _headers(scope)==attempt.headers
            and len(current)==len(prior) and all(a is b for a,b in zip(current,prior))
            and tuple((key,value) for key,value in prior)==attempt.headers
            and attempt.thumbnail_response is self and attempt.thumbnail_response_live and attempt.thumbnail_stat_bound
            and attempt.thumbnail_binding==(self,self.path,tuple(self.raw_headers),self.status_code,self.background))
        if not valid:attempt.fail('thumbnail_header_constructor')
        else:
            # The original captured Headers(scope) constructor alone publishes
            # this exact one-time list copy. Keep the original bytes and rejoin
            # every ordinary attempt/auth invariant before any send can count.
            attempt.thumbnail_headers_receipt=(prior,current,headers)
            attempt.header_container=current;attempt.auth.header_container=current
            attempt.thumbnail_headers_published=True
            if not attempt.original() or not attempt.auth.original():attempt.fail('thumbnail_header_publication')
        return await _ThumbnailFileResponseBase._handle_simple(self,send,send_header_only,send_pathsend)

    def set_stat_headers(self,stat_result):
        attempt=getattr(self,'_dataset_attempt',None)
        if type(attempt) is not _AncillaryAttempt or attempt.name!='get_thumbnail':
            return _ThumbnailFileResponseBase.set_stat_headers(self,stat_result)
        from starlette.responses import formatdate
        frame=sys._getframe(1);prior=tuple(self.raw_headers)
        valid=(attempt.original() and _ACTIVE.get() is attempt and attempt.thumbnail_response_live
            and not attempt.thumbnail_stat_bound and attempt.thumbnail_response is self
            and attempt.thumbnail_binding==(self,self.path,prior,self.status_code,self.background)
            and frame.f_code is _ThumbnailFileResponseBase.__call__.__code__
            and frame.f_locals.get('self') is self and frame.f_locals.get('stat_result') is stat_result
            and type(stat_result) is os.stat_result and self.stat_result is None)
        if not valid:attempt.fail('thumbnail_stat_entry')
        result=_ThumbnailFileResponseBase.set_stat_headers(self,stat_result)
        expected=(*prior,(b'content-length',str(stat_result.st_size).encode('latin-1')),
            (b'last-modified',formatdate(stat_result.st_mtime,usegmt=True).encode('latin-1')))
        if (not valid or result is not None or tuple(self.raw_headers)!=expected
                or not attempt.original()):attempt.fail('thumbnail_stat_publication')
        else:
            attempt.thumbnail_binding=(self,self.path,tuple(self.raw_headers),self.status_code,self.background)
            attempt.thumbnail_stat_bound=True
        return result

    async def __call__(self,scope,receive,send):
        attempt=self._dataset_attempt
        valid=(type(attempt) is _AncillaryAttempt and attempt.thumbnail_response is self
            and attempt.thumbnail_binding==(self,self.path,tuple(self.raw_headers),self.status_code,self.background)
            and attempt.thumbnail_response_entry(self,scope,receive,send)
            and not attempt.thumbnail_response_live and not attempt.thumbnail_response_done)
        if not valid:attempt.fail('thumbnail_response_entry')
        attempt.thumbnail_response_live=True;reader=None;raw=None;starts=terminals=total=0
        async def retained_send(message):
            nonlocal reader,raw,starts,terminals,total
            frame=sys._getframe(1)
            good=(valid and attempt.thumbnail_response_entry(self,scope,receive,send)
                and frame.f_code is attempt.owner.thumbnail_simple.__code__
                and frame.f_locals.get('self') is self and frame.f_locals.get('send') is retained_send
                and frame.f_locals.get('send_header_only') is False and frame.f_locals.get('send_pathsend') is False
                and type(message) is dict)
            if message.get('type')=='http.response.start':
                starts+=1;good=good and starts==1 and terminals==0 and message.get('status')==200 and message.get('headers') is self.raw_headers
            elif message.get('type')=='http.response.body':
                current=frame.f_locals.get('file');handle=getattr(current,'_fp',None)
                good=(good and starts==1 and terminals==0 and type(current) is attempt.owner.thumbnail_async_kind
                    and _methods_original(current,attempt.owner.thumbnail_async_kind,attempt.owner.calls)
                    and type(handle) is io.BufferedReader and not handle.closed and Path(handle.name)==Path(self.path)
                    and type(message.get('body')) is bytes and type(message.get('more_body')) is bool)
                if reader is None:reader,raw=current,handle
                good=good and reader is current and raw is handle
                total+=len(message.get('body',b''))
                if message.get('more_body') is False:terminals+=1
            else:good=False
            if not good:attempt.fail('thumbnail_original_stream')
            await send(message)
            if not attempt.thumbnail_response_entry(self,scope,receive,send):attempt.fail('thumbnail_stream_after_send')
        try:
            await _ThumbnailFileResponseBase.__call__(self,scope,receive,retained_send)
        except BaseException:attempt.fail('thumbnail_response_failed');raise
        else:
            good=(valid and attempt.thumbnail_response_entry(self,scope,receive,send)
                and reader is not None and raw is not None and raw.closed and reader._fp is raw
                and _methods_original(reader,attempt.owner.thumbnail_async_kind,attempt.owner.calls)
                and starts==terminals==1 and total==int(self.headers['content-length'])
                and self.background is None)
            if not good:attempt.fail('thumbnail_stream_unjoined')
            else:attempt.thumbnail_response_done=True
        finally:attempt.thumbnail_response_live=False


def original_dataset_thumbnail_response(kind,*args,**kwargs):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='get_thumbnail':return kind(*args,**kwargs)
    from backend.api import routes_dataset
    frame=sys._getframe(1);function=attempt.owner.handlers['get_thumbnail'][1]
    codes=tuple(code for code in function.__code__.co_consts if type(code) is type(function.__code__) and code.co_name=='respond')
    valid=(attempt.resource_entry('thumbnail_factory') and len(codes)==1 and frame.f_code is codes[0]
        and frame.f_locals.get('request') is attempt.request and not attempt.thumbnail_response
        and kwargs.get('headers') is frame.f_locals.get('headers')
        and kwargs.get('headers')=={'Cache-Control':'private, no-cache','ETag':f'"{frame.f_locals.get("key")}"'})
    if kind is _ThumbnailFileResponseBase:
        valid=(valid and len(args)==1 and set(kwargs)=={'media_type','headers'}
            and Path(args[0])==frame.f_locals.get('cached') and Path(args[0]).parent==routes_dataset.THUMBNAIL_CACHE_DIR
            and kwargs['media_type']=='image/jpeg')
        result=_DatasetThumbnailFileResponse(*args,**kwargs)
    elif kind is _ThumbnailNotModifiedResponseBase:
        memo=[row for row in attempt.thumbnail_operations if row[0]=='memo_get']
        valid=(valid and not args and set(kwargs)=={'status_code','headers'} and kwargs['status_code']==304
            and len(memo)==1 and type(memo[0][1]) is str and re.fullmatch(r'[0-9a-f]{64}',memo[0][1])
            and frame.f_locals.get('rendered') is None and frame.f_locals['cached'].is_file())
        result=_DatasetThumbnailNotModifiedResponse(*args,**kwargs)
    else:
        attempt.fail('thumbnail_response_kind');return kind(*args,**kwargs)
    if not valid:attempt.fail('thumbnail_response_factory')
    result._dataset_attempt=attempt;attempt.thumbnail_response=result
    attempt.thumbnail_binding=(result,getattr(result,'path',None),tuple(result.raw_headers),result.status_code,result.background)
    return result


def observe_original_dataset_thumbnail_operation(kind):
    if kind not in {'memo_get','hash_source','probe_header','render_jpeg','render_thumbnail'}:raise ValueError('Unknown thumbnail operation')
    def decorate(function):
        @wraps(function)
        def observed(*args,**kwargs):
            attempt=_ACTIVE.get()
            if type(attempt) is not _AncillaryAttempt or attempt.name!='get_thumbnail':return function(*args,**kwargs)
            valid=attempt.resource_entry('thumbnail_'+kind)
            source=attempt.thumbnail_source()
            from backend.api import routes_dataset
            if kind=='render_jpeg':
                valid=valid and len(args)==2 and not kwargs and any(image is args[0] for image,_,_ in attempt.images) and args[1]==attempt.original_values['size']
            else:
                valid=valid and bool(args) and Path(args[0]).resolve()==source
                if kind=='hash_source' and len(args)>1:valid=valid and len(args)==2 and any(spool is args[1] for spool in attempt.thumbnail_spools)
            if not valid:attempt.fail('thumbnail_operation_binding')
            ticket=object();attempt.dataset_live.append(ticket)
            try:result=function(*args,**kwargs)
            except BaseException:attempt.fail('thumbnail_operation_failed');raise
            else:
                # The pinned original memo body returns through its builtin
                # lock context's exit. A different original worker may already
                # hold that same lock, so its global locked() state cannot
                # establish whether this operation released its own scope.
                if not valid or not attempt.resource_entry('thumbnail_operation_return'):attempt.fail('thumbnail_operation_return')
                else:attempt.thumbnail_operations.append((kind,result))
                return result
            finally:
                if ticket not in attempt.dataset_live:attempt.fail('thumbnail_operation_ticket')
                else:attempt.dataset_live.remove(ticket)
        _RESOURCES['thumbnail_'+kind]=observed
        return observed
    return decorate


def original_dataset_thumbnail_spool(spool):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='get_thumbnail':return
    from backend.api import routes_dataset
    frame=sys._getframe(1)
    valid=(attempt.resource_entry('thumbnail_spool') and frame.f_code is routes_dataset._render_thumbnail.__wrapped__.__code__
        and frame.f_locals.get('copy') is spool and type(spool) is attempt.owner.thumbnail_spool_kind and not spool.closed
        and not attempt.thumbnail_spools and _methods_original(spool,attempt.owner.thumbnail_spool_kind,attempt.owner.calls))
    if not valid:attempt.fail('thumbnail_spool_binding')
    attempt.thumbnail_spools.append(spool)


def original_dataset_thumbnail_image_open(function,*args,**kwargs):
    image=function(*args,**kwargs);attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='get_thumbnail':return image
    from PIL import Image
    handle=getattr(image,'fp',None);target=args[0] if len(args)==1 and not kwargs else None
    valid=(attempt.resource_entry('thumbnail_image') and function is Image.open and type(image) is attempt.owner.png_kind
        and isinstance(handle,io.IOBase) and not handle.closed)
    if type(target) is attempt.owner.thumbnail_spool_kind:
        valid=valid and handle is target and any(row is target for row in attempt.thumbnail_spools)
    else:
        valid=valid and type(target) is type(Path('.')) and target.resolve()==attempt.thumbnail_source()
        if valid and attempt.selected_image_inputs:
            valid=attempt.selected_image_handle(target.resolve(),handle,attempt.selected_image_inputs[0][3])
    if not valid:attempt.fail('thumbnail_image_binding')
    attempt.images.append((image,handle,attempt.thumbnail_source()))
    return image


def original_dataset_thumbnail_file(handle,path):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='get_thumbnail':return
    from backend.api import routes_dataset
    frame=sys._getframe(1)
    valid=(attempt.resource_entry('thumbnail_source_file') and frame.f_code is routes_dataset._hash_source.__wrapped__.__code__
        and frame.f_locals.get('handle') is handle and Path(path).resolve()==attempt.thumbnail_source())
    if not valid:attempt.fail('thumbnail_file_binding')
    attempt.record_file(handle,path,'thumbnail_source')


def original_dataset_thumbnail_write(path,value):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='get_thumbnail':return path.write_bytes(value)
    from backend.api import routes_dataset
    frame=sys._getframe(1)
    valid=(attempt.resource_entry('thumbnail_write') and frame.f_code is routes_dataset._render_thumbnail.__wrapped__.__code__
        and frame.f_locals.get('staging') is path and frame.f_locals.get('rendered') is value
        and path.parent==routes_dataset.THUMBNAIL_CACHE_DIR and type(value) is bytes)
    if not valid:attempt.fail('thumbnail_write_binding')
    with path.open('wb') as handle:
        attempt.record_file(handle,path,'thumbnail_cache')
        return handle.write(value)


def original_dataset_thumbnail_publish(source,target):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='get_thumbnail':return os.replace(source,target)
    from backend.api import routes_dataset
    frame=sys._getframe(1)
    valid=(attempt.resource_entry('thumbnail_publish') and frame.f_code is routes_dataset._render_thumbnail.__wrapped__.__code__
        and frame.f_locals.get('staging') is source and frame.f_locals.get('cached') is target
        and source.parent==target.parent==routes_dataset.THUMBNAIL_CACHE_DIR)
    if not valid:attempt.fail('thumbnail_publication_binding')
    result=os.replace(source,target)
    if not valid or not attempt.resource_entry('thumbnail_publication_return') or source.exists() or not target.is_file():attempt.fail('thumbnail_publication_return')
    else:attempt.thumbnail_publications.append((source,target))
    return result


def _store_methods_original(instance,kind,calls):
    """Exact retained DatasetIndex/JobStore methods, including original statics.

    Generic _methods_original remains unchanged for its existing Python-bound
    owners. These two real store classes also have declared static functions;
    a static is the exact captured function, while an instance method must keep
    the exact original self/function binding continuously through ASGI finish.
    """
    rows=tuple((name,function) for owner,name,function in calls if owner is kind)
    if type(instance) is not kind or not rows:return False
    for name,function in rows:
        descriptor=inspect.getattr_static(kind,name)
        live=getattr(instance,name,None)
        if type(descriptor) is staticmethod:
            if descriptor.__func__ is not function or live is not function:return False
        elif (type(descriptor) is not FunctionType or descriptor is not function
                or type(live) is not MethodType or live.__self__ is not instance
                or live.__func__ is not function):return False
    return True



def observe_original_UI_global_cursor(function):
    @wraps(function)
    def observed(cursor,stream,ledger_id,store):
        attempt=_global_attempt('job_events')
        if attempt is None:return function(cursor,stream,ledger_id,store)
        caller=sys._getframe(1);frame=attempt.handler_frame()
        valid=(attempt.resource_entry('ledger_cursor') and caller is frame and not attempt.global_cursor_live
            and attempt.global_cursor is None and store is attempt.global_store
            and _store_methods_original(store,attempt.owner.global_job_kind,attempt.owner.calls)
            and cursor is attempt.original_values.get('after') and type(cursor) is str and 0<len(cursor)<=200
            and stream is caller.f_locals.get('stream') and ledger_id is caller.f_locals.get('ledger_id')
            and [name for name,_ in attempt.global_jobs]==['ledger_id'])
        if not valid:attempt.fail('ledger_cursor_entry')
        attempt.global_cursor_live=True
        try:result=function(cursor,stream,ledger_id,store)
        except BaseException:attempt.fail('ledger_cursor_failed');raise
        else:
            if (not valid or not attempt.worker_original() or result is not None and (type(result) is not int or not 0<=result<2**63)):
                attempt.fail('ledger_cursor_return')
            else:attempt.global_cursor=cursor,stream,ledger_id,store,result
            return result
        finally:attempt.global_cursor_live=False
    _RESOURCES['global_cursor']=observed
    return observed

def observe_original_dataset_provenance_operation(kind):
    if kind not in {'data_state','version_verify','training_binding','evaluation_get','package_library','verify_package','checkpoint_hash'}:raise ValueError('Unknown original provenance read')
    def decorate(function):
        @wraps(function)
        def observed(*args,**kwargs):
            attempt=_ACTIVE.get()
            if (type(attempt) is not _AncillaryAttempt or attempt.name not in {'workflow_impact','packages'}
                    or attempt.name=='packages' and kind not in {'package_library','verify_package'}):return function(*args,**kwargs)
            project=attempt.scope.get('state',{}).get('scoped_project');root=Path(project['project_dir']).resolve()
            valid=attempt.resource_entry('provenance_'+kind) and not kwargs
            value=args[0] if args else None;input_path=None
            if kind=='data_state':valid=valid and len(args)==2 and value is project and (args[1] is None or type(args[1]) is dict)
            elif kind=='version_verify':valid=valid and len(args)==3 and value is project and Path(args[1]).resolve().is_relative_to(root/'versions') and type(args[2]) is dict
            elif kind=='training_binding':valid=valid and len(args)==2 and value is project and Path(args[1]).resolve()==Path(project['source_dataset_dir']).resolve()
            elif kind=='evaluation_get':
                valid=(valid and len(args)==2 and type(value) is attempt.owner.dataset_evaluation_kind
                    and value.directory==root/'reports'/'evaluations'
                    and _methods_original(value,attempt.owner.dataset_evaluation_kind,attempt.owner.calls))
                attempt.dataset_objects.append((value,attempt.owner.dataset_evaluation_kind))
            elif kind=='package_library':
                expected={**project,'_delivery_account_id':attempt.auth.account['id']} if attempt.name=='packages' else project
                valid=valid and len(args)==1 and (value==expected if attempt.name=='packages' else value is project)
            elif kind=='verify_package':
                input_path=Path(value).resolve() if len(args)==1 else Path('.')
                valid=valid and len(args)==1 and input_path.is_relative_to(root/'exports'/'flows')
            elif kind=='checkpoint_hash':
                input_path=Path(value).resolve() if len(args)==1 else Path('.')
                valid=valid and len(args)==1 and input_path.is_relative_to(Path(project['models_dir']).resolve()) and input_path.name=='best_model.pt'
            if not valid:attempt.fail('provenance_operation_binding')
            ticket=object();attempt.dataset_live.append(ticket)
            try:result=function(*args,**kwargs)
            except BaseException:attempt.fail('provenance_operation_failed');raise
            else:
                if not valid or not attempt.resource_entry('provenance_operation_return'):attempt.fail('provenance_operation_return')
                else:attempt.provenance_operations.append((kind,value,input_path,result))
                return result
            finally:
                if ticket not in attempt.dataset_live:attempt.fail('provenance_operation_ticket')
                else:attempt.dataset_live.remove(ticket)
        _RESOURCES['dataset_provenance_'+kind]=observed
        return observed
    return decorate


def original_dataset_provenance_file(handle,path):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name not in {'workflow_impact','packages'}:return
    from backend.engine import workflow_impact,flow_package_runtime
    from backend.api import routes_dataset_versions
    frame=sys._getframe(1)
    functions=((flow_package_runtime._sha256,'source'),) if attempt.name=='packages' else (
        (workflow_impact._hash.__wrapped__,'stream'),(routes_dataset_versions._file_hash,'source'),(flow_package_runtime._sha256,'source'))
    valid=(attempt.resource_entry('provenance_file') and any(frame.f_code is function.__code__
        and frame.f_locals.get(name) is handle and frame.f_locals.get('path') is path for function,name in functions))
    if not valid:attempt.fail('provenance_file_source')
    attempt.record_file(handle,path,'dataset_UI')


def original_dataset_provenance_database(connection,database):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='workflow_impact':return
    from backend.engine import workflow_impact
    frame=sys._getframe(1);project=attempt.scope.get('state',{}).get('scoped_project')
    valid=(attempt.resource_entry('provenance_database') and frame.f_code is workflow_impact._analyze.__code__
        and frame.f_locals.get('connection') is connection and frame.f_locals.get('database') is database
        and type(connection) is sqlite3.Connection and database==Path(project['project_dir']).resolve()/'model_deployments.sqlite3'
        and not attempt.provenance_connections)
    if not valid:attempt.fail('provenance_database_binding')
    attempt.provenance_connections.append(connection)


def original_dataset_provenance_journal_read(path,*,encoding=None,errors=None,role):
    attempt=_ACTIVE.get()
    if type(attempt) is not _AncillaryAttempt or attempt.name!='workflow_impact':
        return original_UI_ancillary_read_text(path,encoding=encoding,errors=errors,role=role)
    from backend.engine import product_delivery
    frame=sys._getframe(1);project=attempt.scope.get('state',{}).get('scoped_project')
    valid=(attempt.resource_entry('provenance_journal') and frame.f_code is product_delivery._journal.__code__
        and frame.f_locals.get('path') is path and role=='package'
        and path==Path(project['project_dir']).resolve()/'delivery'/'library.json')
    if not valid:attempt.fail('provenance_journal_binding')
    return original_dataset_UI_read_text(path,encoding=encoding,errors=errors)


class _DatasetThumbnailNotModifiedResponse(_ThumbnailNotModifiedResponseBase):
    async def __call__(self,scope,receive,send):
        attempt=self._dataset_attempt
        valid=(type(attempt) is _AncillaryAttempt and attempt.thumbnail_response_entry(self,scope,receive,send)
            and attempt.thumbnail_return(self) and not attempt.thumbnail_response_live and not attempt.thumbnail_response_done)
        if not valid:attempt.fail('thumbnail_304_entry')
        attempt.thumbnail_response_live=True;starts=terminals=0
        async def retained_send(message):
            nonlocal starts,terminals
            frame=sys._getframe(1)
            good=(valid and attempt.thumbnail_response_entry(self,scope,receive,send)
                and frame.f_code is _ThumbnailNotModifiedResponseBase.__call__.__code__
                and frame.f_locals.get('self') is self and frame.f_locals.get('scope') is scope
                and frame.f_locals.get('send') is retained_send and type(message) is dict)
            if message.get('type')=='http.response.start':
                starts+=1;good=(good and starts==1 and terminals==0 and set(message)=={'type','status','headers'}
                    and message['status']==304 and message['headers'] is self.raw_headers)
            elif message.get('type')=='http.response.body':
                terminals+=1;good=(good and starts==1 and terminals==1 and set(message)=={'type','body'}
                    and message['body'] is self.body and message['body']==b'')
            else:good=False
            if not good:attempt.fail('thumbnail_304_original_send')
            await send(message)
            if not attempt.thumbnail_response_entry(self,scope,receive,send):attempt.fail('thumbnail_304_after_send')
        try:
            await _ThumbnailNotModifiedResponseBase.__call__(self,scope,receive,retained_send)
        except BaseException:attempt.fail('thumbnail_304_failed');raise
        else:
            if not valid or starts!=1 or terminals!=1 or not attempt.thumbnail_response_entry(self,scope,receive,send):attempt.fail('thumbnail_304_unjoined')
            else:attempt.thumbnail_response_done=True
        finally:attempt.thumbnail_response_live=False
