"""Owned three-class pixels with disjoint split identities; no quality claim."""
import hashlib,json,os,sys
from pathlib import Path
import numpy as np
from PIL import Image

def seed(root,project=None):
    root=Path(root).resolve();source=root/'dino-classification-inputs'
    if project is not None:
        assert Path(project['project_dir']).resolve().is_relative_to(root)
        split=Path(project['dataset_dir'])/'splits'/(hashlib.sha256(str(source).encode()).hexdigest()+'.json')
        assignments={p.relative_to(source).as_posix():p.relative_to(source).parts[0] for p in source.rglob('*.png')}
        split.parent.mkdir(parents=True,exist_ok=True);split.write_text(json.dumps({'folder_path':str(source),'assignments':assignments,'seed':110}))
        return {'split_path':str(split),'assignments':assignments}
    files=[]
    for part,count in (('train',2),('val',1),('test',1)):
        for j,label in enumerate(('OK','scratch','stain')):
            for i in range(count):
                pixels=np.random.default_rng(110+len(files)).integers(100,150,(48,64,3),dtype=np.uint8)
                if label=='scratch':pixels[8:39,28:31]=20
                if label=='stain':pixels[18:35,20:45]=40
                p=source/part/label/f'{part}_{j}_{i}.png';p.parent.mkdir(parents=True,exist_ok=True);Image.fromarray(pixels).save(p)
                files.append({'path':str(p),'split':part,'label':label,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    weights=Path(os.environ['MV_E2E_DINO_WEIGHTS']).expanduser().absolute()
    from backend.engine.model_backbones import checkpoint_sha256
    return {'source':str(source),'files':files,'weights':str(weights),'weights_sha256':checkpoint_sha256(weights),'synthetic_only':True,'quality_approved':False}

if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    print(json.dumps(seed(sys.argv[1],json.loads(sys.argv[2]) if len(sys.argv)>2 else None)))
