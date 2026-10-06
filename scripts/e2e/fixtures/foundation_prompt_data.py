"""Owned synthetic prompt images; no user data or downloaded datasets."""
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

source = Path(sys.argv[1]) / 'foundation-inputs'
source.mkdir(exist_ok=False)
files = []
labels = []
for index in range(6):
    image = Image.new('RGB', (256, 192), 'gray')
    draw = ImageDraw.Draw(image)
    draw.rectangle((50 + index, 35, 190 + index, 160), fill='white')
    draw.ellipse((105, 80, 136, 115), fill='black')
    file = source / f'part-{index}.png'
    image.save(file)
    files.append({'path': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    label = file.with_suffix('.json')
    label.write_text(json.dumps({'imageWidth': 256, 'imageHeight': 192, 'shapes': [
        {'label': 'defect', 'shape_type': 'rectangle', 'points': [[50, 35], [190, 160]]},
        {'label': 'background', 'shape_type': 'rectangle', 'points': [[0, 0], [40, 30]]},
    ]}) + '\n')
    labels.append({'path': str(label), 'sha256': hashlib.sha256(label.read_bytes()).hexdigest()})
print(json.dumps({'source': str(source), 'files': files, 'labels': labels}))
