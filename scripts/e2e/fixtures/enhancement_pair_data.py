"""Owned explicit denoising pairs and split; no model/result is synthesized."""
import hashlib,json,sys
from pathlib import Path
import numpy as np
from PIL import Image
def seed(root,project=None):
    root=Path(root).resolve();source=root/'enhancement-inputs';targets=root/'enhancement-targets'
    if project is not None:
        project_root=Path(project['project_dir']).resolve();assert project_root.is_relative_to(root)
        split=Path(project['dataset_dir'])/'splits'/(hashlib.sha256(str(source).encode()).hexdigest()+'.json')
        split.parent.mkdir(parents=True,exist_ok=True);assignments={p.relative_to(source).as_posix():p.relative_to(source).parts[0] for p in source.rglob('*.png')}
        split.write_text(json.dumps({'folder_path':str(source),'assignments':assignments,'seed':107}));return {'split_path':str(split),'assignments':assignments}
    rows=[];files=[]
    for i,part in enumerate(('train','val','test')):
        for j,(label,value) in enumerate((('OK',210),('NG',45))):
            relative=f'{part}/{label}/{i}_{j}.png'
            pixels=np.full((48,64,3),value,np.uint8);pixels[12:20,14:22]=20+j*20
            pixels[i+1,1]=[i+5,j+8,value]
            noise=np.random.default_rng(107+i*2+j).normal(0,12,pixels.shape)
            noisy=np.clip(pixels.astype(float)+noise,0,255).astype(np.uint8)
            for folder,values in ((source,noisy),(targets,pixels)):
                file=folder/relative;file.parent.mkdir(parents=True,exist_ok=True);Image.fromarray(values).save(file)
                files.append({'path':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
            rows.append({'input':relative,'target':relative,'split':part})
    return {'source':str(source),'targets':str(targets),'rows':rows,'files':files,'scope':'Synthetic explicit pixel-aligned pairs; no representative defect/quality approval'}
if __name__=='__main__':print(json.dumps(seed(sys.argv[1],json.loads(sys.argv[2]) if len(sys.argv)>2 else None)))
