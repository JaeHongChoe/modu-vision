"""Early private-descriptor binding for an owned POSIX application backend.

This verifies controller transport and the committed application/database pair.
It grants no packaged-native, inference, or release acceptance. The original
controller remains responsible for durable launch ownership and recovery.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import select
import socket
import sys
import time

MAX_FRAME = 65536
MAX_DEADLINE = 210
_CONTEXT_NAMES = ('VISION_AI_STUDIO_USER_DATA_DIR', 'VISION_APPLICATION_LAUNCH_NONCE',
    'VISION_APPLICATION_GENERATION', 'VISION_APPLICATION_DATABASE_GENERATION',
    'VISION_APPLICATION_BACKEND_FD', 'VISION_APPLICATION_LAUNCH_FD')
_CHALLENGE_FIELDS = {'schema_version', 'kind', 'challenge', 'epoch', 'nonce', 'binding',
    'main_process', 'backend_pid', 'backend_executable', 'backend_build_identity_sha256'}
_CACHE = None


class HandshakeError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8', 'strict')


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length and all(c in '0123456789abcdef' for c in value)


def _deadline(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= MAX_DEADLINE:
        raise HandshakeError('Private descriptor deadline must be positive and at most 210 seconds')
    return time.monotonic() + seconds


def read_frame(sock, deadline_seconds):
    """Read one canonical bounded UTF-8 JSON object; leave later frames unread."""
    deadline = _deadline(deadline_seconds); raw = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([sock], [], [], max(0, remaining))[0]:
                raise HandshakeError('Private descriptor timeout before complete frame')
            chunk = sock.recv(1)
            if not chunk: raise HandshakeError('Private descriptor EOF before complete frame')
            raw.extend(chunk)
            if len(raw) > MAX_FRAME: raise HandshakeError('Private descriptor frame exceeds 65536 bytes')
            if chunk == b'\n': break
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value: raise HandshakeError('Duplicate private descriptor field')
                value[key] = item
            return value
        value = json.loads(bytes(raw[:-1]).decode('utf-8', 'strict'), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(HandshakeError('Nonfinite private descriptor number')))
        if not isinstance(value, dict) or _canonical(value) + b'\n' != raw:
            raise HandshakeError('Private descriptor frame is not a canonical JSON object')
        return value
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Invalid private descriptor frame: ' + str(exc)) from exc


def send_frame(sock, value):
    """Write one bounded canonical object without an unbounded peer wait."""
    try:
        if not isinstance(value, dict): raise HandshakeError('Private descriptor frame must be an object')
        raw = _canonical(value) + b'\n'
        if len(raw) > MAX_FRAME: raise HandshakeError('Private descriptor frame exceeds 65536 bytes')
        deadline = _deadline(10); offset = 0
        while offset < len(raw):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [sock], [], max(0, remaining))[1]:
                raise HandshakeError('Private descriptor timeout while sending frame')
            sent = sock.send(raw[offset:], getattr(socket, 'MSG_DONTWAIT', 0))
            if sent <= 0: raise HandshakeError('Private descriptor closed while sending frame')
            offset += sent
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Cannot send private descriptor frame: ' + str(exc)) from exc


def _context():
    return (tuple(os.environ.get(name) for name in _CONTEXT_NAMES), tuple(sys.argv),
            sys.executable, bool(getattr(sys, 'frozen', False)))


def _root_context():
    """Read context without discovery, creation, or application imports."""
    from backend.engine.global_store_paths import owned_root
    values = {name: os.environ.get(name) for name in _CONTEXT_NAMES}
    launch = [values[name] for name in _CONTEXT_NAMES[1:]]
    if values['VISION_APPLICATION_LAUNCH_FD'] is not None:
        raise HandshakeError('Main launch descriptor must be stripped before backend startup')
    configured = values[_CONTEXT_NAMES[0]]
    if configured is None:
        if any(value is not None for value in launch): raise HandshakeError('Partial owned backend launch context')
        return None
    if not configured or not Path(configured).is_absolute() or str(Path(configured)) != configured:
        raise HandshakeError('Owned backend root must be an explicit canonical absolute path')
    root, owner = owned_root(configured)
    if root is None:
        if any(value is not None for value in launch): raise HandshakeError('Backend launch root has no original ownership')
        return None
    if str(root) != configured: raise HandshakeError('Backend launch root differs from original ownership')
    present = any(value is not None for value in launch)
    if not present:
        if (root/'application-active.json').exists() or any((root/name).exists() for name in
                ('application-launch-lease.json', '.application-launches', 'application-database-ownership.lock')):
            raise HandshakeError('Owned application pair requires its private backend descriptor')
        return None
    if (not _hex(values['VISION_APPLICATION_LAUNCH_NONCE'], 32)
            or not _hex(values['VISION_APPLICATION_GENERATION'], 32)
            or not values['VISION_APPLICATION_DATABASE_GENERATION']
            or values['VISION_APPLICATION_BACKEND_FD'] is None):
        raise HandshakeError('Incomplete owned backend launch context')
    return root, values


def _transport(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or str(int(value)) != value or not 3 <= int(value) <= 8192:
        raise HandshakeError('Backend descriptor must be a canonical inherited descriptor from 3 to 8192')
    fd = int(value)
    try:
        os.set_inheritable(fd, False)
        sock = socket.socket(fileno=fd)
        if (os.name != 'posix' or sock.family != socket.AF_UNIX or sock.type != socket.SOCK_STREAM
                or sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
                or sock.getsockname() not in ('', b'') or sock.getpeername() not in ('', b'')):
            raise HandshakeError('Backend descriptor must be an anonymous connected POSIX stream socket')
        return sock
    except (OSError, ValueError) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Backend private descriptor is unavailable') from exc


def _arguments(root):
    expected = {'--project-dir': str(root/'projects'), '--shared-auth-dir': str(root/'auth')}
    for flag, path in expected.items():
        for argument in sys.argv[1:]:
            option = argument.split('=', 1)[0]
            if option.startswith('--') and option != flag and flag.startswith(option):
                raise HandshakeError('Abbreviated owned backend path option is ambiguous')
        matches = [i for i, argument in enumerate(sys.argv[1:], 1) if argument == flag or argument.startswith(flag+'=')]
        if len(matches) != 1: raise HandshakeError('Owned backend requires exactly one ' + flag)
        i = matches[0]; arg = sys.argv[i]
        value = arg.split('=', 1)[1] if '=' in arg else sys.argv[i+1] if i+1 < len(sys.argv) else None
        if value != path: raise HandshakeError('Owned backend ' + flag + ' differs from original root')


def _same_process(value, actual):
    from backend.engine.application_launch_lease import _identity_shape
    return _identity_shape(value) and value['pid'] == actual['pid'] and value['created_at'] == actual['created_at'] and value['command_sha256'] == actual['command_sha256']


def _executable(root, binding, frame):
    from backend.engine import runtime_update as update
    _, _, manifest = update._validated_intent(root, binding['update_id'])
    application = root/update.GENERATIONS/binding['application_generation']/'application'
    executable = frame['backend_executable']
    if not isinstance(executable, str) or not Path(executable).is_absolute() or str(Path(executable)) != executable:
        raise HandshakeError('Backend executable must be an absolute current application row')
    path = update._unlinked(executable)
    try: relative = path.relative_to(application).as_posix()
    except ValueError as exc: raise HandshakeError('Backend executable is outside current application') from exc
    rows = [row for row in manifest['files'] if row['path'] == relative and row['executable']]
    if len(rows) != 1: raise HandshakeError('Backend executable is not an approved current application row')
    row = rows[0]; update._check_file(path, row)
    import psutil
    command = psutil.Process(os.getpid()).cmdline()
    frozen = bool(getattr(sys, 'frozen', False)); build = frame['backend_build_identity_sha256']
    if frozen:
        if sys.executable != executable or not command or command[0] != executable or not _hex(build):
            raise HandshakeError('Frozen backend executable or build identity differs')
        receipt_path = path.parent/'backend-release.json'
        receipt_rows = [item for item in manifest['files'] if item['path'] == receipt_path.relative_to(application).as_posix()]
        if len(receipt_rows) != 1: raise HandshakeError('Frozen backend release receipt is not an approved application row')
        update._check_file(receipt_path, receipt_rows[0]); receipt = update._json(update._read(receipt_path, 8*1024**2))
        inventory = update._json(update._read(Path(getattr(sys, '_MEIPASS', ''))/'backend-build-inventory.json', 8*1024**2))
        if (not isinstance(receipt, dict) or receipt.get('schema_version') != 1 or receipt.get('executable') != path.name
                or receipt.get('executable_sha256') != row['sha256'] or not isinstance(inventory, dict)
                or inventory.get('build_identity_sha256') != build
                or update._canonical(receipt.get('inventory')) != update._canonical(inventory)):
            raise HandshakeError('Frozen backend release receipt or embedded inventory differs')
    else:
        if build is not None or not sys.argv or sys.argv[0] != executable or executable not in command[1:2]:
            raise HandshakeError('Source backend must execute its approved script and cannot claim a frozen build')
    return {'executable': executable, 'executable_sha256': row['sha256'],
            'build_identity_sha256': build, 'frozen': frozen}


def _validate(root, values, frame):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    if (set(frame) != _CHALLENGE_FIELDS or type(frame['schema_version']) is not int or frame['schema_version'] != 1
            or frame['kind'] != 'backend_challenge' or not _hex(frame['challenge']) or not _hex(frame['epoch'], 32)
            or frame['nonce'] != values['VISION_APPLICATION_LAUNCH_NONCE'] or type(frame['backend_pid']) is not int
            or frame['backend_pid'] != os.getpid()): raise HandshakeError('Invalid or foreign backend challenge')
    _arguments(root)
    with lease._transition_admission(root, frame['nonce']):
        record = lease._load(root)
        if (record is None or record['nonce'] != frame['nonce'] or record['state'] not in {'starting', 'ready'}
                or not record['spawn_attempted'] or record['process'] is None):
            raise HandshakeError('Backend challenge has no current spawned main owner')
        binding = record['binding']
        if (_canonical(frame['binding']) != _canonical(binding)
                or values['VISION_APPLICATION_GENERATION'] != binding['application_generation']
                or values['VISION_APPLICATION_DATABASE_GENERATION'] != binding['database_generation_path']):
            raise HandshakeError('Backend challenge application/database binding differs')
        fresh = update._launch_binding(root, binding['authority_path'], pinned_authority_sha256=binding['authority_sha256'])
        if _canonical(fresh) != _canonical(binding): raise HandshakeError('Committed backend launch pair changed')
        try: main = lease._identity(os.getppid())
        except Exception as exc:
            import psutil
            if not isinstance(exc, psutil.Error): raise
            raise HandshakeError('Backend parent process identity is unavailable') from exc
        if not _same_process(frame['main_process'], main) or not _same_process(record['process'], main):
            raise HandshakeError('Backend challenge main process birth or parent identity differs')
        executable = _executable(root, binding, frame)
        return {'schema_version': 1, 'kind': 'backend_claim', 'challenge': frame['challenge'], 'epoch': frame['epoch'],
            'nonce': frame['nonce'], 'binding_sha256': hashlib.sha256(_canonical(binding)).hexdigest(),
            'process': lease._identity(os.getpid()), **executable}


def _no_replay(sock, *, ready=False):
    if select.select([sock], [], [], 0)[0]:
        raw = sock.recv(1, socket.MSG_PEEK | getattr(socket, 'MSG_DONTWAIT', 0))
        if raw: raise HandshakeError('Unexpected replay on backend private descriptor')
        if not ready: raise HandshakeError('Backend private descriptor closed before readiness')


def early_backend_bootstrap():
    """Authenticate once before application imports; recheck every local reuse."""
    global _CACHE
    try:
        context = _root_context()
        if _CACHE is not None:
            if context is None or _context() != _CACHE['context']:
                raise HandshakeError('Verified backend process context changed')
            root, values = context
            _no_replay(_CACHE['socket'], ready=_CACHE['ready'])
            proof = _validate(root, values, _CACHE['challenge'])
            if proof != _CACHE['proof']: raise HandshakeError('Verified backend process binding changed')
            return dict(proof)
        if context is None: return None
        root, values = context; sock = _transport(values['VISION_APPLICATION_BACKEND_FD'])
        frame = read_frame(sock, 210); proof = _validate(root, values, frame)
        _no_replay(sock); send_frame(sock, proof)
        _CACHE = {'context': _context(), 'root': root, 'socket': sock, 'challenge': frame, 'proof': proof, 'ready': False}
        return dict(proof)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        if isinstance(exc, HandshakeError): raise
        raise HandshakeError('Owned backend bootstrap refused: ' + str(exc)) from exc


def backend_bootstrap_ready():
    """Publish binding readiness only after recovery under shared lifespan admission."""
    proof = early_backend_bootstrap()
    if proof is None: return None
    from backend.engine.migration_guard import _HELD
    root = _CACHE['root']
    if not any(item['active'] and item['key'] == str(root) and not item['exclusive'] for item in _HELD.get()):
        raise HandshakeError('Backend readiness requires admitted shared application lifespan')
    if not _CACHE['ready']:
        send_frame(_CACHE['socket'], {**proof, 'kind': 'backend_ready'}); _CACHE['ready'] = True
    return {**proof, 'kind': 'backend_ready'}
