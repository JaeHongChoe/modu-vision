"""Distinct owned held-out pixels for functional improvement, not quality proof."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import numpy as np
from PIL import Image
from dino_classification_data import seed

root=Path(sys.argv[1]).resolve()
run=Path(os.environ.get('MV_E2E_RUN_DIR','missing')).resolve()
if root.parent!=run/'ws' or not re.fullmatch(r'\d+-[a-f0-9]{8}-r\d+',root.name):
    raise ValueError('Only an owned E2E workspace can host this functional data')
fixture=seed(root)
source=Path(fixture['source'])
for label_index,label in enumerate(('OK','scratch','stain')):
    for index in range(1,8):
        pixels=np.random.default_rng(7300+label_index*100+index).integers(100,150,(48,64,3),dtype=np.uint8)
        if label=='scratch':pixels[8:39,28:31]=20
        if label=='stain':pixels[18:35,20:45]=40
        path=source/'test'/label/f'test_{label_index}_{index}.png'
        Image.fromarray(pixels).save(path)
        fixture['files'].append({'path':str(path),'split':'test','label':label,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
fixture.update(heldout_count=24,distinct_heldout_hashes=len({row['sha256'] for row in fixture['files'] if row['split']=='test'}),
    manufacturing_quality_accepted=False,physical_delivery_accepted=False)
assert fixture['distinct_heldout_hashes']==24
print(json.dumps(fixture))
