"""Persistent identities for durable local registrations only.

Operation receipts and short-lived file selections deliberately keep their
existing device/inode guards. A legacy registration is never enrolled here.
"""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import platform
import re
import stat
import struct
import sys
import uuid


_platform = sys.platform
_RETURNED = 0x80000000
_FILE_COMMON = 0x0200020c  # FSID, OBJTYPE, CRTIME, full unsigned FILEID.
_VOLUME = 0x40060000  # CAPABILITIES, UUID, ATTRIBUTES (INFO is request-only).
_PERSISTENT_IDS = 0x1
_PROVIDER = 'darwin-getattrlist-v2'


class LocationIdentityUnavailable(ValueError):
    """The filesystem could not prove the fixed persistent identity contract."""


def _directory_id(info):
    if not stat.S_ISDIR(info.st_mode):
        raise LocationIdentityUnavailable('The registered location must be a directory')
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def _open_absolute(path):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def open_directory(path):
    """Hold a no-follow directory FD and refuse any changed path on exit."""
    path = Path(path)
    if (not path.is_absolute() or '..' in path.parts or len(os.fsencode(path)) > 4096
            or os.name != 'posix' or not getattr(os, 'O_NOFOLLOW', 0)
            or not getattr(os, 'O_DIRECTORY', 0) or os.open not in os.supports_dir_fd):
        raise LocationIdentityUnavailable('Qualified absolute no-follow directory access is required')
    descriptor = _open_absolute(path)
    try:
        expected = _directory_id(os.fstat(descriptor))
        yield descriptor
        current = _open_absolute(path)
        try:
            if (_directory_id(os.fstat(current)) != expected
                    or _directory_id(os.fstat(descriptor)) != expected):
                raise LocationIdentityUnavailable('Registered directory changed during inspection')
        finally:
            os.close(current)
    finally:
        os.close(descriptor)


class _AttrList(ctypes.Structure):
    _fields_ = [('bitmapcount', ctypes.c_uint16), ('reserved', ctypes.c_uint16),
                ('commonattr', ctypes.c_uint32), ('volattr', ctypes.c_uint32),
                ('dirattr', ctypes.c_uint32), ('fileattr', ctypes.c_uint32),
                ('forkattr', ctypes.c_uint32)]


class _StatFS(ctypes.Structure):
    # Darwin's public 64-bit-inode statfs ABI (sys/mount.h).
    _fields_ = [('bsize', ctypes.c_uint32), ('iosize', ctypes.c_int32),
                ('blocks', ctypes.c_uint64), ('bfree', ctypes.c_uint64),
                ('bavail', ctypes.c_uint64), ('files', ctypes.c_uint64),
                ('ffree', ctypes.c_uint64), ('fsid', ctypes.c_int32 * 2),
                ('owner', ctypes.c_uint32), ('type', ctypes.c_uint32),
                ('flags', ctypes.c_uint32), ('subtype', ctypes.c_uint32),
                ('fstype', ctypes.c_char * 16), ('mount', ctypes.c_char * 1024),
                ('source', ctypes.c_char * 1024), ('flags_ext', ctypes.c_uint32),
                ('reserved', ctypes.c_uint32 * 7)]


def _get_attributes(descriptor, common_mask, volume_mask=0):
    library = ctypes.CDLL(None, use_errno=True)
    function = library.fgetattrlist
    function.argtypes = [ctypes.c_int, ctypes.POINTER(_AttrList), ctypes.c_void_p,
                         ctypes.c_size_t, ctypes.c_ulong]
    function.restype = ctypes.c_int
    requested = _AttrList(5, 0, common_mask, volume_mask, 0, 0, 0)
    buffer = ctypes.create_string_buffer(4096)
    # REPORT_FULLSIZE exposes truncation through the leading size field.
    if function(descriptor, ctypes.byref(requested), buffer, len(buffer), 4) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    length = struct.unpack_from('=I', buffer.raw)[0]
    if not 4 <= length <= len(buffer):
        raise LocationIdentityUnavailable('Filesystem attribute buffer is truncated')
    return buffer.raw[:length]


def _filesystem(descriptor):
    library = ctypes.CDLL(None, use_errno=True)
    try:
        function = getattr(library, 'fstatfs$INODE64')
    except AttributeError as exc:
        # Apple Silicon exposes only the 64-bit-inode ABI under fstatfs;
        # Intel's legacy unqualified ABI has a different layout.
        if platform.machine() not in {'arm64', 'aarch64'}:
            raise LocationIdentityUnavailable('Qualified Darwin mount metadata is unavailable') from exc
        function = library.fstatfs
    function.argtypes = [ctypes.c_int, ctypes.POINTER(_StatFS)]
    function.restype = ctypes.c_int
    result = _StatFS()
    if function(descriptor, ctypes.byref(result)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    mount = bytes(result.mount)
    if not mount or len(mount) >= 1024:
        raise LocationIdentityUnavailable('Filesystem mount metadata is truncated')
    return {'fsid': tuple(result.fsid), 'mount': os.fsdecode(mount)}


def _checked_buffer(buffer, length, common, volume=0):
    # getattrlist results pack fields to four bytes, unlike native ctypes
    # structs containing uint64/timespec. All offsets below are explicit.
    if (not isinstance(buffer, bytes) or len(buffer) != length
            or struct.unpack_from('=I', buffer)[0] != length):
        raise LocationIdentityUnavailable('Filesystem attribute buffer is incomplete')
    returned = struct.unpack_from('=5I', buffer, 4)
    if returned != (common, volume, 0, 0, 0):
        raise LocationIdentityUnavailable('Filesystem did not return every requested attribute')


def _common(descriptor):
    buffer = _get_attributes(descriptor, _RETURNED | _FILE_COMMON)
    _checked_buffer(buffer, 60, _RETURNED | _FILE_COMMON)
    fsid = struct.unpack_from('=2i', buffer, 24)
    kind = struct.unpack_from('=I', buffer, 32)[0]
    created = struct.unpack_from('=qq', buffer, 36)
    file_id = struct.unpack_from('=Q', buffer, 52)[0]
    if kind != 2 or file_id == 0 or not 0 <= created[1] < 1_000_000_000:
        raise LocationIdentityUnavailable('Filesystem directory identity is unsupported')
    return {'fsid': fsid, 'kind': 'directory', 'created': list(created), 'file_id': str(file_id)}


def _volume(descriptor):
    buffer = _get_attributes(descriptor, _RETURNED, _RETURNED | _VOLUME)
    _checked_buffer(buffer, 112, _RETURNED, _VOLUME)
    capabilities = struct.unpack_from('=8I', buffer, 24)
    supported = struct.unpack_from('=5I', buffer, 72)
    # validattr indicates support; nativeattr (also present in the checked
    # buffer) describes conversion cost and is not an extra stability proof.
    if (not capabilities[0] & _PERSISTENT_IDS or not capabilities[4] & _PERSISTENT_IDS
            or supported[0] & _FILE_COMMON != _FILE_COMMON
            or supported[1] & _VOLUME != _VOLUME):
        raise LocationIdentityUnavailable('The volume does not prove persistent directory identity support')
    identifier = uuid.UUID(bytes=buffer[56:72])
    if identifier.int == 0:
        raise LocationIdentityUnavailable('The volume has no persistent UUID')
    return str(identifier)


def capture(descriptor):
    """Capture one directory; Darwin must prove the complete v2 provider."""
    before = _directory_id(os.fstat(descriptor))
    if _platform != 'darwin':
        return list(before[:2])
    common = _common(descriptor)
    filesystem = _filesystem(descriptor)
    if common['fsid'] != filesystem['fsid']:
        raise LocationIdentityUnavailable('Directory and mount metadata disagree')
    with open_directory(filesystem['mount']) as mount_descriptor:
        mount_filesystem = _filesystem(mount_descriptor)
        mount_common = _common(mount_descriptor)
        if (os.fstat(mount_descriptor).st_dev != before[0]
                or mount_filesystem != filesystem or mount_common['fsid'] != common['fsid']):
            raise LocationIdentityUnavailable('The held directory does not belong to the inspected volume')
        volume_uuid = _volume(mount_descriptor)
        if _filesystem(mount_descriptor) != filesystem:
            raise LocationIdentityUnavailable('The inspected mount changed')
    if (_directory_id(os.fstat(descriptor)) != before or _common(descriptor) != common
            or _filesystem(descriptor) != filesystem):
        raise LocationIdentityUnavailable('Directory identity changed during inspection')
    return {'version': 2, 'provider': _PROVIDER, 'volume_uuid': volume_uuid,
            'file_id': common['file_id'], 'kind': common['kind'], 'created': common['created']}


def read(path):
    """Read a registration identity without enrolling or changing any state."""
    if _platform != 'darwin':
        # Preserve the existing non-Darwin registration contract, including
        # Windows where the POSIX directory descriptor API is unavailable.
        info = Path(path).stat()
        return [info.st_dev, info.st_ino]
    with open_directory(path) as descriptor:
        return capture(descriptor)


def _valid_v2(stored):
    if (type(stored) is not dict or set(stored) !=
            {'version', 'provider', 'volume_uuid', 'file_id', 'kind', 'created'}
            or type(stored['version']) is not int or stored['version'] != 2
            or stored['provider'] != _PROVIDER or stored['kind'] != 'directory'):
        return False
    identifier = stored['volume_uuid']
    if type(identifier) is not str:
        return False
    try:
        parsed = uuid.UUID(identifier)
    except ValueError:
        return False
    if parsed.int == 0 or str(parsed) != identifier:
        return False
    file_id = stored['file_id']
    if (type(file_id) is not str or not re.fullmatch('[1-9][0-9]{0,19}', file_id)
            or int(file_id) >= 2**64):
        return False
    created = stored['created']
    return (type(created) is list and len(created) == 2
            and all(type(value) is int for value in created)
            and -(2**63) <= created[0] < 2**63 and 0 <= created[1] < 1_000_000_000)


def matches(stored, path):
    """Exact legacy comparison or strict v2 comparison; never migration."""
    try:
        if type(stored) is list:
            if len(stored) != 2 or any(type(value) is not int or value < 0 for value in stored):
                return False
            info = Path(path).stat()
            return stored == [info.st_dev, info.st_ino]
        if not _valid_v2(stored) or _platform != 'darwin':
            return False
        return stored == read(path)
    except (OSError, ValueError, AttributeError, TypeError, struct.error):
        return False
