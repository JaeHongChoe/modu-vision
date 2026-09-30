"""Capacity and physical identity are enforced across independent schedulers."""
from pathlib import Path
import pytest
from backend.engine.shared_scheduler import ResourceLeases


def inventory(leases):
    leases.configure_devices('host', [
        {'selector': '0', 'uuid': 'GPU-a', 'memory_mb': 1000},
        {'selector': '1', 'uuid': 'GPU-b', 'memory_mb': 2000},
        {'selector': 'MIG-a', 'uuid': 'MIG-a', 'parent_uuid': 'GPU-a', 'memory_mb': 400},
        {'selector': 'MIG-b', 'uuid': 'MIG-b', 'parent_uuid': 'GPU-a', 'memory_mb': 400},
    ])


def test_shared_capacity_is_atomic_and_retains_unknown_remote_runs(tmp_path):
    first = ResourceLeases(tmp_path/'leases.sqlite', owner='a')
    second = ResourceLeases(tmp_path/'leases.sqlite', owner='b')
    inventory(first)
    assert first.acquire('one', 'host', '0', remote=True, memory_budget_mb=400, allow_sharing=True, task='ocr', project_id='p')
    assert second.acquire('two', 'host', 'GPU-a', remote=True, memory_budget_mb=500, allow_sharing=True, task='label', project_id='q')
    assert not first.acquire('over', 'host', '0', remote=True, memory_budget_mb=200, allow_sharing=True)
    first.mark_uncertain('one')
    assert not first.acquire('uncertain-over', 'host', '0', memory_budget_mb=200, allow_sharing=True)
    assert not second.heartbeat('one')
    second.release('one', terminal=True)
    assert {row['job_id'] for row in first.list()} == {'one','two'}
    first.release('one', terminal=True)
    assert first.acquire('three', 'host', '0', memory_budget_mb=200, allow_sharing=True)


def test_mig_children_are_disjoint_but_whole_parent_blocks_children(tmp_path):
    leases=ResourceLeases(tmp_path/'leases.sqlite');inventory(leases)
    assert leases.acquire('child-a','host','MIG-a')
    assert leases.acquire('child-b','host','MIG-b')
    assert not leases.acquire('parent','host','GPU-a')
    leases.release('child-a',terminal=True);leases.release('child-b',terminal=True)
    assert leases.acquire('parent','host','0')
    assert not leases.acquire('child','host','MIG-a')


def test_sharing_requires_observed_capacity_and_cannot_change_claim(tmp_path):
    leases=ResourceLeases(tmp_path/'leases.sqlite');inventory(leases)
    with pytest.raises(ValueError,match='capacity'):leases.acquire('unknown','elsewhere','0',memory_budget_mb=20,allow_sharing=True)
    assert leases.acquire('owned','host','0',memory_budget_mb=400,allow_sharing=True)
    with pytest.raises(ValueError,match='reservation'):leases.acquire('owned','host','1',memory_budget_mb=400,allow_sharing=True)
    with pytest.raises(ValueError,match='memory'):leases.acquire('large','host','1',memory_budget_mb=2001,allow_sharing=True)


def test_capacity_cannot_be_increased_while_reserved(tmp_path):
    leases=ResourceLeases(tmp_path/'leases.sqlite');inventory(leases)
    assert leases.acquire('owned','host','0',memory_budget_mb=900,allow_sharing=True)
    with pytest.raises(ValueError,match='reservation'):leases.configure_devices('host',[{'selector':'0','uuid':'GPU-a','memory_mb':2000}])


def test_manager_shares_only_the_profile_memory_claim_and_queues_over_capacity(tmp_path,monkeypatch):
    import threading
    from backend.api.routes_training import TrainingJobManager
    from backend.remote.profiles import ComputeProfile
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    manager=TrainingJobManager();host='ssh:host:22'
    manager._leases.configure_devices(host,[{'selector':'0','uuid':'GPU-a','memory_mb':1000}])
    profile=ComputeProfile(id='gpu',name='GPU',ssh_target='host',ssh_port=22,remote_root='/workspace',runtime_kind='python',runtime_value='python3',gpu_selector='0',memory_budget_mb=400,allow_sharing=True)
    release=threading.Event();started=[]
    def runner(record):started.append(record.job_id);release.wait(3);return {'status':'completed'}
    records=[]
    try:
        for index in range(3):
            records.append(manager.start_remote_job(job_id=f'job_{index}',task='ocr',dataset_path=str(tmp_path/'data'),output_dir=str(tmp_path/f'job_{index}'),remote_profile_id='gpu',profile=profile,remote_runner=runner,launch_spec={'preparation':'none','project_id':'own','account_id':'user'}))
        assert records[0].status=='running' and records[1].status=='running'
        assert records[2].status=='queued'
        assert len(manager._leases.list())==2
        assert all(row['memory_budget_mb']==400 and row['project_id']=='own' for row in manager._leases.list())
    finally:
        release.set()
        for record in records:
            if record.thread:record.thread.join(3)


def test_unobserved_mig_is_never_assumed_disjoint_from_a_whole_gpu(tmp_path):
    leases=ResourceLeases(tmp_path/'leases.sqlite')
    assert leases.acquire('whole','host','GPU-a')
    assert not leases.acquire('unobserved','host','MIG-a')


def test_probe_observed_uuid_blocks_two_ssh_aliases_for_the_same_gpu(tmp_path):
    leases=ResourceLeases(tmp_path/'leases.sqlite')
    leases.configure_devices('dns',[{'selector':'0','uuid':'GPU-same','memory_mb':1000}])
    leases.configure_devices('ip',[{'selector':'0','uuid':'GPU-same','memory_mb':1000}])
    assert leases.acquire('one','dns','0',remote=True,memory_budget_mb=600,allow_sharing=True)
    assert not leases.acquire('over','ip','0',remote=True,memory_budget_mb=500,allow_sharing=True)
    assert not leases.acquire('exclusive','ip','0',remote=True)
    assert leases.acquire('fits','ip','0',remote=True,memory_budget_mb=400,allow_sharing=True)
