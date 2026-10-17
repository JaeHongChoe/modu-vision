"""Real original preparation closes an active socket; Root-only fresh process.

Run this selector in a fresh test process: original prepare_shutdown intentionally
closes the original singleton's new-task admission. No app lifespan, substitute
owner/task, model, GPU forward, training or synthetic join is used.
"""
import asyncio
import anyio
import json
import time


def _bounded_original_frame(socket, deadline):
    async def receive_original_frame():
        remaining = deadline - time.monotonic()
        assert remaining > 0, 'Original server-close client deadline expired'
        with anyio.fail_after(remaining):
            return await socket._send_rx.receive()
    return socket.portal.call(receive_original_frame)


def test_original_telemetry_preparation_closes_active_socket_and_joins(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_telemetry_socket_lifetime import (
        _original_socket_stack, _original_socket_client)
    app, admission, observer, project, broadcaster = _original_socket_stack(tmp_path, monkeypatch)
    previous_tasks = len(broadcaster._task_lifetimes)
    original_tasks, close_frames, closures = [], [], []
    async def original_transport(scope, receive, send):
        if scope.get('type') != 'websocket':
            return await app(scope, receive, send)
        loop = asyncio.get_running_loop()
        broadcaster.start(loop)
        original_tasks.extend((broadcaster._dispatch_task, broadcaster._hw_task))
        async def original_send(message):
            if message['type'] == 'websocket.close':
                task = asyncio.current_task()
                rows = [row for row in broadcaster._task_lifetimes
                        if row['role'] == 'connection_close' and row['task'] is task]
                assert len(rows) == 1
                assert rows[0]['connection'].scope is scope and task.get_loop() is loop
                assert task.get_coro().cr_code is type(broadcaster)._safe_close.__code__
                assert message['code'] == 1001
                close_frames.append(task)
            await send(message)
        try:
            await app(scope, receive, original_send)
        finally:
            with anyio.CancelScope(shield=True):
                await broadcaster.shutdown(time.monotonic() + 3)
                closures.append(broadcaster._closure_receipt(time.monotonic() + 1))
    client, headers = _original_socket_client(original_transport, project)
    preparation = None
    try:
        with client.websocket_connect('/ws/telemetry', headers=headers) as socket:
            deadline = time.monotonic() + 4
            for _ in range(2):
                frame = _bounded_original_frame(socket, deadline)
                assert frame['type'] == 'websocket.send'
                assert json.loads(frame['text'])['type'] == 'hardware_stats'
            async def prepare_original_broadcaster():
                return await asyncio.to_thread(broadcaster.prepare_shutdown, time.monotonic() + 3)
            preparation = socket.portal.start_task_soon(prepare_original_broadcaster)
            for _ in range(20):
                frame = _bounded_original_frame(socket, deadline)
                if frame['type'] == 'websocket.close':
                    assert frame['code'] == 1001
                    break
                assert frame['type'] == 'websocket.send'
                assert json.loads(frame['text'])['type'] == 'hardware_stats'
            else:
                raise AssertionError('Original preparation never sent its close frame')
            receipt = preparation.result(timeout=4)
            assert receipt['idle'] and receipt['supported'] and receipt['pending_tasks'] == 0
            assert receipt['task_count'] == receipt['joined_tasks'] == previous_tasks + 3
            # Task closure is separate from the still-live ASGI socket scope.
            assert admission.snapshot()['active_scopes'] > 0
        assert not broadcaster.active_connections
    finally:
        client.close()
    assert preparation is not None and preparation.done()
    assert len(original_tasks) == 2 and all(task.done() for task in original_tasks)
    assert len(close_frames) == 1 and close_frames[0].done() and close_frames[0].result() is True
    close_rows = [row for row in broadcaster._task_lifetimes
                  if row['role'] == 'connection_close' and row['task'] is close_frames[0]]
    assert len(close_rows) == 1 and close_rows[0]['joined']
    assert len(closures) == 1 and closures[0]['pending_tasks'] == 0
    assert closures[0]['joined_tasks'] == closures[0]['task_count'] == previous_tasks + 3
    for result in (receipt, closures[0]):
        assert all(result[name] is False for name in (
            'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
    records = observer.snapshot()['records']
    assert len(records) == 3
    assert all(row['completed'] and row['normal_return'] and row['released'] for row in records)
    assert not observer.snapshot()['uncertain'], observer.snapshot()['failures']
    admission.close()
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}
    final = observer.snapshot()
    assert final['all_settled'] and not final['uncertain']
    assert all(final[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
