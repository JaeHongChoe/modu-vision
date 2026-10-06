"""Owned disjoint normals, defects and masks; model-quality approval is separate."""
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def seed(root, project=None):
    root = Path(root).resolve(); source = root / 'anomaly-calibration-inputs'
    if project is not None:
        assert Path(project['project_dir']).resolve().is_relative_to(root)
        split = Path(project['dataset_dir']) / 'splits' / (hashlib.sha256(str(source).encode()).hexdigest() + '.json')
        assignments = {p.relative_to(source).as_posix(): p.relative_to(source).parts[0]
                       for part in ('train', 'val', 'test') for p in (source / part).rglob('*.png')}
        split.parent.mkdir(parents=True, exist_ok=True)
        split.write_text(json.dumps({'folder_path': str(source), 'assignments': assignments, 'seed': 115}))
        return {'split_path': str(split), 'assignments': assignments}
    sha = lambda file: hashlib.sha256(file.read_bytes()).hexdigest()
    files = []
    for part, labels in [('train', ['good', 'good']), ('val', ['good', 'good', 'defect']),
                         ('test', ['good', 'defect', 'defect'])]:
        for i, label in enumerate(labels):
            pixels = np.random.default_rng(115 + len(files)).integers(115, 145, (64, 64, 3), dtype=np.uint8)
            mask = np.zeros((64, 64), dtype=np.uint8)
            if label == 'defect':
                mask[12 + i:30 + i, 20:37] = 255; pixels[mask > 0] = [15, 20, 25]
            path = source / part / label / f'{part}_{label}_{i}.png'
            path.parent.mkdir(parents=True, exist_ok=True); Image.fromarray(pixels).save(path)
            row = {'path': str(path), 'split': part, 'label': label, 'sha256': sha(path)}
            if label == 'defect':
                truth = source / 'ground_truth' / 'defect' / f'{path.stem}_mask.png'
                truth.parent.mkdir(parents=True, exist_ok=True); Image.fromarray(mask).save(truth)
                row.update(mask=str(truth), mask_sha256=sha(truth))
            files.append(row)
    dino = Path(os.environ['MV_E2E_DINO_WEIGHTS']).expanduser().absolute()
    import torch
    from torchvision.models import ResNet18_Weights
    url = ResNet18_Weights.DEFAULT.url
    resnet = Path(torch.hub.get_dir()) / 'checkpoints' / url.rsplit('/', 1)[-1]
    assert sha(resnet).startswith(resnet.stem.rsplit('-', 1)[-1])
    return {'source': str(source), 'files': files, 'dino_weights': str(dino), 'dino_sha256': sha(dino),
            'resnet_weights': str(resnet), 'resnet_sha256': sha(resnet), 'resnet_official_url': url,
            'owned_synthetic_sources': True, 'quality_approved': False}


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    print(json.dumps(seed(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else None)))
