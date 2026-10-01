"""Small trained defect-crop GAN whose outputs always require human review.

This is an explicit crop dataset and a saved generator, distinct from the
procedural sample generator. It makes no image-quality or production claim.
"""

from __future__ import annotations

from io import BytesIO
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any
import json
import os
import uuid
import math
import shutil

import cv2

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset


MANIFEST_NAME = "defect_gan.json"
IMAGE_SIZE = 64
LATENT_SIZE = 64


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_path(root: Path, relative: str) -> Path:
    name = PurePosixPath(relative)
    if name.is_absolute() or not name.parts or any(part in (".", "..") for part in name.parts):
        raise ValueError("Defect GAN image path must be relative and remain within the dataset")
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError("Defect GAN image is outside the dataset or missing")
    return candidate


def _verified_samples(root: Path, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(samples, list) or not samples:
        raise ValueError("Defect GAN manifest needs explicit image crops")
    verified: list[dict[str, Any]] = []
    path_split: dict[str, str] = {}
    hash_split: dict[str, str] = {}
    for sample in samples:
        if not isinstance(sample, dict) or not isinstance(sample.get("image"), str):
            raise ValueError("Defect GAN image path is invalid")
        image = _source_path(root, sample["image"])
        split = sample.get("split")
        if split not in ("train", "val", "test"):
            raise ValueError("Defect GAN split must be train, val, or test")
        fingerprint = _hash(image)
        supplied = sample.get("source_sha256")
        if supplied is not None and supplied != fingerprint:
            raise ValueError("Defect GAN source image hash changed")
        if sample["image"] in path_split and path_split[sample["image"]] != split:
            raise ValueError("Defect GAN source image leaks across split")
        if fingerprint in hash_split and hash_split[fingerprint] != split:
            raise ValueError("Duplicate source bytes leak across split")
        path_split[sample["image"]] = split
        hash_split[fingerprint] = split
        bbox = sample.get("bbox")
        if (not isinstance(bbox, list) or len(bbox) != 4 or
                any(isinstance(value, bool) or not isinstance(value, int) for value in bbox)):
            raise ValueError("Defect GAN crop bbox must contain four integers")
        with Image.open(BytesIO(image.read_bytes())) as opened:
            width, height = opened.size
        x1, y1, x2, y2 = bbox
        if x1 < 0 or y1 < 0 or x2 > width or y2 > height or x2 - x1 < 16 or y2 - y1 < 16:
            raise ValueError("Defect GAN crop bbox is outside the image or too small")
        row = {"image": sample["image"], "bbox": bbox, "split": split, "source_sha256": fingerprint}
        if sample.get('label') is not None:
            if not isinstance(sample['label'], str) or not sample['label'].strip(): raise ValueError('GAN crop class must be an explicit nonempty label')
            row['label'] = sample['label']
        verified.append(row)
    return verified


def write_defect_gan_manifest(root: str | Path, rows: list[dict[str, Any]], *, source_dataset_path=None, source_map=None) -> Path:
    root = Path(root).expanduser().resolve()
    samples = _verified_samples(root, rows)
    manifest = root / MANIFEST_NAME
    if manifest.is_symlink():
        raise ValueError("Defect GAN manifest cannot be a symbolic link")
    temporary = root / f".{MANIFEST_NAME}.{uuid.uuid4().hex}.tmp"
    payload = {"version": 1, "image_size": IMAGE_SIZE, "samples": samples}
    if source_dataset_path is not None:
        canonical_source = str(Path(source_dataset_path).resolve())
        provenance = {'source_dataset_path': canonical_source,
            'source_map': {row['image']: {'source_relative_path': row['source_image'], 'source_sha256': row['source_sha256']}
                           for row in source_map}}
        payload.update(source_dataset_path=canonical_source, source_map=source_map, provenance=provenance)
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, manifest)
    return manifest


def load_defect_gan_manifest(root: str | Path) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    manifest = root / MANIFEST_NAME
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Defect GAN manifest is missing or linked")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Defect GAN manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("image_size") != IMAGE_SIZE:
        raise ValueError("Unsupported Defect GAN manifest")
    samples = payload.get("samples")
    if not isinstance(samples, list) or any(not isinstance(row, dict) or
            not isinstance(row.get("source_sha256"), str) for row in samples):
        raise ValueError("Defect GAN manifest requires source SHA-256 for every crop")
    _verified_samples(root, samples)
    if payload.get('source_dataset_path'):
        from backend.engine.source_aliases import resolve_source_root
        source=resolve_source_root(payload['source_dataset_path'])
        source_map = payload.get('source_map')
        if not isinstance(source_map, list) or len(source_map) != len(samples): raise ValueError('GAN prepared source map is incomplete')
        for sample, mapping in zip(samples, source_map):
            if (not isinstance(mapping, dict) or mapping.get('image') != sample['image']
                    or mapping.get('source_sha256') != sample['source_sha256'] or mapping.get('source_bbox') != sample['bbox']):
                raise ValueError('GAN prepared source map differs from its explicit crop labels')
            original = _source_path(source, mapping['source_image'])
            if _hash(original) != mapping['source_sha256']: raise ValueError('GAN original source image hash changed')
    return payload


def prepare_defect_gan_dataset(source_root, output_root, rows):
    source = Path(source_root).expanduser().resolve()
    from backend.engine.model_execution import eligible_preparation_paths
    eligible_preparation_paths(source,'defect_gan',[row.get('image') for row in rows])
    verified = _verified_samples(source, rows)
    output = Path(output_root).expanduser()
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(source):
        raise ValueError('GAN preparation requires a new owned directory outside original source')
    output.mkdir(parents=True)
    prepared = []; source_map = []
    try:
        for index, row in enumerate(verified):
            original = _source_path(source, row['image'])
            relative = f"images/{index:06d}{original.suffix.lower()}"
            destination = output / relative; destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(original, destination)
            if _hash(destination) != row['source_sha256'] or _hash(original) != row['source_sha256']:
                raise ValueError('GAN original source changed during preparation')
            prepared.append({**row, 'image': relative})
            source_map.append({'image': relative, 'source_image': row['image'], 'source_sha256': row['source_sha256'],
                'source_bbox': row['bbox'], 'split': row['split'], 'label': row.get('label')})
        write_defect_gan_manifest(output, prepared, source_dataset_path=source, source_map=source_map)
        load_defect_gan_manifest(output)
        return output.resolve()
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


class _DefectCrops(Dataset):
    def __init__(self, root: Path, samples: list[dict[str, Any]]):
        self.root = root
        self.samples = samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> torch.Tensor:
        item = self.samples[index]
        image = _source_path(self.root, item["image"])
        raw = image.read_bytes()
        if sha256(raw).hexdigest() != item["source_sha256"]:
            raise ValueError("Defect GAN source image hash changed during training")
        with Image.open(BytesIO(raw)) as opened:
            crop = opened.convert("RGB").crop(tuple(item["bbox"]))
            crop = crop.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BILINEAR)
            array = np.asarray(crop, dtype=np.float32).copy()
        return torch.from_numpy(array).permute(2, 0, 1) / 127.5 - 1.0


class DefectGenerator(nn.Module):
    def __init__(self, base_channels: int = 16):
        super().__init__()
        base = base_channels
        self.network = nn.Sequential(
            nn.ConvTranspose2d(LATENT_SIZE, base * 8, 4, 1, 0, bias=False),
            nn.BatchNorm2d(base * 8), nn.ReLU(True),
            nn.ConvTranspose2d(base * 8, base * 4, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base * 4), nn.ReLU(True),
            nn.ConvTranspose2d(base * 4, base * 2, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base * 2), nn.ReLU(True),
            nn.ConvTranspose2d(base * 2, base, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base), nn.ReLU(True),
            nn.ConvTranspose2d(base, 3, 4, 2, 1, bias=False), nn.Tanh(),
        )

    def forward(self, latent: torch.Tensor) -> torch.Tensor:
        return self.network(latent)


class _DefectDiscriminator(nn.Module):
    def __init__(self, base_channels: int = 16):
        super().__init__()
        base = base_channels
        self.network = nn.Sequential(
            nn.Conv2d(3, base, 4, 2, 1, bias=False), nn.LeakyReLU(0.2, True),
            nn.Conv2d(base, base * 2, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base * 2), nn.LeakyReLU(0.2, True),
            nn.Conv2d(base * 2, base * 4, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base * 4), nn.LeakyReLU(0.2, True),
            nn.Conv2d(base * 4, base * 8, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base * 8), nn.LeakyReLU(0.2, True),
            nn.Conv2d(base * 8, 1, 4, 1, 0, bias=False), nn.Sigmoid(),
        )

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        return self.network(image).flatten()


def train_defect_gan(
    root: str | Path, output_dir: str | Path, *, epochs: int = 20,
    batch_size: int = 8, seed: int = 0, device: str = "cpu", base_channels: int = 16,
    cancel_event=None, on_progress=None, warm_start=None,
) -> dict[str, Any]:
    if epochs < 1 or batch_size < 2 or base_channels < 8:
        raise ValueError("Defect GAN epochs must be positive, batch size >= 2, base channels >= 8")
    root = Path(root).expanduser().resolve()
    manifest = load_defect_gan_manifest(root)
    samples = [row for row in manifest["samples"] if row["split"] == "train"]
    if len(samples) < 2:
        raise ValueError("Defect GAN needs at least two explicit training crops")
    dev = torch.device(device)
    torch.manual_seed(seed)
    loader = DataLoader(_DefectCrops(root, samples), batch_size=min(batch_size, len(samples)),
                        shuffle=True, drop_last=False)
    generator = DefectGenerator(base_channels).to(dev)
    discriminator = _DefectDiscriminator(base_channels).to(dev)
    lineage = {}
    if warm_start is not None:
        from backend.engine.specialized_warm_start import load_family_weights, requested_signature, require_new_candidate
        require_new_candidate(output_dir, warm_start)
        load_family_weights({'generator_state_dict': generator, 'discriminator_state_dict': discriminator},
                            warm_start, requested_signature('defect_gan', root, {'base_channels': base_channels}))
        lineage = {'warm_start': warm_start.lineage()}
    loss_fn = nn.BCELoss()
    generator_opt = torch.optim.Adam(generator.parameters(), lr=2e-4, betas=(0.5, 0.999))
    discriminator_opt = torch.optim.Adam(discriminator.parameters(), lr=2e-4, betas=(0.5, 0.999))
    generator_loss = discriminator_loss = 0.0
    for epoch in range(1,epochs+1):
        if cancel_event is not None and cancel_event.is_set():raise InterruptedError('GAN training cancelled')
        for batch_index,real in enumerate(loader,1):
            if cancel_event is not None and cancel_event.is_set():raise InterruptedError('GAN training cancelled')
            real = real.to(dev)
            size = real.shape[0]
            if size < 2:
                continue  # BatchNorm cannot train on the final singleton batch.
            latent = torch.randn(size, LATENT_SIZE, 1, 1, device=dev)
            fake = generator(latent)
            discriminator_opt.zero_grad(set_to_none=True)
            discriminator_loss_tensor = (
                loss_fn(discriminator(real), torch.ones(size, device=dev)) +
                loss_fn(discriminator(fake.detach()), torch.zeros(size, device=dev))
            )
            discriminator_loss_tensor.backward()
            discriminator_opt.step()
            generator_opt.zero_grad(set_to_none=True)
            generator_loss_tensor = loss_fn(discriminator(fake), torch.ones(size, device=dev))
            generator_loss_tensor.backward()
            generator_opt.step()
            generator_loss = float(generator_loss_tensor.item())
            discriminator_loss = float(discriminator_loss_tensor.item())
            if on_progress is not None:on_progress({'epoch':epoch,'batch':batch_index,'batches':len(loader),'loss':generator_loss,'discriminator_loss':discriminator_loss})
    if cancel_event is not None and cancel_event.is_set():raise InterruptedError('GAN training cancelled')
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / "best_model.pt"
    temporary = output / f".{checkpoint.name}.{uuid.uuid4().hex}.tmp"
    source_manifest_sha256 = _hash(root / MANIFEST_NAME)
    payload = {
        "model_kind": "dcgan_defect_crop", "task":"defect_gan", "dataset_path":str(root), "image_size": IMAGE_SIZE,
        "latent_size": LATENT_SIZE, "base_channels": base_channels,
        "generator_state_dict": generator.cpu().state_dict(),
        "discriminator_state_dict": discriminator.cpu().state_dict(),
        "source_manifest_sha256": source_manifest_sha256,
        "sample_count": len(samples), "epochs": epochs, "seed": seed,
        **lineage,
    }
    torch.save(payload, temporary)
    os.replace(temporary, checkpoint)
    meta = {key: value for key, value in payload.items() if not key.endswith("state_dict")}
    meta.update({"checkpoint_sha256": _hash(checkpoint),
                 "last_generator_loss": generator_loss,
                 "last_discriminator_loss": discriminator_loss,
                 "quality_status": "unvalidated"})
    (output / "model_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"status": "completed", "checkpoint_path": str(checkpoint), **meta}


def generate_defect_candidates(
    checkpoint_path: str | Path, output_dir: str | Path, *, count: int = 8,
    seed: int = 0, device: str = "cpu",
) -> dict[str, Any]:
    if count < 1 or count > 500:
        raise ValueError("Defect GAN output count must be between 1 and 500")
    checkpoint_path = Path(checkpoint_path).expanduser().resolve()
    meta_path = checkpoint_path.parent / "model_meta.json"
    if not checkpoint_path.is_file() or not meta_path.is_file():
        raise ValueError("Defect GAN checkpoint and metadata are required")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    digest = _hash(checkpoint_path)
    if meta.get("checkpoint_sha256") != digest:
        raise ValueError("Defect GAN checkpoint hash changed")
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if (state.get("model_kind") != "dcgan_defect_crop" or
            state.get("source_manifest_sha256") != meta.get("source_manifest_sha256")):
        raise ValueError("Defect GAN checkpoint metadata mismatch")
    base_channels = int(state["base_channels"])
    dev = torch.device(device)
    generator = DefectGenerator(base_channels).to(dev)
    generator.load_state_dict(state["generator_state_dict"], strict=True)
    generator.eval()
    random = torch.Generator(device="cpu").manual_seed(seed)
    latent = torch.randn(count, LATENT_SIZE, 1, 1, generator=random).to(dev)
    with torch.no_grad():
        images = generator(latent).cpu().numpy()
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    candidates = []
    for index, tensor in enumerate(images, 1):
        array = np.clip((tensor.transpose(1, 2, 0) + 1.0) * 127.5, 0, 255).astype(np.uint8)
        path = output / f"synthetic_candidate_{index:04d}.png"
        Image.fromarray(array).save(path)
        candidates.append({"id": f"candidate_{index:04d}", "path": str(path),
                           "sha256": _hash(path), "status": "synthetic_unreviewed"})
    review = {"model_kind": "dcgan_defect_crop", "generator_sha256": digest,
              "source_manifest_sha256": state["source_manifest_sha256"],
              "quality_status": "unvalidated", "seed": seed, "candidates": candidates}
    (output / "review_manifest.json").write_text(json.dumps(review, indent=2), encoding="utf-8")
    return review


def _composition_regions(regions, width, height):
    if not isinstance(regions, list) or not 1 <= len(regions) <= 32:
        raise ValueError('GAN composition needs between one and 32 explicit source regions')
    seen = set(); canonical = []
    for row in regions:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] or row['id'] in seen:
            raise ValueError('GAN regions need unique nonempty IDs')
        seen.add(row['id'])
        bbox = row.get('bbox')
        if not isinstance(bbox, list) or len(bbox) != 4 or any(type(v) is not int for v in bbox):
            raise ValueError('GAN source region bounds must be four native pixel integers')
        x1, y1, x2, y2 = bbox
        if not 0 <= x1 < x2 <= width or not 0 <= y1 < y2 <= height:
            raise ValueError('GAN source region extends outside native image bounds')
        opacity = row.get('opacity', 1.0); feather = row.get('feather_px', 0)
        if isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or not math.isfinite(opacity) or not 0 < opacity <= 1:
            raise ValueError('GAN blend opacity must be finite and from zero exclusive to one')
        if type(feather) is not int or not 0 <= feather <= 1024: raise ValueError('GAN feather pixels must be an integer from 0 to 1024')
        polygon = row.get('mask_polygon')
        if polygon is not None:
            from backend.engine.geometry_measurement import _points
            points = _points(polygon, minimum=3)
            if np.any(points < [x1, y1]) or np.any(points > [x2, y2]): raise ValueError('GAN blend polygon extends outside its region bounds')
            if abs(cv2.contourArea(points.astype(np.float32))) < 1: raise ValueError('GAN blend polygon has no positive source area')
        canonical.append({'id': row['id'], 'bbox': bbox, 'opacity': float(opacity), 'feather_px': feather, 'mask_polygon': polygon})
    return canonical


def generate_composited_candidates(checkpoint_path, source_image_path, output_dir, *, regions,
                                   count=8, seed=0, device='cpu', source_sha256=None):
    """Run the trained generator for each region and retain reviewed-source provenance."""
    source = Path(source_image_path).expanduser()
    if source.is_symlink() or not source.is_file(): raise ValueError('GAN composition source image is unavailable')
    source = source.resolve(); original_hash = _hash(source)
    if source_sha256 is not None and source_sha256 != original_hash: raise ValueError('GAN source image hash changed')
    with Image.open(source) as loaded:
        if loaded.width * loaded.height > 100_000_000: raise ValueError('GAN source exceeds native pixel work budget')
        original = np.asarray(loaded.convert('RGB')).copy()
    height, width = original.shape[:2]
    regions = _composition_regions(regions, width, height)
    if type(count) is not int or not 1 <= count <= 20 or count * len(regions) > 500:
        raise ValueError('GAN composition exceeds the 20-image or 500-region work budget')
    output = Path(output_dir).expanduser()
    if output.exists() or output.is_symlink() or output.resolve() == source.parent:
        raise ValueError('GAN composition requires a new review directory')
    output.mkdir(parents=True)
    try:
        patches = generate_defect_candidates(checkpoint_path, output / 'regions', count=count * len(regions), seed=seed, device=device)
        snapshot = output / 'source_image.png'; Image.fromarray(original).save(snapshot)
        candidates = []
        for index in range(count):
            composed = original.copy(); provenance = []
            for offset, row in enumerate(regions):
                generated = patches['candidates'][index * len(regions) + offset]
                x1, y1, x2, y2 = row['bbox']; rw, rh = x2 - x1, y2 - y1
                with Image.open(generated['path']) as image: patch = np.asarray(image.convert('RGB'))
                patch = cv2.resize(patch, (rw, rh), interpolation=cv2.INTER_LINEAR)
                mask = np.ones((rh, rw), np.uint8)
                if row['mask_polygon'] is not None:
                    mask.fill(0)
                    points = np.rint(np.asarray(row['mask_polygon']) - [x1, y1]).astype(np.int32)
                    cv2.fillPoly(mask, [points], 1)
                alpha = mask.astype(np.float32)
                if row['feather_px']:
                    padded = np.pad(mask, 1)
                    distance = cv2.distanceTransform(padded, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)[1:-1, 1:-1]
                    alpha *= np.clip(distance / row['feather_px'], 0, 1)
                alpha *= row['opacity']
                current = composed[y1:y2, x1:x2].astype(np.float32)
                composed[y1:y2, x1:x2] = np.rint(current * (1-alpha[..., None]) + patch * alpha[..., None]).clip(0, 255).astype(np.uint8)
                provenance.append({**row, 'coordinate_space': 'original_image', 'generated_patch_sha256': generated['sha256'],
                                   'blend_mask_sha256': sha256(alpha.tobytes()).hexdigest(), 'generation_index': index * len(regions) + offset,
                                   'blend_mode': 'alpha_source_over', 'mask_dtype': 'float32', 'mask_shape': [rh, rw]})
            path = output / f'synthetic_candidate_{index+1:04d}.png'; Image.fromarray(composed).save(path)
            candidates.append({'id': f'candidate_{index+1:04d}', 'path': str(path.resolve()), 'sha256': _hash(path),
                               'status': 'synthetic_unreviewed', 'composition_regions': provenance})
        if _hash(source) != original_hash: raise ValueError('GAN source image changed during composition')
        review = {key: value for key, value in patches.items() if key != 'candidates'}
        review.update({'generation_mode': 'source_composition', 'source_image_path': str(source), 'source_image_sha256': original_hash,
                      'source_size': [width, height], 'source_snapshot': str(snapshot.resolve()), 'source_snapshot_sha256': _hash(snapshot),
                      'regions': regions, 'candidates': candidates, 'seed': seed, 'quality_status': 'unvalidated'})
        (output / 'review_manifest.json').write_text(json.dumps(review, ensure_ascii=False, indent=2))
        return review
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


def validate_composition_source(review_dir, manifest):
    if manifest.get('generation_mode') != 'source_composition': return
    review = Path(review_dir).resolve()
    original = Path(manifest['source_image_path']); snapshot = Path(manifest['source_snapshot'])
    if (not original.is_file() or original.is_symlink() or _hash(original) != manifest['source_image_sha256']
            or snapshot.parent.resolve() != review or snapshot.is_symlink() or not snapshot.is_file()
            or _hash(snapshot) != manifest['source_snapshot_sha256']):
        raise ValueError('GAN composition source or preserved snapshot hash changed before review')


def adopt_reviewed_candidates(review_dir: str | Path, source_dataset: str | Path,
                             output_dir: str | Path, decisions: list[dict]) -> dict:
    """Build a classification training snapshot after explicit per-image human review.

    Copies real train/val/test data without changing the source. Generated images
    enter only train under the reviewer's explicit existing defect class.
    """
    from backend.engine.dataset_loaders import ClassificationDataset
    from datetime import datetime, timezone
    import shutil
    import re
    review=Path(review_dir).resolve(); source=Path(source_dataset).resolve(); output=Path(output_dir)
    manifest_path=review/'review_manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or not decisions: raise ValueError('Reviewed candidate manifest and decisions are required')
    manifest=json.loads(manifest_path.read_text())
    validate_composition_source(review, manifest)
    candidates={row['id']:row for row in manifest['candidates']}
    real={split:ClassificationDataset(source,split=split) for split in ('train','val','test')}
    if not len(real['train']) or not len(real['val']): raise ValueError('Adoption needs existing labeled classification train and validation samples')
    classes=real['train'].classes
    selected=[];seen=set()
    for decision in decisions:
        cid=decision.get('candidate_id')
        if cid in seen or cid not in candidates: raise ValueError('Duplicate or unavailable generated candidate')
        seen.add(cid);row=candidates[cid]
        if row.get('status')!='synthetic_unreviewed': raise ValueError('Candidate has already been reviewed')
        if decision.get('decision') not in ('adopt','reject') or not str(decision.get('reviewer','')).strip() or len(str(decision.get('reason','')).strip())<2:
            raise ValueError('Each candidate needs adopt/reject, reviewer and review reason')
        path=Path(row['path'])
        if path.is_symlink() or path.parent.resolve()!=review or not path.is_file() or _hash(path)!=row['sha256']:
            raise ValueError('Generated candidate source hash changed or escaped review directory')
        if decision['decision']=='adopt':
            label=decision.get('label')
            if label not in classes or not re.fullmatch(r'[^/\\.][^/\\]*',str(label)) or str(label).casefold() in ('ok','normal','good','pass','정상'):
                raise ValueError('Adoption requires an existing explicit defect class label')
            selected.append((decision,row,path))
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(source): raise ValueError('Use a new adoption dataset outside the source')
    output.mkdir(parents=True)
    rows=[]
    try:
        for split,dataset in real.items():
            for index,(path,label_index) in enumerate(dataset.samples):
                label=dataset.classes[label_index]
                destination=output/split/label/f'real_{index:06d}_{path.name}'
                destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
                rows.append({'kind':'real','source_image':str(path),'source_sha256':_hash(path),'image':destination.relative_to(output).as_posix(),'split':split,'label':label})
        for decision,row,path in selected:
            destination=output/'train'/decision['label']/f"synthetic_{uuid.uuid4().hex}.png"
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
            rows.append({'kind':'synthetic_reviewed','image':destination.relative_to(output).as_posix(),'split':'train','label':decision['label'],'candidate_id':row['id'],'source_sha256':row['sha256'],'generator_sha256':manifest['generator_sha256'],'reviewer':decision['reviewer'],'reason':decision['reason'],
                'source_image_sha256': manifest.get('source_image_sha256'), 'composition_regions': row.get('composition_regions', []), 'generation_seed': manifest.get('seed')})
        audit={'task':'classification','source_dataset_path':str(source),'generator_sha256':manifest['generator_sha256'],'reviewed_at':datetime.now(timezone.utc).isoformat(),'samples':rows,'decisions':decisions}
        (output/'synthetic_provenance.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
        for decision in decisions:
            row=candidates[decision['candidate_id']];row['status']='synthetic_adopted' if decision['decision']=='adopt' else 'synthetic_rejected';row['review']={**decision,'dataset_path':str(output.resolve()),'reviewed_at':audit['reviewed_at']}
        temporary=manifest_path.with_name(f'.review-{uuid.uuid4().hex}.tmp');temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2));os.replace(temporary,manifest_path)
    except BaseException:
        shutil.rmtree(output,ignore_errors=True);raise
    return {'status':'adopted','dataset_path':str(output.resolve()),'adopted_count':len(selected),'real_image_count':sum(len(ds) for ds in real.values()),'task':'classification'}


def evaluate_defect_generator(checkpoint: str | Path, dataset_path: str | Path,
                              *, split='test', count=8, seed=0, device='cpu') -> dict:
    """Held-out RGB-statistic comparison, a diagnostic without model-quality approval."""
    import tempfile
    if split not in ('val','test'): raise ValueError('GAN evaluation requires held-out val or test crops')
    root=Path(dataset_path).resolve();manifest=load_defect_gan_manifest(root)
    checkpoint=Path(checkpoint)
    metadata=json.loads(checkpoint.with_name('model_meta.json').read_text())
    if metadata.get('source_manifest_sha256')!=_hash(root/MANIFEST_NAME): raise ValueError('GAN evaluation dataset differs from checkpoint')
    rows=[r for r in manifest['samples'] if r['split']==split]
    if not rows: raise ValueError(f'GAN {split} split has no ground-truth crops')
    def features(pixels):
        values=pixels.astype(np.float32)/255
        return np.concatenate([values.mean((0,1)),values.std((0,1))])
    real=[]
    for row in rows:
        with Image.open(_source_path(root,row['image'])) as image:
            real.append(features(np.asarray(image.convert('RGB').crop(tuple(row['bbox'])).resize((64,64)))))
    with tempfile.TemporaryDirectory(prefix='gan-evaluation-') as temp:
        generated=generate_defect_candidates(checkpoint,temp,count=count,seed=seed,device=device)
        fake=[features(np.asarray(Image.open(row['path']).convert('RGB'))) for row in generated['candidates']]
    real,fake=np.asarray(real),np.asarray(fake)
    def kernel(left,right):
        return np.exp(-np.square(left[:,None,:]-right[None,:,:]).sum(2)/(2*0.1**2)).mean()
    mmd=float(kernel(real,real)+kernel(fake,fake)-2*kernel(real,fake))
    return {'task':'defect_gan','split':split,'real_sample_count':len(real),'generated_count':len(fake),'rgb_statistics_mmd':max(0,mmd),'metric_backend':'RGB mean/std Gaussian-kernel MMD (diagnostic)','quality_status':'unvalidated','checkpoint_sha256':_hash(checkpoint),'manifest_sha256':_hash(root/MANIFEST_NAME),'seed':seed}
