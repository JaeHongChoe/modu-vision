"""Root-only causal controls for the exact native small-file allocation path.

The sibling observer delegates real FileIO and SHA exactly once and checks every
retained FD closes. It provides no invented input bytes or validation result.
These declarations do not qualify compiled Native latency or an application.
"""
import os
import subprocess

import pytest
from backend.engine import runtime_update as update
from backend.tests.test_runtime_update_large_file_readinto import (
    invoke, joined, original_check_file, payload_of, row_for,
)


@pytest.fixture(autouse=True)
def no_child(monkeypatch):
    monkeypatch.setattr(subprocess.Popen, '__init__', lambda *a, **k: pytest.fail('No child permitted'))


@pytest.mark.parametrize('size', [1, 37, 8191, 65535, 1024**2 - 1])
def test_small_native_update_uses_bounded_private_buffer_after_original_proof(size, tmp_path, monkeypatch):
    payload = payload_of(size); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    old = invoke(monkeypatch, original_check_file, path, row)
    new = invoke(monkeypatch, update._check_file, path, row)
    assert old[0] is new[0] is None
    assert joined(old) == joined(new) == path.read_bytes() == payload
    assert old[1][-1][1] == new[1][-1][1] == 0, 'Real complete EOF remains mandatory'
    assert old[3] == new[3], 'Every original reached ordered fresh guard remains exact'
    assert all(0 < len(c['bytes']) <= min(1024**2, size + 1) for result in (old, new) for c in result[2]), 'Complete original application chunks remain bounded'
    assert old[2] and all(c['type'] is bytes and c['buffer'] is None for c in old[2])
    # The original product reaches all proof assertions above, then fails here.
    assert new[2] and all(c['type'] is memoryview for c in new[2]), 'Small native SHA still receives newly allocated bytes'
    buffer = new[2][0]['buffer']
    assert type(buffer) is bytearray and len(buffer) == min(1024**2, size + 1)
    assert all(c['buffer'] is buffer for c in new[2]), 'Buffer belongs only to this invocation'


def test_small_native_repeated_calls_keep_private_buffers_and_fresh_guards(tmp_path, monkeypatch):
    payload = payload_of(65535); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    first = invoke(monkeypatch, update._check_file, path, row)
    second = invoke(monkeypatch, update._check_file, path, row)
    assert first[0] is second[0] is None and joined(first) == joined(second) == payload
    assert first[3] == second[3] and first[1][-1][1] == second[1][-1][1] == 0
    one, two = first[2][0]['buffer'], second[2][0]['buffer']
    assert type(one) is type(two) is bytearray and len(one) == len(two) == 65536 and one is not two
    assert all(c['buffer'] is one for c in first[2]) and all(c['buffer'] is two for c in second[2])


@pytest.mark.parametrize('kind', ['growth', 'truncate', 'checksum', 'size', 'read-error', 'keyboard', 'system-exit'])
def test_small_native_preserves_first_refusal_and_exact_error_identity(kind, tmp_path, monkeypatch):
    payload = payload_of(37); path = tmp_path / 'member'; row = row_for(payload)
    injected = OSError('owned raw failure') if kind == 'read-error' else KeyboardInterrupt('owned interruption') if kind == 'keyboard' else SystemExit(23)
    results = []
    for function in (original_check_file, update._check_file):
        path.write_bytes(payload); selected = dict(row); before_read = after_read = None
        if kind == 'growth':
            def after_read():
                with path.open('ab') as writer: writer.write(b'GROWN')
        elif kind == 'truncate':
            def before_read(): path.write_bytes(payload[:9])
        elif kind == 'checksum': selected['sha256'] = '0' * 64
        elif kind == 'size': selected['size'] += 1
        else:
            def before_read(): raise injected
        results.append(invoke(monkeypatch, function, path, selected, before_read=before_read, after_read=after_read))
    old, new = results
    assert old[0] is not None and new[0] is not None and old[3] == new[3]
    if kind in ('read-error', 'keyboard', 'system-exit'): assert old[0] is new[0] is injected
    else:
        expected = 'Update artifact grew while reading' if kind == 'growth' else 'Update artifact size differs' if kind == 'size' else 'Update artifact checksum differs'
        assert type(old[0]) is type(new[0]) is update.UpdateError and str(old[0]) == str(new[0]) == expected
    if kind == 'size': assert old[1] == new[1] == old[2] == new[2] == []


@pytest.mark.parametrize('kind', ['inode-replace', 'post-read-symlink'])
def test_small_native_preserves_fresh_post_read_namespace_refusal(kind, tmp_path, monkeypatch):
    payload = payload_of(37); path = tmp_path / 'member'; alternate = tmp_path / 'alternate'; row = row_for(payload)
    results = []
    for function in (original_check_file, update._check_file):
        if path.is_symlink(): path.unlink()
        path.write_bytes(payload); alternate.write_bytes(payload)
        def after_read():
            if kind == 'inode-replace': os.replace(alternate, path)
            else:
                path.unlink(); path.symlink_to(alternate.name)
        results.append(invoke(monkeypatch, function, path, row, after_read=after_read))
    old, new = results
    expected = 'Update input identity changed while reading' if kind == 'inode-replace' else 'Update storage cannot follow links'
    assert type(old[0]) is type(new[0]) is update.UpdateError and str(old[0]) == str(new[0]) == expected
    assert joined(old) == joined(new) == payload and old[3] == new[3]
    assert old[1][-1][1] == new[1][-1][1] == 0


@pytest.mark.parametrize('size', [37, 65535])
def test_small_copy_path_keeps_original_bytes_writer_and_guards(size, tmp_path, monkeypatch):
    payload = payload_of(size); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    before_copy, after_copy = tmp_path / 'old-copy', tmp_path / 'new-copy'
    old = invoke(monkeypatch, original_check_file, path, row, copy_to=before_copy)
    new = invoke(monkeypatch, update._check_file, path, row, copy_to=after_copy)
    assert old[0] is new[0] is None and joined(old) == joined(new) == payload
    assert before_copy.read_bytes() == after_copy.read_bytes() == payload
    assert old[1] == new[1] and old[3] == new[3] and new[1][-1][1] == 0
    assert all(c['type'] is bytes and c['buffer'] is None for c in new[2])


@pytest.mark.parametrize('size', [37, 65535])
def test_foreign_small_reader_never_uses_foreign_readinto(size, tmp_path, monkeypatch):
    payload = payload_of(size); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    old = invoke(monkeypatch, original_check_file, path, row, foreign=True)
    new = invoke(monkeypatch, update._check_file, path, row, foreign=True)
    assert old[0] is new[0] is None and joined(old) == joined(new) == payload
    assert old[1] == new[1] and old[3] == new[3] and new[1][-1][1] == 0
    assert all(c['type'] is bytes and c['buffer'] is None for c in new[2])


@pytest.mark.parametrize('kind', ['size-mismatch', 'existing-copy'])
def test_small_pre_read_refusal_keeps_full_guard_and_protected_copy(kind, tmp_path, monkeypatch):
    payload = payload_of(37); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    copy = tmp_path / 'copy'; copy.write_bytes(b'protected-copy')
    if kind == 'size-mismatch': row['size'] += 1
    args = {} if kind == 'size-mismatch' else {'copy_to': copy}
    old = invoke(monkeypatch, original_check_file, path, row, **args)
    new = invoke(monkeypatch, update._check_file, path, row, **args)
    expected = update.UpdateError if kind == 'size-mismatch' else FileExistsError
    assert type(old[0]) is type(new[0]) is expected
    if kind == 'size-mismatch': assert str(old[0]) == str(new[0]) == 'Update artifact size differs'
    assert old[1] == new[1] == old[2] == new[2] == [] and old[3] == new[3]
    assert path.read_bytes() == payload and copy.read_bytes() == b'protected-copy'
