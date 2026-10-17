"""Persistent controller for a fixed committed owned application/database pair.

Private descriptor receipts prove process and artifact binding only. Native
acceptance, process-tree reconciliation, inference and release stay unqualified.
"""
import argparse
import base64
import hmac
import json
import math
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
from backend.engine.application_launch_handshake import (read_frame, send_frame, HandshakeError,
    _transition_admission_before_deadline)
from backend.engine import application_node_writer_authority as node


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
        body_error = None
        try:
            yield
        except Exception as exc:
            # A failed body still needs the same fresh publication checks.
            # BaseException interruptions retain their original priority.
            body_error = exc
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
        if body_error is not None:
            raise body_error


def inspect(args):
    root, _ = update._root(args.root)
    with _inspection_snapshot(root):
        binding = update._launch_binding(root, args.authority, pinned_authority_sha256=args.pinned_authority_sha256)
        _expected(binding, args); row = lease._load(root)
        if row is None: return _result(binding)
        if update._canonical(binding) != update._canonical(row['binding']):
            if (row['state'] == 'exited' or row.get('protocol_version') == 5
                    and row['state'] == 'writer_store_closed'): return _result(binding)
            raise lease.LaunchLeaseError('Unresolved launch belongs to a different committed pair')
        if row['state'] == 'exited': return _result(binding, row)
        if row.get('protocol_version') == 5 and row['state'] == 'writer_store_closed':
            # _load validated the exact protocol5 archive through its original
            # protocol4 shadow. This is scoped history, not a live-PID or whole
            # tree assertion; later mutation admission checks controller exit.
            return {**_result(binding, row), 'store_writer_closure_scope': 'original_managed_store_writers',
                'process_tree_exit_verified': False, 'whole_writer_coverage': False}
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


def _registration_remaining(deadline):
    remaining = deadline - time.monotonic()
    if not 0 < remaining <= 210:
        raise HandshakeError('Original backend registration deadline expired or renewed')
    return remaining


def _main_claim_remaining(deadline):
    remaining = deadline - time.monotonic()
    if not 0 < remaining <= 210:
        raise HandshakeError('Original main claim deadline expired or renewed')
    return remaining


def authenticate(owner, row):
    main_deadline = time.monotonic() + 210
    channel = owner._bootstrap_channel; challenge = secrets.token_hex(32); binding = row['binding']
    original_root = owner.root; original_nonce = owner.nonce; original_child = owner._process
    original_transport = update._canonical(owner._bootstrap_transport)
    original_binding = update._canonical(binding); original_process = update._canonical(row['process'])
    main_challenge = {'schema_version': 1, 'kind': 'main_challenge', 'challenge': challenge,
        'nonce': row['nonce'], 'binding': binding, 'process': row['process'], 'transport': owner._bootstrap_transport}
    if row.get('writer_drain') is not None:
        main_challenge['writer'] = {k: row['writer_drain'][k] for k in ('writer_id', 'registration_sha256', 'registration_registry_sha256')}
    authority = getattr(owner, '_node_backend_authority', None)
    def original_scope():
        if (owner.root != original_root or owner.nonce != original_nonce or row['nonce'] != original_nonce
                or owner._bootstrap_channel is not channel or owner._process is not original_child
                or getattr(owner, '_node_backend_authority', None) is not authority
                or update._canonical(owner._bootstrap_transport) != original_transport
                or update._canonical(binding) != original_binding
                or update._canonical(row['binding']) != original_binding
                or update._canonical(row['process']) != original_process):
            raise HandshakeError('Original authentication scope changed while entering')
        current = owner._owned(); owner._binding(current); owner._live(current, row['process'])
        if (current['state'] != 'starting' or update._canonical(current['binding']) != original_binding
                or update._canonical(current['process']) != original_process):
            raise HandshakeError('Original authentication binding changed while entering')
    _main_claim_remaining(main_deadline)
    if authority is not None:
        node.send_authentication_challenge(authority, main_challenge)
        claim_event = node.receive_frame(authority, _main_claim_remaining(main_deadline))
        claim = node.frame_value(authority, claim_event)
    else:
        send_frame(channel, main_challenge, absolute_deadline=main_deadline)
        claim_event = None
        claim = read_frame(channel, _main_claim_remaining(main_deadline))
    expected = {'schema_version': 1, 'kind': 'main_claim', 'challenge': challenge, 'nonce': row['nonce'],
        'binding_sha256': update._sha(update._canonical(binding)), 'pid': row['process']['pid']}
    if update._canonical(claim) != update._canonical(expected): raise HandshakeError('Main claim differs from private challenge')
    original_claim = update._canonical(claim); original_expected = update._canonical(expected)
    def original_main_claim():
        _main_claim_remaining(main_deadline); original_scope()
        if (update._canonical(claim) != original_claim or update._canonical(expected) != original_expected
                or original_claim != original_expected
                or authority is not None and update._canonical(node.frame_value(authority, claim_event)) != original_claim):
            raise HandshakeError('Original main claim changed while entering')
    owner.claim(row['process'], absolute_deadline=main_deadline, before_attempt=original_main_claim)
    original_main_claim()
    send_frame(channel, {**expected, 'kind': 'main_admitted'}, absolute_deadline=main_deadline)
    _main_claim_remaining(main_deadline)
    proof_deadline = time.monotonic() + 210
    proof_event = node.receive_frame(authority, _registration_remaining(proof_deadline)) if authority is not None else None
    frame = node.frame_value(authority, proof_event) if authority is not None else read_frame(channel, _registration_remaining(proof_deadline))
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
    original_frame = update._canonical(frame)
    original_first = update._canonical(first); original_ready = update._canonical(ready)
    def original_backend_proof():
        _registration_remaining(proof_deadline); original_scope()
        if (update._canonical(frame) != original_frame or update._canonical(first) != original_first
                or update._canonical(ready) != original_ready
                or authority is not None and update._canonical(node.frame_value(authority, proof_event)) != original_frame):
            raise HandshakeError('Original backend proof changed while entering')
        if (not _same_identity(ready['process'], ready['process']['pid'])
                or psutil.Process(ready['process']['pid']).ppid() != row['process']['pid']):
            raise HandshakeError('Original backend proof process binding changed while entering')
    with _transition_admission_before_deadline(original_root, original_nonce, proof_deadline,
            before_attempt=original_backend_proof):
        original_backend_proof()
        current = owner._owned(); owner._binding(current); owner._live(current, row['process'])
        _backend_artifact(owner.root, binding, ready['executable'], ready['executable_sha256'],
            ready['build_identity_sha256'], ready['frozen'], ready['process']['pid'])
    receipt = {'schema_version': 1, 'kind': 'authenticated_controller_binding_only', 'nonce': row['nonce'],
        'binding': binding, 'main_process': row['process'], 'backend_process': ready['process'],
        'backend_executable': ready['executable'], 'backend_executable_sha256': ready['executable_sha256'],
        'backend_build_identity_sha256': ready['build_identity_sha256'], 'backend_frozen': ready['frozen'],
        'challenge_sha256': update._sha(challenge.encode()), 'epoch': ready['epoch']}
    if authority is None:
        owner._publish_bootstrap(receipt)
        owner._authenticated_backend_proof = first
        return ready['process']
    owner._authenticated_backend_proof = first
    _registration_remaining(proof_deadline)
    node.seal_authentication(authority, proof_event)
    snapshot = owner._writer_epoch.snapshot(); drain = owner._owned()['writer_drain']
    writers = [writer for writer in snapshot['registry']['writers'] if writer['writer_id'] == drain['writer_id']]
    if (snapshot['registry']['state'] != 'open' or len(writers) != 1 or writers[0]['role'] != 'backend'
            or writers[0]['status'] != 'active' or writers[0]['process'] != ready['process']
            or writers[0]['registration_sha256'] != drain['registration_sha256']):
        raise HandshakeError('Durable original backend registration differs before acknowledgement')
    registered = {'schema_version': 1, 'kind': 'backend_registered', 'nonce': row['nonce'],
        'epoch': first['epoch'], 'binding_sha256': expected['binding_sha256'],
        'backend_claim_sha256': update._sha(update._canonical(first)),
        'main_process': row['process'], 'backend_process': first['process'],
        'writer_id': drain['writer_id'], 'registration_sha256': drain['registration_sha256'],
        'active_registry_sha256': snapshot['registry_sha256']}
    _registration_remaining(proof_deadline)
    send_frame(channel, registered, absolute_deadline=proof_deadline)
    admitted = node.receive_frame(authority, _registration_remaining(proof_deadline))
    _registration_remaining(proof_deadline)
    node.seal_registration_admission(authority, admitted, registered)
    _registration_remaining(proof_deadline)
    owner._publish_bootstrap(receipt, absolute_deadline=proof_deadline)
    _registration_remaining(proof_deadline)
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
    from backend.engine.application_launch_execution import admit_plan
    return admit_plan(root,*values)


def _preflight_controller_event(owner, event):
    from backend.engine import application_preflight_child_relay as relay
    authority = owner._node_backend_authority
    request = node.frame_value(authority, event)['request']
    try:
        reply = relay.process_controller_event(owner, event)
        state, _ = node._fresh(authority)
        cap = owner._preflight_relays[request['request_id']]
        deadline = relay._deadline(relay._CONTROLLERS[cap])
        send_frame(state['channel'], reply, absolute_deadline=deadline)
        node._fresh(authority)
        relay._deadline(relay._CONTROLLERS[cap])
    except BaseException:
        cap = getattr(owner, '_preflight_relays', {}).get(request.get('request_id'))
        if cap is not None: relay._retain(cap)
        raise


def _cpu_controller_event(owner,event):
    from backend.engine import application_owned_cpu_child_relay as cpu
    authority=owner._node_backend_authority
    request=node.frame_value(authority,event)['request']
    try:
        reply=cpu.process_cpu_controller_event(owner,event)
        state,_=node._fresh(authority);cap=owner._cpu_relays[request['request_id']]
        deadline=cpu._controller_deadline(cpu._CONTROLLERS[cap])
        send_frame(state['channel'],reply,absolute_deadline=deadline)
        node._fresh(authority);cpu._controller_deadline(cpu._CONTROLLERS[cap])
    except BaseException:
        cap=getattr(owner,'_cpu_relays',{}).get(request.get('request_id'))
        if cap is not None:cpu._retain(cap)
        raise


def _controller_reply(owner, deadline, expected_kind, *, shutdown=None):
    """Sole controller reader, with the original absolute CPU/drain budget."""
    authority = getattr(owner, '_node_backend_authority', None)
    while True:
        remaining = deadline-time.monotonic()
        if remaining <= 0: raise HandshakeError('Original controller reply deadline expired')
        if authority is None: frame = read_frame(owner._bootstrap_channel, remaining)
        else:
            event = node.receive_frame(authority, remaining, shutdown=shutdown)
            frame = node.frame_value(authority, event, shutdown=shutdown)
            if shutdown is not None:
                if frame.get('kind') != expected_kind: raise HandshakeError('Prepared shutdown reply kind differs')
                return frame
            if frame.get('kind') == 'main_preflight_request':
                _preflight_controller_event(owner, event)
                continue
            if frame.get('kind')=='main_cpu_child_request':
                _cpu_controller_event(owner,event)
                continue
            if frame.get('kind')=='source_cpu_settled' and getattr(owner,'_cpu_awaiting_settlement',None):
                from backend.engine import application_owned_cpu_child_relay as cpu
                if getattr(owner,'_cpu_settlement',None) is not None:raise HandshakeError('Original SOURCE CPU settlement replayed')
                owner._cpu_settlement=cpu.admit_controller_settlement(owner,frame)
                if expected_kind=='source_cpu_settled':return owner._cpu_settlement
                if expected_kind=='managed_drain_proof':continue
                raise HandshakeError('Original SOURCE CPU settlement unexpected')
            if frame.get('kind')=='main_drain_request' and expected_kind=='source_cpu_settled':
                # Receipt is already independently published; only accepted
                # finish/settlement may complete inside this original drain.
                exit_deadline=prepare_drain(owner,frame)
                event=_receive_admitted_backend_exit(owner,exit_deadline)
                observe_backend_exit(owner,event,absolute_deadline=exit_deadline)
                settled=getattr(owner,'_cpu_settlement',None)
                if settled is None:raise HandshakeError('Original SOURCE CPU drain lacks final settlement')
                if time.monotonic()>=deadline:raise HandshakeError('Original SOURCE CPU settlement is late')
                return settled
        if frame.get('kind') != expected_kind:
            raise HandshakeError('Original controller reply kind differs')
        return frame


def execute_cpu(owner, capability):
    """Dispatch exactly once through the original authenticated main/backend."""
    from backend.engine import application_launch_execution as execution
    with lease._transition_admission(owner.root,owner.nonce):
        row=owner._owned();owner._binding(row);owner._live(row,row['process'])
        bootstrap=update._json(update._read(owner.root/lease.LEASES/owner.nonce/'bootstrap-receipt.json'))
        request=execution.request(owner,capability,bootstrap['epoch'])
    intent=owner._begin_cpu_execution(request,capability)
    source_cpu=(getattr(owner,'_node_backend_authority',None) is not None and owner._writer_epoch is not None
        and bootstrap['backend_frozen'] is False and capability['plan'].get('kind')=='owned_cpu_ocr_known_image_plan')
    if owner._writer_epoch is not None and not source_cpu:
        # CPU descriptor propagation is verified separately by its helper.
        # This blocking row remains until complete adapter coverage is proven.
        owner._writer_epoch.block_unsupported('owned_cpu_worker',
            expected_registry_sha256=owner._writer_epoch.snapshot()['registry_sha256'])
    if bootstrap['backend_frozen'] is True and getattr(owner,'_cpu_reply_deadline',None) is not None:
        raise HandshakeError('Original frozen CPU reply deadline was already captured')
    send_frame(owner._bootstrap_channel,request)
    # Retain the original one 210-second controller-reply bound through the
    # frozen controller's two post-proof mutex entries. It is never renewed.
    reply_deadline=time.monotonic()+210
    frozen_settlement=bootstrap['backend_frozen'] is True and source_cpu is False
    if frozen_settlement:owner._cpu_reply_deadline=reply_deadline
    try:
        frame=_controller_reply(owner,reply_deadline,'cpu_execution_proof')
        if (set(frame)!={'schema_version','kind','nonce','proof_b64'} or type(frame['schema_version']) is not int
                or frame['schema_version']!=1 or frame['kind']!='cpu_execution_proof' or frame['nonce']!=owner.nonce):
            raise HandshakeError('CPU forwarding capability differs')
        proof=execution.encoded_proof(frame['proof_b64'])
        if frozen_settlement:
            receipt=execution.verify_completion(owner,intent,proof,absolute_deadline=reply_deadline)
            receipt_sha=owner._publish_cpu_execution(receipt,absolute_deadline=reply_deadline)
        else:
            receipt=execution.verify_completion(owner,intent,proof)
            receipt_sha=owner._publish_cpu_execution(receipt)
    finally:
        if frozen_settlement:owner._cpu_reply_deadline=None
    if source_cpu:
        from backend.engine import application_owned_cpu_child_relay as cpu
        cap=owner._cpu_relays[request['request_id']]
        try:
            ack=cpu.admit_controller_publication(owner,proof,receipt,receipt_sha)
            state,_=node._fresh(owner._node_backend_authority)
            deadline=cpu._controller_deadline(cpu._CONTROLLERS[cap]);owner._cpu_awaiting_settlement=request['request_id']
            send_frame(state['channel'],ack,absolute_deadline=deadline)
            node._fresh(owner._node_backend_authority);cpu._controller_deadline(cpu._CONTROLLERS[cap])
            _controller_reply(owner,deadline,'source_cpu_settled')
        except BaseException:
            cpu._retain(cap);raise
    return receipt


def prepare_drain(owner, frame, *, shutdown=None):
    """Original private main channel only; close epoch before backend gate."""
    started = time.monotonic()
    from backend.engine.application_launch_handshake import validate_drain_receipt, _transition_admission_before_deadline
    # Reject an unbounded/malformed budget before any wait. Retain the exact
    # already-received frame and this original start clock throughout.
    budget = frame.get('budget_ms') if type(frame) is dict else None
    if type(budget) is not int or not 0 < budget <= 4000:
        raise HandshakeError('Original main managed drain request differs or replayed')
    deadline = started + budget/1000
    authority = getattr(owner, '_node_backend_authority', None)
    def entry_current():
        if authority is not None: node._fresh(authority, shutdown=shutdown)
    if shutdown is not None:
        from backend.engine import application_shutdown_transaction as transaction
        transaction.begin_controller_drain(shutdown, frame, started)
    with _transition_admission_before_deadline(owner.root, owner.nonce, deadline,
                                              before_attempt=entry_current):
        row = owner._owned()
        if shutdown is None: owner._binding(row)
        else: transaction.current_controller(shutdown, authority, row)
        owner._live(row, row['process'])
        proof = owner._authenticated_backend_proof
        names = {'schema_version', 'kind', 'nonce', 'epoch', 'binding_sha256', 'backend_claim_sha256', 'request_id', 'budget_ms'}
        if (set(frame) != names or type(frame['schema_version']) is not int or frame['schema_version'] != 1
                or frame['kind'] != 'main_drain_request' or frame['nonce'] != owner.nonce
                or frame['epoch'] != proof['epoch'] or frame['binding_sha256'] != proof['binding_sha256']
                or frame['backend_claim_sha256'] != update._sha(update._canonical(proof))
                or not update._hex(frame['request_id'], 32) or type(frame['budget_ms']) is not int
                or not 0 < frame['budget_ms'] <= 4000 or owner._writer_epoch is None
                or row.get('writer_drain', {}).get('phase') != 'enrolled'):
            raise HandshakeError('Original main managed drain request differs or replayed')
        drain = row['writer_drain']
    authority = getattr(owner, '_node_backend_authority', None)
    if authority is not None: node.begin_drain(authority, frame, started, shutdown=shutdown)
    if shutdown is None:
        snapshot = owner._writer_epoch.snapshot()
        owner._writer_epoch.close_epoch(expected_registry_sha256=snapshot['registry_sha256'])
        closed = owner._writer_epoch.snapshot()
    else: closed = transaction.close_controller_epoch(shutdown)
    remaining = frame['budget_ms'] - int((time.monotonic()-started)*1000)
    if remaining <= 0: raise HandshakeError('Original managed shutdown budget expired before gate')
    request = {'schema_version': 1, 'kind': 'backend_drain_request', 'challenge': secrets.token_hex(32),
        'request_id': frame['request_id'], 'nonce': owner.nonce, 'epoch': proof['epoch'],
        'binding_sha256': proof['binding_sha256'], 'backend_claim_sha256': frame['backend_claim_sha256'],
        'writer_id': drain['writer_id'], 'registration_sha256': drain['registration_sha256'],
        'closed_registry_sha256': closed['registry_sha256'], 'budget_ms': remaining}
    if shutdown is None: owner.publish_writer_drain(phase='closing', request=request)
    else:
        with lease._transition_admission(owner.root,owner.nonce): transaction.publish_controller(shutdown,phase='closing',request=request)
    send_frame(owner._bootstrap_channel, request, absolute_deadline=started+frame['budget_ms']/1000)
    remaining = frame['budget_ms']/1000 - (time.monotonic()-started)
    if remaining <= 0: raise HandshakeError('Original managed shutdown budget expired before response')
    reply = _controller_reply(owner, started+frame['budget_ms']/1000, 'managed_drain_proof',shutdown=shutdown)
    if (set(reply) != {'schema_version', 'kind', 'nonce', 'proof_b64'} or type(reply['schema_version']) is not int
            or reply['schema_version'] != 1 or reply['kind'] != 'managed_drain_proof' or reply['nonce'] != owner.nonce):
        raise HandshakeError('Original main managed drain forwarding differs')
    raw = reply['proof_b64']
    if not isinstance(raw, str) or len(raw) > 32768: raise HandshakeError('Managed drain proof is unbounded')
    try: decoded = base64.b64decode(raw, validate=True); receipt = update._json(decoded)
    except (ValueError, TypeError, RecursionError) as exc: raise HandshakeError('Invalid managed drain proof') from exc
    if len(decoded) > 16384 or update._canonical(receipt) != decoded: raise HandshakeError('Managed drain proof is not bounded/canonical')
    validate_drain_receipt(receipt, request, backend_process=proof['process'])
    deadline = started + frame['budget_ms']/1000
    if shutdown is not None:
        transaction.provisional_receipt(shutdown,receipt)
        send_frame(owner._bootstrap_channel, {'schema_version':1,'kind':'managed_drain_admitted',
            'nonce':owner.nonce,'request_id':request['request_id'],'receipt_sha256':update._sha(decoded),'status':receipt['status']},absolute_deadline=deadline)
        if time.monotonic()>=deadline:raise HandshakeError('Original physical shutdown acknowledgement expired')
        return deadline
    while True:
        if time.monotonic() >= deadline:
            raise HandshakeError('Original managed shutdown budget expired before publication')
        try:
            # A concurrent original backend binding read may briefly hold this
            # mutex. Each attempt retains the original owner/phase/CAS checks;
            # no other error, new deadline, or partial publication is retried.
            owner.publish_writer_drain(phase='drained' if receipt['status'] == 'managed_scopes_drained' else 'refused', receipt=receipt)
            break
        except lease.LeaseTransitionBusy:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HandshakeError('Original managed shutdown budget expired during publication')
            time.sleep(min(.01, remaining))
    if time.monotonic() >= deadline:
        raise HandshakeError('Original managed shutdown budget expired before acknowledgement')
    send_frame(owner._bootstrap_channel, {'schema_version': 1, 'kind': 'managed_drain_admitted',
        'nonce': owner.nonce, 'request_id': request['request_id'], 'receipt_sha256': update._sha(decoded), 'status': receipt['status']}, absolute_deadline=deadline)
    if receipt['status'] != 'managed_scopes_drained': raise HandshakeError('Backend managed scope retains uncovered/active writers')
    return deadline


def shutdown_transaction(owner, event, *, started=None):
    from backend.engine import application_shutdown_transaction as transaction
    authority=owner._node_backend_authority
    started=time.monotonic() if started is None else started
    admission,frame=node.admit_shutdown_preparation(authority,event)
    cap=transaction.prepare_controller(owner,authority,frame,started=started,admission=admission)
    state=transaction._state(cap)
    remaining=int((state['preparation_deadline']-time.monotonic())*1000)
    if remaining<=0:raise HandshakeError('Original full-byte shutdown preparation expired')
    send_frame(owner._bootstrap_channel,{**frame,'kind':'backend_shutdown_prepare','budget_ms':remaining},
        absolute_deadline=state['preparation_deadline'])
    reply=_controller_reply(owner,state['preparation_deadline'],'main_shutdown_prepared',shutdown=cap)
    transaction.prepared_controller(cap,reply)
    send_frame(owner._bootstrap_channel,{'schema_version':1,'kind':'shutdown_prepared','nonce':owner.nonce,
        'request_id':frame['request_id'],'post_budget_ms':transaction.POSTCHECK_BUDGET_MS},absolute_deadline=state['preparation_deadline'])
    remaining=state['preparation_deadline']-time.monotonic()
    commit=node.receive_frame(authority,remaining,shutdown=cap)
    request=node.frame_value(authority,commit,shutdown=cap)
    if request.get('kind')!='main_drain_request':raise HandshakeError('Prepared shutdown commit kind differs')
    deadline=prepare_drain(owner,request,shutdown=cap)
    exit_event=node.receive_frame(authority,_exit_remaining(deadline),shutdown=cap)
    transaction.capture_controller_exit(cap,exit_event)
    return transaction.finalize_controller(cap)


def _exit_remaining(absolute_deadline):
    if type(absolute_deadline) not in (int, float) or not math.isfinite(absolute_deadline):
        raise HandshakeError('Original admitted exit deadline differs')
    remaining = absolute_deadline - time.monotonic()
    if not 0 < remaining <= 4:
        raise HandshakeError('Original admitted exit deadline expired or renewed')
    return remaining


def _receive_admitted_backend_exit(owner, absolute_deadline):
    """Receive an original exit witness before applying live-backend checks.

    A terminal admitted drain may legitimately outlive the backend. Only its
    original typed receive event can prove the exit; absence or a PID cannot.
    """
    authority = getattr(owner, '_node_backend_authority', None)
    if authority is None: raise HandshakeError('Original typed Node backend authority is unavailable')
    while True:
        _exit_remaining(absolute_deadline)
        drain = owner._owned().get('writer_drain')
        if (drain is None or drain['phase'] != 'drained' or drain['receipt'] is None
                or drain['receipt']['status'] != 'managed_scopes_drained'):
            raise HandshakeError('Original backend exit wait lacks admitted drain')
        event = node.receive_frame(authority, _exit_remaining(absolute_deadline))
        _exit_remaining(absolute_deadline)
        frame = node.frame_value(authority, event)
        _exit_remaining(absolute_deadline)
        if frame.get('kind') == 'main_backend_exit': return event
        request = frame.get('request')
        if (frame.get('kind') == 'main_preflight_request' and isinstance(request, dict)
                and request.get('action') == 'finish'):
            # Existing typed table/core checks permit only the original
            # enrolled finish. Reserve/bind/repair never enter this wait.
            _preflight_controller_event(owner, event)
            _exit_remaining(absolute_deadline)
            continue
        if (frame.get('kind')=='main_cpu_child_request' and isinstance(request,dict) and request.get('action')=='finish'):
            _cpu_controller_event(owner,event);_exit_remaining(absolute_deadline)
            continue
        raise HandshakeError('Original admitted backend exit event kind differs')


def observe_backend_exit(owner, event, *, absolute_deadline=None):
    from backend.engine.application_launch_handshake import validate_backend_exit
    if absolute_deadline is not None: _exit_remaining(absolute_deadline)
    authority = getattr(owner, '_node_backend_authority', None)
    if authority is None: raise HandshakeError('Original typed Node backend authority is unavailable')
    frame = node.frame_value(authority, event)
    with lease._transition_admission(owner.root, owner.nonce):
        row = owner._owned(); owner._binding(row); owner._live(row, row['process'])
        drain = row.get('writer_drain')
        if drain is None or drain['phase'] != 'drained': raise HandshakeError('Backend exit lacks admitted original drain')
        proof = owner._authenticated_backend_proof
        validate_backend_exit(frame, drain['request'], proof['process'])
    if absolute_deadline is not None: _exit_remaining(absolute_deadline)
    owner._writer_epoch.observe_authenticated_node_backend_exit(drain['writer_id'], event,
        expected_registry_sha256=owner._writer_epoch.snapshot()['registry_sha256'])
    if absolute_deadline is not None: _exit_remaining(absolute_deadline)
    owner.publish_writer_drain(phase='backend_exited', backend_exit=frame)
    if absolute_deadline is not None: _exit_remaining(absolute_deadline)
    node.assert_exit_acknowledgement(authority)
    acknowledgement = {'schema_version': 1, 'kind': 'backend_exit_observed',
        'nonce': owner.nonce, 'request_id': frame['request_id'], 'exit_sha256': update._sha(update._canonical(frame))}
    if absolute_deadline is None:
        send_frame(owner._bootstrap_channel, acknowledgement)
    else:
        _exit_remaining(absolute_deadline)
        send_frame(owner._bootstrap_channel, acknowledgement, absolute_deadline=absolute_deadline)
        _exit_remaining(absolute_deadline)


def run(args):
    root, _ = update._root(args.root)
    with store_admission(root, exclusive=True):
        binding = update._launch_binding(root, args.authority, pinned_authority_sha256=args.pinned_authority_sha256)
        _expected(binding, args)
        capability=_cpu_capability(args,root)
        owner = lease.LaunchSupervisor.reserve(root, args.authority, pinned_authority_sha256=args.pinned_authority_sha256)
        try:
            owner.enroll_backend_writer()
            mint = object(); node._CONTROLLER_MINTS.add(mint)
            try: authority = node._mint_original_controller(owner, mint)
            finally: node._CONTROLLER_MINTS.discard(mint)
            row = owner.start(bootstrap=True)
            if row['state'] == 'starting': node.capture_original_main(authority, row)
        except Exception as exc:
            # Reservation already owns a durable nonce. An interrupted spawn
            # must keep the original handles, even if its journal is ambiguous.
            try: owner.recovery('Spawn admission interrupted: '+str(exc))
            except (ValueError, OSError, psutil.Error): pass
            row = {'state': 'recovery_required', 'nonce': owner.nonce}
    _emit(_result(binding, row))
    backend = None; failed = row['state'] != 'starting'; pending_reason = None; direct_exit_recorded = False; direct_exit_started = None
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
                if select.select([owner._bootstrap_channel], [], [], 0)[0]:
                    receive_started=time.monotonic()
                    preparation=node.pending_shutdown_preparation(owner._node_backend_authority)
                    if preparation is None:
                        # Wait only for a complete bounded frame; no authority,
                        # deadline or producer admission is minted by this peek.
                        time.sleep(.01)
                        continue
                    if preparation:
                        event=node.receive_shutdown_preparation(owner._node_backend_authority,4)
                        frame={'kind':'main_shutdown_prepare'}
                    else:
                        event = node.receive_frame(owner._node_backend_authority, 4)
                        frame = node.frame_value(owner._node_backend_authority, event)
                    if preparation:
                        final=shutdown_transaction(owner,event,started=receive_started)
                        # All postchecked scope outcomes remain nonrelease. The
                        # original main alone may now quit and be reaped below.
                        failed=True
                    elif frame.get('kind') == 'main_drain_request':
                        deadline = prepare_drain(owner, frame)
                        exit_event = _receive_admitted_backend_exit(owner, deadline)
                        observe_backend_exit(owner, exit_event, absolute_deadline=deadline)
                    elif frame.get('kind') == 'main_preflight_request': _preflight_controller_event(owner, event)
                    elif frame.get('kind')=='main_cpu_child_request':_cpu_controller_event(owner,event)
                    elif frame.get('kind') == 'main_backend_exit': observe_backend_exit(owner, event)
                    else: raise HandshakeError('Private main descriptor ended or replayed after readiness')
                if not failed and owner._process.poll() is not None: owner.observe_exit(); failed = True
                elif not failed and not _same_identity(row['process'], row['process']['pid']):
                    raise HandshakeError('Authenticated main process birth or command changed')
                elif not failed and backend is not None and owner._owned().get('writer_drain', {}).get('phase') != 'backend_exited' and (not _same_identity(backend, backend['pid'])
                        or psutil.Process(backend['pid']).ppid() != row['process']['pid']):
                    raise HandshakeError('Authenticated backend ownership changed')
            except Exception as exc:
                pending_reason = 'Process-tree ownership is unresolved: '+str(exc); failed = True
        if pending_reason is not None:
            try: owner.recovery(pending_reason); pending_reason = None
            except Exception: pass
        if not direct_exit_recorded and owner._process is not None and owner._process.poll() is not None:
            try:
                if direct_exit_started is None: direct_exit_started = time.monotonic()
                owner.observe_exit(); direct_exit_recorded = True; failed = True
            except Exception: pass  # Ambiguous journal keeps original ownership.
        if direct_exit_recorded:
            try:
                # Only observe_exit's original Popen witness and durable recovery
                # permit this controller to close its own OS handles. The lease,
                # active writer epoch and unknown descendants remain blocked.
                if (getattr(owner, '_store_writer_closure', None) is not None
                        and not getattr(owner, '_store_writer_closure_attempted', False)):
                    owner._store_writer_closure_attempted = True
                    try:
                        from backend.engine.application_store_writer_closure import finish_original_controller
                        finish_original_controller(owner)
                    except (ValueError, OSError, psutil.Error) as closure_error:
                        # First refusal location only: no exception values,
                        # private paths, frames, tokens or environment output.
                        # Never rewrite a partial/unknown/terminal publication.
                        try:
                            current = owner._owned()
                            partial = any((owner.root/name/owner.nonce).exists() for name in
                                ('.application-writer-closures', '.application-writer-archives'))
                            if (current.get('protocol_version') == 4 and current['state'] == 'recovery_required'
                                    and current['reason'] == 'Process-tree exit is unverified' and not partial):
                                places = []; trace = closure_error.__traceback__
                                for _ in range(64):
                                    if trace is None: break
                                    name = Path(trace.tb_frame.f_code.co_filename).name
                                    if name in {'application_store_writer_closure.py', 'application_store_writer_archive.py'}:
                                        places.append(name.removeprefix('application_store_writer_').removesuffix('.py')+':'+str(trace.tb_lineno))
                                    trace = trace.tb_next
                                code = type(closure_error).__name__[:64]+':'+','.join(places[-3:])
                                owner.recovery(current['reason']+'; Store-writer closure refused '+code)
                        except (ValueError, OSError, psutil.Error):
                            pass  # Existing ambiguous evidence stays untouched.
                owner.close()
            except Exception:
                pass  # Busy/corrupt publication or close failure retains ownership.
            else:
                return 0 if owner._process.returncode == 0 else 2
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
        return run(args)
    except (ValueError, OSError, psutil.Error) as exc:
        # This handler is only valid before durable spawning; run contains all
        # post-spawn failures and never emits a second acknowledgement.
        _emit({'schema_version': 1, 'status': 'refused', 'error': str(exc)[:2048]}); return 2


if __name__ == '__main__': raise SystemExit(main())
