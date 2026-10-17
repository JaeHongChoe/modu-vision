"""Actual isolated factory/ASGI envelopes; diagnostic data grants no authority."""
import json
from types import SimpleNamespace
import pytest


ORDINARY = {
    'status': 'ok', 'version': None, 'device': 'cpu',
    'device_name': 'Owned diagnostic CPU', 'is_accelerated': False,
    'torch_version': 'controlled-no-forward',
}


@pytest.fixture
def own_app(tmp_path, monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    from fastapi.testclient import TestClient
    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    monkeypatch.setattr(main, 'get_device', lambda: 'cpu')
    monkeypatch.setattr(main, 'get_device_info', lambda _: SimpleNamespace(
        device_type='cpu', device_name='Owned diagnostic CPU',
        is_accelerated=False, torch_version='controlled-no-forward'))
    monkeypatch.delenv('VISION_AI_STUDIO_API_TOKEN', raising=False)
    app = main.create_app(str(tmp_path/'projects'), str(tmp_path/'auth'))
    client = TestClient(app)
    try:
        yield app, client, admission, observer
    finally:
        client.close()


def ordinary(app):
    from backend import main
    return {**ORDINARY, 'version': main.VERSION}


def report(app, client):
    response = client.get('/health', headers={'x-vision-token': app.state.api_token})
    assert response.status_code == 200
    body = response.json()
    assert {k: body[k] for k in ORDINARY} == ordinary(app)
    assert 'owned_request_diagnostic' in body, 'Missing original owned-request diagnostic'
    result = body['owned_request_diagnostic']
    assert all(result[k] is False for k in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
    return result


def test_correct_original_token_reads_real_shared_denial_and_natural_settlement(own_app):
    app, client, admission, observer = own_app
    assert client.get('/api/project/current').status_code == 401
    before = admission.snapshot()
    assert before == {'active_scopes': 0, 'unsupported': []}
    result = report(app, client)
    current = [r for r in result['records'] if r['route'] == 'PROJECT_CURRENT']
    assert len(current) == 1
    assert current[0]['protocol'] == 'HTTP' and current[0]['method'] == 'GET'
    assert current[0]['status'] == 401 and current[0]['completed'] and current[0]['released']
    assert current[0]['entered_sequence'] < current[0]['released_sequence']
    assert result['sampled_active_scopes'] == 1 and result['pending_records'] == 1
    assert result['settled_records'] == 1 and result['request_count'] == 2
    assert result['diagnostic_request_in_flight'] is True
    assert admission.snapshot() == before
    assert observer.snapshot()['pending_requests'] == 0


@pytest.mark.parametrize('headers', [{}, {'x-vision-token': 'wrong-original-token'}, {b'x-vision-token': b'\xff'}])
def test_absent_or_wrong_token_preserves_exact_ordinary_six_fields(own_app, headers):
    app, client, admission, observer = own_app
    assert client.get('/health', headers=headers).json() == ordinary(app)
    assert admission.snapshot() == {'active_scopes': 0, 'unsupported': []}


def test_original_route_order_and_response_status_are_data_not_protocol_exemptions(own_app):
    app, client, admission, _ = own_app
    assert client.get('/api/project/current').status_code == 401
    assert client.options('/api/project/current', headers={
        'origin': 'null', 'access-control-request-method': 'GET',
        'access-control-request-headers': 'content-type'}).status_code == 200
    assert client.get('/api/onboarding').status_code == 401
    assert client.get('/api/project/list').status_code == 401
    assert client.get('/api/accounts/config').status_code == 200
    before = admission.snapshot()
    assert before['unsupported'] == ['uncovered_request_protocol']
    result = report(app, client)
    assert [(r['route'], r['method'], r['status']) for r in result['records'][:-1]] == [
        ('PROJECT_CURRENT', 'GET', 401), ('PROJECT_CURRENT', 'OPTIONS', 200),
        ('ONBOARDING', 'GET', 401), ('PROJECT_LIST', 'GET', 401),
        ('ACCOUNTS_CONFIG', 'GET', 200)]
    assert result['settled_records'] == 5 and result['pending_records'] == 1
    assert admission.snapshot() == before
    admission.close()
    assert admission.drain(.02)['status'] == 'refused'


def test_unknown_original_ingress_never_exports_path_query_or_credentials(own_app):
    app, client, admission, _ = own_app
    secret = 'private_dataset_or_token_never_exported'
    client.get('/'+secret, params={'token': secret}, headers={
        'authorization': 'Bearer '+secret, 'x-private': secret})
    result = report(app, client)
    assert result['records'][0]['route'] == 'UNKNOWN'
    encoded = json.dumps(result)
    assert secret not in encoded and app.state.api_token not in encoded
    assert all(k not in encoded for k in ('query_string', 'raw_path', 'headers', 'authorization'))
    assert admission.snapshot()['unsupported'] == ['uncovered_request_protocol']


@pytest.mark.parametrize('change', ['token', 'admission', 'observer'])
def test_changed_factory_binding_does_not_return_diagnostics_or_change_normal_health(own_app, change):
    app, client, admission, observer = own_app
    original_token = app.state.api_token
    if change == 'token': app.state.api_token = 'substituted-process-token'
    elif change == 'admission': app.state.owned_work_admission = object()
    else: admission._activity_observer = type(observer)()
    assert client.get('/health', headers={'x-vision-token': original_token}).json() == ordinary(app)


def test_foreign_record_owner_is_refused_without_invoking_its_binding_property(own_app):
    app, client, _, observer = own_app
    assert client.get('/health').json() == ordinary(app)
    called = []
    class ForeignOwner:
        @property
        def binding(self):
            called.append('foreign binding invoked')
            raise RuntimeError('foreign property must not be invoked')
    next(iter(observer._records.values()))['carrier'] = ForeignOwner()
    assert client.get('/health', headers={'x-vision-token': app.state.api_token}).json() == ordinary(app)
    assert called == []


def test_original_websocket_denial_is_sanitized_and_does_not_grant_coverage(own_app):
    from starlette.websockets import WebSocketDisconnect
    app, client, admission, _ = own_app
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect('/ws/telemetry'): pass
    assert denied.value.code == 1008
    result = report(app, client)
    row = result['records'][0]
    assert row['protocol'] == 'WEBSOCKET' and row['method'] == 'NONE' and row['route'] == 'TELEMETRY'
    assert row['status'] is None and row['released'] and row['completed']
    assert admission.snapshot() == {'active_scopes': 0, 'unsupported': ['uncovered_request_protocol']}



@pytest.mark.parametrize('request_identity', [128, 129, 168, 256])
def test_current_health_request_is_kept_in_its_real_bounded_projection(own_app, request_identity):
    """The original self request must survive history truncation, without authority."""
    app, client, admission, observer = own_app
    # Real original ASGI health requests fill genuine ingress/release records.
    # Its existing admission/device-info controls remain; no producer, rows,
    # scope, middleware, authentication or observation function is replaced here.
    for _ in range(request_identity - 1):
        response = client.get('/health')
        assert response.status_code == 200 and response.json() == ordinary(app)
    before = admission.snapshot()
    result = report(app, client)
    rows = result['records']
    assert result['diagnostic_request_identity'] == result['request_count'] == request_identity
    assert len(rows) == min(request_identity, 128)
    identities = [row['identity'] for row in rows]
    entered = [row['entered_sequence'] for row in rows]
    assert identities == sorted(set(identities))
    assert entered == sorted(set(entered))
    own = [row for row in rows if row['identity'] == result['diagnostic_request_identity']]
    # Exact existing collector D024/D025 contract, using only genuine rows.
    assert len(own) == 1
    current = own[0]
    assert current['protocol'] == 'HTTP' and current['method'] == 'GET'
    assert current['route'] == 'HEALTH' and current['accepted'] is True
    assert current['released'] is False and current['released_sequence'] is None
    assert current['completed'] is False and current['normal_return'] is False
    assert current['status'] is None
    assert result['diagnostic_request_in_flight'] is True
    assert result['truncated'] is (request_identity > 128)
    assert result['pending_records'] == 1
    assert result['released_records'] == result['settled_records'] == request_identity - 1
    assert result['sampled_active_scopes'] >= sum(
        row['accepted'] and not row['released'] for row in rows)
    assert all(row['released'] and row['completed'] and row['normal_return']
               for row in rows if row is not current)
    assert admission.snapshot() == before
    assert observer.snapshot()['pending_requests'] == 0
    history = observer.snapshot()['settled_history']
    assert len(observer._records) + history['requests'] == request_identity
    assert len(observer._records) + (history['requests'] > 0) <= observer._max_requests == 256
    assert len(observer._events) + (history['events'] > 0) <= observer._max_events == 1024
    encoded = json.dumps(result)
    assert app.state.api_token not in encoded
    assert all(flag not in encoded for flag in ('query_string', 'raw_path', 'authorization'))
    assert all(result[flag] is False for flag in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
