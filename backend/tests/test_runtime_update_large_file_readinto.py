"""Root-only real-native-reader/SHA controls. Author execution and imports: none.

The oracle is exact reviewed c489 _check_file except its name. Each underlying
FileIO read and SHA update delegates exactly once. The exact BufferedReader
uses the original retained real FD; the observer contains no invented bytes or
futures. No service/frame/performance/compiled acceptance is claimed.
"""
from contextlib import contextmanager
import errno
import hashlib
import io
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
            read_size=min(1024**2,before.st_size+1)
            while chunk:=reader.read(read_size):
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


def payload_of(size):
    return (b'full-original-byte-sequence-' * ((size // 28) + 1))[:size]


def row_for(payload):
    return {'path': 'member', 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(), 'executable': False}


def invoke(monkeypatch, function, path, row, *, copy_to=None, before_read=None, after_read=None, foreign=False):
    original_fdopen, original_open, original_close = os.fdopen, os.open, os.close
    original_stat, original_fstat, original_sha = os.stat, os.fstat, hashlib.sha256
    original_file = update._file
    raw_reads, sha_chunks, guards, opened, closed = [], [], [], [], []
    error = None

    def stat_probe(name, *args, **kwargs):
        guards.append(('stat', os.fspath(name), args, dict(kwargs)))
        return original_stat(name, *args, **kwargs)

    def fstat_probe(fd):
        guards.append(('fstat',))
        return original_fstat(fd)

    def open_probe(*args, **kwargs):
        fd = original_open(*args, **kwargs); opened.append(fd); return fd

    def close_probe(fd):
        closed.append(fd); return original_close(fd)

    def fdopen_probe(*args, **kwargs):
        native = original_fdopen(*args, **kwargs)
        assert type(native) is io.BufferedReader
        raw = native.detach()
        class ObservedRaw(io.RawIOBase):
            def readable(self): return raw.readable()
            def seekable(self): return raw.seekable()
            def fileno(self): return raw.fileno()
            def tell(self): return raw.tell()
            def seek(self, *args): return raw.seek(*args)
            def readinto(self, target):
                if before_read is not None and not raw_reads: before_read()
                count = raw.readinto(target)
                raw_reads.append((len(target), count))
                if after_read is not None and len(raw_reads) == 1: after_read()
                return count
            def close(self):
                try: raw.close()
                finally: super().close()
        # Exact native BufferedReader, original real retained FD and FileIO.
        return io.BufferedReader(ObservedRaw())

    class ObservedSHA:
        def __init__(self, *args, **kwargs): self.original = original_sha(*args, **kwargs)
        def update(self, chunk):
            sha_chunks.append({'type': type(chunk), 'buffer': chunk.obj if isinstance(chunk, memoryview) else None, 'bytes': bytes(chunk)})
            return self.original.update(chunk)
        def hexdigest(self): return self.original.hexdigest()

    @contextmanager
    def foreign_file(*args, **kwargs):
        with original_file(*args, **kwargs) as (reader, before):
            class ForeignReader:
                def read(self, n): return reader.read(n)
                def readinto(self, target): raise AssertionError('Foreign readinto must not replace original read semantics')
            yield ForeignReader(), before

    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'fdopen', fdopen_probe)
        scoped.setattr(os, 'open', open_probe)
        scoped.setattr(os, 'close', close_probe)
        scoped.setattr(os, 'stat', stat_probe)
        scoped.setattr(os, 'fstat', fstat_probe)
        scoped.setattr(hashlib, 'sha256', ObservedSHA)
        if foreign: scoped.setattr(update, '_file', foreign_file)
        try: function(path, row, copy_to=copy_to)
        except BaseException as caught: error = caught
    assert opened and opened == closed, 'Every original retained input FD closes'
    for fd in opened:
        with pytest.raises(OSError) as missing: original_fstat(fd)
        assert missing.value.errno == errno.EBADF
    return error, raw_reads, sha_chunks, guards


def joined(result):
    return b''.join(c['bytes'] for c in result[2])


@pytest.mark.parametrize('size', [1024**2, 1024**2 + 37, 3 * 1024**2 + 19])
def test_real_large_native_sha_uses_one_private_buffer_after_full_bytes_eof_and_original_guards(size, tmp_path, monkeypatch):
    payload = payload_of(size); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    old = invoke(monkeypatch, original_check_file, path, row)
    new = invoke(monkeypatch, update._check_file, path, row)
    assert old[0] is new[0] is None
    assert joined(old) == joined(new) == path.read_bytes() == payload
    assert old[1][-1][1] == new[1][-1][1] == 0, 'Genuine original raw EOF retained'
    assert old[3] == new[3], 'Every original ordered fresh stat/fstat guard retained'
    assert old[2] and all(c['type'] is bytes for c in old[2])
    # Genuine old-code RED occurs only after actual SHA/full bytes/EOF/OFD controls.
    assert new[2] and all(c['type'] is memoryview for c in new[2]), 'Large native SHA still receives newly allocated bytes'
    buffer = new[2][0]['buffer']
    assert type(buffer) is bytearray and len(buffer) == 1024**2
    assert all(c['buffer'] is buffer for c in new[2]), 'One invocation must retain one bounded private buffer'


def test_two_real_calls_never_share_buffer_or_skip_fresh_guards(tmp_path, monkeypatch):
    payload = payload_of(2 * 1024**2 + 19); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    first = invoke(monkeypatch, update._check_file, path, row)
    second = invoke(monkeypatch, update._check_file, path, row)
    assert first[0] is second[0] is None and joined(first) == joined(second) == payload
    assert first[3] == second[3] and first[1][-1][1] == second[1][-1][1] == 0
    one, two = first[2][0]['buffer'], second[2][0]['buffer']
    assert type(one) is type(two) is bytearray and one is not two
    assert all(c['buffer'] is one for c in first[2]) and all(c['buffer'] is two for c in second[2])


@pytest.mark.parametrize('size', [0, 37, 65535, 1024**2 - 1])
def test_small_native_file_keeps_reviewed_read_cap_path(size, tmp_path, monkeypatch):
    payload = payload_of(size); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    old = invoke(monkeypatch, original_check_file, path, row)
    new = invoke(monkeypatch, update._check_file, path, row)
    assert old[0] is new[0] is None and joined(old) == joined(new) == payload
    assert old[1] == new[1] and old[3] == new[3] and new[1][-1][1] == 0
    assert all(c['type'] is bytes and c['buffer'] is None for c in new[2])


def test_large_copy_writer_keeps_original_read_bytes_and_output(tmp_path, monkeypatch):
    payload = payload_of(2 * 1024**2 + 19); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    before_dest, after_dest = tmp_path / 'before-copy', tmp_path / 'after-copy'
    old = invoke(monkeypatch, original_check_file, path, row, copy_to=before_dest)
    new = invoke(monkeypatch, update._check_file, path, row, copy_to=after_dest)
    assert old[0] is new[0] is None and joined(old) == joined(new) == payload
    assert before_dest.read_bytes() == after_dest.read_bytes() == payload
    assert old[1] == new[1] and old[3] == new[3] and new[1][-1][1] == 0
    assert all(c['type'] is bytes and c['buffer'] is None for c in new[2])


@pytest.mark.parametrize('size', [37, 2 * 1024**2 + 19])
def test_custom_reader_keeps_original_read_and_never_calls_foreign_readinto(size, tmp_path, monkeypatch):
    payload = payload_of(size); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    old = invoke(monkeypatch, original_check_file, path, row, foreign=True)
    new = invoke(monkeypatch, update._check_file, path, row, foreign=True)
    assert old[0] is new[0] is None and joined(old) == joined(new) == payload
    assert old[1] == new[1] and old[3] == new[3] and new[1][-1][1] == 0
    assert all(c['type'] is bytes and c['buffer'] is None for c in new[2])


@pytest.mark.parametrize('kind', ['growth', 'truncate', 'checksum', 'read-error', 'keyboard', 'system-exit'])
def test_large_native_first_refusal_and_original_exception_identity(kind, tmp_path, monkeypatch):
    payload = payload_of(2 * 1024**2 + 19); path = tmp_path / 'member'; row = row_for(payload)
    injected = OSError('owned raw read failure') if kind == 'read-error' else KeyboardInterrupt('owned raw read interruption') if kind == 'keyboard' else SystemExit(23)
    results = []
    for function in (original_check_file, update._check_file):
        path.write_bytes(payload); selected = dict(row); before_read = after_read = None
        if kind == 'growth':
            def after_read():
                with path.open('ab') as writer: writer.write(b'GROWN')
        elif kind == 'truncate':
            def before_read(): path.write_bytes(payload[:9])
        elif kind == 'checksum': selected['sha256'] = '0' * 64
        else:
            def before_read(): raise injected
        results.append(invoke(monkeypatch, function, path, selected, before_read=before_read, after_read=after_read))
    old, new = results
    assert old[0] is not None and new[0] is not None and old[3] == new[3]
    if kind in ('read-error', 'keyboard', 'system-exit'): assert old[0] is new[0] is injected
    else:
        expected = 'Update artifact grew while reading' if kind == 'growth' else 'Update artifact checksum differs'
        assert type(old[0]) is type(new[0]) is UpdateError and str(old[0]) == str(new[0]) == expected


@pytest.mark.parametrize('kind', ['inode-replace', 'post-read-symlink'])
def test_large_native_post_read_swap_keeps_original_refusal(kind, tmp_path, monkeypatch):
    payload = payload_of(2 * 1024**2 + 19); path = tmp_path / 'member'; alternate = tmp_path / 'alternate'; row = row_for(payload)
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
    assert type(old[0]) is type(new[0]) is UpdateError and str(old[0]) == str(new[0]) == expected
    assert joined(old) == joined(new) == payload and old[3] == new[3]
    assert old[1][-1][1] == new[1][-1][1] == 0


def test_large_size_mismatch_refuses_before_read_or_sha(tmp_path, monkeypatch):
    payload = payload_of(1024**2); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload); row['size'] += 1
    old = invoke(monkeypatch, original_check_file, path, row)
    new = invoke(monkeypatch, update._check_file, path, row)
    assert type(old[0]) is type(new[0]) is UpdateError and str(old[0]) == str(new[0]) == 'Update artifact size differs'
    assert old[1] == new[1] == old[2] == new[2] == [] and old[3] == new[3]


def test_existing_large_copy_destination_refuses_before_read_or_sha(tmp_path, monkeypatch):
    payload = payload_of(1024**2); path = tmp_path / 'member'; path.write_bytes(payload); row = row_for(payload)
    destination = tmp_path / 'copy'; destination.write_bytes(b'protected-copy')
    old = invoke(monkeypatch, original_check_file, path, row, copy_to=destination)
    new = invoke(monkeypatch, update._check_file, path, row, copy_to=destination)
    assert type(old[0]) is type(new[0]) is FileExistsError
    assert old[1] == new[1] == old[2] == new[2] == [] and old[3] == new[3]
    assert destination.read_bytes() == b'protected-copy'
