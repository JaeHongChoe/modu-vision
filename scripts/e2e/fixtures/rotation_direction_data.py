"""Owned asymmetric synthetic direction labels; no representative quality claim."""
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw

root = Path(sys.argv[1]).resolve() / 'rotation-direction-source'
root.mkdir()  # Never replace a previous fixture.
rows = []
for split, copies in [('train', 2), ('val', 1), ('test', 1)]:
    for angle in (0, 90, 180, -90):
        for copy in range(copies):
            image = Image.new('RGB', (32, 32), 'black')
            ImageDraw.Draw(image).polygon([(16, 2), (5, 25), (16, 19), (24, 25)], fill='white')
            image = image.rotate(-angle)
            image.putpixel((0, 0), (len(rows) + 1, 0, 0))
            path = root / f'{split}_{angle}_{copy}.png'
            image.save(path)
            rows.append({'image': path.name, 'correction_deg': angle, 'split': split,
                         'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
print(json.dumps({'source': str(root), 'rows': rows}))
