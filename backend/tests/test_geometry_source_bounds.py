"""Original-source bounds: controlled pixels/mappings, no model or physical calibration claims."""
from datetime import datetime, timezone
import asyncio
import hashlib

import numpy as np
import pytest

from backend.engine.geometry_measurement import measure_geometry
from backend.engine.spatial_calibration import CalibrationScope, manual_calibration, project_calibration_store

SIZE = [32, 24]
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def calibration(mode):
    if mode == 'pixels':
        return {}, None
    if mode == 'inline':
        return {'calibration': {'unit': 'mm', 'mm_per_pixel_x': .25, 'mm_per_pixel_y': .5,
                                'source_size': SIZE}}, None
    artifact = manual_calibration(.25, .5, camera_id='controlled-camera',
                                  acquisition_config={'resolution': SIZE}, source_size=SIZE,
                                  approved_by='controlled fixture only', now=NOW)
    return {'calibration_ref': artifact.ref}, CalibrationScope(lambda ref: artifact if ref == artifact.ref else None)


@pytest.mark.parametrize('mode', ['pixels', 'inline', 'reference'])
@pytest.mark.parametrize('points', [
    [[-0.001, 0], [2, 0], [2, 2]],
    [[0, -0.001], [2, 0], [2, 2]],
    [[30, 20], [32.001, 20], [30, 24]],
    [[30, 20], [32, 20], [30, 24.001]],
])
def test_polygon_outside_original_source_is_refused_without_clipping(mode, points):
    params, scope = calibration(mode)
    with pytest.raises(ValueError):
        measure_geometry(params, source_size=SIZE, polygons=[{'id': 'part', 'points': points}], scope=scope)


@pytest.mark.parametrize('mode,area,unit', [('pixels', 768, 'px2'), ('inline', 96, 'mm2'), ('reference', 96, 'mm2')])
def test_polygon_boundary_preserves_full_pixel_cell_area(mode, area, unit):
    params, scope = calibration(mode)
    points = [[0, 0], [32, 0], [32, 24], [0, 24]]
    [row] = measure_geometry(params, source_size=SIZE, polygons=[{'id': 'part', 'points': points}], scope=scope)
    assert (row['area_px'], row['area'], row['unit'], row['verdict']) == (768, area, unit, 'OK')
    assert row['source_size'] == SIZE and row['coordinate_space'] == 'original_image'


BAD_MASK_BINDINGS = [
    {},  # A smaller raster without mapping is not a full native image.
    {'bbox': [28, 20, 32, 24]},
    {'source_transform': [[1, 0, 28], [0, 1, 20], [0, 0, 1]]},
    {'bbox': [-1, 20, 3, 24], 'source_transform': [[1, 0, -1], [0, 1, 20], [0, 0, 1]]},
    {'bbox': [30, 20, 34, 24], 'source_transform': [[1, 0, 30], [0, 1, 20], [0, 0, 1]]},
    {'bbox': [28, 20, 32, 23], 'source_transform': [[1, 0, 28], [0, 1, 20], [0, 0, 1]]},
    {'bbox': [28, 20, 32, 24], 'source_transform': [[2, 0, 28], [0, 1, 20], [0, 0, 1]]},
    {'bbox': [28, 20, 32, 24], 'source_transform': [[1, 0, 28], [0, 1, 20], [.1, 0, 1]]},
]


@pytest.mark.parametrize('mode', ['pixels', 'inline', 'reference'])
@pytest.mark.parametrize('binding', BAD_MASK_BINDINGS)
def test_crop_mask_requires_exact_native_bbox_and_translation_in_every_calibration_mode(mode, binding):
    params, scope = calibration(mode)
    with pytest.raises(ValueError):
        measure_geometry(params, source_size=SIZE,
                         masks=[{'id': 'class-1', 'mask': np.ones((4, 4), np.uint8), **binding}], scope=scope)


@pytest.mark.parametrize('mode,area,unit', [('pixels', 16, 'px2'), ('inline', 2, 'mm2'), ('reference', 2, 'mm2')])
def test_native_resolution_crop_at_source_boundary_keeps_area_and_mapping(mode, area, unit):
    params, scope = calibration(mode)
    bbox = [28, 20, 32, 24]; transform = [[1, 0, 28], [0, 1, 20], [0, 0, 1]]
    [row] = measure_geometry(params, source_size=SIZE,
                            masks=[{'id': 'class-1', 'mask': np.ones((4, 4), np.uint8),
                                    'bbox': bbox, 'source_transform': transform}], scope=scope)
    assert (row['area_px'], row['area'], row['unit']) == (16, area, unit)
    assert row['bbox'] == bbox and row['source_transform'] == transform


@pytest.mark.parametrize('mode,area', [('pixels', 2), ('inline', .25), ('reference', .25)])
def test_full_source_mask_counts_native_boundary_cells(mode, area):
    params, scope = calibration(mode)
    mask = np.zeros((24, 32), np.uint8); mask[0, 0] = 1; mask[23, 31] = 1
    [row] = measure_geometry(params, source_size=SIZE, masks=[{'id': 'class-1', 'mask': mask}], scope=scope)
    assert row['area_px'] == 2 and row['area'] == area


@pytest.mark.parametrize('source_size', [[0, 24], [-1, 24], [32.5, 24], [32.0, 24],
                                        [True, 24], [float('nan'), 24], [float('inf'), 24],
                                        ['32', 24], [32], [32, 24, 1], None])
def test_source_size_requires_two_positive_whole_native_dimensions(source_size):
    with pytest.raises(ValueError):
        measure_geometry({}, source_size=source_size)


@pytest.mark.parametrize('limit', ['min_length', 'max_length'])
def test_length_limit_without_source_path_cannot_return_successful_area_only(limit):
    with pytest.raises(ValueError):
        measure_geometry({'paths': [], limit: 0}, source_size=SIZE,
                         polygons=[{'id': 'part', 'points': [[0, 0], [2, 0], [2, 2], [0, 2]]}])


def test_explicit_area_only_limits_and_boundary_length_remain_supported():
    [area] = measure_geometry({'min_area': 4, 'max_area': 4}, source_size=SIZE,
                              polygons=[{'id': 'part', 'points': [[0, 0], [2, 0], [2, 2], [0, 2]]}])
    assert (area['area'], area['verdict']) == (4, 'OK')
    [length] = measure_geometry({'paths': [{'id': 'diagonal', 'points': [[0, 0], [32, 24]]}],
                                'min_length': 40, 'max_length': 40}, source_size=(32, 24))
    assert (length['length_px'], length['length'], length['verdict']) == (40, 40, 'OK')


@pytest.mark.parametrize('mode', ['pixels', 'inline', 'reference'])
def test_actual_measure_api_refuses_outside_polygon_and_preserves_source_and_calibration(tmp_path, mode):
    from fastapi import FastAPI
    import httpx
    from PIL import Image
    from backend.api.routes_geometry import router
    app = FastAPI(); app.include_router(router)
    project = {'project_dir': str(tmp_path)}; app.state.current_project = project
    image = tmp_path/'source.png'; Image.new('RGB', (32, 24), (20, 40, 60)).save(image)
    params, _ = calibration(mode)
    if mode == 'reference':
        artifact = manual_calibration(.25, .5, camera_id='controlled-camera',
                                      acquisition_config={'resolution': SIZE}, source_size=SIZE,
                                      approved_by='controlled fixture only', now=NOW)
        assert project_calibration_store(project).save(artifact) == params['calibration_ref']
    before = {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in tmp_path.rglob('*') if p.is_file()}
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
            outside = await api.post('/api/geometry/measure', json={'image_path': str(image), 'params': params,
                                     'polygons': [{'id': 'part', 'points': [[30, 20], [32.001, 20], [30, 24]]}]})
            boundary = await api.post('/api/geometry/measure', json={'image_path': str(image), 'params': params,
                                      'polygons': [{'id': 'part', 'points': [[0, 0], [32, 0], [32, 24], [0, 24]]}]})
            return outside, boundary
    outside, boundary = asyncio.run(request())
    assert outside.status_code == 422, outside.text
    assert boundary.status_code == 200, boundary.text
    row = boundary.json()['measurements'][0]
    assert row['area_px'] == 768 and row['area'] == (768 if mode == 'pixels' else 96)
    assert boundary.json()['source_sha256'] == before['source.png']
    assert {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tmp_path.rglob('*') if p.is_file()} == before
