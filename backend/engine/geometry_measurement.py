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


def threshold_unit(params) -> str:
    """The unit the node's limits are written in: declared, or (older flows) millimetres when a calibration is set."""
    unit = params.get('threshold_unit')
    if unit is not None:
        return unit
    return 'mm' if params.get('calibration') is not None or params.get('calibration_ref') is not None else 'px'


def validate_measurement_params(params, source_size=None):
    from backend.engine.spatial_calibration import is_calibration_ref
    calibration = params.get('calibration')
    reference = params.get('calibration_ref')
    if reference is not None:
        if calibration is not None:
            raise ValueError('Use a calibration artifact or an inline scale, not both')
        if not is_calibration_ref(reference):
            raise ValueError('calibration_ref must name a spatial calibration artifact')
    if params.get('threshold_unit') not in (None, 'px', 'mm'):
        raise ValueError('threshold_unit must be px or mm')
    if threshold_unit(params) == 'mm' and calibration is None and reference is None:
        raise ValueError('Limits in mm need a calibration')
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


def _physical_scale(params, source_size, scope):
    """(scale, calibration record for the result rows, reason millimetres are refused or None)."""
    from backend.engine.spatial_calibration import refusal
    inline = params.get('calibration')
    if inline is not None:
        record = {**inline, 'method': 'inline_manual_scale', 'ref': None, 'acquisition_verified': False}
        return np.array([inline['mm_per_pixel_x'], inline['mm_per_pixel_y']]), record, None
    reference = params.get('calibration_ref')
    if reference is None:
        return None, None, None
    calibration = scope.resolve(reference) if scope is not None else None
    if calibration is None:
        return None, {'ref': reference, 'refused': 'the calibration artifact is not available here'}, f'calibration {reference} is not available here'
    acquisition = scope.acquisition
    reason = refusal(calibration, source_size=source_size, acquisition=acquisition)
    if reason:
        return None, {'ref': reference, 'refused': reason}, reason
    scales = calibration.scales_or_mapping
    record = {'ref': reference, 'method': calibration.method, 'unit': 'mm', 'mm_per_pixel_x': scales['mm_per_pixel_x'],
              'mm_per_pixel_y': scales['mm_per_pixel_y'], 'source_size': list(calibration.source_size),
              'camera_id': calibration.camera_id, 'residual_mm': calibration.residual,
              'valid_plane': dict(calibration.valid_plane), 'acquisition_verified': acquisition is not None}
    return np.array([scales['mm_per_pixel_x'], scales['mm_per_pixel_y']]), record, None


def _require_plane_points(points, region, geometry):
    """Continuous paths and polygon edges may touch the boundary of the calibrated pixel cells.

    The rectangular plane is convex: containing every polyline vertex or Bezier control point contains the whole
    path, and containing every polygon vertex contains its area. No sampled curve or clipped area can hide an
    excursion outside the plane.
    """
    if region is not None and (np.any(points < np.asarray(region[:2])) or np.any(points > np.asarray(region[2:]))):
        raise ValueError(f'{geometry} extends outside the calibration valid plane region {region}')


def _source_mask_bbox(entry, mask, source_size):
    """Bind a native raster or an already reprojected crop to source pixel cells, without resampling or clipping.

    Segmentation class evidence has already restored local rotation/perspective into an original-resolution bbox.
    Its remaining transform must be exactly that bbox's translation; other transforms cannot preserve pixel area
    by counting this raster and must be restored by the producer first.
    """
    if 'bbox' not in entry and 'source_transform' not in entry:
        if mask.shape != (source_size[1], source_size[0]):
            raise ValueError('Area mask must use the source-resolution raster or bind its source bbox and transform')
        return [0, 0, source_size[0], source_size[1]]
    bbox = entry.get('bbox')
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or any(type(v) is not int for v in bbox):
        raise ValueError('Area mask needs a source bbox of four whole pixel coordinates')
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= source_size[0] and 0 <= y1 < y2 <= source_size[1]):
        raise ValueError('Area mask source bbox must lie inside the source image')
    if mask.shape != (y2 - y1, x2 - x1):
        raise ValueError('Area mask source-resolution raster shape must match its source bbox')
    try:
        transform = np.asarray(entry.get('source_transform'), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError('Area mask source transform must map its original-resolution bbox') from exc
    expected = np.array([[1, 0, x1], [0, 1, y1], [0, 0, 1]], dtype=float)
    if transform.shape != (3, 3) or not np.array_equal(transform, expected):
        raise ValueError('Area mask source transform must be the original-resolution bbox translation')
    return list(bbox)


def measure_geometry(params, *, source_size, polygons=(), masks=(), scope=None):
    """Lengths and areas in source pixels and, when a calibration applies, in millimetres. The limits are compared in
    their declared unit; limits in mm without an applying calibration raise (the run reports the image incomplete)
    instead of comparing pixels with millimetres. Referenced calibrations also require all measured geometry to
    lie in their valid plane region; a domain failure refuses the measurement, without clipping or changing units.
    """
    if scope is None:
        from backend.engine.spatial_calibration import current_scope
        scope = current_scope()
    validate_measurement_params(params, source_size)
    physical, calibration, refused = _physical_scale(params, source_size, scope)
    limits_unit = threshold_unit(params)
    if limits_unit == 'mm' and physical is None:
        raise ValueError(f'the limits are in mm but {refused}')
    scale = physical if physical is not None else np.ones(2)
    unit = 'mm' if physical is not None else 'px'
    region = calibration['valid_plane']['region'] if physical is not None and params.get('calibration_ref') is not None else None
    common = {'coordinate_space': 'original_image', 'source_size': list(source_size), 'calibration': calibration,
              'threshold_unit': limits_unit}
    rows = []
    for path in params.get('paths', []):
        control = _points(path['points'])
        _require_plane_points(control, region, f"Measurement path {path['id']}")
        points = _curve(control) if path.get('interpolation') == 'bezier' else control
        distance = np.diff(points, axis=0)
        length = float(np.linalg.norm(distance * scale, axis=1).sum())
        length_px = float(np.linalg.norm(distance, axis=1).sum())
        judged = length if limits_unit == unit else length_px
        rows.append({**common, 'id': path['id'], 'kind': 'length', 'unit': unit, 'length': length,
                     'length_px': length_px, 'points': path['points'],
                     'interpolation': path.get('interpolation', 'polyline'), 'measurement_source': 'source_path',
                     'curve_tolerance_px': 0.001 if path.get('interpolation') == 'bezier' else None,
                     'verdict': 'OK' if params.get('min_length', 0) <= judged <= params.get('max_length', math.inf) else 'NG'})
    for polygon in polygons:
        points = _points(polygon['points'], minimum=3)
        _require_plane_points(points, region, f"Area polygon {polygon['id']}")
        area_px = abs(float(np.dot(points[:, 0], np.roll(points[:, 1], 1)) - np.dot(points[:, 1], np.roll(points[:, 0], 1)))) / 2
        rows.append({**common, 'id': polygon['id'], 'kind': 'area', 'area_px': area_px, 'measurement_source': 'source_polygon'})
    for entry in masks:
        mask = np.asarray(entry['mask'])
        if mask.ndim != 2 or not np.isfinite(mask).all(): raise ValueError('Area mask must be a finite source-resolution 2D raster')
        if region is not None:
            bbox = _source_mask_bbox(entry, mask, source_size)
            # Inspect the source plane's intersection in this raster. The complete mask is still counted below;
            # even one nonzero pixel outside the intersection refuses the whole measurement.
            x1, y1, x2, y2 = [max(0, min(limit, edge - origin)) for edge, origin, limit in
                              zip(region, bbox[:2] * 2, [mask.shape[1], mask.shape[0]] * 2)]
            # Raster support is half-open: zero pixels outside the plane do not claim a calibrated area.
            if (np.any(mask[:y1]) or np.any(mask[y2:]) or np.any(mask[y1:y2, :x1]) or np.any(mask[y1:y2, x2:])):
                raise ValueError(f"Area mask {entry['id']} extends outside the calibration valid plane region {region}")
        rows.append({**common, 'id': entry['id'], 'kind': 'area', 'area_px': int(np.count_nonzero(mask)),
                     **{key: entry[key] for key in ('bbox', 'source_transform') if key in entry},
                     'class_id': entry.get('class_id'), 'measurement_source': 'segmentation_mask'})
    for row in rows:
        if row['kind'] == 'area':
            row['area'] = row['area_px'] * float(np.prod(scale))
            row['unit'] = unit + '2'
            judged = row['area'] if limits_unit == unit else row['area_px']
            row['verdict'] = 'OK' if params.get('min_area', 0) <= judged <= params.get('max_area', math.inf) else 'NG'
    return rows
