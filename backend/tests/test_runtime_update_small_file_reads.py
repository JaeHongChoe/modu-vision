"""Root-only real-file read allocation controls; author has not executed/imported this module.

The original oracle is verbatim runtime66db _check_file except its name. Reader
observation delegates each real read exactly once. All original fresh path/OFD
checks still execute; no model, app, signature, deadline or performance claim.
"""
from contextlib import contextmanager
import errno
import hashlib
import os
import subprocess

import pytest
from backend.engine import runtime_update as update

UpdateError = update.UpdateError


def _file(*args, **kwargs):
    return update._file(*args, **kwargs)


def original_check_file(path,row,*,copy_to=None):
    digest=hashlib.sha256();total=0
    with _file(path,1024**3,allow_empty=row.get('size')==0 and row.get('executable') is False) as (reader,before):
        if before.st_size!=row['size']:raise UpdateError('Update artifact size differs')
        writer=open(copy_to,'xb') if copy_to is not None else None
        try:
            while chunk:=reader.read(1024**2):
                total+=len(chunk)
                if total>row['size']:raise UpdateError('Update artifact grew while reading')
                digest.update(chunk)
                if writer:writer.write(chunk)
            if digest.hexdigest()!=row['sha256'] or total!=row['size']:raise UpdateError('Update artifact checksum differs')
            if writer:writer.flush();os.fsync(writer.fileno())
        finally:
            if writer:writer.close()


@pytest.fixture(autouse=True)
def no_child(monkeypatch):
    monkeypatch.setattr(subprocess.Popen, '__init__', lambda *a, **k: pytest.fail('No child permitted'))


def row_for(payload):
    return {'path': 'member', 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(), 'executable': False}


def invoke(monkeypatch, function, path, row, *, before_read=None, after_read=None, copy_to=None):
    original_file = update._file
    original_stat, original_fstat = os.stat, os.fstat
    original_open, original_close = os.open, os.close
    reads, chunks, guards, opened, closed = [], [], [], [], []
    error = None

    def stat_probe(name, *args, **kwargs):
        guards.append(('stat', os.fspath(name), args, dict(kwargs)))
        return original_stat(name, *args, **kwargs)

    def fstat_probe(fd):
        guards.append(('fstat',))
        return original_fstat(fd)

    def open_probe(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def close_probe(fd):
        closed.append(fd)
        return original_close(fd)

    @contextmanager
    def observed_file(*args, **kwargs):
        with original_file(*args, **kwargs) as (reader, before):
            class ObservedReader:
                def read(self, n):
                    reads.append(n)
                    if before_read is not None and len(reads) == 1:
                        before_read()
                    chunk = reader.read(n)
                    chunks.append(chunk)
                    if after_read is not None and len(reads) == 1:
                        after_read()
                    return chunk
            yield ObservedReader(), before

    with monkeypatch.context() as scoped:
        scoped.setattr(update, '_file', observed_file)
        scoped.setattr(os, 'stat', stat_probe)
        scoped.setattr(os, 'fstat', fstat_probe)
        scoped.setattr(os, 'open', open_probe)
        scoped.setattr(os, 'close', close_probe)
        try:
            function(path, row, copy_to=copy_to)
        except BaseException as caught:
            error = caught
    assert opened and opened == closed, 'Every original real input FD must close'
    for fd in opened:
        with pytest.raises(OSError) as missing:
            original_fstat(fd)
        assert missing.value.errno == errno.EBADF
    return error, reads, chunks, guards


@pytest.mark.parametrize('size', [0, 1, 37, 4095, 65535])
def test_small_real_file_bounds_requests_after_original_bytes_eof_and_fresh_guard_controls(size, tmp_path, monkeypatch):
    payload = (b'full-original-bytes-' * ((size // 20) + 1))[:size]
    path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    old = invoke(monkeypatch, original_check_file, path, row)
    new = invoke(monkeypatch, update._check_file, path, row)
    assert old[0] is None and new[0] is None
    assert b''.join(old[2]) == b''.join(new[2]) == payload
    assert old[2][-1] == new[2][-1] == b'', 'Original full EOF probe retained'
    assert old[3] == new[3], 'Every original ordered fresh stat/fstat call retained'
    assert path.read_bytes() == payload
    assert old[1] and set(old[1]) == {1024**2}
    # Genuine old-code RED only after byte/EOF/fresh-guard/FD assertions above.
    assert new[1] and max(new[1]) <= size + 1, 'Small file still requests original 1MiB buffer'


@pytest.mark.parametrize('size', [1024**2 - 1, 1024**2, 1024**2 + 37, 2 * 1024**2 + 19])
def test_original_one_mib_ceiling_and_large_file_chunk_sequence_retained(size, tmp_path, monkeypatch):
    payload = (b'0123456789abcdef' * ((size // 16) + 1))[:size]
    path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    old = invoke(monkeypatch, original_check_file, path, row)
    new = invoke(monkeypatch, update._check_file, path, row)
    assert old[0] is None and new[0] is None
    assert old[1] == new[1] and old[2] == new[2] and old[3] == new[3]
    assert set(new[1]) == {1024**2} and new[2][-1] == b''
    assert b''.join(new[2]) == path.read_bytes() == payload


def test_real_copy_writer_full_bytes_and_original_fresh_input_guards(tmp_path, monkeypatch):
    payload = b'copy-is-not-validation-authority' * 7
    path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    old_dest, new_dest = tmp_path / 'original-copy', tmp_path / 'candidate-copy'
    old = invoke(monkeypatch, original_check_file, path, row, copy_to=old_dest)
    new = invoke(monkeypatch, update._check_file, path, row, copy_to=new_dest)
    assert old[0] is None and new[0] is None and old[3] == new[3]
    assert old_dest.read_bytes() == new_dest.read_bytes() == path.read_bytes() == payload
    assert b''.join(old[2]) == b''.join(new[2]) == payload and new[2][-1] == b''


@pytest.mark.parametrize('kind', ['growth', 'truncate', 'checksum', 'size', 'read-error', 'keyboard', 'system-exit'])
def test_original_first_refusal_and_injected_error_identity_retained(kind, tmp_path, monkeypatch):
    payload = b'original-real-file' * 5
    path = tmp_path / 'member'; row = row_for(payload)
    injected = OSError('owned read failure') if kind == 'read-error' else KeyboardInterrupt('owned read interruption') if kind == 'keyboard' else SystemExit(23)
    results = []
    for function in (original_check_file, update._check_file):
        path.write_bytes(payload); selected = dict(row); before_read = after_read = None
        if kind == 'growth':
            def after_read():
                with path.open('ab') as writer: writer.write(b'GROWN')
        elif kind == 'truncate':
            def before_read():
                path.write_bytes(payload[:9])
        elif kind == 'checksum': selected['sha256'] = '0' * 64
        elif kind == 'size': selected['size'] += 1
        elif kind in ('read-error', 'keyboard', 'system-exit'):
            def before_read(): raise injected
        results.append(invoke(monkeypatch, function, path, selected, before_read=before_read, after_read=after_read))
    old, new = results
    assert old[0] is not None and new[0] is not None
    assert old[3] == new[3], 'First-refusal boundary keeps every original guard already reached'
    if kind in ('read-error', 'keyboard', 'system-exit'):
        assert old[0] is new[0] is injected
    else:
        expected = 'Update artifact grew while reading' if kind == 'growth' else 'Update artifact size differs' if kind == 'size' else 'Update artifact checksum differs'
        assert type(old[0]) is type(new[0]) is UpdateError
        assert str(old[0]) == str(new[0]) == expected
    if kind == 'size': assert old[1] == new[1] == []


@pytest.mark.parametrize('kind', ['inode-replace', 'post-read-symlink'])
def test_original_post_read_namespace_swap_refusal_retained(kind, tmp_path, monkeypatch):
    payload = b'unchanged-signed-bytes' * 4
    path = tmp_path / 'member'; alternate = tmp_path / 'alternate'; row = row_for(payload)
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
    assert type(old[0]) is type(new[0]) is UpdateError
    assert str(old[0]) == str(new[0]) == expected
    assert old[3] == new[3]
    assert b''.join(old[2]) == b''.join(new[2]) == payload
    assert old[2][-1] == new[2][-1] == b''


def test_existing_copy_destination_refuses_before_any_read_and_closes_original_fd(tmp_path, monkeypatch):
    payload = b'original-input'; path = tmp_path / 'member'; path.write_bytes(payload)
    destination = tmp_path / 'copy'; destination.write_bytes(b'protected-copy')
    old = invoke(monkeypatch, original_check_file, path, row_for(payload), copy_to=destination)
    new = invoke(monkeypatch, update._check_file, path, row_for(payload), copy_to=destination)
    assert type(old[0]) is type(new[0]) is FileExistsError
    assert old[1] == new[1] == [] and old[3] == new[3]
    assert destination.read_bytes() == b'protected-copy' and path.read_bytes() == payload
