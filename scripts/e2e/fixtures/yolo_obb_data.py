"""Owned disjoint original-pixel OBB truth; explicit official local weights only."""
import hashlib, json, os, sys
from pathlib import Path
import cv2
import numpy as np
from PIL import Image

root = Path(sys.argv[1]).resolve(); source = root / 'obb-originals'; source.mkdir(exist_ok=True)
weights = Path(os.environ['MV_E2E_OBB_WEIGHTS']).expanduser().absolute()
digest = hashlib.sha256(weights.read_bytes()).hexdigest()
assert not weights.is_symlink() and digest == '6f51c78197aacda4a33be77294065a9001675fb893f56227a179731b53dbd2b0'
rows=[]; files=[]; tsv=[]
for split_index, split in enumerate(('train','val','test')):
    for background in (False,True):
        name=f'{split}_{"empty" if background else "objects"}.png'; path=source/name
        rng=np.random.default_rng(800+split_index*2+int(background)); rgb=rng.integers(70,95,(128,128,3),dtype=np.uint8)
        objects=[]
        boxes=[(16+16*x,22+20*y,12,6,-20 if (x+y)%2 else 25) for y in range(5) for x in range(7)] if split=='train' else [(38,42,30,12,35),(87,80,24,10,-25)]
        if not background:
            for index,(cx,cy,w,h,angle) in enumerate(boxes):
                label='part_a' if index%2==0 else 'part_b'; box={'cx':cx,'cy':cy,'width':w,'height':h,'angle_deg':angle}
                points=cv2.boxPoints(((cx,cy),(w,h),angle));cv2.fillConvexPoly(rgb,np.rint(points).astype(np.int32),(180,40,35) if index%2 else (35,175,200));objects.append({'label':label,'box':box})
                tsv.append(f'{name}\t{label}\t{cx},{cy},{w},{h},{angle}\t{split}')
        else: tsv.append(f'{name}\t\t\t{split}')
        Image.fromarray(rgb).save(path); sha=hashlib.sha256(path.read_bytes()).hexdigest()
        annotation=path.with_suffix('.json')
        shapes=[{'label':row['label'],'shape_type':'polygon','points':cv2.boxPoints(((row['box']['cx'],row['box']['cy']),(row['box']['width'],row['box']['height']),row['box']['angle_deg'])).tolist(),'flags':{}} for row in objects]
        annotation.write_text(json.dumps({'version':'5.0','imagePath':name,'imageWidth':128,'imageHeight':128,'imageData':None,'flags':{},'shapes':shapes}))
        rows.append({'image':name,'split':split,'objects':objects});files.append({'path':str(path),'sha256':sha,'split':split,'background':background,'truth':objects,'annotation':str(annotation),'annotation_sha256':hashlib.sha256(annotation.read_bytes()).hexdigest()})
assert len({r['sha256'] for r in files})==6
print(json.dumps({'source':str(source),'rows':rows,'tsv':'\n'.join(tsv),'files':files,'weights':str(weights),'weights_sha256':digest,'quality_approval':False}))
