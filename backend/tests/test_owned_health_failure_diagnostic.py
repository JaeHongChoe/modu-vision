"""Original factory/ASGI scope; failure hashes never grant drain authority."""
import asyncio
import hashlib
import json

import pytest

from backend.tests.test_owned_health_diagnostic import ORDINARY, ordinary, own_app


KEY = 'owned_request_failure_diagnostic'
SCHEMA = 'modu-vision.original-owned-health-failure-diagnostic/v1'
FLAGS = ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease')


def read(owned):
    app, client, admission, observer = owned
    response = client.get('/health', headers={'x-vision-token': app.state.api_token})
    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in ORDINARY} == ordinary(app)
    assert body['owned_request_diagnostic']['schema'] == 'modu-vision.original-owned-health-diagnostic/v1'
    return body


def test_original_inflight_health_scope_reports_empty_hashes_without_authority(own_app):
    _, _, admission, observer = own_app
    before = admission.snapshot()
    failures = observer._failures
    body = read(own_app)
    old, new = body['owned_request_diagnostic'], body[KEY]
    assert new == {
        'schema': SCHEMA, 'sequence': new['sequence'], 'request_count': new['request_count'],
        'diagnostic_request_identity': old['diagnostic_request_identity'],
        'uncertain': False, 'failure_count': 0, 'reason_sha256': [],
        **{flag: False for flag in FLAGS},
    }
    assert new['sequence'] >= old['sequence']
    assert new['request_count'] >= old['request_count']
    assert admission.snapshot() == before
    assert observer._failures is failures and failures == []
    assert observer.snapshot()['pending_requests'] == 0


def test_actual_asgi_send_failure_keeps_first_reason_and_cannot_be_cleared_by_health(own_app):
    app, _, admission, observer = own_app
    scope = {
        'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': 'GET', 'path': '/health', 'raw_path': b'/health',
        'query_string': b'', 'headers': [], 'scheme': 'http', 'root_path': '',
        'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', 2),
    }
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def failed_transport(message):
        if message['type'] == 'http.response.body':
            raise OSError('controlled original ASGI transport failure')
    # Original application/middleware/admission/observer and actual send-error
    # path run unchanged. Only this test transport rejects a genuine body.
    with pytest.raises(OSError, match='controlled original ASGI transport failure'):
        asyncio.run(app(scope, receive, failed_transport))
    failures = list(observer._failures)
    assert failures[0] == 'send_error'
    assert 'downstream_error' in failures and 'incomplete_original_envelope' in failures
    before = admission.snapshot()
    first = read(own_app)[KEY]
    second = read(own_app)[KEY]
    expected = [hashlib.sha256(reason.encode('utf-8')).hexdigest() for reason in failures]
    assert first['reason_sha256'] == second['reason_sha256'] == expected
    assert first['failure_count'] == second['failure_count'] == len(failures)
    assert first['uncertain'] is second['uncertain'] is True
    assert all(first[flag] is second[flag] is False for flag in FLAGS)
    assert observer._failures == failures and admission.snapshot() == before
    admission.close()
    assert observer.snapshot()['all_settled'] is False


def test_ordered_first_32_hashes_hide_arbitrary_reason_text_and_remain_sticky(own_app):
    app, _, admission, observer = own_app
    secret = '/private/owned/path?token=do-not-export-this-reason'
    # Controlled poison inputs test the projection contract, not a native cause.
    for reason in [secret, secret, *('controlled_reason_' + str(i) for i in range(33))]:
        observer.poison(reason)
    original_list = observer._failures
    before = list(original_list)
    assert len(before) == 32 and before[0] == secret
    expected = [hashlib.sha256(reason.encode('utf-8')).hexdigest() for reason in before]
    for _ in range(2):
        body = read(own_app)
        diagnostic = body[KEY]
        assert diagnostic['reason_sha256'] == expected
        assert diagnostic['failure_count'] == 32 and diagnostic['uncertain'] is True
        assert all(diagnostic[flag] is False for flag in FLAGS)
        encoded = json.dumps(body)
        assert secret not in encoded and app.state.api_token not in encoded
        assert all(reason not in encoded for reason in before[1:])
        assert len(json.dumps(diagnostic).encode()) < 3072
    assert observer._failures is original_list and original_list == before
    assert observer._uncertain is True and admission.snapshot()['active_scopes'] == 0


class ForeignList(list):
    def __iter__(self):
        raise AssertionError('Foreign reason container must not be iterated')


class ForeignString(str):
    def encode(self, *args, **kwargs):
        raise AssertionError('Foreign reason encoder must not run')


@pytest.mark.parametrize('reason', ['x' * 256, '\U0001f512' * 256])
def test_exact_reason_character_bound_is_accepted_without_exporting_text(own_app, reason):
    _, _, _, observer = own_app
    observer.poison(reason)
    diagnostic = read(own_app)[KEY]
    assert diagnostic['reason_sha256'] == [hashlib.sha256(reason.encode('utf-8')).hexdigest()]
    assert diagnostic['failure_count'] == 1 and diagnostic['uncertain'] is True


@pytest.mark.parametrize('invalid', [
    None, (), {}, ForeignList(['foreign']), [''], ['x' * 257], ['\ud800'],
    [1], [ForeignString('foreign')], ['repeat', 'repeat'],
    ['reason_' + str(i) for i in range(33)],
])
def test_invalid_reason_storage_omits_only_new_diagnostic_without_adoption_or_reset(own_app, invalid):
    _, _, admission, observer = own_app
    observer._failures = invalid
    before = admission.snapshot()
    body = read(own_app)
    assert KEY not in body
    assert observer._failures is invalid and observer._uncertain is False
    assert admission.snapshot() == before


@pytest.mark.parametrize('uncertain,reasons', [(False, ['unconfirmed_reason']), (True, [])])
def test_inconsistent_failure_and_uncertainty_state_is_omitted_without_reset(own_app, uncertain, reasons):
    _, _, admission, observer = own_app
    observer._uncertain, observer._failures = uncertain, reasons
    before = admission.snapshot()
    assert KEY not in read(own_app)
    assert observer._uncertain is uncertain and observer._failures is reasons
    assert admission.snapshot() == before


def test_released_and_foreign_scope_cannot_read_original_failure_diagnostic(own_app):
    _, _, admission, observer = own_app
    read(own_app)
    released = next(iter(observer._records.values()))['carrier'].scope
    before = admission.snapshot()
    assert observer.health_failure_diagnostic(released) is None
    assert observer.health_failure_diagnostic(dict(released)) is None
    assert observer.health_failure_diagnostic(object()) is None
    assert admission.snapshot() == before and observer._failures == []


def test_real_unsupported_request_stays_refused_after_read_only_failure_projection(own_app):
    _, client, admission, observer = own_app
    assert client.get('/api/accounts/config').status_code == 200
    before = admission.snapshot()
    assert before == {'active_scopes': 1, 'unsupported': ['uncovered_request_protocol']}
    assert type(observer._failures) is list
    failures = list(observer._failures)
    assert 1 <= len(failures) <= 32
    assert all(type(reason) is str and 1 <= len(reason) <= 256 for reason in failures)
    assert 'shared_denial_unconfirmed_or_late' in failures
    original = observer.snapshot()['records'][0]
    assert original['released'] and original['completed'] and original['normal_return']
    diagnostic = read(own_app)[KEY]
    assert diagnostic['uncertain'] is True and diagnostic['failure_count'] == len(failures)
    assert diagnostic['reason_sha256'] == [hashlib.sha256(reason.encode()).hexdigest() for reason in failures]
    assert all(diagnostic[flag] is False for flag in FLAGS)
    assert admission.snapshot() == before and observer._failures == failures
    admission.close()
    assert admission.drain(.02)['status'] == 'refused'
    assert observer.snapshot()['all_settled'] is False
