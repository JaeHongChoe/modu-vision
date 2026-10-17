"""Original asyncio tasks must be joined within the caller's one deadline."""
import asyncio
import importlib.util
import os
from pathlib import Path
import time
import threading

import pytest

from backend.api.websocket_telemetry import TelemetryBroadcaster as Original


def broadcaster_type():
    candidate = os.environ.get('MV_TELEMETRY_CANDIDATE')
    if not candidate:
        return Original
    spec = importlib.util.spec_from_file_location('owned_telemetry_candidate', candidate)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TelemetryBroadcaster


def test_original_pair_joined_from_preparation_thread():
    async def run():
        b = broadcaster_type()()
        b.start(asyncio.get_running_loop())
        original = (b._dispatch_task, b._hw_task)
        await asyncio.sleep(0)
        result = await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
        assert all(task.done() for task in original)
        assert result['task_count'] == result['joined_tasks'] == 2
        assert result['pending_tasks'] == 0 and result['idle'] is True
        assert result['supported'] is True
        assert result['whole_writer_coverage'] is False
        assert result['process_tree_exit_verified'] is False
        assert result['can_release_launch_lease'] is False
    asyncio.run(run())


def test_restart_retains_both_original_generations_until_joined():
    async def run():
        b = broadcaster_type()()
        loop = asyncio.get_running_loop()
        b.start(loop)
        old = (b._dispatch_task, b._hw_task)
        await asyncio.sleep(0)
        b._running = False
        b.start(loop)
        current = (b._dispatch_task, b._hw_task)
        assert all(first is not second for first, second in zip(old, current))
        result = await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
        assert all(task.done() for task in (*old, *current))
        assert result['task_count'] == result['joined_tasks'] == 4
        assert result['pending_tasks'] == 0
    asyncio.run(run())


def test_expired_deadline_does_not_cancel_or_claim_original_tasks():
    async def run():
        b = broadcaster_type()()
        b.start(asyncio.get_running_loop())
        original = (b._dispatch_task, b._hw_task)
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match='deadline'):
            await asyncio.to_thread(b.prepare_shutdown, time.monotonic() - .01)
        assert all(not task.done() and task.cancelling() == 0 for task in original)
        with pytest.raises(RuntimeError):
            await b.shutdown()
        assert all(task.done() for task in original)
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
    asyncio.run(run())


def test_sync_bridge_rejects_same_loop_instead_of_deadlocking():
    async def run():
        b = broadcaster_type()()
        b.start(asyncio.get_running_loop())
        original = (b._dispatch_task, b._hw_task)
        with pytest.raises(RuntimeError, match='same event loop'):
            b.prepare_shutdown(time.monotonic() + 1)
        assert all(task.cancelling() == 0 for task in original)
        with pytest.raises(RuntimeError):
            await b.shutdown()
        assert all(task.done() for task in original)
    asyncio.run(run())


def test_substituted_task_is_not_cancelled_and_cannot_qualify():
    async def run():
        b = broadcaster_type()()
        b.start(asyncio.get_running_loop())
        original = b._dispatch_task
        foreign = asyncio.create_task(asyncio.Event().wait())
        b._dispatch_task = foreign
        with pytest.raises(RuntimeError, match='ownership'):
            await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
        assert foreign.cancelling() == 0 and original.cancelling() == 0
        b._dispatch_task = original
        with pytest.raises(RuntimeError):
            await b.shutdown()
        foreign.cancel()
        with pytest.raises(asyncio.CancelledError):
            await foreign
        assert original.done()
    asyncio.run(run())


def test_stubborn_original_task_times_out_and_late_write_never_clears_failure():
    async def run():
        b = broadcaster_type()()
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []
        prepare = b.prepare_shutdown
        async def original_dispatch():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                writes.append('original late write')
        b._event_dispatch_loop = original_dispatch
        b.start(asyncio.get_running_loop())
        original = b._dispatch_task
        await entered.wait()
        deadline = time.monotonic() + .04
        started = time.monotonic()
        with pytest.raises(RuntimeError, match='deadline|unresolved'):
            await asyncio.to_thread(prepare, deadline)
        assert time.monotonic() - started < .5
        assert not original.done()
        release.set()
        await original
        assert writes == ['original late write']
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
        with pytest.raises(RuntimeError):
            await b.shutdown()
    asyncio.run(run())


def test_unexpected_original_task_exception_is_retained_and_refused():
    async def run():
        b = broadcaster_type()()
        async def failed_original():
            raise ValueError('owned failure')
        b._event_dispatch_loop = failed_original
        b.start(asyncio.get_running_loop())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match='original task'):
            await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
        with pytest.raises(RuntimeError):
            await b.shutdown()
        assert b._hw_task is None or b._hw_task.done()
    asyncio.run(run())


def test_async_interface_remains_bounded_and_allows_clean_new_generation():
    async def run():
        b = broadcaster_type()()
        loop = asyncio.get_running_loop()
        b.start(loop)
        first = (b._dispatch_task, b._hw_task)
        await b.shutdown(time.monotonic() + 1)
        assert all(task.done() for task in first)
        b.start(loop)
        second = (b._dispatch_task, b._hw_task)
        await b.shutdown()
        assert all(task.done() for task in second)
        assert not b._running
    asyncio.run(run())


def test_stopped_original_loop_remains_unqualified():
    loop = asyncio.new_event_loop()
    started = threading.Event()
    def serve():
        asyncio.set_event_loop(loop)
        started.set()
        loop.run_forever()
    owner = threading.Thread(target=serve)
    owner.start()
    started.wait(1)
    b = broadcaster_type()()
    async def initialize():
        b.start(loop)
        return b._dispatch_task, b._hw_task
    originals = asyncio.run_coroutine_threadsafe(initialize(), loop).result(timeout=1)
    loop.call_soon_threadsafe(loop.stop)
    owner.join(timeout=1)
    assert not owner.is_alive()
    try:
        with pytest.raises(RuntimeError, match='loop is stopped'):
            b.prepare_shutdown(time.monotonic() + 1)
    finally:
        with pytest.raises(RuntimeError):
            loop.run_until_complete(b.shutdown())
        assert all(task.done() for task in originals)
        loop.close()


def test_connection_close_uses_same_deadline_and_late_completion_is_not_admission():
    async def run():
        b = broadcaster_type()()
        b.start(asyncio.get_running_loop())
        release, entered = asyncio.Event(), asyncio.Event()
        class OriginalConnection:
            async def close(self, **kwargs):
                assert kwargs == {'code': 1001, 'reason': 'Server shutting down'}
                entered.set()
                await release.wait()
        ws = OriginalConnection()
        b._active_connections.add(ws)
        with pytest.raises(RuntimeError, match='deadline|unresolved'):
            await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + .04)
        assert entered.is_set() and ws in b._active_connections
        closes = [record['task'] for record in b._task_lifetimes if record['role'] == 'connection_close']
        assert len(closes) == 1 and not closes[0].done()
        release.set()
        await closes[0]
        with pytest.raises(RuntimeError):
            await b.shutdown()
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
    asyncio.run(run())


def test_partial_pair_creation_retains_first_actual_task(monkeypatch):
    async def run():
        b = broadcaster_type()()
        loop = asyncio.get_running_loop()
        create = loop.create_task
        count = 0
        def fail_second(coroutine, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError('Owned second-task creation failure')
            return create(coroutine, **kwargs)
        with monkeypatch.context() as local:
            local.setattr(loop, 'create_task', fail_second)
            with pytest.raises(RuntimeError, match='creation was incomplete'):
                b.start(loop)
        first = b._dispatch_task
        assert isinstance(first, asyncio.Task)
        with pytest.raises(RuntimeError):
            await b.shutdown()
        assert first.done()
        assert b._task_lifetimes[0]['task'] is first
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
    asyncio.run(run())


def test_successful_preparation_keeps_startup_admission_closed():
    async def run():
        b = broadcaster_type()()
        loop = asyncio.get_running_loop()
        b.start(loop)
        originals = (b._dispatch_task, b._hw_task)
        receipt = await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
        assert receipt['idle'] is True
        with pytest.raises(RuntimeError, match='admission is closed'):
            b.start(loop)
        assert len(b._task_lifetimes) == 2
        assert all(task.done() for task in originals)
    asyncio.run(run())


def test_async_shutdown_does_not_wait_past_deadline_for_stubborn_original():
    async def run():
        b = broadcaster_type()()
        entered, release = asyncio.Event(), asyncio.Event()
        async def stubborn():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
        b._event_dispatch_loop = stubborn
        b.start(asyncio.get_running_loop())
        await entered.wait()
        original = b._dispatch_task
        began = time.monotonic()
        with pytest.raises(RuntimeError, match='deadline|unresolved'):
            await b.shutdown(began + .04)
        assert time.monotonic() - began < .5
        assert not original.done()
        release.set()
        await original
        for observer in b._shutdown_observers:
            future = observer['future']
            if isinstance(future, asyncio.Task):
                try:
                    await future
                except RuntimeError:
                    pass
        await asyncio.sleep(0)
        assert all(observer['terminal_observed'] for observer in b._shutdown_observers)
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
    asyncio.run(run())


def test_held_lifetime_lock_is_bounded_without_cancel_or_failure_lock_reentry():
    async def run():
        b = broadcaster_type()()
        b.start(asyncio.get_running_loop())
        originals = (b._dispatch_task, b._hw_task)
        held, release = threading.Event(), threading.Event()
        def holder():
            with b._lifetime_lock:
                held.set()
                release.wait(2)
        owner = threading.Thread(target=holder)
        owner.start()
        assert held.wait(1)
        began = time.monotonic()
        try:
            with pytest.raises(RuntimeError, match='lock.*deadline'):
                await asyncio.to_thread(b.prepare_shutdown, began + .04)
            assert time.monotonic() - began < .5
            assert all(task.cancelling() == 0 for task in originals)
            assert b._shutdown_failure is not None
        finally:
            release.set()
            owner.join(timeout=1)
        assert not owner.is_alive()
        with pytest.raises(RuntimeError):
            await b.shutdown()
        assert all(task.done() for task in originals)
    asyncio.run(run())


def test_closure_lock_reacquisition_uses_original_deadline(monkeypatch):
    async def run():
        b = broadcaster_type()()
        b.start(asyncio.get_running_loop())
        originals = (b._dispatch_task, b._hw_task)
        held, release = threading.Event(), threading.Event()
        receipt = b._closure_receipt
        owner = None
        def closure(deadline):
            nonlocal owner
            def holder():
                with b._lifetime_lock:
                    held.set()
                    release.wait(2)
            owner = threading.Thread(target=holder)
            owner.start()
            assert held.wait(1)
            return receipt(deadline)
        monkeypatch.setattr(b, '_closure_receipt', closure)
        began = time.monotonic()
        try:
            with pytest.raises(RuntimeError, match='lock.*deadline'):
                await asyncio.to_thread(b.prepare_shutdown, began + .08)
            assert time.monotonic() - began < .5
            assert all(task.done() for task in originals)
            assert b._dispatch_task is originals[0] and b._hw_task is originals[1]
        finally:
            release.set()
            owner.join(timeout=1)
            monkeypatch.setattr(b, '_closure_receipt', receipt)
        with pytest.raises(RuntimeError):
            await b.shutdown()
    asyncio.run(run())


def test_original_observer_substitution_cannot_qualify_later_receipt():
    async def run():
        b = broadcaster_type()()
        b.start(asyncio.get_running_loop())
        await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
        observer = b._shutdown_observers[0]
        original = observer['task']
        assert original.done()
        foreign = asyncio.create_task(asyncio.Event().wait())
        observer['task'] = foreign
        with pytest.raises(RuntimeError, match='observer ownership'):
            await asyncio.to_thread(b.prepare_shutdown, time.monotonic() + 1)
        assert foreign.cancelling() == 0
        foreign.cancel()
        with pytest.raises(asyncio.CancelledError):
            await foreign
    asyncio.run(run())
