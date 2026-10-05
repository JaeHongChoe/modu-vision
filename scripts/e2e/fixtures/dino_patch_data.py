"""Owned tiny region-labeled originals; authentic weights are read, never downloaded."""
import hashlib,json,sys
from pathlib import Path
import numpy as np
from PIL import Image
root=Path(sys.argv[1]).resolve();source=root/'dino-patch-originals';files=[]
for i,part in enumerate(('train','val','test')):
    for j in range(2):
        file=source/part/f'{i}_{j}.png';file.parent.mkdir(parents=True,exist_ok=True)
        pixels=np.random.default_rng(108+i*2+j).integers(185,220,(29,35,3),dtype=np.uint8)
        pixels[0:8,0:8]=[30,10+j,10+i]
        Image.fromarray(pixels).save(file)
        label={'imagePath':file.name,'imageWidth':35,'imageHeight':29,'shapes':[{'label':'chip','shape_type':'rectangle','points':[[0,0],[8,8]]}]}
        label_file=file.with_suffix('.json');label_file.write_text(json.dumps(label))
        for member in (file,label_file):files.append({'path':str(member),'sha256':hashlib.sha256(member.read_bytes()).hexdigest()})
weights=Path(sys.argv[2]).expanduser().absolute()
assert weights.is_file()
print(json.dumps({'source':str(source),'files':files,'weights':str(weights),'weights_sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),
    'scope':'Synthetic original-coordinate reviewed rectangles; authentic existing timm DINOv3 cache, no representative quality approval'}))
