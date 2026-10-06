"""Pin independent normal originals before distance threshold calibration."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import torch

from .cancellation import check_fit_cancelled


def _samples(dataset):
    if isinstance(dataset, torch.utils.data.Subset):
        values = _samples(dataset.dataset)
        return [values[index] for index in dataset.indices]
    if isinstance(dataset, torch.utils.data.ConcatDataset):
        return [row for part in dataset.datasets for row in _samples(part)]
    if getattr(dataset, 'samples', None) is not None:
        return dataset.samples
    if getattr(dataset, 'dataset', None) is not None:
        return _samples(dataset.dataset)
    raise ValueError('Normal calibration requires native source samples')


def _property(dataset, key, default=None):
    if hasattr(dataset, key):
        return getattr(dataset, key)
    if getattr(dataset, 'dataset', None) is not None:
        return _property(dataset.dataset, key, default)
    return default


def _rgb(encoded):
    with Image.open(io.BytesIO(encoded)) as image:
        if image.width * image.height > 100_000_000:
            raise ValueError('Normal calibration image exceeds the 100 megapixel budget')
        return np.asarray(image.convert('RGB'), dtype=np.uint8).copy()


def _pin(dataset, cancellation_requested):
    sources = []
    for sample in _samples(dataset):
        check_fit_cancelled(cancellation_requested)
        if not isinstance(sample, (tuple, list)) or len(sample) < 2 or sample[1] != 0:
            raise ValueError('Training and calibration accept only label 0 normal images')
        path = Path(sample[0]).expanduser().resolve()
        encoded = path.read_bytes()
        rgb = _rgb(encoded)
        pixel_digest = hashlib.sha256(str(rgb.shape).encode() + rgb.tobytes()).hexdigest()
        sources.append({'path': path, 'source_sha256': hashlib.sha256(encoded).hexdigest(),
                        'decoded_sha256': pixel_digest, 'size': [rgb.shape[1], rgb.shape[0]]})
        check_fit_cancelled(cancellation_requested)
    if not sources:
        raise ValueError('Training and calibration require normal source images')
    if len({row['path'] for row in sources}) != len(sources) or len({row['decoded_sha256'] for row in sources}) != len(sources):
        raise ValueError('Normal calibration source images must have unique paths and decoded content')
    return sources


def _public(rows):
    return [{key: row[key] for key in ('source_sha256', 'decoded_sha256', 'size')} for row in rows]


def _digest(rows):
    return hashlib.sha256(json.dumps(sorted(_public(rows), key=lambda row: row['decoded_sha256']),
                                     sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def prepare_normal_calibration(training, calibration, cancellation_requested=None):
    split = _property(calibration, 'split')
    if split not in ('val', 'calibration'):
        raise ValueError('Normal threshold calibration requires a val/calibration split; test is evaluation only')
    if _property(calibration, 'transform') is not None:
        raise ValueError('Normal calibration must use deterministic originals without training augmentation')
    training_rows = _pin(training, cancellation_requested)
    rows = _pin(calibration, cancellation_requested)
    if {row['path'] for row in training_rows} & {row['path'] for row in rows}:
        raise ValueError('Normal calibration training paths overlap')
    if {row['decoded_sha256'] for row in training_rows} & {row['decoded_sha256'] for row in rows}:
        raise ValueError('Normal calibration training decoded content overlaps')
    return {'training': training_rows, 'sources': rows, 'split': split,
            'image_size': _property(calibration, 'image_size'), 'max_dim': _property(calibration, 'max_dim', 0),
            'public': {'split': split, 'images': _public(rows), 'normal_image_count': len(rows),
                       'training_images': _public(training_rows),
                       'source_digest': _digest(rows), 'training_source_digest': _digest(training_rows)}}


def verify_normal_snapshot(snapshot, cancellation_requested=None):
    for row in snapshot['training'] + snapshot['sources']:
        check_fit_cancelled(cancellation_requested)
        if hashlib.sha256(row['path'].read_bytes()).hexdigest() != row['source_sha256']:
            raise ValueError('Normal calibration source changed after its snapshot was pinned')


def normal_calibration_scores(model, snapshot, cancellation_requested=None):
    scores = []
    for row in snapshot['sources']:
        check_fit_cancelled(cancellation_requested)
        encoded = row['path'].read_bytes()
        if hashlib.sha256(encoded).hexdigest() != row['source_sha256']:
            raise ValueError('Normal calibration source changed after its snapshot was pinned')
        rgb = _rgb(encoded)
        height, width = rgb.shape[:2]
        if snapshot['image_size']:
            rgb = cv2.resize(rgb, tuple(snapshot['image_size']), interpolation=cv2.INTER_LINEAR)
        elif snapshot['max_dim'] and max(height, width) > snapshot['max_dim']:
            scale = snapshot['max_dim'] / max(height, width)
            rgb = cv2.resize(rgb, (max(16, int(round(width * scale))), max(16, int(round(height * scale)))),
                             interpolation=cv2.INTER_LINEAR)
        image = torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1))).float().div_(255)
        heatmap, score = model.predict_anomaly_map(image)
        check_fit_cancelled(cancellation_requested)
        if not np.isfinite(heatmap).all() or not np.isfinite(score) or score < 0:
            raise ValueError('Normal calibration returned a nonfinite or negative distance')
        scores.append(float(score))
    verify_normal_snapshot(snapshot, cancellation_requested)
    return scores


def check_normal_batch(batch):
    if isinstance(batch, (tuple, list)) and len(batch) > 1:
        if not torch.all(torch.as_tensor(batch[1]) == 0):
            raise ValueError('Anomaly fit accepts only label 0 normal images')


def statistical_calibration(scores, snapshot=None):
    values = np.asarray(scores, dtype=np.float64)
    if not values.size or not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError('Anomaly fit requires finite normal distance scores')
    mean, std = float(values.mean()), float(values.std())
    metadata = {'version': 1, 'method': 'heldout_normal_mean_plus_3_std' if snapshot else 'training_default_mean_plus_3_std',
                'split': snapshot['split'] if snapshot else 'train', 'comparison': '>=',
                'normal_image_count': len(scores), 'images': [], 'score_mean': mean, 'score_std': std,
                'quality_approved': False, 'population_fpr_verified': False}
    if snapshot:
        metadata.update(snapshot['public'])
    return round(mean + 3 * std, 4), metadata
