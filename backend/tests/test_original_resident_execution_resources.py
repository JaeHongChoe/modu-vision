"""Genuine resident metadata GET and focused refusal controls, Root execution only.

No model is loaded or executed. The PUT control uses its current CPU capacity;
it must remain unowned even though the original configuration call returns.
"""
import asyncio
import json
import os
import threading
from types import MethodType

import httpx
import pytest

from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit


PATH = '/api/flowchart/execution-resources'


def _wanted(engine):
    return {'device': str(engine.device), 'engine_device_capacity': engine._max_device_concurrency,
            'active_executions': engine._active_executions, 'maximum_cpu_slots': min(8, os.cpu_count() or 1),
            'configuration_available': engine.device.type == 'cpu', 'cpu_configuration_available': True,
            'gpu_capacity_requires_reservation': engine.device.type != 'cpu'}


def _refused(admission, observer):
    _no_whole_credit(observer)
    admission.close()
    assert admission.drain(.03)['status'] == 'refused'
    assert 'uncovered_request_protocol' in admission.snapshot()['unsupported']


@pytest.mark.parametrize('device', (None, 'cpu'), ids=('default-resident', 'cpu-resident'))
def test_actual_resident_getter_dict_and_original_HTTP_finish_are_owned(tmp_path, monkeypatch, device):
    from backend.api import routes_flowchart as flows
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    selected = flows._CPU_ENGINE if device == 'cpu' else flows._ENGINE
    wanted = _wanted(selected)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get(PATH, headers=headers, params={} if device is None else {'device': device})
            assert response.status_code == 200 and response.json() == wanted
            assert json.loads(response.headers['x-vision-context']) == json.loads(headers['x-vision-context'])
    asyncio.run(scenario())
    row, = [row for row in observer._records.values() if row['ingress'] == {'type': 'http', 'method': 'GET', 'path': PATH}]
    assert all(row[key] for key in ('completed', 'normal_return', 'released', 'terminal_forwarded'))
    snapshot = observer.snapshot()
    assert snapshot['failures'] == [] and not snapshot['uncertain'] and snapshot['pending_requests'] == 0
    _no_whole_credit(observer)
    admission.close()
    assert admission.drain(.03) == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}


@pytest.mark.parametrize('mode', ('resident_before_factory', 'instance_getter', 'class_getter', 'lock', 'result_after_return'))
def test_replaced_resident_or_getter_lock_and_result_cannot_borrow_completion(tmp_path, monkeypatch, mode):
    from backend.api import routes_flowchart as flows
    from backend.engine.flowchart_engine import FlowchartEngine
    if mode == 'resident_before_factory':
        monkeypatch.setattr(flows, '_CPU_ENGINE', FlowchartEngine(device='cpu'))
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    selected = flows._CPU_ENGINE
    wanted = _wanted(selected)
    mutated = []
    async def transport(scope, receive, send):
        async def forwarded(message):
            if mode == 'result_after_return' and scope.get('path') == PATH and message.get('type') == 'http.response.start':
                attempt, = [attempt for attempt in admission._flow_requests.pending.values() if attempt.scope is scope]
                attempt.resource_result['active_executions'] += 1
                mutated.append(True)
            await send(message)
        await app(scope, receive, forwarded)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=transport), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if mode == 'instance_getter':
                original = selected.execution_resources
                def delegated(engine):
                    return original()
                monkeypatch.setattr(selected, 'execution_resources', MethodType(delegated, selected))
            elif mode == 'class_getter':
                original = FlowchartEngine.execution_resources
                def delegated(engine):
                    return original(engine)
                monkeypatch.setattr(FlowchartEngine, 'execution_resources', delegated)
            elif mode == 'lock':
                monkeypatch.setattr(selected, '_execution_state_lock', threading.RLock())
            response = await client.get(PATH, headers=headers, params={'device': 'cpu'})
            assert response.status_code == 200 and response.json() == wanted
    asyncio.run(scenario())
    if mode == 'result_after_return':
        assert mutated == [True]
    _refused(admission, observer)


def test_original_PUT_configuration_is_not_covered_by_the_resident_GET_lane(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    selected = flows._CPU_ENGINE
    slots = selected._max_device_concurrency
    previous_gate = selected._device_gate
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.put(PATH, headers=headers, json={'device': 'cpu', 'device_slots': slots})
            assert response.status_code == 200 and response.json() == _wanted(selected)
    asyncio.run(scenario())
    assert selected._max_device_concurrency == slots and selected._device_gate is not previous_gate
    _refused(admission, observer)


def test_original_GET_send_failure_is_not_resident_metadata_completion(tmp_path, monkeypatch):
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'GET',
                 'scheme': 'http', 'path': PATH, 'raw_path': PATH.encode(), 'query_string': b'device=cpu',
                 'root_path': '', 'headers': [(k.encode(), v.encode()) for k, v in headers.items()],
                 'client': ('127.0.0.1', 1), 'server': ('owned.invalid', 80)}
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            return {'type': 'http.disconnect'}
        async def send(message):
            if message['type'] == 'http.response.body':
                raise OSError('resident metadata original send failed')
        await app(scope, receive, send)
    with pytest.raises(OSError, match='resident metadata original send failed'):
        asyncio.run(scenario())
    target, = [row for row in observer._records.values() if row['ingress'] == {'type': 'http', 'method': 'GET', 'path': PATH}]
    assert target['response_status'] == 200 and target['transport_failed'] and not target['normal_return']
    _refused(admission, observer)


def test_current_cold_schema_producer_cohort_equals_actual_source_files():
    """A registered current cohort must describe this source tree, not a prior build."""
    import hashlib
    from pathlib import Path
    from backend.engine import required_flow_passive_capture as capture
    source_root = Path(capture.__file__).resolve().parents[2]
    pins = capture.COLD_SCHEMA_PRODUCER_SOURCE_PINS
    assert set(pins) == {
        'backend/engine/flow_operators.py', 'backend/engine/flow_package_runtime.py',
        'backend/engine/flowchart_engine.py', 'backend/engine/flowchart_schema.py',
        'backend/engine/model_backbones.py', 'backend/engine/rotation.py',
        'backend/engine/segmentation_evidence.py',
    }
    for name, pin in pins.items():
        body = (source_root / name).read_bytes()
        assert len(body) == pin['size'], name
        assert hashlib.sha256(body).hexdigest() == pin['sha256'], name
