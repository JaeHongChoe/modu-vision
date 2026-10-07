"""Persistent controller for a fixed committed owned application/database pair.

Private descriptor receipts prove process and artifact binding only. Native
acceptance, process-tree reconciliation, inference and release stay unqualified.
"""
import argparse
import base64
import hmac
import json
import os
from pathlib import Path
import secrets
import select
import sys
import time
from contextlib import contextmanager

import psutil

from backend.engine import application_launch_lease as lease, runtime_update as update
from backend.engine.global_store_paths import store_admission
from backend.engine.application_launch_handshake import read_frame, send_frame, HandshakeError


def _expected(binding, args):
    if (not update._hex(args.expected_installation_id, 32) or not update._hex(args.expected_update_id, 32)
            or not args.expected_database_fence.isascii() or not args.expected_database_fence.isdecimal()
            or str(int(args.expected_database_fence)) != args.expected_database_fence
            or binding['installation_id'] != args.expected_installation_id
            or binding['update_id'] != args.expected_update_id
            or binding['database_pointer']['fence'] != int(args.expected_database_fence)):
        raise lease.LaunchLeaseError('Expected committed application/database pair differs')


def _same_identity(expected, pid):
    return lease._identity_shape(expected) and update._canonical(expected) == update._canonical(lease._identity(pid))


def _result(binding, row=None, *, authenticated=False, status=None, reason=None):
    value = {'schema_version': 1, 'status': status or (row['state'] if row else 'absent'),
        'nonce': row['nonce'] if row else None, 'installation_id': binding['installation_id'],
        'update_id': binding['update_id'], 'database_fence': binding['database_pointer']['fence'],
        'bootstrap_binding_verified': authenticated,
        'readiness': 'authenticated_controller_binding_only' if authenticated else 'unverified',
        'native_app_handshake_verified': False, 'backend_handshake_verified': False,
        'actual_application_inference_verified': False, 'release_ready': False}
    if value['status'] == 'recovery_required' and reason: value['reason'] = str(reason)[:500]
    return value


@contextmanager
def _inspection_snapshot(root):
    """Read-only inspectors never compete for the backend transition mutex.

    Shared installation admission prevents reserve/start/cancel/cutover. Exact
    publication bytes must stay fixed while journal/artifacts/births are read.
    Concurrent controller transitions produce a refusal, never a mixed result.
    """
    with store_admission(root):
        path = update._unlinked(root/lease.ACTIVE_LEASE)
        before = update._read(path) if path.exists() else None
        snapshots = {}; directory = None; members = None
        if before is not None:
            hint = update._json(before); nonce = hint.get('nonce') if isinstance(hint, dict) else None
            if not update._hex(nonce, 32): raise HandshakeError('Invalid read-only launch pointer')
            directory = update._unlinked(root/lease.LEASES/nonce)
            members = {item.name for item in directory.iterdir()}
            for name in ('journal.json', 'spawn-intent.json', 'bootstrap-receipt.json', 'known-image-receipt.json',
                    'cpu-execution-intent.json','cpu-execution-receipt.json'):
                file = update._unlinked(directory/name)
                snapshots[name] = update._read(file) if file.exists() else None
        yield
        update._unlinked(path)
        after = update._read(path) if path.exists() else None
        if before != after: raise HandshakeError('Launch publication changed during read-only inspection')
        if directory is not None:
            update._unlinked(directory)
            if {item.name for item in directory.iterdir()} != members:
                raise HandshakeError('Launch sidecar publication changed during read-only inspection')
            for name, raw in snapshots.items():
                file = update._unlinked(directory/name)
                fresh = update._read(file) if file.exists() else None
                if raw != fresh: raise HandshakeError('Launch journal or receipt changed during read-only inspection')


def inspect(args):
    root, _ = update._root(args.root)
    with _inspection_snapshot(root):
        binding = update._launch_binding(root, args.authority, pinned_authority_sha256=args.pinned_authority_sha256)
        _expected(binding, args); row = lease._load(root)
        if row is None: return _result(binding)
        if update._canonical(binding) != update._canonical(row['binding']):
            if row['state'] == 'exited': return _result(binding)
            raise lease.LaunchLeaseError('Unresolved launch belongs to a different committed pair')
        if row['state'] == 'exited': return _result(binding, row)
        try:
            if not _same_identity(row['supervisor'], row['supervisor']['pid']): raise HandshakeError('Original controller process birth differs')
            if row['process'] is not None and not _same_identity(row['process'], row['process']['pid']): raise HandshakeError('Main process birth differs')
            authenticated = False
            path = root/lease.LEASES/row['nonce']/'bootstrap-receipt.json'
            if row['state'] == 'ready' and path.exists():
                receipt = update._json(update._read(path))  # _load checked its exact hash and fields.
                backend = receipt['backend_process']
                if not _same_identity(backend, backend['pid']) or psutil.Process(backend['pid']).ppid() != row['process']['pid']:
                    raise HandshakeError('Backend process birth or parent differs')
                _backend_artifact(root, binding, receipt['backend_executable'], receipt['backend_executable_sha256'],
                    receipt['backend_build_identity_sha256'], receipt['backend_frozen'], backend['pid'])
                authenticated = True
            return _result(binding, row, authenticated=authenticated, reason=row['reason'])
        except (psutil.Error, OSError, ValueError) as exc:
            # An observation never clears or rewrites durable ownership.
            return _result(binding, row, status='recovery_required', reason=str(exc))


def _backend_artifact(root, binding, executable, digest, build, frozen, pid):
    if type(frozen) is not bool or not isinstance(executable, str) or not Path(executable).is_absolute():
        raise HandshakeError('Invalid backend artifact identity')
    path = update._unlinked(executable)
    application = root/update.GENERATIONS/binding['application_generation']/'application'
    relative = path.relative_to(application).as_posix()
    _, _, manifest = update._validated_intent(root, binding['update_id'])
    rows = [row for row in manifest['files'] if row['path'] == relative and row['executable']]
    if len(rows) != 1 or digest != rows[0]['sha256']: raise HandshakeError('Backend artifact is not a committed executable row')
    update._check_file(path, rows[0]); process = psutil.Process(pid); command = process.cmdline()
    if frozen:
        if not update._hex(build) or process.exe() != executable or not command or command[0] != executable:
            raise HandshakeError('Frozen backend process executable differs')
        receipt_path = path.parent/'backend-release.json'; relative = receipt_path.relative_to(application).as_posix()
        rows = [row for row in manifest['files'] if row['path'] == relative]
        if len(rows) != 1: raise HandshakeError('Frozen backend receipt is not committed')
        update._check_file(receipt_path, rows[0]); receipt = update._json(update._read(receipt_path, 8*1024**2))
        if (not isinstance(receipt, dict) or receipt.get('schema_version') != 1 or receipt.get('executable') != path.name
                or receipt.get('executable_sha256') != digest or not isinstance(receipt.get('inventory'), dict)
                or receipt['inventory'].get('build_identity_sha256') != build):
            raise HandshakeError('Frozen backend release binding differs')
    elif build is not None or executable not in command[1:2]:
        raise HandshakeError('Source backend process is not the committed script')


def _proof(raw):
    if not isinstance(raw, str) or len(raw) > 16384: raise HandshakeError('Backend proof is unbounded')
    try: decoded = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as exc: raise HandshakeError('Invalid backend proof encoding') from exc
    if len(decoded) > 8192: raise HandshakeError('Backend proof is unbounded')
    try: value = update._json(decoded)
    except (ValueError, RecursionError) as exc: raise HandshakeError('Invalid bounded backend proof') from exc
    fields = {'schema_version', 'kind', 'challenge', 'epoch', 'nonce', 'binding_sha256', 'process',
        'executable', 'executable_sha256', 'build_identity_sha256', 'frozen'}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['kind'], str) or value['kind'] not in {'backend_claim', 'backend_ready'}
            or not update._hex(value['challenge']) or not update._hex(value['epoch'], 32)
            or not update._hex(value['nonce'], 32) or not update._hex(value['binding_sha256'])
            or not lease._identity_shape(value['process']) or not isinstance(value['executable'], str)
            or not Path(value['executable']).is_absolute() or str(Path(value['executable'])) != value['executable']
            or not update._hex(value['executable_sha256'])
            or value['build_identity_sha256'] is not None and not update._hex(value['build_identity_sha256'])
            or type(value['frozen']) is not bool or value['frozen'] != (value['build_identity_sha256'] is not None)):
        raise HandshakeError('Invalid exact backend proof')
    try:
        if update._canonical(value) != decoded: raise HandshakeError('Backend proof is not canonical')
    except RecursionError as exc: raise HandshakeError('Backend proof nesting exceeds its bound') from exc
    return value


def authenticate(owner, row):
    channel = owner._bootstrap_channel; challenge = secrets.token_hex(32); binding = row['binding']
    send_frame(channel, {'schema_version': 1, 'kind': 'main_challenge', 'challenge': challenge,
        'nonce': row['nonce'], 'binding': binding, 'process': row['process'], 'transport': owner._bootstrap_transport})
    claim = read_frame(channel, 210)
    expected = {'schema_version': 1, 'kind': 'main_claim', 'challenge': challenge, 'nonce': row['nonce'],
        'binding_sha256': update._sha(update._canonical(binding)), 'pid': row['process']['pid']}
    if update._canonical(claim) != update._canonical(expected): raise HandshakeError('Main claim differs from private challenge')
    owner.claim(row['process'])
    send_frame(channel, {**expected, 'kind': 'main_admitted'})
    frame = read_frame(channel, 210)
    if (set(frame) != {'schema_version', 'kind', 'challenge', 'nonce', 'claim_b64', 'ready_b64'}
            or type(frame['schema_version']) is not int or frame['schema_version'] != 1 or frame['kind'] != 'backend_proof'
            or not update._hex(frame['challenge']) or not hmac.compare_digest(frame['challenge'], challenge)
            or frame['nonce'] != row['nonce']): raise HandshakeError('Backend forwarding capability differs')
    first = _proof(frame['claim_b64']); ready = _proof(frame['ready_b64'])
    if (first['kind'] != 'backend_claim' or ready['kind'] != 'backend_ready'
            or update._canonical({**first, 'kind': 'backend_ready'}) != update._canonical(ready)
            or ready['nonce'] != row['nonce'] or ready['binding_sha256'] != expected['binding_sha256']
            or not _same_identity(ready['process'], ready['process']['pid'])
            or psutil.Process(ready['process']['pid']).ppid() != row['process']['pid']):
        raise HandshakeError('Backend readiness process binding differs')
    with lease._transition_admission(owner.root, owner.nonce):
        current = owner._owned(); owner._binding(current); owner._live(current, row['process'])
        _backend_artifact(owner.root, binding, ready['executable'], ready['executable_sha256'],
            ready['build_identity_sha256'], ready['frozen'], ready['process']['pid'])
    receipt = {'schema_version': 1, 'kind': 'authenticated_controller_binding_only', 'nonce': row['nonce'],
        'binding': binding, 'main_process': row['process'], 'backend_process': ready['process'],
        'backend_executable': ready['executable'], 'backend_executable_sha256': ready['executable_sha256'],
        'backend_build_identity_sha256': ready['build_identity_sha256'], 'backend_frozen': ready['frozen'],
        'challenge_sha256': update._sha(challenge.encode()), 'epoch': ready['epoch']}
    owner._publish_bootstrap(receipt)
    owner._authenticated_backend_proof = first
    return ready['process']


def _emit(value):
    # The updater deliberately closes its pipes after the first line. This must
    # never release the original controller's private descriptor or DB handle.
    raw = update._canonical(value) + b'\n'
    if len(raw) > 65536: raise HandshakeError('Controller acknowledgement is unbounded')
    try:
        total = 0
        while total < len(raw): total += os.write(1, raw[total:])
    except OSError: pass  # A lost updater pipe never changes controller ownership.


def _cpu_capability(args, root):
    values=[getattr(args,name,None) for name in ('cpu_known_image_workspace_id','cpu_known_image_project_id','cpu_known_image_plan_sha256')]
    if not any(value is not None for value in values):return None
    if any(value is None for value in values) or args.inspect:raise HandshakeError('CPU known-image request requires all pins and a new owned launch')
    if getattr(sys,'frozen',False):raise HandshakeError('requires_target: fixed frozen CPU worker is not qualified')
    from backend.engine.application_launch_execution import admit_plan
    return admit_plan(root,*values)


def execute_cpu(owner, capability):
    """Dispatch exactly once through the original authenticated main/backend."""
    from backend.engine import application_launch_execution as execution
    with lease._transition_admission(owner.root,owner.nonce):
        row=owner._owned();owner._binding(row);owner._live(row,row['process'])
        bootstrap=update._json(update._read(owner.root/lease.LEASES/owner.nonce/'bootstrap-receipt.json'))
        if bootstrap['backend_frozen']:raise HandshakeError('requires_target: fixed frozen CPU worker is not qualified')
        request=execution.request(owner,capability,bootstrap['epoch'])
    intent=owner._begin_cpu_execution(request,capability)
    send_frame(owner._bootstrap_channel,request)
    frame=read_frame(owner._bootstrap_channel,210)
    if (set(frame)!={'schema_version','kind','nonce','proof_b64'} or type(frame['schema_version']) is not int
            or frame['schema_version']!=1 or frame['kind']!='cpu_execution_proof' or frame['nonce']!=owner.nonce):
        raise HandshakeError('CPU forwarding capability differs')
    proof=execution.encoded_proof(frame['proof_b64'])
    receipt=execution.verify_completion(owner,intent,proof)
    owner._publish_cpu_execution(receipt)
    return receipt


def run(args):
    root, _ = update._root(args.root)
    with store_admission(root, exclusive=True):
        binding = update._launch_binding(root, args.authority, pinned_authority_sha256=args.pinned_authority_sha256)
        _expected(binding, args)
        capability=_cpu_capability(args,root)
        owner = lease.LaunchSupervisor.reserve(root, args.authority, pinned_authority_sha256=args.pinned_authority_sha256)
        try: row = owner.start(bootstrap=True)
        except Exception as exc:
            # Reservation already owns a durable nonce. An interrupted spawn
            # must keep the original handles, even if its journal is ambiguous.
            try: owner.recovery('Spawn admission interrupted: '+str(exc))
            except (ValueError, OSError, psutil.Error): pass
            row = {'state': 'recovery_required', 'nonce': owner.nonce}
    _emit(_result(binding, row))
    backend = None; failed = row['state'] != 'starting'; pending_reason = None
    if not failed:
        try:
            backend = authenticate(owner, row)
            if capability is not None:execute_cpu(owner,capability)
        except Exception as exc:
            # After spawn, even an unexpected validator/transport exception
            # keeps the original handles. Durable ownership is never released.
            pending_reason = 'Private bootstrap or execution refused: '+str(exc); failed = True
    # No further stdout/stderr writes. No stop on updater stdin/pipe EOF.
    while True:
        if not failed:
            try:
                if owner._process.poll() is not None: owner.observe_exit(); failed = True
                elif not _same_identity(row['process'], row['process']['pid']):
                    raise HandshakeError('Authenticated main process birth or command changed')
                elif backend is not None and (not _same_identity(backend, backend['pid'])
                        or psutil.Process(backend['pid']).ppid() != row['process']['pid']):
                    raise HandshakeError('Authenticated backend ownership changed')
                elif select.select([owner._bootstrap_channel], [], [], 0)[0]:
                    raise HandshakeError('Private main descriptor ended or replayed after readiness')
            except Exception as exc:
                pending_reason = 'Process-tree ownership is unresolved: '+str(exc); failed = True
        if pending_reason is not None:
            try: owner.recovery(pending_reason); pending_reason = None
            except Exception: pass
        time.sleep(.2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    observers=parser.add_mutually_exclusive_group()
    observers.add_argument('--inspect', action='store_true')
    observers.add_argument('--inspect-cpu-execution', action='store_true')
    parser.add_argument('--expected-launch-nonce')
    for name in ('root', 'authority', 'pinned-authority-sha256', 'expected-installation-id', 'expected-update-id', 'expected-database-fence'):
        parser.add_argument('--'+name, required=True)
    for name in ('cpu-known-image-workspace-id','cpu-known-image-project-id','cpu-known-image-plan-sha256'):
        parser.add_argument('--'+name)
    args = parser.parse_args(argv)
    try:
        if args.inspect_cpu_execution:
            from backend.engine.application_launch_execution import inspect_execution
            _emit(inspect_execution(args));return 0
        if args.expected_launch_nonce is not None:raise HandshakeError('Expected launch nonce is only valid for CPU receipt inspection')
        if args.inspect:
            if any(getattr(args,name) is not None for name in ('cpu_known_image_workspace_id','cpu_known_image_project_id','cpu_known_image_plan_sha256')):
                raise HandshakeError('Read-only lifecycle inspection cannot dispatch CPU execution')
            _emit(inspect(args)); return 0
        run(args)
    except (ValueError, OSError, psutil.Error) as exc:
        # This handler is only valid before durable spawning; run contains all
        # post-spawn failures and never emits a second acknowledgement.
        _emit({'schema_version': 1, 'status': 'refused', 'error': str(exc)[:2048]}); return 2


if __name__ == '__main__': raise SystemExit(main())
