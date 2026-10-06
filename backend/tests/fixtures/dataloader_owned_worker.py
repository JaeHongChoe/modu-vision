"""Finite-data spawn worker kept alive for process-tree cancellation controls."""
import json
import time
import torch

def main():
    torch.set_num_threads(1)
    loader=torch.utils.data.DataLoader(torch.utils.data.TensorDataset(torch.arange(8)),
        batch_size=2,num_workers=2,multiprocessing_context='spawn',persistent_workers=True,prefetch_factor=1)
    iterator=iter(loader)
    values=next(iterator)[0].tolist()
    print(json.dumps({'ready':True,'values':values,'workers':[p.pid for p in iterator._workers]}),flush=True)
    time.sleep(180)

if __name__=='__main__':
    torch.multiprocessing.freeze_support()
    main()
