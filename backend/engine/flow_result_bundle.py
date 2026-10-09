"""Opt-in lossless FlowResultBundle/v1 transport for complete flow results.

Each parsed JSON member is at most16MiB. Canonical complete result bytes and
the sum of header/step/crop JSON bytes are each at most64MiB. At most256 files
include manifest, header, complete raw result and every ordered step/crop.
The complete raw file is only streamed and hashed, never parsed as JSON.

Directory-descriptor support is required for this optional transport. Default
CLI JSON output is separate and unchanged. This format grants no inference,
model-quality, installed-target or process ownership authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

SCHEMA = 'FlowResultBundle/v1'
JSON_MEMBER_LIMIT = 16 * 1024 * 1024
COMPLETE_RESULT_LIMIT = 64 * 1024 * 1024
DATA_MEMBER_LIMIT = 64 * 1024 * 1024
FILE_COUNT_LIMIT = 256
WRITE_BUFFER_LIMIT = 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _remaining(deadline):
    if deadline is not None:
        _require(type(deadline) in (int, float) and math.isfinite(deadline), 'Invalid result bundle deadline')
        _require(time.monotonic() < deadline, 'Result bundle absolute deadline expired')


def _identity(row):
    return [row.st_dev, row.st_ino, row.st_mode, row.st_size, row.st_mtime_ns, row.st_ctime_ns]


def _no_links(path):
    for member in (path, *path.parents):
        _require(not stat.S_ISLNK(member.stat(follow_symlinks=False).st_mode), 'Linked result bundle path')



def _close(fd, primary=None):
    """Close once; a cleanup failure cannot replace an existing refusal."""
    try:
        os.close(fd)
    except BaseException as secondary:
        if primary is None:
            raise
        # Category only: no exception message, PID, path or result payload.
        category = ('OSError' if isinstance(secondary, OSError) else
                    'ValueError' if isinstance(secondary, ValueError) else
                    'KeyboardInterrupt' if isinstance(secondary, KeyboardInterrupt) else
                    'SystemExit' if isinstance(secondary, SystemExit) else 'BaseException')
        note = 'Result bundle descriptor cleanup failed (' + category + '); original error retained'
        try:
            add_note = getattr(BaseException, 'add_note', None)
            if add_note is not None:
                add_note(primary, note)  # bypass an exception subclass override
            else:  # Python 3.10: retain the same bounded diagnostic as metadata
                namespace = BaseException.__getattribute__(primary, '__dict__')
                notes = namespace.setdefault('__notes__', [])
                if type(notes) is list:
                    notes.append(note)
        except BaseException:
            # Advisory note attachment must also preserve the primary object.
            pass


def _root(path, create=False):
    _require(os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
             and os.listdir in os.supports_fd and hasattr(os, 'O_DIRECTORY')
             and hasattr(os, 'O_NOFOLLOW'), 'Result bundles require directory-descriptor support')
    path = Path(path).absolute()
    _require('..' not in path.parts, 'Result bundle path contains a parent component')
    _no_links(path.parent)
    if create:
        path.mkdir(mode=0o700)  # no reuse, overwrite, recursive mkdir or cleanup of a failed output
    _no_links(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        row = os.fstat(fd)
        _require(stat.S_ISDIR(row.st_mode), 'Result bundle root is not a directory')
        _require(_identity(row) == _identity(path.stat(follow_symlinks=False)), 'Result bundle directory changed')
        return path, fd, row
    except BaseException as primary:
        _close(fd, primary)
        raise


def _check_root(path, fd, before, *, stable=False):
    _no_links(path)
    opened, current = os.fstat(fd), path.stat(follow_symlinks=False)
    _require([opened.st_dev, opened.st_ino, opened.st_mode] == [before.st_dev, before.st_ino, before.st_mode]
             and _identity(opened) == _identity(current), 'Result bundle directory identity changed')
    if stable:
        _require(_identity(opened) == _identity(before), 'Result bundle namespace changed during read')


def _valid_json(value):
    kind = type(value)
    if kind is dict:
        _require(all(type(key) is str for key in value), 'Result dictionary has non-string keys')
        for item in value.values():
            _valid_json(item)
    elif kind is list:
        for item in value:
            _valid_json(item)
    elif kind is float:
        _require(math.isfinite(value), 'Nonfinite result JSON value')
    else:
        _require(kind in (str, int, bool, type(None)), 'Result contains a non-JSON value')


def _encoded(value):
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    for piece in encoder.iterencode(value):
        yield piece.encode('utf-8')


def _canonical_pin(value, limit, deadline):
    size, digest = 0, hashlib.sha256()
    for block in _encoded(value):
        _remaining(deadline)
        size += len(block)
        _require(size <= limit, 'Canonical complete result exceeds its bound')
        digest.update(block)
    return {'size': size, 'sha256': digest.hexdigest()}


def _write(fd, name, value, limit, deadline):
    _remaining(deadline)
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=fd)
    count, digest = 0, hashlib.sha256()
    primary = None
    buffer = bytearray()
    def flush():
        view = memoryview(buffer)
        try:
            offset = 0
            while offset < len(view):
                _remaining(deadline)
                written = os.write(out, view[offset:])
                _require(written > 0, 'Result member write made no progress')
                offset += written
        finally:
            view.release()
        buffer.clear()
    try:
        for block in _encoded(value):
            _remaining(deadline)
            count += len(block)
            _require(count <= limit, 'Result JSON member exceeds its bound')
            digest.update(block)
            source = memoryview(block)
            try:
                offset = 0
                while offset < len(source):
                    _remaining(deadline)
                    take = min(WRITE_BUFFER_LIMIT - len(buffer), len(source) - offset)
                    buffer.extend(source[offset:offset + take])
                    offset += take
                    if len(buffer) == WRITE_BUFFER_LIMIT:
                        flush()
            finally:
                source.release()
        flush()
        os.fsync(out)
        row = os.fstat(out)
        _require(stat.S_ISREG(row.st_mode) and row.st_nlink == 1 and row.st_size == count
                 and _identity(row) == _identity(os.stat(name, dir_fd=fd, follow_symlinks=False)),
                 'Result member changed during publication')
    except BaseException as error:
        primary = error
        raise
    finally:
        _close(out, primary)
    return {'path': name, 'size': count, 'sha256': digest.hexdigest()}


def write_result_bundle(result, output, *, deadline=None):
    """Publish a NEW bundle from the genuine complete result dictionary.

No fields are projected or omitted. A too-large individual record refuses;
failed partial outputs remain evidence and are never silently retried.
"""
    _remaining(deadline)
    _require(type(result) is dict and type(result.get('execution_steps')) is list
             and type(result.get('crops')) is list, 'Complete result requires step and crop lists')
    steps, crops = result['execution_steps'], result['crops']
    _require(all(type(value) is dict for value in steps + crops), 'Result step/crop must be a complete dictionary')
    _require(len(steps) + len(crops) + 3 <= FILE_COUNT_LIMIT, 'Result bundle has too many files')
    _valid_json(result)
    raw_pin = _canonical_pin(result, COMPLETE_RESULT_LIMIT, deadline)
    header = {key: value for key, value in result.items() if key not in ('execution_steps', 'crops')}
    path, fd, before = _root(output, create=True)
    primary = None
    try:
        raw = _write(fd, 'result.json', result, COMPLETE_RESULT_LIMIT, deadline)
        _require({key: raw[key] for key in ('size', 'sha256')} == raw_pin, 'Complete result changed during serialization')
        head = _write(fd, 'header.json', header, JSON_MEMBER_LIMIT, deadline)
        size = head['size']; members = {'execution_steps': [], 'crops': []}
        for field, values, prefix in [('execution_steps', steps, 'step'), ('crops', crops, 'crop')]:
            for index, value in enumerate(values):
                _remaining(deadline); _check_root(path, fd, before)
                row = _write(fd, f'{prefix}-{index:04d}.json', value, JSON_MEMBER_LIMIT, deadline)
                size += row['size']; _require(size <= DATA_MEMBER_LIMIT, 'Result data members exceed their aggregate bound')
                members[field].append({'index': index, **row})
        _require(size <= DATA_MEMBER_LIMIT, 'Result data members exceed their aggregate bound')
        body = {'schema': SCHEMA, 'result': raw, 'header': head, **members,
                'data_member_bytes': size, 'file_count': len(steps) + len(crops) + 3}
        _write(fd, 'manifest.json', body, JSON_MEMBER_LIMIT, deadline)
        os.fsync(fd); _check_root(path, fd, before)
        # Read our actual committed files with the same public reader. This also
        # detects a caller mutating the input dictionary between writer passes.
    except BaseException as error:
        primary = error
        raise
    finally:
        _close(fd, primary)
    restored, proof = read_result_bundle(path, deadline=deadline)
    _require(restored == result, 'Complete result changed during bundle publication')
    return proof


def _file(fd, name, cap, deadline, *, content=False):
    _remaining(deadline)
    source = os.open(name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), dir_fd=fd)
    chunks, size, digest = [], 0, hashlib.sha256()
    primary = None
    try:
        before = os.fstat(source)
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= cap,
                 'Input must be a bounded unaliased regular result file')
        while True:
            _remaining(deadline)
            block = os.read(source, min(1024 * 1024, cap + 1 - size))
            if not block:
                break
            size += len(block); _require(size <= cap, 'Result file grew past its bound')
            digest.update(block)
            if content:
                chunks.append(block)
        after = os.fstat(source); current = os.stat(name, dir_fd=fd, follow_symlinks=False)
        _require(_identity(before) == _identity(after) == _identity(current)
                 and after.st_nlink == current.st_nlink == 1 and size == before.st_size,
                 'Result file changed during read')
        pin = {'size': size, 'sha256': digest.hexdigest(), 'identity': _identity(after)}
        return (pin, b''.join(chunks)) if content else pin
    except BaseException as error:
        primary = error
        raise
    finally:
        _close(source, primary)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, 'Duplicate result JSON key')
        value[key] = item
    return value


def _json_document(fd, name, deadline):
    pin, raw = _file(fd, name, JSON_MEMBER_LIMIT, deadline, content=True)
    def nonfinite(value):
        raise ValueError('Nonfinite result JSON input')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=nonfinite)
    _valid_json(value)
    return value, pin


def _row(row, name, cap, *, index=None):
    keys = {'path', 'size', 'sha256'} | ({'index'} if index is not None else set())
    _require(type(row) is dict and set(row) == keys and row['path'] == name,
             'Result manifest member path/shape/order differs')
    _require(type(row['size']) is int and 0 <= row['size'] <= cap
             and type(row['sha256']) is str and re.fullmatch('[0-9a-f]{64}', row['sha256']) is not None,
             'Result manifest member pin differs')
    if index is not None:
        _require(type(row['index']) is int and row['index'] == index, 'Result manifest member index differs')


def _matches(actual, expected):
    _require({key: actual[key] for key in ('size', 'sha256')}
             == {key: expected[key] for key in ('size', 'sha256')}, 'Result member raw pin differs')


def read_result_bundle(root, *, deadline=None):
    """Read exact bounded members and reconstruct every original result field.

The raw complete result file is streamed twice for full custody. It is never
passed to json.loads. All members receive fresh before/post identity checks.
"""
    _remaining(deadline); path, fd, before = _root(root)
    primary = None
    try:
        body, manifest_pin = _json_document(fd, 'manifest.json', deadline)
        keys = {'schema', 'result', 'header', 'execution_steps', 'crops', 'data_member_bytes', 'file_count'}
        _require(type(body) is dict and set(body) == keys and body['schema'] == SCHEMA, 'Result bundle manifest schema differs')
        _require(type(body['execution_steps']) is list and type(body['crops']) is list, 'Result bundle lists differ')
        count = len(body['execution_steps']) + len(body['crops']) + 3
        _require(type(body['file_count']) is int and body['file_count'] == count <= FILE_COUNT_LIMIT,
                 'Result bundle file count differs')
        _require(type(body['data_member_bytes']) is int and 0 <= body['data_member_bytes'] <= DATA_MEMBER_LIMIT,
                 'Result bundle aggregate data bound differs')
        _row(body['result'], 'result.json', COMPLETE_RESULT_LIMIT)
        _row(body['header'], 'header.json', JSON_MEMBER_LIMIT)
        expected = {'manifest.json', 'result.json', 'header.json'}
        for field, prefix in [('execution_steps', 'step'), ('crops', 'crop')]:
            for index, row in enumerate(body[field]):
                name = f'{prefix}-{index:04d}.json'; _row(row, name, JSON_MEMBER_LIMIT, index=index); expected.add(name)
        _require(set(os.listdir(fd)) == expected, 'Result bundle has missing/orphan members')
        raw_pin = _file(fd, 'result.json', COMPLETE_RESULT_LIMIT, deadline); _matches(raw_pin, body['result'])
        header, header_pin = _json_document(fd, 'header.json', deadline); _matches(header_pin, body['header'])
        _require(type(header) is dict and not {'execution_steps', 'crops'} & set(header), 'Result header is not exact')
        restored = dict(header); pins = {'manifest.json': manifest_pin, 'result.json': raw_pin, 'header.json': header_pin}
        size = header_pin['size']
        for field in ('execution_steps', 'crops'):
            values = []
            for row in body[field]:
                _remaining(deadline)
                value, pin = _json_document(fd, row['path'], deadline); _matches(pin, row)
                _require(type(value) is dict, 'Result step/crop member is not a complete dictionary')
                values.append(value); pins[row['path']] = pin; size += pin['size']
                _require(size <= DATA_MEMBER_LIMIT, 'Result data members exceed their aggregate bound')
            restored[field] = values
        _require(size == body['data_member_bytes'], 'Result aggregate data bytes differ')
        complete_pin = _canonical_pin(restored, COMPLETE_RESULT_LIMIT, deadline)
        _require(complete_pin == {key: raw_pin[key] for key in ('size', 'sha256')}, 'Reconstructed complete result raw identity differs')
        # Re-open/hash every file after reconstruction; a mutation of an earlier
        # member or the manifest while a later member was read must not qualify.
        for name, pin in pins.items():
            cap = COMPLETE_RESULT_LIMIT if name == 'result.json' else JSON_MEMBER_LIMIT
            _require(_file(fd, name, cap, deadline) == pin, 'Result bundle member changed during complete readback')
        _require(set(os.listdir(fd)) == expected, 'Result bundle namespace changed during readback')
        _check_root(path, fd, before, stable=True)
        return restored, {'schema': SCHEMA, 'raw': raw_pin, 'files': pins,
                          'file_count': count, 'data_member_bytes': size}
    except BaseException as error:
        primary = error
        raise
    finally:
        _close(fd, primary)
