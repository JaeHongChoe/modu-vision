import json,hashlib
from pathlib import Path
import numpy as np
import torch
from PIL import Image
from backend.engine.defect_gan import write_defect_gan_manifest,train_defect_gan,generate_defect_candidates


def test_heldout_gan_evaluation_and_verified_generation_package(tmp_path:Path):
    from backend.engine.defect_gan import evaluate_defect_generator
    from backend.engine.gan_package_runtime import build_generator_package,run_generator_package
    rows=[]
    for index,split in enumerate(('train','train','val','test')):
        image=tmp_path/f'{index}.png';Image.fromarray(np.full((64,64,3),30+index*30,np.uint8)).save(image)
        rows.append({'image':image.name,'bbox':[0,0,64,64],'split':split})
    write_defect_gan_manifest(tmp_path,rows);torch.set_num_threads(2)
    output=tmp_path/'model';train_defect_gan(tmp_path,output,epochs=1,batch_size=2,base_channels=8)
    metrics=evaluate_defect_generator(output/'best_model.pt',tmp_path,split='test',count=2)
    assert metrics['real_sample_count']==1
    assert metrics['quality_status']=='unvalidated'
    package=build_generator_package(output/'best_model.pt',tmp_path/'package')
    local=generate_defect_candidates(output/'best_model.pt',tmp_path/'local',count=2,seed=7)
    import subprocess,sys,os
    completed=subprocess.run([sys.executable,str(package/'generate.py'),'--output',str(tmp_path/'packaged'),'--count','2','--seed','7'],cwd=tmp_path,env={**os.environ,'PYTHONPATH':''},capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr
    packaged=json.loads(completed.stdout)
    assert [c['sha256'] for c in local['candidates']]==[c['sha256'] for c in packaged['candidates']]
    assert all(c['status']=='synthetic_unreviewed' for c in packaged['candidates'])
    (package/'best_model.pt').write_bytes(b'tampered')
    import pytest
    with pytest.raises(ValueError,match='checksum'):
        run_generator_package(package,tmp_path/'bad')
