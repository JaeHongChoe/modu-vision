"""Early private-descriptor binding for an owned POSIX application backend.

This verifies controller transport and the committed application/database pair.
It grants no packaged-native, inference, or release acceptance. The original
controller remains responsible for durable launch ownership and recovery.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import select
import socket
import sys
import threading
import time
import weakref
from contextlib import ExitStack, contextmanager

MAX_FRAME = 65536
MAX_DEADLINE = 210
_CONTEXT_NAMES = ('VISION_AI_STUDIO_USER_DATA_DIR', 'VISION_APPLICATION_LAUNCH_NONCE',
    'VISION_APPLICATION_GENERATION', 'VISION_APPLICATION_DATABASE_GENERATION',
    'VISION_APPLICATION_BACKEND_FD', 'VISION_APPLICATION_LAUNCH_FD')
_CHALLENGE_FIELDS = {'schema_version', 'kind', 'challenge', 'epoch', 'nonce', 'binding',
    'main_process', 'backend_pid', 'backend_executable', 'backend_build_identity_sha256'}
_CACHE = None
_FIXED_CPU_PRODUCER = object()
_PREFLIGHT_TICKETS = weakref.WeakSet()
_PREFLIGHT_UNRESOLVED = set()


class BackendWorkAdmission:
    """Only the reviewed foreground scopes; no background/tree exit authority."""
    def __init__(self, *, activity_observer=None):
        self._condition = threading.Condition()
        self._closed = False
        self._active = 0
        self._unsupported = set()
        self._unsupported_origin_observation_gap = False  # Diagnostic only, sticky.
        self._startup_joins = {}
        self._startup_join_preparing = False
        self._startup_pending = {}
        self._activity_observer = activity_observer
        self._activity_sequence = 0
        self._current_project_requests = None
        self._desktop_denials = None
        self._shared_denials = None
        self._account_requests = None
        self._flow_requests = None
        self._flow_execution_requests = None
        self._UI_ancillary_requests = None
        self._telemetry_socket_requests = None
        if activity_observer is not None:
            from backend.engine.original_request_activity import OriginalRequestActivityObserver, ActivityObservationError
            if type(activity_observer) is not OriginalRequestActivityObserver:
                raise ActivityObservationError('An original typed activity observer is required')
            activity_observer.bind(self)

    def _activity_transition(self, action, *args):
        self._activity_sequence += 1
        observer = self._activity_observer
        if observer is not None:
            try: observer.event(self, self._activity_sequence, action, *args)
            except BaseException:
                try: observer.poison('observer_recording_failure')
                except BaseException: pass

    def bind_activity_middleware(self, middleware, downstream):
        with self._condition:
            if self._activity_observer is not None:
                try: self._activity_observer.bind_middleware(self, middleware, downstream)
                except BaseException: self._activity_observer.poison('middleware_binding_failure')

    def request_activity(self, middleware, downstream, scope):
        with self._condition:
            if self._activity_observer is None: return None
            try: return self._activity_observer.capture(self, middleware, downstream, scope)
            except BaseException:
                self._activity_observer.poison('request_capture_failure'); return None

    def observe_activity(self, action, activity, *args):
        with self._condition: self._activity_transition(action, activity, *args)

    def finish_rejected_activity(self, activity, *, normal_return):
        with self._condition: self._activity_transition('rejected_leave', activity, normal_return)

    def register_startup_join(self, role, callback):
        if role not in {'job_ledger_maintenance', 'preflight_run_sweep', 'telemetry_broadcaster'} or not callable(callback):
            raise HandshakeError('Unknown original startup thread join adapter')
        with self._condition:
            if self._closed or self._startup_join_preparing or role in self._startup_joins:
                raise HandshakeError('Original startup join registration is closed or repeated')
            self._startup_joins[role] = callback

    @contextmanager
    def _startup_condition(self, absolute_deadline, original_condition):
        if self._condition is not original_condition:
            raise HandshakeError('Original startup condition changed')
        remaining = absolute_deadline - time.monotonic()
        if remaining <= 0 or not original_condition.acquire(timeout=remaining):
            raise HandshakeError('Original startup condition deadline expired')
        try:
            if self._condition is not original_condition:
                raise HandshakeError('Original startup condition changed')
            if time.monotonic() >= absolute_deadline:
                raise HandshakeError('Original startup condition deadline expired')
            yield
        finally:
            original_condition.release()

    def prepare_startup_threads(self, absolute_deadline):
        if (type(absolute_deadline) not in (int, float) or not math.isfinite(absolute_deadline)
                or time.monotonic() >= absolute_deadline):
            raise HandshakeError('Original startup preparation deadline expired')
        original_condition = self._condition
        with self._startup_condition(absolute_deadline, original_condition):
            if self._startup_join_preparing:
                raise HandshakeError('Original startup preparation cannot replay')
            self._startup_join_preparing = True
            callbacks = tuple(self._startup_joins.items())
        receipts = []
        for role, callback in callbacks:
            if time.monotonic() >= absolute_deadline:
                raise HandshakeError('Original startup preparation deadline expired')
            receipts.append(callback(absolute_deadline))
            with self._startup_condition(absolute_deadline, original_condition):
                if self._startup_joins.get(role) is not callback:
                    raise HandshakeError('Original startup join adapter changed')
        if time.monotonic() >= absolute_deadline:
            raise HandshakeError('Original startup preparation completed late')
        # These original local joins do not cover recovery, detached training,
        # a whole writer tree or lease release. Unsupported status stays sticky.
        return receipts

    def begin_original_startup(self, observed):
        from backend.engine.original_fresh_startup import _claim_pending
        with self._condition:
            if (self._closed or self._active or self._unsupported or self._activity_sequence
                    or self._startup_join_preparing or self._startup_pending):
                raise HandshakeError('Original startup lifetime must precede other admitted activity')
            token = _claim_pending(observed, self)
            self._startup_pending[token] = observed
            # Typed startup bookkeeping is distinct from anonymous producers.
            self._active += 1

    def finish_original_startup(self, observed, absolute_deadline, reader, receipts):
        from backend.engine.original_fresh_startup import _pending_token, _mark_settled, _settle_original
        condition = self._condition
        with self._startup_condition(absolute_deadline, condition):
            token = _pending_token(observed, self)
            if token is None or self._startup_pending.get(token) is not observed:
                raise HandshakeError('Original pending startup lifetime is missing or replayed')
        receipt = _settle_original(observed, absolute_deadline, reader, receipts)
        with self._startup_condition(absolute_deadline, condition):
            if self._startup_pending.get(token) is not observed or self._active < 1 or self._closed:
                raise HandshakeError('Original startup lifetime changed before settlement')
            if receipt['supported'] is not True: self._unsupported.add('startup_background_protocols')
            del self._startup_pending[token]
            self._active -= 1
            _mark_settled(observed)
            self._condition.notify_all()
        return receipt

    def enter(self, scope, *, activity=None):
        with self._condition:
            if self._closed:
                self._activity_transition('enter', scope, activity, False)
                return False
            self._active += 1
            self._activity_transition('enter', scope, activity, True)
            named = self._current_project_requests
            denials = self._desktop_denials
            covered = denials is not None and denials.begin(activity)
            shared = self._shared_denials
            if not covered: covered = shared is not None and shared.begin(activity)
            accounts = self._account_requests
            if not covered: covered = accounts is not None and accounts.begin(activity)
            flows = self._flow_requests
            if not covered: covered = flows is not None and flows.begin(activity)
            executions = self._flow_execution_requests
            if not covered: covered = executions is not None and executions.begin(activity)
            ancillary = self._UI_ancillary_requests
            if not covered: covered = ancillary is not None and ancillary.begin(activity)
            sockets = self._telemetry_socket_requests
            if not covered: covered = sockets is not None and sockets.begin(activity)
            if not covered: covered = named is not None and named.begin(activity)
            # Even GET can create projects or recover jobs. Uncovered requests
            # still work normally, but cannot qualify a clean managed drain.
            if not covered and not (scope.get('type') == 'http' and scope.get('path') == '/health'):
                self._unsupported.add('uncovered_request_protocol')
                self._unsupported_origin_observation_gap = True
                if self._activity_observer is not None:
                    try:self._activity_observer.observe_unsupported_origin(self,'DIRECT_ENTER',activity)
                    except Exception:pass  # Optional data cannot change the original admission.
            return True

    def leave(self, *, activity=None, normal_return=False):
        with self._condition:
            named = self._current_project_requests
            denials = self._desktop_denials
            shared = self._shared_denials
            accounts = self._account_requests
            flows = self._flow_requests
            executions = self._flow_execution_requests
            ancillary = self._UI_ancillary_requests
            sockets = self._telemetry_socket_requests
            if self._active <= (len(self._startup_pending) + (len(named.pending) if named is not None else 0)
                    + (len(denials.pending) if denials is not None else 0)
                    + (len(shared.pending) if shared is not None else 0)
                    + (len(accounts.pending) if accounts is not None else 0)
                    + (len(flows.pending) if flows is not None else 0)
                    + (len(executions.pending) if executions is not None else 0)
                    + (len(ancillary.pending) if ancillary is not None else 0)
                    + (len(sockets.pending) if sockets is not None else 0)):
                self._activity_transition('unmatched_leave')
                raise HandshakeError('Accepted scope lifetime is unbalanced')
            self._activity_transition('leave', activity, normal_return)
            self._active -= 1
            if named is not None: named.finish(activity)
            if denials is not None: denials.finish(activity)
            if shared is not None: shared.finish(activity)
            if accounts is not None: accounts.finish(activity)
            if flows is not None: flows.finish(activity)
            if executions is not None: executions.finish(activity)
            if ancillary is not None: ancillary.finish(activity)
            if sockets is not None: sockets.finish(activity)
            observer=self._activity_observer
            if observer is not None:
                try:observer.original_request_settled(self,activity)
                except BaseException:observer.poison('settled_history_recording_failure')
            self._condition.notify_all()

    def response(self, status):
        if status == 202: self.uncovered('accepted_background_work')

    def uncovered(self, reason):
        if reason not in {'uncovered_request_protocol', 'accepted_background_work', 'startup_background_protocols', 'cpu_producer_unconfirmed'}:
            raise HandshakeError('Unknown uncovered writer protocol')
        with self._condition:
            self._unsupported.add(reason)
            self._unsupported_origin_observation_gap = True
            if self._activity_observer is not None:
                try:self._activity_observer.observe_unsupported_origin(self,'OTHER_UNSUPPORTED')
                except Exception:pass

    @contextmanager
    def producer(self):
        self._begin_producer()
        try: yield
        except BaseException:
            self.uncovered('cpu_producer_unconfirmed')
            raise
        finally: self.leave()

    def _begin_producer(self):
        with self._condition:
            if self._closed: raise HandshakeError('Original backend producer admission is closed')
            self._active += 1
            self._activity_transition('producer')

    def close(self):
        with self._condition:
            was_closed = self._closed
            self._closed = True
            self._activity_transition('close', was_closed)

    def snapshot(self):
        with self._condition:
            return {'active_scopes': self._active, 'unsupported': sorted(self._unsupported)}

    def drain(self, seconds):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 4:
            raise HandshakeError('Managed drain budget must be positive and at most four seconds')
        deadline = time.monotonic() + seconds
        with self._condition:
            if not self._closed: raise HandshakeError('Managed admission must close before draining')
            if self._current_project_requests is not None:
                named = self._current_project_requests
                named.deadline = deadline if named.deadline is None else min(named.deadline, deadline)
            if self._desktop_denials is not None:
                denials = self._desktop_denials
                denials.deadline = deadline if denials.deadline is None else min(denials.deadline, deadline)
            if self._shared_denials is not None:
                shared = self._shared_denials
                shared.deadline = deadline if shared.deadline is None else min(shared.deadline, deadline)
            if self._account_requests is not None:
                accounts = self._account_requests
                accounts.deadline = deadline if accounts.deadline is None else min(accounts.deadline, deadline)
            if self._flow_requests is not None:
                flows = self._flow_requests
                flows.deadline = deadline if flows.deadline is None else min(flows.deadline, deadline)
            if self._flow_execution_requests is not None:
                executions = self._flow_execution_requests
                executions.deadline = deadline if executions.deadline is None else min(executions.deadline, deadline)
            if self._UI_ancillary_requests is not None:
                ancillary = self._UI_ancillary_requests
                ancillary.deadline = deadline if ancillary.deadline is None else min(ancillary.deadline, deadline)
            if self._telemetry_socket_requests is not None:
                sockets = self._telemetry_socket_requests
                sockets.deadline = deadline if sockets.deadline is None else min(sockets.deadline, deadline)
            while self._active and not self._unsupported:
                remaining = deadline - time.monotonic()
                if remaining <= 0: break
                self._condition.wait(remaining)
            return {'status': 'refused' if self._active or self._unsupported else 'managed_scopes_drained',
                'active_scopes': self._active, 'unsupported': sorted(self._unsupported)}


def backend_work_admission():
    return _CACHE.get('admission') if _CACHE is not None else None


class _PreflightWriterTicket:
    """One original admitted lifetime, not reconstructable launch authority.

    No contextmanager or maintenance ContextVar crosses threads. The original
    request validates OPEN once; its private OFD duplicate and producer count
    remain until the exact dispatched thread completes its cleanup. The ticket
    grants no enrollment, process-tree exit, or release authority.
    """
    def __init__(self):
        raise HandshakeError('An original preflight ticket must be minted')

    def _original(self):
        if (self not in _PREFLIGHT_TICKETS or os.getpid() != self._pid
                or _CACHE is not self._cache or _context() != self._context):
            raise HandshakeError('Original preflight ticket context is unavailable')

    def claim(self):
        with self._lock:
            self._original()
            if self._phase not in {'created', 'dispatched'}:
                raise HandshakeError('Preflight ticket is already used or finished')
            if threading.current_thread() is not self._thread:
                raise HandshakeError('Original preflight ticket thread differs')
            self._phase = 'active'

    def dispatch_to(self, thread):
        with self._lock:
            self._original()
            if (threading.current_thread() is not self._creator or self._phase != 'created'
                    or not isinstance(thread, threading.Thread) or thread.ident is not None):
                raise HandshakeError('Preflight ticket requires one original new thread')
            if self._admission is not None: self._admission.uncovered('accepted_background_work')
            self._thread, self._phase = thread, 'dispatched'

    @contextmanager
    def transport(self, *, device):
        with self._lock:
            self._original()
            if self._phase != 'active' or threading.current_thread() is not self._thread:
                raise HandshakeError('Original preflight ticket is inactive or belongs to another thread')
            if type(device) is not str or device != self._device:
                raise HandshakeError('Original preflight ticket device differs')
            try: private = tuple(os.dup(fd) for fd in self._transport)
            except BaseException:
                if self._admission is not None: self._admission.uncovered('cpu_producer_unconfirmed')
                raise
            self._using_transport += 1
        try: yield private
        except BaseException:
            self.unconfirmed()
            raise
        finally:
            try:
                for fd in private: os.close(fd)
            except BaseException:
                # close may have consumed the descriptor before raising. Never
                # retry it or release this in-flight lifetime on that ambiguity.
                self.retain_unconfirmed()
                raise
            else:
                with self._lock: self._using_transport -= 1

    def unconfirmed(self):
        with self._lock:
            self._original()
            if threading.current_thread() not in (self._creator, self._thread):
                raise HandshakeError('Original preflight ticket cleanup thread differs')
            if self._admission is not None: self._admission.uncovered('cpu_producer_unconfirmed')

    def retain_unconfirmed(self):
        """Unknown cleanup retains custody; no observation repairs this ticket."""
        with self._lock:
            self._original()
            if (threading.current_thread() not in (self._creator, self._thread)
                    or self._phase == 'finished'):
                raise HandshakeError('Original preflight ticket cleanup differs')
            self._retain_locked()

    def _retain_locked(self):
        if self._admission is not None: self._admission.uncovered('cpu_producer_unconfirmed')
        self._phase = 'unresolved'
        # Keep original custody/count after any uncertain close or final write.
        # The descriptor is never inspected, retried, or exposed again.
        _PREFLIGHT_UNRESOLVED.add(self)

    def _release(self, before_leave):
        self._phase = 'settling'
        try:
            for fd in self._transport: os.close(fd)  # Never LOCK_UN the inherited OFD.
            # Reservation release and final state publication remain counted,
            # and only happen after every original ticket close returned.
            if before_leave is not None: before_leave()
            if self._admission is not None: self._admission.leave()
        except BaseException:
            self._retain_locked()
            raise
        self._phase = 'finished'

    def finish(self, *, before_leave=None):
        with self._lock:
            self._original()
            if (self._phase not in {'created', 'active', 'cancelled'} or self._using_transport
                    or threading.current_thread() is not self._thread):
                raise HandshakeError('Original preflight ticket is inactive or belongs to another thread')
            self._release(before_leave)

    def cancel_dispatch(self):
        """Abort before claim, or retain custody after ambiguous Thread.start."""
        with self._lock:
            self._original()
            if threading.current_thread() is not self._creator:
                raise HandshakeError('Only the original dispatcher can cancel its ticket')
            if self._admission is not None: self._admission.uncovered('cpu_producer_unconfirmed')
            if self._phase == 'dispatched':
                self._phase, self._thread = 'cancelled', self._creator
                return True  # Retain custody through the dispatcher's cleanup.
            if self._phase in {'active', 'finished'}:
                return False  # Never close a capability already held by work.
            raise HandshakeError('Original preflight ticket has not been dispatched')


def _mint_preflight_ticket(device, transport, admission, *, unresolved=False):
    ticket = object.__new__(_PreflightWriterTicket)
    ticket._pid, ticket._cache, ticket._context = os.getpid(), _CACHE, _context()
    ticket._creator = ticket._thread = threading.current_thread()
    ticket._device, ticket._transport, ticket._admission = device, transport, admission
    ticket._phase, ticket._lock, ticket._using_transport = ('unresolved' if unresolved else 'created'), threading.Lock(), 0
    _PREFLIGHT_TICKETS.add(ticket)
    if unresolved: _PREFLIGHT_UNRESOLVED.add(ticket)
    return ticket


def create_preflight_writer_ticket(*, device):
    """Acquire before planning/writes; retain the original capability on dispatch.

    Device selection stays the preflight's own explicit CPU/CUDA/MPS contract.
    This adapter does not discover or execute an accelerator at admission.
    """
    if type(device) is not str or device not in {'cpu', 'cuda', 'mps'}:
        raise HandshakeError('Preflight ticket requires an explicit supported device')
    context = _root_context()
    if _CACHE is None:
        if context is not None: raise HandshakeError('Owned preflight has no original cached writer admission')
        return _mint_preflight_ticket(device, (), None)
    if context is None or _context() != _CACHE['context'] or not _CACHE['ready']:
        raise HandshakeError('Original preflight writer capability is unavailable')
    root, values = context
    validated = {}
    if _validate(root, values, _CACHE['challenge'], validated=validated) != _CACHE['proof']:
        raise HandshakeError('Original preflight process binding changed')
    admission = _CACHE['admission']
    fields = {'writer_guard', 'writer_handle', 'writer_private_fd', 'writer_fd_identity'}
    if 'writer' not in _CACHE['challenge']:
        if (validated.get('protocol_version') != 3 or not fields.issubset(_CACHE)
                or any(_CACHE[k] is not None for k in fields)):
            raise HandshakeError('Original legacy preflight capability is partial or unsupported')
        admission._begin_producer()
        try: return _mint_preflight_ticket(device, (), admission)
        except BaseException: admission.leave(); raise
    if validated.get('protocol_version') != 4 or any(_CACHE.get(k) is None for k in fields):
        raise HandshakeError('Original enrolled preflight capability is partial')
    writer = _CACHE['challenge']['writer']
    from backend.engine.application_launch_quiescence import writer_guard
    # Finish every validation/registry/maintenance context on this thread.
    private = None; counted = False
    try:
        with writer_guard(root, _CACHE['proof']['nonce'], writer['writer_id'],
                expected_registration_sha256=writer['registration_sha256']):
            anchor = _CACHE['writer_private_fd']; info = os.fstat(anchor)
            if (info.st_dev, info.st_ino) != _CACHE['writer_fd_identity']:
                raise HandshakeError('Original backend writer descriptor identity changed')
            private = os.dup(anchor)
            admission._begin_producer(); counted = True
        # Mint only after the validation guard's final identity check succeeds.
        return _mint_preflight_ticket(device, (private,), admission)
    except BaseException:
        if counted: admission.uncovered('cpu_producer_unconfirmed')
        try:
            if private is not None: os.close(private)
        except BaseException:
            # Guard validation failed, so no usable ticket may escape. Retain
            # opaque original custody without retrying a possibly consumed fd.
            admission.uncovered('cpu_producer_unconfirmed')
            _mint_preflight_ticket(device, (private,), admission, unresolved=True)
            raise
        else:
            if counted: admission.leave()
        raise


@contextmanager
def _preflight_ticket_transport(ticket, *, device):
    if type(ticket) is not _PreflightWriterTicket or ticket not in _PREFLIGHT_TICKETS:
        raise HandshakeError('An original minted preflight ticket is required')
    with ticket.transport(device=device) as transport: yield transport


@contextmanager
def owned_cpu_writer_scope():
    """One fixed CPU producer; duplicate original OFD, never expose its anchor.

    The bootstrap private duplicate is the capability. Inode checks detect
    storage changes, not OFD equivalence. Arbitrary trusted-process descriptor
    table mutation is outside this private in-process contract.
    """
    with _owned_cpu_producer_scope() as transport:
        yield transport


@contextmanager
def owned_flow_cpu_writer_scope(*, device):
    """Selected CPU package worker only; unowned SDK devices keep defaults."""
    with _owned_cpu_producer_scope(flow_device=device) as transport:
        yield transport


@contextmanager
def _owned_cpu_producer_scope(*, flow_device=_FIXED_CPU_PRODUCER):
    context = _root_context()
    if _CACHE is None:
        if context is not None: raise HandshakeError('Owned CPU producer has no original cached writer admission')
        yield ()  # Ordinary unowned/export compatibility, no owned authority.
        return
    if flow_device is not _FIXED_CPU_PRODUCER and (type(flow_device) is not str or flow_device != 'cpu'):
        raise HandshakeError('Owned flow producer admission supports only selected CPU execution')
    if context is None or _context() != _CACHE['context'] or not _CACHE['ready']:
        raise HandshakeError('Original owned CPU producer capability is unavailable')
    root, values = context
    validated = {}
    # NEW fixed admission-only cap, captured once. This is not an inherited
    # HTTP client timeout and cannot renew an execution or drain deadline.
    entry_deadline = time.monotonic() + 1.0
    original_cache = _CACHE; original_context = _context()
    original_socket = original_cache.get('socket')
    original_challenge = _canonical(original_cache['challenge'])
    original_proof = _canonical(original_cache['proof'])
    original_admission = original_cache['admission']
    # Only this original frozen controller CPU callback can separate its
    # fixed mutex-entry budget from its already captured full action budget.
    # HTTP/source/flow callers do not receive a deadline or marker argument.
    authentication_deadline = entry_deadline
    original_execution = original_cache.get('original_cpu_execution')
    execution_fields = None
    if (flow_device is _FIXED_CPU_PRODUCER and getattr(sys, 'frozen', False) is True
            and original_cache['proof'].get('frozen') is True
            and type(original_execution) is dict
            and original_execution.get('thread') is threading.current_thread()):
        if (set(original_execution) != {'done', 'result', 'error', 'deadline', 'thread'}
                or type(original_execution['done']) is not threading.Event
                or original_execution['done'].is_set()
                or original_execution['result'] is not None or original_execution['error'] is not None
                or type(original_execution['deadline']) not in (int, float)
                or not math.isfinite(original_execution['deadline'])):
            raise HandshakeError('Original frozen CPU callback is incomplete or settled')
        execution_fields = (original_execution['thread'], original_execution['done'],
                            original_execution['deadline'], type(original_execution['deadline']))
        authentication_deadline = execution_fields[2]
        if not 0 < authentication_deadline - time.monotonic() <= 210:
            raise HandshakeError('Original frozen CPU action deadline expired or differs')
    original_writer = tuple(original_cache.get(key) for key in
                            ('writer_guard', 'writer_handle', 'writer_private_fd', 'writer_fd_identity'))
    endpoint = None
    if original_socket is not None:
        from backend.engine.application_preflight_child_relay import _endpoint
        endpoint = _endpoint(original_socket)
    def entry_current():
        if time.monotonic() >= authentication_deadline:
            raise HandshakeError('Original CPU producer action deadline expired')
        current = _root_context()
        if (_CACHE is not original_cache or current is None or current[0] != root
                or current[1] != values or _context() != original_context
                or original_cache['context'] != original_context or not original_cache['ready']
                or original_cache['admission'] is not original_admission
                or original_cache.get('socket') is not original_socket
                or _canonical(original_cache['challenge']) != original_challenge
                or _canonical(original_cache['proof']) != original_proof):
            raise HandshakeError('Original CPU producer admission context changed')
        writer = tuple(original_cache.get(key) for key in
                       ('writer_guard', 'writer_handle', 'writer_private_fd', 'writer_fd_identity'))
        if (writer[0] is not original_writer[0] or writer[1] is not original_writer[1]
                or writer[2:] != original_writer[2:]):
            raise HandshakeError('Original CPU producer admission writer changed')
        if original_socket is not None and _endpoint(original_socket) != endpoint:
            raise HandshakeError('Original CPU producer admission endpoint changed')
        if execution_fields is not None:
            if (original_cache.get('original_cpu_execution') is not original_execution
                    or set(original_execution) != {'done', 'result', 'error', 'deadline', 'thread'}
                    or original_execution['thread'] is not execution_fields[0]
                    or threading.current_thread() is not execution_fields[0]
                    or original_execution['done'] is not execution_fields[1]
                    or original_execution['done'].is_set()
                    or original_execution['result'] is not None or original_execution['error'] is not None
                    or type(original_execution['deadline']) is not execution_fields[3]
                    or original_execution['deadline'] != execution_fields[2]):
                raise HandshakeError('Original frozen CPU callback custody changed')
        if time.monotonic() >= authentication_deadline:
            raise HandshakeError('Original CPU producer action deadline expired')
    def entry_acquire():
        # Context/endpoint checks consume the same once-captured entry budget.
        # Slow successful __enter__ is checked before any original owner hash.
        if time.monotonic() >= entry_deadline:
            raise HandshakeError('Original transition entry deadline expired')
        entry_current()
        if time.monotonic() >= entry_deadline:
            raise HandshakeError('Original transition entry deadline expired')
    if _validate(root, values, _CACHE['challenge'], validated=validated,
                 absolute_deadline=authentication_deadline, before_acquire=entry_acquire) != _CACHE['proof']:
        raise HandshakeError('Original owned CPU producer process binding changed')
    entry_current()
    if 'writer' not in _CACHE['challenge']:
        fields = {'writer_guard', 'writer_handle', 'writer_private_fd', 'writer_fd_identity'}
        if (validated.get('protocol_version') != 3 or not fields.issubset(_CACHE)
                or any(_CACHE[k] is not None for k in fields)):
            raise HandshakeError('Original legacy CPU producer capability is partial or unsupported')
        # Original authenticated protocol3 compatibility: count the accepted
        # producer, but do not invent an enrolled lock or descendant fence.
        with _CACHE['admission'].producer(): yield ()
        return
    if any(_CACHE.get(k) is None for k in ('writer_guard', 'writer_handle', 'writer_private_fd', 'writer_fd_identity')):
        raise HandshakeError('Original enrolled CPU producer capability is partial')
    writer = _CACHE['challenge']['writer']
    from backend.engine.application_launch_quiescence import writer_guard
    with writer_guard(root, _CACHE['proof']['nonce'], writer['writer_id'],
            expected_registration_sha256=writer['registration_sha256']):
        # Temporary guard linearizes OPEN admission. The exposed Guard number
        # cannot become authority after close/reuse; duplicate only the private
        # anchor that bootstrap obtained directly from the acquired original OFD.
        anchor = _CACHE['writer_private_fd']
        info = os.fstat(anchor)
        if (info.st_dev, info.st_ino) != _CACHE['writer_fd_identity']:
            raise HandshakeError('Original backend writer descriptor identity changed')
        with _CACHE['admission'].producer():
            transport = os.dup(anchor)
            try: yield (transport,)
            finally: os.close(transport)


class HandshakeError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8', 'strict')


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length and all(c in '0123456789abcdef' for c in value)


def validate_drain_request(frame, *, nonce, epoch, binding, writer_id, registration_sha256):
    names = {'schema_version', 'kind', 'challenge', 'request_id', 'nonce', 'epoch', 'binding_sha256',
        'backend_claim_sha256', 'writer_id', 'registration_sha256', 'closed_registry_sha256', 'budget_ms'}
    if (not isinstance(frame, dict) or set(frame) != names or type(frame['schema_version']) is not int
            or frame['schema_version'] != 1 or frame['kind'] != 'backend_drain_request'
            or not _hex(frame['challenge']) or not _hex(frame['request_id'], 32)
            or frame['nonce'] != nonce or frame['epoch'] != epoch
            or frame['binding_sha256'] != hashlib.sha256(_canonical(binding)).hexdigest()
            or not _hex(frame['backend_claim_sha256']) or frame['writer_id'] != writer_id
            or frame['registration_sha256'] != registration_sha256 or not _hex(frame['closed_registry_sha256'])
            or type(frame['budget_ms']) is not int or not 0 < frame['budget_ms'] <= 4000):
        raise HandshakeError('Foreign or invalid managed drain request')
    return frame


def validate_drain_receipt(frame, request, *, backend_process):
    names = {'schema_version', 'kind', 'request', 'backend_proof', 'status', 'active_scopes', 'unsupported',
        'scope', 'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'}
    if (not isinstance(frame, dict) or set(frame) != names or type(frame['schema_version']) is not int
            or frame['schema_version'] != 1 or frame['kind'] != 'backend_managed_drain'
            or _canonical(frame['request']) != _canonical(request) or not isinstance(frame['backend_proof'], dict)
            or not _same_process(frame['backend_proof'].get('process'), backend_process)
            or hashlib.sha256(_canonical(frame['backend_proof'])).hexdigest() != request['backend_claim_sha256']
            or not isinstance(frame['status'], str) or frame['status'] not in {'managed_scopes_drained', 'refused'} or type(frame['active_scopes']) is not int
            or frame['active_scopes'] < 0 or not isinstance(frame['unsupported'], list)
            or any(not isinstance(x, str) for x in frame['unsupported']) or frame['unsupported'] != sorted(set(frame['unsupported']))
            or any(x not in {'uncovered_request_protocol', 'accepted_background_work', 'startup_background_protocols', 'cpu_producer_unconfirmed'} for x in frame['unsupported'])
            or frame['scope'] != 'reviewed_foreground_scopes_only'
            or any(frame[x] is not False for x in ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
            or frame['status'] == 'managed_scopes_drained' and (frame['active_scopes'] != 0 or frame['unsupported'])):
        raise HandshakeError('Managed drain proof is changed or exceeds its scope')
    return frame


def validate_backend_exit(frame, request, backend_process):
    expected = {'schema_version': 1, 'kind': 'main_backend_exit', 'nonce': request['nonce'],
        'epoch': request['epoch'], 'request_id': request['request_id'], 'challenge': request['challenge'],
        'backend_process': backend_process, 'returncode': 0, 'signal': None}
    if (not isinstance(frame, dict) or set(frame) != set(expected)
            or not _same_process(frame['backend_process'], backend_process)
            or type(frame['schema_version']) is not int or type(frame['returncode']) is not int
            or any(frame[k] != v for k, v in expected.items() if k != 'backend_process')):
        raise HandshakeError('Original Node backend handle exit frame differs')
    return frame


def _deadline(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= MAX_DEADLINE:
        raise HandshakeError('Private descriptor deadline must be positive and at most 210 seconds')
    return time.monotonic() + seconds


def read_frame(sock, deadline_seconds):
    """Read one canonical bounded UTF-8 JSON object; leave later frames unread."""
    deadline = _deadline(deadline_seconds); raw = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([sock], [], [], max(0, remaining))[0]:
                raise HandshakeError('Private descriptor timeout before complete frame')
            chunk = sock.recv(1)
            if not chunk: raise HandshakeError('Private descriptor EOF before complete frame')
            raw.extend(chunk)
            if len(raw) > MAX_FRAME: raise HandshakeError('Private descriptor frame exceeds 65536 bytes')
            if chunk == b'\n': break
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value: raise HandshakeError('Duplicate private descriptor field')
                value[key] = item
            return value
        value = json.loads(bytes(raw[:-1]).decode('utf-8', 'strict'), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(HandshakeError('Nonfinite private descriptor number')))
        if not isinstance(value, dict) or _canonical(value) + b'\n' != raw:
            raise HandshakeError('Private descriptor frame is not a canonical JSON object')
        return value
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Invalid private descriptor frame: ' + str(exc)) from exc


def send_frame(sock, value, *, absolute_deadline=None):
    """Write one bounded canonical object without an unbounded peer wait."""
    try:
        deadline = _deadline(10)
        if absolute_deadline is not None:
            if type(absolute_deadline) not in (int, float) or not math.isfinite(absolute_deadline):
                raise HandshakeError('Original private send deadline differs')
            deadline = min(deadline, absolute_deadline)
        if not isinstance(value, dict): raise HandshakeError('Private descriptor frame must be an object')
        raw = _canonical(value) + b'\n'
        if len(raw) > MAX_FRAME: raise HandshakeError('Private descriptor frame exceeds 65536 bytes')
        offset = 0
        while offset < len(raw):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [sock], [], max(0, remaining))[1]:
                raise HandshakeError('Private descriptor timeout while sending frame')
            sent = sock.send(raw[offset:], getattr(socket, 'MSG_DONTWAIT', 0))
            if sent <= 0: raise HandshakeError('Private descriptor closed while sending frame')
            offset += sent
        if time.monotonic() >= deadline: raise HandshakeError('Original private send completed after its deadline')
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Cannot send private descriptor frame: ' + str(exc)) from exc


def _context():
    return (tuple(os.environ.get(name) for name in _CONTEXT_NAMES), tuple(sys.argv),
            sys.executable, bool(getattr(sys, 'frozen', False)))


def _root_context():
    """Read context without discovery, creation, or application imports."""
    from backend.engine.global_store_paths import owned_root
    values = {name: os.environ.get(name) for name in _CONTEXT_NAMES}
    launch = [values[name] for name in _CONTEXT_NAMES[1:]]
    if values['VISION_APPLICATION_LAUNCH_FD'] is not None:
        raise HandshakeError('Main launch descriptor must be stripped before backend startup')
    configured = values[_CONTEXT_NAMES[0]]
    if configured is None:
        if any(value is not None for value in launch): raise HandshakeError('Partial owned backend launch context')
        return None
    if not configured or not Path(configured).is_absolute() or str(Path(configured)) != configured:
        raise HandshakeError('Owned backend root must be an explicit canonical absolute path')
    root, owner = owned_root(configured)
    if root is None:
        if any(value is not None for value in launch): raise HandshakeError('Backend launch root has no original ownership')
        return None
    if str(root) != configured: raise HandshakeError('Backend launch root differs from original ownership')
    present = any(value is not None for value in launch)
    if not present:
        if (root/'application-active.json').exists() or any((root/name).exists() for name in
                ('application-launch-lease.json', '.application-launches', 'application-database-ownership.lock')):
            raise HandshakeError('Owned application pair requires its private backend descriptor')
        return None
    if (not _hex(values['VISION_APPLICATION_LAUNCH_NONCE'], 32)
            or not _hex(values['VISION_APPLICATION_GENERATION'], 32)
            or not values['VISION_APPLICATION_DATABASE_GENERATION']
            or values['VISION_APPLICATION_BACKEND_FD'] is None):
        raise HandshakeError('Incomplete owned backend launch context')
    return root, values


def _transport(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or str(int(value)) != value or not 3 <= int(value) <= 8192:
        raise HandshakeError('Backend descriptor must be a canonical inherited descriptor from 3 to 8192')
    fd = int(value)
    try:
        os.set_inheritable(fd, False)
        sock = socket.socket(fileno=fd)
        if (os.name != 'posix' or sock.family != socket.AF_UNIX or sock.type != socket.SOCK_STREAM
                or sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
                or sock.getsockname() not in ('', b'') or sock.getpeername() not in ('', b'')):
            raise HandshakeError('Backend descriptor must be an anonymous connected POSIX stream socket')
        return sock
    except (OSError, ValueError) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Backend private descriptor is unavailable') from exc


def _arguments(root):
    expected = {'--project-dir': str(root/'projects'), '--shared-auth-dir': str(root/'auth')}
    for flag, path in expected.items():
        for argument in sys.argv[1:]:
            option = argument.split('=', 1)[0]
            if option.startswith('--') and option != flag and flag.startswith(option):
                raise HandshakeError('Abbreviated owned backend path option is ambiguous')
        matches = [i for i, argument in enumerate(sys.argv[1:], 1) if argument == flag or argument.startswith(flag+'=')]
        if len(matches) != 1: raise HandshakeError('Owned backend requires exactly one ' + flag)
        i = matches[0]; arg = sys.argv[i]
        value = arg.split('=', 1)[1] if '=' in arg else sys.argv[i+1] if i+1 < len(sys.argv) else None
        if value != path: raise HandshakeError('Owned backend ' + flag + ' differs from original root')


def _same_process(value, actual):
    from backend.engine.application_launch_lease import _identity_shape
    return _identity_shape(value) and value['pid'] == actual['pid'] and value['created_at'] == actual['created_at'] and value['command_sha256'] == actual['command_sha256']


def _executable(root, binding, frame):
    from backend.engine import runtime_update as update
    _, _, manifest = update._validated_intent(root, binding['update_id'])
    application = root/update.GENERATIONS/binding['application_generation']/'application'
    executable = frame['backend_executable']
    if not isinstance(executable, str) or not Path(executable).is_absolute() or str(Path(executable)) != executable:
        raise HandshakeError('Backend executable must be an absolute current application row')
    path = update._unlinked(executable)
    try: relative = path.relative_to(application).as_posix()
    except ValueError as exc: raise HandshakeError('Backend executable is outside current application') from exc
    rows = [row for row in manifest['files'] if row['path'] == relative and row['executable']]
    if len(rows) != 1: raise HandshakeError('Backend executable is not an approved current application row')
    row = rows[0]; update._check_file(path, row)
    import psutil
    command = psutil.Process(os.getpid()).cmdline()
    frozen = bool(getattr(sys, 'frozen', False)); build = frame['backend_build_identity_sha256']
    if frozen:
        if sys.executable != executable or not command or command[0] != executable or not _hex(build):
            raise HandshakeError('Frozen backend executable or build identity differs')
        receipt_path = path.parent/'backend-release.json'
        receipt_rows = [item for item in manifest['files'] if item['path'] == receipt_path.relative_to(application).as_posix()]
        if len(receipt_rows) != 1: raise HandshakeError('Frozen backend release receipt is not an approved application row')
        update._check_file(receipt_path, receipt_rows[0]); receipt = update._json(update._read(receipt_path, 8*1024**2))
        inventory = update._json(update._read(Path(getattr(sys, '_MEIPASS', ''))/'backend-build-inventory.json', 8*1024**2))
        if (not isinstance(receipt, dict) or receipt.get('schema_version') != 1 or receipt.get('executable') != path.name
                or receipt.get('executable_sha256') != row['sha256'] or not isinstance(inventory, dict)
                or inventory.get('build_identity_sha256') != build
                or update._canonical(receipt.get('inventory')) != update._canonical(inventory)):
            raise HandshakeError('Frozen backend release receipt or embedded inventory differs')
    else:
        if build is not None or not sys.argv or sys.argv[0] != executable or executable not in command[1:2]:
            raise HandshakeError('Source backend must execute its approved script and cannot claim a frozen build')
    return {'executable': executable, 'executable_sha256': row['sha256'],
            'build_identity_sha256': build, 'frozen': frozen}


@contextmanager
def _transition_admission_before_deadline(root, nonce, absolute_deadline, *, before_attempt=None):
    """Retry only original mutex __enter__, never a yielded body or exit.

    The caller supplies one captured absolute bound. Generic lease admission
    remains nonblocking; this helper cannot read a new frame or create authority.
    """
    from backend.engine import application_launch_lease as lease
    if absolute_deadline is None:
        with lease._transition_admission(root, nonce):
            yield
        return
    if type(absolute_deadline) not in (int, float) or not math.isfinite(absolute_deadline):
        raise HandshakeError('Original transition entry deadline differs')
    def remaining():
        value = absolute_deadline - time.monotonic()
        if value <= 0: raise HandshakeError('Original transition entry deadline expired')
        return value
    # ExitStack forwards body/GeneratorExit to the original context exactly
    # once. Its exit, authentication and post-body failures are never retried.
    with ExitStack() as stack:
        while True:
            remaining()
            if before_attempt is not None: before_attempt()
            remaining()
            try:
                stack.enter_context(lease._transition_admission(root, nonce))
            except lease.LeaseTransitionBusy:
                if before_attempt is not None: before_attempt()
                time.sleep(min(.005, remaining()))
                continue
            break
        remaining()
        yield
        remaining()
    # A normal guard exit cannot turn an expired original action into success.
    # Original body/exit exceptions propagate before this check, without retry.
    remaining()


@contextmanager
def _validated_backend_admission(root, values, frame, *, validated=None,
                                 absolute_deadline=None, before_acquire=None):
    """Full original backend authentication under one retained transition OFD.

    This is not a cached-proof or held-lock bypass. Every entry acquires the
    original mutex and rechecks all original owner evidence before and after
    its body; no transferable admission marker is accepted.
    """
    from backend.engine import application_launch_lease as lease, runtime_update as update
    def original_challenge():
        expected_fields = _CHALLENGE_FIELDS | ({'writer'} if 'writer' in frame else set())
        if (set(frame) != expected_fields or type(frame['schema_version']) is not int or frame['schema_version'] != 1
                or frame['kind'] != 'backend_challenge' or not _hex(frame['challenge']) or not _hex(frame['epoch'], 32)
                or frame['nonce'] != values['VISION_APPLICATION_LAUNCH_NONCE'] or type(frame['backend_pid']) is not int
                or frame['backend_pid'] != os.getpid()): raise HandshakeError('Invalid or foreign backend challenge')
        _arguments(root)
    original_challenge()
    def before_attempt():
        original_challenge()
        if before_acquire is not None: before_acquire()
    with _transition_admission_before_deadline(root, frame['nonce'], absolute_deadline,
                                              before_attempt=before_attempt):
        # Outside the enter-retry loop: callback/body/exit failures propagate
        # once, and slow successful entry cannot spend a larger auth budget.
        before_attempt()
        protocol = [None]
        def original_owner():
            original_challenge()
            record = lease._load(root)
            if (record is None or record['nonce'] != frame['nonce'] or record['state'] not in {'starting', 'ready'}
                    or not record['spawn_attempted'] or record['process'] is None):
                raise HandshakeError('Backend challenge has no current spawned main owner')
            binding = record['binding']
            if 'writer' in frame:
                drain = record.get('writer_drain')
                expected = {k: drain[k] for k in ('writer_id', 'registration_sha256', 'registration_registry_sha256')} if drain else None
                if expected is None or _canonical(frame['writer']) != _canonical(expected):
                    raise HandshakeError('Backend writer registration differs from original controller')
            elif record['protocol_version'] == 4:
                raise HandshakeError('Enrolled backend requires exact private writer registration')
            if (_canonical(frame['binding']) != _canonical(binding)
                    or values['VISION_APPLICATION_GENERATION'] != binding['application_generation']
                    or values['VISION_APPLICATION_DATABASE_GENERATION'] != binding['database_generation_path']):
                raise HandshakeError('Backend challenge application/database binding differs')
            fresh, executable = update._backend_launch_binding(root, binding['authority_path'], frame,
                                                              pinned_authority_sha256=binding['authority_sha256'])
            if _canonical(fresh) != _canonical(binding): raise HandshakeError('Committed backend launch pair changed')
            protocol[0] = record['protocol_version']
            if validated is not None: validated['protocol_version'] = record['protocol_version']
            return {'schema_version': 1, 'kind': 'backend_claim', 'challenge': frame['challenge'], 'epoch': frame['epoch'],
                'nonce': frame['nonce'], 'binding_sha256': hashlib.sha256(_canonical(binding)).hexdigest(),
                'process': lease._identity(os.getpid()), **executable}

        before = original_owner(); original_protocol = protocol[0]
        body_error = None
        try:
            yield before
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            try:
                after = original_owner()
                if after != before or protocol[0] != original_protocol:
                    raise HandshakeError('Original backend authentication changed during admission')
            except BaseException as exc:
                if body_error is None: raise
                try:
                    body_error.add_note('Original backend post-body authentication also refused: ' + str(exc))
                except BaseException:
                    pass  # Advisory diagnostics cannot replace the first body failure.


def _validate(root, values, frame, *, validated=None, absolute_deadline=None, before_acquire=None):
    with _validated_backend_admission(root, values, frame, validated=validated,
                                      absolute_deadline=absolute_deadline, before_acquire=before_acquire) as proof:
        return proof


def _no_replay(sock, *, ready=False):
    if select.select([sock], [], [], 0)[0]:
        raw = sock.recv(1, socket.MSG_PEEK | getattr(socket, 'MSG_DONTWAIT', 0))
        if raw: raise HandshakeError('Unexpected replay on backend private descriptor')
        if not ready: raise HandshakeError('Backend private descriptor closed before readiness')


def _new_original_work_admission():
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    return BackendWorkAdmission(activity_observer=OriginalRequestActivityObserver())


def begin_fresh_startup_observation(app, manager, telemetry):
    """Optional shadow startup facts; ineligible owned startup remains sticky."""
    admission = getattr(app.state, 'owned_work_admission', None)
    if admission is None: return None
    from backend.engine.original_fresh_startup import _begin_from_original_cache
    try: return _begin_from_original_cache(_CACHE, app, manager, telemetry)
    except Exception:
        admission.uncovered('startup_background_protocols')
        return None


def _finish_fresh_startup_observation(original_cache, deadline, receipts):
    observed = original_cache.get('fresh_startup_observation')
    if observed is None: return None
    if _CACHE is not original_cache:
        raise HandshakeError('Original startup cache changed during preparation')
    from backend.engine.migration_guard import _admission_capability
    reader = _admission_capability(original_cache['root'], exclusive=False)
    return original_cache['admission'].finish_original_startup(observed, deadline, reader, receipts)


def _startup_remaining(deadline):
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise HandshakeError('Original backend startup deadline differs')
    remaining = deadline - time.monotonic()
    if not 0 < remaining <= MAX_DEADLINE:
        raise HandshakeError('Original backend startup deadline expired or differs')
    return remaining


def _startup_cache_freshness(cache):
    """Pin only the original pre-READY endpoint and immutable binding values."""
    from backend.engine.application_preflight_child_relay import BackendRelayQueue
    deadline = cache['startup_deadline']; context = cache['context']; root = cache['root']
    sock = cache['socket']; queue = cache.get('preflight_queue')
    challenge = cache['challenge']; proof = cache['proof']
    challenge_bytes = _canonical(challenge); proof_bytes = _canonical(proof)
    def fresh():
        _startup_remaining(deadline)
        current = _root_context()
        if (_CACHE is not cache or current is None or current[0] != root or _context() != context
                or cache['context'] != context or cache['root'] != root or cache['ready'] is not False
                or type(cache['startup_deadline']) is not type(deadline) or cache['startup_deadline'] != deadline
                or cache['socket'] is not sock or cache.get('preflight_queue') is not queue
                or type(queue) is not BackendRelayQueue
                or cache['challenge'] is not challenge or cache['proof'] is not proof
                or _canonical(challenge) != challenge_bytes or _canonical(proof) != proof_bytes):
            raise HandshakeError('Original backend startup cache or binding changed')
        queue._fresh(sock, reader=False)
        _startup_remaining(deadline)
    fresh()
    return deadline, fresh


def early_backend_bootstrap():
    """Authenticate once before application imports; recheck every local reuse."""
    global _CACHE
    try:
        startup_deadline = _deadline(210) if _CACHE is None else None
        context = _root_context()
        if _CACHE is not None:
            if context is None or _context() != _CACHE['context']:
                raise HandshakeError('Verified backend process context changed')
            root, values = context
            _no_replay(_CACHE['socket'], ready=_CACHE['ready'])
            if _CACHE['ready']:
                proof = _validate(root, values, _CACHE['challenge'])
            else:
                original_cache = _CACHE
                deadline, fresh = _startup_cache_freshness(original_cache)
                def before_acquire():
                    fresh()
                    _no_replay(original_cache['socket'], ready=False)
                    fresh()
                proof = _validate(root, values, original_cache['challenge'],
                                  absolute_deadline=deadline, before_acquire=before_acquire)
                before_acquire()
            if proof != _CACHE['proof']: raise HandshakeError('Verified backend process binding changed')
            return dict(proof)
        if context is None: return None
        _startup_remaining(startup_deadline)
        root, values = context; sock = _transport(values['VISION_APPLICATION_BACKEND_FD'])
        from backend.engine.application_preflight_child_relay import BackendRelayQueue
        queue = BackendRelayQueue(sock); original_context = _context()
        frame = read_frame(sock, _startup_remaining(startup_deadline)); frame_bytes = _canonical(frame)
        def initial_fresh():
            _startup_remaining(startup_deadline)
            if (_CACHE is not None or _context() != original_context or _root_context() != context
                    or _canonical(frame) != frame_bytes):
                raise HandshakeError('Original backend startup context or challenge changed')
            queue._fresh(sock, reader=False)
            _no_replay(sock, ready=False)
            _startup_remaining(startup_deadline)
        proof = _validate(root, values, frame, absolute_deadline=startup_deadline,
                          before_acquire=initial_fresh)
        initial_fresh()
        guard = None; handle = None; fd_identity = None; private_fd = None
        if 'writer' in frame:
            from backend.engine.application_launch_quiescence import writer_guard, inspect_epoch
            writer = frame['writer']; snapshot = inspect_epoch(root, frame['nonce'])
            if snapshot['registry_sha256'] != writer['registration_registry_sha256']:
                raise HandshakeError('Backend initial writer registry changed before admission')
            guard = writer_guard(root, frame['nonce'], writer['writer_id'], expected_registration_sha256=writer['registration_sha256'])
            handle = guard.__enter__()  # Retained before claim/mutable imports.
            info = os.fstat(handle.pass_fds[0]); fd_identity = (info.st_dev, info.st_ino)
            private_fd = os.dup(handle.pass_fds[0])  # Original acquired OFD, never exported.
        initial_fresh(); send_frame(sock, proof, absolute_deadline=startup_deadline)
        initial_fresh()
        _CACHE = {'context': original_context, 'root': root, 'socket': sock, 'challenge': frame, 'proof': proof,
            'startup_deadline': startup_deadline,
            'ready': False, 'writer_guard': guard, 'writer_handle': handle, 'writer_private_fd': private_fd, 'writer_fd_identity': fd_identity,
            'admission': _new_original_work_admission()}
        _CACHE['preflight_queue'] = queue
        if proof['frozen'] is False and 'writer' in frame:
            from backend.engine.application_owned_cpu_child_relay import CpuRelayQueue
            _CACHE['source_cpu_queue'] = CpuRelayQueue(_CACHE)
        _startup_remaining(startup_deadline)
        return dict(proof)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Owned backend bootstrap refused: ' + str(exc)) from exc


def backend_bootstrap_ready():
    """Publish binding readiness only after recovery under shared lifespan admission."""
    proof = early_backend_bootstrap()
    if proof is None: return None
    from backend.engine.migration_guard import shared_admitted
    root = _CACHE['root']
    if not shared_admitted(root):
        raise HandshakeError('Backend readiness requires admitted shared application lifespan')
    if not _CACHE['ready']:
        cache = _CACHE; deadline, fresh = _startup_cache_freshness(cache)
        _no_replay(cache['socket'], ready=False)
        send_frame(cache['socket'], {**proof, 'kind': 'backend_ready'}, absolute_deadline=deadline)
        # The peer may already have queued the next valid phase. Only its
        # original service reader consumes that frame after READY publication.
        fresh(); cache['ready'] = True
    return {**proof, 'kind': 'backend_ready'}


def _validate_service_action(original_cache, deadline):
    """Read-only exact binding checks within one existing channel deadline.

    Publication mutex contention changes no authority or record. Only that
    named transient can wait, on the same cached endpoint/context and absolute
    budget; every structural failure immediately refuses without repair.
    """
    from backend.engine.application_launch_lease import LeaseTransitionBusy
    from backend.engine.application_preflight_child_relay import BackendRelayQueue
    original_queue = original_cache.get('preflight_queue')
    original_socket = original_cache.get('socket')
    if type(original_queue) is not BackendRelayQueue:
        raise HandshakeError('Original service action queue is unavailable')
    def endpoint():
        if (original_cache.get('preflight_queue') is not original_queue
                or original_cache.get('socket') is not original_socket):
            raise HandshakeError('Original service action queue/socket changed')
        original_queue._fresh(original_socket, reader=True)
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise HandshakeError('Original service action deadline is invalid')
    while True:
        context = _root_context()
        if (_CACHE is not original_cache or context is None
                or _context() != original_cache['context'] or not original_cache['ready']):
            raise HandshakeError('Original service action cache/context differs')
        endpoint()
        if time.monotonic() >= deadline:
            raise HandshakeError('Original service binding verification deadline expired')
        try:
            proof = _validate(original_cache['root'], context[1], original_cache['challenge'])
        except LeaseTransitionBusy:
            endpoint()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise HandshakeError('Original service binding verification deadline expired')
            time.sleep(min(.005, remaining))
            continue
        if (_CACHE is not original_cache or _context() != original_cache['context']
                or proof != original_cache['proof']):
            raise HandshakeError('Original service action binding changed')
        endpoint()
        if time.monotonic() >= deadline:
            raise HandshakeError('Original service binding verification completed late')
        return proof



_BACKEND_SHUTDOWN_MINTS = set()
_BACKEND_SHUTDOWNS = weakref.WeakKeyDictionary()


def _backend_shutdown_endpoint(original_cache):
    """Fresh private endpoint checks; untrusted peeks confer no authority."""
    from backend.engine.application_preflight_child_relay import BackendRelayQueue
    context = _root_context()
    queue = original_cache.get('preflight_queue')
    if (_CACHE is not original_cache or context is None or context[0] != original_cache['root']
            or _context() != original_cache['context'] or not original_cache['ready']
            or type(queue) is not BackendRelayQueue):
        raise HandshakeError('Original shutdown endpoint/context differs')
    queue._fresh(original_cache['socket'], reader=True)
    return context


def _peek_backend_shutdown_prepare(sock, deadline, current):
    """Classify only a complete canonical preparation without consuming it.

    Other or malformed frames retain their original full validator/read path.
    The sole original reader owns this bounded peek; no socket reader is added.
    """
    while time.monotonic() < deadline:
        current()
        if not select.select([sock], [], [], max(0, deadline-time.monotonic()))[0]:
            return False
        raw = sock.recv(MAX_FRAME+1, socket.MSG_PEEK | getattr(socket, 'MSG_DONTWAIT', 0))
        if not raw or len(raw) > MAX_FRAME: return False
        end = raw.find(b'\n')
        if end >= 0:
            raw = raw[:end+1]
            try:
                def pairs(items):
                    value = {}
                    for key, item in items:
                        if key in value: raise ValueError('duplicate')
                        value[key] = item
                    return value
                frame = json.loads(raw[:-1].decode('utf-8', 'strict'), object_pairs_hook=pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
                return (type(frame) is dict and _canonical(frame)+b'\n' == raw
                        and frame.get('kind') == 'backend_shutdown_prepare')
            except (ValueError, UnicodeError, RecursionError):
                return False
        # A partial readable stream must not become an unbounded busy loop.
        time.sleep(min(.001, max(0, deadline-time.monotonic())))
    return False


def _backend_shutdown_controls(original_cache):
    """Read original control publications/fences, without whole asset hashing."""
    from backend.engine import application_launch_lease as lease, runtime_update as update
    from backend.engine.global_store_paths import active_generation
    from backend.engine.application_launch_quiescence import inspect_epoch
    context = _backend_shutdown_endpoint(original_cache)
    root, owner = update._root(original_cache['root'])
    update.validate_attachment(root)
    pointer = update._pointer(root); current = active_generation(root)
    challenge = original_cache['challenge']; binding = challenge['binding']
    if (root != context[0] or pointer is None or current is None
            or owner['installation_id'] != binding['installation_id']
            or pointer['installation_id'] != binding['installation_id']
            or pointer['update_id'] != binding['update_id']
            or pointer['application_generation'] != binding['application_generation']
            or _canonical(pointer['database_pointer']) != _canonical(binding['database_pointer'])
            or _canonical(current[1]) != _canonical(binding['database_pointer'])
            or str(current[0]) != binding['database_generation_path']):
        raise HandshakeError('Original shutdown application/database fence changed')
    row = lease._load(root)
    writer = challenge.get('writer'); drain = row.get('writer_drain') if row is not None else None
    if (row is None or row['nonce'] != challenge['nonce'] or row['protocol_version'] != 4
            or row['state'] not in {'starting', 'ready'} or not row['spawn_attempted']
            or not _same_process(row['process'], challenge['main_process'])
            or _canonical(row['binding']) != _canonical(binding) or writer is None or drain is None
            or any(drain[k] != writer[k] for k in ('writer_id','registration_sha256','registration_registry_sha256'))):
        raise HandshakeError('Original shutdown launch/writer controls changed')
    return {'row': row, 'epoch': inspect_epoch(root, challenge['nonce'])}


def _shutdown_fd(fd):
    import stat
    if type(fd) is not int: raise HandshakeError('Original shutdown writer descriptor differs')
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
            or info.st_size != 0 or stat.S_IMODE(info.st_mode) != 0o600):
        raise HandshakeError('Original shutdown writer descriptor is not its private lifetime lock')
    return (fd, info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_uid,
            info.st_nlink, info.st_size, info.st_mode, info.st_mtime_ns, info.st_ctime_ns)


class _BackendShutdown:
    """One private original preparation; intermediate guards grant no release."""
    __slots__ = ('__weakref__',)

    def __init__(self, *, _mint=None):
        if _mint not in _BACKEND_SHUTDOWN_MINTS:
            raise HandshakeError('Original backend shutdown must be freshly prepared')

    def __copy__(self): raise HandshakeError('Original backend shutdown cannot be copied')
    def __deepcopy__(self, memo): raise HandshakeError('Original backend shutdown cannot be copied')
    def __reduce_ex__(self, protocol): raise HandshakeError('Original backend shutdown cannot be serialized')

    def _state(self):
        state = _BACKEND_SHUTDOWNS.get(self)
        if (state is None or state['failed'] or state['pid'] != os.getpid()
                or state['thread'] is not threading.current_thread()
                or state['parent_pid'] != os.getppid() or state['session'] != os.getsid(0)
                or state['process_group'] != os.getpgrp()):
            raise HandshakeError('Original backend shutdown process/thread differs')
        return state

    @property
    def deadline(self): return self._state()['deadline']

    def _current(self, deadline, *, gate=None):
        state = self._state(); cache = state['cache']
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or deadline > state['deadline'] or time.monotonic() >= deadline):
            raise HandshakeError('Original backend shutdown deadline expired or renewed')
        from backend.engine import application_launch_lease as lease
        from backend.engine.migration_guard import _admission_capability
        context = _backend_shutdown_endpoint(cache)
        if (cache['root'] is not state['root'] or cache['context'] is not state['context']
                or _context() != state['context'] or context[0] != state['root']
                or cache['socket'] is not state['socket'] or cache['preflight_queue'] is not state['queue']
                or cache['admission'] is not state['admission'] or cache['proof'] is not state['proof_record']
                or cache['challenge'] is not state['challenge_record']
                or _canonical(cache['proof']) != state['proof'] or _canonical(cache['challenge']) != state['challenge']
                or cache.get('shutdown_prepare_attempt') is not state['attempt']
                or _admission_capability(state['root'], exclusive=False) is not state['lifespan']
                or cache['writer_guard'] is not state['writer_guard'] or cache['writer_handle'] is not state['writer_handle']
                or cache['writer_handle'].pass_fds != state['pass_fds']
                or cache['writer_private_fd'] != state['private_fd']
                or cache['writer_fd_identity'] != state['writer_fd_identity']
                or tuple(_shutdown_fd(fd) for fd in state['pass_fds']) != state['writer_fds']
                or _shutdown_fd(state['private_fd']) != state['private_identity']
                or cache.get('source_cpu_queue') is not state['source_queue']
                or not _same_process(lease._identity(os.getpid()), state['proof_record']['process'])
                or not _same_process(lease._identity(os.getppid()), state['challenge_record']['main_process'])):
            raise HandshakeError('Original backend shutdown retained custody changed')
        state['queue']._fresh(state['socket'], reader=True)
        if state['source_queue'] is not None: state['source_queue']._fresh(state['socket'], reader=True)
        if state['queue']._active is not None or state['source_queue'] is not None and state['source_queue']._active is not None:
            raise HandshakeError('Original backend shutdown has an unresolved accepted exchange')
        # Preparation metadata is provisional custody only. After the exact
        # preparation response, physical drain actions use small fresh controls;
        # complete bytes/namespaces must pass controller postvalidation while
        # the original main/channel remain alive before any final publication.
        if state['phase'] == 'prepared' and not state['sent'] and gate is None:
            from backend.engine.application_shutdown_transaction import asset_custody
            asset_error = None
            try: assets = asset_custody(state['root'], state['challenge_record']['binding'])
            except BaseException as exc: asset_error = exc; assets = None
            if asset_error is not None or assets != state['assets']:
                # Preserve the genuine validator's original error priority. Even a
                # successful full recheck cannot accept changed/restored custody.
                _validate(state['root'], context[1], state['challenge_record'], absolute_deadline=deadline)
                raise HandshakeError('Original backend shutdown asset custody changed') from asset_error
        controls = _backend_shutdown_controls(cache)
        from backend.engine.runtime_process_control import private_json_bytes
        original_row = state['controls']['row']; original_epoch = state['controls']['epoch']
        closed_registry = {**original_epoch['registry'], 'state': 'closed',
                           'revision': original_epoch['registry']['revision']+1}
        closed_epoch = {'registry': closed_registry,
                        'registry_sha256': hashlib.sha256(private_json_bytes(closed_registry)).hexdigest()}
        def closing_row(request):
            return {**original_row, 'revision': original_row['revision']+1,
                    'writer_drain': {**original_row['writer_drain'], 'phase': 'closing', 'request': request}}
        if state['phase'] == 'prepared':
            if _canonical(controls['epoch']) not in (_canonical(original_epoch), _canonical(closed_epoch)):
                raise HandshakeError('Original backend shutdown registry has an unplanned transition')
            row = controls['row']; request = row.get('writer_drain', {}).get('request')
            if _canonical(row) != _canonical(original_row):
                self._request(state, request)
                if (_canonical(row) != _canonical(closing_row(request))
                        or _canonical(controls['epoch']) != _canonical(closed_epoch)
                        or request['closed_registry_sha256'] != closed_epoch['registry_sha256']):
                    raise HandshakeError('Original backend shutdown lease closing differs')
            if gate is not None:
                self._request(state, gate)
                if (_canonical(row) != _canonical(closing_row(gate))
                        or _canonical(controls['epoch']) != _canonical(closed_epoch)
                        or gate['closed_registry_sha256'] != closed_epoch['registry_sha256']):
                    raise HandshakeError('Original backend shutdown gate differs from exact closing publication')
            if cache['admission']._closed or state['queue']._closed_deadline is not None:
                raise HandshakeError('Original backend preparation closed admission early')
        else:
            if (_canonical(controls['row']) != _canonical(closing_row(state['request']))
                    or _canonical(controls['epoch']) != _canonical(closed_epoch)
                    or not state['admission']._closed or state['queue']._closed_deadline != state['deadline']
                    or cache.get('preflight_drain_deadline') != state['deadline']
                    or state['source_queue'] is not None and state['source_queue']._closed_deadline != state['deadline']):
                raise HandshakeError('Original backend shutdown closing controls changed')
        if time.monotonic() >= deadline:
            raise HandshakeError('Original backend shutdown controls completed late')
        return json.loads(state['proof'])

    @staticmethod
    def _request(state, frame):
        writer = state['challenge_record']['writer']; proof = state['proof_record']
        validate_drain_request(frame, nonce=proof['nonce'], epoch=proof['epoch'],
            binding=state['challenge_record']['binding'], writer_id=writer['writer_id'],
            registration_sha256=writer['registration_sha256'])
        if (frame['request_id'] != state['prepare']['request_id']
                or frame['backend_claim_sha256'] != state['prepare']['backend_claim_sha256']):
            raise HandshakeError('Original backend shutdown preparation/request differs')

    def fresh(self, deadline):
        try: return self._current(deadline)
        except BaseException:
            state = _BACKEND_SHUTDOWNS.get(self)
            if state is not None: state['failed'] = True
            raise

    def prepared_frame(self):
        state = self._state()
        if state['phase'] != 'prepared' or state['sent']:
            raise HandshakeError('Original backend shutdown preparation is replayed')
        self.fresh(state['deadline']); state['sent'] = True
        return {'schema_version': 1, 'kind': 'backend_shutdown_prepared',
                'nonce': state['prepare']['nonce'], 'request_id': state['prepare']['request_id'],
                'backend_claim_sha256': state['prepare']['backend_claim_sha256']}

    def begin_drain(self, frame, *, started):
        state = self._state()
        try:
            if (state['phase'] != 'prepared' or type(started) not in (int, float)
                    or not math.isfinite(started) or started > time.monotonic()):
                raise HandshakeError('Original backend shutdown gate is replayed or clock differs')
            self._request(state, frame)
            self._current(min(state['deadline'], started+frame['budget_ms']/1000), gate=frame)
            state.update(phase='draining', request=json.loads(_canonical(frame)),
                         deadline=started+frame['budget_ms']/1000)
        except BaseException:
            state['failed'] = True
            raise


def _prepare_backend_shutdown(original_cache, frame, *, started):
    """One complete original full-byte admission before physical drainage."""
    names = {'schema_version','kind','nonce','epoch','binding_sha256','backend_claim_sha256','request_id','budget_ms'}
    proof = original_cache['proof']
    if (type(frame) is not dict or set(frame) != names or type(frame['schema_version']) is not int
            or frame['schema_version'] != 1 or frame['kind'] != 'backend_shutdown_prepare'
            or frame['nonce'] != proof['nonce'] or frame['epoch'] != proof['epoch']
            or frame['binding_sha256'] != proof['binding_sha256']
            or frame['backend_claim_sha256'] != hashlib.sha256(_canonical(proof)).hexdigest()
            or not _hex(frame['request_id'], 32) or type(frame['budget_ms']) is not int
            or not 0 < frame['budget_ms'] <= 30000 or type(started) not in (int,float)
            or not math.isfinite(started) or started > time.monotonic()
            or original_cache.get('shutdown_prepare_attempt') is not None):
        raise HandshakeError('Original backend shutdown preparation differs or is replayed')
    deadline = started+frame['budget_ms']/1000
    context = _backend_shutdown_endpoint(original_cache)
    queue = original_cache['preflight_queue']; source_queue = original_cache.get('source_cpu_queue')
    if (queue._active is not None or source_queue is not None and source_queue._active is not None
            or original_cache['admission']._closed or queue._closed_deadline is not None
            or original_cache.get('preflight_drain_deadline') is not None
            or original_cache['writer_guard'] is None or original_cache['writer_handle'] is None
            or original_cache['writer_private_fd'] is None):
        raise HandshakeError('Original backend shutdown preparation has unresolved/closed work')
    from backend.engine.migration_guard import _admission_capability
    root = original_cache['root']; context_record = original_cache['context']
    sock = original_cache['socket']; challenge = original_cache['challenge']
    admission = original_cache['admission']; writer_guard = original_cache['writer_guard']
    handle = original_cache['writer_handle']; pass_fds = handle.pass_fds
    private_fd = original_cache['writer_private_fd']; fd_identity = original_cache['writer_fd_identity']
    if type(pass_fds) is not tuple or len(pass_fds) != 1:
        raise HandshakeError('Original backend writer lifetime descriptor set differs')
    writer_fds = tuple(_shutdown_fd(fd) for fd in pass_fds); private_identity = _shutdown_fd(private_fd)
    if writer_fds[0][1:3] != fd_identity or private_identity[1:3] != fd_identity:
        raise HandshakeError('Original backend writer descriptor differs from admitted identity')
    original_proof = _canonical(proof); original_challenge = _canonical(challenge)
    lifespan = _admission_capability(root, exclusive=False)
    if lifespan is None: raise HandshakeError('Original backend shared lifespan is unavailable')
    pid = os.getpid(); thread = threading.current_thread(); parent = os.getppid()
    session = os.getsid(0); process_group = os.getpgrp()
    attempt = object(); original_cache['shutdown_prepare_attempt'] = attempt
    def original():
        current = _backend_shutdown_endpoint(original_cache)
        if (os.getpid() != pid or threading.current_thread() is not thread or os.getppid() != parent
                or os.getsid(0) != session or os.getpgrp() != process_group
                or original_cache['root'] is not root or original_cache['context'] is not context_record
                or original_cache['socket'] is not sock or original_cache['preflight_queue'] is not queue
                or original_cache['admission'] is not admission or original_cache['proof'] is not proof
                or original_cache['challenge'] is not challenge or original_cache['writer_guard'] is not writer_guard
                or original_cache['writer_handle'] is not handle or handle.pass_fds != pass_fds
                or original_cache['writer_private_fd'] != private_fd or original_cache['writer_fd_identity'] != fd_identity
                or original_cache.get('source_cpu_queue') is not source_queue
                or original_cache.get('shutdown_prepare_attempt') is not attempt
                or _canonical(proof) != original_proof or _canonical(challenge) != original_challenge
                or _admission_capability(root, exclusive=False) is not lifespan
                or tuple(_shutdown_fd(fd) for fd in pass_fds) != writer_fds
                or _shutdown_fd(private_fd) != private_identity or time.monotonic() >= deadline):
            raise HandshakeError('Original backend preparation custody changed or completed late')
        return current
    original()
    from backend.engine.application_shutdown_transaction import asset_custody
    asset_error = None
    try: assets_before = asset_custody(root, challenge['binding'])
    except BaseException as exc: asset_error = exc; assets_before = None
    fresh = _validate(root, context[1], challenge, absolute_deadline=deadline, before_acquire=original)
    original()
    if fresh != proof:
        raise HandshakeError('Original backend shutdown full admission changed')
    assets = asset_custody(root, challenge['binding'])
    if asset_error is not None or assets != assets_before:
        raise HandshakeError('Original backend preparation asset custody changed') from asset_error
    startup_receipts = admission.prepare_startup_threads(deadline)
    original()
    _finish_fresh_startup_observation(original_cache, deadline, startup_receipts)
    original()
    controls = _backend_shutdown_controls(original_cache)
    original()
    if (controls['row']['writer_drain']['phase'] != 'enrolled'
            or controls['epoch']['registry']['state'] != 'open'):
        raise HandshakeError('Original backend preparation requires the open enrolled writer epoch')
    mint = object(); _BACKEND_SHUTDOWN_MINTS.add(mint)
    try: capability = _BackendShutdown(_mint=mint)
    finally: _BACKEND_SHUTDOWN_MINTS.discard(mint)
    _BACKEND_SHUTDOWNS[capability] = {'pid': pid, 'thread': thread, 'parent_pid': parent,
        'session': session, 'process_group': process_group,
        'cache': original_cache, 'root': original_cache['root'], 'context': original_cache['context'],
        'socket': original_cache['socket'], 'queue': queue, 'source_queue': source_queue,
        'admission': original_cache['admission'], 'lifespan': lifespan,
        'proof_record': proof, 'challenge_record': original_cache['challenge'],
        'proof': original_proof, 'challenge': original_challenge,
        'writer_guard': original_cache['writer_guard'], 'writer_handle': handle, 'pass_fds': handle.pass_fds,
        'writer_fds': writer_fds,
        'private_fd': private_fd, 'private_identity': private_identity,
        'writer_fd_identity': original_cache['writer_fd_identity'], 'assets': assets,
        'controls': copy.deepcopy(controls), 'prepare': json.loads(_canonical(frame)),
        'deadline': deadline, 'phase': 'prepared', 'failed': False, 'sent': False, 'attempt': attempt}
    capability.fresh(deadline)
    return capability


def _write_final_diagnostic_descriptor(fd, data):
    """One nonblocking attempt on an existing descriptor; never a destination.

    Production supplies only inherited FD2. The explicit descriptor is the
    source-test OS transport seam, not an application argument or capability.
    """
    duplicate = None; expected = None; success = False
    try:
        import fcntl
        import signal
        import stat
        if type(fd) is not int or fd < 0 or type(data) is not bytes or not 0 < len(data) <= 8192:
            return False

        def pin(number):
            value = os.fstat(number)
            # Darwin socket permission bits reflect readiness; retain the same
            # kernel-object/type identity as the original stdin adapter.
            return ((value.st_dev, value.st_ino,
                stat.S_IFMT(value.st_mode) if stat.S_ISSOCK(value.st_mode) else value.st_mode, value.st_nlink,
                value.st_uid, value.st_gid), fcntl.fcntl(number, fcntl.F_GETFL))

        expected = pin(fd)
        mode = expected[0][2]; flags = expected[1]
        if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)): return False
        # A per-call socket flag did not prove bounded on a full Darwin stream.
        # Never change an inherited OFD or attempt any blocking descriptor.
        if not flags & os.O_NONBLOCK: return False
        if signal.getsignal(signal.SIGPIPE) != signal.SIG_IGN: return False
        duplicate = os.dup(fd)
        if pin(fd) != expected or pin(duplicate) != expected: return False
        written = os.write(duplicate, data)  # Exactly one already-nonblocking attempt.
        current = pin(fd)
        # Darwin exposes FWASWRITTEN in F_GETFL after raw write. Preserve raw
        # values and classify only this documented 0->1 kernel-history change;
        # every access/nonblock/other bit and object identity must still match.
        if (sys.platform == 'darwin' and not flags & 0x10000
                and current == (expected[0], flags | 0x10000)):
            expected = current
        success = written == len(data) and pin(fd) == expected and pin(duplicate) == expected
    except Exception:
        success = False
    finally:
        if duplicate is not None:
            try:
                current = pin(duplicate)
                if current[0] != expected[0]: success = False
                else:
                    if current != expected: success = False
                    os.close(duplicate)  # Only this call's checked duplicate.
            except Exception: success = False
        if expected is not None:
            try:
                if pin(fd) != expected: success = False
            except Exception: success = False
    return success


def _emit_final_drain_diagnostic(admission, receipt):
    """Optional stderr sibling only; never an authority frame or a new request."""
    try:
        from backend.engine.original_request_activity import OriginalRequestActivityObserver
        observer = admission._activity_observer
        if type(observer) is not OriginalRequestActivityObserver: return False
        value = observer.final_drain_diagnostic(admission)
        if value is None: return False
        value.update(capture_phase='after_original_receipt_send',
            receipt_sha256=hashlib.sha256(_canonical(receipt)).hexdigest())
        try:
            origin=observer.first_unsupported_origin_diagnostic()
            if (origin is not None and origin['sequence']==value['sequence']
                    and origin['request_count']==value['request_count']):
                candidate=dict(value,first_unsupported_origin=origin)
                encoded='OWNED_FINAL_DRAIN_DIAGNOSTIC '+json.dumps(candidate,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n'
                if len(encoded.encode('utf-8'))<=8*1024:value=candidate
        except Exception:pass  # Optional data cannot suppress an old bounded packet.
        line = 'OWNED_FINAL_DRAIN_DIAGNOSTIC '+json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n'
        if len(line.encode('utf-8')) > 8 * 1024: return False
        return _write_final_diagnostic_descriptor(2, line.encode('utf-8'))
    except Exception:
        return False  # Missing/partial diagnostics are unavailable, never success.


def backend_execution_service(stop_event=None):
    """Consume at most one controller-origin CPU request after admitted ready.

    Idle waiting has no bootstrap deadline. A request has a hard worker budget;
    EOF, replay or an interrupted execution closes the epoch instead of retrying.
    The daemon caller retains its shared installation lifespan throughout.
    """
    if _CACHE is None or not _CACHE['ready']:raise HandshakeError('CPU consumer requires original admitted backend readiness')
    from backend.engine.migration_guard import maintenance_guard
    from backend.engine.application_launch_execution import execute_backend
    original_cache = _CACHE
    root=original_cache['root'];sock=original_cache['socket'];proof=original_cache['proof'];executed=False;drained=False
    from backend.engine.application_preflight_child_relay import BackendRelayQueue
    queue = _CACHE['preflight_queue']
    if type(queue) is not BackendRelayQueue: raise HandshakeError('Original backend sole reader queue differs')
    queue.claim_reader(); pending = None; cpu = None; drain = None; closing_deadline = None; closing_unsupported = None
    shutdown = None; closing_status = None
    from backend.engine.application_owned_cpu_child_relay import CpuRelayQueue
    source_queue=original_cache.get('source_cpu_queue'); source_pending=None
    if source_queue is not None:
        if type(source_queue) is not CpuRelayQueue:raise HandshakeError('Original SOURCE CPU sole-reader queue differs')
        source_queue.claim_reader()
    original_admission = original_cache['admission']
    original_context = original_cache['context']
    original_proof = _canonical(proof); original_challenge = _canonical(original_cache['challenge'])
    original_proof_record = proof; original_challenge_record = original_cache['challenge']

    def closing_current():
        # The post-send terminal receipt permits only bounded local stopping.
        # It cannot authorize another frame, repair the owner, clear unsupported
        # work, or establish any writer/tree/lease acceptance.
        context = _root_context()
        if (type(stop_event) is not threading.Event or _CACHE is not original_cache
                or context is None or context[0] != root or _context() != original_context
                or original_cache['context'] != original_context or not original_cache['ready']
                or original_cache['root'] != root or original_cache['socket'] is not sock
                or original_cache['preflight_queue'] is not queue
                or original_cache['admission'] is not original_admission
                or _canonical(original_cache['proof']) != original_proof
                or _canonical(original_cache['challenge']) != original_challenge
                or original_cache.get('preflight_drain_deadline') != closing_deadline
                or queue._closed_deadline != closing_deadline):
            raise HandshakeError('Original terminal service closing context changed')
        queue._fresh(sock, reader=True)
        if shutdown is not None: shutdown.fresh(closing_deadline)
        if time.monotonic() >= closing_deadline:
            raise HandshakeError('Original terminal service closing deadline expired')
        snapshot = original_admission.snapshot()
        if (not original_admission._closed or not set(closing_unsupported).issubset(snapshot['unsupported'])
                or pending is not None or cpu is not None or drain is not None
                or queue._active is not None
                or snapshot['active_scopes'] != 0 and (shutdown is None or closing_status != 'refused')):
            raise HandshakeError('Original terminal service closing still has accepted work')
        if source_queue is not None:
            source_queue._fresh(sock,reader=True)
            if (original_cache.get('source_cpu_queue') is not source_queue or source_pending is not None
                    or source_queue._active is not None or source_queue._closed_deadline!=closing_deadline):
                raise HandshakeError('Original terminal SOURCE CPU closing context changed')

    try:
        with maintenance_guard(root):
            while True:
                if closing_deadline is not None:
                    closing_current()
                    if stop_event.is_set():
                        closing_current()
                        return
                    stop_event.wait(min(.02, closing_deadline-time.monotonic()))
                    closing_current()
                    continue
                if stop_event is not None and stop_event.is_set(): return
                if shutdown is not None and (pending is not None or source_pending is not None or cpu is not None
                        or queue._active is not None or source_queue is not None and source_queue._active is not None):
                    raise HandshakeError('Prepared shutdown refuses unresolved accepted exchanges without clearing them')
                context=_root_context()
                if _CACHE is not original_cache or context is None or _context()!=original_cache['context']:raise HandshakeError('CPU backend process context changed')
                # Idle polling holds no publication mutex. Exact endpoint and
                # context remain fresh; full binding reads surround each real
                # authenticated channel action inside its original deadline.
                queue._fresh(sock, reader=True)
                if source_queue is not None:source_queue._fresh(sock,reader=True)
                if pending is None:
                    pending = queue.take(sock)
                    if pending is not None:
                        outgoing, deadline = queue.outgoing(pending)
                        if (time.monotonic() >= deadline or drained and outgoing['action'] != 'finish'):
                            raise HandshakeError('Original preflight exchange is late or admission is closed')
                        deadline = min(deadline, _CACHE.get('preflight_drain_deadline', float('inf')))
                        proof = _validate_service_action(original_cache, deadline)
                        send_frame(sock, outgoing, absolute_deadline=deadline)
                        _validate_service_action(original_cache, deadline)
                if source_queue is not None and source_pending is None:
                    source_pending=source_queue.take(sock)
                    if source_pending is not None:
                        outgoing,deadline=source_queue.outgoing(source_pending)
                        if drained and outgoing['action']!='finish':raise HandshakeError('Original SOURCE CPU admission closed')
                        deadline=min(deadline,original_cache.get('preflight_drain_deadline',float('inf')))
                        _validate_service_action(original_cache,deadline);source_queue._fresh(sock,reader=True)
                        send_frame(sock,outgoing,absolute_deadline=deadline)
                        _validate_service_action(original_cache,deadline);source_queue._fresh(sock,reader=True)
                if source_queue is not None:
                    completed=source_queue.completion_outgoing()
                    if completed is not None:
                        deadline=source_queue.action_deadline()
                        _validate_service_action(original_cache,deadline);send_frame(sock,completed,absolute_deadline=deadline)
                        _validate_service_action(original_cache,deadline);source_queue._fresh(sock,reader=True)
                if cpu is not None and cpu['done'].is_set():
                    if cpu['error'] is not None: raise HandshakeError('Original CPU execution callback failed') from cpu['error']
                    deadline=cpu['deadline'] if source_queue is None else min(cpu['deadline'],source_queue.action_deadline())
                    completed=cpu['result'] if source_queue is None else source_queue.settlement_outgoing()
                    proof = _validate_service_action(original_cache, deadline)
                    send_frame(sock, completed, absolute_deadline=deadline)
                    _validate_service_action(original_cache, deadline); cpu = None
                if drain is not None:
                    # Keep this sole reader available for an already accepted
                    # child's final ACK while admission waits. One original
                    # deadline; no second socket reader or refreshed budget.
                    snapshot = _CACHE['admission'].snapshot()
                    # Count release may finish between the done check above
                    # and this snapshot. The retained SOURCE callback still
                    # owes its exact settlement before a clean drain receipt.
                    source_callback_pending = source_queue is not None and cpu is not None
                    if ((not snapshot['active_scopes'] and not source_callback_pending)
                            or snapshot['unsupported'] or time.monotonic() >= drain['deadline']):
                        completed = {'schema_version': 1, 'kind': 'backend_managed_drain', 'request': drain['frame'],
                            'backend_proof': proof, 'status': 'refused' if snapshot['active_scopes'] or snapshot['unsupported'] else 'managed_scopes_drained',
                            **snapshot, 'scope': 'reviewed_foreground_scopes_only', 'whole_writer_coverage': False,
                            'process_tree_exit_verified': False, 'can_release_launch_lease': False}
                        proof = (shutdown.fresh(drain['deadline']) if shutdown is not None
                                 else _validate_service_action(original_cache, drain['deadline']))
                        validate_drain_receipt(completed, drain['frame'], backend_process=proof['process'])
                        send_frame(sock, completed, absolute_deadline=drain['deadline'])
                        (shutdown.fresh(drain['deadline']) if shutdown is not None
                         else _validate_service_action(original_cache, drain['deadline']))
                        closing_status = completed['status']
                        closing_deadline = drain['deadline']; closing_unsupported = tuple(completed['unsupported']); drain = None
                        closing_current()
                        _emit_final_drain_diagnostic(original_admission, completed)
                        continue
                if pending is not None:
                    _, deadline = queue.outgoing(pending)
                    if time.monotonic() >= deadline: raise HandshakeError('Original preflight exchange deadline expired')
                if source_pending is not None:
                    _,deadline=source_queue.outgoing(source_pending)
                    if time.monotonic()>=deadline:raise HandshakeError('Original SOURCE CPU exchange deadline expired')
                wait = .02 if pending is not None or source_pending is not None or cpu is not None or drain is not None else .2
                if not select.select([sock],[],[],wait)[0]:continue
                budget = 10
                if pending is not None: budget = min(budget, queue.outgoing(pending)[1]-time.monotonic())
                if source_pending is not None:budget=min(budget,source_queue.outgoing(source_pending)[1]-time.monotonic())
                if source_queue is not None and cpu is not None:budget=min(budget,source_queue.action_deadline()-time.monotonic())
                if drain is not None: budget = min(budget, drain['deadline']-time.monotonic())
                if budget <= 0: raise HandshakeError('Original private service request deadline expired')
                deadline = time.monotonic()+budget
                original_frame_deadline = deadline
                incoming_started = time.monotonic()
                if (shutdown is None and not drained
                        and _peek_backend_shutdown_prepare(sock, original_frame_deadline,
                            lambda: _backend_shutdown_endpoint(original_cache))):
                    _backend_shutdown_endpoint(original_cache)
                    frame = read_frame(sock, original_frame_deadline-time.monotonic())
                    _backend_shutdown_endpoint(original_cache)
                    if pending is not None or source_pending is not None or cpu is not None:
                        raise HandshakeError('Original preparation refuses pending work without clearing it')
                    shutdown = _prepare_backend_shutdown(original_cache, frame, started=incoming_started)
                    send_frame(sock, shutdown.prepared_frame(), absolute_deadline=shutdown.deadline)
                    shutdown.fresh(shutdown.deadline)
                    continue
                initial_cpu_deadline = None; frozen_initial_cpu = False
                if shutdown is not None:
                    deadline = min(deadline, shutdown.deadline)
                    proof = shutdown.fresh(deadline)
                    frame = read_frame(sock, deadline-time.monotonic())
                    proof = shutdown.fresh(deadline)
                    if frame.get('kind') != 'backend_drain_request':
                        raise HandshakeError('Prepared shutdown accepts only its original drain gate')
                else:
                    initial_cpu_deadline = None
                    frozen_initial_cpu = (getattr(sys, 'frozen', False) is True
                        and original_cache['proof'].get('frozen') is True
                        and source_queue is None and original_cache.get('source_cpu_queue') is None
                        and pending is None and source_pending is None and cpu is None and drain is None
                        and not executed and not drained
                        and original_cache.get('original_cpu_execution') is None
                        and original_cache.get('preflight_drain_deadline') is None)
                    if frozen_initial_cpu:
                        # One original CPU action fence includes both full fresh
                        # binding reads. Transport alone retains its separate 10s
                        # bound; no proof or deadline is adopted from a frame.
                        initial_cpu_deadline = time.monotonic()+210
                        def initial_cpu_current():
                            current = _root_context()
                            if (_CACHE is not original_cache or current is None or current[0] != root
                                    or original_cache['root'] is not root or _context() != original_context
                                    or original_cache['context'] is not original_context
                                    or not original_cache['ready'] or original_cache['socket'] is not sock
                                    or original_cache['preflight_queue'] is not queue
                                    or original_cache['admission'] is not original_admission
                                    or original_cache['proof'] is not original_proof_record
                                    or original_cache['challenge'] is not original_challenge_record
                                    or _canonical(original_cache['proof']) != original_proof
                                    or _canonical(original_cache['challenge']) != original_challenge
                                    or getattr(sys, 'frozen', False) is not True
                                    or original_cache['proof'].get('frozen') is not True
                                    or original_cache.get('source_cpu_queue') is not None
                                    or source_queue is not None or pending is not None or source_pending is not None
                                    or cpu is not None or drain is not None or executed or drained
                                    or original_cache.get('original_cpu_execution') is not None
                                    or original_cache.get('preflight_drain_deadline') is not None):
                                raise HandshakeError('Original initial frozen CPU service context changed')
                            queue._fresh(sock, reader=True)
                            if time.monotonic() >= initial_cpu_deadline:
                                raise HandshakeError('Original initial frozen CPU action deadline expired')
                        initial_cpu_current()
                        deadline = initial_cpu_deadline
                    proof = _validate_service_action(original_cache, deadline)
                    if source_queue is not None:source_queue._fresh(sock,reader=True)
                    read_deadline = deadline
                    if frozen_initial_cpu:
                        initial_cpu_current()
                        read_deadline = min(time.monotonic()+10, initial_cpu_deadline)
                    frame=read_frame(sock,read_deadline-time.monotonic())
                    if frozen_initial_cpu:
                        if time.monotonic() >= read_deadline:
                            raise HandshakeError('Original initial frozen CPU frame completed late')
                        initial_cpu_current()
                    proof = _validate_service_action(original_cache, deadline)
                    if source_queue is not None:source_queue._fresh(sock,reader=True)
                    if frozen_initial_cpu:
                        initial_cpu_current()
                        if frame.get('kind') != 'cpu_execution_request' and time.monotonic() >= original_frame_deadline:
                            raise HandshakeError('Original non-CPU private service request completed late')
                if frame.get('kind') == 'controller_preflight_reply':
                    if pending is None: raise HandshakeError('Original preflight reply is unsolicited or replayed')
                    queue.complete(pending, frame); pending = None
                    continue
                if frame.get('kind')=='controller_cpu_child_reply':
                    if source_queue is None or source_pending is None:raise HandshakeError('Original SOURCE CPU reply unsolicited or replayed')
                    source_queue.complete(source_pending,frame);source_pending=None
                    continue
                if frame.get('kind')=='controller_cpu_publication_ack':
                    if source_queue is None:raise HandshakeError('Original SOURCE CPU publication unavailable')
                    source_queue.deliver_publication(frame)
                    continue
                if frame.get('kind') == 'backend_drain_request':
                    if drained:raise HandshakeError('Managed drain request replay requires recovery')
                    started = incoming_started if shutdown is not None else time.monotonic()
                    if shutdown is not None: shutdown.begin_drain(frame, started=started)
                    writer = _CACHE['challenge'].get('writer')
                    if writer is None or _CACHE['writer_guard'] is None:
                        raise HandshakeError('Managed drain requires original retained writer admission')
                    validate_drain_request(frame, nonce=proof['nonce'], epoch=proof['epoch'], binding=_CACHE['challenge']['binding'],
                        writer_id=writer['writer_id'], registration_sha256=writer['registration_sha256'])
                    if frame['backend_claim_sha256'] != hashlib.sha256(_canonical(proof)).hexdigest():
                        raise HandshakeError('Managed drain original backend claim changed')
                    from backend.engine.application_launch_quiescence import inspect_epoch
                    snapshot = inspect_epoch(root, frame['nonce'])
                    if snapshot['registry']['state'] != 'closed' or snapshot['registry_sha256'] != frame['closed_registry_sha256']:
                        raise HandshakeError('Managed drain requires original closed writer epoch')
                    state = _CACHE['admission']; state.close(); drained = True
                    drain = {'frame': frame, 'deadline': started+frame['budget_ms']/1000}
                    _CACHE['preflight_drain_deadline'] = drain['deadline']
                    with state._condition:
                        named = state._current_project_requests
                        if named is not None:
                            named.deadline = drain['deadline'] if named.deadline is None else min(named.deadline, drain['deadline'])
                    queue.close_admission(drain['deadline'])
                    if source_queue is not None:source_queue.close_admission(drain['deadline'])
                else:
                    if executed or drained:raise HandshakeError('CPU private request replay or closed admission requires recovery')
                    executed=True
                    cpu = {'done': threading.Event(), 'result': None, 'error': None, 'deadline':
                        initial_cpu_deadline if frozen_initial_cpu and frame.get('kind') == 'cpu_execution_request' else time.monotonic()+210}
                    execution = cpu
                    def work(request=frame, authenticated=proof, state=execution):
                        try: state['result'] = execute_backend(request,authenticated,root)
                        except BaseException as exc:
                            _CACHE['admission'].uncovered('cpu_producer_unconfirmed'); state['error'] = exc
                        finally: state['done'].set()
                    execution['thread'] = threading.Thread(target=work, name='owned-controller-cpu', daemon=True)
                    _CACHE['original_cpu_execution'] = execution  # Strong retained original callback, never receipt adoption.
                    execution['thread'].start()
    except BaseException:
        queue.abandon(HandshakeError('Original backend private service is unresolved'))
        if source_queue is not None:source_queue.abandon(HandshakeError('Original SOURCE CPU private service unresolved'))
        original_admission.uncovered('cpu_producer_unconfirmed')
        # Closing this original endpoint tells main/controller to retain durable
        # recovery. It never proves worker descendants exited or clears a lease.
        try:sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        sock.close()
        raise
