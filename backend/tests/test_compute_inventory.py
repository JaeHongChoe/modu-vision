import pytest
from backend.remote.profiles import ComputeProfile


def profile(**values):
    return ComputeProfile(id='host',name='Host',ssh_target='operator@host',ssh_port=22,remote_root='/workspace/vision',runtime_kind='python',runtime_value='python3',gpu_selector='0',**values)


def test_profile_declares_shared_memory_and_separate_distributed_mode():
    value=profile(memory_budget_mb=512,allow_sharing=True)
    assert value.memory_budget_mb==512
    with pytest.raises(ValueError,match='memory'):profile(allow_sharing=True)
    with pytest.raises(ValueError,match='sharing'):profile(memory_budget_mb=512,allow_sharing=True,distributed_processes=2)
    with pytest.raises(ValueError,match='GPU'):profile(distributed_processes=2)


def test_inventory_reports_real_cpu_without_invented_gpu():
    from backend.engine.compute_inventory import device_inventory
    result=device_inventory()
    assert result['cpu']['available']
    assert isinstance(result['devices'],list)
    assert 'mig_supported' in result
    for row in result['devices']:
        assert row['uuid'] and row['memory_mb']>0
