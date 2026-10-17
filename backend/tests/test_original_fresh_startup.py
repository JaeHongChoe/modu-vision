"""Original local source observations; never native/tree or lease authority."""
import asyncio
import importlib
import threading
import time
from pathlib import Path
import pytest


def api():
    try:return importlib.import_module('backend.engine.original_fresh_startup')
    except ModuleNotFoundError as error:
        if error.name=='backend.engine.original_fresh_startup':pytest.fail('Original fresh startup composition is missing')
        raise


def test_real_migration_acquisition_is_retained_before_yield_and_invalidated_before_release(tmp_path):
    from backend.engine import migration_guard as guard
    with guard.maintenance_guard(tmp_path):
        record=guard._admission_capability(tmp_path,exclusive=False)
        held=record.get('original_lock_observation')
        assert held is not None, 'Original successful flock handle is lost before yield'
        assert held.current(record=record)['original_acquisition_retained'] is True
        assert held.current(record=record)['kernel_lock_query_performed'] is False
    with pytest.raises(ValueError):held.current(record=record)


def test_folder_cache_requires_retained_original_acquisition_not_cached_true(tmp_path):
    from backend.api import routes_training as training
    assert training._own_data_folder(tmp_path)
    try:
        read=getattr(training,'_original_folder_lock_observation',None)
        assert read is not None, 'Folder ownership is only a cached Boolean'
        held=read(tmp_path)
        assert held is not None and held.current()['original_acquisition_retained'] is True
    finally:training._FOLDER_LOCKS.pop(str(tmp_path.resolve())).close()


def test_plain_json_and_caller_boolean_cannot_mint_startup_lifetime():
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    admission=BackendWorkAdmission()
    begin=getattr(admission,'begin_original_startup',None)
    assert begin is not None, 'Typed pending startup lifetime is missing'
    for value in ({'empty':True},True,object()):
        with pytest.raises(ValueError):begin(value)
    assert admission.snapshot()['active_scopes']==0


def test_fresh_startup_object_cannot_be_constructed_by_caller():
    with pytest.raises(ValueError):api().OriginalFreshStartupObservation()

@pytest.fixture
def live(tmp_path,monkeypatch):
    """Real owned store generation + local modeled launch cache, no launch grant."""
    import json,os,socket
    from types import SimpleNamespace
    from backend.engine import global_store_paths as paths,job_store,application_launch_handshake as hs
    from backend.api import routes_training as training
    from backend.contracts.context import ContextRegistry
    from backend.engine.shared_scheduler import ResourceLeases
    from backend import main
    root=tmp_path.resolve()/'user';root.mkdir();generation=root/'.global-generations'/('1'*32);generation.mkdir(parents=True)
    st=root.stat();installation='2'*32
    (root/paths.OWNER_FILE).write_text(json.dumps({'schema_version':1,'installation_id':installation,'scopes':paths.SUPPORTED_SCOPES,
        'root_identity':{'path':str(root),'device':st.st_dev,'inode':st.st_ino}}))
    pointer={'schema_version':1,'installation_id':installation,'generation_id':'1'*32,'fence':1,'sealed_sha256':'a'*64}
    (root/paths.POINTER_FILE).write_text(json.dumps(pointer))
    (generation/'.global-generation.json').write_text(json.dumps({**pointer}))
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    store=job_store.JobStore(generation/'jobs/ledger.sqlite3');monkeypatch.setattr(job_store,'_shared',store)
    manager=training.TrainingJobManager();manager._leases=ResourceLeases(generation/'resource_leases.sqlite3')
    (generation/'projects').mkdir()
    app=SimpleNamespace(state=SimpleNamespace(context_registry=ContextRegistry(root/'projects')))
    left,right=socket.socketpair();handle=(root/'writer-source-control.lock').open('a+b');os.fchmod(handle.fileno(),0o600)
    import fcntl
    fcntl.flock(handle.fileno(),fcntl.LOCK_SH|fcntl.LOCK_NB)
    private=os.dup(handle.fileno());info=os.fstat(handle.fileno())
    admission=hs._new_original_work_admission() if hasattr(hs,'_new_original_work_admission') else hs.BackendWorkAdmission()
    app.state.owned_work_admission=admission
    cache={'root':root,'context':hs._context(),'socket':left,'ready':False,'admission':admission,
           'proof':{'nonce':'3'*32,'epoch':'4'*32},'challenge':{'binding':{'database_pointer':pointer,'database_generation_path':str(generation)}},
           'writer_guard':object(),'writer_handle':SimpleNamespace(pass_fds=(handle.fileno(),)),
           'writer_private_fd':private,'writer_fd_identity':(info.st_dev,info.st_ino)}
    monkeypatch.setattr(hs,'_CACHE',cache)
    from backend.api.websocket_telemetry import TelemetryBroadcaster
    telemetry=TelemetryBroadcaster()
    monkeypatch.setattr(main,'broadcaster',telemetry)
    async def run(case):
        from backend.engine.migration_guard import maintenance_guard
        with maintenance_guard(root):
            observed=api()._begin_from_original_cache(cache,app,manager,telemetry)
            return await case(observed)
    value=SimpleNamespace(root=root,generation=generation,store=store,manager=manager,app=app,admission=admission,
                          cache=cache,hs=hs,telemetry=telemetry,run=run)
    yield value
    manager._shutdown.set()
    for owner in manager._maintenance_owners:owner.thread.join(1)
    for key in [str(store.path.parent.resolve())]:
        held=training._FOLDER_LOCKS.pop(key,None)
        if held is not None:held.close()
    os.close(private);handle.close();left.close();right.close()


def recover(live,observed):
    from backend.remote.coordinator import recover_remote_jobs
    from backend.engine.local_training_worker import recover_local_jobs
    from backend.api.routes_dataset_imports import recover_imports_at_startup
    from backend.engine.worker_preflight import sweep_stale_runs
    from backend.engine.startup_thread_lifetime import OriginalStartupThread
    from backend import main
    live.admission.register_startup_join('telemetry_broadcaster',main._capture_telemetry_startup_join(live.telemetry))
    live.telemetry.start(asyncio.get_running_loop())
    recover_remote_jobs(live.manager,outcome_observer=observed.recovery)
    recover_local_jobs(live.manager,outcome_observer=observed.recovery)
    result=recover_imports_at_startup(live.app,outcome_observer=observed.recovery)
    assert result=={'completed':[],'interrupted':[],'backups':{'interrupted':[],'completed':[]},'restores':{'completed':[],'interrupted':[]}}
    owner=OriginalStartupThread('preflight_run_sweep',lambda stop:sweep_stale_runs(observed.sweep.root,stop_event=stop,outcome_observer=observed.sweep))
    observed.bind_sweep(owner)
    live.admission.register_startup_join('job_ledger_maintenance',live.manager.capture_startup_maintenance_join())
    live.admission.register_startup_join('preflight_run_sweep',lambda deadline:owner.stop_and_join(deadline,expected_thread=owner.thread))
    owner.start();observed.recovery_ready()
    return owner


def finish_in_original_reader(live,observed,deadline):
    from backend.engine.migration_guard import maintenance_guard,_admission_capability
    with maintenance_guard(live.root):
        receipts=live.admission.prepare_startup_threads(deadline)
        return live.admission.finish_original_startup(observed,deadline,_admission_capability(live.root,exclusive=False),receipts)


def test_actual_eight_recoveries_real_threads_and_tasks_settle_one_local_token(live):
    async def case(observed):
        assert live.admission.snapshot()=={'active_scopes':1,'unsupported':[]}
        assert live.admission._activity_observer._sequence==0 and not live.admission._activity_observer._uncertain
        recover(live,observed)
        deadline=time.monotonic()+2
        receipt=await asyncio.to_thread(finish_in_original_reader,live,observed,deadline)
        assert receipt['supported'] and receipt['all_original_startup_local_lifetimes_settled']
        assert all(receipt[key] is False for key in ('kernel_lock_query_performed','whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))
        assert observed.recovery.snapshot()['observed_roles']==sorted(api()._OBSERVATIONS[observed]['recovery'].snapshot()['observed_roles'])
        assert len(observed.recovery.snapshot()['outcomes'])==8
        assert live.admission.snapshot()=={'active_scopes':0,'unsupported':[]}
        assert not live.admission._closed
        with pytest.raises(ValueError):live.admission.begin_original_startup(observed)
        with pytest.raises(ValueError):live.admission.finish_original_startup(observed,deadline,None,[])
    asyncio.run(live.run(case))


@pytest.mark.parametrize('field',['root','context','socket','proof','challenge','writer_guard','writer_handle','writer_private_fd','writer_fd_identity'])
def test_cache_original_binding_substitution_never_adopts_current_replacement(live,field):
    async def case(observed):
        value=live.cache[field]
        if field in ('proof','challenge'):replacement=dict(value)
        elif field in ('root','context'):replacement=Path(str(value)) if field=='root' else tuple(list(value))
        elif field in ('writer_private_fd','writer_fd_identity'):replacement=-1
        else:replacement=object()
        live.cache[field]=replacement
        try:
            with pytest.raises(ValueError):observed._fresh(time.monotonic()+.2)
            assert live.admission.snapshot()['active_scopes']==1
        finally:live.cache[field]=value
    asyncio.run(live.run(case))


@pytest.mark.parametrize('field',['condition','pending','manager','store','generation','nonce','pid','fd','loop','task','thread','observer_lock','activity_observer'])
def test_original_owner_generation_or_descriptor_drift_refuses_local_settlement(live,field,monkeypatch):
    async def case(observed):
        row=api()._OBSERVATIONS[observed]
        if field=='condition':monkeypatch.setattr(live.admission,'_condition',threading.Condition())
        elif field=='pending':monkeypatch.setattr(live.admission,'_startup_pending',{})
        elif field=='manager':monkeypatch.setattr(live.manager,'_leases',object())
        elif field=='store':
            from backend.engine import job_store
            monkeypatch.setattr(job_store,'_shared',object())
        elif field=='generation':(live.root/'global-active.json').write_bytes(b'{}')
        elif field=='nonce':live.cache['proof']['nonce']='f'*32
        elif field=='pid':monkeypatch.setattr(api().os,'getpid',lambda:row['pid']+1)
        elif field=='fd':
            import os
            os.close(live.cache['writer_private_fd']);live.cache['writer_private_fd']=-1
        elif field=='loop':row['loop']=object()
        elif field=='task':row['task']=object()
        elif field=='thread':row['creator']=threading.Thread()
        elif field=='observer_lock':observed.recovery._lock=threading.RLock()
        elif field=='activity_observer':live.admission._activity_observer=object()
        if field=='loop':
            with pytest.raises(ValueError):observed._fresh(time.monotonic()+.2)
        else:
            with pytest.raises((ValueError,OSError,KeyError)):observed._fresh(time.monotonic()+.2)
    # The owned fixture closes only the original private fd. FD-close test uses
    # replacement through dup2 in the separate descriptor-specific test below.
    if field=='fd':
        async def fdcase(observed):
            import os
            original=live.cache['writer_private_fd'];foreign=(live.root/'foreign').open('w+b')
            backup=os.dup(original)
            try:
                os.dup2(foreign.fileno(),original)
                with pytest.raises(ValueError):observed._fresh(time.monotonic()+.2)
            finally:os.dup2(backup,original);os.close(backup);foreign.close()
        asyncio.run(live.run(fdcase))
    else:asyncio.run(live.run(case))


def test_existing_reason_and_nonhealth_request_remain_sticky(live):
    async def case(observed):
        live.admission.uncovered('accepted_background_work')
        assert live.admission.enter({'type':'http','path':'/api/errors'})
        live.admission.leave()
        recover(live,observed)
        receipt=await asyncio.to_thread(finish_in_original_reader,live,observed,time.monotonic()+2)
        assert receipt['supported'] # Startup facts do not claim request effect coverage.
        assert live.admission.snapshot()['unsupported']==['accepted_background_work','uncovered_request_protocol']
        assert live.admission._activity_observer._uncertain # Anonymous direct fixture ingress stays unsupported.
    asyncio.run(live.run(case))


def test_swallowed_recovery_failure_is_sticky_even_when_threads_join(live,monkeypatch):
    from backend.engine.job_scheduler import JobScheduler
    def failed(*args,**kwargs):raise OSError('controlled original reservation sweep error')
    monkeypatch.setattr(JobScheduler,'sweep_orphaned_reservations',failed)
    async def case(observed):
        recover(live,observed)
        receipt=await asyncio.to_thread(finish_in_original_reader,live,observed,time.monotonic()+2)
        assert not receipt['supported']
        assert live.admission.snapshot()=={'active_scopes':0,'unsupported':['startup_background_protocols']}
    asyncio.run(live.run(case))


def test_expired_preparation_retains_exact_unresolved_token(live):
    async def case(observed):
        with pytest.raises(ValueError):live.admission.finish_original_startup(observed,time.monotonic()-.01,None,[])
        assert live.admission.snapshot()['active_scopes']==1
    asyncio.run(live.run(case))


def test_preparation_condition_contention_obeys_same_absolute_deadline(live):
    async def case(observed):
        lock=live.admission._condition;held=threading.Event();release=threading.Event()
        def owner():
            with lock:held.set();release.wait(1)
        thread=threading.Thread(target=owner);thread.start();assert held.wait(.2)
        deadline=time.monotonic()+.04;started=time.monotonic()
        try:
            with pytest.raises(ValueError):live.admission.finish_original_startup(observed,deadline,None,[])
            assert time.monotonic()-started<.2 and observed in live.admission._startup_pending.values()
        finally:release.set();thread.join(.5)
    asyncio.run(live.run(case))


def test_replayed_or_substituted_actual_flock_record_is_not_current(tmp_path):
    from backend.engine import migration_guard as guard
    with guard.maintenance_guard(tmp_path):
        record=guard._admission_capability(tmp_path,exclusive=False);held=record['original_lock_observation']
        with pytest.raises(ValueError):held.current(record=dict(record))
        owner=record['owner'];record['owner']=(owner[0],threading.Thread(),owner[2])
        with pytest.raises(ValueError):held.current(record=record)
        record['owner']=owner


def test_cache_hit_without_original_folder_acquisition_never_mints(tmp_path,monkeypatch):
    from backend.api import routes_training as training
    key=str(tmp_path.resolve());handle=(tmp_path/'owner.lock').open('a+b')
    monkeypatch.setitem(training._FOLDER_LOCKS,key,handle)
    try:
        assert training._own_data_folder(tmp_path) is True # Legacy API remains compatible.
        assert training._original_folder_lock_observation(tmp_path) is None
    finally:handle.close()

@pytest.mark.parametrize('mutation',['ready','reason','activity','manager_jobs','store_rows','lease_rows','unknown_journal'])
def test_first_startup_mint_refuses_preexisting_or_nonempty_facts(live,mutation):
    from backend.contracts.context import ProjectContext
    if mutation=='ready':live.cache['ready']=True
    elif mutation=='reason':live.admission.uncovered('accepted_background_work')
    elif mutation=='activity':
        assert live.admission.enter({'type':'http','path':'/health'});live.admission.leave()
    elif mutation=='manager_jobs':live.manager._jobs['existing']=object()
    elif mutation=='store_rows':live.store.submit(ProjectContext(workspace_id='w',project_id='p',actor_id='a',mode='local'),'ns','training',{})
    elif mutation=='lease_rows':assert live.manager._leases.acquire('existing-source-lease','local-source','all')
    else:
        path=live.generation/'local_jobs';path.mkdir();(path/'unknown.keep').write_bytes(b'original retained')
    async def case():
        from backend.engine.migration_guard import maintenance_guard
        with maintenance_guard(live.root):
            with pytest.raises(ValueError):api()._begin_from_original_cache(live.cache,live.app,live.manager,live.telemetry)
            assert live.admission.snapshot()['active_scopes']==0
    asyncio.run(case())


def test_settlement_cannot_be_substituted_with_a_caller_json_receipt(live,monkeypatch):
    async def case(observed):
        monkeypatch.setattr(observed,'settle',lambda *args:{'supported':True})
        with pytest.raises(ValueError):live.admission.finish_original_startup(observed,time.monotonic()+.2,None,[])
        assert live.admission.snapshot()['active_scopes']==1
    asyncio.run(live.run(case))


def test_recovery_snapshot_method_substitution_refuses_original_observer(live,monkeypatch):
    async def case(observed):
        monkeypatch.setattr(observed.recovery,'snapshot',lambda:{'all_completed_empty':True})
        with pytest.raises(ValueError):observed._fresh(time.monotonic()+.2)
    asyncio.run(live.run(case))


def test_official_original_shared_guard_release_invalidates_retained_startup(live):
    retained=[]
    async def case(observed):retained.append(observed)
    asyncio.run(live.run(case))
    with pytest.raises(ValueError):retained[0]._fresh(time.monotonic()+.2)
    assert live.admission.snapshot()['active_scopes']==1


def test_original_folder_handle_close_and_replacement_refuse_retained_observation(tmp_path,monkeypatch):
    from backend.api import routes_training as training
    key=str(tmp_path.resolve());assert training._own_data_folder(tmp_path)
    held=training._original_folder_lock_observation(tmp_path);handle=training._FOLDER_LOCKS[key]
    foreign=(tmp_path/'foreign').open('a+b')
    try:
        monkeypatch.setitem(training._FOLDER_LOCKS,key,foreign)
        with pytest.raises(ValueError):held.current()
        monkeypatch.setitem(training._FOLDER_LOCKS,key,handle);handle.close()
        with pytest.raises(ValueError):held.current()
    finally:foreign.close();training._FOLDER_LOCKS.pop(key,None)


def test_original_readonly_table_recheck_obeys_locked_database_deadline(live):
    import sqlite3
    # Original connection owns this isolated DB. A read must not borrow the
    # store's mutable 30-second WAL setup under a shorter preparation budget.
    db=sqlite3.connect(live.store.path);db.execute('PRAGMA journal_mode=DELETE');db.execute('BEGIN EXCLUSIVE')
    deadline=time.monotonic()+.04;started=time.monotonic()
    try:
        with pytest.raises((ValueError,sqlite3.OperationalError)):api()._zero_tables(live.store.path,('jobs',),deadline)
        assert time.monotonic()-started<.2
    finally:db.rollback();db.close()


def test_released_guard_observation_does_not_retain_every_finished_request(tmp_path):
    import gc,weakref
    from backend.engine import migration_guard as guard
    refs=[]
    for _ in range(8):
        with guard.maintenance_guard(tmp_path):
            record=guard._admission_capability(tmp_path,exclusive=False)
            refs.append(weakref.ref(record['original_lock_observation']))
        del record
    gc.collect()
    assert all(ref() is None for ref in refs), 'Finished admissions must not accumulate retained handles'


def test_shadow_acquisition_observation_error_keeps_legacy_guard_behavior(tmp_path,monkeypatch):
    from backend.engine import migration_guard as guard,original_lock_acquisition as capture
    def fail(*args,**kwargs):raise OSError('controlled observation unavailable')
    monkeypatch.setattr(capture,'_capture_successful_acquisition',fail)
    with guard.maintenance_guard(tmp_path):
        assert guard.shared_admitted(tmp_path)
        assert guard._admission_capability(tmp_path,exclusive=False)['original_lock_observation'] is None
    assert not guard.shared_admitted(tmp_path)


def test_shadow_folder_observation_error_keeps_legacy_lock_and_returns_no_fact(tmp_path,monkeypatch):
    from backend.api import routes_training as training
    from backend.engine import original_lock_acquisition as capture
    def fail(*args,**kwargs):raise OSError('controlled observation unavailable')
    monkeypatch.setattr(capture,'_capture_successful_acquisition',fail)
    key=str(tmp_path.resolve())
    try:
        assert training._own_data_folder(tmp_path)
        assert training._original_folder_lock_observation(tmp_path) is None
        assert not training._FOLDER_LOCKS[key].closed
    finally:
        handle=training._FOLDER_LOCKS.pop(key,None)
        if handle is not None:handle.close()


def test_final_condition_reacquisition_cannot_extend_original_settlement_deadline(live,monkeypatch):
    async def case(observed):
        recover(live,observed)
        original=api()._zero_tables;held=threading.Event();release=threading.Event();owners=[]
        def hold():
            with live.admission._condition:held.set();release.wait(1)
        def observed_read(path,tables,deadline=None):
            result=original(path,tables,deadline)
            if tables==('leases',) and deadline is not None:
                thread=threading.Thread(target=hold);owners.append(thread);thread.start();assert held.wait(.2)
            return result
        monkeypatch.setattr(api(),'_zero_tables',observed_read)
        deadline=time.monotonic()+.15;started=time.monotonic()
        try:
            with pytest.raises(ValueError):await asyncio.to_thread(finish_in_original_reader,live,observed,deadline)
            assert time.monotonic()-started<.4
            assert observed in live.admission._startup_pending.values() and not live.admission._closed
        finally:
            release.set()
            for thread in owners:thread.join(.5)
    asyncio.run(live.run(case))


def test_actual_late_maintenance_keeps_original_pending_token_and_sticky_reason(live,monkeypatch):
    from backend.api import routes_training as training
    held=threading.Event();release=threading.Event()
    def original_inflight(manager,store,folders,*,outcome_observer=None):held.set();release.wait(1)
    monkeypatch.setattr(training,'_maintain_job_ledger',original_inflight)
    async def case(observed):
        recover(live,observed);assert held.wait(.2)
        live.admission.uncovered('accepted_background_work')
        deadline=time.monotonic()+.06;started=time.monotonic()
        try:
            with pytest.raises(ValueError):await asyncio.to_thread(finish_in_original_reader,live,observed,deadline)
            assert time.monotonic()-started<.3
            assert live.manager._maintenance.is_alive()
            assert live.admission.snapshot()=={'active_scopes':1,'unsupported':['accepted_background_work']}
        finally:release.set()
    asyncio.run(live.run(case))


@pytest.mark.parametrize('kind',['inner_swallowed','outer_swallowed'])
def test_later_real_maintenance_error_cannot_be_hidden_by_successful_join(live,monkeypatch,kind):
    from backend.api import routes_training as training
    from backend.engine.job_scheduler import JobScheduler
    fired=threading.Event()
    monkeypatch.setattr(training,'_MAINTENANCE_SECONDS',.005) # Controlled source schedule; production clock unchanged.
    async def case(observed):
        recover(live,observed)
        def failed(*args,**kwargs):
            fired.set()
            if kind=='inner_swallowed':raise OSError('controlled later reservation sweep error')
            raise TypeError('controlled later maintenance target error')
        if kind=='inner_swallowed':monkeypatch.setattr(JobScheduler,'sweep_orphaned_reservations',failed)
        else:monkeypatch.setattr(training,'_ledger_maintenance_tick',failed)
        assert await asyncio.to_thread(fired.wait,.3)
        receipt=await asyncio.to_thread(finish_in_original_reader,live,observed,time.monotonic()+2)
        assert not receipt['supported'], 'Successful thread join hid a swallowed later maintenance failure'
        assert 'startup_background_protocols' in live.admission.snapshot()['unsupported']
    asyncio.run(live.run(case))


def test_real_main_lifespan_wires_same_observers_before_recovery_and_thread_start(live,monkeypatch):
    from backend import main
    from backend.engine.migration_guard import maintenance_guard
    monkeypatch.setattr(main,'training_job_manager',live.manager)
    # Model only the absent controller ready/service transport; startup body,
    # real recovery functions, original owner joins and cleanup stay genuine.
    monkeypatch.setattr(main,'backend_bootstrap_ready',lambda:None)
    async def run():
        with maintenance_guard(live.root):
            async with main._admitted_lifespan(live.app):
                observed=live.cache['fresh_startup_observation']
                assert live.admission.snapshot()=={'active_scopes':1,'unsupported':[]}
                assert observed.recovery.original_owner('import_recovery') is live.app
                assert observed.recovery.original_owner('ledger_reconcile') is live.manager
                assert len(observed.recovery.snapshot()['outcomes'])==8
                assert observed.sweep.original_owner is not None
                receipt=await asyncio.to_thread(finish_in_original_reader,live,observed,time.monotonic()+2)
                assert receipt['supported'] and not live.admission._closed
            assert live.admission.snapshot()=={'active_scopes':0,'unsupported':[]}
    asyncio.run(run())


def test_ordinary_leave_cannot_consume_original_typed_pending_startup(live):
    async def case(observed):
        with pytest.raises(ValueError):live.admission.leave()
        assert live.admission.snapshot()['active_scopes']==1
        assert observed in live.admission._startup_pending.values()
    asyncio.run(live.run(case))
