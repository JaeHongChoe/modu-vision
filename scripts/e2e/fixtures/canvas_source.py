"""Owned original image solely for UI/coordinate QA; no model-quality labels."""
import hashlib,json,sys
from pathlib import Path
from PIL import Image,ImageDraw
root=Path(sys.argv[1])/'canvas-originals';(root/'ok').mkdir(parents=True)
f=root/'ok'/'coordinate-512x256.png';image=Image.new('RGB',(512,256),(80,100,120));draw=ImageDraw.Draw(image);draw.rectangle((64,32,256,160),fill=(200,150,100));image.save(f)
print(json.dumps({'source':str(root),'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'size':[512,256],'scope':'UI geometry fixture; no training or quality approval'}))
