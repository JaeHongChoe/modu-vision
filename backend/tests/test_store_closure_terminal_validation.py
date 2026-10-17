"""Independent original-controller component regressions for reviewed gaps.

Uses the fixed real temporary signed original controller fixture. No new
capability, original producer, frame, process or timeout predicate is patched.
These tests do not provide installed/native or complete OS-tree acceptance.
"""
import argparse
import copy
import ctypes
import os
import sys

import pytest

from backend.engine import application_launch_controller as controller
from backend.engine import application_launch_lease as lease
from backend.tests.test_application_launch_lease import controlled_canary_publication
from backend.tests.test_application_store_writer_closure import component, _finish, _files


def test_original_scoped_terminal_is_publicly_inspectable_and_protocol_type_is_exact(tmp_path):
    with component(tmp_path) as (root, child):
        row = _finish(root, child)
        value = child._source_fixture_value
        binding = row['binding']
        arguments = argparse.Namespace(root=str(root), authority=str(value['authority']),
            pinned_authority_sha256=value['pinned_authority_sha256'],
            expected_installation_id=binding['installation_id'],
            expected_update_id=binding['update_id'],
            expected_database_fence=str(binding['database_pointer']['fence']))
        before = _files(root)
        observed = controller.inspect(arguments)
        assert observed['status'] == 'writer_store_closed'
        assert observed['nonce'] == row['nonce']
        assert observed['release_ready'] is False
        original_record = lease._load(root)
        assert lease._record(root, row['nonce'], record=original_record) == original_record
        for invalid_version in (5.0, '5', True):
            malformed = copy.deepcopy(original_record)
            malformed['protocol_version'] = invalid_version
            with pytest.raises(ValueError):
                lease._record(root, row['nonce'], record=malformed)
        assert _files(root) == before


def _mark_owned_fixture(path):
    if sys.platform == 'darwin':
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        setter = library.setxattr
        setter.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                           ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
        setter.restype = ctypes.c_int
        body = ctypes.create_string_buffer(b'refuse')
        result = setter(os.fsencode(path), b'org.moduvision.fixture', body, 6, 0, 1)
        assert result == 0, ctypes.get_errno()
    elif sys.platform.startswith('linux'):
        os.setxattr(path, 'user.moduvision.fixture', b'refuse', follow_symlinks=False)
    else:
        pytest.skip('Original supported nofollow metadata platforms only')


@pytest.mark.parametrize('target', ['parent', 'directory', 'receipt'])
def test_original_scoped_terminal_rejects_changed_closure_metadata_without_repair(tmp_path, target):
    with component(tmp_path) as (root, child):
        row = _finish(root, child)
        parent = root/'.application-writer-closures'
        directory = parent/row['nonce']
        path = {'parent': parent, 'directory': directory,
                'receipt': directory/'receipt.json'}[target]
        _mark_owned_fixture(path)
        before = _files(root)
        with pytest.raises((ValueError, OSError)):
            lease.assert_quiescent(root)
        assert _files(root) == before


def test_original_scoped_terminal_refuses_equal_content_database_lock_replacement(tmp_path):
    with component(tmp_path) as (root, child):
        _finish(root, child)
        path = root/lease.DATABASE_LOCK
        original = path.stat()
        body = path.read_bytes()
        path.rename(root/'projects/retained-original-database-lock')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as writer:
            writer.write(body)
        replacement = path.stat()
        assert (replacement.st_dev, replacement.st_ino) != (original.st_dev, original.st_ino)
        assert path.read_bytes() == body
        before = _files(root)
        with pytest.raises((ValueError, OSError)):
            lease.assert_quiescent(root)
        assert _files(root) == before
