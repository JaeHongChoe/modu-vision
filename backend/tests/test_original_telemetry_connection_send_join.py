"""Real-ASGI per-connection send/teardown regressions with fresh original owners.

Each case constructs a new canonical broadcaster before app binding and restores
both prior quiescent original aliases unchanged. Only outer transport holds an
already-forwarded original frame; genuine client disconnect releases it next turn.
No product handler/function/task/lock is replaced; no bound owner or completion is fabricated.
"""
import asyncio
import json
import os
from pathlib import Path
import time

import anyio
from anyio.from_thread import start_blocking_portal
import pytest


def _broadcaster_state(owner):
    """Freeze identities/scalars, never keep a shallow mutable state copy."""
    sends = getattr(owner, '_connection_sends', None)
    closing = getattr(owner, '_closing_connections', None)
    return (
        id(owner), id(owner._active_connections), tuple(sorted(id(ws) for ws in owner.active_connections)),
        id(sends), tuple(sorted((id(ws), id(lock)) for ws, lock in sends.items())) if sends is not None else None,
        id(closing), tuple(sorted(id(ws) for ws in closing)) if closing is not None else None,
        owner._running, id(owner._loop), id(owner._queue), id(owner._dispatch_task), id(owner._hw_task),
        id(owner._device), id(owner._lifetime_lock), owner._shutdown_started, owner._prepared_shutdown,
        owner._shutdown_failure, id(owner._shutdown_failures), tuple(sorted(owner._shutdown_failures.items())),
        id(owner._task_lifetimes), tuple((id(row), id(row['task']), id(row['loop']), row['role'],
            row['joined'], row['task'].done(), id(row.get('connection'))) for row in owner._task_lifetimes),
        id(owner._task_heads), tuple(sorted((role, id(row)) for role, row in owner._task_heads.items())),
        id(owner._shutdown_observers), tuple((id(row), id(row.get('loop')), id(row.get('task')),
            id(row.get('original_task')), id(row.get('future')),
            row['task'].done() if row.get('task') is not None else None,
            row['future'].done() if row.get('future') is not None else None,
            row.get('terminal_observed'), row.get('cancelled'), row.get('exception_type'))
            for row in owner._shutdown_observers))


def _broadcaster_quiescent(owner):
    return (owner._running is False and not owner.active_connections
        and not getattr(owner, '_connection_sends', {}) and not getattr(owner, '_closing_connections', set())
        and all(row['joined'] is True and row['task'].done() for row in owner._task_lifetimes)
        and all(row.get('task') is not None and row['task'].done()
            and row.get('original_task') is row['task']
            and row.get('future') is not None and row['future'].done()
            for row in owner._shutdown_observers))


@pytest.fixture(autouse=True)
def _fresh_original_broadcaster(monkeypatch):
    # Product imports occur after the original backend.conftest store isolation.
    from backend import main
    from backend.api import websocket_telemetry as telemetry
    old = telemetry.broadcaster
    assert main.broadcaster is old and type(old) is telemetry.TelemetryBroadcaster
    assert _broadcaster_quiescent(old), 'Existing original broadcaster is not quiescent'
    before = _broadcaster_state(old)
    fresh = telemetry.TelemetryBroadcaster()
    checks = {}
    try:
        with monkeypatch.context() as binding:
            binding.setattr(telemetry, 'broadcaster', fresh)
            binding.setattr(main, 'broadcaster', fresh)
            try:
                yield fresh
            finally:
                checks.update(own_aliases=telemetry.broadcaster is fresh and main.broadcaster is fresh,
                    own_original_tasks_and_connections_closed=_broadcaster_quiescent(fresh),
                    old_original_unchanged_inside=_broadcaster_state(old)==before)
    finally:
        # Context restoration runs even if the case or its checks fail. The
        # fixture adds no shutdown/cancel call, loop, deadline or field reset.
        checks.update(original_aliases_restored=telemetry.broadcaster is old and main.broadcaster is old,
            old_original_unchanged_after=_broadcaster_state(old)==before)
        assert checks and all(checks.values()), checks


def _exercise(tmp_path, monkeypatch, role, fail_send, second_connection):
    from backend.api.websocket_telemetry import broadcaster
    from backend.engine.original_owned_telemetry_socket_request import _TASKS
    from backend.tests.test_original_owned_telemetry_socket_lifetime import (
        _original_socket_stack, _original_socket_client)
    from backend.tests.test_original_owned_telemetry_server_close_lifetime import _bounded_original_frame
    app, admission, observer, project, actual_broadcaster = _original_socket_stack(tmp_path, monkeypatch)
    from backend import main
    assert actual_broadcaster is broadcaster is main.broadcaster
    assert admission._telemetry_socket_requests.broadcaster is broadcaster
    previous_tasks = len(broadcaster._task_lifetimes)
    state = dict(held=False, before_release=None, errors=[], second_connection=False, previous_tasks=previous_tasks)
    held = {}
    started, finished = [], []
    deadline = time.monotonic() + 4

    def project_before_release(scope):
        try:
            with admission._condition, observer._lock:
                row, attempt = held['row'], held['attempt']
                assert row['carrier'] is held['activity'] and attempt.activity is held['activity']
                assert held['activity'].scope is scope
                pending = attempt.sender_pending.get(held['task'])
                state['before_release'] = dict(
                    identity=row['identity'], released=row['released'], ws_disconnect=row['ws_disconnect'],
                    handler_done=attempt.handler_done, unregistered=attempt.unregistered,
                    socket_still_registered=attempt.socket in broadcaster.active_connections,
                    closing=attempt.socket in getattr(broadcaster, '_closing_connections', set()),
                    exact_carrier=row['carrier'] is attempt.activity and attempt.activity.scope is scope,
                    exact_pending_task=pending is not None and pending[0] is _TASKS.get(held['task'])
                        and pending[0].task is held['task'] and pending[0].role == role,
                    exact_original_source=held['task'].get_coro().cr_code is (
                        type(broadcaster)._event_dispatch_loop.__code__ if role == 'dispatch'
                        else type(broadcaster)._safe_close.__code__),
                    exact_original_message=pending is not None and pending[1] is held['message']
                        and pending[2] == tuple(held['message'].items()),
                    send_pending=row['send_attempt'] is not None)
        except BaseException as error:
            state['errors'].append(dict(phase='pre_release_projection', type=type(error).__name__))
        finally:
            held['release'].set()

    async def original_transport(scope, receive, send):
        if scope.get('type') != 'websocket':
            return await app(scope, receive, send)
        loop = asyncio.get_running_loop()
        if not broadcaster._running:
            broadcaster.start(loop)
            started.extend((broadcaster._dispatch_task, broadcaster._hw_task))
        assert broadcaster._loop is loop
        async def original_send(message):
            task = asyncio.current_task()
            birth = _TASKS.get(task)
            target = (not state['held'] and birth is not None and birth.role == role
                      and message['type'] == ('websocket.send' if role == 'dispatch' else 'websocket.close'))
            await send(message)  # Actual original frame has reached the client.
            if target:
                assert birth.task is task and birth.loop is loop and task.get_loop() is loop
                state['held'] = True
                with admission._condition, observer._lock:
                    rows = [row for row in observer._records.values() if row['carrier'].scope is scope]
                    assert len(rows) == 1
                    row = rows[0]; activity = row['carrier']
                    attempt = admission._telemetry_socket_requests.pending[activity.token]
                    pending = attempt.sender_pending.get(task)
                    assert pending is not None and pending[0] is birth and pending[1] is message
                    assert attempt.activity is activity and activity.scope is scope and not row['released']
                held.update(task=task, message=message, scope=scope, row=row, activity=activity, attempt=attempt,
                            release=asyncio.Event(), returned=asyncio.Event())
                try:
                    with anyio.fail_after(max(0, deadline-time.monotonic())):
                        await held['release'].wait()
                    if fail_send:
                        raise OSError('Controlled already-forwarded original dispatch transport failure')
                finally:
                    held['returned'].set()
        async def original_receive():
            message = await receive()
            if message['type'] == 'websocket.disconnect' and held.get('scope') is scope:
                # Exact original tracked_receive consumes this genuine message,
                # then the next loop callback observes whether teardown waited.
                loop.call_soon(project_before_release, scope)
            return message
        # TestClient cancels its session after sending disconnect. Shield only
        # this test transport so its real disconnect is processed as by Uvicorn.
        with anyio.CancelScope(shield=True):
            try:
                await app(scope, original_receive, original_send)
            finally:
                finished.append(scope)

    client, headers = _original_socket_client(original_transport, project)
    primary = None
    preparation = None
    shutdown_deadline = None
    with start_blocking_portal() as portal:
        # The original TestClient factory yields this owned portal. This avoids
        # running app lifespan and puts both actual sockets on one original loop.
        client.portal = portal
        try:
            with client.websocket_connect('/ws/telemetry', headers=headers) as socket:
                first = _bounded_original_frame(socket, deadline)
                assert first['type'] == 'websocket.send'
                assert json.loads(first['text'])['type'] == 'hardware_stats'
                periodic = _bounded_original_frame(socket, deadline)
                assert periodic['type'] == 'websocket.send'
                assert json.loads(periodic['text'])['type'] == 'hardware_stats'
                if role == 'connection_close':
                    shutdown_deadline = time.monotonic()+3.903
                    async def prepare_original():
                        return await asyncio.to_thread(broadcaster.prepare_shutdown, shutdown_deadline)
                    preparation = portal.start_task_soon(prepare_original)
                    for _ in range(20):
                        close = _bounded_original_frame(socket, deadline)
                        if close['type'] == 'websocket.close':
                            assert close['code'] == 1001
                            break
                    else:raise AssertionError('No original shutdown close frame')
                assert state['held'] and held['scope'] is not None
                if second_connection:
                    with client.websocket_connect('/ws/telemetry', headers=headers) as peer:
                        initial = _bounded_original_frame(peer, deadline)
                        assert initial['type'] == 'websocket.send'
                        assert json.loads(initial['text'])['type'] == 'hardware_stats'
                        peer.send_text('ping')
                        pong = _bounded_original_frame(peer, deadline)
                        assert pong == {'type':'websocket.send', 'text':'pong'}
                        state['second_connection'] = True
            if preparation is not None:
                state['preparation'] = preparation.result(timeout=max(0, deadline-time.monotonic()))
        except BaseException as error:
            primary = error
            state['errors'].append(dict(phase='actual_ASGI_sequence', type=type(error).__name__))
        finally:
            if held:
                held['release'].set()
            if shutdown_deadline is None:shutdown_deadline=time.monotonic()+3.903
            state['one_original_shutdown_deadline']=shutdown_deadline
            async def original_shutdown():
                if held:
                    with anyio.fail_after(max(0, shutdown_deadline-time.monotonic())):
                        await held['returned'].wait()
                await broadcaster.shutdown(shutdown_deadline)
                return broadcaster._closure_receipt(shutdown_deadline)
            try:state['closure'] = portal.call(original_shutdown)
            except BaseException as error:
                state['errors'].append(dict(phase='original_shutdown', type=type(error).__name__))
            client.portal = None
            client.close()
    state['observer'] = observer.snapshot()
    state['raw_ws'] = [{key:row[key] for key in ('identity','released','completed','normal_return',
                         'ws_disconnect','send_attempt','transport_failed','error_type')}
                      for row in observer._records.values() if row['ingress']['type'] == 'websocket']
    # No body data; reduce pending message to a boolean before saving evidence.
    for row in state['raw_ws']:row['send_attempt'] = row['send_attempt'] is not None
    state['active_connections'] = len(broadcaster.active_connections)
    state['original_tasks_done'] = len(started)==2 and all(task.done() for task in started)
    state['original_socket_calls_returned'] = len(finished)
    admission.close();state['drain'] = admission.drain(.03)
    state['final_observer'] = observer.snapshot()
    output = Path(os.environ.get('MV_SEND_JOIN_RESULT', tmp_path/'send-join-result.json'))
    output.write_text(json.dumps(state, indent=2, sort_keys=True)+'\n')
    if primary is not None:raise primary
    return state


@pytest.mark.parametrize('role,fail_send,second_connection', [
    pytest.param('dispatch', False, True, id='dispatch_success_second_connection'),
    pytest.param('dispatch', True, False, id='dispatch_actual_send_error'),
    pytest.param('connection_close', False, False, id='original_shutdown_close_success'),
])
def test_original_connection_send_finishes_before_teardown(tmp_path, monkeypatch, role, fail_send, second_connection):
    state = _exercise(tmp_path, monkeypatch, role, fail_send, second_connection)
    assert state['errors'] == [] and state['active_connections'] == 0 and state['original_tasks_done']
    before = state['before_release']
    assert before is not None and all(before[key] for key in ('ws_disconnect','exact_carrier',
        'exact_pending_task','exact_original_source','exact_original_message','send_pending'))
    assert before['released'] is False and before['handler_done'] is False
    assert before['unregistered'] == 0 and before['socket_still_registered'] and before['closing']
    assert state['second_connection'] is second_connection
    assert state['original_socket_calls_returned'] == (2 if second_connection else 1)
    assert state['closure']['joined_tasks'] == state['closure']['task_count'] == (
        state['previous_tasks'] + 2 + (1 if role == 'connection_close' else 0))
    assert all(state['closure'][key] is False for key in (
        'whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))
    if fail_send:
        assert state['observer']['uncertain'] and state['drain']['status'] == 'refused'
        assert 'send_error' in state['observer']['failures']
        assert any(row['transport_failed'] and row['error_type']=='OSError' and not row['completed']
                   for row in state['raw_ws'])
    else:
        assert not state['observer']['uncertain'] and state['observer']['failures'] == []
        assert all(row['completed'] and row['normal_return'] and row['ws_disconnect']
                   and not row['send_attempt'] and not row['transport_failed'] for row in state['raw_ws'])
        assert state['drain'] == {'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
        assert state['final_observer']['all_settled']
    assert all(state['final_observer'][key] is False for key in (
        'whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))


def test_original_shutdown_during_accept_keeps_never_registered_path(tmp_path, monkeypatch):
    """Actual shutdown inside forwarded accept; no synthetic registration/lock."""
    from backend.tests.test_original_owned_telemetry_socket_lifetime import (
        _original_socket_stack, _original_socket_client)
    from backend.tests.test_original_owned_telemetry_server_close_lifetime import _bounded_original_frame
    app, admission, observer, project, broadcaster = _original_socket_stack(tmp_path, monkeypatch)
    from backend import main
    assert broadcaster is main.broadcaster is admission._telemetry_socket_requests.broadcaster
    shutdown_deadline = time.monotonic()+3.903
    frame_deadline = time.monotonic()+4
    actual = []
    async def original_transport(scope, receive, send):
        async def original_send(message):
            await send(message)
            if scope['type']=='websocket' and message['type']=='websocket.accept':
                assert not broadcaster.active_connections and not broadcaster._task_lifetimes
                await broadcaster.shutdown(shutdown_deadline)
                actual.append(broadcaster._closure_receipt(shutdown_deadline))
        await app(scope, receive, original_send)
    client, headers = _original_socket_client(original_transport, project)
    try:
        with client.websocket_connect('/ws/telemetry', headers=headers) as socket:
            close = _bounded_original_frame(socket, frame_deadline)
            assert close['type']=='websocket.close' and close['code']==1001
    finally:
        client.close()
    snapshot = observer.snapshot()
    assert len(actual)==1 and actual[0]['idle'] and actual[0]['task_count']==0
    assert not broadcaster.active_connections
    assert getattr(broadcaster, '_connection_sends', {})=={}
    assert all(row['normal_return'] and row['released'] for row in snapshot['records'])
    assert not any(row['error_type'] in ('KeyError','TypeError') for row in snapshot['records'])
    assert 'owned_socket_handler_failed' not in snapshot['failures']
    # Existing original register-return proof still refuses never-registration.
    assert 'owned_socket_register_return' in snapshot['failures']
    admission.close()
    assert admission.drain(.03)['status']=='refused'
    assert all(observer.snapshot()[key] is False for key in (
        'whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))
