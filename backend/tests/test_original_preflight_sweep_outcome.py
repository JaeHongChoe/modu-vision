"""Original cooperative sweep + real isolated inventories, no native/model work."""
import copy
import importlib
import os
import time
from pathlib import Path
import pytest
from backend.engine.startup_thread_lifetime import OriginalStartupThread


def api():
    try:return importlib.import_module('backend.engine.original_preflight_sweep_outcome')
    except ModuleNotFoundError as error:
        if error.name=='backend.engine.original_preflight_sweep_outcome':pytest.fail('Original sweep outcome hook is missing')
        raise


def run(root, *, fault=None):
    from backend.engine import worker_preflight as sweep
    held=api().OriginalPreflightSweepOutcome(root)
    results=[]
    owner=OriginalStartupThread('preflight_run_sweep',lambda stop:results.append(sweep.sweep_stale_runs(root,stop_event=stop,outcome_observer=held)),name='PrivateOriginalSweep')
    held.bind(owner)
    if fault:fault(held,owner)
    owner.start();owner.thread.join(1)
    receipt=owner.stop_and_join(time.monotonic()+1,expected_thread=owner.thread,expected_stop=owner.stop_event)
    assert receipt['original_thread_joined']
    return held,owner,results


def refused(held):
    snap=held.snapshot();assert snap['uncertain'] and not snap['empty_without_uncertainty']
    assert all(snap[key] is False for key in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))


@pytest.mark.parametrize('exists',[True,False])
def test_original_empty_sweep_records_actual_complete_thread_and_full_root(tmp_path,exists):
    root=tmp_path/'worker_preflight_runs'
    if exists:root.mkdir()
    held,owner,results=run(root)
    snap=held.snapshot();assert snap['completed'] and snap['empty_without_uncertainty'] and not snap['uncertain']
    assert held.original_owner is owner and held.original_thread is owner.thread and held.original_stop is owner.stop_event
    assert results==[[]] and not owner.thread.is_alive()
    assert all(snap[key] is False for key in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))


def test_nonempty_sweep_never_qualifies_and_original_fresh_bytes_remain(tmp_path):
    root=tmp_path/'worker_preflight_runs';root.mkdir();entry=root/'fresh';entry.mkdir();path=entry/'retained';raw=b'original input';path.write_bytes(raw)
    held,_,results=run(root);refused(held);assert path.read_bytes()==raw and results==[[]]


def test_swallowed_original_empty_sweep_read_error_stays_uncertain(tmp_path,monkeypatch):
    root=tmp_path/'worker_preflight_runs';root.mkdir();old=Path.iterdir
    def fail(path):
        if path==root:raise PermissionError('controlled original sweep read failure')
        return old(path)
    monkeypatch.setattr(Path,'iterdir',fail)
    held,_,results=run(root);refused(held);assert results==[[]]
    assert any(row['reason']=='sweep_read_error' and row['type']=='PermissionError' for row in held.snapshot()['errors'])


def test_missing_root_created_during_actual_sweep_cannot_replace_initial_absence(tmp_path,monkeypatch):
    root=tmp_path/'worker_preflight_runs';old=Path.iterdir
    def changed(path):
        if path==root and not root.exists():root.mkdir();(root/'late').write_bytes(b'late original retained input')
        return old(path)
    monkeypatch.setattr(Path,'iterdir',changed)
    held,_,_=run(root);refused(held);assert (root/'late').read_bytes()==b'late original retained input'


def test_swallowed_original_cleanup_error_preserves_data_and_uncertainty(tmp_path,monkeypatch):
    from backend.engine import worker_preflight
    root=tmp_path/'worker_preflight_runs';root.mkdir();entry=root/'old';entry.mkdir();path=entry/'retained';path.write_bytes(b'keep');os.utime(entry,(0,0))
    monkeypatch.setattr(worker_preflight.shutil,'rmtree',lambda *args,**kwargs:(_ for _ in ()).throw(OSError('controlled removal failure')))
    held,_,results=run(root);refused(held);assert path.read_bytes()==b'keep' and results==[[]]
    assert any(row['reason']=='sweep_entry_error' for row in held.snapshot()['errors'])


def test_linked_root_observation_is_uncertain_and_foreign_fresh_input_unchanged(tmp_path):
    foreign=tmp_path/'foreign';foreign.mkdir();path=foreign/'retained';path.write_bytes(b'foreign retained');root=tmp_path/'worker_preflight_runs';root.symlink_to(foreign,target_is_directory=True)
    held,_,_=run(root);refused(held);assert path.read_bytes()==b'foreign retained'


def test_original_binding_must_precede_thread_start_and_cannot_replay(tmp_path):
    held,owner,_=run(tmp_path/'worker_preflight_runs')
    with pytest.raises(api().SweepObservationError):held.bind(owner)
    refused(held)


def test_original_sweep_owner_and_event_substitution_remain_uncertain(tmp_path):
    held,owner,_=run(tmp_path/'worker_preflight_runs')
    import threading
    owner._stop=threading.Event()
    refused(held)


def test_original_sweep_observer_cannot_be_copied_or_used_by_foreign_thread(tmp_path):
    held=api().OriginalPreflightSweepOutcome(tmp_path/'worker_preflight_runs')
    with pytest.raises(api().SweepObservationError):copy.copy(held)
    from backend.engine.worker_preflight import sweep_stale_runs
    with pytest.raises(api().SweepObservationError):sweep_stale_runs(held.root,outcome_observer=held)
    refused(held)


def test_restoring_a_changed_original_event_never_clears_observed_uncertainty(tmp_path):
    held,owner,_=run(tmp_path/'worker_preflight_runs')
    import threading
    original=owner._stop;owner._stop=threading.Event();refused(held)
    owner._stop=original;refused(held)


def test_copied_original_thread_owner_cannot_replace_retained_owner_identity(tmp_path):
    held,owner,_=run(tmp_path/'worker_preflight_runs')
    held._owner=copy.copy(owner)
    refused(held)


def test_original_pending_handle_and_late_thread_cleanup_are_unresolved_not_success(tmp_path):
    import threading
    from backend.engine.worker_preflight import sweep_stale_runs
    root=tmp_path/'worker_preflight_runs';root.mkdir();held=api().OriginalPreflightSweepOutcome(root)
    pending=held.snapshot();assert not pending['completed'] and not pending['empty_without_uncertainty'] and not pending['uncertain']
    begun=threading.Event();release=threading.Event()
    def target(stop):
        sweep_stale_runs(root,stop_event=stop,outcome_observer=held);begun.set();assert release.wait(1)
    owner=OriginalStartupThread('preflight_run_sweep',target);held.bind(owner);owner.start()
    try:
        assert begun.wait(1);pending=held.snapshot()
        assert not pending['completed'] and not pending['empty_without_uncertainty'] and not pending['uncertain']
    finally:release.set();owner.thread.join(1)
    assert held.snapshot()['empty_without_uncertainty']


def test_write_after_actual_sweep_before_original_thread_end_poisoned_at_receipt(tmp_path):
    from backend.engine.worker_preflight import sweep_stale_runs
    root=tmp_path/'worker_preflight_runs';root.mkdir();held=api().OriginalPreflightSweepOutcome(root)
    def target(stop):
        sweep_stale_runs(root,stop_event=stop,outcome_observer=held)
        (root/'late-original-input').write_bytes(b'retained')
    owner=OriginalStartupThread('preflight_run_sweep',target);held.bind(owner);owner.start();owner.thread.join(1)
    refused(held);assert (root/'late-original-input').read_bytes()==b'retained'
