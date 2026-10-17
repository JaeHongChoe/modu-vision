"""Tests first: original real ASGI/UI reads with bounded settled summaries.

No implementation or runtime/native acceptance. Default caps stay1024/256 and
ancillary live/recent attempt cap128. Only original successful completed scopes
can roll into a monotonic count/digest; pending/failed/replayed scopes cannot.
The barrier profiles one exact original handler return without replacing any
handler/engine/DB/result, then always releases and awaits the real request.
"""
import asyncio
import hashlib
import re
import threading
import time

import httpx
import pytest


_FLAGS = ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease')


def _assert_data_only(observer):
    assert all(observer.snapshot()[key] is False for key in _FLAGS)


def _summary(observer):
    value = observer.snapshot()['settled_history']
    assert set(value) == {'requests', 'events', 'sha256'}
    assert type(value['requests']) is int and value['requests'] > 0
    assert type(value['events']) is int and value['events'] > 0
    assert type(value['sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['sha256'])
    assert value['sha256'] != hashlib.sha256(b'').hexdigest()
    return value


def _healthy(admission, observer):
    assert admission.snapshot() == {'active_scopes': 0, 'unsupported': []}
    snap = observer.snapshot()
    assert not snap['uncertain']
    assert observer._max_events == 1024 and observer._max_requests == 256
    assert len(observer._events) <= 1024 and len(observer._records) <= 256
    assert len(admission._UI_ancillary_requests.records) <= 128
    _assert_data_only(observer)


async def _selection(client, headers):
    response = await client.get('/api/compute/selection', headers=headers)
    assert response.status_code == 200
    assert set(response.json()) == {'compute_profile_id'}
    value = response.json()['compute_profile_id']
    assert value is None or type(value) is str
    return response.json()


def _stack(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _stack as original
    monkeypatch.chdir(tmp_path)
    return original(tmp_path, monkeypatch)


def test_more_than128_original_reads_and1024_events_roll_only_settled_history(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current
    app, admission, observer, _, project, _, _, _, audit = _stack(tmp_path, monkeypatch)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            baseline = None
            for index in range(300):
                value = await _selection(client, headers)
                if baseline is None: baseline = value
                assert value == baseline
                _healthy(admission, observer)
            summary = _summary(observer)
            snap = observer.snapshot()
            assert snap['request_count'] > 256 and snap['sequence'] > 1024
            assert summary['requests'] + len(snap['records']) == snap['request_count']
            assert len(observer._records) + 1 <= observer._max_requests
            assert len(observer._events) + 1 <= observer._max_events
            copy = observer.snapshot()['settled_history']
            copy['requests'] = -1
            assert _summary(observer) == summary
            await _selection(client, headers)
            later = _summary(observer)
            assert later['requests'] >= summary['requests'] and later['events'] >= summary['events']
    asyncio.run(scenario())
    assert audit.verify() == {'intact': True, 'rows': 0}
    admission.close()
    assert admission.drain(.03) == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    assert observer.snapshot()['all_settled']
    _assert_data_only(observer)


async def _join_original_request(task, release):
    release.set()
    done, _ = await asyncio.wait((task,), timeout=10)
    if task not in done:
        # Never cancel or restore the profiler while the owned original worker
        # remains active. Join the real request, then report exceeded join cap.
        await task
        raise AssertionError('Original request did not join within10 seconds after barrier release')
    return await task


def test_pending_original_handler_is_not_archived_while_other_reads_roll(tmp_path, monkeypatch):
    from backend.api import routes_compute
    from backend.tests.test_original_owned_flow_lifetime import _login_current
    app, admission, observer, _, project, _, _, _, audit = _stack(tmp_path, monkeypatch)
    original = routes_compute.get_selection
    assert admission._UI_ancillary_requests.handlers['get_selection'][1] is original
    exact_code = original.__code__
    armed = threading.Event(); begun = threading.Event(); release = threading.Event()
    guard = threading.Lock(); claimed = []; timed_out = []
    previous = threading.getprofile()

    def profile(frame, event, arg):
        if event != 'return' or frame.f_code is not exact_code or not armed.is_set(): return
        with guard:
            if claimed: return
            claimed.append(threading.current_thread())
        begun.set()
        if not release.wait(120): timed_out.append(True)

    threading.setprofile(profile)
    async def scenario():
        task = None
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
                headers = await _login_current(client, app, project)
                armed.set()
                task = asyncio.create_task(_selection(client, headers))
                deadline = time.monotonic() + 10
                while not begun.is_set() and time.monotonic() < deadline:
                    await asyncio.sleep(.001)
                assert begun.is_set(), 'Exact original handler did not reach its own return barrier'
                pending = list(admission._UI_ancillary_requests.pending.values())
                assert len(pending) == 1
                held = pending[0]; carrier = held.activity; token = carrier.token
                assert token in observer._records and not held.settled
                for _ in range(200):
                    await _selection(client, headers)
                    assert admission.snapshot()['unsupported'] == []
                    assert not observer.snapshot()['uncertain']
                    assert admission._UI_ancillary_requests.pending.get(token) is held
                    assert observer._records[token]['carrier'] is carrier
                    assert not observer._records[token]['released']
                    assert not observer.snapshot()['all_settled']
                    assert len(observer._events) <= 1024 and len(observer._records) <= 256
                assert _summary(observer)['requests'] > 0
                assert admission.snapshot()['active_scopes'] >= 2
                await _join_original_request(task, release); task = None
                _healthy(admission, observer)
        finally:
            release.set()
            if task is not None: await _join_original_request(task, release)
    try: asyncio.run(scenario())
    finally: threading.setprofile(previous)
    assert claimed and timed_out == []
    assert audit.verify() == {'intact': True, 'rows': 0}
    admission.close()
    assert admission.drain(.03)['status'] == 'managed_scopes_drained'
    assert observer.snapshot()['all_settled']
    _assert_data_only(observer)


def test_old_evicted_carrier_and_attempt_cannot_replay_or_regain_pending_identity(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            await _selection(client, headers)
            old_attempt = admission._UI_ancillary_requests.records[-1]
            old = old_attempt.activity; token = old.token
            assert old_attempt.settled and token in observer._records
            old_entered=observer._records[token]['entered_sequence']
            original_enter=next(event for event in observer._events
                if event['sequence']==old_entered and event['action']=='enter')
            assert observer._event_owners[id(original_enter)] is old
            for _ in range(200):
                await _selection(client, headers); _healthy(admission, observer)
            assert token not in observer._records
            assert not any(event is original_enter for event in observer._events)
            assert id(original_enter) not in observer._event_owners
            assert old_attempt not in admission._UI_ancillary_requests.records
            assert old.token is token and old_attempt.settled
            assert not old_attempt.base()
            assert observer._row(old) is None
            assert observer.snapshot()['uncertain']
            assert not observer.snapshot()['all_settled']
            assert not admission._UI_ancillary_requests.pending
            _assert_data_only(observer)
    asyncio.run(scenario())
    admission.close()
    assert not observer.snapshot()['all_settled']


def test_uncovered_reason_stays_sticky_across_pressure_and_no_summary_erases_it(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            for _ in range(200):
                await _selection(client, headers); _healthy(admission, observer)
            response = await client.get('/not-an-original-covered-source-route', headers=headers)
            assert response.status_code in (401, 404)
            assert admission.snapshot()['unsupported'] == ['uncovered_request_protocol']
            for _ in range(3):
                await _selection(client, headers)
                assert admission.snapshot()['unsupported'] == ['uncovered_request_protocol']
            _assert_data_only(observer)
    asyncio.run(scenario())
    admission.close()
    assert admission.drain(.03)['status'] == 'refused'
    assert not observer.snapshot()['all_settled']


@pytest.mark.parametrize('change', ['equal_binding_tuple', 'context_and_binding', 'ingress_scope_and_binding'])
def test_completed_history_rejects_replaced_original_binding_before_any_archive(change, tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            await _selection(client, headers)
            attempt = admission._UI_ancillary_requests.records[-1]
            carrier = attempt.activity; token = carrier.token
            assert attempt.settled and token in observer._settled_ready
            original_binding, original_context, original_ingress = carrier.binding, carrier.context, carrier.ingress
            original_path = carrier.scope['path']
            summary_before = observer._settled_history
            records_before = set(observer._records)
            try:
                fields = list(original_binding)
                if change == 'equal_binding_tuple':
                    carrier.binding = tuple(fields)
                    assert carrier.binding == original_binding and carrier.binding is not original_binding
                elif change == 'context_and_binding':
                    carrier.context = tuple(list(original_context))
                    assert carrier.context == original_context and carrier.context is not original_context
                    fields[5] = carrier.context; carrier.binding = tuple(fields)
                else:
                    carrier.scope['path'] = '/source-only-changed-completed-ingress'
                    carrier.ingress = {**original_ingress, 'path': carrier.scope['path']}
                    fields[6] = tuple(carrier.ingress.items()); carrier.binding = tuple(fields)
                with admission._condition:
                    # A hypothetical pressure calculation alone cannot fit a
                    # new full event budget plus summary. Without the exact
                    # tamper it returnsFalse WITHOUT uncertainty/eviction.
                    # This test additionally requires first structural refusal.
                    assert observer._room_for_original_event(events=observer._max_events) is False
                assert observer.snapshot()['uncertain']
                assert 'settled_history_changed' in observer.snapshot()['failures']
                assert observer._settled_history is summary_before
                assert set(observer._records) == records_before
                assert token in observer._records
                _assert_data_only(observer)
            finally:
                carrier.binding, carrier.context, carrier.ingress = original_binding, original_context, original_ingress
                carrier.scope['path'] = original_path
    asyncio.run(scenario())
    admission.close()
    assert not observer.snapshot()['all_settled']
