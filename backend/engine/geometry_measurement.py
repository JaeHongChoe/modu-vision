"""Calibrated measurements over original-image coordinates and class rasters."""
from __future__ import annotations

import math
import numpy as np


def _points(value, minimum=2):
    if not isinstance(value, list) or len(value) < minimum or len(value) > 10000:
        raise ValueError('Measurement needs a bounded list of source coordinate points')
    if any(not isinstance(p, list) or len(p) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in p) for p in value):
        raise ValueError('Measurement points need two finite numeric coordinates')
    coordinates = np.asarray(value, dtype=float)
    if not np.isfinite(coordinates).all(): raise ValueError('Measurement points must be finite')
    return coordinates


def validate_measurement_params(params, source_size=None):
    calibration = params.get('calibration')
    if calibration is not None:
        if not isinstance(calibration, dict) or calibration.get('unit') != 'mm':
            raise ValueError('Calibration must declare millimeter units')
        for key in ('mm_per_pixel_x', 'mm_per_pixel_y'):
            value = calibration.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('Calibration scale must be finite and positive')
        size = calibration.get('source_size')
        if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v < 1 for v in size):
            raise ValueError('Calibration must bind the native source width and height')
        if source_size is not None and size != list(source_size):
            raise ValueError('Calibration source dimensions differ from the inspected image')
    paths = params.get('paths', [])
    if not isinstance(paths, list) or len(paths) > 64: raise ValueError('Measurement paths must be a list with at most 64 entries')
    seen = set()
    for path in paths:
        if not isinstance(path, dict) or not isinstance(path.get('id'), str) or not path['id'] or path['id'] in seen:
            raise ValueError('Measurement paths need unique nonempty IDs')
        seen.add(path['id'])
        points = _points(path.get('points'))
        interpolation = path.get('interpolation', 'polyline')
        if interpolation not in ('polyline', 'bezier') or (interpolation == 'bezier' and len(points) != 4):
            raise ValueError('Cubic Bezier paths require exactly four source control points')
        if source_size is not None and (np.any(points < 0) or np.any(points > np.asarray(source_size))):
            raise ValueError('Measurement path extends outside the source image')
    for low, high in (('min_length', 'max_length'), ('min_area', 'max_area')):
        for key in (low, high):
            if key in params and (isinstance(params[key], bool) or not isinstance(params[key], (int, float)) or not math.isfinite(params[key]) or params[key] < 0):
                raise ValueError('Measurement bounds must be finite nonnegative numbers')
        if params.get(low, 0) > params.get(high, math.inf): raise ValueError('Measurement maximum is below its minimum')


def _curve(points):
    # Adaptive subdivision measures the actual curve, with a declared source-pixel tolerance.
    result = [points[0]]
    def append(control, depth):
        chord = float(np.linalg.norm(control[3] - control[0]))
        polygon = float(np.linalg.norm(np.diff(control, axis=0), axis=1).sum())
        if depth == 16 or polygon - chord <= 0.001:
            result.append(control[3]); return
        a, b, c = (control[:-1] + control[1:]) / 2
        d, e = (a + b) / 2, (b + c) / 2
        midpoint = (d + e) / 2
        append(np.array([control[0], a, d, midpoint]), depth + 1)
        append(np.array([midpoint, e, c, control[3]]), depth + 1)
    append(points, 0)
    return np.asarray(result)


def measure_geometry(params, *, source_size, polygons=(), masks=()):
    validate_measurement_params(params, source_size)
    calibration = params.get('calibration')
    scale = np.array([calibration['mm_per_pixel_x'], calibration['mm_per_pixel_y']]) if calibration else np.ones(2)
    unit = 'mm' if calibration else 'px'
    common = {'coordinate_space': 'original_image', 'source_size': list(source_size), 'calibration': calibration}
    rows = []
    for path in params.get('paths', []):
        control = _points(path['points'])
        points = _curve(control) if path.get('interpolation') == 'bezier' else control
        distance = np.diff(points, axis=0)
        length = float(np.linalg.norm(distance * scale, axis=1).sum())
        rows.append({**common, 'id': path['id'], 'kind': 'length', 'unit': unit, 'length': length,
                     'length_px': float(np.linalg.norm(distance, axis=1).sum()), 'points': path['points'],
                     'interpolation': path.get('interpolation', 'polyline'), 'measurement_source': 'source_path',
                     'curve_tolerance_px': 0.001 if path.get('interpolation') == 'bezier' else None,
                     'verdict': 'OK' if params.get('min_length', 0) <= length <= params.get('max_length', math.inf) else 'NG'})
    for polygon in polygons:
        points = _points(polygon['points'], minimum=3)
        area_px = abs(float(np.dot(points[:, 0], np.roll(points[:, 1], 1)) - np.dot(points[:, 1], np.roll(points[:, 0], 1)))) / 2
        rows.append({**common, 'id': polygon['id'], 'kind': 'area', 'area_px': area_px, 'measurement_source': 'source_polygon'})
    for entry in masks:
        mask = np.asarray(entry['mask'])
        if mask.ndim != 2 or not np.isfinite(mask).all(): raise ValueError('Area mask must be a finite source-resolution 2D raster')
        rows.append({**common, 'id': entry['id'], 'kind': 'area', 'area_px': int(np.count_nonzero(mask)),
                     'class_id': entry.get('class_id'), 'measurement_source': 'segmentation_mask'})
    for row in rows:
        if row['kind'] == 'area':
            row['area'] = row['area_px'] * float(np.prod(scale))
            row['unit'] = unit + '2'
            row['verdict'] = 'OK' if params.get('min_area', 0) <= row['area'] <= params.get('max_area', math.inf) else 'NG'
    return rows
