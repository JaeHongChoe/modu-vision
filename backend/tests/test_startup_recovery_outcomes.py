"""Real isolated ledger/journal controls; observation never grants release authority."""
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest


def api():
    try: return importlib.import_module('backend.engine.startup_recovery_outcomes')
    except ModuleNotFoundError as error:
        if error.name == 'backend.engine.startup_recovery_outcomes':
            pytest.fail('Typed original startup recovery completion observations are missing')
        raise


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    from backend.api import routes_training as training, routes_dataset_imports as imports
    from backend.remote import coordinator
    from backend.engine import local_training_worker as local
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.engine.data_backup_job import DataBackupJobs
    from backend.engine.data_restore_job import DataRestoreJobs
    from backend.contracts.context import ContextRegistry, ProjectContext
    root = tmp_path/'user'; root.mkdir()
    store = JobStore(root/'jobs/ledger.sqlite3')
    manager = training.TrainingJobManager(); manager._leases = ResourceLeases(root/'leases.sqlite3')
    app = SimpleNamespace(state=SimpleNamespace(context_registry=ContextRegistry(root/'projects')))
    monkeypatch.setattr(training, 'job_ledger', lambda: store)
    monkeypatch.setattr(imports, 'ledger', lambda: store)
    monkeypatch.setattr(imports, '_backup_jobs', lambda: DataBackupJobs(store))
    monkeypatch.setattr(imports, '_restore_jobs', lambda: DataRestoreJobs(store))
    monkeypatch.setattr(local, '_index', lambda: root/'local_jobs')
    monkeypatch.setattr(coordinator, '_journal_index', lambda: root/'remote_jobs')
    context = ProjectContext(workspace_id='w',project_id='p',actor_id='a',mode='local')
    yield SimpleNamespace(root=root,store=store,manager=manager,app=app,training=training,imports=imports,
                          local=local,remote=coordinator,context=context)
    manager._shutdown.set()
    for scope in getattr(manager, '_maintenance_owners', []): scope.thread.join(1)


def observer(recovery): return api().RecoveryOutcomeObserver(recovery.app)

def outcome(held, role):
    return next(row for row in held.snapshot()['outcomes'] if row['role']==role)


def assert_uncertain(held):
    snap = held.snapshot()
    assert snap['uncertain'] and not snap['all_completed_empty']
    assert all(snap[key] is False for key in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))


def test_fresh_empty_remote_completes_without_creating_workers_or_journals(recovery):
    held = observer(recovery)
    assert recovery.remote.recover_remote_jobs(recovery.manager, outcome_observer=held) is None
    row = outcome(held, 'remote_recovery')
    assert row['completed'] and row['empty_without_uncertainty'] and not held.snapshot()['uncertain']
    assert recovery.manager.list_jobs()==[] and not (recovery.root/'remote_jobs').exists()


def test_fresh_empty_local_recovery_retains_original_maintenance_thread(recovery):
    held = observer(recovery)
    assert recovery.local.recover_local_jobs(recovery.manager, outcome_observer=held) is None
    for role in ('local_recovery','local_journals','ledger_reconcile'):
        assert outcome(held,role)['completed'] and outcome(held,role)['empty_without_uncertainty']
    assert held.original_thread('job_ledger_maintenance') is recovery.manager._maintenance
    assert recovery.manager._maintenance.is_alive() and not held.snapshot()['uncertain']


def test_fresh_empty_import_backup_restore_outcomes_keep_existing_return_contract(recovery):
    held = observer(recovery)
    result = recovery.imports.recover_imports_at_startup(recovery.app, outcome_observer=held)
    empty = {'completed':[], 'interrupted':[]}
    assert result == {**empty, 'backups':{'interrupted':[],'completed':[]}, 'restores':empty}
    for role in ('import_recovery','import_orphans','backup_orphans','restore_orphans'):
        assert outcome(held,role)['completed'] and outcome(held,role)['empty_without_uncertainty']
    assert not held.snapshot()['uncertain']


def test_unknown_remote_inventory_entry_is_sticky_and_original_bytes_preserved(recovery):
    root = recovery.root/'remote_jobs'; root.mkdir(); path=root/'unrecognized-entry.tmp'
    raw=b'original unknown retained journal';path.write_bytes(raw)
    held=observer(recovery)
    assert recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=held) is None
    assert_uncertain(held); assert path.read_bytes()==raw
    assert 'unknown_inventory_entry' in outcome(held,'remote_recovery')['uncertainties']


def test_invalid_remote_journal_error_is_observed_despite_normal_return(recovery):
    root=recovery.root/'remote_jobs';root.mkdir();path=root/'job_broken.json';raw=b'{';path.write_bytes(raw)
    held=observer(recovery)
    assert recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=held) is None
    assert_uncertain(held); assert path.read_bytes()==raw
    assert 'journal_read_error' in outcome(held,'remote_recovery')['uncertainties']


def test_invalid_local_journal_error_is_observed_despite_normal_return(recovery):
    root=recovery.root/'local_jobs';root.mkdir();path=root/'job_broken.json';raw=b'{';path.write_bytes(raw)
    held=observer(recovery)
    assert recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held) is None
    assert_uncertain(held); assert path.read_bytes()==raw
    assert 'journal_recovery_error' in outcome(held,'local_journals')['uncertainties']


def test_unknown_local_inventory_entry_is_not_mistaken_for_empty(recovery):
    root=recovery.root/'local_jobs';root.mkdir();path=root/'.unrecognized';raw=b'retained';path.write_bytes(raw)
    held=observer(recovery)
    recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held)
    assert_uncertain(held); assert path.read_bytes()==raw
    assert 'unknown_inventory_entry' in outcome(held,'local_journals')['uncertainties']


def test_swallowed_empty_reservation_sweep_error_is_not_an_empty_success(recovery,monkeypatch):
    from backend.engine.job_scheduler import JobScheduler
    def fail(self): raise OSError('controlled reservation sweep failure')
    monkeypatch.setattr(JobScheduler,'sweep_orphaned_reservations',fail)
    held=observer(recovery)
    assert recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held) is None
    assert_uncertain(held)
    assert 'reservation_sweep_error' in outcome(held,'ledger_reconcile')['uncertainties']
    assert recovery.store.active()==[]


def test_another_actual_folder_lock_owner_is_observed_as_skipped(recovery):
    import fcntl
    path=recovery.store.path.parent/'owner.lock';foreign=path.open('a+b')
    fcntl.flock(foreign.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        held=observer(recovery)
        result=recovery.imports.recover_imports_at_startup(recovery.app,outcome_observer=held)
        assert result=={'completed':[],'interrupted':[],'skipped':'another backend owns the data folder'}
        assert_uncertain(held)
        assert outcome(held,'import_recovery')['skipped']=='another_folder_owner'
        assert recovery.store.active()==[]
    finally: foreign.close()


def test_ledger_reconcile_row_error_does_not_alter_original_spec_or_hide_failure(recovery,monkeypatch):
    ref=recovery.store.submit(recovery.context,'ns','training',{'task':'classification','original':'keep'})
    before=recovery.store.record(ref.id)
    def fail(*args): raise KeyError('controlled row failure')
    monkeypatch.setattr(recovery.training,'_reconcile_one',fail)
    held=observer(recovery)
    assert recovery.training.reconcile_job_ledger(recovery.manager,outcome_observer=held)==[]
    assert_uncertain(held)
    assert 'reconcile_row_error' in outcome(held,'ledger_reconcile')['uncertainties']
    assert recovery.store.record(ref.id)==before


def test_swallowed_import_inner_error_is_retained_without_publication(recovery,monkeypatch):
    from backend.engine.dataset_import_job import DatasetImportJobs,KIND
    from backend.engine.dataset_index import DatasetIndex
    index=DatasetIndex(recovery.root/'index.sqlite3');jobs=DatasetImportJobs(recovery.store,index)
    ref=recovery.store.submit(recovery.context,'ns',KIND,{'source_root':str(recovery.root/'source')})
    before=recovery.store.record(ref.id)
    def fail(*args): raise ValueError('controlled sealed revision lookup failure')
    monkeypatch.setattr(index,'_published',fail)
    held=observer(recovery)
    assert jobs.recover_orphans(outcome_observer=held)=={'completed':[],'interrupted':[]}
    assert_uncertain(held)
    assert 'orphan_recovery_error' in outcome(held,'import_orphans')['uncertainties']
    assert recovery.store.record(ref.id)==before


def test_swallowed_backup_stale_revision_is_observed_without_rewriting_job(recovery,monkeypatch):
    from backend.engine.data_backup_job import DataBackupJobs,KIND
    from backend.engine.job_store import StaleRevision
    jobs=DataBackupJobs(recovery.store);ref=recovery.store.submit(recovery.context,'ns',KIND,{})
    before=recovery.store.record(ref.id)
    def fail(*args,**kwargs): raise StaleRevision('controlled stale backup')
    monkeypatch.setattr(recovery.store,'transition',fail)
    held=observer(recovery)
    assert jobs.recover_orphans(outcome_observer=held)=={'interrupted':[],'completed':[]}
    assert_uncertain(held)
    assert 'orphan_recovery_error' in outcome(held,'backup_orphans')['uncertainties']
    assert recovery.store.record(ref.id)==before


def test_swallowed_restore_cancel_cleanup_error_keeps_original_cancel_intent(recovery,monkeypatch):
    from backend.engine.data_restore_job import DataRestoreJobs,KIND
    jobs=DataRestoreJobs(recovery.store);ref=recovery.store.submit(recovery.context,'ns',KIND,{'target_dir':str(recovery.root/'target')})
    recovery.store.request_cancel(ref.id,'a','original cancellation')
    intent=recovery.store.cancel_intent(ref.id);spec=recovery.store.record(ref.id)['spec_json']
    monkeypatch.setattr(jobs,'_verified_target',lambda *args,**kwargs:False)
    def fail(*args): raise OSError('controlled staging cleanup failure')
    monkeypatch.setattr(jobs,'_cleanup_cancelled',fail)
    held=observer(recovery);result=jobs.recover_orphans(outcome_observer=held)
    assert result=={'interrupted':[],'completed':[]}
    assert_uncertain(held)
    assert 'cancellation_cleanup_error' in outcome(held,'restore_orphans')['uncertainties']
    assert recovery.store.cancel_intent(ref.id)==intent and recovery.store.record(ref.id)['spec_json']==spec


def test_import_outer_caught_error_preserves_error_return_and_observation(recovery,monkeypatch):
    def fail(*args,**kwargs): raise RuntimeError('controlled dataset index construction failure')
    monkeypatch.setattr(recovery.imports,'DatasetImportJobs',fail)
    held=observer(recovery)
    assert recovery.imports.recover_imports_at_startup(recovery.app,outcome_observer=held)=={'completed':[],'interrupted':[],'error':True}
    assert_uncertain(held)
    assert 'startup_recovery_error' in outcome(held,'import_recovery')['uncertainties']


def test_nonempty_terminal_history_cannot_look_empty_through_active_rows(recovery):
    from backend.engine.data_backup_job import DataBackupJobs,KIND
    ref=recovery.store.submit(recovery.context,'ns',KIND,{})
    recovery.store.transition(ref.id,ref.revision,'fail',{'reason':'original retained failed history'})
    before=recovery.store.record(ref.id);held=observer(recovery)
    assert DataBackupJobs(recovery.store).recover_orphans(outcome_observer=held)=={'interrupted':[],'completed':[]}
    assert_uncertain(held); assert recovery.store.record(ref.id)==before
    assert 'nonempty_store' in outcome(held,'backup_orphans')['uncertainties']


def test_uncertainty_remains_after_an_independent_later_empty_stage(recovery):
    root=recovery.root/'remote_jobs';root.mkdir();path=root/'unknown';path.write_bytes(b'keep')
    held=observer(recovery)
    recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=held)
    recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held)
    assert outcome(held,'local_journals')['empty_without_uncertainty']
    assert_uncertain(held); assert path.read_bytes()==b'keep'


def test_copied_return_snapshot_cannot_change_retained_outcomes(recovery):
    held=observer(recovery);recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=held)
    first=held.snapshot();first['outcomes'].clear();first['uncertain']=True
    second=held.snapshot();assert len(second['outcomes'])==1 and not second['uncertain']
    assert all(second[k] is False for k in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))


def test_full_fresh_empty_recovery_set_observes_all_roles_without_claiming_release(recovery):
    held=observer(recovery)
    recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=held)
    recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held)
    recovery.imports.recover_imports_at_startup(recovery.app,outcome_observer=held)
    snap=held.snapshot()
    assert set(snap['observed_roles'])==api().ROLES and snap['all_completed_empty']
    assert not snap['uncertain']
    assert all(snap[k] is False for k in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))


def test_a_partial_empty_role_set_is_not_complete_startup_coverage(recovery):
    held=observer(recovery)
    recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=held)
    assert not held.snapshot()['all_completed_empty']


def test_copied_observer_cannot_borrow_original_owner_context(recovery):
    import copy
    held=observer(recovery);borrowed=copy.copy(held)
    recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=borrowed)
    assert_uncertain(borrowed);assert_uncertain(held)
    assert 'original_observer_context_changed' in outcome(borrowed,'remote_recovery')['uncertainties']


def test_original_ledger_open_exception_is_observed_when_local_recovery_swallows_it(recovery,monkeypatch):
    def fail():raise OSError('controlled ledger resolver failure')
    monkeypatch.setattr(recovery.training,'job_ledger',fail)
    held=observer(recovery)
    assert recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held) is None
    assert_uncertain(held)
    assert not outcome(held,'ledger_reconcile')['completed']
    assert 'ledger_recovery_error' in outcome(held,'local_recovery')['uncertainties']


def test_swallowed_original_restore_marker_parse_error_is_observed(recovery,monkeypatch):
    from backend.engine.data_restore_job import DataRestoreJobs,KIND,MARKER
    jobs=DataRestoreJobs(recovery.store);target=recovery.root/'retained-target';target.mkdir()
    marker=target/MARKER;raw=b'{original invalid marker';marker.write_bytes(raw)
    ref=recovery.store.submit(recovery.context,'ns',KIND,{'target_dir':str(target)})
    identity=target.stat()
    recovery.store.checkpoint(ref.id,{'result_ref':{'directory_identity':[identity.st_dev,identity.st_ino]}})
    monkeypatch.setattr(jobs,'_check_reservation',lambda *args:None)
    held=observer(recovery)
    result=jobs.recover_orphans(outcome_observer=held)
    assert result=={'interrupted':[ref.id],'completed':[]}
    assert_uncertain(held)
    assert 'restore_verification_error' in outcome(held,'restore_orphans')['uncertainties']
    assert marker.read_bytes()==raw and target.is_dir()


def test_changed_empty_inventory_after_original_stage_is_refused(recovery):
    root=recovery.root/'remote_jobs';root.mkdir();held=observer(recovery)
    with api().recovery_stage(held,'remote_recovery',recovery.manager) as stage:
        stage.inventory(root)
        path=root/'new-unknown';path.write_bytes(b'retained late input')
    assert_uncertain(held)
    assert 'inventory_changed' in outcome(held,'remote_recovery')['uncertainties']
    assert path.read_bytes()==b'retained late input'


def test_observed_nonempty_remote_recovery_preserves_original_runner_arguments_and_journal(recovery,monkeypatch):
    from backend.remote.profiles import ComputeProfile
    root=recovery.root/'remote_jobs';root.mkdir();output=recovery.root/'output';output.mkdir()
    profile=ComputeProfile(id='fixture',name='Fixture',ssh_target='qa@fixture.example',ssh_port=22,
                           remote_root='/data/fixture',runtime_kind='python',runtime_value='/usr/bin/python3')
    journal={'job_id':'job_fixture','operation':'train','state':'launched','profile':profile.model_dump(),
             'output_dir':str(output),'task':'classification','preset':'fast','dataset_path':str(recovery.root/'source'),
             'launch_spec':{'original':'unchanged'}}
    path=root/'job_fixture.json';raw=json.dumps(journal).encode();path.write_bytes(raw)
    calls=[];runner=object()
    monkeypatch.setattr(recovery.remote,'make_remote_runner',lambda original_profile,spec:runner)
    monkeypatch.setattr(recovery.manager,'start_remote_job',lambda **kwargs:calls.append(kwargs))
    held=observer(recovery)
    assert recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=held) is None
    assert len(calls)==1 and calls[0]['job_id']=='job_fixture' and calls[0]['remote_runner'] is runner
    assert calls[0]['recovery_state']=='launched' and calls[0]['launch_spec']==journal['launch_spec']
    assert_uncertain(held);assert path.read_bytes()==raw


def test_observed_nonempty_confirmed_local_history_keeps_original_spec_and_callback(recovery,monkeypatch):
    import hashlib
    root=recovery.root/'local_jobs';root.mkdir();output=recovery.root/'local-output';output.mkdir()
    spec={'job_id':'job_local_fixture','task':'classification','preset':'fast','dataset_path':str(recovery.root/'source'),
          'output_dir':str(output),'lease_path':str(recovery.manager._leases.path),'lease_owner':'fixture-owner'}
    spec_path=output/'local_spec.json';spec_raw=json.dumps(spec).encode();spec_path.write_bytes(spec_raw)
    journal={'job_id':spec['job_id'],'output_dir':str(output),'spec_path':str(spec_path),
             'spec_sha256':hashlib.sha256(spec_raw).hexdigest(),'worker_exit_confirmed':True,'status':'failed'}
    path=root/'job_local_fixture.json';raw=json.dumps(journal).encode();path.write_bytes(raw)
    calls=[]
    monkeypatch.setattr(recovery.local,'_restore_confirmed_terminal',lambda *args:calls.append(args))
    held=observer(recovery)
    assert recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held) is None
    assert len(calls)==1 and calls[0][0] is recovery.manager and calls[0][2]==journal
    assert_uncertain(held);assert path.read_bytes()==raw and spec_path.read_bytes()==spec_raw


def test_linked_original_inventory_stays_uncertain_and_foreign_bytes_are_preserved(recovery):
    foreign=recovery.root/'foreign';foreign.mkdir();path=foreign/'unknown';raw=b'foreign retained input';path.write_bytes(raw)
    (recovery.root/'remote_jobs').symlink_to(foreign,target_is_directory=True)
    held=observer(recovery)
    assert recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=held) is None
    assert_uncertain(held);assert path.read_bytes()==raw
    assert 'inventory_observation_error' in outcome(held,'remote_recovery')['uncertainties']


def test_copy_after_complete_empty_stages_cannot_reuse_original_completion(recovery):
    import copy
    held=observer(recovery)
    recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=held)
    recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held)
    recovery.imports.recover_imports_at_startup(recovery.app,outcome_observer=held)
    assert held.snapshot()['all_completed_empty']
    assert_uncertain(copy.copy(held))
    assert held.snapshot()['all_completed_empty']


def test_original_store_and_folder_lock_objects_are_retained_for_later_binding(recovery):
    held=observer(recovery)
    recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held)
    assert held.original_owner('ledger_reconcile') is recovery.manager
    assert held.original_store('ledger_reconcile')==(recovery.store,recovery.store.path)
    handle,fd,identity=held.original_folder_lock('ledger_reconcile')
    assert recovery.training._FOLDER_LOCKS[str(recovery.store.path.parent.resolve())] is handle
    assert handle.fileno()==fd and os.fstat(fd).st_ino==identity[1]
    assert not held.snapshot()['uncertain']


def test_a_copied_observer_cannot_return_original_store_or_folder_handles(recovery):
    import copy
    held=observer(recovery)
    recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held)
    borrowed=copy.copy(held)
    for method in (borrowed.original_store,borrowed.original_folder_lock,borrowed.original_owner):
        with pytest.raises(api().RecoveryObservationError,match='owner'):
            method('ledger_reconcile')


def test_unknown_original_role_after_all_empty_recoveries_makes_uncertainty_sticky(recovery):
    held=observer(recovery)
    recovery.remote.recover_remote_jobs(recovery.manager,outcome_observer=held)
    recovery.local.recover_local_jobs(recovery.manager,outcome_observer=held)
    recovery.imports.recover_imports_at_startup(recovery.app,outcome_observer=held)
    before=held.snapshot()
    assert before['all_completed_empty'] and not before['uncertain']
    with pytest.raises(api().RecoveryObservationError,match='Unknown startup recovery stage'):
        with api().recovery_stage(held,'unknown_original_recovery_role',recovery.app):
            pytest.fail('Unknown recovery stage entered')
    assert_uncertain(held)
    after=held.snapshot()
    assert after['outcomes']==before['outcomes'] and after['observed_roles']==before['observed_roles']
    assert held.original_owner('ledger_reconcile') is recovery.manager
    assert held.original_store('ledger_reconcile')==(recovery.store,recovery.store.path)
