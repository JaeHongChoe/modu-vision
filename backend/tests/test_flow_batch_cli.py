"""Delivered batch CLI retains full outcomes without turning failures into OK."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image
from backend.tests.test_runtime_deadline_sdk import real_package


def _batch_failure_note(result):
    """Fixed failure classifications and projections; never raw error text."""
    value = result['results'][0]['result']
    note = {}
    for key, allowed in (
        ('status', ('ok', 'completed', 'error', 'timeout', 'cancelled', 'uncertain')),
        ('rejection_reason', ('INPUT_ERROR', 'EXECUTION_ERROR', 'INPUT_CHANGED',
                              'OWNED_PROCESS_GROUP_UNRECONCILED', 'INFERENCE_DEADLINE_EXCEEDED', 'CANCELLED')),
    ):
        item = value.get(key)
        if type(item) is str and item in allowed:
            note[key] = item
    message = value.get('error')
    if type(message) is not str:
        note['error'] = {'classification': 'UNAVAILABLE'}
    else:
        classification = {
            'Owned inference returned no bounded result': 'OWNED_INFERENCE_RESULT_UNAVAILABLE',
            'Batch input changed during execution': 'BATCH_INPUT_CHANGED',
            'Batch input checksum mismatch': 'BATCH_INPUT_CHECKSUM_MISMATCH',
        }.get(message, 'UNCLASSIFIED')
        for prefix, label in (('Owned inference failed:', 'OWNED_INFERENCE_FAILED'),
                              ('Package checksum mismatch:', 'PACKAGE_CHECKSUM_MISMATCH')):
            if message.startswith(prefix):
                classification = label
                break
        note['error'] = {'classification': classification,
                         'message_sha256': hashlib.sha256(message.encode('utf-8', errors='surrogatepass')).hexdigest(),
                         'message_length_chars': len(message)}
        # Only the existing outcome schema is eligible. Its raw JSON is never
        # copied: every retained value must match a fixed enum or scalar type.
        _, separator, suffix = message.rpartition('; diagnostic=')
        if classification == 'OWNED_INFERENCE_FAILED' and separator and len(suffix) <= 8192:
            try:
                diagnostic = json.loads(suffix)
            except (ValueError, RecursionError):
                diagnostic = None
            if (type(diagnostic) is dict and type(diagnostic.get('schema_version')) is int
                    and diagnostic['schema_version'] == 1
                    and diagnostic.get('kind') == 'owned_flow_runtime_outcome_diagnostic'):
                def project(source, enums=(), flags=(), integers=()):
                    projected = {}
                    source = source if type(source) is dict else {}
                    for key, choices in enums:
                        item = source.get(key)
                        if type(item) is str and item in choices:
                            projected[key] = item
                    for key in flags:
                        item = source.get(key)
                        if type(item) is bool:
                            projected[key] = item
                    for key, low, high in integers:
                        item = source.get(key)
                        if type(item) is int and low <= item <= high:
                            projected[key] = item
                    return projected
                observed = {'schema_version': 1, 'kind': 'owned_flow_runtime_outcome_diagnostic'}
                observed.update(project(diagnostic,
                    enums=(('status', ('completed', 'timeout', 'cancelled', 'uncertain', 'unrecognized')),
                           ('rejection_reason', ('OWNED_PROCESS_GROUP_UNRECONCILED', 'INFERENCE_DEADLINE_EXCEEDED', 'CANCELLED'))),
                    flags=('returncode_present', 'leader_returncode_present', 'process_tree_exit_verified',
                           'diagnostic_only', 'release_qualified'),
                    integers=(('returncode', -(2**31), 2**32 - 1), ('leader_returncode', -(2**31), 2**32 - 1))))
                observed['ownership'] = project(diagnostic.get('ownership'),
                    enums=(('scope', ('observed_original_process_group', 'observed_descendants')),),
                    flags=('leader_exit_confirmed', 'observation_failed', 'termination_attempted', 'termination_failed'),
                    integers=tuple((key, 0, 65536) for key in
                                   ('registered_member_count', 'remaining_member_count', 'unknown_member_count')))
                observed['deadline'] = project(diagnostic.get('deadline'),
                    flags=('terminated', 'leader_exit_confirmed'))
                note['owned_runtime_diagnostic'] = observed
    deadline = value.get('deadline')
    if type(deadline) is dict:
        note['deadline'] = {}
        for key in ('deadline_ms', 'elapsed_ms', 'pid', 'terminated',
                    'leader_exit_confirmed', 'process_tree_exit_verified'):
            item = deadline.get(key)
            if type(item) is bool and key in ('terminated', 'leader_exit_confirmed', 'process_tree_exit_verified'):
                note['deadline'][key] = item
            elif type(item) is int and 0 <= item < 2**32 and key in ('deadline_ms', 'elapsed_ms', 'pid'):
                note['deadline'][key] = item
    return json.dumps(note, ensure_ascii=True, allow_nan=False, separators=(',', ':'))


@pytest.mark.parametrize('message,classification', [
    ('Owned inference failed: PRIVATE-DETAIL', 'OWNED_INFERENCE_FAILED'),
    ('Owned inference returned no bounded result', 'OWNED_INFERENCE_RESULT_UNAVAILABLE'),
    ('Batch input changed during execution', 'BATCH_INPUT_CHANGED'),
    ('Batch input checksum mismatch', 'BATCH_INPUT_CHECKSUM_MISMATCH'),
    ('Package checksum mismatch: PRIVATE-PATH', 'PACKAGE_CHECKSUM_MISMATCH'),
    ('Runtime worker exited before result publication', 'UNCLASSIFIED'),
])
def test_batch_failure_note_keeps_only_fixed_error_classification(message, classification):
    note = json.loads(_batch_failure_note({'results': [{'result': {
        'status': 'error', 'rejection_reason': 'EXECUTION_ERROR', 'error': message,
        'deadline': {'deadline_ms': 30000, 'elapsed_ms': 17, 'terminated': False,
                     'private': 'PRIVATE-DEADLINE'},
        'environment': {'SECRET': 'PRIVATE-ENV'}, 'argv': ['PRIVATE-ARGV'],
    }}]}))
    assert note == {'status': 'error', 'rejection_reason': 'EXECUTION_ERROR',
                    'error': {'classification': classification,
                              'message_sha256': hashlib.sha256(message.encode()).hexdigest(),
                              'message_length_chars': len(message)},
                    'deadline': {'deadline_ms': 30000, 'elapsed_ms': 17, 'terminated': False}}


@pytest.mark.parametrize('private_text', [
    'password="PRIVATE QUOTED PASSWORD"',
    'Authorization: Basic PRIVATE-BASIC-AUTHORIZATION',
    'command=/PRIVATE/PATH --token PRIVATE-TOKEN-VALUE',
    'an unrecognized error with PRIVATE-UNKNOWN-CONTENT',
])
def test_batch_failure_note_never_exports_arbitrary_error_text(private_text):
    diagnostic = {'schema_version': 1, 'kind': 'owned_flow_runtime_outcome_diagnostic',
        'status': 'uncertain', 'returncode_present': True, 'returncode': -9,
        'leader_returncode_present': False, 'leader_returncode': None,
        'rejection_reason': 'OWNED_PROCESS_GROUP_UNRECONCILED',
        'ownership': {'scope': 'observed_original_process_group',
            'leader_exit_confirmed': True, 'registered_member_count': 2,
            'remaining_member_count': 0, 'unknown_member_count': 1,
            'observation_failed': False, 'termination_attempted': True,
            'termination_failed': False, 'unknown': {'nested': private_text}},
        'deadline': {'terminated': True, 'leader_exit_confirmed': True, 'unknown': private_text},
        'process_tree_exit_verified': False, 'diagnostic_only': True,
        'release_qualified': False, 'unknown': {'nested': [private_text]}}
    message = 'Owned inference failed: ' + private_text + '; diagnostic=' + json.dumps(diagnostic)
    encoded = _batch_failure_note({'results': [{'result': {
        'status': 'error', 'rejection_reason': 'EXECUTION_ERROR', 'error': message}}]})
    note = json.loads(encoded)
    for private in ('PRIVATE', 'password', 'Authorization', 'command=', '--token', 'nested'):
        assert private not in encoded
    assert note['error'] == {'classification': 'OWNED_INFERENCE_FAILED',
                            'message_sha256': hashlib.sha256(message.encode()).hexdigest(),
                            'message_length_chars': len(message)}
    diagnostic['ownership'].pop('unknown'); diagnostic['deadline'].pop('unknown'); diagnostic.pop('unknown')
    diagnostic.pop('leader_returncode')  # Only fixed enum/bool/int values are retained.
    assert note['owned_runtime_diagnostic'] == diagnostic
    assert len(encoded.encode('utf-8')) <= 4096


def test_batch_failure_note_reprojects_only_strict_diagnostic_values():
    diagnostic = {'schema_version': 1, 'kind': 'owned_flow_runtime_outcome_diagnostic',
        'status': 'PRIVATE-STATUS', 'returncode_present': 'PRIVATE-FLAG', 'returncode': True,
        'leader_returncode_present': True, 'leader_returncode': 2**60,
        'rejection_reason': {'nested': 'PRIVATE-REASON'},
        'ownership': {'scope': 'PRIVATE-SCOPE', 'leader_exit_confirmed': 'PRIVATE-BOOL',
                      'registered_member_count': True, 'remaining_member_count': -1,
                      'unknown_member_count': 65537, 'observation_failed': {'PRIVATE': True}},
        'deadline': {'terminated': ['PRIVATE'], 'leader_exit_confirmed': 1},
        'process_tree_exit_verified': 'PRIVATE', 'diagnostic_only': 'PRIVATE', 'release_qualified': 'PRIVATE'}
    encoded = _batch_failure_note({'results': [{'result': {
        'status': 'PRIVATE-RESULT-STATUS', 'rejection_reason': 'PRIVATE-RESULT-REASON',
        'error': 'Owned inference failed: PRIVATE; diagnostic=' + json.dumps(diagnostic),
        'deadline': {'elapsed_ms': 1.5, 'pid': 2**10000, 'terminated': 'PRIVATE'},
    }}]})
    note = json.loads(encoded)
    assert 'PRIVATE' not in encoded
    assert note['owned_runtime_diagnostic'] == {'schema_version': 1,
        'kind': 'owned_flow_runtime_outcome_diagnostic', 'leader_returncode_present': True,
        'ownership': {}, 'deadline': {}}
    assert 'status' not in note and 'rejection_reason' not in note and note['deadline'] == {}


def test_batch_failure_note_bounds_values_without_string_coercion():
    class Hostile:
        def __str__(self):
            pytest.fail('Failure diagnostics must not stringify unknown values')
        __repr__ = __str__
    note = _batch_failure_note({'results': [{'result': {
        'status': 'error', 'rejection_reason': 'EXECUTION_ERROR', 'error': Hostile(),
        'deadline': {'deadline_ms': 30000, 'private': Hostile(), 'pid': 2**10000}}}]})
    assert json.loads(note)['error'] == {'classification': 'UNAVAILABLE'}
    huge = _batch_failure_note({'results': [{'result': {
        'status': 'error', 'error': 'PRIVATE' * 10000}}]})
    assert len(huge.encode('utf-8')) <= 4096 and 'PRIVATE' not in huge


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
    assert result['results'][0]['result']['rejection_reason'] == 'INPUT_CHANGED', _batch_failure_note(result)
    assert result['results'][0]['result']['final_verdict'] == 'REVIEW', _batch_failure_note(result)


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
