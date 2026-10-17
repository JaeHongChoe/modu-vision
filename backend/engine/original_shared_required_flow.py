"""Passive original authorized continuation; no authentication or release authority."""
import asyncio
import dis
from contextlib import contextmanager, _GeneratorContextManager
from contextvars import ContextVar
import os
from pathlib import Path
import stat
import sys
from types import MethodType

_ACTIVE = ContextVar('original_authorized_required_flow_continuation', default=None)


def _file_identity(path):
    value=path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink!=1 or value.st_uid!=os.getuid() or path.resolve()!=path:return None
    return value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid


def _directory_identity(path):
    value=path.lstat()
    if not stat.S_ISDIR(value.st_mode) or value.st_uid!=os.getuid() or path.resolve()!=path:return None
    return value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid


def _authorized_entry_offsets(code):
    """Only the exact original final continuation import and direct call."""
    if code is None:return ()
    instructions=tuple(dis.get_instructions(code))
    hook='original_shared_flow_continuation'
    pattern=(('LOAD_CONST',0),('LOAD_CONST',(hook,)),
        ('IMPORT_NAME','backend.engine.original_shared_required_flow'),
        ('IMPORT_FROM',hook),('STORE_FAST',hook),('POP_TOP',None),
        ('LOAD_FAST',hook),('PUSH_NULL',None),('LOAD_FAST','self'),
        ('LOAD_DEREF','scope'),('CALL',2),('BEFORE_WITH',None))
    matches=[]
    for position in range(len(instructions)-len(pattern)+1):
        rows=instructions[position:position+len(pattern)]
        if all(row.opname==name and row.argval==value for row,(name,value)in zip(rows,pattern)):
            matches.append(rows[-1].offset)
    stores=tuple(row for row in instructions if row.opname in {'STORE_FAST','DELETE_FAST'}and row.argval==hook)
    return tuple(matches)if len(matches)==1 and len(stores)==1 else ()


class OriginalSharedRequiredFlowOwner:
    """Only the final original shared-auth downstream call can mint this data."""
    def __init__(self,app,admission,auth_type):
        from backend.engine.shared_accounts import AccountStore
        from backend.api.shared_authorization import SharedAuthorizationMiddleware
        from backend.api import shared_authorization
        from backend.contracts.authentication import PermissionDecision,permission_action
        self.app,self.state,self.admission=app,app.state,admission
        self.store=app.state.accounts;self.kind=AccountStore;self.auth_type=auth_type
        self.registry=app.state.context_registry;self.workspace=self.registry.workspace_id
        self.path=self.store.path if type(self.store)is AccountStore else None
        self.path_identity=_file_identity(self.path) if self.path is not None else None
        self.parent_identity=_directory_identity(self.path.parent)if self.path is not None else None
        self.decision_type=PermissionDecision;self.permission_action=permission_action
        self.auth_body=getattr(auth_type.__call__,'__wrapped__',None)
        self.auth_body_code=getattr(self.auth_body,'__code__',None)
        self.auth_globals=getattr(self.auth_body,'__globals__',None)
        self.hook_body=original_shared_flow_continuation.__wrapped__
        self.hook_code=self.hook_body.__code__
        self.enter_call=_GeneratorContextManager.__enter__;self.enter_code=self.enter_call.__code__
        self.entry_offsets=_authorized_entry_offsets(self.auth_body_code)
        self.active_context=_ACTIVE
        self.pid=os.getpid();self.middleware=self.stack=self.downstream=None
        self.eligible=(auth_type is SharedAuthorizationMiddleware and self.path_identity is not None and self.parent_identity is not None and len(self.entry_offsets)==1)
        self.calls=tuple((owner,name,getattr(owner,name)) for owner,names in (
            (AccountStore,('_db','bind_workspace','authenticate','cookie_session','project_for','project_role','authorize','record_decision')),
            (SharedAuthorizationMiddleware,('__init__','__call__')),
            (shared_authorization,('permission_action','browser_origin_allowed')),
            (sys.modules[__name__],('_file_identity','_directory_identity','_authorized_entry_offsets','original_shared_flow_continuation')),
            (_GeneratorContextManager,('__enter__',)),
            (OriginalSharedRequiredFlowOwner,('original','_entry','begin','current')),
            (_OriginalSharedContinuation,('original',))) for name in names)
        self.codes=[];self.wrapped=[]
        for _,_,function in self.calls:
            for _ in range(4):
                if hasattr(function,'__code__'):self.codes.append((function,function.__code__))
                original=getattr(function,'__wrapped__',None)
                if original is None:break
                self.wrapped.append((function,original));function=original

    def original(self):
        try:valid_path=(_file_identity(self.path)==self.path_identity and _directory_identity(self.path.parent)==self.parent_identity)
        except (OSError,AttributeError):valid_path=False
        return (_ACTIVE is self.active_context and self.eligible and valid_path and self.pid==os.getpid() and self.app.state is self.state
            and self.state.accounts is self.store and type(self.store)is self.kind and self.store.path is self.path
            and self.state.owned_work_admission is self.admission and self.state.context_registry is self.registry
            and self.registry.workspace_id is self.workspace
            and all(getattr(owner,name)is function for owner,name,function in self.calls)
            and all(function.__code__ is code for function,code in self.codes)
            and all(function.__wrapped__ is original for function,original in self.wrapped)
            and all(type(getattr(self.store,name))is MethodType and getattr(self.store,name).__self__ is self.store
                and getattr(self.store,name).__func__ is function for owner,name,function in self.calls if owner is self.kind))

    def _entry(self,middleware,scope):
        # Actual live call chain, not an echoed token or caller-supplied frame.
        begin=sys._getframe(1);hook=begin.f_back;enter=hook.f_back if hook is not None else None;auth=enter.f_back if enter is not None else None
        if (begin.f_code is not OriginalSharedRequiredFlowOwner.begin.__code__
                or hook is None or hook.f_code is not self.hook_code or hook.f_globals is not self.hook_body.__globals__
                or enter is None or enter.f_code is not self.enter_code or _GeneratorContextManager.__enter__ is not self.enter_call
                or auth is None or auth.f_code is not self.auth_body_code or auth.f_globals is not self.auth_globals
                or auth.f_lasti not in self.entry_offsets):return False
        manager=enter.f_locals.get('self')
        if getattr(getattr(manager,'gen',None),'gi_frame',None)is not hook:return False
        values=auth.f_locals;state=scope.get('state',{})
        return (values.get('self')is middleware and values.get('scope')is scope and values.get('state')is state
            and values.get('store')is self.store and values.get('account')is state.get('account_user')
            and values.get('project')is state.get('scoped_project') and values.get('decision')is state.get('permission_decision')
            and values.get('token')is state.get('account_session_token')
            and values.get('role')==getattr(state.get('permission_decision'),'role',None)
            and values.get('path')=='/api/flowchart/run' and values.get('method')=='POST')

    def begin(self,middleware,scope):
        if not self.original() or type(middleware)is not self.auth_type or middleware.project_app is not self.app:return None
        if not self._entry(middleware,scope):return None
        if (type(scope)is not dict or scope.get('app')is not self.app or scope.get('type')!='http'
                or scope.get('method')!='POST' or scope.get('path')!='/api/flowchart/run' or scope.get('query_string',b'')!=b''):return None
        # Retain only an actual member of this factory-created original stack.
        node=self.app.middleware_stack;found=False
        for _ in range(16):
            if node is middleware:found=True;break
            node=getattr(node,'app',None)
            if node is None:break
        if not found:return None
        if self.middleware is None:self.middleware,self.stack,self.downstream=middleware,self.app.middleware_stack,middleware.app
        if middleware is not self.middleware or self.app.middleware_stack is not self.stack or middleware.app is not self.downstream:return None
        state=scope.get('state',{});account=state.get('account_user');project=state.get('scoped_project');decision=state.get('permission_decision')
        if (type(account)is not dict or type(project)is not dict or type(decision)is not self.decision_type
                or decision.allowed is not True or decision.reason!='allowed' or decision.actor_id!=account.get('id')
                or decision.project_id!=project.get('id') or decision.workspace_id!=self.workspace
                or decision.action!=self.permission_action('/api/flowchart/run','POST') or decision.role not in {'owner','trainer','reviewer'}
                or type(state.get('account_session_token'))is not str or not state['account_session_token']):return None
        task=asyncio.current_task();loop=asyncio.get_running_loop()
        return _OriginalSharedContinuation(self,middleware,scope,state,account,project,decision,task,loop)

    def current(self,request):
        witness=_ACTIVE.get()
        if type(witness)is not _OriginalSharedContinuation or witness.owner is not self or not witness.original(request):
            raise ValueError('Original authorized shared-flow continuation unavailable or changed')
        return witness


class _OriginalSharedContinuation:
    def __init__(self,owner,middleware,scope,state,account,project,decision,task,loop):
        self.owner,self.middleware,self.scope,self.state=owner,middleware,scope,state
        self.account,self.project,self.decision=account,project,decision
        self.headers=tuple(scope.get('headers',()));self.session=state['account_session_token']
        self.task,self.loop=task,loop;self.pid=os.getpid();self.active=True
        self.values=(account.get('id'),project.get('id'),decision.actor_id,decision.project_id,decision.workspace_id,decision.action,decision.role)
        self.account_values=tuple(account.items());self.decision_values=tuple(vars(decision).items())
        self.project_keys=('id','project_dir','models_dir','source_dataset_dir','task','active_labelset_id')
        self.project_values=tuple(project.get(k)for k in self.project_keys)

    def __copy__(self):raise ValueError('Original shared-flow continuation cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):return self.__copy__()

    def original(self,request):
        owner=self.owner;scope=request.scope;state=scope.get('state',{})
        from backend.contracts.context import current_project_context
        context=state.get('project_context')
        return (self.active and self.pid==os.getpid() and owner.original() and request.app is owner.app
            and scope is self.scope and scope.get('app')is owner.app and state is self.state
            and owner.middleware is self.middleware and owner.app.middleware_stack is owner.stack and self.middleware.app is owner.downstream
            and self.task is not None and not self.task.done() and self.loop.is_running()
            and tuple(scope.get('headers',()))==self.headers and state.get('account_session_token')is self.session
            and state.get('account_user')is self.account and state.get('scoped_project')is self.project and state.get('permission_decision')is self.decision
            and self.values==(self.account.get('id'),self.project.get('id'),self.decision.actor_id,self.decision.project_id,self.decision.workspace_id,self.decision.action,self.decision.role)
            and tuple(self.account.items())==self.account_values and tuple(vars(self.decision).items())==self.decision_values
            and tuple(self.project.get(k)for k in self.project_keys)==self.project_values
            and self.decision.allowed is True and context is not None and current_project_context.get()is context
            and context.actor_id==self.account['id'] and context.project_id==self.project['id'] and context.workspace_id==owner.workspace and context.mode=='team')


@contextmanager
def original_shared_flow_continuation(middleware,scope):
    """After original authorization only; exceptions/results pass through unchanged."""
    selector=getattr(middleware.project_app.state,'required_retained_flow_capture',None)
    owner=getattr(selector,'shared_owner',None)
    try:witness=owner.begin(middleware,scope) if type(owner)is OriginalSharedRequiredFlowOwner else None
    except Exception:witness=None  # Passive evidence cannot turn original authorization into a failure.
    if witness is None:
        readback=None
        if type(owner)is OriginalSharedRequiredFlowOwner and scope.get('method')=='GET' and getattr(selector,'http_capture',None)is not None:
            from backend.engine.required_flow_http_capture import begin_original_get_readback,end_original_get_readback
            readback=begin_original_get_readback(owner,middleware,scope)
        elif type(owner)is OriginalSharedRequiredFlowOwner and scope.get('method')=='POST' and scope.get('path')in {'/api/flowchart/pipeline','/api/export/flow'} and getattr(selector,'http_capture',None)is not None:
            from backend.engine.required_flow_http_capture import begin_original_exchange_readback
            from backend.engine.required_flow_http_capture import end_original_get_readback
            readback=begin_original_exchange_readback(owner,middleware,scope)
        readback_normal=False
        try:
            yield
            readback_normal=True
        finally:
            if readback is not None:end_original_get_readback(readback,readback_normal)
        return
    token=_ACTIVE.set(witness)
    try:yield
    finally:
        witness.active=False
        _ACTIVE.reset(token)
