"""Lossless bounded transport, not model/quality acceptance.

These Source-authored controls are executed only by Root. They use real files
and the production codec; no inference is substituted in the parity contract.
"""
import base64
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import struct
import sys
import time
import zlib

import pytest


def codec():
    try:
        return importlib.import_module('backend.engine.flow_result_bundle')
    except ModuleNotFoundError:
        pytest.fail('Complete bounded result transport is missing')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def array(values, dtype='float32'):
    raw = struct.pack('<' + ('f' if dtype == 'float32' else 'B') * len(values), *values)
    return {'dtype': dtype, 'encoding': 'zlib_base64', 'shape': [1, len(values)],
            'data': base64.b64encode(zlib.compress(raw)).decode('ascii')}


def result():
    crop = {'roi_id': 'roi1', 'source_node_id': 'inspect', 'label': 'defect',
            'bbox': [0, 0, 2, 1], 'verdict': 'NG', 'defect_score': 0.5,
            'crop_thumbnail': 'data:image/png;base64,controlled-display',
            'segmentation_classes': [{'class_id': 1, 'mask': array([0, 1], 'uint8'),
                                      'probability': array([0.1, 0.5])}],
            'unknown_complete_field': {'ordered': [None, True, 3, 1.25, '↑']}}
    step = {'node_id': 'inspect', 'status': 'passed', 'input_payload_type': 'roi',
            'output_payload_type': 'result', 'input_count': 1, 'output_count': 1,
            'branch_verdict': 'NG', 'selected_edge_ids': ['fail'], 'skip_reason': None,
            'latency_ms': 1.25, 'artifacts': [{'roi_id': 'roi1', 'evidence': copy.deepcopy(crop)}]}
    return {'status': 'success', 'image_path': '/controlled/input.png', 'image_id': 'controlled-uuid',
            'final_verdict': 'NG', 'roi_count': 1, 'defective_roi_count': 1,
            'routed_output_node_id': 'output', 'rejection_reason': 'controlled',
            'inspected_image_size': [2, 1], 'execution_resources': {'device': 'cpu'},
            'execution_steps': [step], 'crops': [crop], 'total_latency_ms': 1.25,
            'unknown_top_level': {'preserved': ['a', 'b']}}


def bundle(tmp_path, value=None):
    mod = codec(); root = tmp_path / 'bundle'
    mod.write_result_bundle(result() if value is None else value, root)
    return mod, root


def manifest(root):
    return json.loads((root / 'manifest.json').read_bytes())


def put_manifest(root, value):
    (root / 'manifest.json').write_bytes(canonical(value))


def replace_member(root, row, data):
    (root / row['path']).write_bytes(data)
    row['size'] = len(data); row['sha256'] = hashlib.sha256(data).hexdigest()


def test_complete_result_larger_than_original_member_cap_round_trips_without_raw_json_parse(tmp_path, monkeypatch):
    mod = codec(); value = result()
    value['execution_steps'] = [copy.deepcopy(value['execution_steps'][0]) for _ in range(3)]
    for index, step in enumerate(value['execution_steps']):
        step['node_id'] = str(index)
        step['artifacts'][0]['complete_display'] = 'x' * (6 * 1024 * 1024)
    root = tmp_path / 'large'
    mod.write_result_bundle(value, root)
    assert (root / 'result.json').stat().st_size > 16 * 1024 * 1024
    original = mod._json_document
    parsed = []
    def observed(fd, name, deadline):
        assert name != 'result.json', 'Oversized complete raw JSON must never be parsed'
        parsed.append(name)
        return original(fd, name, deadline)
    monkeypatch.setattr(mod, '_json_document', observed)
    restored, proof = mod.read_result_bundle(root, deadline=time.monotonic() + 20)
    assert restored == value
    assert proof['raw']['sha256'] == hashlib.sha256(canonical(value)).hexdigest()
    assert parsed and all((root / name).stat().st_size <= 16 * 1024 * 1024 for name in parsed)
    assert proof['file_count'] == len(list(root.iterdir()))


def test_full_unknown_fields_order_types_and_raw_bytes_are_preserved(tmp_path):
    mod, root = bundle(tmp_path)
    restored, proof = mod.read_result_bundle(root)
    assert restored == result()
    assert (root / 'result.json').read_bytes() == canonical(result())
    assert type(restored['unknown_top_level']['preserved']) is list
    assert restored['crops'][0]['unknown_complete_field']['ordered'] == [None, True, 3, 1.25, '↑']
    assert proof['schema'] == 'FlowResultBundle/v1'


def test_original_nine_field_comparison_sees_changed_mask_and_probability(tmp_path):
    mod, root = bundle(tmp_path)
    restored, _ = mod.read_result_bundle(root)
    from backend.engine.flow_package_runtime import compare_flow_results
    compared = compare_flow_results(result(), restored)
    assert compared['status'] == 'passed'
    assert compared['compared_fields'] == ['final_verdict', 'roi_count', 'defective_roi_count',
        'routed_output_node_id', 'rejection_reason', 'inspected_image_size',
        'execution_resources', 'execution_steps', 'crops']
    changed = copy.deepcopy(restored)
    changed['crops'][0]['segmentation_classes'][0]['mask'] = array([1, 1], 'uint8')
    assert compare_flow_results(result(), changed)['status'] == 'mismatch'
    changed = copy.deepcopy(restored)
    changed['crops'][0]['segmentation_classes'][0]['probability'] = array([0.2, 0.5])
    assert compare_flow_results(result(), changed)['status'] == 'mismatch'


@pytest.mark.parametrize('field', ['file_count', 'data_member_bytes'])
def test_manifest_integer_bounds_reject_bool(tmp_path, field):
    mod, root = bundle(tmp_path); body = manifest(root); body[field] = True
    put_manifest(root, body)
    with pytest.raises(ValueError): mod.read_result_bundle(root)


@pytest.mark.parametrize('field', ['size', 'index'])
def test_member_integer_guards_reject_bool(tmp_path, field):
    mod, root = bundle(tmp_path); body = manifest(root); body['crops'][0][field] = True
    put_manifest(root, body)
    with pytest.raises(ValueError): mod.read_result_bundle(root)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'schema', 'order', 'duplicate', 'path', 'header-list', 'raw-pin'])
def test_manifest_shape_order_paths_and_full_raw_pin_refuse(tmp_path, mutation):
    value = result(); value['crops'].append(copy.deepcopy(value['crops'][0]))
    mod, root = bundle(tmp_path, value); body = manifest(root)
    if mutation == 'missing': del body['header']
    elif mutation == 'extra': body['unexpected'] = 1
    elif mutation == 'schema': body['schema'] = 'foreign'
    elif mutation == 'order': body['crops'].reverse()
    elif mutation == 'duplicate': body['crops'][1] = copy.deepcopy(body['crops'][0])
    elif mutation == 'path': body['header']['path'] = '../header.json'
    elif mutation == 'header-list': replace_member(root, body['header'], b'[]')
    elif mutation == 'raw-pin': body['result']['sha256'] = '0' * 64
    put_manifest(root, body)
    with pytest.raises(ValueError): mod.read_result_bundle(root)


@pytest.mark.parametrize('name', ['orphan.json', 'directory', 'linked.json', 'hardlinked.json'])
def test_orphan_directory_symlink_and_hardlink_refuse(tmp_path, name):
    mod, root = bundle(tmp_path)
    if name == 'directory': (root / name).mkdir()
    elif name == 'linked.json': (root / name).symlink_to(root / 'header.json')
    elif name == 'hardlinked.json': os.link(root / 'header.json', root / name)
    else: (root / name).write_text('{}')
    with pytest.raises(ValueError): mod.read_result_bundle(root)


def test_root_alias_and_member_hardlink_refuse(tmp_path):
    mod, root = bundle(tmp_path); alias = tmp_path / 'alias'; alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError): mod.read_result_bundle(alias)
    external = tmp_path / 'other'; os.link(root / 'header.json', external)
    with pytest.raises(ValueError): mod.read_result_bundle(root)


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e400}'])
def test_duplicate_keys_and_nonfinite_data_never_parse_as_accepted(tmp_path, raw):
    mod, root = bundle(tmp_path); body = manifest(root)
    old_size = body['header']['size']; replace_member(root, body['header'], raw)
    body['data_member_bytes'] += len(raw) - old_size; put_manifest(root, body)
    with pytest.raises(ValueError): mod.read_result_bundle(root)


def test_correctly_repinned_changed_member_still_refuses_complete_raw_identity(tmp_path):
    mod, root = bundle(tmp_path); body = manifest(root)
    header = json.loads((root / 'header.json').read_bytes()); header['final_verdict'] = 'OK'
    old_size = body['header']['size']; replace_member(root, body['header'], canonical(header))
    body['data_member_bytes'] += body['header']['size'] - old_size; put_manifest(root, body)
    with pytest.raises(ValueError, match='complete result'): mod.read_result_bundle(root)


@pytest.mark.parametrize('kind', ['raw', 'member'])
def test_sparse_oversize_files_are_rejected_before_reading(tmp_path, kind):
    mod, root = bundle(tmp_path); body = manifest(root)
    row = body['result'] if kind == 'raw' else body['header']
    size = (64 if kind == 'raw' else 16) * 1024 * 1024 + 1
    with (root / row['path']).open('r+b') as writer: writer.truncate(size)
    row['size'] = size
    if kind == 'member': body['data_member_bytes'] += size
    put_manifest(root, body)
    with pytest.raises(ValueError): mod.read_result_bundle(root)


def test_writer_member_oversize_refuses_instead_of_splitting_or_dropping(tmp_path):
    value = result(); value['crops'][0]['full_display'] = 'x' * (16 * 1024 * 1024)
    with pytest.raises(ValueError): codec().write_result_bundle(value, tmp_path / 'oversize')


def test_file_count_256_boundary_is_exact(tmp_path):
    mod = codec(); value = result(); value['execution_steps'] = [copy.deepcopy(value['execution_steps'][0]) for _ in range(126)]
    value['crops'] = [copy.deepcopy(value['crops'][0]) for _ in range(127)]
    root = tmp_path / 'maximum'; mod.write_result_bundle(value, root)
    restored, proof = mod.read_result_bundle(root); assert restored == value and proof['file_count'] == 256
    value['crops'].append(copy.deepcopy(value['crops'][0]))
    with pytest.raises(ValueError): mod.write_result_bundle(value, tmp_path / 'over-count')


def test_aggregate_manifest_limit_and_expired_absolute_deadline_refuse(tmp_path):
    mod, root = bundle(tmp_path); body = manifest(root); body['data_member_bytes'] = 64 * 1024 * 1024 + 1
    put_manifest(root, body)
    with pytest.raises(ValueError): mod.read_result_bundle(root)
    with pytest.raises(ValueError): mod.read_result_bundle(root, deadline=time.monotonic() - 1)


def test_real_file_mutation_during_read_refuses_and_does_not_return_result(tmp_path, monkeypatch):
    mod, root = bundle(tmp_path); real_read = mod.os.read; changed = []
    target = (root / 'header.json').stat().st_ino
    def mutate(fd, length):
        data = real_read(fd, length)
        if data and os.fstat(fd).st_ino == target and not changed:
            changed.append(True)
            with (root / 'header.json').open('ab') as writer: writer.write(b' ')
        return data
    monkeypatch.setattr(mod.os, 'read', mutate)
    with pytest.raises(ValueError): mod.read_result_bundle(root)
    assert changed == [True]


def test_default_cli_output_and_optional_real_bundle_branch(tmp_path, monkeypatch):
    from backend.engine import flow_package_runtime as runtime
    value = result(); calls = []
    def controlled_result(*args, **kwargs): calls.append((args, kwargs)); return copy.deepcopy(value)
    monkeypatch.setattr(runtime, 'run_flow_package', controlled_result)
    output = tmp_path / 'default.json'
    monkeypatch.setattr(sys, 'argv', ['run_flow.py', '--image', 'controlled.png', '--output', str(output)])
    assert runtime.main() == 0
    assert output.read_text() == json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    root = tmp_path / 'cli-bundle'
    monkeypatch.setattr(sys, 'argv', ['run_flow.py', '--image', 'controlled.png', '--output-bundle', str(root)])
    assert runtime.main() == 0
    assert codec().read_result_bundle(root)[0] == value
    assert len(calls) == 2  # serialization test seam, no model-inference claim


@pytest.mark.parametrize('status,exit_code', [('timeout', 3), ('cancelled', 0)])
def test_bundle_unconfirmed_outcome_keeps_original_full_body_exit_and_no_bundle(tmp_path, monkeypatch, capsys, status, exit_code):
    from backend.engine import flow_package_runtime as runtime
    outcome = {'status': status, 'returncode': None, 'image_id': 'original',
               'ownership': {'uncertain': True}, 'diagnostic_only': True}
    monkeypatch.setattr(runtime, 'run_flow_package', lambda *args, **kwargs: copy.deepcopy(outcome))
    root = tmp_path / 'not-completed'
    monkeypatch.setattr(sys, 'argv', ['run_flow.py', '--image', 'controlled.png', '--output-bundle', str(root)])
    assert runtime.main() == exit_code
    assert capsys.readouterr().out == json.dumps(outcome, ensure_ascii=False, indent=2) + '\n'
    assert not root.exists()  # no list fabrication, successful bundle or receipt


@pytest.mark.parametrize('extra', [['--output', 'legacy.json'], ['--batch', 'batch.json'], ['--verify-only'], ['--preflight'], ['--show-preflight']])
def test_bundle_cli_rejects_ambiguous_or_non_single_image_modes(monkeypatch, extra):
    from backend.engine import flow_package_runtime as runtime
    monkeypatch.setattr(sys, 'argv', ['run_flow.py', '--image', 'controlled.png', '--output-bundle', 'bundle', *extra])
    with pytest.raises(SystemExit) as refused: runtime.main()
    assert refused.value.code == 2


def test_actual_package_builder_includes_codec_in_signed_manifest(tmp_path):
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import get_single_detection_flowchart
    checkpoint = tmp_path / 'job_bundle' / 'best_model.pt'; checkpoint.parent.mkdir()
    checkpoint.write_bytes(b'controlled builder checkpoint; no inference')
    checkpoint.with_name('model_meta.json').write_text(json.dumps({'task': 'detection'}))
    built = build_flow_package(pipeline=get_single_detection_flowchart(job_id='job_bundle'),
        checkpoints={'job_bundle': checkpoint}, output_base_dir=tmp_path / 'exports', package_name='bundle_codec')
    package = Path(built['package_path']); rows = json.loads((package / 'manifest.json').read_bytes())['files']
    member = next(row for row in rows if row['path'] == 'backend/engine/flow_result_bundle.py')
    original = Path(codec().__file__).read_bytes()
    assert (package / member['path']).read_bytes() == original
    assert member['size'] == len(original) and member['sha256'] == hashlib.sha256(original).hexdigest()


def captured_require(mod, monkeypatch):
    original = mod._require; observed = []
    def checked(condition, message):
        try:
            return original(condition, message)
        except BaseException as error:
            observed.append(error)
            raise
    monkeypatch.setattr(mod, '_require', checked)
    return observed


def closing_failure(mod, monkeypatch, *, directory_only=False, secondary=None):
    real_close, real_fstat = os.close, os.fstat
    secondary = OSError('private cleanup text must not enter notes') if secondary is None else secondary
    closed = []
    def close(fd):
        directory = __import__('stat').S_ISDIR(real_fstat(fd).st_mode)
        real_close(fd); closed.append(fd)
        if not directory_only or directory:
            raise secondary
    monkeypatch.setattr(mod.os, 'close', close)
    return real_close, real_fstat, secondary, closed


def assert_primary_and_closed(error, observed, closed, real_fstat):
    assert observed and error is observed[0]
    assert type(error) is ValueError
    assert error.__notes__ == ['Result bundle descriptor cleanup failed (OSError); original error retained']
    assert 'private' not in str(error.__notes__)
    assert closed
    for fd in closed:
        with pytest.raises(OSError): real_fstat(fd)


def test_writer_member_refusal_survives_real_close_secondary_failure(tmp_path, monkeypatch):
    mod = codec(); directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    observed = captured_require(mod, monkeypatch)
    real_close, real_fstat, _, closed = closing_failure(mod, monkeypatch)
    try:
        with pytest.raises(ValueError, match='Result JSON member exceeds its bound') as caught:
            mod._write(directory, 'oversize.json', {'x': 1}, 1, None)
        assert_primary_and_closed(caught.value, observed, closed, real_fstat)
    finally:
        real_close(directory)


def test_file_bound_refusal_survives_real_close_secondary_failure(tmp_path, monkeypatch):
    mod = codec(); (tmp_path / 'oversize.json').write_bytes(b'1234')
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    observed = captured_require(mod, monkeypatch)
    real_close, real_fstat, _, closed = closing_failure(mod, monkeypatch)
    try:
        with pytest.raises(ValueError, match='bounded unaliased regular') as caught:
            mod._file(directory, 'oversize.json', 2, None)
        assert_primary_and_closed(caught.value, observed, closed, real_fstat)
    finally:
        real_close(directory)


def test_successful_file_read_close_failure_does_not_return_a_pin(tmp_path, monkeypatch):
    mod = codec(); (tmp_path / 'member.json').write_bytes(b'{}')
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_close, real_fstat, secondary, closed = closing_failure(mod, monkeypatch)
    try:
        with pytest.raises(OSError) as caught:
            mod._file(directory, 'member.json', 2, None)
        assert caught.value is secondary and len(closed) == 1
        with pytest.raises(OSError): real_fstat(closed[0])
    finally:
        real_close(directory)


def test_reader_raw_pin_refusal_survives_root_close_secondary_failure(tmp_path, monkeypatch):
    mod, root = bundle(tmp_path); body = manifest(root)
    body['result']['sha256'] = '0' * 64; put_manifest(root, body)
    observed = captured_require(mod, monkeypatch)
    _, real_fstat, _, closed = closing_failure(mod, monkeypatch, directory_only=True)
    with pytest.raises(ValueError, match='Result member raw pin differs') as caught:
        mod.read_result_bundle(root)
    assert_primary_and_closed(caught.value, observed, closed, real_fstat)


def test_writer_refusal_survives_root_close_secondary_failure(tmp_path, monkeypatch):
    mod = codec(); monkeypatch.setattr(mod, 'JSON_MEMBER_LIMIT', 1)
    observed = captured_require(mod, monkeypatch)
    _, real_fstat, _, closed = closing_failure(mod, monkeypatch, directory_only=True)
    with pytest.raises(ValueError, match='Result JSON member exceeds its bound') as caught:
        mod.write_result_bundle(result(), tmp_path / 'refused')
    assert_primary_and_closed(caught.value, observed, closed, real_fstat)
    assert not (tmp_path / 'refused' / 'manifest.json').exists()


def test_root_identity_refusal_survives_real_close_secondary_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    mod = codec(); original_fstat = os.fstat
    observed = captured_require(mod, monkeypatch)
    _, real_fstat, _, closed = closing_failure(mod, monkeypatch)
    def changed(fd):
        row = original_fstat(fd)
        values = {key: getattr(row, key) for key in
                  ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
        values['st_ino'] += 1
        return SimpleNamespace(**values)
    monkeypatch.setattr(mod.os, 'fstat', changed)
    with pytest.raises(ValueError, match='Result bundle directory changed') as caught:
        mod._root(tmp_path)
    assert_primary_and_closed(caught.value, observed, closed, real_fstat)


@pytest.mark.parametrize('secondary,category', [
    (OSError('private'), 'OSError'), (ValueError('private'), 'ValueError'),
    (KeyboardInterrupt('private'), 'KeyboardInterrupt'), (SystemExit('private'), 'SystemExit'),
    (RuntimeError('private'), 'BaseException')])
def test_primary_object_and_bounded_category_note_survive_every_secondary_kind(tmp_path, monkeypatch, secondary, category):
    mod = codec(); fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    class Primary(ValueError):
        def add_note(self, note):
            raise AssertionError('Subclass note callback must not run')
    primary = Primary('original refusal')
    _, real_fstat, _, closed = closing_failure(mod, monkeypatch, secondary=secondary)
    mod._close(fd, primary)
    assert closed == [fd]
    assert primary.__notes__ == ['Result bundle descriptor cleanup failed (' + category + '); original error retained']
    assert 'private' not in str(primary.__notes__)
    with pytest.raises(OSError): real_fstat(fd)


@pytest.mark.parametrize('flag', ['O_DIRECTORY', 'O_NOFOLLOW'])
def test_optional_bundle_refuses_missing_nofollow_directory_capability_before_creation(tmp_path, monkeypatch, flag):
    mod = codec(); output = tmp_path / 'must-not-exist'
    monkeypatch.delattr(mod.os, flag)
    with pytest.raises(ValueError, match='directory-descriptor support'):
        mod.write_result_bundle(result(), output)
    assert not output.exists()


def test_numeric_encoder_pieces_are_buffered_into_bounded_real_writes(tmp_path, monkeypatch):
    mod = codec(); value = {'values': list(range(200000))}; expected = canonical(value)
    real_write, original_encoded = os.write, mod._encoded
    writes, pieces = [], []
    def encoded(item):
        for piece in original_encoded(item):
            pieces.append(len(piece)); yield piece
    def written(fd, view):
        assert type(view) is memoryview and 0 < len(view) <= 1024 * 1024
        writes.append(len(view)); return real_write(fd, view)
    monkeypatch.setattr(mod, '_encoded', encoded); monkeypatch.setattr(mod.os, 'write', written)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try: pin = mod._write(fd, 'numeric.json', value, 16 * 1024 * 1024, None)
    finally: os.close(fd)
    assert (tmp_path / 'numeric.json').read_bytes() == expected
    assert pin['size'] == len(expected) and pin['sha256'] == hashlib.sha256(expected).hexdigest()
    assert len(pieces) > 200000 and len(writes) == (len(expected) + 1048575) // 1048576
    assert sum(writes) == len(expected)


@pytest.mark.parametrize('characters', [350000, 700000, 1000000])
def test_large_utf8_encoder_piece_has_complete_bounded_byte_transport(tmp_path, monkeypatch, characters):
    mod = codec(); value = {'leaf': '↑' * characters}; expected = canonical(value)
    real_write = os.write; lengths = []
    def written(fd, view):
        lengths.append(len(view)); assert len(view) <= 1048576
        return real_write(fd, view)
    monkeypatch.setattr(mod.os, 'write', written)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try: pin = mod._write(fd, 'unicode.json', value, 16 * 1024 * 1024, None)
    finally: os.close(fd)
    assert (tmp_path / 'unicode.json').read_bytes() == expected
    assert pin['sha256'] == hashlib.sha256(expected).hexdigest() and pin['size'] == len(expected)
    assert sum(lengths) == len(expected) and max(lengths) <= 1048576


def test_buffered_writer_retains_real_partial_write_loop_and_full_hash(tmp_path, monkeypatch):
    mod = codec(); value = {'text': '↑' * 1000}; expected = canonical(value)
    real_write = os.write; counts = []
    def partial(fd, view):
        count = real_write(fd, view[:7]); counts.append(count); return count
    monkeypatch.setattr(mod.os, 'write', partial)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try: pin = mod._write(fd, 'partial.json', value, 16 * 1024 * 1024, None)
    finally: os.close(fd)
    assert len(counts) > 1 and sum(counts) == len(expected)
    assert (tmp_path / 'partial.json').read_bytes() == expected
    assert pin['sha256'] == hashlib.sha256(expected).hexdigest() and pin['size'] == len(expected)


def test_buffered_writer_checks_original_absolute_deadline_for_each_piece(tmp_path, monkeypatch):
    mod = codec(); real_encoded = mod._encoded; clock = [0.0]; writes = []
    def advancing(item):
        for index, block in enumerate(real_encoded(item)):
            if index == 1: clock[0] = 11.0
            yield block
    monkeypatch.setattr(mod, '_encoded', advancing)
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])
    real_write = os.write
    def written(fd, view): writes.append(len(view)); return real_write(fd, view)
    monkeypatch.setattr(mod.os, 'write', written)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match='absolute deadline expired'):
            mod._write(fd, 'expired.json', {'x': 1}, 16 * 1024 * 1024, 10.0)
    finally: os.close(fd)
    assert writes == [] and (tmp_path / 'expired.json').read_bytes() == b''


def test_buffered_write_error_object_survives_real_close_secondary(tmp_path, monkeypatch):
    mod = codec(); primary = OSError('original real-write refusal')
    real_write = os.write
    def failed(fd, view):
        real_write(fd, view[:1]); raise primary
    monkeypatch.setattr(mod.os, 'write', failed)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_close, real_fstat, _, closed = closing_failure(mod, monkeypatch)
    try:
        with pytest.raises(OSError) as caught:
            mod._write(directory, 'failed.json', {'x': 1}, 16 * 1024 * 1024, None)
        assert caught.value is primary
        assert primary.__notes__ == ['Result bundle descriptor cleanup failed (OSError); original error retained']
        assert (tmp_path / 'failed.json').read_bytes() == b'{'
        with pytest.raises(OSError): real_fstat(closed[0])
    finally: real_close(directory)


def test_buffered_member_cap_refuses_before_flushing_an_uncommitted_prefix(tmp_path, monkeypatch):
    mod = codec(); writes = []; real_write = os.write
    def written(fd, view): writes.append(len(view)); return real_write(fd, view)
    monkeypatch.setattr(mod.os, 'write', written)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match='Result JSON member exceeds its bound'):
            mod._write(fd, 'refused.json', {'x': 'x' * 32}, 8, None)
    finally: os.close(fd)
    assert writes == [] and (tmp_path / 'refused.json').read_bytes() == b''
