"""Delivered batch CLI retains full outcomes without turning failures into OK."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image
from backend.tests.test_runtime_deadline_sdk import real_package


def manifest(tmp_path, rows):
    path = tmp_path / 'batch.json'
    path.write_text(json.dumps({'schema': 'FlowBatchInput/v1', 'images': rows}), encoding='utf-8')
    return path


def cli(package, batch, output, *options):
    return subprocess.run([sys.executable, str(package / 'run_flow.py'), '--batch', str(batch),
                           '--output', str(output), *options], capture_output=True, text=True,
                          encoding='utf-8', timeout=60)


def test_delivered_batch_cli_retains_order_hashes_full_graph_and_input_errors(real_package, tmp_path):
    package, image = real_package
    second = tmp_path / 'second.png'; Image.new('RGB', (48, 32), 'white').save(second)
    rows = [{'image_path': image.name, 'image_id': 'first'},
            {'image_path': second.name, 'image_id': 'second'},
            {'image_path': 'missing.png', 'image_id': 'missing'}]
    batch = manifest(tmp_path, rows); output = tmp_path / 'result.json'
    process = cli(package, batch, output, '--deadline-ms', '30000')
    assert process.returncode == 2, process.stdout + process.stderr
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['schema'] == 'FlowBatchResult/v1'
    assert result['input_manifest_sha256'] == hashlib.sha256(batch.read_bytes()).hexdigest()
    assert result['package_manifest_sha256'] == hashlib.sha256((package / 'manifest.json').read_bytes()).hexdigest()
    assert [r['image_id'] for r in result['results']] == ['first', 'second', 'missing']
    for path, row in zip((image, second), result['results']):
        assert row['input_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert row['result']['image_id'] == row['image_id']
        assert row['result']['final_verdict'] == 'NG' and row['result']['roi_count'] == 1
        assert row['result']['execution_steps']
    assert result['results'][2]['result']['final_verdict'] == 'REVIEW'
    assert result['summary'] == {'total': 3, 'completed': 2, 'errors': 1, 'timeouts': 0, 'cancelled': 0, 'review': 1}


def test_delivered_batch_deadline_records_review_for_each_image(real_package, tmp_path):
    package, image = real_package
    batch = manifest(tmp_path, [{'image_path': str(image), 'image_id': 'timed'}]); output = tmp_path / 'timed.json'
    process = cli(package, batch, output, '--deadline-ms', '1')
    assert process.returncode == 3, process.stderr
    row = json.loads(output.read_text())['results'][0]
    assert row['status'] == 'timeout' and row['result']['final_verdict'] == 'REVIEW'
    assert row['result']['deadline']['terminated'] is True


def test_duplicate_batch_identity_refuses_before_any_result(real_package, tmp_path):
    package, image = real_package
    batch = manifest(tmp_path, [{'image_path': str(image), 'image_id': 'same'}] * 2); output = tmp_path / 'out.json'
    process = cli(package, batch, output)
    assert process.returncode == 2 and 'duplicate image identity' in process.stderr.lower()
    assert not output.exists()


def test_batch_expected_hash_refuses_without_executing(real_package, tmp_path, monkeypatch):
    from backend.engine.flow_package_runtime import Executor, run_flow_batch
    package, image = real_package
    batch = manifest(tmp_path, [{'image_path': str(image), 'image_id': 'changed', 'sha256': '0' * 64}])
    def forbidden(*args, **kwargs):
        raise AssertionError('Mismatched input must not execute')
    monkeypatch.setattr(Executor, 'predict', forbidden)
    result = run_flow_batch(package, batch)
    assert result['results'][0]['result']['rejection_reason'] == 'INPUT_ERROR'
    assert result['results'][0]['result']['final_verdict'] == 'REVIEW'


def test_batch_discards_a_result_when_input_changes(real_package, tmp_path, monkeypatch):
    from backend.engine.flow_package_runtime import Executor, run_flow_batch
    package, image = real_package
    batch = manifest(tmp_path, [{'image_path': str(image), 'image_id': 'changed'}])
    original = Executor.predict
    def predict_then_mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        Image.new('RGB', (48, 32), 'red').save(image)
        return result
    monkeypatch.setattr(Executor, 'predict', predict_then_mutate)
    result = run_flow_batch(package, batch, deadline_ms=30000)
    assert result['results'][0]['result']['rejection_reason'] == 'INPUT_CHANGED'
    assert result['results'][0]['result']['final_verdict'] == 'REVIEW'


@pytest.mark.parametrize('rows', [[], [{'image_id': 'x'}],
    [{'image_path': 'x', 'image_id': ''}], [{'image_path': 'x', 'image_id': 'x', 'device': 'cuda'}],
    [{'image_path': 'x', 'image_id': 'x', 'sha256': 'invalid'}]])
def test_malformed_batch_refuses_before_package_initialization(tmp_path, rows):
    from backend.engine.flow_package_runtime import run_flow_batch
    with pytest.raises(ValueError):
        run_flow_batch(tmp_path / 'nonexistent-package', manifest(tmp_path, rows))


def test_duplicate_keys_and_large_batch_refuse_before_initialization(tmp_path):
    from backend.engine.flow_package_runtime import run_flow_batch
    path = tmp_path / 'invalid.json'
    path.write_text('{"schema":"FlowBatchInput/v1","schema":"other","images":[]}')
    with pytest.raises(ValueError, match='Duplicate'):
        run_flow_batch(tmp_path / 'nonexistent-package', path)
    path.write_bytes(b' ' * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match='one MiB'):
        run_flow_batch(tmp_path / 'nonexistent-package', path)
