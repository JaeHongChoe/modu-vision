"""Bounded CUDA exact-resume control using production checkpoint code.

Three tiny stochastic optimizer steps, not a model-quality experiment, actual
Studio training job, DDP, or mid-epoch continuation. No downloads or providers.
Run only on an explicitly reserved, otherwise idle CUDA device.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sys
from types import SimpleNamespace

import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from backend.engine.training_resume import save_training_state,restore_training_state,backend_numeric_flags


def seed():
    random.seed(83); np.random.seed(83); torch.manual_seed(83); torch.cuda.manual_seed_all(83)


def components():
    model=torch.nn.Sequential(torch.nn.Linear(3,5),torch.nn.Dropout(.3),torch.nn.Linear(5,2)).cuda()
    optimizer=torch.optim.AdamW(model.parameters(),lr=.03)
    scheduler=torch.optim.lr_scheduler.StepLR(optimizer,step_size=1,gamma=.8)
    scaler=torch.amp.GradScaler('cuda',init_scale=16)
    return model,optimizer,scheduler,scaler,SimpleNamespace(completed=0)


def step(model,optimizer,scheduler,scaler,early):
    optimizer.zero_grad()
    with torch.autocast('cuda',dtype=torch.float16):
        loss=model(torch.randn(4,3,device='cuda')).square().mean()*(random.random()+np.random.random())
    scaler.scale(loss).backward(); scaler.step(optimizer); scaler.update(); scheduler.step(); early.completed+=1
    return float(loss.detach())


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    # Bound host BLAS/PyTorch pools before the CUDA driver needs helper threads
    # in a process-limited worker container.
    torch.set_num_threads(1)
    if not torch.cuda.is_available() or torch.cuda.device_count()!=1:
        raise ValueError('One explicitly selected CUDA device is required; no CPU fallback')
    if os.environ.get('CUBLAS_WORKSPACE_CONFIG')!=':4096:8': raise ValueError('Set deterministic CUDA workspace before starting the process')
    args.output.mkdir(parents=True,exist_ok=False)
    torch.use_deterministic_algorithms(True); torch.backends.cudnn.benchmark=False
    torch.backends.cudnn.allow_tf32=False; torch.backends.cuda.matmul.allow_tf32=False
    seed(); original=components(); step(*original); step(*original)
    identity={'task':'classification','fixture':'tiny_stochastic_cuda_control','device':'cuda:0',
              'torch_version':str(torch.__version__),'backend_flags':backend_numeric_flags(),'steps':3}
    checkpoint=args.output/'latest_training_state.pt'
    save_training_state(checkpoint,*original[:4],identity=identity,next_epoch=2,global_step=2,early_stopping=original[4])
    checkpoint_sha=hashlib.sha256(checkpoint.read_bytes()).hexdigest(); expected=step(*original)
    seed(); restored=components()
    state=restore_training_state(checkpoint,*restored[:4],identity=identity,early_stopping=restored[4]); actual=step(*restored)
    if actual!=expected: raise ValueError('Restored next stochastic loss differs')
    for name,value in original[0].state_dict().items(): torch.testing.assert_close(restored[0].state_dict()[name],value,rtol=0,atol=0)
    for before,after in zip(original[1].state.values(),restored[1].state.values()):
        for name in before: torch.testing.assert_close(after[name],before[name],rtol=0,atol=0)
    if original[2].state_dict()!=restored[2].state_dict() or original[3].state_dict()!=restored[3].state_dict() or vars(original[4])!=vars(restored[4]):
        raise ValueError('Scheduler, AMP or stopping state differs')
    refused=False
    try: restore_training_state(checkpoint,*restored[:4],identity={**identity,'steps':4},early_stopping=restored[4])
    except ValueError: refused=True
    if not refused or hashlib.sha256(checkpoint.read_bytes()).hexdigest()!=checkpoint_sha: raise ValueError('Changed identity accepted or original checkpoint changed')
    props=torch.cuda.get_device_properties(0)
    receipt={'schema_version':1,'kind':'cuda_exact_resume_component_control','actual_cuda':True,
             'physical_gpu_uuid':str(getattr(props,'uuid','unavailable')),'device_name':props.name,'logical_index':0,
             'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'cuda_visible_devices':os.environ.get('CUDA_VISIBLE_DEVICES'),
             'nvidia_visible_devices':os.environ.get('NVIDIA_VISIBLE_DEVICES'),'torch_version':str(torch.__version__),
             'expected_next_loss':expected,'resumed_next_loss':actual,'model_optimizer_amp_rng_exact':True,
             'resume':{k:v for k,v in state.items() if k!='best_model_payload'},'identity_change_refused':True,
             'source_files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/engine/training_resume.py',Path(__file__)]},
             'checkpoint_sha256':checkpoint_sha,'checkpoint_unchanged':True,'quality_approved':False,
             'studio_job_verified':False,'ddp_verified':False,'mid_epoch_verified':False}
    with (args.output/'receipt.json').open('x',encoding='utf-8') as out:
        json.dump(receipt,out,indent=2); out.flush(); os.fsync(out.fileno())
    print(json.dumps(receipt))


if __name__=='__main__': main()
