"""Read-only, bounded evidence for one original closed writer epoch.

This reader never selects a live lease/current application, takes a process
identity, moves an epoch, or supplies shutdown/release authority.  The caller
supplies the original validated epoch binding and its one absolute deadline.
"""
import base64
import ctypes
import errno
import hashlib
import math
import os
from pathlib import Path
import stat
import sys
import time

from backend.engine import application_launch_quiescence as q
from backend.engine import runtime_update as u

SCHEMA = 'modu-vision.original-store-writer-archive/v1'
DOCUMENT_LIMIT = 128 * 1024
TOTAL_LIMIT = 256 * 1024
MAX_FILES = 131
MAX_DIRECTORIES = 130
PROVENANCE_ATTRIBUTE = 'com.apple.provenance'
ATTRIBUTE_LIMIT = 4096
_FIELDS = {'schema', 'nonce', 'epoch_binding', 'registry_sha256',
           'publication_sha256', 'root_raw9', 'directories', 'files', 'xattrs'}
_BINDING_FIELDS = {'installation_id', 'nonce', 'update_id',
                   'application_generation', 'database_generation_id',
                   'database_fence', 'launch_binding_sha256', 'controller'}


def _require(condition, message):
    if not condition:
        raise ValueError('Original writer archive refuses: ' + message)


def _clock(deadline):
    _require(type(deadline) in (int, float) and math.isfinite(deadline)
             and time.monotonic() < deadline, 'original deadline expired/invalid')


def _raw9(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns]


def _raw_shape(value):
    return (type(value) is list and len(value) == 9
            and all(type(item) is int and item >= 0 for item in value))


def _metadata(path, expected, deadline):
    """Strict nofollow Darwin getters, derived from the existing adapter.

    Unlike os.listxattr, the original explicit libSystem ABI is also available
    in the admitted conda Python.  A missing/failed getter is a refusal.  On
    The one OS-created provenance attribute is data, never authority. Its actual
    bytes are retained; all other names and all ACLs remain refused. On Linux,
    POSIX ACLs are among the nofollow xattrs and are rejected with them.
    """
    _clock(deadline)
    _require(_raw9(path.lstat()) == expected, 'metadata identity changed')
    if sys.platform == 'darwin':
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        list_fn, get_fn = library.listxattr, library.getxattr
        list_fn.argtypes = (ctypes.c_char_p, ctypes.c_void_p,
                            ctypes.c_size_t, ctypes.c_int)
        list_fn.restype = ctypes.c_ssize_t
        get_fn.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                           ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
        get_fn.restype = ctypes.c_ssize_t
        encoded = os.fsencode(path)
        # Original NOFOLLOW | SHOWCOMPRESSION adapter flags, no empty fallback.
        def count(function, arguments, limit):
            _clock(deadline)
            ctypes.set_errno(0)
            size = function(*arguments)
            if size < 0:
                raise OSError(ctypes.get_errno(), 'Darwin xattr read failed')
            _require(size <= limit, 'extended attribute exceeds explicit bound')
            _require(_raw9(path.lstat()) == expected, 'xattr identity changed')
            return size
        def names():
            size = count(list_fn, (encoded, None, 0, 0x21), ATTRIBUTE_LIMIT)
            buffer = ctypes.create_string_buffer(max(1, size))
            _require(count(list_fn, (encoded, buffer, size, 0x21), ATTRIBUTE_LIMIT) == size,
                     'extended attribute names changed')
            raw = buffer.raw[:size]
            _require(not raw or raw.endswith(b'\0'), 'invalid extended attribute names')
            values = raw[:-1].split(b'\0') if raw else []
            _require(len(values) == len(set(values))
                     and set(values) <= {PROVENANCE_ATTRIBUTE.encode('ascii')},
                     'unknown extended attribute/ACL')
            return values
        def values(names_before):
            result = {}
            for name in names_before:
                size = count(get_fn, (encoded, name, None, 0, 0, 0x21), ATTRIBUTE_LIMIT)
                _require(size > 0, 'empty provenance attribute')
                buffer = ctypes.create_string_buffer(size)
                _require(count(get_fn, (encoded, name, buffer, size, 0, 0x21), ATTRIBUTE_LIMIT) == size,
                         'provenance attribute bytes changed')
                result[PROVENANCE_ATTRIBUTE] = base64.b64encode(buffer.raw[:size]).decode('ascii')
            return result
        initial = names()
        result = values(initial)
        _require(names() == initial and values(initial) == result and names() == initial,
                 'extended attribute evidence changed during repeated reads')
        get, text, free = (library.acl_get_link_np, library.acl_to_text,
                           library.acl_free)
        get.argtypes, get.restype = (ctypes.c_char_p, ctypes.c_int), ctypes.c_void_p
        text.argtypes, text.restype = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)), ctypes.c_void_p
        free.argtypes, free.restype = (ctypes.c_void_p,), ctypes.c_int
        _clock(deadline)
        ctypes.set_errno(0)
        acl = get(encoded, 0x100)  # Original SDK ACL_TYPE_EXTENDED.
        getter_errno = ctypes.get_errno()
        try:
            _require(_raw9(path.lstat()) == expected, 'ACL identity changed')
            # Original adapter establishes ENOENT as absent ACL, with fresh
            # before/after identity.  Any actual ACL is outside this contract.
            _require(not acl and getter_errno == errno.ENOENT,
                     'nonempty, unavailable or failed ACL')
        finally:
            if acl:
                _require(free(acl) == 0, 'ACL release failed')
    elif sys.platform.startswith('linux'):
        getter = getattr(os, 'listxattr', None)
        _require(callable(getter), 'nofollow xattr/ACL getter unavailable')
        def values():
            _clock(deadline)
            names = getter(path, follow_symlinks=False)
            _require(len(names) == len(set(names)) and set(names) <= {PROVENANCE_ATTRIBUTE},
                     'unknown extended attribute/ACL')
            result = {}
            for name in names:
                _clock(deadline)
                read_value = getattr(os, 'getxattr', None)
                _require(callable(read_value), 'nofollow provenance getter unavailable')
                raw = read_value(path, name, follow_symlinks=False)
                _require(0 < len(raw) <= ATTRIBUTE_LIMIT, 'provenance attribute size outside bound')
                result[name] = base64.b64encode(raw).decode('ascii')
            _require(_raw9(path.lstat()) == expected, 'xattr identity changed')
            return result
        result = values()
        _require(values() == result, 'extended attribute evidence changed during repeated reads')
    else:
        _require(False, 'unsupported nofollow xattr/ACL platform')
    _clock(deadline)
    _require(_raw9(path.lstat()) == expected, 'metadata changed during read')
    return result


def _entry(path, *, directory, deadline):
    _clock(deadline)
    u._unlinked(path)
    info = path.lstat()
    _require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
             and info.st_uid == os.geteuid() and info.st_gid == os.getegid()
             and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
             and (directory or info.st_nlink == 1), 'nonprivate, linked or wrong entry type')
    identity = _raw9(info)
    _metadata(path, identity, deadline)
    return identity


def _members(directory, expected, deadline):
    _clock(deadline)
    seen = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            _clock(deadline)
            _require(entry.name in expected and entry.name not in seen,
                     'unknown or duplicate archive member')
            seen.add(entry.name)
    _require(seen == expected, 'missing archive member')


def _read(path, *, empty, deadline):
    before = _entry(path, directory=False, deadline=deadline)
    _require(before[6] == 0 if empty else 0 < before[6] <= DOCUMENT_LIMIT,
             'lock/document size outside original bound')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _require(_raw9(os.fstat(fd)) == before, 'opened file identity changed')
        pieces, size = [], 0
        while True:
            _clock(deadline)
            piece = os.read(fd, min(16 * 1024, DOCUMENT_LIMIT + 1 - size))
            if not piece:
                break
            pieces.append(piece)
            size += len(piece)
            _require(size <= DOCUMENT_LIMIT and size <= before[6], 'file grew during read')
        _require(size == before[6] and _raw9(os.fstat(fd)) == before,
                 'file changed during bounded read')
        _require(_raw9(path.lstat()) == before, 'published file changed during read')
        _metadata(path, before, deadline)
        raw = b''.join(pieces)
        return {'raw9': before, 'size': size,
                'sha256': hashlib.sha256(raw).hexdigest()}, raw
    finally:
        os.close(fd)


def _binding(value):
    _require(type(value) is dict and set(value) == _BINDING_FIELDS
             and u._hex(value['nonce'], 32), 'invalid original epoch binding')
    # Copy the supplied validated shadow binding; no live lease/current lookup.
    return u._json(u._canonical(value))


def _snapshot(directory, expected_binding, deadline):
    _clock(deadline)
    binding = _binding(expected_binding)
    directory = Path(directory)
    _require(directory.is_absolute() and directory.name == binding['nonce']
             and directory.parent.name in {q.EPOCHS, '.application-writer-archives'},
             'foreign epoch/archive namespace')
    _entry(directory.parent, directory=True, deadline=deadline)
    root = _entry(directory, directory=True, deadline=deadline)
    _members(directory, {'registry.json', 'publication.json', 'authority.lock', 'writers'}, deadline)
    files, directories = {}, {}
    xattrs = {'.': _metadata(directory, root, deadline)}
    registry_row, registry_raw = _read(directory/'registry.json', empty=False, deadline=deadline)
    publication_row, publication_raw = _read(directory/'publication.json', empty=False, deadline=deadline)
    value, pointer = u._json(registry_raw), u._json(publication_raw)
    _clock(deadline)
    # Pure original shape/member checks include registration/lock identity pins.
    # They never call _load/_launch/inspect_epoch or inspect a current pointer.
    q._shape(value, binding, directory)
    _clock(deadline)
    _require(value['state'] == 'closed' and all(
        row['status'] == 'direct_exited' and type(row['exit_code']) is int
        and row['exit_code'] == 0 and row['reason_code'] is None
        for row in value['writers']), 'epoch/writer is not directly closed')
    expected_pointer = {'schema_version': 1, 'nonce': binding['nonce'],
                        'revision': value['revision'],
                        'registry_sha256': registry_row['sha256']}
    _require(u._canonical(pointer) == u._canonical(expected_pointer),
             'partial registry publication/CAS differs')
    files['registry.json'], files['publication.json'] = registry_row, publication_row
    files['authority.lock'], _ = _read(directory/'authority.lock', empty=True, deadline=deadline)
    directories['writers'] = _entry(directory/'writers', directory=True, deadline=deadline)
    writer_ids = {row['writer_id'] for row in value['writers']}
    _members(directory/'writers', writer_ids, deadline)
    for writer_id in sorted(writer_ids):
        _clock(deadline)
        relative = 'writers/' + writer_id
        child = directory/relative
        directories[relative] = _entry(child, directory=True, deadline=deadline)
        _members(child, {'ownership.lock'}, deadline)
        files[relative+'/ownership.lock'], _ = _read(child/'ownership.lock', empty=True, deadline=deadline)
    for relative, identity in directories.items():
        xattrs[relative] = _metadata(directory/relative, identity, deadline)
    for relative, row in files.items():
        xattrs[relative] = _metadata(directory/relative, row['raw9'], deadline)
    _require(len(files) <= MAX_FILES and len(directories) <= MAX_DIRECTORIES
             and sum(row['size'] for row in files.values()) <= TOTAL_LIMIT,
             'archive census exceeds reviewed bound')
    # Final complete census refuses entry/content drift during initial capture.
    _members(directory, {'registry.json', 'publication.json', 'authority.lock', 'writers'}, deadline)
    _members(directory/'writers', writer_ids, deadline)
    for relative, expected in directories.items():
        _require(_entry(directory/relative, directory=True, deadline=deadline) == expected,
                 'directory changed during capture')
        _require(_metadata(directory/relative, expected, deadline) == xattrs[relative],
                 'directory provenance changed during capture')
        if relative != 'writers':
            _members(directory/relative, {'ownership.lock'}, deadline)
    for relative, expected in files.items():
        row, _ = _read(directory/relative, empty=relative.endswith('.lock'), deadline=deadline)
        _require(row == expected, 'file changed during capture')
        _require(_metadata(directory/relative, expected['raw9'], deadline) == xattrs[relative],
                 'file provenance changed during capture')
    _require(_entry(directory, directory=True, deadline=deadline) == root,
             'archive root changed during capture')
    _require(_metadata(directory, root, deadline) == xattrs['.'],
             'archive root provenance changed during capture')
    return {'schema': SCHEMA, 'nonce': binding['nonce'], 'epoch_binding': binding,
            'registry_sha256': registry_row['sha256'],
            'publication_sha256': publication_row['sha256'], 'root_raw9': root,
            'directories': directories, 'files': files, 'xattrs': xattrs}, value


def capture_epoch(directory, expected_binding, *, deadline):
    """Capture the original bounded closed namespace without any mutation."""
    manifest, _ = _snapshot(directory, expected_binding, deadline)
    return manifest


def validate_epoch_archive(directory, manifest, *, archive_root_after_raw9, deadline):
    """Validate the exact rename result and retained descendants, read-only."""
    _clock(deadline)
    _require(type(manifest) is dict and set(manifest) == _FIELDS
             and manifest['schema'] == SCHEMA
             and _raw_shape(manifest['root_raw9']) and _raw_shape(archive_root_after_raw9)
             and type(manifest['directories']) is dict
             and type(manifest['files']) is dict
             and type(manifest['xattrs']) is dict
             and len(manifest['directories']) <= MAX_DIRECTORIES
             and len(manifest['files']) <= MAX_FILES
             and all(type(name) is str and _raw_shape(row)
                     for name, row in manifest['directories'].items())
             and all(type(name) is str and type(row) is dict
                     and set(row) == {'raw9', 'size', 'sha256'}
                     and _raw_shape(row['raw9']) and type(row['size']) is int
                     and row['size'] >= 0 and u._hex(row['sha256'])
                     for name, row in manifest['files'].items())
             and set(manifest['xattrs']) == {'.'} | set(manifest['directories']) | set(manifest['files'])
             and all(type(row) is dict and set(row) <= {PROVENANCE_ATTRIBUTE}
                     and all(type(value) is str and 0 < len(value) <= 4 * ((ATTRIBUTE_LIMIT+2)//3)
                             for value in row.values())
                     for row in manifest['xattrs'].values()),
             'invalid archive manifest/root transition')
    before, after = manifest['root_raw9'], archive_root_after_raw9
    _require(before[:8] == after[:8], 'rename changed more than root ctime')
    directory = Path(directory)
    _require(directory.parent.name == '.application-writer-archives', 'not the fixed archive namespace')
    _require(_entry(directory, directory=True, deadline=deadline) == after,
             'archive root differs from original after-rename receipt')
    current, registry = _snapshot(directory, manifest['epoch_binding'], deadline)
    expected = dict(manifest, root_raw9=after)
    _require(current == expected, 'retained archive manifest/body/security differs')
    return registry
