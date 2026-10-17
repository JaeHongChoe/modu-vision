"""Real auth/ASGI wire-codec controls; controlled bodies execute no model.

The fixed input is Root's genuine Node JSON.stringify output. The run stand-in
retains the original typed FlowchartPipeline boundary and original one-use
carrier. Only the execution body is replaced; original middleware, admission,
auth, receive/send observation and private receipt producers execute unchanged.
"""
import base64
import hashlib
import json
from pathlib import Path
from backend.tests.private_qa_fixtures import private_qa_root

# External qualification is explicit; absence is a collection failure, never a skip.
J = private_qa_root()
import pytest
pytestmark = pytest.mark.private_qualification

from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.engine.flowchart_engine import FlowchartPipeline
from backend.tests import test_required_flow_http_framework as original
from backend.tests import test_required_flow_save_export as exchanges


WIRE = J / 'Native-wire-graph-codec-reproduction-Root01/original-JS-wire-graph.json'
WIRE_FILE_SHA = 'e7ac8eb988adf9cdfc20bd17b850802dcc02d157e726655edd787c6bad3ce5dc'
WIRE_ASCII_SHA = 'dd8d079af861bcc350e4a3a4625957e96647abd2d891093cd4793660db2d69b4'


def genuine_wire():
    raw = WIRE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == WIRE_FILE_SHA
    graph = json.loads(raw)
    assert graph == original.original.GRAPH
    assert original.capture._graph_sha(graph) == WIRE_ASCII_SHA
    typed = FlowchartPipeline.model_validate(graph).model_dump()
    assert original.capture._graph_sha(typed) == original.capture.RETAINED_GRAPH_SHA
    assert typed == original.original.GRAPH
    return raw, graph


def typed_nonmodel_run(app, selector, monkeypatch, seen):
    route = next(route for route in app.routes
                 if getattr(route, 'path', None) == '/api/flowchart/run'
                 and 'POST' in route.methods)

    async def nonmodel_execution_body(scope, receive, send):
        request = Request(scope, receive)
        body = await request.json()
        # Production route validates this typed boundary before selecting the
        # passive carrier. No extra forward/model/remote operation is executed.
        pipeline = FlowchartPipeline.model_validate(body['pipeline']).model_dump()

        def consume_original():
            seen['witness'] = selector.shared_owner.current(request)
            carrier = original.capture.offer_original_required_flow_capture(
                selector, request, original.original.Profile(), pipeline=pipeline,
                image_path=Path(body['image_path']), device=body['device'],
                stop_node_id=None)
            assert carrier is not None
            grant = original.capture.serialize_required_flow_grant(
                carrier, profile_id=original.original.Profile.id,
                execution_profile_sha256=original.original.FACTS[
                    'normalized_execution_profile_defaultASCII_sha256'],
                pipeline=pipeline, image_sha256=original.capture.RETAINED_IMAGE_SHA,
                models=[{'job_id': job, 'task': task, 'checkpoint_sha256': digest}
                        for job, task, digest in original.capture.RETAINED_MODELS])
            seen['calls'] = seen.get('calls', 0) + 1
            seen['grant'] = grant
            seen['carrier'] = carrier

        await run_in_threadpool(consume_original)
        await JSONResponse({'data_only_nonmodel_execution_body': True,
                            'GPU_forward': False})(scope, receive, send)

    monkeypatch.setattr(route, 'app', nonmodel_execution_body)


def wire_body(project, graph):
    return {**original.body(project), 'pipeline': graph, 'image_id': None}


def test_genuine_native_JS_wire_RUN_preserves_typed_grant_and_exact_seven_keys(
        tmp_path, monkeypatch):
    _, graph = genuine_wire()
    app, selector, session, project, output = original.owned_http(tmp_path, monkeypatch)
    seen = {}
    typed_nonmodel_run(app, selector, monkeypatch, seen)
    body = wire_body(project, graph)
    assert set(body) == {'image_path', 'image_id', 'pipeline', 'project_id',
                         'execution_target', 'device', 'compute_profile_id'}
    client = TestClient(app, headers={'Authorization': 'Bearer ' + session['token']})
    try:
        response = client.post('/api/flowchart/run', json=body)
    finally:
        client.close()
    assert response.status_code == 200, response.text
    assert seen['calls'] == 1 and seen['carrier'].used and selector.used
    assert seen['grant']['pipeline_sha256'] == original.capture.RETAINED_GRAPH_SHA
    assert seen['witness'].active is False
    terminal = json.loads((output / 'terminal.json').read_bytes())
    assert terminal['complete'] is True, terminal['refusals']
    assert terminal['refusals'] == []
    observed = json.loads(base64.b64decode(terminal['request_base64'], validate=True))
    assert observed == body and observed['image_id'] is None
    assert original.capture._graph_sha(observed['pipeline']) == WIRE_ASCII_SHA
    assert terminal['server_ASGI_send_receipt'] is True
    assert terminal['client_receive_proven'] is False


def test_genuine_native_JS_wire_SAVE_promotes_only_after_original_typed_RUN(
        tmp_path, monkeypatch):
    raw, graph = genuine_wire()
    app, selector, session, project, output, seen = exchanges.setup(tmp_path, monkeypatch)
    typed_nonmodel_run(app, selector, monkeypatch, seen)
    client = exchanges.client(app, session)
    try:
        saved = client.post('/api/flowchart/pipeline', params={
            'recipe_task': 'segmentation',
            'source_dataset_path': project['source_dataset_dir'],
            'change_reason': 'Original native JSON wire codec control; no model',
            'expected_version_id': exchanges.SEED},
            content=raw, headers={'Content-Type': 'application/json'})
        assert saved.status_code == 200, saved.text
        assert exchanges.receipts(output) == []
        response = client.post('/api/flowchart/run', json=original.body(project))
        assert response.status_code == 200, response.text
    finally:
        client.close()
    terminal = json.loads((output / 'terminal.json').read_bytes())
    assert terminal['complete'] is True, terminal['refusals']
    assert seen['calls'] == seen['save_calls'] == 1
    rows = exchanges.receipts(output)
    assert len(rows) == 1
    row = rows[0]
    assert row['path'] == '/api/flowchart/pipeline' and row['complete'] is True
    assert row['actual_original_shared_authorization'] is True
    assert base64.b64decode(row['request_base64'], validate=True) == raw
    assert json.loads(raw) == graph
    assert row['saved_version'] == exchanges.SAVED
    assert row['elapsed_monotonic_seconds'] < 10
    assert row['server_ASGI_send_receipt'] is True
    assert row['client_receive_proven'] is False


# Appended controls preserve the exact original causal-RED test-file prefix.


def rejected_wire(mutation):
    _, graph = genuine_wire()
    if mutation == 'mixed-number':
        # Neither declared full representation: only one wire integer becomes
        # a Python float. The actual typed pipeline remains exactly identical.
        graph['nodes'][0]['position']['x'] = float(graph['nodes'][0]['position']['x'])
    else:
        assert mutation == 'ignored-extra-key'
        graph['unreviewed_wire_extension'] = 'must-not-be-promoted'
    assert original.capture._graph_sha(graph) not in (
        WIRE_ASCII_SHA, original.capture.RETAINED_GRAPH_SHA)
    typed = FlowchartPipeline.model_validate(graph).model_dump()
    assert typed == original.original.GRAPH
    assert original.capture._graph_sha(typed) == original.capture.RETAINED_GRAPH_SHA
    return graph


@pytest.mark.parametrize('mutation', ['mixed-number', 'ignored-extra-key'])
def test_other_RUN_wire_representation_refused_despite_same_valid_typed_carrier(
        tmp_path, monkeypatch, mutation):
    graph = rejected_wire(mutation)
    app, selector, session, project, output = original.owned_http(tmp_path, monkeypatch)
    seen = {}
    typed_nonmodel_run(app, selector, monkeypatch, seen)
    client = TestClient(app, headers={'Authorization': 'Bearer ' + session['token']})
    try:
        response = client.post('/api/flowchart/run', json=wire_body(project, graph))
    finally:
        client.close()
    assert response.status_code == 200, response.text
    assert seen['calls'] == 1 and seen['carrier'].used and selector.used
    assert seen['grant']['pipeline_sha256'] == original.capture.RETAINED_GRAPH_SHA
    assert seen['witness'].active is False
    terminal = json.loads((output / 'terminal.json').read_bytes())
    assert terminal['complete'] is False
    assert 'original_JSON_data_differs' in terminal['refusals']
    assert 'request_base64' not in terminal and 'response_base64' not in terminal
    assert terminal['client_receive_proven'] is False


@pytest.mark.parametrize('mutation', ['mixed-number', 'ignored-extra-key'])
def test_other_SAVE_wire_representation_not_promoted_after_valid_original_RUN(
        tmp_path, monkeypatch, mutation):
    graph = rejected_wire(mutation)
    app, selector, session, project, output, seen = exchanges.setup(tmp_path, monkeypatch)
    typed_nonmodel_run(app, selector, monkeypatch, seen)
    client = exchanges.client(app, session)
    try:
        saved = client.post('/api/flowchart/pipeline', params={
            'recipe_task': 'segmentation',
            'source_dataset_path': project['source_dataset_dir'],
            'change_reason': 'Refuse unreviewed wire representation; no model',
            'expected_version_id': exchanges.SEED}, json=graph)
        assert saved.status_code == 200, saved.text
        assert seen['save_body'] == graph and seen['save_calls'] == 1
        assert exchanges.receipts(output) == []
        response = client.post('/api/flowchart/run', json=original.body(project))
        assert response.status_code == 200, response.text
    finally:
        client.close()
    terminal = json.loads((output / 'terminal.json').read_bytes())
    assert terminal['complete'] is True, terminal['refusals']
    assert seen['calls'] == 1 and selector.used
    assert all(row['path'] != '/api/flowchart/pipeline' for row in exchanges.receipts(output))
    classes = exchanges.counts(output)
    assert classes['counts']['SAVE'] == classes['counts']['RUN'] == 1
    assert classes['counts']['EXPORT'] == classes['counts']['FORBIDDEN_TRAIN'] == 0
