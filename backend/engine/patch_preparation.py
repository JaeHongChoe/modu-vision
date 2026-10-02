"""Create source-linked patch truth in owned storage from reviewed region labels."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import uuid

import cv2
import numpy as np
from PIL import Image

from backend.engine.grouped_dataset_views import _annotations, source_image_paths
from backend.engine.native_patches import native_grid
from backend.engine.patch_classification import load_patch_manifest


def prepare_patch_dataset(source, target, *, patch_size=256, stride=128,
                          assignments=None, normal_class='OK', minimum_overlap=.05):
    source = Path(source).expanduser().resolve()
    requested = Path(target).expanduser()
    target = requested.resolve()
    if (requested.is_symlink() or target == source or target.is_relative_to(source)
            or target.exists()):
        raise ValueError('Patch preparation needs a new owned directory outside the original source')
    if not isinstance(normal_class, str) or not normal_class.strip():
        raise ValueError('Choose a normal patch class')
    if not 0 < minimum_overlap <= 1:
        raise ValueError('minimum_overlap must be in (0,1]')
    assignments = assignments or {}
    inventory = []
    for path in source_image_paths(source, 'segmentation'):
        annotations, mask_path = _annotations(source, path)
        # Raster labels need an explicit ID-to-class mapping before conversion.
        if mask_path and not annotations:
            continue
        if annotations is None or not annotations:
            continue
        regions = [a for a in annotations if a.get('type') in {'bbox', 'polygon', 'rotated_bbox'}]
        if not regions:
            continue
        relative = path.relative_to(source).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        split = assignments.get(str(path), assignments.get(relative))
        if split not in {'train', 'val', 'test'}:
            split = next((p for p in Path(relative).parts[:-1] if p in {'train', 'val', 'test'}), None)
        inventory.append((path, relative, digest, split, regions))
    if not inventory:
        raise ValueError('Patch preparation needs annotated source images; unlabeled images cannot establish normal truth')
    labels = sorted({r['label'] for *_, regions in inventory for r in regions
                     if not r.get('is_normal') and r['label'] != normal_class})
    if not labels:
        raise ValueError('Patch classification needs at least one annotated defect class')
    classes = [normal_class, *labels]
    hashes = sorted({r[2] for r in inventory})
    missing = [r for r in inventory if r[3] is None]
    if missing:
        if len(hashes) < 3:
            raise ValueError('Automatic split requires three distinct annotated source images')
        # A source and byte-identical copies always share a partition.
        partitions = {h: ('test' if i == len(hashes)-1 else 'val' if i == len(hashes)-2 else 'train')
                      for i, h in enumerate(hashes)}
    else:
        partitions = {}
    staging = target.parent / f'.patch-preparation-{uuid.uuid4().hex}'
    staging.mkdir(parents=True)
    rows, source_map, verdicts, warnings = [], {}, {}, []
    try:
        for path, relative, digest, split, regions in inventory:
            split = split or partitions[digest]
            copied = f'images/{relative}'
            destination = staging / copied
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            with Image.open(path) as opened:
                width, height = opened.size
            masks = {name: np.zeros((height, width), dtype=np.uint8) for name in labels}
            for region in regions:
                if region.get('is_normal') or region['label'] == normal_class:
                    continue
                mask = masks[region['label']]
                if region['type'] == 'bbox':
                    x1, y1, x2, y2 = region['bbox']
                    x1, y1, x2, y2 = max(0, int(x1)), max(0, int(y1)), min(width, int(x2)), min(height, int(y2))
                    mask[y1:y2, x1:x2] = 1
                elif region['type'] == 'polygon':
                    cv2.fillPoly(mask, [np.round(region.get('polygon') or region.get('points')).astype(np.int32)], 1)
                else:
                    cx, cy, rw, rh, angle = region['rotated_bbox']
                    cv2.fillPoly(mask, [np.round(cv2.boxPoints(((cx, cy), (rw, rh), angle))).astype(np.int32)], 1)
            for box in native_grid(width, height, patch_size, stride):
                x1, y1, x2, y2 = box
                area = (x2-x1)*(y2-y1)
                overlaps = sorted(((float(m[y1:y2, x1:x2].sum())/area, name) for name, m in masks.items()), reverse=True)
                matching = [(fraction, name) for fraction, name in overlaps if fraction >= minimum_overlap]
                label = matching[0][1] if matching else normal_class
                if len(matching) > 1:
                    warnings.append({'image': copied, 'box': list(box), 'reason': 'multiple_classes', 'overlap': matching})
                rows.append({'image': copied, 'box': list(box), 'label': label, 'split': split, 'source_sha256': digest})
            source_map[copied] = {'source_relative_path': relative, 'source_sha256': digest}
            if split == 'test':
                verdicts[copied] = 'NG' if any(m.any() for m in masks.values()) else 'OK'
        payload = {'version': 1, 'classes': classes, 'normal_class': normal_class,
                   'patch_size': patch_size, 'stride': stride, 'patches': rows,
                   'source_dataset_path': str(source), 'source_map': source_map,
                   'test_image_verdicts': verdicts, 'review_warnings': warnings,
                   'label_semantics': 'background outside supplied complete region annotations',
                   'minimum_overlap': minimum_overlap}
        (staging/'patches.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        (staging/'source_manifest.json').write_text(json.dumps([
            {'image': str(target/image), 'source_image': str(source/row['source_relative_path']),
             'source_sha256': row['source_sha256']} for image, row in source_map.items()
        ], ensure_ascii=False, indent=2), encoding='utf-8')
        manifest = load_patch_manifest(staging)
        staging.rename(target)
        return {'dataset_path': str(target), 'classes': classes, 'patch_count': len(rows),
                'split_counts': manifest.provenance['split_counts'], 'warnings': warnings,
                'source_dataset_path': str(source)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
