"""Real original periodic telemetry producer plus socket closure, Root-only.

No application lifespan, substitute task, fake join, inference, GPU forward or
training. The global original broadcaster creates, sends and joins its actual
dispatch/hardware tasks on the actual socket's event loop.
"""
import asyncio
import anyio
import json
import time


def _bounded_original_client_text(socket, deadline):
    async def receive_original_frame():
        remaining = deadline - time.monotonic()
        assert remaining > 0, 'Original telemetry client receive deadline expired'
        with anyio.fail_after(remaining):
            message = await socket._send_rx.receive()
        socket._raise_on_close(message)
        assert message['type'] == 'websocket.send' and type(message.get('text')) is str
        return message['text']
    return socket.portal.call(receive_original_frame)


def test_original_periodic_broadcaster_socket_disconnect_and_join_drains(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from backend.api.websocket_telemetry import broadcaster
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    _, (project,) = _owned_projects(app, observer, 'Owned periodic telemetry source')
    assert not broadcaster.active_connections
    previous_tasks = len(broadcaster._task_lifetimes)
    originals, dispatched, closures = [], [], []
    async def original_transport(scope, receive, send):
        if scope.get('type') != 'websocket':
            return await app(scope, receive, send)
        loop = asyncio.get_running_loop()
        broadcaster.start(loop)
        dispatch, hardware = broadcaster._dispatch_task, broadcaster._hw_task
        originals.extend((dispatch, hardware))
        assert dispatch is broadcaster._task_heads['dispatch']['task']
        assert hardware is broadcaster._task_heads['hardware']['task']
        assert all(task.get_loop() is loop for task in (dispatch, hardware))
        assert dispatch.get_coro().cr_code is type(broadcaster)._event_dispatch_loop.__code__
        assert hardware.get_coro().cr_code is type(broadcaster)._hardware_telemetry_loop.__code__
        async def original_send(message):
            if message['type'] == 'websocket.send' and asyncio.current_task() is dispatch:
                payload = json.loads(message['text'])
                assert payload['type'] == payload['event'] == 'hardware_stats'
                assert 'cpu_percent' in payload
                assert any(socket.scope is scope for socket in broadcaster.active_connections)
                dispatched.append((dispatch, loop))
            await send(message)
        try:
            await app(scope, receive, original_send)
        finally:
            with anyio.CancelScope(shield=True):
                await broadcaster.shutdown(time.monotonic() + 3)
                closures.append(broadcaster._closure_receipt(time.monotonic() + 1))
    client = TestClient(original_transport, base_url='http://owned.invalid')
    try:
        login = client.post('/api/accounts/login', json={
            'username':'fixture-admin', 'password':'fixture-password-123'})
        assert login.status_code == 200
        headers = {'authorization':'Bearer '+login.json()['token'], 'x-vision-project':project['id']}
        current = client.get('/api/project/current', headers=headers)
        assert current.status_code == 200 and current.json()['id'] == project['id']
        headers['x-vision-context'] = current.headers['x-vision-context']
        with client.websocket_connect('/ws/telemetry', headers=headers) as socket:
            receive_deadline = time.monotonic() + 4
            assert json.loads(_bounded_original_client_text(socket, receive_deadline))['type'] == 'hardware_stats'
            periodic = json.loads(_bounded_original_client_text(socket, receive_deadline))
            assert periodic['type'] == periodic['event'] == 'hardware_stats'
            socket.send_text('ping')
            for _ in range(20):
                text = _bounded_original_client_text(socket, receive_deadline)
                if text == 'pong':
                    break
                assert json.loads(text)['type'] == 'hardware_stats'
            else:
                raise AssertionError('Original periodic telemetry never returned pong')
        assert not broadcaster.active_connections
    finally:
        client.close()
    assert len(originals) == 2 and all(task.done() for task in originals)
    assert dispatched and all(task is originals[0] for task, _ in dispatched)
    assert len(broadcaster._task_lifetimes) == previous_tasks + 2
    assert len(closures) == 1
    closure = closures[0]
    assert closure['idle'] and closure['supported'] and closure['pending_tasks'] == 0
    assert closure['joined_tasks'] == closure['task_count'] == previous_tasks + 2
    assert all(closure[name] is False for name in (
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
