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
                if sys.getprofile() is profile.installed: sys.setprofile(original_profile)
            profile.action = None
    profile.action = None
    profile.installed = profile
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
        yield
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
        yield
    finally:
        # CPython clears a throwing profile; preserve its retained upstream,
        # but never revive an upstream callback after an intentional handoff.
        if not profile.released and (sys.getprofile() is profile.installed or sys.getprofile() is None):
            sys.setprofile(profile.upstream)
