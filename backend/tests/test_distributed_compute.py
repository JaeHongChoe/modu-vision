"""Real two-process gloo verifies a single model's synchronized gradients."""
import json
import os
from pathlib import Path
import subprocess
import sys
import socket


def test_distributed_helpers_partition_and_synchronize_real_optimizer_steps(tmp_path):
    script=tmp_path/'train.py'
    script.write_text('''
import json,os,torch
from pathlib import Path
from torch.utils.data import TensorDataset
from backend.engine.distributed_training import initialize_distributed,distributed_loader,wrap_model,unwrap_model,distributed_mean,synchronize_cancel,set_sampler_epoch,shutdown_distributed
ctx=initialize_distributed('cpu')
torch.manual_seed(11)
model=wrap_model(torch.nn.Linear(1,1,bias=False))
dataset=TensorDataset(torch.tensor([[1.],[2.],[3.],[4.]]),torch.tensor([[2.],[4.],[6.],[8.]]))
loader=distributed_loader(dataset,batch_size=2,shuffle=False,num_workers=0)
set_sampler_epoch(loader,0)
initial=unwrap_model(model).weight.detach().clone()
optimizer=torch.optim.SGD(model.parameters(),lr=.01)
seen=[]
for x,y in loader:
    seen+=x.flatten().tolist();optimizer.zero_grad();loss=((model(x)-y)**2).mean();loss.backward();optimizer.step()
weight=float(unwrap_model(model).weight)
assert abs(weight-float(initial+.01*(2-initial)*15))<1e-5
assert synchronize_cancel(ctx.rank==1)
mean=distributed_mean(float(ctx.rank+1))
Path(os.environ['RESULT_DIR'],str(ctx.rank)+'.json').write_text(json.dumps({'weight':weight,'seen':seen,'mean':mean,'world_size':ctx.world_size}))
shutdown_distributed()
''')
    env={**os.environ,'RESULT_DIR':str(tmp_path),'PYTHONPATH':str(Path(__file__).resolve().parents[2]),'OMP_NUM_THREADS':'1','GLOO_SOCKET_IFNAME':'lo0'}
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    result=subprocess.run([sys.executable,'-m','torch.distributed.run','--rdzv_backend=static','--master_addr=127.0.0.1',f'--master_port={port}','--nproc_per_node=2','--run_path',str(script)],
                          env=env,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    rows=[json.loads((tmp_path/f'{rank}.json').read_text()) for rank in range(2)]
    assert rows[0]['weight']==rows[1]['weight']
    assert sorted(rows[0]['seen']+rows[1]['seen'])==[1,2,3,4]
    assert all(row['mean']==1.5 and row['world_size']==2 for row in rows)


def test_distributed_launcher_rejects_unsupported_hardware_without_launch(tmp_path):
    from backend.remote.distributed import validate_distributed_request
    import pytest
    with pytest.raises(ValueError,match='CUDA'):validate_distributed_request('classification','cuda',2,available_cuda=0)
    with pytest.raises(ValueError,match='task'):validate_distributed_request('defect_gan','cpu',2,available_cuda=0)
    with pytest.raises(ValueError,match='process'):validate_distributed_request('classification','cpu',1,available_cuda=0)
