"""Real owned ASGI failure attribution; diagnostics never accept a refused scope."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend.tests.test_owned_health_diagnostic import own_app

KEY = 'owned_ancillary_failure_attribution'
SCHEMA = 'modu-vision.original-owned-ancillary-failure-attribution/v1'
FLAGS = ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease')
REASONS = ('owned_ancillary_handler_return', 'owned_ancillary_unconfirmed_lifetime')
CORRUPT_REASONS = (
    'owned_ancillary_provenance_operation_failed',
    'owned_ancillary_provenance_operation_return_entry',
    'owned_ancillary_provenance_operation_return',
    'owned_ancillary_handler_return',
    'owned_ancillary_unconfirmed_lifetime',
)


def _package_fixture(tmp_path, monkeypatch, *, corrupt):
    from backend import main
    from backend.engine.flow_package import build_flow_package
    from backend.engine.product_delivery import record_package
    from backend.engine.migration_guard import maintenance_guard
    from backend.tests.test_original_owned_run_export_lifetime import _model_stack
    monkeypatch.chdir(tmp_path)
    # Only machine device discovery is controlled; no request/handler/owner,
    # package verifier, authentication, database or completion is replaced.
    monkeypatch.setattr(main, 'get_device', lambda: 'cpu')
    monkeypatch.setattr(main, 'get_device_info', lambda _: SimpleNamespace(
        device_type='cpu', device_name='Owned diagnostic CPU',
        is_accelerated=False, torch_version='controlled-no-forward'))
    app, admission, observer, project, graph, seed, checkpoint, protected = _model_stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    built = build_flow_package(pipeline=graph, checkpoints={checkpoint.parent.name: checkpoint},
        output_base_dir=root/'exports'/'flows', package_name='private_path_must_not_be_exported')
    package = Path(built['package_path'])
    record_package(project, package, version_id=seed, recipe_task='ocr', parity={'status': 'not_run'})
    with maintenance_guard(root): pass
    if corrupt:
        (package/'models'/checkpoint.parent.name/'best_model.pt').write_bytes(b'actual corrupt owned package')
    return app, admission, observer, project, root, protected


def _packet(body):
    assert KEY in body, 'Missing original ancillary reason-to-request attribution'
    value = body[KEY]
    assert value['schema'] == SCHEMA
    assert all(value[flag] is False for flag in FLAGS)
    old = body['owned_request_failure_diagnostic']
    assert value['diagnostic_request_identity'] == old['diagnostic_request_identity']
    assert value['sequence'] >= old['sequence']
    assert [row['reason_sha256'] for row in value['reasons']] == old['reason_sha256']
    return value


def _refused(admission, observer):
    before = admission.snapshot()
    assert before == {'active_scopes': 1, 'unsupported': ['uncovered_request_protocol']}
    admission.close()
    assert admission.drain(.03)['status'] == 'refused'
    assert observer.snapshot()['uncertain'] and not observer.snapshot()['all_settled']


def test_simultaneous_real_failure_keeps_its_request_not_the_healthy_inflight_request(tmp_path, monkeypatch):
    """Deleting attribution or using current/last request identity must fail."""
    from backend.tests.test_original_owned_run_export_lifetime import _login_current
    from backend.tests.test_original_owned_populated_package_library import _basis
    app, admission, observer, project, root, protected = _package_fixture(tmp_path, monkeypatch, corrupt=True)
    baseline = _basis(root)
    async def sequence():
        healthy_entered = asyncio.Event(); release_healthy = asyncio.Event(); armed = False
        async def external_transport(scope, receive, send):
            async def external_send(message):
                if armed and scope['path'] == '/health' and message['type'] == 'http.response.start':
                    healthy_entered.set()
                    await asyncio.wait_for(release_healthy.wait(), 10)
                await send(message)
            await app(scope, receive, external_send)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=external_transport), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            armed = True
            healthy = asyncio.create_task(client.get('/health'))
            try:
                await asyncio.wait_for(healthy_entered.wait(), 10)
                failed = await client.get('/api/product-delivery/packages', headers=headers,
                    params={'private_query': 'private_query_must_not_be_exported'})
                assert failed.status_code == 200 and failed.json()['packages'][0]['integrity'] == 'failed'
            finally:
                release_healthy.set()
            assert (await healthy).status_code == 200
            armed = False
            first = (await client.get('/health', headers={'x-vision-token': app.state.api_token})).json()
            duplicate = await client.get('/api/product-delivery/packages', headers=headers)
            assert duplicate.status_code == 200
            second = (await client.get('/health', headers={'x-vision-token': app.state.api_token})).json()
            return first, second
    first, second = asyncio.run(sequence())
    (tmp_path/'actual-failure-observation.json').write_text(json.dumps({
        'failures': observer.snapshot()['failures'],
        'records': observer.snapshot()['records'],
        'admission': admission.snapshot(), 'first_health': first, 'second_health': second}, indent=2))
    assert observer.snapshot()['failures'] == list(CORRUPT_REASONS)
    rows = observer.snapshot()['records']
    failed_rows = [row for row in rows if row['ingress']['path'] == '/api/product-delivery/packages']
    assert len(failed_rows) == 2 and failed_rows[0]['identity'] != failed_rows[1]['identity']
    assert all(row['released'] and row['completed'] and row['normal_return'] for row in rows)
    assert [(row['identity'], row['ingress']['path']) for row in rows[:4]] == [
        (1, '/api/accounts/login'), (2, '/api/project/current'),
        (3, '/health'), (4, '/api/product-delivery/packages')]
    actual = _packet(first); later = _packet(second)
    assert actual['reasons'] == later['reasons']
    attributed = {row['reason_sha256']: row for row in actual['reasons']}
    for reason in CORRUPT_REASONS:
        row = attributed[hashlib.sha256(reason.encode()).hexdigest()]
        assert row['reason_sha256'] == hashlib.sha256(reason.encode()).hexdigest()
        attribution = row['attribution']
        assert attribution['request_sequence'] == failed_rows[0]['identity']
        original = next(item for item in first['owned_request_diagnostic']['records']
                        if item['identity'] == failed_rows[0]['identity'])
        assert attribution['entered_sequence'] == original['entered_sequence']
        assert attribution['owner'] == 'UI_ANCILLARY' and attribution['route'] == 'PACKAGES'
        assert attribution['method'] == 'GET'
        assert attribution['entered_sequence'] > 0 and attribution['observed_sequence'] >= attribution['entered_sequence']
    encoded = json.dumps(actual)
    for secret in [str(root), project['id'], app.state.api_token,
                   'private_query_must_not_be_exported', 'private_path_must_not_be_exported']:
        assert secret not in encoded
    assert _basis(root) == baseline
    assert all(path.read_bytes() == raw for path, raw in protected.items())
    # Two genuine failed ancillary attempts remain outstanding; observing them
    # does not settle either attempt or authorize the failed native lease.
    assert admission.snapshot() == {'active_scopes': 2, 'unsupported': ['uncovered_request_protocol']}
    admission.close(); assert admission.drain(.03)['status'] == 'refused'


def test_earlier_unbound_reason_is_not_adopted_by_a_later_real_failure(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_run_export_lifetime import _login_current
    app, admission, observer, project, _, _ = _package_fixture(tmp_path, monkeypatch, corrupt=True)
    observer.poison(REASONS[0])  # Negative unbound input, never a positive witness.
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            result = await client.get('/api/product-delivery/packages', headers=headers)
            assert result.status_code == 200 and result.json()['packages'][0]['integrity'] == 'failed'
            return (await client.get('/health', headers={'x-vision-token': app.state.api_token})).json()
    packet = _packet(asyncio.run(sequence()))
    attributed = {row['reason_sha256']: row for row in packet['reasons']}
    assert attributed[hashlib.sha256(REASONS[0].encode()).hexdigest()]['attribution'] is None
    assert attributed[hashlib.sha256(REASONS[1].encode()).hexdigest()]['attribution']['route'] == 'PACKAGES'
    _refused(admission, observer)


def test_healthy_original_package_has_no_attribution_and_still_drains(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_run_export_lifetime import _login_current
    app, admission, observer, project, _, _ = _package_fixture(tmp_path, monkeypatch, corrupt=False)
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/product-delivery/packages', headers=headers)
            assert response.status_code == 200 and response.json()['packages'][0]['integrity'] == 'verified'
            return (await client.get('/health', headers={'x-vision-token': app.state.api_token})).json()
    assert _packet(asyncio.run(sequence()))['reasons'] == []
    admission.close()
    assert admission.drain(.03) == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    assert observer.snapshot()['all_settled']


def test_unbound_reason_cap_privacy_and_authenticated_guard_remain_original(own_app):
    app, client, admission, observer = own_app
    secrets = ['private_reason_'+str(i) for i in range(34)]
    for reason in secrets: observer.poison(reason)
    before = admission.snapshot(); original_failures = list(observer._failures)
    body = client.get('/health', headers={'x-vision-token': app.state.api_token}).json()
    packet = _packet(body)
    assert len(packet['reasons']) == 32 and all(row['attribution'] is None for row in packet['reasons'])
    assert [row['reason_sha256'] for row in packet['reasons']] == [hashlib.sha256(x.encode()).hexdigest() for x in secrets[:32]]
    assert all(x not in json.dumps(packet) for x in secrets)
    assert len(json.dumps(packet).encode()) < 16384
    assert observer._failures == original_failures and admission.snapshot() == before
    assert KEY not in client.get('/health').json()
    assert KEY not in client.get('/health', headers={'x-vision-token': 'foreign'}).json()


def test_foreign_caller_or_attempt_cannot_bind_an_unbound_poison(own_app):
    app, client, admission, observer = own_app
    observer.poison(REASONS[0]); before = admission.snapshot()
    class Foreign:
        @property
        def activity(self): raise AssertionError('Foreign properties must not be read')
    observer.observe_ancillary_failure(Foreign(), REASONS[0], True)
    body = client.get('/health', headers={'x-vision-token': app.state.api_token}).json()
    assert _packet(body)['reasons'][0]['attribution'] is None
    assert observer._failures == [REASONS[0]] and admission.snapshot() == before


@pytest.mark.parametrize('changed', ['foreign-carrier', 'attempt-binding', 'carrier-token', 'raising-binding'])
def test_changed_genuine_failure_binding_omits_attribution_without_changing_refusal(tmp_path, monkeypatch, changed):
    from backend.tests.test_original_owned_run_export_lifetime import _login_current
    app, admission, observer, project, _, _ = _package_fixture(tmp_path, monkeypatch, corrupt=True)
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/product-delivery/packages', headers=headers)
            assert response.status_code == 200 and response.json()['packages'][0]['integrity'] == 'failed'
            body = (await client.get('/health', headers={'x-vision-token': app.state.api_token})).json()
            assert all(row['attribution']['route'] == 'PACKAGES' for row in _packet(body)['reasons'])
            attempt = admission._UI_ancillary_requests.records[-1]
            assert attempt.name == 'packages'
            unbound = 'controlled_unbound_foreign_caller'
            observer.poison(unbound)
            before = admission.snapshot()
            # A real original attempt is insufficient without its original fail
            # call site; this negative input must stay unbound.
            observer.observe_ancillary_failure(attempt, unbound, True)
            if changed == 'foreign-carrier':
                class ForeignCarrier:
                    @property
                    def token(self): raise AssertionError('Foreign carrier property was invoked')
                attempt.activity = ForeignCarrier()
            elif changed == 'attempt-binding': attempt.binding = ()
            elif changed == 'carrier-token': attempt.activity.token = object()
            else:
                class RaisingBinding:
                    def __eq__(self, other): raise AssertionError('Foreign binding equality was invoked')
                attempt.binding = RaisingBinding()
            after = (await client.get('/health', headers={'x-vision-token': app.state.api_token})).json()
            assert observer.snapshot()['failures'] == [*CORRUPT_REASONS, unbound]
            assert admission.snapshot() == before
            assert after['owned_request_failure_diagnostic']['reason_sha256'] == [
                hashlib.sha256(reason.encode()).hexdigest() for reason in [*CORRUPT_REASONS, unbound]]
            return after
    packet = _packet(asyncio.run(sequence()))
    assert all(row['attribution'] is None for row in packet['reasons'])
    _refused(admission, observer)


def test_fresh_exact_type_attempt_cannot_adopt_a_canonical_request(tmp_path, monkeypatch):
    from backend.engine.original_owned_UI_ancillary_request import _AncillaryAttempt
    from backend.tests.test_original_owned_run_export_lifetime import _login_current
    app, admission, observer, project, _, _ = _package_fixture(tmp_path, monkeypatch, corrupt=True)
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/product-delivery/packages', headers=headers)
            assert response.status_code == 200 and response.json()['packages'][0]['integrity'] == 'failed'
            owner = admission._UI_ancillary_requests
            original = owner.records[-1]
            # Negative forgery uses the exact real class and real retained
            # carrier, but is not the original attempt in owner.records.
            forged = _AncillaryAttempt(owner, original.activity, original.name,
                original.headers, original.auth.project_id, original.auth.token)
            owner.pending[original.activity.token] = forged
            before = admission.snapshot()
            forged.fail('controlled_forged_attempt')
            assert admission.snapshot() == before
            return (await client.get('/health', headers={'x-vision-token': app.state.api_token})).json()
    packet = _packet(asyncio.run(sequence()))
    row = next(row for row in packet['reasons'] if row['reason_sha256'] ==
               hashlib.sha256(b'owned_ancillary_controlled_forged_attempt').hexdigest())
    assert row['attribution'] is None
    assert observer.snapshot()['failures'] == [*CORRUPT_REASONS, 'owned_ancillary_controlled_forged_attempt']
    _refused(admission, observer)
