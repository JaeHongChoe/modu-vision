"""A path still names the file behind an open descriptor, compared like for like on every platform.

Windows hosted run (S1-06 ingest): ``os.stat(path)`` and ``os.fstat(fd)`` report different ``st_dev``/``st_ino`` for the
same file there, so comparing them refused every managed copy with "Source file changed". The divergence is emulated
by a path stat that reports another device; a real replacement between reading and checking must still be refused.
"""
import os
from pathlib import Path

import pytest


class _OtherDevice:
    """A path stat as Windows reports it: same file, but device/file id taken from a different system call."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        value = getattr(self._real, name)
        return value + 1 if name in ('st_dev', 'st_ino') else value


def _diverging_path_stat(monkeypatch, target: Path):
    real_stat = os.stat
    resolved = str(target.resolve())

    def stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if not isinstance(path, int) and kwargs.get('follow_symlinks', True) and os.path.abspath(os.fspath(path)) == resolved:
            return _OtherDevice(result)
        return result

    monkeypatch.setattr(os, 'stat', stat)


def test_a_managed_copy_reads_its_source_although_a_path_stat_names_another_device(tmp_path, monkeypatch):
    from backend.engine.dataset_fingerprint import source_artifact_identity
    source = tmp_path / 'source'
    source.mkdir()
    image = source / 'source.png'
    image.write_bytes(b'original fixture bytes')
    _diverging_path_stat(monkeypatch, image)
    with source_artifact_identity(source, 'source.png') as (handle, digest, size):
        assert handle.read() == b'original fixture bytes' and size == 22 and len(digest) == 64


@pytest.mark.skipif(os.name == 'nt', reason='Windows does not let another process replace or delete a file that is open')
def test_a_source_replaced_between_reading_and_the_check_is_still_refused(tmp_path, monkeypatch):
    from backend.engine import dataset_fingerprint
    source = tmp_path / 'source'
    source.mkdir()
    image = source / 'source.png'
    image.write_bytes(b'original fixture bytes')
    replacement = source / 'other.bin'
    replacement.write_bytes(b'replaced')
    real = dataset_fingerprint.stat_by_handle

    def replaced_first(path):
        os.replace(replacement, image)  # another process swaps the file after it was read
        return real(path)

    monkeypatch.setattr(dataset_fingerprint, 'stat_by_handle', replaced_first)
    with pytest.raises(ValueError, match='changed while computing'):
        with dataset_fingerprint.source_artifact_identity(source, 'source.png'):
            pass


def test_the_remote_operation_lock_opens_although_a_path_stat_names_another_device(tmp_path, monkeypatch):
    from backend.remote.operations import _open_operation_lock
    lock = tmp_path / 'operation.lock'
    lock.write_bytes(b'')
    _diverging_path_stat(monkeypatch, lock)
    descriptor = _open_operation_lock(lock)
    os.close(descriptor)


@pytest.mark.skipif(os.name == 'nt', reason='Windows does not let another process replace or delete a file that is open')
def test_names_descriptor_tells_the_same_file_from_a_replacement(tmp_path):
    from backend.engine.file_identity import names_descriptor
    path = tmp_path / 'a.bin'
    path.write_bytes(b'a')
    descriptor = os.open(path, os.O_RDONLY)
    try:
        assert names_descriptor(path, descriptor)
        other = tmp_path / 'b.bin'
        other.write_bytes(b'b')
        os.replace(other, path)
        assert not names_descriptor(path, descriptor)
        path.unlink()
        assert not names_descriptor(path, descriptor), 'a path that no longer exists names nothing'
    finally:
        os.close(descriptor)
