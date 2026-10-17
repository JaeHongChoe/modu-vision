"""Real bounded threads in private temp trees; no native/model/process execution."""
import importlib
import threading
import time

import pytest


def owner_api():
    try:
        return importlib.import_module('backend.engine.startup_thread_lifetime')
    except ModuleNotFoundError as error:
        if error.name == 'backend.engine.startup_thread_lifetime':
            pytest.fail('Original startup Thread/Event lifetime ownership is missing')
        raise


def test_original_thread_cooperatively_finishes_before_return(tmp_path):
    owner = owner_api()
    begun = threading.Event()
    writes = tmp_path/'writes.txt'
    def writer(stop):
        begun.set()
        while not stop.wait(.002):
            with writes.open('ab') as out: out.write(b'x')
        with writes.open('ab') as out: out.write(b'closed')
    scope = owner.OriginalStartupThread('preflight_run_sweep', writer)
    scope.start(); assert begun.wait(1)
    scope.stop_and_join(time.monotonic()+1, expected_thread=scope.thread)
    before = writes.read_bytes()
    time.sleep(.015)
    assert writes.read_bytes() == before and before.endswith(b'closed')
    assert not scope.thread.is_alive()


def test_original_join_refuses_unjoined_late_writer(tmp_path):
    owner = owner_api()
    release = threading.Event(); begun = threading.Event(); path=tmp_path/'late'
    def writer(stop):
        begun.set(); release.wait(1); path.write_text('late')
    scope=owner.OriginalStartupThread('preflight_run_sweep', writer)
    scope.start(); assert begun.wait(1)
    try:
        with pytest.raises(owner.StartupThreadError, match='deadline|alive'):
            scope.stop_and_join(time.monotonic()+.015, expected_thread=scope.thread)
        assert scope.thread.is_alive() and not path.exists()
    finally:
        release.set(); scope.thread.join(1)


def test_unknown_handle_is_refused_without_touching_either_stop_event():
    owner=owner_api(); begun=threading.Event()
    def loop(stop): begun.set(); stop.wait(1)
    scope=owner.OriginalStartupThread('job_ledger_maintenance', loop)
    scope.start(); assert begun.wait(1)
    foreign=threading.Thread(target=lambda:None)
    try:
        with pytest.raises(owner.StartupThreadError, match='original|handle'):
            scope.stop_and_join(time.monotonic()+1, expected_thread=foreign)
        assert scope.thread.is_alive()
    finally:
        scope.stop_and_join(time.monotonic()+1, expected_thread=scope.thread)


def test_finished_but_failed_writer_cannot_qualify_join():
    owner=owner_api()
    def failure(stop):raise RuntimeError('original write failed')
    scope=owner.OriginalStartupThread('preflight_run_sweep',failure);scope.start();scope.thread.join(1)
    with pytest.raises(owner.StartupThreadError, match='failed'):
        scope.stop_and_join(time.monotonic()+1,expected_thread=scope.thread)


def test_expired_deadline_is_not_replaced_with_a_new_join_budget():
    owner=owner_api();scope=owner.OriginalStartupThread('preflight_run_sweep',lambda stop:None)
    scope.start();scope.thread.join(1)
    with pytest.raises(owner.StartupThreadError,match='deadline'):
        scope.stop_and_join(time.monotonic()-.001,expected_thread=scope.thread)


def test_startup_joins_do_not_clear_uncovered_protocol_or_grant_whole_coverage():
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    owner=owner_api();admission=BackendWorkAdmission()
    scope=owner.OriginalStartupThread('job_ledger_maintenance',lambda stop:stop.wait(1));scope.start()
    admission.uncovered('startup_background_protocols')
    admission.register_startup_join('job_ledger_maintenance',lambda deadline:scope.stop_and_join(deadline,expected_thread=scope.thread))
    try:
        admission.prepare_startup_threads(time.monotonic()+1)
        admission.close()
        outcome=admission.drain(.02)
        assert outcome=={'status':'refused','active_scopes':0,'unsupported':['startup_background_protocols']}
        assert not scope.thread.is_alive()
    finally:
        if scope.thread.is_alive():scope.stop_and_join(time.monotonic()+1,expected_thread=scope.thread)


def test_changed_original_stop_is_refused_before_signaling():
    owner = owner_api()
    original_stop = threading.Event()
    scope = owner.OriginalStartupThread('job_ledger_maintenance', lambda stop: stop.wait(1), stop_event=original_stop)
    scope.start()
    replacement = threading.Event()
    scope._stop = replacement
    try:
        with pytest.raises(owner.StartupThreadError, match='changed|handle'):
            scope.stop_and_join(time.monotonic()+1, expected_thread=scope.thread, expected_stop=replacement)
        assert not original_stop.is_set() and not replacement.is_set()
    finally:
        scope._stop = original_stop
        original_stop.set()
        scope.thread.join(1)


def test_replaced_dead_thread_cannot_stand_in_for_original_live_thread():
    owner = owner_api()
    scope = owner.OriginalStartupThread('preflight_run_sweep', lambda stop: stop.wait(1))
    scope.start()
    original = scope.thread
    replacement = threading.Thread(target=lambda: None)
    replacement.start(); replacement.join(1)
    scope._thread = replacement
    try:
        with pytest.raises(owner.StartupThreadError, match='changed|handle'):
            scope.stop_and_join(time.monotonic()+1, expected_thread=replacement)
        assert original.is_alive() and not scope.stop_event.is_set()
    finally:
        scope._thread = original
        scope.stop_event.set(); original.join(1)


def test_join_receipt_has_no_whole_writer_or_lease_authority():
    owner = owner_api()
    scope = owner.OriginalStartupThread('preflight_run_sweep', lambda stop: None)
    scope.start(); scope.thread.join(1)
    receipt = scope.stop_and_join(time.monotonic()+1, expected_thread=scope.thread)
    assert receipt == {'role':'preflight_run_sweep', 'original_thread_joined':True,
                       'whole_writer_coverage':False, 'process_tree_exit_verified':False,
                       'can_release_launch_lease':False}


def test_repeated_startup_registration_or_preparation_is_refused():
    from backend.engine.application_launch_handshake import BackendWorkAdmission, HandshakeError
    admission = BackendWorkAdmission()
    callback = lambda deadline: {'original_thread_joined':True}
    admission.register_startup_join('preflight_run_sweep', callback)
    with pytest.raises(HandshakeError, match='repeated'):
        admission.register_startup_join('preflight_run_sweep', callback)
    admission.prepare_startup_threads(time.monotonic()+1)
    with pytest.raises(HandshakeError, match='replay'):
        admission.prepare_startup_threads(time.monotonic()+1)
    with pytest.raises(HandshakeError, match='closed'):
        admission.register_startup_join('job_ledger_maintenance', callback)


def test_changed_join_callback_does_not_qualify_preparation():
    from backend.engine.application_launch_handshake import BackendWorkAdmission, HandshakeError
    admission = BackendWorkAdmission()
    def callback(deadline):
        admission._startup_joins['preflight_run_sweep'] = lambda other: {}
        return {'original_thread_joined':True}
    admission.register_startup_join('preflight_run_sweep', callback)
    with pytest.raises(HandshakeError, match='changed'):
        admission.prepare_startup_threads(time.monotonic()+1)


def test_all_startup_joins_share_exact_absolute_deadline():
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    admission = BackendWorkAdmission(); seen = []
    for role in ('job_ledger_maintenance', 'preflight_run_sweep'):
        admission.register_startup_join(role, lambda deadline: seen.append(deadline))
    deadline = time.monotonic()+1
    admission.prepare_startup_threads(deadline)
    assert seen == [deadline, deadline]


def start_maintenance(monkeypatch, tick):
    from backend.api import routes_training as training
    manager = training.TrainingJobManager()
    monkeypatch.setattr(training, '_MAINTENANCE_SECONDS', .002)
    monkeypatch.setattr(training, '_configured_folders', lambda: {})
    monkeypatch.setattr(training, '_ledger_maintenance_tick', tick)
    training._start_ledger_maintenance(manager, object())
    return training, manager


def test_real_maintenance_tick_must_finish_before_join_receipt(monkeypatch, tmp_path):
    owner = owner_api(); begun = threading.Event(); release = threading.Event()
    path = tmp_path/'maintenance-write'
    def tick(manager, store):
        begun.set(); assert release.wait(1); path.write_text('settled')
    training, manager = start_maintenance(monkeypatch, tick)
    assert begun.wait(1)
    callback = manager.capture_startup_maintenance_join()
    result = []; errors = []; deadline = time.monotonic()+1
    def join():
        try: result.append(callback(deadline))
        except BaseException as error: errors.append(error)
    observer = threading.Thread(target=join); observer.start()
    try:
        assert manager._shutdown.wait(1)
        assert manager._maintenance.is_alive() and result == [] and not path.exists()
        release.set(); observer.join(1)
        assert errors == [] and result[0]['original_thread_joined'] is True
        assert path.read_text() == 'settled' and not manager._maintenance.is_alive()
    finally:
        release.set(); manager._shutdown.set(); manager._maintenance.join(1); observer.join(1)


def test_late_real_maintenance_tick_is_retained_without_kill(monkeypatch):
    owner = owner_api(); begun = threading.Event(); release = threading.Event()
    def tick(manager, store): begun.set(); release.wait(1)
    training, manager = start_maintenance(monkeypatch, tick)
    assert begun.wait(1)
    callback = manager.capture_startup_maintenance_join()
    try:
        with pytest.raises(owner.StartupThreadError, match='alive|deadline'):
            callback(time.monotonic()+.015)
        assert manager._maintenance.is_alive()
    finally:
        release.set(); manager._shutdown.set(); manager._maintenance.join(1)


def test_maintenance_replacement_retains_and_joins_every_original_handle(monkeypatch):
    owner = owner_api()
    training, manager = start_maintenance(monkeypatch, lambda manager, store: None)
    first = manager._maintenance
    training._start_ledger_maintenance(manager, object())
    second = manager._maintenance
    assert first is not second
    callback = manager.capture_startup_maintenance_join()
    receipt = callback(time.monotonic()+1)
    assert not first.is_alive() and not second.is_alive()
    assert receipt['original_threads_joined'] == 2


def test_changed_manager_handle_refuses_without_stopping_original(monkeypatch):
    owner = owner_api()
    training, manager = start_maintenance(monkeypatch, lambda manager, store: None)
    callback = manager.capture_startup_maintenance_join()
    original = manager._maintenance
    manager._maintenance = threading.Thread(target=lambda: None)
    try:
        with pytest.raises(owner.StartupThreadError, match='changed|handle'):
            callback(time.monotonic()+1)
        assert not manager._shutdown.is_set()
    finally:
        manager._maintenance = original
        manager._shutdown.set(); original.join(1)


def test_real_sweep_in_flight_delete_settles_before_join_and_no_next_delete(monkeypatch, tmp_path):
    import os
    from backend.engine import worker_preflight as preflight
    owner = owner_api(); begun = threading.Event(); release = threading.Event()
    runs = tmp_path/'runs'; runs.mkdir()
    for name in ('first', 'second'):
        folder = runs/name; folder.mkdir(); (folder/'only-test-data').write_text('x'); os.utime(folder,(0,0))
    removals = []
    original_remove = preflight.shutil.rmtree
    def remove(path):
        removals.append(path.name); begun.set(); assert release.wait(1); original_remove(path)
    monkeypatch.setattr(preflight.shutil, 'rmtree', remove)
    scope = owner.OriginalStartupThread('preflight_run_sweep',
        lambda stop: preflight.sweep_stale_runs(runs, now=1000, older_than=1, stop_event=stop))
    scope.start(); assert begun.wait(1)
    result = []; errors = []
    def join():
        try: result.append(scope.stop_and_join(time.monotonic()+1, expected_thread=scope.thread))
        except BaseException as error: errors.append(error)
    observer = threading.Thread(target=join); observer.start()
    try:
        assert scope.stop_event.wait(1)
        assert scope.thread.is_alive() and result == []
        release.set(); observer.join(1)
        assert errors == [] and len(result) == 1 and not scope.thread.is_alive()
        assert len(removals) == 1 and len(list(runs.iterdir())) == 1
    finally:
        release.set(); scope.stop_event.set(); scope.thread.join(1); observer.join(1)


def test_owned_lifespan_registers_sweep_before_start_and_joins_captured_handles(monkeypatch):
    import asyncio
    from fastapi import FastAPI
    from backend import main
    from backend.engine import application_launch_handshake as handshake, local_training_worker, worker_preflight
    from backend.api import routes_dataset_imports, routes_workers, routes_training
    from backend.remote import coordinator
    owner = owner_api(); admission = handshake.BackendWorkAdmission(); app = FastAPI()
    app.state.owned_work_admission = admission
    manager = routes_training.TrainingJobManager()
    monkeypatch.setattr(main, 'training_job_manager', manager)
    monkeypatch.setattr(routes_training, '_MAINTENANCE_SECONDS', .002)
    monkeypatch.setattr(routes_training, '_configured_folders', lambda: {})
    monkeypatch.setattr(routes_training, '_ledger_maintenance_tick', lambda manager, store: None)
    monkeypatch.setattr(coordinator, 'recover_remote_jobs', lambda manager: None)
    monkeypatch.setattr(local_training_worker, 'recover_local_jobs',
                        lambda manager: routes_training._start_ledger_maintenance(manager, object()))
    monkeypatch.setattr(routes_dataset_imports, 'recover_imports_at_startup', lambda app: None)
    monkeypatch.setattr(worker_preflight, 'sweep_stale_runs', lambda **kw: kw['stop_event'].wait(1))
    monkeypatch.setattr(main, 'backend_bootstrap_ready', lambda: None)
    monkeypatch.setattr(routes_workers, 'stop_for_shutdown', lambda: None)
    monkeypatch.setattr(main, 'clear_device_cache', lambda: None)
    from backend.api.websocket_telemetry import TelemetryBroadcaster
    telemetry = TelemetryBroadcaster()
    original_telemetry_start = telemetry.start
    def telemetry_start(loop):
        assert 'telemetry_broadcaster' in admission._startup_joins
        return original_telemetry_start(loop)
    monkeypatch.setattr(telemetry, 'start', telemetry_start)
    monkeypatch.setattr(main, 'broadcaster', telemetry)
    original_start = owner.OriginalStartupThread.start; captured = []
    def start(scope):
        if scope._role == 'preflight_run_sweep':
            assert 'preflight_run_sweep' in admission._startup_joins
            captured.append(scope)
        return original_start(scope)
    monkeypatch.setattr(owner.OriginalStartupThread, 'start', start)
    async def run():
        async with main._admitted_lifespan(app):
            receipts = await asyncio.to_thread(admission.prepare_startup_threads, time.monotonic()+1)
            assert len(receipts) == 3
            assert not manager._maintenance.is_alive() and not captured[0].thread.is_alive()
            admission.close()
            assert admission.drain(.02)['unsupported'] == ['startup_background_protocols']
    asyncio.run(run())


def test_missing_maintenance_is_refused_at_preparation_without_blocking_degraded_startup():
    from backend.api.routes_training import TrainingJobManager
    owner = owner_api(); manager = TrainingJobManager()
    callback = manager.capture_startup_maintenance_join()
    with pytest.raises(owner.StartupThreadError, match='unavailable'):
        callback(time.monotonic()+1)
    assert not manager._shutdown.is_set()


from backend.tests.test_shutdown_private_registry_publication import shutdown_transport


def test_original_preparation_joins_two_owned_threads_under_its_existing_deadline(shutdown_transport):
    from backend.engine import application_launch_handshake as handshake
    owner = owner_api(); admission = shutdown_transport.cache['admission']
    admission.uncovered('startup_background_protocols')
    scopes = []; seen = []
    for role in ('job_ledger_maintenance', 'preflight_run_sweep'):
        scope = owner.OriginalStartupThread(role, lambda stop: stop.wait(1)); scope.start(); scopes.append(scope)
        def join(deadline, scope=scope):
            assert shutdown_transport.full == [130.0]
            seen.append(deadline)
            return scope.stop_and_join(deadline, expected_thread=scope.thread)
        admission.register_startup_join(role, join)
    try:
        capability = handshake._prepare_backend_shutdown(shutdown_transport.cache, shutdown_transport.prep, started=100.0)
        assert capability.deadline == 130.0 and seen == [130.0,130.0]
        assert all(not scope.thread.is_alive() for scope in scopes)
        assert admission.snapshot()['unsupported'] == ['startup_background_protocols']
    finally:
        for scope in scopes:
            scope.stop_event.set(); scope.thread.join(1)


def test_maintenance_replacement_during_inflight_join_cannot_qualify_receipt(monkeypatch):
    owner = owner_api(); begun = threading.Event(); release = threading.Event()
    def tick(manager, store): begun.set(); release.wait(1)
    training, manager = start_maintenance(monkeypatch, tick)
    assert begun.wait(1)
    original = manager._maintenance
    callback = manager.capture_startup_maintenance_join()
    result = []; errors = []
    def join():
        try: result.append(callback(time.monotonic()+1))
        except BaseException as error: errors.append(error)
    observer = threading.Thread(target=join); observer.start()
    try:
        assert manager._shutdown.wait(1)
        training._start_ledger_maintenance(manager, object())
        replacement = manager._maintenance
        release.set(); observer.join(1)
        assert result == [] and len(errors) == 1
        assert isinstance(errors[0], owner.StartupThreadError) and 'changed' in str(errors[0])
        assert replacement is not original
    finally:
        release.set(); manager._shutdown.set()
        for scope in manager._maintenance_owners: scope.thread.join(1)
        observer.join(1)


def test_replaced_thread_during_join_cannot_hide_original_inflight_writer():
    owner = owner_api(); begun = threading.Event(); release = threading.Event()
    def writer(stop): begun.set(); release.wait(1)
    scope = owner.OriginalStartupThread('preflight_run_sweep', writer); scope.start()
    assert begun.wait(1)
    original = scope.thread
    result = []; errors = []
    def join():
        try: result.append(scope.stop_and_join(time.monotonic()+1, expected_thread=original))
        except BaseException as error: errors.append(error)
    observer = threading.Thread(target=join); observer.start()
    try:
        assert scope.stop_event.wait(1)
        replacement = threading.Thread(target=lambda: None); replacement.start(); replacement.join(1)
        scope._thread = replacement
        assert original.is_alive() and result == []
        release.set(); observer.join(1)
        assert result == [] and len(errors) == 1
        assert isinstance(errors[0], owner.StartupThreadError) and 'changed' in str(errors[0])
    finally:
        scope._thread = original
        release.set(); scope.stop_event.set(); original.join(1); observer.join(1)
