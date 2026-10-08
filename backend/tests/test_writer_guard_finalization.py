"""Original descriptor finalization under a controlled module shutdown.

No child process or application execution. Registry/lease acceptance stays
unchanged; the original guard alone owns the exact open descriptor.
"""
from types import SimpleNamespace
import errno
import os

import pytest

from backend.tests.test_application_launch_quiescence import epoch, enroll, digest


@pytest.mark.parametrize('exit_kind', ['generator_close', 'exception'])
def test_original_guard_keeps_close_callable_and_propagates_abnormal_exit(epoch, monkeypatch, exit_kind):
    q, root, owner, authority = epoch
    registration = enroll(authority)
    before = digest(authority)
    guard = q.writer_guard(root, owner.nonce, registration.writer_id,
        expected_registration_sha256=registration.registration_sha256)
    handle = guard.__enter__()
    fd, = handle.pass_fds
    original_os = os
    expected = original_os.fstat(fd)
    # The writer was admitted before shutdown cleared module globals. A normal
    # post-yield validation must not run after GeneratorExit or this exception.
    monkeypatch.setattr(q, 'os', SimpleNamespace(close=None, fstat=None))
    class OriginalFailure(RuntimeError):
        pass
    try:
        if exit_kind == 'generator_close':
            guard.gen.close()
        else:
            original = OriginalFailure('original failure remains authoritative')
            assert guard.__exit__(type(original), original, None) is False
        with pytest.raises(OSError) as failure:
            original_os.fstat(fd)
        assert failure.value.errno == errno.EBADF
    finally:
        monkeypatch.setattr(q, 'os', original_os)
        # Retain failed test custody; close only the exact still-open handle.
        try:
            current = original_os.fstat(fd)
        except OSError as error:
            assert error.errno == errno.EBADF
        else:
            assert (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)
            original_os.close(fd)
    assert digest(authority) == before


@pytest.mark.parametrize('read_kind', ['busy_then_committed', 'unknown_member',
    'wrong_nonce', 'foreign_witness', 'late_identity'])
def test_controlled_finish_reads_original_committed_lease_through_admission(tmp_path, monkeypatch, read_kind):
    from backend.tests import test_application_launch_quiescence_bridge as fixtures
    from backend.engine import application_launch_lease as lease
    (tmp_path / 'projects').mkdir()
    calls = []
    clock = [0.0]
    child_calls = []
    identity_reads = []
    class OriginalChild:
        # Explicit modeled controller witness; no OS process is created or
        # authenticated here. The original managed main is a distinct PID.
        pid = 912
        returncode = None
        _original_cpu_fixture_nonce = 'a' * 32
        _original_cpu_fixture_supervisor = {
            'pid': 912, 'created_at': 1.0, 'command_sha256': 'b' * 64}
        def terminate(self):
            child_calls.append('terminate original test handle')
        def wait(self, timeout):
            child_calls.append(('wait original test handle', timeout))
            self.returncode = 0
    child = OriginalChild()
    row = {'nonce': child._original_cpu_fixture_nonce,
        'supervisor': dict(child._original_cpu_fixture_supervisor),
        'state': 'recovery_required', 'claimed': True,
        'ready_receipt_sha256': 'c' * 64,
        'process': {'pid': 913}, 'exit_observation': {
            'direct_child_pid': 913, 'direct_child_returncode': 0,
            'process_tree_exit_verified': False}}
    if read_kind == 'wrong_nonce':
        row['nonce'] = 'd' * 32
    if read_kind == 'foreign_witness':
        row['supervisor']['created_at'] = 2.0
    def original_identity(pid):
        assert pid == child.pid == 912
        identity_reads.append(pid)
        if read_kind == 'late_identity':
            clock[0] += 5
        return dict(child._original_cpu_fixture_supervisor)
    def strict_unlocked_read(root):
        raise lease.LaunchLeaseError('Application launch ownership requires recovery: unknown lease history member')
    def admitted_read(root):
        assert root == tmp_path
        calls.append(root)
        if read_kind == 'unknown_member':
            strict_unlocked_read(root)
        if len(calls) == 1:
            raise lease.LeaseTransitionBusy('Lease transition is busy; retry after it completes')
        return row
    monkeypatch.setattr(lease, '_load', strict_unlocked_read)
    monkeypatch.setattr(lease, 'inspect_launch', admitted_read)
    monkeypatch.setattr(lease, '_identity', original_identity)
    monkeypatch.setattr(fixtures, 'wait_file', lambda *args, **kwargs: b'{}')
    monkeypatch.setattr(fixtures, 'time', SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay)))
    if read_kind == 'unknown_member':
        with pytest.raises(lease.LaunchLeaseError, match='unknown lease history member'):
            fixtures.finish_managed(child, tmp_path)
        assert child_calls == []
        assert identity_reads == []
    elif read_kind in {'wrong_nonce', 'foreign_witness', 'late_identity'}:
        with pytest.raises(AssertionError):
            fixtures.finish_managed(child, tmp_path)
        assert len(calls) == 2
        assert child_calls == []
        assert identity_reads == ([912] if read_kind == 'late_identity' else [])
        assert child.returncode is None
        assert not (tmp_path / 'projects' / 'exit.trigger').exists()
        if read_kind == 'late_identity':
            assert clock[0] >= 5  # Expiry is refused, never renewed.
    else:
        fixtures.finish_managed(child, tmp_path)
        assert len(calls) == 2
        assert child_calls == ['terminate original test handle', ('wait original test handle', 5)]
        assert identity_reads == [912]
        assert clock[0] < 5  # Original absolute observation budget was retained.
