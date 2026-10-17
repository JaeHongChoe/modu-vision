"""Original frozen controller settlement under the one CPU reply deadline.

These controls use an owned fake lease entry; they never launch an application,
CPU worker, or installation. The native negative remains a separate receipt.
"""
from contextlib import contextmanager, nullcontext
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from backend.engine import application_launch_controller as controller
from backend.engine import application_launch_execution as execution
from backend.engine import application_launch_lease as lease


class BodyStopped(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _gate(monkeypatch, *, busy=0, on_busy=None, on_exit=None):
    entries = []

    @contextmanager
    def admission(root, nonce):
        entries.append((root, nonce))
        if len(entries) <= busy:
            if on_busy is not None:
                on_busy()
            raise lease.LeaseTransitionBusy('controlled entry contention')
        yield
        if on_exit is not None:
            raise on_exit

    monkeypatch.setattr(lease, '_transition_admission', admission)
    return entries


def _verify_values(tmp_path, deadline):
    request = {'nonce': 'n', 'request_id': 'r', 'workspace_id': 'w',
               'project_id': 'p', 'plan_sha256': 'a'*64, 'backend_claim_sha256': 'b'*64}
    capability = {'plan': {'kind': 'owned_cpu_ocr_known_image_plan'}}
    intent = {'request': request, 'capability': capability}
    proof = {'schema_version': 1, 'kind': 'cpu_execution_completed',
             'request': request, 'backend_proof': {'frozen': True},
             'output_path': 'unused', 'output_sha256': 'c'*64,
             'semantic_output': {}, 'worker_pid': 1, 'runtime_source_sha256': 'd'*64}
    owner = SimpleNamespace(root=tmp_path, nonce='n',
                            _cpu_reply_deadline=deadline,
                            _authenticated_backend_proof={'frozen': True})
    owner._owned = lambda: {'state': 'ready', 'process': {'pid': 1}}
    return owner, intent, proof, capability


def _verify_body(monkeypatch, capability, owner, exception):
    calls = []
    monkeypatch.setattr(execution, 'admit_plan', lambda *a, **k: capability)

    def binding(row):
        calls.append('body')
        raise exception

    owner._binding = binding
    return calls


def test_frozen_verification_retries_only_busy_entry(monkeypatch, tmp_path):
    deadline = time.monotonic()+1
    owner, intent, proof, capability = _verify_values(tmp_path, deadline)
    body = _verify_body(monkeypatch, capability, owner, BodyStopped('body once'))
    entries = _gate(monkeypatch, busy=1)
    with pytest.raises(BodyStopped, match='body once'):
        execution.verify_completion(owner, intent, proof, absolute_deadline=deadline)
    assert len(entries) == 2 and body == ['body']


def test_frozen_verification_never_retries_body_busy(monkeypatch, tmp_path):
    deadline = time.monotonic()+1
    owner, intent, proof, capability = _verify_values(tmp_path, deadline)
    body = _verify_body(monkeypatch, capability, owner, lease.LeaseTransitionBusy('body busy'))
    entries = _gate(monkeypatch)
    with pytest.raises(lease.LeaseTransitionBusy, match='body busy'):
        execution.verify_completion(owner, intent, proof, absolute_deadline=deadline)
    assert len(entries) == 1 and body == ['body']


def test_frozen_verification_stale_owner_or_expired_bound_refuses(monkeypatch, tmp_path):
    deadline = time.monotonic()+1
    owner, intent, proof, capability = _verify_values(tmp_path, deadline)
    body = _verify_body(monkeypatch, capability, owner, BodyStopped('must not enter'))
    entries = _gate(monkeypatch, busy=1, on_busy=lambda: setattr(owner, 'nonce', 'foreign'))
    with pytest.raises(execution.ExecutionError, match='changed while entering'):
        execution.verify_completion(owner, intent, proof, absolute_deadline=deadline)
    assert len(entries) == 1 and body == []
    owner.nonce = 'n'
    for invalid in (time.monotonic()-1, time.monotonic()+211):
        owner._cpu_reply_deadline = invalid
        with pytest.raises(execution.ExecutionError, match='bound differs'):
            execution.verify_completion(owner, intent, proof, absolute_deadline=invalid)
    assert len(entries) == 1 and body == []


def test_ordinary_verification_keeps_nonblocking_entry(monkeypatch, tmp_path):
    owner, intent, proof, capability = _verify_values(tmp_path, None)
    body = _verify_body(monkeypatch, capability, owner, BodyStopped('must not enter'))
    entries = _gate(monkeypatch, busy=1)
    with pytest.raises(lease.LeaseTransitionBusy):
        execution.verify_completion(owner, intent, proof)
    assert len(entries) == 1 and body == []


def test_nonfrozen_verification_cannot_opt_into_controller_retry(monkeypatch, tmp_path):
    deadline = time.monotonic()+1
    owner, intent, proof, capability = _verify_values(tmp_path, deadline)
    owner._authenticated_backend_proof['frozen'] = False
    monkeypatch.setattr(execution, 'admit_plan', lambda *a, **k: capability)
    entries = _gate(monkeypatch)
    with pytest.raises(execution.ExecutionError, match='bound differs'):
        execution.verify_completion(owner, intent, proof, absolute_deadline=deadline)
    assert entries == []


def _publisher(monkeypatch, tmp_path, deadline, *, body_error=None):
    receipt = {'nonce': 'n', 'backend_frozen': True, 'request_id': 'r'}
    row = {'state': 'ready', 'process': {'pid': 1},
           'cpu_execution': {'request_id': 'r', 'receipt_sha256': None}}
    events = []
    stored = {}
    update = SimpleNamespace(
        _canonical=_canonical,
        _sha=lambda raw: hashlib.sha256(raw).hexdigest(),
        _write=lambda path, value: (events.append('write'), stored.__setitem__(str(path), _canonical(value))),
        _read=lambda path: stored[str(path)])
    monkeypatch.setattr(lease, '_update', lambda: update)

    def recheck(*args):
        events.append('recheck')
        if body_error is not None:
            raise body_error

    monkeypatch.setattr(execution, 'recheck_receipt_artifacts', recheck)
    owner = SimpleNamespace(root=tmp_path, nonce='n',
                            _cpu_reply_deadline=deadline,
                            _authenticated_backend_proof={'frozen': True},
                            _bootstrap_channel=object())
    owner._owned = lambda: row
    owner._binding = lambda value: events.append('binding')
    owner._live = lambda value, process: events.append('live')
    owner._persist = lambda value, **changes: (events.append('persist'), changes)[1]
    return owner, receipt, events


def test_frozen_receipt_publication_retries_entry_without_republication(monkeypatch, tmp_path):
    deadline = time.monotonic()+1
    owner, receipt, events = _publisher(monkeypatch, tmp_path, deadline)
    entries = _gate(monkeypatch, busy=1)
    digest = lease.LaunchSupervisor._publish_cpu_execution(
        owner, receipt, absolute_deadline=deadline)
    assert len(entries) == 2 and len(digest) == 64
    assert events == ['binding', 'live', 'recheck', 'write', 'persist']


def test_frozen_receipt_body_busy_is_not_retried(monkeypatch, tmp_path):
    deadline = time.monotonic()+1
    error = lease.LeaseTransitionBusy('recheck body busy')
    owner, receipt, events = _publisher(monkeypatch, tmp_path, deadline, body_error=error)
    entries = _gate(monkeypatch)
    with pytest.raises(lease.LeaseTransitionBusy, match='recheck body busy'):
        lease.LaunchSupervisor._publish_cpu_execution(owner, receipt, absolute_deadline=deadline)
    assert len(entries) == 1 and events == ['binding', 'live', 'recheck']


def test_frozen_receipt_exit_failure_does_not_republish(monkeypatch, tmp_path):
    deadline = time.monotonic()+1
    owner, receipt, events = _publisher(monkeypatch, tmp_path, deadline)
    entries = _gate(monkeypatch, on_exit=lease.LeaseTransitionBusy('exit busy'))
    with pytest.raises(lease.LeaseTransitionBusy, match='exit busy'):
        lease.LaunchSupervisor._publish_cpu_execution(owner, receipt, absolute_deadline=deadline)
    assert len(entries) == 1 and events.count('write') == events.count('persist') == 1


def test_frozen_receipt_expiring_during_auth_does_not_publish(monkeypatch, tmp_path):
    clock = [time.monotonic()]
    deadline = clock[0]+1
    owner, receipt, events = _publisher(monkeypatch, tmp_path, deadline)
    monkeypatch.setattr(lease, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    def slow_recheck(*args):
        events.append('recheck')
        clock[0] = deadline+1

    monkeypatch.setattr(execution, 'recheck_receipt_artifacts', slow_recheck)
    entries = _gate(monkeypatch)
    with pytest.raises(lease.LaunchLeaseError, match='deadline expired'):
        lease.LaunchSupervisor._publish_cpu_execution(owner, receipt, absolute_deadline=deadline)
    assert len(entries) == 1 and 'write' not in events and 'persist' not in events


def test_frozen_receipt_stale_owner_or_expired_bound_cannot_publish(monkeypatch, tmp_path):
    deadline = time.monotonic()+1
    owner, receipt, events = _publisher(monkeypatch, tmp_path, deadline)
    entries = _gate(monkeypatch, busy=1, on_busy=lambda: setattr(owner, 'nonce', 'foreign'))
    with pytest.raises(lease.LaunchLeaseError, match='changed while entering'):
        lease.LaunchSupervisor._publish_cpu_execution(owner, receipt, absolute_deadline=deadline)
    assert len(entries) == 1 and events == []
    owner.nonce = 'n'
    for invalid in (time.monotonic()-1, time.monotonic()+211):
        owner._cpu_reply_deadline = invalid
        with pytest.raises(lease.LaunchLeaseError, match='bound differs'):
            lease.LaunchSupervisor._publish_cpu_execution(owner, receipt, absolute_deadline=invalid)
    assert len(entries) == 1 and events == []


def test_ordinary_receipt_publication_keeps_nonblocking_entry(monkeypatch, tmp_path):
    owner, receipt, events = _publisher(monkeypatch, tmp_path, None)
    entries = _gate(monkeypatch, busy=1)
    with pytest.raises(lease.LeaseTransitionBusy):
        lease.LaunchSupervisor._publish_cpu_execution(owner, receipt)
    assert len(entries) == 1 and events == []


def test_nonfrozen_receipt_cannot_opt_into_controller_retry(monkeypatch, tmp_path):
    deadline = time.monotonic()+1
    owner, receipt, events = _publisher(monkeypatch, tmp_path, deadline)
    receipt['backend_frozen'] = False
    entries = _gate(monkeypatch)
    with pytest.raises(lease.LaunchLeaseError, match='bound differs'):
        lease.LaunchSupervisor._publish_cpu_execution(owner, receipt, absolute_deadline=deadline)
    assert entries == [] and events == []


@pytest.mark.parametrize('frozen', [True, False])
def test_original_controller_dispatches_once_and_passes_only_frozen_deadline(monkeypatch, tmp_path, frozen):
    events = []
    monkeypatch.setattr(lease, '_transition_admission', lambda *a: nullcontext())
    monkeypatch.setattr(controller.update, '_read', lambda *a: b'{}')
    monkeypatch.setattr(controller.update, '_json', lambda raw: {'epoch': 'e', 'backend_frozen': frozen})
    monkeypatch.setattr(execution, 'request', lambda *a: {'request_id': 'r'})
    monkeypatch.setattr(execution, 'encoded_proof', lambda raw: {'backend_proof': {'frozen': frozen}})
    monkeypatch.setattr(controller, 'send_frame', lambda *a: events.append('dispatch'))
    seen = {}

    def reply(owner, deadline, kind):
        events.append('reply')
        seen['deadline'] = deadline
        assert kind == 'cpu_execution_proof'
        return {'schema_version': 1, 'kind': 'cpu_execution_proof',
                'nonce': owner.nonce, 'proof_b64': 'p'}

    monkeypatch.setattr(controller, '_controller_reply', reply)

    def verify(owner, intent, proof, **kwargs):
        events.append('verify')
        seen['verify'] = kwargs
        return {'nonce': owner.nonce, 'backend_frozen': frozen}

    monkeypatch.setattr(execution, 'verify_completion', verify)
    owner = SimpleNamespace(root=tmp_path, nonce='n', _bootstrap_channel=object(),
                            _writer_epoch=None)
    owner._owned = lambda: {'process': {'pid': 1}}
    owner._binding = lambda row: None
    owner._live = lambda row, process: None
    owner._begin_cpu_execution = lambda request, capability: {'request': request}

    def publish(receipt, **kwargs):
        events.append('publish')
        seen['publish'] = kwargs
        return 'd'*64

    owner._publish_cpu_execution = publish
    result = controller.execute_cpu(owner, {'plan': {'kind': 'owned_cpu_ocr_known_image_plan'}})
    assert result['backend_frozen'] is frozen
    assert events == ['dispatch', 'reply', 'verify', 'publish']
    if frozen:
        assert seen['verify'] == seen['publish'] == {'absolute_deadline': seen['deadline']}
        assert owner._cpu_reply_deadline is None
    else:
        assert seen['verify'] == seen['publish'] == {}
        assert not hasattr(owner, '_cpu_reply_deadline')


def test_controller_settlement_failure_does_not_redispatch(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(lease, '_transition_admission', lambda *a: nullcontext())
    monkeypatch.setattr(controller.update, '_read', lambda *a: b'{}')
    monkeypatch.setattr(controller.update, '_json', lambda raw: {'epoch': 'e', 'backend_frozen': True})
    monkeypatch.setattr(execution, 'request', lambda *a: {'request_id': 'r'})
    monkeypatch.setattr(execution, 'encoded_proof', lambda raw: {'backend_proof': {'frozen': True}})
    monkeypatch.setattr(controller, 'send_frame', lambda *a: events.append('dispatch'))
    monkeypatch.setattr(controller, '_controller_reply',
                        lambda owner, deadline, kind: {'schema_version': 1,
                            'kind': 'cpu_execution_proof', 'nonce': owner.nonce, 'proof_b64': 'p'})

    def failed_verify(owner, intent, proof, **kwargs):
        events.append('verify')
        raise lease.LeaseTransitionBusy('unresolved settlement')

    monkeypatch.setattr(execution, 'verify_completion', failed_verify)
    owner = SimpleNamespace(root=tmp_path, nonce='n', _bootstrap_channel=object(), _writer_epoch=None)
    owner._owned = lambda: {'process': {'pid': 1}}
    owner._binding = lambda row: None
    owner._live = lambda row, process: None
    owner._begin_cpu_execution = lambda request, capability: {'request': request}
    owner._publish_cpu_execution = lambda *a, **k: pytest.fail('Failed verification published')
    with pytest.raises(lease.LeaseTransitionBusy, match='unresolved settlement'):
        controller.execute_cpu(owner, {'plan': {'kind': 'owned_cpu_ocr_known_image_plan'}})
    assert events == ['dispatch', 'verify']
    assert owner._cpu_reply_deadline is None
