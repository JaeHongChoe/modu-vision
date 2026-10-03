"""Launch one model on multiple owned processes; terminate the whole group."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import socket


SUPPORTED_TASKS={'classification','patch_classification','segmentation'}


def validate_distributed_request(task,device,processes,*,available_cuda=None):
    if task not in SUPPORTED_TASKS:raise ValueError('Distributed training is not supported by this task adapter')
    if type(processes) is not int or not 2<=processes<=16:raise ValueError('Distributed training requires 2 to 16 processes')
    if str(device).startswith('cuda'):
        if available_cuda is None:
            import torch
            available_cuda=torch.cuda.device_count()
        if available_cuda<processes:raise ValueError('The requested CUDA device count is unavailable')
    elif str(device)!='cpu':raise ValueError('Distributed training supports CUDA or CPU gloo')


def _terminate_group(process):
    if process.poll() is not None:return
    try:
        if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
        else:process.terminate()
    except ProcessLookupError:return
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
        else:process.kill()
        process.wait(timeout=5)


def launch_distributed(spec_path,*,cancel_event=None,status_writer=None):
    spec_path=Path(spec_path).resolve();spec=json.loads(spec_path.read_text(encoding='utf-8'))
    processes=(spec.get('distributed') or {}).get('processes',0)
    validate_distributed_request(spec['task'],spec.get('device') or 'cuda',processes)
    output=spec_path.parent/'outputs';output.mkdir(exist_ok=True)
    log_path=spec_path.parent/'distributed.log'
    with socket.socket() as socket_handle:
        socket_handle.bind(('127.0.0.1',0));port=socket_handle.getsockname()[1]
    command=[sys.executable,'-m','torch.distributed.run','--rdzv_backend=static','--master_addr=127.0.0.1',f'--master_port={port}',f'--nproc_per_node={processes}',
             '-m','backend.remote.distributed_worker','--spec',str(spec_path)]
    with log_path.open('wb') as log:
        from backend.engine.process_isolation import session_isolation
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,**session_isolation(),
                                 env={**os.environ,'OMP_NUM_THREADS':os.environ.get('OMP_NUM_THREADS','1')})
        try:
            while process.poll() is None:
                progress=spec_path.parent/'distributed_progress'/'status.json'
                if status_writer is not None and progress.is_file():
                    try:
                        values=json.loads(progress.read_text(encoding='utf-8'))
                        status_writer.update(**{key:values[key] for key in ('current_epoch','total_epochs','current_step','total_steps','train_loss','val_loss','loss_history','metrics') if key in values})
                    except (OSError,ValueError):pass
                if (cancel_event is not None and cancel_event.is_set()) or (spec_path.parent/'cancel').exists():
                    _terminate_group(process);return {'status':'aborted'}
                time.sleep(.05)
            result_path=output/'distributed_result.json'
            if process.returncode!=0:raise RuntimeError(f'Distributed worker group failed with exit {process.returncode}; inspect distributed.log')
            if not result_path.is_file():raise RuntimeError('Distributed rank zero did not publish a training result')
            result=json.loads(result_path.read_text(encoding='utf-8'));result['distributed']={'processes':processes,'backend':'nccl' if str(spec.get('device')).startswith('cuda') else 'gloo'}
            return result
        finally:_terminate_group(process)
