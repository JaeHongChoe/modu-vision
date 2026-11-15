"""Versioned ordinary remote detection identity/dispatch with owned artifacts, no model or SSH."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.remote.coordinator import ArtifactValidationError
from backend.engine.detection.metrics import DETECTION_AP_LEGACY, DETECTION_AP_V2


def context(tmp_path):
    output = tmp_path / 'outputs';output.mkdir()
    source = tmp_path / 'source';source.mkdir()
    return SimpleNamespace(job_id='owned-model', task='detection', output_dir=output, dataset_path=source,
                           input_manifest_sha256='a' * 64)


def result(marker=DETECTION_AP_V2):
    metrics = {'evaluated_split':'test', 'mAP_50':None, 'mAP_50_95':None,
               'detection_ap_available':False, 'detection_ap_unavailable_reason':'no_ground_truth_objects'}
    if marker is not None: metrics['detection_metric_contract'] = marker
    return {'job_id':'owned-model','task':'detection','evaluation_contract_version':2,'metrics':metrics,
            'confusion_matrix':{'cell_samples':{}},'test_predictions':[]}


def test_ordinary_remote_detection_pins_v2_before_operation_identity(tmp_path, monkeypatch):
    from backend.remote import operations
    owned = context(tmp_path);artifact = tmp_path / 'owned-result.json';artifact.write_text(json.dumps(result()))
    captured = []
    def remote(ctx, operation, spec, artifact_name, **kwargs):
        captured.append(dict(spec))
        return artifact
    monkeypatch.setattr(operations, 'run_remote_operation', remote)
    observed = operations.run_remote_evaluation(owned)
    assert captured[0]['detection_metric_contract'] == DETECTION_AP_V2
    old = {'protocol_version':1,'operation':'evaluate','job_id':owned.job_id,'task':owned.task,
           'input_manifest_sha256':owned.input_manifest_sha256,'evaluation_contract_version':2}
    new = {**old, **captured[0]}
    assert operations._operation_journal_path(owned, 'evaluate', old) != operations._operation_journal_path(owned, 'evaluate', new)
    assert observed['metrics']['mAP_50'] is None


@pytest.mark.parametrize('marker', [None, DETECTION_AP_LEGACY, 'unknown'])
def test_v2_ordinary_remote_result_cannot_adopt_a_missing_old_or_unknown_marker(tmp_path, monkeypatch, marker):
    from backend.remote import operations
    owned = context(tmp_path);artifact = tmp_path / 'owned-result.json';artifact.write_text(json.dumps(result(marker)))
    monkeypatch.setattr(operations, 'run_remote_operation', lambda *args, **kwargs: artifact)
    with pytest.raises(ArtifactValidationError, match='metric contract'):
        operations.run_remote_evaluation(owned)
    assert not (owned.output_dir / 'eval_results.json').exists()


@pytest.mark.parametrize('marker', [None, DETECTION_AP_LEGACY, DETECTION_AP_V2])
def test_worker_dispatches_frozen_contract_with_missing_spec_retaining_legacy(tmp_path, monkeypatch, marker):
    from backend.api import routes_evaluation
    from backend.engine import device
    from backend.remote.worker import _evaluate_model
    calls = []
    def evaluator(checkpoint, metadata, data, selected, *, metric_contract='implicit-default'):
        calls.append(metric_contract)
        return result(metric_contract)
    monkeypatch.setattr(routes_evaluation, '_evaluate_detection', evaluator)
    monkeypatch.setattr(routes_evaluation, '_resolve_dataset_dir', lambda path, task: path)
    monkeypatch.setattr(device, 'get_device', lambda value: 'cpu')
    spec = {'job_id':'owned-model','task':'detection','device':'cpu','evaluation_contract_version':2}
    if marker is not None: spec['detection_metric_contract'] = marker
    observed = _evaluate_model(spec, tmp_path / 'unused.pt', {}, tmp_path)
    expected = marker or DETECTION_AP_LEGACY
    assert calls == [expected]
    assert observed['metrics']['detection_metric_contract'] == expected


def test_worker_refuses_a_result_from_another_detection_contract(tmp_path, monkeypatch):
    from backend.api import routes_evaluation
    from backend.engine import device
    from backend.remote.snapshot import SnapshotValidationError
    from backend.remote.worker import _evaluate_model
    monkeypatch.setattr(routes_evaluation, '_evaluate_detection', lambda *args, **kwargs: result(DETECTION_AP_V2))
    monkeypatch.setattr(routes_evaluation, '_resolve_dataset_dir', lambda path, task: path)
    monkeypatch.setattr(device, 'get_device', lambda value: 'cpu')
    with pytest.raises(SnapshotValidationError, match='metric contract'):
        _evaluate_model({'job_id':'owned-model','task':'detection','device':'cpu'}, tmp_path/'unused.pt', {}, tmp_path)


def test_worker_rejects_unknown_frozen_detection_contract_before_evaluator(tmp_path, monkeypatch):
    from backend.api import routes_evaluation
    from backend.engine import device
    from backend.remote.snapshot import SnapshotValidationError
    from backend.remote.worker import _evaluate_model
    calls = []
    def evaluator(*args, **kwargs):
        calls.append(kwargs)
        return result(DETECTION_AP_V2)
    monkeypatch.setattr(routes_evaluation, '_evaluate_detection', evaluator)
    monkeypatch.setattr(routes_evaluation, '_resolve_dataset_dir', lambda path, task: path)
    monkeypatch.setattr(device, 'get_device', lambda value: 'cpu')
    with pytest.raises(SnapshotValidationError, match='metric contract'):
        _evaluate_model({'job_id':'owned-model','task':'detection','device':'cpu','detection_metric_contract':'unknown'}, tmp_path/'unused.pt', {}, tmp_path)
    assert calls == []
