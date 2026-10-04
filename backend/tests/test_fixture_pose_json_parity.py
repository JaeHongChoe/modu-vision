"""Actual fixture-flow evidence retains semantics at the independent JSON boundary."""
import json
import numpy as np
import pytest
from backend.engine.flowchart_engine import FlowchartEngine
from backend.engine.fixture_flow import fixture_scope
from backend.engine.flow_package_runtime import compare_flow_results
from backend.tests.test_fixture_flow_integration import reference_store, pipeline, inspect_spy
from backend.tests.test_service_e02 import moved, warp


def result_with_fixture(reference_store, monkeypatch):
    store, artifact = reference_store
    observed = warp(artifact.reference.grey, moved(0, np.array((90, 70), float)))
    engine = FlowchartEngine(device='cpu')
    inspect_spy(engine, monkeypatch)
    with fixture_scope(store.load):
        result = engine.execute(pipeline=pipeline(artifact.ref), image=observed)
    assert result['final_verdict'] == 'OK'
    return result


def test_actual_fixture_flow_is_unchanged_by_package_json_transport(reference_store, monkeypatch):
    reference = result_with_fixture(reference_store, monkeypatch)
    packaged = json.loads(json.dumps(reference, allow_nan=False))
    assert compare_flow_results(reference, packaged)['status'] == 'passed'
    pose = reference['execution_steps'][1]['artifacts'][0]['fixture_pose']
    assert pose == json.loads(json.dumps(pose, allow_nan=False))


@pytest.mark.parametrize('field,value', [
    ('reference_artifact_ref', 'fixture-ref:' + '0' * 64),
    ('reference_revision', 99),
    ('reference_shape', [1, 1]),
    ('observed_to_reference_transform', [[1, 0, 99], [0, 1, 99]]),
])
def test_json_transport_still_refuses_changed_fixture_evidence(reference_store, monkeypatch, field, value):
    reference = result_with_fixture(reference_store, monkeypatch)
    packaged = json.loads(json.dumps(reference, allow_nan=False))
    packaged['execution_steps'][1]['artifacts'][0]['fixture_pose'][field] = value
    compared = compare_flow_results(reference, packaged)
    assert compared['status'] == 'mismatch'
    assert 'execution_steps[1].artifacts' in compared['mismatched_fields']
