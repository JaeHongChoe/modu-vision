"""Owned RGB controls, explicit disjoint classes/splits and local official CPU weights."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
import numpy as np
from PIL import Image

def seed(root, project=None):
    root=Path(root).resolve();source=root/'gan-adoption-inputs'
    if project is not None:
        assert Path(project['project_dir']).resolve().is_relative_to(root)
        split=Path(project['dataset_dir'])/'splits'/(hashlib.sha256(str(source).encode()).hexdigest()+'.json')
        assignments={p.relative_to(source).as_posix():p.relative_to(source).parts[0] for p in source.rglob('*.png')}
        split.parent.mkdir(parents=True,exist_ok=True);split.write_text(json.dumps({'folder_path':str(source),'assignments':assignments,'seed':117}))
        return {'split_path':str(split),'assignments':assignments}
    weights=Path(os.environ['MV_E2E_GAN_CLASSIFIER_WEIGHTS']).expanduser().absolute()
    digest=hashlib.sha256(weights.read_bytes()).hexdigest()
    assert weights.name=='efficientnet_b0_rwightman-7f5810bc.pth' and digest=='7f5810bc96def8f7552d5b7e68d53c4786f81167d28291b21c0d90e1fca14934'
    owned_cache=root/'home/.cache/torch/hub/checkpoints'/weights.name;owned_cache.parent.mkdir(parents=True,exist_ok=True)
    if sys.platform=='darwin':subprocess.run(['/bin/cp','-c',str(weights),str(owned_cache)],check=True)
    else:owned_cache.write_bytes(weights.read_bytes())
    assert hashlib.sha256(owned_cache.read_bytes()).hexdigest()==digest
    files=[];rows=[]
    for part,count in (('train',2),('val',1),('test',1)):
        for j,label in enumerate(('OK','scratch','stain')):
            for i in range(count):
                pixels=np.random.default_rng(117+len(files)).integers(100,150,(48,64,3),dtype=np.uint8)
                if label=='scratch':pixels[8:39,28:31]=20
                if label=='stain':pixels[18:35,20:45]=40
                p=source/part/label/f'{part}_{j}_{i}.png';p.parent.mkdir(parents=True,exist_ok=True);Image.fromarray(pixels).save(p)
                files.append({'path':str(p),'split':part,'label':label,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
                if label!='OK':rows.append({'image':p.relative_to(source).as_posix(),'bbox':[8,8,48,40],'split':part,'label':label})
    return {'source':str(source),'files':files,'rows':rows,'weights':str(weights),'owned_cache':str(owned_cache),'weights_sha256':digest,'synthetic_only':True,'quality_approved':False}

if __name__=='__main__':print(json.dumps(seed(sys.argv[1],json.loads(sys.argv[2]) if len(sys.argv)>2 else None)))
