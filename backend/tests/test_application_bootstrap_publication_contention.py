"""Real original Node ACK plus a real lease mutex; no native/inference credit.

The release test catches one-shot bootstrap publication refusing an unrelated
observer's brief mutex ownership. The expiry test catches deadline renewal;
the damaged-proof test catches retrying with a changed accepted proof.
"""
import copy
import json
import threading
import time
from argparse import Namespace
from contextlib import ExitStack, contextmanager

import pytest

from backend.engine import application_launch_controller as controller
from backend.engine import application_launch_lease as lease
from backend.engine import application_launch_quiescence as quiescence
from backend.engine import application_node_writer_authority as node
from backend.tests.test_application_launch_controller import controller_arguments
from backend.tests.test_application_launch_lease import controlled_canary_publication
from backend.tests.test_application_node_writer_authority import source_stack


class StopControlledRun(BaseException):
    pass


def controlled_publication(tmp_path, monkeypatch, mode):
    root, value, current = source_stack(tmp_path)
    arguments = controller_arguments(root, value, current)
    args = Namespace(**{arguments[i][2:].replace('-', '_'): arguments[i + 1]
                       for i in range(0, len(arguments), 2)}, inspect=False)
    captured = {'sent': [], 'admissions': 0, 'publication_busy_entries': 0}
    entered = threading.Event(); release = threading.Event(); holder_errors = []
    holder = None
    controller_thread = threading.get_ident()
    publication_started = False
    authenticate = controller.authenticate
    seal_admission = node.seal_registration_admission
    publish = lease.LaunchSupervisor._publish_bootstrap
    recover = lease.LaunchSupervisor.recovery
    send = controller.send_frame
    transition = lease._transition_admission

    @contextmanager
    def observe_real_entry(root, nonce=None):
        # Delegate real acquisition and observe only its __enter__ refusal.
        with ExitStack() as stack:
            try:
                stack.enter_context(transition(root, nonce))
            except lease.LeaseTransitionBusy:
                if publication_started and threading.get_ident() == controller_thread:
                    captured['publication_busy_entries'] += 1
                    if mode == 'release':
                        # Release only after a real busy entry was observed:
                        # old one-shot code cannot escape by scheduling luck.
                        release.set()
                raise
            yield

    def original_send(channel, frame, **kwargs):
        captured['sent'].append(copy.deepcopy(frame))
        return send(channel, frame, **kwargs)

    def admitted(authority, event, registered):
        nonlocal holder
        seal_admission(authority, event, registered)
        captured['admissions'] += 1
        owner = captured['owner']
        captured['accepted_proof'] = copy.deepcopy(owner._authenticated_backend_proof)
        captured['accepted_registry'] = owner._writer_epoch.snapshot()
        def hold_original_mutex():
            try:
                with lease._transition_admission(root, owner.nonce):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('Controlled holder release was not bounded')
            except BaseException as error:
                holder_errors.append(error); entered.set()
        holder = threading.Thread(target=hold_original_mutex)
        holder.start()
        assert entered.wait(2) and not holder_errors

    def publication(owner, receipt, **kwargs):
        nonlocal publication_started
        publication_started = True
        captured['publication_receipt'] = copy.deepcopy(receipt)
        captured['original_deadline'] = kwargs.get('absolute_deadline')
        if mode == 'expire':
            # A shorter test bound cannot renew the original production bound.
            bound = time.monotonic() + .075
            assert kwargs['absolute_deadline'] > bound
            kwargs['absolute_deadline'] = bound
            captured['short_deadline'] = bound
        elif mode == 'wrong_proof':
            owner._authenticated_backend_proof = {
                **owner._authenticated_backend_proof, 'epoch': '0' * 32}
        return publish(owner, receipt, **kwargs)

    def original_authentication(owner, row):
        captured['owner'] = owner
        try:
            captured['backend'] = authenticate(owner, row)
        except Exception as error:
            captured['error'] = error
            raise
        captured['ready'] = owner._owned()
        raise StopControlledRun()

    def retain_recovery(owner, reason):
        release.set()
        if holder is not None:
            holder.join(timeout=2)
            assert not holder.is_alive()
        result = recover(owner, reason)
        captured['recovery'] = result
        raise StopControlledRun()

    try:
        with monkeypatch.context() as patch:
            patch.setattr(controller, 'authenticate', original_authentication)
            patch.setattr(controller, '_emit', lambda value: None)
            patch.setattr(controller, 'send_frame', original_send)
            patch.setattr(node, 'seal_registration_admission', admitted)
            patch.setattr(lease.LaunchSupervisor, '_publish_bootstrap', publication)
            patch.setattr(lease.LaunchSupervisor, 'recovery', retain_recovery)
            patch.setattr(lease, '_transition_admission', observe_real_entry)
            with pytest.raises(StopControlledRun):
                controller.run(args)
        release.set()
        if holder is not None:
            holder.join(timeout=2); assert not holder.is_alive()
        assert not holder_errors
        owner = captured['owner']
        captured['final'] = lease.inspect_launch(root)
        captured['final_registry'] = owner._writer_epoch.snapshot()
        path = root / lease.LEASES / owner.nonce / 'bootstrap-receipt.json'
        captured['bootstrap_exists'] = path.exists()
        if path.exists():
            captured['bootstrap'] = json.loads(path.read_bytes())
        captured['proof_after'] = copy.deepcopy(owner._authenticated_backend_proof)
        captured['quiescent'] = None
        try:
            lease.assert_quiescent(root)
        except ValueError as error:
            captured['quiescent'] = str(error)
        return captured
    finally:
        release.set()
        if holder is not None:
            holder.join(timeout=2)
            assert not holder.is_alive(), 'Controlled transition holder remains unresolved'
        owner = captured.get('owner')
        if owner is not None:
            # Only the fixture's cooperative markers; no process signals.
            (root / 'projects' / 'backend-exit').write_text('cooperative original fixture')
            (root / 'projects' / 'main-exit').write_text('cooperative original fixture')
            owner._process.wait(timeout=5)
            owner.close()
            observed = json.loads((root / 'projects' / 'original-backend-exit.json').read_bytes())
            assert observed['code'] == 0 and observed['signal'] is None


def test_original_accepted_ack_publishes_after_brief_real_mutex_contention(tmp_path, monkeypatch):
    result = controlled_publication(tmp_path, monkeypatch, 'release')
    assert 'error' not in result, repr(result.get('error'))
    assert result['final']['state'] == 'ready'
    assert result['bootstrap_exists'] and result['bootstrap'] == result['publication_receipt']
    assert result['original_deadline'] is not None
    assert result['proof_after'] == result['accepted_proof']
    assert result['final_registry'] == result['accepted_registry']
    assert result['admissions'] == 1
    assert result['publication_busy_entries'] >= 1
    assert [row['kind'] for row in result['sent']].count('backend_registered') == 1
    assert [row['kind'] for row in result['sent']].count('main_admitted') == 1
    assert len(result['final_registry']['registry']['writers']) == 1
    assert result['final_registry']['registry']['writers'][0]['status'] == 'active'
    assert result['quiescent'] is not None


def test_expired_original_publication_bound_retains_recovery_and_active_writer(tmp_path, monkeypatch):
    result = controlled_publication(tmp_path, monkeypatch, 'expire')
    assert 'Original transition entry deadline expired' in str(result['error'])
    assert result['final']['state'] == 'recovery_required' and not result['bootstrap_exists']
    assert result['proof_after'] == result['accepted_proof']
    assert result['final_registry'] == result['accepted_registry']
    assert result['final']['writer_drain']['phase'] == 'enrolled'
    assert result['quiescent'] is not None
    assert result['admissions'] == 1
    assert result['publication_busy_entries'] >= 1


def test_changed_accepted_proof_is_refused_before_mutex_retry(tmp_path, monkeypatch):
    result = controlled_publication(tmp_path, monkeypatch, 'wrong_proof')
    assert 'Original authenticated bootstrap publication differs' in str(result['error'])
    assert result['final']['state'] == 'recovery_required' and not result['bootstrap_exists']
    assert result['final_registry'] == result['accepted_registry']
    assert result['quiescent'] is not None
    assert result['admissions'] == 1
    assert result['publication_busy_entries'] == 0
