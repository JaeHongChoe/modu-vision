"""One original stdin EOF Thread and descriptor; local facts, no release authority."""
from __future__ import annotations

import logging
import errno
import ctypes
import math
import os
import socket
import stat
import sys
import threading
import time


class OriginalStdinError(ValueError):
    pass


def _anonymous_unix_address(raw, length, platform):
    """Only the observed anonymous sockaddr forms; no paths/abstract names."""
    if type(raw) is not bytes or type(length) is not int or len(raw) != length:
        return False
    if platform == 'darwin':
        # Darwin's socketpair uses a 16-byte padded sockaddr with sa_len first.
        return length == 16 and raw[:2] == bytes((16, int(socket.AF_UNIX))) and raw[2:] == bytes(14)
    if platform == 'linux':
        # Linux has a native uint16 family and zero sun_path bytes for socketpair.
        return length == 2 and int.from_bytes(raw, sys.byteorder) == int(socket.AF_UNIX)
    return False


def _inspect_unix_stream(fd, *, original_identity, identity):
    """Read kernel metadata without a Python socket wrapper changing OFD flags."""
    if sys.platform not in ('darwin', 'linux'):
        raise OriginalStdinError('Original stdin socket platform is unsupported')
    libc = ctypes.CDLL(None, use_errno=True)
    get_option = libc.getsockopt
    get_option.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                           ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    get_option.restype = ctypes.c_int
    def option(name):
        value = ctypes.c_int(); length = ctypes.c_uint32(ctypes.sizeof(value))
        if get_option(fd, socket.SOL_SOCKET, name, ctypes.byref(value), ctypes.byref(length)) != 0:
            number = ctypes.get_errno(); raise OSError(number, os.strerror(number))
        if length.value != ctypes.sizeof(value):
            raise OriginalStdinError('Original stdin socket option shape differs')
        return value.value
    if option(socket.SO_TYPE) != socket.SOCK_STREAM:
        raise OriginalStdinError('Original stdin socket is not a supervisor stream')
    if hasattr(socket, 'SO_PROTOCOL') and option(socket.SO_PROTOCOL) != 0:
        raise OriginalStdinError('Original stdin Unix stream protocol differs')
    for name in ('getsockname', 'getpeername'):
        call = getattr(libc, name)
        call.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        address = ctypes.create_string_buffer(256); length = ctypes.c_uint32(256)
        if call(fd, address, ctypes.byref(length)) != 0:
            number = ctypes.get_errno()
            # Darwin forgets a socketpair's peer after natural close/EOF. Only
            # the same stream admitted while connected may reach this state.
            if (name == 'getpeername' and original_identity == identity
                    and number in (errno.EINVAL, errno.ENOTCONN)):
                continue
            raise OSError(number, os.strerror(number))
        if length.value > 256 or not _anonymous_unix_address(address.raw[:length.value], length.value, sys.platform):
            raise OriginalStdinError('Original stdin socket is not an anonymous connected supervisor stream')


def _descriptor_identity(fd, *, original_identity=None):
    try: held = os.fstat(fd)
    except OSError as error:
        raise OriginalStdinError('Original stdin descriptor is unavailable') from error
    # macOS socket permission bits reflect read/write readiness and change at
    # peer-close/EOF. The kernel object and socket file type remain the identity.
    facts = lambda value: (value.st_dev, value.st_ino,
                           stat.S_IFMT(value.st_mode) if stat.S_ISSOCK(value.st_mode) else value.st_mode,
                           value.st_uid, value.st_gid, value.st_nlink)
    identity = facts(held)
    if stat.S_ISFIFO(held.st_mode):
        return identity
    # Node/libuv's POSIX stdio 'pipe' is an anonymous Unix stream socketpair.
    # Inspect only a private duplicate; retain the original fd and EOF reader.
    if os.name != 'posix' or not stat.S_ISSOCK(held.st_mode):
        raise OriginalStdinError('Original stdin descriptor must be the supervisor pipe or anonymous Unix stream')
    duplicate = None
    try:
        duplicate = os.dup(fd)
        _inspect_unix_stream(duplicate, original_identity=original_identity, identity=identity)
        if facts(os.fstat(fd)) != identity or facts(os.fstat(duplicate)) != identity:
            raise OriginalStdinError('Original stdin stream descriptor changed during validation')
    except OSError as error:
        raise OriginalStdinError('Original stdin supervisor stream is unavailable or unconnected') from error
    finally:
        if duplicate is not None: os.close(duplicate)
    return identity


class OwnedStdinLifetime:
    """Retain the created EOF reader without duplicating or closing its stream.

    The owning thread alone records genuine EOF before cleanup. A bounded join
    is available only afterwards, under one original caller-supplied deadline.
    An open pipe, failed cleanup, changed descriptor, or late join stays refused.
    """
    def __init__(self, fd, reader):
        if type(fd) is not int or fd < 0 or not callable(reader):
            raise OriginalStdinError('Original stdin descriptor and reader are required')
        self._pid = os.getpid()
        self._creator = threading.current_thread()
        self._fd = fd
        self._identity = _descriptor_identity(fd)
        self._reader = reader
        self._eof = threading.Event()
        self._completed = threading.Event()
        self._failure = None
        self._result = None
        self._started = False
        self._deadline = None
        completed = self._completed
        def original():
            try:
                self._fresh()
                result = reader(fd, self._observed_eof)
                self._fresh()
                if (type(result) is not dict or set(result) != {'natural_stdin_eof', 'detach_completed'}
                        or result['natural_stdin_eof'] is not True or result['detach_completed'] is not True
                        or not self._eof.is_set()):
                    raise OriginalStdinError('Original stdin reader did not complete genuine EOF cleanup')
                self._result = dict(result)
            except BaseException as error:
                self._failure = error
                logging.getLogger(__name__).exception('Original stdin lifetime failed')
            finally:
                completed.set()
        self._thread = threading.Thread(target=original, daemon=True, name='StopOnStdinEOF')
        self._binding = (self._pid, self._creator, self._fd, self._identity,
                         self._reader, self._thread, self._eof, self._completed)

    @property
    def thread(self): return self._thread

    @property
    def eof_event(self): return self._eof

    @property
    def natural_eof_observed(self): return self._eof.is_set()

    def _fresh(self):
        if (os.getpid() != self._pid
                or (self._pid, self._creator, self._fd, self._identity, self._reader,
                    self._thread, self._eof, self._completed) != self._binding):
            raise OriginalStdinError('Original stdin thread handle or owner changed')
        if _descriptor_identity(self._fd, original_identity=self._identity) != self._identity:
            raise OriginalStdinError('Original stdin stream descriptor changed')

    def _observed_eof(self):
        self._fresh()
        if not self._started or threading.current_thread() is not self._thread:
            raise OriginalStdinError('Original stdin EOF caller is not its owned thread')
        self._eof.set()

    def start(self):
        self._fresh()
        if self._started or threading.current_thread() is not self._creator:
            raise OriginalStdinError('Original stdin thread start changed or replayed')
        self._started = True
        self._thread.start()

    def after_eof_receipt(self, absolute_deadline, *, expected_thread, expected_fd):
        self._fresh()
        if (not self._started or expected_thread is not self._thread
                or expected_fd != self._fd or type(expected_fd) is not int
                or self._thread is threading.current_thread()):
            raise OriginalStdinError('Original stdin thread is unstarted or its handle differs')
        now = time.monotonic()
        if (type(absolute_deadline) not in (int,float) or not math.isfinite(absolute_deadline)
                or not 0 < absolute_deadline-now <= 4):
            raise OriginalStdinError('Original stdin join deadline expired or exceeds original bounded exit budget')
        if self._deadline is None: self._deadline = absolute_deadline
        if self._deadline != absolute_deadline:
            raise OriginalStdinError('Original stdin join deadline cannot renew')
        if self._failure is not None:
            raise OriginalStdinError('Original stdin reader or detach cleanup failed') from self._failure
        if not self._eof.is_set():
            raise OriginalStdinError('Original stdin EOF remains unresolved; no early join or close')
        original_thread = self._thread
        original_thread.join(max(0.0, absolute_deadline-time.monotonic()))
        self._fresh()
        if original_thread.is_alive() or time.monotonic() >= absolute_deadline:
            raise OriginalStdinError('Original stdin thread remains alive or join deadline expired')
        if self._failure is not None:
            raise OriginalStdinError('Original stdin reader or detach cleanup failed') from self._failure
        if not self._completed.is_set() or self._result != {'natural_stdin_eof':True, 'detach_completed':True}:
            raise OriginalStdinError('Original stdin EOF cleanup completion is unresolved')
        return {'role':'stop_on_stdin_eof', 'natural_stdin_eof':True,
                'original_thread_joined':True, 'original_detach_completed':True,
                'whole_writer_coverage':False, 'process_tree_exit_verified':False,
                'can_release_launch_lease':False}
