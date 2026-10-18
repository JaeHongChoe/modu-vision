"""Final diagnostic data, never managed-drain or native acceptance.

The original factory, ASGI middleware, admission and observer process genuine
requests. The existing terminal-service fixture explicitly models only the
controller/artifact authority, frame transport and clock. This file models the
low-level descriptor sink; the separate OS transport file uses real descriptors.
Its socketpair,
relay queue, service loop, admission and drain validators remain original.
No browser, listener, subprocess, model, lease or installed app is used.
"""
import asyncio
import copy
import hashlib
import json
import sys

import pytest

from backend.engine import application_launch_handshake as handshake
from backend.tests.test_application_terminal_drain_closing import original_terminal
from backend.tests.test_owned_health_diagnostic import own_app


PREFIX = 'OWNED_FINAL_DRAIN_DIAGNOSTIC '
FLAGS = ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease')


@pytest.fixture(autouse=True)
def factory_precedes_modeled_service_cache(own_app):
    # Autouse dependency creates the real factory before the unrelated terminal
    # fixture installs its explicitly modeled service cache/clock/transport.
    return own_app


@pytest.fixture(autouse=True)
def modeled_external_descriptor_sink(monkeypatch):
    def captured(fd, data):
        assert fd == 2 and type(data) is bytes
        return sys.stderr.write(data.decode('utf-8')) == len(data)
    monkeypatch.setattr(handshake, '_write_final_diagnostic_descriptor', captured, raising=False)


def run_original_service(state, owned):
    _, _, admission, _ = owned
    # Connect two existing fixtures; no admitted method or owner is replaced.
    state.admission = admission
    state.cache['admission'] = admission
    before = admission.snapshot()
    handshake.backend_execution_service(state.event)
    assert state.event.is_set() and len(state.waits) == 2
    assert len(state.reads) == len(state.sends) == 1
    assert len(state.validates) == 4 and state.clock.now < 10.2
    receipt = state.sends[0]
    handshake.validate_drain_receipt(receipt, state.frame,
        backend_process=state.cache['proof']['process'])
    assert receipt['active_scopes'] == before['active_scopes']
    assert receipt['unsupported'] == before['unsupported']
    assert receipt['status'] == ('refused' if before['active_scopes'] or before['unsupported'] else 'managed_scopes_drained')
    assert set(receipt) == {'schema_version', 'kind', 'request', 'backend_proof',
        'status', 'active_scopes', 'unsupported', 'scope', *FLAGS}
    assert all(receipt[flag] is False for flag in FLAGS)
    assert admission.snapshot() == before
    assert admission._closed and state.queue._closed_deadline == 10.2
    assert state.left.fileno() >= 0 and not state.queue.unresolved
    return receipt


def read_projection(capsys, receipt):
    stderr = capsys.readouterr().err
    lines = [line for line in stderr.splitlines() if line.startswith(PREFIX)]
    assert len(lines) == 1, 'Missing final original drain diagnostic'
    raw = lines[0][len(PREFIX):].encode('utf-8')
    assert len(lines[0].encode('utf-8')) + 1 <= 8 * 1024
    value = json.loads(raw)
    assert value['schema'] == 'modu-vision.original-final-drain-diagnostic/v1'
    assert value['receipt_sha256'] == hashlib.sha256(handshake._canonical(receipt)).hexdigest()
    assert value['capture_phase'] == 'after_original_receipt_send'
    assert all(value[flag] is False for flag in FLAGS)
    assert value['active_scopes'] == receipt['active_scopes']
    assert value['unsupported'] == receipt['unsupported']
    return value, stderr


def fail_original_health_send(app):
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': 'GET', 'path': '/health', 'raw_path': b'/health',
        'query_string': b'', 'headers': [], 'scheme': 'http', 'root_path': '',
        'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', 2)}

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        if message['type'] == 'http.response.body':
            raise OSError('controlled external ASGI body transport failure')

    # Same explicit external transport failure as the existing health control.
    with pytest.raises(OSError, match='controlled external ASGI body transport failure'):
        asyncio.run(app(scope, receive, send))


@pytest.mark.parametrize('refused', [False, True])
def test_genuine_final_drain_exports_bounded_original_observer_after_unchanged_receipt(
        original_terminal, own_app, capsys, refused):
    state = original_terminal
    app, client, admission, observer = own_app
    secret = 'private-dataset-query-never-exported'
    if refused:
        response = client.get('/'+secret, params={'token': secret},
            headers={'authorization': 'Bearer '+secret, 'x-private': secret})
        assert response.status_code == 401
        fail_original_health_send(app)
        assert admission.snapshot() == {'active_scopes': 0,
            'unsupported': ['uncovered_request_protocol']}
        assert observer._failures[0] == 'send_error'
        assert 'downstream_error' in observer._failures
        assert 'incomplete_original_envelope' in observer._failures
    else:
        response = client.get('/api/project/current')
        assert response.status_code == 401
        assert admission.snapshot() == {'active_scopes': 0, 'unsupported': []}
        assert observer._failures == []
    full_body = response.content
    assert full_body and response.is_closed
    before = copy.deepcopy(observer.snapshot())
    receipt = run_original_service(state, own_app)
    after = observer.snapshot()
    assert after['records'] == before['records']
    assert after['failures'] == before['failures']
    assert after['request_count'] == before['request_count']
    value, stderr = read_projection(capsys, receipt)
    assert value['request_count'] == before['request_count']
    assert value['sequence'] == after['sequence']
    assert value['uncertain'] is bool(before['failures'])
    assert value['reason_sha256'] == [hashlib.sha256(reason.encode()).hexdigest()
        for reason in before['failures']]
    assert value['retained_records'] == len(before['records'])
    assert value['omitted_records'] == 0 and value['truncated'] is False
    assert [row['identity'] for row in value['records']] == [row['identity'] for row in before['records']]
    assert all(row['released'] for row in value['records'])
    if refused:
        failed = next(row for row in value['records'] if row['route'] == 'HEALTH')
        assert failed['completed'] is False and failed['normal_return'] is False
        assert failed['canceled'] is False and failed['error'] == 'ERROR'
        assert failed['terminal_forwarded'] is False
    assert secret not in stderr and app.state.api_token not in stderr
    assert all(key not in json.dumps(value) for key in ('headers', 'query_string', 'raw_path', 'authorization'))


def test_failed_stderr_is_unavailable_without_replacing_original_protocol_result(
        original_terminal, own_app, monkeypatch):
    _, client, _, observer = own_app
    assert client.get('/api/project/current').status_code == 401

    class FailedStderr:
        def write(self, value):
            raise OSError('controlled external stderr failure')

    returns = []
    old = sys.getprofile()
    assert old is None

    def returned(frame, event, value):
        if (event == 'return' and frame.f_code.co_name == '_emit_final_drain_diagnostic'
                and frame.f_code.co_filename == handshake.__file__):
            returns.append(value)

    monkeypatch.setattr(sys, 'stderr', FailedStderr())
    sys.setprofile(returned)
    try:
        receipt = run_original_service(original_terminal, own_app)
    finally:
        sys.setprofile(old)
    assert sys.getprofile() is old
    assert receipt['status'] == 'managed_scopes_drained'
    assert observer._failures == [] and observer._uncertain is False
    assert returns == [False], 'Missing diagnostic-unavailable result'


def test_projection_limits_keep_total_and_omit_older_normal_rows_without_revealing_reasons(
        original_terminal, own_app, capsys):
    app, client, _, observer = own_app
    private = '/private/path?password=never-export-reason'
    # Controlled format/bound inputs; not a claimed native failure sequence.
    for _ in range(20):
        assert client.get('/health').status_code == 200
    for reason in [private, *('controlled_reason_'+str(i) for i in range(31))]:
        observer.poison(reason)
    failures = list(observer._failures)
    receipt = run_original_service(original_terminal, own_app)
    value, stderr = read_projection(capsys, receipt)
    assert value['request_count'] == value['retained_records'] == 20
    assert len(value['records']) == 16 and value['omitted_records'] == 4
    assert value['truncated'] is True
    assert [row['identity'] for row in value['records']] == list(range(5, 21))
    assert value['reason_sha256'] == [hashlib.sha256(reason.encode()).hexdigest() for reason in failures]
    assert len(value['reason_sha256']) == 32 and observer._failures == failures
    assert private not in stderr and app.state.api_token not in stderr
    assert 'controlled_reason_' not in stderr
