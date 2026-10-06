"""One explicitly generated training input for an owned operations GUI gate."""
import hashlib,json,sys
from pathlib import Path
import numpy as np
from PIL import Image

root=Path(sys.argv[1]).resolve();source=Path(sys.argv[2]).resolve()
assert source.is_relative_to(root) and source.is_dir()
path=source/'train'/'scratch'/'operations_fresh.png'
assert not path.exists()
pixels=np.random.default_rng(20261006).integers(20,230,(48,64,3),dtype=np.uint8)
Image.fromarray(pixels).save(path)
print(json.dumps({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
    'synthetic_only':True,'human_truth_reviewed':False}))
