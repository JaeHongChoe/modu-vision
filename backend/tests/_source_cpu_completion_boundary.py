"""One test-owned SOURCE completion boundary; never an execution capability."""
import ast
from contextlib import contextmanager
import dis
import hashlib
import inspect
import json
import math
from pathlib import Path
import sys
import threading
import time


CONFIG = 'source-cpu-fixture-boundary.json'
READY = 'source-cpu-completion-ready.json'
PENDING = 'source-cpu-completion-pending.json'


class _CodeStarts:
    """Own only vacant monitoring slots and these exact Python code starts."""
    def __init__(self):
        self.tool = None
        self.name = 'source-cpu-fixture-' + hex(id(self))
        self.codes = []
        monitoring = getattr(sys, 'monitoring', None)
        if monitoring is None:
            return
        for tool in range(5, -1, -1):
            if monitoring.get_tool(tool) is None:
                try:
                    monitoring.use_tool_id(tool, self.name)
                except ValueError:
                    continue
                self.tool = tool
                break

    def owned(self):
        return self.tool is not None and sys.monitoring.get_tool(self.tool) == self.name

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        self.close(error)

    def arm(self, codes, callback):
        assert self.owned()
        self.codes = list(dict.fromkeys(codes))
        try:
            sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, callback)
            for code in self.codes:
                sys.monitoring.set_local_events(self.tool, code, sys.monitoring.events.PY_START)
        except BaseException as error:
            self.close(error)
            raise

    def remove(self, code):
        if self.owned() and code in self.codes:
            sys.monitoring.set_local_events(self.tool, code, 0)
            self.codes.remove(code)

    def close(self, primary=None):
        if not self.owned():
            return
        errors = []
        for operation in [*(lambda code=code: sys.monitoring.set_local_events(self.tool, code, 0)
                            for code in self.codes),
                          lambda: sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, None),
                          lambda: sys.monitoring.free_tool_id(self.tool)]:
            try:
                if self.owned(): operation()
            except BaseException as error:
                errors.append(error)
        self.codes.clear()
        if errors and primary is None:
            raise errors[0]


def _restore_profile(profile, previous, primary=None):
    try:
        if sys.getprofile() is profile: sys.setprofile(previous)
    except BaseException:
        if primary is None: raise


@contextmanager
def backend_observer(projects, original_profile, factory):
    """Arm the original profile only inside the selected real validator body.

    Source inventory/copy, cold child imports and receipt checks are ordinary
    runtime work. A fixture must not install Python callbacks on those frames.
    Unknown retained profiles keep the original all-event compatibility path.
    """
    previous = sys.getprofile()
    profile = factory(projects, original_profile)
    boundary = getattr(profile, 'boundary', profile)
    guard_code = getattr(original_profile, '_fixture_guard_code', None)
    sparse = (previous is None and hasattr(boundary, 'action')
              and (original_profile is None or guard_code is not None))
    monitor = _CodeStarts() if sparse else None
    if monitor is None or not monitor.owned():
        sys.setprofile(profile)
        try:
            yield
        finally:
            _restore_profile(profile, previous, sys.exc_info()[1])
        return
    with monitor:
        from backend.engine import application_launch_handshake as h
        validator = h._validate_service_action.__code__
        service = h.backend_execution_service.__code__
        post_line = completion_post_line(h.backend_execution_service)
        thread = threading.get_ident()

        def retire():
            _restore_profile(profile, previous)
            monitor.remove(validator)
            if guard_code is None:
                monitor.close()

        boundary.retire = retire
        def start(code, offset):
            if threading.get_ident() != thread:
                return
            current = sys.getprofile()
            if current is profile:
                return  # The selected genuine validator already owns its profile.
            if current is not previous:
                monitor.close()  # Never adopt or replace an external profile.
                return
            frame = sys._getframe(1)
            if code is guard_code:
                try:
                    original_profile(frame, 'call', None)
                except BaseException as error:
                    if original_profile._fixture_guard_consumed: monitor.close(error)
                    raise
                if original_profile._fixture_guard_consumed: monitor.close()
                return
            caller = frame.f_back
            if caller is None or caller.f_code is not service or caller.f_lineno != post_line:
                return
            sys.setprofile(profile)
            try:
                # PY_START is already inside this frame; CPython's earlier profile
                # call was not installed. Deliver that one genuine entry explicitly.
                profile(frame, 'call', None)
                profile._sparse_entry = frame
            except BaseException as error:
                _restore_profile(profile, previous, error)
                monitor.close(error)
                raise
            if boundary.action is not frame and sys.getprofile() is profile:
                sys.setprofile(previous)
        codes = [validator] + ([] if guard_code is None else [guard_code])
        monitor.arm(codes, start)
        primary = None
        try:
            yield
        except BaseException as error:
            primary = error
            raise
        finally:
            _restore_profile(profile, previous, primary)
            monitor.close(primary)


@contextmanager
def controller_dispatch(readiness, codes, *, enabled=True):
    """Dispatch the same original controller callbacks only at their code starts."""
    if not enabled or readiness is None or readiness.upstream is not None:
        yield
        return
    monitor = _CodeStarts()
    if not monitor.owned():
        yield
        return
    with monitor:
        previous = readiness.upstream
        active = sys.getprofile()
        assert active is readiness.installed
        thread = threading.get_ident()
        def start(code, offset):
            nonlocal active
            if threading.get_ident() != thread or active is None:
                return
            if sys.getprofile() is not previous:
                active = None
                monitor.close()
                return
            frame = sys._getframe(1)
            callback = active
            sys.setprofile(callback)
            try:
                callback(frame, 'call', None)
                callback._sparse_entry = frame
            except BaseException as error:
                _restore_profile(callback, previous, error)
                active = None
                monitor.close(error)
                raise
            current = sys.getprofile()
            if current is readiness.installed:
                active = current
                sys.setprofile(previous)
            else:
                active = None
                monitor.close()
        monitor.arm(codes, start)
        sys.setprofile(previous)
        primary = None
        try:
            yield
        except BaseException as error:
            primary = error
            raise
        finally:
            if active is not None: _restore_profile(active, previous, primary)
            monitor.close(primary)


def read(projects, name):
    raw = (projects / name).read_bytes()
    assert len(raw) <= 4096, 'Fixture boundary observation exceeds its bound'
    return json.loads(raw)


def write(projects, name, value):
    from backend.tests.test_application_launch_execution import _atomic_fixture_json
    _atomic_fixture_json(projects / name, value, exclusive=True, sort_keys=True)


def configuration(projects):
    """Configured means a complete original bound, never a legacy fallback."""
    value = read(projects, CONFIG)
    assert type(value) is dict and set(value) in ({'token', 'root', 'deadline'}, {'token', 'root', 'deadline', 'contention'})
    assert type(value['token']) is str and value['token']
    assert value['root'] == str(projects.parent)
    deadline = value['deadline']
    assert type(deadline) in (int, float) and math.isfinite(deadline)
    assert time.monotonic() < deadline, 'SOURCE completion readiness exceeded its original deadline'
    return value


def request_hash(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def normal_return(frame, event):
    # CPython also emits a return profile event during exception unwinding.
    return event == 'return' and dis.opname[frame.f_code.co_code[frame.f_lasti]] in {'RETURN_VALUE', 'RETURN_CONST'}


def selected(*, actual_code, code, actual_thread, thread, actual_cache, cache,
             actual_queue, queue, request, retained_request, token, retained_token,
             completion_sent, phase, deadline, now, actual_callsite, callsite,
             actual_root, root, nonce):
    return (actual_code is code and actual_thread == thread and actual_cache is cache
            and actual_queue is queue and request == retained_request and token == retained_token
            and actual_root == root and request.get('nonce') == nonce
            and actual_callsite == callsite and completion_sent is True and phase == 'waiting_publication'
            and type(deadline) in (int, float) and math.isfinite(deadline) and now < deadline)


def completion_post_line(service):
    source, first = inspect.getsourcelines(service)
    for node in ast.walk(ast.parse(''.join(source))):
        if isinstance(node, ast.If) and ast.unparse(node.test) == 'completed is not None':
            calls = [s for s in node.body if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
                     and isinstance(s.value.func, ast.Name) and s.value.func.id == '_validate_service_action']
            assert len(calls) == 2
            return first + calls[1].lineno - 1
    raise AssertionError('Original completion post-send branch not found')


def backend_profile(projects, original_profile):
    from backend.engine import application_launch_handshake as h, application_owned_cpu_child_relay as cpu
    metadata = configuration(projects)
    thread = threading.get_ident(); cache = h._CACHE; queue = cache['source_cpu_queue']
    root = cache['root']; nonce = cache['proof']['nonce']; proof = cache['proof']
    assert Path(metadata['root']) == root
    code = h._validate_service_action.__code__; service = h.backend_execution_service.__code__
    post_line = completion_post_line(h.backend_execution_service)
    done = False

    def context(action):
        caller = action.f_back
        if caller is None or caller.f_code is not service or caller.f_lineno != post_line:
            return None
        completed = caller.f_locals.get('completed')
        if type(completed) is not dict: return None
        state = cpu._PRODUCERS.get(cpu._CACHE_TICKETS.get(id(cache)))
        if state is None: return None
        deadline = action.f_locals.get('deadline')
        current = read(projects, CONFIG)
        if not selected(actual_code=action.f_code, code=code,
                actual_thread=threading.get_ident(), thread=thread,
                actual_cache=action.f_locals.get('original_cache'), cache=cache,
                actual_queue=caller.f_locals.get('source_queue'), queue=queue,
                request=completed.get('request', {}), retained_request=state['frame'],
                token=current['token'], retained_token=metadata['token'],
                completion_sent=state.get('completion_sent'), phase=state['phase'],
                deadline=deadline, now=time.monotonic(), actual_callsite=caller.f_lineno, callsite=post_line,
                actual_root=cache['root'], root=root, nonce=nonce): return None
        assert current == metadata and h._CACHE is cache and cache['source_cpu_queue'] is queue
        assert state['cache'] is cache and state['cpu_queue'] is queue and completed == state['completion']
        assert deadline == queue.action_deadline() and time.monotonic() < metadata['deadline']
        return state, deadline

    def profile(frame, event, arg):
        nonlocal done
        if event == 'call' and getattr(profile, '_sparse_entry', None) is frame: return
        if original_profile is not None: original_profile(frame, event, arg)
        # The retained drain guard may deliberately clear profiling itself.
        if sys.getprofile() is not profile.installed or done: return
        if event == 'call' and frame.f_code is code:
            picked = context(frame)
            if picked is not None:
                profile.action = frame
                profile.request = picked[0]['frame'].copy()
                profile.state = picked[0]
                profile.request_sha256 = request_hash(profile.request)
                profile.deadline = picked[1]
                write(projects, PENDING, {'token': metadata['token'], 'root': metadata['root'],
                    'nonce': profile.request['nonce'], 'request_sha256': request_hash(profile.request),
                    'deadline': min(profile.deadline, metadata['deadline'])})
        if frame is profile.action and event == 'return':
            done = True
            if normal_return(frame, event):
                picked = context(frame)
                assert picked is not None, 'Original completion context changed before normal return'
                state, deadline = picked
                assert state is profile.state and request_hash(state['frame']) == profile.request_sha256
                assert state['frame'] == profile.request and deadline == profile.deadline
                assert arg == proof and cache['proof'] is proof
                write(projects, READY, {'token': metadata['token'], 'root': metadata['root'],
                    'nonce': state['frame']['nonce'], 'request_sha256': request_hash(state['frame']),
                    'deadline': min(deadline, metadata['deadline']), 'normal_return': True,
                    'time': time.monotonic()})
                # The outer contention hook shares this one-shot ownership.
                if sys.getprofile() is profile.installed:
                    if profile.retire is None: sys.setprofile(original_profile)
                    else: profile.retire()
            profile.action = None
    profile.action = None
    profile.installed = profile
    profile.retire = None
    return profile


def wait_ready(projects, metadata, request):
    assert Path(metadata['root']) == projects.parent
    deadline = metadata['deadline']
    assert type(deadline) in (int, float) and math.isfinite(deadline)
    pending = None
    while time.monotonic() < deadline:
        name = PENDING if pending is None else READY
        if (projects / name).exists():
            value = read(projects, name)
            assert value['token'] == metadata['token'] and value['root'] == metadata['root']
            assert value['nonce'] == request['nonce'] and value['request_sha256'] == request_hash(request)
            action_deadline = value['deadline']
            assert type(action_deadline) in (int, float) and math.isfinite(action_deadline)
            assert action_deadline <= deadline and time.monotonic() < action_deadline
            if pending is None:
                pending = value
                deadline = action_deadline
                continue
            assert value['normal_return'] is True and value['time'] < action_deadline
            assert action_deadline == pending['deadline']
            return value
        time.sleep(min(.005, max(0, deadline - time.monotonic())))
    raise AssertionError('SOURCE completion readiness exceeded its original deadline')


@contextmanager
def controller_boundary(owner, original_code):
    from backend.engine import application_launch_execution as execution
    projects = owner.root / 'projects'
    if not (projects / CONFIG).exists():
        yield None
        return
    metadata = configuration(projects); thread = threading.get_ident(); previous = sys.getprofile()
    def forward(frame, event, arg):
        if profile.upstream is not None:
            installed = sys.getprofile()
            try: profile.upstream(frame, event, arg)
            except BaseException:
                # Match CPython: a throwing upstream profile becomes disabled.
                profile.upstream = None
                profile.released = True
                raise
            else:
                if sys.getprofile() is not installed:
                    # A retained callback owns its explicit disable/replacement.
                    profile.upstream = sys.getprofile()
                    profile.released = True

    def release():
        if sys.getprofile() is profile.installed: sys.setprofile(profile.upstream)
        profile.released = True

    def profile(frame, event, arg):
        if event == 'call' and getattr(profile, '_sparse_entry', None) is frame: return
        forward(frame, event, arg)
        if profile.released or event != 'call' or frame.f_code is not execution.verify_completion.__code__: return
        if threading.get_ident() != thread or frame.f_locals.get('owner') is not owner: return
        assert frame.f_back.f_code is original_code
        intent = frame.f_locals['intent']; proof = frame.f_locals['proof']; request = intent['request']
        assert proof['request'] == request and request['nonce'] == owner.nonce
        assert owner.root == Path(metadata['root']) and read(projects, CONFIG) == metadata
        assert frame.f_locals.get('absolute_deadline') is None
        wait_ready(projects, metadata, request)
        profile.ready = True
        release()
    # Explicit handoff to the test-owned contention hook; no target is wrapped.
    profile.upstream = previous
    profile.installed = profile
    profile.ready = profile.released = False
    profile.forward = forward
    profile.release = release
    sys.setprofile(profile)
    try:
        yield profile
    finally:
        # CPython clears a throwing profile; preserve its retained upstream,
        # but never revive an upstream callback after an intentional handoff.
        if not profile.released and (sys.getprofile() is profile.installed or sys.getprofile() is None):
            sys.setprofile(profile.upstream)
