"""The example dataset of the first-run demo (S2-01): synthetic brushed-metal surfaces, clean (OK) or with a dark
scratch or blot (NG), drawn here at run time from a fixed seed. Nothing is downloaded and no image or model file ships
with the app, so the example is free to use and redistribute; it says nothing about a real process.
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any

DEMO_ID = 'surface-scratch-classification'
DEMO_VERSION = 1
DEMO_TASK = 'classification'
DEMO_CLASSES = ('OK', 'NG')
DEMO_SPLITS = {'train': 20, 'val': 6, 'test': 6}  # images per class
DEMO_SIZE = 64
DEMO_SEED = 20261003


def _surface(rng: random.Random, size: int) -> list[list[int]]:
    """A grey brushed-metal surface: horizontal streaks with a little grain (values 0-255)."""
    base = rng.randint(150, 185)
    rows = []
    for _ in range(size):
        streak = rng.randint(-10, 10)
        rows.append([max(0, min(255, base + streak + rng.randint(-4, 4))) for _ in range(size)])
    return rows


def _defect(rng: random.Random, pixels: list[list[int]]) -> None:
    """A dark scratch (a short slanted line) or a dark blot, somewhere inside the surface."""
    size = len(pixels)
    if rng.random() < 0.5:
        x, y = rng.randint(8, size - 24), rng.randint(8, size - 24)
        length, slope = rng.randint(12, 20), rng.choice((-1, 0, 1))
        for step in range(length):
            for width in (0, 1):
                px, py = x + step, y + (step * slope) // 3 + width
                if 0 <= px < size and 0 <= py < size:
                    pixels[py][px] = rng.randint(30, 60)
    else:
        cx, cy, radius = rng.randint(12, size - 12), rng.randint(12, size - 12), rng.randint(4, 7)
        for py in range(cy - radius, cy + radius + 1):
            for px in range(cx - radius, cx + radius + 1):
                if (px - cx) ** 2 + (py - cy) ** 2 <= radius * radius:
                    pixels[py][px] = rng.randint(35, 65)


def _write_png(path: Path, pixels: list[list[int]]) -> str:
    from PIL import Image
    size = len(pixels)
    image = Image.new('L', (size, size))
    image.putdata([value for row in pixels for value in row])
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert('RGB').save(path, format='PNG')
    # The pixel hash identifies the image whatever the PNG encoder writes.
    return hashlib.sha256(bytes(value for row in pixels for value in row)).hexdigest()


def write_demo_dataset(root: Path | str) -> dict[str, Any]:
    """Write the example dataset under ``root`` (<split>/<class>/<name>.png); the same seed always draws the same pixels.
    Returns its manifest, which the caller keeps outside the dataset folder (a JSON file there would be read as a label
    file by the importer)."""
    root = Path(root)
    rng = random.Random(DEMO_SEED)
    images = []
    for split, count in DEMO_SPLITS.items():
        for label in DEMO_CLASSES:
            for index in range(count):
                pixels = _surface(rng, DEMO_SIZE)
                if label == 'NG':
                    _defect(rng, pixels)
                relative = f'{split}/{label}/{label.lower()}_{split}_{index:02d}.png'
                images.append({'path': relative, 'split': split, 'label': label,
                               'pixel_sha256': _write_png(root / relative, pixels)})
    manifest = {'demo_id': DEMO_ID, 'version': DEMO_VERSION, 'task': DEMO_TASK, 'classes': list(DEMO_CLASSES),
                'seed': DEMO_SEED, 'image_size': DEMO_SIZE, 'synthetic': True, 'images': images}
    return manifest
