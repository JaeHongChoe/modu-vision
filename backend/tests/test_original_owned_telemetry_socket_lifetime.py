"""Real original shared telemetry handshake, ping and disconnect; no native proof.

Do not start application background telemetry tasks in this request component
control. The original broadcaster itself accepts/registers/sends/unregisters.
Only the existing test fixture supplies a real admission to the app factory.
"""
import json


def test_original_shared_telemetry_socket_ping_disconnect_drains(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from backend.api.websocket_telemetry import broadcaster
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    _, (project,) = _owned_projects(app, observer, 'Owned telemetry source')
    assert not broadcaster.active_connections
    tasks_before = list(broadcaster._task_lifetimes)
    client = TestClient(app, base_url='http://owned.invalid')
    try:
        login = client.post('/api/accounts/login', json={
            'username':'fixture-admin', 'password':'fixture-password-123'})
        assert login.status_code == 200
        headers = {'authorization':'Bearer '+login.json()['token'],
                   'x-vision-project':project['id']}
        current = client.get('/api/project/current', headers=headers)
        assert current.status_code == 200 and current.json()['id'] == project['id']
        context = json.loads(current.headers['x-vision-context'])
        assert context['actor_id'] == login.json()['user']['id']
        assert context['project_id'] == project['id'] and context['mode'] == 'team'
        headers['x-vision-context'] = current.headers['x-vision-context']
        with client.websocket_connect('/ws/telemetry', headers=headers) as socket:
            initial = socket.receive_json()
            assert initial['type'] == initial['event'] == 'hardware_stats'
            assert 'cpu_percent' in initial and len(broadcaster.active_connections) == 1
            original = next(iter(broadcaster.active_connections))
            assert original.scope['state']['account_user']['id'] == context['actor_id']
            assert original.scope['state']['scoped_project']['id'] == project['id']
            socket.send_text('ping')
            assert socket.receive_text() == 'pong'
        assert not broadcaster.active_connections
        assert broadcaster._task_lifetimes == tasks_before
    finally:
        client.close()
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


def _original_socket_stack(tmp_path, monkeypatch):
    from backend.api.websocket_telemetry import broadcaster
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    _, (project,) = _owned_projects(app, observer, 'Owned telemetry boundary')
    assert not broadcaster.active_connections
    return app, admission, observer, project, broadcaster


def _original_socket_client(app, project):
    from starlette.testclient import TestClient
    client = TestClient(app, base_url='http://owned.invalid')
    login = client.post('/api/accounts/login', json={
        'username':'fixture-admin', 'password':'fixture-password-123'})
    assert login.status_code == 200
    headers = {'authorization':'Bearer '+login.json()['token'],
               'x-vision-project':project['id']}
    current = client.get('/api/project/current', headers=headers)
    assert current.status_code == 200 and current.json()['id'] == project['id']
    context = json.loads(current.headers['x-vision-context'])
    assert context['actor_id'] == login.json()['user']['id']
    assert context['project_id'] == project['id'] and context['mode'] == 'team'
    headers['x-vision-context'] = current.headers['x-vision-context']
    return client, headers


def _original_socket_refused(admission, observer, broadcaster):
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    assert not broadcaster.active_connections
    assert len(observer.snapshot()['records']) == 3
    _refused(admission)
    final = observer.snapshot()
    assert all(final[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


def test_original_socket_instance_receive_delegate_cannot_borrow_completion(tmp_path, monkeypatch):
    app, admission, observer, project, broadcaster = _original_socket_stack(tmp_path, monkeypatch)
    client, headers = _original_socket_client(app, project)
    delegated = []
    try:
        with client.websocket_connect('/ws/telemetry', headers=headers) as socket:
            assert socket.receive_json()['type'] == 'hardware_stats'
            original = next(iter(broadcaster.active_connections))
            receive_text = original.receive_text
            async def delegated_receive():
                delegated.append(True)
                return await receive_text()
            monkeypatch.setattr(original, 'receive_text', delegated_receive)
            socket.send_text('ping')
            assert socket.receive_text() == 'pong'
        assert delegated
    finally:
        client.close()
    records = observer.snapshot()['records']
    assert all(row['completed'] and row['normal_return'] and row['released'] for row in records)
    _original_socket_refused(admission, observer, broadcaster)


"""Only the real transport-failure control correction; no runtime execution."""

def test_original_socket_forwarded_send_failure_cannot_settle(tmp_path, monkeypatch):
    app, admission, observer, project, broadcaster = _original_socket_stack(tmp_path, monkeypatch)
    failed = []
    async def original_transport(scope, receive, send):
        async def failing_send(message):
            await send(message)
            if (scope.get('type') == 'websocket' and message['type'] == 'websocket.send'
                    and not failed):
                failed.append(True)
                raise OSError('Controlled original telemetry transport failure')
        await app(scope, receive, failing_send)
    client, headers = _original_socket_client(original_transport, project)
    try:
        with client.websocket_connect('/ws/telemetry', headers=headers) as socket:
            assert socket.receive_json()['type'] == 'hardware_stats'
            # The original WebSocket marks the failed transport disconnected.
            # Exit this genuine client; no further frame is expected from it.
    finally:
        client.close()
    assert failed == [True]
    snapshot = observer.snapshot()
    record = snapshot['records'][-1]
    assert record['released'] and not record['completed'] and record['error_type'] == 'OSError'
    assert 'send_error' in snapshot['failures']
    assert any(event['action'] == 'send_error' for event in snapshot['events'])
    _original_socket_refused(admission, observer, broadcaster)


def test_same_scope_foreign_socket_reference_cannot_borrow_completion(tmp_path, monkeypatch):
    from starlette.websockets import WebSocket
    app, admission, observer, project, broadcaster = _original_socket_stack(tmp_path, monkeypatch)
    altered = []
    async def original_transport(scope, receive, send):
        async def original_send(message):
            if scope.get('type') == 'websocket' and message['type'] == 'websocket.accept':
                attempts = [attempt for attempt in admission._telemetry_socket_requests.pending.values()
                            if attempt.scope is scope]
                assert len(attempts) == 1
                attempt = attempts[0]
                other = WebSocket(scope, receive=receive, send=send)
                assert other is not attempt.socket and other.scope is attempt.socket.scope
                attempt.values['websocket'] = other
                altered.append(True)
            await send(message)
        await app(scope, receive, original_send)
    client, headers = _original_socket_client(original_transport, project)
    try:
        with client.websocket_connect('/ws/telemetry', headers=headers) as socket:
            assert socket.receive_json()['type'] == 'hardware_stats'
            socket.send_text('ping')
            assert socket.receive_text() == 'pong'
    finally:
        client.close()
    assert altered == [True]
    records = observer.snapshot()['records']
    assert all(row['completed'] and row['normal_return'] and row['released'] for row in records)
    _original_socket_refused(admission, observer, broadcaster)
