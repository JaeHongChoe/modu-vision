"""One original selected-compute sub-lifetime; no execution/launch authority.

Only the original ExecutionAttempt may own these observations. Thread birth,
SQLite close, terminal lease deletion and operation-fd close stay separate from
remote terminal status and the original owned-worker exit confirmation.
"""
from contextlib import contextmanager
from functools import wraps
import errno
import inspect
import io
import os
from pathlib import Path
import sqlite3
import stat
import sys
import threading
from types import FunctionType, MethodType
import weakref

_THREADS = weakref.WeakKeyDictionary()
_LOCK = threading.RLock()
_KINDS = {'run', 'monitor', 'worker', 'confirm', 'profile', 'acquire', 'heartbeat', 'release', 'uncertain'}


def _bound(value, kind, calls):
    if type(value) is not kind: return False
    for owner, name, function in calls:
        if owner is not kind: continue
        live = getattr(value, name, None)
        raw = inspect.getattr_static(kind, name)
        if type(raw) is staticmethod:
            if live is not function: return False
        else:
            expected = function.__get__(value, kind)
            if (type(live) is not type(expected) or live != expected
                    or getattr(live, '__self__', None) is not value): return False
    return True


def _frame(function):
    frame = sys._getframe(1)
    for _ in range(64):
        if frame is None: return None
        if frame.f_code is function.__code__: return frame
        frame = frame.f_back
    return None


def _current():
    from backend.engine.original_owned_flow_execution_request import _attempt
    attempt = _attempt()
    value = getattr(attempt, 'selected', None)
    if type(value) is SelectedComputeLifetime: return value
    with _LOCK: value = _THREADS.get(threading.current_thread())
    return value if type(value) is SelectedComputeLifetime else None


def observe_original_selected_compute(kind):
    if kind not in _KINDS: raise ValueError('Unknown selected-compute observation')
    def decorate(function):
        @wraps(function)
        def observed(*args, **kwargs):
            value = _current()
            if value is None: return function(*args, **kwargs)
            ticket = value.enter(kind, function, args, kwargs)
            try: result = function(*args, **kwargs)
            except BaseException:
                value.fail(kind+'_failed')
                raise
            else:
                value.returned(kind, function, args, kwargs, result, ticket)
                return result
        return observed
    return decorate


def observe_original_selected_lease_connection(function):
    @wraps(function)
    def observed(store, *args, **kwargs):
        manager = function(store, *args, **kwargs)
        value = _current()
        if value is None: return manager
        @contextmanager
        def retained():
            ticket = value.database_entry(store, manager)
            connection = None
            try:
                with manager as connection:
                    frame = manager.gen.gi_frame
                    if (type(connection) is not sqlite3.Connection or frame is None
                            or frame.f_locals.get('self') is not store or frame.f_locals.get('conn') is not connection):
                        value.fail('lease_connection_source')
                    yield connection
            except BaseException:
                value.fail('lease_database_failed')
                raise
            else:
                closed = False
                if type(connection) is sqlite3.Connection:
                    try: connection.in_transaction
                    except sqlite3.ProgrammingError: closed = True
                if not (ticket and closed and manager.gen.gi_frame is None and value.source_original()):
                    value.fail('lease_database_not_closed')
                else: value.database_done += 1
            finally: value.database_live -= 1
        return retained()
    return observed


def observe_original_selected_maintenance(function):
    @wraps(function)
    def observed(*args, **kwargs):
        manager = function(*args, **kwargs)
        value = _current()
        if value is None: return manager
        @contextmanager
        def retained():
            handle = observation = record = None
            valid = value.source_original()
            value.maintenance_live += 1
            try:
                with manager as result:
                    frame = manager.gen.gi_frame
                    if frame is None: value.fail('maintenance_source')
                    else:
                        handle = frame.f_locals.get('handle')
                        record = frame.f_locals.get('ownership') or frame.f_locals.get('prior')
                        observation = record.get('original_lock_observation') if type(record) is dict else None
                        # Reentrant admissions retain the exact original outer
                        # record; only its original owner may reenter it.
                        if observation is not None:
                            from backend.engine.original_lock_acquisition import OriginalLockAcquisition
                            if not _bound(observation,OriginalLockAcquisition,value.calls):
                                value.fail('maintenance_observation_changed')
                            else: observation.current(record=record)
                        else:
                            value.fail('maintenance_acquisition_missing')
                    yield result
            except BaseException:
                value.fail('maintenance_failed')
                raise
            else:
                if not valid or manager.gen.gi_frame is not None or handle is not None and not handle.closed:
                    value.fail('maintenance_not_closed')
                else: value.maintenance_done += 1
            finally: value.maintenance_live -= 1
        return retained()
    return observed


def selected_source_resolution(function, args, kwargs, kind):
    value = _current()
    if value is None: return function(*args, **kwargs)
    ticket = value.resolver_entry(function, args, kwargs, kind)
    try: result = function(*args, **kwargs)
    except BaseException:
        value.fail(kind+'_failed')
        raise
    else:
        value.resolver_return(function, args, kwargs, kind, result, ticket)
        return result


def selected_file(handle, path, role):
    value = _current()
    if value is not None: value.record_file(handle, path, role)


def selected_read_text(path, encoding=None, errors=None):
    value = _current()
    if value is None: return path.read_text(encoding=encoding, errors=errors)
    with path.open('r', encoding=io.text_encoding(encoding), errors=errors) as handle:
        value.record_file(handle, path, 'operation_text')
        return handle.read()


def selected_pin_open(descriptor, path):
    value = _current()
    if value is not None: value.pin_open(descriptor, path)


def selected_pin_closed(descriptor):
    value = _current()
    if value is not None: value.pin_closed(descriptor)


def selected_operation_fd(descriptor, path):
    value = _current()
    if value is not None: value.operation_fd(descriptor, path)


def selected_operation_closed(descriptor):
    value = _current()
    if value is not None: value.operation_closed(descriptor)


def selected_thread_born(thread, target, stop, leases, key):
    value = _current()
    if value is not None: value.thread_born(thread, target, stop, leases, key)


def _current_profile_path(store):
    from backend.remote import profiles
    from backend.engine import global_store_paths
    nominal = profiles.default_profile_path()
    if store.path == nominal: return True
    try:
        if profiles.resolve_store_path is not global_store_paths.resolve_store_path: return False
        root, owner = global_store_paths.owned_root(nominal)
        if root is None or nominal != root / owner['scopes']['profiles']: return False
        active = global_store_paths.active_generation(root)
        return (active is not None and store.path == active[0] / owner['scopes']['profiles']
            and store.path == profiles.resolve_store_path(nominal))
    except Exception:
        # Optional ownership inspection must not replace the original get.
        return False


@contextmanager
def selected_heartbeat():
    value = _current()
    if value is None:
        yield
        return
    valid = value.heartbeat_original()
    if not valid: value.fail('heartbeat_birth')
    value.heartbeat_live = True
    try: yield
    except BaseException:
        value.fail('heartbeat_target_failed')
        raise
    else:
        if valid and value.heartbeat_original() and value.database_live == 0:
            value.heartbeat_exited = True
        else: value.fail('heartbeat_target_exit')
    finally: value.heartbeat_live = False


def _original_sources():
    """Capture original factories at app construction, before any request."""
    from backend.api import routes_flowchart as flows
    from backend.engine import retained_preprocessors as retained, shared_scheduler as scheduler, migration_guard, global_store_paths, original_lock_acquisition
    from backend.remote import operations, coordinator, profiles, ssh_transport
    calls = [(module, name, getattr(module, name)) for module, names in (
        (operations, ('run_verified_flowchart_on_compute', 'run_remote_operation_artifacts', '_run_remote_operation_artifacts',
            '_open_operation_lock', '_confirm_owned_exit', '_operation_journal_path', '_save_operation', '_launch_or_resume',
            '_bundle_backend', 'names_descriptor', 'fcntl', 'observe_original_selected_compute', 'selected_operation_fd',
            'selected_operation_closed', 'selected_thread_born', 'selected_heartbeat', 'selected_read_text',
            'SSHTransport', 'RemoteJobContext', '_sha256', 'threading', 'os')),
        (coordinator, ('_confirm_owned_exit', '_sha256', '_atomic_json', '_bundle_backend', 'selected_file', 'observe_original_selected_compute')),
        (profiles, ('get_profile_store', 'ProfileStore', '_STORE_LOCK', 'default_profile_path', 'resolve_store_path', 'store_admission', 'selected_file', 'observe_original_selected_compute')),
        (profiles.ProfileStore, ('__init__', 'get', '_read')),
        (profiles, ('ComputeProfile',)),
        (scheduler, ('ResourceLeases', 'shared_leases', 'store_admission', 'use_wal', 'sqlite3', 'observe_original_selected_compute', 'observe_original_selected_lease_connection')),
        (scheduler.ResourceLeases, ('__init__', 'connect', 'acquire', 'heartbeat', 'release', 'mark_uncertain', '_available', '_assess', '_identities', '_overlap', 'conflict')),
        (retained, ('resolve_retained_rotation', 'retained_rotation_transport', 'retained_transport_model_root', '_TRANSPORT', '_pin', '_exact', '_source_model', 'read_registered_project', 'selected_pin_open', 'selected_pin_closed')),
        (flows, ('get_current_project', '_resolve_project_specialist', '_registered_retained_source')),
        (migration_guard, ('maintenance_guard', '_execution_owner', '_admission_capability', '_HELD', 'observe_original_selected_maintenance')),
        (original_lock_acquisition, ('_capture_successful_acquisition', 'OriginalLockAcquisition')),
        (original_lock_acquisition.OriginalLockAcquisition, ('__init__', 'current', 'invalidate', '__copy__', '__deepcopy__', '__reduce_ex__')),
        (global_store_paths, ('store_admission', 'owned_root', '_assert_current_store', 'resolve_store_path', 'active_generation', 'validate_owner', '_hex')),
        (ssh_transport, ('SSHTransport',)),
        (ssh_transport.SSHTransport, ('__init__', 'launch', 'recover_handle', 'stop_owned', 'is_running', 'exec', 'upload', 'download')),
        (threading, ('Thread', 'Event', 'current_thread')),
        (threading.Thread, ('__init__', 'start', 'join', 'is_alive', 'run')),
        (threading.Event, ('wait', 'set', 'is_set')),
        (sqlite3, ('Connection', 'connect')),
        (os, ('open', 'close', 'fdopen', 'fstat', 'getpid')),
        (sys.modules[__name__], ('_THREADS', '_LOCK', 'SelectedComputeLifetime')),
    ) for name in names]
    for name, value in tuple(globals().items()):
        if type(value) is FunctionType and value.__module__ == __name__:
            calls.append((sys.modules[__name__], name, value))
    for name in dir(SelectedComputeLifetime):
        value = getattr(SelectedComputeLifetime, name)
        if type(value) is FunctionType: calls.append((SelectedComputeLifetime, name, value))
    if operations.fcntl is not None:
        calls.append((operations.fcntl, 'flock', operations.fcntl.flock))
    return calls


class SelectedComputeLifetime:
    def __init__(self, attempt):
        from backend.api import routes_flowchart as flows
        from backend.engine import retained_preprocessors as retained, shared_scheduler as scheduler, migration_guard, global_store_paths, original_lock_acquisition
        from backend.remote import operations, coordinator, profiles, ssh_transport
        self.attempt = attempt
        self.pid = os.getpid()
        self.creator = threading.current_thread()
        self.failed = False
        self.live = {}
        self.completed = []
        self.sources = []
        self.source_live = None
        self.specialist_live = None
        self.specialists = []
        self.verified_checkpoints = self.verified_binding = None
        self.files = []
        self.pins = {}
        self.pin_done = 0
        self.profile = self.profile_store = None
        self.transport = None
        self.target_code = None
        self.context = self.operation_spec = self.journal_path = None
        self.descriptor = self.descriptor_path = None
        self.descriptor_closed = False
        self.leases = self.thread = self.target = self.stop = self.lease_key = None
        self.thread_methods = self.event_methods = None
        self.heartbeat_live = self.heartbeat_exited = self.thread_started = False
        self.database_live = self.database_done = 0
        self.maintenance_live = self.maintenance_done = 0
        self.acquired = self.released = self.confirmed = False
        self.stores = []
        self.binding = self, attempt, attempt.owner, attempt.activity, attempt.token
        req=attempt.req
        self.request_binding=(attempt.request,req,req.pipeline,req.compute_profile_id,req.device,req.project_id,req.image_id,req.stop_node_id)
        self.pipeline_value=req.pipeline.model_dump()
        self.modules = flows, retained, scheduler, migration_guard, global_store_paths, operations, coordinator, profiles, ssh_transport
        self.functions = {
            'run': operations.run_verified_flowchart_on_compute,
            'monitor': operations.run_remote_operation_artifacts,
            'worker': operations._run_remote_operation_artifacts,
            'confirm': operations._confirm_owned_exit,
            'profile': profiles.ProfileStore.get,
            'acquire': scheduler.ResourceLeases.acquire,
            'heartbeat': scheduler.ResourceLeases.heartbeat,
            'release': scheduler.ResourceLeases.release,
            'uncertain': scheduler.ResourceLeases.mark_uncertain,
        }
        self.calls = list(attempt.owner.selected_calls)
        self.codes = tuple(attempt.owner.selected_codes)
        self.original_kind = scheduler.ResourceLeases
        self.profile_kind = profiles.ComputeProfile
        self.thread_kind = threading.Thread
        self.event_kind = threading.Event

    def __copy__(self):
        self.fail('copy'); raise ValueError('Selected-compute observations cannot be copied')
    def __deepcopy__(self, memo): return self.__copy__()
    def __reduce_ex__(self, protocol): return self.__copy__()

    def fail(self, reason):
        self.failed = True
        # A failed resource cannot be redeemed by later journal/cleanup state.
        from backend.engine.original_owned_flow_request import _FlowAttempt
        _FlowAttempt.fail(self.attempt, 'selected_compute_'+reason)

    def source_original(self):
        attempt = self.attempt
        return (not self.failed and self.pid == os.getpid() and attempt.selected is self
            and self.binding == (self, attempt, attempt.owner, attempt.activity, attempt.token)
            and attempt.owner.pending.get(attempt.activity.token) is attempt and not attempt.settled
            and self.request_binding==(attempt.request,attempt.req,attempt.req.pipeline,attempt.req.compute_profile_id,
                attempt.req.device,attempt.req.project_id,attempt.req.image_id,attempt.req.stop_node_id)
            and self.request_binding[0] is attempt.request and self.request_binding[1] is attempt.req
            and self.request_binding[2] is attempt.req.pipeline and attempt.req.pipeline.model_dump()==self.pipeline_value
            and all(getattr(owner, name) is function for owner, name, function in self.calls)
            and all(function.__code__ is code for function, code in self.codes)
            and _bound(self, SelectedComputeLifetime, self.calls)
            and (self.transport is None or _bound(self.transport, self.modules[-1].SSHTransport, self.calls))
            and self.thread_bindings_original()
            and self.modules[5]._confirm_owned_exit is self.modules[6]._confirm_owned_exit
            and self.modules[5]._bundle_backend is self.modules[6]._bundle_backend
            and all(_bound(store, self.original_kind, self.calls) for store in self.stores))

    def thread_bindings_original(self):
        if self.thread is None: return self.stop is None
        if self.target is None or self.target.__code__ is not self.target_code: return False
        closure = dict(zip(self.target.__code__.co_freevars,
            (cell.cell_contents for cell in self.target.__closure__ or ())))
        return (_bound(self.thread, self.thread_kind, self.calls) and _bound(self.stop, self.event_kind, self.calls)
            and all(getattr(self.thread, name) == method for name, method in self.thread_methods)
            and all(getattr(self.stop, name) == method for name, method in self.event_methods)
            and closure == {'lease_key':self.lease_key, 'leases':self.leases, 'stop':self.stop})

    def worker(self):
        return (self.source_original() and self.attempt.worker_original() and self.attempt.worker is not None
            and threading.current_thread() is self.attempt.worker[1])

    def body(self, kind): return self.functions[kind].__wrapped__

    def monitor_frame(self): return _frame(self.body('monitor'))

    def enter(self, kind, function, args, kwargs):
        valid = (self.source_original() and function is self.body(kind)
            and (self.heartbeat_original() if kind == 'heartbeat' else self.worker()))
        frame = self.attempt.handler_frame() if kind in {'profile', 'run'} else self.monitor_frame()
        token = object()
        if kind == 'profile':
            from backend.remote import profiles
            store = args[0] if args else None
            valid = (valid and self.profile is None and frame is not None and len(args) == 2 and not kwargs
                and type(store) is profiles.ProfileStore and args[1] == self.attempt.req.compute_profile_id
                and frame.f_locals.get('req') is self.attempt.req and _current_profile_path(store)
                and not profiles._STORE_LOCK._is_owned() and _bound(store, profiles.ProfileStore, self.calls))
            self.profile_store = store
        elif kind == 'run':
            project = self.attempt.scope.get('state', {}).get('scoped_project')
            valid = (valid and frame is not None and len(args) == 6 and args[0] is self.profile
                and args[1] is project and frame.f_locals.get('profile') is self.profile
                and args[2] == self.attempt.req.pipeline.model_dump()
                and args[3] is frame.f_locals.get('verified_checkpoints')
                and Path(args[4]) == Path(self.attempt.req.image_path) and args[5] == self.attempt.req.image_id
                and kwargs.get('device') == self.attempt.req.device
                and set(kwargs) <= {'device', 'required_passive_capture_grant'}
                and len(self.specialists) == 1 and len(self.sources) == 1
                and self.selected_models(args[3]))
            if valid:
                self.verified_checkpoints = args[3]
                self.verified_binding = tuple(sorted((key,str(path)) for key,path in args[3].items()))
        elif kind == 'monitor':
            parent = _frame(self.body('run'))
            context = args[0] if args else None
            valid = (valid and parent is not None and len(args) == 3 and args[1] == 'flowchart_run'
                and args[0] is parent.f_locals.get('context') and args[2].get('portable_models') is True
                and args[2].get('pipeline') == self.attempt.req.pipeline.model_dump()
                and kwargs.get('transport') is None and not kwargs.get('force_new', False)
                and kwargs.get('cancelled') is None and len(self.sources) == 3)
            self.context = context
            self.operation_spec = args[2] if len(args) > 2 else None
        elif kind == 'worker':
            valid = (valid and frame is not None and self.thread_started and self.acquired
                and len(args) == 3 and args[0] is self.context and args[1] == 'flowchart_run'
                and args[2] is frame.f_locals.get('extra_spec')
                and kwargs.get('transport') is None and not kwargs.get('force_new', False)
                and kwargs.get('cancelled') is None)
        elif kind == 'confirm':
            parent = _frame(self.body('worker'))
            from backend.remote.ssh_transport import SSHTransport
            valid = (valid and parent is not None and len(args) == 4 and not kwargs
                and type(args[0]) is SSHTransport and _bound(args[0], SSHTransport, self.calls)
                and args[0] is parent.f_locals.get('transport') and args[1] is self.profile
                and args[2] == parent.f_locals.get('op_id')
                and args[3] == parent.f_locals.get('journal', {}).get('remote_handle')
                and type(args[3]) is str and bool(args[3]) and not self.confirmed)
            if valid: self.transport = args[0]
        elif kind in {'acquire', 'release', 'uncertain', 'heartbeat'}:
            store = args[0] if args else None
            valid = (valid and type(store) is self.original_kind and _bound(store, self.original_kind, self.calls)
                and len(args) >= 2 and store.owner == args[1] == self.lease_key)
            if kind == 'acquire':
                valid = (valid and frame is not None and not self.acquired and frame.f_locals.get('leases') is store
                    and kwargs.get('remote') is True and kwargs.get('task') == 'flowchart_run')
                self.leases = store
            else: valid = valid and store is self.leases
            if kind == 'release':
                valid = (valid and self.confirmed and self.heartbeat_exited and self.thread is not None
                    and not self.thread.is_alive() and kwargs == {'terminal': True})
            elif kind == 'uncertain': valid = False
        if not valid: self.fail(kind+'_entry')
        self.live[token] = kind
        return token, valid

    def returned(self, kind, function, args, kwargs, result, ticket):
        token, valid = ticket
        valid = valid and self.source_original() and self.live.get(token) == kind
        if kind == 'profile':
            from backend.remote import profiles
            valid = (valid and type(result) is self.profile_kind and result.id == self.attempt.req.compute_profile_id
                and result.runtime_kind == 'docker' and result.distributed_processes == 1
                and not profiles._STORE_LOCK._is_owned() and self.files_closed('profile'))
            if valid: self.profile = result
        elif kind == 'acquire':
            valid = valid and result is True and self.database_live == 0
            if valid: self.acquired = True
        elif kind == 'heartbeat': valid = valid and result is True and self.database_live == 0
        elif kind == 'confirm':
            valid = valid and result is None
            if valid: self.confirmed = True
        elif kind == 'release':
            valid = valid and result is True and self.database_live == 0
            if valid: self.released = True
        elif kind == 'worker':
            valid = valid and self.confirmed and type(result) is dict
        elif kind == 'monitor':
            valid = (valid and self.confirmed and self.released and self.descriptor_closed
                and self.heartbeat_exited and not self.thread.is_alive() and self.database_live == 0
                and self.maintenance_live == 0 and self.files_closed() and not self.pins)
        elif kind == 'run':
            valid = (valid and self.confirmed and self.released and self.descriptor_closed
                and 'monitor' in self.completed and len(self.sources) == 3 and self.files_closed()
                and type(result) is dict and result.get('execution_target') == 'selected_compute'
                and result.get('execution_device') == self.attempt.req.device
                and result.get('compute_profile_id') == self.profile.id)
        if not valid: self.fail(kind+'_return')
        else: self.completed.append(kind)
        self.live.pop(token, None)
        if kind == 'monitor' and valid:
            with _LOCK:
                if _THREADS.get(self.thread) is not self: self.fail('heartbeat_registry_exit')
                else: del _THREADS[self.thread]

    def resolver_entry(self, function, args, kwargs, kind):
        from backend.api import routes_flowchart as flows
        valid = self.worker() and self.attempt.handler_frame() is not None
        if kind == 'specialist':
            valid = (valid and function is flows._resolve_project_specialist.__wrapped__ and self.specialist_live is None
                and len(args) == 5 and args[4] is self.attempt.request
                and set(kwargs) == {'node'} and args[0] is self.attempt.scope['state']['scoped_project']
                and any(kwargs['node'] is node for node in self.attempt.req.pipeline.nodes) and args[2] in {'rotation', 'segmentation'})
            self.specialist_live = args, kwargs
        else:
            frame = _frame(flows._registered_retained_source.__wrapped__)
            # The wrapper calls before entering the source function. Require
            # its original resolver caller, not a constructed Request carrier.
            from backend.engine import retained_preprocessors as retained
            caller = _frame(retained.resolve_retained_rotation)
            value = caller.f_locals.get('value') if caller is not None else None
            valid = (valid and function is flows._registered_retained_source.__wrapped__ and self.source_live is None
                and len(args) == 2 and not kwargs and args[0] is self.attempt.request
                and caller is not None and type(value) is dict and args[1] == value.get('source_project_id')
                and caller.f_locals.get('project') is self.attempt.scope['state']['scoped_project']
                and value.get('task') == 'rotation' and value.get('operation') == 'learned_rotation'
                and all(value.get(name) is False for name in ('model_quality_approved', 'release_approval', 'permission_authority'))
                and (self.specialist_live is not None or _frame(self.body('run')) is not None))
            self.source_live = {'source_id':args[1] if len(args)>1 else None,
                'database_start':len(self.attempt.auth.databases), 'value':value}
        if not valid: self.fail(kind+'_source')
        return valid

    def resolver_return(self, function, args, kwargs, kind, result, valid):
        if kind == 'specialist':
            good = (valid and self.worker() and self.specialist_live == (args, kwargs)
                and type(result) is tuple and len(result) == 2)
            if good: self.specialists.append((args[1], args[2], result, kwargs['node']))
            self.specialist_live = None
        else:
            row = self.source_live
            good = (valid and self.worker() and row is not None and type(result) is dict
                and result.get('id') == row['source_id'] and result.get('id') != self.attempt.auth.project_id
                and self.attempt.auth.databases[row['database_start']:] == ['authorize', 'project_role', 'project_for']
                and not self.attempt.auth.db_live
                and result.get('_retained_manifest_pin') == row['value'].get('pins', {}).get('source_project'))
            if good: self.sources.append(row)
            self.source_live = None
        if not good: self.fail(kind+'_result')

    def source_database_entry(self, store):
        from backend.api import routes_flowchart as flows
        row = self.source_live
        names = ('authorize', 'project_role', 'project_for')
        index = len(self.attempt.auth.databases) - row['database_start'] if row is not None else -1
        name = names[index] if 0 <= index < 3 else None
        function = getattr(type(store), name, None) if name is not None else None
        function = getattr(function, '__wrapped__', function)
        frame = _frame(function) if function is not None else None
        source = _frame(flows._registered_retained_source.__wrapped__)
        valid = (self.worker() and row is not None and self.attempt.auth.original()
            and store is self.attempt.owner.accounts.store and not self.attempt.auth.db_live
            and source is not None and source.f_locals.get('request') is self.attempt.request
            and source.f_locals.get('source_project_id') == row['source_id']
            and source.f_locals.get('binding_target') is None and frame is not None
            and frame.f_locals.get('self') is store)
        if name == 'authorize':
            valid = (valid and frame.f_locals.get('actor') == self.attempt.auth.account.get('id')
                and frame.f_locals.get('project') == row['source_id']
                and frame.f_locals.get('action') == 'flow.execute'
                and frame.f_locals.get('session_token') == self.attempt.auth.token)
        elif name in {'project_role', 'project_for'}:
            valid = (valid and frame.f_locals.get('user_id') == self.attempt.auth.account.get('id')
                and frame.f_locals.get('project_id') == row['source_id'])
        if not valid: self.fail('source_database_entry')
        self.attempt.auth.db_live = True
        return valid, name

    def database_entry(self, store, manager):
        from backend.engine import shared_scheduler as scheduler
        frame = self.monitor_frame()
        thread = threading.current_thread() is self.thread
        valid = (self.source_original() and type(store) is self.original_kind
            and _bound(store, self.original_kind, self.calls)
            and (self.heartbeat_original() and store is self.leases if thread else self.worker() and frame is not None))
        if not any(value is store for value in self.stores):
            constructor = _frame(scheduler.ResourceLeases.__init__)
            valid = valid and constructor is not None and constructor.f_locals.get('self') is store
            self.stores.append(store)
        self.database_live += 1
        if not valid: self.fail('lease_database_entry')
        return valid

    def record_file(self, handle, path, role):
        from backend.remote import profiles, coordinator
        valid = self.worker() and isinstance(handle, io.IOBase) and not handle.closed and len(self.files) < 1024
        if role == 'profile':
            frame = _frame(profiles.ProfileStore._read)
            valid = (valid and frame is not None and frame.f_locals.get('self') is self.profile_store
                and frame.f_locals.get('handle') is handle and Path(path) == self.profile_store.path
                and profiles._STORE_LOCK._is_owned() and 'profile' in self.live.values())
        elif role == 'checksum':
            frame = _frame(coordinator._sha256)
            valid = (valid and frame is not None and frame.f_locals.get('handle') is handle
                and frame.f_locals.get('path') is path and 'run' in self.live.values())
        elif role == 'operation_text':
            read = _frame(selected_read_text)
            caller = read.f_back if read is not None else None
            frames = (_frame(self.body('run')), self.monitor_frame())
            valid = (valid and caller is not None and any(caller is frame
                and any(frame.f_locals.get(name) is path
                    for name in ('metadata', 'result_file', 'journal_path'))
                for frame in frames if frame is not None))
        else: valid = False
        if not valid:
            self.fail('read_handle_source'); return
        path = Path(path)
        fd = os.fstat(handle.fileno()); named = path.lstat()
        if path.is_symlink() or (fd.st_dev, fd.st_ino) != (named.st_dev, named.st_ino) or not stat.S_ISREG(fd.st_mode):
            self.fail('read_descriptor'); return
        self.files.append((handle, path, role))

    def files_closed(self, role=None):
        rows = [row for row in self.files if role is None or row[2] == role]
        return (bool(rows) if role is not None else True) and all(handle.closed for handle, _, _ in rows)

    def pin_open(self, descriptor, path):
        from backend.engine import retained_preprocessors as retained
        frame = _frame(retained._pin)
        if (not self.worker() or frame is None or frame.f_locals.get('fd') != descriptor
                or Path(frame.f_locals.get('path')) != Path(path) or descriptor in self.pins):
            self.fail('retained_pin_open'); return
        self.pins[descriptor] = Path(path)

    def pin_closed(self, descriptor):
        from backend.engine import retained_preprocessors as retained
        frame = _frame(retained._pin)
        if not self.worker() or frame is None or frame.f_locals.get('fd') != descriptor or descriptor not in self.pins:
            self.fail('retained_pin_close'); return
        del self.pins[descriptor]; self.pin_done += 1

    def operation_fd(self, descriptor, path):
        from backend.remote import operations
        frame = _frame(operations._open_operation_lock)
        monitor = self.monitor_frame()
        if (not self.worker() or frame is None or monitor is None or self.descriptor is not None
                or frame.f_locals.get('descriptor') != descriptor or Path(frame.f_locals.get('path')) != Path(path)):
            self.fail('operation_fd_source'); return
        info = os.fstat(descriptor); named = Path(path).lstat()
        if (Path(path).is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
            self.fail('operation_fd_identity'); return
        self.descriptor, self.descriptor_path = descriptor, Path(path)
        self.journal_path = monitor.f_locals.get('journal_path')
        self.lease_key = monitor.f_locals.get('lease_key')
        if self.journal_path is None or self.descriptor_path != self.journal_path.with_suffix('.lock'):
            self.fail('operation_fd_path')

    def operation_closed(self, descriptor):
        frame = self.monitor_frame()
        if (not self.worker() or frame is None or frame.f_locals.get('descriptor') != descriptor
                or descriptor != self.descriptor or not self.confirmed or not self.released
                or not self.heartbeat_exited or self.thread.is_alive()):
            self.fail('operation_fd_close'); return
        self.descriptor_closed = True

    def thread_born(self, thread, target, stop, leases, key):
        frame = self.monitor_frame()
        monitor_code = self.body('monitor').__code__
        codes = tuple(code for code in monitor_code.co_consts if type(code) is type(monitor_code) and code.co_name == 'heartbeat')
        closure = dict(zip(target.__code__.co_freevars, (cell.cell_contents for cell in target.__closure__ or ())))
        valid = (self.worker() and frame is not None and self.thread is None and type(thread) is self.thread_kind
            and type(stop) is self.event_kind and type(target) is FunctionType and len(codes) == 1
            and target.__code__ is codes[0] and closure == {'lease_key':key, 'leases':leases, 'stop':stop}
            and thread._target is target and thread._args == () and thread._kwargs == {} and thread.ident is None
            and thread.name == key and thread.daemon is True and leases is self.leases and key == self.lease_key
            and frame.f_locals.get('thread') is thread and frame.f_locals.get('heartbeat') is target
            and frame.f_locals.get('stop') is stop and frame.f_locals.get('leases') is leases
            and _bound(thread, self.thread_kind, self.calls) and _bound(stop, self.event_kind, self.calls))
        if not valid: self.fail('heartbeat_birth_source')
        self.thread, self.target, self.stop = thread, target, stop
        self.target_code = target.__code__
        self.thread_methods = tuple((name, getattr(thread, name)) for owner, name, _ in self.calls if owner is self.thread_kind)
        self.event_methods = tuple((name, getattr(stop, name)) for owner, name, _ in self.calls if owner is self.event_kind)
        with _LOCK:
            if thread in _THREADS: self.fail('heartbeat_birth_repeated')
            _THREADS[thread] = self
        self.thread_started = True

    def heartbeat_original(self):
        current = threading.current_thread()
        frame = _frame(self.target) if self.target is not None else None
        return (self.source_original() and current is self.thread and _THREADS.get(current) is self
            and frame is not None and frame.f_locals.get('leases') is self.leases
            and frame.f_locals.get('lease_key') == self.lease_key and frame.f_locals.get('stop') is self.stop
            and _bound(current, self.thread_kind, self.calls) and _bound(self.stop, self.event_kind, self.calls)
            and all(getattr(current, name) == method for name, method in self.thread_methods)
            and all(getattr(self.stop, name) == method for name, method in self.event_methods))

    def selected_models(self, checkpoints):
        """The one original retained rotation and generic segmentation pair."""
        nodes = self.attempt.req.pipeline.nodes
        rotations = [node for node in nodes if node.data.node_type == 'preprocess'
            and node.data.params.get('operation') == 'learned_rotation']
        segments = [node for node in nodes if node.data.node_type == 'inspection'
            and node.data.task == 'segmentation']
        if (len(rotations) != 1 or len(segments) != 1 or len(self.specialists) != 1
                or not self.sources or type(checkpoints) is not dict): return False
        rotation, segment = rotations[0], segments[0]
        rotation_key = rotation.data.model_job_id, 'rotation'
        segment_key = segment.data.model_job_id, 'segmentation'
        row = self.specialists[0]
        project = self.attempt.scope.get('state', {}).get('scoped_project')
        source_pin = self.sources[0]['value'].get('pins', {}).get('checkpoint')
        return (set(checkpoints) == {rotation_key, segment_key}
            and row[:2] == rotation_key and len(row) == 4 and row[3] is rotation
            and type(row[2]) is tuple and len(row[2]) == 2
            and Path(row[2][0]) == checkpoints[rotation_key]
            and type(source_pin) is dict and Path(source_pin['path']) == checkpoints[rotation_key]
            and type(project) is dict
            and checkpoints[segment_key] == Path(project['models_dir']) / segment_key[0] / 'best_model.pt')

    def complete(self):
        return (self.source_original() and not self.failed and not self.live and self.profile is not None
            and self.completed.count('run') == self.completed.count('monitor') == self.completed.count('worker') == 1
            and self.confirmed and self.acquired and self.released and self.descriptor_closed
            and self.heartbeat_exited and not self.heartbeat_live and self.thread is not None
            and not self.thread.is_alive() and self.database_live == self.maintenance_live == 0
            and self.database_done >= 4 and self.files_closed() and not self.pins and self.pin_done > 0
            and len(self.specialists) == 1 and len(self.sources) == 3 and self.source_live is None
            and self.selected_models(self.verified_checkpoints)
            and tuple(sorted((key,str(path)) for key,path in self.verified_checkpoints.items())) == self.verified_binding
            and self.specialist_live is None)
