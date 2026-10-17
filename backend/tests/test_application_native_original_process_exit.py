"""Modeled original-process observations; no app, process signal or PID probe."""
import sys
from types import SimpleNamespace

import pytest

from backend.tests import test_application_launch_electron as native


class MissingOriginal(Exception):
    pass


class ObservationDenied(Exception):
    pass


def model(monkeypatch, *, created_at=1700000000.25, status='running',
          fresh_birth=None, command='a'*64, denied=None, missing=False):
    original = {'pid':32701, 'created_at':1700000000.25, 'command_sha256':'a'*64}
    calls = []

    class Process:
        def __init__(self, pid):
            assert pid == original['pid']
            calls.append('Process')
            if missing:
                raise MissingOriginal(pid)

        def create_time(self):
            calls.append('create_time')
            if denied == 'create_time':
                raise ObservationDenied('birth unknown')
            return created_at

        def status(self):
            calls.append('status')
            if denied == 'status':
                raise ObservationDenied('status unknown')
            return status

        def terminate(self):
            raise AssertionError('Exit observation must not send signals')

        kill = terminate
        send_signal = terminate

    def identity(pid):
        assert pid == original['pid']
        calls.append('cmdline')
        if missing:
            raise MissingOriginal(pid)
        if denied == 'cmdline':
            raise ObservationDenied('cmdline unavailable')
        return {**original, 'created_at':created_at if fresh_birth is None else fresh_birth,
                'command_sha256':command}

    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(
        Process=Process, NoSuchProcess=MissingOriginal, AccessDenied=ObservationDenied,
        STATUS_ZOMBIE='zombie'))
    monkeypatch.setattr(native.lease, '_identity', identity)
    return original, calls


def test_original_zombie_is_exited_before_unreadable_cmdline(monkeypatch):
    original, calls = model(monkeypatch, status='zombie', denied='cmdline')
    assert native._original_process_exited(original) is True
    assert calls == ['Process', 'create_time', 'status']


def test_same_pid_new_birth_proves_only_original_exit_without_cmdline(monkeypatch):
    original, calls = model(monkeypatch, created_at=1700000010.25, denied='cmdline')
    assert native._original_process_exited(original) is True
    assert calls == ['Process', 'create_time']


def test_same_birth_live_changed_command_is_unresolved_not_exited(monkeypatch):
    original, calls = model(monkeypatch, command='b'*64)
    assert native._original_process_exited(original) is False
    assert calls == ['Process', 'create_time', 'status', 'cmdline']


def test_missing_original_is_exited(monkeypatch):
    original, calls = model(monkeypatch, missing=True)
    assert native._original_process_exited(original) is True
    assert calls == ['Process']


def test_original_same_birth_alive_is_not_exited(monkeypatch):
    original, calls = model(monkeypatch)
    assert native._original_process_exited(original) is False
    assert calls == ['Process', 'create_time', 'status', 'cmdline']


@pytest.mark.parametrize('denied', ['create_time', 'status', 'cmdline'])
def test_unknown_access_denied_never_becomes_exit_authority(monkeypatch, denied):
    original, calls = model(monkeypatch, denied=denied)
    with pytest.raises(ObservationDenied):
        native._original_process_exited(original)
    assert denied in calls


def test_pid_reuse_during_live_identity_observation_is_original_exit(monkeypatch):
    original, calls = model(monkeypatch, fresh_birth=1700000010.25)
    assert native._original_process_exited(original) is True
    assert calls == ['Process', 'create_time', 'status', 'cmdline']


def race_model(monkeypatch, *, fallback='zombie', unknown_stage=None):
    """First status is running; only the subsequent cmdline observation fails."""
    original = {'pid':32701, 'created_at':1700000000.25, 'command_sha256':'a'*64}
    first_error = ObservationDenied('original cmdline denied')
    calls = []
    generation = []

    class Process:
        def __init__(self, pid):
            assert pid == original['pid']
            self.observation = len(generation)+1
            generation.append(self.observation)
            calls.append(('Process', self.observation))
            assert self.observation <= 2, 'No unbounded observation retry allowed'
            if self.observation == 2:
                if fallback == 'missing':
                    raise MissingOriginal(pid)
                if unknown_stage == 'Process':
                    raise ObservationDenied('fallback identity unknown')

        def create_time(self):
            calls.append(('create_time', self.observation))
            if self.observation == 2 and unknown_stage == 'create_time':
                raise ObservationDenied('fallback birth unknown')
            return (original['created_at']+10
                    if self.observation == 2 and fallback == 'reused'
                    else original['created_at'])

        def status(self):
            calls.append(('status', self.observation))
            if self.observation == 2 and unknown_stage == 'status':
                raise ObservationDenied('fallback status unknown')
            if self.observation == 2 and unknown_stage == 'unexpected':
                raise RuntimeError('fallback observation failed')
            return 'zombie' if self.observation == 2 and fallback == 'zombie' else 'running'

        def terminate(self):
            raise AssertionError('Exit readback must not send signals')

        kill = terminate
        send_signal = terminate

    def identity(pid):
        assert pid == original['pid']
        calls.append(('cmdline', 1))
        raise first_error

    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(
        Process=Process, NoSuchProcess=MissingOriginal, AccessDenied=ObservationDenied,
        STATUS_ZOMBIE='zombie'))
    monkeypatch.setattr(native.lease, '_identity', identity)
    return original, first_error, calls


def test_late_cmdline_denial_with_fresh_same_birth_zombie_is_original_exit(monkeypatch):
    original, first_error, calls = race_model(monkeypatch)
    assert native._original_process_exited(original) is True
    assert calls == [('Process',1),('create_time',1),('status',1),('cmdline',1),
                     ('Process',2),('create_time',2),('status',2)]


def test_late_cmdline_denial_with_fresh_missing_original_is_exit(monkeypatch):
    original, first_error, calls = race_model(monkeypatch, fallback='missing')
    assert native._original_process_exited(original) is True
    assert calls == [('Process',1),('create_time',1),('status',1),('cmdline',1),
                     ('Process',2)]


def test_late_cmdline_denial_with_fresh_reused_pid_is_original_exit_only(monkeypatch):
    original, first_error, calls = race_model(monkeypatch, fallback='reused')
    assert native._original_process_exited(original) is True
    assert calls == [('Process',1),('create_time',1),('status',1),('cmdline',1),
                     ('Process',2),('create_time',2)]


def test_late_cmdline_denial_with_same_birth_live_preserves_original_error(monkeypatch):
    original, first_error, calls = race_model(monkeypatch, fallback='live')
    with pytest.raises(ObservationDenied) as captured:
        native._original_process_exited(original)
    assert captured.value is first_error
    assert calls == [('Process',1),('create_time',1),('status',1),('cmdline',1),
                     ('Process',2),('create_time',2),('status',2)]


@pytest.mark.parametrize('unknown_stage', ['Process','create_time','status','unexpected'])
def test_late_cmdline_denial_with_unknown_readback_preserves_original_error(
        monkeypatch, unknown_stage):
    original, first_error, calls = race_model(
        monkeypatch, fallback='live', unknown_stage=unknown_stage)
    with pytest.raises(ObservationDenied) as captured:
        native._original_process_exited(original)
    assert captured.value is first_error
    assert calls.count(('Process',2)) == 1
    assert calls.count(('cmdline',1)) == 1


class ExitWaitClock:
    def __init__(self, now=100.0):
        self.now = now
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        assert 0 < duration <= .05
        self.sleeps.append(duration)
        self.now += duration


def wait_model(monkeypatch):
    clock = ExitWaitClock()
    monkeypatch.setattr(native, 'time', clock)
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(AccessDenied=ObservationDenied))
    return clock


def test_exit_wait_pending_denial_then_fresh_exit(monkeypatch):
    clock = wait_model(monkeypatch)
    original = {'pid':32701, 'created_at':1700000000.25, 'command_sha256':'a'*64}
    first_error = ObservationDenied('still running during normal exit')
    calls = []

    def observe(identity):
        assert identity is original
        calls.append(clock.now)
        if len(calls) == 1:
            raise first_error
        return True

    monkeypatch.setattr(native, '_original_process_exited', observe)
    deadline = clock.now+1
    assert native._wait_original_process_exit(original, deadline) is True
    assert calls == [100.0, 100.05]
    assert clock.sleeps == [.05] and clock.now < deadline


def test_exit_wait_persistent_denial_raises_exact_first_at_original_deadline(monkeypatch):
    clock = wait_model(monkeypatch)
    original = {'pid':32701}
    first_error = ObservationDenied('first original observation failure')
    calls = []

    def observe(identity):
        assert identity is original
        calls.append(clock.now)
        raise first_error if len(calls) == 1 else ObservationDenied('later refusal')

    monkeypatch.setattr(native, '_original_process_exited', observe)
    deadline = clock.now+.12
    with pytest.raises(ObservationDenied) as captured:
        native._wait_original_process_exit(original, deadline)
    assert captured.value is first_error
    assert clock.now == deadline and all(now < deadline for now in calls)
    assert len(calls) == 3


@pytest.mark.parametrize('command', ['a'*64, 'b'*64])
def test_exit_wait_alive_or_changed_command_never_proves_exit(monkeypatch, command):
    clock = ExitWaitClock()
    monkeypatch.setattr(native, 'time', clock)
    original, calls = model(monkeypatch, command=command)
    deadline = clock.now+.12
    with pytest.raises(AssertionError, match='exit deadline expired'):
        native._wait_original_process_exit(original, deadline)
    assert clock.now == deadline
    assert calls.count('cmdline') == 3


@pytest.mark.parametrize('error_type', [ValueError, native.lease.LeaseTransitionBusy])
def test_exit_wait_non_access_denied_error_is_immediate(monkeypatch, error_type):
    clock = wait_model(monkeypatch)
    error = error_type('non-transient ownership refusal')
    calls = []

    def observe(identity):
        calls.append(identity)
        raise error

    monkeypatch.setattr(native, '_original_process_exited', observe)
    original = {'pid':32701}
    with pytest.raises(error_type) as captured:
        native._wait_original_process_exit(original, clock.now+30)
    assert captured.value is error and calls == [original]
    assert clock.now == 100.0 and clock.sleeps == []


def test_exit_wait_expired_absolute_deadline_has_no_observation(monkeypatch):
    clock = wait_model(monkeypatch)

    def unexpected(identity):
        raise AssertionError('An expired deadline must not probe')

    monkeypatch.setattr(native, '_original_process_exited', unexpected)
    with pytest.raises(AssertionError, match='exit deadline expired'):
        native._wait_original_process_exit({'pid':32701}, clock.now)
    assert clock.now == 100.0 and clock.sleeps == []


@pytest.mark.parametrize('seconds', [30, 15])
def test_exit_wait_retains_original_main_backend_absolute_caps(monkeypatch, seconds):
    clock = wait_model(monkeypatch)
    first_error = ObservationDenied('pending until original deadline')
    calls = []

    def observe(identity):
        calls.append(clock.now)
        raise first_error

    monkeypatch.setattr(native, '_original_process_exited', observe)
    deadline = clock.now+seconds
    with pytest.raises(ObservationDenied) as captured:
        native._wait_original_process_exit({'pid':32701}, deadline)
    assert captured.value is first_error
    assert clock.now == deadline
    assert all(now < deadline for now in calls)
    assert sum(clock.sleeps) == pytest.approx(seconds)


@pytest.mark.parametrize('prior_denial', [False, True])
def test_exit_wait_positive_observation_after_original_deadline_is_refused(
        monkeypatch, prior_denial):
    clock = wait_model(monkeypatch)
    first_error = ObservationDenied('first pending observation')
    calls = []
    deadline = clock.now+.1

    def observe(identity):
        calls.append(clock.now)
        if prior_denial and len(calls) == 1:
            raise first_error
        clock.now = deadline+.01
        return True

    monkeypatch.setattr(native, '_original_process_exited', observe)
    expected = ObservationDenied if prior_denial else AssertionError
    with pytest.raises(expected) as captured:
        native._wait_original_process_exit({'pid':32701}, deadline)
    if prior_denial:
        assert captured.value is first_error
    else:
        assert 'exit deadline expired' in str(captured.value)
    assert len(calls) == (2 if prior_denial else 1)
