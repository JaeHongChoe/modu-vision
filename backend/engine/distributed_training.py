"""Torch distributed primitives shared by the actual supervised trainer.

Every rank participates at matching batch/epoch cancellation boundaries. Worker
process failures are handled by torchrun, which terminates the other ranks.
"""
from dataclasses import dataclass
from datetime import timedelta
import os
import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel
from torch.utils.data import DataLoader
from torch.utils.data.distributed import DistributedSampler


@dataclass(frozen=True)
class DistributedContext:
    rank:int=0
    world_size:int=1
    local_rank:int=0
    device:str='cpu'
    backend:str|None=None


def current_distributed_context():
    if not dist.is_available() or not dist.is_initialized():return DistributedContext()
    rank=dist.get_rank();local=int(os.environ.get('LOCAL_RANK',rank))
    backend=dist.get_backend()
    return DistributedContext(rank,dist.get_world_size(),local,f'cuda:{local}' if backend=='nccl' else 'cpu',backend)


def initialize_distributed(device='cpu'):
    world=int(os.environ.get('WORLD_SIZE','1'));rank=int(os.environ.get('RANK','0'));local=int(os.environ.get('LOCAL_RANK','0'))
    if world<2:return DistributedContext(device=str(device))
    if not dist.is_available():raise ValueError('PyTorch distributed support is unavailable')
    cuda=str(device).startswith('cuda')
    if cuda:
        if not torch.cuda.is_available() or local>=torch.cuda.device_count():raise ValueError('Distributed CUDA devices are unavailable')
        torch.cuda.set_device(local)
    backend='nccl' if cuda else 'gloo'
    if not dist.is_initialized():dist.init_process_group(backend=backend,rank=rank,world_size=world,timeout=timedelta(seconds=45))
    return current_distributed_context()


def is_primary():return current_distributed_context().rank==0


def wrap_model(model):
    context=current_distributed_context()
    if context.world_size==1:return model
    kwargs={'device_ids':[context.local_rank],'output_device':context.local_rank} if context.backend=='nccl' else {}
    return DistributedDataParallel(model,**kwargs)


def unwrap_model(model):return model.module if isinstance(model,DistributedDataParallel) else model


def distributed_loader(dataset,*,batch_size,shuffle=False,partition=True,**kwargs):
    context=current_distributed_context()
    sampler=DistributedSampler(dataset,num_replicas=context.world_size,rank=context.rank,shuffle=shuffle,drop_last=False) if context.world_size>1 and partition else None
    return DataLoader(dataset,batch_size=batch_size,shuffle=shuffle if sampler is None else False,sampler=sampler,**kwargs)


def set_sampler_epoch(loader,epoch):
    if hasattr(loader.sampler,'set_epoch'):loader.sampler.set_epoch(epoch)


def distributed_mean(value,weight=1):
    context=current_distributed_context()
    if context.world_size==1:return float(value)
    values=torch.tensor([float(value)*weight,float(weight)],dtype=torch.float64,device=context.device)
    dist.all_reduce(values,op=dist.ReduceOp.SUM)
    return float(values[0]/values[1]) if values[1] else 0.


def synchronize_cancel(event):
    cancelled=bool(event.is_set()) if hasattr(event,'is_set') else bool(event)
    context=current_distributed_context()
    if context.world_size==1:return cancelled
    flag=torch.tensor(int(cancelled),device=context.device)
    dist.all_reduce(flag,op=dist.ReduceOp.MAX)
    if flag and hasattr(event,'set'):event.set()
    return bool(flag)


def shutdown_distributed():
    if dist.is_available() and dist.is_initialized():dist.destroy_process_group()
