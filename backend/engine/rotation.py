"""Supervised upright correction with circular angle truth and native output geometry."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path, PurePosixPath
import shutil

import cv2
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset
from PIL import Image

ARCHITECTURE = 'small_cnn_angle_v1'
MANIFEST_NAME = 'rotation.json'


def _atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    temporary.replace(path)


def _hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class RotationSample:
    image: str
    image_path: Path
    correction_deg: float
    split: str
    source_sha256: str


@dataclass(frozen=True)
class RotationManifest:
    root: Path
    samples: tuple[RotationSample, ...]
    provenance: dict


def _manifest(root, rows, *, require_hash=False):
    root = Path(root).expanduser().resolve()
    if not isinstance(rows, list) or not rows:
        raise ValueError('Rotation requires explicit image and correction angle truth')
    samples = []; path_splits = {}; hash_splits = {}; seen = set()
    for row in rows:
        image = row.get('image') if isinstance(row, dict) else None
        relative = PurePosixPath(image) if isinstance(image, str) else None
        if relative is None or relative.is_absolute() or not relative.parts or any(p in ('.', '..') for p in relative.parts):
            raise ValueError('Rotation image must be a relative dataset path')
        path = root / image
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('Rotation image is missing or outside the dataset')
        angle = row.get('correction_deg'); split = row.get('split')
        if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle) or not -180 <= angle <= 180:
            raise ValueError('Rotation correction angle must be finite in [-180,180] degrees')
        if split not in ('train', 'val', 'test') or image in seen:
            raise ValueError('Rotation needs unique images and explicit train/val/test split')
        digest = _hash(path)
        if (require_hash and not row.get('source_sha256')) or (row.get('source_sha256') and row['source_sha256'] != digest):
            raise ValueError('Rotation source SHA-256 hash changed')
        if image in path_splits and path_splits[image] != split or digest in hash_splits and hash_splits[digest] != split:
            raise ValueError('Rotation source bytes leak across split')
        seen.add(image); path_splits[image] = split; hash_splits[digest] = split
        with Image.open(path) as opened:
            if min(opened.size) < 8: raise ValueError('Rotation source image is too small')
        samples.append(RotationSample(image, path, (float(angle) + 180) % 360 - 180, split, digest))
    canonical = [dict(image=s.image, correction_deg=s.correction_deg, split=s.split, source_sha256=s.source_sha256) for s in samples]
    provenance = {'dataset_sha256': sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                  'source_sha256': {s.image: s.source_sha256 for s in samples},
                  'split_counts': {split: sum(s.split == split for s in samples) for split in ('train', 'val', 'test')},
                  'angle_semantics': 'counterclockwise_upright_correction_degrees_360'}
    return RotationManifest(root, tuple(samples), provenance), canonical


def write_rotation_manifest(root, rows, *, source_dataset_path=None):
    manifest, canonical = _manifest(root, rows)
    path = manifest.root / MANIFEST_NAME
    if path.is_symlink(): raise ValueError('Rotation manifest cannot be linked')
    _atomic(path, {'version': 1, 'samples': canonical,
                   **({'source_dataset_path': str(Path(source_dataset_path).resolve())} if source_dataset_path else {})})
    return path


def load_rotation_manifest(root):
    root = Path(root).expanduser().resolve(); path = root / MANIFEST_NAME
    if path.is_symlink() or not path.is_file(): raise ValueError('Rotation needs an explicit rotation.json angle manifest')
    payload = json.loads(path.read_text())
    if payload.get('version') != 1: raise ValueError('Unsupported Rotation manifest version')
    manifest = _manifest(root, payload.get('samples'), require_hash=True)[0]
    if payload.get('source_dataset_path'):
        from backend.engine.source_aliases import resolve_source_root
        canonical=Path(payload['source_dataset_path']).resolve()
        source=resolve_source_root(canonical)
        for row in manifest.samples:
            original = source / row.image
            if original.is_symlink() or not original.is_file() or not original.resolve().is_relative_to(source) or _hash(original) != row.source_sha256:
                raise ValueError('Rotation original source hash changed')
        manifest.provenance['source_dataset_path'] = str(canonical)
    return manifest


def prepare_rotation_dataset(source_root, output_root, rows):
    """Copy verified native pixels and angle labels into new owned storage."""
    manifest, canonical = _manifest(source_root, rows); output = Path(output_root).resolve()
    if output.exists() or output == manifest.root or output.is_relative_to(manifest.root):
        raise ValueError('Rotation preparation requires a new directory outside the original source')
    output.mkdir(parents=True)
    try:
        for row in manifest.samples:
            destination = output / row.image; destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(row.image_path, destination)
            if _hash(destination) != row.source_sha256: raise ValueError('Rotation source changed during preparation')
        write_rotation_manifest(output, canonical, source_dataset_path=manifest.root)
        return load_rotation_manifest(output)
    except (ValueError, OSError): shutil.rmtree(output); raise


def _tensor(rgb, image_size):
    image = Image.fromarray(rgb).resize((image_size, image_size), Image.Resampling.BILINEAR)
    return torch.from_numpy(np.asarray(image).copy()).permute(2, 0, 1).float() / 255


class RotationDataset(Dataset):
    def __init__(self, manifest, split, image_size):
        self.samples = [s for s in manifest.samples if s.split == split]; self.image_size = image_size
    def __len__(self): return len(self.samples)
    def __getitem__(self, index):
        row = self.samples[index]; raw = row.image_path.read_bytes()
        if sha256(raw).hexdigest() != row.source_sha256: raise ValueError('Rotation source hash changed during training')
        with Image.open(BytesIO(raw)) as image: rgb = np.asarray(image.convert('RGB'))
        radians = math.radians(row.correction_deg)
        return _tensor(rgb, self.image_size), torch.tensor([math.cos(radians), math.sin(radians)], dtype=torch.float32)


class RotationNet(nn.Module):
    """A learned visual 360-degree direction, distinct from an affine operator."""
    def __init__(self, width=16):
        super().__init__()
        self.features = nn.Sequential(nn.Conv2d(3, width, 3, 2, 1), nn.ReLU(),
            nn.Conv2d(width, width * 2, 3, 2, 1), nn.ReLU(),
            nn.Conv2d(width * 2, width * 2, 3, 2, 1), nn.ReLU(), nn.AdaptiveAvgPool2d((4, 4)))
        self.head = nn.Sequential(nn.Flatten(), nn.Linear(width * 32, 64), nn.ReLU(), nn.Linear(64, 2))
    def forward(self, images): return self.head(self.features(images))


def _cancel(event):
    if event is not None and event.is_set(): raise InterruptedError('Rotation training cancelled')


def _metrics(model, dataset, device, batch_size=16, cancel_event=None):
    if not len(dataset): raise ValueError('Rotation evaluation split must be nonempty')
    losses = []; errors = []; predictions = []; model.eval()
    with torch.no_grad():
        for images, truth in DataLoader(dataset, batch_size=batch_size):
            _cancel(cancel_event); outputs = model(images.to(device)).cpu()
            if not torch.isfinite(outputs).all(): raise ValueError('Rotation prediction is not finite')
            losses.extend(((outputs - truth) ** 2).mean(1).tolist())
            angles = torch.rad2deg(torch.atan2(outputs[:, 1], outputs[:, 0]))
            expected = torch.rad2deg(torch.atan2(truth[:, 1], truth[:, 0]))
            errors.extend(torch.abs((angles - expected + 180) % 360 - 180).tolist())
            predictions.extend(angles.tolist())
    return {'loss': float(np.mean(losses)), 'angular_mae_deg': float(np.mean(errors)),
            'within_10_deg': float(np.mean(np.asarray(errors) <= 10)), 'sample_count': len(errors),
            'predictions_deg': predictions}


def train_rotation(dataset_root, output_dir, *, epochs=20, batch_size=8, image_size=64,
                   learning_rate=1e-3, width=16, device='cpu', seed=17, cancel_event=None, on_progress=None, warm_start=None):
    if type(epochs) is not int or not 1 <= epochs <= 500 or type(batch_size) is not int or not 1 <= batch_size <= 128:
        raise ValueError('Rotation epochs and batch size must be positive and bounded')
    if type(image_size) is not int or not 16 <= image_size <= 512 or width not in (8, 16, 32) or not math.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError('Invalid Rotation structure or learning rate')
    manifest = load_rotation_manifest(dataset_root); _cancel(cancel_event)
    train = RotationDataset(manifest, 'train', image_size); val = RotationDataset(manifest, 'val', image_size)
    if not len(train) or not len(val): raise ValueError('Rotation requires nonempty train and validation angle truth')
    from backend.engine.runtime_device import resolve_runtime_device
    target = resolve_runtime_device(device); torch.manual_seed(seed); model = RotationNet(width).to(target)
    output = Path(output_dir).expanduser().resolve(); lineage = {}
    if warm_start is not None:
        from backend.engine.specialized_warm_start import require_new_candidate, load_family_weights, requested_signature
        require_new_candidate(output, warm_start)
        load_family_weights({'model_state_dict': model}, warm_start, requested_signature('rotation', dataset_root, {'width': width, 'image_size': image_size}))
        lineage['warm_start'] = warm_start.lineage()
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    loader = DataLoader(train, batch_size=batch_size, shuffle=True, generator=torch.Generator().manual_seed(seed))
    output.mkdir(parents=True, exist_ok=True); history = []; best = math.inf; best_epoch = 0
    config = dict(epochs=epochs, batch_size=batch_size, image_size=image_size, learning_rate=learning_rate, width=width, seed=seed)
    try:
        for epoch in range(1, epochs + 1):
            _cancel(cancel_event); model.train(); total = 0.
            for batch_index, (images, truth) in enumerate(loader, 1):
                _cancel(cancel_event); optimizer.zero_grad(set_to_none=True)
                loss = nn.functional.mse_loss(model(images.to(target)), truth.to(target))
                if not torch.isfinite(loss): raise ValueError('Rotation training loss is not finite')
                loss.backward(); optimizer.step(); total += float(loss.item()) * len(images)
                if on_progress: on_progress({'epoch': epoch, 'batch': batch_index, 'batches': len(loader), 'loss': float(loss.item())})
            history.append(total / len(train)); metrics = _metrics(model, val, target, batch_size, cancel_event)
            if metrics['loss'] < best:
                best = metrics['loss']; best_epoch = epoch
                payload = dict(task='rotation', version=1, architecture=ARCHITECTURE, width=width, image_size=image_size,
                    angle_semantics=manifest.provenance['angle_semantics'], model_state_dict=model.state_dict(),
                    dataset_provenance=manifest.provenance, training_config=config, validation=metrics, best_epoch=epoch, **lineage)
                temporary = output / 'best_model.pt.tmp'; torch.save(payload, temporary); temporary.replace(output / 'best_model.pt')
            if on_progress: on_progress({'epoch': epoch, 'batch': len(loader), 'batches': len(loader), 'loss': history[-1], 'val_loss': metrics['loss'], 'angular_mae_deg': metrics['angular_mae_deg']})
        _cancel(cancel_event)
        if load_rotation_manifest(dataset_root).provenance != manifest.provenance: raise ValueError('Rotation dataset provenance changed during training')
        metadata = {k: v for k, v in payload.items() if k != 'model_state_dict'}
        metadata.update(best_epoch=best_epoch, best_validation_loss=best, training_loss_history=history, epochs_completed=epochs,
                        checkpoint_sha256=_hash(output / 'best_model.pt'), quality_approved=False)
        _atomic(output / 'model_meta.json', metadata)
        return metadata
    except (InterruptedError, ValueError, OSError, RuntimeError):
        for name in ('best_model.pt', 'best_model.pt.tmp', 'model_meta.json'): (output / name).unlink(missing_ok=True)
        raise


def load_rotation_model(checkpoint, device='cpu'):
    from backend.engine.runtime_device import resolve_runtime_device
    path = Path(checkpoint)
    if path.is_symlink() or not path.is_file(): raise ValueError('Rotation checkpoint is unavailable')
    payload = torch.load(path, map_location='cpu', weights_only=True)
    if payload.get('task') != 'rotation' or payload.get('architecture') != ARCHITECTURE or payload.get('version') != 1 or payload.get('width') not in (8, 16, 32) or type(payload.get('image_size')) is not int or not 16 <= payload['image_size'] <= 512:
        raise ValueError('Invalid Rotation checkpoint architecture or geometry')
    metadata = path.with_name('model_meta.json')
    if metadata.exists() and json.loads(metadata.read_text()).get('checkpoint_sha256') != _hash(path):
        raise ValueError('Rotation checkpoint checksum differs from metadata')
    model = RotationNet(payload['width']); model.load_state_dict(payload['model_state_dict'], strict=True)
    return model.to(resolve_runtime_device(device)).eval(), payload


def evaluate_rotation_checkpoint(checkpoint, dataset_root, *, split='test', device='cpu', batch_size=16, allow_dataset_revision=False):
    if split not in ('val', 'test'): raise ValueError('Rotation evaluation requires heldout val or test')
    model, payload = load_rotation_model(checkpoint, device); manifest = load_rotation_manifest(dataset_root)
    if not allow_dataset_revision and manifest.provenance != payload.get('dataset_provenance'): raise ValueError('Rotation evaluation dataset provenance differs from checkpoint')
    metrics = _metrics(model, RotationDataset(manifest, split, payload['image_size']), next(model.parameters()).device, batch_size)
    return {**metrics, 'split': split, 'checkpoint_sha256': _hash(checkpoint), 'dataset_provenance': manifest.provenance,
            'dataset_sha256': manifest.provenance['dataset_sha256']}


def _aligned(rgb, angle):
    height, width = rgb.shape[:2]
    matrix = cv2.getRotationMatrix2D(((width - 1) / 2, (height - 1) / 2), angle, 1)
    cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
    ow = max(1, int(math.ceil(width * cosine + height * sine - 1e-8)))
    oh = max(1, int(math.ceil(height * cosine + width * sine - 1e-8)))
    matrix[0, 2] += (ow - width) / 2; matrix[1, 2] += (oh - height) / 2
    return cv2.warpAffine(rgb, matrix, (ow, oh), flags=cv2.INTER_LINEAR), np.vstack([matrix, [0, 0, 1]])


def _predict(model, image_size, rgb, device):
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1:
        raise ValueError('Rotation input must be native RGB uint8 H×W×3')
    if rgb.shape[0] * rgb.shape[1] > 100_000_000: raise ValueError('Rotation source exceeds native image work budget')
    with torch.no_grad(): vector = model(_tensor(rgb, image_size).unsqueeze(0).to(device))[0].cpu()
    if not torch.isfinite(vector).all() or float(vector.norm()) < 1e-8: raise ValueError('Rotation angle direction is undefined')
    angle = float(torch.rad2deg(torch.atan2(vector[1], vector[0])))
    aligned, transform = _aligned(rgb, angle)
    return {'correction_deg': angle, 'aligned_image': aligned, 'transform': transform, 'source_size': [rgb.shape[1], rgb.shape[0]],
            'output_size': [aligned.shape[1], aligned.shape[0]], 'angle_semantics': 'counterclockwise_upright_correction_degrees_360'}


def predict_rotation_array(checkpoint, rgb, *, device='cpu'):
    model, payload = load_rotation_model(checkpoint, device)
    return {**_predict(model, payload['image_size'], rgb, next(model.parameters()).device), 'checkpoint_sha256': _hash(checkpoint)}


def export_rotation_package(checkpoint, output_dir):
    model, payload = load_rotation_model(checkpoint); output = Path(output_dir)
    if output.exists(): raise ValueError('Rotation export needs a new output directory')
    output.mkdir(parents=True)
    torch.jit.script(model).save(str(output / 'model.ts'))
    _atomic(output / 'config.json', {'task': 'rotation', 'architecture': ARCHITECTURE, 'image_size': payload['image_size'], 'checkpoint_sha256': _hash(checkpoint)})
    target = output / 'backend' / 'engine'; target.mkdir(parents=True)
    shutil.copyfile(__file__, target / 'rotation.py')
    for path in (output / 'backend' / '__init__.py', target / '__init__.py'): path.write_text('')
    (output / 'requirements.txt').write_text('numpy>=1.26\nPillow>=10.4\nopencv-python-headless>=4.10\ntorch>=2.4\n')
    (output / 'infer.py').write_text("import argparse, json\nfrom pathlib import Path\nimport numpy as np\nfrom PIL import Image\nfrom backend.engine.rotation import run_rotation_package\np=argparse.ArgumentParser();p.add_argument('--image',required=True);p.add_argument('--output',required=True);a=p.parse_args()\nr=run_rotation_package(Path(__file__).parent,np.asarray(Image.open(a.image).convert('RGB')))\nImage.fromarray(r.pop('aligned_image')).save(a.output)\nr['transform']=r['transform'].tolist();print(json.dumps(r))\n")
    _atomic(output / 'manifest.json', {'version': 1, 'files': [{'path': p.relative_to(output).as_posix(), 'sha256': _hash(p)} for p in sorted(output.rglob('*')) if p.is_file()]})
    return output


def run_rotation_package(package_dir, rgb):
    root = Path(package_dir).resolve(); manifest = json.loads((root / 'manifest.json').read_text()); seen = set()
    if manifest.get('version') != 1: raise ValueError('Invalid Rotation package version')
    for row in manifest['files']:
        relative = Path(row['path']); path = root / relative
        if relative.is_absolute() or '..' in relative.parts or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root) or row['path'] in seen or _hash(path) != row['sha256']:
            raise ValueError('Rotation package checksum or path differs')
        seen.add(row['path'])
    if not {'model.ts', 'config.json'}.issubset(seen): raise ValueError('Rotation package is incomplete')
    config = json.loads((root / 'config.json').read_text()); model = torch.jit.load(str(root / 'model.ts'), map_location='cpu').eval()
    return {**_predict(model, config['image_size'], rgb, 'cpu'), 'checkpoint_sha256': config['checkpoint_sha256']}
