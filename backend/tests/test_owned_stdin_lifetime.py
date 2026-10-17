"""Original owned EOF pipe controls; no backend stdin, app or worker execution."""
import importlib
import os
import threading
import time
from types import SimpleNamespace
import pytest


def api():
    try: return importlib.import_module('backend.engine.owned_stdin_lifetime')
    except ModuleNotFoundError as error:
        if error.name == 'backend.engine.owned_stdin_lifetime':
            pytest.fail('Original stdin Thread/descriptor lifetime is missing')
        raise


@pytest.fixture
def pipe():
    read, write = os.pipe()
    yield read, write
    for fd in (read, write):
        try: os.close(fd)
        except OSError: pass


def main_scope(monkeypatch, pipe, detach=lambda: None):
    from backend import main
    server = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(main.training_job_manager, 'detach_all_for_shutdown', detach)
    owner = api()
    scope = owner.OwnedStdinLifetime(pipe[0],
        lambda fd, observed: main._stop_when_stdin_closes(server, stdin_fd=fd, on_natural_eof=observed))
    scope.start()
    return owner, scope, server


def finish_test_pipe(scope, write):
    try: os.close(write)
    except OSError: pass
    scope.thread.join(1)


def test_open_original_pipe_is_unresolved_and_never_closed_by_owner(monkeypatch, pipe):
    owner, scope, server = main_scope(monkeypatch, pipe)
    deadline = time.monotonic()+1
    try:
        with pytest.raises(owner.OriginalStdinError, match='EOF|unresolved'):
            scope.after_eof_receipt(deadline, expected_thread=scope.thread, expected_fd=pipe[0])
        assert scope.thread.is_alive() and not scope.natural_eof_observed and not server.should_exit
        assert os.fstat(pipe[0])
        os.write(pipe[1], b'still-open')
    finally: finish_test_pipe(scope, pipe[1])


def test_real_main_reader_only_receipts_natural_eof_and_completed_detach(monkeypatch, pipe):
    calls = []
    owner, scope, server = main_scope(monkeypatch, pipe, lambda: calls.append('detached'))
    deadline = time.monotonic()+1
    os.write(pipe[1], b'original supervisor data')
    os.close(pipe[1]); assert scope.eof_event.wait(1)
    receipt = scope.after_eof_receipt(deadline, expected_thread=scope.thread, expected_fd=pipe[0])
    assert calls == ['detached'] and server.should_exit and not scope.thread.is_alive()
    assert receipt == {'role':'stop_on_stdin_eof', 'natural_stdin_eof':True,
        'original_thread_joined':True, 'original_detach_completed':True,
        'whole_writer_coverage':False, 'process_tree_exit_verified':False,
        'can_release_launch_lease':False}
    assert os.fstat(pipe[0])  # the owner never closes its stream


def test_natural_eof_with_late_detach_remains_unresolved(monkeypatch, pipe):
    begun = threading.Event(); release = threading.Event()
    def detach(): begun.set(); release.wait(1)
    owner, scope, server = main_scope(monkeypatch, pipe, detach)
    os.close(pipe[1]); assert scope.eof_event.wait(1) and begun.wait(1)
    deadline = time.monotonic()+.015
    try:
        with pytest.raises(owner.OriginalStdinError, match='alive|deadline'):
            scope.after_eof_receipt(deadline, expected_thread=scope.thread, expected_fd=pipe[0])
        assert scope.thread.is_alive() and scope.natural_eof_observed and not server.should_exit
        release.set(); scope.thread.join(1)
        assert server.should_exit
        with pytest.raises(owner.OriginalStdinError, match='deadline|renew'):
            scope.after_eof_receipt(time.monotonic()+1, expected_thread=scope.thread, expected_fd=pipe[0])
    finally: release.set(); scope.thread.join(1)


def test_foreign_thread_cannot_qualify_original_eof(monkeypatch, pipe):
    owner, scope, server = main_scope(monkeypatch, pipe)
    foreign = threading.Thread(target=lambda: None); foreign.start(); foreign.join(1)
    try:
        with pytest.raises(owner.OriginalStdinError, match='handle'):
            scope.after_eof_receipt(time.monotonic()+1, expected_thread=foreign, expected_fd=pipe[0])
        assert scope.thread.is_alive() and not scope.natural_eof_observed
    finally: finish_test_pipe(scope, pipe[1])


def test_unstarted_original_thread_cannot_qualify_eof(pipe):
    owner = api(); scope = owner.OwnedStdinLifetime(pipe[0], lambda fd, eof: None)
    with pytest.raises(owner.OriginalStdinError, match='unstarted|handle'):
        scope.after_eof_receipt(time.monotonic()+1, expected_thread=scope.thread, expected_fd=pipe[0])
    assert scope.thread.ident is None and not scope.natural_eof_observed


def test_substituted_original_pipe_descriptor_is_refused_before_start(pipe):
    owner = api(); scope = owner.OwnedStdinLifetime(pipe[0], lambda fd, eof: None)
    foreign_read, foreign_write = os.pipe()
    try:
        os.dup2(foreign_read, pipe[0])
        with pytest.raises(owner.OriginalStdinError, match='descriptor|stream'):
            scope.start()
        assert scope.thread.ident is None and os.fstat(foreign_read)
    finally: os.close(foreign_read); os.close(foreign_write)


def test_stream_substitution_during_detach_cannot_qualify_original_completion(monkeypatch, pipe):
    begun = threading.Event(); release = threading.Event()
    def detach(): begun.set(); release.wait(1)
    owner, scope, server = main_scope(monkeypatch, pipe, detach)
    os.close(pipe[1]); assert scope.eof_event.wait(1) and begun.wait(1)
    foreign_read, foreign_write = os.pipe()
    try:
        os.dup2(foreign_read, pipe[0]); release.set(); scope.thread.join(1)
        with pytest.raises(owner.OriginalStdinError, match='descriptor|stream'):
            scope.after_eof_receipt(time.monotonic()+1, expected_thread=scope.thread, expected_fd=pipe[0])
        assert server.should_exit
    finally: release.set(); scope.thread.join(1); os.close(foreign_read); os.close(foreign_write)


def test_original_read_failure_still_requests_stop_but_cannot_qualify_eof(monkeypatch, pipe):
    from backend import main
    def error(fd, size): raise OSError('controlled original read error')
    monkeypatch.setattr(main.os, 'read', error)
    calls = []
    owner, scope, server = main_scope(monkeypatch, pipe, lambda: calls.append('detach'))
    scope.thread.join(1)
    assert calls == ['detach'] and server.should_exit and not scope.natural_eof_observed
    with pytest.raises(owner.OriginalStdinError, match='failed|EOF'):
        scope.after_eof_receipt(time.monotonic()+1, expected_thread=scope.thread, expected_fd=pipe[0])


def test_original_detach_failure_is_retained_and_never_becomes_exit_credit(monkeypatch, pipe):
    def fail(): raise RuntimeError('original detach failed')
    owner, scope, server = main_scope(monkeypatch, pipe, fail)
    os.close(pipe[1]); assert scope.eof_event.wait(1); scope.thread.join(1)
    assert server.should_exit and scope.natural_eof_observed
    with pytest.raises(owner.OriginalStdinError, match='failed') as held:
        scope.after_eof_receipt(time.monotonic()+1, expected_thread=scope.thread, expected_fd=pipe[0])
    assert isinstance(held.value.__cause__, RuntimeError)


def test_foreign_caller_cannot_fabricate_original_eof(pipe):
    owner = api(); scope = owner.OwnedStdinLifetime(pipe[0], lambda fd, eof: None)
    with pytest.raises(owner.OriginalStdinError, match='thread|caller'):
        scope._observed_eof()
    assert not scope.natural_eof_observed


@pytest.mark.parametrize('fd', [True, -1, None, '0'])
def test_unknown_descriptor_type_is_refused(fd):
    owner = api()
    with pytest.raises(owner.OriginalStdinError, match='descriptor'):
        owner.OwnedStdinLifetime(fd, lambda fd, eof: None)


def test_closed_original_descriptor_is_refused_before_thread_start(pipe):
    owner = api(); scope = owner.OwnedStdinLifetime(pipe[0], lambda fd, eof: None)
    os.close(pipe[0])
    with pytest.raises(owner.OriginalStdinError, match='descriptor'):
        scope.start()
    assert scope.thread.ident is None


def test_expired_original_deadline_cannot_be_replaced(pipe):
    owner = api(); scope = owner.OwnedStdinLifetime(pipe[0], lambda fd, eof: None); scope.start(); scope.thread.join(1)
    with pytest.raises(owner.OriginalStdinError, match='deadline'):
        scope.after_eof_receipt(time.monotonic()-.001, expected_thread=scope.thread, expected_fd=pipe[0])


def test_changed_original_thread_during_join_cannot_hide_late_detach(monkeypatch, pipe):
    begun = threading.Event(); release = threading.Event()
    def detach(): begun.set(); release.wait(1)
    owner, scope, server = main_scope(monkeypatch, pipe, detach)
    os.close(pipe[1]); assert scope.eof_event.wait(1) and begun.wait(1)
    original = scope.thread; errors = []; result = []; joined = threading.Event()
    original_join = original.join
    def tracked_join(seconds=None):
        joined.set(); return original_join(seconds)
    monkeypatch.setattr(original, 'join', tracked_join)
    def join():
        try: result.append(scope.after_eof_receipt(time.monotonic()+1, expected_thread=original, expected_fd=pipe[0]))
        except BaseException as error: errors.append(error)
    observer = threading.Thread(target=join); observer.start()
    try:
        assert joined.wait(1)
        foreign = threading.Thread(target=lambda: None); foreign.start(); foreign.join(1)
        scope._thread = foreign
        assert original.is_alive() and result == []
        release.set(); observer.join(1)
        assert result == [] and len(errors) == 1 and 'handle' in str(errors[0])
    finally: scope._thread = original; release.set(); original.join(1); observer.join(1)


def test_failed_owned_eof_observation_never_drops_original_stop_request(monkeypatch, pipe):
    from backend import main
    owner = api(); calls = []; server = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(main.training_job_manager, 'detach_all_for_shutdown', lambda: calls.append('detach'))
    def reader(fd, observed):
        def failed_observation():
            observed(); raise RuntimeError('original EOF observation failed')
        return main._stop_when_stdin_closes(server, stdin_fd=fd, on_natural_eof=failed_observation)
    scope = owner.OwnedStdinLifetime(pipe[0], reader); scope.start()
    os.close(pipe[1]); assert scope.eof_event.wait(1); scope.thread.join(1)
    assert calls == ['detach'] and server.should_exit
    with pytest.raises(owner.OriginalStdinError, match='failed') as held:
        scope.after_eof_receipt(time.monotonic()+1, expected_thread=scope.thread, expected_fd=pipe[0])
    assert isinstance(held.value.__cause__, RuntimeError)
