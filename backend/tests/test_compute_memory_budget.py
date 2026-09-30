import pytest
from backend.remote import worker


def test_worker_enforces_the_reserved_cuda_memory_budget(monkeypatch):
    import torch
    calls=[]
    monkeypatch.setattr(torch.cuda,'is_available',lambda:True)
    monkeypatch.setattr(torch.cuda,'device_count',lambda:1)
    monkeypatch.setattr(torch.cuda,'get_device_properties',lambda index:type('Properties',(),{'total_memory':1024*1024*1000})())
    monkeypatch.setattr(torch.cuda,'set_per_process_memory_fraction',lambda value,index:calls.append((value,index)))
    worker.apply_memory_budget({'device':'cuda:0','resources':{'memory_budget_mb':400,'allow_sharing':True}})
    assert calls==[(.4,0)]
    with pytest.raises(ValueError,match='observed'):worker.apply_memory_budget({'device':'cuda:0','resources':{'memory_budget_mb':1001,'allow_sharing':True}})
    with pytest.raises(ValueError,match='resources'):worker.apply_memory_budget({'device':'cuda:0','resources':{'memory_budget_mb':400,'unexpected':True}})


def test_cuda_sharing_is_not_silently_claimed_for_cpu_workers():
    with pytest.raises(ValueError,match='CUDA'):worker.apply_memory_budget({'device':'cpu','resources':{'memory_budget_mb':400,'allow_sharing':True}})
