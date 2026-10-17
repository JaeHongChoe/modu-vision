"""Captured original broadcaster and one shared startup preparation deadline."""
import asyncio
import threading
import time
import types

import pytest

from backend import main
from backend.api.websocket_telemetry import TelemetryBroadcaster
from backend.engine.application_launch_handshake import BackendWorkAdmission, HandshakeError


def test_original_telemetry_callback_closes_actual_tasks_without_whole_authority(monkeypatch):
    async def run():
        original = TelemetryBroadcaster()
        monkeypatch.setattr(main, 'broadcaster', original)
        callback = main._capture_telemetry_startup_join(original)
        admission = BackendWorkAdmission()
        admission.uncovered('startup_background_protocols')
        admission.register_startup_join('telemetry_broadcaster', callback)
        original.start(asyncio.get_running_loop())
        tasks = (original._dispatch_task, original._hw_task)
        receipts = await asyncio.to_thread(admission.prepare_startup_threads, time.monotonic() + 1)
        assert all(task.done() for task in tasks)
        assert receipts[0]['idle'] is True and receipts[0]['pending_tasks'] == 0
        assert receipts[0]['whole_writer_coverage'] is False
        assert receipts[0]['process_tree_exit_verified'] is False
        assert receipts[0]['can_release_launch_lease'] is False
        assert admission.snapshot()['unsupported'] == ['startup_background_protocols']
        await original.shutdown()
    asyncio.run(run())


def test_replaced_original_broadcaster_is_refused_before_either_owner_is_stopped(monkeypatch):
    async def run():
        original, foreign = TelemetryBroadcaster(), TelemetryBroadcaster()
        monkeypatch.setattr(main, 'broadcaster', original)
        callback = main._capture_telemetry_startup_join(original)
        original.start(asyncio.get_running_loop())
        tasks = (original._dispatch_task, original._hw_task)
        monkeypatch.setattr(main, 'broadcaster', foreign)
        with pytest.raises(HandshakeError, match='broadcaster.*changed'):
            await asyncio.to_thread(callback, time.monotonic() + 1)
        assert all(not task.done() and task.cancelling() == 0 for task in tasks)
        assert foreign._task_lifetimes == []
        await original.shutdown()
    asyncio.run(run())


def test_replaced_bound_prepare_method_cannot_substitute_original_callback(monkeypatch):
    async def run():
        original = TelemetryBroadcaster()
        monkeypatch.setattr(main, 'broadcaster', original)
        callback = main._capture_telemetry_startup_join(original)
        original.start(asyncio.get_running_loop())
        tasks = (original._dispatch_task, original._hw_task)
        original.prepare_shutdown = lambda deadline: {'idle': True}
        with pytest.raises(HandshakeError, match='broadcaster.*changed'):
            await asyncio.to_thread(callback, time.monotonic() + 1)
        assert all(task.cancelling() == 0 for task in tasks)
        await original.shutdown()
    asyncio.run(run())


def test_broadcaster_replacement_during_original_join_cannot_qualify_receipt(monkeypatch):
    async def run():
        original, foreign = TelemetryBroadcaster(), TelemetryBroadcaster()
        actual = original.prepare_shutdown
        def prepare(owner, deadline):
            receipt = actual(deadline)
            monkeypatch.setattr(main, 'broadcaster', foreign)
            return receipt
        original.prepare_shutdown = types.MethodType(prepare, original)
        monkeypatch.setattr(main, 'broadcaster', original)
        callback = main._capture_telemetry_startup_join(original)
        original.start(asyncio.get_running_loop())
        tasks = (original._dispatch_task, original._hw_task)
        with pytest.raises(HandshakeError, match='broadcaster.*changed'):
            await asyncio.to_thread(callback, time.monotonic() + 1)
        assert all(task.done() for task in tasks)
        assert foreign._task_lifetimes == []
        await original.shutdown()
    asyncio.run(run())


def test_three_original_roles_share_exact_absolute_deadline(monkeypatch):
    async def run():
        original = TelemetryBroadcaster()
        actual = original.prepare_shutdown
        seen = []
        def prepare(owner, deadline):
            seen.append(('telemetry_broadcaster', deadline))
            return actual(deadline)
        original.prepare_shutdown = types.MethodType(prepare, original)
        monkeypatch.setattr(main, 'broadcaster', original)
        admission = BackendWorkAdmission()
        admission.uncovered('startup_background_protocols')
        admission.register_startup_join('telemetry_broadcaster', main._capture_telemetry_startup_join(original))
        from backend.engine.startup_thread_lifetime import OriginalStartupThread
        scopes = []
        for role in ('job_ledger_maintenance', 'preflight_run_sweep'):
            owner = OriginalStartupThread(role, lambda stop: stop.wait(1))
            owner.start()
            scopes.append(owner)
            def join(deadline, owner=owner, role=role):
                seen.append((role, deadline))
                return owner.stop_and_join(deadline, expected_thread=owner.thread)
            admission.register_startup_join(role, join)
        original.start(asyncio.get_running_loop())
        deadline = time.monotonic() + 1
        try:
            receipts = await asyncio.to_thread(admission.prepare_startup_threads, deadline)
            assert seen == [(role, deadline) for role in ('telemetry_broadcaster', 'job_ledger_maintenance', 'preflight_run_sweep')]
            assert len(receipts) == 3 and all(not owner.thread.is_alive() for owner in scopes)
            assert admission.snapshot()['unsupported'] == ['startup_background_protocols']
        finally:
            for owner in scopes:
                owner.stop_event.set()
                owner.thread.join(1)
            await original.shutdown()
    asyncio.run(run())


def held_condition(admission):
    held, release = threading.Event(), threading.Event()
    def holder():
        with admission._condition:
            held.set()
            release.wait(2)
    owner = threading.Thread(target=holder)
    owner.start()
    assert held.wait(1)
    return owner, release


def test_initial_startup_condition_acquisition_is_within_original_deadline():
    admission = BackendWorkAdmission()
    seen = []
    admission.register_startup_join('preflight_run_sweep', lambda deadline: seen.append(deadline))
    owner, release = held_condition(admission)
    began = time.monotonic()
    try:
        with pytest.raises(HandshakeError, match='condition|deadline'):
            admission.prepare_startup_threads(began + .04)
        assert time.monotonic() - began < .5
        assert seen == []
    finally:
        release.set()
        owner.join(1)


def test_callback_condition_reacquisition_is_within_same_original_deadline():
    admission = BackendWorkAdmission()
    holder = []
    seen = []
    def join(deadline):
        seen.append(deadline)
        holder.append(held_condition(admission))
        return {'original_thread_joined': True}
    admission.register_startup_join('preflight_run_sweep', join)
    began = time.monotonic()
    deadline = began + .08
    try:
        with pytest.raises(HandshakeError, match='condition|deadline|late'):
            admission.prepare_startup_threads(deadline)
        assert time.monotonic() - began < .5
        assert seen == [deadline]
    finally:
        owner, release = holder[0]
        release.set()
        owner.join(1)


def test_original_condition_replacement_during_callback_is_refused():
    admission = BackendWorkAdmission()
    original = admission._condition
    def join(deadline):
        admission._condition = threading.Condition()
        return {'original_thread_joined': True}
    admission.register_startup_join('preflight_run_sweep', join)
    with pytest.raises(HandshakeError, match='condition.*changed'):
        admission.prepare_startup_threads(time.monotonic() + 1)
    assert admission._condition is not original
